from utils.mod_utils import mod_only
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.models import Player, Character, Titan, DailyExplores, TITAN_NAME_VARIANTS, generate_titan_hp, generate_titan_name, generate_titan_xp
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

# Import mission-related functions
from database.missions import (
    check_mission_item_drops, add_mission_item, 
    process_travel_mission_progress, process_explore_mission_progress
)

logger = logging.getLogger(__name__)

def get_titan_difficulty_by_level(level: int) -> str:
    """Get titan difficulty based on level ranges"""
    if level <= 50:
        return "Easy"
    elif level <= 100:
        return "Normal"
    else:
        return "Hard"

# Pre-generate titan pool for instant generation
TITAN_POOL = {}
for lvl in range(1, 126):
    TITAN_POOL[lvl] = []
    for _ in range(20):  # 20 variations per level for better randomness
        difficulty = get_titan_difficulty_by_level(lvl)
        name = generate_titan_name(difficulty)
        max_hp = generate_titan_hp(lvl, difficulty)
        xp = generate_titan_xp(lvl, difficulty)
        TITAN_POOL[lvl].append({
            "name": name,
            "level": lvl,
            "max_hp": max_hp,
            "xp_reward": xp,
            "difficulty": difficulty
        })

# Rate limiting and locking for explore command
user_last_explore: Dict[str, float] = {}
user_explore_locks: Dict[str, asyncio.Lock] = {}
TITAN_TIMEOUT_SECONDS = 60 * 3  # 3 minutes


# # Titan type to image URL mapping
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

# # Pre-calculate difficulty levels for faster lookup - Single category
DIFFICULTY_BY_LEVEL = {level: "Normal" for level in range(1, 30)}
# Pre-defined default areas to avoid recreating this list every time
DEFAULT_AREAS = ["Trost", "Karanes", "Shiganshina", "Orvud"]

# Enhanced cached titans for better performance
ENHANCED_CACHED_TITANS = {
    "All": {
        "names": [
            "Bearded Titan", "Potbellied Titan", "Goofy Grinning Titan", "Gaping Mouth Titan", 
            "Small Jogger Titan", "Leaper Titan", "Bloated Titan", "Staggering Creepers Titan", "Wailing Titan"
        ]
    }
}

# Battle button text constant
BATTLE_BUTTON_TEXT = "⚔️ Battle"



def format_titan_message(name: str, level: int, image_embed: str = "") -> str:
    """Fast string formatting for titan messages - optimized for speed"""
    return (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{name} Lvl ({level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>"
    )

def _generate_cached_titan(player_level: int, difficulty: str, user_id: int) -> dict:
    """Generate a cached titan for the given level with varied images and names - Single category"""
    # Get titan data from single category
    difficulty_data = ENHANCED_CACHED_TITANS["All"]

    # Use multiple sources for better randomness including user_id for uniqueness
    import time
    current_time = time.time()
    seed = int((current_time * 1000000) + id(difficulty_data) + player_level + user_id) % (2**32)
    titan_random = random.Random(seed)

    # Select random name and image from the pool
    name = titan_random.choice(difficulty_data["names"])
    
    # Map name to specific image if available, otherwise random
    key = name.lower().replace(" titan", "")
    if key in TITAN_TYPE_IMAGE_URLS:
        image_url = TITAN_TYPE_IMAGE_URLS[key]
    else:
        # For names that don't match, use random from available images
        image_url = titan_random.choice(list(TITAN_TYPE_IMAGE_URLS.values()))

    # Base stats (single tier)
    base_xp = 35

    # Scale stats by player level
    level_multiplier = max(1, player_level // 5 + 1)
    titan_level = max(1, player_level + titan_random.randint(-1, 1))

    # Get correct difficulty based on titan level
    actual_difficulty = get_titan_difficulty_by_level(titan_level)

    # Use varying HP from models
    max_hp = generate_titan_hp(titan_level, actual_difficulty)

    return {
        "name": name,
        "level": titan_level,
        "max_hp": max_hp,
        "abilities": [],
        "difficulty": actual_difficulty, 
        "drop_table": {},
        "xp_reward": base_xp * level_multiplier,
        "min_level_requirement": max(1, player_level - 2),
        "image_url": image_url
    }


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



# Helper function to check if a user is in battle - reduces code duplication
def _is_in_battle(user_id_str: str) -> bool:
    """Check if a user is in battle - centralized helper function"""
    try:
        from game.battle_system import active_battles
        return user_id_str in active_battles
    except ImportError:
        return False
    
async def _handle_verification(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, now: float, db):
    """Handle hCaptcha verification requirement"""
    user_id_str = str(user_id)

    # Get fresh player data
    player = await db.get_player(user_id_str)
    if not player:
        return False

    # Check if player is already verified
    if player and getattr(player, "hcaptcha_verified", True):
        if context.user_data:
            context.user_data["hcaptcha_prompted"] = False
        return False

    # Check for verification success message from web
    last_verified_time = getattr(player, "last_verified", 0)
    if last_verified_time and now - last_verified_time < 600:  # Within 10 minutes
        # User was recently verified via web
        if context.user_data:
            context.user_data["hcaptcha_prompted"] = False
        await db.update_player(user_id_str, {"hcaptcha_verified": True})
        if update.message:
            await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
        return False

    # Check if verification is already in progress
    if context.user_data and context.user_data.get("hcaptcha_prompted", False):
        # Check if database has been updated since the last check
        if player and getattr(player, "last_verified", 0) > context.user_data.get("last_verification_check", 0):
            # Database was updated, user was verified
            context.user_data["hcaptcha_prompted"] = False
            context.user_data["last_verification_check"] = now
            await db.update_player(user_id_str, {
                "hcaptcha_verified": True,
                "last_verified": now
            })
            if update.message:
                await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
            return False
        else:
            # Update the last check timestamp
            context.user_data["last_verification_check"] = now
            if update.message:
                await update.message.reply_text(
                    "🔄 Please complete the hCaptcha verification to continue exploring.\n"
                    "If you've already completed verification, please wait a moment and try again.",
                    parse_mode=ParseMode.HTML
                )
            return True

    # If this is the first time prompting for verification
    # Set the prompted flag to prevent repeated prompts
    if context.user_data:
        context.user_data["hcaptcha_prompted"] = True
        context.user_data["last_verification_check"] = now
    timestamp = int(now)

    # Generate verification URL with user ID and timestamp
    verification_url = f"https://attackontitangamebot.onrender.com/hcaptcha?user_id={user_id}&ts={timestamp}"

    try:
        # Send verification message with button
        if update.message:
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

    except Exception as e:
        logger.error(f"Error sending verification message: {e}")
        if context.user_data:
            context.user_data["hcaptcha_prompted"] = False

    return True


@maintenance_protected
@ban_protected
async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ultra-optimized explore command targeting <200ms response time"""
    
    start_time = time.time()
    
    # Basic validation (no DB calls)
    if not update.effective_user or not update.effective_chat:
        return
    
    if update.effective_chat.type != "private":
        await _reply_error(update, "This command can only be used in private chats.")
        return
    
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    username = update.effective_user.username or update.effective_user.first_name or "Unknown"
    
    # Rate limiting check (in-memory only)
    now_ms = time.time()
    if now_ms - user_last_explore.get(user_id_str, 0) < 1.0:
        return
    user_last_explore[user_id_str] = now_ms
    
    # Battle check (fastest possible)
    if _is_in_battle(user_id_str):
        await _reply_error(update, f"{username} is currently battling!")
        return
    
    # Lock check (non-blocking)
    if user_id_str not in user_explore_locks:
        user_explore_locks[user_id_str] = asyncio.Lock()
    
    if user_explore_locks[user_id_str].locked():
        return
    
    # Get database reference
    db = context.bot_data.get("db")
    if not db:
        await _reply_error(update, "Internal error: Database not initialized.")
        return
    
    # PARALLEL DATABASE QUERIES - Start both immediately
    player_future = db.get_player(user_id_str)
    player = await player_future
    if not player or not player.team:
        await _reply_error(update, "You haven't created a player account yet! Use /start to begin.")
        return

    # Get character name from first team member
    character_name = player.team[0].character_name if hasattr(player.team[0], 'character_name') else player.team[0]
    
    # Start character query immediately
    character_future = db.get_character(player.user_id, character_name)
    
    # While character is being fetched, prepare other data that doesn't depend on it
    location = getattr(player, 'location', None)
    unlocked_areas = getattr(player, 'unlocked_areas', DEFAULT_AREAS)
    
    # Await character data
    character = await character_future
    player_level = character.level if character and hasattr(character, 'level') else 1
    gas = character.gas if character and hasattr(character, 'gas') else 0
    
    # Quick validations that don't need DB
    if not character_name:
        await _reply_error(update, "You don't have any character in your team.")
        return
    
    if gas < 100:
        await _reply_error(update, f"{character_name} doesn't have enough gas to explore (needs at least 100). Current: {gas}")
        return
    
    # Check for captcha with lower probability 
    if random.random() < 0.02 and context.user_data and not context.user_data.get('captcha_active', False):
        t0 = time.time()
        captcha_triggered = await spawn_captcha(update, context)
        if captcha_triggered:
            return

    # Check for verification requirement (only if not recently checked)
    now = time.time()
    last_verification_check = context.user_data.get("last_verification_check", 0) if context.user_data else 0
    
    if now - last_verification_check > 300:  # Only check every 5 minutes
        player_verified = getattr(player, "hcaptcha_verified", False)
        last_explore = getattr(player, "last_explore_time", None)
        explore_start_time = getattr(player, "explore_start_time", None)

        if context.user_data and context.user_data.get("hcaptcha_prompted", False):
            t0 = time.time()
            player_fresh = await db.get_player(user_id_str, force_refresh=True) if hasattr(db, 'get_player') and 'force_refresh' in db.get_player.__code__.co_varnames else await db.get_player(user_id_str)
            player_verified_fresh = getattr(player_fresh, "hcaptcha_verified", False)
            last_verified_fresh = getattr(player_fresh, "last_verified", 0)
            now_fresh = time.time()
            if player_verified_fresh or (last_verified_fresh and (now_fresh - last_verified_fresh) < 600):
                if context.user_data:
                    context.user_data["hcaptcha_prompted"] = False
                if update.message:
                    await update.message.reply_text("✅ Verification confirmed! You can now continue exploring.")
                await db.update_player(user_id_str, {"hcaptcha_verified": True})
                player = await db.get_player(user_id_str, force_refresh=True) if hasattr(db, 'get_player') and 'force_refresh' in db.get_player.__code__.co_varnames else await db.get_player(user_id_str)
            else:
                await _reply_error(update, "Please complete the hCaptcha verification to continue exploring.")
                return

        if not explore_start_time:
            # Use background update for initial timestamp
            asyncio.create_task(db._background_update_player(user_id, {"last_explore_time": now, "explore_start_time": now}))
        else:
            if (now - explore_start_time) > 1500:
                # Reset timer in background
                asyncio.create_task(db._background_update_player(user_id, {"last_explore_time": now, "explore_start_time": None}))
                last_verified_time = getattr(player, "last_verified", 0)
                if not last_verified_time or (now - last_verified_time) > 1800:
                    t0 = time.time()
                    if await _handle_verification(update, context, user_id, now, db):
                        return
            else:
                # Update timer in background
                asyncio.create_task(db._background_update_player(user_id, {"last_explore_time": now, "explore_start_time": now}))

        if last_explore and (now - last_explore) > 1800:
            last_verified_time = getattr(player, "last_verified", 0)
            if not last_verified_time or (now - last_verified_time) > 1800:
                t0 = time.time()
                if await _handle_verification(update, context, user_id, now, db):
                    return
        
        if context.user_data:
            context.user_data["last_verification_check"] = now
    

    # Generate a random titan instantly from pre-generated pool
    titan_variations = []
    for offset in [-1, 0, 1]:
        lvl = max(1, min(125, player_level + offset))
        titan_variations.extend(TITAN_POOL[lvl])
    
    titan_data = random.choice(titan_variations)
    titan_name = titan_data["name"]
    titan_level = titan_data["level"]
    titan_max_hp = titan_data["max_hp"]
    titan_xp = titan_data["xp_reward"]
    difficulty = titan_data["difficulty"]
    
    titan_image_url = None
    # Try to map titan name to image if possible
    titan_key = titan_name.lower().replace(" titan", "")
    if titan_key in TITAN_TYPE_IMAGE_URLS:
        titan_image_url = TITAN_TYPE_IMAGE_URLS[titan_key]
    else:
        titan_image_url = random.choice(list(TITAN_TYPE_IMAGE_URLS.values()))

    # Generate battle ID
    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id

    # Create message components
    keyboard = [[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Pre-format message with image
    image_embed = f'<a href="{titan_image_url}">!</a>' if titan_image_url else ""
    reply_text = format_titan_message(
        name=titan_name,
        level=titan_level,
        image_embed=image_embed
    )

    # Send response immediately
    if update.message:
        send_message_task = asyncio.create_task(
            update.message.reply_text(
                reply_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        )
    else:
        return

    # Create Titan object for database
    titan = Titan(
        name=titan_name,
        level=titan_level,
        max_hp=titan_max_hp,
        abilities=[],
        created_at=datetime.now(timezone.utc),
        difficulty=difficulty,
        spawn_areas=unlocked_areas,
        drop_table={},
        xp_reward=titan_xp,
        min_level_requirement=max(1, player_level - 2)
    )

    # Record response time BEFORE background processing
    response_time = (time.time() - start_time) * 1000
    logger.info(f"Explore response time: {response_time:.1f}ms")

    # Store titan in background
    store_titan_task = asyncio.create_task(
        db.store_titan(user_id_str, titan)
    )

    # Wait for message to be sent
    try:
        sent_message = await send_message_task
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return

    # Run ALL background processing asynchronously
    asyncio.create_task(_handle_explore_background(
        update, context, user_id, user_id_str, username, db,
        player, titan, sent_message, start_time
    ))



async def _handle_explore_background(update, context, user_id, user_id_str, username, db, player, titan, sent_message, start_time):
    """Handle all background processing for explore command"""
    try:
        # Update player's gas (subtract 100 for exploration)
        character_name = player.team[0].character_name if hasattr(player.team[0], 'character_name') else player.team[0]
        character = await db.get_character(player.user_id, character_name)
        if character and hasattr(character, 'gas'):
            new_gas = max(0, character.gas - 100)
            character.gas = new_gas
            await db.update_character(character)
        
        # Update player's last explore time
        await db.update_player(user_id_str, {"last_explore_time": time.time()})
        
        # Handle mission progress for exploration
        location = getattr(player, 'location', None)
        await process_explore_mission_progress(db, player, location)
        
        # Track explore stats
        await track_explore_stats(user_id_str, username, battle_completed=False)
        
        # Start titan encounter timeout
        asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
        
        # Handle other background tasks
        await _handle_travel_progress(update, context, user_id_str, db, player)
        await _handle_mission_items(update, context, db, player)
        
    except Exception as e:
        logger.error(f"Error in _handle_explore_background: {e}", exc_info=True)

async def _handle_travel_progress(update, context, user_id_str, db, player):
    """Handle travel progress in background"""
    travel = getattr(player, "travel", {})
    
    if not travel.get("in_progress") or _is_in_battle(user_id_str):
        return
    
    travel_progress = travel.get("progress", 0) + 1
    travel_required = travel.get("required", 1)
    
    if travel_progress >= travel_required:
        # Travel completed
        new_location = travel.get("to", player.location)
        from_location = travel.get("from", "Unknown")
        
        update_data = {
            "location": new_location,
            "travel": {}
        }
        
        await db.update_player(user_id_str, update_data)
        
        # Notify user
        try:
            arrival_message = f"🗺️ You have arrived at <b>{new_location}</b>!"
            if update.message:
                await update.message.reply_text(arrival_message, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        # Update progress - fix for travel.progress field issue
        current_travel = getattr(player, "travel", {})
        updated_travel = current_travel.copy()
        updated_travel["progress"] = travel_progress
        await db.update_player(user_id_str, {"travel": updated_travel})

async def _handle_mission_items(update, context, db, player):
    """Handle mission item drops in background"""
    try:
        active_missions = [m for m in player.missions if m["status"] == "in_progress"]
        if not active_missions:
            return
        
        mission_item_drops = await check_mission_item_drops(player)
        
        for item_drop in mission_item_drops:
            result = await add_mission_item(db, player, item_drop["key"])
            if isinstance(result, tuple) and len(result) == 2:
                success, msg = result
                if success and msg:
                    try:
                        if update.message:
                            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                    
    except Exception as e:
        logger.error(f"Mission items error: {e}")

async def _send_spam_warning(user_id: int, context: ContextTypes.DEFAULT_TYPE, warning_level: int, message: str):
    """Send spam warning message to user in background"""
    try:
        # Get bot instance
        bot = context.bot
        if not bot:
            return
            
        # Send warning message
        await bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"Sent spam warning level {warning_level} to user {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to send spam warning to user {user_id}: {e}")


async def _handle_spam_ban(user_id, update, context):
    """Handle spam banning in background - optimized for async"""
    try:
        db = context.bot_data.get("db")
        if not db:
            return
        
        current_time = int(time.time())
        expiry = current_time + 24*3600
        reason = "Spamming explore without battle"
        
        # Run the database update and notification in parallel
        tasks = []
        
        # 1. Database update
        ban_data = {
            "user_id": user_id, 
            "expiry": expiry, 
            "reason": reason, 
            "banned_by": user_id, 
            "banned_at": current_time
        }
        
        db_task = asyncio.create_task(
            db.bans.update_one(
                {"user_id": user_id},
                {"$set": ban_data},
                upsert=True
            )
        )
        tasks.append(db_task)
        
        # 2. Send notification to user
        if update.message:
            notify_task = asyncio.create_task(
                update.message.reply_text("You are banned for spamming explore without battle.")
            )
            tasks.append(notify_task)
        
        # Reset spam count immediately (in-memory operation)
        context.bot_data["explore_spam_count"][str(user_id)] = 0
        
        # Wait for database and notification tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Optional: Send notification to admin channel
        try:
            bot_username = (await context.bot.get_me()).username or "Bot"
            ban_log_msg = (
                f"<b>#BanEvent</b>\n\n"
                f"<b>Target</b> : <a href=\"tg://user?id={user_id}\">{update.effective_user.first_name}</a>\n"
                f"<b>Target ID</b> : <code>{user_id}</code>\n"
                f"<b>By</b> : <a href=\"tg://user?id={context.bot.id}\">{bot_username}</a>\n"
                f"<b>Reason</b> : <code>Spamming explore without battle</code>\n"
                f"<b>Time</b> : <code>24 hours</code>"
            )
            
            # Send this in fire-and-forget mode
            BAN_LOG_CHAT_ID = -1002873117075
            asyncio.create_task(
                context.bot.send_message(BAN_LOG_CHAT_ID, ban_log_msg, parse_mode=ParseMode.HTML)
            )
        except Exception:
            pass
        
    except Exception as e:
        logger.error(f"Spam ban error: {e}", exc_info=True)

# Keep existing timeout and other helper functions...
async def titan_encounter_timeout(user_id: int, context: ContextTypes.DEFAULT_TYPE, sent_message=None):
    """Handle titan encounter timeout with proper cleanup - optimized for async efficiency"""
    try:
        # Record the start time to check if new explore commands happened during the timeout
        start_time = time.time()
        
        # Sleep for the timeout duration
        await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
        
        # Fast-fail conditions - check these first to exit early if possible
        user_id_str = str(user_id)
        
        # Check if user has explored since this timeout was created
        last_explore_time = user_last_explore.get(user_id_str, 0)
        if last_explore_time > start_time:
            return
        
        # Check if user is in battle - no need to do anything if so
        if _is_in_battle(user_id_str):
            return
        
        # SPAM DETECTION: User didn't explore or battle during timeout - increment spam count
        if "explore_spam_count" not in context.bot_data:
            context.bot_data["explore_spam_count"] = {}
        
        if user_id_str not in context.bot_data["explore_spam_count"]:
            context.bot_data["explore_spam_count"][user_id_str] = 0
        
        # Increment spam count for not battling
        context.bot_data["explore_spam_count"][user_id_str] += 1
        current_spam_count = context.bot_data["explore_spam_count"][user_id_str]
        
        # Send warning only at 10 timeouts
        if current_spam_count == 10:
            # Warning at 10 timeouts
            asyncio.create_task(_send_spam_warning(user_id, context, 10, "🚨 <b>Warning:</b> You have let 10 titan encounters expire!\n\nContinuing this behavior may result in a ban. Please battle the titans you encounter."))
        
        # Check if spam count exceeds threshold (15 timeouts without battle)
        SPAM_THRESHOLD = 15
        if current_spam_count >= SPAM_THRESHOLD:
            # Trigger spam ban
            logger.warning(f"User {user_id} reached spam threshold ({SPAM_THRESHOLD}) - triggering ban")
            # Create a mock update object for the ban function
            class MockUpdate:
                def __init__(self, user_id, context):
                    self.effective_user = type('obj', (object,), {'id': user_id, 'first_name': f'User_{user_id}'})()
                    self.message = type('obj', (object,), {'reply_text': lambda text: None})()
            
            mock_update = MockUpdate(user_id, context)
            await _handle_spam_ban(user_id, mock_update, context)
            return
        # Get battle ID info to compare later
        battle_id_key = f"active_battle_id_{user_id}"
        current_battle_id = context.bot_data.get(battle_id_key)
        
        # Get database and check for titan
        db = context.bot_data.get("db")
        if not db:
            return
            
        # Perform database operations and message editing in parallel
        tasks = []
        
        # 1. Delete titan from database
        titan_check_task = asyncio.create_task(db.get_titan(user_id_str))
        titan_in_db = await titan_check_task
        
        if titan_in_db:
            # Launch delete task but don't wait for it
            delete_task = asyncio.create_task(db.delete_titan(user_id_str))
            tasks.append(delete_task)
            
            # 2. Update message if the battle ID is still the same
            if sent_message and current_battle_id == context.bot_data.get(battle_id_key):
                try:
                    from game.safe_edit import safe_edit_message_text
                    edit_task = asyncio.create_task(safe_edit_message_text(
                        sent_message,
                        "⏰ Titan encounter expired!\n\nYou took too long to respond. Use /explore to find another titan.",
                        parse_mode=ParseMode.HTML
                    ))
                    tasks.append(edit_task)
                except Exception:
                    pass
                    
        # Wait for all tasks to complete if there are any
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
                        
    except asyncio.CancelledError:
        # Task was cancelled - this is expected behavior
        pass
    except Exception as e:
        logger.error(f"Error in titan_encounter_timeout: {e}", exc_info=True)


@mod_only
async def reset_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    # Determine target user (either from reply or from args)
    target_user_id = None
    
    # Check if replying to a message
    if update.message and update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        target_user_name = update.message.reply_to_message.from_user.first_name
    # Check if user ID was provided as argument
    elif context.args and context.args[0].isdigit():
        target_user_id = int(context.args[0])
        target_user_name = f"User {target_user_id}"
    else:
        if update.message:
            await update.message.reply_text(
                "❌ Please either reply to a user's message or provide a user ID."
            )
        return
    
    # Reset verification for the target user
    success = await reset_verification_state(target_user_id, context)
    
    if success:
        if update.message:
            await update.message.reply_text(
                f"✅ Verification state has been reset for {target_user_name}.\n"
                f"They can now use /explore again normally."
            )
    else:
        if update.message:
            await update.message.reply_text(
                f"❌ Failed to reset verification state for {target_user_name}.\n"
                f"Please try again later or check if the user ID is valid."
            )


@maintenance_protected
@ban_protected
async def open_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /open command to show the keyboard for exploring."""
    
    if not update.effective_chat or update.effective_chat.type != "private":
        if update.message:
            await update.message.reply_text("This command can only be used in private chats.")
        return
    
    # Create a persistent keyboard with explore and close buttons
    keyboard = [["/explore", "/close"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )
    
    # Set flag to prevent showing keyboard multiple times
    if context.user_data is not None:
        context.user_data["persistent_keyboard_sent"] = True
    
    # Show keyboard to user
    if update.message:
        await update.message.reply_text(
            "Keyboard opened. You can use these buttons to explore or close the keyboard.",
            reply_markup=reply_markup
        )

@ban_protected
async def close_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close the persistent keyboard menu."""
    if update.message:
        await update.message.reply_text(
            "Closing keyboard...",
            reply_markup=ReplyKeyboardRemove()
        )


async def reset_verification_state(user_id: int, context: ContextTypes.DEFAULT_TYPE):
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
        
        return True
    except Exception as e:
        logger.error(f"Failed to reset verification state: {e}")
        return False