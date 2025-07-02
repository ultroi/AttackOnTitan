import os
import logging
import signal
import sys
from functools import partial
import asyncio
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
from database.db_instance import get_database, close_connection
from game.character_system import (
    profile,
    show_character_profile,
    add_to_team,
    save_team,
    clear_team,
    manage_team,
    show_character_selection,
    show_character_details,
    confirm_character_selection,
    create_character,
    show_team,
    back_to_selection
)
from game.explore import explore
from game.callback_handlers import button_callback
from utils.web_dashboard import owner_monitor
from game.shop_system import ShopSystem
from config import PORT, DEBUG

# Load environment variables and config
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

IS_VERCEL = bool(os.getenv('VERCEL_URL'))

# Initialize logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO if not DEBUG else logging.DEBUG
)
logger = logging.getLogger(__name__)

SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1])

# Only try to import web_dashboard in development environment
if not IS_VERCEL:
    try:
        from utils.web_dashboard import owner_monitor
        logger.info("Web dashboard available in development mode")
    except (ImportError, ModuleNotFoundError) as e:
        owner_monitor = None  # Create a dummy fallback
        logger.warning(f"Web dashboard not available - {str(e)}")
else:
    owner_monitor = None

# Initialize application and loop as None at module level
application = None
loop = None

OWNERS = {5956598856, 5845254367}
ADMIN_LOG_CHANNEL = -1002848899456

async def initialize_database():
    """Initialize database connection with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            db = await get_database()
            if db is None:
                raise ConnectionError("Failed to get database instance")
            
            await db.command('ping')
            logger.info("Database initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Database initialization attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                raise RuntimeError(f"Failed to initialize database after {max_retries} attempts")
            await asyncio.sleep(2)

async def start_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        user_id = update.effective_user.id
        await show_character_selection(update, context, user_id)

async def owner_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner command to reset/nuke data."""
    if update.effective_user and update.message:
        user_id = update.effective_user.id
        if user_id not in OWNERS:
            await update.message.reply_text("⛔ This command is only available to bot owners.")
            return
        
        # Add your reset logic here
        await update.message.reply_text("🔄 Reset command acknowledged.")
        # Use context to avoid linting error
        if context:
            logger.info(f"Reset command executed by {user_id}")

async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user:
            user_id = update.effective_user.id
            await ShopSystem.show_shop(update, context, user_id)
        
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and send a message to admin channel."""
    logger.error("Invalid update in owner_reset")
    logger.error(f"Update {update} caused error {context.error}")
    
    error_text = f"❌ Error in update {update}:\n{context.error}"
    if ADMIN_LOG_CHANNEL:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_LOG_CHANNEL,
                text=error_text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send error to admin channel: {e}")

async def setup_application():
    """Initialize and configure the bot application"""
    global application
    
    if not TOKEN:
        raise ValueError("TELEGRAM_TOKEN is required")

    try:
        # Build and initialize the application
        builder = Application.builder()
        builder.token(TOKEN)
        application = builder.build()
        
        # Add command handlers
        application.add_handler(CommandHandler("shop", shop_command))
        application.add_handler(CommandHandler("profile", profile))
        application.add_handler(CommandHandler("explore", explore))
        application.add_handler(CommandHandler("nuke", owner_reset))
        application.add_handler(CommandHandler("start", start_character_selection))
        application.add_handler(CommandHandler("monitor", owner_monitor))

        # Add callback handlers
        
        application.add_handler(CallbackQueryHandler(show_character_selection, pattern="^start_journey$"))
        application.add_handler(CallbackQueryHandler(show_character_details, pattern=r"^select_"))
        application.add_handler(CallbackQueryHandler(confirm_character_selection, pattern=r"^confirm_"))
        application.add_handler(CallbackQueryHandler(create_character, pattern=r"^birthplace_"))
        application.add_handler(CallbackQueryHandler(back_to_selection, pattern="^back_to_selection$"))
        application.add_handler(CallbackQueryHandler(show_team, pattern="^show_team$"))
        application.add_handler(CallbackQueryHandler(manage_team, pattern="^manage_team$"))
        application.add_handler(CallbackQueryHandler(add_to_team, pattern=r"^add_to_team_"))
        application.add_handler(CallbackQueryHandler(save_team, pattern="^save_team$"))
        application.add_handler(CallbackQueryHandler(clear_team, pattern="^clear_team$"))
        application.add_handler(CallbackQueryHandler(show_character_profile, pattern="^show_character_profile$"))
        application.add_handler(CommandHandler("button", button_callback))
        # Add error handler
        application.add_error_handler(error_handler)
        
        # Initialize the application
        await application.initialize()
        
        logger.info("Application setup completed successfully")
        return application
    except Exception as e:
        logger.error(f"Failed to setup application: {e}")
        if application:
            try:
                await application.shutdown()
            except Exception as shutdown_error:
                logger.error(f"Error during application shutdown: {shutdown_error}")
        raise

async def get_or_create_application():
    """Get or create the application instance"""
    global application
    if application is None:
        await initialize_database()
        application = await setup_application()
    return application

async def shutdown(signal=None):
    """Clean shutdown procedure"""
    global application, loop
    
    logger.info("Shutting down gracefully...")
    
    if application:
        try:
            logger.info("Stopping application...")
            if hasattr(application, 'updater') and application.updater.running:
                await application.updater.stop()
            if hasattr(application, 'running') and application.running:
                await application.stop()
            await application.shutdown()
        except Exception as e:
            logger.error(f"Error stopping application: {e}")
    
    try:
        logger.info("Closing database connections...")
        await close_connection()
    except Exception as e:
        logger.error(f"Error closing database connection: {e}")
    
    # Cancel all running tasks
    try:
        current_loop = loop or asyncio.get_running_loop()
        tasks = [t for t in asyncio.all_tasks(current_loop) if not t.done()]
        if tasks:
            logger.info(f"Cancelling {len(tasks)} outstanding tasks")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"Error cancelling tasks: {e}")
    
    logger.info("Shutdown complete")

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook updates"""
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        return Response(status=403)
        
    try:
        json_data = request.get_json()
        
        # Process in a separate event loop
        async def process_update_async():
            app_instance = await get_or_create_application()
            update = Update.de_json(json_data, app_instance.bot)
            await app_instance.process_update(update)
        
        # Create a new event loop for this request
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        new_loop.run_until_complete(process_update_async())
        new_loop.close()
        
        return Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=500)

@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy"})

# Initialize Flask app without static folder
app = Flask(__name__, static_folder=None)

@app.route('/favicon.ico')
@app.route('/favicon.png')
def favicon():
    """Handle favicon requests"""
    return '', 204  # Always return no content for favicon requests

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors gracefully"""
    logger.info(f"404 error: {error}")
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {e}")
    return jsonify({"error": "Internal server error"}), 500

async def run_application():
    """Run the application with proper event loop management"""
    global application, loop
    
    try:
        loop = asyncio.get_running_loop()
        
        # Set up signal handlers
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                if sys.platform == 'win32':
                    signal.signal(sig, lambda s, _: asyncio.create_task(shutdown(s)))
                else:
                    loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))
        except Exception as e:
            logger.warning(f"Failed to set up signal handlers: {e}")
        
        await initialize_database()
        application = await setup_application()
        
        if IS_VERCEL:
            webhook_url = "https://attack-on-titan-4vxv4stum-fakeryukshinigami-gmailcoms-projects.vercel.app/webhook"
            await application.bot.set_webhook(
                url=webhook_url,
                secret_token=SECRET_TOKEN,
                allowed_updates=Update.ALL_TYPES
            )
            logger.info(f"Webhook set to {webhook_url}")
            
            # In Vercel, we just keep the app alive
            while True:
                await asyncio.sleep(3600)
        else:
            # In local development, use polling mode
            logger.info("Running in polling mode for local development")
            await application.bot.delete_webhook()
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                close_loop=False
            )

    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
    finally:
        await shutdown()

def run_bot():
    try:
        if IS_VERCEL:
            app.run(host="0.0.0.0", port=PORT)
        else:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(initialize_database())
            app_instance = loop.run_until_complete(setup_application())
            logger.info("Running in polling mode for local development")
            app_instance.run_polling()
    except Exception as e:
        logger.error(f"Fatal startup error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_bot()


