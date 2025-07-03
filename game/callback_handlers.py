from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from database.db import Database
from database.models import Player, Character
from game.explore import active_battles
from game.battle_system import BattleSystem, handle_battle_end, handle_battle_start, handle_battle_action
from game.shop_system import shop_system
from game.character_system import (
    show_character_selection,
    show_character_details,
    confirm_character_selection,
    create_character,
    profile,
    show_character_profile
)
import logging

logger = logging.getLogger(__name__)

async def update_battle_status(query: Update.callback_query, battle: BattleSystem, message: str):
    """Update the battle status message with current state."""
    try:
        status = battle.get_battle_status()
        keyboard = []
        for ability_type in ["active", "ultimate"]:
            for ability_name, ability in battle.character.get_abilities().get(ability_type, {}).items():
                if (battle.character.unlocked_abilities.get(ability_name, False) and
                    not ability.disabled_against_titans and
                    battle.ability_cooldowns.get(ability_name, 0) == 0):
                    keyboard.append([InlineKeyboardButton(
                        f"{ability.name} ({ability.gas_cost} gas)",
                        callback_data=f"ability_{ability.name}"
                    )])
        keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"{message}\n\n"
            f"| {battle.titan.name} (Lv. {battle.titan.level}) |\n"
            f"HP: {status['titan_hp']}/{battle.titan.max_hp} [{status['titan_bar']}]\n\n"
            f"| {battle.character.name} (Lv. {battle.character.level}) |\n"
            f"HP: {status['character_hp']}/{battle.character.stats.HP} [{status['character_bar']}]\n"
            f"Gas: {status['gas']}/{battle.character.gas}\n\n"
            f"{status['status_message']}\n"
            f"Choose your action:",
            reply_markup=reply_markup
        )
    except BadRequest as e:
        logger.error(f"Error updating battle status: {e}")
        await query.answer("Error updating battle status.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries for shop, character, and battle actions."""
    query = update.callback_query
    if not query or not update.effective_user:
        logger.warning("No query or user information available")
        return
    await query.answer()
    user_id = str(update.effective_user.id)
    try:
        db = context.bot_data.get("db")
        if not db:
            logger.error("Database not initialized in context.bot_data")
            await query.edit_message_text("Internal error: Database not initialized.")
            return

        # Initialize context.user_data if needed
        if not context.user_data:
            context.user_data.update({"message_history": []})

        # Dispatch table for callbacks
        handlers = {
            "shop_": lambda data: handle_shop_category(db, query, data.split("_")[1], context),
            "buy_": lambda data: handle_buy_item(db, query, data.split("_", 1)[1], context),
            "show_character_profile": lambda data: show_character_profile(update, context),
            "back_to_profile": lambda data: profile(update, context),
            "exit_profile": lambda data: handle_exit_profile(query),
            "fill_gas": lambda data: handle_fill_gas(db, query, user_id),
            "select_": lambda data: handle_select_character(query, context, data.split("_")[1]),
            "confirm_": lambda data: confirm_character_selection(update, context),
            "back_to_selection": lambda data: show_character_selection(update, context),
            "cancel_selection": lambda data: show_character_selection(update, context),
            "birthplace_": lambda data: create_character(update, context),
            "ability_": lambda data: handle_battle_action(update, context),
            "action_run": lambda data: handle_battle_action(update, context),
            "battle_": lambda data: handle_battle_start(update, context),
        }

        # Handle callback based on prefix
        for prefix, handler in handlers.items():
            if query.data and query.data.startswith(prefix):
                result = await handler(query.data)
                if result:
                    message, reply_markup = result
                    await query.edit_message_text(
                        text=message,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                return

        await query.edit_message_text("Unknown action.")
    except (BadRequest, PyMongoError) as e:
        logger.error(f"Error in button_callback for user {user_id}: {e}")
        await query.edit_message_text(f"Error processing action: {str(e)}")

async def handle_shop_category(db: Database, query: Update.callback_query, category: str, context: ContextTypes.DEFAULT_TYPE):
    """Handle shop category callbacks."""
    user_id = str(query.from_user.id)
    message_text, reply_markup = await shop_system.show_shop(context, user_id, category)
    context.user_data["message_history"].append(f"shop_{category}")
    return message_text, reply_markup

async def handle_buy_item(db: Database, query: Update.callback_query, item_key: str, context: ContextTypes.DEFAULT_TYPE):
    """Handle item purchase callbacks."""
    user_id = str(query.from_user.id)
    result = await shop_system.purchase_item(context, user_id, item_key)
    category = "main"
    for prev_msg in reversed(context.user_data.get("message_history", [])):
        if prev_msg.startswith("shop_"):
            category = prev_msg.split("_")[1]
            break
    message_text, reply_markup = await shop_system.show_shop(context, user_id, category)
    return f"{result['message']}\n\n{message_text}", reply_markup

async def handle_exit_profile(query: Update.callback_query):
    """Handle profile exit callback."""
    try:
        if query.message:
            await query.message.delete()
        else:
            await query.edit_message_text("Profile closed.")
    except BadRequest:
        await query.edit_message_text("Profile closed.")
    return None

async def handle_fill_gas(db: Database, query: Update.callback_query, user_id: str):
    """Handle gas refill callback."""
    player = await db.get_player(user_id)
    if not player:
        return "Player data not found!", None
    if not player.team or not player.owned_characters:
        return "You haven't created a character yet! Use /start to begin.", None
    character_name = player.team[0].character_name if player.team else player.owned_characters[0]
    character = await db.get_character(user_id, character_name)
    if not character:
        return "Character not found!", None
    if character.gas >= character.max_gas:
        return (
            f"⛽ {character.name}'s gas tank is already full!\n"
            f"Gas: {character.gas}/{character.max_gas}",
            None
        )
    gas_needed = character.max_gas - character.gas
    if player.gas <= 0:
        return (
            "❌ Your personal gas reserves are empty!\n"
            "You need to buy gas from shop.",
            None
        )
    available_gas = min(player.gas, gas_needed)
    player.gas -= available_gas
    character.gas += available_gas
    await db.update_character(character)
    await db.update_player(user_id, {"gas": player.gas})
    return (
        f"⛽ Gas tank refilled! (-{available_gas} from reserves)\n"
        f"✅ {character.name} gas: {character.gas}/{character.max_gas}\n"
        f"🏪 Your remaining gas: {player.gas}",
        None
    )

async def handle_select_character(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, char_name: str):
    """Handle character selection callback."""
    context.user_data["selected_character"] = char_name
    await show_character_details(query, context)
    return None