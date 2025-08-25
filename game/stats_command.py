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

logger = logging.getLogger(__name__)

# Stats storage
stats_data = {
    "weekly_explorers": {},  # Format: {user_id: {"name": name, "count": count}}
    "daily_explorers": {},   # Format: {user_id: {"name": name, "count": count}}
    "last_weekly_reset": None,
    "last_daily_reset": None
}

# Initialize the scheduler
stats_scheduler = None

# IST timezone for resets
ist_timezone = pytz.timezone('Asia/Kolkata')

async def reset_weekly_stats():
    """Reset weekly explorer stats at midnight on Sunday (IST)"""
    stats_data["weekly_explorers"] = {}
    stats_data["last_weekly_reset"] = datetime.now(ist_timezone)
    logger.info(f"[Stats] Weekly explorer stats reset at {stats_data['last_weekly_reset']} IST")

async def reset_daily_stats():
    """Reset daily explorer stats at midnight (IST)"""
    stats_data["daily_explorers"] = {}
    stats_data["last_daily_reset"] = datetime.now(ist_timezone)
    logger.info(f"[Stats] Daily explorer stats reset at {stats_data['last_daily_reset']} IST")

def start_stats_scheduler():
    """Start the scheduler for stats reset"""
    global stats_scheduler
    if stats_scheduler is not None:
        stats_scheduler.shutdown()
    
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
        misfire_grace_time=300  # Allow up to 5 minutes delay
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
        misfire_grace_time=300  # Allow up to 5 minutes delay
    )
    
    stats_scheduler.start()
    logger.info("[Stats] Stats scheduler started successfully")

async def update_explorer_stats(user_id: str, name: str, battle_completed: bool = False):
    """Update explorer stats for a player"""
    # Only update stats if a battle was completed
    if not battle_completed:
        return
        
    # Update weekly stats
    if user_id not in stats_data["weekly_explorers"]:
        stats_data["weekly_explorers"][user_id] = {"name": name, "count": 1}
    else:
        stats_data["weekly_explorers"][user_id]["count"] += 1
        # Always update name in case it changed
        stats_data["weekly_explorers"][user_id]["name"] = name
    
    # Update daily stats
    if user_id not in stats_data["daily_explorers"]:
        stats_data["daily_explorers"][user_id] = {"name": name, "count": 1}
    else:
        stats_data["daily_explorers"][user_id]["count"] += 1
        # Always update name in case it changed
        stats_data["daily_explorers"][user_id]["name"] = name

def get_top_explorers(explorer_data: dict, limit: int = 3):
    """Get top explorers from the provided explorer data"""
    # Sort by count (descending)
    sorted_explorers = sorted(
        explorer_data.items(),
        key=lambda x: x[1]["count"],
        reverse=True
    )
    
    # Return top N explorers
    return [(data["name"], data["count"]) for _, data in sorted_explorers[:limit]]

@maintenance_protected
@ban_protected
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command to show game statistics"""
    if not update.effective_user:
        return
        
    # Check for manual reset (for admin/debugging purposes)
    if update.effective_user.id == 1794054461:  # Replace with actual admin ID if needed
        if context.args and context.args[0] == "reset":
            if len(context.args) > 1 and context.args[1] == "weekly":
                await reset_weekly_stats()
                await update.message.reply_text("Weekly stats have been manually reset.")
                return
            elif len(context.args) > 1 and context.args[1] == "daily":
                await reset_daily_stats()
                await update.message.reply_text("Daily stats have been manually reset.")
                return
            elif len(context.args) > 1 and context.args[1] == "all":
                await reset_weekly_stats()
                await reset_daily_stats()
                await update.message.reply_text("All stats have been manually reset.")
                return

    # /stats users: only mods can use
    if context.args and context.args[0] == "users":
        return await stats_users_command(update, context)
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
            if user.get("name"):
                user_lines.append(user["name"])
            elif user.get("user_id"):
                user_lines.append(str(user["user_id"]))
        user_list_text = "\n".join(user_lines)
        await update.message.reply_text(f"<b>All Users:</b>\n<code>{user_list_text}</code>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in /stats users: {e}", exc_info=True)
        await update.message.reply_text("An error occurred while fetching users.")

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

        # Get top weekly explorers
        top_weekly = get_top_explorers(stats_data["weekly_explorers"])

        # Get top daily explorers
        top_daily = get_top_explorers(stats_data["daily_explorers"])

        # Format weekly explorers
        weekly_explorers_text = "\n".join([
            f"{i+1}. {name} - {count} explores"
            for i, (name, count) in enumerate(top_weekly)
        ]) if top_weekly else "No data yet"

        # Format daily explorers
        daily_explorers_text = "\n".join([
            f"{i+1}. {name} - {count} explores"
            for i, (name, count) in enumerate(top_daily)
        ]) if top_daily else "No data yet"

        # Create message
        message = (
            f"<b>Total Users:</b> <code>{total_users}</code>\n"
            f"<b>Total Groups:</b> <code>{total_groups}</code>\n\n"
            f" <b>WEEKLY TOP EXPLORERS</b>:\n{weekly_explorers_text}\n\n"
            f" <b>DAILY TOP EXPLORERS</b> :\n{daily_explorers_text}\n\n"
        )

        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in stats_command: {e}", exc_info=True)
        await update.message.reply_text("An error occurred while processing the command.")

# Hook into the explore command to track statistics
async def track_explore_stats(user_id: str, name: str, battle_completed: bool = False):
    """Track explore statistics for a user"""
    try:
        await update_explorer_stats(user_id, name, battle_completed)
    except Exception as e:
        logger.error(f"Error tracking explore stats: {e}")
