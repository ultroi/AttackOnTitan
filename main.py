import os
import logging
import asyncio
import uvicorn
from flask import Flask, request, Response
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from telegram import Update as TelegramUpdate

# Import handlers
from game.character_system import (
    show_character_selection, profile, show_character_profile,
    add_to_team, save_team, clear_team, manage_team,
    show_character_details, confirm_character_selection,
    create_character, show_team, back_to_selection,
    remove_from_team, start_character_selection
)
from game.explore import explore
from game.callback_handlers import button_callback
from game.shop_system import ShopSystem
from database.db import Database
from game.battle_system import handle_battle_action  # Import the battle action handler

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if TOKEN is None:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1])
ENV = os.getenv("ENV", "development")
ALLOWED_IPS = os.getenv("ALLOWED_IPS", "149.154.160.0/20").split(",")

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

async def create_application():
    """Create and configure the Telegram bot application."""
    global application
    if application is None:
        try:
            application = Application.builder().token(TOKEN).build()
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
            application.add_handler(CommandHandler("start", start_character_selection))
            application.add_handler(CommandHandler("profile", profile))
            application.add_handler(CommandHandler("explore", explore))

            async def shop_command(update: Update, context):
                try:
                    if not update.effective_user or not update.message:
                        if update.message:
                            await update.message.reply_text("User or message information not available.")
                        return
                    await init_user_data(update, context)
                    shop_system = context.bot_data["shop_system"]
                    await shop_system.check_daily_refresh()
                    message, reply_markup = await shop_system.show_shop(context, str(update.effective_user.id))
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
            application.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))  # Register battle action handler
            application.add_handler(CallbackQueryHandler(button_callback))

            async def buy_command(update: Update, context):
                try:
                    if not update.effective_user or not update.message:
                        if update.message:
                            await update.message.reply_text("User or message information not available.")
                        return
                    await init_user_data(update, context)
                    shop_system = context.bot_data["shop_system"]
                    args = context.args
                    if len(args) < 1:
                        await update.message.reply_text("Usage: /buy item_name [quantity]\nE.g., /buy gas 20 or /buy crystal 1")
                        return
                    item_name = args[0].lower()
                    quantity = 1
                    if len(args) > 1:
                        try:
                            quantity = int(args[1])
                        except ValueError:
                            await update.message.reply_text("Quantity must be a number. Usage: /buy item_name [quantity]")
                            return
                    result = await shop_system.purchase_item(context, str(update.effective_user.id), item_name, quantity)
                    await update.message.reply_text(result["message"])
                except (BadRequest, PyMongoError) as e:
                    user_id = update.effective_user.id if update.effective_user else "unknown"
                    logger.error(f"Error in buy_command for user {user_id}: {e}")
                    if update.message:
                        await update.message.reply_text(f"Error purchasing item: {str(e)}")

            application.add_handler(CommandHandler("buy", buy_command))

            async def error_handler(update: object, context):
                """Handle errors in the application."""
                logger.error(f"Update {update} caused error {context.error}")

                if isinstance(update, TelegramUpdate) and getattr(update, "effective_message", None):
                    try:
                        await update.effective_message.reply_text(
                            f"An error occurred: {str(context.error)}"
                        )
                    except BadRequest:
                        pass

            application.add_error_handler(error_handler)
            await application.initialize()
            logger.info("Bot application initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize application: {e}")
            raise
    return application

async def run_polling():
    """Run the bot in polling mode."""
    try:
        application = await create_application()
        logger.info("Starting bot in polling mode...")
        await application.run_polling()
    except RuntimeError as e:
        logger.error(f"Polling error: {e}")
        raise

# Webhook endpoints
@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handle incoming webhook updates."""
    logger.info("Received a webhook POST from Telegram")
    client_ip = request.remote_addr
    if not any(client_ip.startswith(ip.strip()) for ip in ALLOWED_IPS):
        logger.warning(f"Unauthorized IP: {client_ip}")
        return Response(status=403)

    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        logger.warning("Invalid secret token received")
        return Response(status=403)

    try:
        json_data = request.get_json()
        logger.info(f"Webhook payload: {json_data}")
        if not json_data:
            return Response(status=400)

        application = await create_application()
        update = Update.de_json(json_data, application.bot)
        await application.process_update(update)
        return Response(status=200)
    except (BadRequest, PyMongoError) as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=500)

@app.route('/')
def index():
    """Health check endpoint."""
    return {
        "status": "ok",
        "message": "Attack on Titan Bot is running"
    }

@app.route('/set_webhook', methods=['GET', 'POST'])
async def set_webhook():
    """Set webhook URL for the bot."""
    try:
        webhook_url = f"https://{request.host}/webhook"
        if not webhook_url.startswith("https://"):
            return {"status": "error", "message": "Webhook URL must use HTTPS"}, 400
        application = await create_application()
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=SECRET_TOKEN
        )
        return {
            "status": "success",
            "message": f"Webhook set to {webhook_url}"
        }
    except (BadRequest, PyMongoError) as e:
        logger.error(f"Failed to set webhook: {e}")
        return {"status": "error", "message": str(e)}, 500

async def main():
    """Main entry point for the bot."""
    try:
        if ENV == "production":
            logger.info("Starting in production mode with webhook")
            config = uvicorn.Config(
                app=app,
                host="0.0.0.0",
                port=int(os.environ.get('PORT', 5000)),
                log_level="info"
            )
            server = uvicorn.Server(config)
            await create_application()  # Ensure application is initialized
            await server.serve()
        else:
            logger.info("Starting in development mode with polling")
            await run_polling()
    except (RuntimeError, KeyboardInterrupt) as e:
        logger.info(f"Bot stopped: {e}")
    except Exception as e:
        logger.error(f"Bot crashed with error: {e}")
        raise

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if ENV != "production":
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        application = loop.run_until_complete(create_application())
        application.run_polling()
    else:
        asyncio.run(main())
