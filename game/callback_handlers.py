import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.db_instance import get_database
from game.explore import BattleSystem, active_battles, handle_battle_end, handle_battle_start, handle_battle_action
from game.character_system import (
    show_character_selection,
    show_character_details,
    confirm_character_selection,
    create_character,
    profile,
    show_character_profile
)

async def update_battle_status(query, battle, message):
    """Update the battle status message with current state."""
    status = battle.get_battle_status()
    
    # Generate action buttons for available abilities
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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await get_database()
    query = update.callback_query
    await query.answer()
    
    if query.data == "show_character_profile":
        await show_character_profile(update, context)
    
    elif query.data == "back_to_profile":
        await profile(update, context)
    
    elif query.data == "exit_profile":
        await query.message.delete()
    
    elif query.data == "fill_gas":
        player = await db.get_player(update.effective_user.id)
        character_name = query.message.text.split('\n')[0].strip()
        character = await db.get_character(update.effective_user.id, character_name)
        if character:
            cost = 100  # Cost in marks
            if player.marks >= cost:
                player.marks -= cost
                character.gas = 1000
                await db.update_character(character)
                await db.update_player(update.effective_user.id, {"marks": player.marks})
                await query.edit_message_text(
                    f"Gas tank filled! (-{cost} marks)\n"
                    f"Current gas: {character.gas}/1000"
                )
            else:
                await query.edit_message_text(
                    f"Not enough marks! Need {cost} marks to fill gas tank."
                )
    
    elif query.data == "start_journey":
        await show_character_selection(update, context)
    
    elif query.data.startswith("select_"):
        char_name = query.data.split("_")[1]
        context.user_data['selected_character'] = char_name
        await show_character_details(update, context)
    
    elif query.data.startswith("confirm_"):
        await confirm_character_selection(update, context)
    
    elif query.data == "back_to_selection":
        await show_character_selection(update, context)
    
    elif query.data == "cancel_selection":
        await show_character_selection(update, context)
    
    elif query.data.startswith("birthplace_"):
        await create_character(update, context)
    
    elif query.data.startswith("battle_"):
        await handle_battle_start(update, context)
    
    elif query.data.startswith("ability_"):
        await handle_battle_action(update, context)