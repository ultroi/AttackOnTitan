import os
import logging
import asyncio
import uvicorn
import time
from datetime import datetime
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ChatMemberHandler, MessageHandler, filters
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from telegram import Update as TelegramUpdate
from ipaddress import ip_network, ip_address
from game.map_system import show_map, MAP_IMAGE_URL
from database.db import Database
from database.db_instance import get_persistent_database
import signal
# Scheduler import
from game.scheduler import start_scheduler
from utils.sudo_reset import reset_handler
from utils.ban_utils import ban_protected, ban_user, unban_user
from utils.mod_utils import promote_mod, demote_mod
from utils.maintenance import maintenance_protected, maintenance
from utils.disable_mode import disable_command, enable_command, disable_protected
from utils.diagnostics import diagnostic_db_command, check_group_record

# Import database models
from database.models import Character, Player
from pymongo import UpdateOne
from typing import List, Dict

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
    show_team, manage_team, add_to_team, remove_from_team, save_team, clear_team, back_from_manage_team,
    show_inventory, view_weapons, view_gear, view_military, view_utilities, view_echo_shards, view_miscellaneous, referral_info,
    fill_gas, exit_profile, view_weapons_char, equip_weapon, char_detail_callback, view_abilities,
    show_characters
)
from game.bank_command import handle_bank_command, handle_deposit_command, handle_withdrawal_command, handle_open_bank_callback
from utils.fastapi_dashboard import include_dashboard_route
from utils.group import group_update_handler
from utils.monitor import monitor_command
from utils.extra import buy_command, give_command
from game.explore import explore, close_keyboard, open_keyboard
from game.callback_handlers import button_callback, handle_travel_decision
from game.shop_system import ShopSystem
from game.battle_system import handle_battle_action, active_battles
from utils.scheduled_tasks import start_scheduled_tasks
from game.travel_system import travel_command, handle_travel_direction, handle_cancel_travel
from game.captcha import button
from game.pvp_system import pvp_command, pvp_callback_handler
from game.tax_command import tax_status_command, force_tax_check_command
from game.stats_command import stats_command, start_stats_scheduler
from game.missions_command import missions_command, missions_callback_handler, reset_mission_command, remission_command, reset_mission_callback_handler
from game.dealer_system import handle_dealer_callback

# Spin System
from game.spin_system import spin_command, spin_callback_handler

# Item Usage
from game.item_usage import use_command

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if TOKEN is None:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", TOKEN.split(":")[1])
ENV = os.getenv("ENV", "development")

# Updated ALLOWED_IPS with proper CIDR ranges and specific IPs
ALLOWED_IPS = [
    "91.108.4.0/22",      
    "91.108.5.82",        
    "91.108.56.0/22",     
    "149.154.160.0/20",   
    "95.161.64.0/20",     
    "35.197.0.0/16",      
    "35.235.0.0/16",      
    "35.236.0.0/16",      
    "35.237.0.0/16",      
    "34.0.0.0/8",         
    "0.0.0.0/0"           
]

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
)

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

async def migrate_schema(db):
    # --- Character migration ---
    char_fields = Character.model_fields if hasattr(Character, "model_fields") else Character.__annotations__
    from datetime import datetime, timezone
    from database.models import CharacterStats
    def get_default_value(field_type, field_name=None):
        # Special handling for known fields
        if field_name == "stats":
            return CharacterStats()
        if field_name in ("active_abilities", "passive_abilities", "ultimate_abilities"):
            return []
        if field_name == "unlocked_abilities":
            return {}
        if field_name in ("created_at", "updated_at"):
            return datetime.now(timezone.utc)
        origin = getattr(field_type, "__origin__", None)
        if field_type is list or field_type is List or origin is list:
            return []
        if field_type is dict or field_type is Dict or origin is dict:
            return {}
        if field_type is str:
            return ""
        if field_type is int:
            return 0
        if field_type is float:
            return 0.0
        if field_type is bool:
            return False
        return None

    char_defaults = {}
    for k, v in char_fields.items():
        default = getattr(v, "default", None)
        if default is None or str(default).startswith("PydanticUndefined"):
            field_type = getattr(v, "annotation", None) or v
            char_defaults[k] = get_default_value(field_type, k)
        else:
            char_defaults[k] = default
    char_default = Character(**char_defaults)
    char_dict = char_default.dict() if hasattr(char_default, "dict") else char_default.__dict__

    char_cursor = db.characters.find({})
    char_updates = []
    async for doc in char_cursor:
        update_data = {}
        for field, default in char_dict.items():
            if field not in doc:
                update_data[field] = default
        for field in doc:
            if field not in char_dict:
                update_data[field] = None
        if update_data:
            update_op = {
                "$set": {k: v for k, v in update_data.items() if v is not None},
                "$unset": {k: "" for k, v in update_data.items() if v is None and k != "_id"}
            }
            char_updates.append(UpdateOne({"_id": doc["_id"]}, update_op))
    if char_updates:
        result = await db.characters.bulk_write(char_updates)
        logger.info(f"Migrated {result.modified_count} character documents to latest schema.")
    else:
        logger.info("No character documents needed migration.")

    # --- Player migration ---
    player_fields = Player.model_fields if hasattr(Player, "model_fields") else Player.__annotations__
    player_defaults = {}
    for k, v in player_fields.items():
        default = getattr(v, "default", None)
        if default is None or str(default).startswith("PydanticUndefined"):
            field_type = getattr(v, "annotation", None) or v
            player_defaults[k] = get_default_value(field_type, k)
        else:
            player_defaults[k] = default
    player_default = Player(**player_defaults)
    player_dict = player_default.dict() if hasattr(player_default, "dict") else player_default.__dict__

    player_cursor = db.players.find({})
    player_updates = []
    async for doc in player_cursor:
        update_data = {}
        for field, default in player_dict.items():
            if field not in doc:
                update_data[field] = default
        for field in doc:
            if field not in player_dict:
                update_data[field] = None
        if update_data:
            update_op = {
                "$set": {k: v for k, v in update_data.items() if v is not None},
                "$unset": {k: "" for k, v in update_data.items() if v is None and k != "_id"}
            }
            player_updates.append(UpdateOne({"_id": doc["_id"]}, update_op))
    if player_updates:
        result = await db.players.bulk_write(player_updates)
        logger.info(f"Migrated {result.modified_count} player documents to latest schema.")
    else:
        logger.info("No player documents needed migration.")



async def initialize_application():
    global application, app_initialized, global_db
    if application is None:
        if not TOKEN:
            logger.error("TELEGRAM_TOKEN is not set or is None")
            return None
        application = Application.builder().token(TOKEN).build()
    try:
        if global_db is None:
            motor_db = await get_persistent_database()
            global_db = Database()
            await global_db.init_db(motor_db)  
            await migrate_schema(global_db)
            
            # Apply battle system fixes
            from game.battle_fix import apply_battle_fixes
            fixes_applied = await apply_battle_fixes(global_db)
            if fixes_applied:
                logger.info("Applied battle system fixes")
            
        application.bot_data["db"] = global_db
        shop_system = ShopSystem()
        application.bot_data["shop_system"] = shop_system
        application.bot_data["shop_items"] = {**shop_system.shop_items, **shop_system.hidden_items}
        
        # Initialize spin system
        from game.spin_system import SpinSystem
        application.bot_data["spin_system"] = SpinSystem()
        
        register_handlers(application)

        
        async def error_handler(update: object, context):
            if isinstance(context.error, asyncio.CancelledError):
                logger.warning(f"Task cancelled for update {update}")
                return
                
            # Special handling for rate limiting errors
            from telegram.error import RetryAfter
            if isinstance(context.error, RetryAfter):
                retry_seconds = context.error.retry_after
                logger.warning(f"Rate limited. Retry after {retry_seconds} seconds")
                
                # For rate limit errors, only notify the user if possible, but don't send to error group
                if isinstance(update, TelegramUpdate) and getattr(update, "effective_message", None):
                    try:
                        if update.effective_message:
                            await asyncio.sleep(min(retry_seconds, 5))  
                            await update.effective_message.reply_text(
                                f"Bot is being rate limited. Please try again in {int(retry_seconds)} seconds."
                            )
                    except Exception as e:
                        logger.error(f"Failed to notify user about rate limit: {e}")
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
            # Stop receiving updates first
            if hasattr(application, 'updater') and application.updater is not None:
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
    client_ip = request.client.host if request.client else "unknown"
    logger.info(f"Received webhook request from IP: {client_ip}")

    # Log all headers for debugging
    headers = dict(request.headers)
    logger.info(f"Webhook headers: {headers}")

    # For now, skip IP check if we're on Render
    if client_ip != "unknown" and ENV != "development" and not is_ip_allowed(client_ip):
        logger.warning(f"Unauthorized IP blocked: {client_ip}")
        return Response(status_code=403)

    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        logger.warning(f"Invalid secret token received: {token}")
        return Response(status_code=403)

    try:
        json_data = await request.json()
        if not json_data:
            logger.warning("Empty webhook payload received")
            return Response(status_code=400)

        logger.info(f"Webhook payload: {json_data}")

        # Use global application and db
        if not app_initialized:
            logger.info("Initializing application for webhook")
            app_instance = await initialize_application()
        else:
            app_instance = application
            
        if not app_instance:
            logger.error("Application not initialized, cannot process webhook")
            return Response(status_code=500)
            
        update = Update.de_json(json_data, app_instance.bot)
        if update:
            logger.info(f"Processing update ID: {update.update_id}")
            await app_instance.process_update(update)
            logger.info(f"Successfully processed update ID: {update.update_id}")
        else:
            logger.warning("Failed to parse update from webhook data")
        
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        return Response(status_code=500)

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
            allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"]
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
async def monitor_dashboard(request: Request):
    try:
        # Get session cookie
        from utils.fastapi_dashboard import verify_session, SESSION_COOKIE, log_dashboard_access, active_sessions
        
        # Get client IP
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host
        
        # Default to admin access - for development
        user_id = 123456789  # Default admin user ID
        
        session = request.cookies.get(SESSION_COOKIE)
        if session:
            # Verify session using in-memory active_sessions and database
            if session in active_sessions:
                session_data = active_sessions[session]
                current_time = time.time()
                
                # Check if session is expired
                if session_data["expiry"] < current_time:
                    del active_sessions[session]
                    # Also remove from database asynchronously
                    from utils.fastapi_dashboard import delete_dashboard_session
                    asyncio.create_task(delete_dashboard_session(session))
                    user_id = None
                else:
                    # Extend session and update last activity
                    session_data["expiry"] = current_time + 3600
                    session_data["last_activity"] = current_time
                    user_id = session_data["user_id"]
                    
                    # Update database asynchronously
                    from utils.fastapi_dashboard import save_dashboard_session
                    asyncio.create_task(save_dashboard_session(
                        session,
                        user_id,
                        session_data.get("ip_address", client_ip),
                        session_data["expiry"],
                        session_data.get("created_at", current_time),
                        current_time
                    ))
            
            # Use default admin access if session verification failed
            if not user_id:
                # In development mode, allow access without session
                # For production, you might want to return an error instead
                user_id = 123456789
                
                # Log unauthorized access attempt with debug info
                log_dashboard_access(
                    user_id=user_id,  # Using default admin ID
                    action="api_access_with_default",
                    ip_address=client_ip,
                    details={"reason": "session_not_found_or_expired", "using_default_access": True}
                )
        else:
            # No session provided, but still allow access with default admin ID
            # Log the access with warning
            log_dashboard_access(
                user_id=user_id,  # Using default admin ID
                action="api_access_with_default",
                ip_address=client_ip,
                details={"reason": "no_session", "using_default_access": True}
            )
            
        # Log successful API access
        log_dashboard_access(
            user_id=user_id,
            action="api_access",
            ip_address=client_ip,
            details={"endpoint": "/monitor"}
        )
        
        # Get active sessions info
        active_session_count = len(active_sessions)
        formatted_sessions = []
        current_time = time.time()
        
        # Format session data with error handling
        try:
            for sess_id, sess_data in active_sessions.items():
                # Only include minimal info for security
                formatted_sessions.append({
                    "user_id": sess_data.get("user_id", "unknown"),
                    "ip": sess_data.get("ip_address", "unknown"),
                    "created": datetime.fromtimestamp(sess_data.get("created_at", current_time)).strftime("%Y-%m-%d %H:%M:%S"),
                    "expires_in": int(sess_data.get("expiry", 0) - current_time),
                    "last_active": datetime.fromtimestamp(sess_data.get("last_activity", current_time)).strftime("%Y-%m-%d %H:%M:%S")
                })
        except Exception as e:
            logger.error(f"Error formatting session data: {e}")
            # Continue with empty sessions list if there's an error
            formatted_sessions = []
            
        # Get live player stats with error handling
        try:
            # Import here to avoid circular imports
            from utils.monitor import resource_monitor
            logger.info("Monitor dashboard API called by user ID: " + str(user_id))
            
            live_players = resource_monitor.get_live_player_stats()
            player_count = len(live_players.get('players', []))
            logger.info(f"Got live player stats: {player_count} players")
            
            # Get memory and CPU usage
            memory_usage = resource_monitor.get_memory_usage()
            system_load = resource_monitor.get_system_load()
            
        except Exception as e:
            logger.error(f"Error getting live player stats: {e}")
            # Provide fallback data structure
            live_players = {
                "total_active": 0,
                "in_battle": 0,
                "exploring": 0,
                "ended": 0,
                "players": []
            }
            memory_usage = {
                "rss_mb": 0,
                "vms_mb": 0,
                "percent": 0
            }
            system_load = {
                "cpu_percent": 0,
                "load_average": [0, 0, 0]
            }
            
        # Build and return response
        return {
            "live_players": live_players, 
            "status": "success",
            "user_id": user_id,  # Return user ID to frontend for verification
            "active_sessions_count": active_session_count,
            "active_sessions": formatted_sessions,
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "memory_usage": memory_usage,  # Add memory usage
            "system_load": system_load,    # Add CPU usage
            "version": "1.0.2"  # Update version for tracking changes
        }
    except Exception as e:
        logger.error(f"Error in /monitor endpoint: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e), "details": traceback.format_exc(), "status": "error"}
    





def register_handlers(app_instance):

    # Command handlers


    # User commands (protected only by disable)
    app_instance.add_handler(CommandHandler("start", disable_protected(start_character_selection)))
    app_instance.add_handler(CommandHandler("inv", disable_protected(profile)))
    app_instance.add_handler(CommandHandler("explore", disable_protected(explore)))
    app_instance.add_handler(CommandHandler("open", disable_protected(open_keyboard)))
    app_instance.add_handler(CommandHandler("close", disable_protected(close_keyboard)))
    app_instance.add_handler(CommandHandler("map", disable_protected(show_map)))
    app_instance.add_handler(CommandHandler("travel", disable_protected(travel_command)))
    app_instance.add_handler(CommandHandler("shop", disable_protected(shop_command)))
    app_instance.add_handler(CommandHandler("status", disable_protected(profile)))
    app_instance.add_handler(CommandHandler("buy", disable_protected(buy_command)))
    app_instance.add_handler(CommandHandler("referral", disable_protected(referral_info)))
    app_instance.add_handler(CommandHandler("chars", disable_protected(show_characters)))
    app_instance.add_handler(CommandHandler("char", disable_protected(char_detail)))
    app_instance.add_handler(CommandHandler("give", disable_protected(give_command)))
    app_instance.add_handler(CommandHandler("add", disable_protected(add_resource_command)))
    app_instance.add_handler(CommandHandler("remove", disable_protected(add_resource_command)))
    app_instance.add_handler(CommandHandler("stats", disable_protected(stats_command)))
    app_instance.add_handler(CommandHandler("missions", disable_protected(missions_command)))
    app_instance.add_handler(CommandHandler("resetmission", disable_protected(reset_mission_command)))
    app_instance.add_handler(CommandHandler("remission", disable_protected(remission_command)))
    app_instance.add_handler(CallbackQueryHandler(missions_callback_handler, pattern=r"^mission_"))
    app_instance.add_handler(CallbackQueryHandler(reset_mission_callback_handler, pattern=r"^reset_"))

    # Mod/owner commands (not protected by disable)
    app_instance.add_handler(CommandHandler("monitor", monitor_command))
    app_instance.add_handler(CommandHandler("nuke", reset_handler))
    app_instance.add_handler(CommandHandler("bfb", ban_user))
    app_instance.add_handler(CommandHandler("ubfb", unban_user))
    app_instance.add_handler(CommandHandler("mod", promote_mod))
    app_instance.add_handler(CommandHandler("demod", demote_mod))
    app_instance.add_handler(CommandHandler("mm", maintenance))
    app_instance.add_handler(CommandHandler("disablecmd", disable_command))
    app_instance.add_handler(CommandHandler("enablecmd", enable_command))
    app_instance.add_handler(CommandHandler("dbdiag", diagnostic_db_command))
    app_instance.add_handler(CommandHandler("checkgroup", check_group_record))
    app_instance.add_handler(CommandHandler("taxstatus", tax_status_command))
    app_instance.add_handler(CommandHandler("forcetax", force_tax_check_command))

    # Bank system handlers
    app_instance.add_handler(CommandHandler("bank", disable_protected(handle_bank_command)))
    app_instance.add_handler(CommandHandler("deposit", disable_protected(handle_deposit_command)))
    app_instance.add_handler(CommandHandler("withdraw", disable_protected(handle_withdrawal_command)))
    app_instance.add_handler(CallbackQueryHandler(handle_open_bank_callback, pattern="^bank_open_account$"))
    
    # PVP system handlers
    app_instance.add_handler(CommandHandler("pvp", disable_protected(pvp_command)))
    app_instance.add_handler(CallbackQueryHandler(pvp_callback_handler, pattern="^pvp_"))
    
    # Dealer system handlers

    app_instance.add_handler(CallbackQueryHandler(handle_dealer_callback, pattern="^dealer_"))

    # Spin system handlers
    app_instance.add_handler(CommandHandler("spin", disable_protected(spin_command)))
    app_instance.add_handler(CallbackQueryHandler(spin_callback_handler, pattern="^spin_"))
    
    # Item usage handlers
    app_instance.add_handler(CommandHandler("use", disable_protected(use_command)))

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
    app_instance.add_handler(CallbackQueryHandler(back_from_manage_team, pattern="^back_from_manage_team$"))

    # Profile and inventory
    app_instance.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
    app_instance.add_handler(CallbackQueryHandler(show_inventory, pattern="^show_inventory$"))
    app_instance.add_handler(CallbackQueryHandler(view_weapons, pattern="^view_weapons$"))
    app_instance.add_handler(CallbackQueryHandler(view_gear, pattern="^view_gear$"))
    app_instance.add_handler(CallbackQueryHandler(view_military, pattern="^view_military$"))
    app_instance.add_handler(CallbackQueryHandler(view_utilities, pattern="^view_utilities$"))
    app_instance.add_handler(CallbackQueryHandler(view_miscellaneous, pattern="^view_miscellaneous$"))
    app_instance.add_handler(CallbackQueryHandler(view_echo_shards, pattern="^view_echo_shards$"))

    # Character detail handlers (new)
    app_instance.add_handler(CallbackQueryHandler(fill_gas, pattern=r"^fill_gas_"))
    app_instance.add_handler(CallbackQueryHandler(view_weapons_char, pattern=r"^view_weapons_"))
    app_instance.add_handler(CallbackQueryHandler(equip_weapon, pattern=r"^equip_weapon_"))
    app_instance.add_handler(CallbackQueryHandler(view_abilities, pattern=r"^view_abilities_"))
    app_instance.add_handler(CallbackQueryHandler(char_detail_callback, pattern=r"^char_detail_"))
    app_instance.add_handler(CallbackQueryHandler(exit_profile, pattern=r"^exit_profile$"))

    # Battle and travel
    app_instance.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))
    app_instance.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)"))
    app_instance.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$"))
    app_instance.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))

    # Shop and purchases
    app_instance.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))

    # Group membership handler
    app_instance.add_handler(ChatMemberHandler(group_update_handler, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER | ChatMemberHandler.CHAT_MEMBER))
    app_instance.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, group_update_handler))

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
        # Always set shop_items and hidden_items in context.bot_data for consistency
        context.bot_data["shop_items"] = {**shop_system.shop_items, **shop_system.hidden_items}
        text, reply_markup = await shop_system.show_shop(context, user_id)
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in shop_command: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while showing the shop.")



async def main():
    try:
        await asyncio.sleep(2)
        logger.info(f"Starting bot in {ENV} environment")
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)
        
        # Initialize application before starting server
        app_instance = await initialize_application()

        # Start the midnight tax scheduler with bot instance
        start_scheduler(app_instance.bot)
        
        # Start the stats scheduler for daily/weekly resets
        await start_stats_scheduler(global_db)

        # Set webhook for Telegram
        if ENV != "development" and app_instance:
            webhook_url = "https://attackontitangamebot.onrender.com/webhook"
            logger.info(f"Setting webhook to: {webhook_url}")
            try:
                await app_instance.bot.set_webhook(
                    url=webhook_url,
                    secret_token=SECRET_TOKEN,
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"]
                )
                webhook_info = await app_instance.bot.get_webhook_info()
                logger.info(f"Webhook info: {webhook_info}")
            except Exception as e:
                logger.error(f"Failed to set webhook: {e}", exc_info=True)

        # Configure and start server
        port = int(os.environ.get('PORT', 10000))
        logger.info(f"Starting server on port {port}")
        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=port,
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