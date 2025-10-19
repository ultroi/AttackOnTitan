
import logging
import asyncio
import pytz
from datetime import datetime, timedelta
from telegram import Update, Bot
from telegram.ext import ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected
from database.db import Database
from utils.mod_utils import mod_only

# MongoDB stats persistence helpers
STATS_DOC_ID = "explore_stats"

async def load_stats_data_from_db(db):
    doc = await db.stats.find_one({"_id": STATS_DOC_ID})
    if doc:
        return {
            "weekly_explorers": doc.get("weekly_explorers", {}),
            "daily_explorers": doc.get("daily_explorers", {}),
            "last_weekly_reset": doc.get("last_weekly_reset"),
            "last_daily_reset": doc.get("last_daily_reset"),
        }
    return {
        "weekly_explorers": {},
        "daily_explorers": {},
        "last_weekly_reset": None,
        "last_daily_reset": None,
    }

async def save_stats_data_to_db(db, stats_data):
    await db.stats.update_one(
        {"_id": STATS_DOC_ID},
        {"$set": {
            "weekly_explorers": stats_data["weekly_explorers"],
            "daily_explorers": stats_data["daily_explorers"],
            "last_weekly_reset": stats_data["last_weekly_reset"],
            "last_daily_reset": stats_data["last_daily_reset"],
        }},
        upsert=True
    )

logger = logging.getLogger(__name__)


# Stats storage (in-memory, will sync with DB)
stats_data = {
    "weekly_explorers": {},
    "daily_explorers": {},
    "last_weekly_reset": None,
    "last_daily_reset": None
}

# Initialize the scheduler
stats_scheduler = None

# IST timezone for resets
ist_timezone = pytz.timezone('Asia/Kolkata')


async def reset_weekly_stats():
    """Reset weekly explorer stats at midnight on Sunday (IST) and save to DB"""
    stats_data["weekly_explorers"] = {}
    stats_data["last_weekly_reset"] = datetime.now(ist_timezone)
    db = stats_data.get("_db")
    if db:
        await save_stats_data_to_db(db, stats_data)
    logger.info(f"[Stats] Weekly explorer stats reset at {stats_data['last_weekly_reset']} IST")


async def reset_daily_stats():
    """Reset daily explorer stats at midnight (IST) and save to DB"""
    stats_data["daily_explorers"] = {}
    stats_data["last_daily_reset"] = datetime.now(ist_timezone)
    db = stats_data.get("_db")
    if db:
        await save_stats_data_to_db(db, stats_data)
    logger.info(f"[Stats] Daily explorer stats reset at {stats_data['last_daily_reset']} IST")


async def start_stats_scheduler(db):
    """Start the scheduler for stats reset and load stats from DB"""
    global stats_scheduler
    if stats_scheduler is not None:
        stats_scheduler.shutdown()

    # Load stats from DB
    loaded = await load_stats_data_from_db(db)
    stats_data.update(loaded)
    stats_data["_db"] = db

    # Initialize last reset times if not set
    current_time = datetime.now(ist_timezone)
    if not stats_data["last_weekly_reset"]:
        stats_data["last_weekly_reset"] = current_time
    if not stats_data["last_daily_reset"]:
        stats_data["last_daily_reset"] = current_time

    stats_scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

    # Add job to reset weekly stats every Sunday at midnight IST
    stats_scheduler.add_job(
        reset_weekly_stats,
        'cron',
        day_of_week='sun',
        hour=0,
        minute=0,
        second=0,
        id='reset_weekly_stats',
        replace_existing=True,
        misfire_grace_time=300
    )

    # Add job to reset daily stats every midnight IST
    stats_scheduler.add_job(
        reset_daily_stats,
        'cron',
        hour=0,
        minute=0,
        second=0,
        id='reset_daily_stats',
        replace_existing=True,
        misfire_grace_time=300
    )

    stats_scheduler.start()
    logger.info("[Stats] Stats scheduler started successfully and stats loaded from DB")


async def update_explorer_stats(user_id: str, name: str, battle_completed: bool = False):
    """Update explorer stats for a player and persist to DB (non-blocking)"""
    if not battle_completed:
        return

    try:
        # Update weekly stats
        if user_id not in stats_data["weekly_explorers"]:
            stats_data["weekly_explorers"][user_id] = {"name": name, "count": 1}
            logger.info(f"[Stats] New weekly explorer: {name} ({user_id})")
        else:
            stats_data["weekly_explorers"][user_id]["count"] += 1
            stats_data["weekly_explorers"][user_id]["name"] = name
            logger.debug(f"[Stats] Updated weekly explorer: {name} - {stats_data['weekly_explorers'][user_id]['count']} explores")

        # Update daily stats (store first_name for display)
        if user_id not in stats_data["daily_explorers"]:
            stats_data["daily_explorers"][user_id] = {"name": name, "count": 1}
            logger.info(f"[Stats] New daily explorer: {name} ({user_id})")
        else:
            stats_data["daily_explorers"][user_id]["count"] += 1
            stats_data["daily_explorers"][user_id]["name"] = name
            logger.debug(f"[Stats] Updated daily explorer: {name} - {stats_data['daily_explorers'][user_id]['count']} explores")

        db = stats_data.get("_db")
        if db:
            # Fire-and-forget DB update for speed
            asyncio.create_task(save_stats_data_to_db(db, stats_data))
        else:
            logger.warning("[Stats] Database reference not found in stats_data, stats not persisted")
    except Exception as e:
        logger.error(f"[Stats] Error updating explorer stats: {e}", exc_info=True)

def get_top_explorers(explorer_data: dict, limit: int = 3):
    """Get top explorers from the provided explorer data"""
    # Sort by count (descending)
    sorted_explorers = sorted(
        explorer_data.items(),
        key=lambda x: x[1]["count"],
        reverse=True
    )
    # Return top N explorers as (user_id, name, count)
    return [(user_id, data["name"], data["count"]) for user_id, data in sorted_explorers[:limit]]

@maintenance_protected
@ban_protected
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command to show game statistics"""
    if not update.effective_user:
        return
        
    # Reset commands - mod only
    if context.args and len(context.args) > 0 and context.args[0] == "reset":
        # Check if user is mod/owner (reusing mod_only logic)
        from utils.owners import is_owner
        user_id = update.effective_user.id
        
        # Check if user is owner or mod
        if not is_owner(user_id):
            await update.message.reply_text("⛔ You don't have permission to reset stats.")
            return
            
        if len(context.args) > 1 and context.args[1] == "weekly":
            await reset_weekly_stats()
            await update.message.reply_text("✅ Weekly stats have been manually reset.")
            return
        elif len(context.args) > 1 and context.args[1] == "daily":
            await reset_daily_stats()
            await update.message.reply_text("✅ Daily stats have been manually reset.")
            return
        elif len(context.args) > 1 and context.args[1] == "all":
            await reset_weekly_stats()
            await reset_daily_stats()
            await update.message.reply_text("✅ All stats have been manually reset.")
            return

    # Check if user is in active battle
    from game.battle_system import active_battles
    user_id_str = str(update.effective_user.id)
    if user_id_str in active_battles:
        first_name = update.effective_user.first_name or "Player"
        battle_message = f"⚔️ <a href='tg://user?id={update.effective_user.id}'>{first_name}</a> is currently battling!"
        await update.message.reply_text(battle_message, parse_mode="HTML")
        return

    # /stats users: only mods can use
    if context.args and context.args[0] == "users":
        await stats_users_command(update, context)
        return
        
    # Start typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Get database from context
    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Error: Database not initialized.")
        return

    try:
        # Get current time in IST
        current_time_ist = datetime.now(ist_timezone)

        # Count total users
        total_users = await db.players.count_documents({})

        # Count total groups
        total_groups = await db.groups.count_documents({})

        # Get top 3 explorers (by explore_count) efficiently
        top_explorers_cursor = db.players.find({}, {"name": 1, "explore_count": 1, "user_id": 1}).sort("explore_count", -1).limit(3)
        top_explorers_data = await top_explorers_cursor.to_list(None)

        # Get top 3 levels efficiently
        top_levels_cursor = db.players.find({}, {"name": 1, "level": 1, "user_id": 1}).sort("level", -1).limit(3)
        top_levels_data = await top_levels_cursor.to_list(None)

        # Try to get first_name from update.effective_user
        def get_display_name(player_data, update):
            if update.effective_user and str(update.effective_user.id) == player_data.get("user_id", ""):
                return update.effective_user.first_name or player_data.get("name", "Unknown")
            return player_data.get("name", "Unknown")

        # Top 3 explorers (by explore_count)
        top_explorers_text = "\n".join([
            f"{i+1}. {get_display_name(p, update)} - {p.get('explore_count', 0)} explores"
            for i, p in enumerate(top_explorers_data)
        ]) if top_explorers_data else "~"

        # Top 3 levels
        top_levels_text = "\n".join([
            f"{i+1}. {get_display_name(p, update)} - Level {p.get('level', 0)}"
            for i, p in enumerate(top_levels_data)
        ]) if top_levels_data else "~"

        # Get top daily explorers (top 10, from stats_data)
        def get_daily_display_name(user_id, name, update):
            # Always use Telegram first_name if available for this user_id
            if update.effective_user and str(update.effective_user.id) == str(user_id):
                return update.effective_user.first_name or name
            return name

        top_daily = get_top_explorers(stats_data["daily_explorers"], limit=10)
        daily_explorers_text = "\n".join([
            f"{i+1}. {get_daily_display_name(user_id, name, update)} - {count} explores"
            for i, (user_id, name, count) in enumerate(top_daily)
        ]) if top_daily else "No data yet"
        
        # Debug log to check stats_data
        logger.info(f"[Stats] Daily explorers count: {len(stats_data['daily_explorers'])}")
        logger.info(f"[Stats] Weekly explorers count: {len(stats_data['weekly_explorers'])}")

        # Create message
        message = (
            f"📊 <b>GAME STATISTICS</b> 📊\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
            f"👥 <b>Total Groups:</b> <code>{total_groups}</code>\n\n"
            f"🏆 <b>TOP 3 EXPLORERS (ALL-TIME):</b>\n{top_explorers_text}\n\n"
            f"⭐ <b>TOP 3 LEVELS:</b>\n{top_levels_text}\n\n"
            f"🔥 <b>DAILY TOP 10 EXPLORERS:</b>\n{daily_explorers_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in stats_command: {e}", exc_info=True)
        await update.message.reply_text("An error occurred while processing the command.")

        
# Mod-only command for /stats users
@mod_only
async def stats_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Error: Database not initialized.")
        return
    try:
        users_cursor = db.players.find({}, {"name": 1, "user_id": 1, "username": 1})
        users = await users_cursor.to_list(length=10000)
        if not users:
            await update.message.reply_text("No users found.")
            return
        user_lines = []
        for user in users:
            user_id = user.get("user_id", "N/A")
            name = user.get("name", "Unknown")
            user_lines.append(f"{name} - {user_id}")
        user_list_text = "\n".join(user_lines)
        await update.message.reply_text(f"<b>All Users:</b>\n<code>{user_list_text}</code>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in /stats users: {e}", exc_info=True)
        await update.message.reply_text("An error occurred while fetching users.")

# Hook into the explore command to track statistics
async def track_explore_stats(user_id: str, name: str, battle_completed: bool = False):
    """Track explore statistics for a user - fully fire-and-forget"""
    try:
        # Don't block on stats update - just fire and forget
        asyncio.create_task(update_explorer_stats(user_id, name, battle_completed))
    except Exception as e:
        logger.error(f"Error tracking explore stats: {e}")
