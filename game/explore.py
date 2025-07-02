from database.characters import get_character_data, AbilityEffect, CharacterData, Ability
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db_instance import get_database
from game.battle_system import cleanup_battle
from database.db import Database
from database.models import Character, Player, Titan
from datetime import datetime
from typing import List, Optional, Dict
import asyncio
import random
import logging
import threading
import time
from pydantic import BaseModel

logger = logging.getLogger(__name__)



active_battles = {}



# Rate limiting for explore command
user_last_explore = {}
EXPLORE_COOLDOWN = 5  # 5 seconds between explores

async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /explore command to find titans."""
    if not update.effective_user:
        if update.message:
            await update.message.reply_text("Cannot identify user. Please try again.")
        elif update.callback_query:
            # Handle case where update comes from callback query
            await update.callback_query.answer("Cannot identify user. Please try again.")
        else:
            # Handle case where neither message nor callback_query is available
            logger.error("Cannot identify user and no way to respond")
        return
    
    if not update.message:
        logger.error("No message object in update")
        return
        
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name or "Unknown"
    
    # Import here to avoid circular import
    try:
        from utils.monitor import track_player_action, remove_player_activity
        track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
    except ImportError:
        pass  # Monitor not available
    
    # Rate limiting check (using timestamp to avoid datetime issues)
    current_time = datetime.now().timestamp()
    if user_id in user_last_explore:
        time_diff = current_time - user_last_explore[user_id]
        if time_diff < EXPLORE_COOLDOWN:
            remaining = EXPLORE_COOLDOWN - time_diff
            await update.message.reply_text(f"⏳ Please wait {remaining:.1f} seconds before exploring again.")
            try:
                remove_player_activity(user_id)
            except:
                pass
            return
    
    user_last_explore[user_id] = current_time
    
    db = await get_database()
    
    # Get player data
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You need to create a profile first with /start")
        return
        
    # Check if this explore should award daily EXP (first 125 explores)
    current_date = datetime.utcnow()
    daily_explores_count = player.get_daily_explores_count(current_date)
    
    if daily_explores_count < 125:
        # Calculate and award daily explore EXP
        explore_exp = player.calculate_exp_gain('daily_explore')
        player.xp += explore_exp
        player.total_xp += explore_exp
        
        # Increment daily explores count
        daily_explores_count = player.increment_daily_explores(current_date)
        
        # Check for level up
        level_ups = 0
        while player.xp >= player.xp_to_next_level:
            player.level_up()
            level_ups += 1
            
        # Update player in database
        update_data = {
            "xp": player.xp,
            "total_xp": player.total_xp,
            "level": player.level,
            "daily_explores": [d.dict() for d in player.daily_explores]  # Convert to dict for storage
        }
        await db.update_player(user_id=player.user_id, update_data=update_data)
        
        # Prepare EXP message
        exp_message = f"\nDaily explore EXP: +{explore_exp:,}"
        if level_ups > 0:
            exp_message += f"\nLevel up! You're now level {player.level}!"
    else:
        exp_message = ""  # No EXP message if over daily limit
    
    try:
        player_data = await db.players.find_one({"user_id": user_id})
        if not player_data:
            await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
            try:
                remove_player_activity(user_id)
            except:
                pass
            return

        player = Player(**player_data)
        if not player.team or len(player.team) == 0:
            await update.message.reply_text("You need to have at least one character in your team. Use /team to manage your team.")
            try:
                remove_player_activity(user_id)
            except:
                pass
            return

        # Check if user is already in an active battle
        if user_id in active_battles:
            await update.message.reply_text("⚔️ You're already in an active battle! Finish it before exploring again.")
            try:
                remove_player_activity(user_id)
            except:
                pass
            return

        team_sorted = sorted(player.team, key=lambda x: x.position)
        character_name = team_sorted[0].character_name
        character = await db.get_character(user_id, character_name)

        if not character:
            await update.message.reply_text(f"Error: Your character {character_name} was not found.")
            try:
                remove_player_activity(user_id)
            except:
                pass
            return

        if character.gas < 100:
            await update.message.reply_text(f"{character_name} doesn't have enough gas to explore (needs at least 100). Use /profile to refill gas.")
            try:
                remove_player_activity(user_id)
            except:
                pass
            return

        character.gas -= 100
        await db.update_character(character)
        
        # Generate random titan for this exploration
        titan = await db.get_random_titan(
            max(1, character.level - 2),
            character.level + 2,
            target_level=character.level,
            unlocked_areas=player.unlocked_areas or ["Trost District", "Karanes District", "Shiganshina District", "Wall Maria", "Wall Rose"]
        )
        
    except Exception as e:
        logger.error(f"Error during exploration setup for user {user_id}: {e}")
        await update.message.reply_text("❌ An error occurred while exploring. Please try again.")
        try:
            remove_player_activity(user_id)
        except:
            pass
        return
    
    # Log titan generation for debugging
    logger.info(f"Generated titan for user {user_id}: {titan.name if titan else 'None'} (Level {titan.level if titan else 'N/A'}, HP: {titan.max_hp if titan else 'N/A'})")

    if not titan:
        await update.message.reply_text("No titans found in your level range.")
        return
    
    context.bot_data[f"last_titan_{user_id}"] = titan.name  # Store last titan name
    context.bot_data[f"last_titan_data_{user_id}"] = titan  # Store complete titan data
    
    # Use a simple battle identifier to avoid issues with long names
    battle_id = f"battle_{user_id}"
    keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Create dynamic HP bar based on actual HP
    hp_bar_length = min(10, max(1, titan.max_hp // 100))  # Scale bar to HP
    titan_bar = "█" * hp_bar_length
    
    # Enhanced titan display with more details
    special_abilities_text = ""
    if titan.special_abilities:
        abilities_formatted = []
        for ability in titan.special_abilities:
            if ability in ["Armor Plating", "Crystal Armor", "Hardening"]:
                abilities_formatted.append(f"🛡️ {ability}")
            elif ability in ["Steam Blast", "Colossal Explosion", "Thunder Spear"]:
                abilities_formatted.append(f"💥 {ability}")
            elif ability in ["Regeneration", "Quick Recovery", "Fast Healing"]:
                abilities_formatted.append(f"💚 {ability}")
            elif ability in ["Berserker Rage", "Primal Scream", "Intimidating Presence"]:
                abilities_formatted.append(f"🔥 {ability}")
            else:
                abilities_formatted.append(f"⚡ {ability}")
        special_abilities_text = f"\n🔥 <b>Special Abilities:</b> {', '.join(abilities_formatted)}"
    
    # Determine threat level based on level difference
    level_diff = titan.level - character.level
    if level_diff >= 3:
        threat = "🔴 DANGEROUS"
    elif level_diff >= 0:
        threat = "🟡 MODERATE"
    else:
        threat = "🟢 MANAGEABLE"
    # Generate dynamic encounter flavor text
    encounter_texts = {
        "Easy": [
            "🌫️ A stumbling titan emerges from the mist...",
            "🚶 A slow-moving titan shambles into view...",
            "😵 A confused titan wanders nearby...",
            "🤕 An injured titan limps into the area..."
        ],
        "Normal": [
            "⚡ A fierce titan charges through the trees!",
            "🔥 An aggressive titan roars in the distance!",
            "🎯 A hunting titan has caught your scent!",
            "💀 A dangerous titan blocks your path!"
        ],
        "Hard": [
            "☠️ A legendary titan emerges from the shadows!",
            "🌋 The ground shakes as a colossal presence appears!",
            "⚫ A nightmare titan materializes before you!",
            "💥 A devastating titan breaks through the wall!"
        ]
    }
    
    encounter_text = random.choice(encounter_texts[titan.difficulty])
    
    # Add mutant detection
    mutant_text = ""
    if "Mutant" in titan.name:
        mutant_text = "\n⚠️ <b>WARNING:</b> <i>This appears to be a rare mutant variant!</i>"
        
    await update.message.reply_text(
        text=(
            f"{encounter_text}\n\n"
            f"🚨 <b>TITAN SPOTTED!</b> 🚨\n\n"
            f"📍 <b>{titan.name}</b>\n"
            f"⚡ <b>Level:</b> {titan.level}\n"
            f"❤️ <b>HP:</b> {titan.max_hp} [{titan_bar}]\n"
            f"⚔️ <b>Difficulty:</b> {titan.difficulty}\n"
            f"🎯 <b>Threat Level:</b> {threat}\n"
            f"{special_abilities_text}{mutant_text}\n\n"
            f"💨 <i>Gas cost to explore: 100</i>\n"
            f"🎮 <b>Ready to engage?</b>"
        ),
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

# Memory management utilities
def cleanup_stale_explore_records(max_age_hours: int = 24):
    """Clean up stale explore records to prevent memory leaks"""
    current_time = datetime.now().timestamp()
    stale_users = []
    
    for user_id, timestamp in user_last_explore.items():
        if current_time - timestamp > (max_age_hours * 3600):
            stale_users.append(user_id)
    
    for user_id in stale_users:
        del user_last_explore[user_id]
    
    if stale_users:
        logger.info(f"Cleaned up {len(stale_users)} stale explore records")

def force_cleanup_user(user_id: int):
    """Force cleanup of all user-related data"""
    try:
        # Remove from active battles
        if user_id in active_battles:
            cleanup_battle(user_id, "forced_cleanup")
        
        # Remove from explore tracking
        if user_id in user_last_explore:
            del user_last_explore[user_id]
        
        # Remove from activity tracking
        try:
            from utils.monitor import remove_player_activity
            remove_player_activity(user_id)
        except ImportError:
            pass
        
        logger.info(f"Force cleaned up all data for user {user_id}")
    except Exception as e:
        logger.error(f"Error in force_cleanup_user for {user_id}: {e}")

# Cleanup stale records periodically (run this from a background task)

def periodic_cleanup():
    """Background task to clean up stale data"""
    while True:
        try:
            time.sleep(3600)  # Run every hour
            cleanup_stale_explore_records(24)  # Clean records older than 24 hours
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}")

# Start cleanup thread (daemon so it doesn't prevent shutdown)
cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
cleanup_thread.start()
