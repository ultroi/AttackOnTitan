from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.models import Player, Character, Titan, DailyExplores, TITAN_NAME_VARIANTS
from database.db import Database
from game.travel_map import TRAVEL_MAP
from game.captcha import spawn_captcha
from utils.ban_utils import ban_protected
from datetime import datetime, timezone
from typing import Dict
import time
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
TITAN_TYPE_IMAGE_URLS = {
    "Goofy Grinning": "https://i.ibb.co/dJ6J58s0/image.jpg",
    "Potbellied": "https://i.ibb.co/XkMw0Xt5/image.jpg",
    "Bearded": "https://i.ibb.co/7J8S4s6v/image.jpg",
    "Gaping Mouth": "https://i.ibb.co/9mMK2FG1/image.jpg",
}

# Pre-generated titan pool per user
PREGENERATED_TITANS: Dict[str, list] = {}
PREGEN_POOL_SIZE = 3

async def get_pregenerated_titan(user_id_str, db, player_character, unlocked_areas):
    pool = PREGENERATED_TITANS.get(user_id_str, [])
    if pool:
        titan = pool.pop(0)
        PREGENERATED_TITANS[user_id_str] = pool
        # Refill pool in background
        if len(pool) < PREGEN_POOL_SIZE:
            asyncio.create_task(refill_titan_pool(user_id_str, db, player_character, unlocked_areas))
        return titan
    else:
        titan = await db.generate_titan(player_character.level, unlocked_areas)
        # Refill pool in background
        asyncio.create_task(refill_titan_pool(user_id_str, db, player_character, unlocked_areas))
        return titan

async def refill_titan_pool(user_id_str, db, player_character, unlocked_areas):
    pool = PREGENERATED_TITANS.get(user_id_str, [])
    while len(pool) < PREGEN_POOL_SIZE:
        titan = await db.generate_titan(player_character.level, unlocked_areas)
        pool.append(titan)
    PREGENERATED_TITANS[user_id_str] = pool

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

async def reset_explore_timer(user_id, db):
    """Reset the explore_start_time for a user (for inactivity/hCaptcha logic)."""
    await db.update_player(user_id, {"explore_start_time": None})
    logger.info(f"[DEBUG] explore_start_time reset for user {user_id}")

@ban_protected
async def close_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close the persistent keyboard menu."""
    if update.message:
        await update.message.reply_text(
            "Closing keyboard...",
            reply_markup=ReplyKeyboardRemove()
        )

@ban_protected
async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /explore command to find titans."""
    if not update.effective_user:
        await _reply_error(update, "Cannot identify user. Please try again.")
        return

    user_id = update.effective_user.id
    user_id_str = str(user_id)
    username = update.effective_user.username or update.effective_user.first_name or "Unknown"

    # Show persistent keyboard only the first time
    if context.user_data is not None and not context.user_data.get("persistent_keyboard_sent"):
        keyboard = [["/explore", "/close"]]
        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False
        )
        send_keyboard = None
        if update.message:
            send_keyboard = update.message.reply_text
        elif update.callback_query and update.callback_query.message:
            send_keyboard = update.callback_query.message.reply_text
        if send_keyboard:
            try:
                await send_keyboard(
                    "Opening keyboard...",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Failed to send persistent keyboard: {e}")
        context.user_data["persistent_keyboard_sent"] = True
    

    # Get player data (only once)
    db = context.bot_data.get("db")
    if db is None:
        logger.error("Database not initialized in context.bot_data")
        await _reply_error(update, "Internal error: Database not initialized.")
        return

        # Check if user is banned
        banned = False
        try:
            ban_info = await db.get_ban(user_id_str)
            if ban_info and ban_info.get("expiry") is None:
                banned = True
        except Exception as e:
            logger.error(f"Error checking ban status: {e}")
        if banned:
            await _reply_error(update, "You are banned and cannot explore.")
            return

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
                await reset_explore_timer(user_id, db)
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
            await reset_explore_timer(user_id, db)
            return

    try:
        from utils.monitor import track_player_action, remove_player_activity
        track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
    except ModuleNotFoundError:
        logger.warning("utils.monitor not found, skipping activity tracking")
    except Exception as e:
        logger.error(f"Error in track_player_action: {e}")

    # Get player data (only once)
    db = context.bot_data.get("db")
    if db is None:
        logger.error("Database not initialized in context.bot_data")
        await _reply_error(update, "Internal error: Database not initialized.")
        return
    

    player = await db.get_player(user_id_str)
    if not player:
        if update.message:
            await update.message.reply_text("You need to create a profile first with /start")
        return

    # Always check and reset hcaptcha flags after verification
    hcaptcha_verified = getattr(player, "hcaptcha_verified", False)
    if hcaptcha_verified:
        # Reset explore_start_time and hcaptcha_prompted so user can explore freely
        await db.update_player(user_id, {"explore_start_time": time.time()})
        if context.user_data is not None:
            context.user_data["hcaptcha_prompted"] = False

    # Track explore time in database
    now = time.time()
    explore_start = getattr(player, "explore_start_time", None)
    if explore_start is None:
        explore_start = now
        await db.update_player(user_id, {"explore_start_time": now})
    total_explore_time = now - explore_start
    INACTIVITY_THRESHOLD = 2 * 60  # 2 minutes
    logger.info(f"[DEBUG] explore_start_time: {explore_start}, now: {now}, total_explore_time: {total_explore_time}, hcaptcha_verified: {getattr(player, 'hcaptcha_verified', False)}, hcaptcha_prompted: {context.user_data.get('hcaptcha_prompted', False) if context.user_data is not None else False}")
    logger.info(f"[DEBUG] inactivity check: total_explore_time={total_explore_time}, hcaptcha_verified={getattr(player, 'hcaptcha_verified', False)}, hcaptcha_prompted={context.user_data.get('hcaptcha_prompted', False) if context.user_data is not None else False}")

    # Check hCaptcha verification status
    hcaptcha_prompted = context.user_data.get("hcaptcha_prompted", False) if context.user_data is not None else False
    # Prompt hCaptcha if inactive for > 2 minutes and not verified
    if total_explore_time > INACTIVITY_THRESHOLD and not hcaptcha_verified:
        if not hcaptcha_prompted:
            if context.user_data is not None:
                context.user_data["hcaptcha_prompted"] = True
            hcaptcha_url = f"https://attackontitan-j5yh.onrender.com/hcaptcha?user_id={user_id}"
            if update.message is not None and hasattr(update.message, "reply_text"):
                await update.message.reply_text(
                    "🔒 <b>Verification Required</b>\n\n"
                    "You must complete hCaptcha to continue exploring.\n",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Verify with hCaptcha", url=hcaptcha_url)]
                    ]),
                    parse_mode=ParseMode.HTML
                )
            return
        else:
            # Block all explore actions until verified
            await _reply_error(update, "Please complete hCaptcha verification to continue exploring.")
            return
    else:
        # Reset prompted flag if active and verified
        if context.user_data is not None and hcaptcha_verified:
            context.user_data["hcaptcha_prompted"] = False

    # Check team requirements
    if not player.team:
        await _reply_error(update, "You need to have at least one character in your team. Use /inv to manage your team.")
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

    # Handle travel/decision points
    location = getattr(player, "location", None)
    if location and location in TRAVEL_MAP and location.startswith("Decision_"):
        directions = TRAVEL_MAP[location]
        keyboard = [
            [InlineKeyboardButton(dir, callback_data=f"travel_decision_{dir.strip().lower()}")]
            for dir in directions.keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            if update.message:
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


    # --- LOGGING DELAY START ---
    
    start_time = time.time()
    titan = await get_pregenerated_titan(user_id_str, db, player_character, player.unlocked_areas)
    titan_gen_time = time.time()
    logger.info(f"Titan generation (pregenerated) took {titan_gen_time - start_time:.3f} seconds.")
    if not titan:
        await _reply_error(update, "No titans found in your level range.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    await db.store_titan(user_id_str, titan)

    battle_id = f"battle_{user_id}_{uuid4().hex}"
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id

    keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    titan_image_url = None
    titan_name_lower = titan.name.lower()
    for titan_type, url in TITAN_TYPE_IMAGE_URLS.items():
        if titan_type.lower() in titan_name_lower:
            titan_image_url = url
            break

    image_embed = f'<a href="{titan_image_url}">!</a>' if titan_image_url else ""
    reply_text = (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{titan.name} Lvl ({titan.level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>\n"
    )

    sent_message = None
    send_reply = None
    if update.message:
        send_reply = update.message.reply_text
    elif update.callback_query and update.callback_query.message:
        if hasattr(update.callback_query.message, "edit_text"):
            send_reply = update.callback_query.message.edit_text
    msg_send_start = time.time()
    if send_reply:
        try:
            sent_message = await send_reply(
                text=reply_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            msg_send_end = time.time()
            logger.info(f"Titan message sending took {msg_send_end - msg_send_start:.3f} seconds.")
            logger.info(f"Total delay from titan generation to message sent: {msg_send_end - start_time:.3f} seconds.")
        except Exception as e:
            await _reply_error(update, "An error occurred while displaying the titan.")
            sent_message = None

    # Move all cleanup and timeout tasks to background after message is sent
    if sent_message:
        asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
        # Clean up stale explore records in background
        asyncio.create_task(cleanup_stale_explore_records())
        key = f"titan_timeouts_{user_id}"
        if key not in context.bot_data:
            context.bot_data[key] = []
        context.bot_data[key].append(asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message)))

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


