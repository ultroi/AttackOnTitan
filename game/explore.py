import asyncio
import logging
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple
from uuid import uuid4
from collections import OrderedDict

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                    ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database.db import Database
from database.models import (Player, Titan, Character, CharacterStats, generate_titan_hp,
                           generate_titan_name, generate_titan_xp)
from game.captcha import spawn_captcha
from game.dealer_system import show_dealer
from game.stats_command import track_explore_stats
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected

logger = logging.getLogger(__name__)

# =====================================================================================
# Constants and Global Variables
# =====================================================================================

TITAN_TIMEOUT_SECONDS = 60
BATTLE_BUTTON_TEXT = "⚔️ Battle"
DEFAULT_AREAS = ["Trost", "Karanes", "Shiganshina", "Orvud"]
EXPLORATION_MOODS = [
    "A chilling wind whispers:",
    "The ground trembles as:",
    "In the misty horizon:",
    "You sense danger ahead:",
    "A distant roar echoes:",
]
AREA_SCENES = {
    "Trost": [
        "the ruins of Trost hide a lurking terror.",
        "shadows twist among broken walls.",
        "a cold fog curls around collapsed towers."
    ],
    "Karanes": [
        "the burned plains of Karanes hold a deadly silence.",
        "smoke and flame reveal a wounded titan.",
        "ashes swirl as you press forward."
    ],
    "Shiganshina": [
        "the outer gates of Shiganshina still stand, faintly humming.",
        "distant cries carry from the forest edge.",
        "old battle scars form an eerie path."
    ],
    "Orvud": [
        "the cratered fields of Orvud tremble beneath your boots.",
        "fog veils ruins where something moves.",
        "blighted trees gnarled by titan corruption."
    ],
}
MAX_CACHE_SIZE = 500  # CRITICAL: Limit cache size
MAX_BOT_DATA_ENTRIES = 1000  # Prevent unbounded growth

# Short per-user explore cooldown (seconds) — prevents rapid-fire abuse
EXPLORE_COOLDOWN_SECONDS = 0.5

# Anti-spam: Continuous explore without battle protection
CONSECUTIVE_EXPLORE_WARNING_THRESHOLD = 10  # Warn at 10 consecutive explores without battle
CONSECUTIVE_EXPLORE_BAN_THRESHOLD = 15      # Ban at 15 consecutive explores without battle
user_consecutive_explores: Dict[str, int] = {}  # Track consecutive explores per user
user_explore_warned: Dict[str, bool] = {}  # Track if user was already warned

# Performance: In-memory caches with size limits
user_last_explore: Dict[str, float] = {}
user_explore_locks: Dict[str, asyncio.Lock] = {}
user_timeout_tasks: Dict[str, asyncio.Task] = {}
_battle_system_cache = {}
user_cache: Dict[str, Tuple[Player, Character]] = OrderedDict()  # LRU ordering
cache_expiry: Dict[str, float] = {}
CACHE_TTL = 300

TITAN_TYPE_IMAGE_URLS = {
    "goofy grinning": "https://i.ibb.co/dJ6J58s0/image.jpg",
    "beast": "https://i.ibb.co/B2C79CM4/image.jpg",
    "ancient beast": "https://i.ibb.co/jZJHL3D3/image.jpg",
    "potbellied": "https://i.ibb.co/XkMw0Xt5/image.jpg",
    "dancing": "https://i.ibb.co/DgK98CzY/image.jpg",
    "bearded": "https://i.ibb.co/7J8S4s6v/image.jpg",
    "gaping mouth": "https://i.ibb.co/9mMK2FG1/image.jpg",
    "smiling": "https://i.ibb.co/3YTw6Wy2/image.jpg",
    "abnormal": "https://i.ibb.co/4Z6kfGnJ/image.jpg",
    "small jogger": "https://i.ibb.co/Fk8NspGP/image.jpg",
    "leaper": "https://i.ibb.co/k2XqYdX6/image.jpg",
    "cart": "https://i.ibb.co/nNRhLMqH/image.jpg",
    "female": "https://i.ibb.co/vNQF4Cb/image.jpg",
    "bloated": "https://i.ibb.co/fYrcqngz/image.jpg",
    "staggering creepers": "https://i.ibb.co/mFchdbj9/image.jpg",
    "wailing": "https://i.ibb.co/1JJQg9Db/image.jpg",
}

BOSS_TITAN_IMAGE_URLS = ["https://i.ibb.co/cz6bJ0J/image.jpg"]

# =====================================================================================
# OPTIMIZATION: Pre-computed Image Cache
# =====================================================================================

# Pre-computed image mappings (loaded at bot startup)
TITAN_IMAGE_CACHE = {}

def precompute_titan_images():
    global TITAN_IMAGE_CACHE
    TITAN_IMAGE_CACHE.clear()
    for titan_type, url in TITAN_TYPE_IMAGE_URLS.items():
        TITAN_IMAGE_CACHE[titan_type] = url
    logger.info(f"✅ Pre-computed {len(TITAN_IMAGE_CACHE)} titan images")

# =====================================================================================
# ANTI-SPAM: Continuous Explore Detection
# =====================================================================================

async def check_consecutive_explores_spam(user_id_str: str, user_id: int, context: ContextTypes.DEFAULT_TYPE, update: Update) -> Optional[bool]:
    # Increment consecutive explore count
    user_consecutive_explores[user_id_str] = user_consecutive_explores.get(user_id_str, 0) + 1
    consecutive_count = user_consecutive_explores[user_id_str]
    
    # Check BAN threshold (15 consecutive explores without battle)
    if consecutive_count >= CONSECUTIVE_EXPLORE_BAN_THRESHOLD:
        logger.warning(f"🚫 AUTO-BAN: User {user_id_str} has {consecutive_count} consecutive explores without battle!")
        
        # Import ban function
        from utils.ban_utils import ban_user as ban_user_func
        
        # Ban the user for 24 hours
        db = context.bot_data.get("db")
        if db:
            ban_until = datetime.now(timezone.utc) + timedelta(hours=24)
            await db.add_ban(
                user_id_str, 
                "Auto-banned for consecutive explore spam (15+ without battle)",
                ban_until=ban_until
            )
        
        # Get user info for group notification
        username = update.effective_user.username if update.effective_user else "Unknown"
        user_link = f"<a href='tg://user?id={user_id}'>@{username}</a>" if username else f"User {user_id}"
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🚫 <b>You have been BANNED for 24 HOURS!</b>\n\n"
                     "Reason: Excessive explore spam (15+ without battle)\n\n"
                     "⏰ Duration: 24 hours\n\n"
                     "⚠️ If this is a mistake, contact support.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to notify banned user {user_id_str}: {e}")
        
        # Notify moderation group
        try:
            await context.bot.send_message(
                chat_id=-1002873117075,  # Spam group
                text=f"#Spam\n\n"
                     f"<b>Name:</b> {user_link}\n"
                     f"<b>ID:</b> <code>{user_id}</code>\n"
                     f"<b>Reason:</b> Consecutive explore spam (15 without battle)\n"
                     f"<b>Duration:</b> 24h\n"
                     f"<b>Auto-Ban:</b> ✅ Active",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send spam report to group: {e}")
        
        # Clean up tracking
        user_consecutive_explores.pop(user_id_str, None)
        user_explore_warned.pop(user_id_str, None)
        
        return True  # User banned
    
    # Check WARNING threshold (10 consecutive explores without battle)
    elif consecutive_count == CONSECUTIVE_EXPLORE_WARNING_THRESHOLD and not user_explore_warned.get(user_id_str, False):
        logger.warning(f"⚠️ WARNING: User {user_id_str} has {consecutive_count} consecutive explores without battle!")
        
        # Mark user as warned
        user_explore_warned[user_id_str] = True
        
        # Send warning message
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="⚠️ <b>SPAM WARNING!</b>\n\n"
                     "❌ Continuing to spam explore will result in a <b>BAN</b>!\n\n",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send warning to user {user_id_str}: {e}")
        
        return False  # Warning sent
    
    return None  # No action needed

def reset_consecutive_explores(user_id_str: str):
    """Reset consecutive explore counter when user engages in battle"""
    if user_id_str in user_consecutive_explores and user_consecutive_explores[user_id_str] > 0:
        logger.info(f"✅ Reset explore spam counter for {user_id_str} (they engaged in battle)")
        user_consecutive_explores[user_id_str] = 0
        user_explore_warned.pop(user_id_str, None)

def reset_spam_tracking_by_id(user_id: int):
    """Public function to reset spam tracking (called by battle system)"""
    user_id_str = str(user_id)
    reset_consecutive_explores(user_id_str)

def cleanup_spam_tracking():
    """Remove spam tracking for users who haven't explored in 1 hour"""
    current_time = time.time()
    users_to_clean = []
    
    for user_id_str, last_time in user_last_explore.items():
        if current_time - last_time > 3600:  # 1 hour timeout
            users_to_clean.append(user_id_str)
    
    for user_id_str in users_to_clean:
        user_consecutive_explores.pop(user_id_str, None)
        user_explore_warned.pop(user_id_str, None)
    
    if users_to_clean:
        logger.debug(f"Cleaned up spam tracking for {len(users_to_clean)} idle users")

# =====================================================================================
# MEMORY: Cleanup Functions
# =====================================================================================

def cleanup_bot_data(bot_data: dict):
    """Prevent unbounded growth of bot_data"""
    titan_keys = [k for k in bot_data if k.startswith("last_titan_data_")]
    battle_keys = [k for k in bot_data if k.startswith("active_battle_id_")]
    
    # Keep only recent 500 entries
    if len(titan_keys) > 500:
        for key in sorted(titan_keys)[:-500]:
            bot_data.pop(key, None)
    
    if len(battle_keys) > 500:
        for key in sorted(battle_keys)[:-500]:
            bot_data.pop(key, None)

def cleanup_cache():
    """Remove expired cache entries"""
    current_time = time.time()
    expired_keys = [
        k for k, expiry_time in cache_expiry.items() 
        if expiry_time <= current_time
    ]
    
    for key in expired_keys:
        user_cache.pop(key, None)
        cache_expiry.pop(key, None)
    
    # Also enforce max size (LRU)
    while len(user_cache) > MAX_CACHE_SIZE:
        # Remove oldest (OrderedDict pops from front)
        oldest_key, _ = user_cache.popitem(last=False)
        cache_expiry.pop(oldest_key, None)  # Ensure both structures stay in sync

def cleanup_locks():
    """Remove unused locks to prevent accumulation"""
    unlocked_users = [
        user_id for user_id, lock in user_explore_locks.items()
        if not lock.locked()
    ]
    
    # Only keep locks for active users (last 10 minutes)
    current_time = time.time()
    for user_id in unlocked_users:
        if user_id not in user_last_explore or \
           (current_time - user_last_explore.get(user_id, 0)) > 600:
            user_explore_locks.pop(user_id, None)

def cleanup_timeout_tasks():
    """Remove completed timeout tasks"""
    completed_users = [
        user_id for user_id, task in user_timeout_tasks.items()
        if task.done()
    ]
    
    for user_id in completed_users:
        user_timeout_tasks.pop(user_id, None)

def cleanup_all():
    """Comprehensive cleanup (called periodically)"""
    cleanup_cache()
    cleanup_locks()
    cleanup_timeout_tasks()
    cleanup_spam_tracking()  # ← Added spam tracking cleanup

# =====================================================================================
# PERFORMANCE: Ultra-Fast Pre-Check System
# =====================================================================================

class FastPreCheck:
    """In-memory pre-checks that run in microseconds"""
    
    @staticmethod
    def is_locked(user_id_str: str) -> bool:
        """Check if user has explore lock (in-memory only)"""
        if user_id_str not in user_explore_locks:
            user_explore_locks[user_id_str] = asyncio.Lock()
        return user_explore_locks[user_id_str].locked()
    
    @staticmethod
    def is_in_battle(user_id_str: str) -> bool:
        """Check if user is in battle (in-memory only)"""
        try:
            from game.battle_system import active_battles
            _battle_system_cache['active_battles'] = active_battles
        except ImportError:
            _battle_system_cache.setdefault('active_battles', {})

        try:
            from game.pvp_system import active_pvp_battles
            _battle_system_cache['active_pvp_battles'] = active_pvp_battles
        except ImportError:
            _battle_system_cache.setdefault('active_pvp_battles', {})

        return user_id_str in _battle_system_cache.get('active_battles', {}) or \
               user_id_str in _battle_system_cache.get('active_pvp_battles', {})
    
    @staticmethod
    def is_chat_private(update: Update) -> bool:
        """Check if chat is private"""
        return update.effective_chat is not None and update.effective_chat.type == "private"

# =====================================================================================
# PERFORMANCE: Caching Layer (FIXED)
# =====================================================================================

async def get_cached_player_data(user_id_str: str, db: Database) -> Optional[Tuple[Player, Character]]:
    """ULTRA-FAST cached player + character data (< 5ms) with proper expiry"""
    current_time = time.time()
    
    # FASTEST: Check cache first (< 0.1ms)
    if user_id_str in cache_expiry:
        if cache_expiry[user_id_str] > current_time:
            # Move to end (LRU)
            if isinstance(user_cache, OrderedDict):
                user_cache.move_to_end(user_id_str)
            return user_cache[user_id_str]
        else:
            # FIX: Clean up expired entry immediately
            user_cache.pop(user_id_str, None)
            cache_expiry.pop(user_id_str, None)
    
    # Cache miss - fetch with minimal overhead
    try:
        player = await db.get_player(user_id_str)
        if not player or not hasattr(player, 'team') or not player.team:
            return None
        
        character = await db.get_character(user_id_str, player.team[0].character_name)
        
        # AUTO-CREATE: If character data missing, create it now
        if not character or not character.stats:
            logger.warning(f"⚠️ Character document missing for {user_id_str}/{player.team[0].character_name}, creating...")
            try:
                success = await db.add_new_character_to_player(user_id_str, player.team[0].character_name)
                if success:
                    # Try fetching again
                    character = await db.get_character(user_id_str, player.team[0].character_name)
                    logger.info(f"✅ Successfully created character document for {player.team[0].character_name}")
                else:
                    logger.error(f"❌ Failed to create character {player.team[0].character_name}")
                    return None
            except Exception as e:
                logger.error(f"❌ Error auto-creating character: {e}")
                return None
        
        if not character or not character.stats:
            return None
        
        # Update cache inline
        result = (player, character)
        user_cache[user_id_str] = result
        cache_expiry[user_id_str] = current_time + CACHE_TTL
        
        # FIX: Enforce max cache size
        if len(user_cache) > MAX_CACHE_SIZE:
            oldest_key = next(iter(user_cache))
            user_cache.pop(oldest_key, None)
            cache_expiry.pop(oldest_key, None)
        
        return result
        
    except Exception as e:
        logger.error(f"Cache fetch error: {e}")
        return None

# =====================================================================================
# PERFORMANCE: Parallel Event Generation
# =====================================================================================

def check_random_events() -> Optional[str]:
    """Ultra-fast random event check - returns event type or None"""
    rand_val = random.random()
    # Distribution:
    #  - boss_titan: 2%
    #  - dealer: next 3% (total < 5%)
    #  - captcha: next 5% (total < 10%)
    if rand_val < 0.02:
        return "boss_titan"
    elif rand_val < 0.05:
        return "dealer"
    elif rand_val < 0.10:
        return "captcha"
    return None

# =====================================================================================
# CORE: Ultra-Fast Explore (FIXED)
# =====================================================================================

@maintenance_protected
@ban_protected
async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_chat:
        return
    
    # Check 1: Private chat
    if not FastPreCheck.is_chat_private(update):
        await _reply_error(update, "This command can only be used in private chats.")
        return
    
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Check 2: Already locked
    if FastPreCheck.is_locked(user_id_str):
        return
    
    # Check 3: In battle
    if FastPreCheck.is_in_battle(user_id_str):
        await _reply_error(update, "⚔️ You are currently in a battle! Complete it first.")
        return

    # Short per-user cooldown to prevent rapid-fire explore abuse
    now_ts = time.time()
    last_ts = user_last_explore.get(user_id_str, 0)
    if now_ts - last_ts < EXPLORE_COOLDOWN_SECONDS:
        wait = int(EXPLORE_COOLDOWN_SECONDS - (now_ts - last_ts))
        if wait <= 0:
            wait = 1
        await _reply_error(update, f"⚠️ Slow down — try again in {wait}s.")
        return
    
    db = context.bot_data.get("db")
    if not db:
        await _reply_error(update, "Database not available.")
        return
    
    # Periodic cleanup (every 10 explores to avoid overhead)
    if random.random() < 0.1:
        cleanup_cache()
        cleanup_locks()
        cleanup_timeout_tasks()
        cleanup_bot_data(context.bot_data)
        cleanup_spam_tracking()  # ← Added spam cleanup
    
    # Pre-generate random values
    event_type = check_random_events()
    
    # Check cache
    current_time = time.time()
    user_last_explore[user_id_str] = current_time  # Track activity
    cached_data = None
    
    if user_id_str in cache_expiry and cache_expiry[user_id_str] > current_time:
        cached_data = user_cache[user_id_str]
    
    # Fetch data if needed
    if cached_data:
        player, character = cached_data
    else:
        try:
            cached_data = await asyncio.wait_for(
                get_cached_player_data(user_id_str, db),
                timeout=2.0
            )
            if not cached_data:
                await _reply_error(update, "Player data not found. Use /start first!")
                return
            player, character = cached_data
        except asyncio.TimeoutError:
            await _reply_error(update, "⚠️ Database timeout. Try again!")
            return
    
    # ANTI-SPAM: Check for consecutive explores without battles
    spam_result = await check_consecutive_explores_spam(user_id_str, user_id, context, update)
    if spam_result is True:
        # User banned
        return
    elif spam_result is False:
        # Warning sent, continue but log it
        logger.warning(f"⚠️ Spam warning sent to user {user_id_str}")
    
    # Handle random events
    if event_type == "dealer":
        try:
            # Offload the dealer flow so we return to the user immediately (non-blocking)
            asyncio.create_task(show_dealer(update, context))
            # Track stats in background (fire-and-forget)
            try:
                asyncio.create_task(track_explore_stats(user_id_str, update.effective_user.username or player.username, False))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Dealer event error: {e}", exc_info=True)
        return
    
    elif event_type == "captcha":
        try:
            await spawn_captcha(update, context)
            try:
                asyncio.create_task(track_explore_stats(user_id_str, update.effective_user.username or player.username, False))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Captcha event error: {e}", exc_info=True)
        return
    
    elif event_type == "boss_titan":
        try:
            # Spawn boss titan in background - immediate return to user
            asyncio.create_task(spawn_boss_titan_directly(update, context, user_id_str, player, character, db))
            asyncio.create_task(track_explore_stats(user_id_str, update.effective_user.username or player.username, False))
        except Exception as e:
            logger.error(f"Boss Titan event error: {e}", exc_info=True)
        return

    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    
    # Generate titan
    difficulty = get_titan_difficulty_by_level(character.level)
    
    min_level = max(1, character.level - 3)
    max_level = character.level + 3
    titan_level = random.randint(min_level, max_level)
    
    titan_hp = generate_titan_hp(level=titan_level, difficulty=difficulty, character_stats=character.stats if isinstance(character.stats, CharacterStats) else None)
    titan_name = generate_titan_name(difficulty)
    titan_xp = generate_titan_xp(titan_level, difficulty)
    
    # OPTIMIZATION: Use fast image lookup (O(1) from pre-computed cache)
    titan_image = get_titan_image_fast(titan_name)
    
    # Create titan object
    titan = Titan(
        name=titan_name,
        level=titan_level,
        max_hp=titan_hp,
        xp_reward=titan_xp,
        difficulty=difficulty,
        created_at=datetime.now(timezone.utc),
        spawn_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS),
        min_level_requirement=max(1, titan_level - 2),
        abilities=[],
        drop_table={}
    )
    
    # Build exploration narrative + titan message
    scene_text = generate_explore_scene(
        unlocked_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS)
    )

    reply_text = (
        f"<code>-------------------------</code>\n"
        f"{scene_text}\n"
        f"📍 <b>{titan_name} Lvl ({titan_level})</b>\n"
        f"<b>has blocked your way<a href=\"{titan_image}\">&#8203;</a></b>\n"
        f"<code>-------------------------</code>"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]])
    
    # Send message
    sent_message = None
    try:
        if not update.message:
            return
        
        context.bot_data[f"active_battle_id_{user_id}"] = battle_id
        
        # FIX: Store minimal titan data only
        minimal_titan_data = {
            "name": titan_name,
            "level": titan_level,
            "max_hp": titan_hp,
            "xp_reward": titan_xp,
            "difficulty": difficulty,
            "image_url": titan_image,
            "is_boss": False
        }
        context.bot_data[f"last_titan_data_{user_id_str}"] = minimal_titan_data

        # Use link preview (hidden anchor) so Telegram renders the image preview
        sent_message = await update.message.reply_text(
            reply_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )

    except Exception as e:
        logger.error(f"Send error: {e}")
        return
    
    # Deferred operations
    try:
        await asyncio.wait_for(
            _deferred_explore_operations(
                context, user_id_str, user_id, db, titan, sent_message, 
                player, update.effective_user.username
            ),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        logger.warning(f"Explore deferred operations timed out for user {user_id_str}")
    except Exception as e:
        logger.error(f"Error in deferred explore operations: {e}")


async def _deferred_explore_operations(context, user_id_str, user_id, db, titan, 
                                      sent_message, player, username):
    """All heavy operations run in parallel with optimized concurrency"""
    try:
        # Ensure a lock exists for this user before using it (race-safe)
        lock = user_explore_locks.get(user_id_str)
        if lock is None:
            lock = asyncio.Lock()
            user_explore_locks[user_id_str] = lock

        async with lock:
            # Ensure old titan is removed before storing the new one (prevents race/delete of newly stored titan)
            await _cleanup_existing_titan(user_id_str, db)

            # Prepare update data (no I/O - pure computation)
            player.increment_daily_explores(datetime.now(timezone.utc))
            update_data = {
                "last_explore_time": time.time(),
                "daily_explores": player.daily_explores
            }

            # Handle travel (can run in parallel)
            travel = getattr(player, "travel", {})
            travel_update_task = None
            if travel.get("in_progress") and not FastPreCheck.is_in_battle(user_id_str):
                travel_progress = travel.get("progress", 0) + 1
                if travel_progress >= travel.get("required", 1):
                    update_data["location"] = travel.get("to", player.location)
                    update_data["travel"] = {}
                    travel_update_task = asyncio.create_task(
                        context.bot.send_message(
                            chat_id=user_id,
                            text=f"🗺️ Arrived at <b>{travel.get('to')}</b>!",
                            parse_mode=ParseMode.HTML
                        )
                    )
                else:
                    travel["progress"] = travel_progress
                    update_data["travel"] = travel

            # Run DB operations in parallel (store titan + update player)
            parallel_tasks = [
                db.store_titan(user_id_str, titan),
                db.batch_update_player(user_id_str, update_data),
            ]
            if travel_update_task:
                parallel_tasks.append(travel_update_task)

            await asyncio.gather(*parallel_tasks, return_exceptions=True)
            
            # FIX: Store minimal data
            minimal_titan_data = {
                "name": titan.name,
                "max_hp": titan.max_hp,
                "xp_reward": titan.xp_reward,
                "difficulty": titan.difficulty
            }
            context.bot_data[f"last_titan_data_{user_id_str}"] = minimal_titan_data
            
            # Cancel old timeout before starting new one
            old_timeout_task = user_timeout_tasks.get(user_id_str)
            if old_timeout_task and not old_timeout_task.done():
                old_timeout_task.cancel()
            
            # Start timeout
            timeout_task = asyncio.create_task(
                titan_encounter_timeout(user_id, context, sent_message)
            )
            user_timeout_tasks[user_id_str] = timeout_task
            context.bot_data[f"titan_timeout_{user_id_str}"] = timeout_task
            
            # Track stats (fire and forget with timeout)
            try:
                await asyncio.wait_for(
                    track_explore_stats(user_id_str, username or player.username, False),
                    timeout=0.5
                )
            except (asyncio.TimeoutError, Exception):
                pass
                
    except Exception as e:
        logger.error(f"Error in deferred operations: {e}", exc_info=True)

# =====================================================================================
# Helper Functions
# =====================================================================================

def get_titan_difficulty_by_level(level: int) -> str:
    """Returns difficulty level based on player level"""
    if level < 50:
        return "Easy"
    elif level <= 100:
        return "Normal"
    else:  
        return "Hard"

def get_titan_image_fast(titan_name: str) -> str:
    """Ultra-fast image lookup (O(1) - from pre-computed cache)"""
    titan_key = titan_name.lower().replace(" titan", "")
    # Use pre-computed cache first (fastest)
    if TITAN_IMAGE_CACHE:
        return TITAN_IMAGE_CACHE.get(titan_key, random.choice(list(TITAN_IMAGE_CACHE.values())))
    # Fallback if cache not initialized
    return TITAN_TYPE_IMAGE_URLS.get(titan_key, random.choice(list(TITAN_TYPE_IMAGE_URLS.values())))


def generate_explore_scene(area: Optional[str] = None, unlocked_areas: Optional[list] = None) -> str:
    """Generate an immersive description stanza based on area."""
    valid_areas = [a for a in (unlocked_areas or []) if a in AREA_SCENES]
    if area and area in AREA_SCENES:
        chosen_area = area
    elif valid_areas:
        chosen_area = random.choice(valid_areas)
    else:
        chosen_area = random.choice(DEFAULT_AREAS)

    mood = random.choice(EXPLORATION_MOODS)
    scene = random.choice(AREA_SCENES.get(chosen_area, ["the wild is filled with unknown threats."]))

    return f"{mood} {scene}"

def format_titan_message(name: str, level: int, image_embed: str = "") -> str:
    return (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{name} Lvl ({level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>"
    )

def format_boss_titan_message(name: str, level: int, image_embed: str = "") -> str:
    return (
        f"<code>-------------------------</code>\n"
        f"🚨 <b>BOSS APPEARED!</b> 🚨\n"
        f"🔥 <b>{name} Lvl ({level})</b>\n"
        f"<b>stands in your path, radiating immense power!{image_embed}</b>\n"
        f"<code>-------------------------</code>"
    )

async def _cleanup_existing_titan(user_id_str: str, db: Database):
    """Clean up old titan with race condition protection"""
    try:
        if FastPreCheck.is_in_battle(user_id_str):
            return
        
        result = await db.titans.delete_one({"user_id": user_id_str}) if db.titans is not None else None
        deleted_count = getattr(result, 'deleted_count', 0) if result else 0
        if deleted_count > 0:
            db.invalidate_titan_cache(user_id_str)
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# =====================================================================================
# Random Events
# =====================================================================================

async def spawn_boss_titan_directly(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                    user_id_str: str, player: Player, character: Character, db: Database):
    """Spawn boss titan directly"""
    try:
        user_id = int(user_id_str)
        
        if user_id_str not in user_explore_locks:
            user_explore_locks[user_id_str] = asyncio.Lock()
        
        async with user_explore_locks[user_id_str]:
            await _cleanup_existing_titan(user_id_str, db)
            
            # Generate BOSS titan
            boss_level = character.level + random.randint(1, 3)  # +1 to +3 for epic challenge
            boss_hp = generate_titan_hp(level=boss_level, difficulty="Hard", character_stats=None) * 3
            boss_name = "Armored Titan"
            boss_xp = generate_titan_xp(boss_level, "Hard") * 5
            
            # OPTIMIZATION: Use pre-computed boss image (fastest - no lookup needed)
            boss_image_url = BOSS_TITAN_IMAGE_URLS[0] if BOSS_TITAN_IMAGE_URLS else "https://i.ibb.co/cz6bJ0J/image.jpg"
            
            # Create boss titan
            boss_titan = Titan(
                name=boss_name,
                level=boss_level,
                max_hp=boss_hp,
                xp_reward=boss_xp,
                difficulty="Hard",
                created_at=datetime.now(timezone.utc),
                spawn_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS),
                min_level_requirement=max(1, boss_level - 2),
                abilities=[],
                drop_table={},
                is_boss=True
            )
            
            # Store boss titan
            await db.store_titan(user_id_str, boss_titan)
            
            # FIX: Store minimal data only
            minimal_boss_data = {
                "name": boss_name,
                "level": boss_level,
                "max_hp": boss_hp,
                "xp_reward": boss_xp,
                "difficulty": "Hard",
                "image_url": boss_image_url,
                "is_boss": True
            }
            context.bot_data[f"last_titan_data_{user_id_str}"] = minimal_boss_data
            
            # Create battle button
            battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
            context.bot_data[f"active_battle_id_{user_id}"] = battle_id
            
            # Scene text for boss encounter
            scene_text = generate_explore_scene(
                unlocked_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS)
            )

            # Send boss message (link preview with hidden anchor)
            image_embed = f'<a href="{boss_image_url}">&#8203;</a>'
            boss_message = (
                f"<code>-------------------------</code>\n"
                f"{scene_text}\n"
                f"🚨 <b>BOSS APPEARED!</b> 🚨\n"
                f"🔥 <b>{boss_name} Lvl ({boss_level})</b>\n"
                f"<b>stands in your path, radiating immense power!{image_embed}</b>\n"
                f"<code>-------------------------</code>"
            )
            keyboard = [[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.message:
                sent_message = await update.message.reply_text(
                    boss_message,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
            else:
                sent_message = None
            
            # Update player stats in background
            player.increment_daily_explores(datetime.now(timezone.utc))
            update_data = {
                "last_explore_time": time.time(),
                "daily_explores": player.daily_explores
            }
            
            # Handle travel
            travel = getattr(player, "travel", {})
            if travel.get("in_progress") and not FastPreCheck.is_in_battle(user_id_str):
                travel_progress = travel.get("progress", 0) + 1
                if travel_progress >= travel.get("required", 1):
                    update_data["location"] = travel.get("to", player.location)
                    update_data["travel"] = {}
                    asyncio.create_task(
                        context.bot.send_message(
                            chat_id=user_id,
                            text=f"🗺️ Arrived at <b>{travel.get('to')}</b>!",
                            parse_mode=ParseMode.HTML
                        )
                    )
                else:
                    travel["progress"] = travel_progress
                    update_data["travel"] = travel
            
            # Update player data
            await db.batch_update_player(user_id_str, update_data)
            
            # Cancel old timeout
            old_timeout_task = user_timeout_tasks.get(user_id_str)
            if old_timeout_task and not old_timeout_task.done():
                old_timeout_task.cancel()
            
            # Start timeout
            timeout_task = asyncio.create_task(
                titan_encounter_timeout(user_id, context, sent_message)
            )
            user_timeout_tasks[user_id_str] = timeout_task
            context.bot_data[f"titan_timeout_{user_id_str}"] = timeout_task
            
    except Exception as e:
        logger.error(f"Boss spawn error: {e}", exc_info=True)

# =====================================================================================
# Timeout Handling
# =====================================================================================

async def titan_encounter_timeout(user_id: int, context: ContextTypes.DEFAULT_TYPE, sent_message=None):
    """Handle timeout without blocking"""
    user_id_str = str(user_id)
    # Acquire per-user lock to avoid races with explore flow
    if user_id_str not in user_explore_locks:
        user_explore_locks[user_id_str] = asyncio.Lock()
    lock = user_explore_locks[user_id_str]
    try:
        await asyncio.sleep(TITAN_TIMEOUT_SECONDS)

        async with lock:
            battle_id_key = f"active_battle_id_{user_id_str}"
            old_battle_id = context.bot_data.get(battle_id_key)

            if old_battle_id and old_battle_id.startswith("used_"):
                return

            if FastPreCheck.is_in_battle(user_id_str):
                return

        if old_battle_id:
            context.bot_data[battle_id_key] = f"expired_{old_battle_id}_{time.time()}"
        
        db = context.bot_data.get("db")
        if not db:
            return
        
        # Cleanup titan
        await _cleanup_existing_titan(user_id_str, db)
        
        # Update message
        if sent_message:
            try:
                from game.safe_edit import safe_edit_message_text
                await safe_edit_message_text(
                    sent_message,
                    "⏰ <b>Titan Encounter Expired!</b>\n\nUse /explore to find another titan.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Timeout edit error: {e}")
        
        # Spam detection
        spam_counts = context.bot_data.setdefault("explore_spam_count", {})
        spam_counts[user_id_str] = spam_counts.get(user_id_str, 0) + 1
        
        await db.bans.update_one(
            {"user_id": user_id_str},
            {"$set": {"spam_count": spam_counts[user_id_str]}},
            upsert=True
        )
        
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Timeout error: {e}")
    finally:
        if user_id_str in user_timeout_tasks:
            del user_timeout_tasks[user_id_str]

# =====================================================================================
# Utility
# =====================================================================================

async def _reply_error(update: Update, message: str):
    """Send error reply"""
    if update.message:
        await update.message.reply_text(message)

@maintenance_protected
@ban_protected
async def open_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open keyboard"""
    if not FastPreCheck.is_chat_private(update):
        await _reply_error(update, "This command can only be used in private chats.")
        return
    
    keyboard = [["/explore", "/close"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    if update.message:
        await update.message.reply_text("Keyboard opened.", reply_markup=reply_markup)

@ban_protected
async def close_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close keyboard"""
    if update.message:
        await update.message.reply_text("Closing keyboard...", reply_markup=ReplyKeyboardRemove())