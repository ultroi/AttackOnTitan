import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from uuid import uuid4

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

# Performance: In-memory caches
user_last_explore: Dict[str, float] = {}
user_explore_locks: Dict[str, asyncio.Lock] = {}
user_timeout_tasks: Dict[str, asyncio.Task] = {}
_battle_system_cache = {}
user_cache: Dict[str, Tuple[Player, Character]] = {}  
cache_expiry: Dict[str, float] = {}  
CACHE_TTL = 300  

TITAN_TYPE_IMAGE_URLS = {
    "goofy grinning": "https://i.ibb.co/dJ6J58s0/image.jpg",
    "beast": "https://i.ibb.co/B2C79CM4/image.jpg",
    "potbellied": "https://i.ibb.co/XkMw0Xt5/image.jpg",
    "bearded": "https://i.ibb.co/7J8S4s6v/image.jpg",
   # "colossal": "https://i.ibb.co/5hCrTgjN/image.jpg",
   # "war hammer": "https://i.ibb.co/SSts9zx/image.jpg",
    "gaping mouth": "https://i.ibb.co/9mMK2FG1/image.jpg",
   # "founding": "https://i.ibb.co/svn2w3C0/image.jpg",
    "small jogger": "https://i.ibb.co/Fk8NspGP/image.jpg",
    "leaper": "https://i.ibb.co/k2XqYdX6/image.jpg",
   # "attack": "https://i.ibb.co/JW8TyhmR/image.jpg",
    "cart": "https://i.ibb.co/nNRhLMqH/image.jpg",
    "female": "https://i.ibb.co/vNQF4Cb/image.jpg",
    "bloated": "https://i.ibb.co/fYrcqngz/image.jpg",
    "staggering creepers": "https://i.ibb.co/mFchdbj9/image.jpg",
   # "armored": "https://i.ibb.co/212Jh2XG/image.jpg",
    "wailing": "https://i.ibb.co/1JJQg9Db/image.jpg",
   # "jaw": "https://i.ibb.co/ychR6L72/image.jpg"
    
}

BOSS_TITAN_IMAGE_URLS = ["https://i.ibb.co/cz6bJ0J/image.jpg"]

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
# PERFORMANCE: Caching Layer
# =====================================================================================

async def get_cached_player_data(user_id_str: str, db: Database) -> Optional[Tuple[Player, Character]]:
    """ULTRA-FAST cached player + character data (< 5ms)"""
    current_time = time.time()
    
    # FASTEST: Check cache first (< 0.1ms)
    if user_id_str in cache_expiry:
        if cache_expiry[user_id_str] > current_time:
            return user_cache[user_id_str]  # Cache hit - instant return
    
    # Cache miss - fetch with minimal overhead
    try:
        # Single await for player (no intermediate variables)
        player = await db.get_player(user_id_str)
        if not player or not hasattr(player, 'team') or not player.team:
            return None
        
        # Get character inline (no extra variable)
        character = await db.get_character(user_id_str, player.team[0].character_name)
        if not character or not character.stats:
            return None
        
        # Update cache inline (fastest possible)
        result = (player, character)
        user_cache[user_id_str] = result
        cache_expiry[user_id_str] = current_time + CACHE_TTL
        
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
    if rand_val < 0.02:  
        return "boss_titan"
    elif rand_val < 0.05:  
        return "dealer"
    elif rand_val < 0.05:  
        return "captcha"
    return None  # 88% chance - Normal titan

# =====================================================================================
# CORE: Ultra-Fast Explore
# =====================================================================================

@maintenance_protected
@ban_protected
async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    
    # ========== PHASE 1: Lightning-Fast Pre-Checks (< 5ms) ==========
    if not update.effective_user or not update.effective_chat:
        return
    
    # Check 1: Private chat (in-memory only)
    if not FastPreCheck.is_chat_private(update):
        await _reply_error(update, "This command can only be used in private chats.")
        return
    
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Check 2: Already locked (in-memory only)
    if FastPreCheck.is_locked(user_id_str):
        return
    
    # Check 3: In battle (in-memory only)
    if FastPreCheck.is_in_battle(user_id_str):
        await _reply_error(update, "⚔️ You are currently in a battle! Complete it first.")
        return
    
    # Get DB reference
    db = context.bot_data.get("db")
    if not db:
        await _reply_error(update, "Database not available.")
        return
    
    # ========== PHASE 2: ULTRA-FAST DATA FETCH (< 10ms with cache) ==========
    # Pre-generate random values FIRST (< 0.5ms) - don't wait for anything
    event_type = check_random_events()
    
    # OPTIMIZED: Check cache first (< 0.1ms)
    current_time = time.time()
    cached_data = None
    
    if user_id_str in user_cache and user_id_str in cache_expiry:
        if cache_expiry[user_id_str] > current_time:
            cached_data = user_cache[user_id_str]
    
    # Parallel: Fetch data if needed
    if cached_data:
        player, character = cached_data
    else:
        # Fast fetch with reasonable timeout protection (max 2 seconds)
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
    
    # ========== HANDLE RANDOM EVENTS (These replace normal titan encounter) ==========
    if event_type == "dealer":
        # Dealer event - NO titan, only dealer
        try:
            await show_dealer(update, context)
            # Track stats with timeout (don't block on failure)
            try:
                await asyncio.wait_for(
                    track_explore_stats(user_id_str, update.effective_user.username or player.username, False),
                    timeout=0.5
                )
            except (asyncio.TimeoutError, Exception):
                pass  # Don't block main flow
        except Exception as e:
            logger.error(f"Dealer event error: {e}", exc_info=True)
        return
    
    elif event_type == "captcha":
        # Captcha event - NO titan, only captcha
        try:
            await spawn_captcha(update, context)
            # Track stats with timeout (don't block on failure)
            try:
                await asyncio.wait_for(
                    track_explore_stats(user_id_str, update.effective_user.username or player.username, False),
                    timeout=0.5
                )
            except (asyncio.TimeoutError, Exception):
                pass  # Don't block main flow
        except Exception as e:
            logger.error(f"Captcha event error: {e}", exc_info=True)
        return
    
    elif event_type == "boss_titan":
        # Boss Titan event - NO normal titan, directly show boss
        try:
            await spawn_boss_titan_directly(update, context, user_id_str, player, db)
            # Track stats
            asyncio.create_task(track_explore_stats(user_id_str, update.effective_user.username or player.username, False))
        except Exception as e:
            logger.error(f"Boss Titan event error: {e}", exc_info=True)
        return
    

    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    
    # Fast inline titan generation (no function call overhead)
    difficulty = get_titan_difficulty_by_level(player.level)
    titan_hp = generate_titan_hp(level=player.level, difficulty=difficulty, character_stats=character.stats if isinstance(character.stats, CharacterStats) else None)
    titan_name = generate_titan_name(difficulty)
    titan_xp = generate_titan_xp(player.level, difficulty)
    titan_key = titan_name.lower().replace(" titan", "")
    titan_image = TITAN_TYPE_IMAGE_URLS.get(titan_key, random.choice(list(TITAN_TYPE_IMAGE_URLS.values())))
    
    # Create titan object for immediate storage
    titan = Titan(
        name=titan_name,
        level=player.level,
        max_hp=titan_hp,
        xp_reward=titan_xp,
        difficulty=difficulty,
        created_at=datetime.now(timezone.utc),
        spawn_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS),
        min_level_requirement=max(1, player.level - 2),
        abilities=[],
        drop_table={}
    )
    
    # Build message inline (no function calls)
    reply_text = (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{titan_name} Lvl ({player.level})</b>\n"
        f"<b>has blocked your way<a href=\"{titan_image}\">!</a></b>\n"
        f"<code>-------------------------</code>"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]])
    
    # ========== PHASE 4: INSTANT SEND (< 30ms) ==========
    sent_message = None
    try:
        if not update.message:
            return
        
        
        context.bot_data[f"active_battle_id_{user_id}"] = battle_id
        context.bot_data[f"last_titan_data_{user_id_str}"] = titan.dict()
        
        # Send message with minimal overhead
        if update.message:
            sent_message = await update.message.reply_text(
                reply_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        else:
            sent_message = None

    except Exception as e:
        logger.error(f"Send error: {e}")
        return
    
    titan_data = {
        "name": titan_name,
        "level": player.level,
        "max_hp": titan_hp,
        "xp_reward": titan_xp,
        "difficulty": difficulty,
        "image_url": titan_image,
        "is_boss": False
    }
    
    # Execute deferred operations with timeout to prevent hanging
    try:
        await asyncio.wait_for(
            _deferred_explore_operations(
                context, user_id_str, user_id, db, titan, sent_message, 
                player, update.effective_user.username, event_type
            ),
            timeout=5.0  # 5 second timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"Explore deferred operations timed out for user {user_id_str}")
    except Exception as e:
        logger.error(f"Error in deferred explore operations: {e}")


async def _deferred_explore_operations(context, user_id_str, user_id, db, titan, 
                                      sent_message, player, username, event_type):
    """
    ULTRA OPTIMIZED: All heavy operations run in parallel - NO IMPACT ON RESPONSE TIME
    """
    try:
        # Acquire lock AFTER message sent
        async with user_explore_locks[user_id_str]:
            # OPTIMIZED: Parallel execution of independent operations
            
            # 1. Cleanup old titan (non-blocking)
            cleanup_task = asyncio.create_task(_cleanup_existing_titan(user_id_str, db))
            
            # 2. Prepare update data (fast)
            player.increment_daily_explores(datetime.now(timezone.utc))
            update_data = {
                "last_explore_time": time.time(),
                "daily_explores": player.daily_explores
            }
            
            # 4. Handle travel (fast)
            travel = getattr(player, "travel", {})
            if travel.get("in_progress") and not FastPreCheck.is_in_battle(user_id_str):
                travel_progress = travel.get("progress", 0) + 1
                if travel_progress >= travel.get("required", 1):
                    update_data["location"] = travel.get("to", player.location)
                    update_data["travel"] = {}
                    try:
                        await asyncio.wait_for(
                            context.bot.send_message(
                                chat_id=user_id,
                                text=f"🗺️ Arrived at <b>{travel.get('to')}</b>!",
                                parse_mode=ParseMode.HTML
                            ),
                            timeout=2.0
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Travel message send timed out for user {user_id_str}")
                    except Exception as e:
                        logger.error(f"Failed to send travel message: {e}")
                else:
                    travel["progress"] = travel_progress
                    update_data["travel"] = travel
            
            # OPTIMIZED: Run all DB operations in parallel
            await asyncio.gather(
                cleanup_task,
                db.store_titan(user_id_str, titan),
                db.batch_update_player(user_id_str, update_data),
                return_exceptions=True
            )
            
            # Store titan data in memory
            context.bot_data[f"last_titan_data_{user_id_str}"] = titan.dict()
            
            # 6. Cancel any existing timeout task before starting a new one
            old_timeout_task = user_timeout_tasks.get(user_id_str)
            if old_timeout_task and not old_timeout_task.done():
                old_timeout_task.cancel()
            
            # Start new timeout task (non-blocking)
            timeout_task = asyncio.create_task(
                titan_encounter_timeout(user_id, context, sent_message)
            )
            user_timeout_tasks[user_id_str] = timeout_task
            context.bot_data[f"titan_timeout_{user_id_str}"] = timeout_task
            
            # 7. Track stats with timeout (don't block on failure)
            try:
                await asyncio.wait_for(
                    track_explore_stats(user_id_str, username or player.username, False),
                    timeout=0.5
                )
            except (asyncio.TimeoutError, Exception):
                pass  # Don't block main flow
                
    except Exception as e:
        logger.error(f"Error in deferred operations: {e}", exc_info=True)

# =====================================================================================
# Helper Functions
# =====================================================================================

def get_titan_difficulty_by_level(level: int) -> str:
    """Returns difficulty level based on player level"""
    if level <= 50:
        return "Easy"
    elif level <= 100:
        return "Normal"
    else:
        return "Hard"

def _generate_dynamic_titan(player: Player, character) -> dict:
    """Generate titan in < 2ms"""
    difficulty = get_titan_difficulty_by_level(player.level)
    hp = generate_titan_hp(level=player.level, difficulty=difficulty, character_stats=character.stats)
    name = generate_titan_name(difficulty)
    xp = generate_titan_xp(player.level, difficulty)
    
    titan_key = name.lower().replace(" titan", "")
    image_url = TITAN_TYPE_IMAGE_URLS.get(titan_key, random.choice(list(TITAN_TYPE_IMAGE_URLS.values())))

    return {
        "name": name,
        "level": player.level,
        "max_hp": hp,
        "xp_reward": xp,
        "difficulty": difficulty,
        "image_url": image_url,
        "is_boss": False
    }

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
    """Clean up old titan - with race condition protection"""
    try:
        # CRITICAL: Double-check if battle is starting before cleanup
        if FastPreCheck.is_in_battle(user_id_str):
            # Titan cleanup skip logging removed for cleaner logs
            return
        
        result = await db.titans.delete_one({"user_id": user_id_str}) if db.titans else None
        deleted_count = getattr(result, 'deleted_count', 0) if result else 0
        if deleted_count > 0:
            db.invalidate_titan_cache(user_id_str)
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# =====================================================================================
# Random Events (Background Operations)
# =====================================================================================

async def spawn_boss_titan_directly(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                    user_id_str: str, player: Player, db: Database):
    """Spawn boss titan directly without showing normal titan first"""
    try:
        user_id = int(user_id_str)
        
        # Get or create lock
        if user_id_str not in user_explore_locks:
            user_explore_locks[user_id_str] = asyncio.Lock()
        
        async with user_explore_locks[user_id_str]:
            # Cleanup any existing titan
            await _cleanup_existing_titan(user_id_str, db)
            
            # Generate BOSS titan
            difficulty = get_titan_difficulty_by_level(player.level)
            boss_hp = generate_titan_hp(level=player.level, difficulty="Hard", character_stats=None) * 3  # 3x HP for boss
            boss_name = "Armored Titan"
            boss_xp = generate_titan_xp(player.level, "Hard") * 5  # 5x XP for boss
            boss_image_url = random.choice(BOSS_TITAN_IMAGE_URLS)
            
            # Create boss titan
            boss_titan = Titan(
                name=boss_name,
                level=player.level,
                max_hp=boss_hp,
                xp_reward=boss_xp,
                difficulty="Hard",
                created_at=datetime.now(timezone.utc),
                spawn_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS),
                min_level_requirement=max(1, player.level - 2),
                abilities=[],
                drop_table={},
                is_boss=True  # Mark as boss
            )
            
            # Store boss titan
            await db.store_titan(user_id_str, boss_titan)
            context.bot_data[f"last_titan_data_{user_id_str}"] = boss_titan.dict()
            
            # Create battle button
            battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
            context.bot_data[f"active_battle_id_{user_id}"] = battle_id
            
            # Send boss message with battle button
            image_embed = f'<a href="{boss_image_url}">!</a>'
            boss_message = format_boss_titan_message(boss_name, player.level, image_embed)
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
            
            # Handle travel in background
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
            
            # Cancel any existing timeout task before starting a new one
            old_timeout_task = user_timeout_tasks.get(user_id_str)
            if old_timeout_task and not old_timeout_task.done():
                old_timeout_task.cancel()
            
            # Start timeout for boss
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
    try:
        await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
        
        
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
