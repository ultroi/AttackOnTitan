from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from game.battle_system import cleanup_battle, active_battles
from database.models import Player, Character, Titan, DailyExplores
from database.db import Database
from game.travel_map import TRAVEL_MAP  # Add this import at the top

from datetime import datetime, timezone
from typing import Dict
import random
import logging
import asyncio

logger = logging.getLogger(__name__)

# Rate limiting for explore command
user_last_explore: Dict[str, float] = {}
EXPLORE_COOLDOWN = 3 
DAILY_EXPLORE_LIMIT = 125  # Configurable daily limit

async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /explore command to find titans."""
    if not update.effective_user:
        await _reply_error(update, "Cannot identify user. Please try again.")
        return

    user_id = str(update.effective_user.id)
    username = update.effective_user.username or update.effective_user.first_name or "Unknown"

    try:
        from utils.monitor import track_player_action, remove_player_activity
        track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
    except ModuleNotFoundError:
        logger.warning("utils.monitor not found, skipping activity tracking")

    # Rate limiting check
    current_time = datetime.now(timezone.utc).timestamp()
    db = context.bot_data.get("db")
    if not db:
        logger.error("Database not initialized in context.bot_data")
        await _reply_error(update, "Internal error: Database not initialized.")
        return

    if user_id in user_last_explore:
        time_diff = current_time - user_last_explore[user_id]
        if time_diff < EXPLORE_COOLDOWN:
            remaining = EXPLORE_COOLDOWN - time_diff
            await _reply_error(update, f"⏳ Please wait {remaining:.1f} seconds before exploring again.")
            try:
                remove_player_activity(user_id)
            except NameError:
                pass
            return

    user_last_explore[user_id] = current_time
    
    # Get player data
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You need to create a profile first with /start")
        return
    # MIGRATION: If player.location missing, set from first character's birthplace if available
    if not getattr(player, "location", None):
        chars = await db.get_player_characters(user_id)
        if chars and hasattr(chars[0], "birthplace"):
            player.location = chars[0].birthplace
            await db.update_player(user_id, {"location": player.location})
        
    # Check if this explore should award daily EXP (first 125 explores)
    current_date = datetime.utcnow()
    daily_explores_count = player.get_daily_explores_count(current_date)

    if daily_explores_count >= DAILY_EXPLORE_LIMIT:
        await _reply_error(update, f"You've reached the daily exploration limit ({DAILY_EXPLORE_LIMIT}). Try again tomorrow!")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    explore_exp = player.calculate_exp_gain("daily_explore")
    player.xp += explore_exp
    player.total_xp += explore_exp
    daily_explores_count = player.increment_daily_explores(current_date)
    level_ups = 0
    while player.xp >= player.xp_to_next_level:
        player.level_up()
        level_ups += 1
    update_data = {
        "xp": player.xp,
        "total_xp": player.total_xp,
        "level": player.level,
        "daily_explores": [d.model_dump() for d in player.daily_explores],
        "updated_at": datetime.now(timezone.utc)
    }
    try:
        await db.update_player(player.user_id, update_data)
    except Exception as e:
        logger.error(f"Failed to update player {user_id}: {e}")
        await _reply_error(update, "An error occurred while updating your profile.")
        return

    if not player.team:
        await _reply_error(update, "You need to have at least one character in your team. Use /team to manage your team.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    if user_id in active_battles:
        await _reply_error(update, "⚔️ You're already in an active battle! Finish it before exploring again.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    player_character_name = player.team[0].character_name
    player_character = await db.get_character(user_id, player_character_name)
    if not player_character:
        await _reply_error(update, f"Error: Your character {player_character_name} was not found.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    if player_character.gas < 100:
        await _reply_error(update, f"{player_character_name} doesn't have enough gas to explore (needs at least 100). Use /profile to refill gas.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    player_character.gas -= 100
    try:
        await db.update_character(player_character)
    except Exception as e:
        logger.error(f"Failed to update character {player_character_name} for user {user_id}: {e}")
        await _reply_error(update, "An error occurred while updating your character.")
        return

    # Show EXP gain message for explore
    exp_message = f"🧭 EXP gained: {explore_exp}"

    # --- TRAVEL/DECISION POINT HANDLING ---
    travel = getattr(player, "travel", {})
    location = getattr(player, "location", None)
    # If at a decision point, only show direction options, do not spawn titan
    if location and location in TRAVEL_MAP and location.startswith("Decision_"):
        directions = TRAVEL_MAP[location]
        # Log button creation for debugging
        logger.info(f"[EXPLORE] Creating travel decision buttons: {[f'travel_decision_{dir.strip().lower()}' for dir in directions.keys()]}")
        keyboard = [
            [InlineKeyboardButton(dir, callback_data=f"travel_decision_{dir.strip().lower()}")] for dir in directions.keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await update.message.reply_text(
                f"You are at a decision point: <b>{location}</b>\nChoose a direction to continue your journey:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send decision point reply: {e}")
        finally:
            try:
                remove_player_activity(user_id)
            except NameError:
                pass
        return

    titan = await db.generate_titan(player_character.level, player.unlocked_areas)
    if not titan:
        await _reply_error(update, "No titans found in your level range.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    logger.info(f"Generated titan for user {user_id}: {titan.name} (Level {titan.level}, HP: {titan.max_hp})")

    await db.store_titan(user_id, titan)

    battle_id = f"battle_{user_id}"
    keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    hp_bar_length = min(20, max(1, titan.max_hp // 50))  # Improved scaling
    titan_bar = "█" * hp_bar_length

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

    level_diff = titan.level - player_character.level
    threat = "🟢 MANAGEABLE" if level_diff < 0 else "🟡 MODERATE" if level_diff < 3 else "🔴 DANGEROUS"

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

    encounter_text = random.choice(encounter_texts.get(titan.difficulty, encounter_texts["Normal"]))

    mutant_text = "\n⚠️ <b>WARNING:</b> <i>This appears to be a rare mutant variant!</i>" if "Mutant" in titan.name else ""

    reply_text = (
        f"{encounter_text}\n\n"
        f"🚨 <b>TITAN SPOTTED!</b> 🚨\n\n"
        f"📍 <b>{titan.name}</b>\n"
        f"⚡ <b>Level:</b> {titan.level}\n"
        f"❤️ <b>HP:</b> {titan.max_hp} [{titan_bar}]\n"
        f"⚔️ <b>Difficulty:</b> {titan.difficulty}\n"
        f"🎯 <b>Threat Level:</b> {threat}\n"
        f"{special_abilities_text}{mutant_text}\n\n"
        f"💨 <b>Character:</b> {player_character.name} (Lv. {player_character.level})\n"
        f"💨 <i>Gas cost to explore: 100</i>\n"
        f"{exp_message}\n"
        f"🎮 <b>Ready to engage?</b>"
    )

    try:
        if update.message:
            sent_message = await update.message.reply_text(
                text=reply_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        elif update.callback_query:
            sent_message = await update.callback_query.message.edit_text(
                text=reply_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            sent_message = None
    except Exception as e:
        logger.error(f"Failed to send reply for user {user_id}: {e}")
        await _reply_error(update, "An error occurred while displaying the titan.")
        sent_message = None

    # --- Titan encounter expiration logic ---
    async def titan_encounter_timeout():
        await asyncio.sleep(60)
        titan_in_db = await db.get_titan(user_id)
        if titan_in_db:
            try:
                await db.delete_titan(user_id)
                if sent_message:
                    try:
                        await sent_message.edit_text(
                            "⏰ Titan encounter expired!\n\nYou took too long to respond. Use /explore to find another titan.",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Failed to cleanup expired titan for user {user_id}: {e}")
    titan_timeout_task = asyncio.create_task(titan_encounter_timeout())
    context.bot_data[f"titan_timeout_{user_id}"] = titan_timeout_task
    # --- End titan encounter expiration logic ---
# ...existing code...
async def _reply_error(update: Update, message: str):
    """Helper to reply with error messages."""
    try:
        if update.message:
            await update.message.reply_text(message)
        elif update.callback_query:
            await update.callback_query.answer(message)
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")

async def cleanup_stale_explore_records(max_age_hours: int = 24):
    """Clean up stale explore records to prevent memory leaks."""
    while True:
        try:
            db = Database()
            current_time = datetime.now(timezone.utc).timestamp()
            players = await db.get_all_players()
            stale_users = [
                player.user_id for player in players
                if player.last_explore and (current_time - player.last_explore) > (max_age_hours * 3600)
            ]
            for user_id in stale_users:
                await db.update_player(user_id, {"last_explore": None})
                if user_id in user_last_explore:
                    del user_last_explore[user_id]
            if stale_users:
                logger.info(f"Cleaned up {len(stale_users)} stale explore records")
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"Error in cleanup_stale_explore_records: {e}")
            await asyncio.sleep(3600)

async def force_cleanup_user(user_id: str, db: Database):
    """Force cleanup of all user-related data."""
    try:
        if user_id in active_battles:
            await cleanup_battle(user_id, "forced_cleanup")
        await db.update_player(user_id, {"last_explore": None})
        await db.delete_titan(user_id)
        if user_id in user_last_explore:
            del user_last_explore[user_id]
        try:
            from utils.monitor import remove_player_activity
            remove_player_activity(user_id)
        except ModuleNotFoundError:
            pass
        logger.info(f"Force cleaned up all data for user {user_id}")
    except Exception as e:
        logger.error(f"Error in force_cleanup_user for {user_id}: {e}")

async def start_cleanup_task():
    """Start the cleanup task."""
    asyncio.create_task(cleanup_stale_explore_records())