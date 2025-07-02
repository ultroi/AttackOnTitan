import os
import logging
import asyncio
from threading import Thread
import time
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
    clear_team,
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
    active_battles
)
from game.battle_system import handle_battle_start, handle_battle_action
from game.callback_handlers import button_callback
from game.shop_system import shop_system

# Load environment variables and config
load_dotenv()
from config import PORT, DEBUG

TOKEN = os.getenv("TELEGRAM_TOKEN")
IS_VERCEL = bool(os.getenv('VERCEL_URL'))

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

# Initialize logging early
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1])

# Initialize Flask app
app = Flask(__name__)

# Application state tracking
app_state = {
    'initialized': False,
    'loop': None
}

# STRICT OWNER VERIFICATION
OWNERS = {5956598856, 5845254367}
ADMIN_LOG_CHANNEL = -1002848899456

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Endpoint for Telegram webhook updates"""
    if not application:
        return Response(status=503)
        
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        return Response(status=403)
        
    try:
        json_data = request.get_json()
        update = Update.de_json(json_data, application.bot)
        
        async def process_update():
            try:
                await application.process_update(update)
                return True
            except Exception as e:
                logger.error(f"Error processing update: {str(e)}")
                return False

        asyncio.run(process_update())
        return Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=500)

@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })

async def setup_application():
    """Initialize and configure the bot application with all handlers"""
    global application
    application = Application.builder().token(TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("explore", explore))
    application.add_handler(CommandHandler("nuke", owner_reset))
    application.add_handler(CommandHandler("shop", show_shop))
    application.add_handler(CommandHandler("buy", buy))

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
    
    # Battle handlers
    application.add_handler(CallbackQueryHandler(handle_battle_start, pattern=r"^battle_"))
    application.add_handler(CallbackQueryHandler(handle_battle_action, pattern=r"^ability_"))
    application.add_handler(CallbackQueryHandler(handle_battle_action, pattern=r"^action_"))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Error handler
    application.add_error_handler(error_handler)

    return application

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
            await application.bot.set_webhook(
                url=webhook_url,
                secret_token=SECRET_TOKEN
            )
            logger.info(f"Webhook set to {webhook_url}")
        else:
            # Local development - start polling
            await application.bot.delete_webhook()
            await application.start_polling()
            
        logger.info("🚀 Bot is ready!")
        
        # Keep the app running
        while True:
            await asyncio.sleep(3600)
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    try:
        if IS_VERCEL:
            # Vercel deployment - use Flask
            app.run(host="0.0.0.0", port=PORT)
        else:
            # Local development - use asyncio
            asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        logger.info("Bot shutdown complete")