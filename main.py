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
from game.map_system import show_map, MAP_IMAGE_URL
from database.db import Database
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
from game.profile_system import (
    profile, show_character_profile,
    show_team, manage_team, add_to_team, remove_from_team, save_team, clear_team,
    show_inventory, view_weapons, view_gear, view_utilities, view_echo_shards
)
from game.battle_system import handle_battle_action, active_battles  # Import active_battles
from game.travel_system import travel_command, handle_travel_direction, handle_cancel_travel

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
                    # ShopSystem now handles daily refresh internally
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
            # Add only specific non-shop callback handlers
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
            application.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)") )
            application.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$") )
            application.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))
            # The catch-all handler must be last and should match all shop-related callbacks
            application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))
            # Absolute last fallback for anything else
            application.add_handler(CallbackQueryHandler(button_callback))

            async def error_handler(update: object, context):
                """Handle errors in the application."""
                logger.error(f"Update {update} caused error {context.error}")

                if isinstance(update, TelegramUpdate) and getattr(update, "effective_message", None):
                    try:
                        if update.effective_message:
                            await update.effective_message.reply_text(
                                "An error occurred while processing your request. Please try again later."
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

# Wrap start_character_selection to clear all temporary memory for the user
async def start_and_clear_memory(update: Update, context):
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
        except Exception:
            pass
    # Optionally clear other temp memory here
    # Call the original start handler
    await start_character_selection(update, context)

async def main():
    """Main entry point for the bot."""
    try:
        logger.info("Starting in webhook mode")
        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=int(os.environ.get('PORT', 5000)),
            log_level="info"
        )
        server = uvicorn.Server(config)
        await create_application()  # Ensure application is initialized
        await server.serve()
    except Exception as e:
        logger.error(f"Bot crashed with error: {e}")
        raise

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
