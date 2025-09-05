from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod
from database.db import Database
from config import TRANSACTION_LOG_CHANNEL
from telegram.constants import ParseMode
import logging

logger = logging.getLogger(__name__)

async def add_resource_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message if hasattr(update, "message") and update.message else None
    if not user:
        if message:
            await message.reply_text("User not found.")
        return
    user_id = user.id
    owner_ids = get_owner_ids()
    if user_id not in owner_ids and not await is_mod(user_id):
        if message:
            await message.reply_text("You are not authorized to use this command.")
        return
    args = context.args if context.args is not None else []
    if not isinstance(args, list):
        args = list(args)
    # --- Custom: /add char level <char_name> <level_number> [user_id] ---
    if len(args) >= 3 and args[0].lower() == "char" and args[1].lower() == "level":
        char_name = args[2]
        try:
            target_level = int(args[3]) if len(args) >= 4 else None
        except ValueError:
            if message:
                await message.reply_text("Level must be a number.")
            return
        if target_level is None:
            if message:
                await message.reply_text("Usage: /add char level <char_name> <level_number> [user_id]")
            return
        # Determine user id
        target_user_id = None
        if len(args) >= 5:
            target_user_id = args[4]
        elif message:
            reply_to = getattr(message, "reply_to_message", None)
            if reply_to is not None:
                from_user = getattr(reply_to, "from_user", None)
                if from_user is not None and hasattr(from_user, "id"):
                    target_user_id = from_user.id
        if not target_user_id:
            target_user_id = user_id
        try:
            target_user_id_int = int(target_user_id)
        except (TypeError, ValueError):
            if message:
                await message.reply_text("Invalid user ID.")
            return
        db = context.bot_data.get("db")
        if not isinstance(db, Database):
            if message:
                await message.reply_text("Database not initialized.")
            return
        
        # Try exact match first
        character = await db.get_character(str(target_user_id_int), char_name)
        # If not found, try case-insensitive and partial search
        if not character:
            player_chars = await db.get_player_characters(str(target_user_id_int))
            match = None
            # 1. Try case-insensitive full match
            for c in player_chars:
                if c.name.lower() == char_name.lower():
                    match = c
                    break
            # 2. If still not found, try case-insensitive partial (substring) match
            if not match:
                for c in player_chars:
                    if char_name.lower() in c.name.lower():
                        match = c
                        break
            character = match
        if not character:
            if message:
                await message.reply_text(f"Character '{char_name}' not found for user {target_user_id_int}.")
            return
        if target_level == character.level:
            if message:
                await message.reply_text(f"Character is already level {character.level}.")
            return
        
        # Handle level increase
        if target_level > character.level:
            old_level = character.level
            level_ups = []
            while character.level < target_level:
                result = character.level_up()
                level_ups.append(result)
            await db.update_character(character)
            if message:
                await message.reply_text(f"Character '{char_name}' leveled up to {character.level} (added {len(level_ups)} levels). All stats, abilities, and rewards updated.")
            
            # Log the character level up
            admin_name = user.first_name or "Admin"
            log_msg = (
                f"<b>#CharacterLevelUp</b>\n\n"
                f"<b>Admin</b>: <a href=\"tg://user?id={user_id}\">{admin_name}</a>\n"
                f"<b>Admin ID</b>: <code>{user_id}</code>\n"
                f"<b>Target User</b>: <code>{target_user_id_int}</code>\n"
                f"<b>Character</b>: <code>{char_name}</code>\n"
                f"<b>Level Change</b>: <code>{old_level} → {character.level}</code>\n"
                f"<b>Levels Added</b>: <code>{len(level_ups)}</code>"
            )
            try:
                await context.bot.send_message(TRANSACTION_LOG_CHANNEL, log_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send character level up log: {e}")
        # Handle level decrease
        else:
            old_level = character.level
            character.level = max(1, target_level)  # Ensure level doesn't go below 1
            await db.update_character(character)
            if message:
                await message.reply_text(f"Character '{char_name}' level set to {character.level}.")
            
            # Log the character level decrease
            admin_name = user.first_name or "Admin"
            log_msg = (
                f"<b>#CharacterLevelDown</b>\n\n"
                f"<b>Admin</b>: <a href=\"tg://user?id={user_id}\">{admin_name}</a>\n"
                f"<b>Admin ID</b>: <code>{user_id}</code>\n"
                f"<b>Target User</b>: <code>{target_user_id_int}</code>\n"
                f"<b>Character</b>: <code>{char_name}</code>\n"
                f"<b>Level Change</b>: <code>{old_level} → {character.level}</code>"
            )
            try:
                await context.bot.send_message(TRANSACTION_LOG_CHANNEL, log_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send character level down log: {e}")
        return

    # --- Custom: /add char <char_name> [user_id] ---
    if len(args) >= 2 and args[0].lower() == "char" and args[1].lower() != "level":
        char_name = args[1]
        # Determine user id
        target_user_id = None
        if len(args) >= 3:
            target_user_id = args[2]
        elif message:
            reply_to = getattr(message, "reply_to_message", None)
            if reply_to is not None:
                from_user = getattr(reply_to, "from_user", None)
                if from_user is not None and hasattr(from_user, "id"):
                    target_user_id = from_user.id
        if not target_user_id:
            target_user_id = user_id
        try:
            target_user_id_int = int(target_user_id)
        except (TypeError, ValueError):
            if message:
                await message.reply_text("Invalid user ID.")
            return
        db = context.bot_data.get("db")
        if not isinstance(db, Database):
            if message:
                await message.reply_text("Database not initialized.")
            return
        
        # Check if player exists
        player = await db.get_player(str(target_user_id_int))
        if not player:
            if message:
                await message.reply_text("Target user not found.")
            return
        
        # Check if character already exists
        existing_char = await db.get_character(str(target_user_id_int), char_name)
        if not existing_char:
            player_chars = await db.get_player_characters(str(target_user_id_int))
            match = None
            # 1. Try case-insensitive full match
            for c in player_chars:
                if c.name.lower() == char_name.lower():
                    match = c
                    break
            # 2. If still not found, try case-insensitive partial (substring) match
            if not match:
                for c in player_chars:
                    if char_name.lower() in c.name.lower():
                        match = c
                        break
            existing_char = match
        if existing_char:
            if message:
                await message.reply_text(f"Character '{char_name}' already exists for user {target_user_id_int}.")
            return
        
        # Check if character type is valid
        from database.characters import get_character_data, CHARACTERS
        char_data = get_character_data(char_name)
        matched_char_name = None
        
        # If exact match fails, try case-insensitive matching
        if not char_data:
            for existing_name in CHARACTERS.keys():
                if existing_name.lower() == char_name.lower():
                    matched_char_name = existing_name
                    char_data = CHARACTERS[existing_name]
                    break
                elif char_name.lower() in existing_name.lower():
                    matched_char_name = existing_name
                    char_data = CHARACTERS[existing_name]
                    break
        
        if not char_data:
            if message:
                await message.reply_text(f"Character type '{char_name}' not found. Available characters: Hitch Dreyse, Mina Carolina, Daz")
            return
        
        # Use the matched character name if found, otherwise use original
        final_char_name = matched_char_name if matched_char_name else char_name
        
        # Create the character
        try:
            character = await db.create_character(
                user_id=str(target_user_id_int),
                name=final_char_name,
                character_type=final_char_name,
                current_hp=char_data.base_stats.HP
            )
            if message:
                await message.reply_text(f"Character '{final_char_name}' added to user {target_user_id_int}'s collection!")
            
            # Log the character addition
            admin_name = user.first_name or "Admin"
            log_msg = (
                f"<b>#CharacterAdded</b>\n\n"
                f"<b>Admin</b>: <a href=\"tg://user?id={user_id}\">{admin_name}</a>\n"
                f"<b>Admin ID</b>: <code>{user_id}</code>\n"
                f"<b>Target User</b>: <code>{target_user_id_int}</code>\n"
                f"<b>Character</b>: <code>{final_char_name}</code>\n"
                f"<b>Action</b>: <code>Added to collection</code>"
            )
            try:
                await context.bot.send_message(TRANSACTION_LOG_CHANNEL, log_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send character addition log: {e}")
        except Exception as e:
            logger.error(f"Failed to create character: {e}")
            if message:
                await message.reply_text("Failed to add character. Please try again.")
        return

    # --- Custom: /remove char <char_name> [user_id] ---
    if len(args) >= 2 and args[0].lower() == "char":
        char_name = args[1]
        # Determine user id
        target_user_id = None
        if len(args) >= 3:
            target_user_id = args[2]
        elif message:
            reply_to = getattr(message, "reply_to_message", None)
            if reply_to is not None:
                from_user = getattr(reply_to, "from_user", None)
                if from_user is not None and hasattr(from_user, "id"):
                    target_user_id = from_user.id
        if not target_user_id:
            target_user_id = user_id
        try:
            target_user_id_int = int(target_user_id)
        except (TypeError, ValueError):
            if message:
                await message.reply_text("Invalid user ID.")
            return
        db = context.bot_data.get("db")
        if not isinstance(db, Database):
            if message:
                await message.reply_text("Database not initialized.")
            return
        
        # Check if player exists
        player = await db.get_player(str(target_user_id_int))
        if not player:
            if message:
                await message.reply_text("Target user not found.")
            return
        
        # Check if character exists
        existing_char = await db.get_character(str(target_user_id_int), char_name)
        if not existing_char:
            player_chars = await db.get_player_characters(str(target_user_id_int))
            match = None
            # 1. Try case-insensitive full match
            for c in player_chars:
                if c.name.lower() == char_name.lower():
                    match = c
                    break
            # 2. If still not found, try case-insensitive partial (substring) match
            if not match:
                for c in player_chars:
                    if char_name.lower() in c.name.lower():
                        match = c
                        break
            existing_char = match
        if not existing_char:
            if message:
                await message.reply_text(f"Character '{char_name}' not found for user {target_user_id_int}.")
            return
        
        # Remove character from player's owned_characters list
        player.remove_character(existing_char.name)
        await db.update_player(str(target_user_id_int), {"owned_characters": player.owned_characters})
        
        # Delete the character document from database
        if db.characters is None:
            logger.error("Characters collection not initialized")
            if message:
                await message.reply_text("Database not initialized properly.")
            return
        await db.characters.delete_one({"user_id": str(target_user_id_int), "name": existing_char.name})
        
        if message:
            await message.reply_text(f"Character '{existing_char.name}' removed from user {target_user_id_int}'s collection!")
        
        # Log the character removal
        admin_name = user.first_name or "Admin"
        log_msg = (
            f"<b>#CharacterRemoved</b>\n\n"
            f"<b>Admin</b>: <a href=\"tg://user?id={user_id}\">{admin_name}</a>\n"
            f"<b>Admin ID</b>: <code>{user_id}</code>\n"
            f"<b>Target User</b>: <code>{target_user_id_int}</code>\n"
            f"<b>Character</b>: <code>{existing_char.name}</code>\n"
            f"<b>Action</b>: <code>Removed from collection</code>"
        )
        try:
            await context.bot.send_message(TRANSACTION_LOG_CHANNEL, log_msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send character removal log: {e}")
        return

    # --- Default: player resource/level add ---
    if len(args) < 2:
        if message:
            await message.reply_text("Usage: /add <gems|crystal|gas|valor|level> <amount> [user_id]\nOr: /add char <char_name> [user_id]\nOr: /add char level <char_name> <level_number> [user_id]")
        return
    resource = args[0].lower()
    amount = args[1]
    target_user_id = None
    if len(args) >= 3:
        target_user_id = args[2]
    elif message:
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is not None:
            from_user = getattr(reply_to, "from_user", None)
            if from_user is not None and hasattr(from_user, "id"):
                target_user_id = from_user.id
    if not target_user_id:
        if message:
            await message.reply_text("Please specify a user ID or reply to a user's message.")
        return
    try:
        amount = int(amount)
        # Allow negative amounts for resource deduction
    except ValueError:
        if message:
            await message.reply_text("Amount must be a number.")
        return
    if resource not in ["marks", "crystal", "gas", "valor", "level"]:
        if message:
            await message.reply_text("Resource must be one of: marks, crystal, gas, valor, level.")
        return
    db = context.bot_data.get("db")
    if not isinstance(db, Database):
        if message:
            await message.reply_text("Database not initialized.")
        return
    try:
        target_user_id_int = int(target_user_id)
    except (TypeError, ValueError):
        if message:
            await message.reply_text("Invalid user ID.")
        return
    player = await db.get_player(str(target_user_id_int))
    if not player:
        if message:
            await message.reply_text("Target user not found.")
        return
    update_data = {}
    if resource == "marks":
        update_data["marks"] = getattr(player, "marks", 0) + amount
    elif resource == "crystal":
        update_data["crystal"] = getattr(player, "crystal", 0) + amount
    elif resource == "gas":
        update_data["gas"] = getattr(player, "gas", 0) + amount
    elif resource == "valor":
        update_data["valor"] = getattr(player, "valor", 0) + amount
    elif resource == "level":
        target_level = amount
        if target_level == player.level:
            if message:
                await message.reply_text(f"User is already level {player.level}.")
            return
        
        # Handle level increase
        if target_level > player.level:
            old_level = player.level
            level_ups = []
            while player.level < target_level:
                level_up_data = player.level_up()
                level_ups.append(level_up_data)
            await db.update_player(str(target_user_id_int), player.dict())
            if message:
                await message.reply_text(f"User {target_user_id_int} leveled up to {player.level} (added {len(level_ups)} levels). Rewards applied.")
            
            # Log the player level up
            admin_name = user.first_name or "Admin"
            log_msg = (
                f"<b>#PlayerLevelUp</b>\n\n"
                f"<b>Admin</b>: <a href=\"tg://user?id={user_id}\">{admin_name}</a>\n"
                f"<b>Admin ID</b>: <code>{user_id}</code>\n"
                f"<b>Target User</b>: <code>{target_user_id_int}</code>\n"
                f"<b>Level Change</b>: <code>{old_level} → {player.level}</code>\n"
                f"<b>Levels Added</b>: <code>{len(level_ups)}</code>"
            )
            try:
                await context.bot.send_message(TRANSACTION_LOG_CHANNEL, log_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send player level up log: {e}")
            return
        # Handle level decrease
        else:
            old_level = player.level
            # Simple approach - just set the level directly
            player.level = max(1, target_level)  # Ensure level doesn't go below 1
            await db.update_player(str(target_user_id_int), {"level": player.level})
            if message:
                await message.reply_text(f"User {target_user_id_int} level set to {player.level}.")
            
            # Log the player level decrease
            admin_name = user.first_name or "Admin"
            log_msg = (
                f"<b>#PlayerLevelDown</b>\n\n"
                f"<b>Admin</b>: <a href=\"tg://user?id={user_id}\">{admin_name}</a>\n"
                f"<b>Admin ID</b>: <code>{user_id}</code>\n"
                f"<b>Target User</b>: <code>{target_user_id_int}</code>\n"
                f"<b>Level Change</b>: <code>{old_level} → {player.level}</code>"
            )
            try:
                await context.bot.send_message(TRANSACTION_LOG_CHANNEL, log_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send player level down log: {e}")
            return
    await db.update_player(str(target_user_id_int), update_data)
    
    # Log the resource change
    admin_name = user.first_name or "Admin"
    action = "Added" if amount >= 0 else "Deducted"
    preposition = "to" if amount >= 0 else "from"
    display_amount = abs(amount)
    
    log_msg = (
        f"<b>#ResourceChange</b>\n\n"
        f"<b>Admin</b>: <a href=\"tg://user?id={user_id}\">{admin_name}</a>\n"
        f"<b>Admin ID</b>: <code>{user_id}</code>\n"
        f"<b>Target User</b>: <code>{target_user_id_int}</code>\n"
        f"<b>Action</b>: <code>{action}</code>\n"
        f"<b>Resource</b>: <code>{resource}</code>\n"
        f"<b>Amount</b>: <code>{display_amount}</code>\n"
        f"<b>Direction</b>: <code>{preposition} user</code>"
    )
    try:
        await context.bot.send_message(TRANSACTION_LOG_CHANNEL, log_msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to send resource change log: {e}")
    
    if message:
        action_verb = "Added" if amount >= 0 else "Deducted"
        display_amount = abs(amount)  # Use absolute value for display
        await message.reply_text(f"{action_verb} {display_amount} {resource} {'to' if amount >= 0 else 'from'} user {target_user_id_int}.")


