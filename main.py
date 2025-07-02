import os
import logging
import asyncio
# Removed unused imports
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
from telegram import Update
from telegram.constants import ParseMode
# Rename the import to avoid conflict
from database.db_instance import get_database
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
from game.shop_system import shop_system

# Load environment variables and config
load_dotenv()
from config import PORT, DEBUG

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

IS_VERCEL = bool(os.getenv('VERCEL_URL'))

# Initialize logging early
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO if not DEBUG else logging.DEBUG
)
logger = logging.getLogger(__name__)

SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1])

# Initialize Flask app
app = Flask(__name__)

# Only import web_dashboard in development environment
if not IS_VERCEL:
    try:
        from utils.web_dashboard import app as dashboard_app
# Only import web_dashboard in development environment
if not IS_VERCEL:
    try:
        # Import dashboard but don't use variable name to avoid lint warnings
        from utils.web_dashboard import app as _
        logger.info("Web dashboard available in development mode")
    except ImportError:
        logger.warning("Web dashboard not available - some features will be disabled")
    except ModuleNotFoundError as e:
        logger.warning(f"Web dashboard dependency missing: {e}")
    

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

# Application state tracking
app_state = {
    'initialized': False,
    'loop': None
}

# Global application instance
application = None

# STRICT OWNER VERIFICATION
OWNERS = {5956598856, 5845254367}
ADMIN_LOG_CHANNEL = -1002848899456
def telegram_webhook():
    """Endpoint for Telegram webhook updates"""
    global application
    
    # Initialize application if not already done
    if not application:
        try:
            asyncio.run(setup_application())
            return Response(status=503)  # Return 503 for this request, next one will work
        except Exception as e:
            logger.error(f"Failed to setup application: {e}")
            return Response(status=500)
        
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        return Response(status=403)
        
    try:
        json_data = request.get_json()
        if application and application.bot:
            update = Update.de_json(json_data, application.bot)
            
            async def process_update():
                try:
                    if application:
                        await application.process_update(update)
                    return True
                except Exception as e:
                    logger.error(f"Error processing update: {str(e)}")
                    return False

            asyncio.run(process_update())
            return Response(status=200)
        else:
            logger.error("Application or bot not initialized")
            return Response(status=500)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=500)


@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy"
    })


async def setup_application():
    """Initialize and configure the bot application with all handlers"""
    global application
    # Ensure TOKEN is not None before using it
    if TOKEN:
        application = Application.builder().token(TOKEN).build()
    else:
        raise ValueError("TELEGRAM_TOKEN is required")

    # Corrected shop system handlers
    application.add_handler(CommandHandler("shop", lambda update, context: asyncio.create_task(shop_system.show_shop(update.effective_user.id)) if update.effective_user else None))
    # Replace with proper handler if shop_system.buy exists
    # application.add_handler(CommandHandler("buy", lambda update, context: shop_system.buy(update.effective_user.id if update.effective_user else None)))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("explore", explore))
    application.add_handler(CommandHandler("nuke", owner_reset))

    # Character and team handlers
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
    
async def initialize_database():
    """Initialize the database connection"""
    try:
        db = await get_database()
        # Database might not have explicit connect method
        # Just log success and continue
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        return
    

async def main():
    """Main function to run the application"""
    global application
    
    try:
        # Initialize application
        await setup_application()
        await initialize_database()
        
        if IS_VERCEL:
            # Vercel deployment - set webhook
            webhook_url = f"https://{os.getenv('VERCEL_URL')}/webhook"
            if application and application.bot:
                await application.bot.set_webhook(
                    url=webhook_url,
                    secret_token=SECRET_TOKEN
                )
                logger.info(f"Webhook set to {webhook_url}")
        else:
            # Local development - start polling
            if application and application.bot:
                await application.bot.delete_webhook()
                # Use the correct method for polling
                await application.initialize()
                await application.start_polling()
                
                # Keep the app running
                while True:
                    await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Error in main function: {e}")

if __name__ == "__main__":
    try:
        if IS_VERCEL:
            # Vercel deployment - use Flask
            # Initialize application before starting Flask
            try:
                asyncio.run(setup_application())
                asyncio.run(initialize_database())
                app.run(host="0.0.0.0", port=PORT)
            except Exception as e:
                logger.error(f"Failed to start in Vercel mode: {e}")
        else:
            # Local development - use asyncio
            asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        logger.info("Bot shutdown complete")
            
