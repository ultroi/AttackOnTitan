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

# Import mission-related functions
from database.missions import (
    check_mission_item_drops, add_mission_item, 
    process_travel_mission_progress
)

logger = logging.getLogger(__name__)


# # Rate limiting and locking for explore command
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

# # Pre-calculate difficulty levels for faster lookup
DIFFICULTY_BY_LEVEL = {level: "Easy" if level < 8 else ("Normal" if level < 15 else "Hard") for level in range(1, 30)}
# Pre-defined default areas to avoid recreating this list every time
DEFAULT_AREAS = ["Trost District", "Karanes District", "Shiganshina District"]

# Battle button text constant
BATTLE_BUTTON_TEXT = "⚔️ Battle"

# Cached titans for instant access - pre-generated titan data
CACHED_TITANS = {
    "1_Easy": {
        "name": "Small Titan",
        "level": 1,
        "max_hp": 80,
        "abilities": [],
        "difficulty": "Easy",
        "drop_table": {},
        "xp_reward": 25,
        "min_level_requirement": 1,
        "image_url": "https://i.ibb.co/dJ6J58s0/image.jpg"
    },
    "1_Normal": {
        "name": "Normal Titan",
        "level": 1,
        "max_hp": 100,
        "abilities": [],
        "difficulty": "Normal",
        "drop_table": {},
        "xp_reward": 35,
        "min_level_requirement": 1,
        "image_url": "https://i.ibb.co/XkMw0Xt5/image.jpg"
    },
    "1_Hard": {
        "name": "Armored Titan",
        "level": 1,
        "max_hp": 120,
        "abilities": ["armor"],
        "difficulty": "Hard",
        "drop_table": {},
        "xp_reward": 50,
        "min_level_requirement": 1,
        "image_url": "https://i.ibb.co/7J8S4s6v/image.jpg"
    }
}

def format_titan_message(name: str, level: int, image_embed: str = "") -> str:
    """Fast string formatting for titan messages - optimized for speed"""
    return (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{name} Lvl ({level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>"
    )

def _generate_cached_titan(player_level: int, difficulty: str) -> dict:
    """Generate a cached titan for the given level and difficulty - fallback function"""
    # Base stats by difficulty
    base_stats = {
        "Easy": {"hp": 80, "xp": 25},
        "Normal": {"hp": 100, "xp": 35},
        "Hard": {"hp": 120, "xp": 50}
    }

    stats = base_stats.get(difficulty, base_stats["Normal"])

    # Scale stats by player level (simple scaling)
    level_multiplier = max(1, player_level // 5 + 1)
    titan_level = max(1, player_level + random.randint(-1, 1))

    return {
        "name": f"{difficulty} Titan",
        "level": titan_level,
        "max_hp": stats["hp"] * level_multiplier,
        "abilities": ["armor"] if difficulty == "Hard" else [],
        "difficulty": difficulty,
        "drop_table": {},
        "xp_reward": stats["xp"] * level_multiplier,
        "min_level_requirement": max(1, player_level - 2),
        "image_url": TITAN_TYPE_IMAGE_URLS.get("small jogger", "")
    }

# from typing import Optional, List

# def generate_titan_directly(player_level: int, unlocked_areas: Optional[List] = None):
#     """Generate a titan directly without database calls for maximum speed - highly optimized version"""
#     from database.models import Titan, generate_titan_name, generate_titan_hp, generate_titan_xp
    
#     # Get difficulty from pre-calculated mapping
#     difficulty = DIFFICULTY_BY_LEVEL.get(player_level, "Hard")
    
#     # Use current microseconds as a seed for better randomness
#     seed = datetime.now(timezone.utc).microsecond
#     titan_random = random.Random(seed)
    
#     # Titan level: within -2 to +2 of player level, but at least 1
#     level = max(1, player_level + titan_random.randint(-2, 2))
    
#     # Generate name and stats
#     name = generate_titan_name(difficulty)
#     max_hp = generate_titan_hp(level, difficulty)
#     xp_reward = generate_titan_xp(level, difficulty)
    
#     # Use shared constant for default areas
#     areas = unlocked_areas if unlocked_areas else DEFAULT_AREAS
    
#     # Use a shared UTC timestamp for all created_at fields
#     now = datetime.now(timezone.utc)
    
#     # Create titan with all required fields - optimized
#     return Titan(
#         name=name,
#         level=level,
#         max_hp=max_hp,
#         abilities=[],
#         created_at=now,
#         difficulty=difficulty,
#         spawn_areas=areas,
#         drop_table={},
#         xp_reward=xp_reward,
#         min_level_requirement=level
#     )

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

# async def titan_encounter_timeout(user_id: int, context: ContextTypes.DEFAULT_TYPE, sent_message=None):
#     """Handle titan encounter timeout with proper cleanup."""
#     try:
#         # Store the timestamp when the timeout task started
#         start_time = time.time()
        
#         # Wait for the timeout period
#         await asyncio.sleep(TITAN_TIMEOUT_SECONDS)
        
#         # Get the latest battle_id for this user
#         battle_id_key = f"active_battle_id_{user_id}"
#         current_battle_id = context.bot_data.get(battle_id_key)
        
#         # Check if user's last explore was AFTER this timeout task started
#         user_id_str = str(user_id)
#         last_explore_time = user_last_explore.get(user_id_str, 0)
#         if last_explore_time > start_time:
#             return
        
#         # Use helper function to check if there's an active battle
#         if _is_in_battle(user_id_str):
#             return
        
#         # Clean up the titan if no battle is active
#         db = context.bot_data.get("db")
#         if db:
#             titan_in_db = await db.get_titan(user_id_str)
#             if titan_in_db:
#                 await db.delete_titan(user_id_str)
                
#                 # Clear battle flags to fix button issues
#                 battle_id_key = f"active_battle_id_{user_id}"
#                 battle_started_key = f"titan_battle_started_{user_id}"
                
#                 if battle_started_key in context.bot_data:
#                     del context.bot_data[battle_started_key]
                
#                 # Only edit message if no battle has started
#                 if sent_message and current_battle_id == context.bot_data.get(battle_id_key):
#                     try:
#                         from game.safe_edit import safe_edit_message_text
#                         await safe_edit_message_text(
#                             sent_message,
#                             "⏰ Titan encounter expired!\n\nYou took too long to respond. Use /explore to find another titan.",
#                             parse_mode=ParseMode.HTML
#                         )
#                     except Exception:
#                         pass
#     except asyncio.CancelledError:
#         # Task was cancelled, this is expected behavior
#         pass
#     except Exception as e:
#         logger.error(f"Error in titan_encounter_timeout: {e}")
#     finally:
#         # Clean up the task reference
#         key = f"titan_timeouts_{user_id}"
#         if key in context.bot_data:
#             tasks = context.bot_data[key]
#             # Remove completed tasks and limit to at most 1 active task
#             context.bot_data[key] = [t for t in tasks if not t.done()][:1]

# async def cleanup_user_timeouts(user_id: int, context: ContextTypes.DEFAULT_TYPE):
#     """Cancel and clean up all timeout tasks for a user."""
#     key = f"titan_timeouts_{user_id}"
#     if key in context.bot_data:
#         for task in context.bot_data[key]:
#             if not task.done():
#                 task.cancel()
#         del context.bot_data[key]

# async def reset_explore_timer(user_id, db):
#     """Reset the explore_start_time for a user (for inactivity/hCaptcha logic)."""
#     await db.update_player(user_id, {"explore_start_time": None})
#     return True




# Helper function to check if a user is in battle - reduces code duplication
def _is_in_battle(user_id_str: str) -> bool:
    """Check if a user is in battle - centralized helper function"""
    try:
        from game.battle_system import active_battles
        return user_id_str in active_battles
    except ImportError:
        return False

# # Decorator to protect explore command from bans and maintenance mode

# # --- ULTRA-FAST EXPLORE COMMAND ---
# @maintenance_protected
# @ban_protected
# async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Ultra-fast explore: send titan instantly, run all checks in background, preserve all logic/messages."""

#     import time
#     debug_times = {}
#     debug_times['start'] = time.time()
#     if not update.effective_user:
#         await _reply_error(update, "Cannot identify user. Please try again.")
#         return

#     user_id = update.effective_user.id
#     user_id_str = str(user_id)
#     username = update.effective_user.username or update.effective_user.first_name or "Unknown"
#     debug_times['after_user'] = time.time()

#     # Only use in private chats
#     if not update.effective_chat or update.effective_chat.type != "private":
#         await _reply_error(update, "This command can only be used in private chats.")
#         return

#     debug_times['after_private'] = time.time()

#     # Per-user lock to prevent overlapping explores (race condition fix)
#     if user_id_str not in user_explore_locks:
#         user_explore_locks[user_id_str] = asyncio.Lock()
#     lock = user_explore_locks[user_id_str]
#     if lock.locked():
#         await _reply_error(update, "Please wait, your previous explore is still processing.")
#         return
#     async with lock:
#         debug_times['after_lock'] = time.time()
#         # Check if user is already in a battle BEFORE anything else
#         if _is_in_battle(user_id_str):
#             await _reply_error(update, f"{username or 'Player'} is currently battling!")
#             return

#         now_ms = time.time()
#         if now_ms - user_last_explore.get(user_id_str, 0) < 1.2:
#             return
#         user_last_explore[user_id_str] = now_ms
#         debug_times['after_rate'] = time.time()

#     # Get database immediately (critical dependency)
#     db = context.bot_data.get("db")
#     if db is None:
#         await _reply_error(update, "Internal error: Database not initialized.")
#         return

#     debug_times['after_db'] = time.time()

#     # Get player data (await, as we need team for titan gen)
#     t0 = time.time()
#     player = await db.get_player(user_id_str)
#     if not player:
#         if update.message:
#             await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
#         elif update.callback_query:
#             await update.callback_query.edit_message_text("You haven't created a player account yet! Use /start to begin.")
#         return

#     debug_times['after_player'] = time.time()

#     # Get character name for titan gen
#     player_character_name = player.team[0].character_name if player.team else None
#     if not player_character_name:
#         await _reply_error(update, "You don't have any character in your team.")
#         return

#     debug_times['after_charname'] = time.time()

#     # Check for decision points first before generating a titan
#     location = getattr(player, "location", None)
#     if location and location in TRAVEL_MAP and location.startswith("Decision_"):
#         directions = TRAVEL_MAP[location]
#         keyboard = [
#             [InlineKeyboardButton(dir, callback_data=f"travel_decision_{dir.strip().lower()}")]
#             for dir in directions.keys()
#         ]
#         reply_markup = InlineKeyboardMarkup(keyboard)
#         asyncio.create_task(_handle_decision_point_cleanup(user_id_str, context))
#         if update.message:
#             await update.message.reply_text(
#                 f"⚠️ <b>Decision Required!</b> ⚠️\n\n"
#                 f"You are at a decision point: <b>{location}</b>\n"
#                 f"You must choose a direction to continue your journey.\n\n"
#                 f"<i>You cannot explore further until you make a choice.</i>",
#                 reply_markup=reply_markup,
#                 parse_mode=ParseMode.HTML
#             )
#         elif update.callback_query and update.callback_query.message:
#             await update.callback_query.message.chat.send_message(
#                 f"⚠️ <b>Decision Required!</b> ⚠️\n\n"
#                 f"You are at a decision point: <b>{location}</b>\n"
#                 f"You must choose a direction to continue your journey.\n\n",
#                 reply_markup=reply_markup,
#                 parse_mode=ParseMode.HTML
#             )
#         return

#     debug_times['after_decision'] = time.time()

#     # Check for captcha with lower probability (3% instead of 4%)
#     if random.random() < 0.03 and not context.user_data.get('captcha_active', False):
#         t0 = time.time()
#         captcha_triggered = await spawn_captcha(update, context)
#         if captcha_triggered:
#             return

#     debug_times['after_captcha'] = time.time()

#     # Check for verification requirement
#     now = time.time()
#     player_verified = getattr(player, "hcaptcha_verified", False)
#     last_explore = getattr(player, "last_explore_time", None)
#     explore_start_time = getattr(player, "explore_start_time", None)

#     if context.user_data.get("hcaptcha_prompted", False):
#         t0 = time.time()
#         player_fresh = await db.get_player(user_id_str, force_refresh=True) if hasattr(db, 'get_player') and 'force_refresh' in db.get_player.__code__.co_varnames else await db.get_player(user_id_str)
#         player_verified_fresh = getattr(player_fresh, "hcaptcha_verified", False)
#         last_verified_fresh = getattr(player_fresh, "last_verified", 0)
#         now_fresh = time.time()
#         if player_verified_fresh or (last_verified_fresh and (now_fresh - last_verified_fresh) < 600):
#             context.user_data["hcaptcha_prompted"] = False
#             await update.message.reply_text("✅ Verification confirmed! You can now continue exploring.")
#             await db.update_player(user_id_str, {"hcaptcha_verified": True})
#             player = await db.get_player(user_id_str, force_refresh=True) if hasattr(db, 'get_player') and 'force_refresh' in db.get_player.__code__.co_varnames else await db.get_player(user_id_str)
#         else:
#             await _reply_error(update, "Please complete the hCaptcha verification to continue exploring.")
#             return

#     debug_times['after_hcaptcha'] = time.time()

#     if not explore_start_time:
#         asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": now}))
#     else:
#         if (now - explore_start_time) > 1500:
#             asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": None}))
#             last_verified_time = getattr(player, "last_verified", 0)
#             if not last_verified_time or (now - last_verified_time) > 1800:
#                 t0 = time.time()
#                 if await _handle_verification(update, context, user_id, now, db):
#                     return
#         else:
#             asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": now}))

#     debug_times['after_explore_timer'] = time.time()

#     if last_explore and (now - last_explore) > 1800:
#         last_verified_time = getattr(player, "last_verified", 0)
#         if not last_verified_time or (now - last_verified_time) > 1800:
#             t0 = time.time()
#             if await _handle_verification(update, context, user_id, now, db):
#                 return

#     debug_times['after_last_explore'] = time.time()

#     # --- INSTANT TITAN GENERATION & MESSAGE ---
#     try:
#         t0 = time.time()
#         titan = generate_titan_directly(
#             player_level=getattr(player.team[0], 'level', 1),
#             unlocked_areas=player.unlocked_areas
#         )
#     except Exception:
#         titan = Titan(
#             name="Unknown Titan",
#             level=1,
#             max_hp=100,
#             abilities=[],
#             created_at=datetime.now(timezone.utc),
#             difficulty="Normal",
#             spawn_areas=["Trost District"],
#             drop_table={},
#             xp_reward=50,
#             min_level_requirement=1
#         )

#     debug_times['after_titan'] = time.time()

#     # Always generate a new battle ID for each explore
#     battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
#     context.bot_data[f"active_battle_id_{user_id}"] = battle_id

#     # Minimal inline keyboard
#     keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
#     reply_markup = InlineKeyboardMarkup(keyboard)

#     # Minimal titan image lookup
#     titan_name_lower = titan.name.lower()
#     titan_image_url = None
#     for type_, url in TITAN_TYPE_IMAGE_URLS.items():
#         if type_ in titan_name_lower:
#             titan_image_url = url
#             break
#     image_embed = f'<a href="{titan_image_url}">!</a>' if titan_image_url else ""

#     reply_text = (
#         f"<code>-------------------------</code>\n"
#         f"📍 <b>{titan.name} Lvl ({titan.level})</b>\n"
#         f"<b>has blocked your way{image_embed}</b>\n"
#         f"<code>-------------------------</code>\n"
#     )

#     sent_message = None
#     try:
#         t0 = time.time()
#         if update.message:
#             sent_message = await update.message.reply_text(
#                 reply_text,
#                 reply_markup=reply_markup,
#                 parse_mode=ParseMode.HTML,
#                 disable_web_page_preview=False
#             )
#         elif update.callback_query and update.callback_query.message:
#             sent_message = await update.callback_query.message.chat.send_message(
#                 reply_text,
#                 reply_markup=reply_markup,
#                 parse_mode=ParseMode.HTML,
#                 disable_web_page_preview=False
#             )
#     except Exception:
#         if update.message:
#             await update.message.reply_text("An error occurred. Please try again.")
#         return

#     # --- ALL LOGIC, CHECKS, MESSAGING IN BACKGROUND ---
#     asyncio.create_task(_background_explore_checks(
#         update, context, user_id, user_id_str, username, db, player, player_character_name, titan, sent_message
#     ))

# # --- BACKGROUND LOGIC TASK ---
# async def _background_explore_checks(update, context, user_id, user_id_str, username, db, player, player_character_name, titan, sent_message):
#     """Run all explore logic/checks/messaging in background after titan message."""
#     # --- TRAVEL PROGRESS LOGIC ---
#     travel = getattr(player, "travel", {})
#     is_in_battle = _is_in_battle(user_id_str)
    
#     if travel.get("in_progress") and not is_in_battle:
#         travel_progress = travel.get("progress", 0) + 1
#         travel_required = travel.get("required", 1)
#         travel_update = {"travel.progress": travel_progress}
#         # If travel completed
#         if travel_progress >= travel_required:
#             # Update location and clear travel state
#             new_location = travel.get("to", player.location)
#             from_location = travel.get("from", "Unknown")
#             travel_update = {
#                 "location": new_location,
#                 "travel": {}
#             }
            
#             # Update travel mission progress
#             travel_notifications = []
#             try:
#                 travel_notifications = await process_travel_mission_progress(db, player, from_location, new_location)
#             except Exception as e:
#                 logger.error(f"Failed to update travel mission progress: {e}")
            
#             # Notify user of arrival
#             try:
#                 arrival_message = f"🗺️ You have arrived at <b>{new_location}</b>!"
                
#                 # Add any mission notifications
#                 if travel_notifications:
#                     arrival_message += "\n\n" + "\n".join(travel_notifications)
                
#                 if update.message:
#                     await update.message.reply_text(
#                         arrival_message,
#                         parse_mode=ParseMode.HTML
#                     )
#                 elif update.callback_query and update.callback_query.message:
#                     await update.callback_query.message.chat.send_message(
#                         arrival_message,
#                         parse_mode=ParseMode.HTML
#                     )
#             except Exception:
#                 pass
#         await db.update_player(user_id_str, travel_update)
        
#     try:
#         # --- SPAM TRACKING: Warn at 10, ban at 15 explores without battle ---
#         if "explore_spam_count" not in context.bot_data:
#             context.bot_data["explore_spam_count"] = {}
#         spam_count = context.bot_data["explore_spam_count"].get(user_id_str, 0) + 1
#         context.bot_data["explore_spam_count"][user_id_str] = spam_count

#         is_in_battle = _is_in_battle(user_id_str)
#         if is_in_battle:
#             context.bot_data["explore_spam_count"][user_id_str] = 0
#         else:
#             if spam_count == 10:
#                 if update.message:
#                     await update.message.reply_text("⚠️ Warning: Don't spam, you will be banned.")
#             elif spam_count >= 15:
#                 asyncio.create_task(_handle_spam_ban(user_id, update, context))
#                 return

#         if is_in_battle:
#             await _reply_error(update, f"{username or 'Player'} is currently battling!")
#             return

#         # Invalidate titan cache if not in battle
#         if not is_in_battle:
#             db.invalidate_titan_cache(user_id_str)

#         # Get character data
#         player_character = await db.get_character(user_id_str, player_character_name)
#         if not player_character:
#             await _reply_error(update, f"Your character {player_character_name} was not found.")
#             return

#         if player_character.gas < 100:
#             try:
#                 await sent_message.edit_text(
#                     f"{player_character_name} doesn't have enough gas to explore (needs at least 100). Use /char char_name to refill gas.",
#                     parse_mode=ParseMode.HTML
#                 )
#             except Exception:
#                 await _reply_error(update, f"{player_character_name} doesn't have enough gas to explore (needs at least 100). Use /char char_name to refill gas.")
#             return


#         # Only store titan if user is not in battle (double check for race conditions)
#         if not _is_in_battle(user_id_str):
#             db._titan_cache[user_id_str] = titan
#             asyncio.create_task(db.store_titan(user_id_str, titan))

#         # Reset verification flag but maintain recent verification record
#         player_verified = getattr(player, "hcaptcha_verified", False)
#         if player_verified:
#             asyncio.create_task(db.update_player(user_id_str, {
#                 "hcaptcha_start_time": None
#             }))


#         # Check for mission item drops (optimized for speed)
#         mission_item_drops = []
#         mission_notifications = []
#         if hasattr(player, "missions") and player.missions:
#             active_missions = [m for m in player.missions if m["status"] == "in_progress"]
#             if active_missions:
#                 mission_item_drops = await check_mission_item_drops(player)
#                 for item_drop in mission_item_drops:
#                     success, msg = await add_mission_item(db, player, item_drop["key"])
#                     if success and msg:
#                         mission_notifications.append(msg)
#                 # Removed process_explore_mission_progress call - it will be called only after battle completion
#         if mission_notifications:
#             try:
#                 for msg in mission_notifications:
#                     if update.message:
#                         await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
#                     elif update.callback_query and update.callback_query.message:
#                         await update.callback_query.message.chat.send_message(msg, parse_mode=ParseMode.HTML)
#             except Exception as e:
#                 logger.error(f"Failed to send mission notification: {e}")

#         # Setup timeout
#         asyncio.create_task(_handle_post_explore_tasks(user_id, context, sent_message, time.time(), user_id_str))
#     except Exception as e:
#         logger.error(f"[EXPLORE BACKGROUND] Error: {e}")
    
# async def _handle_timeout_setup(user_id, context, sent_message):
#     """Setup the timeout handler in background - simplified version"""
#     # Use simpler key structure
#     key = f"titan_timeouts_{user_id}"
#     if key not in context.bot_data:
#         context.bot_data[key] = []
    
#     if context.bot_data[key]:
#         for task in context.bot_data[key]:
#             if not task.done():
#                 try:
#                     task.cancel()
#                     logger.debug(f"Cancelled previous timeout task for user {user_id}")
#                 except Exception:
#                     pass
        
#         # Clear the list of tasks
#         context.bot_data[key] = []
    
#     # Create a new timeout task
#     timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
#     context.bot_data[key].append(timeout_task)
    
#     # Store creation time in bot_data instead of as a task attribute
#     task_id = id(timeout_task)
#     if "task_creation_times" not in context.bot_data:
#         context.bot_data["task_creation_times"] = {}
#     context.bot_data["task_creation_times"][task_id] = time.time()
    
#     # Limit to max 2 tasks
#     if len(context.bot_data[key]) > 2:
#         context.bot_data[key] = context.bot_data[key][-2:]


# async def _handle_post_explore_tasks(user_id, context, sent_message, start_time, user_id_str):
#     """Combined handler for all post-explore background tasks for better performance"""
#     # Execute tasks in parallel where possible
#     tasks = []
    
#     # Handle timeout
#     tasks.append(_handle_timeout_setup(user_id, context, sent_message))
    
#     # Log performance
#     end_time = time.time()
#     response_time = end_time - start_time
#     await asyncio.gather(*tasks)


# async def _handle_verification(update, context, user_id, now, db):

#     user_id_str = str(user_id)
#     player = await db.get_player(user_id_str)
    
async def _handle_verification(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, now: float, db):
    """Handle hCaptcha verification requirement"""
    user_id_str = str(user_id)

    # Get fresh player data
    player = await db.get_player(user_id_str)
    if not player:
        return False

    # Check if player is already verified
    if player and getattr(player, "hcaptcha_verified", True):
        context.user_data["hcaptcha_prompted"] = False
        return False

    # Check for verification success message from web
    last_verified_time = getattr(player, "last_verified", 0)
    if last_verified_time and now - last_verified_time < 600:  # Within 10 minutes
        # User was recently verified via web
        context.user_data["hcaptcha_prompted"] = False
        await db.update_player(user_id_str, {"hcaptcha_verified": True})
        await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
        return False

    # Check if verification is already in progress
    if context.user_data.get("hcaptcha_prompted", False):
        # Check if database has been updated since the last check
        if player and getattr(player, "last_verified", 0) > context.user_data.get("last_verification_check", 0):
            # Database was updated, user was verified
            context.user_data["hcaptcha_prompted"] = False
            context.user_data["last_verification_check"] = now
            await db.update_player(user_id_str, {
                "hcaptcha_verified": True,
                "last_verified": now
            })
            await update.message.reply_text("✅ Verification successful! You can now continue exploring.")
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

    except Exception as e:
        logger.error(f"Error sending verification message: {e}")
        context.user_data["hcaptcha_prompted"] = False

    return True

# async def _handle_decision_point_cleanup(user_id_str, context):
#     """Cleanup all battle-related data when at a decision point"""
#     try:
#         # Use the helper function to check for battle
#         if _is_in_battle(user_id_str):
#             try:
#                 from game.battle_system import active_battles
#                 active_battles.pop(user_id_str, None)
#             except ImportError:
#                 pass
            
#         # Also clear active_battle_id in bot_data if present
#         battle_id_key = f"active_battle_id_{user_id_str}"
#         if battle_id_key in context.bot_data:
#             context.bot_data.pop(battle_id_key, None)
            
#         # Clean up any titan data
#         db = context.bot_data.get("db")
#         if db:
#             await db.delete_titan(user_id_str)
            
#         # Clear any timeout tasks
#         key = f"titan_timeouts_{user_id_str}"
#         if key in context.bot_data:
#             for task in context.bot_data[key]:
#                 if not task.done():
#                     task.cancel()
#             context.bot_data[key] = []
#     except Exception as e:
#         logger.error(f"Error in decision point cleanup: {e}")
#         pass
    
# async def _handle_spam_ban(user_id, update, context):
#     """Handle banning for spam in the background"""
#     db = context.bot_data.get("db")
#     if not db:
#         return
        
#     try:
#         expiry = int(time.time()) + 24*3600
#         reason = "Spamming explore without battle"
#         await db.bans.update_one(
#             {"user_id": user_id},
#             {"$set": {"user_id": user_id, "expiry": expiry, "reason": reason, "banned_by": user_id, "banned_at": int(time.time())}},
#             upsert=True
#         )
        
#         # Notify user
#         if update.message:
#             await update.message.reply_text("You are banned for spamming explore without battle.")
        
#         context.bot_data["explore_spam_count"][str(user_id)] = 0
        
#         # Send ban log in background
#         bot_username = (await context.bot.get_me()).username or "Bot"
#         time_str = "24 hours"
#         msg = (
#             f"<b>#BanEvent</b>\n\n"
#             f"<b>Target</b> : <a href=\"tg://user?id={user_id}\">{update.effective_user.first_name}</a>\n"
#             f"<b>Target ID</b> : <code>{user_id}</code>\n"
#             f"<b>By</b> : <a href=\"tg://user?id={context.bot.id}\">{bot_username}</a>\n"
#             f"<b>Reason</b> : <code>Spamming explore without battle</code>\n"
#             f"<b>Time</b> : <code>{time_str}</code>"
#         )
#         BAN_LOG_CHAT_ID = -1002873117075
#         await context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.HTML)
#     except Exception:
#         pass


# async def _expire_cache_key(context, cache_key, seconds):
#     """Helper to expire a cache key after a set time"""
#     await asyncio.sleep(seconds)
#     if cache_key in context.bot_data:
#         context.bot_data.pop(cache_key, None)


# async def cleanup_stale_explore_records(max_age_hours: int = 24):
#     """Clean up stale explore records to prevent memory leaks."""
#     while True:
#         try:
#             current_time = datetime.now(timezone.utc).timestamp()
#             # Prune user_last_explore
#             for uid in list(user_last_explore.keys()):
#                 if current_time - user_last_explore[uid] > (max_age_hours * 3600):
#                     user_last_explore.pop(uid, None)
#             await asyncio.sleep(3600)
#         except Exception:
#             pass
#             await asyncio.sleep(3600)



# async def force_cleanup_user(user_id: int, db: Database):
#     """Force cleanup of all user-related data."""
#     try:
#         user_id_str = str(user_id)
#         # Check if user is in battle using helper function
#         if _is_in_battle(user_id_str):
#             try:
#                 from game.battle_system import cleanup_battle
#                 cleanup_battle(user_id_str, "forced_cleanup")
#                 # Remove from active battles directly
#                 from game.battle_system import active_battles
#                 active_battles.pop(user_id_str, None)
#             except Exception:
#                 pass
        
#         # Clean up other data
#         user_last_explore.pop(user_id_str, None)
#         await db.update_player(user_id, {"last_explore": None})
#         await db.delete_titan(user_id_str)
        
#         # Remove player activity tracking if available
#         try:
#             from utils.monitor import remove_player_activity
#             remove_player_activity(user_id)
#         except ModuleNotFoundError:
#             pass
#     except Exception:
#         pass

# async def start_cleanup_task():
#     """Start the cleanup task."""
#     asyncio.create_task(cleanup_stale_explore_records())





# # Import centralized safe edit functions
# try:
#     from game.safe_edit import safe_edit_message_text, safe_edit_message_caption
# except ImportError:
#     # Fallback implementations if import fails
#     async def safe_edit_message_text(message, text, reply_markup=None, parse_mode=None):
#         """Helper function to safely edit messages, handling common errors"""
#         try:
#             # Check if content is identical (basic check)
#             if hasattr(message, "text") and message.text == text:
#                 # Check if markup is also identical
#                 existing_markup = getattr(message, "reply_markup", None)
#                 if (existing_markup is None and reply_markup is None) or \
#                    (existing_markup is not None and reply_markup is not None and 
#                     existing_markup.to_dict() == reply_markup.to_dict()):
        
#                     return False
                    
#             # Create kwargs dynamically
#             kwargs = {"text": text}
#             if reply_markup is not None:
#                 kwargs["reply_markup"] = reply_markup
#             if parse_mode is not None:
#                 kwargs["parse_mode"] = parse_mode
                
#             await message.edit_text(**kwargs)
#             return True
            
#         except Exception as e:
#             if "message is not modified" in str(e).lower():
#                 return False
#             else:
#                 # Log other errors at warning level
#                 logging.warning(f"Error editing message: {e}")
#                 return False
                
#     async def safe_edit_message_caption(message, caption, reply_markup=None, parse_mode=None):
#         """Helper function to safely edit message captions, handling common errors"""
#         try:
#             # Check if content is identical (basic check)
#             if hasattr(message, "caption") and message.caption == caption:
#                 # Check if markup is also identical
#                 existing_markup = getattr(message, "reply_markup", None)
#                 if (existing_markup is None and reply_markup is None) or \
#                    (existing_markup is not None and reply_markup is not None and 
#                     existing_markup.to_dict() == reply_markup.to_dict()):
#                     return False
                    
#             # Create kwargs dynamically
#             kwargs = {"caption": caption}
#             if reply_markup is not None:
#                 kwargs["reply_markup"] = reply_markup
#             if parse_mode is not None:
#                 kwargs["parse_mode"] = parse_mode
                
#             await message.edit_caption(**kwargs)
#             return True
            
#         except Exception as e:
#             if "message is not modified" in str(e).lower():
#                 return False
#             else:
#                 return False




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
    
    # Start multiple async operations in parallel
    # This reduces waiting time by running DB queries concurrently
    player_future = db.get_player(user_id_str)
    
    # Always use db.get_player and fetch character for level
    player = await player_future
    if not player or not player.team:
        await _reply_error(update, "You haven't created a player account yet! Use /start to begin.")
        return

    # Get character name from first team member
    character_name = player.team[0].character_name if hasattr(player.team[0], 'character_name') else player.team[0]
    
    # Fetch character object for level - start this query right away
    character_future = db.get_character(player.user_id, character_name)
    
    # While character is being fetched, prepare other data that doesn't depend on it
    location = getattr(player, 'location', None)
    unlocked_areas = getattr(player, 'unlocked_areas', DEFAULT_AREAS)
    
    # Now await the character data
    character = await character_future
    player_level = character.level if character and hasattr(character, 'level') else 1
    gas = character.gas if character and hasattr(character, 'gas') else 0
    
    # Quick validations
    if not character_name:
        await _reply_error(update, "You don't have any character in your team.")
        return
    
    if gas < 100:
        await _reply_error(update, f"{character_name} doesn't have enough gas to explore (needs at least 100). Current: {gas}")
        return
    
    # Check for captcha with lower probability (3% instead of 4%)
    if random.random() < 0.03 and not context.user_data.get('captcha_active', False):
        t0 = time.time()
        captcha_triggered = await spawn_captcha(update, context)
        if captcha_triggered:
            return

    # Check for verification requirement
    now = time.time()
    player_verified = getattr(player, "hcaptcha_verified", False)
    last_explore = getattr(player, "last_explore_time", None)
    explore_start_time = getattr(player, "explore_start_time", None)

    if context.user_data.get("hcaptcha_prompted", False):
        t0 = time.time()
        player_fresh = await db.get_player(user_id_str, force_refresh=True) if hasattr(db, 'get_player') and 'force_refresh' in db.get_player.__code__.co_varnames else await db.get_player(user_id_str)
        player_verified_fresh = getattr(player_fresh, "hcaptcha_verified", False)
        last_verified_fresh = getattr(player_fresh, "last_verified", 0)
        now_fresh = time.time()
        if player_verified_fresh or (last_verified_fresh and (now_fresh - last_verified_fresh) < 600):
            context.user_data["hcaptcha_prompted"] = False
            await update.message.reply_text("✅ Verification confirmed! You can now continue exploring.")
            await db.update_player(user_id_str, {"hcaptcha_verified": True})
            player = await db.get_player(user_id_str, force_refresh=True) if hasattr(db, 'get_player') and 'force_refresh' in db.get_player.__code__.co_varnames else await db.get_player(user_id_str)
        else:
            await _reply_error(update, "Please complete the hCaptcha verification to continue exploring.")
            return

    if not explore_start_time:
        asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": now}))
    else:
        if (now - explore_start_time) > 1500:
            asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": None}))
            last_verified_time = getattr(player, "last_verified", 0)
            if not last_verified_time or (now - last_verified_time) > 1800:
                t0 = time.time()
                if await _handle_verification(update, context, user_id, now, db):
                    return
        else:
            asyncio.create_task(db.update_player(user_id_str, {"last_explore_time": now, "explore_start_time": now}))

    if last_explore and (now - last_explore) > 1800:
        last_verified_time = getattr(player, "last_verified", 0)
        if not last_verified_time or (now - last_verified_time) > 1800:
            t0 = time.time()
            if await _handle_verification(update, context, user_id, now, db):
                return
    
    # Generate titan instantly (from cache)
    titan_data = CACHED_TITANS.get(f"{player_level}_{DIFFICULTY_BY_LEVEL.get(player_level, 'Hard')}")
    if not titan_data:
        titan_data = _generate_cached_titan(player_level, DIFFICULTY_BY_LEVEL.get(player_level, "Hard"))
    
    # Add slight randomization to cached titan
    titan_level = max(1, titan_data['level'] + random.randint(-1, 1))
    
    # Generate battle ID
    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id
    
    # Create message components
    keyboard = [[InlineKeyboardButton(BATTLE_BUTTON_TEXT, callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Pre-format message with image - using the faster formatter
    image_embed = f'<a href="{titan_data["image_url"]}">!</a>' if titan_data.get('image_url') else ""
    reply_text = format_titan_message(
        name=titan_data['name'],
        level=titan_level,
        image_embed=image_embed
    )
    
    # Send response immediately - no need to wait for this to complete before proceeding
    # This improves responsiveness by not waiting for Telegram's API response
    send_message_task = asyncio.create_task(
        update.message.reply_text(
            reply_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )
    )
    
    # Create actual Titan object for database immediately
    titan = Titan(
        name=titan_data['name'],
        level=titan_level,
        max_hp=titan_data['max_hp'],
        abilities=titan_data['abilities'],
        created_at=datetime.now(timezone.utc),
        difficulty=titan_data['difficulty'],
        spawn_areas=unlocked_areas,
        drop_table=titan_data['drop_table'],
        xp_reward=titan_data['xp_reward'],
        min_level_requirement=titan_data['min_level_requirement']
    )
    
    # Start background tasks while we wait for the message to send
    # Launch titan storage immediately without waiting
    store_titan_task = asyncio.create_task(
        db.store_titan(user_id_str, titan)
    )
    
    # Wait for message to be sent to get the sent_message object
    try:
        sent_message = await send_message_task
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        # Even if the message fails, we can still continue with background processing
        # The user can try again, but we won't lose state
        return
    
    # Record response time
    response_time = (time.time() - start_time) * 1000
    logger.info(f"Explore response time: {response_time:.1f}ms")
    
    # Run ALL background processing asynchronously
    # We don't await this - it runs completely in the background
    asyncio.create_task(_handle_explore_background(
        update, context, user_id, user_id_str, username, db, 
        player, titan, sent_message, start_time
    ))

async def _handle_explore_background(update, context, user_id, user_id_str, username, db, 
                                   player, titan, sent_message, start_time):
    """Handle all background processing after sending the titan message"""
    try:
        # Cache the titan in memory immediately
        if not _is_in_battle(user_id_str):
            db._titan_cache[user_id_str] = titan
            # Note: titan storage task is launched earlier in the main function

        # Spam protection - all in-memory operations (very fast)
        if "explore_spam_count" not in context.bot_data:
            context.bot_data["explore_spam_count"] = {}
        spam_count = context.bot_data["explore_spam_count"].get(user_id_str, 0) + 1
        context.bot_data["explore_spam_count"][user_id_str] = spam_count

        # Launch background tasks in parallel
        background_tasks = []
        
        # Spam warning task if needed
        if spam_count == 10:
            # Use create_task instead of awaiting directly
            warning_task = asyncio.create_task(
                update.message.reply_text("⚠️ Warning: Don't spam, you will be banned.")
            )
            background_tasks.append(warning_task)
        elif spam_count >= 15:
            # Ban handling
            ban_task = asyncio.create_task(_handle_spam_ban(user_id, update, context))
            background_tasks.append(ban_task)
            # Exit early since user will be banned
            await asyncio.gather(*background_tasks, return_exceptions=True)
            return

        # Reset spam count if in battle
        if _is_in_battle(user_id_str):
            context.bot_data["explore_spam_count"][user_id_str] = 0

        # Setup timeout
        timeout_task = asyncio.create_task(_setup_timeout(user_id, context, sent_message))
        background_tasks.append(timeout_task)

        # Handle verification, missions, etc. - run these in parallel
        player_character_name = player.team[0].character_name if player.team else None
        
        # Update player data task
        update_task = asyncio.create_task(
            db.update_player(user_id_str, {
                "last_explore_time": time.time(), 
                "explore_start_time": getattr(player, 'explore_start_time', None) or time.time()
            })
        )
        background_tasks.append(update_task)
        
        # Handle missions, travel, and other tasks
        additional_task = asyncio.create_task(
            _handle_additional_background_tasks(update, context, user_id, user_id_str, db, player_character_name)
        )
        background_tasks.append(additional_task)

        # Wait for all tasks to complete in the background
        # The return_exceptions=True prevents one failed task from causing all to fail
        async def wait_for_tasks():
            await asyncio.gather(*background_tasks, return_exceptions=True)
            
        asyncio.create_task(wait_for_tasks())

    except Exception as e:
        logger.error(f"Background processing error: {e}", exc_info=True)

async def _setup_timeout(user_id, context, sent_message):
    """Setup timeout for titan encounter - optimized for speed"""
    key = f"titan_timeouts_{user_id}"
    
    # Fast path - we just need to store a single task
    # Cancel any existing tasks first (quick in-memory operation)
    if key in context.bot_data:
        for task in context.bot_data[key]:
            if not task.done():
                task.cancel()
    else:
        context.bot_data[key] = []
    
    # Create new timeout - fire and forget
    timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
    
    # Store task in context for future reference
    context.bot_data[key] = [timeout_task]

async def _handle_additional_background_tasks(update, context, user_id, user_id_str, db, player_data):
    """Handle verification, missions, and other background tasks in parallel"""
    try:
        # Get full player data only if needed for complex operations
        # We use cache whenever possible for better performance
        player = await db.get_player(user_id_str)
        if not player:
            return

        # Launch all background tasks in parallel to save time
        background_tasks = []
        
        # 1. Handle verification logic
        verification_task = asyncio.create_task(
            _handle_verification_background(update, context, user_id, db, player)
        )
        background_tasks.append(verification_task)

        # 2. Handle travel progress
        travel_task = asyncio.create_task(
            _handle_travel_progress(update, context, user_id_str, db, player)
        )
        background_tasks.append(travel_task)

        # 3. Handle mission items (if player has missions)
        if hasattr(player, "missions") and player.missions:
            mission_task = asyncio.create_task(
                _handle_mission_items(update, context, db, player)
            )
            background_tasks.append(mission_task)

        # Wait for all background tasks to complete
        # Using gather with return_exceptions=True means one task failing won't affect others
        await asyncio.gather(*background_tasks, return_exceptions=True)

    except Exception as e:
        # Log but don't propagate - this is a background task
        logger.error(f"Additional background tasks error: {e}", exc_info=True)

async def _handle_verification_background(update, context, user_id, db, player):
    """Handle verification checks in background"""
    now = time.time()
    explore_start_time = getattr(player, "explore_start_time", None)
    
    if not explore_start_time:
        return
    
    # Check if verification is needed (25 minutes of exploring)
    if (now - explore_start_time) > 1500:
        last_verified_time = getattr(player, "last_verified", 0)
        if not last_verified_time or (now - last_verified_time) > 1800:
            # User needs verification - this will be handled on next explore
            pass

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
            await update.message.reply_text(arrival_message, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        # Update progress
        await db.update_player(user_id_str, {"travel.progress": travel_progress})

async def _handle_mission_items(update, context, db, player):
    """Handle mission item drops in background"""
    try:
        active_missions = [m for m in player.missions if m["status"] == "in_progress"]
        if not active_missions:
            return
        
        mission_item_drops = await check_mission_item_drops(player)
        
        for item_drop in mission_item_drops:
            success, msg = await add_mission_item(db, player, item_drop["key"])
            if success and msg:
                try:
                    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
                    
    except Exception as e:
        logger.error(f"Mission items error: {e}")

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


@maintenance_protected
@ban_protected
async def open_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /open command to show the keyboard for exploring."""
    
    if not update.effective_chat or update.effective_chat.type != "private":
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