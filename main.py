import os
import logging
import asyncio
import uvicorn
from datetime import datetime
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
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

# Initialize FastAPI app
app = FastAPI()

# Global application and db instance for persistent server
application = None
app_initialized = False
global_db = None

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
    global application, app_initialized, global_db
    if application is None:
        application = Application.builder().token(TOKEN).build()
    try:
        # Initialize database and other services ONCE
        if global_db is None:
            db = Database()
            await db.init_db()
            global_db = db
        application.bot_data["db"] = global_db
        application.bot_data["shop_system"] = ShopSystem()
        register_handlers(application)
        async def error_handler(update: object, context):
            if isinstance(context.error, asyncio.CancelledError):
                logger.warning(f"Task cancelled for update {update}")
                return
            logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
            if isinstance(update, TelegramUpdate) and getattr(update, "effective_message", None):
                try:
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            "An error occurred !! Please report to mods"
                        )
                except BadRequest:
                    pass
        application.add_error_handler(error_handler)
        if not app_initialized:
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
        logger.info("Starting graceful shutdown...")
        try:
            # Stop receiving updates first
            await application.updater.stop()
            # Then stop the application
            await application.stop()
            await application.shutdown()
            logger.info("Shutdown completed successfully")
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
    except Exception as e:
        logger.error(f"Error during shutdown handling: {e}")
    finally:
        loop.close()
        if loop.is_running():
            loop.stop()


@app.post("/webhook")
async def webhook(request: Request):
    client_ip = request.client.host
    logger.info(f"Received webhook request from IP: {client_ip}")

    if not is_ip_allowed(client_ip):
        logger.warning(f"Unauthorized IP blocked: {client_ip}")
        return Response(status_code=403)

    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        logger.warning("Invalid secret token received")
        return Response(status_code=403)

    try:
        json_data = await request.json()
        if not json_data:
            logger.warning("Empty webhook payload received")
            return Response(status_code=400)

        logger.debug(f"Webhook payload: {json_data}")

        # Use global application and db
        if not app_initialized:
            await initialize_application()
        update = Update.de_json(json_data, application.bot)
        await application.process_update(update)
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return Response(status_code=500)

@app.get("/")
async def index():
    return {
        "status": "ok",
        "message": "Attack on Titan Bot is running",
        "environment": ENV,
        "initialized": app_initialized
    }

@app.get("/health")
async def health_check():
    try:
        bot_username = None
        if app_initialized and application and application.bot:
            bot_username = (await application.bot.get_me()).username
        return {
            "status": "ok",
            "initialized": app_initialized,
            "bot_username": bot_username
        }
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/status")
async def status():
    return {
        "status": "running",
        "initialized": True,
        "last_update": datetime.now().isoformat(),
        "active_battles": len(active_battles)
    }

@app.get("/set_webhook")
async def set_webhook(request: Request):
    try:
        host = request.headers.get("host")
        webhook_url = f"https://{host}/webhook"
        logger.info(f"Attempting to set webhook to: {webhook_url}")
        if not webhook_url.startswith("https://"):
            return JSONResponse({"status": "error", "message": "Webhook URL must use HTTPS"}, status_code=400)
        app_instance = await initialize_application()
        await app_instance.bot.set_webhook(
            url=webhook_url,
            secret_token=SECRET_TOKEN,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )
        webhook_info = await app_instance.bot.get_webhook_info()
        logger.info(f"Webhook info: {webhook_info}")
        return {
            "status": "success",
            "message": f"Webhook set to {webhook_url}",
            "webhook_info": webhook_info.to_dict()
        }
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

def register_handlers(app_instance):
    app_instance.add_handler(CommandHandler("start", start_and_clear_memory))
    app_instance.add_handler(CommandHandler("inv", profile))
    app_instance.add_handler(CommandHandler("explore", explore))
    app_instance.add_handler(CommandHandler("map", show_map))
    app_instance.add_handler(CommandHandler("travel", travel_command))
    app_instance.add_handler(CommandHandler("shop", shop_command))
    app_instance.add_handler(CommandHandler("status", profile))
    app_instance.add_handler(CommandHandler("buy", buy_command))
    app_instance.add_handler(CallbackQueryHandler(show_character_selection, pattern="^start_journey$"))
    app_instance.add_handler(CallbackQueryHandler(show_character_details, pattern=r"^select_"))
    app_instance.add_handler(CallbackQueryHandler(confirm_character_selection, pattern=r"^confirm_"))
    app_instance.add_handler(CallbackQueryHandler(create_character, pattern=r"^birthplace_"))
    app_instance.add_handler(CallbackQueryHandler(back_to_selection, pattern="^back_to_selection$"))
    app_instance.add_handler(CallbackQueryHandler(show_team, pattern="^show_team$"))
    app_instance.add_handler(CallbackQueryHandler(manage_team, pattern="^manage_team$"))
    app_instance.add_handler(CallbackQueryHandler(add_to_team, pattern=r"^add_to_team_"))
    app_instance.add_handler(CallbackQueryHandler(remove_from_team, pattern="^remove_from_team_"))
    app_instance.add_handler(CallbackQueryHandler(save_team, pattern="^save_team$"))
    app_instance.add_handler(CallbackQueryHandler(clear_team, pattern="^clear_team$"))
    app_instance.add_handler(CallbackQueryHandler(show_character_profile, pattern="^show_character_profile$"))
    app_instance.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
    app_instance.add_handler(CallbackQueryHandler(show_inventory, pattern="^show_inventory$"))
    app_instance.add_handler(CallbackQueryHandler(view_weapons, pattern="^view_weapons$"))
    app_instance.add_handler(CallbackQueryHandler(view_gear, pattern="^view_gear$"))
    app_instance.add_handler(CallbackQueryHandler(view_utilities, pattern="^view_utilities$"))
    app_instance.add_handler(CallbackQueryHandler(view_echo_shards, pattern="^view_echo_shards$"))
    app_instance.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))
    app_instance.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)"))
    app_instance.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$"))
    app_instance.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))
    app_instance.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))
    app_instance.add_handler(CallbackQueryHandler(button_callback))

# Shop command handler for /shop
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        shop_system = context.bot_data.get("shop_system")
        if not shop_system:
            await update.message.reply_text("Shop system not initialized. Please try again later.")
            return
        text, reply_markup = await shop_system.show_shop(context, user_id)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in shop_command: {e}")
        await update.message.reply_text("An error occurred while showing the shop.")

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


async def main():
    try:
        await asyncio.sleep(2)
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
