from datetime import datetime, timezone, timedelta
import random
from typing import Dict, List, Optional, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from database.models import Player
from database.characters import get_character_data
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected
import logging

logger = logging.getLogger(__name__)

# Spin System Configuration
SPIN_ITEMS = {
    # Common Items (70%)
    "gas": {"name": "Gas", "rarity": "common", "amount": 500, "type": "resource"},
    "marks": {"name": "Marks", "rarity": "common", "amount": 1000, "type": "resource"},
    "training_dummy": {"name": "Training Dummy", "rarity": "common", "amount": 1, "type": "item"},
    "battle_journal": {"name": "Battle Journal", "rarity": "common", "amount": 1, "type": "item"},

    # Uncommon Items (19%)
    "mark_surge_token": {"name": "Mark Surge Token", "rarity": "uncommon", "amount": 1, "type": "buff"},
    "double_gas_injector": {"name": "Double Gas Injector", "rarity": "uncommon", "amount": 1, "type": "buff"},
    "frenzy_elixir": {"name": "Frenzy Elixir", "rarity": "uncommon", "amount": 1, "type": "buff"},
    "titan_biology_manual": {"name": "Titan Biology Manual", "rarity": "uncommon", "amount": 1, "type": "item"},

    # Rare Items (9%)
    "rdo": {"name": "Regiment Dispatch Order (RDO)", "rarity": "rare", "amount": 1, "type": "item"},
    "daz": {"name": "Daz", "rarity": "rare", "amount": 1, "type": "character"},
    "hitch_dreyse": {"name": "Hitch Dreyse", "rarity": "rare", "amount": 1, "type": "character"},
    "mina_carolina": {"name": "Mina Carolina", "rarity": "rare", "amount": 1, "type": "character"},

    # Ultra-Rare Items (2%)
    "floch_forster": {"name": "Floch Forster", "rarity": "ultra_rare", "amount": 1, "type": "character"},
    "commander_pixis": {"name": "Commander Pixis", "rarity": "ultra_rare", "amount": 1, "type": "character"},
}

RARITY_WEIGHTS = {
    "common": 70,
    "uncommon": 19,
    "rare": 9,
    "ultra_rare": 2,
}

SPIN_COSTS = {
    "single": 5,  # 5 Valor per spin
    "multi": 45,  # 45 Valor for 10 spins
}

class SpinSystem:
    def __init__(self):
        self.spin_counts = {}  # Track community spins
        self.last_reset = datetime.now(timezone.utc)
        self.event_active = False
        self.event_marks_bonus = 1.25
        self.event_spin_discount = 0.8

    def get_spin_cost(self, spins: int = 1) -> int:
        """Get cost for specified number of spins"""
        if spins == 1:
            base_cost = SPIN_COSTS["single"]
        elif spins == 10:
            base_cost = SPIN_COSTS["multi"]
        else:
            base_cost = SPIN_COSTS["single"] * spins

        # Apply community event discount if active
        if self.event_active:
            base_cost = int(base_cost * self.event_spin_discount)

        return base_cost

    def check_community_event(self) -> bool:
        """Check if community spin event should be active"""
        current_time = datetime.now(timezone.utc)

        # Reset weekly count
        if current_time - self.last_reset >= timedelta(days=7):
            self.spin_counts = {}
            self.last_reset = current_time

        # Check if 250 spins reached in the week
        total_spins = sum(self.spin_counts.values())
        if total_spins >= 250 and not self.event_active:
            self.event_active = True
            return True
        elif total_spins < 250 and self.event_active:
            self.event_active = False

        return self.event_active

    def get_random_item(self, pity_counter: int = 0) -> str:
        """Get random item based on rarity weights and pity counter"""
        # Calculate effective weights
        weights = RARITY_WEIGHTS.copy()

        # Apply pity counter (after 50 spins, ultra-rare chance increases to 18%)
        if pity_counter >= 50:
            weights["ultra_rare"] = 18
            # Adjust other weights proportionally
            total_other = 100 - 18
            original_other = 100 - weights["ultra_rare"]
            for rarity in ["common", "uncommon", "rare"]:
                weights[rarity] = int(weights[rarity] * total_other / original_other)

        # Create weighted list
        items = []
        for item_key, item_data in SPIN_ITEMS.items():
            weight = weights.get(item_data["rarity"], 1)
            items.extend([item_key] * weight)

        return random.choice(items)

    def process_spin_reward(self, player: Player, item_key: str) -> Dict[str, Any]:
        """Process the reward for a spin"""
        item = SPIN_ITEMS[item_key]
        reward_info = {
            "item_key": item_key,
            "item_name": item["name"],
            "rarity": item["rarity"],
            "amount": item["amount"],
            "type": item["type"],
            "duplicate": False,
            "valor_refund": 0
        }

        if item["type"] == "resource":
            if item_key == "gas":
                player.gas += item["amount"]
            elif item_key == "marks":
                player.marks += item["amount"]

        elif item["type"] == "item":
            # Add to inventory
            if item_key not in player.inventory:
                player.inventory[item_key] = 0
            player.inventory[item_key] += item["amount"]

        elif item["type"] == "buff":
            # Add to inventory
            if item_key not in player.inventory:
                player.inventory[item_key] = 0
            player.inventory[item_key] += item["amount"]

        elif item["type"] == "character":
            # Check if character already owned
            if item_key in player.owned_characters:
                reward_info["duplicate"] = True
                # Refund based on rarity
                if item["rarity"] == "rare":
                    reward_info["valor_refund"] = 3
                elif item["rarity"] == "ultra_rare":
                    reward_info["valor_refund"] = 10
                player.valor += reward_info["valor_refund"]
            else:
                player.owned_characters.append(item_key)

        return reward_info

    def check_spin_streak_bonus(self, player: Player) -> bool:
        """Check if player gets spin streak bonus (20 spins in a row)"""
        # This would need to be tracked in player data
        # For now, return False (implement later if needed)
        return False

async def spin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /spin command"""
    if context.user_data is None:
        context.user_data = {}
    user_id = str(update.effective_user.id)

    # Anti-spam check
    now = datetime.now(timezone.utc).timestamp()
    last_spin = context.user_data.get('last_spin_time', 0)
    if now - last_spin < 2:  # 2 second cooldown
        return
    context.user_data['last_spin_time'] = now

    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("❌ Database not initialized.")
        return

    player = await db.get_player(user_id)
    if not player:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return

    spin_system = context.bot_data.get("spin_system", SpinSystem())

    # Check community event
    event_active = spin_system.check_community_event()

    # Show spin menu
    text = "🎰 <b>Spin System</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if event_active:
        text += "🎉 <b>Community Event Active!</b>\n"
        text += "• +25% Titan Kill Marks\n"
        text += "• 20% Spin Discount\n\n"

    single_cost = spin_system.get_spin_cost(1)
    multi_cost = spin_system.get_spin_cost(10)

    text += f"💰 <b>Costs:</b>\n"
    text += f"• 1 Spin: {single_cost} Valor\n"
    text += f"• 10 Spins: {multi_cost} Valor\n\n"

    text += f"💎 <b>Your Valor:</b> {player.valor}\n\n"
    text += "<i>Choose how many spins:</i>"

    keyboard = [
        [InlineKeyboardButton(f"🎰 1 Spin ({single_cost}💎)", callback_data="spin_single"),
         InlineKeyboardButton(f"🎰 10 Spins ({multi_cost}💎)", callback_data="spin_multi")],
        [InlineKeyboardButton("📊 Spin Info", callback_data="spin_odds"),
         InlineKeyboardButton("🏆 Spin Medals", callback_data="spin_medals")],
        [InlineKeyboardButton("❌ Close", callback_data="spin_close")]
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def spin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle spin callbacks"""
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    data = query.data

    db = context.bot_data.get("db")
    if not db:
        await query.edit_message_text("❌ Database not initialized.")
        return

    player = await db.get_player(user_id)
    if not player:
        await query.edit_message_text("You haven't created a player account yet!")
        return

    spin_system = context.bot_data.get("spin_system", SpinSystem())

    if data == "spin_close":
        await query.edit_message_text("Spin menu closed.")
        return

    elif data == "spin_odds":
        text = "🎰 <b>Spin Info</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        text += "🎁 <b>Common Items:</b>\n"
        common_items = [item["name"] for item in SPIN_ITEMS.values() if item["rarity"] == "common"]
        for item in common_items:
            text += f"• {item}\n"
        text += "\n"
        
        text += "🟢 <b>Uncommon Items:</b>\n"
        uncommon_items = [item["name"] for item in SPIN_ITEMS.values() if item["rarity"] == "uncommon"]
        for item in uncommon_items:
            text += f"• {item}\n"
        text += "\n"
        
        text += "🟣 <b>Rare Items:</b>\n"
        for item_key, item_data in SPIN_ITEMS.items():
            if item_data["rarity"] == "rare":
                item_type = "👤" if item_data["type"] == "character" else "📦"
                text += f"{item_type} {item_data['name']}\n"
        text += "\n"
        
        text += "🟡 <b>Ultra-Rare Items:</b>\n"
        for item_key, item_data in SPIN_ITEMS.items():
            if item_data["rarity"] == "ultra_rare":
                item_type = "👤" if item_data["type"] == "character" else "📦"
                text += f"{item_type} {item_data['name']}\n"
        text += "\n"
        
        text += "🎯 <b>Pity Counter:</b>\n"
        text += "After 50 spins without Ultra-Rare,\n"
        text += "Ultra-Rare chance increases to 18%\n\n"
        text += "🎉 <b>Community Event:</b>\n"
        text += "250+ spins/week = 25% more marks + 20% discount"

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="spin_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    elif data == "spin_medals":
        # Show spin medals with exchange options
        medals = getattr(player, 'spin_medals', 0)
        fragments = player.inventory.get("ultra_rare_fragment", 0)
        
        text = "🏆 <b>Spin Medals</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"🎖️ <b>Your Medals:</b> {medals}\n"
        if fragments > 0:
            text += f"🟡 <b>Ultra-Rare Fragments:</b> {fragments}/50\n"
        text += "\n💰 <b>Redeem Options:</b>\n"
        text += "• 10 Medals = 500 Gas\n"
        text += "• 25 Medals = 1 RDO\n"
        text += "• 50 Medals = 1/50 Ultra-Rare Fragment\n\n"
        text += "<i>Select an option to redeem:</i>"

        keyboard = []
        
        # Add redeem buttons based on available medals
        if medals >= 10:
            keyboard.append([InlineKeyboardButton("🔄 10 Medals → 500 Gas", callback_data="redeem_gas")])
        if medals >= 25:
            keyboard.append([InlineKeyboardButton("🔄 25 Medals → 1 RDO", callback_data="redeem_rdo")])
        if medals >= 50:
            keyboard.append([InlineKeyboardButton("🔄 50 Medals → Ultra-Rare Fragment", callback_data="redeem_fragment")])
        
        # Always show back button
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="spin_menu")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    # Handle medal redemption
    elif data == "redeem_gas":
        medals = getattr(player, 'spin_medals', 0)
        if medals < 10:
            await query.answer("❌ Not enough medals! Need 10 medals.", show_alert=True)
            return
        
        # Deduct medals and add gas
        player.spin_medals -= 10
        player.gas += 500
        
        # Save to database
        await db.update_player(user_id, {
            "spin_medals": player.spin_medals,
            "gas": player.gas,
            "updated_at": datetime.now(timezone.utc)
        })
        
        await query.answer("✅ Redeemed 10 medals for 500 Gas!", show_alert=True)
        # Return to medals menu
        await spin_callback_handler(update, context)
        return

    elif data == "redeem_rdo":
        medals = getattr(player, 'spin_medals', 0)
        if medals < 25:
            await query.answer("❌ Not enough medals! Need 25 medals.", show_alert=True)
            return
        
        # Deduct medals and add RDO to inventory
        player.spin_medals -= 25
        if "rdo" not in player.inventory:
            player.inventory["rdo"] = 0
        player.inventory["rdo"] += 1
        
        # Save to database
        await db.update_player(user_id, {
            "spin_medals": player.spin_medals,
            "inventory": player.inventory,
            "updated_at": datetime.now(timezone.utc)
        })
        
        await query.answer("✅ Redeemed 25 medals for 1 RDO!", show_alert=True)
        # Return to medals menu
        await spin_callback_handler(update, context)
        return

    elif data == "redeem_fragment":
        medals = getattr(player, 'spin_medals', 0)
        if medals < 50:
            await query.answer("❌ Not enough medals! Need 50 medals.", show_alert=True)
            return
        
        # Deduct medals and add ultra-rare fragment
        player.spin_medals -= 50
        if "ultra_rare_fragment" not in player.inventory:
            player.inventory["ultra_rare_fragment"] = 0
        player.inventory["ultra_rare_fragment"] += 1
        
        # Check if player has enough fragments for a complete ultra-rare
        fragments = player.inventory.get("ultra_rare_fragment", 0)
        if fragments >= 50:
            # Award a random ultra-rare character
            ultra_rare_items = [key for key, item in SPIN_ITEMS.items() if item["rarity"] == "ultra_rare"]
            awarded_item = random.choice(ultra_rare_items)
            
            # Add character to owned_characters
            if awarded_item not in player.owned_characters:
                player.owned_characters.append(awarded_item)
            
            # Reset fragments
            player.inventory["ultra_rare_fragment"] = fragments - 50
            
            # Save to database
            await db.update_player(user_id, {
                "spin_medals": player.spin_medals,
                "inventory": player.inventory,
                "owned_characters": player.owned_characters,
                "updated_at": datetime.now(timezone.utc)
            })
            
            item_name = SPIN_ITEMS[awarded_item]["name"]
            await query.answer(f"🎉 Congratulations! You collected 50 fragments and received: {item_name}!", show_alert=True)
        else:
            # Save to database
            await db.update_player(user_id, {
                "spin_medals": player.spin_medals,
                "inventory": player.inventory,
                "updated_at": datetime.now(timezone.utc)
            })
            
            await query.answer(f"✅ Redeemed 50 medals for 1 Ultra-Rare Fragment! You now have {fragments}/50 fragments.", show_alert=True)
        
        # Return to medals menu
        await spin_callback_handler(update, context)
        return

    elif data == "spin_menu":
        # Return to main spin menu
        event_active = spin_system.check_community_event()
        text = "🎰 <b>Spin System</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        if event_active:
            text += "🎉 <b>Community Event Active!</b>\n"
            text += "• +25% Titan Kill Marks\n"
            text += "• 20% Spin Discount\n\n"

        single_cost = spin_system.get_spin_cost(1)
        multi_cost = spin_system.get_spin_cost(10)

        text += f"💰 <b>Costs:</b>\n"
        text += f"• 1 Spin: {single_cost} Valor\n"
        text += f"• 10 Spins: {multi_cost} Valor\n\n"

        text += f"💎 <b>Your Valor:</b> {player.valor}\n\n"
        text += "<i>Choose how many spins:</i>"

        keyboard = [
            [InlineKeyboardButton(f"🎰 1 Spin ({single_cost}💎)", callback_data="spin_single"),
             InlineKeyboardButton(f"🎰 10 Spins ({multi_cost}💎)", callback_data="spin_multi")],
            [InlineKeyboardButton("📊 Spin Info", callback_data="spin_odds"),
             InlineKeyboardButton("🏆 Spin Medals", callback_data="spin_medals")],
            [InlineKeyboardButton("❌ Close", callback_data="spin_close")]
        ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    # Handle actual spins
    num_spins = 1 if data == "spin_single" else 10
    cost = spin_system.get_spin_cost(num_spins)

    if player.valor < cost:
        await query.answer(f"❌ Not enough Valor! Need {cost}, you have {player.valor}", show_alert=True)
        return

    # Deduct cost
    player.valor -= cost

    # Track community spins
    if user_id not in spin_system.spin_counts:
        spin_system.spin_counts[user_id] = 0
    spin_system.spin_counts[user_id] += num_spins

    # Get pity counter
    pity_counter = getattr(player, 'spin_pity_counter', 0)

    rewards = []
    total_medals = 0

    for _ in range(num_spins):
        item_key = spin_system.get_random_item(pity_counter)
        reward = spin_system.process_spin_reward(player, item_key)
        rewards.append(reward)

        # Award spin medals (1 per spin)
        total_medals += 1

        # Update pity counter
        if reward["rarity"] == "ultra_rare":
            pity_counter = 0
        else:
            pity_counter += 1

    # Update player data
    player.spin_pity_counter = pity_counter
    if not hasattr(player, 'spin_medals'):
        player.spin_medals = 0
    player.spin_medals += total_medals

    # Check spin streak bonus
    streak_bonus = spin_system.check_spin_streak_bonus(player)
    if streak_bonus:
        player.valor += 2  # 2 free Valor for 20 spin streak

    # Save player
    await db.update_player(user_id, {
        "valor": player.valor,
        "gas": player.gas,
        "marks": player.marks,
        "inventory": player.inventory,
        "owned_characters": player.owned_characters,
        "spin_pity_counter": pity_counter,
        "spin_medals": player.spin_medals,
        "updated_at": datetime.now(timezone.utc)
    })

    # Format results
    text = f"🎰 <b>Spin Results ({num_spins} spins)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for i, reward in enumerate(rewards, 1):
        rarity_emoji = {
            "common": "⚪",
            "uncommon": "🟢",
            "rare": "🟣",
            "ultra_rare": "🟡"
        }.get(reward["rarity"], "⚪")

        text += f"{i}. {rarity_emoji} <b>{reward['item_name']}</b>"
        if reward["amount"] > 1:
            text += f" x{reward['amount']}"
        if reward["duplicate"]:
            text += f" (Duplicate! +{reward['valor_refund']}💎)"
        text += "\n"

    if total_medals > 0:
        text += f"\n🎖️ <b>Spin Medals Earned:</b> +{total_medals}"

    if streak_bonus:
        text += f"\n🎯 <b>Streak Bonus:</b> +2💎 Valor"

    text += f"\n\n💎 <b>Remaining Valor:</b> {player.valor}"

    keyboard = [
        [InlineKeyboardButton("🔙 Back", callback_data="spin_menu"),
         InlineKeyboardButton("❌ Close", callback_data="spin_close")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)