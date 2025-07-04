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

logger = logging.getLogger(__name__)

async def start_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

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
        if not player:
            player = await db.create_player(user_id, username, name)
        existing_char = await db.get_character(user_id, selected_character)
        if existing_char:
            await query.edit_message_text(f"Error: You already have a character named {selected_character}.")
            return
        current_hp = char_data.get_max_hp(1)
        character = await db.create_character(str(user_id), selected_character, selected_character, current_hp=current_hp)
        # Set player location to selected location
        await db.update_player(user_id, {
            "gas": 10000,
            "crystal": 500,
            "valor": 1000,
            "marks": 15000,
            "explore_count": 0,
            "team": [TeamMember(character_name=selected_character, position=1).model_dump()],
            "location": location,
            "updated_at": datetime.now(timezone.utc)
        })
        welcome_text = (
            f"👋 <b>Welcome, {escape(name)}!</b>\n\n"
            f"Your journey begins in <b>{location}</b> as <b>{selected_character}</b>.\n"
            f"Initial Resources:\n"
            f"• 🔋 <b>Gas:</b> <code>10000</code>\n"
            f"• 🔮 <b>Titan Crystals:</b> <code>500</code>\n"
            f"• 🏅 <b>Valor Points:</b> <code>1000</code>\n"
            f"• 🎯 <b>Marks:</b> <code>15000</code>\n\n"
            "Use /profile to view your character details and /explore to start your adventure!"
        )
        await query.edit_message_text(welcome_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error creating character for user {user_id}: {e}")
        await query.edit_message_text("An error occurred while creating your character. Please try again.")

