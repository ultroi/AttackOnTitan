from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from pymongo.errors import PyMongoError
from database.db import Database
from database.models import Player, Character
from game.battle_system import (
    BattleSystem,
    handle_battle_end,
    handle_battle_start,
    handle_battle_action,
    active_battles,
)
from game.shop_system import ShopSystem
from database.db import Database
from game.profile_system import profile, exit_profile, fill_gas
from game.start import (
    show_character_selection,
    show_character_details,
    confirm_character_selection,
    create_character,
)
from game.travel_map import TRAVEL_MAP
import logging

logger = logging.getLogger(__name__)

async def update_battle_status(query: Update.callback_query, battle: BattleSystem, message: str):
    """Update the battle status message with current state."""
    try:
        status = battle.get_battle_status()
        keyboard = []
        for ability_type in ["active", "ultimate"]:
            for ability_name, ability in battle.character.get_abilities().get(ability_type, {}).items():
                # Fix for level 25 abilities not showing up
                if ability.level_required <= battle.character.level:
                    if (not ability.disabled_against_titans and
                        battle.ability_cooldowns.get(ability_name, 0) == 0):
                        keyboard.append([InlineKeyboardButton(
                            f"{ability.name} ({ability.gas_cost} gas)",
                            callback_data=f"ability_{ability.name}"
                        )])
        keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        battle_message = (
            f"{message}\n\n"
            f"| {battle.titan.name} (Lv. {battle.titan.level}) |\n"
            f"HP: {status['titan_hp']}/{battle.titan.max_hp} [{status['titan_bar']}]\n\n"
            f"| {battle.character.name} (Lv. {battle.character.level}) |\n"
            f"HP: {status['character_hp']}/{battle.character.stats.HP} [{status['character_bar']}]\n"
            f"Gas: {status['gas']}/{battle.character.gas}\n\n"
            f"{status['status_message']}\n"
            f"Choose your action:"
        )
        
        try:
            from game.safe_edit import safe_edit_message_text
            success = await safe_edit_message_text(
                query.message,
                battle_message,
                reply_markup=reply_markup
            )
            
            # If edit failed, try sending a new message
            if not success:
                logger.info(f"Battle status update failed, attempting to send new message")
                try:
                    chat_id = query.message.chat_id
                    await query.bot.send_message(
                        chat_id=chat_id,
                        text=battle_message,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                except Exception as send_error:
                    logger.error(f"Failed to send new battle status message: {send_error}")
        except ImportError:
            try:
                await query.edit_message_text(
                    text=battle_message,
                    reply_markup=reply_markup
                )
            except BadRequest as e:
                if "query is too old" in str(e).lower() or "query id is invalid" in str(e).lower():
                    # Try to send a new message if edit fails due to old query
                    try:
                        chat_id = query.message.chat_id
                        await query.bot.send_message(
                            chat_id=chat_id,
                            text=battle_message,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    except Exception as send_error:
                        logger.error(f"Failed to send new battle status message: {send_error}")
                else:
                    raise
    except BadRequest as e:
        logger.error(f"Error updating battle status: {e}")
        await query.answer("Error updating battle status.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"[HANDLER] button_callback got callback: {query.data}")
    if not query or not update.effective_user:
        logger.warning("No query or user information available")
        return
    user_id = str(update.effective_user.id)
    try:
        shop_system = context.bot_data.get("shop_system")
        if not shop_system:
            logger.error("Shop system not initialized in context.bot_data")
            if query.message.text:
                await query.edit_message_text("Internal error: Shop system not initialized.")
            elif query.message.caption:
                await query.edit_message_caption("Internal error: Shop system not initialized.")
            else:
                await query.answer("Internal error: Shop system not initialized.")
            return
        # Use ShopSystem's handle_callback for shop-related actions
        if query.data and (query.data.startswith("shop_") or query.data.startswith("buy_") or query.data == "shop_refresh"):
            result = await shop_system.handle_callback(context, user_id, query.data)
            if result is not None:
                message, reply_markup = result
                if query.message.text:
                    try:
                        from game.safe_edit import safe_edit_message_text
                        await safe_edit_message_text(
                            query.message,
                            message,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    except ImportError:
                        await query.edit_message_text(
                            text=message,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                elif query.message.caption:
                    try:
                        from game.safe_edit import safe_edit_message_caption
                        await safe_edit_message_caption(
                            query.message,
                            message,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    except ImportError:
                        await query.edit_message_caption(
                            caption=message,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                else:
                    await query.answer("Shop action completed.")
            return
        # Initialize context.user_data if needed
        if not context.user_data:
            context.user_data.update({"message_history": []})
        # Dispatch table for callbacks
        handlers = {
            "back_to_profile": lambda data: profile(update, context),
            "exit_profile": lambda data: exit_profile(update, context),
            "select_": lambda data: handle_select_character(query, context, data.split("_")[1]),
            "confirm_": lambda data: confirm_character_selection(update, context),
            "back_to_selection": lambda data: show_character_selection(update, context),
            "cancel_selection": lambda data: show_character_selection(update, context),
            "birthplace_": lambda data: create_character(update, context),
            "ability_": lambda data: handle_battle_action(update, context),
            "action_": lambda data: handle_battle_action(update, context),
            "cooldown_": lambda data: handle_battle_action(update, context),
            "lowgas_": lambda data: handle_battle_action(update, context),
            "battle_": lambda data: handle_battle_start(update, context),
            "location_": lambda data: create_character(update, context),
            "switch_to_": lambda data: handle_battle_action(update, context),
            "switch_back": lambda data: handle_battle_action(update, context),
        }
        # Handle callback based on prefix
        for prefix, handler in handlers.items():
            if query.data and query.data.startswith(prefix):
                result = await handler(query.data)
                if result is not None:
                    message, reply_markup = result
                    if query.message.text:
                        await query.edit_message_text(
                            text=message,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    elif query.message.caption:
                        await query.edit_message_caption(
                            caption=message,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    else:
                        await query.answer("Action completed.")
                return
        # Fix for unknown action error
        if query.message.text:
            await query.edit_message_text("Unknown action.")
        elif query.message.caption:
            await query.edit_message_caption("Unknown action.")
        else:
            await query.answer("Unknown action.")
    except (BadRequest, PyMongoError) as e:
        error_str = str(e).lower()
        if "query is too old" in error_str or "query id is invalid" in error_str or "response timeout expired" in error_str:
            # Handle expired callback query by sending a new message
            logger.info(f"Expired callback query for user {user_id}: {e}")
            try:
                chat_id = query.message.chat_id if hasattr(query.message, 'chat_id') else query.message.chat.id
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="This action has expired. Please try the command again.",
                    parse_mode="HTML"
                )
                await query.answer("Action expired, please try again.")
            except Exception as send_error:
                logger.error(f"Failed to send message about expired query: {send_error}")
        else:
            # Handle other errors
            logger.error(f"Error in button_callback for user {user_id}: {e}")
            try:
                if query.message.text:
                    await query.edit_message_text(f"Error processing action: {str(e)}")
                elif query.message.caption:
                    await query.edit_message_caption(f"Error processing action: {str(e)}")
                else:
                    await query.answer(f"Error processing action: {str(e)}")
            except Exception as nested_error:
                logger.error(f"Failed to notify user about error: {nested_error}")

async def handle_select_character(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, char_name: str):
    if context.user_data is None:
        context.user_data = {}
    context.user_data["selected_character"] = char_name
    await show_character_details(query, context)
    return None


async def handle_travel_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle travel decision point direction selection with robust debug logging and direction validation."""
    query = update.callback_query
    await query.answer()
    db = context.bot_data.get("db")
    user_id = str(query.from_user.id)
    player = await db.get_player(user_id)
    location = getattr(player, "location", "Unknown")
    # Debug logging for troubleshooting
    logger.info(f"[TRAVEL_DECISION] Callback data: {query.data}, Player location: {location}")
    if not location.startswith("Decision_") or location not in TRAVEL_MAP:
        logger.warning(f"[TRAVEL_DECISION] Not at a valid decision point. (location: {location})")
        await query.edit_message_text(f"Not at a valid decision point. (location: {location})")
        return
    directions = TRAVEL_MAP[location]
    logger.info(f"[TRAVEL_DECISION] Available directions: {list(directions.keys())}")
    dir_selected = query.data.replace("travel_decision_", "").replace("travel_decision:", "").strip().lower()
    # Normalize direction keys for comparison
    normalized_directions = {k.strip().lower(): k for k in directions.keys()}
    logger.info(f"[TRAVEL_DECISION] Normalized directions: {normalized_directions}, Selected: {dir_selected}")
    # Extra debug log for failure
    if dir_selected not in normalized_directions:
        logger.error(f"[TRAVEL_DECISION] dir_selected: {dir_selected}, normalized_directions: {normalized_directions}, directions.keys(): {list(directions.keys())}, location: {location}")
        logger.warning(f"[TRAVEL_DECISION] Invalid direction: {dir_selected}. Available: {list(directions.keys())}")
        await query.edit_message_text(f"Invalid direction: {dir_selected}. Available: {list(directions.keys())}")
        return
    real_key = normalized_directions[dir_selected]
    to, required = directions[real_key]
    travel_state = {
        "in_progress": True,
        "direction": real_key,  # Always use the original map key
        "from": location,
        "to": to,
        "progress": 0,
        "required": required
    }
    # Update both travel and location so the user moves forward
    await db.update_player(user_id, {"travel": travel_state, "location": to})
    msg = f"You continue your journey <b>{real_key}</b> ({location} → {to}) [0/{required} explores]"
    try:
        from game.safe_edit import safe_edit_message_caption, safe_edit_message_text
        if query.message and getattr(query.message, "photo", None):
            await safe_edit_message_caption(query.message, msg, parse_mode="HTML")
        else:
            await safe_edit_message_text(query.message, msg, parse_mode="HTML")
    except ImportError:
        if query.message and getattr(query.message, "photo", None):
            await query.edit_message_caption(msg, parse_mode="HTML")
        else:
            await query.edit_message_text(msg, parse_mode="HTML")