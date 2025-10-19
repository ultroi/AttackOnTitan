"""
Handles the /explore command, titan encounters, and other random events.
"""
import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Dict
from uuid import uuid4

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                    ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database.db import Database
from database.models import (Player, Titan, generate_titan_hp,
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

# In-memory caches and locks for performance
user_last_explore: Dict[str, float] = {}
user_explore_locks: Dict[str, asyncio.Lock] = {}
user_timeout_tasks: Dict[str, asyncio.Task] = {}
_battle_system_cache = {}

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

BOSS_TITAN_IMAGE_URLS = [
    "https://ibb.co/vCkN4cxY",
    
]

# =====================================================================================
# Helper Functions (defined before use in module-level code)
# =====================================================================================

def get_titan_difficulty_by_level(level: int) -> str:
    """Returns the difficulty level based on player level."""
    if level <= 50:
        return "Easy"
    elif level <= 100:
        return "Normal"
    else:
        return "Hard"

# Pre-generate a pool of titans for instant generation and variety
TITAN_POOL = {}
for lvl in range(1, 126):
    TITAN_POOL[lvl] = []
    for _ in range(30):
        difficulty = get_titan_difficulty_by_level(lvl)
        name = generate_titan_name(difficulty)
        max_hp = generate_titan_hp(lvl, difficulty)
        xp = generate_titan_xp(lvl, difficulty)
        
        titan_key = name.lower().replace(" titan", "")
        image_url = TITAN_TYPE_IMAGE_URLS.get(titan_key, random.choice(list(TITAN_TYPE_IMAGE_URLS.values())))
        
        TITAN_POOL[lvl].append({
            "name": name,
            "level": lvl,
            "max_hp": max_hp,
            "xp_reward": xp,
            "difficulty": difficulty,
            "image_url": image_url
        })

# =====================================================================================
# Core Explore Logic
# =====================================================================================

@maintenance_protected
@ban_protected
async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    
    if not update.effective_user or not update.effective_chat:
        return
    
    if update.effective_chat.type != "private":
        await _reply_error(update, "This command can only be used in private chats.")
        return
    
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Immediate validation: Check if user is already in a battle.
    if _is_in_battle(user_id_str):
        await _reply_error(update, "⚔️ You are currently in a battle! Complete it first before exploring.")
        return
    
    # Use a non-blocking lock to prevent concurrent explore commands.
    if user_id_str not in user_explore_locks:
        user_explore_locks[user_id_str] = asyncio.Lock()
    if user_explore_locks[user_id_str].locked():
        return
    
    db = context.bot_data.get("db")
    if not db:
        await _reply_error(update, "Database not available. Please try again later.")
        return
    
    # Optimized check for player existence
    player = await db.get_player(user_id_str)
    if not player or not hasattr(player, 'team') or not player.team:
        await _reply_error(update, "You need to start the bot with /start first!")
        return

    # Fetch active character for dynamic scaling
    try:
        character = await db.get_character(user_id_str, player.team[0].character_name)
        if not character or not character.stats:
            await _reply_error(update, "Your active character data is missing.")
            return
    except Exception as e:
        logger.error(f"Failed to get character for explore for user {user_id_str}: {e}")
        return
    
    # --- Event Spawning ---
    # Randomly trigger a dealer, captcha, or titan encounter.
    
    # Boss Titan Encounter (2% chance)
    if random.random() < 0.02:
        asyncio.create_task(handle_boss_titan_encounter(update, context, player))
        return

    if random.random() < 0.02:
        asyncio.create_task(handle_dealer_encounter(update, context))
        return

    if context.user_data and random.random() < 0.02 and not context.user_data.get('captcha_active', False):
        asyncio.create_task(spawn_captcha(update, context))
        return
    
    # --- Titan Encounter ---
    # Dynamically generate titan based on character stats
    titan_data = _generate_dynamic_titan(player, character)
    
    # Prepare the battle message and send it immediately.
    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id
    
    image_embed = f'<a href="{titan_data["image_url"]}">!</a>'
    reply_text = format_titan_message(
        name=titan_data["name"],
        level=titan_data["level"],
        image_embed=image_embed
    )
    
    keyboard = [[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if update.message:
            send_message_task = asyncio.create_task(
                update.message.reply_text(
                    reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
            )
        response_time = (time.time() - start_time) * 1000
        if response_time > 500:
            logger.warning(f"Explore response time: {response_time:.1f}ms")
        else:
            logger.debug(f"Explore response time: {response_time:.1f}ms")
    except Exception as e:
        logger.error(f"Failed to send titan message: {e}")
        return
        
    # Offload all further processing to a background task to keep the bot responsive.
    asyncio.create_task(_process_explore_after_reply(
        update, context, user_id, db, titan_data, send_message_task, start_time, player
    ))

async def _process_explore_after_reply(update, context, user_id, db, titan_data, send_message_task, start_time, player):
    try:
        sent_message = await send_message_task
    except Exception as e:
        logger.error(f"Failed to get sent message: {e}")
        return
    
    user_id_str = str(user_id)
    
    # Fetch player and character data.
    # Player already passed from main function
    if not player or not hasattr(player, 'team') or not player.team:
        logger.warning(f"Player {user_id_str} validation failed after reply.")
        return
    
    try:
        character_name = player.team[0].character_name
        # Character validation skipped for performance - assume valid if team exists
    except (IndexError, AttributeError) as e:
        logger.error(f"Error getting character name for player {user_id_str}: {e}")
        return
    
    # Update context with fresh player level for future commands.
    context.user_data["player_level"] = player.level
    
    # Update daily explore count.
    player.increment_daily_explores(datetime.now(timezone.utc))
    
    # Clean up any old titan data for the user.
    await _cleanup_existing_titan(user_id_str, db)
    
    # Create and store the new Titan object.
    titan = Titan(
        name=titan_data["name"],
        level=titan_data["level"],
        max_hp=titan_data["max_hp"],
        xp_reward=titan_data["xp_reward"],
        difficulty=titan_data["difficulty"],
        created_at=datetime.now(timezone.utc),
        spawn_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS),
        min_level_requirement=max(1, player.level - 2),
        abilities=[],
        drop_table={}
    )
    context.bot_data[f"last_titan_data_{user_id_str}"] = titan.dict()
    await db.store_titan(user_id_str, titan)
    
    # Create timeout task and store it in BOTH places for compatibility
    timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
    user_timeout_tasks[user_id_str] = timeout_task
    # Also store in bot_data so battle_system can cancel it
    context.bot_data[f"titan_timeout_{user_id_str}"] = timeout_task
    
    await _handle_background_tasks(update, context, user_id_str, db, player)
    # Don't log here - moved to separate async task to avoid blocking

async def _handle_background_tasks(update, context, user_id_str, db, player):
    username = update.effective_user.username or update.effective_user.first_name
    asyncio.create_task(track_explore_stats(user_id_str, username, battle_completed=False))
    
    # Combine multiple updates into a single batch_update_player call
    update_data = {"last_explore_time": time.time()}
    
    # Update daily_explores properly - it's a Dict[str, int], not a list
    if hasattr(player, "daily_explores") and isinstance(player.daily_explores, dict):
        update_data["daily_explores"] = player.daily_explores
    
    # Handle travel progress in the same update
    travel = getattr(player, "travel", {})
    if travel.get("in_progress") and not _is_in_battle(user_id_str):
        travel_progress = travel.get("progress", 0) + 1
        travel_required = travel.get("required", 1)
        
        if travel_progress >= travel_required:
            # Travel completed.
            new_location = travel.get("to", player.location)
            update_data["location"] = new_location
            update_data["travel"] = {}
            
            # Send arrival message
            arrival_message = f"🗺️ You have arrived at <b>{new_location}</b>!"
            if update.message:
                asyncio.create_task(update.message.reply_text(arrival_message, parse_mode=ParseMode.HTML))
        else:
            # Travel in progress.
            travel["progress"] = travel_progress
            update_data["travel"] = travel
    
    asyncio.create_task(db.batch_update_player(user_id_str, update_data))

# =====================================================================================
# Boss Titan Encounter
# =====================================================================================

async def handle_boss_titan_encounter(update: Update, context: ContextTypes.DEFAULT_TYPE, player: Player):
    """Handles the logic for a rare Boss Titan encounter."""
    start_time = time.time()
    user_id = player.user_id
    
    db = context.bot_data.get("db")
    if not db:
        return

    # Fetch the player's active character to scale the boss
    try:
        character = await db.get_character(user_id, player.team[0].character_name)
        if not character or not character.stats:
            await _reply_error(update, "Your character data is missing. Cannot spawn boss.")
            return
    except Exception as e:
        logger.error(f"Failed to get character for boss encounter for user {user_id}: {e}")
        return

    # --- Boss Titan Generation ---
    boss_hp = character.stats.HP * 15
    boss_level = player.level + 5
    boss_name = "The Armored Titan" # Example name
    boss_xp = generate_titan_xp(boss_level, "Hard") * 5

    titan_data = {
        "name": boss_name,
        "level": boss_level,
        "max_hp": boss_hp,
        "xp_reward": boss_xp,
        "difficulty": "Boss",
        "image_url": random.choice(BOSS_TITAN_IMAGE_URLS),
        "is_boss": True
    }

    # Prepare the battle message
    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id
    
    image_embed = f'<a href="{titan_data["image_url"]}">!</a>'
    reply_text = format_boss_titan_message(
        name=titan_data["name"],
        level=titan_data["level"],
        image_embed=image_embed
    )
    
    keyboard = [[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if update.message:
            send_message_task = asyncio.create_task(
                update.message.reply_text(
                    reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
            )
        response_time = (time.time() - start_time) * 1000
        logger.info(f"Boss Titan encounter response time: {response_time:.1f}ms")
    except Exception as e:
        logger.error(f"Failed to send boss titan message: {e}")
        return

    # Process the rest in the background
    asyncio.create_task(_process_boss_explore_after_reply(
        update, context, user_id, db, titan_data, send_message_task, player
    ))

async def _process_boss_explore_after_reply(update, context, user_id, db, titan_data, send_message_task, player):
    try:
        sent_message = await send_message_task
    except Exception as e:
        logger.error(f"Failed to get sent message for boss encounter: {e}")
        return
    
    user_id_str = str(user_id)
    
    await _cleanup_existing_titan(user_id_str, db)
    
    # Create and store the new Boss Titan object
    titan = Titan(
        name=titan_data["name"],
        level=titan_data["level"],
        max_hp=titan_data["max_hp"],
        xp_reward=titan_data["xp_reward"],
        difficulty=titan_data["difficulty"],
        created_at=datetime.now(timezone.utc),
        spawn_areas=getattr(player, 'unlocked_areas', DEFAULT_AREAS),
        min_level_requirement=max(1, player.level - 2),
        abilities=[],
        drop_table={},
        is_boss=True
    )
    context.bot_data[f"last_titan_data_{user_id_str}"] = titan.dict()
    await db.store_titan(user_id_str, titan)
    
    # Create timeout task
    timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
    user_timeout_tasks[user_id_str] = timeout_task
    context.bot_data[f"titan_timeout_{user_id_str}"] = timeout_task
    
    # Handle other background tasks like travel
    await _handle_background_tasks(update, context, user_id_str, db, player)

# =====================================================================================
# Titan Management
# =====================================================================================

def format_boss_titan_message(name: str, level: int, image_embed: str = "") -> str:
    return (
        f"<code>-------------------------</code>\n"
        f"🚨 <b>A BOSS APPEARED!</b> 🚨\n"
        f"🔥 <b>{name} Lvl ({level})</b>\n"
        f"<b>stands in your path, radiating immense power!{image_embed}</b>\n"
        f"<code>-------------------------</code>"
    )

def format_titan_message(name: str, level: int, image_embed: str = "") -> str:
    return (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{name} Lvl ({level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>"
    )

def _generate_titan_from_pool(player_level: int) -> dict:
    """Selects a random titan from the pre-generated pool based on player level."""
    titan_variations = []
    for offset in [-1, 0, 1]:
        lvl = max(1, min(125, player_level + offset))
        titan_variations.extend(TITAN_POOL[lvl])
    return random.choice(titan_variations)

async def _cleanup_existing_titan(user_id_str: str, db: Database):
    """Deletes any old, un-battled titan for a user."""
    try:
        result = await db.titans.delete_one({"user_id": user_id_str})
        if result.deleted_count > 0:
            db.invalidate_titan_cache(user_id_str)
            logger.info(f"Cleaned up existing titan for user {user_id_str}")
    except Exception as e:
        logger.error(f"Error cleaning up existing titan for user {user_id_str}: {e}")

# =====================================================================================
# Random Encounters (Dealer)
# =====================================================================================

async def handle_dealer_encounter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wrapper to call the dealer system."""
    try:
        await show_dealer(update, context)
    except Exception as e:
        logger.error(f"Error initiating dealer encounter: {e}")

# =====================================================================================
# Timeout and Spam Handling
# =====================================================================================

async def titan_encounter_timeout(user_id: int, context: ContextTypes.DEFAULT_TYPE, sent_message=None):
    """Handles the timeout when a user doesn't respond to a titan encounter."""
    user_id_str = str(user_id)
    try:
        await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
        
        # If user is in battle or has explored again, the timeout is no longer valid.
        if _is_in_battle(user_id_str) or user_last_explore.get(user_id_str, 0) > time.time() - TITAN_TIMEOUT_SECONDS:
            logger.info(f"Timeout cancelled for user {user_id_str} - already in battle or explored again")
            return
        
        db = context.bot_data.get("db")
        if not db:
            logger.error(f"Database not available for timeout handling for user {user_id_str}")
            return
        
        # Handle spam detection FIRST before cleanup
        await _handle_timeout_spam_detection(user_id, user_id_str, context, db)
        
        # Then clean up titan data
        await _handle_timeout_titan_cleanup(user_id_str, context, db, sent_message)
        
    except asyncio.CancelledError:
        logger.info(f"Timeout task cancelled for user {user_id_str}")
        pass  # This is expected if the user explores again.
    except Exception as e:
        logger.error(f"Error in titan_encounter_timeout for user {user_id_str}: {e}", exc_info=True)
    finally:
        # Clean up from both storage locations
        if user_id_str in user_timeout_tasks:
            del user_timeout_tasks[user_id_str]
        # Also remove from bot_data
        timeout_key = f"titan_timeout_{user_id_str}"
        if timeout_key in context.bot_data:
            del context.bot_data[timeout_key]

async def _handle_timeout_spam_detection(user_id, user_id_str, context, db):
    """Increments spam count and issues warnings or bans if necessary."""
    try:
        spam_counts = context.bot_data.setdefault("explore_spam_count", {})
        current_spam_count = spam_counts.get(user_id_str, 0) + 1
        spam_counts[user_id_str] = current_spam_count
        
        # Update database immediately (not as background task)
        await db.bans.update_one(
            {"user_id": user_id_str},
            {"$set": {"spam_count": current_spam_count, "last_spam_update": int(time.time())}},
            upsert=True
        )
        
        logger.info(f"Spam count for user {user_id_str}: {current_spam_count}")
        
        if current_spam_count == 10:
            # Send warning immediately
            await _send_spam_warning(user_id, context, "🚨 <b>Warning:</b> You have let 10 titan encounters expire! Continuing this may result in a ban.")
        elif current_spam_count >= 15:
            # Apply ban immediately
            await _handle_spam_ban(user_id, user_id_str, context, db)
    
    except Exception as e:
        logger.error(f"Error in spam detection for user {user_id_str}: {e}")

async def _handle_timeout_titan_cleanup(user_id_str, context, db, sent_message):
    try:
        # Clean up titan data
        if await db.get_titan(user_id_str):
            await db.delete_titan(user_id_str)
            logger.info(f"Deleted expired titan for user {user_id_str}")
        
        # Clean up battle ID to prevent stale battle buttons
        battle_id_key = f"active_battle_id_{user_id_str}"
        if battle_id_key in context.bot_data:
            del context.bot_data[battle_id_key]
            logger.info(f"Cleaned up expired battle ID for user {user_id_str}")
        
        # Update message if available - IMMEDIATELY with no delays
        if sent_message:
            try:
                from game.safe_edit import safe_edit_message_text
                timeout_message = (
                    "⏰ <b>Titan Encounter Expired!</b>\n\n"
                    "You didn't engage the titan in time.\n"
                    "Use /explore to find another titan."
                )
                await safe_edit_message_text(
                    sent_message,
                    timeout_message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"✅ Updated timeout message for user {user_id_str}")
            except Exception as edit_error:
                logger.error(f"Failed to update timeout message for user {user_id_str}: {edit_error}")
    except Exception as e:
        logger.error(f"Error in titan cleanup for user {user_id_str}: {e}")

async def _send_spam_warning(user_id: int, context: ContextTypes.DEFAULT_TYPE, message: str):
    """Sends a spam warning message to the user."""
    try:
        await context.bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.HTML)
        logger.info(f"✅ Sent spam warning to user {user_id}")
    except Exception as e:
        logger.error(f"❌ Failed to send spam warning to user {user_id}: {e}")

async def _handle_spam_ban(user_id, user_id_str, context, db):
    """Bans a user for spamming the explore command."""
    try:
        expiry = int(time.time()) + 24 * 3600  # 24 hours
        ban_data = {
            "user_id": user_id_str,
            "expiry": expiry,
            "reason": "Spamming explore - letting titans timeout",
            "banned_by": context.bot.id,
            "banned_at": int(time.time())
        }
        
        # Update ban in database immediately
        await db.bans.update_one({"user_id": user_id_str}, {"$set": ban_data}, upsert=True)
        
        # Reset spam count
        context.bot_data.setdefault("explore_spam_count", {})[user_id_str] = 0
        
        # Send ban notification immediately
        ban_message = (
            "🚫 <b>You have been banned for 24 hours!</b>\n\n"
            "<b>Reason:</b> Spamming /explore command without engaging in battles.\n"
        )
        await context.bot.send_message(chat_id=user_id, text=ban_message, parse_mode=ParseMode.HTML)
        logger.warning(f"⚠️ BANNED user {user_id_str} for explore spam - 24 hour ban applied")
    except Exception as e:
        logger.error(f"❌ Spam ban error for user {user_id}: {e}", exc_info=True)

# =====================================================================================
# Utility and Helper Functions
# =====================================================================================

def _is_in_battle(user_id_str: str) -> bool:
    """Checks if a user is currently in any type of battle (Titan or PVP)."""
    global _battle_system_cache
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

    return user_id_str in _battle_system_cache['active_battles'] or user_id_str in _battle_system_cache['active_pvp_battles']

async def _reply_error(update: Update, message: str):
    """Helper to send a reply message if the update context allows."""
    if update.message:
        await update.message.reply_text(message)

# =====================================================================================
# Admin and Keyboard Commands
# =====================================================================================

async def check_spam_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to check the spam count of a user."""
    if not update.effective_user or not update.message: return
    
    try:
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user_id = update.message.reply_to_message.from_user.id
            target_user_name = update.message.reply_to_message.from_user.first_name
        elif context.args and context.args[0].isdigit():
            target_user_id = int(context.args[0])
            target_user_name = f"User {target_user_id}"
        else:
            await update.message.reply_text("Please reply to a user or provide a user ID.")
            return
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid command usage.")
        return

    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Database not available.")
        return
    
    try:
        spam_doc = await db.bans.find_one({"user_id": str(target_user_id)})
        spam_count = spam_doc.get("spam_count", 0) if spam_doc else 0
        
        await update.message.reply_text(
            f"📊 <b>Spam Count for {target_user_name}</b>: {spam_count}\n"
            f"<b>User ID:</b> <code>{target_user_id}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to check spam count for {target_user_id}: {e}")
        await update.message.reply_text("Failed to check spam count.")

async def reset_user_spam_count(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Resets a user's spam count in memory and the database."""
    user_id_str = str(user_id)
    db = context.bot_data.get("db")
    if not db: return False
        
    try:
        context.bot_data.setdefault("explore_spam_count", {})[user_id_str] = 0
        await db.bans.update_one(
            {"user_id": user_id_str},
            {"$set": {"spam_count": 0}},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to reset spam count for {user_id}: {e}")
        return False

@maintenance_protected
@ban_protected
async def open_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens a persistent keyboard with /explore and /close buttons."""
    if not update.effective_chat or update.effective_chat.type != "private":
        await _reply_error(update, "This command can only be used in private chats.")
        return
    
    keyboard = [["/explore", "/close"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text("Keyboard opened.", reply_markup=reply_markup)

@ban_protected
async def close_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Closes the persistent keyboard."""
    if update.message:
        await update.message.reply_text("Closing keyboard...", reply_markup=ReplyKeyboardRemove())