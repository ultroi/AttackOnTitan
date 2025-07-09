from typing import Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from database.characters import get_character_data, CharacterData, CHARACTERS
from database.models import Character, Player, TeamMember
from html import escape
from datetime import datetime, timezone
import logging
from game.battle_system import active_battles

logger = logging.getLogger(__name__)

async def start_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Always clear user memory at the start
    if hasattr(context, 'user_data') and context.user_data is not None:
        logger.info("Clearing user data for character selection")
        context.user_data.clear()
    user_id = str(update.effective_user.id) if update.effective_user and hasattr(update.effective_user, 'id') else None
    # Remove from active_battles if present
    if user_id and user_id in active_battles:
        try:
            battle = active_battles.pop(user_id)
            if hasattr(battle, 'dispose'):
                battle.dispose()
        except Exception as e:
            logger.error(f"Error clearing battle for user {user_id}: {e}")
    if not update.message:
        logger.error("start_character_selection called with no message")
        return
    db = Database()
    if not update.effective_user or not hasattr(update.effective_user, "id"):
        logger.error("start_character_selection called with no effective_user or id")
        await update.message.reply_text("Error: Could not identify user.")
        return
    user_id = str(update.effective_user.id)
    player = await db.get_player(user_id)
    if player:
        await update.message.reply_text(
            "You have already started your journey!\n"
            "Use /explore to continue your adventure."
        )
        return
    keyboard = [[InlineKeyboardButton("Start Your Journey", callback_data="start_journey")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        "Welcome to Attack on Titan!\n\n"
        "In this world, humanity fights for survival against the Titans. "
        "Your journey begins now, as you choose your path and character.\n\n"
        "Are you ready to join the fight?"
    )
    # Send image first, then welcome text with button
    await update.message.reply_photo(
        photo="https://i.ibb.co/tpg301ZQ/image.jpg",
        caption=welcome_text,
        reply_markup=reply_markup
    )

async def show_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await query.edit_message_text(details_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def back_to_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_character_selection(update, context)

async def confirm_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not hasattr(query, "data") or query.data is None:
        logger.error("confirm_character_selection called with no callback query or data")
        return
    await query.answer()
    char_name = query.data.split("_", 1)[1] if "_" in query.data else ""
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
    birthplaces = ["Shiganshina", "Karanes", "Trost", "Krolva"]
    keyboard = [[InlineKeyboardButton(place, callback_data=f"location_{place}") for place in birthplaces]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Choose your starting location:", reply_markup=reply_markup)

async def create_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not update.callback_query:
        logger.error("create_character called with no callback_query")
        return
    query = update.callback_query

    if not hasattr(query, "data") or query.data is None:
        logger.error("create_character called with no callback_query.data")
        return

    if not update.effective_user or not hasattr(update.effective_user, "id"):
        logger.error("create_character called with no effective_user or id")
        await query.edit_message_text("Error: Could not identify user.")
        return

    await query.answer()
    data_parts = query.data.split("_", 1)
    if len(data_parts) < 2:
        await query.edit_message_text("Error: Invalid location selection.")
        return
    location = data_parts[1]

    user_id = str(update.effective_user.id)
    username = getattr(update.effective_user, "username", None) or f"user_{user_id}"
    name = getattr(update.effective_user, "first_name", None) or f"Player {user_id}"

    if not hasattr(context, "user_data") or context.user_data is None:
        logger.error("context.user_data is not available")
        await query.edit_message_text("Error: Internal context error.")
        return

    selected_character = context.user_data.get('selected_character')
    if not selected_character:
        logger.error("No selected character found in context.user_data")
        await query.edit_message_text("Error: No character selected.")
        return

    db = Database()
    char_data = get_character_data(selected_character)
    if not char_data:
        await query.edit_message_text("Error: Character data not found.")
        return
    try:
        player = await db.get_player(user_id)
        referred_by = context.user_data.get('referred_by')
        if not player:
            # Create player with referral info if available
            # Only pass allowed arguments to create_player
            if referred_by:
                player = await db.create_player(user_id, username, name, referred_by=referred_by)
            else:
                player = await db.create_player(user_id, username, name)
        existing_char = await db.get_character(user_id, selected_character)
        if existing_char:
            await query.edit_message_text(f"Error: You already have a character named {selected_character}.")
            return
        current_hp = char_data.get_max_hp(1)
        character = await db.create_character(str(user_id), selected_character, selected_character, current_hp=current_hp)
        # Set player location to selected location and give starter rewards
        starter_rewards = {
            "gas": 10000,
            "crystal": 10,
            "valor": 250,
            "marks": 12500,
            "explore_count": 0,
            "team": [TeamMember(character_name=selected_character, position=1).dict()],
            "location": location,
            "updated_at": datetime.now(timezone.utc)
        }
        # Referral starter rewards
        referral_rewards = {"marks": 25000, "valor": 25, "crystal": 2}
        referrer_rewards = {"valor": 40}
        # If referred, give new user extra rewards and update referrer
        if referred_by:
            for k, v in referral_rewards.items():
                if k in starter_rewards:
                    starter_rewards[k] += v
                else:
                    starter_rewards[k] = v
            # Update referrer: +40 Valor, +1 referral_count
            ref_player = await db.players.find_one({"referral_code": referred_by})
            if ref_player:
                await db.players.update_one({"referral_code": referred_by}, {"$inc": {"referral_count": 1, "valor": referrer_rewards["valor"]}})
                # Notify the referrer about their reward
                try:
                    from telegram import Bot
                    bot = context.bot if hasattr(context, 'bot') else None
                    if bot:
                        ref_user_id = ref_player.get('user_id') or ref_player.get('_id') or None
                        if ref_user_id:
                            ref_message = (
                                f"🎉 <b>Referral Reward!</b>\n\n"
                                f"You received <b>+40 Valor</b> because a new player joined using your referral link!\n"
                                f"Keep sharing to earn more rewards."
                            )
                            await bot.send_message(chat_id=ref_user_id, text=ref_message, parse_mode=ParseMode.HTML)
                except Exception as notify_err:
                    logger.error(f"Failed to notify referrer {referred_by}: {notify_err}")
        await db.update_player(user_id, starter_rewards)
        # Prepare reward summary for welcome message
        reward_lines = [
            f"• 🔋 <b>Gas:</b> <code>{starter_rewards['gas']}</code>",
            f"• 🔮 <b>Titan Crystals:</b> <code>{starter_rewards['crystal']}</code>",
            f"• 🏅 <b>Valor Points:</b> <code>{starter_rewards['valor']}</code>",
            f"• 🎯 <b>Marks:</b> <code>{starter_rewards['marks']}</code>"
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
            "Use /profile to view your character details and /explore to start your adventure!"
        )
        # Only edit the message if the content is different to avoid Telegram 'message is not modified' error
        try:
            current_message = query.message.text if hasattr(query, 'message') and query.message else None
            if current_message != welcome_text:
                await query.edit_message_text(welcome_text, parse_mode=ParseMode.HTML)
        except Exception as edit_err:
            logger.error(f"Error editing welcome message: {edit_err}")
        # Send log to channel for new user
        try:
            log_channel_id = -1002873117075
            ref_display = referred_by if referred_by else "None"
            user_link = f"<a href='tg://user?id={user_id}'>{escape(name)}</a>"
            log_text = (
                "<b>#New User</b>\n\n"
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
        logger.error(f"Error creating character for user {user_id}: {e}")
        await query.edit_message_text("An error occurred while creating your character. Please try again.")

