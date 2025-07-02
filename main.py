import os
import logging
import asyncio
from flask import Flask, request, Response
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application
from telegram.ext import CommandHandler, CallbackQueryHandler

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1] if TOKEN else "")

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

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

async def setup_bot():
    """Setup the Telegram bot with all handlers"""
    global application
    
    if application is None:
        # Create application
        application = Application.builder().token(TOKEN).build()
        
        # Import and add all your handlers here
        from game.character_system import (
            show_character_selection, profile, show_character_profile,
            add_to_team, save_team, clear_team, manage_team,
            show_character_details, confirm_character_selection,
            create_character, show_team, back_to_selection,
        )
        from game.explore import explore
        from game.callback_handlers import button_callback
        from game.shop_system import ShopSystem
        
        # Add command handlers
        application.add_handler(CommandHandler("start", show_character_selection))
        application.add_handler(CommandHandler("profile", profile))
        application.add_handler(CommandHandler("explore", explore))
        
        # Shop command with proper async handling
        async def shop_command(update: Update, context):
            if not update.effective_user or not update.message:
                return
            shop_system = ShopSystem()
            await shop_system.check_daily_refresh()
            message, reply_markup = await shop_system.show_shop(update.effective_user.id)
            await update.message.reply_text(
                text=message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        application.add_handler(CommandHandler("shop", shop_command))
        
        # Add callback handlers
        application.add_handler(CallbackQueryHandler(button_callback))
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
        application.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
        
        # Error handler
        async def error_handler(update: object, context):
            logger.error(f"Update {update} caused error {context.error}")
        
        application.add_error_handler(error_handler)
        
        # Initialize application
        await application.initialize()
        logger.info("Bot application initialized successfully")
    
    return application

@app.route('/webhook', methods=['POST'])
def webhook():
    logger.info("Received a webhook POST from Telegram")
    # Verify secret token
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        logger.warning("Invalid secret token received")
        return Response(status=403)
    
    try:
        # Get update data
        json_data = request.get_json()
        if not json_data:
            return Response(status=400)
        
        # Process update asynchronously
        async def process_update():
            try:
                app_instance = await setup_bot()
                update = Update.de_json(json_data, app_instance.bot)
                await app_instance.process_update(update)
            except Exception as e:
                logger.error(f"Error processing update: {e}")
        
        # Run the async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(process_update())
        finally:
            loop.close()
        
        return Response(status=200)
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=500)

@app.route('/')
def index():
    """Health check endpoint"""
    return {
        "status": "ok",
        "message": "Attack on Titan Bot is running on Vercel"
    }

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook():
    """Set webhook URL for the bot"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app_instance = loop.run_until_complete(setup_bot())
        webhook_url = f"https://{request.host}/webhook"
        loop.run_until_complete(app_instance.bot.set_webhook(
            url=webhook_url,
            secret_token=SECRET_TOKEN
        ))
        return {
            "status": "success",
            "message": f"Webhook set to {webhook_url}"
        }
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return {"status": "error", "message": str(e)}, 500

# For local/production deployment
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Auto-set webhook on startup
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app_instance = loop.run_until_complete(setup_bot())
        public_url = os.environ.get("PUBLIC_URL")
        if public_url:
            webhook_url = f"{public_url}/webhook"
        else:
            webhook_url = f"http://localhost:{port}/webhook"
        loop.run_until_complete(app_instance.bot.set_webhook(
            url=webhook_url,
            secret_token=SECRET_TOKEN
        ))
        logger.info(f"Webhook auto-set to {webhook_url}")
    except Exception as e:
        logger.error(f"Failed to auto-set webhook: {e}")
    app.run(debug=False, port=port, host="0.0.0.0")
