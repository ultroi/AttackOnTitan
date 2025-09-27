from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from database.models import Player, TeamMember
from database.characters import get_character_data, CHARACTER_IMAGES
from utils.ban_utils import ban_protected
from html import escape
from game.shop_system import ShopSystem
from utils.maintenance import maintenance_protected
from typing import Optional
import logging
import re
from game.battle_system import active_battles

logger = logging.getLogger(__name__)


@maintenance_protected
@ban_protected
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    user_id = str(update.effective_user.id)
    context.user_data['owner_id'] = user_id  # Set owner for this session
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_profile_click', 0)
    if now - last < 1.5:
        return  # Ignore spam clicks silently
    context.user_data['last_profile_click'] = now
    # Check if user is in active battle
    if user_id in active_battles:
        first_name = escape(update.effective_user.first_name)
        battle_message = f"⚔️ <a href='tg://user?id={user_id}'>{first_name}</a> is currently battling!"
        if update.message:
            await update.message.reply_text(battle_message, parse_mode=ParseMode.HTML)
        elif update.callback_query:
            await update.callback_query.answer("⚔️ You cannot access your inventory during an active battle with a titan!", show_alert=True)
        return
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        if update.message:
            await update.message.reply_text("❌ Database not initialized. Please try again later.")
        elif update.callback_query:
            await update.callback_query.edit_message_text("❌ Database not initialized. Please try again later.")
        return
    
    # Force fresh player data fetch to prevent stale data issues after battles
    if hasattr(db, 'invalidate_player_cache'):
        db.invalidate_player_cache(user_id)
    
    # Get fresh player data
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
    if context.user_data is None:
        context.user_data = {}
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if not owner_id:
        owner_id = str(update.effective_user.id)
        context.user_data['owner_id'] = owner_id
    if not query or str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
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
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await query.edit_message_text("❌ Database not initialized. Please try again later.")
        return
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
        InlineKeyboardButton("Back", callback_data="back_from_manage_team")
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
    if context.user_data is None:
        context.user_data = {}
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
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
    if context.user_data is None:
        context.user_data = {}
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
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
    if context.user_data is None:
        context.user_data = {}
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    context.user_data["team"] = []
    await query.answer("Team cleared.")
    await manage_team(update, context)

async def save_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    user_id = str(query.from_user.id)
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await query.edit_message_text("❌ Database not initialized. Please try again later.")
        return
    team = context.user_data.get("team", [])
    if not team:
        await query.answer("⚠️ Add members first!")
        return
    team = sorted(team, key=lambda x: x.position)
    for idx, m in enumerate(team, 1):
        m.position = idx
    await db.update_player(user_id, {
        "team": [m.dict() if hasattr(m, "dict") else vars(m) for m in team],
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

async def back_from_manage_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    # Clear unsaved team changes
    if "team" in context.user_data:
        del context.user_data["team"]
    # Call the profile function
    await profile(update, context)

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    user_id = str(update.effective_user.id)
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await update.message.reply_text("❌ Database not initialized. Please try again later.")
        return
    player = await db.get_player(user_id)
    if not player or not player.team:
        await update.message.reply_text("You have not created a team yet.")
        return
    team_text = "Your current team:\n" + "\n".join(f"{m.position}. {m.character_name}" for m in player.team)
    await update.message.reply_text(team_text)


async def show_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_inventory_click', 0)
    if now - last < 1.5:
        return
    context.user_data['last_inventory_click'] = now
    # Check if user is in active battle
    if user_id in active_battles:
        await query.edit_message_text("⚔️ You cannot access your inventory during an active battle with a titan!")
        return
    # --- Privacy: Only allow owner to access ---
    if str(query.from_user.id) != user_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await query.edit_message_text("❌ Database not initialized. Please try again later.")
        return
    
    # Always invalidate player cache before showing inventory
    # This ensures we get fresh data and don't use stale cached data
    if hasattr(db, 'invalidate_player_cache'):
        db.invalidate_player_cache(user_id)
    
    # Get fresh player data
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    inv = getattr(player, 'inventory', {}) or {}
    weapons = [k for k in inv if (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type == "weapon"]
    gear = [k for k in inv if (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type == "gear"]
    military = [k for k in inv if (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type == "military"]
    utilities = [k for k in inv if (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type == "utility"]
    echo_shards = inv.get("echo_shard", 0)
    miscellaneous = [k for k in inv if k != "echo_shard" and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)) and (shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)).type not in ["weapon", "gear", "military", "utility"]]
    inv_text = (
        "🧳 <b>Your Inventory:</b>\n"
        f"- Weapons: <b>{len(weapons)}</b>\n"
        f"- Gear: <b>{len(gear)}</b>\n"
        f"- Military: <b>{len(military)}</b>\n"
        f"- Utilities: <b>{len(utilities)}</b>\n"
        f"- Miscellaneous: <b>{len(miscellaneous)}</b>\n"
        f"- Echo Shards: <b>{echo_shards}</b>\n\n"
        "<i>View details:</i>"
    )
    keyboard = [
        [InlineKeyboardButton("View Weapons", callback_data="view_weapons"),
         InlineKeyboardButton("View Gear", callback_data="view_gear")],
        [InlineKeyboardButton("View Military", callback_data="view_military"),
         InlineKeyboardButton("View Utilities", callback_data="view_utilities")],
        [InlineKeyboardButton("View Miscellaneous", callback_data="view_miscellaneous"),
         InlineKeyboardButton("View Echo Shards", callback_data="view_echo_shards")],
        [InlineKeyboardButton("Back", callback_data="show_profile")]
    ]
    # Fix: Use edit_message_caption if message has photo/caption, else edit_message_text
    try:
        if hasattr(query, "message") and getattr(query.message, "photo", None):
            await query.edit_message_caption(inv_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(inv_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    except Exception as e:
        # fallback: try the other method
        try:
            await query.edit_message_text(inv_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        except Exception:
            await query.edit_message_caption(inv_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def view_weapons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
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
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
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
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
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

async def view_military(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_view_military', 0)
    if now - last < 1.5:
        return
    context.user_data['last_view_military'] = now
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
    military = []
    for k, v in inv.items():
        item = shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)
        if item and getattr(item, 'type', None) == "military":
            military.append((k, v))
    text = "<b>Military:</b>\n" + ("\n".join(f"- {getattr(shop_system.shop_items.get(k) or shop_system.hidden_items.get(k), 'name', k)} x{v}" for k, v in military) if military else "No military items.")
    keyboard = [[InlineKeyboardButton("Back", callback_data="show_inventory")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def view_echo_shards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
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

async def view_miscellaneous(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    user_id = str(getattr(query.from_user, 'id', ''))
    # --- Anti-spam: ignore if called again within 1.5s ---
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_view_miscellaneous', 0)
    if now - last < 1.5:
        return
    context.user_data['last_view_miscellaneous'] = now
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
    miscellaneous = []
    for k, v in inv.items():
        if k != "echo_shard":
            item = shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)
            if item and getattr(item, 'type', None) not in ["weapon", "gear", "military", "utility"]:
                miscellaneous.append((k, v))
    text = "<b>Miscellaneous:</b>\n" + ("\n".join(f"- {getattr(shop_system.shop_items.get(k) or shop_system.hidden_items.get(k), 'name', k)} x{v}" for k, v in miscellaneous) if miscellaneous else "No miscellaneous items.")
    text += "\n\n<b>Usage:</b> /use <item_name> [amount]"
    keyboard = [[InlineKeyboardButton("Back", callback_data="show_inventory")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)



@maintenance_protected
@ban_protected
async def referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    user_id = str(update.effective_user.id)
    db = context.bot_data.get("db") or Database()
    # First, initialize DB if needed
    if db.players is None:
        logger.warning("Database not properly initialized, players collection is None")
    
    if db.players is not None:
        player_doc = await db.players.find_one({"user_id": user_id})
        if player_doc:
            from database.models import Player
            player = Player(**player_doc)
            # Log the raw referral data from the document
            logger.info(f"Raw referral data from DB: referral_count={player_doc.get('referral_count', 0)}, "
                        f"referral_code={player_doc.get('referral_code')}, "
                        f"referred_by={player_doc.get('referred_by')}")
        else:
            # If not found, use standard get_player (should not normally happen)
            player = await db.get_player(user_id)
            logger.warning(f"Player {user_id} not found directly in DB, using cached version")
    else:
        # Fallback if database isn't initialized
        player = await db.get_player(user_id)
        logger.warning(f"DB not initialized, using cached player for {user_id}")
    
    # Clear cache for this player to ensure subsequent calls get fresh data
    if hasattr(db, 'invalidate_player_cache'):
        db.invalidate_player_cache(user_id)
    
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return
    
    bot_username = "Attackon_TitanBot"
    referral_code = player.referral_code or user_id
    referral_link = f"https://t.me/{bot_username}?start=referral_{referral_code}"
    referred_by = player.referred_by or "None"
    referral_count = player.referral_count if hasattr(player, 'referral_count') else 0
    
    # Log referral information to help with debugging
    logger.info(f"Referral info for {user_id}: code={referral_code}, count={referral_count}, referred_by={referred_by}")
    
    text = (
        "<b>[©] Referral System</b>\n\n"
        "<b>[©] Your Referral Link:</b>\n"
        f"<a href='{referral_link}'>{referral_link}</a>\n\n"
        f"➳ <b>Your Referral Count:</b> <code>{referral_count}</code>\n"
        f"➳ <b>Referred By:</b> <code>{referred_by}</code>\n\n"
        "<b>[©] Starter Rewards</b>\n"
        "For New Players: 15,000 Marks, 15 Valor, 3 Titan Crystals\n"
        "For Referrers: 20 Valor\n\n"
        "<b>[©] Level Up Rewards</b>\n"
        "• When Referee reaches level 20, Referrer gets 50 Valor.\n"
        "• When Referee reaches level 50, Referrer gets 2 Titan Crystals.\n\n"
        "<b>[©] Mass Share Milestones</b>\n"
        "• 2 Titan Crystals for 10 referrals.\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    # Call the main profile function to show the profile
    await profile(update, context)



def _create_char_profile_text(character, char_data) -> str:
    """Generates the character profile text."""
    # Ensure stats is a CharacterStats object
    if isinstance(character.stats, dict):
        from database.models import CharacterStats
        character.stats = CharacterStats(**character.stats)
    
    profile_text = (
        f"<b>{escape(character.name)}</b>\n"
        f"<b>Level:</b> {character.level}\n"
        f"<b>XP:</b> {character.xp} / {character.xp_to_next_level}\n\n"
        f"<b>Stats:</b>\n" + "\n".join(f"{stat}: {value}" for stat, value in character.stats.dict().items()) + "\n\n"
        f"<b>Gas:</b> {character.gas}\n"
        f"<b>Unlocked Abilities:</b> {sum(1 for ability_type in ['active', 'passive', 'ultimate'] for ability in getattr(char_data, f'{ability_type}_abilities') if character.unlocked_abilities.get(ability.name, False))}"
    )
    
    return profile_text



def _create_abilities_text(character, char_data, category=None) -> str:
    abilities_text = f"<b>{escape(character.name)}'s Abilities</b>\n\n"
    
    ability_types = ["active", "passive", "ultimate"] if category is None else [category]
    
    for ability_type in ability_types:
        type_title = ability_type.capitalize()
        abilities = getattr(char_data, f"{ability_type}_abilities")
        unlocked_abilities = []
        
        for ability in abilities:
            # Check if ability is unlocked either by the character's unlocked_abilities dict or by level
            is_unlocked = (
                character.unlocked_abilities.get(ability.name, False) or
                character.level >= getattr(ability, 'level_required', 1)
            )
            if is_unlocked:
                unlocked_abilities.append(ability)
        
        if unlocked_abilities:
            abilities_text += f"<b>📌 {type_title} Abilities:</b>\n"
            for ability in unlocked_abilities:
                abilities_text += f"<b>⚔️ {escape(ability.name)}</b>\n"
                abilities_text += f"<i>{escape(ability.description)}</i>\n"
                abilities_text += f"⚡ Gas Cost: {ability.gas_cost}\n"
                if ability.cooldown:
                    abilities_text += f"⏱️ Cooldown: {ability.cooldown} turns\n"
                abilities_text += "\n"
    
    return abilities_text

# --- UPDATED char_detail FUNCTION ---

@maintenance_protected
@ban_protected
async def char_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, *, char_name=None):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not owner_id:
        owner_id = str(update.effective_user.id)
        context.user_data['owner_id'] = owner_id
    if query and str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to view this.", show_alert=True)
        return
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        if update.callback_query:
            await update.callback_query.edit_message_text("❌ Database not initialized. Please try again later.")
        elif update.message:
            await update.message.reply_text("❌ Database not initialized. Please try again later.")
        return
    user_id = update.effective_user.id
    player = await db.get_player(str(user_id))
    if not player or not player.owned_characters:
        await update.message.reply_text("❌ You do not own any characters.")
        return
    # If char_name is provided (from callback), use it directly
    if char_name:
        matched_name = None
        for name in player.owned_characters:
            if char_name.lower() == name.lower():
                matched_name = name
                break
        if not matched_name:
            # fallback: partial match
            for name in player.owned_characters:
                if char_name.lower() in name.lower():
                    matched_name = name
                    break
        if not matched_name:
            if update.callback_query:
                msg = update.callback_query.message
                if msg and getattr(msg, "photo", None):
                    await update.callback_query.edit_message_caption(caption="❌ No matching character found. Please check the name.", reply_markup=None)
                else:
                    await update.callback_query.edit_message_text(text="❌ No matching character found. Please check the name.", reply_markup=None)
            else:
                await update.message.reply_text("❌ No matching character found. Please check the name.")
            return
    else:
        args = context.args or []
        query_name = " ".join(args).strip().lower()
        matched_name = None
        for name in player.owned_characters:
            if query_name == name.lower():
                matched_name = name
                break
        if not matched_name:
            for name in player.owned_characters:
                if query_name and query_name in name.lower():
                    matched_name = name
                    break
        if not matched_name:
            if update.message:
                await update.message.reply_text("❌ No matching character found. Please check the name.")
            elif update.callback_query:
                msg = update.callback_query.message
                if msg and getattr(msg, "photo", None):
                    await update.callback_query.edit_message_caption(caption="❌ No matching character found. Please check the name.", reply_markup=None)
                else:
                    await update.callback_query.edit_message_text(text="❌ No matching character found. Please check the name.", reply_markup=None)
            return
    character = await db.get_character(str(user_id), matched_name)
    if not character:
        if update.message:
            await update.message.reply_text(f"❌ Character {matched_name} not found.")
        elif update.callback_query:
            msg = update.callback_query.message
            if msg and getattr(msg, "photo", None):
                await update.callback_query.edit_message_caption(caption=f"❌ Character {matched_name} not found.", reply_markup=None)
            else:
                await update.callback_query.edit_message_text(text=f"❌ Character {matched_name} not found.", reply_markup=None)
        return
    char_data = get_character_data(character.name)
    if not char_data:
        if update.message:
            await update.message.reply_text("❌ Character data not found.")
        elif update.callback_query:
            msg = update.callback_query.message
            if msg and getattr(msg, "photo", None):
                await update.callback_query.edit_message_caption(caption="❌ Character data not found.", reply_markup=None)
            else:
                await update.callback_query.edit_message_text(text="❌ Character data not found.", reply_markup=None)
        return
    profile_text = _create_char_profile_text(character, char_data)
    keyboard = [
        [InlineKeyboardButton("Fill Gas", callback_data=f"fill_gas_{character.name.replace(' ', '_')}"),
         InlineKeyboardButton("Weapons", callback_data=f"view_weapons_{character.name.replace(' ', '_')}")],
        [InlineKeyboardButton("Abilities", callback_data=f"view_abilities_{character.name.replace(' ', '_')}")],
        [InlineKeyboardButton("Exit", callback_data="exit_profile")]
    ]
    image_url = CHARACTER_IMAGES.get(character.name)
    # If this is a callback query, edit the existing message
    if update.callback_query:
        msg = update.callback_query.message
        if msg and getattr(msg, "photo", None):
            await update.callback_query.edit_message_caption(
                caption=profile_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            await update.callback_query.edit_message_text(
                text=profile_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
    # Otherwise, send a new message (for command usage)
    elif update.message:
        if image_url:
            await update.message.reply_photo(
                photo=image_url,
                caption=profile_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                profile_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )

# Wrapper for char_detail to extract character name from callback data
async def char_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id') if context.user_data else None
    if not owner_id and query:
        owner_id = str(query.from_user.id)
        if context.user_data is None:
            context.user_data = {}
        context.user_data['owner_id'] = owner_id
    char_name = None
    if query and hasattr(query, 'data') and query.data.startswith("char_detail_"):
        char_name = query.data.replace("char_detail_", "").replace("_", " ")
    await char_detail(update, context, char_name=char_name)

# Function to show character abilities
async def view_abilities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id')
    if not owner_id and query:
        owner_id = str(query.from_user.id)
        context.user_data['owner_id'] = owner_id
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    
    # Parse callback data to extract char_name and category
    data = query.data.replace("view_abilities_", "")
    if "_" in data:
        parts = data.split("_", 1)
        char_name = parts[0].replace("_", " ")
        category = parts[1] if len(parts) > 1 else None
    else:
        char_name = data.replace("_", " ")
        category = None
    
    user_id = str(query.from_user.id)
    
    # Anti-spam check
    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get('last_abilities_click', 0)
    if now - last < 1.5:
        return
    context.user_data['last_abilities_click'] = now
    
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await query.edit_message_text("❌ Database not initialized. Please try again later.")
        return
    
    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("❌ You have no player account.")
        return
        
    character = await db.get_character(int(user_id), char_name)
    if not character:
        await query.edit_message_text(f"❌ Character {char_name} not found.")
        return
        
    char_data = get_character_data(character.name)
    if not char_data:
        await query.edit_message_text("❌ Character data not found.")
        return
        
    abilities_text = _create_abilities_text(character, char_data, category)
    
    # Create keyboard with category buttons
    keyboard = [
        [InlineKeyboardButton("Active", callback_data=f"view_abilities_{character.name.replace(' ', '_')}_active"),
         InlineKeyboardButton("Passive", callback_data=f"view_abilities_{character.name.replace(' ', '_')}_passive"),
         InlineKeyboardButton("Ultimate", callback_data=f"view_abilities_{character.name.replace(' ', '_')}_ultimate")],
        [InlineKeyboardButton("All Abilities", callback_data=f"view_abilities_{character.name.replace(' ', '_')}")],
        [InlineKeyboardButton("Back", callback_data=f"char_detail_{character.name.replace(' ', '_')}")]
    ]
    
    # Update the message
    try:
        if getattr(query.message, "photo", None):
            await query.edit_message_caption(
                caption=abilities_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            await query.edit_message_text(
                text=abilities_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Error updating abilities view: {e}")
        await query.answer("Error displaying abilities", show_alert=True)

# --- Weapons View and Equip Handlers ---
async def view_weapons_char(update: Update, context: ContextTypes.DEFAULT_TYPE, *, char_name=None, status_message=None):
    query = update.callback_query
    owner_id = context.user_data.get('owner_id') if context.user_data else None
    if not owner_id and query:
        owner_id = str(query.from_user.id)
        if context.user_data is None:
            context.user_data = {}
        context.user_data['owner_id'] = owner_id
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
        return
    logger.info(f"[view_weapons_char] Callback data: {getattr(query, 'data', None)}")
    await query.answer()
    user_id = str(query.from_user.id)
    # If char_name is not provided, extract from callback data
    if char_name is None:
        char_name = query.data.replace("view_weapons_", "").replace("_", " ")
    logger.info(f"[view_weapons_char] user_id: {user_id}, char_name: {char_name}")
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await query.answer("❌ Database not initialized. Please try again later.", show_alert=True)
        return
    player = await db.get_player(user_id)
    character = await db.get_character(str(user_id), char_name)
    logger.info(f"[view_weapons_char] player: {player is not None}, character: {character is not None}")
    if not player or not character:
        logger.warning(f"[view_weapons_char] Player or character not found for user_id={user_id}, char_name={char_name}")
        await query.answer("❌ Character or Player not found.", show_alert=True)
        return
    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    inv = getattr(player, 'inventory', {}) or {}
    weapons = []
    for k, v in inv.items():
        item = shop_system.shop_items.get(k) or shop_system.hidden_items.get(k)
        if item and getattr(item, 'type', None) in ["weapon", "gear", "military"]:
            weapons.append((k, item, v))
    logger.info(f"[view_weapons_char] weapons found: {len(weapons)}")
    text = ""
    if status_message:
        text += f"<b>{status_message}</b>\n\n"
    text += f"<b>🗡️ Equippable Items for {character.name}:</b>\n\n"
    keyboard = []
    
    # Check if Basic Attack is equipped
    basic_attack_equipped = character.equipped_weapon is None
    
    # First add Basic Attack option
    if basic_attack_equipped:
        text += f"• <b>Basic Attack</b> <i>(Equipped)</i>\n"
    else:
        text += f"• <b>Basic Attack</b>\n"
    
    # Add all other weapons
    for k, item, v in weapons:
        equipped = (character.equipped_weapon == k)
        name = getattr(item, 'name', k)
        
        # Add weapon name with equipped status if applicable
        if equipped:
            text += f"• <b>{name}</b> <i>(Equipped)</i>\n"
        else:
            text += f"• <b>{name}</b>\n"
        
        # Add description under the weapon name
        description = getattr(item, 'description', '')
        if description:
            text += f"  <i>{description}</i>\n"
        
        # Only show equip button if not equipped
        if not equipped:
            safe_key = re.sub(r'[^a-zA-Z0-9_]', '_', k)
            keyboard.append([InlineKeyboardButton(f"Equip {name}", callback_data=f"equip_weapon_{character.name.replace(' ', '_')}__{safe_key}")])
    
    # Add 'Equip Basic Attack' button if not currently equipped
    if not basic_attack_equipped:
        keyboard.append([InlineKeyboardButton("Equip Basic Attack", callback_data=f"equip_weapon_{character.name.replace(' ', '_')}__basic_attack")])
    
    keyboard.append([InlineKeyboardButton("Back", callback_data=f"char_detail_{character.name.replace(' ', '_')}")])
    logger.info(f"[view_weapons_char] Updating message with weapons list.")
    
    # Prevent 'Message is not modified' error by checking current content and markup
    current_message = getattr(query, "message", None)
    new_markup = InlineKeyboardMarkup(keyboard)
    if current_message:
        # For photo messages, compare caption and markup
        if getattr(current_message, "photo", None):
            current_caption = getattr(current_message, "caption", None)
            current_markup = getattr(current_message, "reply_markup", None)
            if current_caption == text and current_markup == new_markup:
                logger.info("[view_weapons_char] Message not modified, skipping edit.")
                await query.answer("No changes made.", show_alert=True)
                return
            await query.edit_message_caption(text, reply_markup=new_markup, parse_mode=ParseMode.HTML)
        else:
            current_text = getattr(current_message, "text", None)
            current_markup = getattr(current_message, "reply_markup", None)
            if current_text == text and current_markup == new_markup:
                logger.info("[view_weapons_char] Message not modified, skipping edit.")
                await query.answer("No changes made.", show_alert=True)
                return
            await query.edit_message_text(text, reply_markup=new_markup, parse_mode=ParseMode.HTML)
    else:
        # Fallback: just send edit_message_text
        await query.edit_message_text(text, reply_markup=new_markup, parse_mode=ParseMode.HTML)

async def equip_weapon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    owner_id = context.user_data.get('owner_id') if context.user_data else None
    if not owner_id and query:
        owner_id = str(query.from_user.id)
        if context.user_data is None:
            context.user_data = {}
        context.user_data['owner_id'] = owner_id
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
        return
    logger.info(f"[equip_weapon] Callback data: {query.data}")
    await query.answer()
    user_id = str(query.from_user.id)
    
    # Check if user is in PVP battle
    from game.pvp_system import active_pvp_battles
    if user_id in active_pvp_battles:
        await query.answer("⚔️ You cannot equip items during PVP battles!", show_alert=True)
        return
    
    # Check if user is in titan battle
    if user_id in active_battles:
        await query.answer("⚔️ You cannot equip items during titan battles!", show_alert=True)
        return
    
    data = query.data.replace("equip_weapon_", "")
    # Split on double underscore to separate char_name and weapon_key
    if "__" in data:
        char_name_part, weapon_key = data.split("__", 1)
        char_name = char_name_part.replace("_", " ")
    else:
        # fallback for old format
        parts = data.split("_")
        char_name = " ".join(parts[:-1])
        weapon_key = parts[-1]
    logger.info(f"[equip_weapon] user_id: {user_id}, char_name: {char_name}, weapon_key: {weapon_key}")
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await query.answer("❌ Database not initialized. Please try again later.", show_alert=True)
        return
    character = await db.get_character(str(user_id), char_name)
    logger.info(f"[equip_weapon] character found: {character is not None}")
    if not character:
        logger.warning(f"[equip_weapon] Character not found for user_id={user_id}, char_name={char_name}")
        await query.answer("❌ Character not found.", show_alert=True)
        return
    # Equip weapon
    last_weapon = character.equipped_weapon
    # If basic_attack is selected, unequip any weapon
    if weapon_key == "basic_attack":
        character.equipped_weapon = None
        status_message = f"Basic Attack equipped successfully."
    else:
        character.equipped_weapon = weapon_key
        status_message = f"{weapon_key} equipped successfully."
    logger.info(f"[equip_weapon] Equipping weapon: {weapon_key}, last_weapon: {last_weapon}")
    await db.update_character(character)
    # Call view_weapons_char with char_name and status_message argument
    logger.info(f"[equip_weapon] Calling view_weapons_char to update UI for char_name: {char_name}")
    await view_weapons_char(update, context, char_name=char_name, status_message=status_message)
    await query.answer(status_message, show_alert=True)



async def fill_gas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    owner_id = context.user_data.get('owner_id') if context.user_data else None
    if not owner_id and query:
        owner_id = str(query.from_user.id)
        context.user_data['owner_id'] = owner_id
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to view this.", show_alert=True)
        return
    await query.answer()
    user_id = str(query.from_user.id)
    char_name = query.data.replace("fill_gas_", "").replace("_", " ")
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await query.answer("❌ Database not initialized. Please try again later.", show_alert=True)
        return
    try:
        db.invalidate_character_cache(user_id, char_name)
        player = await db.get_player(user_id)
        character = await db.get_character(str(user_id), char_name)
        if not player or not character:
            await query.answer("❌ Character or Player not found.", show_alert=True)
            return
        character.max_gas = 5000 + (max(0, character.level - 1) * 250)
        if character.gas >= character.max_gas:
            await query.answer(f"⛽ {character.name}'s gas is already full!", show_alert=True)
            return
        gas_needed = character.max_gas - character.gas
        if player.gas <= 0:
            await query.answer("❌ You don't have sufficient gas to fill up.", show_alert=True)
            return
        gas_to_fill = min(gas_needed, player.gas)
        try:
            player.gas -= gas_to_fill
            await db.update_player(str(user_id), {"gas": player.gas})
            character.gas += gas_to_fill
            await db.update_character(character)
            db.invalidate_character_cache(user_id, char_name)
        except Exception:
            try:
                await db.update_player(str(user_id), {"gas": player.gas + gas_to_fill})
                await query.answer("❌ Gas filling failed. Your gas has been refunded.", show_alert=True)
                return
            except:
                await query.answer("❌ Error occurred. Please check your gas balance.", show_alert=True)
                return
        char_data = get_character_data(character.name)
        updated_profile = _create_char_profile_text(character, char_data) if char_data else "Profile updated."
        keyboard = [
            [InlineKeyboardButton("Fill Gas", callback_data=f"fill_gas_{character.name.replace(' ', '_')}"),
             InlineKeyboardButton("Weapons", callback_data=f"view_weapons_{character.name.replace(' ', '_')}")],
            [InlineKeyboardButton("Abilities", callback_data=f"view_abilities_{character.name.replace(' ', '_')}")],
            [InlineKeyboardButton("Exit", callback_data="exit_profile")]
        ]
        try:
            if query.message is not None and getattr(query.message, "photo", None):
                await query.edit_message_caption(
                    caption=updated_profile,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            elif query.message is not None:
                await query.edit_message_text(
                    text=updated_profile,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            else:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=updated_profile,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            if gas_to_fill < gas_needed:
                await query.answer(f"✅ {gas_to_fill} gas transferred.", show_alert=True)
            else:
                await query.answer(f"✅ Gas filled! {gas_to_fill} used.", show_alert=True)
        except Exception:
            await query.answer(f"✅ Gas filled! {gas_to_fill} used.", show_alert=True)
    except Exception as e:
        print(f"ERROR fill_gas: {e}")
        await query.answer("❌ Error filling gas. Try again later.", show_alert=True)


async def exit_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    if not owner_id and query:
        owner_id = str(query.from_user.id)
        context.user_data['owner_id'] = owner_id
    # Always try to close the profile, but show alert if unauthorized
    if not query:
        return
    if str(query.from_user.id) != owner_id:
        await query.answer("⚠️ You cannot access this!", show_alert=True)
        return
    await query.answer("Profile closed.")
    try:
        if getattr(query.message, "photo", None):
            await query.edit_message_caption(
                caption="Profile closed.",
                reply_markup=None
            )
        else:
            await query.edit_message_text(
                text="Profile closed.",
                reply_markup=None
            )
    except Exception as e:
        logger.error(f"Error closing profile: {e}")

@maintenance_protected
@ban_protected
async def show_characters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display all unlocked characters in formatted list"""
    if context.user_data is None:
        context.user_data = {}
    user_id = str(update.effective_user.id)
    
    db = context.bot_data.get("db")
    if not db or not hasattr(db, 'players') or db.players is None:
        await update.message.reply_text("❌ Database not initialized. Please try again later.")
        return
    
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return
    
    owned_characters = player.owned_characters or []
    if not owned_characters:
        await update.message.reply_text("🎭 You have no unlocked characters yet.\nComplete missions to unlock more!")
        return
    
    # Build formatted character list
    text = "🎭 <b>Your Unlocked Characters</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, char_name in enumerate(owned_characters, 1):
        char_data = get_character_data(char_name)
        if char_data:
            role = char_data.role or "Unknown"
            archetype = char_data.archetype or "Unknown"
            text += f"👤 <b>{i}. {escape(char_name)}</b>\n"
            text += f"   └ <i>{escape(role)}</i>\n"
            text += f"   └ <i>{escape(archetype)}</i>\n\n"
        else:
            text += f"👤 <b>{i}. {escape(char_name)}</b>\n"
            text += f"   └ <i>Character data not found</i>\n\n"
    
    text += f"📊 <b>Total:</b> {len(owned_characters)} characters\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>Use /char &lt;name&gt; for detailed info</i>"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

