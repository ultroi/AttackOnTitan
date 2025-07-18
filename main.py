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
from database.db_instance import get_persistent_database
import signal
from utils.sudo_reset import reset_handler
from utils.ban_utils import ban_protected, ban_user, unban_user
from utils.mod_utils import promote_mod, demote_mod
from utils.maintenance import maintenance_protected, maintenance

# Import handlers
from game.start import (
    show_character_selection,
    show_character_details, confirm_character_selection,
    create_character, back_to_selection,
    start_character_selection
)
from game.add_resource_command import add_resource_command
from game.profile_system import (
    profile, char_detail,
    show_team, manage_team, add_to_team, remove_from_team, save_team, clear_team,
    show_inventory, view_weapons, view_gear, view_utilities, view_echo_shards, referral_info, 
    fill_gas, exit_profile, view_weapons_char, equip_weapon
)
from utils.fastapi_dashboard import include_dashboard_route
from utils.monitor import monitor_command
from utils.extra import buy_command, give_command
from game.explore import explore, close_keyboard
from game.callback_handlers import button_callback, handle_travel_decision
from game.shop_system import ShopSystem
from game.battle_system import handle_battle_action, active_battles
from game.travel_system import travel_command, handle_travel_direction, handle_cancel_travel
from game.captcha import button

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
# Register dashboard route for FastAPI

include_dashboard_route(app)

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

async def migrate_character_schema(db):
    # Example: Add equipped_weapon field if missing
    result = await db.characters.update_many(
        {"equipped_weapon": {"$exists": False}},
        {"$set": {"equipped_weapon": None}}
    )
    if result.modified_count > 0:
        print(f"[MIGRATION] Updated {result.modified_count} character(s) with equipped_weapon field.")

async def initialize_application():
    global application, app_initialized, global_db
    if application is None:
        application = Application.builder().token(TOKEN).build()
    try:
        # Initialize database and other services ONCE
        if global_db is None:
            # Use persistent DB connection for best performance
            motor_db = await get_persistent_database()
            global_db = Database()
            await global_db.init_db()  

        await migrate_character_schema(global_db)
        application.bot_data["db"] = global_db
        shop_system = ShopSystem()
        application.bot_data["shop_system"] = shop_system
        # Ensure shop_items is always available for battle system
        application.bot_data["shop_items"] = shop_system.shop_items
        register_handlers(application)


        
        async def error_handler(update: object, context):
            if isinstance(context.error, asyncio.CancelledError):
                logger.warning(f"Task cancelled for update {update}")
                return
            logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
            # Prepare detailed error message
            command = None
            if isinstance(update, TelegramUpdate):
                if hasattr(update, "message") and update.message is not None and hasattr(update.message, "text") and update.message.text:
                    command = update.message.text
                elif hasattr(update, "callback_query") and update.callback_query is not None and hasattr(update.callback_query, "data") and update.callback_query.data:
                    command = f"Callback: {update.callback_query.data}"
            user_id = getattr(update, "effective_user", None)
            user_id_str = getattr(user_id, "id", "N/A") if user_id is not None else "N/A"
            error_text = (
                f"⚠️ <b>Error Occurred</b>\n"
                f"<b>Command:</b> <code>{command}</code>\n"
                f"<b>User:</b> <code>{user_id_str}</code>\n"
                f"<b>Error:</b>\n<pre>{repr(context.error)}</pre>\n"
            )
            # Send error to group
            try:
                await context.bot.send_message(
                    chat_id=-1002463105932,
                    text=error_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to send error to group: {e}")
            # Notify user
            if isinstance(update, TelegramUpdate) and getattr(update, "effective_message", None):
                try:
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            "An error occurred! Please report to mods."
                        )
                except BadRequest:
                    pass

        application.add_error_handler(error_handler)
        if not app_initialized:
            await application.initialize()
            await application.start()
            app_initialized = True
            logger.info("Bot application initialized and started successfully")
            # Get latest commit message
            commit_message = None
            try:
                import subprocess
                commit_message = subprocess.check_output([
                    "git", "log", "-1", "--pretty=%B"
                ], cwd=os.path.dirname(os.path.abspath(__file__)), encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"Could not fetch commit message: {e}")
            # Send startup message to group
            try:
                msg = "<b>✅ Bot Started!</b>"
                if commit_message:
                    msg += f"\n\n<b>Latest Commit:</b>\n<code>{commit_message}</code>"
                await application.bot.send_message(chat_id=-1002463105932, text=msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send startup message: {e}")
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
            # The only call needed here is shutdown(), which cleans up resources.
            # run_polling() already handles stopping the updater and the application tasks.
            await application.shutdown()
            logger.info("Shutdown completed successfully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            app_initialized = False

# @app.post("/webhook")
# async def webhook(request: Request):
#     client_ip = request.client.host
#     logger.info(f"Received webhook request from IP: {client_ip}")
#
#     if not is_ip_allowed(client_ip):
#         logger.warning(f"Unauthorized IP blocked: {client_ip}")
#         return Response(status_code=403)
#
#     token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
#     if token != SECRET_TOKEN:
#         logger.warning("Invalid secret token received")
#         return Response(status_code=403)
#
#     try:
#         json_data = await request.json()
#         if not json_data:
#             logger.warning("Empty webhook payload received")
#             return Response(status_code=400)
#
#         logger.debug(f"Webhook payload: {json_data}")
#
#         # Use global application and db
#         if not app_initialized:
#             await initialize_application()
#         update = Update.de_json(json_data, application.bot)
#         await application.process_update(update)
#         return Response(status_code=200)
#     except Exception as e:
#         logger.error(f"Webhook processing error: {e}")
#         return Response(status_code=500)

@app.api_route("/", methods=["GET", "POST", "HEAD"])
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


@app.get("/monitor")
async def monitor_dashboard():
    try:
        from utils.monitor import resource_monitor
        live_players = resource_monitor.get_live_player_stats()
        return {"live_players": live_players}
    except Exception as e:
        return {"error": str(e)}
    


def register_handlers(app_instance):
    # Command handlers
    app_instance.add_handler(CommandHandler("start", start_character_selection))
    app_instance.add_handler(CommandHandler("inv", profile))
    app_instance.add_handler(CommandHandler("explore", explore))
    app_instance.add_handler(CommandHandler("close", close_keyboard))
    app_instance.add_handler(CommandHandler("map", show_map))
    app_instance.add_handler(CommandHandler("travel", travel_command))
    app_instance.add_handler(CommandHandler("shop", shop_command))
    app_instance.add_handler(CommandHandler("status", profile))
    app_instance.add_handler(CommandHandler("buy", buy_command))
    app_instance.add_handler(CommandHandler("referral", referral_info))
    app_instance.add_handler(CommandHandler("monitor", monitor_command))
    app_instance.add_handler(CommandHandler("nuke", reset_handler))
    app_instance.add_handler(CommandHandler("char", char_detail))
    app_instance.add_handler(CommandHandler("bfb", ban_user))
    app_instance.add_handler(CommandHandler("ubfb", unban_user))
    app_instance.add_handler(CommandHandler("give", give_command))
    app_instance.add_handler(CommandHandler("mod", promote_mod))
    app_instance.add_handler(CommandHandler("demod", demote_mod))
    app_instance.add_handler(CommandHandler("mm", maintenance))
    app_instance.add_handler(CommandHandler("add", add_resource_command))

    # Character selection and team management
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

    # Profile and inventory
    app_instance.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
    app_instance.add_handler(CallbackQueryHandler(show_inventory, pattern="^show_inventory$"))
    app_instance.add_handler(CallbackQueryHandler(view_weapons, pattern="^view_weapons$"))
    app_instance.add_handler(CallbackQueryHandler(view_gear, pattern="^view_gear$"))
    app_instance.add_handler(CallbackQueryHandler(view_utilities, pattern="^view_utilities$"))
    app_instance.add_handler(CallbackQueryHandler(view_echo_shards, pattern="^view_echo_shards$"))

    # Character detail handlers (new)
    app_instance.add_handler(CallbackQueryHandler(fill_gas, pattern=r"^fill_gas_"))
    app_instance.add_handler(CallbackQueryHandler(view_weapons_char, pattern=r"^view_weapons_"))
    app_instance.add_handler(CallbackQueryHandler(equip_weapon, pattern=r"^equip_weapon_"))
    app_instance.add_handler(CallbackQueryHandler(char_detail, pattern=r"^char_detail_"))
    app_instance.add_handler(CallbackQueryHandler(exit_profile, pattern=r"^exit_profile$"))

    # Battle and travel
    app_instance.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))
    app_instance.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)"))
    app_instance.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$"))
    app_instance.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))

    # Shop and purchases
    app_instance.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))

    # Generic button handler (should be last)
    app_instance.add_handler(CallbackQueryHandler(button, pattern=r"^[A-Z0-9]+$"))

    # Fallback handler (must be absolutely last)
    app_instance.add_handler(CallbackQueryHandler(button_callback))

# Shop command handler for /shop
@maintenance_protected
@ban_protected
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id) if update.effective_user else None
    try:
        shop_system = context.bot_data.get("shop_system")
        if not shop_system:
            if update.message:
                await update.message.reply_text("Shop system not initialized. Please try again later.")
            return
        # Always set shop_items in context.bot_data for consistency
        context.bot_data["shop_items"] = shop_system.shop_items
        text, reply_markup = await shop_system.show_shop(context, user_id)
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in shop_command: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while showing the shop.")



async def main():
    """Initializes and runs the bot application."""
    
    # Initialize the application
    bot_app = await initialize_application()
    
    # Start fetching updates from Telegram
    await bot_app.updater.start_polling(drop_pending_updates=True)
    
    logger.info("Bot is now running and polling for updates...")

    # The main loop to keep the bot alive
    try:
        # This will run forever until a signal like Ctrl+C is received
        while True:
            await asyncio.sleep(3600)
            
    except (KeyboardInterrupt, SystemExit):
        logger.info("Received exit signal, shutting down gracefully...")
        
    finally:
        # Graceful shutdown sequence
        if bot_app.updater and bot_app.updater.is_running:
            await bot_app.updater.stop()
        if bot_app.running:
            await bot_app.stop()
        await bot_app.shutdown()
        logger.info("Bot has been shut down.")

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())