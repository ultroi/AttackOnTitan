from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.models import Player, Character, Titan, DailyExplores, TITAN_NAME_VARIANTS
from database.db import Database
from game.travel_map import TRAVEL_MAP
from game.captcha import spawn_captcha
from utils.ban_utils import ban_protected
from datetime import datetime, timezone
from utils.maintenance import maintenance_protected
from typing import Dict
import time
import random
import logging
import asyncio
from uuid import uuid4

logger = logging.getLogger(__name__)

# Rate limiting for explore command
user_last_explore: Dict[str, float] = {}
TITAN_TIMEOUT_SECONDS = 60 * 3  # 3 minutes


# Titan type to image URL mapping
TITAN_TYPE_IMAGE_URLS = {
    "Goofy Grinning": "https://i.ibb.co/dJ6J58s0/image.jpg",
    "Potbellied": "https://i.ibb.co/XkMw0Xt5/image.jpg",
    "Bearded": "https://i.ibb.co/7J8S4s6v/image.jpg",
    "Gaping Mouth": "https://i.ibb.co/9mMK2FG1/image.jpg",
    "Small Jogger": "https://i.ibb.co/Fk8NspGP/image.jpg",
    "Leaper": "https://i.ibb.co/k2XqYdX6/image.jpg",
    "Bloated": "https://i.ibb.co/fYrcqngz/image.jpg",
    "Staggering Creepers": "https://i.ibb.co/mFchdbj9/image.jpg",
    "Wailing": "https://i.ibb.co/1JJQg9Db/image.jpg"
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
    except Exception:
        pass

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
                pass
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
                    except Exception:
                        pass
    except Exception:
        pass
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
    pass

@ban_protected
async def close_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close the persistent keyboard menu."""
    if update.message:
        await update.message.reply_text(
            "Closing keyboard...",
            reply_markup=ReplyKeyboardRemove()
        )

# Decorator to protect explore command from bans and maintenance mode
@maintenance_protected
@ban_protected
async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /explore command to find titans."""
    if not update.effective_user:
        await _reply_error(update, "Cannot identify user. Please try again.")
        return

    user_id = update.effective_user.id
    user_id_str = str(user_id)
    username = update.effective_user.username or update.effective_user.first_name or "Unknown"

    # Only use in private chats
    if not update.effective_chat or update.effective_chat.type != "private":
        await _reply_error(update, "This command can only be used in private chats.")
        return

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
            except Exception:
                pass
        context.user_data["persistent_keyboard_sent"] = True

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
    
    # --- SPAM PROTECTION ---
    if "explore_spam_count" not in context.bot_data:
        context.bot_data["explore_spam_count"] = {}
    spam_count = context.bot_data["explore_spam_count"].get(user_id_str, 0)

    try:
        from game.battle_system import active_battles, active_battles_lock
    except ImportError:
        active_battles = {}
        active_battles_lock = None

    is_in_battle = False
    if active_battles_lock:
        async with active_battles_lock:
            if user_id_str in active_battles:
                is_in_battle = True
    elif user_id_str in active_battles:
        is_in_battle = True

    if is_in_battle:
        # Reset the spam count for this user
        context.bot_data["explore_spam_count"][user_id_str] = 0
        first_name = update.effective_user.first_name or "Player"
        await _reply_error(update, f"{first_name} is currently battling !!")
        try:
            from utils.monitor import remove_player_activity
            remove_player_activity(user_id)
        except Exception:
            pass
        return

    # If not in battle, increment spam count
    spam_count += 1
    context.bot_data["explore_spam_count"][user_id_str] = spam_count

    # Warn at 15 explores
    if spam_count == 15:
        if update.message:
            await update.message.reply_text("⚠️ Warning: Don't Spam, you will be banned.")

    # Ban at 20 explores
    if spam_count >= 20:
        # Directly insert ban in DB, bypassing mod/owner check
        db = context.bot_data.get("db")
        # Removed local import to prevent UnboundLocalError
        expiry = int(time.time()) + 24*3600
        reason = "Spamming explore without battle"
        await db.bans.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "expiry": expiry, "reason": reason, "banned_by": user_id, "banned_at": int(time.time())}},
            upsert=True
        )
        # Notify user
        if update.message:
            await update.message.reply_text("You are banned for spamming explore without battle.")
        context.bot_data["explore_spam_count"][user_id_str] = 0

        # Send ban log message to group (same format as ban_user)
        bot_username = None
        try:
            bot_username = (await context.bot.get_me()).username
        except Exception:
            bot_username = "Bot"
        time_str = "24 hours"
        msg = (
            f"<b>#BanEvent</b>\n\n"
            f"<b>Target</b> : <a href=\"tg://user?id={user_id}\">{update.effective_user.first_name}</a>\n"
            f"<b>Target ID</b> : <code>{user_id}</code>\n"
            f"<b>By</b> : <a href=\"tg://user?id={context.bot.id}\">{bot_username}</a>\n"
            f"<b>Reason</b> : <code>Spamming explore without battle</code>\n"
            f"<b>Time</b> : <code>{time_str}</code>"
        )
        BAN_LOG_CHAT_ID = -1002873117075
        try:
            await context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return

    # Block if verification is in progress
    if context.user_data.get("hcaptcha_prompted", False) and not getattr(player, "hcaptcha_verified", False):
        return

    now = time.time()
    last_explore = getattr(player, "last_explore_time", None)

    # Check inactivity
    inactive = False
    INACTIVITY_THRESHOLD = 1500
    if last_explore is not None:
        inactivity_duration = now - last_explore
        if inactivity_duration > INACTIVITY_THRESHOLD:
            inactive = True
    else:
        pass

    # If inactive and not verified
    if inactive and not getattr(player, "hcaptcha_verified", False):
        if not context.user_data.get("hcaptcha_prompted", False):
            context.user_data["hcaptcha_prompted"] = True
            timestamp = int(now)
            verification_url = (
                f"https://attackontitangamebot.onrender.com/hcaptcha?user_id={user_id}&ts={timestamp}"
            )
            await update.message.reply_text(
                "🔒 <b>Verification Required</b>\n\n"
                "Complete hCaptcha to continue exploring\n",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Verify Now", url=verification_url)]
                ]),
                parse_mode=ParseMode.HTML,
            )
            await db.update_player(user_id_str, {"hcaptcha_start_time": timestamp})
            return

    # Reset verification flag if verified
    if getattr(player, "hcaptcha_verified", False):
        context.user_data["hcaptcha_prompted"] = False
        await db.update_player(user_id_str, {"hcaptcha_verified": False})

    # Always update last_explore_time
    await db.update_player(user_id_str, {"last_explore_time": now})

    # Check for active battle before allowing explore
    try:
        from game.battle_system import active_battles, active_battles_lock
    except ImportError:
        active_battles = {}
        active_battles_lock = None

    is_in_battle = False
    if active_battles_lock:
        async with active_battles_lock:
            if user_id_str in active_battles:
                is_in_battle = True
    elif user_id_str in active_battles:
        is_in_battle = True

    if is_in_battle:
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
        pass
    except Exception:
        pass

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
        await _reply_error(update, f"{player_character_name} doesn't have enough gas to explore (needs at least 100). Use /char char_name to refill gas.")
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
        # --- Clear active battle if present ---
        try:
            from game.battle_system import active_battles
            user_id_str = str(user_id)
            if user_id_str in active_battles:
                active_battles.pop(user_id_str, None)
            # Also clear active_battle_id in bot_data if present
            battle_id_key = f"active_battle_id_{user_id_str}"
            if battle_id_key in context.bot_data:
                context.bot_data.pop(battle_id_key, None)
        except Exception:
            pass
        try:
            if update.message:
                await update.message.reply_text(
                    f"You are at a decision point: <b>{location}</b>\nChoose a direction to continue your journey:",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            pass
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
    logger.info(f"[EXPLORE] Generated titan for user {user_id_str}: {titan}")
    if not titan:
        await _reply_error(update, "No titans found in your level range.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

    await db.store_titan(user_id_str, titan)
    logger.info(f"[EXPLORE] Stored titan in DB for user {user_id_str}")

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

    msg_send_start = time.time()  # Added missing variable
    sent_message = None
    try:
        if update.message:
            sent_message = await update.message.reply_text(
                text=reply_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        elif update.callback_query and update.callback_query.message:
            try:
                sent_message = await update.callback_query.message.edit_text(
                    text=reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
            except Exception as edit_error:
                pass
                sent_message = await update.callback_query.message.chat.send_message(
                    text=reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
        
        if sent_message:
            msg_send_end = time.time()
    except Exception as e:
        await _reply_error(update, "An error occurred while displaying the titan.")
        try:
            remove_player_activity(user_id)
        except NameError:
            pass
        return

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
    except Exception:
        pass


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
        except Exception:
            pass
            await asyncio.sleep(3600)

async def force_cleanup_user(user_id: int, db: Database):
    """Force cleanup of all user-related data."""
    try:
        from game.battle_system import cleanup_battle, active_battles
        user_id_str = str(user_id)
        if user_id_str in active_battles:
            try:
                cleanup_battle(user_id_str, "forced_cleanup")
            except Exception:
                pass
            active_battles.pop(user_id_str, None)
        user_last_explore.pop(user_id_str, None)
        await db.update_player(user_id, {"last_explore": None})
        await db.delete_titan(user_id_str)
        try:
            from utils.monitor import remove_player_activity
            remove_player_activity(user_id)
        except ModuleNotFoundError:
            pass
        pass
    except Exception:
        pass

async def start_cleanup_task():
    """Start the cleanup task."""
    asyncio.create_task(cleanup_stale_explore_records())


