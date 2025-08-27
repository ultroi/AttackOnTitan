import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from utils.maintenance import maintenance_protected
from utils.ban_utils import ban_protected
from database.db import Database
from database.missions import (
    MISSIONS_BY_ID, MISSION_DEFINITIONS,
    get_available_missions, get_active_missions, 
    start_mission, cancel_mission, update_mission_progress,
    process_item_use_mission_progress, process_titan_reward_mission_progress,
    process_pvp_mission_progress, process_explore_mission_progress,
    process_travel_mission_progress,
    MISSION_STATUS_IN_PROGRESS, MISSION_STATUS_COMPLETED
)

logger = logging.getLogger(__name__)

# Page size for pagination
MISSIONS_PER_PAGE = 5

@maintenance_protected
@ban_protected
async def missions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command handler for /missions"""
    if not update.effective_user or not update.message:
        return
        
    user_id = str(update.effective_user.id)
    
    # Get DB instance
    db = context.bot_data.get("db")
    if not db:
        db = Database()
        await db.init_db()
        context.bot_data["db"] = db
    
    # Get player data
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You need to start the game first! Use /start")
        return
        
    # Default to showing active missions first
    active_missions = await get_active_missions(db, player)
    
    # If no active missions, show available missions
    if not active_missions:
        await show_available_missions(update, context, player, db)
    else:
        await show_active_missions(update, context, player, db, active_missions)

async def show_available_missions(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                 player, db, page=0) -> None:
    """Show available missions to the player"""
    available_missions = await get_available_missions(db, player)
    
    if not available_missions:
        await update.message.reply_text(
            "📜 *No missions available!*\n\n"
            "Complete current missions or level up to unlock more.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Create paginated view
    start_idx = page * MISSIONS_PER_PAGE
    end_idx = min(start_idx + MISSIONS_PER_PAGE, len(available_missions))
    current_page_missions = available_missions[start_idx:end_idx]
    
    # Create message text
    message = "📜 *Available Missions*\n\n"
    
    for mission in current_page_missions:
        message += f"*{mission.id}. {mission.title}*\n"
        message += f"_{mission.description}_\n\n"
    
    # Create keyboard with mission selection buttons
    keyboard = []
    row = []
    
    command_user_id = str(update.effective_user.id) if update.effective_user else ""
    for i, mission in enumerate(current_page_missions):
        if i % 3 == 0 and i > 0:
            keyboard.append(row)
            row = []
        row.append(InlineKeyboardButton(f"{mission.id}", callback_data=f"mission_view_{mission.id}_{command_user_id}"))
    
    if row:
        keyboard.append(row)
    
    # Add navigation buttons if needed
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"mission_page_{page-1}_{command_user_id}"))
    
    if end_idx < len(available_missions):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"mission_page_{page+1}_{command_user_id}"))
    
    if nav_row:
        keyboard.append(nav_row)
    
    # Add button to view active missions if any
    active_missions = await get_active_missions(db, player)
    if active_missions:
        keyboard.append([InlineKeyboardButton("📋 View Active Missions", callback_data="mission_active")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send message with keyboard
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

async def show_active_missions(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                              player, db, active_missions=None) -> None:
    """Show player's active missions"""
    if active_missions is None:
        active_missions = await get_active_missions(db, player)
    
    if not active_missions:
        await show_available_missions(update, context, player, db)
        return
    
    # Create message text
    message = "📋 *Active Missions*\n\n"
    
    for mission_data in active_missions:
        mission = mission_data["definition"]
        progress = mission_data["progress"]
        
        message += f"*{mission.id}. {mission.title}*\n"
        message += f"Progress: {progress['current_progress']}/{progress['required_progress']}\n\n"
    
    # Create keyboard with mission selection buttons for details
    keyboard = []
    row = []
    
    command_user_id = str(update.effective_user.id) if update.effective_user else ""
    for i, mission_data in enumerate(active_missions):
        mission = mission_data["definition"]
        if i % 3 == 0 and i > 0:
            keyboard.append(row)
            row = []
        row.append(InlineKeyboardButton(f"{mission.id}", callback_data=f"mission_detail_{mission.id}_{command_user_id}"))
    
    if row:
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send message with keyboard
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

async def show_mission_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             mission_id: int, player, db) -> None:
    """Show details of a specific active mission"""
    # Get player's active missions
    player_missions = getattr(player, "missions", [])
    
    # Find the specific mission
    mission_progress = None
    for pm in player_missions:
        if pm["mission_id"] == mission_id and pm["status"] == MISSION_STATUS_IN_PROGRESS:
            mission_progress = pm
            break
    
    if not mission_progress:
        await update.callback_query.answer("Mission not found or not active.")
        return
    
    # Get mission definition
    mission = MISSIONS_BY_ID.get(mission_id)
    if not mission:
        await update.callback_query.answer("Mission definition not found.")
        return
    
    # Create message with mission details
    message = f"📋 *Mission #{mission_id}: {mission.title}*\n\n"
    message += f"{mission.description}\n\n"
    message += f"*Requirement:* {mission.requirement}\n"
    message += f"*Progress:* {mission_progress['current_progress']}/{mission_progress['required_progress']}\n\n"
    message += f"*Reward:* {mission.reward_description}\n"
    
    # Add time limit info if applicable
    if mission.time_limit_hours:
        started_at = mission_progress.get("started_at")
        if isinstance(started_at, str):
            try:
                started_at = datetime.fromisoformat(started_at)
            except ValueError:
                started_at = None
        
        if started_at:
            expires_at = started_at + timedelta(hours=mission.time_limit_hours)
            now = datetime.now(timezone.utc)
            
            if now < expires_at:
                hours_left = (expires_at - now).total_seconds() / 3600
                message += f"\n*Time Remaining:* {hours_left:.1f} hours"
            else:
                message += "\n*Time Expired!*"
    
    # Create keyboard with cancel button and back button
    command_user_id = str(update.effective_user.id) if update.effective_user else ""
    keyboard = [
        [InlineKeyboardButton("❌ Cancel Mission", callback_data=f"mission_cancel_{mission_id}_{command_user_id}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def show_mission_view(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                           mission_id: int, player, db) -> None:
    """Show detailed view of a mission with accept/decline options"""
    mission = MISSIONS_BY_ID.get(mission_id)
    if not mission:
        await update.callback_query.answer("Mission not found.")
        return
    
    # Check if mission is already active or completed
    player_missions = getattr(player, "missions", [])
    for pm in player_missions:
        if pm["mission_id"] == mission_id:
            if pm["status"] == MISSION_STATUS_COMPLETED:
                await update.callback_query.answer("You have already completed this mission.")
                return
            if pm["status"] == MISSION_STATUS_IN_PROGRESS:
                await update.callback_query.answer("This mission is already in progress.")
                return
    
    # Create message with mission details
    message = f"📜 *Mission #{mission_id}: {mission.title}*\n\n"
    message += f"{mission.description}\n\n"
    message += f"*Requirement:* {mission.requirement}\n"
    message += f"*Reward:* {mission.reward_description}\n"
    
    # Add time limit info if applicable
    if mission.time_limit_hours:
        message += f"\n*Time Limit:* {mission.time_limit_hours} hours"
    
    # Create keyboard with accept/decline buttons
    command_user_id = str(update.effective_user.id) if update.effective_user else ""
    keyboard = [
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"mission_accept_{mission_id}_{command_user_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"mission_available_{command_user_id}")
        ],
        [InlineKeyboardButton("⬅️ Back to Available Missions", callback_data=f"mission_available_{command_user_id}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def missions_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for missions-related callback queries"""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    # Answer callback query to clear the loading icon
    await query.answer()
    
    user_id = str(update.effective_user.id)
    
    # Get DB instance
    db = context.bot_data.get("db")
    if not db:
        db = Database()
        await db.init_db()
        context.bot_data["db"] = db
    
    # Get player data
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("You need to start the game first! Use /start")
        return
    
    # Parse callback data
    callback_data = query.data
    # Extract user_id from callback_data if present (always last part after last '_')
    parts = callback_data.split('_')
    cb_user_id = parts[-1] if parts[-1].isdigit() else None
    # If callback is not for this user, block access
    if cb_user_id and cb_user_id != user_id:
        await query.answer("Only the user who issued the command can use these buttons.", show_alert=True)
        return

    # Remove user_id from callback_data for action parsing
    if cb_user_id:
        callback_data = '_'.join(parts[:-1])

    # Handle different callback actions
    if callback_data == "mission_available":
        await show_available_missions(update, context, player, db)
    elif callback_data == "mission_active":
        await show_active_missions(update, context, player, db)
    elif callback_data.startswith("mission_page_"):
        page = int(callback_data.split("_")[-1])
        await show_available_missions(update, context, player, db, page)
    elif callback_data.startswith("mission_view_"):
        mission_id = int(callback_data.split("_")[-1])
        await show_mission_view(update, context, mission_id, player, db)
    elif callback_data.startswith("mission_detail_"):
        mission_id = int(callback_data.split("_")[-1])
        await show_mission_detail(update, context, mission_id, player, db)
    elif callback_data.startswith("mission_accept_"):
        mission_id = int(callback_data.split("_")[-1])
        success, message = await start_mission(db, player, mission_id)
        if success:
            player = await db.get_player(user_id)
            await query.edit_message_text(
                f"✅ {message}\n\nUse /missions to check your mission progress.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.answer(message)
    elif callback_data.startswith("mission_cancel_"):
        mission_id = int(callback_data.split("_")[-1])
        success, message = await cancel_mission(db, player, mission_id)
        if success:
            player = await db.get_player(user_id)
            active_missions = await get_active_missions(db, player)
            if active_missions:
                await show_active_missions(update, context, player, db, active_missions)
            else:
                await show_available_missions(update, context, player, db)
        else:
            await query.answer(message)


