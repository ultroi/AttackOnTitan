# character_system.py
from typing import Dict, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.db_instance import get_database
from database.characters import get_character_data, CharacterData, CHARACTERS
import logging
from database.models import Character
from html import escape
import datetime

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the character selection process with a welcome message."""
    if not update.message:
        logger.error("start_character_selection called with no message")
        return  # Silently return if no message (should not happen with proper handler)

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
    """Show available characters for selection."""
    keyboard = [[InlineKeyboardButton(char_name, callback_data=f"select_{char_name}")] for char_name in CHARACTERS]
    reply_markup = InlineKeyboardMarkup(keyboard)
    selection_text = (
        "Choose your character:\n\n"
        "Each character has unique abilities and playstyles. "
        "Select carefully, as this choice will shape your journey."
    )
    await update.callback_query.edit_message_text(selection_text, reply_markup=reply_markup)

async def show_character_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed information about the selected character."""
    query = update.callback_query
    char_name = query.data.replace("select_", "")
    char_info: CharacterData = get_character_data(char_name)

    details_text = (
        f"*{char_name}*\n"
        f"_{char_info.quote}_\n\n"
        f"*Role:* {char_info.role}\n"
        f"*Combat Archetype:* {char_info.archetype}\n"
        f"*Core Trait:* {char_info.core_trait}\n\n"
        "*Base Stats:*\n" + "\n".join(f"{stat}: {value}" for stat, value in char_info.base_stats.items())
    )
    keyboard = [
        [InlineKeyboardButton("Select This Character", callback_data=f"confirm_{char_name}")],
        [InlineKeyboardButton("Back to Selection", callback_data="back_to_selection")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(details_text, reply_markup=reply_markup, parse_mode="Markdown")

async def back_to_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back to selection button."""
    await show_character_selection(update, context)

async def confirm_character_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle character selection confirmation and show birthplace selection."""
    query = update.callback_query
    char_name = query.data.split("_")[1]
    context.user_id = update.effective_user.id
    context.user_data['selected_character'] = char_name
    logger.info(f"User {context.user_id} selected character: {char_name}")  # Log the selected character

    birthplaces = ["Shiganshina", "Karanes", "Trost", "Krolva"]
    keyboard = [[InlineKeyboardButton(place, callback_data=f"birthplace_{place}") for place in birthplaces]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Choose your character's birthplace:", reply_markup=reply_markup)

async def create_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create the character in the database and show initial rewards."""
    query = update.callback_query
    birthplace = query.data.split("_")[1]
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    name = update.effective_user.first_name or f"Player {user_id}"
    selected_character = context.user_data.get('selected_character')
    if not selected_character:
        logger.error("No selected character found in context.user_data")
        await query.edit_message_text("Error: No character selected.")
        return

    db = await get_database()
    char_data = get_character_data(selected_character)
    if not char_data:
        await query.edit_message_text("Error: Character data not found.")
        return

    try:
        # Create player if not exists
        player = await db.get_player(user_id)
        if not player:
            player = await db.create_player(user_id, username, name)

        # Check if character already exists
        existing_char = await db.get_character(user_id, selected_character)
        if existing_char:
            await query.edit_message_text(f"Error: You already have a character named {selected_character}.")
            return
        
        #BaseModel se current hp
        current_hp = char_data.get_max_hp(1) #get max hp of the character

        # Create character using db.create_character
        character = await db.create_character(user_id, selected_character, selected_character, birthplace, current_hp=current_hp)

        # Update player resources
        await db.update_player(user_id, {
            "gas": 10000,
            "crystal": 500,
            "valor": 1000,
            "marks": 15000,
            "explore_count": 0
        })

        # Show welcome message
        welcome_text = (
            f"👋 *Welcome, {escape(name)}!*\n\n"
            f"Your journey begins in *{birthplace}* as *{selected_character}*.\n"
            f"Initial Resources:\n"
            f"• 🔋 *Gas:* `10000`\n"
            f"• 🔮 *Titan Crystals:* `500`\n"
            f"• 🏅 *Valor Points:* `1000`\n"
            f"• 🎯 *Marks:* `15000`\n\n"
            "Use /profile to view your character details and /explore to start your adventure!"
        )
        await query.edit_message_text(welcome_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error creating character: {e}")
        await query.edit_message_text("An error occurred while creating your character. Please try again.")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show player profile with character details."""
    user_id = update.effective_user.id
    db = await get_database()
    player = await db.players.find_one({"user_id": user_id})
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return

    character = await db.characters.find_one({"user_id": user_id})
    if not character:
        await update.message.reply_text("You haven't created a character yet! Use /start to begin.")
        return

    player_level = player.get('level', 1)
    player_xp_to_next = player_level * 100
    first_name = escape(player.get('name', 'Player'))

    player_text = (
        f"👤 <b>PLAYER PROFILE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👨‍💼 <b>Name:</b> <a href='tg://user?id={user_id}'>{first_name}</a>\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"⭐ <b>Level:</b> {player_level}\n"
        f"⚡ <b>XP:</b> {player.get('xp', 0)} / {player_xp_to_next}\n"
        f"🔋 <b>Total Gas:</b> {player.get('gas', 5000)}\n"
        f"🏠 <b>Birthplace:</b> {character.get('birthplace', 'Unknown')}\n\n"
        f"💰 <b>Resources</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔮 <b>Titan Crystals:</b> <code>{player.get('crystal', 0)}</code>\n"
        f"🏅 <b>Valor:</b> <code>{player.get('valor', 0)}</code>\n"
        f"🎯 <b>Marks:</b> <code>{player.get('marks', 0)}</code>\n"
        f"🗺️ <b>Explore:</b> <code>{player.get('explore_count', 0)}</code>\n"
    )

    keyboard = [
        [InlineKeyboardButton(f"🎭 Character", callback_data="show_character_profile"),
        InlineKeyboardButton("👥 Team", callback_data="manage_team")],
        [InlineKeyboardButton("❌ Exit", callback_data="exit_profile")]
    ]
    await update.message.reply_text(player_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def manage_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage the character team with an improved interface."""
    user_id = update.effective_user.id
    db = await get_database()
    player = await db.get_player(user_id)
    if not player:
        await update.callback_query.edit_message_text("You have no player account.")
        return

    owned_characters = player.owned_characters
    if not owned_characters:
        await update.callback_query.edit_message_text(
            "You have no unlocked characters to form a team.\n"
            "Complete missions and events to unlock more characters!"
        )
        return

    # Initialize team in context
    context.user_data['team'] = [member.dict() for member in player.team] if player.team else []
    
    # Get character data for each team member
    team_details = []
    for member in context.user_data['team']:
        char_data = get_character_data(member['character_name'])
        if char_data:
            team_details.append({
                'position': member['position'],
                'name': member['character_name'],
                'role': char_data.role,
                'level': await db.get_character_level(user_id, member['character_name'])
            })
    
    # Sort team by position
    team_details.sort(key=lambda x: x['position'])
    
    # Create team display text
    team_text = "🎭 <b>Team Management</b>\n"
    team_text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if team_details:
        team_text += "<b>Current Team:</b>\n"
        for member in team_details:
            team_text += (
                f"{get_position_emoji(member['position'])} "
                f"<b>{member['name']}</b> (Lv.{member['level']})\n"
                f"└ Role: {member['role']}\n"
            )
    else:
        team_text += "No team members selected yet.\n"
    
    team_text += "\n<i>Select characters to form your team (max 3):</i>\n"
    
    # Create keyboard with character selection and team management options
    keyboard = []
    
    # Add character selection buttons
    char_buttons = []
    for char in owned_characters:
        char_data = get_character_data(char)
        if not char_data:
            continue
            
        current_position = next(
            (member['position'] 
            for member in context.user_data['team'] 
            if member['character_name'] == char
        ), None)
        
        if current_position:
            button_text = f"✅ {char} (#{current_position})"
        else:
            button_text = f"⭕ {char}"
            
        char_buttons.append(InlineKeyboardButton(button_text, callback_data=f"add_to_team_{char}"))
        
        # Create rows of 2 buttons each
        if len(char_buttons) == 2:
            keyboard.append(char_buttons)
            char_buttons = []
            
    # Add any remaining buttons
    if char_buttons:
        keyboard.append(char_buttons)
    
    # Add team management buttons
    keyboard.append([
        InlineKeyboardButton("🔄 Clear Team", callback_data="clear_team"),
        InlineKeyboardButton("💾 Save Team", callback_data="save_team")
    ])
    
    # Add navigation buttons
    keyboard.append([
        InlineKeyboardButton("👤 Profile", callback_data="show_character_profile"),
        InlineKeyboardButton("❌ Exit", callback_data="exit_profile")
    ])
    
    await update.callback_query.edit_message_text(
        team_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

def get_position_emoji(position: int) -> str:
    """Get emoji for team position."""
    return {
        1: "1️⃣",
        2: "2️⃣",
        3: "3️⃣"
    }.get(position, "❓")

async def add_to_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add or remove a character from the team."""
    query = update.callback_query
    char_name = query.data.replace("add_to_team_", "")
    
    context.user_data.setdefault('team', [])
    
    # Check if character is already in team
    existing_member = next(
        (member for member in context.user_data['team'] 
         if member['character_name'] == char_name), 
        None
    )
    
    if existing_member:
        # Remove character if already in team
        context.user_data['team'] = [
            member for member in context.user_data['team'] 
            if member['character_name'] != char_name
        ]
        await query.answer(f"Removed {char_name} from team")
    else:
        # Add character if team isn't full
        if len(context.user_data['team']) < 3:
            used_positions = {member['position'] for member in context.user_data['team']}
            next_position = min({1, 2, 3} - used_positions, default=1)
            context.user_data['team'].append({
                "character_name": char_name,
                "position": next_position
            })
            await query.answer(f"Added {char_name} to position {next_position}")
        else:
            await query.answer("Team is full! Remove a character first.")
    
    # Refresh team management view
    await manage_team(update, context)

async def clear_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all members from the team."""
    context.user_data['team'] = []
    await update.callback_query.answer("Team cleared!")
    await manage_team(update, context)

async def save_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the current team composition."""
    user_id = update.effective_user.id
    db = await get_database()
    team = context.user_data.get('team', [])
    
    if not team:
        await update.callback_query.answer("Please add members to your team first!")
        return
        
    # Ensure positions are sequential
    team = sorted(team, key=lambda x: x['position'])
    for i, member in enumerate(team, 1):
        member['position'] = i
        
    # Update player with the new team
    await db.players.update_one(
        {"user_id": user_id},
        {"$set": {"team": team}}
    )
    
    # Create success message with team details
    team_text = "✅ Team saved successfully!\n\n"
    team_text += "<b>Final Team Composition:</b>\n"
    
    for member in team:
        char_data = get_character_data(member['character_name'])
        position_emoji = get_position_emoji(member['position'])
        team_text += f"{position_emoji} <b>{member['character_name']}</b>"
        if char_data:
            team_text += f" - {char_data.role}\n"
        else:
            team_text += "\n"
            
    keyboard = [
        [InlineKeyboardButton("🔄 Edit Team", callback_data="manage_team")],
        [InlineKeyboardButton("❌ Exit", callback_data="exit_profile")]
    ]
    
    await update.callback_query.edit_message_text(
        team_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the current team composition."""
    user_id = update.effective_user.id
    db = await get_database()
    player = await db.get_player(user_id)
    if not player or not player.team:
        await update.message.reply_text("You have not created a team yet.")
        return

    team_text = "Your current team:\n" + "\n".join(f"{char['position']}. {char['character_name']}" for char in player.team)
    await update.message.reply_text(team_text)

async def show_character_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show character profile with stats and abilities."""
    user_id = update.effective_user.id
    db = await get_database()
    character = await db.characters.find_one({"user_id": user_id})
    if not character:
        await update.callback_query.edit_message_text("You haven't created a character yet! Use /start to begin.")
        return

    char_data = get_character_data(character['name'])
    if not char_data:
        await update.callback_query.edit_message_text("Error: Character data not found.")
        return

    profile_text = (
        f"*{character['name']}*\n"
        f"Birthplace: {character['birthplace']}\n"
        f"Level: {character['level']}\n"
        f"Rank: {character['rank']}\n"
        f"XP: {character['xp']} / {Character.xp_to_next_level.fget(Character(**character))}\n\n"
        f"*Stats:*\n" + "\n".join(f"{stat}: {value}" for stat, value in character['stats'].items()) + "\n\n"
        f"Gas: {character['gas']}\n"
        f"*Unlocked Abilities:*\n"
    )

    for ability_type, abilities in char_data.abilities.items():
        for ability_name, ability in abilities.items():
            if character['unlocked_abilities'].get(ability_name, False):
                profile_text += (
                    f"• {ability.name} ({ability_type})\n"
                    f"  {ability.description}\n"
                    f"  Gas Cost: {ability.gas_cost}\n"
                )
                if ability.cooldown:
                    profile_text += f"  Cooldown: {ability.cooldown} turns\n"
                profile_text += "\n"

    keyboard = [
        [InlineKeyboardButton("Fill Gas", callback_data="fill_gas"),
        InlineKeyboardButton("Exit", callback_data="exit_profile")]
    ]
    await update.callback_query.edit_message_text(profile_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
