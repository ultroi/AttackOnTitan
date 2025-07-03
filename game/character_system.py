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
    await query.answer()
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
    await query.answer()
    char_name = query.data.split("_")[1]
    context.user_data['selected_character'] = char_name
    user_id = str(update.effective_user.id)
    logger.info(f"User {user_id} selected character: {char_name}")
    birthplaces = ["Shiganshina", "Karanes", "Trost", "Krolva"]
    keyboard = [[InlineKeyboardButton(place, callback_data=f"birthplace_{place}") for place in birthplaces]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Choose your character's birthplace:", reply_markup=reply_markup)

async def create_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    birthplace = query.data.split("_")[1]
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or f"user_{user_id}"
    name = update.effective_user.first_name or f"Player {user_id}"
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
        character = await db.create_character(user_id, selected_character, selected_character, birthplace, current_hp=current_hp)
        await db.update_player(user_id, {
            "gas": 10000,
            "crystal": 500,
            "valor": 1000,
            "marks": 15000,
            "explore_count": 0,
            "team": [TeamMember(character_name=selected_character, position=1).model_dump()],
            "updated_at": datetime.now(timezone.utc)
        })
        welcome_text = (
            f"👋 <b>Welcome, {escape(name)}!</b>\n\n"
            f"Your journey begins in <b>{birthplace}</b> as <b>{selected_character}</b>.\n"
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

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    db = Database()
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return
    character_name = player.team[0].character_name if player.team else None
    if not character_name:
        await update.message.reply_text("You haven't created a character yet! Use /start to begin.")
        return
    character = await db.get_character(user_id, character_name)
    if not character:
        await update.message.reply_text(f"Error: Character {character_name} not found.")
        return
    player_level = player.level
    player_xp_to_next = player.xp_to_next_level
    first_name = escape(player.name)
    player_text = (
        f"👤 <b>PLAYER PROFILE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👨‍💼 <b>Name:</b> <a href='tg://user?id={user_id}'>{first_name}</a>\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"⭐ <b>Level:</b> {player_level}\n"
        f"⚡ <b>XP:</b> {player.xp} / {player_xp_to_next}\n"
        f"🔋 <b>Total Gas:</b> {player.gas}\n"
        f"🏠 <b>Birthplace:</b> {character.birthplace}\n\n"
        f"💰 <b>Resources</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔮 <b>Titan Crystals:</b> <code>{player.crystal}</code>\n"
        f"🏅 <b>Valor:</b> <code>{player.valor}</code>\n"
        f"🎯 <b>Marks:</b> <code>{player.marks}</code>\n"
        f"🗺️ <b>Explore:</b> <code>{player.explore_count}</code>\n"
    )
    keyboard = [
        [InlineKeyboardButton("🎭 Character", callback_data="show_character_profile"),
         InlineKeyboardButton("👥 Team", callback_data="manage_team")],
        [InlineKeyboardButton("❌ Exit", callback_data="exit_profile")]
    ]
    await update.message.reply_text(player_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def manage_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        logger.error("manage_team called without a callback query")
        return
    await query.answer()
    user_id = str(query.from_user.id)
    db = Database()
    await db.init_db()  # Ensure DB is initialized before use
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
    owned_characters = player.owned_characters
    if not owned_characters:
        await query.edit_message_text(
            "🎭 You have no unlocked characters to form a team.\n"
            "Complete missions to unlock more!"
        )
        return
    
    context.user_data.setdefault(
        "team",
        [m if isinstance(m, TeamMember) else TeamMember(**m) for m in player.team] if player.team else []
    )
    team = context.user_data["team"]
    team_text = "🎯 <b>Team Management</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    if team:
        team_text += "<b>Current Team:</b>\n"
        for member in sorted(team, key=lambda m: m.position):
            char_data = get_character_data(member.character_name)
            role = char_data.role if char_data else "Unknown"
            team_text += (
                f"{get_position_emoji(member.position)} "
                f"<b>{escape(member.character_name)}</b> (Role: {escape(role)})\n"
            )
    else:
        team_text += "No members selected yet.\n"
    team_text += "\n<i>Select up to 3 characters:</i>"
    keyboard = []
    row = []
    for char in owned_characters:
        in_team = any(m.character_name == char for m in team)
        if in_team:
            label = f"✅ {char}"
            cb_data = f"remove_from_team_{char}"
        else:
            label = f"➕ {char}"
            cb_data = f"add_to_team_{char}"
        row.append(InlineKeyboardButton(label, callback_data=cb_data))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([
        InlineKeyboardButton("🔄 Clear", callback_data="clear_team"),
        InlineKeyboardButton("💾 Save", callback_data="save_team")
    ])
    keyboard.append([
        InlineKeyboardButton("👤 Profile", callback_data="show_character_profile"),
        InlineKeyboardButton("❌ Exit", callback_data="exit_profile")
    ])
    await query.edit_message_text(
        team_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

def get_position_emoji(position: int) -> str:
    return {
        1: "1️⃣",
        2: "2️⃣",
        3: "3️⃣"
    }.get(position, "❓")

async def add_to_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    char_name = query.data.replace("add_to_team_", "")
    context.user_data.setdefault("team", [])
    team = context.user_data["team"]
    if len(team) >= 3:
        await query.answer("⚠️ Team is full!")
        return
    used_positions = {m.position for m in team}
    next_pos = min(set([1, 2, 3]) - used_positions)
    team.append(TeamMember(character_name=char_name, position=next_pos))
    await query.answer(f"Added {char_name}.")
    await manage_team(update, context)

async def remove_from_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    char_name = query.data.replace("remove_from_team_", "")
    context.user_data.setdefault("team", [])
    before = len(context.user_data["team"])
    context.user_data["team"] = [
        m for m in context.user_data["team"]
        if m.character_name != char_name
    ]
    if len(context.user_data["team"]) < before:
        await query.answer(f"Removed {char_name}.")
    else:
        await query.answer("Character not in team.")
    await manage_team(update, context)

async def clear_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["team"] = []
    await query.answer("Team cleared.")
    await manage_team(update, context)

async def save_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    db = Database()
    team = context.user_data.get("team", [])
    if not team:
        await query.answer("⚠️ Add members first!")
        return
    team = sorted(team, key=lambda x: x.position)
    for idx, m in enumerate(team, 1):
        m.position = idx
    await db.update_player(user_id, {
        "team": [m.model_dump() for m in team],
        "updated_at": datetime.now(timezone.utc)
    })
    text = "✅ <b>Team saved!</b>\n\n<b>Composition:</b>\n"
    for m in team:
        role = get_character_data(m.character_name).role if get_character_data(m.character_name) else "Unknown"
        text += f"{get_position_emoji(m.position)} {escape(m.character_name)} - {escape(role)}\n"
    keyboard = [
        [InlineKeyboardButton("🔄 Edit", callback_data="manage_team")],
        [InlineKeyboardButton("❌ Exit", callback_data="exit_profile")]
    ]
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    db = Database()
    player = await db.get_player(user_id)
    if not player or not player.team:
        await update.message.reply_text("You have not created a team yet.")
        return
    team_text = "Your current team:\n" + "\n".join(f"{m.position}. {m.character_name}" for m in player.team)
    await update.message.reply_text(team_text)

async def show_character_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    db = Database()
    player = await db.get_player(user_id)
    if not player or not player.team:
        await query.edit_message_text("You haven't created a team yet! Use /start to begin.")
        return
    character_name = player.team[0].character_name
    character = await db.get_character(user_id, character_name)
    if not character:
        await query.edit_message_text(f"Error: Character {character_name} not found.")
        return
    char_data = get_character_data(character.name)
    if not char_data:
        await query.edit_message_text("Error: Character data not found.")
        return
    profile_text = (
        f"<b>{escape(character.name)}</b>\n"
        f"<b>Birthplace:</b> {escape(character.birthplace)}\n"
        f"<b>Level:</b> {character.level}\n"
        f"<b>Rank:</b> {character.rank}\n"
        f"<b>XP:</b> {character.xp} / {character.xp_to_next_level}\n\n"
        f"<b>Stats:</b>\n" + "\n".join(f"{stat}: {value}" for stat, value in character.stats.model_dump().items()) + "\n\n"
        f"<b>Gas:</b> {character.gas}\n"
        f"<b>Unlocked Abilities:</b>\n"
    )
    for ability_type in ["active", "passive", "ultimate"]:
        abilities = getattr(char_data, f"{ability_type}_abilities")
        for ability in abilities:
            if character.unlocked_abilities.get(ability.name, False):
                profile_text += (
                    f"• {escape(ability.name)} ({ability_type})\n"
                    f"  {escape(ability.description)}\n"
                    f"  Gas Cost: {ability.gas_cost}\n"
                )
                if ability.cooldown:
                    profile_text += f"  Cooldown: {ability.cooldown} turns\n"
                profile_text += "\n"
    keyboard = [
        [InlineKeyboardButton("Fill Gas", callback_data="fill_gas"),
         InlineKeyboardButton("Exit", callback_data="exit_profile")]
    ]
    await query.edit_message_text(profile_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def fill_gas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    db = Database()
    player = await db.get_player(user_id)
    if not player or not player.team:
        await query.edit_message_text("You haven't created a team yet! Use /start to begin.")
        return
    character_name = player.team[0].character_name
    character = await db.get_character(user_id, character_name)
    if not character:
        await query.edit_message_text(f"Error: Character {character_name} not found.")
        return
    if player.marks < 1000:
        await query.edit_message_text("Not enough marks to refill gas (requires 1000 marks).")
        return
    character.gas = 10000
    await db.update_character(character)
    await db.update_player(user_id, {
        "marks": player.marks - 1000,
        "updated_at": datetime.now(timezone.utc)
    })
    await query.edit_message_text(
        f"✅ Filled {character_name}'s gas to 10000 for 1000 marks!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to Profile", callback_data="show_character_profile"),
             InlineKeyboardButton("Exit", callback_data="exit_profile")]
        ]),
        parse_mode=ParseMode.HTML
    )