from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod
from database.db import Database
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
        character = await db.get_character(target_user_id_int, char_name)
        # If not found, try case-insensitive and partial search
        if not character:
            player_chars = await db.get_player_characters(target_user_id_int)
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
            level_ups = []
            while character.level < target_level:
                result = character.level_up()
                level_ups.append(result)
            await db.update_character(character)
            if message:
                await message.reply_text(f"Character '{char_name}' leveled up to {character.level} (added {len(level_ups)} levels). All stats, abilities, and rewards updated.")
        # Handle level decrease
        else:
            character.level = max(1, target_level)  # Ensure level doesn't go below 1
            await db.update_character(character)
            if message:
                await message.reply_text(f"Character '{char_name}' level set to {character.level}.")
        return

    # --- Default: player resource/level add ---
    if len(args) < 2:
        if message:
            await message.reply_text("Usage: /add <gems|crystal|gas|valor|level> <amount> [user_id]")
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
            await message.reply_text("Resource must be one of: gems, crystal, gas, valor, level.")
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
            level_ups = []
            while player.level < target_level:
                level_up_data = player.level_up()
                level_ups.append(level_up_data)
            await db.update_player(target_user_id_int, player.dict())
            if message:
                await message.reply_text(f"User {target_user_id_int} leveled up to {player.level} (added {len(level_ups)} levels). Rewards applied.")
            return
        # Handle level decrease
        else:
            # Simple approach - just set the level directly
            player.level = max(1, target_level)  # Ensure level doesn't go below 1
            await db.update_player(target_user_id_int, {"level": player.level})
            if message:
                await message.reply_text(f"User {target_user_id_int} level set to {player.level}.")
            return
    await db.update_player(target_user_id_int, update_data)
    if message:
        action_verb = "Added" if amount >= 0 else "Deducted"
        display_amount = abs(amount)  # Use absolute value for display
        await message.reply_text(f"{action_verb} {display_amount} {resource} {'to' if amount >= 0 else 'from'} user {target_user_id_int}.")


