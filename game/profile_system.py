from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from database.models import Player, TeamMember
from database.characters import get_character_data
from html import escape
from game.shop_system import ShopSystem
import logging

logger = logging.getLogger(__name__)

def check_authorization(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the user is authorized to access the current interaction."""
    if not update.effective_user:
        return False
    user_id = str(update.effective_user.id)
    callback_data = getattr(update.callback_query, 'data', None) if update.callback_query else None
    # For callback queries, verify the user matches
    if update.callback_query and str(update.callback_query.from_user.id) != user_id:
        logger.warning(f"Unauthorized access attempt by {update.callback_query.from_user.id} for {user_id}'s data")
        return False
    # Check if we have a valid user in context
    if not context.user_data.get('authorized', False):
        context.user_data['authorized'] = True  # Mark as authorized for this session
    return True

async def handle_unauthorized(update: Update):
    """Handle unauthorized access attempts."""
    if update.callback_query:
        try:
            await update.callback_query.answer("⚠️ You are not authorized to view this!", show_alert=True)
        except Exception as e:
            logger.error(f"Error handling unauthorized access: {e}")
    elif update.message:
        try:
            await update.message.reply_text("⚠️ Authorization required!")
        except Exception as e:
            logger.error(f"Error sending unauthorized message: {e}")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    user_id = str(update.effective_user.id)
    context.user_data['owner_id'] = user_id  # Set owner for this session
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_profile_click', 0)
    if now - last < 1.5:
        return  # Ignore spam clicks silently
    context.user_data['last_profile_click'] = now
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        if update.message:
            await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        elif update.callback_query:
            await update.callback_query.edit_message_text("You haven't created a player account yet! Use /start to begin.")
        return
    character_name = player.team[0].character_name if player.team else None
    if not character_name:
        if update.message:
            await update.message.reply_text("You haven't created a character yet! Use /start to begin.")
        elif update.callback_query:
            await update.callback_query.edit_message_text("You haven't created a character yet! Use /start to begin.")
        return
    character = await db.get_character(user_id, character_name)
    if not character:
        if update.message:
            await update.message.reply_text(f"Error: Character {character_name} not found.")
        elif update.callback_query:
            await update.callback_query.edit_message_text(f"Error: Character {character_name} not found.")
        return
    player_level = player.level
    player_xp_to_next = player.xp_to_next_level
    first_name = escape(player.name)
    player_text = (
        f"👤 <b>PLAYER PROFILE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👨‍💼 <b>Name:</b> <a href='tg://user?id={user_id}'>{first_name}</a>\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"🎖️ <b>Level:</b> {player_level}\n"
        f"⚡ <b>XP:</b> {player.xp} / {player_xp_to_next}\n"
        f"🌪️ <b>Total Gas:</b> {player.gas}\n"
        f"🏠 <b>Location:</b> {escape(getattr(player, 'location', 'Unknown'))}\n\n"
        f"💰 <b>Resources</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💠 <b>Titan Crystals:</b> <code>{player.crystal}</code>\n"
        f"⚔️ <b>Valor:</b> <code>{player.valor}</code>\n"
        f"🪙   <b>Marks:</b> <code>{player.marks}</code>\n"
        f"🗺️ <b>Explore:</b> <code>{player.explore_count}</code>\n"
    )
    keyboard = [
        [InlineKeyboardButton("👥 Team", callback_data="manage_team"),
        InlineKeyboardButton("🧳 Items", callback_data="show_inventory")],
        [InlineKeyboardButton("Exit", callback_data="exit_profile")]
    ]
    if update.message:
        await update.message.reply_text(player_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    elif update.callback_query:
        await update.callback_query.edit_message_text(player_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def manage_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    user_id = str(query.from_user.id)
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_team_click', 0)
    if now - last < 1.5:
        return
    context.user_data['last_team_click'] = now
    # --- Privacy: Only allow owner to access ---
    if str(query.from_user.id) != user_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    db = context.bot_data.get("db") or Database()
    # --- Optimization: Only fetch player once, use in-memory team for UI updates ---
    player = context.user_data.get('cached_player')
    if not player:
        player = await db.get_player(user_id)
        context.user_data['cached_player'] = player
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
                f"<b>{escape(member.character_name)}</b> (Role: {escape(role)}) "
                f"<i>[Remove]</i>"
                "\n"
            )
    else:
        team_text += "No members selected yet.\n"
    team_text += "\n<i>Select up to 3 characters:</i>"
    # Add buttons for adding characters not in team
    add_buttons = []
    for char in owned_characters:
        if not any(m.character_name == char for m in team):
            add_buttons.append(InlineKeyboardButton(f"➕ {char}", callback_data=f"add_to_team_{char}"))
    # Remove buttons for characters in team
    remove_buttons = []
    for member in team:
        remove_buttons.append(InlineKeyboardButton(f"❌ {member.character_name}", callback_data=f"remove_from_team_{member.character_name}"))
    keyboard = []
    # Add add_buttons in rows of 2
    for i in range(0, len(add_buttons), 2):
        keyboard.append(add_buttons[i:i+2])
    # Add remove_buttons in rows of 2
    for i in range(0, len(remove_buttons), 2):
        keyboard.append(remove_buttons[i:i+2])
    keyboard.append([
        InlineKeyboardButton("🔄 Clear", callback_data="clear_team"),
        InlineKeyboardButton("💾 Save", callback_data="save_team")
    ])
    keyboard.append([
        InlineKeyboardButton("Back", callback_data="show_profile")
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
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
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
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
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
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    context.user_data["team"] = []
    await query.answer("Team cleared.")
    await manage_team(update, context)

async def save_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    user_id = str(query.from_user.id)
    db = context.bot_data.get("db") or Database()
    await db.init_db()  # Ensure DB is initialized before use
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
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    user_id = str(update.effective_user.id)
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player or not player.team:
        await update.message.reply_text("You have not created a team yet.")
        return
    team_text = "Your current team:\n" + "\n".join(f"{m.position}. {m.character_name}" for m in player.team)
    await update.message.reply_text(team_text)

async def show_character_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    db = context.bot_data.get("db") or Database()
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
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    db = context.bot_data.get("db") or Database()
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

async def show_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_inventory_click', 0)
    if now - last < 1.5:
        return
    context.user_data['last_inventory_click'] = now
    # --- Privacy: Only allow owner to access ---
    if str(query.from_user.id) != user_id:
        await query.edit_message_text("You are not authorized to view this.")
        return
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    inv = getattr(player, 'inventory', {}) or {}
    weapons = [k for k in inv if (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type == "weapon"]
    gear = [k for k in inv if (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type == "gear"]
    utilities = [k for k in inv if (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type == "utility"]
    echo_shards = inv.get("echo_shard", 0)
    inv_text = (
        "🧳 <b>Your Inventory:</b>\n"
        f"- Weapons: <b>{len(weapons)}</b>\n"
        f"- Gear: <b>{len(gear)}</b>\n"
        f"- Utilities: <b>{len(utilities)}</b>\n"
        f"- Echo Shards: <b>{echo_shards}</b>\n\n"
        "<i>View details:</i>"
    )
    keyboard = [
        [InlineKeyboardButton("View Weapons", callback_data="view_weapons"),
         InlineKeyboardButton("View Gear", callback_data="view_gear")],
        [InlineKeyboardButton("View Utilities", callback_data="view_utilities"),
        InlineKeyboardButton("View Echo Shards", callback_data="view_echo_shards")],
        [InlineKeyboardButton("Back", callback_data="show_profile")]
    ]
    await query.edit_message_text(inv_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def view_weapons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_view_weapons', 0)
    if now - last < 1.5:
        return
    context.user_data['last_view_weapons'] = now
    # --- Privacy: Only allow owner to access ---
    if str(query.from_user.id) != user_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    inv = getattr(player, 'inventory', {}) or {}
    weapons = []
    for k, v in inv.items():
        item = shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)
        if item and getattr(item, 'type', None) == "weapon":
            weapons.append((k, v))
    text = "<b>Weapons:</b>\n" + ("\n".join(f"- {getattr(shop_system.shop_items.get(k) or shop_system.hidden_items.get(k), 'name', k)} x{v}" for k, v in weapons) if weapons else "No weapons.")
    keyboard = [[InlineKeyboardButton("Back", callback_data="show_inventory")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def view_gear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_view_gear', 0)
    if now - last < 1.5:
        return
    context.user_data['last_view_gear'] = now
    # --- Privacy: Only allow owner to access ---
    if str(query.from_user.id) != user_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    inv = getattr(player, 'inventory', {}) or {}
    gear = []
    for k, v in inv.items():
        item = shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)
        if item and getattr(item, 'type', None) == "gear":
            gear.append((k, v))
    text = "<b>Gear:</b>\n" + ("\n".join(f"- {getattr(shop_system.shop_items.get(k) or shop_system.hidden_items.get(k), 'name', k)} x{v}" for k, v in gear) if gear else "No gear.")
    keyboard = [[InlineKeyboardButton("Back", callback_data="show_inventory")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def view_utilities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_view_utilities', 0)
    if now - last < 1.5:
        return
    context.user_data['last_view_utilities'] = now
    # --- Privacy: Only allow owner to access ---
    if str(query.from_user.id) != user_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    inv = getattr(player, 'inventory', {}) or {}
    utilities = []
    for k, v in inv.items():
        item = shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)
        if item and getattr(item, 'type', None) == "utility":
            utilities.append((k, v))
    text = "<b>Utilities:</b>\n" + ("\n".join(f"- {getattr(shop_system.shop_items.get(k) or shop_system.hidden_items.get(k), 'name', k)} x{v}" for k, v in utilities) if utilities else "No utilities.")
    keyboard = [[InlineKeyboardButton("Back", callback_data="show_inventory")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def view_echo_shards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_view_echo_shards', 0)
    if now - last < 1.5:
        return
    context.user_data['last_view_echo_shards'] = now
    # --- Privacy: Only allow owner to access ---
    if str(query.from_user.id) != user_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
    inv = getattr(player, 'inventory', {}) or {}
    echo_shards = inv.get("echo_shard", 0)
    text = f"<b>Echo Shards:</b>\n- {echo_shards}"
    keyboard = [[InlineKeyboardButton("Back", callback_data="show_inventory")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return
    bot_username = "Attackon_TitanBot"  # Your bot's username without @
    referral_code = player.referral_code or user_id
    referral_link = f"https://t.me/{bot_username}?start=referral_{referral_code}"
    referred_by = player.referred_by or "None"
    referral_count = player.referral_count if hasattr(player, 'referral_count') else 0
    text = (
        "<b>[©] Referral System</b>\n\n"
        "<b>[©] Your Referral Link:</b>\n"
        f"<a href='{referral_link}'>{referral_link}</a>\n\n"
        f"➳ <b>Your Referral Count:</b> <code>{referral_count}</code>\n"
        f"➳ <b>Referred By:</b> <code>{referred_by}</code>\n\n"
        "<b>[©] Starter Rewards</b>\n"
        "For New Players: 25,000 Marks, 25 Valor, 2 Titan Crystals\n"
        "For Referrers: 40 Valor\n\n"
        "<b>[©] Level Up Rewards</b>\n"
        "• When Referee reaches level 20, Referrer gets 50 Valor.\n"
        "• When Referee reaches level 50, Referrer gets 2 Titan Crystals.\n\n"
        "<b>[©] Mass Share Milestones</b>\n"
        "• 2 Titan Crystals for 10 referrals.\n"
        "• 20 Titan Crystals for 50 referrals.\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)