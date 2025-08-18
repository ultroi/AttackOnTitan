
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from database.characters import get_character_data, CHARACTERS
from database.models import TeamMember
from html import escape
from datetime import datetime, timezone
import logging
import asyncio
from game.battle_system import active_battles
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected

logger = logging.getLogger(__name__)

# --- Constants for rewards and channel IDs ---
STARTER_REWARDS = {
    "gas": 10000,
    "crystal": 10,
    "valor": 250,
    "marks": 12500,
    "explore_count": 0
}
REFERRAL_REWARDS = {"marks": 25000, "valor": 25, "crystal": 2}
REFERRER_REWARDS = {"valor": 40}
LOG_CHANNEL_ID = -1002873117075

# Allowed values for validation
ALLOWED_LOCATIONS = ["Shiganshina", "Karanes", "Trost", "Orvud"]
ALLOWED_CHARACTERS = set(CHARACTERS)

@maintenance_protected
@ban_protected
async def start_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id) if update.effective_user else None
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)

    # If journey already started, show same message in both PM and group
    if player:
        await update.message.reply_text(
            "You have already started your journey!\n"
            "Use /explore to continue your adventure."
        )
        return

    # Only allow in private chat; in group, show PM redirect button
    if update.effective_chat.type != "private":
        keyboard = [[InlineKeyboardButton("Start Your Journey (PM)", url=f"https://t.me/{context.bot.username}?start=start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "To begin your journey, please start the bot in a private chat (PM).\n\n"
            "Tap the button below to continue.",
            reply_markup=reply_markup
        )
        return

    # Step 0: Prevent bypass if hCaptcha is pending
    if hasattr(context, 'user_data') and context.user_data is not None:
        if context.user_data.get('hcaptcha_pending'):
            await update.message.reply_text(
                "⚠️ Please complete the hCaptcha verification before continuing."
            )
            return

    # Step 1: Extract and preserve referral code
    referral_code = None
    if hasattr(update, 'message') and update.message and update.message.text:
        parts = update.message.text.strip().split()
        if len(parts) > 1:
            # Check both formats: "referral_CODE" and just "CODE"
            if parts[1].startswith('referral_'):
                referral_code = parts[1][len('referral_'):]
            else:
                referral_code = parts[1]  # Accept direct code format too
            logger.info(f"Detected referral code: {referral_code} for user {user_id}")

    # Step 2: FULL MEMORY CLEANUP (except referral and hcaptcha_pending)
    if hasattr(context, 'user_data') and context.user_data is not None:
        hcaptcha_pending = context.user_data.get('hcaptcha_pending')
        # Save any existing referral code before clearing
        existing_referral = context.user_data.get('referred_by')
        # Use newly detected referral or keep existing one
        final_referral = referral_code or existing_referral
        
        context.user_data.clear()  # Clear all first
        
        if final_referral:
            context.user_data['referred_by'] = final_referral  # Restore referral
            logger.info(f"Saved referral code {final_referral} to context for user {user_id}")
        if hcaptcha_pending:
            context.user_data['hcaptcha_pending'] = hcaptcha_pending

    # Step 3: Force cleanup battles/timeouts
    if user_id:
        # Clean active battles
        try:
            from game.battle_system import active_battles
            if user_id in active_battles:
                battle = active_battles.pop(user_id)
                if hasattr(battle, 'dispose'):
                    battle.dispose()
        except Exception as e:
            logger.error(f"Battle cleanup error: {e}")

        # Cancel titan timeouts ONLY if hCaptcha is not pending
        timeout_key = f"titan_timeouts_{user_id}"
        if not (hasattr(context, 'user_data') and context.user_data.get('hcaptcha_pending')):
            if timeout_key in context.bot_data:
                for task in context.bot_data[timeout_key]:
                    task.cancel()
                del context.bot_data[timeout_key]

    # Step 5: Send welcome message
    keyboard = [[InlineKeyboardButton("Start Your Journey", callback_data="start_journey")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        "<b>Welcome to AOT World!</b>\n\n"
        "In this world, humanity fights for survival against the Titans. "
        "Your journey begins now, as you choose your path and character.\n\n"
        "<i>Are you ready to join the fight?</i>"
    )
    
    # Log the referral context before sending welcome message
    if hasattr(context, 'user_data') and context.user_data is not None:
        referral_code = context.user_data.get('referred_by')
        logger.info(f"Before sending welcome: User {user_id} has referral code: {referral_code}")
    
    try:
        await update.message.reply_photo(
            photo="https://i.ibb.co/tpg301Z/image.jpg",  # Fixed URL
            caption=welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error sending welcome photo: {e}")
        # Fallback to text message if photo fails
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )

async def show_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Do NOT send a new photo here; only edit the existing message (text or caption)
    query = update.callback_query
    if not query:
        logger.error("show_character_selection called with no query")
        return
    await query.answer()
    keyboard = [[InlineKeyboardButton(char_name, callback_data=f"select_{char_name}")] for char_name in CHARACTERS]
    reply_markup = InlineKeyboardMarkup(keyboard)
    selection_text = (
        "Choose your character:\n\n"
        "Each character has unique abilities and playstyles. "
        "Select carefully, as this choice will shape your journey."
    )
    # Use edit_message_caption if the original message is a photo with a caption
    if query.message and hasattr(query.message, "photo") and getattr(query.message, "photo", None):
        await query.edit_message_caption(caption=selection_text, reply_markup=reply_markup)
    else:
        await query.edit_message_text(selection_text, reply_markup=reply_markup)

async def show_character_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        logger.error("show_character_details called with no query")
        return
    await query.answer()
    if not query.data:
        await query.edit_message_text("Error: No character selected.")
        return
    char_name = query.data.replace("select_", "")
    char_info = get_character_data(char_name)
    if not char_info:
        await query.edit_message_text("Error: Character data not found.")
        return
    details_text = (
        f"<b>{escape(char_name)}</b>\n"
        f"<i>{escape(char_info.quote)}</i>\n\n"
        f"<b>Role:</b> {escape(char_info.role)}\n"
        f"<b>Combat Archetype:</b> {escape(char_info.archetype)}\n"
        f"<b>Core Trait:</b> {escape(char_info.core_trait)}\n\n"
        f"<b>Base Stats:</b>\n" + "\n".join(f"{stat}: {value}" for stat, value in char_info.base_stats.dict().items())
    )
    keyboard = [
        [InlineKeyboardButton("Select This Character", callback_data=f"confirm_{char_name}")],
        [InlineKeyboardButton("Back to Selection", callback_data="back_to_selection")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Character-specific images
    char_images = {
        "Hitch Dreyse": "https://i.ibb.co/BM7pq4z/image.jpg",
        "Mina Carolina": "https://i.ibb.co/wZN4Zwvd/image.jpg",
        "Daz": "https://i.ibb.co/B5sPkmZJ/image.jpg"
    }
    if char_name in char_images:
        # Try to edit the existing message's photo/caption first
        try:
            if query.message and getattr(query.message, "photo", None):
                from telegram import InputMediaPhoto
                await query.edit_message_media(
                    media=InputMediaPhoto(media=char_images[char_name], caption=details_text, parse_mode=ParseMode.HTML),
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(details_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            logger.warning(f"Failed to edit message for character image: {e}")
            # If editing fails, delete and send a new photo
            try:
                if query.message and getattr(query.message, "photo", None):
                    await context.bot.delete_message(chat_id=query.message.chat.id, message_id=query.message.message_id)
            except Exception as del_err:
                logger.error(f"Failed to delete previous photo message: {del_err}")
            if query.message and getattr(query.message, "chat", None):
                await context.bot.send_photo(
                    chat_id=query.message.chat.id,
                    photo=char_images[char_name],
                    caption=details_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
            else:
                logger.error("Cannot send photo: query.message or query.message.chat is None")
    else:
        # If the original message is a photo, edit the caption; else, edit the text
        if query.message and getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=details_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(details_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def back_to_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_character_selection(update, context)

async def confirm_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not hasattr(query, "data") or query.data is None:
        logger.error("confirm_character_selection called with no callback query or data")
        return
    await query.answer()
    data_parts = query.data.split("_", 1)
    if len(data_parts) < 2 or not data_parts[1]:
        logger.error(f"Malformed callback data for confirm_character_selection: {query.data}")
        await query.edit_message_text("Error: Invalid character selection. Please try again.")
        return
    char_name = data_parts[1]
    if not hasattr(context, "user_data") or context.user_data is None:
        logger.error("context.user_data is not available")
        await query.edit_message_text("Error: Internal context error.")
        return
    context.user_data['selected_character'] = char_name
    if not update.effective_user or not hasattr(update.effective_user, "id"):
        logger.error("confirm_character_selection called with no effective_user or id")
        await query.edit_message_text("Error: Could not identify user.")
        return
    user_id = str(update.effective_user.id)
    logger.info(f"User {user_id} selected character: {char_name}")
    birthplaces = ["Shiganshina", "Karanes", "Trost", "Orvud"]
    # Arrange buttons 2 per row
    keyboard = [
        [InlineKeyboardButton(place, callback_data=f"location_{place}") for place in birthplaces[i:i+2]]
        for i in range(0, len(birthplaces), 2)
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    location_image_url = "https://i.ibb.co/BV70bWdr/image.jpg"
    location_caption = "Choose your starting location:"
    # Try to edit the existing message first
    try:
        if query.message and getattr(query.message, "photo", None):
            await query.edit_message_media(
                media=InputMediaPhoto(media=location_image_url, caption=location_caption),
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                location_caption,
                reply_markup=reply_markup
            )
        return
    except Exception as edit_err:
        logger.warning(f"Could not edit message for location selection: {edit_err}")
    # If editing fails, delete the previous message and send a new photo
    try:
        if query.message and getattr(query.message, "photo", None):
            await context.bot.delete_message(chat_id=query.message.chat.id, message_id=query.message.message_id)
        await context.bot.send_photo(
            chat_id=query.message.chat.id,
            photo=location_image_url,
            caption=location_caption,
            reply_markup=reply_markup
        )
    except Exception as send_err:
        logger.error(f"Error sending location selection image: {send_err}")
        # Fallback to editing the message text if sending photo fails
        try:
            await query.edit_message_text(
                location_caption,
                reply_markup=reply_markup
            )
        except Exception as edit_err2:
            logger.error(f"Error editing message for location selection: {edit_err2}")

async def create_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # --- Prevent button spam: lock per user ---
    if hasattr(context, "user_data"):
        if not hasattr(context, "user_data") or context.user_data is None:
            logger.error("context.user_data is not available")
            if hasattr(update, "callback_query") and update.callback_query:
                query = update.callback_query
                await _safe_edit_message(query, "Error: Internal context error.")
            return
        if context.user_data.get("processing_character_creation"):
            if hasattr(update, "callback_query") and update.callback_query:
                query = update.callback_query
                await _safe_edit_message(query, "⏳ Please wait, your character is being created. Don't spam the button.")
            logger.warning(f"User {getattr(update.effective_user, 'id', None)} tried to spam character creation.")
            return
        context.user_data["processing_character_creation"] = True
    try:
        query = getattr(update, "callback_query", None)
        if not query or not hasattr(query, "data") or query.data is None:
            logger.error("create_character called with no callback_query.data")
            if query and hasattr(query, "message") and query.message and getattr(query.message, "photo", None):
                await query.edit_message_caption("Error: Invalid callback data.")
            elif query and hasattr(query, "edit_message_text"):
                await query.edit_message_text("Error: Invalid callback data.")
            return
        if not update.effective_user or not hasattr(update.effective_user, "id"):
            logger.error("create_character called with no effective_user or id")
            if query and hasattr(query, "message") and query.message and getattr(query.message, "photo", None):
                await query.edit_message_caption("Error: Could not identify user.")
            elif query and hasattr(query, "edit_message_text"):
                await query.edit_message_text("Error: Could not identify user.")
            return
        if hasattr(query, "answer") and callable(query.answer):
            await query.answer()
        data_parts = query.data.split("_", 1)
        if len(data_parts) < 2:
            await _safe_edit_message(query, "Error: Invalid location selection.")
            return
        location = data_parts[1]
        # --- Input validation for location ---
        if location not in ALLOWED_LOCATIONS:
            await _safe_edit_message(query, "Error: Invalid location selected.")
            logger.warning(f"User {getattr(update.effective_user, 'id', None)} tried invalid location: {location}")
            return
        user_id = str(update.effective_user.id)
        username = getattr(update.effective_user, "username", None) or f"user_{user_id}"
        name = getattr(update.effective_user, "first_name", None) or f"Player {user_id}"
        if not hasattr(context, "user_data") or context.user_data is None:
            logger.error("context.user_data is not available")
            await _safe_edit_message(query, "Error: Internal context error.")
            return
        selected_character = context.user_data.get('selected_character')
        # --- Input validation for character ---
        if not selected_character or selected_character not in ALLOWED_CHARACTERS:
            logger.warning(f"User {user_id} tried invalid character: {selected_character}")
            await _safe_edit_message(query, "Error: Invalid character selected.")
            return
        db = context.bot_data.get("db") or Database()
        char_data = get_character_data(selected_character)
        if not char_data:
            await _safe_edit_message(query, "Error: Character data not found.")
            return      ...
        try:
            player = await db.get_player(user_id)
            referred_by = None
            if hasattr(context, 'user_data') and context.user_data is not None:
                referred_by = context.user_data.get('referred_by')
                logger.info(f"Creating character: User {user_id} has referral code in context: {referred_by}")
            
            ref_player = None
            is_new_player = False
            
            # --- Validate referral code ---
            if referred_by and referred_by == user_id:
                logger.warning(f"User {user_id} tried to refer themselves.")
                referred_by = None
            
            if not player:
                is_new_player = True
                try:
                    if referred_by:
                        logger.info(f"Creating player with referral: user={user_id}, referrer={referred_by}")
                        # Convert user_id to int for DB compatibility
                        player = await db.create_player(int(user_id), username, name, referred_by=referred_by)
                    else:
                        logger.info(f"Creating player without referral: user={user_id}")
                        player = await db.create_player(int(user_id), username, name)
                except Exception as create_err:
                    logger.error(f"Failed to create player: {create_err}")
                    await _safe_edit_message(query, "An error occurred while creating your player. Please try again.")
                    return
            existing_char = await db.get_character(int(user_id), selected_character)
            if existing_char:
                # Character already exists, just show the welcome message again (idempotent)
                # Use actual player/character values instead of undefined starter_rewards
                player_gas = getattr(player, 'gas') if player else 0
                player_crystal = getattr(player, 'crystal') if player else 0
                player_valor = getattr(player, 'valor') if player else 0
                player_marks = getattr(player, 'marks') if player else 0
                reward_lines = [
                    f"• 🔋 <b>Gas:</b> <code>{player_gas}</code>",
                    f"• 🔮 <b>Titan Crystals:</b> <code>{player_crystal}</code>",
                    f"• 🏅 <b>Valor Points:</b> <code>{player_valor}</code>",
                    f"• 🎯 <b>Marks:</b> <code>{player_marks}</code>"
                ]
                reward_text = "\n".join(reward_lines)
                reward_note = "<b>Starter Rewards Unlocked!</b>\n"
                if referred_by and ref_player:
                    reward_note += (
                        "<b>Referral Bonus:</b> +25,000 Marks, +25 Valor, +2 Titan Crystals\n"
                    )
                else:
                    reward_note += "(No referral bonus applied)\n"
                welcome_text = (
                    f"👋 <b>Welcome, {escape(name)}!</b>\n\n"
                    f"Your journey begins in <b>{location}</b> as <b>{selected_character}</b>.\n\n"
                    f"{reward_note}"
                    f"<b>Initial Resources:</b>\n{reward_text}\n\n"
                    "Use /inv to view your resources details and /explore to start your adventure!"
                )
                try:
                    is_photo = query.message and getattr(query.message, "photo", None)
                    current_text = getattr(query.message, "caption", None) if is_photo else getattr(query.message, "text", None) if query.message else None
                    if current_text != welcome_text:
                        if is_photo:
                            await query.edit_message_caption(welcome_text, parse_mode=ParseMode.HTML)
                        else:
                            await query.edit_message_text(welcome_text, parse_mode=ParseMode.HTML)
                except Exception as edit_err:
                    logger.error(f"Error editing welcome message: {edit_err}")
                return
            current_hp = char_data.get_max_hp(1)
            try:
                # Add DB-level duplicate check for character creation
                await db.create_character(str(user_id), selected_character, selected_character, current_hp=current_hp)
            except Exception as char_err:
                # Handle duplicate character creation gracefully
                if 'duplicate' in str(char_err).lower() or 'already exists' in str(char_err).lower():
                    logger.warning(f"Duplicate character creation attempt for user {user_id}, character {selected_character}")
                    # Fetch the character again and proceed as idempotent
                    existing_char = await db.get_character(int(user_id), selected_character)
                    # ...existing idempotent welcome message logic...
                    return
                logger.error(f"Character creation failed for user {user_id}: {char_err}")
                # Cleanup: If player was just created, delete it to avoid partial data
                if is_new_player:
                    try:
                        # Make sure db is initialized and players collection exists
                        if not db.players:
                            await db.init_db()
                        if db.players:
                            await db.players.delete_one({"user_id": str(user_id)})
                        else:
                            logger.error(f"Failed to delete player: db.players is None")
                    except Exception as del_err:
                        logger.error(f"Failed to cleanup player after character creation error: {del_err}")
                if query.message and getattr(query.message, "photo", None):
                    await query.edit_message_caption("An error occurred while creating your character. Please try again.")
                else:
                    await query.edit_message_text("An error occurred while creating your character. Please try again.")
                return
            # Set player location to selected location and give starter rewards only if new
            starter_rewards = None
            if is_new_player:
                starter_rewards = STARTER_REWARDS.copy()
                extra_data = {
                    "team": [TeamMember(character_name=selected_character, position=1).dict()],
                    "location": location,
                    "updated_at": datetime.now(timezone.utc)
                }
                if referred_by:
                    try:
                        # Make sure DB is initialized
                        if not db.players:
                            logger.info("DB not initialized, initializing now...")
                            await db.init_db()
                            
                        # Now check if DB initialization was successful
                        if not db.players:
                            logger.error("DB initialization failed, can't process referral")
                            ref_player = None
                        else:
                            logger.info(f"Looking up referrer with code: {referred_by}")
                            ref_player = await db.players.find_one({"referral_code": referred_by})
                            logger.info(f"Referral lookup result: {ref_player is not None}")
                        
                        if ref_player and str(ref_player.get('user_id')) != user_id:
                            for k, v in REFERRAL_REWARDS.items():
                                starter_rewards[k] = starter_rewards.get(k, 0) + v
                            
                            # Update referrer if db was initialized
                            if db.players:
                                await db.players.update_one(
                                    {"referral_code": referred_by}, 
                                    {"$inc": {"referral_count": 1, "valor": REFERRER_REWARDS["valor"]}}
                                )
                            # Notify referrer
                            try:
                                bot = context.bot if hasattr(context, 'bot') else None
                                if bot:
                                    ref_user_id = str(ref_player.get('user_id') or ref_player.get('_id') or "")
                                    if ref_user_id:
                                        ref_message = (
                                            f"🎉 <b>Referral Reward!</b>\n\n"
                                            f"You received <b>+40 Valor</b> because a new player joined using your referral link!\n"
                                            f"Keep sharing to earn more rewards."
                                        )
                                        await bot.send_message(chat_id=ref_user_id, text=ref_message, parse_mode=ParseMode.HTML)
                            except Exception as notify_err:
                                logger.error(f"Failed to notify referrer {referred_by}: {notify_err}")
                        else:
                            logger.warning(f"Invalid or self-referral code used: {referred_by}")
                            referred_by = None
                    except Exception as ref_db_err:
                        logger.error(f"Referral DB update failed: {ref_db_err}")
                try:
                    # Merge int rewards and extra data for player update
                    player_update = {**starter_rewards, **extra_data}
                    await db.update_player(int(user_id), player_update)
                except Exception as update_err:
                    logger.error(f"Failed to update player with starter rewards: {update_err}")
                    # Rollback: delete player and character if possible
                    try:
                        await db.delete_player(user_id)
                        # Delete character doesn't exist in Database class - fix this
                        try:
                            # Ensure DB is initialized
                            if not db.characters:
                                logger.info("DB characters collection not initialized, initializing DB...")
                                await db.init_db()
                                
                            if db.characters:
                                await db.characters.delete_one({"user_id": str(user_id), "name": selected_character})
                                logger.info(f"Deleted character {selected_character} for user {user_id}")
                            else:
                                logger.error("Failed to initialize DB characters collection")
                        except Exception as del_char_err:
                            logger.error(f"Failed to delete character: {del_char_err}")
                    except Exception as rollback_err:
                        logger.error(f"Rollback failed: {rollback_err}")
                    await _safe_edit_message(query, "An error occurred while assigning your starter rewards. Please try again.")
                    return
            # Prepare reward summary for welcome message
            reward_lines = []
            if is_new_player and starter_rewards:
                reward_lines = [
                    f"• 🔋 <b>Gas:</b> <code>{starter_rewards['gas']}</code>",
                    f"• 🔮 <b>Titan Crystals:</b> <code>{starter_rewards['crystal']}</code>",
                    f"• 🏅 <b>Valor Points:</b> <code>{starter_rewards['valor']}</code>",
                    f"• 🎯 <b>Marks:</b> <code>{starter_rewards['marks']}</code>"
                ]
            else:
                player_gas = getattr(player, 'gas', 0) if player else 0
                player_crystal = getattr(player, 'crystal', 0) if player else 0
                player_valor = getattr(player, 'valor', 0) if player else 0
                player_marks = getattr(player, 'marks', 0) if player else 0
                reward_lines = [
                    f"• 🔋 <b>Gas:</b> <code>{player_gas}</code>",
                    f"• 🔮 <b>Titan Crystals:</b> <code>{player_crystal}</code>",
                    f"• 🏅 <b>Valor Points:</b> <code>{player_valor}</code>",
                    f"• 🎯 <b>Marks:</b> <code>{player_marks}</code>"
                ]
            reward_text = "\n".join(reward_lines)
            reward_note = "<b>Starter Rewards Unlocked!</b>\n"
            if referred_by and ref_player:
                reward_note += (
                    "<b>Referral Bonus:</b> +25,000 Marks, +25 Valor, +2 Titan Crystals\n"
                )
            else:
                reward_note += "(No referral bonus applied)\n"
            welcome_text = (
                f"👋 <b>Welcome, {escape(name)}!</b>\n\n"
                f"Your journey begins in <b>{location}</b> as <b>{selected_character}</b>.\n\n"
                f"{reward_note}"
                f"<b>Initial Resources:</b>\n{reward_text}\n\n"
                "Use /inv to view your resources details and /explore to start your adventure!"
            )
            # Only edit the message if the content is different to avoid Telegram 'message is not modified' error
            try:
                is_photo = query.message and getattr(query.message, "photo", None)
                current_text = query.message.caption if is_photo else query.message.text if query.message else None
                image_url = "https://i.ibb.co/tpg301ZQ/image.jpg"
                if is_photo:
                    if current_text != welcome_text:
                        # Always update the image and caption to ensure the correct image is shown
                        await query.edit_message_media(
                            media=InputMediaPhoto(media=image_url, caption=welcome_text, parse_mode=ParseMode.HTML)
                        )
                else:
                    # If not a photo, send a new photo with the welcome message and delete the old message
                    if query.message:
                        try:
                            await context.bot.delete_message(chat_id=query.message.chat.id, message_id=query.message.message_id)
                        except Exception as del_err:
                            logger.warning(f"Failed to delete previous message: {del_err}")
                    await context.bot.send_photo(
                        chat_id=query.message.chat.id,
                        photo=image_url,
                        caption=welcome_text,
                        parse_mode=ParseMode.HTML
                    )
                # After sending the welcome message, send a new message inviting to join the main group chat
                try:
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text="<b>Your journey has just begun!\n\nJoin our main group chat for helpful tips, latest updates, and a vibrant community experience!\n\n👉 <a href='https://t.me/AOTMainChat'>AOT Main Chat</a></b>",
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
                except Exception as group_msg_err:
                    logger.error(f"Failed to send main group chat invite: {group_msg_err}")
            except Exception as edit_err:
                logger.error(f"Error editing welcome message: {edit_err}")
            # Send log to channel for new user
            try:
                log_channel_id = -1002873117075
                ref_display = referred_by if referred_by else "None"
                user_link = f"<a href='tg://user?id={user_id}'>{escape(name)}</a>"
                log_text = (
                    "<b>#NewUser</b>\n\n"
                    f"<b>Name:</b> {user_link}\n"
                    f"<b>ID:</b> <code>{user_id}</code>\n"
                    f"<b>Referred by:</b> <code>{ref_display}</code>"
                )
                bot = context.bot if hasattr(context, 'bot') else None
                if bot:
                    await bot.send_message(chat_id=log_channel_id, text=log_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except Exception as log_err:
                logger.error(f"Failed to log new user to channel: {log_err}")
        except Exception as e:
            logger.error(f"Error creating character for user {getattr(update.effective_user, 'id', None)}: {e}")
            # Placeholder for critical alerting (e.g., send to admin)
            # if is_critical(e): alert_admin(e)
            if query.message and getattr(query.message, "photo", None):
                await query.edit_message_caption("An error occurred while creating your character. Please try again.")
            else:
                await query.edit_message_text("An error occurred while creating your character. Please try again.")
    finally:
        # --- Always clear the lock ---
        if hasattr(context, "user_data"):
            context.user_data["processing_character_creation"] = False

def _safe_edit_message(query, text):
    """Helper to safely edit a Telegram message caption or text."""
    async def inner():
        try:
            is_photo = query.message and getattr(query.message, "photo", None)
            if is_photo:
                await query.edit_message_caption(text, parse_mode=ParseMode.HTML)
            else:
                await query.edit_message_text(text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")
    return asyncio.create_task(inner())

