from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.models import Player, Character, Titan, DailyExplores, TITAN_NAME_VARIANTS
from database.db import Database
from game.travel_map import TRAVEL_MAP
from game.captcha import spawn_captcha

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

# Titan type to image URL mapping
# Titan type to image URL mapping
TITAN_TYPE_IMAGE_URLS = {
    "Goofy Grinning": "https://i.ibb.co/dJ6J58s0/image.jpg",
    "Potbellied": "https://i.ibb.co/XkMw0Xt5/image.jpg",
    "Bearded": "https://i.ibb.co/7J8S4s6v/image.jpg",
    "Gaping Mouth": "https://i.ibb.co/9mMK2FG1/image.jpg",
    # Add more titan type to image URL mappings here
}

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

async def titan_encounter_timeout(user_id: int, context: ContextTypes.DEFAULT_TYPE, sent_message=None):
    """Handle titan encounter timeout with proper cleanup."""
    try:
        await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
        
        # Get the latest battle_id for this user
        battle_id_key = f"active_battle_id_{user_id}"
        current_battle_id = context.bot_data.get(battle_id_key)
        
        # Check if there's an active battle
        try:
            from game.battle_system import active_battles
            if str(user_id) in active_battles:
                logger.info(f"Skipping timeout for user {user_id} - active battle in progress")
                return
        except ImportError:
            pass
        
        # Clean up the titan if no battle is active
        db = context.bot_data.get("db")
        if db:
            titan_in_db = await db.get_titan(str(user_id))
            if titan_in_db:
                await db.delete_titan(str(user_id))
                
                # Only edit message if no battle has started
                if sent_message and current_battle_id == context.bot_data.get(battle_id_key):
                    try:
                        await sent_message.edit_text(
                            "⏰ Titan encounter expired!\n\nYou took too long to respond. Use /explore to find another titan.",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        logger.error(f"Failed to edit message for user {user_id}: {e}")
    except Exception as e:
        logger.error(f"Error in titan_encounter_timeout for user {user_id}: {e}")
    finally:
        # Clean up the task reference
        key = f"titan_timeouts_{user_id}"
        if key in context.bot_data:
            tasks = context.bot_data[key]
            # Remove completed tasks
            context.bot_data[key] = [t for t in tasks if not t.done()]

async def cleanup_user_timeouts(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Cancel and clean up all timeout tasks for a user."""
    key = f"titan_timeouts_{user_id}"
    if key in context.bot_data:
        for task in context.bot_data[key]:
            if not task.done():
                task.cancel()
        del context.bot_data[key]


async def close_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close the persistent keyboard menu."""
    await update.message.reply_text(
        "Closing keyboard...",
        reply_markup=ReplyKeyboardRemove()
    )


async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /explore command to find titans."""
    if not update.effective_user:
        await _reply_error(update, "Cannot identify user. Please try again.")
        return

    user_id = update.effective_user.id
    user_id_str = str(user_id)
    username = update.effective_user.username or update.effective_user.first_name or "Unknown"

    # Show persistent keyboard only if not already shown
    keyboard = [
        ["/explore", "/close"]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )

    # Show keyboard only if message is not already using it
    if not (update.message and update.message.reply_markup == reply_markup):
        await update.message.reply_text(
            "",
            reply_markup=reply_markup
        )
    
    # Check for active battle before allowing explore
    try:
        from game.battle_system import active_battles, active_battles_lock
    except ImportError:
        active_battles = {}
        active_battles_lock = None


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

        if user_id_str in user_last_explore:
            time_diff = current_time - user_last_explore[user_id_str]
            if time_diff < EXPLORE_COOLDOWN:
                remaining = EXPLORE_COOLDOWN - time_diff
                await _reply_error(update, f"⏳ Please wait {remaining:.1f} seconds before exploring again.")
                try:
                    remove_player_activity(user_id)
                except NameError:
                    pass
                return

        user_last_explore[user_id_str] = current_time
        
        # Get player data
        player = await db.get_player(user_id_str)
        
        # Get player data
        player = await db.get_player(user_id_str)
        if not player:
            await update.message.reply_text("You need to create a profile first with /start")
            return

        # Set default location if not set
        if not getattr(player, "location", None):
            chars = await db.get_player_characters(user_id_str)
            if chars and hasattr(chars[0], "birthplace"):
                player.location = chars[0].birthplace
                await db.update_player(user_id_str, {"location": player.location})

        # Handle daily explores and XP
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

        # Update player data if changed
        if player.xp != old_xp or player.level != old_level:
            update_data = {
                "xp": player.xp,
                "total_xp": player.total_xp,
                "level": player.level,
                "daily_explores": [d.model_dump() for d in player.daily_explores],
                "updated_at": datetime.now(timezone.utc)
            }


            try:
                await db.update_player(user_id_str, update_data)
                await db.update_player(user_id_str, update_data)
            except Exception as e:
                logger.error(f"Failed to update player {user_id}: {e}")
                await _reply_error(update, "An error occurred while updating your profile.")
                return

        # Check team requirements
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

        # Show EXP gain message for explore
        exp_message = f"🧭 EXP gained: {explore_exp}"

        # Handle travel/decision points
        travel = getattr(player, "travel", {})
        location = getattr(player, "location", None)
        
        # If at a decision point, show direction options
        if location and location in TRAVEL_MAP and location.startswith("Decision_"):
            directions = TRAVEL_MAP[location]
            keyboard = [
                [InlineKeyboardButton(dir, callback_data=f"travel_decision_{dir.strip().lower()}")]
                for dir in directions.keys()
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

        # Spawn CAPTCHA with 6% chance
        if random.random() < 0.06:
            captcha_triggered = await spawn_captcha(update, context)
            if captcha_triggered:
                try:
                    remove_player_activity(user_id)
                except NameError:
                    pass
                return

        # Generate and display titan
        titan = await db.generate_titan(player_character.level, player.unlocked_areas)
        if not titan:
            await _reply_error(update, "No titans found in your level range.")
            try:
                remove_player_activity(user_id)
            except NameError:
                pass
            return

        logger.info(f"Generated titan for user {user_id}: {titan.name} (Level {titan.level}, HP: {titan.max_hp})")


        # Store titan in database
        await db.store_titan(user_id_str, titan)

        # Generate battle ID and store it
        battle_id = f"battle_{user_id}_{uuid4().hex}"
        context.bot_data[f"active_battle_id_{user_id}"] = battle_id
        
        
        # Create battle button
        keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Find appropriate titan image
        titan_image_url = None
        for difficulty, titan_types in TITAN_NAME_VARIANTS.items():
            for titan_type in titan_types:
                if titan_type in titan.name and titan_type in TITAN_TYPE_IMAGE_URLS:
                    titan_image_url = TITAN_TYPE_IMAGE_URLS[titan_type]
                    break
            if titan_image_url:
                break

        # Prepare encounter message
        image_embed = f'<a href="{titan_image_url}">!</a>' if titan_image_url else ""
        reply_text = (
            f"<code>-------------------------</code>\n"
            f"📍 <b>{titan.name} Lvl ({titan.level})</b>\n"
            f"<b>has blocked your way{image_embed}</b>\n"
            f"<code>-------------------------</code>\n"
        )

        # Send message with battle button
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

        # Start timeout task
        if sent_message:
            titan_timeout_task = asyncio.create_task(
                titan_encounter_timeout(user_id, context, sent_message)
            )
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

    # Clean up stale explore records
    try:
        max_age = 24 * 3600  # 24 hours
        now = datetime.now(timezone.utc).timestamp()
        for uid in list(user_last_explore.keys()):
            if now - user_last_explore[uid] > max_age:
                user_last_explore.pop(uid, None)
    except Exception as e:
        logger.warning(f"Error cleaning up user_last_explore: {e}")

async def cleanup_stale_explore_records(max_age_hours: int = 24):
    """Clean up stale explore records to prevent memory leaks."""
    while True:
        try:
            current_time = datetime.now(timezone.utc).timestamp()
            # Prune user_last_explore
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
        from game.battle_system import cleanup_battle, active_battles
        from game.battle_system import cleanup_battle, active_battles
        user_id_str = str(user_id)
        if user_id_str in active_battles:
            try:
                cleanup_battle(user_id_str, "forced_cleanup")
            except Exception as e:
                logger.warning(f"Error cleaning up battle for user {user_id}: {e}")
            active_battles.pop(user_id_str, None)
        user_last_explore.pop(user_id_str, None)
        await db.update_player(user_id_str, {"last_explore": None})
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
