import os
import logging
import asyncio
import uvicorn
from flask import Flask, request, Response
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from telegram import Update as TelegramUpdate
from ipaddress import ip_network, ip_address
from game.map_system import show_map, MAP_IMAGE_URL
from database.db import Database
import signal

# Import handlers
from game.character_system import (
    show_character_selection,
    show_character_details, confirm_character_selection,
    create_character, back_to_selection,
    start_character_selection
)
from game.profile_system import (
    profile, show_character_profile,
    show_team, manage_team, add_to_team, remove_from_team, save_team, clear_team,
    show_inventory, view_weapons, view_gear, view_utilities, view_echo_shards
)
from utils.extra import buy_command
from game.explore import explore
from game.callback_handlers import button_callback, handle_travel_decision
from game.shop_system import ShopSystem
from game.battle_system import handle_battle_action, active_battles
from game.travel_system import travel_command, handle_travel_direction, handle_cancel_travel

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if TOKEN is None:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1])
ENV = os.getenv("ENV", "development")

# Updated ALLOWED_IPS with proper CIDR ranges and specific IPs
ALLOWED_IPS = [
    "91.108.4.0/22",      # Telegram IP range
    "91.108.5.82",        # Specific Telegram IP that was being blocked
    "91.108.56.0/22",     # Telegram IP range
    "149.154.160.0/20",   # Telegram IP range
    "95.161.64.0/20",     # Telegram IP range
    "64.29.17.131"        # Your Vercel IP
]

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Global application instance
application = None
app_initialized = False

def is_ip_allowed(client_ip: str) -> bool:
    """Check if client IP is in allowed list."""
    try:
        ip = ip_address(client_ip)
        for net in ALLOWED_IPS:
            if ip in ip_network(net):
                return True
        return False
    except ValueError:
        return False

async def initialize_application():
    """Initialize the Telegram bot application with all handlers."""
    global application, app_initialized
    
    if application is None:
        application = Application.builder().token(TOKEN).build()
    
    try:
        # Initialize database and other services
        db = Database()
        await db.init_db()
        application.bot_data["db"] = db
        application.bot_data["shop_system"] = ShopSystem()

        # Initialize user_data for all updates
        async def init_user_data(update: Update, context):
            if not context.user_data:
                context.user_data.clear()
                context.user_data.update({"message_history": []})

        # Add command handlers
        application.add_handler(CommandHandler("start", start_and_clear_memory))
        application.add_handler(CommandHandler("profile", profile))
        application.add_handler(CommandHandler("explore", explore))
        application.add_handler(CommandHandler("map", show_map))
        application.add_handler(CommandHandler("travel", travel_command))

        async def shop_command(update: Update, context):
            try:
                if not update.effective_user or not update.message:
                    if update.message:
                        await update.message.reply_text("User or message information not available.")
                    return
                await init_user_data(update, context)
                shop_system = context.bot_data["shop_system"]
                user_id = str(update.effective_user.id)
                message, reply_markup = await shop_system.show_shop(context, user_id)
                await update.message.reply_text(
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except (BadRequest, PyMongoError) as e:
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"Error in shop_command for user {user_id}: {e}")
                if update.message:
                    await update.message.reply_text(f"Error accessing shop: {str(e)}")

        async def status_command(update: Update, context):
            try:
                if update.message:
                    await update.message.reply_text(
                        "🛠 Attack on Titan Bot is running!\n"
                        f"Environment: {ENV}\n"
                        "Use /start to begin your journey."
                    )
            except BadRequest as e:
                logger.error(f"Error in status_command: {e}")
                if update.message:
                    await update.message.reply_text("Error checking bot status.")

        application.add_handler(CommandHandler("shop", shop_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("buy", buy_command))
        
        # Add callback handlers
        application.add_handler(CallbackQueryHandler(show_character_selection, pattern="^start_journey$"))
        application.add_handler(CallbackQueryHandler(show_character_details, pattern=r"^select_"))
        application.add_handler(CallbackQueryHandler(confirm_character_selection, pattern=r"^confirm_"))
        application.add_handler(CallbackQueryHandler(create_character, pattern=r"^birthplace_"))
        application.add_handler(CallbackQueryHandler(back_to_selection, pattern="^back_to_selection$"))
        application.add_handler(CallbackQueryHandler(show_team, pattern="^show_team$"))
        application.add_handler(CallbackQueryHandler(manage_team, pattern="^manage_team$"))
        application.add_handler(CallbackQueryHandler(add_to_team, pattern=r"^add_to_team_"))
        application.add_handler(CallbackQueryHandler(remove_from_team, pattern="^remove_from_team_"))
        application.add_handler(CallbackQueryHandler(save_team, pattern="^save_team$"))
        application.add_handler(CallbackQueryHandler(clear_team, pattern="^clear_team$"))
        application.add_handler(CallbackQueryHandler(show_character_profile, pattern="^show_character_profile$"))
        application.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
        application.add_handler(CallbackQueryHandler(show_inventory, pattern="^show_inventory$"))
        application.add_handler(CallbackQueryHandler(view_weapons, pattern="^view_weapons$"))
        application.add_handler(CallbackQueryHandler(view_gear, pattern="^view_gear$"))
        application.add_handler(CallbackQueryHandler(view_utilities, pattern="^view_utilities$"))
        application.add_handler(CallbackQueryHandler(view_echo_shards, pattern="^view_echo_shards$"))
        application.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))
        application.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)"))
        application.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$"))
        application.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))
        application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))
        application.add_handler(CallbackQueryHandler(button_callback))

        async def error_handler(update: object, context):
            """Handle errors in the application."""
            if isinstance(context.error, asyncio.CancelledError):
                logger.warning(f"Task cancelled for update {update}")
                return
                
            logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)

            if isinstance(update, TelegramUpdate) and getattr(update, "effective_message", None):
                try:
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            "An error occurred while processing your request. Please try again later."
                        )
                except BadRequest:
                    pass

        application.add_error_handler(error_handler)
        
        # Initialize the application
        await application.initialize()
        await application.start()
        app_initialized = True
        logger.info("Bot application initialized and started successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}", exc_info=True)
        app_initialized = False
        raise

    return application

async def shutdown_application():
    """Gracefully shutdown the application."""
    global application, app_initialized
    if application and app_initialized:
        logger.info("Starting application shutdown...")
        try:
            await asyncio.wait_for(application.stop(), timeout=10)
            await asyncio.wait_for(application.shutdown(), timeout=5)
            logger.info("Application shutdown completed successfully")
        except asyncio.TimeoutError:
            logger.warning("Application shutdown timed out")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            app_initialized = False

def handle_shutdown(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received shutdown signal {signum}")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(shutdown_application())
    finally:
        loop.close()
        if loop.is_running():
            loop.stop()

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handle incoming webhook updates."""
    client_ip = request.remote_addr
    logger.info(f"Received webhook request from IP: {client_ip}")

    # IP verification
    if not is_ip_allowed(client_ip):
        logger.warning(f"Unauthorized IP blocked: {client_ip}")
        return Response(status=403)

    # Secret token verification
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        logger.warning("Invalid secret token received")
        return Response(status=403)

    try:
        json_data = request.get_json()
        if not json_data:
            logger.warning("Empty webhook payload received")
            return Response(status=400)

        logger.debug(f"Webhook payload: {json_data}")
        
        # Ensure application is initialized
        app_instance = await initialize_application()
        if not app_initialized:
            logger.error("Application not initialized properly")
            return Response("Service unavailable", status=503)
            
        update = Update.de_json(json_data, app_instance.bot)
        await app_instance.process_update(update)
        return Response(status=200)
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return Response(status=500)

@app.route('/')
def index():
    """Health check endpoint."""
    return {
        "status": "ok",
        "message": "Attack on Titan Bot is running",
        "environment": ENV,
        "initialized": app_initialized
    }

@app.route('/health')
async def health_check():
    """Detailed health check endpoint."""
    try:
        return {
            "status": "ok",
            "initialized": app_initialized,
            "bot_username": application.bot.username if application and application.bot else None
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route('/favicon.ico')
def favicon():
    return Response(status=204)

@app.route('/set_webhook', methods=['GET', 'POST'])
async def set_webhook():
    """Set webhook URL for the bot."""
    try:
        webhook_url = f"https://{request.host}/webhook"
        logger.info(f"Attempting to set webhook to: {webhook_url}")
        
        if not webhook_url.startswith("https://"):
            return {"status": "error", "message": "Webhook URL must use HTTPS"}, 400
        
        app_instance = await initialize_application()
        await app_instance.bot.set_webhook(
            url=webhook_url,
            secret_token=SECRET_TOKEN,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )
        
        # Verify webhook was set
        webhook_info = await app_instance.bot.get_webhook_info()
        logger.info(f"Webhook info: {webhook_info}")
        
        return {
            "status": "success",
            "message": f"Webhook set to {webhook_url}",
            "webhook_info": webhook_info.to_dict()
        }
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return {"status": "error", "message": str(e)}, 500

async def start_and_clear_memory(update: Update, context):
    """Clear all temporary memory for the user and start fresh."""
    user_id = str(update.effective_user.id) if update.effective_user else None
    
    # Clear user_data
    if hasattr(context, 'user_data'):
        context.user_data.clear()
    
    # Remove from active_battles if present
    if user_id and user_id in active_battles:
        try:
            battle = active_battles.pop(user_id)
            if hasattr(battle, 'dispose'):
                battle.dispose()
        except Exception as e:
            logger.error(f"Error clearing battle for user {user_id}: {e}")
    
    # Call the original start handler
    await start_character_selection(update, context)

def handle_shutdown(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received shutdown signal {signum}")
    if application and app_initialized:
        # Create a new event loop for shutdown if needed
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(application.stop())
            loop.run_until_complete(application.shutdown())
        finally:
            loop.close()

async def main():
    """Main entry point for the bot."""
    try:
        logger.info(f"Starting bot in {ENV} environment")
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)
        
        # Initialize application before starting server
        await initialize_application()
        
        # Configure and start server
        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=int(os.environ.get('PORT', 5000)),
            log_level="info"
        )
        server = uvicorn.Server(config)
        
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("Server shutdown requested")
        finally:
            await shutdown_application()
                
    except Exception as e:
        logger.error(f"Bot crashed with error: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
