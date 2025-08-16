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
PREGEN_POOL_SIZE = 5  # Increased from 3 to 5

# Template storage for fast titan generation
TITAN_TEMPLATES = {}

# Initialize titan pools at startup
async def initialize_titan_pools(db=None):
    """Pre-generate common titan types for faster access"""
    from database.models import Titan, generate_titan_name, generate_titan_hp, generate_titan_xp
    from datetime import datetime, timezone
    
    # Generate common titan templates for different levels
    common_levels = [1, 5, 10, 15, 20, 25]
    difficulties = ["Easy", "Normal", "Hard"]
    template_titans = {}
    
    # Create common titan templates
    for level in common_levels:
        for difficulty in difficulties:
            key = f"{level}_{difficulty}"
            name = generate_titan_name(difficulty)
            max_hp = generate_titan_hp(level, difficulty)
            now = datetime.now(timezone.utc)
            
            template_titans[key] = Titan(
                name=name,
                level=level,
                max_hp=max_hp,
                abilities=[],
                created_at=now,
                difficulty=difficulty,
                spawn_areas=[],
                drop_table={},
                xp_reward=generate_titan_xp(level, difficulty),
                min_level_requirement=level
            )
    
    # Store templates for fast cloning
    global TITAN_TEMPLATES
    TITAN_TEMPLATES = template_titans
    logger.info(f"Initialized {len(template_titans)} titan templates")

async def get_pregenerated_titan(user_id_str, db, player_character, unlocked_areas):
    """Ultra-fast titan generation using templates and caching."""
    # First check if we have a pre-generated titan in the user pool
    pool = PREGENERATED_TITANS.get(user_id_str, [])
    if pool:
        titan = pool.pop(0)
        PREGENERATED_TITANS[user_id_str] = pool
        # Refill pool in background without awaiting
        if len(pool) < PREGEN_POOL_SIZE:
            asyncio.create_task(refill_titan_pool(user_id_str, db, player_character, unlocked_areas))
        return titan
    
    # No pre-generated titans, use templates for instant creation
    from database.models import Titan, generate_titan_name
    from copy import deepcopy
    from datetime import datetime, timezone
    
    # Determine level and difficulty
    player_level = player_character.level
    if player_level < 8:
        difficulty = "Easy"
    elif player_level < 15:
        difficulty = "Normal"
    else:
        difficulty = "Hard"
    
    level = max(1, player_level + random.randint(-2, 2))
    
    # Find closest template
    template_key = None
    closest_level = 0
    
    # Round to nearest 5
    template_level = 5 * round(level / 5)
    if template_level < 1:
        template_level = 1
    elif template_level > 25:
        template_level = 25
    
    template_key = f"{template_level}_{difficulty}"
    
    # Use template if available
    if template_key in TITAN_TEMPLATES:
        # Clone the template
        titan = deepcopy(TITAN_TEMPLATES[template_key])
        # Update a few fields
        titan.name = generate_titan_name(difficulty)  # New name for uniqueness
        titan.created_at = datetime.now(timezone.utc)
        titan.spawn_areas = unlocked_areas or []
        
        # Adjust level slightly if needed
        if titan.level != level:
            titan.level = level
    else:
        # Fast titan generation as fallback
        from database.models import generate_titan_hp, generate_titan_xp
        
        name = generate_titan_name(difficulty)
        max_hp = generate_titan_hp(level, difficulty)
        now = datetime.now(timezone.utc)
        
        titan = Titan(
            name=name,
            level=level,
            max_hp=max_hp,
            abilities=[],
            created_at=now,
            difficulty=difficulty,
            spawn_areas=unlocked_areas or [],
            drop_table={},
            xp_reward=generate_titan_xp(level, difficulty),
            min_level_requirement=level
        )
    
    # Start refill in background without waiting
    asyncio.create_task(refill_titan_pool(user_id_str, db, player_character, unlocked_areas))
    return titan

async def refill_titan_pool(user_id_str, db, player_character, unlocked_areas):
    """Fast titan generation for the pool"""
    from database.models import Titan, generate_titan_name, generate_titan_hp, generate_titan_xp
    from datetime import datetime, timezone
    
    pool = PREGENERATED_TITANS.get(user_id_str, [])
    player_level = player_character.level
    
    # Generate all titans at once for efficiency
    needed_titans = PREGEN_POOL_SIZE - len(pool)
    if needed_titans <= 0:
        return
        
    new_titans = []
    for _ in range(needed_titans):
        # Quick difficulty calculation
        if player_level < 8:
            difficulty = "Easy"
        elif player_level < 15:
            difficulty = "Normal"
        else:
            difficulty = "Hard"
            
        # Fast titan generation
        level = max(1, player_level + random.randint(-2, 2))
        name = generate_titan_name(difficulty)
        max_hp = generate_titan_hp(level, difficulty)
        now = datetime.now(timezone.utc)
        
        titan = Titan(
            name=name,
            level=level,
            max_hp=max_hp,
            abilities=[],
            created_at=now,
            difficulty=difficulty,
            spawn_areas=unlocked_areas or [],
            drop_table={},
            xp_reward=generate_titan_xp(level, difficulty),
            min_level_requirement=level
        )
        new_titans.append(titan)
    
    # Update pool in one go
    pool.extend(new_titans)
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

    # Fast check for active battle
    try:
        from game.battle_system import active_battles
        if user_id_str in active_battles:
            first_name = update.effective_user.first_name or "Player"
            await _reply_error(update, f"{first_name} is currently battling !!")
            return
    except ImportError:
        pass

    # Get database reference
    db = context.bot_data.get("db")
    if db is None:
        logger.error("Database not initialized in context.bot_data")
        await _reply_error(update, "Internal error: Database not initialized.")
        return
        
    # Show persistent keyboard only the first time - moved down after active battle check
    if context.user_data is not None and not context.user_data.get("persistent_keyboard_sent"):
        context.user_data["persistent_keyboard_sent"] = True
        keyboard = [["/explore", "/close"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        
        # Non-blocking, start in background
        if update.message:
            asyncio.create_task(update.message.reply_text("Opening keyboard...", reply_markup=reply_markup))

    # Use projection to get only the needed fields for faster database access
    player = await db.players.find_one(
        {"user_id": user_id_str}, 
        {
            "team": 1, 
            "unlocked_areas": 1, 
            "location": 1,
            "last_explore_time": 1,
            "hcaptcha_verified": 1
        }
    )
    
    if not player:
        if update.message:
            await update.message.reply_text("You need to create a profile first with /start")
        return
    
    # --- SPAM PROTECTION ---
    if "explore_spam_count" not in context.bot_data:
        context.bot_data["explore_spam_count"] = {}
    spam_count = context.bot_data["explore_spam_count"].get(user_id_str, 0)

    # If not in battle, increment spam count
    spam_count += 1
    context.bot_data["explore_spam_count"][user_id_str] = spam_count

    # Warn at 15 explores
    if spam_count == 15:
        if update.message:
            await update.message.reply_text("⚠️ Warning: Don't Spam, you will be banned.")

    # Ban at 20 explores
    if spam_count >= 20:
        # Schedule ban in background properly
        async def ban_spammer_bg():
            try:
                await ban_spammer(user_id, update, context)
            except Exception:
                pass
                
        # Now create the task
        asyncio.create_task(ban_spammer_bg())
        return

    now = time.time()
    last_explore = player.get("last_explore_time")

    # Handle captcha checks properly
    if "hcaptcha_prompted" in context.user_data or (last_explore and now - last_explore > 1500):
        # Create a properly wrapped coroutine
        async def check_captcha_bg():
            try:
                await handle_captcha_check(user_id_str, player, update, context, now)
            except Exception:
                pass
        
        # Now create the task
        asyncio.create_task(check_captcha_bg())
    
    # Update last explore time in background - create a proper coroutine first
    async def update_last_explore():
        try:
            await db.players.update_one(
                {"user_id": user_id_str},
                {"$set": {"last_explore_time": now}}
            )
        except Exception:
            pass
    
    # Now create the task with a proper coroutine
    asyncio.create_task(update_last_explore())

    # Handle travel/decision points
    location = player.get("location")
    if location and location in TRAVEL_MAP and location.startswith("Decision_"):
        # Create a properly wrapped coroutine
        async def handle_decision_bg():
            try:
                await handle_decision_point(user_id, location, update, context)
            except Exception:
                pass
                
        # Now create the task
        asyncio.create_task(handle_decision_bg())
        return

    # CAPTCHA trigger moved to a lower chance and run properly in background
    if random.random() < 0.03:  # Reduced from 6% to 3%
        # Create a properly wrapped coroutine
        async def spawn_captcha_bg():
            try:
                await spawn_captcha_background(update, context, user_id)
            except Exception:
                pass
                
        # Now create the task
        asyncio.create_task(spawn_captcha_bg())
        return

    # Get character data for titan encounter
    if not player.get("team"):
        await _reply_error(update, "You don't have any characters in your team!")
        return
        
    player_character_name = player["team"][0]["character_name"]
    
    # Fast character check with minimal fields
    player_character = await db.characters.find_one(
        {"user_id": user_id_str, "name": player_character_name},
        {"gas": 1, "level": 1}
    )
    
    if not player_character:
        await _reply_error(update, f"Error: Your character {player_character_name} was not found.")
        return

    if player_character["gas"] < 100:
        await _reply_error(update, f"{player_character_name} doesn't have enough gas to explore (needs at least 100). Use /char {player_character_name} to refill gas.")
        return

    # Convert to Character object for consistency with the rest of the code
    from database.models import Character
    player_character = Character(**player_character)
    
    # Generate titan
    titan = await get_pregenerated_titan(user_id_str, db, player_character, player.get("unlocked_areas", []))
    if not titan:
        await _reply_error(update, "No titans found in your level range.")
        return

    # Store titan in database in background
    async def store_titan_bg():
        try:
            await db.store_titan(user_id_str, titan)
        except Exception:
            pass
    
    # Now create the task with a proper coroutine
    asyncio.create_task(store_titan_bg())

    # Generate battle ID
    battle_id = f"battle_{user_id}_{uuid4().hex[:8]}"  # Using shorter UUID for speed
    context.bot_data[f"active_battle_id_{user_id}"] = battle_id

    # Create reply markup
    keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=battle_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Get titan image faster with simplified check
    titan_image_url = None
    titan_name_lower = titan.name.lower()
    for titan_type, url in TITAN_TYPE_IMAGE_URLS.items():
        if titan_type.lower() in titan_name_lower:
            titan_image_url = url
            break

    # Generate reply text
    image_embed = f'<a href="{titan_image_url}">!</a>' if titan_image_url else ""
    reply_text = (
        f"<code>-------------------------</code>\n"
        f"📍 <b>{titan.name} Lvl ({titan.level})</b>\n"
        f"<b>has blocked your way{image_embed}</b>\n"
        f"<code>-------------------------</code>\n"
    )

    # Send response immediately
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
            except Exception:
                sent_message = await update.callback_query.message.chat.send_message(
                    text=reply_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
    except Exception as e:
        await _reply_error(update, "An error occurred while displaying the titan.")
        return

    # Start all cleanup tasks in background - don't block the response
    if sent_message:
        key = f"titan_timeouts_{user_id}"
        if key not in context.bot_data:
            context.bot_data[key] = []
        timeout_task = asyncio.create_task(titan_encounter_timeout(user_id, context, sent_message))
        context.bot_data[key].append(timeout_task)
        
        # Track player action if needed, but don't wait for it
        try:
            from utils.monitor import track_player_action
            # Check if it's actually a coroutine function first
            if asyncio.iscoroutinefunction(track_player_action):
                # Create a wrapped coroutine to handle the tracking
                async def track_exploration():
                    try:
                        await track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
                    except Exception:
                        pass
                
                # Now create the task with the properly wrapped coroutine
                asyncio.create_task(track_exploration())
            else:
                # If it's not a coroutine, just call it directly
                try:
                    track_player_action(user_id, username, "🗺️ Exploring", {"action": "looking_for_titans"})
                except Exception:
                    pass
        except Exception:
            pass

# Helper functions to run tasks in background

async def ban_spammer(user_id, update, context):
    """Handle banning a spammer in the background"""
    try:
        db = context.bot_data.get("db")
        user_id_str = str(user_id)
        
        # Reset spam counter
        if "explore_spam_count" in context.bot_data:
            context.bot_data["explore_spam_count"][user_id_str] = 0
            
        # Ban the user
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
            
        # Log the ban to admin group
        BAN_LOG_CHAT_ID = -1002873117075
        bot_username = "Bot"
        try:
            bot_username = (await context.bot.get_me()).username
        except Exception:
            pass
            
        msg = (
            f"<b>#BanEvent</b>\n\n"
            f"<b>Target</b> : <a href=\"tg://user?id={user_id}\">{update.effective_user.first_name}</a>\n"
            f"<b>Target ID</b> : <code>{user_id}</code>\n"
            f"<b>By</b> : <a href=\"tg://user?id={context.bot.id}\">{bot_username}</a>\n"
            f"<b>Reason</b> : <code>Spamming explore without battle</code>\n"
            f"<b>Time</b> : <code>24 hours</code>"
        )
        await context.bot.send_message(BAN_LOG_CHAT_ID, msg, parse_mode=ParseMode.HTML)
    except Exception:
        pass

async def handle_captcha_check(user_id_str, player, update, context, now):
    """Handle captcha verification in background"""
    try:
        if context.user_data.get("hcaptcha_prompted", False) and not player.get("hcaptcha_verified", False):
            return
            
        # Check if inactive and needs verification
        inactive = False
        last_explore = player.get("last_explore_time")
        if last_explore and (now - last_explore) > 1500:
            inactive = True
            
        if inactive and not player.get("hcaptcha_verified", False):
            if not context.user_data.get("hcaptcha_prompted", False):
                context.user_data["hcaptcha_prompted"] = True
                timestamp = int(now)
                verification_url = f"https://attackontitangamebot.onrender.com/hcaptcha?user_id={user_id_str}&ts={timestamp}"
                
                if update.message:
                    await update.message.reply_text(
                        "🔒 <b>Verification Required</b>\n\n"
                        "Complete hCaptcha to continue exploring\n",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("✅ Verify Now", url=verification_url)]
                        ]),
                        parse_mode=ParseMode.HTML,
                    )
                
                db = context.bot_data.get("db")
                if db:
                    await db.update_player(user_id_str, {"hcaptcha_start_time": timestamp})
        
        # Reset verification flag if verified
        elif player.get("hcaptcha_verified", False):
            context.user_data["hcaptcha_prompted"] = False
            db = context.bot_data.get("db")
            if db:
                await db.update_player(user_id_str, {"hcaptcha_verified": False})
    except Exception:
        pass

async def handle_decision_point(user_id, location, update, context):
    """Handle travel decision points in background"""
    try:
        directions = TRAVEL_MAP[location]
        keyboard = [
            [InlineKeyboardButton(dir, callback_data=f"travel_decision_{dir.strip().lower()}")]
            for dir in directions.keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Clear active battle if present
        try:
            from game.battle_system import active_battles
            user_id_str = str(user_id)
            if user_id_str in active_battles:
                active_battles.pop(user_id_str, None)
                
            battle_id_key = f"active_battle_id_{user_id_str}"
            if battle_id_key in context.bot_data:
                context.bot_data.pop(battle_id_key, None)
        except Exception:
            pass
            
        # Send decision point message
        if update.message:
            await update.message.reply_text(
                f"You are at a decision point: <b>{location}</b>\nChoose a direction to continue your journey:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except Exception:
        pass

async def spawn_captcha_background(update, context, user_id):
    """Handle captcha spawning in background"""
    try:
        captcha_triggered = await spawn_captcha(update, context)
        if captcha_triggered:
            try:
                from utils.monitor import remove_player_activity
                remove_player_activity(user_id)
            except Exception:
                pass
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


