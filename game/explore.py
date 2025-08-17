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
    "goofy grinning": "https://i.ibb.co/dJ6J58s0/image.jpg",
    "potbellied": "https://i.ibb.co/XkMw0Xt5/image.jpg",
    "bearded": "https://i.ibb.co/7J8S4s6v/image.jpg",
    "gaping mouth": "https://i.ibb.co/9mMK2FG1/image.jpg",
    "small jogger": "https://i.ibb.co/Fk8NspGP/image.jpg",
    "leaper": "https://i.ibb.co/k2XqYdX6/image.jpg",
    "bloated": "https://i.ibb.co/fYrcqngz/image.jpg",
    "staggering creepers": "https://i.ibb.co/mFchdbj9/image.jpg",
    "wailing": "https://i.ibb.co/1JJQg9Db/image.jpg"
}

# Direct titan generation - removed pool for simplicity and speed
# This is more efficient since we're no longer maintaining a large pool of titans that might never be used

# Helper function to quickly generate a titan without database calls
def generate_titan_directly(player_level: int, unlocked_areas: list = None):
    """Generate a titan directly without database calls for maximum speed"""
    from database.models import Titan, generate_titan_name, generate_titan_hp, generate_titan_xp
    
    # Determine difficulty based on player level
    if player_level < 8:
        difficulty = "Easy"
    elif player_level < 15:
        difficulty = "Normal"
    else:
        difficulty = "Hard"
    
    # Titan level: within -2 to +2 of player level, but at least 1
    level = max(1, player_level + random.randint(-2, 2))
    
    # Generate name and stats
    name = generate_titan_name(difficulty)
    max_hp = generate_titan_hp(level, difficulty)
    xp_reward = generate_titan_xp(level, difficulty)
    
    # Ensure default areas
    default_areas = ["Trost District", "Karanes District", "Shiganshina District"]
    areas = unlocked_areas if unlocked_areas else default_areas
    
    # Create titan with all required fields
    return Titan(
        name=name,
        level=level,
        max_hp=max_hp,
        abilities=[],
        created_at=datetime.now(timezone.utc),
        difficulty=difficulty,
        spawn_areas=areas,
        drop_table={},
        xp_reward=xp_reward,
        min_level_requirement=level
    )

async def _reply_error(update: Update, message: str):
    """Helper to reply with error messages - simplified for speed."""
    try:
        # Faster implementation - directly check update.message
        if update.message:
            await update.message.reply_text(message)
        # Fallback to callback query
        elif update.callback_query:
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
    # Start timing the operation for performance monitoring
    start_time = time.time()
    
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

    # Check for active battle early - this is a fast check to abort quickly
    is_in_battle = False
    try:
        from game.battle_system import active_battles
        is_in_battle = user_id_str in active_battles
    except ImportError:
        pass
    
    if is_in_battle:
        await _reply_error(update, f"{update.effective_user.first_name or 'Player'} is currently battling !!")
        return

    # Get database immediately (critical dependency) - non-blocking
    db = context.bot_data.get("db")
    if db is None:
        await _reply_error(update, "Internal error: Database not initialized.")
        return
    
    # Start several tasks in parallel to speed up response time
    player_task = db.get_player(user_id_str)
    
    # Show persistent keyboard in the background - non-blocking
    if context.user_data is not None and not context.user_data.get("persistent_keyboard_sent"):
        context.user_data["persistent_keyboard_sent"] = True
        # Move this to a background task to not slow down main response
        asyncio.create_task(_show_keyboard_background(update))

    # --- SPAM PROTECTION (optimized) - Only check if needed ---
    # Using faster modulo check instead of full counter logic
    check_spam = random.random() < 0.25  # Only check 25% of the time for better performance
    
    if check_spam:
        if "explore_spam_count" not in context.bot_data:
            context.bot_data["explore_spam_count"] = {}
        spam_count = context.bot_data["explore_spam_count"].get(user_id_str, 0) + 1
        context.bot_data["explore_spam_count"][user_id_str] = spam_count

        # Only check these conditions if the count hits specific thresholds
        if spam_count == 15:
            if update.message:
                await update.message.reply_text("⚠️ Warning: Don't Spam, you will be banned.")
        elif spam_count >= 20:
            # Move banning to background task to not slow down response
            asyncio.create_task(_handle_spam_ban(user_id, update, context))
            return

    # Wait for player data
    player = await player_task
    if not player:
        if update.message:
            await update.message.reply_text("You need to create a profile first with /start")
        return
    
    # Block if verification is in progress - fast check
    if context.user_data.get("hcaptcha_prompted", False) and not getattr(player, "hcaptcha_verified", False):
        await _reply_error(update, "Please complete the hCaptcha verification to continue exploring.")
        return

    # Record time and check inactivity 
    now = time.time()
    player_verified = getattr(player, "hcaptcha_verified", False)
    last_explore = getattr(player, "last_explore_time", None)
    
    # Use the new non-blocking update pattern for last_explore_time
    # Since we're using the new fire-and-forget pattern, we don't need to await this
    db.update_player(user_id_str, {"last_explore_time": now})

    # Check inactivity and handle verification
    if last_explore and (now - last_explore) > 1500 and not player_verified:
        # Handle verification and return without spawning titan
        await _handle_verification(update, context, user_id, now, db)
        return
        
    # Reset verification flag if verified
    if player_verified:
        context.user_data["hcaptcha_prompted"] = False
        # Non-blocking update
        asyncio.create_task(db.update_player(user_id_str, {"hcaptcha_verified": False}))

    # Tracking happens in background - with reduced frequency for better performance
    if random.random() < 0.33:  # Only track 33% of explores to reduce overhead
        try:
            from utils.monitor import track_player_action
            track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
        except Exception:
            pass

    # Start player character lookup task - non-blocking
    player_character_name = player.team[0].character_name if player.team else None
    if not player_character_name:
        await _reply_error(update, "You don't have any character in your team.")
        return
    
    character_task = db.get_character(user_id_str, player_character_name)

    # Handle travel/decision points
    location = getattr(player, "location", None)
    if location and location in TRAVEL_MAP and location.startswith("Decision_"):
        directions = TRAVEL_MAP[location]
        keyboard = [
            [InlineKeyboardButton(dir, callback_data=f"travel_decision_{dir.strip().lower()}")]
            for dir in directions.keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Handle decision point cleanup in background - non-blocking
        asyncio.create_task(_handle_decision_point_cleanup(user_id_str, context))
        
        if update.message:
            await update.message.reply_text(
                f"You are at a decision point: <b>{location}</b>\nChoose a direction to continue your journey:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        return

    # Spawn CAPTCHA with lower probability (3% instead of 4%) for faster response
    if random.random() < 0.03:
        captcha_triggered = await spawn_captcha(update, context)
        if captcha_triggered:
            return
            
    # Double-check that hCaptcha verification is not required before spawning titan
    if context.user_data.get("hcaptcha_prompted", False) and not player_verified:
        await _reply_error(update, "Please complete the hCaptcha verification to continue exploring.")
        return
        
    # Wait for character data
    player_character = await character_task
    if not player_character:
        await _reply_error(update, f"Your character {player_character_name} was not found.")
        return

    if player_character.gas < 100:
        await _reply_error(update, f"{player_character_name} doesn't have enough gas to explore (needs at least 100). Use /char char_name to refill gas.")
        return
        
    # Direct titan generation - super fast with no database calls
    try:
        # Generate titan directly without any database queries
        titan = generate_titan_directly(
            player_level=player_character.level, 
            unlocked_areas=player.unlocked_areas
        )
    except Exception as e:
        # Fallback to database generation if direct generation fails
        try:
            titan = await db.generate_titan(player_character.level, player.unlocked_areas)
        except Exception:
            # Ultimate fallback - basic titan with minimal properties
            titan = Titan(
                name="Unknown Titan",
                level=player_character.level,
                max_hp=100 * player_character.level,
                abilities=[],
                created_at=datetime.now(timezone.utc),
                difficulty="Normal",
                spawn_areas=["Trost District"],
                drop_table={},
                xp_reward=50 * player_character.level,
                min_level_requirement=player_character.level
            )

    if not titan:
        await _reply_error(update, "No titans found in your level range.")
        return

    # Start titan storage immediately in background (non-blocking)
    titan_store_task = asyncio.create_task(db.store_titan(user_id_str, titan))

    # Prepare battle UI immediately (this is fast)
    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id

    # Pre-built keyboard for fast response
    keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Optimized image URL lookup
    titan_name_lower = titan.name.lower()
    titan_image_url = None
    # Direct iteration is faster than comprehension for small dictionaries
    for type_, url in TITAN_TYPE_IMAGE_URLS.items():
        if type_ in titan_name_lower:
            titan_image_url = url
            break
    
    image_embed = f'<a href="{titan_image_url}">!</a>' if titan_image_url else ""

    # Pre-built message text (avoids string concatenation in the critical path)
    reply_text = (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{titan.name} Lvl ({titan.level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>\n"
    )

    # Pre-built message parameters
    message_params = {
        "text": reply_text,
        "reply_markup": reply_markup,
        "parse_mode": ParseMode.HTML,
        "disable_web_page_preview": False
    }

    # Simplified message sending with optimized error handling
    sent_message = None
    try:
        # Direct path approach - always try the most common case first
        if update.message:
            sent_message = await update.message.reply_text(**message_params)
        elif update.callback_query and update.callback_query.message:
            sent_message = await update.callback_query.message.chat.send_message(**message_params)
    except Exception:
        # Simple error notification - no need for detailed logging here
        if update.message:
            await update.message.reply_text("An error occurred. Please try again.")
        return

    # Don't wait for titan storage - let it run in the background
    
    # Move all cleanup and timeout tasks to background after message is sent
    if sent_message:
        # Handle timeout in background (non-blocking)
        asyncio.create_task(_handle_timeout_setup(user_id, context, sent_message))
        
    # Log performance metrics in background (non-blocking)
    end_time = time.time()
    response_time = end_time - start_time
    asyncio.create_task(_log_performance(user_id_str, response_time))
    
async def _handle_timeout_setup(user_id, context, sent_message):
    """Setup the timeout handler in background - simplified version"""
    # Use simpler key structure
    key = f"titan_timeouts_{user_id}"
    if key not in context.bot_data:
        context.bot_data[key] = []
    
    # Limit number of tasks for a user (avoid memory leaks)
    if len(context.bot_data[key]) > 3:
        # Too many timeout tasks, clean up old ones
        context.bot_data[key] = [t for t in context.bot_data[key] if not t.done()][:2]
        
    # Create timeout task
    timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
    context.bot_data[key].append(timeout_task)

# Helper functions moved outside the main function for cleaner code
async def _show_keyboard_background(update: Update):
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

async def _handle_verification(update, context, user_id, now, db):
    if not context.user_data.get("hcaptcha_prompted", False):
        context.user_data["hcaptcha_prompted"] = True
        timestamp = int(now)
        verification_url = f"https://attackontitangamebot.onrender.com/hcaptcha?user_id={user_id}&ts={timestamp}"
        try:
            await update.message.reply_text(
                "🔒 <b>Verification Required</b>\n\n"
                "Complete hCaptcha to continue exploring\n"
                "After completing verification, use /explore again to continue.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Verify Now", url=verification_url)]
                ]),
                parse_mode=ParseMode.HTML,
            )
            await db.update_player(str(user_id), {"hcaptcha_start_time": timestamp})
        except Exception:
            pass
    return True

async def _handle_decision_point_cleanup(user_id_str, context):
    try:
        from game.battle_system import active_battles
        if user_id_str in active_battles:
            active_battles.pop(user_id_str, None)
            
        # Also clear active_battle_id in bot_data if present
        battle_id_key = f"active_battle_id_{user_id_str}"
        if battle_id_key in context.bot_data:
            context.bot_data.pop(battle_id_key, None)
    except Exception:
        pass
    
async def _handle_spam_ban(user_id, update, context):
    """Handle banning for spam in the background"""
    db = context.bot_data.get("db")
    if not db:
        return
        
    try:
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
        
        context.bot_data["explore_spam_count"][str(user_id)] = 0
        
        # Send ban log in background
        bot_username = (await context.bot.get_me()).username or "Bot"
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
        await context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.HTML)
    except Exception:
        pass
        
async def _log_performance(user_id_str, response_time):
    """Log performance metrics for monitoring"""
    logger.info(f"[PERFORMANCE] Explore command for user {user_id_str} took {response_time:.3f}s")


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


