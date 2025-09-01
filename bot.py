"""
Attack on Titan Bot - Test Runner

This file is designed for testing the bot locally before deploying to production.

Features:
- Loads bot token from .env file (TEST_BOT_TOKEN)
- Uses test database to prevent changes to production data
- Actually runs the bot with test token for real testing
- Data safety: Uses test database

Usage:
1. Set TEST_BOT_TOKEN in .env file
2. Set TEST_MODE=true in .env (default: true)
3. Run: python bot.py
4. Bot will start and respond to commands in Telegram

Environment Variables:
- TEST_BOT_TOKEN: Your test bot token
- TEST_MODE: true/false (enables test database)
- DEBUG: true/false (enables debug logging)
"""

import os
import logging
import asyncio
import signal
import sys
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ChatMemberHandler, MessageHandler, filters
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from telegram import Update as TelegramUpdate
from database.db import Database
from database.db_instance import get_persistent_database
from game.scheduler import start_scheduler
from utils.sudo_reset import reset_handler
from utils.ban_utils import ban_protected, ban_user, unban_user
from utils.mod_utils import promote_mod, demote_mod
from utils.maintenance import maintenance_protected, maintenance
from utils.disable_mode import disable_command, enable_command, disable_protected
from utils.diagnostics import diagnostic_db_command, check_group_record
from utils.group import group_update_handler
from utils.monitor import monitor_command
from utils.extra import buy_command, give_command
from game.explore import explore, close_keyboard, reset_verify, open_keyboard
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
    show_inventory, view_weapons, view_gear, view_military, view_utilities, view_echo_shards, referral_info,
    fill_gas, exit_profile, view_weapons_char, equip_weapon, char_detail_callback, view_abilities
)
from game.bank_command import handle_bank_command, handle_deposit_command, handle_withdrawal_command, handle_open_bank_callback
from database.models import Character, Player
from pymongo import UpdateOne
from typing import List, Dict
import motor.motor_asyncio

# Load environment variables
load_dotenv()

# Get environment variables
ENV = os.getenv("ENV", "development")
USE_POLLING = os.getenv("USE_POLLING", "true").lower() == "true"
TEST_BOT_TOKEN = os.getenv("TEST_BOT_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or TEST_BOT_TOKEN
MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "attackontitan")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Test mode configuration - ensures data safety
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
if TEST_MODE:
    # Use test database to prevent changes to production data
    DB_NAME = f"{DB_NAME}_test"


# Validate required environment variables
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN or TEST_BOT_TOKEN environment variable is not set")

if not MONGODB_URI:
    raise ValueError("MONGODB_URI environment variable is not set")

# Configure logging based on DEBUG setting
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=log_level
)
logger = logging.getLogger(__name__)

# Log environment info
logger.info(f"Environment: {ENV}")
logger.info(f"Using polling: {USE_POLLING}")
logger.info(f"Database: {DB_NAME}")
logger.info(f"Debug mode: {DEBUG}")
logger.info(f"Test mode: {TEST_MODE}")
logger.info("✅ Environment variables loaded successfully from .env file")

# Global variables
application = None
app_initialized = False
global_db = None

# Initialize database and services
async def initialize_bot():
    global db, shop_system, application

    logger.info("Initializing bot environment...")

    # Set environment variables for database
    os.environ["MONGODB_URI"] = MONGODB_URI
    os.environ["DB_NAME"] = DB_NAME

    # Initialize database
    motor_db = await get_persistent_database()
    db = Database()
    await db.init_db(motor_db)

    # Initialize shop system
    shop_system = ShopSystem()

    logger.info("Bot environment initialized successfully!")
    logger.info(f"Connected to database: {DB_NAME}")
    logger.info(f"Shop system loaded with {len(shop_system.shop_items)} items")

# Setup bot handlers
def setup_handlers(application):
    """Setup all command and callback handlers"""

    # User commands
    application.add_handler(CommandHandler("start", disable_protected(start_character_selection)))
    application.add_handler(CommandHandler("inv", disable_protected(profile)))
    application.add_handler(CommandHandler("explore", disable_protected(explore)))
    application.add_handler(CommandHandler("open", disable_protected(open_keyboard)))
    application.add_handler(CommandHandler("close", disable_protected(close_keyboard)))
    application.add_handler(CommandHandler("resetverify", disable_protected(reset_verify)))
    application.add_handler(CommandHandler("map", disable_protected(lambda u, c: None)))  # Placeholder
    application.add_handler(CommandHandler("travel", disable_protected(travel_command)))
    application.add_handler(CommandHandler("shop", maintenance_protected(ban_protected(lambda u, c: None))))  # Placeholder
    application.add_handler(CommandHandler("status", disable_protected(profile)))
    application.add_handler(CommandHandler("buy", ban_protected(buy_command)))
    application.add_handler(CommandHandler("referral", disable_protected(referral_info)))
    application.add_handler(CommandHandler("char", disable_protected(char_detail)))
    application.add_handler(CommandHandler("give", ban_protected(give_command)))
    application.add_handler(CommandHandler("add", ban_protected(add_resource_command)))
    application.add_handler(CommandHandler("stats", disable_protected(stats_command)))
    application.add_handler(CommandHandler("missions", disable_protected(missions_command)))
    application.add_handler(CommandHandler("resetmission", disable_protected(reset_mission_command)))
    application.add_handler(CommandHandler("remission", disable_protected(remission_command)))

    # Mod commands
    application.add_handler(CommandHandler("monitor", monitor_command))
    application.add_handler(CommandHandler("nuke", reset_handler))
    application.add_handler(CommandHandler("bfb", ban_user))
    application.add_handler(CommandHandler("ubfb", unban_user))
    application.add_handler(CommandHandler("mod", promote_mod))
    application.add_handler(CommandHandler("demod", demote_mod))
    application.add_handler(CommandHandler("mm", maintenance))
    application.add_handler(CommandHandler("disablecmd", disable_command))
    application.add_handler(CommandHandler("enablecmd", enable_command))
    application.add_handler(CommandHandler("dbdiag", diagnostic_db_command))
    application.add_handler(CommandHandler("checkgroup", check_group_record))
    application.add_handler(CommandHandler("taxstatus", tax_status_command))
    application.add_handler(CommandHandler("forcetax", force_tax_check_command))

    # Bank commands
    application.add_handler(CommandHandler("bank", disable_protected(handle_bank_command)))
    application.add_handler(CommandHandler("deposit", disable_protected(handle_deposit_command)))
    application.add_handler(CommandHandler("withdraw", disable_protected(handle_withdrawal_command)))

    # PVP commands
    application.add_handler(CommandHandler("pvp", disable_protected(pvp_command)))

    # Callback handlers
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CallbackQueryHandler(handle_travel_decision))
    application.add_handler(CallbackQueryHandler(pvp_callback_handler))
    application.add_handler(CallbackQueryHandler(missions_callback_handler))
    application.add_handler(CallbackQueryHandler(reset_mission_callback_handler))
    application.add_handler(CallbackQueryHandler(handle_open_bank_callback))

    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button))

    # Chat member handler
    application.add_handler(ChatMemberHandler(group_update_handler))

    logger.info("✅ All handlers registered successfully")

async def main():
    """Main bot runner"""
    print("🤖 ATTACK ON TITAN BOT - TEST MODE")
    print("=" * 50)
    print(f"📋 Environment: {ENV}")
    print(f"📋 Database: {DB_NAME}")
    print(f"📋 Debug Mode: {DEBUG}")
    print(f"📋 Using Polling: {USE_POLLING}")
    print(f"📋 Test Mode: {TEST_MODE}")
    if TEST_MODE:
        print("🧪 TEST MODE ACTIVE - Using separate test database")
        print("⚠️  NO CHANGES will be made to production database")
    print(f"📋 Bot Token: {'*' * 10 + TELEGRAM_TOKEN[-10:] if TELEGRAM_TOKEN else 'Not Set'}")
    print("=" * 50)

    # Validate environment
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: No bot token found in environment variables!")
        print("   Make sure TEST_BOT_TOKEN or TELEGRAM_TOKEN is set in .env file")
        return

    if not MONGODB_URI:
        print("❌ ERROR: MongoDB URI not found in environment variables!")
        print("   Make sure MONGODB_URI is set in .env file")
        return

    # Initialize bot environment
    try:
        await initialize_bot()
    except Exception as e:
        print(f"❌ ERROR: Failed to initialize bot environment: {e}")
        if DEBUG:
            import traceback
            traceback.print_exc()
        return

    # Create application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Setup handlers
    setup_handlers(application)

    # Store global data
    application.bot_data["db"] = db
    application.bot_data["shop_system"] = shop_system
    application.bot_data["shop_items"] = {**shop_system.shop_items, **shop_system.hidden_items}

    # Start scheduler
    start_scheduler()

    # Start scheduled tasks
    start_scheduled_tasks(application.bot)

    # Start stats scheduler
    await start_stats_scheduler(db)

    print("\n🚀 Starting bot...")
    print("🤖 Bot is now running! Send commands to your test bot in Telegram")
    print("Press Ctrl+C to stop the bot")

    # Start the bot
    if USE_POLLING:
        if asyncio.get_running_loop().is_running():
            # Loop is running, start manually
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            # Wait for stop signal
            stop_event = asyncio.Event()
            def stop_bot(signum, frame):
                stop_event.set()
            signal.signal(signal.SIGINT, stop_bot)
            await stop_event.wait()
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        else:
            await application.run_polling(close_loop=True)
    else:
        # Webhook mode (for production)
        if asyncio.get_running_loop().is_running():
            await application.initialize()
            await application.start()
            await application.updater.start_webhook(
                listen="0.0.0.0",
                port=int(os.getenv("PORT", 8080)),
                url_path="webhook",
                webhook_url=os.getenv("WEBHOOK_URL")
            )
            stop_event = asyncio.Event()
            def stop_bot(signum, frame):
                stop_event.set()
            signal.signal(signal.SIGINT, stop_bot)
            await stop_event.wait()
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        else:
            await application.run_webhook(
                listen="0.0.0.0",
                port=int(os.getenv("PORT", 8080)),
                url_path="webhook",
                webhook_url=os.getenv("WEBHOOK_URL"),
                close_loop=True
            )

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "cannot run the event loop" in str(e).lower():
            # Loop is running, run main in the existing loop
            loop = asyncio.get_running_loop()
            loop.create_task(main())
            # To keep the script running
            def stop_loop(signum, frame):
                loop.stop()
            signal.signal(signal.SIGINT, stop_loop)
            try:
                loop.run_forever()
            except KeyboardInterrupt:
                print("\n👋 Bot stopped by user")
        else:
            raise
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)