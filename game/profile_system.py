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

logger = logging.getLogger(__name__)

def check_authorization(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the user is authorized to access the current interaction."""
    if context.user_data is None:
        context.user_data = {}
    effective_user = getattr(update, 'effective_user', None)
    if not effective_user or not hasattr(effective_user, 'id') or effective_user.id is None:
        return False
    user_id = str(effective_user.id)
    callback_query = getattr(update, 'callback_query', None)
    callback_data = getattr(callback_query, 'data', None) if callback_query else None
    # For callback queries, verify the user matches
    if callback_query and hasattr(callback_query, 'from_user') and str(getattr(callback_query.from_user, 'id', '')) != user_id:
        logger.warning(f"Unauthorized access attempt by {getattr(callback_query.from_user, 'id', 'unknown')} for {user_id}'s data")
        return False
    # Check if we have a valid user in context
    if not context.user_data.get('authorized', False):
        context.user_data['authorized'] = True  # Mark as authorized for this session
    return True

async def handle_unauthorized(update: Update):
    """Handle unauthorized access attempts."""
    callback_query = getattr(update, 'callback_query', None)
    message = getattr(update, 'message', None)
    if callback_query:
        try:
            await callback_query.answer("⚠️ You are not authorized to view this!", show_alert=True)
        except Exception as e:
            logger.error(f"Error handling unauthorized access: {e}")
    elif message:
        try:
            await message.reply_text("⚠️ Authorization required!")
        except Exception as e:
            logger.error(f"Error sending unauthorized message: {e}")


@maintenance_protected
@ban_protected
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
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
    if context.user_data is None:
        context.user_data = {}
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
    if context.user_data is None:
        context.user_data = {}
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
    if context.user_data is None:
        context.user_data = {}
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
    if context.user_data is None:
        context.user_data = {}
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
    if context.user_data is None:
        context.user_data = {}
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

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
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


async def show_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
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
        await handle_unauthorized(update)
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
    if context.user_data is None:
        context.user_data = {}
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
    if context.user_data is None:
        context.user_data = {}
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
    if context.user_data is None:
        context.user_data = {}
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



@maintenance_protected
@ban_protected
async def referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    user_id = str(update.effective_user.id)
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return
    bot_username = "Attackon_TitanBot"
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

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data['owner_id'] if hasattr(context, 'user_data') and context.user_data else None
    if not query or str(query.from_user.id) != owner_id:
        await handle_unauthorized(update)
        return
    await query.answer()
    # Call the main profile function to show the profile
    await profile(update, context)


@maintenance_protected
@ban_protected
async def char_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    if not update.effective_user or not hasattr(update.effective_user, "id"):
        await update.message.reply_text("❌ Unable to get your user ID. Please try again.")
        return
    
    user_id = str(update.effective_user.id)
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    
    if not player or not player.owned_characters:
        await update.message.reply_text("❌ You have no unlocked characters.")
        return
    
    args = context.args if hasattr(context, 'args') else []
    if not args:
        await update.message.reply_text("Usage: /char <character name>")
        return
    
    query_name = " ".join(args).strip().lower()
    matched_name: Optional[str] = None
    
    for char_name in player.owned_characters:
        if query_name in char_name.lower():
            matched_name = char_name
            break
    
    if not matched_name:
        await update.message.reply_text("❌ Character not found or not owned.")
        return
    
    character = await db.get_character(user_id, matched_name)
    if not character:
        await update.message.reply_text(f"Error: Character {matched_name} not found.")
        return
    
    char_data = get_character_data(character.name)
    if not char_data:
        await update.message.reply_text("Error: Character data not found.")
        return
    
    context.user_data['owner_id'] = user_id
    
    # Store profile text in user_data for later editing
    profile_text = (
        f"<b>{escape(character.name)}</b>\n"
        f"<b>Level:</b> {character.level}\n"
        f"<b>XP:</b> {character.xp} / {character.xp_to_next_level}\n\n"
        f"<b>Stats:</b>\n" + "\n".join(f"{stat}: {value}" for stat, value in character.stats.dict().items()) + "\n\n"
        f"<b>Gas:</b> {character.gas}\n"
        f"<b>Unlocked Abilities:</b>\n"
    )
    
    for ability_type in ["active", "passive", "ultimate"]:
        abilities = getattr(char_data, f"{ability_type}_abilities")
        for ability in abilities:
            if character.unlocked_abilities.get(ability.name, False):
                profile_text += (
                    f"• {escape(ability.name)} ({ability_type})\n"
                    f"  <i>{escape(ability.description)}</i>\n"
                    f"  Gas Cost: {ability.gas_cost}\n"
                )
                if ability.cooldown:
                    profile_text += f"  Cooldown: {ability.cooldown} turns\n"
                profile_text += "\n"
    
    keyboard = [
        [InlineKeyboardButton("Fill Gas", callback_data=f"fill_gas_{character.name}"),
         InlineKeyboardButton("Weapons", callback_data=f"show_weapons_{character.name}"),
         InlineKeyboardButton("Exit", callback_data="exit_profile")]
    ]
    
    image_url = CHARACTER_IMAGES.get(character.name)
    
    # Ensure context.user_data is a dict
    if context.user_data is None:
        context.user_data = {}
    
    context.user_data['char_detail_message_id'] = None
    context.user_data['char_detail_profile_text'] = profile_text
    context.user_data['char_detail_character_name'] = character.name
    
    msg = None
    if image_url and getattr(update, "message", None):
        msg = await update.message.reply_photo(
            photo=image_url,
            caption=profile_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    elif getattr(update, "message", None):
        msg = await update.message.reply_text(profile_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    
    if msg:
        context.user_data['char_detail_message_id'] = msg.message_id


async def fill_gas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    
    query = update.callback_query
    owner_id = context.user_data.get('owner_id')
    
    if not query or str(query.from_user.id) != owner_id:
        if query:
            await query.answer("You are not authorized to use this button!", show_alert=True)
        return
    
    user_id = str(query.from_user.id)
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(user_id)
    
    if not player or not player.team:
        await query.answer("You haven't created a team yet! Use /start to begin.", show_alert=True)
        return
    
    # Get character name from callback_data or user_data
    char_name = None
    if query.data.startswith("fill_gas_"):
        char_name = query.data.replace("fill_gas_", "")
    else:
        char_name = context.user_data.get('char_detail_character_name')
    
    character = await db.get_character(user_id, char_name)
    if not character:
        await query.answer(f"Error: Character {char_name} not found.", show_alert=True)
        return
    
    if getattr(character, "gas", 0) >= 5000:
        await query.answer(f"{char_name}'s gas is already full! (5000/5000)", show_alert=True)
        return
    
    prev_gas = getattr(character, "gas", 0)
    gas_needed = 5000 - prev_gas
    
    if getattr(player, "gas", 0) < gas_needed:
        await query.answer(f"Not enough gas! You need {gas_needed} gas to refill, but you only have {player.gas}.", show_alert=True)
        return
    
    player.gas -= gas_needed
    character.gas = 5000
    character.max_gas = 5000
    
    await db.update_player(player.user_id, {"gas": player.gas, "updated_at": datetime.now(timezone.utc)})
    await db.update_character(character)
    
    # Update profile text with new gas value and refill info
    profile_text = context.user_data.get('char_detail_profile_text', '')
    # Replace gas line with updated value and add refill info
    profile_text = re.sub(r"<b>Gas:</b> \d+", f"<b>Gas:</b> {character.gas} (Refilled by {gas_needed})", profile_text)
    context.user_data['char_detail_profile_text'] = profile_text
    
    keyboard = [
        [InlineKeyboardButton("Fill Gas", callback_data=f"fill_gas_{character.name}"),
         InlineKeyboardButton("Weapons", callback_data=f"show_weapons_{character.name}"),
         InlineKeyboardButton("Exit", callback_data="exit_profile")]
    ]
    
    image_url = CHARACTER_IMAGES.get(character.name)
    message_id = context.user_data.get('char_detail_message_id')
    chat_id = query.message.chat_id if query.message else None
    
    try:
        if image_url and chat_id and message_id:
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=profile_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        elif chat_id and message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=profile_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        # fallback: edit current query message
        try:
            if image_url:
                await query.edit_message_caption(profile_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            else:
                await query.edit_message_text(profile_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        except Exception as e2:
            pass
    
    await query.answer(
        f"{char_name}'s gas was {prev_gas}/5000.\nNow filled to 5000! {gas_needed} gas deducted from your resources.",
        show_alert=True
    )
    return


async def exit_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    
    if not check_authorization(update, context):
        await handle_unauthorized(update)
        return
    
    query = getattr(update, 'callback_query', None)
    owner_id = context.user_data.get('owner_id') if context.user_data else None
    
    if not query or str(query.from_user.id) != owner_id:
        await query.answer("You are not authorized to use this button!", show_alert=True)
        return
    
    await query.answer("Profile closed.")
    
    try:
        # Try to delete the message (removes both text and image/caption)
        await query.message.delete()
    except Exception as e:
        try:
            # If can't delete, fallback to edit caption/text
            await query.edit_message_text("Exited")
        except Exception as e2:
            try:
                await query.message.edit_caption("Exited")
            except Exception as e3:
                logger.error(f"Error closing profile: {e} / {e2} / {e3}")


async def show_weapons_ui_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}

    query = getattr(update, 'callback_query', None)
    if query is None or not hasattr(query, 'answer') or not hasattr(query, 'data'):
        return

    await query.answer()
    effective_user = getattr(update, 'effective_user', None)
    if effective_user is None or not hasattr(effective_user, 'id') or effective_user.id is None:
        await query.edit_message_text("❌ Unable to get your user ID.")
        return

    user_id = effective_user.id
    db = context.bot_data.get("db") or Database()
    player = await db.get_player(str(user_id))
    if player is None or not hasattr(player, 'inventory'):
        await query.edit_message_text("❌ Player or inventory not found.")
        return

    char_name = query.data.replace("show_weapons_", "") if query.data else None
    if not char_name:
        await query.edit_message_text("❌ Character name not found in callback data.")
        return

    character = await db.get_character(int(user_id), char_name)
    if character is None or not hasattr(character, 'name'):
        await query.edit_message_text("❌ Character not found.")
        return

    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    shop_items = shop_system.shop_items
    weapon_keys = [k for k in player.inventory if k in shop_items and shop_items[k].type == "weapon" and player.inventory[k] > 0]

    text = f"<b>{character.name} - Equip Weapon</b>\n\nAvailable Weapons:\n"
    keyboard = []
    equipped_weapon = getattr(character, "equipped_weapon", None)
    if weapon_keys:
        for k in weapon_keys:
            weapon = shop_items[k]
            is_equipped = equipped_weapon == k
            text += f"• {weapon.name}{' (equipped)' if is_equipped else ''}\n"
            btn_text = "Unequip" if is_equipped else "Equip"
            keyboard.append([InlineKeyboardButton(f"{btn_text} {weapon.name}", callback_data=f"equip_weapon_{char_name}_{k}")])
        # If any weapon is equipped, show button to equip basic attack
        if equipped_weapon:
            keyboard.append([InlineKeyboardButton("Equip Basic Attack", callback_data=f"equip_weapon_{char_name}_basic_attack")])
    else:
        text += "No weapons purchased from shop."

    # Only add one Back button at the end
    keyboard.append([InlineKeyboardButton("Back", callback_data=f"show_char_detail_{character.name}")])

    # Use edit_message_caption if the message has a photo/caption, else edit_message_text
    try:
        if hasattr(query, "message") and getattr(query.message, "photo", None):
            await query.edit_message_caption(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    except Exception as e:
        # fallback: try the other method
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        except Exception:
            await query.edit_message_caption(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def show_char_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = getattr(update, 'callback_query', None)
    if not query or not hasattr(query, 'data'):
        return
    
    await query.answer()
    char_name = query.data.replace("show_char_detail_", "")
    user_id = str(getattr(query.from_user, 'id', ''))
    db = context.bot_data.get("db") or Database()
    character = await db.get_character(int(user_id), char_name)
    
    if not character:
        await query.edit_message_text("Character not found.")
        return
    
    char_data = get_character_data(character.name)
    profile_text = (
        f"<b>{escape(character.name)}</b>\n"
        f"<b>Level:</b> {character.level}\n"
        f"<b>XP:</b> {character.xp} / {character.xp_to_next_level}\n\n"
        f"<b>Stats:</b>\n" + "\n".join(f"{stat}: {value}" for stat, value in character.stats.dict().items()) + "\n\n"
        f"<b>Gas:</b> {character.gas}\n"
        f"<b>Unlocked Abilities:</b>\n"
    )
    
    for ability_type in ["active", "passive", "ultimate"]:
        abilities = getattr(char_data, f"{ability_type}_abilities")
        for ability in abilities:
            if character.unlocked_abilities.get(ability.name, False):
                profile_text += (
                    f"• {escape(ability.name)} ({ability_type})\n"
                    f"  <i>{escape(ability.description)}</i>\n"
                    f"  Gas Cost: {ability.gas_cost}\n"
                )
                if ability.cooldown:
                    profile_text += f"  Cooldown: {ability.cooldown} turns\n"
                profile_text += "\n"
    
    keyboard = [
        [InlineKeyboardButton("Fill Gas", callback_data=f"fill_gas_{character.name}"),
         InlineKeyboardButton("Weapons", callback_data=f"show_weapons_{character.name}"),
         InlineKeyboardButton("Exit", callback_data="exit_profile")]
    ]
    
    image_url = CHARACTER_IMAGES.get(character.name)
    
    if hasattr(query, "message") and getattr(query.message, "photo", None):
        await query.edit_message_caption(profile_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(profile_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    
    # Build weapon buttons and text
    equipped_weapon = getattr(character, "equipped_weapon", None)
    if weapon_keys and len(weapon_keys) > 0:
        for k in weapon_keys:
            weapon = shop_items[k]
            if equipped_weapon == k:
                text += f"• {weapon.name} (equipped)\n"
                # Do not show button for equipped weapon
            else:
                text += f"• {weapon.name}\n"
                btn_text = "Equip"
                keyboard.append([InlineKeyboardButton(f"{btn_text} {weapon.name}", callback_data=f"equip_weapon_{char_name}_{k}")])
        
        # If any weapon is equipped, show button to equip basic attack
        if equipped_weapon:
            keyboard.append([InlineKeyboardButton("Equip Basic Attack", callback_data=f"equip_weapon_{char_name}_basic_attack")])
    else:
        text += "No weapons purchased from shop."
    
    # Only add one Back button at the end
    if context.user_data is None:
        context.user_data = {}
    
    if context.user_data.get('char_detail_character_name') == character.name:
        keyboard.append([InlineKeyboardButton("Back", callback_data=f"show_char_detail_{character.name}")])
    else:
        keyboard.append([InlineKeyboardButton("Back", callback_data="show_inventory")])
    
    # Use edit_message_caption if the message has a photo/caption, else edit_message_text
    try:
        if hasattr(query, "message") and getattr(query.message, "photo", None):
            await query.edit_message_caption(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    except Exception as e:
        # fallback: try the other method
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        except Exception:
            await query.edit_message_caption(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def handle_equip_weapon_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data is None:
        context.user_data = {}
    
    query = getattr(update, 'callback_query', None)
    if not query or not hasattr(query, 'data') or query.data is None:
        return
    
    await query.answer()
    user_id = getattr(update.effective_user, 'id', None)
    
    if user_id is None:
        await handle_unauthorized(update)
        return
    
    db = context.bot_data.get("db") or Database()
    data = query.data.replace("equip_weapon_", "")
    
    if "_" not in data:
        await query.edit_message_text("Invalid weapon data.")
        return
    
    char_name, weapon_key = data.split("_", 1)
    character = await db.get_character(int(user_id), char_name) if char_name else None
    shop_system = context.bot_data["shop_system"] if hasattr(context, "bot_data") and "shop_system" in context.bot_data else ShopSystem()
    shop_items = shop_system.shop_items
    player = await db.get_player(str(user_id))
    
    if not player or not character:
        await query.edit_message_text("Player or character not found.")
        return
    
    # Handle basic attack equip
    if weapon_key == "basic_attack":
        character.equipped_weapon = None
        await db.update_character(character)
        result_text = "Basic Attack equipped."
    else:
        if weapon_key not in shop_items or weapon_key not in player.inventory or player.inventory.get(weapon_key, 0) == 0:
            await query.edit_message_text("You do not own this weapon from shop.")
            return
        
        result_text = f"{shop_items[weapon_key].name} {'unequipped' if getattr(character, 'equipped_weapon', None) == weapon_key else 'equipped.'}"
        
        if getattr(character, "equipped_weapon", None) == weapon_key:
            character.equipped_weapon = None
            await db.update_character(character)
        else:
            character.equipped_weapon = weapon_key
            await db.update_character(character)
    
    # Fix: Use edit_message_caption if message has photo/caption, else edit_message_text
    try:
        if hasattr(query, "message") and getattr(query.message, "photo", None):
            await query.edit_message_caption(result_text)
        else:
            await query.edit_message_text(result_text)
    except Exception:
        try:
            await query.edit_message_text(result_text)
        except Exception:
            await query.edit_message_caption(result_text)