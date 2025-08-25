from utils.mod_utils import mod_only
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
from game.stats_command import track_explore_stats

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

# Pre-calculate difficulty levels for faster lookup
DIFFICULTY_BY_LEVEL = {level: "Easy" if level < 8 else ("Normal" if level < 15 else "Hard") for level in range(1, 30)}
# Pre-defined default areas to avoid recreating this list every time
DEFAULT_AREAS = ["Trost District", "Karanes District", "Shiganshina District"]

def generate_titan_directly(player_level: int, unlocked_areas: list = None):
    """Generate a titan directly without database calls for maximum speed - highly optimized version"""
    from database.models import Titan, generate_titan_name, generate_titan_hp, generate_titan_xp
    
    # Get difficulty from pre-calculated mapping
    difficulty = DIFFICULTY_BY_LEVEL.get(player_level, "Hard")
    
    # Use current microseconds as a seed for better randomness
    seed = datetime.now(timezone.utc).microsecond
    titan_random = random.Random(seed)
    
    # Titan level: within -2 to +2 of player level, but at least 1
    level = max(1, player_level + titan_random.randint(-2, 2))
    
    # Generate name and stats
    name = generate_titan_name(difficulty)
    max_hp = generate_titan_hp(level, difficulty)
    xp_reward = generate_titan_xp(level, difficulty)
    
    # Use shared constant for default areas
    areas = unlocked_areas if unlocked_areas else DEFAULT_AREAS
    
    # Use a shared UTC timestamp for all created_at fields
    now = datetime.now(timezone.utc)
    
    # Create titan with all required fields - optimized
    return Titan(
        name=name,
        level=level,
        max_hp=max_hp,
        abilities=[],
        created_at=now,
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
        # Store the timestamp when the timeout task started
        start_time = time.time()
        
        # Wait for the timeout period
        await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
        
        # Get the latest battle_id for this user
        battle_id_key = f"active_battle_id_{user_id}"
        current_battle_id = context.bot_data.get(battle_id_key)
        
        # Check if user's last explore was AFTER this timeout task started
        user_id_str = str(user_id)
        last_explore_time = user_last_explore.get(user_id_str, 0)
        if last_explore_time > start_time:
            # User has already started a new explore after this timeout task began
            # So we shouldn't expire this titan - they might be actively using it
            logger.info(f"User {user_id} has recent activity, not expiring titan")
            return
        
        # Use helper function to check if there's an active battle
        if _is_in_battle(user_id_str):
            return
        
        # Clean up the titan if no battle is active
        db = context.bot_data.get("db")
        if db:
            titan_in_db = await db.get_titan(user_id_str)
            if titan_in_db:
                await db.delete_titan(user_id_str)
                
                # Only edit message if no battle has started
                if sent_message and current_battle_id == context.bot_data.get(battle_id_key):
                    try:
                        from game.safe_edit import safe_edit_message_text
                        await safe_edit_message_text(
                            sent_message,
                            "⏰ Titan encounter expired!\n\nYou took too long to respond. Use /explore to find another titan.",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        pass
    except asyncio.CancelledError:
        # Task was cancelled, this is expected behavior
        pass
    except Exception as e:
        logger.error(f"Error in titan_encounter_timeout: {e}")
    finally:
        # Clean up the task reference
        key = f"titan_timeouts_{user_id}"
        if key in context.bot_data:
            tasks = context.bot_data[key]
            # Remove completed tasks and limit to at most 1 active task
            context.bot_data[key] = [t for t in tasks if not t.done()][:1]

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
    logger.info(f"Reset explore timer for user {user_id}")
    return True

@ban_protected
async def close_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close the persistent keyboard menu."""
    if update.message:
        await update.message.reply_text(
            "Closing keyboard...",
            reply_markup=ReplyKeyboardRemove()
        )

# Helper function to check if a user is in battle - reduces code duplication
def _is_in_battle(user_id_str: str) -> bool:
    """Check if a user is in battle - centralized helper function"""
    try:
        from game.battle_system import active_battles
        return user_id_str in active_battles
    except ImportError:
        return False

# Helper function to check if verification is required
async def _check_verification_required(player, user_id_str: str, context) -> bool:
    """Check if verification is required for this user"""
    # Check context flag first (fastest)
    if context.user_data.get("hcaptcha_prompted", False):
        # Then verify against database state
        player_verified = getattr(player, "hcaptcha_verified", False)
        
        # Also check if user was recently verified (within last 10 minutes)
        last_verified = getattr(player, "last_verified", 0)
        now = time.time()
        recently_verified = last_verified and (now - last_verified) < 600
        
        # User is not required to verify if either they're verified in DB or recently verified
        if player_verified or recently_verified:
            # Clear the prompted flag since they're verified
            context.user_data["hcaptcha_prompted"] = False
            logger.info(f"Player {user_id_str} verification check passed (verified: {player_verified}, recently: {recently_verified})")
            return False
        return True
    return False

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

    now_ms = time.time()
    last_explore = user_last_explore.get(user_id_str, 0)
    if now_ms - last_explore < 1.2:
        # Ignore repeated /explore within 1 second
        return
    user_last_explore[user_id_str] = now_ms

    # --- SPAM TRACKING: Warn at 10, ban at 15 explores without battle ---
    if "explore_spam_count" not in context.bot_data:
        context.bot_data["explore_spam_count"] = {}
    spam_count = context.bot_data["explore_spam_count"].get(user_id_str, 0) + 1
    context.bot_data["explore_spam_count"][user_id_str] = spam_count

    # Check if user is in battle using the helper function
    is_in_battle = _is_in_battle(user_id_str)
    if is_in_battle:
        # Reset spam count if user enters battle
        context.bot_data["explore_spam_count"][user_id_str] = 0
    else:
        if spam_count == 10:
            if update.message:
                await update.message.reply_text("⚠️ Warning: Don't spam, you will be banned.")
        elif spam_count >= 15:
            # Ban user for spamming (permanent)
            asyncio.create_task(_handle_spam_ban(user_id, update, context))
            return

    # Second check for active battle (using the same helper function)
    
    if is_in_battle:
        await _reply_error(update, f"{update.effective_user.first_name or 'Player'} is currently battling!")
        return

    # Get database immediately (critical dependency) - non-blocking
    db = context.bot_data.get("db")
    if db is None:
        await _reply_error(update, "Internal error: Database not initialized.")
        return
    
    # Start several tasks in parallel to speed up response time - now with batch fetching
    # Optimize by fetching player and character data in parallel to reduce wait time
    player_task = db.get_player(user_id_str)
    
    # Show persistent keyboard in the background - non-blocking
    if context.user_data is not None and not context.user_data.get("persistent_keyboard_sent"):
        context.user_data["persistent_keyboard_sent"] = True
        asyncio.create_task(_show_keyboard_background(update))
        
    is_in_battle = _is_in_battle(user_id_str)
        
    # Only use cached titan if in active battle, otherwise always generate new
    titan_cached = user_id_str in db._titan_cache and is_in_battle
    
    # Clear cached titan if not in battle to ensure different titans each explore
    if not is_in_battle:
        # Use the dedicated method to invalidate titan cache
        db.invalidate_titan_cache(user_id_str)

    # Wait for player data
    player = await player_task
    if not player:
        if update.message:
            await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        elif update.callback_query:
            await update.callback_query.edit_message_text("You haven't created a player account yet! Use /start to begin.")
        return
    
    # Record time and check inactivity 
    now = time.time()
    player_verified = getattr(player, "hcaptcha_verified", False)
    last_explore = getattr(player, "last_explore_time", None)
    explore_start_time = getattr(player, "explore_start_time", None)

    # Always reset the prompted flag if the player is verified in database
    if player_verified:
        if context.user_data.get("hcaptcha_prompted", False):
            context.user_data["hcaptcha_prompted"] = False
            # Show a success message if this is the first time seeing them after verification
            await update.message.reply_text("✅ Verification confirmed! You can now continue exploring.")
        logger.debug(f"Player {user_id_str} verification confirmed in database, resetting hcaptcha_prompted flag")

    # Check if verification is still being requested after checking database
    if await _check_verification_required(player, user_id_str, context):
        # If the player has a last_verified time that's recent, consider them verified
        last_verified = getattr(player, "last_verified", 0)
        if last_verified and (now - last_verified) < 600:  # 10 minutes
            context.user_data["hcaptcha_prompted"] = False
            await update.message.reply_text("✅ Verification confirmed! You can now continue exploring.")
            # Update player verification status (non-blocking)
            asyncio.create_task(db.update_player(user_id_str, {"hcaptcha_verified": True}))
        else:
            await _reply_error(update, "Please complete the hCaptcha verification to continue exploring.")
            return

    # Non-blocking: Set or update explore start time for the 25-minute inactivity check
    if not explore_start_time:
        # First explore or after a reset - set the start time (non-blocking)
        asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": now}))
    else:
        # Check if 25 minutes (1500 seconds) have passed since last explore session started
        if (now - explore_start_time) > 1500: 
            # Reset the explore timer after showing hCaptcha (non-blocking)
            asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": None}))
            # Only require verification if not recently verified
            last_verified_time = getattr(player, "last_verified", 0)
            if not last_verified_time or (now - last_verified_time) > 1800: 
                # Handle verification and return without spawning titan
                if await _handle_verification(update, context, user_id, now, db):
                    return  # Only return if verification was actually prompted
        else:
            # User explored within 25 minutes - just update last explore time (non-blocking)
            # This resets the 25-minute timer by updating explore_start_time
            asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": now}))

    # Check inactivity and handle verification (for long term inactivity)
    if last_explore and (now - last_explore) > 1800:  # 30 minutes inactivity check
        # Only require verification if not recently verified
        last_verified_time = getattr(player, "last_verified", 0)
        if not last_verified_time or (now - last_verified_time) > 1800: 
            # Handle verification and return without spawning titan
            if await _handle_verification(update, context, user_id, now, db):
                return  # Only return if verification was actually prompted

    # Reset verification flag but maintain recent verification record
    if player_verified:
        # Keep last_verified, but reset hcaptcha_verified flag for next session (non-blocking)
        asyncio.create_task(db.update_player(user_id_str, {
            "hcaptcha_verified": False,  # Reset for next session
            "hcaptcha_start_time": None  # Clear start time
        }))

    # Tracking happens in background - with reduced frequency for better performance
    if random.random() < 0.33:  # Only track 33% of explores to reduce overhead
        try:
            from utils.monitor import track_player_action
            track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
        except Exception:
            pass

    # Get character information with optimized logic
    # Extract character name from player data to avoid additional DB query
    player_character_name = player.team[0].character_name if player.team else None
    if not player_character_name:
        await _reply_error(update, "You don't have any character in your team.")
        return
    
    # For character data, we'll always get fresh data from the database
    # This ensures we always have the latest HP values
    character_task = db.get_character(user_id_str, player_character_name)
    
    # Track exploration for stats (will only count if battle is completed)
    asyncio.create_task(track_explore_stats(user_id_str, update.effective_user.first_name, False))

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
    if await _check_verification_required(player, user_id_str, context):
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
        
    # Use cached titan if available, otherwise generate a new one
    if titan_cached:
        titan = db._titan_cache.get(user_id_str)
    else:
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
                titan = await db.generate_titan(player_character.level, player.unlocked_areas, user_id_str)
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
        
    # Don't cache character data in context.bot_data anymore
    # to ensure we always get fresh HP values from the database
    # We'll rely on the DB-level caching with proper invalidation

    # Skip titan storage if we're using a cached titan
    if not titan_cached:
        # Store titan in cache directly first for immediate access
        db._titan_cache[user_id_str] = titan
        # Then trigger database storage in background
        titan_store_task = asyncio.create_task(db.store_titan(user_id_str, titan))

    # Prepare battle UI immediately (this is fast)
    # Use a cached battle ID if one exists
    battle_id_key = f"active_battle_id_{user_id}"
    if battle_id_key in context.bot_data:
        battle_id = context.bot_data[battle_id_key]
    else:
        battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
        context.bot_data[battle_id_key] = battle_id

    # Pre-built keyboard for fast response - use a static keyboard when possible
    keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Pre-cached image URL lookup using titan name pattern matching
    # This is much faster than checking each type
    titan_name_lower = titan.name.lower()
    titan_image_url = None
    
    # Use cached image URLs when possible
    cache_key = f"titan_image_{titan_name_lower[:10]}"  # First 10 chars as cache key
    if cache_key in context.bot_data:
        titan_image_url = context.bot_data[cache_key]
    else:
        # Only look up if not in cache
        for type_, url in TITAN_TYPE_IMAGE_URLS.items():
            if type_ in titan_name_lower:
                titan_image_url = url
                # Cache this result for future use
                context.bot_data[cache_key] = url
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
        # Use a single combined task for all post-response operations
        asyncio.create_task(_handle_post_explore_tasks(user_id, context, sent_message, start_time, user_id_str))
    
async def _handle_timeout_setup(user_id, context, sent_message):
    """Setup the timeout handler in background - simplified version"""
    # Use simpler key structure
    key = f"titan_timeouts_{user_id}"
    if key not in context.bot_data:
        context.bot_data[key] = []
    
    # Cancel ALL previous timeouts for this user to prevent unwanted expiration
    # when they're actively interacting with the bot
    if context.bot_data[key]:
        for task in context.bot_data[key]:
            if not task.done():
                try:
                    task.cancel()
                    logger.debug(f"Cancelled previous timeout task for user {user_id}")
                except Exception:
                    pass
        
        # Clear the list of tasks
        context.bot_data[key] = []
    
    # Create a new timeout task
    timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
    context.bot_data[key].append(timeout_task)
    
    # Store creation time in bot_data instead of as a task attribute
    task_id = id(timeout_task)
    if "task_creation_times" not in context.bot_data:
        context.bot_data["task_creation_times"] = {}
    context.bot_data["task_creation_times"][task_id] = time.time()
    
    # Log creation of timeout task (debug level)
    logger.debug(f"Created new timeout task for user {user_id}")
    
    # Limit to max 2 tasks
    if len(context.bot_data[key]) > 2:
        context.bot_data[key] = context.bot_data[key][-2:]


async def _handle_post_explore_tasks(user_id, context, sent_message, start_time, user_id_str):
    """Combined handler for all post-explore background tasks for better performance"""
    # Execute tasks in parallel where possible
    tasks = []
    
    # Handle timeout
    tasks.append(_handle_timeout_setup(user_id, context, sent_message))
    
    # Log performance
    end_time = time.time()
    response_time = end_time - start_time
    tasks.append(_log_performance(user_id_str, response_time))
    
    # Run all tasks in parallel and wait for them to complete
    await asyncio.gather(*tasks)

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
    """
    Handle hCaptcha verification with improved checks and proper flag management.
    """
    # Double-check database first to see if player is verified
    # This ensures we have the most up-to-date information
    user_id_str = str(user_id)
    player = await db.get_player(user_id_str)
    
    # If player is verified in database, clear all verification flags and continue
    if player and getattr(player, "hcaptcha_verified", True):
        # User is already verified in database, reset all verification flags
        context.user_data["hcaptcha_prompted"] = False
        logger.info(f"Player {user_id} is verified in database, cleared hcaptcha_prompted flag")
        return False
    
    # Check for verification success message from web
    last_verified_time = getattr(player, "last_verified", 0)
    if last_verified_time and now - last_verified_time < 600:  # Within 10 minutes
        # User was recently verified via web
        context.user_data["hcaptcha_prompted"] = False
        # Ensure database state is synchronized
        await db.update_player(user_id_str, {"hcaptcha_verified": True})
        await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
        logger.info(f"Player {user_id} was recently verified via web, cleared verification flags")
        return False
    
    # Check if verification is already in progress
    if context.user_data.get("hcaptcha_prompted", False):
        # Check if database has been updated since the last check
        if player and getattr(player, "last_verified", 0) > context.user_data.get("last_verification_check", 0):
            # Database was updated, user was verified
            context.user_data["hcaptcha_prompted"] = False
            context.user_data["last_verification_check"] = now
            await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
            logger.info(f"Player {user_id} verification detected, cleared verification flags")
            return False
        else:
            # Update the last check timestamp
            context.user_data["last_verification_check"] = now
            await update.message.reply_text(
                "🔄 Please complete the hCaptcha verification to continue exploring.\n"
                "If you've already completed verification, please wait a moment and try again.",
                parse_mode=ParseMode.HTML
            )
            return True
    
    # If this is the first time prompting for verification
    # Set the prompted flag to prevent repeated prompts
    context.user_data["hcaptcha_prompted"] = True
    context.user_data["last_verification_check"] = now
    timestamp = int(now)
    
    # Generate verification URL with user ID and timestamp
    verification_url = f"https://attackontitangamebot.onrender.com/hcaptcha?user_id={user_id}&ts={timestamp}"
    
    try:
        # Send verification message with button
        await update.message.reply_text(
            "🔒 <b>Verification Required</b>\n\n"
            + "Complete hCaptcha to continue exploring.\n"
            + "After completing verification, use /explore again to continue.\n\n",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Verify Now", url=verification_url)]
            ]),
            parse_mode=ParseMode.HTML,
        )
        
        # Update player record with verification start time and reset explore timer
        await db.update_player(user_id_str, {
            "hcaptcha_start_time": timestamp,
            "hcaptcha_verified": False,  # Explicitly set to false
            "explore_start_time": None   # Reset explore timer when verification is required
        })
        
        logger.info(f"Sent verification request to player {user_id} and reset explore timer")
    except Exception as e:
        logger.error(f"Failed to send verification message: {e}")
        # If we fail to send the message, don't leave the user stuck in verification limbo
        context.user_data["hcaptcha_prompted"] = False
    
    return True

async def _handle_decision_point_cleanup(user_id_str, context):
    try:
        # Use the helper function to check for battle
        if _is_in_battle(user_id_str):
            try:
                from game.battle_system import active_battles
                active_battles.pop(user_id_str, None)
            except ImportError:
                pass
            
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
    """Log performance metrics for monitoring asynchronously"""
    # Use a dedicated background task to avoid blocking the main flow
    try:
        # Only log if it's slower than expected (>0.5s)
        if response_time > 0.5:
            logger.info(f"[PERFORMANCE] Explore command for user {user_id_str} took {response_time:.3f}s")
        # For very fast responses, log at debug level only
        else:
            logger.debug(f"[PERFORMANCE] Fast explore response for user {user_id_str}: {response_time:.3f}s")
    except Exception:
        pass  # Never fail on logging


async def _expire_cache_key(context, cache_key, seconds):
    """Helper to expire a cache key after a set time"""
    await asyncio.sleep(seconds)
    if cache_key in context.bot_data:
        context.bot_data.pop(cache_key, None)


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

async def reset_verification_state(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Reset verification state for a user who might be stuck in verification limbo.
    This function clears all verification flags in context and database.
    """
    user_id_str = str(user_id)
    db = context.bot_data.get("db")
    if not db:
        return False
        
    try:
        # Clear verification flags in context
        if context.user_data:
            context.user_data["hcaptcha_prompted"] = False
            
        # Reset verification in database
        current_time = time.time()
        await db.update_player(user_id, {
            "hcaptcha_verified": False,
            "hcaptcha_start_time": None,
            "explore_start_time": current_time,  # Set current time to reset the 25-minute timer
            "last_explore_time": current_time    # Update last explore time too
        })
        
        logger.info(f"Reset verification state for user {user_id} and set explore timer")
        return True
    except Exception as e:
        logger.error(f"Failed to reset verification state: {e}")
        return False

async def force_cleanup_user(user_id: int, db: Database):
    """Force cleanup of all user-related data."""
    try:
        user_id_str = str(user_id)
        # Check if user is in battle using helper function
        if _is_in_battle(user_id_str):
            try:
                from game.battle_system import cleanup_battle
                cleanup_battle(user_id_str, "forced_cleanup")
                # Remove from active battles directly
                from game.battle_system import active_battles
                active_battles.pop(user_id_str, None)
            except Exception:
                pass
        
        # Clean up other data
        user_last_explore.pop(user_id_str, None)
        await db.update_player(user_id, {"last_explore": None})
        await db.delete_titan(user_id_str)
        
        # Remove player activity tracking if available
        try:
            from utils.monitor import remove_player_activity
            remove_player_activity(user_id)
        except ModuleNotFoundError:
            pass
    except Exception:
        pass

async def start_cleanup_task():
    """Start the cleanup task."""
    asyncio.create_task(cleanup_stale_explore_records())


@mod_only
async def reset_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Command to reset verification state for a user.
    Usage: /resetverify [user_id] or reply to a user's message
    Only moderators can use this command.
    """
    if not update.effective_user:
        return
    
    # Determine target user (either from reply or from args)
    target_user_id = None
    
    # Check if replying to a message
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        target_user_name = update.message.reply_to_message.from_user.first_name
    # Check if user ID was provided as argument
    elif context.args and context.args[0].isdigit():
        target_user_id = int(context.args[0])
        target_user_name = f"User {target_user_id}"
    else:
        await update.message.reply_text(
            "❌ Please either reply to a user's message or provide a user ID."
        )
        return
    
    # Reset verification for the target user
    success = await reset_verification_state(target_user_id, context)
    
    if success:
        await update.message.reply_text(
            f"✅ Verification state has been reset for {target_user_name}.\n"
            f"They can now use /explore again normally."
        )
    else:
        await update.message.reply_text(
            f"❌ Failed to reset verification state for {target_user_name}.\n"
            f"Please try again later or check if the user ID is valid."
        )


# Import centralized safe edit functions
try:
    from game.safe_edit import safe_edit_message_text, safe_edit_message_caption
except ImportError:
    # Fallback implementations if import fails
    async def safe_edit_message_text(message, text, reply_markup=None, parse_mode=None):
        """Helper function to safely edit messages, handling common errors"""
        try:
            # Check if content is identical (basic check)
            if hasattr(message, "text") and message.text == text:
                # Check if markup is also identical
                existing_markup = getattr(message, "reply_markup", None)
                if (existing_markup is None and reply_markup is None) or \
                   (existing_markup is not None and reply_markup is not None and 
                    existing_markup.to_dict() == reply_markup.to_dict()):
                    # Both text and markup are identical - don't attempt edit
                    logging.debug(f"Skipping edit: message content and markup unchanged")
                    return False
                    
            # Create kwargs dynamically
            kwargs = {"text": text}
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            if parse_mode is not None:
                kwargs["parse_mode"] = parse_mode
                
            await message.edit_text(**kwargs)
            return True
            
        except Exception as e:
            if "message is not modified" in str(e).lower():
                # Just log at debug level
                logging.debug(f"Message not modified: {e}")
                return False
            else:
                # Log other errors at warning level
                logging.warning(f"Error editing message: {e}")
                return False
                
    async def safe_edit_message_caption(message, caption, reply_markup=None, parse_mode=None):
        """Helper function to safely edit message captions, handling common errors"""
        try:
            # Check if content is identical (basic check)
            if hasattr(message, "caption") and message.caption == caption:
                # Check if markup is also identical
                existing_markup = getattr(message, "reply_markup", None)
                if (existing_markup is None and reply_markup is None) or \
                   (existing_markup is not None and reply_markup is not None and 
                    existing_markup.to_dict() == reply_markup.to_dict()):
                    # Both caption and markup are identical - don't attempt edit
                    logging.debug(f"Skipping edit: caption and markup unchanged")
                    return False
                    
            # Create kwargs dynamically
            kwargs = {"caption": caption}
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            if parse_mode is not None:
                kwargs["parse_mode"] = parse_mode
                
            await message.edit_caption(**kwargs)
            return True
            
        except Exception as e:
            if "message is not modified" in str(e).lower():
                # Just log at debug level
                logging.debug(f"Caption not modified: {e}")
                return False
            else:
                # Log other errors at warning level
                logging.warning(f"Error editing caption: {e}")
                return False