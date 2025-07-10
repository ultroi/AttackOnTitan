from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.models import Player, Character, Titan, DailyExplores, TITAN_NAME_VARIANTS
from database.db import Database
from game.travel_map import TRAVEL_MAP  # Add this import at the top

from datetime import datetime, timezone
from typing import Dict
import random
import logging
import asyncio
from uuid import uuid4

logger = logging.getLogger(__name__)

# Rate limiting for explore command
user_last_explore: Dict[str, float] = {}
EXPLORE_COOLDOWN = 3 
TITAN_TIMEOUT_SECONDS = 60

# Titan type to image URL mapping (add URLs for types you have images for)
TITAN_TYPE_IMAGE_URLS = {
    "Goofy Grinning": "https://i.ibb.co/dJ6J58s0/image.jpg",
    "Potbellied": "https://i.ibb.co/XkMw0Xt5/image.jpg",
    "Bearded": "https://i.ibb.co/7J8S4s6v/image.jpg",
    "Gaping Mouth": "https://i.ibb.co/9mMK2FG1/image.jpg",

    # Add more titan type to image URL mappings here
}

# Move _reply_error above all usages
async def _reply_error(update: Update, message: str):
    """Helper to reply with error messages."""
    try:
        if hasattr(update, "message") and update.message:
            if hasattr(update.message, "reply_text"):
                await update.message.reply_text(message)
        elif hasattr(update, "callback_query") and update.callback_query:
            if hasattr(update.callback_query, "answer"):
                await update.callback_query.answer(message)
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")


async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /explore command to find titans."""
    if not update.effective_user:
        await _reply_error(update, "Cannot identify user. Please try again.")
        return

    user_id = update.effective_user.id
    user_id_str = str(user_id)
    username = update.effective_user.username or update.effective_user.first_name or "Unknown"
    
    # Check for active battle before allowing explore
    try:
        from game.battle_system import active_battles, active_battles_lock
    except ImportError:
        active_battles = {}
        active_battles_lock = None
    user_id_str = str(user_id)
    if active_battles_lock:
        async with active_battles_lock:
            if user_id_str in active_battles:
                first_name = update.effective_user.first_name or "Player"
                await _reply_error(update, f"{first_name} is currently battling !!")
                try:
                    from utils.monitor import remove_player_activity
                    remove_player_activity(user_id)
                except Exception:
                    pass
                return
    else:
        if user_id_str in active_battles:
            first_name = update.effective_user.first_name or "Player"
            await _reply_error(update, f"{first_name} is currently battling !!")
            try:
                from utils.monitor import remove_player_activity
                remove_player_activity(user_id)
            except Exception:
                pass
            return

    try:
        from utils.monitor import track_player_action, remove_player_activity
        track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
    except ModuleNotFoundError:
        logger.warning("utils.monitor not found, skipping activity tracking")
    except Exception as e:
        logger.error(f"Error in track_player_action: {e}")

    try:
        # Rate limiting check
        current_time = datetime.now(timezone.utc).timestamp()
        db = context.bot_data.get("db")
        if db is None:
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

        user_last_explore[user_id_str] = current_time
        # --- OPTIMIZED: Fetch player and character in parallel for speed ---
        player_task = asyncio.create_task(db.get_player(user_id_str))
        # We'll need the team name, so fetch player first, then character
        player = await player_task
        if not player:
            await update.message.reply_text("You need to create a profile first with /start")
            return
        if not getattr(player, "location", None):
            chars = await db.get_player_characters(user_id_str)
            if chars and hasattr(chars[0], "birthplace"):
                player.location = chars[0].birthplace
                await db.update_player(user_id_str, {"location": player.location})
        # --- Only update player if EXP or level actually changed ---
        current_date = datetime.utcnow()
        daily_explores_count = player.get_daily_explores_count(current_date)
        explore_exp = player.calculate_exp_gain("daily_explore")
        old_xp, old_level = player.xp, player.level
        player.xp += explore_exp
        player.total_xp += explore_exp
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
        if player.xp != old_xp or player.level != old_level:
            try:
                await db.update_player(player.user_id, update_data)
            except Exception as e:
                logger.error(f"Failed to update player {user_id}: {e}")
                await _reply_error(update, "An error occurred while updating your profile.")
                return
        # --- Only fetch character if team exists ---
        if not player.team:
            await _reply_error(update, "You need to have at least one character in your team. Use /team to manage your team.")
            try:
                remove_player_activity(user_id)
            except NameError:
                pass
            return
        player_character_name = player.team[0].character_name
        player_character = await db.get_character(user_id_str, player_character_name)
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
        # --- Only update character if something changed (placeholder for future optimization) ---
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

        logger.info(f"[STORE_TITAN] Storing titan for user_id: {str(user_id)} (type: {type(user_id)})")
        await db.store_titan(str(user_id), titan)

        # Generate a unique battle_id for this encounter
        battle_id = f"battle_{user_id}_{uuid4().hex}"
        # Store the latest battle_id for this user in bot_data
        context.bot_data[f"active_battle_id_{user_id}"] = battle_id
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

        # --- MINIMAL TITAN ENCOUNTER MESSAGE WITH EMBEDDED IMAGE ---
        titan_image_url = None
        for difficulty, titan_types in TITAN_NAME_VARIANTS.items():
            for titan_type in titan_types:
                if titan_type in titan.name and titan_type in TITAN_TYPE_IMAGE_URLS:
                    titan_image_url = TITAN_TYPE_IMAGE_URLS[titan_type]
                    break
            if titan_image_url:
                break
        if titan_image_url:
            # Embed image as clickable '?' in the message text
            image_embed = f'<a href="{titan_image_url}">!</a>'
        else:
            image_embed = ""
        reply_text = (
            f"<code>-------------------------</code>\n"
            f"📍 <b>{titan.name} Lvl ({titan.level})</b>\n"
            f"<b>has blocked your way{image_embed}</b>\n"
            f"<code>-------------------------</code>\n"
        )
        try:
            if update.message:
                sent_message = await update.message.reply_text(
                    text=reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
            elif update.callback_query:
                sent_message = await update.callback_query.message.edit_text(
                    text=reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
        except Exception as e:
            logger.error(f"Failed to send reply for user {user_id}: {e}")
            await _reply_error(update, "An error occurred while displaying the titan.")
            sent_message = None

        # Fix: Defensive edit_text for sent_message
        async def titan_encounter_timeout():
            await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
            # Import active_battles here to avoid circular import issues
            try:
                from game.battle_system import active_battles
            except ImportError:
                active_battles = {}
            if user_id in active_battles:
                logger.info(f"Skipping timeout for user {user_id} - active battle in progress")
                return
            if db is not None:
                titan_in_db = await db.get_titan(user_id)
                if titan_in_db:
                    try:
                        await db.delete_titan(str(user_id))
                        if sent_message:
                            try:
                                if hasattr(sent_message, "edit_text") and callable(getattr(sent_message, "edit_text", None)):
                                    await sent_message.edit_text(
                                        "⏰ Titan encounter expired!\n\nYou took too long to respond. Use /explore to find another titan.",
                                        parse_mode=ParseMode.HTML
                                    )
                            except Exception:
                                pass
                    except Exception as e:
                        logger.error(f"Failed to cleanup expired titan for user {user_id}: {e}")
        # --- Store all titan timeout tasks in a list ---
        titan_timeout_task = asyncio.create_task(titan_encounter_timeout())
        key = f"titan_timeouts_{user_id}"
        if key not in context.bot_data:
            context.bot_data[key] = []
        context.bot_data[key].append(titan_timeout_task)

    except Exception as e:
        logger.error(f"Error in explore command: {e}")
        await _reply_error(update, "An error occurred while exploring. Please try again.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass

    # --- After explore, cleanup stale explore records for this user ---
    try:
        # Remove from user_last_explore if too old or after battle
        max_age = 24 * 3600  # 24 hours
        now = datetime.now(timezone.utc).timestamp()
        for uid in list(user_last_explore.keys()):
            if now - user_last_explore[uid] > max_age:
                user_last_explore.pop(uid, None)
    except Exception as e:
        logger.warning(f"Error cleaning up user_last_explore: {e}")

async def cancel_titan_timeout(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any pending titan timeout for a user."""
    try:
        if context.user_data is not None and "titan_timeout_task" in context.user_data:
            task = context.user_data["titan_timeout_task"]
            task.cancel()
            del context.user_data["titan_timeout_task"]
            logger.info(f"Cancelled titan timeout for user {user_id}")
    except Exception as e:
        logger.error(f"Error cancelling titan timeout for user {user_id}: {e}")


async def cleanup_stale_explore_records(max_age_hours: int = 24):
    """Clean up stale explore records to prevent memory leaks."""
    while True:
        try:
            db = Database()
            current_time = datetime.now(timezone.utc).timestamp()
            # Prune user_last_explore to avoid unbounded growth
            for uid in list(user_last_explore.keys()):
                if current_time - user_last_explore[uid] > (max_age_hours * 3600):
                    user_last_explore.pop(uid, None)
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"Error in cleanup_stale_explore_records: {e}")
            await asyncio.sleep(3600)


async def force_cleanup_user(user_id: int, db: Database):
    """Force cleanup of all user-related data."""
    try:
        from game.battle_system import cleanup_battle, active_battles  # Import here to avoid circular import
        user_id_str = str(user_id)
        if user_id_str in active_battles:
            try:
                cleanup_battle(user_id_str, "forced_cleanup")
            except Exception as e:
                logger.warning(f"Error cleaning up battle for user {user_id}: {e}")
            active_battles.pop(user_id_str, None)
        user_last_explore.pop(user_id_str, None)
        await db.update_player(user_id, {"last_explore": None})
        await db.delete_titan(user_id_str)
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
