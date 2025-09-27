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

logger = logging.getLogger(__name__)

async def _reply_error(update: Update, message: str):
    if update.message:
        await update.message.reply_text(message)

def get_titan_difficulty_by_level(level: int) -> str:
    if level <= 50:
        return "Easy"
    elif level <= 100:
        return "Normal"
    else:
        return "Hard"

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

# Pre-generate larger titan pool for instant generation and better variety
TITAN_POOL = {}
for lvl in range(1, 126):
    TITAN_POOL[lvl] = []
    for _ in range(30):  # Increased to 30 variations per level for better randomness
        difficulty = get_titan_difficulty_by_level(lvl)
        name = generate_titan_name(difficulty)
        max_hp = generate_titan_hp(lvl, difficulty)
        xp = generate_titan_xp(lvl, difficulty)
        
        # Pre-calculate titan keys for image matching to avoid string operations during request
        titan_key = name.lower().replace(" titan", "")
        image_url = TITAN_TYPE_IMAGE_URLS.get(titan_key, random.choice(list(TITAN_TYPE_IMAGE_URLS.values())))
        
        TITAN_POOL[lvl].append({
            "name": name,
            "level": lvl,
            "max_hp": max_hp,
            "xp_reward": xp,
            "difficulty": difficulty,
            "image_url": image_url  # Pre-cache the image URL
        })

# Rate limiting and locking for explore command
user_last_explore: Dict[str, float] = {}
user_explore_locks: Dict[str, asyncio.Lock] = {}
user_timeout_tasks: Dict[str, asyncio.Task] = {}  # Track active timeout tasks
TITAN_TIMEOUT_SECONDS = 60

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
    return (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{name} Lvl ({level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>"
    )

def _generate_cached_titan(player_level: int, difficulty: str, user_id: int) -> dict:
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


async def cleanup_user_timeout_tasks():
    to_remove = []
    for user_id_str, task in user_timeout_tasks.items():
        if task.done():
            to_remove.append(user_id_str)
    
    for user_id_str in to_remove:
        del user_timeout_tasks[user_id_str]
    
    if to_remove:
        logger.info(f"Cleaned up {len(to_remove)} completed timeout tasks")


_battle_system_cache = {}

# optimized helper function to check if a user is in battle
def _is_in_battle(user_id_str: str) -> bool:
    global _battle_system_cache
    
    # Check regular battles
    if 'active_battles' in _battle_system_cache:
        if user_id_str in _battle_system_cache['active_battles']:
            return True
    else:
        try:
            from game.battle_system import active_battles
            _battle_system_cache['active_battles'] = active_battles
            if user_id_str in active_battles:
                return True
        except ImportError:
            _battle_system_cache['active_battles'] = {}
    
    if 'active_pvp_battles' in _battle_system_cache:
        if user_id_str in _battle_system_cache['active_pvp_battles']:
            return True
    else:
        try:
            from game.pvp_system import active_pvp_battles
            _battle_system_cache['active_pvp_battles'] = active_pvp_battles
            if user_id_str in active_pvp_battles:
                return True
        except ImportError:
            _battle_system_cache['active_pvp_battles'] = {}
    
    return False
    
# async def _handle_verification(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, now: float, db, player=None):
#     """Handle hCaptcha verification requirement - OPTIMIZED to reuse player data"""
#     user_id_str = str(user_id)

#     # Use provided player data or fetch fresh if not provided
#     if player is None:
#         try:
#             player = await db.get_player(user_id_str)
#         except Exception as e:
#             logger.error(f"Failed to fetch fresh player data for verification: {e}")
#             return True  # Block exploration on error

#     if not player:
#         logger.warning(f"Player {user_id_str} not found during verification check")
#         return True  # Block exploration if player not found

#     # Check if player is already verified - use False as default for safety
#     player_verified = getattr(player, "hcaptcha_verified", False)
#     last_verified_time = getattr(player, "last_verified", 0)

#     logger.info(f"Verification check for user {user_id_str}: verified={player_verified}, last_verified={last_verified_time}")

#     # If already verified, clear any pending prompts and allow exploration
#     if player_verified:
#         if context.user_data:
#             context.user_data["hcaptcha_prompted"] = False
#             context.user_data["last_verification_check"] = now
#         logger.info(f"User {user_id_str} is already verified, allowing exploration")
#         return False

#     # Check for recent verification (within 10 minutes) - this handles web verification
#     if last_verified_time and now - last_verified_time < 600:
#         logger.info(f"User {user_id_str} was recently verified at {last_verified_time}, updating status")
#         # Update verification status and clear prompts
#         try:
#             await db.update_player(user_id_str, {
#                 "hcaptcha_verified": True,
#                 "last_verified": now  # Update timestamp to prevent repeated checks
#             })
#             if context.user_data:
#                 context.user_data["hcaptcha_prompted"] = False
#                 context.user_data["last_verification_check"] = now
#             if update.message:
#                 await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
#             return False
#         except Exception as e:
#             logger.error(f"Failed to update verification status for user {user_id_str}: {e}")
#             return True  # Block on error

#     # Check if verification is already in progress
#     if context.user_data and context.user_data.get("hcaptcha_prompted", False):
#         # Check if database has been updated since the last check
#         last_check = context.user_data.get("last_verification_check", 0)
#         if last_verified_time > last_check:
#             # Database was updated, user was verified
#             logger.info(f"User {user_id_str} verification detected via database update")
#             try:
#                 await db.update_player(user_id_str, {
#                     "hcaptcha_verified": True,
#                     "last_verified": now
#                 })
#                 context.user_data["hcaptcha_prompted"] = False
#                 context.user_data["last_verification_check"] = now
#                 if update.message:
#                     await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
#                 return False
#             except Exception as e:
#                 logger.error(f"Failed to update verification status after detection: {e}")
#                 return True
#         else:
#             # Still waiting for verification, update timestamp
#             context.user_data["last_verification_check"] = now
#             logger.info(f"User {user_id_str} still waiting for verification")
#             if update.message:
#                 await update.message.reply_text(
#                     "🔄 Please complete the hCaptcha verification to continue exploring.\n"
#                     "If you've already completed verification, please wait a moment and try again.",
#                     parse_mode=ParseMode.HTML
#                 )
#             return True

#     # If this is the first time prompting for verification
#     logger.info(f"Prompting user {user_id_str} for hCaptcha verification")
#     if context.user_data:
#         context.user_data["hcaptcha_prompted"] = True
#         context.user_data["last_verification_check"] = now

#     timestamp = int(now)
#     verification_url = f"https://attackontitangamebot.onrender.com/hcaptcha?user_id={user_id}&ts={timestamp}"

#     try:
#         if update.message:
#             await update.message.reply_text(
#                 "🔒 <b>Verification Required</b>\n\n"
#                 + "Complete hCaptcha to continue exploring.\n"
#                 + "After completing verification, use /explore again to continue.\n\n",
#                 reply_markup=InlineKeyboardMarkup([
#                     [InlineKeyboardButton("✅ Verify Now", url=verification_url)]
#                 ]),
#                 parse_mode=ParseMode.HTML,
#             )

#         # Update player record with verification start time
#         await db.update_player(user_id_str, {
#             "hcaptcha_start_time": timestamp,
#             "hcaptcha_verified": False,  # Explicitly set to false
#             "explore_start_time": None   # Reset explore timer when verification is required
#         })


#     except Exception as e:
#         logger.error(f"Error sending verification message to user {user_id_str}: {e}")
#         if context.user_data:
#             context.user_data["hcaptcha_prompted"] = False

#     return True


@maintenance_protected
@ban_protected
async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
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
    
    # Immediate battle check (fastest possible)
    if _is_in_battle(user_id_str):
        battle_type = "battle"
        try:
            from game.battle_system import active_battles
            if user_id_str in active_battles:
                battle_type = "titan battle"
        except:
            pass
        
        try:
            from game.pvp_system import active_pvp_battles
            if user_id_str in active_pvp_battles:
                battle_type = "PVP battle"
        except:
            pass
        
        await _reply_error(update, f"⚔️ You are currently in a {battle_type}! Complete it first before exploring.")
        return
    
    # Fast lock check (non-blocking)
    if user_id_str not in user_explore_locks:
        user_explore_locks[user_id_str] = asyncio.Lock()
    
    if user_explore_locks[user_id_str].locked():
        return
    
    # Get player level from context if available (use default 1 if not)
    actual_player_level = 1
    if context.user_data and "player_level" in context.user_data:
        actual_player_level = context.user_data.get("player_level", 1)
    
    # IMPORTANT: Check if user has started the game BEFORE any encounters
    db = context.bot_data.get("db")
    if not db:
        await _reply_error(update, "Database not available. Please try again later.")
        return
    
    try:
        player_check = await db.get_player(user_id_str)
        if not player_check:
            await _reply_error(update, "You need to start the game first! Use /start to begin your adventure.")
            return
    except Exception as e:
        logger.error(f"Error checking player existence for {user_id_str}: {e}")
        await _reply_error(update, "Unable to verify player data. Please try again.")
        return
    
    # Check for dealer encounter (2% chance) - synchronous to prevent showing both
    dealer_appeared = False
    try:
        from game.dealer_command import explore_dealer
        dealer_appeared = await explore_dealer(update, context)
    except Exception as e:
        logger.error(f"Error scheduling dealer encounter check: {e}")
    
    if dealer_appeared:
        return  # Only dealer appears, no titan
    
    # Check for captcha (2% chance)
    should_spawn_captcha = (
        random.random() < 0.02 and 
        context.user_data and 
        not context.user_data.get('captcha_active', False)
    )
    
    if should_spawn_captcha:
        # Clean up any existing dealer before spawning captcha
        active_dealers = context.bot_data.get("active_dealer_encounters", {})
        if user_id_str in active_dealers:
            del active_dealers[user_id_str]
            logger.info(f"Cleared existing dealer encounter for user {user_id_str} due to captcha")
        
        # Spawn captcha instead of titan
        asyncio.create_task(spawn_captcha(update, context))
        return 
    
    # Generate titan instantly with correct level using pre-generated pool
    titan_variations = []
    for offset in [-1, 0, 1]:
        lvl = max(1, min(125, actual_player_level + offset))
        titan_variations.extend(TITAN_POOL[lvl])
    
    titan_data = random.choice(titan_variations)
    titan_name = titan_data["name"]
    titan_level = titan_data["level"]
    titan_max_hp = titan_data["max_hp"]
    titan_xp = titan_data["xp_reward"]
    difficulty = titan_data["difficulty"]
    
    # Fast image lookup
    titan_key = titan_name.lower().replace(" titan", "")
    titan_image_url = TITAN_TYPE_IMAGE_URLS.get(
        titan_key, 
        random.choice(list(TITAN_TYPE_IMAGE_URLS.values()))
    )

    # Generate battle ID
    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id

    # Clean up any existing dealer encounter before showing titan
    active_dealers = context.bot_data.get("active_dealer_encounters", {})
    if user_id_str in active_dealers:
        del active_dealers[user_id_str]
        logger.info(f"Cleared existing dealer encounter for user {user_id_str} due to titan encounter")

    # Create message components
    keyboard = [[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Pre-format message with image - optimize string operations
    image_embed = f'<a href="{titan_image_url}">!</a>'
    reply_text = format_titan_message(
        name=titan_name,
        level=titan_level,
        image_embed=image_embed
    )
        
    # SEND RESPONSE IMMEDIATELY before any DB operations - absolute priority
    if update.message:
        try:
            # Use the fastest possible reply approach
            send_message_task = asyncio.create_task(
                update.message.reply_text(
                    reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
            )
            # Record response time immediately
            response_time = (time.time() - start_time) * 1000
            if response_time > 100:
                logger.warning(f"Explore response above target: {response_time:.1f}ms > 100ms")
            else:
                logger.info(f"Explore response time: {response_time:.1f}ms (target: <100ms)")
        except Exception as e:
            logger.error(f"Failed to send titan message: {e}")
            return
    else:
        return
        
    # Get database reference for quick validation
    # Note: DB reference already obtained above
    
    # Quick validation: Player existence already checked above, so proceed
    
    asyncio.create_task(_validate_and_process_optimized(
        update, context, user_id, user_id_str, username, db,
        titan_name, titan_level, titan_max_hp, titan_xp, difficulty, titan_image_url,
        send_message_task, start_time, actual_player_level, reply_text
    ))
    
    # Periodic cleanup in background (1% chance)
    if len(user_timeout_tasks) > 0 and random.random() < 0.01:
        asyncio.create_task(cleanup_user_timeout_tasks())



async def _validate_and_process_optimized(update, context, user_id, user_id_str, username, db,
                               titan_name, titan_level, titan_max_hp, titan_xp, difficulty, titan_image_url,
                               send_message_task, start_time, actual_player_level, reply_text):
    
    # Before anything else, store sent time for benchmarking
    send_time = time.time() - start_time
    logger.info(f"Initial response time (before validation): {send_time*1000:.1f}ms")
    
    # First thing - check spam count in memory (faster than DB check)
    spam_detected = False
    if "explore_spam_count" in context.bot_data:
        current_spam_count = context.bot_data["explore_spam_count"].get(user_id_str, 0)
        SPAM_THRESHOLD = 15
        if current_spam_count >= SPAM_THRESHOLD:
            logger.warning(f"Player {user_id_str} has spam count {current_spam_count} - blocking explore in background")
            spam_detected = True
    
    # Start multiple DB operations in parallel for maximum efficiency
    try:
        # Launch all DB operations in parallel
        player_future = db.get_player(user_id_str)
        
        # Try to get spam count from DB in parallel
        spam_count_future = db.bans.find_one({"user_id": user_id_str, "spam_count": {"$exists": True}})
        
        # Clean up existing titan in parallel
        cleanup_titan_future = asyncio.create_task(_cleanup_existing_titan(user_id_str, db))
        
        # Wait for message to be sent
        try:
            sent_message = await send_message_task
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return
        
        # Wait for player data
        try:
            player = await player_future
        except Exception as db_error:
            logger.error(f"Database error fetching player {user_id_str}: {db_error}")
            return
        
        if not player:
            logger.warning(f"Player {user_id_str} not found in database")
            return
        
        if not hasattr(player, 'team') or not player.team:
            logger.warning(f"Player {user_id_str} has no team")
            return
        
        # Get character name and launch character query
        try:
            character_name = player.team[0].character_name if hasattr(player.team[0], 'character_name') else player.team[0]
            character_future = db.get_character(user_id_str, character_name)
        except (IndexError, AttributeError) as e:
            logger.error(f"Error getting character name for player {user_id_str}: {e}")
            return
        
        # Update actual player level in context for faster future lookups
        try:
            spam_count_doc = await spam_count_future
            if spam_count_doc:
                # Initialize explore_spam_count dict if it doesn't exist
                if "explore_spam_count" not in context.bot_data:
                    context.bot_data["explore_spam_count"] = {}
                context.bot_data["explore_spam_count"][user_id_str] = spam_count_doc.get("spam_count", 0)
        except Exception as e:
            logger.error(f"Error fetching spam count: {e}")
            
        # Get location and unlocked areas while waiting for character
        location = getattr(player, 'location', None)
        unlocked_areas = getattr(player, 'unlocked_areas', DEFAULT_AREAS)
        
        # Get character data
        try:
            character = await character_future
        except Exception as db_error:
            logger.error(f"Database error fetching character for {user_id_str}: {db_error}")
            return
        
        if not character:
            logger.warning(f"Character {character_name} not found for player {user_id_str}")
            return
        
        # Update daily explores
        player.increment_daily_explores(datetime.now(timezone.utc))
        
        # Wait for titan cleanup to finish
        await cleanup_titan_future
        
        # Create and store titan object
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
            min_level_requirement=max(1, actual_player_level - 2)
        )
        
        # Store titan object - fire and forget for better performance
        try:
            asyncio.create_task(db.store_titan(user_id_str, titan))

            # Capture response time before proceeding
            response_time = (time.time() - start_time) * 1000
            logger.info(f"Full response time: {response_time:.1f}ms")

            # Cache titan data immediately (don't wait for DB)
            context.bot_data[f"last_titan_data_{user_id_str}"] = titan.dict()
            
            # Process Emergency Heal (Mission 7) if applicable
            mission_task = None
            if player and hasattr(character, 'current_hp'):
                mission_7_completed = False
                player_missions = getattr(player, "missions", [])
                
                for mission in player_missions:
                    if (mission.get("mission_id") == 7 and mission.get("status") == "completed"):
                        mission_7_completed = True
                        break
                
                if mission_7_completed and character.current_hp < 100:
                    heal_amount = 40
                    old_hp = character.current_hp
                    character.current_hp = min(character.stats.HP, character.current_hp + heal_amount)
                    actual_heal = character.current_hp - old_hp
                    
                    if actual_heal > 0:
                        mission_task = asyncio.create_task(db.update_character(user_id_str, character))
                        
                        # Send additional heal notification
                        try:
                            heal_message = f"🩹 *Emergency Heal!* Restored {actual_heal} HP from Mission 7 reward!"
                            asyncio.create_task(update.message.reply_text(heal_message, parse_mode=ParseMode.MARKDOWN))
                        except Exception:
                            pass
            
            # Start timeout task for titan
            timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
            user_timeout_tasks[user_id_str] = timeout_task
            
            # Wait for mission task if it exists
            if mission_task:
                await mission_task
            
            # Launch remaining background operations in parallel
            asyncio.create_task(_handle_explore_background_optimized(
                update, context, user_id, user_id_str, username, db,
                player, titan, sent_message, start_time
            ))
            
        except Exception as db_error:
            logger.error(f"Database error storing titan for {user_id_str}: {db_error}")
    
    except Exception as e:
        logger.error(f"Error in _validate_and_process_optimized: {e}", exc_info=True)


async def _cleanup_existing_titan(user_id_str, db):
    
    try:
        existing_titan = await db.get_titan(user_id_str)
        if existing_titan:
            try:
                if hasattr(existing_titan, 'created_at') and existing_titan.created_at:
                    titan_age = time.time() - existing_titan.created_at.timestamp()
                    if titan_age > 30:  # Reduced from 60 to 30 seconds for faster cleanup
                        await db.delete_titan(user_id_str)
                        logger.info(f"Cleaned up existing titan for user {user_id_str} (age: {titan_age:.1f}s)")
                    else:
                        logger.info(f"Skipping cleanup of recent titan for user {user_id_str} (age: {titan_age:.1f}s)")
                else:
                    # If no created_at, assume it's old and delete
                    await db.delete_titan(user_id_str)
            except (AttributeError, TypeError, OSError) as e:
                logger.warning(f"Error checking titan age for user {user_id_str}: {e}, deleting anyway")
                await db.delete_titan(user_id_str)
    except Exception as e:
        logger.error(f"Error cleaning up existing titan for user {user_id_str}: {e}")
        # Continue processing anyway


async def _handle_explore_background_optimized(update, context, user_id, user_id_str, username, db, player, titan, sent_message, start_time):
    
    try:
        # Start all operations in parallel for maximum speed
        tasks = []
        
        # 1. Track statistics (fire-and-forget)
        stats_task = asyncio.create_task(track_explore_stats(user_id_str, username, battle_completed=False))
        tasks.append(stats_task)
        
        # 2. Update last explore time and cancel previous timeout - batched operation
        current_time = time.time()
        update_data = {}
        update_data["last_explore_time"] = current_time
        
        # Properly handle DailyExplores objects to ensure they can be serialized
        if hasattr(player, "daily_explores") and isinstance(player.daily_explores, list):
            daily_explores_dicts = []
            for de in player.daily_explores:
                if hasattr(de, "dict") and callable(getattr(de, "dict")):
                    daily_explores_dicts.append(de.dict())
                elif isinstance(de, dict):
                    daily_explores_dicts.append(de)
                else:
                    try:
                        daily_explores_dicts.append({"date": getattr(de, "date", ""), "count": getattr(de, "count", 0)})
                    except:
                        logger.warning(f"Could not convert DailyExplores object to dict: {de}")
            update_data["daily_explores"] = daily_explores_dicts
        
        
        # 4. Handle travel progress asynchronously
        travel_task = asyncio.create_task(_handle_travel_progress(update, context, user_id_str, db, player))
        tasks.append(travel_task)
        
        # 5. Cancel existing timeout task if needed
        if user_id_str in user_timeout_tasks:
            existing_task = user_timeout_tasks[user_id_str]
            if not existing_task.done():
                existing_task.cancel()
        
        # 5. Batch update all player data changes - optimize for speed
        if update_data:
            asyncio.create_task(db.batch_update_player(user_id_str, update_data))
        
        # Wait for all critical tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log completion time
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"Complete explore processing time: {processing_time:.1f}ms for user {user_id_str}")
        
    except Exception as e:
        logger.error(f"Error in _handle_explore_background_optimized: {e}", exc_info=True)

async def _handle_travel_progress(update, context, user_id_str, db, player):
    
    # Fast path: check if travel is in progress or player in battle
    travel = getattr(player, "travel", {})
    
    if not travel.get("in_progress"):
        return
        
    # Skip travel updates if player is in battle (quick check)
    if _is_in_battle(user_id_str):
        return
    
    # Calculate new progress
    travel_progress = travel.get("progress", 0) + 1
    travel_required = travel.get("required", 1)
    
    try:
        if travel_progress >= travel_required:
            # Travel completed - prepare update
            new_location = travel.get("to", player.location)
            from_location = travel.get("from", player.location)
            
            # Direct update with minimal data
            update_data = {
                "location": new_location,
                "travel": {}
            }
            
            # Fire-and-forget - update player data
            asyncio.create_task(db.batch_update_player(user_id_str, update_data))
            
            # Process travel mission progress in background
            try:
                from database.missions import process_travel_mission_progress
                # Get fresh player data for mission processing
                fresh_player = await db.get_player(user_id_str)
                if fresh_player:
                    # Process travel-related missions
                    mission_notifications = await process_travel_mission_progress(db, fresh_player, from_location, new_location)
                    # Send mission notifications if any
                    if mission_notifications and update.message:
                        for notification in mission_notifications:
                            asyncio.create_task(update.message.reply_text(notification, parse_mode=ParseMode.MARKDOWN))
            except Exception as e:
                logger.error(f"Error processing travel mission progress: {e}")
            
            # Notify user - also fire-and-forget
            if update.message:
                arrival_message = f"🗺️ You have arrived at <b>{new_location}</b>!"
                asyncio.create_task(update.message.reply_text(arrival_message, parse_mode=ParseMode.HTML))
        else:
            # Optimize update by only changing the progress field
            updated_travel = {"progress": travel_progress}
            for key, value in travel.items():
                if key != "progress":
                    updated_travel[key] = value
            
            # Fire-and-forget update
            asyncio.create_task(db.batch_update_player(user_id_str, {"travel": updated_travel}))
    except Exception as e:
        logger.error(f"Error updating travel progress: {e}")


async def _send_spam_warning(user_id: int, context: ContextTypes.DEFAULT_TYPE, warning_level: int, message: str):
    
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
        if "explore_spam_count" not in context.bot_data:
            context.bot_data["explore_spam_count"] = {}
        context.bot_data["explore_spam_count"][str(user_id)] = 0
        
        # Also reset in database
        await db.bans.update_one(
            {"user_id": str(user_id)},
            {"$set": {"spam_count": 0, "last_spam_update": int(time.time())}},
            upsert=True
        )
        
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
    
    user_id_str = str(user_id)
    try:
        # Record the start time to check if new explore commands happened during the timeout
        start_time = time.time()
        
        # Sleep for the timeout duration - this is the main waiting period
        await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
        
        # Fast path checks - immediate return conditions
        
        # 1. Check if user has explored since this timeout was created
        last_explore_time = user_last_explore.get(user_id_str, 0)
        if last_explore_time > start_time:
            return
        
        # 2. Check if user is in battle - no need to do anything if so
        if _is_in_battle(user_id_str):
            return
        
        # 3. Check if the battle ID has changed (user started a new battle)
        battle_id_key = f"active_battle_id_{user_id}"
        original_battle_id = context.bot_data.get(battle_id_key, None)
        current_battle_id = context.bot_data.get(battle_id_key, None)
        
        if original_battle_id != current_battle_id or not original_battle_id:
            return
        
        # Get database reference for further operations
        db = context.bot_data.get("db")
        if not db:
            return
            
        # Start all heavy operations in parallel for maximum efficiency
        tasks = []
        
        # 1. Handle spam detection and update
        spam_task = asyncio.create_task(_handle_timeout_spam_detection(user_id, user_id_str, context, db))
        tasks.append(spam_task)
        
        # 2. Delete titan and update message
        titan_task = asyncio.create_task(_handle_timeout_titan_cleanup(user_id, user_id_str, context, db, sent_message))
        tasks.append(titan_task)
        
        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
        
    except asyncio.CancelledError:
        # Task was cancelled - normal behavior when user explores again
        pass
    except Exception as e:
        logger.error(f"Error in titan_encounter_timeout: {e}", exc_info=True)
    finally:
        # Clean up the task reference when done
        if user_id_str in user_timeout_tasks:
            del user_timeout_tasks[user_id_str]


async def _handle_timeout_spam_detection(user_id, user_id_str, context, db):
    
    try:
        # Initialize spam count tracking if needed
        if "explore_spam_count" not in context.bot_data:
            context.bot_data["explore_spam_count"] = {}
        
        # Get current spam count - first check memory then database
        if user_id_str not in context.bot_data["explore_spam_count"]:
            spam_count_doc = await db.bans.find_one({"user_id": user_id_str, "spam_count": {"$exists": True}})
            if spam_count_doc:
                context.bot_data["explore_spam_count"][user_id_str] = spam_count_doc.get("spam_count", 0)
            else:
                context.bot_data["explore_spam_count"][user_id_str] = 0
        
        # Increment spam count for not battling
        context.bot_data["explore_spam_count"][user_id_str] += 1
        current_spam_count = context.bot_data["explore_spam_count"][user_id_str]
        
        # Update database in background (fire and forget)
        asyncio.create_task(
            db.bans.update_one(
                {"user_id": user_id_str},
                {"$set": {"spam_count": current_spam_count, "last_spam_update": int(time.time())}},
                upsert=True
            )
        )
        
        # Warning thresholds - non-blocking
        if current_spam_count == 10:
            # Warning at 10 timeouts
            asyncio.create_task(_send_spam_warning(user_id, context, 10, 
                "🚨 <b>Warning:</b> You have let 10 titan encounters expire!\n\n"
                "Continuing this behavior may result in a ban. Please battle the titans you encounter."
            ))
        
        # Ban threshold check
        SPAM_THRESHOLD = 15
        if current_spam_count >= SPAM_THRESHOLD:
            # Create minimal mock update object
            class MockUpdate:
                def __init__(self, user_id):
                    self.effective_user = type('obj', (object,), {'id': user_id, 'first_name': f'User_{user_id}'})()
                    self.message = type('obj', (object,), {'reply_text': lambda text: None})()
            
            mock_update = MockUpdate(user_id)
            # Launch ban handling in background
            asyncio.create_task(_handle_spam_ban(user_id, mock_update, context))
    
    except Exception as e:
        logger.error(f"Error in spam detection: {e}")


async def _handle_timeout_titan_cleanup(user_id, user_id_str, context, db, sent_message):
    
    try:
        battle_id_key = f"active_battle_id_{user_id}"
        current_battle_id = context.bot_data.get(battle_id_key)
        
        # Check if titan exists in DB
        titan_in_db = await db.get_titan(user_id_str)
        
        if titan_in_db:
            # Start titan deletion (don't wait)
            asyncio.create_task(db.delete_titan(user_id_str))
            
            # Update message if possible
            if sent_message and current_battle_id == context.bot_data.get(battle_id_key):
                try:
                    from game.safe_edit import safe_edit_message_text
                    asyncio.create_task(safe_edit_message_text(
                        sent_message,
                        "⏰ Titan encounter expired!\n\nYou took too long to respond. Use /explore to find another titan.",
                        parse_mode=ParseMode.HTML
                    ))
                except Exception:
                    pass
    
    except Exception as e:
        logger.error(f"Error in titan cleanup: {e}")


async def check_spam_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
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
    
    # Get database reference
    db = context.bot_data.get("db")
    if not db:
        if update.message:
            await update.message.reply_text("❌ Database not available.")
        return
    
    try:
        # Get spam count from database
        spam_doc = await db.bans.find_one({"user_id": str(target_user_id), "spam_count": {"$exists": True}})
        spam_count = spam_doc.get("spam_count", 0) if spam_doc else 0
        
        # Get in-memory count if available
        memory_count = 0
        if "explore_spam_count" in context.bot_data:
            memory_count = context.bot_data["explore_spam_count"].get(str(target_user_id), 0)
        
        if update.message:
            await update.message.reply_text(
                f"📊 <b>Spam Count for {target_user_name}</b>\n\n"
                f"<b>Database:</b> {spam_count}\n"
                f"<b>Memory:</b> {memory_count}\n"
                f"<b>User ID:</b> <code>{target_user_id}</code>",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Failed to check spam count: {e}")
        if update.message:
            await update.message.reply_text("❌ Failed to check spam count.")
    
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
    
    # Reset spam count
    success = await reset_user_spam_count(target_user_id, context)
    
    if success:
        if update.message:
            await update.message.reply_text(
                f"✅ Spam count has been reset for {target_user_name}.\n"
                f"They can now use /explore again normally."
            )
    else:
        if update.message:
            await update.message.reply_text(
                f"❌ Failed to reset spam count for {target_user_name}.\n"
                f"Please try again later or check if the user ID is valid."
            )


async def reset_user_spam_count(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    
    user_id_str = str(user_id)
    db = context.bot_data.get("db")
    if not db:
        return False
        
    try:
        # Reset in memory
        if "explore_spam_count" in context.bot_data:
            context.bot_data["explore_spam_count"][user_id_str] = 0
            
        # Reset in database
        await db.bans.update_one(
            {"user_id": user_id_str},
            {"$set": {"spam_count": 0, "last_spam_update": int(time.time())}},
            upsert=True
        )
        
        return True
    except Exception as e:
        logger.error(f"Failed to reset spam count: {e}")
        return False
    
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
            pass
            # context.user_data["hcaptcha_prompted"] = False
            
        # Reset verification in database
        current_time = time.time()
        await db.update_player(user_id, {
            # "hcaptcha_verified": False,
            # "hcaptcha_start_time": None,
            "explore_start_time": current_time,  # Set current time to reset the 25-minute timer
            "last_explore_time": current_time    # Update last explore time too
        })
        
        return True
    except Exception as e:
        logger.error(f"Failed to reset verification state: {e}")
        return False