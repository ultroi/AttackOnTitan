import random
from datetime import datetime, timedelta, timezone
import logging
from typing import Dict, List, Optional, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from database.models import Equipment, Player
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

class ShopSystem:
    def __init__(self):
        self.db = None
        self.shop_items = self._initialize_shop_items()
        self.hidden_items = {}  # Items that appear under special conditions
        self.rotation_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        self.last_refresh_times = {}  # Track last refresh times per user

    async def _get_db(self, context: ContextTypes.DEFAULT_TYPE) -> Database:
        """Get database instance from context."""
        db = context.bot_data.get("db")
        if not db:
            logger.error("Database not initialized in context.bot_data")
            raise ValueError("Database not initialized")
        return db

    def _initialize_shop_items(self) -> Dict[str, Equipment]:
        """Initialize all shop items with their specifications."""
        items = {
            "echo_shard": Equipment(
            name="Dual Blades / Ultrahard Steel Blades", type="echo_shards", rarity="rare", attributes={"damage_min": 40.0, "damage_max": 60.0, "accuracy": 25.0}, price=50000, currency="marks", description="Blades made of ultrahard steel."
            ),
            "combat_boots": Equipment(
            name="Thunder Spears", type="gear", rarity="epic", attributes={"damage_min": 80.0, "damage_max": 120.0}, price=10000, currency="marks", description="Spears that unleash thunderous attacks."
            ),
            "anti_titan_armor": Equipment(
            name="Rifles (Bolt-Action)", type="gear", rarity="uncommon", attributes={"damage_min": 30.0, "damage_max": 45.0}, price=6000, currency="valor", description="Bolt-action rifles."
            ),
            "time_contract": Equipment(
            name="Time Contract Scroll", type="utility", rarity="uncommon", attributes={
                "buff_name": "Chrono Edge",
                "cooldown_reduction": 1,
                "battles_remaining": 5,
                "flash_initiative": 3
            }, price=40000, currency="marks", description="Reduces cooldown of all combat abilities by 1 turn for 5 battles. Also grants 'Flash Initiative': guaranteed first strike in your next 3 PvE fights."
            ),
            "bounty_permit": Equipment(
            name="Bounty Permit", type="utility", rarity="uncommon", attributes={
                "buff_name": "Marked for Reward",
                "valor_drop_multiplier": 1.07,
                "elite_double_loot_chance": 0.15,
                "bounty_missions_unlocked": True,
                "buff_duration_minutes": 30
            }, price=50000, currency="marks", description="Unlocks access to hidden bounty missions. While active, +7% Valor drop rate and a 15% chance for double loot on elite Titan kills."
            ),
            "training_dummy": Equipment(
            name="Training Dummy", type="utility", rarity="common", attributes={
                "buff_name": "Training Precision",
                "attack_multiplier": 1.05,
                "xp_gain_multiplier": 1.10,
                "buff_duration_minutes": 15,
                "crit_rate_bonus_regiment": 5
            }, price=14000, currency="marks", description="Grants +5% Attack Power and +10% XP gain for 15 minutes after use. If used in a regiment zone, grants +5 Critical Hit Rate temporarily."
            ),
            "battle_journal": Equipment(
            name="Battle Journal", type="utility", rarity="common", attributes={
                "buff_name": "Combat Reflection",
                "journal_uses": 0,
                "accuracy_bonus": 5,
                "defense_bonus": 5,
                "buff_trigger_uses": 3
            }, price=24000, currency="marks", description="Logs enemy behavior patterns. After 3 uses, grants +5 Accuracy and +5 Defense against the last enemy."
            ),
            "titan_biology_manual": Equipment(
            name="Titan Biology Manual", type="utility", rarity="uncommon", attributes={
                "buff_name": "Anatomical Edge",
                "titan_damage_multiplier": 1.10,
                "intelligence_bonus": 20,
                "buff_duration_minutes": 30
            }, price=30000, currency="marks", description="All attacks against Titans deal +10% damage for 30 minutes. Additionally, +20 Intelligence against Abnormal or Intelligent Titans during that duration."
            ),
            "dual_blades": Equipment(
            name="Dual Blades / Ultrahard Steel Blades", type="weapon", rarity="legendary", attributes={"damage_min": 35, "damage_max": 50}, price=50000, currency="marks", description="Blades made of ultrahard steel."
            ),
            "bladed_gloves": Equipment(
            name="Bladed Gloves", type="weapon", rarity="legendary", attributes={"damage_min": 105, "damage_max": 145}, price=120000, currency="marks", description="Gloves with blades for close combat. High critical chance."
            ),
            "satchel_bombs": Equipment(
            name="Satchel Bombs (Grenades)", type="weapon", rarity="legendary", attributes={"damage_min": 65, "damage_max": 95}, price=80000, currency="marks", description="Grenades for anti-titan combat. High critical chance."
            ),
            "titan_serum": Equipment(
            name="Titan Serum Injections", type="weapon", rarity="legendary", attributes={"damage_min": 180, "damage_max": 220}, price=180000, currency="marks", description="Serum that transforms the user into a titan."
            ),
            "colossal_power": Equipment(
            name="Colossal Titan Transformation", type="weapon", rarity="legendary", attributes={"damage_min": 275, "damage_max": 310}, price=490000, currency="marks", description="Grants the power of the Colossal Titan."
            ),
            "control_rod": Equipment(
            name="Founding Titan Control Rod", type="weapon", rarity="legendary", attributes={"damage_min": 300, "damage_max": 375}, price=600000, currency="marks", description="Rod to control the Founding Titan."
            ),
            "warhammer_weapons": Equipment(
            name="Warhammer Constructed Weapons", type="weapon", rarity="legendary", attributes={"damage_min": 275, "damage_max": 330}, price=500000, currency="marks", description="Weapons constructed by the Warhammer Titan."
            ),
            "warhammer_titan_weapons": Equipment(
            name="Warhammer Titan's Constructed Weapons", type="weapon", rarity="legendary", attributes={"damage_min": 210, "damage_max": 255}, price=400000, currency="marks", description="Weapons constructed by the Warhammer Titan."
            ),
            # Military category weapons
            "airship_machine_guns": Equipment(
            name="Airship-Mounted Machine Guns", type="barracks", rarity="legendary", attributes={"damage_min": 175, "damage_max": 235}, price=200000, currency="marks", description="Heavy machine guns mounted on airships for aerial combat."
            ),
            "airship_machine_guns_v2": Equipment(
            name="Airship-Mounted Machine Guns (v2)", type="barracks", rarity="legendary", attributes={"damage_min": 200, "damage_max": 250}, price=380000, currency="marks", description="Upgraded version of airship machine guns with improved firing rate and accuracy."
            ),
            "naval_ship_cannons": Equipment(
            name="Naval Ship Cannons", type="barracks", rarity="legendary", attributes={"damage_min": 300, "damage_max": 375}, price=600000, currency="marks", description="Powerful cannons from naval vessels, repurposed for anti-Titan warfare."
            ),
        }
        # self.hidden_items is intentionally left empty
        return items

    async def show_shop(self, context: ContextTypes.DEFAULT_TYPE, user_id: str, category: str = "main") -> tuple[str, InlineKeyboardMarkup]:
        """Display the shop interface."""
        await self._check_daily_refresh(user_id)
        db = await self._get_db(context)
        player = await db.get_player(user_id)
        if not player:
            return "❌ Player not found! Create a profile with /start.", None
        if category == "main":
            return await self._show_main_shop(context, player)
        return await self._show_category(context, player, category)

    async def _show_main_shop(self, context: ContextTypes.DEFAULT_TYPE, player: Player) -> tuple[str, InlineKeyboardMarkup]:
        """Show main shop interface with categories."""
        now = datetime.now(timezone.utc)
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time_until_refresh = next_midnight - now
        
        # Calculate time left for shop refresh
        total_seconds = int(time_until_refresh.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_left = f"{hours:02d}h {minutes:02d}m {seconds:02d}s"
    
        refresh_cost = await self._get_refresh_cost(context, user_id=player.user_id)

        header = (
            "═════════════════════════════\n"
            "       <b>ATTACK ON TITAN SHOP</b>       \n"
            "═════════════════════════════\n\n"
            "<b>💰 Your Resources</b>\n"
            f"🎯 Marks:    <code>{player.marks:,}</code>\n"
            f"💎 Crystals: <code>{player.crystal:,}</code>\n"
            f"⚡ Valor:    <code>{player.valor:,}</code>\n"
            f"🛢️ Gas:      <code>{player.gas:,}</code>\n\n"
            "<b>💱 Exchange Rates</b>\n"
            "• 2 Marks    ➜ 1 Gas\n"
            "• 25,000 Marks ➜ 1 Crystal\n"
            "• 500 Marks   ➜ 1 Valor\n"
            "• 1 Crystal   ➜ 50 Valor\n\n"
            "<b>📋 How to Purchase</b>\n"
            "<code>/buy item_name quantity</code>\n"
            "Example: <code>/buy gas 20</code> or <code>/buy crystal 100</code>\n\n"
            "<b>⏰ Shop Information</b>\n"
            f"• <b>Next Free Refresh:</b> {time_left}\n"
            f"• <b>Manual Refresh Cost:</b> {refresh_cost} Valor\n\n"
            "═════════════════════════════\n"
        )
        keyboard = [
            [InlineKeyboardButton("⚔️ Weapons", callback_data="shop_weapons"),
             InlineKeyboardButton("🔷 Echo Shards", callback_data="shop_echo_shards")],
            [InlineKeyboardButton("🛡️ Gear", callback_data="shop_gear"),
             InlineKeyboardButton("🌀 Utilities", callback_data="shop_utilities")],
            [InlineKeyboardButton("🏛️ Military Quarter", callback_data="shop_barracks"),
             InlineKeyboardButton("💀 Black Market", callback_data="shop_hollow")],
            [InlineKeyboardButton(f"🔄 Refresh Shop ({refresh_cost} Valor)", callback_data="shop_refresh")]
        ]
        return header, InlineKeyboardMarkup(keyboard)

    async def _show_category(self, context: ContextTypes.DEFAULT_TYPE, player: Player, category: str) -> tuple[str, InlineKeyboardMarkup]:
        """Show items in a specific category with pagination."""

        category_items = self._get_category_items(category)
        available_items = list(category_items.items())
        if not available_items:
            message = f"🚫 No items available in this category or you don't meet the requirements."
            keyboard = [[InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")]]
            return message, InlineKeyboardMarkup(keyboard)

        # Use random selection for shop items, persist per user until refresh
        paged_items = self._get_random_shop_items(category, str(player.user_id), context)
        
        # Sort items by price (ascending order)
        paged_items.sort(key=lambda x: x[1].price)
        paged_items = paged_items[:10]  # Limit to 10 items per section

        category_names = {
            "weapons": "⚔️ Weapons",
            "echo_shards": "🔷 Echo Shards",
            "gear": "🛡️ Gear",
            "utilities": "🌀 Utilities",
            "barracks": "🏛️ Barracks Quartermaster",
            "hollow": "💀 Black Market"
        }
        
        # Header with divider
        message = f"<b>{category_names.get(category, category.title())}</b>\n"
        message += "═══════════════════════\n\n"
        
        keyboard = []
        db = await self._get_db(context)

        row = []
        for idx, (item_key, item) in enumerate(paged_items, start=1):
            price_str = f"{item.price:,} {item.currency.title()}"
            rarity_emoji = {"common": "⚪", "uncommon": "🟢", "rare": "🔵", "epic": "🟣", "legendary": "🟡"}
            
            # Get rarity symbol and create consistent header format
            rarity_symbol = rarity_emoji.get(item.rarity, '⚪')
            item_header = f"{rarity_symbol} <b>{idx}. {item.name}</b>"
            
            # Handle damage attributes consistently for all item types
            damage_info = ""
            if "damage_min" in item.attributes and "damage_max" in item.attributes:
                damage_min = item.attributes["damage_min"]
                damage_max = item.attributes["damage_max"]
                damage_info = f" | ⚔️ DMG: <code>{damage_min}-{damage_max}</code>"
            
            # Format price information consistently
            price_info = f"💰 <b>{price_str}</b>{damage_info}"
            
            # Collect all other attributes
            other_attrs = []
            important_attrs = ["speed", "accuracy", "defense", "area_damage"]
            for attr_name in important_attrs:
                if attr_name in item.attributes and item.attributes[attr_name]:
                    attr_display_name = attr_name.replace('_', ' ').title()
                    attr_value = item.attributes[attr_name]
                    other_attrs.append(f"{attr_display_name}: <code>{attr_value}</code>")
            
            # Construct the full item text with consistent formatting
            item_text = f"{item_header}\n{price_info}\n"
            
            if other_attrs:
                item_text += f"📊 {' | '.join(other_attrs)}\n"
                
            # Always add description at the end
            item_text += f"📝 <i>{item.description}</i>\n\n"
            
            message += item_text
            
            # Add purchase buttons for affordable items
            if await self._can_afford(player, item):
                row.append(InlineKeyboardButton(f"🛒 {idx}", callback_data=f"buy_{item_key}"))
                if len(row) == 3:
                    keyboard.append(row)
                    row = []
        
        if row:
            keyboard.append(row)
            
        keyboard.append([InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")])
        message += "\n<i>Buttons will only show for items you can afford and purchase.</i>"
        
        return message, InlineKeyboardMarkup(keyboard)

    def _get_category_items(self, category: str) -> Dict[str, Equipment]:
        """Get items for a specific category."""
        if category == "weapons":
            return {k: v for k, v in self.shop_items.items() if v.type == "weapon"}
        elif category == "echo_shards":
            return {k: v for k, v in self.shop_items.items() if v.type == "echo_shard"}
        elif category == "gear":
            return {k: v for k, v in self.shop_items.items() if v.type == "gear"}
        elif category == "utilities":
            return {k: v for k, v in self.shop_items.items() if v.type == "utility"}
        elif category == "barracks":
            return {k: v for k, v in self.shop_items.items() if v.type in ["weapon", "gear"] and v.rarity in ["common", "uncommon"]}
        elif category == "hollow":
            regular_items = {k: v for k, v in self.shop_items.items() if v.rarity in ["rare", "epic", "legendary"]}
            regular_items.update(self.hidden_items)
            return regular_items
        return {}

    async def _can_afford(self, player: Player, item: Equipment) -> bool:
        """Check if player can afford the item."""
        if item.currency == "marks":
            return player.marks >= item.price
        elif item.currency == "crystal":
            return player.crystal >= item.price
        elif item.currency == "valor":
            return player.valor >= item.price
        return False

    async def purchase_item(self, context: ContextTypes.DEFAULT_TYPE, user_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        """Purchase an item from the shop."""
        try:
            # Check if player is in PVP battle
            from game.pvp_system import active_pvp_battles
            if user_id in active_pvp_battles:
                return {"success": False, "message": "⚔️ You cannot buy items during PVP battles!"}
                
            if quantity <= 0:
                raise ValueError("Quantity must be positive")
            db = await self._get_db(context)
            player = await db.get_player(user_id)
            if not player:
                return {"success": False, "message": "Player not found"}
            item = self.shop_items.get(item_name) or self.hidden_items.get(item_name)
            if not item:
                return {"success": False, "message": "Item not found"}
           

            total_cost = item.price * quantity
            if item.currency == "marks" and player.marks < total_cost:
                return {"success": False, "message": "Not enough marks"}
            elif item.currency == "valor" and player.valor < total_cost:
                return {"success": False, "message": "Not enough valor"}
            elif item.currency == "crystal" and player.crystal < total_cost:
                return {"success": False, "message": "Not enough crystals"}

            player.inventory[item_name] = player.inventory.get(item_name, 0) + quantity
            if item.currency == "marks":
                player.marks -= total_cost
            elif item.currency == "valor":
                player.valor -= total_cost
            elif item.currency == "crystal":
                player.crystal -= total_cost

            shop_exp = player.calculate_exp_gain("shop_purchase", quantity)
            player.xp += shop_exp
            player.total_xp += shop_exp
            level_ups = 0
            while player.xp >= player.xp_to_next_level:
                player.level_up()
                level_ups += 1

            await db.update_player(player.user_id, {
                "marks": player.marks,
                "valor": player.valor,
                "crystal": player.crystal,
                "inventory": player.inventory,
                "xp": player.xp,
                "total_xp": player.total_xp,
                "level": player.level,
                "updated_at": datetime.now(timezone.utc)
            })
            for _ in range(quantity):
                await db.record_purchase(user_id, item_name)

            message = f"Successfully purchased {quantity}x {item.name}"
            if shop_exp > 0:
                message += f"\nEXP gained: {shop_exp:,}"
                if level_ups > 0:
                    message += f"\nLevel up! You're now level {player.level}!"
            return {"success": True, "message": message, "exp_gained": shop_exp, "level_ups": level_ups}
        except (ValueError, PyMongoError) as e:
            logger.error(f"Error in purchase_item for user {user_id}: {e}")
            return {"success": False, "message": f"Error purchasing {item_name}: {str(e)}"}

    async def buy_currency(self, context: ContextTypes.DEFAULT_TYPE, user_id: str, currency_type: str, amount: int) -> str:
        """Handle currency purchases and exchanges."""
        try:
            # Check if player is in PVP battle
            from game.pvp_system import active_pvp_battles
            if user_id in active_pvp_battles:
                return "⚔️ You cannot buy currency during PVP battles!"
                
            if amount <= 0:
                raise ValueError("Amount must be positive")
            db = await self._get_db(context)
            player = await db.get_player(user_id)
            if not player:
                return "❌ Player not found! Create a profile with /start."

            if currency_type == "gas":
                cost = amount * 2
                if player.marks < cost:
                    return f"❌ Insufficient marks! You need {cost:,} marks for {amount:,} gas."
                await db.update_player(user_id, {"marks": player.marks - cost, "gas": player.gas + amount})
                return f"✅ Successfully purchased {amount:,} gas for {cost:,} marks."

            elif currency_type == "crystal":
                valor_cost = amount * 50
                if player.valor < valor_cost:
                    return f"❌ Insufficient valor! You need {valor_cost:,} valor for {amount:,} crystals."
                await db.update_player(user_id, {"valor": player.valor - valor_cost, "crystal": player.crystal + amount})
                return f"✅ Successfully purchased {amount:,} crystals for {valor_cost:,} valor."

            elif currency_type == "valor":
                cost = amount * 500
                if player.marks < cost:
                    return f"❌ Insufficient marks! You need {cost:,} marks for {amount:,} valor points."
                await db.update_player(user_id, {"marks": player.marks - cost, "valor": player.valor + amount})
                return f"✅ Successfully purchased {amount:,} valor points for {cost:,} marks."

            elif currency_type == "marks":
                marks_gained = amount * 25000
                if player.crystal < amount:
                    return f"❌ Insufficient crystals! You need {amount:,} crystals."
                await db.update_player(user_id, {"crystal": player.crystal - amount, "marks": player.marks + marks_gained})
                return f"✅ Successfully exchanged {amount:,} crystals for {marks_gained:,} marks."

            return "❌ Invalid currency type. Use: gas, crystals, valor, or marks."
        except (ValueError, PyMongoError) as e:
            logger.error(f"Error in buy_currency for user {user_id}: {e}")
            return f"❌ Error purchasing currency: {str(e)}"

    async def _get_refresh_cost(self, context: ContextTypes.DEFAULT_TYPE, user_id: str) -> int:
        """Get the current refresh cost for a user."""
        db = await self._get_db(context)
        count = await db.get_shop_refresh_count(user_id)
        return 150 + (50 * count)

    async def refresh_shop(self, context: ContextTypes.DEFAULT_TYPE, user_id: str) -> str:
        """Handle manual shop refresh."""
        try:
            db = await self._get_db(context)
            player = await db.get_player(user_id)
            if not player:
                return "❌ Player not found!"
            refresh_cost = await self._get_refresh_cost(context, user_id)
            if player.valor < refresh_cost:
                return f"❌ Insufficient valor! Shop refresh costs {refresh_cost} valor."

            await db.update_player(user_id, {"valor": player.valor - refresh_cost})
            await db.store_shop_refresh(user_id, (await db.get_shop_refresh_count(user_id)) + 1)
            
            # Update last refresh time for this user
            self.last_refresh_times[user_id] = datetime.now(timezone.utc)
            
            # Clear the user's random shop selection so new items are shown
            self._clear_random_shop_items(str(user_id), context)
            
            next_cost = refresh_cost + 50
            return f"✅ Shop refreshed successfully!"
        except (ValueError, PyMongoError) as e:
            logger.error(f"Error in refresh_shop for user {user_id}: {e}")
            return f"❌ Error refreshing shop: {str(e)}"

    async def handle_callback(self, context: ContextTypes.DEFAULT_TYPE, user_id: str, callback_data: str) -> Optional[tuple[str, InlineKeyboardMarkup] | tuple[str, InlineKeyboardMarkup, bool]]:
        """Handle shop-related callback queries."""
        try:
            if callback_data == "shop_refresh":
                refresh_result = await self.refresh_shop(context, user_id)
                shop_message, shop_keyboard = await self.show_shop(context, user_id)
                # Show popup alert for shop refresh
                update = context.update if hasattr(context, 'update') else None
                if update and hasattr(update, 'callback_query') and update.callback_query:
                    await context.bot.answer_callback_query(
                        callback_query_id=update.callback_query.id,
                        text="✅ Shop refreshed successfully!",
                        show_alert=True
                    )
                combined_message = f"{shop_message}"
                return combined_message, shop_keyboard
            
            elif callback_data.startswith("buy_"):
                item_key = callback_data.replace("buy_", "")
                result = await self.purchase_item(context, user_id, item_key)
                keyboard = [[InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")]]
                return result["message"], InlineKeyboardMarkup(keyboard)
            
            elif callback_data.startswith("shop_"):
                category = callback_data.replace("shop_", "")
                return await self.show_shop(context, user_id, category)
            
            return None
        
        except (ValueError, PyMongoError) as e:
            logger.error(f"Error in handle_callback for user {user_id}: {e}")
            return f"❌ Error handling shop action: {str(e)}", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")]])

    async def _check_daily_refresh(self, user_id: str):
        """Check and handle daily shop refresh for a specific user."""
        current_time = datetime.now(timezone.utc)
        midnight = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Check if we need to do a daily refresh (after midnight)
        if current_time >= midnight and self.rotation_date < midnight:
            self.rotation_date = midnight
            # Clear the user's random shop selection
            self._clear_random_shop_items(user_id)
            # Reset refresh counts in database (handled by get_shop_refresh_count)

    def _get_random_shop_items(self, category: str, user_id: str, context: ContextTypes.DEFAULT_TYPE) -> list:
        """Get a randomized list of items for the user and category, store in bot_data for persistence."""
        all_items = list(self._get_category_items(category).items())
        all_keys = set(k for k, _ in all_items)
        # Use bot_data for persistence
        bot_data = context.bot_data if hasattr(context, 'bot_data') else {}
        if 'shop_random_selections' not in bot_data:
            bot_data['shop_random_selections'] = {}
        shop_random_selections = bot_data['shop_random_selections']
        key = f"{user_id}_{category}"
        # For all categories (including hollow), persist selection until refresh
        if key in shop_random_selections:
            selected_keys = shop_random_selections[key]
            expected_len = 6 if category == "hollow" else 12
            if (
                len(selected_keys) == expected_len and
                all(k in all_keys for k in selected_keys)
            ):
                selected = [item for item in all_items if item[0] in selected_keys]
                # Sort by price (ascending order)
                selected.sort(key=lambda x: x[1].price)
                return selected
        # If not present or invalid, randomize and store
        if category == "hollow":
            pick_count = min(6, len(all_items))
            selected = random.sample(all_items, pick_count)
        else:
            pick_count = min(12, len(all_items))
            selected = random.sample(all_items, pick_count)
        # Store the keys for persistence
        shop_random_selections[key] = [item[0] for item in selected]
        # Sort by price (ascending order)
        selected.sort(key=lambda x: x[1].price)
        return selected

    def _clear_random_shop_items(self, user_id: str, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
        """Clear the user's random shop selection for all categories from bot_data."""
        bot_data = context.bot_data if context is not None and hasattr(context, 'bot_data') else {}
        if 'shop_random_selections' not in bot_data:
            return
        shop_random_selections = bot_data['shop_random_selections']
        for category in ["weapons", "echo_shards", "gear", "utilities", "barracks", "hollow"]:
            key = f"{user_id}_{category}"
            if key in shop_random_selections:
                del shop_random_selections[key]
            

shop_system = ShopSystem()
