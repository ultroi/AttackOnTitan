from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from database.models import Player
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected
import logging

logger = logging.getLogger(__name__)

@maintenance_protected
@ban_protected
async def use_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /use command for using items"""
    user_id = str(update.effective_user.id)

    if not context.args or len(context.args) < 1:
        usable_items = ["`double_gas_injector`", "`mark_surge_token`", "`frenzy_elixir`"]
        await update.message.reply_text(
            "<b>Usage:</b> /use &lt;item_name&gt; [amount]\n\n"
            "<b>Usable items:</b>\n" + "\n".join(usable_items) + "\n\n"
            "<i>Check your inventory with /inv to see available items</i>",
            parse_mode=ParseMode.HTML
        )
        return

    item_name = context.args[0].lower()
    amount = 1  # Default to 1

    if len(context.args) > 1:
        try:
            amount = int(context.args[1])
            if amount < 1:
                await update.message.reply_text("❌ Amount must be positive!")
                return
        except ValueError:
            await update.message.reply_text("❌ Invalid amount!")
            return

    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("❌ Database not initialized.")
        return

    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return

    # Check if item exists in inventory
    inventory = getattr(player, 'inventory', {}) or {}
    
    # Define usable items
    usable_items = ["double_gas_injector", "mark_surge_token", "frenzy_elixir"]
    
    if item_name not in inventory:
        available_items = [f"`{k}`" for k in inventory.keys() if k != "echo_shard"]
        if available_items:
            await update.message.reply_text(
                f"❌ You don't have '{item_name}' in your inventory!\n\n"
                f"<b>Your items:</b>\n" + "\n".join(available_items) + "\n\n"
                f"<b>Usable items:</b> {', '.join(f'`{item}`' for item in usable_items)}",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text("❌ You have no items in your inventory!")
        return
    
    if inventory[item_name] < amount:
        await update.message.reply_text(f"❌ You don't have enough '{item_name}'! You have {inventory[item_name]}, but need {amount}.")
        return
    
    # Check if item is actually usable
    if item_name not in usable_items:
        await update.message.reply_text(
            f"❌ '{item_name}' is not a usable item!\n\n"
            f"<b>Usable items:</b>\n" + "\n".join(f"`{item}`" for item in usable_items) + "\n\n"
            f"<i>This item may be a collectible or not yet implemented for use.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Check for active buffs (only one of each type can be active at a time)
    if item_name == "double_gas_injector":
        if player.double_gas_injector_uses > 0:
            await update.message.reply_text("❌ You already have Double Gas Injector active! Wait for it to expire.")
            return
        # Activate buff
        player.double_gas_injector_uses = 3  # 3 explorations
        message = f"✅ <b>Double Gas Injector activated!</b>\nNext 3 explorations will use half gas cost!"

    elif item_name == "mark_surge_token":
        if player.mark_surge_token_uses > 0:
            await update.message.reply_text("❌ You already have Mark Surge Token active! Wait for it to expire.")
            return
        # Activate buff
        player.mark_surge_token_uses = 10  # 10 titan kills
        message = f"✅ <b>Mark Surge Token activated!</b>\nNext 10 titan kills will give double marks!"

    elif item_name == "frenzy_elixir":
        if player.frenzy_elixir_uses > 0:
            await update.message.reply_text("❌ You already have Frenzy Elixir active! Wait for it to expire.")
            return
        # Activate buff
        player.frenzy_elixir_uses = 3  # 3 explorations
        message = f"✅ <b>Frenzy Elixir activated!</b>\nNext 3 explorations will give triple XP!"

    # Remove item from inventory
    inventory[item_name] -= amount
    if inventory[item_name] <= 0:
        del inventory[item_name]

    # Save player data
    await db.update_player(user_id, {
        "inventory": inventory,
        "double_gas_injector_uses": player.double_gas_injector_uses,
        "mark_surge_token_uses": player.mark_surge_token_uses,
        "frenzy_elixir_uses": player.frenzy_elixir_uses,
        "updated_at": datetime.now(timezone.utc)
    })

    await update.message.reply_text(message, parse_mode=ParseMode.HTML)

