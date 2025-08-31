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
    MISSION_STATUS_IN_PROGRESS, MISSION_STATUS_COMPLETED, MISSION_STATUS_CANCELLED
)
from utils.mod_utils import mod_only

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

    # --- Check for expired missions and cancel them automatically ---
    expired_missions = []
    player_missions = getattr(player, "missions", [])
    now = datetime.now(timezone.utc)
    
    for i, mission_progress in enumerate(player_missions):
        if mission_progress["status"] == MISSION_STATUS_IN_PROGRESS:
            mission_id = mission_progress["mission_id"]
            mission = MISSIONS_BY_ID.get(mission_id)
            
            if mission and mission.time_limit_hours:
                started_at = mission_progress.get("started_at")
                if isinstance(started_at, str):
                    try:
                        started_at = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        continue
                
                if started_at:
                    # Ensure started_at is timezone-aware (UTC)
                    if started_at.tzinfo is None or started_at.tzinfo.utcoffset(started_at) is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    
                    expires_at = started_at + timedelta(hours=mission.time_limit_hours)
                    
                    if now >= expires_at:
                        # Mission has expired, cancel it
                        player_missions[i]["status"] = MISSION_STATUS_CANCELLED
                        player_missions[i]["cancelled_at"] = now
                        expired_missions.append(mission.title)
                        logger.info(f"Auto-cancelled expired mission {mission_id} for player {user_id}")
    
    # Update player data if any missions were cancelled
    if expired_missions:
        await db.update_player(int(user_id), {"missions": player_missions})
        player = await db.get_player(user_id)  # Refresh player data
        active_missions = await get_active_missions(db, player)  # Refresh active missions list

    # --- Mission progress update logic ---
    # For each active mission, update progress if possible (without adding items to inventory)
    progress_notifications = []
    player_updated = False
    for mission_data in active_missions:
        mission = mission_data["definition"]
        progress = mission_data["progress"]
        # --- Collect-type missions (bricks, odm_gear_part, scout_journal) ---
        if mission.id in (5, 8, 12):
            inventory = getattr(player, "inventory", {})
            item_key = None
            if mission.id == 5:
                item_key = "brick"
            elif mission.id == 8:
                item_key = "odm_gear_part"
            elif mission.id == 12:
                item_key = "scout_journal"
            if item_key:
                item_count = inventory.get(item_key, 0)
                new_progress = min(item_count, mission.required_progress)
                if progress["current_progress"] != new_progress:
                    progress_amount = new_progress - progress["current_progress"]
                    if progress_amount > 0:
                        notification = await update_mission_progress(db, player, mission.id, progress_amount)
                        if notification:
                            progress_notifications.append(notification)
                        player_updated = True
        # --- Exploring-type missions ---
        elif mission.id == 1:
            explores = getattr(player, "explores", 0)
            new_progress = min(explores, mission.required_progress)
            if progress["current_progress"] != new_progress:
                progress_amount = new_progress - progress["current_progress"]
                if progress_amount > 0:
                    notification = await update_mission_progress(db, player, mission.id, progress_amount)
                    if notification:
                        progress_notifications.append(notification)
                    player_updated = True
        elif mission.id == 9:
            explores = getattr(player, "explores", 0)
            travel_data = getattr(player, "travel", {})
            if travel_data and not travel_data.get("in_progress", False):
                consecutive_explores = travel_data.get("consecutive_explores", 0)
                new_progress = min(consecutive_explores, mission.required_progress)
            else:
                new_progress = min(explores, mission.required_progress)
            if progress["current_progress"] != new_progress:
                progress_amount = new_progress - progress["current_progress"]
                if progress_amount > 0:
                    notification = await update_mission_progress(db, player, mission.id, progress_amount)
                    if notification:
                        progress_notifications.append(notification)
                    player_updated = True
        elif mission.id == 11:
            weekly_explores = 0
            daily_explores_data = getattr(player, "daily_explores", {})
            mission_start_time = None
            if progress.get("started_at"):
                try:
                    mission_start_time = datetime.fromisoformat(progress["started_at"].replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    mission_start_time = datetime.now(timezone.utc) - timedelta(days=1)
            if isinstance(daily_explores_data, dict) and mission_start_time:
                for date_str, count in daily_explores_data.items():
                    try:
                        date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        if date >= mission_start_time:
                            weekly_explores += count
                    except (ValueError, TypeError):
                        pass
            else:
                weekly_explores = getattr(player, "explores", 0)
            new_progress = min(weekly_explores, mission.required_progress)
            if progress["current_progress"] != new_progress:
                progress_amount = new_progress - progress["current_progress"]
                if progress_amount > 0:
                    notification = await update_mission_progress(db, player, mission.id, progress_amount)
                    if notification:
                        progress_notifications.append(notification)
                    player_updated = True
        elif mission.id == 14:
            mission_area_counts = getattr(player, "mission14_area_counts", {}) or {}
            AREAS = [
                "Orvud", "Krolva", "Mitras", "Royal Capital", "Utopia",
                "Karanes", "Stohess", "Trost", "Shiganshina", "Ehrmich"
            ]
            completed_areas = 0
            for area in AREAS:
                if mission_area_counts.get(area, 0) >= 500:
                    completed_areas += 1
            new_progress = min(completed_areas, mission.required_progress)
            
            # Always update the mission progress to match the actual completed areas count
            if progress["current_progress"] != new_progress:
                progress_amount = new_progress - progress["current_progress"]
                if progress_amount > 0:
                    notification = await update_mission_progress(db, player, mission.id, progress_amount)
                    if notification:
                        progress_notifications.append(notification)
                    player_updated = True
    # Always refresh player object after any progress update
    if player_updated:
        player = await db.get_player(user_id)
        # Recalculate active missions after player refresh
        active_missions = await get_active_missions(db, player)

    # If no active missions, show available missions
    if not active_missions:
        await show_available_missions(update, context, player, db)
    else:
        # Show progress notifications if any
        if progress_notifications:
            user_id = update.effective_user.id if update.effective_user else None
            for msg in progress_notifications:
                if user_id:
                    try:
                        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN)
                    except Exception as e:
                        logger.error(f"Failed to send private mission completion message: {e}")
        
        # Show expired mission notifications if any
        if expired_missions:
            user_id = update.effective_user.id if update.effective_user else None
            if user_id:
                expired_message = "⏰ *Expired Missions Cancelled:*\n\n"
                for mission_title in expired_missions:
                    expired_message += f"❌ {mission_title}\n"
                expired_message += "\n*Time ran out!*"
                
                try:
                    await context.bot.send_message(
                        chat_id=user_id, 
                        text=expired_message, 
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Failed to send expired mission notification: {e}")
        
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
    
    # Create improved message text
    message = "📜 *Available Missions*\n\n"
    for mission in current_page_missions:
        message += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"*{mission.id}. {mission.title}*\n"
            f"_{mission.description}_\n"
            f"*Requirement:* `{mission.requirement}`\n"
            f"*Reward:* `{mission.reward_description}`\n"
        )
        if mission.time_limit_hours:
            message += f"⏳ *Time Limit:* {mission.time_limit_hours} hours\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
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
        

def format_mission_14_progress(player, max_count=500):
    """Format progress for Mission 14: 500 explores in each place of the map."""
    AREAS = [
        "Orvud", "Krolva", "Mitras", "Royal Capital", "Utopia",
        "Karanes", "Stohess", "Trost", "Shiganshina", "Ehrmich"
    ]
    # Use mission-specific counts
    mission_area_counts = getattr(player, "mission14_area_counts", {}) or {}
    lines = []
    completed_areas = 0

    for area in AREAS:
        count = mission_area_counts.get(area, 0)
        if count >= max_count:
            status = "✅"
            completed_areas += 1
        else:
            status = f"{count}/{max_count}"
        lines.append(f"• {area}: {status}")

    # Add summary line
    lines.append(f"\nProgress: {completed_areas}/{len(AREAS)} areas completed")
    return "\n".join(lines)

async def show_active_missions(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                              player, db, active_missions=None) -> None:
    """Show player's active missions"""
    if active_missions is None:
        active_missions = await get_active_missions(db, player)
    
    if not active_missions:
        await show_available_missions(update, context, player, db)
        return
    
    # Create improved message text
    message = "📋 *Active Missions*\n\n"
    completed_missions = []
    keyboard = []
    row = []
    command_user_id = str(update.effective_user.id) if update.effective_user else ""
    for mission_data in active_missions:
        mission = mission_data["definition"]
        progress = mission_data["progress"]
        # For Mission 14, always show latest area_explore_counts progress in UI
        if mission.id == 14:
            area_explore_counts = getattr(player, "area_explore_counts", {}) or {}
            completed_areas = 0
            for area_count in area_explore_counts.values():
                if area_count >= 500:
                    completed_areas += 1
            display_progress = completed_areas
            is_completed = progress["status"] == "completed" or display_progress >= progress["required_progress"]
        else:
            display_progress = progress["current_progress"]
            is_completed = progress["status"] == "completed" or display_progress >= progress["required_progress"]
        message += "━━━━━━━━━━━━━━━━━━━━━━\n"
        if is_completed:
            message += f"*{mission.id}. {mission.title}* ✅\n"
            completed_missions.append(mission["id"])
            # For completed missions, show description with completed status
            message += f"_{mission.description} - Completed! ✅\n"
        else:
            message += f"*{mission.id}. {mission.title}*\n"
            message += f"_{mission.description}_\n"
            message += f"*Requirement:* `{mission.requirement}`\n"
            if mission.id == 14:
                message += "*Progress:*\n"
                message += format_mission_14_progress(player) + "\n"
            else:
                message += f"*Progress:* {display_progress}/{progress['required_progress']}\n"
            message += f"*Reward:* `{mission.reward_description}`\n"
            if hasattr(mission, "time_limit_hours") and mission.time_limit_hours:
                # Calculate remaining time
                started_at = progress.get("started_at")
                if isinstance(started_at, str):
                    try:
                        started_at = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        started_at = None
                
                if started_at:
                    # Ensure started_at is timezone-aware (UTC)
                    if started_at.tzinfo is None or started_at.tzinfo.utcoffset(started_at) is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    expires_at = started_at + timedelta(hours=mission.time_limit_hours)
                    now = datetime.now(timezone.utc)
                    if now < expires_at:
                        hours_left = (expires_at - now).total_seconds() / 3600
                        if hours_left < 1:
                            minutes_left = int(hours_left * 60)
                            message += f"⏳ *Time Left:* {minutes_left} minutes\n"
                        else:
                            message += f"⏳ *Time Left:* {hours_left:.1f} hours\n"
                    else:
                        message += "⏳ *Time Expired!*\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        # Only add button for non-completed missions
        if not is_completed:
            if len(row) == 3:
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
    
    # Special handling for Mission 14 progress display
    if mission_id == 14:
        message += "*Progress:* \n"
        message += format_mission_14_progress(player) + "\n\n"
    else:
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
            # Ensure started_at is timezone-aware (UTC)
            if started_at.tzinfo is None or started_at.tzinfo.utcoffset(started_at) is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
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


@mod_only
@maintenance_protected
@ban_protected
async def reset_mission_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command handler for /resetmission - Reset all user missions and deduct rewards if claimed"""
    if not update.effective_user or not update.message:
        return
        
    user_id = str(update.effective_user.id)
    
    # Determine target user: if replying to someone, use that user, otherwise use command sender
    target_user_id = user_id
    target_user_name = update.effective_user.first_name
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = str(update.message.reply_to_message.from_user.id)
        target_user_name = update.message.reply_to_message.from_user.first_name
    
    # Get DB instance
    db = context.bot_data.get("db")
    if not db:
        db = Database()
        await db.init_db()
        context.bot_data["db"] = db
    
    # Get target player data
    target_player = await db.get_player(target_user_id)
    if not target_player:
        await update.message.reply_text(f"Target user {target_user_name} needs to start the game first! Use /start")
        return
    
    # Get all target player missions
    target_player_missions = getattr(target_player, "missions", [])
    if not target_player_missions:
        await update.message.reply_text(f"📜 No missions found for {target_user_name} to reset.")
        return
    
    # Count missions and calculate total rewards to deduct
    completed_missions = []
    total_marks_deduct = 0
    total_valor_deduct = 0
    reset_count = 0
    
    for mission_progress in target_player_missions:
        if mission_progress["status"] == MISSION_STATUS_COMPLETED:
            mission_id = mission_progress["mission_id"]
            mission = MISSIONS_BY_ID.get(mission_id)
            if mission:
                completed_missions.append(mission)
                reset_count += 1
                
                # Calculate rewards to deduct
                rewards = mission.rewards
                if "marks" in rewards:
                    total_marks_deduct += rewards["marks"]
                if "valor" in rewards:
                    total_valor_deduct += rewards["valor"]
    
    if reset_count == 0:
        await update.message.reply_text(f"📜 No completed missions found for {target_user_name} to reset.")
        return
    
    # Check if target player has enough resources to deduct
    if target_player.marks < total_marks_deduct:
        await update.message.reply_text(f"❌ {target_user_name} doesn't have enough marks! Need {total_marks_deduct} marks, they have {target_player.marks}.")
        return
        
    if target_player.valor < total_valor_deduct:
        await update.message.reply_text(f"❌ {target_user_name} doesn't have enough valor! Need {total_valor_deduct} valor, they have {target_player.valor}.")
        return
    
    # Create confirmation message
    message = f"🔄 *Reset All Missions for {target_user_name}*\n\n"
    message += f"📊 Missions to reset: {reset_count}\n"
    message += f"💰 Total marks to deduct: {total_marks_deduct}\n"
    message += f"⚔️ Total valor to deduct: {total_valor_deduct}\n\n"
    message += "⚠️ *This action cannot be undone!*\n\n"
    message += f"Are you sure you want to reset all completed missions for {target_user_name}?"
    
    # Create confirmation keyboard
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Reset All", callback_data=f"reset_all_confirm_{target_user_id}_{user_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="reset_cancel")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

@mod_only
@maintenance_protected
@ban_protected
async def remission_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command handler for /remission <mission_no> - Reset a specific mission"""
    if not update.effective_user or not update.message:
        return
        
    user_id = str(update.effective_user.id)
    
    # Determine target user: if replying to someone, use that user, otherwise use command sender
    target_user_id = user_id
    target_user_name = update.effective_user.first_name
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = str(update.message.reply_to_message.from_user.id)
        target_user_name = update.message.reply_to_message.from_user.first_name
    
    # Get DB instance
    db = context.bot_data.get("db")
    if not db:
        db = Database()
        await db.init_db()
        context.bot_data["db"] = db
    
    # Get target player data
    target_player = await db.get_player(target_user_id)
    if not target_player:
        await update.message.reply_text(f"Target user {target_user_name} needs to start the game first! Use /start")
        return
    
    # Parse mission number from command
    args = context.args
    if not args:
        await update.message.reply_text("❌ Please specify a mission number. Usage: /remission <mission_no>")
        return
    
    try:
        mission_no = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid mission number. Please use a number.")
        return
    
    # Check if mission exists
    mission = MISSIONS_BY_ID.get(mission_no)
    if not mission:
        await update.message.reply_text(f"❌ Mission #{mission_no} not found.")
        return
    
    # Find the mission in target player's missions
    target_player_missions = getattr(target_player, "missions", [])
    mission_progress = None
    
    for pm in target_player_missions:
        if pm["mission_id"] == mission_no:
            mission_progress = pm
            break
    
    if not mission_progress:
        await update.message.reply_text(f"📜 {target_user_name} hasn't started mission #{mission_no} yet.")
        return
    
    if mission_progress["status"] != MISSION_STATUS_COMPLETED:
        await update.message.reply_text(f"📜 Mission #{mission_no} is not completed for {target_user_name}.")
        return
    
    # Calculate rewards to deduct
    rewards = mission.rewards
    marks_deduct = rewards.get("marks", 0)
    valor_deduct = rewards.get("valor", 0)
    
    # Check if target player has enough resources
    if target_player.marks < marks_deduct:
        await update.message.reply_text(f"❌ {target_user_name} doesn't have enough marks! Need {marks_deduct} marks, they have {target_player.marks}.")
        return
        
    if target_player.valor < valor_deduct:
        await update.message.reply_text(f"❌ {target_user_name} doesn't have enough valor! Need {valor_deduct} valor, they have {target_player.valor}.")
        return
    
    # Reset the mission
    mission_progress["status"] = MISSION_STATUS_CANCELLED
    mission_progress["cancelled_at"] = datetime.now(timezone.utc)
    mission_progress["current_progress"] = 0
    
    # Deduct rewards
    update_data = {"missions": target_player_missions}
    if marks_deduct > 0:
        update_data["marks"] = target_player.marks - marks_deduct
    if valor_deduct > 0:
        update_data["valor"] = target_player.valor - valor_deduct
    
    # Update target player in database
    await db.update_player(int(target_user_id), update_data)
    
    # Send confirmation message
    message = f"✅ *Mission #{mission_no} Reset for {target_user_name}!*\n\n"
    message += f"📜 {mission.title}\n"
    if marks_deduct > 0:
        message += f"💰 Deducted: {marks_deduct} marks\n"
    if valor_deduct > 0:
        message += f"⚔️ Deducted: {valor_deduct} valor\n"
    message += "\n🔄 They can now attempt this mission again!"
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.MARKDOWN
    )

async def reset_mission_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for reset mission-related callback queries"""
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
    
    if callback_data.startswith("reset_all_confirm_"):
        # Extract target_user_id and mod_user_id from callback_data
        parts = callback_data.split('_')
        target_user_id = parts[3]  # reset_all_confirm_{target_user_id}_{mod_user_id}
        mod_user_id = parts[4] if len(parts) > 4 else user_id
        
        # Check if callback is for this mod user
        if mod_user_id != user_id:
            await query.answer("Only the mod who issued the command can use these buttons.", show_alert=True)
            return
        
        # Get target player
        target_player = await db.get_player(target_user_id)
        if not target_player:
            await query.edit_message_text("Target player not found.")
            return
        
        # Reset all completed missions for target player
        await _reset_all_missions(db, target_player, query)
        
    elif callback_data == "reset_cancel":
        await query.edit_message_text("🔄 Mission reset cancelled.")

async def _reset_all_missions(db, target_player, query):
    """Reset all completed missions for a player"""
    target_player_missions = getattr(target_player, "missions", [])
    
    total_marks_deduct = 0
    total_valor_deduct = 0
    reset_count = 0
    
    # Calculate total deductions and reset missions
    for mission_progress in target_player_missions:
        if mission_progress["status"] == MISSION_STATUS_COMPLETED:
            mission_id = mission_progress["mission_id"]
            mission = MISSIONS_BY_ID.get(mission_id)
            if mission:
                # Reset mission
                mission_progress["status"] = MISSION_STATUS_CANCELLED
                mission_progress["cancelled_at"] = datetime.now(timezone.utc)
                mission_progress["current_progress"] = 0
                reset_count += 1
                
                # Calculate rewards to deduct
                rewards = mission.rewards
                if "marks" in rewards:
                    total_marks_deduct += rewards["marks"]
                if "valor" in rewards:
                    total_valor_deduct += rewards["valor"]
    
    # Deduct rewards
    update_data = {"missions": target_player_missions}
    if total_marks_deduct > 0:
        update_data["marks"] = target_player.marks - total_marks_deduct
    if total_valor_deduct > 0:
        update_data["valor"] = target_player.valor - total_valor_deduct
    
    # Update target player in database
    await db.update_player(int(target_player.user_id), update_data)
    
    # Send confirmation message
    message = f"✅ *All Missions Reset for {target_player.user_id}!*\n\n"
    message += f"📊 Missions reset: {reset_count}\n"
    if total_marks_deduct > 0:
        message += f"💰 Marks deducted: {total_marks_deduct}\n"
    if total_valor_deduct > 0:
        message += f"⚔️ Valor deducted: {total_valor_deduct}\n"
    message += "\n🔄 All completed missions have been reset. They can attempt them again!"
    
    await query.edit_message_text(
        message,
        parse_mode=ParseMode.MARKDOWN
    )