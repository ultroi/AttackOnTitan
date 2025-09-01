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
from game.map_system import show_map
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

# Local memory storage for test mode
class LocalMemoryDatabase:
    """In-memory database for test mode that doesn't persist to MongoDB"""

    def __init__(self):
        self.players = {}
        self.characters = {}
        self.titans = {}
        self.bans = {}
        self.groups = {}
        self.shop_items = {}
        self.active_battles = {}
        self.titan_timeout_tasks = {}
        # Add compatibility properties
        self.db = None
        self.characters_collection = None
        self.players_collection = None
        self.titans_collection = None
        self.equipment = None
        self.shop_purchases = None
        self.shop_purchases_collection = None
        self.bank_accounts = None
        self.groups_collection = None
        self.stats = None
        self._titan_cache = {}
        self.bans_collection = None

    async def init_db(self, motor_db=None):
        """Initialize the local memory database"""
        logger.info("🧪 Initializing Local Memory Database for Test Mode")
        # Don't connect to MongoDB in test mode
        return self

    async def get_player(self, user_id):
        """Get player from local memory, load from DB if not present (lazy load)"""
        user_id_str = str(user_id)
        if user_id_str not in self.players:
            # Lazy load from DB only once per session
            await self.load_user_from_db(user_id_str)
        return self.players.get(user_id_str)

    async def load_user_from_db(self, user_id_str):
        """Load user data from MongoDB into memory (only once per session)"""
        # Use the global get_persistent_database and Database
        try:
            motor_db = await get_persistent_database()
            if motor_db is None:
                logger.warning(f"[LocalMemoryDB] Could not get DB instance for user {user_id_str}")
                return
            db = Database()
            await db.init_db(motor_db)
            # Try to get player data
            player = await db.get_player(user_id_str)
            if player:
                # Convert to dict if needed
                if hasattr(player, '__dict__'):
                    self.players[user_id_str] = dict(player.__dict__)
                else:
                    self.players[user_id_str] = player
                logger.info(f"[LocalMemoryDB] Loaded player {user_id_str} from DB into memory")
            # Load all characters for this user
            if hasattr(db, 'get_player_characters'):
                chars = await db.get_player_characters(user_id_str)
                if chars:
                    for char in chars:
                        # Convert to dict if needed
                        if hasattr(char, '__dict__'):
                            char_dict = dict(char.__dict__)
                        else:
                            char_dict = char
                        key = f"{user_id_str}_{char_dict['name']}"
                        self.characters[key] = char_dict
                    logger.info(f"[LocalMemoryDB] Loaded {len(chars)} characters for user {user_id_str} from DB into memory")
        except Exception as e:
            logger.error(f"[LocalMemoryDB] Error loading user {user_id_str} from DB: {e}")

    async def update_player(self, user_id, update_data):
        """Update player in local memory"""
        user_id_str = str(user_id)
        if user_id_str not in self.players:
            self.players[user_id_str] = {}

        # Merge update data
        for key, value in update_data.items():
            if key in self.players[user_id_str] and isinstance(self.players[user_id_str][key], dict) and isinstance(value, dict):
                # Deep merge for nested dictionaries
                self._deep_merge(self.players[user_id_str][key], value)
            else:
                self.players[user_id_str][key] = value

        logger.debug(f"📝 Local DB: Updated player {user_id_str} with keys: {list(update_data.keys())}")

    def _deep_merge(self, target, source):
        """Deep merge two dictionaries"""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value

    async def get_character(self, user_id, character_name):
        """Get character from local memory"""
        key = f"{user_id}_{character_name}"
        char = self.characters.get(key)
        if char:
            logger.debug(f"📖 Local DB: Retrieved character {key}")
        return char

    async def update_character(self, character):
        """Update character in local memory"""
        key = f"{character.user_id}_{character.name}"
        self.characters[key] = character
        logger.debug(f"📝 Local DB: Updated character {key}")

    async def store_titan(self, user_id, titan):
        """Store titan in local memory"""
        key = f"{user_id}_titan"
        self.titans[key] = titan
        logger.debug(f"📝 Local DB: Stored titan for user {user_id}")

    async def get_titan(self, user_id):
        """Get titan from local memory"""
        key = f"{user_id}_titan"
        titan = self.titans.get(key)
        if titan:
            logger.debug(f"📖 Local DB: Retrieved titan for user {user_id}")
        return titan

    async def delete_titan(self, user_id):
        """Delete titan from local memory"""
        key = f"{user_id}_titan"
        if key in self.titans:
            del self.titans[key]
            logger.debug(f"�️ Local DB: Deleted titan for user {user_id}")

    async def batch_update_player(self, user_id, update_data):
        """Batch update player in local memory"""
        await self.update_player(user_id, update_data)

    def get_status_message(self):
        """Get a status message showing current local database state"""
        stats = self.get_stats()
        return (
            f"🧪 *Local Memory Database Status*\n\n"
            f"📊 Current Data:\n"
            f"• Players: {stats['players']}\n"
            f"• Characters: {stats['characters']}\n"
            f"• Active Titans: {stats['titans']}\n"
            f"• Bans: {stats['bans']}\n\n"
            f"💾 All data is stored in memory\n"
            f"🔄 Data will be lost on restart\n"
            f"🗑️ Use /cleardb to reset all data"
        )

    # Additional methods to handle other database operations
    async def find_one(self, collection, query):
        """Mock find_one for compatibility"""
        logger.debug(f"📖 Local DB: find_one called on {collection} with query: {query}")
        # This is a simplified mock - in a real implementation you'd need to handle different collections
        return None

    async def update_one(self, collection, query, update_data, upsert=False):
        """Mock update_one for compatibility"""
        logger.debug(f"📝 Local DB: update_one called on {collection}")
        # This is a simplified mock - in a real implementation you'd need to handle different collections
        return None

    def invalidate_titan_cache(self, user_id: str):
        """Mock invalidate_titan_cache for compatibility"""
        if user_id in self._titan_cache:
            del self._titan_cache[user_id]
            logger.debug(f"Invalidated titan cache for user {user_id}")
        return True

    def invalidate_player_cache(self, user_id: str):
        """Mock invalidate_player_cache for compatibility"""
        logger.debug(f"Mock: Invalidated player cache for user {user_id}")
        return True

    def invalidate_character_cache(self, user_id: str, character_name: str):
        """Mock invalidate_character_cache for compatibility"""
        logger.debug(f"Mock: Invalidated character cache for {character_name}")
        return True

    def get_stats(self):
        """Get database statistics"""
        return {
            "players": len(self.players),
            "characters": len(self.characters),
            "titans": len(self.titans),
            "bans": len(self.bans)
        }

    def clear_all(self):
        """Clear all data (for testing)"""
        self.players.clear()
        self.characters.clear()
        self.titans.clear()
        self.bans.clear()
        self.groups.clear()
        self.shop_items.clear()
        self.active_battles.clear()
        self.titan_timeout_tasks.clear()
        logger.info("🧪 Local DB: All data cleared")

# Global local database instance
local_db = LocalMemoryDatabase()


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

    if TEST_MODE:
        # Use local memory database for test mode
        logger.info("🧪 Using Local Memory Database (Test Mode)")
        db = local_db
        await db.init_db()
    else:
        # Set environment variables for database
        if MONGODB_URI:
            os.environ["MONGODB_URI"] = MONGODB_URI
        os.environ["DB_NAME"] = DB_NAME

        # Initialize database
        try:
            motor_db = await get_persistent_database()
            if motor_db is not None:
                db = Database()
                await db.init_db(motor_db)
            else:
                logger.error("Failed to get database instance")
                db = None
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            db = None

    # Initialize shop system
    shop_system = ShopSystem()

    logger.info("Bot environment initialized successfully!")
    if TEST_MODE:
        logger.info("🧪 Local Memory Database loaded - NO PERSISTENT STORAGE")
        if db is not None and isinstance(db, LocalMemoryDatabase) and hasattr(db, "get_stats"):
            try:
                stats = db.get_stats()
                logger.info(f"📊 Local DB Stats: Players: {stats['players']}, Characters: {stats['characters']}, Titans: {stats['titans']}")
            except Exception as e:
                logger.error(f"Error getting stats: {e}")
                logger.info("📊 Local DB Stats: No statistics available")
        else:
            logger.info("📊 Local DB Stats: No statistics available")
        logger.info("💡 Use /dbstatus to check current data and /cleardb to reset")
    else:
        logger.info(f"Connected to database: {DB_NAME}")
    logger.info(f"Shop system loaded with {len(shop_system.shop_items)} items")

# Setup bot handlers
def setup_handlers(application):
    """Setup all command and callback handlers"""

    # User commands (protected only by disable)
    application.add_handler(CommandHandler("start", disable_protected(start_character_selection)))
    application.add_handler(CommandHandler("inv", disable_protected(profile)))
    application.add_handler(CommandHandler("explore", disable_protected(explore)))
    application.add_handler(CommandHandler("open", disable_protected(open_keyboard)))
    application.add_handler(CommandHandler("close", disable_protected(close_keyboard)))
    application.add_handler(CommandHandler("resetverify", disable_protected(reset_verify)))
    application.add_handler(CommandHandler("map", disable_protected(show_map)))
    application.add_handler(CommandHandler("travel", disable_protected(travel_command)))
    application.add_handler(CommandHandler("shop", disable_protected(shop_command)))
    application.add_handler(CommandHandler("status", disable_protected(profile)))
    application.add_handler(CommandHandler("buy", disable_protected(buy_command)))
    application.add_handler(CommandHandler("referral", disable_protected(referral_info)))
    application.add_handler(CommandHandler("char", disable_protected(char_detail)))
    application.add_handler(CommandHandler("give", disable_protected(give_command)))
    application.add_handler(CommandHandler("add", disable_protected(add_resource_command)))
    application.add_handler(CommandHandler("stats", disable_protected(stats_command)))
    application.add_handler(CommandHandler("missions", disable_protected(missions_command)))
    application.add_handler(CommandHandler("resetmission", disable_protected(reset_mission_command)))
    application.add_handler(CommandHandler("remission", disable_protected(remission_command)))

    # Mod/owner commands (not protected by disable)
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
    if TEST_MODE:
        application.add_handler(CommandHandler("cleardb", clear_local_db_command))
        application.add_handler(CommandHandler("dbstatus", local_db_status_command))

    # Bank system handlers
    application.add_handler(CommandHandler("bank", disable_protected(handle_bank_command)))
    application.add_handler(CommandHandler("deposit", disable_protected(handle_deposit_command)))
    application.add_handler(CommandHandler("withdraw", disable_protected(handle_withdrawal_command)))

    # PVP system handlers
    application.add_handler(CommandHandler("pvp", disable_protected(pvp_command)))

    # Callback handlers with patterns
    application.add_handler(CallbackQueryHandler(missions_callback_handler, pattern=r"^mission_"))
    application.add_handler(CallbackQueryHandler(reset_mission_callback_handler, pattern=r"^reset_"))
    application.add_handler(CallbackQueryHandler(handle_open_bank_callback, pattern="^bank_open_account$"))
    application.add_handler(CallbackQueryHandler(pvp_callback_handler, pattern="^pvp_"))
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
    application.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
    application.add_handler(CallbackQueryHandler(show_inventory, pattern="^show_inventory$"))
    application.add_handler(CallbackQueryHandler(view_weapons, pattern="^view_weapons$"))
    application.add_handler(CallbackQueryHandler(view_gear, pattern="^view_gear$"))
    application.add_handler(CallbackQueryHandler(view_military, pattern="^view_military$"))
    application.add_handler(CallbackQueryHandler(view_utilities, pattern="^view_utilities$"))
    application.add_handler(CallbackQueryHandler(view_echo_shards, pattern="^view_echo_shards$"))
    application.add_handler(CallbackQueryHandler(fill_gas, pattern=r"^fill_gas_"))
    application.add_handler(CallbackQueryHandler(view_weapons_char, pattern=r"^view_weapons_"))
    application.add_handler(CallbackQueryHandler(equip_weapon, pattern=r"^equip_weapon_"))
    application.add_handler(CallbackQueryHandler(view_abilities, pattern=r"^view_abilities_"))
    application.add_handler(CallbackQueryHandler(char_detail_callback, pattern=r"^char_detail_"))
    application.add_handler(CallbackQueryHandler(exit_profile, pattern=r"^exit_profile$"))
    application.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))
    application.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)"))
    application.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$"))
    application.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))
    application.add_handler(CallbackQueryHandler(button, pattern=r"^[A-Z0-9]+$"))
    application.add_handler(CallbackQueryHandler(button_callback))  # Fallback

    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, group_update_handler))

    # Chat member handler
    application.add_handler(ChatMemberHandler(group_update_handler, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER | ChatMemberHandler.CHAT_MEMBER))

    logger.info("✅ All handlers registered successfully")

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

# Test mode command to clear local database
async def clear_local_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all data from local memory database (test mode only)"""
    if not TEST_MODE:
        if update.message:
            await update.message.reply_text("❌ This command is only available in test mode.")
        return

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    # Check if user is owner (you can modify this check as needed)
    # For now, allow anyone to use it in test mode
    try:
        local_db.clear_all()
        stats = local_db.get_stats()
        message = (
            "🧪 *Local Database Cleared!*\n\n"
            "📊 Current Stats:\n"
            f"• Players: {stats['players']}\n"
            f"• Characters: {stats['characters']}\n"
            f"• Titans: {stats['titans']}\n"
            f"• Bans: {stats['bans']}\n\n"
            "✅ All test data has been reset."
        )
        if update.message:
            await update.message.reply_text(message, parse_mode="Markdown")
        logger.info(f"🧪 Local database cleared by user {user_id}")
    except Exception as e:
        logger.error(f"Error clearing local database: {e}")
        if update.message:
            await update.message.reply_text("❌ Failed to clear local database.")

# Test mode command to show local database status
async def local_db_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show status of local memory database (test mode only)"""
    if not TEST_MODE:
        if update.message:
            await update.message.reply_text("❌ This command is only available in test mode.")
        return

    if not update.effective_user:
        return

    try:
        message = local_db.get_status_message()
        if update.message:
            await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error getting local database status: {e}")
        if update.message:
            await update.message.reply_text("❌ Failed to get database status.")

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
        print("🧪 TEST MODE ACTIVE - Using LOCAL MEMORY DATABASE")
        print("⚠️  NO CHANGES will be saved to any database")
        print("💾 All data is stored in memory and lost on restart")
        print("🗑️  Use /cleardb to reset all test data")
    else:
        print("💾 PRODUCTION MODE - Using MongoDB database")
    print(f"📋 Bot Token: {'*' * 10 + TELEGRAM_TOKEN[-10:] if TELEGRAM_TOKEN and len(TELEGRAM_TOKEN) > 10 else 'Not Set'}")
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
    try:
        if db is not None:
            await start_stats_scheduler(db)
        else:
            logger.warning("Cannot start stats scheduler: Database not initialized")
    except Exception as e:
        logger.error(f"Failed to start stats scheduler: {e}")

    print("\n🚀 Starting bot...")
    print("🤖 Bot is now running! Send commands to your test bot in Telegram")
    print("Press Ctrl+C to stop the bot")

    # Start the bot
    if USE_POLLING:
        if asyncio.get_running_loop().is_running():
            # Loop is running, start manually
            await application.initialize()
            await application.start()
            if application.updater:
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
                logger.error("Updater not initialized - cannot start polling")
        else:
            try:
                # Run polling directly instead of using application.run_polling
                application.run_polling(close_loop=True)
            except Exception as e:
                logger.error(f"Failed to start polling: {e}")
    else:
        # Webhook mode (for production)
        if asyncio.get_running_loop().is_running():
            await application.initialize()
            await application.start()
            if application.updater:
                webhook_url = os.getenv("WEBHOOK_URL")
                if webhook_url:
                    await application.updater.start_webhook(
                        listen="0.0.0.0",
                        port=int(os.getenv("PORT", "8080")),
                        url_path="webhook",
                        webhook_url=webhook_url
                    )
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
                    logger.error("WEBHOOK_URL not set - cannot start webhook")
            else:
                logger.error("Updater not initialized - cannot start webhook")
        else:
            try:
                webhook_url = os.getenv("WEBHOOK_URL")
                if webhook_url:
                    # Run webhook directly instead of using await
                    application.run_webhook(
                        listen="0.0.0.0",
                        port=int(os.getenv("PORT", "8080")),
                        url_path="webhook",
                        webhook_url=webhook_url,
                        close_loop=True
                    )
                else:
                    logger.error("WEBHOOK_URL not set - cannot run webhook")
            except Exception as e:
                logger.error(f"Failed to start webhook: {e}")

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