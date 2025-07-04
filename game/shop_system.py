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
                name="Echo Shard", type="echo_shard", rarity="common", durability=100, weight=0.1, attributes={}, price=100, currency="marks", stock_limit=10, cooldown_hours=0, description="A mysterious shard with echoing power."
            ),
            "combat_boots": Equipment(
                name="Combat Boots", type="gear", rarity="common", durability=80, weight=2.5, attributes={"speed": 5, "durability": 15}, price=500, currency="marks", stock_limit=5, cooldown_hours=0, description="Boots that increase your speed and durability."
            ),
            "anti_titan_armor": Equipment(
                name="Anti-Titan Armor Vest", type="gear", rarity="uncommon", durability=100, weight=8.0, attributes={"defense": 20, "durability": 25}, price=2000, currency="marks", stock_limit=2, cooldown_hours=0, description="Armor vest for protection against titans."
            ),
            "time_contract": Equipment(
                name="Time Contract Scroll", type="utility", rarity="uncommon", durability=100, weight=0.1, attributes={}, price=1500, currency="marks", stock_limit=5, cooldown_hours=0, description="A scroll that contracts time."
            ),
            "bounty_permit": Equipment(
                name="Bounty Permit", type="utility", rarity="uncommon", durability=100, weight=0.1, attributes={}, price=2000, currency="marks", stock_limit=5, cooldown_hours=0, description="Permit to claim bounties."
            ),
            "training_dummy": Equipment(
                name="Training Dummy", type="utility", rarity="common", durability=100, weight=0.1, attributes={}, price=500, currency="marks", stock_limit=10, cooldown_hours=0, description="Dummy for training purposes."
            ),
            "battle_journal": Equipment(
                name="Battle Journal", type="utility", rarity="common", durability=100, weight=0.1, attributes={}, price=300, currency="marks", stock_limit=10, cooldown_hours=0, description="Journal to record battles."
            ),
            "titan_biology_manual": Equipment(
                name="Titan Biology Manual", type="utility", rarity="uncommon", durability=100, weight=0.1, attributes={}, price=1200, currency="marks", stock_limit=5, cooldown_hours=0, description="Manual containing titan biology."
            ),
            "dual_blades": Equipment(
                name="Dual Blades / Ultrahard Steel Blades", type="weapon", rarity="rare", durability=85, weight=3.5, attributes={"attack": 40, "accuracy": 25}, price=5000, currency="marks", stock_limit=3, cooldown_hours=0, description="Blades made of ultrahard steel."
            ),
            "odm_gear": Equipment(
                name="Vertical Maneuvering Equipment (ODM Gear)", type="weapon", rarity="rare", durability=90, weight=15.0, attributes={"speed": 50, "maneuverability": 40}, price=7000, currency="marks", stock_limit=3, cooldown_hours=0, description="Equipment for vertical maneuvering."
            ),
            "thunder_spears": Equipment(
                name="Thunder Spears", type="weapon", rarity="epic", durability=1, weight=4.0, attributes={"attack": 80, "armor_piercing": 60}, price=10000, currency="marks", stock_limit=1, cooldown_hours=0, description="Spears that unleash thunderous attacks."
            ),
            "anti_personnel_odm": Equipment(
                name="Anti-Personnel ODM Gear", type="weapon", rarity="epic", durability=85, weight=12.0, attributes={"attack": 35, "speed": 45, "human_effective": 50}, price=9000, currency="marks", stock_limit=2, cooldown_hours=0, description="ODM gear effective against humans."
            ),
            "pistols": Equipment(
                name="Double-Barreled Pistols", type="weapon", rarity="uncommon", durability=70, weight=1.5, attributes={"attack": 25, "speed": 30}, price=2500, currency="marks", stock_limit=5, cooldown_hours=0, description="Pistols with double barrels."
            ),
            "rifles": Equipment(
                name="Rifles (Bolt-Action)", type="weapon", rarity="uncommon", durability=75, weight=4.0, attributes={"attack": 30, "range": 40}, price=3000, currency="marks", stock_limit=5, cooldown_hours=0, description="Bolt-action rifles."
            ),
            "sniper_rifles": Equipment(
                name="Sniper Rifles", type="weapon", rarity="rare", durability=80, weight=6.0, attributes={"attack": 45, "range": 80, "accuracy": 50}, price=6000, currency="marks", stock_limit=3, cooldown_hours=0, description="Rifles designed for long-range precision."
            ),
            "machine_guns": Equipment(
                name="Machine Guns / Mounted Guns", type="weapon", rarity="epic", durability=90, weight=25.0, attributes={"attack": 60, "area_damage": 30}, price=12000, currency="marks", stock_limit=2, cooldown_hours=0, description="Guns that fire bullets in rapid succession."
            ),
            "anti_titan_cannons": Equipment(
                name="Anti-Titan Cannons (Wall-Mounted)", type="weapon", rarity="epic", durability=95, weight=500.0, attributes={"attack": 75, "range": 100}, price=15000, currency="marks", stock_limit=1, cooldown_hours=0, description="Cannons mounted on walls to fight titans."
            ),
            "mobile_artillery": Equipment(
                name="Mobile Artillery / Anti-Titan Mortars", type="weapon", rarity="epic", durability=85, weight=300.0, attributes={"attack": 70, "area_damage": 50}, price=13000, currency="marks", stock_limit=1, cooldown_hours=0, description="Artillery that can be moved and used against titans."
            ),
            "titan_restraints": Equipment(
                name="Titan Restraint Traps", type="weapon", rarity="epic", durability=60, weight=50.0, attributes={"restraint": 80, "durability_bonus": 20}, price=11000, currency="marks", stock_limit=2, cooldown_hours=0, description="Traps designed to restrain titans."
            ),
            "titan_guillotine": Equipment(
                name="Titan Guillotine (Executioner from Hell)", type="weapon", rarity="epic", durability=70, weight=200.0, attributes={"attack": 100, "execution_chance": 25}, price=14000, currency="marks", stock_limit=1, cooldown_hours=0, description="A guillotine designed for titans."
            ),
            "anti_titan_grenades": Equipment(
                name="Anti-Titan Grenades / Satchel Bombs", type="weapon", rarity="rare", durability=1, weight=2.0, attributes={"attack": 50, "area_damage": 40}, price=3500, currency="marks", stock_limit=5, cooldown_hours=0, description="Grenades effective against titans."
            ),
            "control_rod": Equipment(
                name="Founding Titan Control Rod", type="weapon", rarity="legendary", durability=50, weight=5.0, attributes={"special_ability": 100}, price=20000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Control rod for the Founding Titan."
            ),
            "warhammer_weapons": Equipment(
                name="Warhammer Titan's Constructed Weapons", type="weapon", rarity="legendary", durability=80, weight=8.0, attributes={"attack": 65, "adaptability": 40}, price=18000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Weapons constructed by the Warhammer Titan."
            ),
            "titan_serum": Equipment(
                name="Titan Serum Injections", type="weapon", rarity="legendary", durability=1, weight=0.1, attributes={"transformation": 100}, price=22000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Serum that transforms the user into a titan."
            ),
            "colossal_power": Equipment(
                name="Colossal Titan Transformation", type="weapon", rarity="legendary", durability=1, weight=0.0, attributes={"nuclear_strike": 200}, price=25000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Grants the power of the Colossal Titan."
            ),
            "bladed_gloves": Equipment(
                name="Bladed Gloves", type="weapon", rarity="legendary", durability=75, weight=1.0, attributes={"attack": 45, "assassination": 60}, price=17000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Gloves with blades for close combat."
            ),
            "airship_guns": Equipment(
                name="Airship-Mounted Machine Guns", type="weapon", rarity="legendary", durability=95, weight=1000.0, attributes={"attack": 120, "aerial_combat": 80}, price=30000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Machine guns mounted on airships."
            ),
            "naval_cannons": Equipment(
                name="Naval Ship Cannons", type="weapon", rarity="legendary", durability=98, weight=2000.0, attributes={"attack": 150, "naval_combat": 100}, price=35000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Cannons used on naval ships."
            )
        }
        self.hidden_items = {
            "shadow_echo_shard": Equipment(
                name="Shadow-Enhanced Echo Shard", type="echo_shard", rarity="legendary", durability=100, weight=0.1, attributes={}, price=10000, currency="crystal", stock_limit=1, cooldown_hours=24, description="A legendary shard with shadow powers."
            ),
            "titan_serum_fragment": Equipment(
                name="Fragmented Titan Serum", type="utility", rarity="legendary", durability=100, weight=0.1, attributes={}, price=5000, currency="crystal", stock_limit=1, cooldown_hours=24, description="A rare fragment of titan serum."
            ),
            "stolen_military_tech": Equipment(
                name="Stolen Military Tech Gear", type="gear", rarity="legendary", durability=100, weight=8.0, attributes={}, price=8000, currency="crystal", stock_limit=1, cooldown_hours=24, description="Advanced gear stolen from the military."
            )
        }
        return items

    async def show_shop(self, context: ContextTypes.DEFAULT_TYPE, user_id: str, category: str = "main") -> tuple[str, InlineKeyboardMarkup]:
        """Display the shop interface."""
        await self.check_daily_refresh()
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
        hours = int(time_until_refresh.total_seconds() // 3600)
        minutes = int((time_until_refresh.total_seconds() % 3600) // 60)
        refresh_cost = await self._get_refresh_cost(context, user_id=player.user_id)

        header = (
            "<b>ATTACK ON TITAN SHOP</b>\n"
            "═══════════════════════\n\n"
            "<b> Your Resources</b>\n"
            f"🎯 Marks: <code>{player.marks:,}</code>\n"
            f"💎 Crystals: <code>{player.crystal:,}</code>\n"
            f"⚡ Valor: <code>{player.valor:,}</code>\n"
            f"🛢️ Gas: <code>{player.gas:,}</code>\n\n"
            "<b>💱 Exchange</b>\n"
            "• 2 Marks ➜ 1 Gas\n"
            "• 50000 Marks ➜ 1 Crystal\n"
            "• 1250 Marks ➜ 1 Valor\n"
            "• 1 Crystal ➜ 40 Valor\n\n"
            "<code>/buy item_name quantity</code>\nE.g., /buy gas 20 or /buy crystal 100\n\n"
            "<b>⏰ Shop Information</b>\n"
            f"• Next Free Refresh: {hours}h {minutes}m\n"
            f"• Manual Refresh Cost: {refresh_cost} Valor\n\n"
            "<b>🛍️ SHOP CATEGORIES</b>\n"
            "═══════════════════════\n"
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
        available_items = [(key, item) for key, item in category_items.items() if self._check_unlock_conditions(player, item)]
        if not available_items:
            message = f"🚫 No items available in this category or you don't meet the requirements."
            keyboard = [[InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")]]
            return message, InlineKeyboardMarkup(keyboard)

        # Use random selection for shop items, persist per user until refresh
        paged_items = self._get_random_shop_items(category, str(player.user_id), context)

        category_names = {
            "weapons": "⚔️ Weapons",
            "echo_shards": "🔷 Echo Shards",
            "gear": "🛡️ Gear",
            "utilities": "🌀 Utilities",
            "barracks": "🏛️ Barracks Quartermaster",
            "hollow": "💀 Hollow Exchange"
        }
        message = f" <b>{category_names.get(category, category.title())}</b>\n═══════════════════════\n\n"
        keyboard = []
        db = await self._get_db(context)

        row = []
        for idx, (item_key, item) in enumerate(paged_items, start=1):
            price_str = f"{item.price:,} {item.currency.title()}"
            damage_info = f" | DMG: {getattr(item, 'damage_range', '')}" if getattr(item, 'damage_range', None) else ""
            rarity_emoji = {"common": "⚪", "uncommon": "🟢", "rare": "🔵", "epic": "🟣", "legendary": "🟡"}
            purchases_today = await db.get_daily_purchases(player.user_id, item_key)
            remaining = max(0, item.stock_limit - purchases_today) if item.stock_limit > 0 else None
            last_purchase = await db.shop_purchases_collection.find_one(
                {"user_id": player.user_id, "item_key": item_key},
                sort=[("purchase_date", -1)]
            )
            can_purchase = True
            if item.cooldown_hours > 0 and last_purchase:
                last_time = last_purchase["purchase_date"]
                if (datetime.now(timezone.utc) - last_time).total_seconds() < item.cooldown_hours * 3600:
                    can_purchase = False

            item_text = (
                f"{rarity_emoji.get(item.rarity, '⚪')} <b>{idx}. {item.name}</b>\n"
                f"💰 <b>{price_str}</b>{damage_info}\n"
                f"📝 {item.description}\n"
            )
            if remaining is not None:
                item_text += f"📦 Stock: {remaining}/{item.stock_limit}\n"
            if not can_purchase:
                item_text += f"⏳ Cooldown: Wait {(item.cooldown_hours * 3600 - (datetime.now(timezone.utc) - last_time).total_seconds()) / 3600:.1f} hours\n"

            message += item_text + "\n"
            if await self._can_afford(player, item) and can_purchase and (item.stock_limit == -1 or (remaining is not None and remaining > 0)):
                row.append(InlineKeyboardButton(f"🛒 {idx}", callback_data=f"buy_{item_key}"))
                if len(row) == 3:
                    keyboard.append(row)
                    row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")])
        new_markup = InlineKeyboardMarkup(keyboard)
        # Check for duplicate message and markup to avoid Telegram 400 error
        if context.user_data is None:
            context.user_data = {}
        last_text = context.user_data.get(f"last_shop_text_{category}", None)
        last_markup = context.user_data.get(f"last_shop_markup_{category}", None)
        if last_text == message and str(last_markup) == str(new_markup):
            # No change, do not edit
            return message, new_markup
        context.user_data[f"last_shop_text_{category}"] = message
        context.user_data[f"last_shop_markup_{category}"] = str(new_markup)
        # Ensure message is not empty before returning
        if not message.strip():
            message = "🚫 No items available in this category or you don't meet the requirements."
        return message, new_markup

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

    def _check_unlock_conditions(self, player: Player, item: Equipment) -> bool:
        """Check if player meets item unlock conditions."""
        for condition, requirement in item.unlock_conditions.items():
            if condition == "level" and player.level < requirement:
                return False
            elif condition == "valor" and player.valor < requirement:
                return False
            elif condition == "rank" and player.rank != requirement:
                return False
            elif condition == "birthplace" and player.birthplace != requirement:
                return False
            elif condition == "hollow_token" and player.get("hollow_tokens", 0) < requirement:
                return False
            elif condition == "elite_contract_complete" and player.get("elite_contracts_completed", 0) < requirement:
                return False
        return True

    async def _can_afford(self, player: Player, item: Equipment) -> bool:
        """Check if player can afford the item."""
        if item.currency == "marks":
            return player.marks >= item.price
        elif item.currency == "crystals":
            return player.crystal >= item.price
        elif item.currency == "valor":
            return player.valor >= item.price
        return False

    async def purchase_item(self, context: ContextTypes.DEFAULT_TYPE, user_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        """Purchase an item from the shop."""
        try:
            if quantity <= 0:
                raise ValueError("Quantity must be positive")
            db = await self._get_db(context)
            player = await db.get_player(user_id)
            if not player:
                return {"success": False, "message": "Player not found"}
            item = self.shop_items.get(item_name) or self.hidden_items.get(item_name)
            if not item:
                return {"success": False, "message": "Item not found"}
            if not self._check_unlock_conditions(player, item):
                return {"success": False, "message": f"You don't meet the requirements for {item.name}"}
            purchases_today = await db.get_daily_purchases(user_id, item_name)
            if item.stock_limit > 0 and purchases_today + quantity > item.stock_limit:
                return {"success": False, "message": f"Stock limit exceeded for {item.name}"}
            last_purchase = await db.shop_purchases_collection.find_one(
                {"user_id": user_id, "item_key": item_name},
                sort=[("purchase_date", -1)]
            )
            if item.cooldown_hours > 0 and last_purchase:
                last_time = last_purchase["purchase_date"]
                if (datetime.now(timezone.utc) - last_time).total_seconds() < item.cooldown_hours * 3600:
                    return {"success": False, "message": f"{item.name} is on cooldown for {(item.cooldown_hours * 3600 - (datetime.now(timezone.utc) - last_time).total_seconds()) / 3600:.1f} hours"}

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
                valor_cost = amount * 40
                if player.valor < valor_cost:
                    return f"❌ Insufficient valor! You need {valor_cost:,} valor for {amount:,} crystals."
                await db.update_player(user_id, {"valor": player.valor - valor_cost, "crystal": player.crystal + amount})
                return f"✅ Successfully purchased {amount:,} crystals for {valor_cost:,} valor."

            elif currency_type == "valor":
                cost = amount * 1250
                if player.marks < cost:
                    return f"❌ Insufficient marks! You need {cost:,} marks for {amount:,} valor points."
                await db.update_player(user_id, {"marks": player.marks - cost, "valor": player.valor + amount})
                return f"✅ Successfully purchased {amount:,} valor points for {cost:,} marks."

            elif currency_type == "marks":
                marks_gained = amount * 50000
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
            self.rotation_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            # Clear the user's random shop selection so new items are shown
            self._clear_random_shop_items(str(user_id), context)
            next_cost = refresh_cost + 50
            return f"✅ Shop refreshed successfully!\nSpent: {refresh_cost} valor\nNext refresh will cost: {next_cost} valor"
        except (ValueError, PyMongoError) as e:
            logger.error(f"Error in refresh_shop for user {user_id}: {e}")
            return f"❌ Error refreshing shop: {str(e)}"

    async def handle_callback(self, context: ContextTypes.DEFAULT_TYPE, user_id: str, callback_data: str) -> Optional[tuple[str, InlineKeyboardMarkup]]:
        """Handle shop-related callback queries."""
        try:
            if callback_data == "shop_refresh":
                refresh_result = await self.refresh_shop(context, user_id)
                # Always show the same shop UI after refresh, but send a popup alert
                update = getattr(context, 'update', None)
                callback_query = getattr(update, 'callback_query', None) if update else None
                if "✅" in refresh_result:
                    # Send popup alert (show_alert=True)
                    if hasattr(context, 'bot') and callback_query:
                        await context.bot.answer_callback_query(
                            callback_query.id,
                            text="All items refreshed!",
                            show_alert=True
                        )
                    # Show the same shop UI (items will be randomized)
                    return await self.show_shop(context, user_id)
                return refresh_result, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")]])
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

    async def check_daily_refresh(self):
        """Check and handle daily shop refresh."""
        current_time = datetime.now(timezone.utc)
        midnight = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        if current_time >= midnight and self.rotation_date < midnight:
            self.rotation_date = midnight
            # Reset refresh counts in database (handled by get_shop_refresh_count)

    def _get_random_shop_items(self, category: str, user_id: str, context: ContextTypes.DEFAULT_TYPE) -> list:
        """Get a randomized list of 12 items for the user and category, store in user_data."""
        all_items = list(self._get_category_items(category).items())
        if len(all_items) <= 12:
            return all_items
        # Use user_data to persist the random selection per user per category
        user_data = context.user_data if context.user_data is not None else {}
        key = f"shop_random_{category}_{user_id}"
        if key in user_data:
            selected_keys = user_data[key]
            selected = [item for item in all_items if item[0] in selected_keys]
            # If for some reason not enough items, re-randomize
            if len(selected) == 12:
                return selected
        # Randomly select 12 unique items
        selected = random.sample(all_items, 12)
        user_data[key] = [item[0] for item in selected]  # Only update the key, do not reassign user_data
        return selected

    def _clear_random_shop_items(self, user_id: str, context: ContextTypes.DEFAULT_TYPE):
        """Clear the user's random shop selection for all categories."""
        user_data = context.user_data if context.user_data is not None else {}
        for category in ["weapons", "echo_shards", "gear", "utilities", "barracks", "hollow"]:
            key = f"shop_random_{category}_{user_id}"
            if key in user_data:
                del user_data[key]

shop_system = ShopSystem()
