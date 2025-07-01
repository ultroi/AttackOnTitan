import os
import logging
import asyncio
from threading import Thread
import threading
import time
import signal
import json
from datetime import datetime
from flask import Flask, jsonify, request, Response
from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from database.db_instance import initialize_database, get_database
from game.character_system import (
    start_character_selection,
    profile,
    show_character_profile,
    add_to_team,
    save_team,
    manage_team,
    show_character_selection,
    show_character_details,
    confirm_character_selection,
    create_character,
    show_team,
    back_to_selection
)
from game.explore import (
    explore,
    active_battles,
    handle_battle_start,
    handle_battle_action
)
from game.callback_handlers import button_callback
from utils.monitor import resource_monitor
from game.shop_system import shop_system
from utils.web_dashboard import (
    owner_monitor,
    owner_cleanup, 
    owner_health,
    app,
    log_startup_info,
    setup_ngrok_dashboard,
    get_dashboard_url,
    PUBLIC_DASHBOARD_URL,
    set_application,
    WEBHOOK_SECRET_PATH
)

# Load environment variables and config
load_dotenv()
from config import PORT, DEBUG

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

# Initialize logging early
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1])  # Use part of bot token as secret if not set

# Initialize Flask app
app = Flask(__name__)

# Application state tracking
app_state = {
    'initialized': False,
    'loop': None
}

async def initialize_application():
    """Initialize the application"""
    try:
        await application.initialize()
        await application.start()
        # Store the event loop in app_state
        app_state['loop'] = asyncio.get_running_loop()
        app_state['initialized'] = True
        logger.info("✅ Application initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}")
        raise

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Endpoint for Telegram webhook updates"""
    if not application:
        logger.error("Application not initialized yet")
        return Response(status=503)  # Service Unavailable
        
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        logger.warning(f"Unauthorized webhook request. Got incorrect token")
        return Response(status=403)
        
    try:
        json_data = request.get_json()
        if not json_data:
            logger.error("No JSON data in webhook request")
            return Response(status=400)
        
        update = Update.de_json(json_data, application.bot)
        if not update:
            logger.error("Failed to parse update")
            return Response(status=400)

        async def process_update():
            try:
                await application.process_update(update)
                return True
            except Exception as e:
                logger.error(f"Error processing update: {str(e)}")
                return False

        # Get the main event loop from app_state
        if not app_state['loop']:
            logger.error("No event loop available")
            return Response(status=503)  # Service Unavailable

        # Run the update processing in the main event loop
        future = asyncio.run_coroutine_threadsafe(process_update(), app_state['loop'])
        try:
            success = future.result(timeout=30)
            if success:
                return Response(status=200)
        except asyncio.TimeoutError:
            logger.error("Update processing timed out")
            return Response(status=504)  # Gateway Timeout
        except Exception as e:
            logger.error(f"Error processing update: {str(e)}")
        
        return Response(status=500)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return Response(status=500)

@app.route('/')
def health_check():
    """Root health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "AOT Bot"
    })

@app.route('/health')
def ngrok_health_check():
    """Health check endpoint for ngrok validation"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "AOT Bot Ngrok"
    })

# STRICT OWNER VERIFICATION
OWNERS = {5956598856, 5845254367}
ADMIN_LOG_CHANNEL = -1002848899456

async def initialize_app():
    """Initialize async components like the database."""
    try:
        await initialize_database()
        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.warning(f"⚠️ Database initialization had issues: {e}")
        logger.info("🚀 Continuing bot startup with limited database functionality")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        logger.error("Invalid update: missing user or message")
        return
        
    user_id = update.effective_user.id
    db = await get_database()
    logger.info(f"Start command triggered for user_id {user_id}")
    player = await db.get_player(user_id)
    
    if player:
        logger.info(f"Player found for user_id {user_id}: {player.name}")
        existing_player = await db.players.find_one({"user_id": user_id})
        if existing_player:
            await update.message.reply_text(
                "You have already started your journey! Use /explore to explore the world of Attack on Titan."
            )
        else:
            await start_character_selection(update, context)
    else:
        await start_character_selection(update, context)

async def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and hasattr(update, 'effective_user') and update.effective_user:
        user_id = update.effective_user.id
        logger.error(f"Error for user {user_id}: {context.error}")
        
        if "battle" in str(context.error).lower() or "explore" in str(context.error).lower():
            try:
                from game.explore import force_cleanup_user
                force_cleanup_user(user_id)
                logger.info(f"Cleaned up user {user_id} after error")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup user {user_id}: {cleanup_error}")
    
    try:
        if update and update.message:
            await update.message.reply_text("❌ An error occurred. Please try again or contact support.")
        elif update and update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text("❌ An error occurred. Please try again or contact support.")
        else:
            logger.error("Cannot send error message - no valid message context")
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")

async def show_shop(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        logger.error("Invalid update: missing user or message")
        return
        
    user_id = update.effective_user.id
    
    try:
        message_text, reply_markup = await shop_system.show_shop(user_id)
        await update.message.reply_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
            
    except Exception as e:
        logger.error(f"Shop error for user {user_id}: {e}")
        await update.message.reply_text("❌ An error occurred while loading the shop. Please try again later.")

async def owner_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Immediate reset by owner — no confirmation"""
    if not update.effective_user or not update.message:
        logger.error("Invalid update: missing user or message")
        return
        
    user = update.effective_user
    message = update.message 

    if user.id not in OWNERS:
        await message.reply_text("U not owner, Baka !! 😾")
        return

    if not context.args or len(context.args) == 0:
        await message.reply_text("Usage: /nuke <user_id> [reason]")
        return

    try:
        target_id = int(context.args[0])
    except (ValueError, IndexError):
        await message.reply_text("Invalid user ID provided.")
        return

    reason = " ".join(context.args[1:]).strip() if len(context.args) > 1 else ""
    
    db_instance = await get_database()
    
    try:
        await db_instance.players.delete_one({"user_id": target_id})
        await db_instance.characters.delete_many({"user_id": target_id})
        target_name = f"`{target_id}`"
    except Exception:
        target_name = f"`{target_id}`"

    executor_name = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    log_msg = (
        f"☢️ <b>RESET INITIATED</b>☢️\n\n"
        f"👤 <b>Target:</b> {target_name}\n"
        f"🆔 <b>ID:</b> <code>{target_id}</code>\n"
        f"🛡️ <b>By:</b> {executor_name}\n"
    )
    if reason:
        log_msg += f"📌 Reason: <code>{reason}</code>"

    try:
        await context.bot.send_message(ADMIN_LOG_CHANNEL, log_msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send log message: {e}")

    try:
        await context.bot.send_message(
            target_id,
            "⚠️ Your account has been reset.\n"
        )
    except Exception as e:
        logger.error(f"Failed to notify target user: {e}")

    await message.reply_text(f"✅ User {target_id} has been reset successfully.")

async def notify_owner_dashboard_url():
    """Notify the owner about the public dashboard URL"""
    if PUBLIC_DASHBOARD_URL and PUBLIC_DASHBOARD_URL != "http://localhost:5000":
        for owner_id in OWNERS:
            try:
                message = f"🌐 <b>Dashboard is Live!</b>\n\n"
                message += f"📱 <b>Public Access:</b>\n{PUBLIC_DASHBOARD_URL}/dashboard\n\n"
                message += f"📱 <b>Mobile:</b>\n{PUBLIC_DASHBOARD_URL}/m\n\n"
                message += f"🔗 <b>API:</b>\n{PUBLIC_DASHBOARD_URL}/api/players"
                
                dashboard_url = get_dashboard_url()
                keyboard = [[InlineKeyboardButton("🌐 Open Dashboard", url=dashboard_url)]] if dashboard_url else None
                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                
                await application.bot.send_message(
                    chat_id=owner_id,
                    text=message,
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                logger.info(f"📨 Dashboard URL sent to owner {owner_id}")
            except Exception as e:
                logger.error(f"Failed to notify owner {owner_id} about dashboard: {e}")
async def setup_application():
    """Initialize and configure the bot application with all handlers"""

    global application
    # Initialize bot application
    assert TOKEN is not None, "Telegram token cannot be None"
    application = Application.builder().token(TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("explore", explore))
    application.add_handler(CommandHandler("nuke", owner_reset))
    application.add_handler(CommandHandler("monitor", owner_monitor))
    application.add_handler(CommandHandler("cleanup", owner_cleanup))
    application.add_handler(CommandHandler("health", owner_health))
    application.add_handler(CommandHandler("shop", show_shop))
    application.add_handler(CallbackQueryHandler(show_character_selection, pattern="^start_journey$"))
    application.add_handler(CallbackQueryHandler(show_character_details, pattern=r"^select_"))
    application.add_handler(CallbackQueryHandler(confirm_character_selection, pattern=r"^confirm_"))
    application.add_handler(CallbackQueryHandler(create_character, pattern=r"^birthplace_"))
    application.add_handler(CallbackQueryHandler(back_to_selection, pattern="^back_to_selection$"))
    application.add_handler(CallbackQueryHandler(show_team, pattern="^show_team$"))
    application.add_handler(CallbackQueryHandler(manage_team, pattern="^manage_team$"))
    application.add_handler(CallbackQueryHandler(add_to_team, pattern=r"^add_to_team_"))
    application.add_handler(CallbackQueryHandler(save_team, pattern="^save_team$"))
    application.add_handler(CallbackQueryHandler(show_character_profile, pattern="^show_character_profile$"))
    application.add_handler(CallbackQueryHandler(handle_battle_start, pattern=r"^battle_"))
    application.add_handler(CallbackQueryHandler(handle_battle_action, pattern=r"^ability_"))
    application.add_handler(CallbackQueryHandler(handle_battle_action, pattern=r"^action_"))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)

    # Register the application for webhook handling
    set_application(application)

    return application




def run_flask():
    """Run Flask app in a separate thread"""
    app.run(host="0.0.0.0", port=PORT)
    logger.info(f"✅ Flask server running on port {PORT}")

async def wait_for_flask(timeout=30):
    """Wait for Flask server to be ready"""
    import requests
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"http://localhost:{PORT}/")
            if response.status_code == 200:
                logger.info("✅ Flask server is ready")
                return True
        except requests.exceptions.RequestException:
            await asyncio.sleep(0.5)
    return False


async def main():
    """Main function to run the application"""
    global application
    try:
        # Store the main event loop
        app_state['loop'] = asyncio.get_running_loop()

        # 1. Initialize the application first
        await setup_application()
        if not application:
            raise RuntimeError("Failed to initialize application")

        # 2. Initialize the application components
        await initialize_application()

        # 3. Start the Flask app
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()

        # Wait for Flask to be ready
        if not await wait_for_flask():
            raise RuntimeError("Flask server failed to start")

        # 4. Initialize ngrok and get URLs
        ngrok_url = await setup_ngrok_dashboard()
        if not ngrok_url:
            raise RuntimeError("Failed to get ngrok URL")

        # 5. Set up webhook using the ngrok URL
        webhook_url = f"{ngrok_url}/webhook"
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=SECRET_TOKEN,
            drop_pending_updates=True
        )
        logger.info(f"✅ Webhook set to: {webhook_url}")
        
        # 6. Initialize database and other components
        await initialize_app()
        
        # 7. Notify owners about the dashboard URL
        await notify_owner_dashboard_url()
        
        # Notify about successful setup
        logger.info("🚀 Bot is fully initialized and ready to receive commands")

        # Keep the event loop running
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour

    except Exception as e:
        logger.error(f"Error in main function: {e}")
        raise
    finally:
        if 'application' in globals() and application:
            await application.stop()
            await application.shutdown()
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        logger.info("Bot shutdown complete")