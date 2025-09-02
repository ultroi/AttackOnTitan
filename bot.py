"""
Attack on Titan Bot - Local Test Runner

This file is designed for testing the bot locally before deploying to production.
It follows the same structure as main.py but runs in polling mode for local development.

Features:
- Loads bot token from .env file (TEST_BOT_TOKEN)
- Uses test database to prevent changes to production data
- Runs the bot with test token for local testing
- Supports all commands and functionality of the production bot

Usage:
1. Set TEST_BOT_TOKEN in .env file
2. Set TEST_MODE=true in .env (default: true)
3. Run: python bot.py
4. Bot will start and respond to commands in Telegram

Environment Variables:
- TEST_BOT_TOKEN: Your test bot token
- TEST_MODE: true/false (enables test database)
- DEBUG: true/false (enables debug logging)
- MONGODB_URI: MongoDB connection string
- DB_NAME: Database name (will append "_test" in test mode)
"""

import os
import logging
import asyncio
import signal
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ChatMemberHandler, MessageHandler, filters
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from telegram import Update as TelegramUpdate
from database.db import Database
from database.db_instance import get_persistent_database
from game.map_system import show_map, MAP_IMAGE_URL
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
USE_POLLING = True  # Always use polling for local testing
TEST_BOT_TOKEN = os.getenv("TEST_BOT_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or TEST_BOT_TOKEN
MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "attackontitan")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"  # Default to debug mode in test environment
SECRET_TOKEN = os.getenv("SECRET_TOKEN", TELEGRAM_TOKEN.split(":")[1] if TELEGRAM_TOKEN else "")

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
        self.characters_collection = self.characters  # Use the dict as mock collection
        self.characters = self.characters  # For compatibility
        self.players_collection = self.players  # Use the dict as mock collection
        self.titans_collection = self.titans  # Use the dict as mock collection
        self.equipment = {}
        self.shop_purchases = {}
        self.shop_purchases_collection = {}
        self.bank_accounts = {}
        self.groups_collection = self.groups  # Use the dict as mock collection
        self.stats = {}
        self._titan_cache = {}
        self.bans_collection = self.bans  # Use the dict as mock collection

    async def init_db(self, motor_db=None):
        """Initialize the local memory database"""
        logger.info("🧪 Initializing Local Memory Database for Test Mode")
        return self

    async def get_player(self, user_id):
        """Get player from local memory, lazy load if needed"""
        user_id_str = str(user_id)
        if user_id_str not in self.players:
            # Try to load from persistent DB if available
            await self._try_load_from_persistent_db(user_id_str)
        return self.players.get(user_id_str)

    async def _try_load_from_persistent_db(self, user_id_str):
        """Try to load user data from persistent DB if possible"""
        try:
            motor_db = await get_persistent_database()
            if motor_db:
                db = Database()
                await db.init_db(motor_db)
                player = await db.get_player(user_id_str)
                if player:
                    self.players[user_id_str] = player
                    logger.info(f"[LocalMemoryDB] Loaded player {user_id_str} from persistent DB")
        except Exception as e:
            logger.debug(f"[LocalMemoryDB] Could not load from persistent DB: {e}")

    async def update_player(self, user_id, update_data):
        """Update player in local memory"""
        user_id_str = str(user_id)
        if user_id_str not in self.players:
            self.players[user_id_str] = {}

        # Apply updates
        for key, value in update_data.items():
            if isinstance(self.players[user_id_str].get(key), dict) and isinstance(value, dict):
                # Merge nested dictionaries
                if key not in self.players[user_id_str]:
                    self.players[user_id_str][key] = {}
                self.players[user_id_str][key].update(value)
            else:
                self.players[user_id_str][key] = value

        return self.players[user_id_str]

    async def get_character(self, user_id, character_name):
        """Get character from local memory"""
        key = f"{user_id}_{character_name}"
        return self.characters.get(key)

    async def update_character(self, character):
        """Update character in local memory"""
        key = f"{character.user_id}_{character.name}"
        self.characters[key] = character
        return character

    async def store_titan(self, user_id, titan):
        """Store titan in local memory"""
        key = f"{user_id}_titan"
        self.titans[key] = titan

    async def get_titan(self, user_id):
        """Get titan from local memory"""
        key = f"{user_id}_titan"
        return self.titans.get(key)

    async def delete_titan(self, user_id):
        """Delete titan from local memory"""
        key = f"{user_id}_titan"
        if key in self.titans:
            del self.titans[key]

    async def batch_update_player(self, user_id, update_data):
        """Batch update player in local memory"""
        return await self.update_player(user_id, update_data)

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

    # Compatibility methods
    async def find_one(self, collection, query):
        """Mock find_one for compatibility"""
        logger.debug(f"📖 Local DB: find_one called on {collection} with query: {query}")
        return None

    async def update_one(self, collection, query, update_data, upsert=False):
        """Mock update_one for compatibility"""
        logger.debug(f"📝 Local DB: update_one called on {collection}")
        return None

    def invalidate_titan_cache(self, user_id: str):
        """Mock invalidate_titan_cache for compatibility"""
        if user_id in self._titan_cache:
            del self._titan_cache[user_id]
        return True

    def invalidate_player_cache(self, user_id: str):
        """Mock invalidate_player_cache for compatibility"""
        return True

    def invalidate_character_cache(self, user_id: str, character_name: str):
        """Mock invalidate_character_cache for compatibility"""
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

async def check_database_health(db_instance):
    """Check if database is healthy and responsive"""
    try:
        if TEST_MODE:
            # For test mode, just check if the instance exists
            return db_instance is not None
        else:
            # For production, try a simple database operation
            if hasattr(db_instance, 'players') and db_instance.players is not None:
                # Try to count documents (lightweight operation)
                count = await db_instance.players.count_documents({})
                logger.info(f"✅ Database health check passed - found {count} player documents")
                return True
            else:
                logger.error("❌ Database health check failed - players collection not available")
                return False
    except Exception as e:
        logger.error(f"❌ Database health check failed: {e}")
        return False

async def handle_database_error(context, error):
    """Handle database-related errors and attempt recovery"""
    logger.error(f"Database error detected: {error}")

    # Check if it's a connection error
    if "connection" in str(error).lower() or "timeout" in str(error).lower():
        logger.info("🔄 Attempting database reconnection...")

        # Try to reinitialize database
        db_instance = await initialize_database()
        if db_instance is not None:
            global global_db
            global_db = db_instance

            # Update application bot_data if application exists
            if application is not None:
                application.bot_data["db"] = global_db
                logger.info("✅ Database reconnected successfully")
                return True
            else:
                logger.error("❌ Application not available for database update")
        else:
            logger.error("❌ Database reconnection failed")

    return False


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
async def initialize_database():
    """Initialize database and return the database instance"""
    global global_db

    logger.info("🔄 Initializing database connection...")

    try:
        if TEST_MODE:
            # Use local memory database for test mode
            logger.info("🧪 Using Local Memory Database (Test Mode)")
            if global_db is None or not isinstance(global_db, LocalMemoryDatabase):
                global_db = local_db
                await global_db.init_db()
        else:
            # Use persistent MongoDB connection for production
            if global_db is None:
                logger.info("💾 Connecting to MongoDB database")
                motor_db = await get_persistent_database()
                if motor_db is not None:
                    global_db = Database()
                    await global_db.init_db(motor_db)

                    # Apply battle system fixes if needed
                    from game.battle_fix import apply_battle_fixes
                    fixes_applied = await apply_battle_fixes(global_db)
                    if fixes_applied:
                        logger.info("Applied battle system fixes")
                else:
                    logger.error("❌ Failed to get database instance")
                    return None

        # Verify database is working
        if global_db is not None:
            logger.info("✅ Database connection established successfully")
            if TEST_MODE:
                logger.info("🧪 Local Memory Database loaded - NO PERSISTENT STORAGE")
                if isinstance(global_db, LocalMemoryDatabase) and hasattr(global_db, "get_stats"):
                    try:
                        stats = global_db.get_stats()
                        logger.info(f"📊 Local DB Stats: Players: {stats['players']}, Characters: {stats['characters']}, Titans: {stats['titans']}")
                    except Exception as e:
                        logger.error(f"Error getting stats: {e}")
            else:
                logger.info(f"� Connected to database: {DB_NAME}")
        else:
            logger.error("❌ Database initialization failed")
            return None

        return global_db

    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}", exc_info=True)
        return None

# Initialize shop system
async def initialize_shop_system():
    """Initialize shop system and return the instance"""
    try:
        logger.info("� Initializing shop system...")
        shop_system = ShopSystem()
        logger.info(f"✅ Shop system loaded with {len(shop_system.shop_items)} items")
        return shop_system
    except Exception as e:
        logger.error(f"❌ Failed to initialize shop system: {e}")
        return None

async def initialize_bot():
    """Initialize all bot components in the correct order"""
    global application, global_db

    logger.info("� Starting bot initialization...")

    # Step 1: Initialize database first
    db_instance = await initialize_database()
    if db_instance is None:
        logger.error("❌ Database initialization failed - cannot continue")
        return False
    global_db = db_instance

    # Step 2: Initialize shop system
    shop_system = await initialize_shop_system()
    if shop_system is None:
        logger.error("❌ Shop system initialization failed - cannot continue")
        return False

    # Step 3: Create application
    logger.info("🤖 Creating Telegram application...")
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        logger.info("✅ Application created successfully")
    except Exception as e:
        logger.error(f"❌ Failed to create application: {e}")
        return False

    # Step 4: Store global data in application
    logger.info("💾 Storing global data in application...")
    application.bot_data["db"] = global_db
    application.bot_data["shop_system"] = shop_system
    application.bot_data["shop_items"] = {**shop_system.shop_items, **shop_system.hidden_items}

    # Step 5: Set up error handler
    await setup_error_handler(application)

    # Step 6: Register all handlers
    logger.info("📋 Registering command handlers...")
    setup_handlers(application)

    # Step 7: Start schedulers
    logger.info("⏰ Starting schedulers...")
    try:
        # Start the midnight tax scheduler with bot instance
        start_scheduler(application.bot)
        logger.info("✅ Tax scheduler started")

        # Start scheduled tasks
        start_scheduled_tasks(application.bot)
        logger.info("✅ Scheduled tasks started")

        # Start stats scheduler
        if global_db is not None:
            await start_stats_scheduler(global_db)
            logger.info("✅ Stats scheduler started")
        else:
            logger.warning("⚠️ Cannot start stats scheduler: Database not initialized")
    except Exception as e:
        logger.error(f"❌ Failed to start schedulers: {e}")
        # Don't fail completely if schedulers fail
        pass

    logger.info("🎉 Bot initialization completed successfully!")
    return True

# Register all command and callback handlers
def setup_handlers(application):
    """Register all command and callback handlers"""

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
    
    # Test mode only commands
    if TEST_MODE:
        application.add_handler(CommandHandler("cleardb", clear_local_db_command))
        application.add_handler(CommandHandler("dbstatus", local_db_status_command))

    # Bank system handlers
    application.add_handler(CommandHandler("bank", disable_protected(handle_bank_command)))
    application.add_handler(CommandHandler("deposit", disable_protected(handle_deposit_command)))
    application.add_handler(CommandHandler("withdraw", disable_protected(handle_withdrawal_command)))
    application.add_handler(CallbackQueryHandler(handle_open_bank_callback, pattern="^bank_open_account$"))
    
    # PVP system handlers
    application.add_handler(CommandHandler("pvp", disable_protected(pvp_command)))
    application.add_handler(CallbackQueryHandler(pvp_callback_handler, pattern="^pvp_"))

    # Character selection and team management
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

    # Profile and inventory
    application.add_handler(CallbackQueryHandler(profile, pattern="^show_profile$"))
    application.add_handler(CallbackQueryHandler(show_inventory, pattern="^show_inventory$"))
    application.add_handler(CallbackQueryHandler(view_weapons, pattern="^view_weapons$"))
    application.add_handler(CallbackQueryHandler(view_gear, pattern="^view_gear$"))
    application.add_handler(CallbackQueryHandler(view_military, pattern="^view_military$"))
    application.add_handler(CallbackQueryHandler(view_utilities, pattern="^view_utilities$"))
    application.add_handler(CallbackQueryHandler(view_echo_shards, pattern="^view_echo_shards$"))

    # Character detail handlers
    application.add_handler(CallbackQueryHandler(fill_gas, pattern=r"^fill_gas_"))
    application.add_handler(CallbackQueryHandler(view_weapons_char, pattern=r"^view_weapons_"))
    application.add_handler(CallbackQueryHandler(equip_weapon, pattern=r"^equip_weapon_"))
    application.add_handler(CallbackQueryHandler(view_abilities, pattern=r"^view_abilities_"))
    application.add_handler(CallbackQueryHandler(char_detail_callback, pattern=r"^char_detail_"))
    application.add_handler(CallbackQueryHandler(exit_profile, pattern=r"^exit_profile$"))

    # Battle and travel
    application.add_handler(CallbackQueryHandler(handle_battle_action, pattern="^action_"))
    application.add_handler(CallbackQueryHandler(handle_travel_direction, pattern=r"^travel_(?!decision_)"))
    application.add_handler(CallbackQueryHandler(handle_cancel_travel, pattern="^cancel_travel$"))
    application.add_handler(CallbackQueryHandler(handle_travel_decision, pattern=r"^travel_decision_"))

    # Mission handlers
    application.add_handler(CallbackQueryHandler(missions_callback_handler, pattern=r"^mission_"))
    application.add_handler(CallbackQueryHandler(reset_mission_callback_handler, pattern=r"^reset_"))

    # Shop and purchases
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(shop_|buy_|shop_refresh)"))

    # Group membership handler
    application.add_handler(ChatMemberHandler(group_update_handler, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER | ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, group_update_handler))

    # Generic button handler (should be last)
    application.add_handler(CallbackQueryHandler(button, pattern=r"^[A-Z0-9]+$"))

    # Fallback handler (must be absolutely last)
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Text message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button))

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

async def setup_error_handler(application):
    """Set up error handler for the bot"""
    
    async def error_handler(update: object, context):
        if isinstance(context.error, asyncio.CancelledError):
            logger.warning(f"Task cancelled for update {update}")
            return
                
        # Special handling for rate limiting errors
        from telegram.error import RetryAfter
        if isinstance(context.error, RetryAfter):
            retry_seconds = context.error.retry_after
            logger.warning(f"Rate limited. Retry after {retry_seconds} seconds")
            
            # For rate limit errors, only notify the user if possible
            if isinstance(update, Update) and getattr(update, "effective_message", None):
                try:
                    if update.effective_message:
                        await asyncio.sleep(min(retry_seconds, 5))  # Wait a bit before sending the message
                        await update.effective_message.reply_text(
                            f"Bot is being rate limited. Please try again in {int(retry_seconds)} seconds."
                        )
                except Exception as e:
                    logger.error(f"Failed to notify user about rate limit: {e}")
            return

        # Handle database connection errors
        if "connection" in str(context.error).lower() or "timeout" in str(context.error).lower():
            recovery_success = await handle_database_error(context, context.error)
            if recovery_success:
                # If recovery successful, don't log as error
                logger.info("Database error recovered successfully")
                return
                
        logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
            
        # Prepare detailed error message
        command = None
        if isinstance(update, Update):
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
        
        # In test mode, log to console
        if TEST_MODE:
            logger.error(f"ERROR: {error_text}")
            if DEBUG:
                import traceback
                traceback.print_exc()

    application.add_error_handler(error_handler)

async def database_health_monitor():
    """Monitor database health periodically"""
    while True:
        try:
            await asyncio.sleep(300)  # Check every 5 minutes

            if not TEST_MODE and global_db is not None:
                health_ok = await check_database_health(global_db)
                if not health_ok:
                    logger.warning("⚠️ Database health check failed, attempting recovery...")
                    recovery_success = await handle_database_error(None, "Health check failed")
                    if recovery_success:
                        logger.info("✅ Database health restored")
                    else:
                        logger.error("❌ Database health recovery failed")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in database health monitor: {e}")
            await asyncio.sleep(60)  # Wait a minute before retrying

# Global instances
local_db = LocalMemoryDatabase()
health_monitor_task = None
    """Main bot runner with proper initialization sequence"""
    print("🤖 ATTACK ON TITAN BOT - LOCAL TEST MODE")
    print("=" * 50)
    print(f"📋 Environment: {ENV}")
    print(f"📋 Database: {DB_NAME}")
    print(f"📋 Debug Mode: {DEBUG}")
    print(f"📋 Test Mode: {TEST_MODE}")
    if TEST_MODE:
        print("🧪 TEST MODE ACTIVE - Using LOCAL MEMORY DATABASE")
        print("⚠️  NO CHANGES will be saved to production database")
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

    # Initialize bot with all components
    success = await initialize_bot()
    if not success:
        print("❌ ERROR: Failed to initialize bot")
        return

    # Final database readiness check
    if not await ensure_database_ready():
        print("❌ ERROR: Database not ready after initialization")
        return

    # Start database health monitor for production mode
    global health_monitor_task
    if not TEST_MODE:
        health_monitor_task = asyncio.create_task(database_health_monitor())
        logger.info("✅ Database health monitor started")

    print("\n🚀 Starting bot...")
    print("🤖 Bot is now running in polling mode! Send commands to your test bot in Telegram")
    print("Press Ctrl+C to stop the bot")

    # Set up signal handlers for graceful shutdown
    stop_event = asyncio.Event()
    def stop_bot(signum, frame):
        print("\n⏱️ Stopping bot...")
        stop_event.set()
    signal.signal(signal.SIGINT, stop_bot)
    signal.signal(signal.SIGTERM, stop_bot)

    # Start the bot with polling
    try:
        # Initialize and start the application
        await application.initialize()
        await application.start()
        app_initialized = True

        # Start polling for updates
        if application.updater:
            await application.updater.start_polling(drop_pending_updates=True)
            print("✅ Bot is now polling for updates")

            # Wait until stop signal
            await stop_event.wait()
        else:
            logger.error("Updater not initialized - cannot start polling")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}", exc_info=True)
    finally:
        # Perform graceful shutdown
        print("\n⏱️ Shutting down...")
        
        # Cancel health monitor task
        if health_monitor_task and not health_monitor_task.done():
            health_monitor_task.cancel()
            try:
                await health_monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("✅ Database health monitor stopped")
        
        if application:
            if application.updater:
                await application.updater.stop()
            await application.stop()
            await application.shutdown()
        print("👋 Bot stopped")

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