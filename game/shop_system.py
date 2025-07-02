import random
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from database.db_instance import get_database
from database.models import Equipment

logger = logging.getLogger(__name__)

class ShopItem:
    def __init__(self, name: str, item_type: str, price: int, currency: str = "marks", 
                 rarity: str = "common", description: str = "", damage_range: str = "",
                 stock_limit: int = -1, cooldown_hours: int = 0, unlock_conditions: Optional[Dict] = None,
                 attributes: Optional[Dict] = None, weight: float = 0.0, durability: int = 100):
        self.name = name
        self.item_type = item_type  # weapon, echo_shard, gear, utility, contract
        self.price = price
        self.currency = currency
        self.rarity = rarity
        self.description = description
        self.damage_range = damage_range
        self.stock_limit = stock_limit  # -1 means unlimited
        self.cooldown_hours = cooldown_hours
        self.unlock_conditions = unlock_conditions or {}
        self.attributes = attributes or {}
        self.weight = weight
        self.durability = durability

class ShopSystem:
    def __init__(self):
        self.db = None
        self.shop_items = self._initialize_shop_items()
        # Initialize with the start of the current day
        now = datetime.utcnow()
        self.rotation_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.hidden_items = {}  # Items that appear under special conditions
        self.refresh_costs = {}  # Track refresh costs per user
    
    async def _get_db(self):
        """Get database instance"""
        if self.db is None:
            self.db = await get_database()
        return self.db
        
    def _initialize_shop_items(self) -> Dict[str, ShopItem]:
        """Initialize all shop items with their specifications"""
        items = {}
        
        # Echo Shards
        items["echo_shard"] = ShopItem(
            name="Echo Shard",
            item_type="echo_shard",
            price=850,
            description="Level-up currency usable to enhance Echo traits or unlock trait branches.",
            rarity="common"
        )
        
        # Gear
        items["combat_boots"] = ShopItem(
            name="Combat Boots",
            item_type="gear",
            price=9000,
            description="Standard military boots providing basic protection and mobility.",
            rarity="common",
            attributes={"speed": 5, "durability": 15},
            weight=2.5,
            durability=80
        )
        
        items["anti_titan_armor"] = ShopItem(
            name="Anti-Titan Armor Vest",
            item_type="gear",
            price=13000,
            description="Reinforced armor vest designed to protect against Titan encounters.",
            rarity="uncommon",
            attributes={"defense": 20, "durability": 25},
            weight=8.0,
            durability=100
        )
        
        # Utilities
        items["time_contract"] = ShopItem(
            name="Time Contract Scroll",
            item_type="utility",
            price=1600,
            description="Allows participation in time-limited special missions.",
            rarity="uncommon",
            cooldown_hours=24
        )
        
        items["bounty_permit"] = ShopItem(
            name="Bounty Permit",
            item_type="utility",
            price=2000,
            description="Grants access to high-reward bounty missions.",
            rarity="uncommon",
            cooldown_hours=48
        )
        
        items["training_dummy"] = ShopItem(
            name="Training Dummy",
            item_type="utility",
            price=750,
            description="Practice target for skill improvement.",
            rarity="common",
            cooldown_hours=12
        )
        
        items["battle_journal"] = ShopItem(
            name="Battle Journal",
            item_type="utility",
            price=800,
            description="Records combat experience for enhanced learning.",
            rarity="common"
        )
        
        items["titan_biology_manual"] = ShopItem(
            name="Titan Biology Manual",
            item_type="utility",
            price=1100,
            description="Study guide providing insights into Titan weaknesses.",
            rarity="uncommon"
        )
        
        # Standard Anti-Titan Weapons
        items["dual_blades"] = ShopItem(
            name="Dual Blades / Ultrahard Steel Blades",
            item_type="weapon",
            price=60000,
            description="Standard melee weapon used with ODM gear. Made from ultrahard steel.",
            damage_range="120-180",
            rarity="rare",
            attributes={"attack": 40, "accuracy": 25},
            weight=3.5,
            durability=85
        )
        
        items["odm_gear"] = ShopItem(
            name="Vertical Maneuvering Equipment (ODM Gear)",
            item_type="weapon",
            price=55000,
            description="Essential for combat mobility. Equipped with blade holders and grappling hooks.",
            damage_range="0-0 (Mobility)",
            rarity="rare",
            attributes={"speed": 50, "maneuverability": 40},
            weight=15.0,
            durability=90
        )
        
        items["thunder_spears"] = ShopItem(
            name="Thunder Spears",
            item_type="weapon",
            price=75000,
            description="High-explosive, armor-piercing projectiles for heavy Titans.",
            damage_range="200-300",
            rarity="epic",
            attributes={"attack": 80, "armor_piercing": 60},
            weight=4.0,
            durability=1  # Single use
        )
        
        items["anti_personnel_odm"] = ShopItem(
            name="Anti-Personnel ODM Gear",
            item_type="weapon",
            price=75000,
            description="Modified ODM gear with repeating pistols for human combat.",
            damage_range="80-120",
            rarity="epic",
            attributes={"attack": 35, "speed": 45, "human_effective": 50},
            weight=12.0,
            durability=85
        )
        
        # Firearms
        items["pistols"] = ShopItem(
            name="Double-Barreled Pistols",
            item_type="weapon",
            price=30000,
            description="Compact and fast-firing pistols for close combat.",
            damage_range="60-90",
            rarity="uncommon",
            attributes={"attack": 25, "speed": 30},
            weight=1.5,
            durability=70
        )
        
        items["rifles"] = ShopItem(
            name="Rifles (Bolt-Action)",
            item_type="weapon",
            price=35000,
            description="Basic military firearms effective against humans.",
            damage_range="80-110",
            rarity="uncommon",
            attributes={"attack": 30, "range": 40},
            weight=4.0,
            durability=75
        )
        
        items["sniper_rifles"] = ShopItem(
            name="Sniper Rifles",
            item_type="weapon",
            price=25000,
            description="Long-range precision weapons for tactical advantage.",
            damage_range="100-150",
            rarity="rare",
            attributes={"attack": 45, "range": 80, "accuracy": 50},
            weight=6.0,
            durability=80
        )
        
        items["machine_guns"] = ShopItem(
            name="Machine Guns / Mounted Guns",
            item_type="weapon",
            price=100000,
            description="Heavy weapons for infantry suppression.",
            damage_range="150-200",
            rarity="epic",
            attributes={"attack": 60, "area_damage": 30},
            weight=25.0,
            durability=90
        )
        
        items["anti_titan_cannons"] = ShopItem(
            name="Anti-Titan Cannons (Wall-Mounted)",
            item_type="weapon",
            price=125000,
            description="Heavy artillery for wall defense.",
            damage_range="200-250",
            rarity="epic",
            attributes={"attack": 75, "range": 100},
            weight=500.0,
            durability=95
        )
        
        items["mobile_artillery"] = ShopItem(
            name="Mobile Artillery / Anti-Titan Mortars",
            item_type="weapon",
            price=150000,
            description="Vehicle-mounted bombardment weapons.",
            damage_range="180-230",
            rarity="epic",
            attributes={"attack": 70, "area_damage": 50},
            weight=300.0,
            durability=85
        )
        
        # Experimental & Heavy Weapons
        items["titan_restraints"] = ShopItem(
            name="Titan Restraint Traps",
            item_type="weapon",
            price=100000,
            description="Combination of spears, cables, and anchors to immobilize Titans.",
            damage_range="0-0 (Restraint)",
            rarity="epic",
            attributes={"restraint": 80, "durability_bonus": 20},
            weight=50.0,
            durability=60
        )
        
        items["titan_guillotine"] = ShopItem(
            name="Titan Guillotine (Executioner from Hell)",
            item_type="weapon",
            price=90000,
            description="Large blade rig for mass Titan execution.",
            damage_range="300-400",
            rarity="epic",
            attributes={"attack": 100, "execution_chance": 25},
            weight=200.0,
            durability=70
        )
        
        items["anti_titan_grenades"] = ShopItem(
            name="Anti-Titan Grenades / Satchel Bombs",
            item_type="weapon",
            price=85000,
            description="Explosive devices for urban Titan encounters.",
            damage_range="120-180",
            rarity="rare",
            attributes={"attack": 50, "area_damage": 40},
            weight=2.0,
            durability=1  # Single use
        )
        
        # Special/Unique Weapons
        items["control_rod"] = ShopItem(
            name="Founding Titan Control Rod",
            item_type="weapon",
            price=200000,
            description="Rare artifact with mysterious Titan control properties.",
            damage_range="Variable",
            rarity="legendary",
            attributes={"special_ability": 100},
            weight=5.0,
            durability=50,
            unlock_conditions={"level": 50, "valor": 1000}
        )
        
        items["warhammer_weapons"] = ShopItem(
            name="Warhammer Titan's Constructed Weapons",
            item_type="weapon",
            price=175000,
            description="Crystallized weapons with adaptive properties.",
            damage_range="180-250",
            rarity="legendary",
            attributes={"attack": 65, "adaptability": 40},
            weight=8.0,
            durability=80,
            unlock_conditions={"level": 40}
        )
        
        items["titan_serum"] = ShopItem(
            name="Titan Serum Injections",
            item_type="weapon",
            price=350000,
            description="Biological warfare agent of immense power.",
            damage_range="???",
            rarity="legendary",
            attributes={"transformation": 100},
            weight=0.1,
            durability=1,
            unlock_conditions={"level": 75, "valor": 2000}
        )
        
        items["colossal_power"] = ShopItem(
            name="Colossal Titan Transformation",
            item_type="weapon",
            price=500000,
            description="Nuclear-level explosive transformation ability.",
            damage_range="500-1000",
            rarity="legendary",
            attributes={"nuclear_strike": 200},
            weight=0.0,
            durability=1,
            unlock_conditions={"level": 100, "valor": 5000}
        )
        
        items["bladed_gloves"] = ShopItem(
            name="Bladed Gloves",
            item_type="weapon",
            price=250000,
            description="Hand-based blades for close-quarters assassination.",
            damage_range="90-140",
            rarity="legendary",
            attributes={"attack": 45, "assassination": 60},
            weight=1.0,
            durability=75,
            unlock_conditions={"level": 35}
        )
        
        # Military Vehicle Weaponry (Ultra Rare)
        items["airship_guns"] = ShopItem(
            name="Airship-Mounted Machine Guns",
            item_type="weapon",
            price=2350000,
            description="Heavy aerial combat systems.",
            damage_range="300-400",
            rarity="legendary",
            attributes={"attack": 120, "aerial_combat": 80},
            weight=1000.0,
            durability=95,
            unlock_conditions={"level": 80, "valor": 3000}
        )
        
        items["naval_cannons"] = ShopItem(
            name="Naval Ship Cannons",
            item_type="weapon",
            price=2500000,
            description="Massive ship-mounted artillery systems.",
            damage_range="400-500",
            rarity="legendary",
            attributes={"attack": 150, "naval_combat": 100},
            weight=2000.0,
            durability=98,
            unlock_conditions={"level": 85, "valor": 4000}
        )
        
        # Hollow Exchange Exclusives (Hidden until unlocked)
        self.hidden_items = {
            "shadow_echo_shard": ShopItem(
                name="Shadow-Enhanced Echo Shard",
                item_type="echo_shard",
                price=0,  # Special acquisition only
                description="Forbidden Echo enhancement with unknown power.",
                rarity="legendary",
                unlock_conditions={"hollow_token": 1}
            ),
            "titan_serum_fragment": ShopItem(
                name="Fragmented Titan Serum",
                item_type="utility",
                price=0,  # Mission unlock only
                description="Mysterious Titan serum fragment.",
                rarity="legendary",
                unlock_conditions={"elite_contract_complete": 5}
            ),
            "stolen_military_tech": ShopItem(
                name="Stolen Military Tech Gear",
                item_type="gear",
                price=0,  # Rank unlock only
                description="High-tech military equipment of questionable origin.",
                rarity="legendary",
                unlock_conditions={"rank": "Elite", "valor": 1500}
            )
        }
        
        return items
    
    async def show_shop(self, user_id: int, category: str = "main") -> tuple:
        """Display the shop interface"""
        await self.check_daily_refresh()  # Check for daily refresh
        
        db = await self._get_db()
        player = await db.players.find_one({"user_id": user_id})
        if not player:
            return "❌ Character not found! Create a character first.", None
            
        # Ensure user_id is in the player data for refresh cost tracking
        player['user_id'] = user_id
        
        if category == "main":
            return await self._show_main_shop(player)
        else:
            return await self._show_category(player, category)
    
    async def _show_main_shop(self, player: Dict) -> tuple:
        """Show main shop interface with categories"""
        marks = player.get('marks', 0)
        crystals = player.get('crystal', 0)
        gas = player.get('gas', 0)
        valor = player.get('valor', 0)
        user_id = player.get('user_id')

        # Calculate time until next midnight (00:00)
        now = datetime.utcnow()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time_until_refresh = next_midnight - now
        hours = int(time_until_refresh.total_seconds() // 3600)
        minutes = int((time_until_refresh.total_seconds() % 3600) // 60)

        # Ensure user_id is an integer
        user_id_int = int(user_id) if user_id is not None else 0
        refresh_cost = await self._get_refresh_cost(user_id_int)

        header = (
            "🏪 *ATTACK ON TITAN SHOP*\n"
            "═══════════════════════\n\n"
            "💰 *Your Resources*\n"
            f"🎯 Marks: `{marks:,}`\n"
            f"💎 Crystals: `{crystals:,}`\n"
            f"⚡ Valor: `{valor:,}`\n"
            f"🛢️ Gas: `{gas:,}`\n\n"
            "� *Currency Exchange*\n"
            "• 2 Marks ➜ 1 Gas\n"
            "• 1250 Marks ➜ 1 Valor\n"
            "• 40 Valor ➜ 1 Crystal\n"
            "• 1 Crystal ➜ 50000 Marks\n\n"
            "`/buy <item_name> <quantity>`\n E.g - /buy gas 20 or /buy crystal 100\n\n"
            "⏰ *Shop Information*\n"
            f"• Next Free Refresh: {hours}h {minutes}m\n"
            f"• Manual Refresh Cost: {refresh_cost} Valor\n\n"
            "🛍️ *SHOP CATEGORIES*\n"
            "═══════════════════════\n"
        )
        
        keyboard = [
            [InlineKeyboardButton("⚔️ Weapons", callback_data="shop_weapons"),
             InlineKeyboardButton("🔷 Echo Shards", callback_data="shop_echo_shards")],
            [InlineKeyboardButton("🛡️ Gear", callback_data="shop_gear"),
             InlineKeyboardButton("🌀 Utilities", callback_data="shop_utilities")],
            [InlineKeyboardButton("🏛️ Military Quarter", callback_data="shop_barracks"),
             InlineKeyboardButton("💀 Black Market", callback_data="shop_hollow")],
            [InlineKeyboardButton("🔄 Refresh Shop", callback_data="shop_refresh")]
        ]
        
        return header, InlineKeyboardMarkup(keyboard)
    
    async def _show_category(self, player: Dict, category: str) -> tuple:
        """Show items in a specific category"""
        category_items = self._get_category_items(category)
        available_items = []
        
        for item_key, item in category_items.items():
            if self._check_unlock_conditions(player, item):
                available_items.append((item_key, item))
        
        if not available_items:
            message = f"🚫 No items available in this category or you don't meet the requirements."
            keyboard = [[InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")]]
            return message, InlineKeyboardMarkup(keyboard)
        
        category_names = {
            "weapons": "⚔️ Weapons",
            "echo_shards": "🔷 Echo Shards", 
            "gear": "🛡️ Gear",
            "utilities": "🌀 Utilities",
            "barracks": "🏛️ Barracks Quartermaster",
            "hollow": "💀 Hollow Exchange"
        }
        
        message = f"🏪 **{category_names.get(category, category.title())}**\n"
        message += "═══════════════════════\n\n"
        
        keyboard = []
        remaining = None
        
        for i, (item_key, item) in enumerate(available_items):
            price_str = f"{item.price:,} {item.currency.title()}"
            if item.damage_range:
                damage_info = f" | DMG: {item.damage_range}"
            else:
                damage_info = ""
            
            rarity_emoji = {"common": "⚪", "uncommon": "🟢", "rare": "🔵", "epic": "🟣", "legendary": "🟡"}
            
            item_text = (
                f"{rarity_emoji.get(item.rarity, '⚪')} **{item.name}**\n"
                f"💰 {price_str}{damage_info}\n"
                f"📝 {item.description}\n"
            )
            
            if item.stock_limit > 0:
                # Check player's purchase history for this item
                purchases_today = await self._get_daily_purchases(player['user_id'], item_key)
                remaining = max(0, item.stock_limit - purchases_today)
                item_text += f"📦 Stock: {remaining}/{item.stock_limit}\n"
            
            message += item_text + "\n"
            
            # Add buy button if player can afford and has stock
            if await self._can_afford(player, item):
                if item.stock_limit == -1 or (remaining is not None and remaining > 0):
                    buy_button = InlineKeyboardButton(f"🛒 Buy {item.name}", callback_data=f"buy_{item_key}")
                    keyboard.append([buy_button])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")])
        
        return message, InlineKeyboardMarkup(keyboard)
    
    def _get_category_items(self, category: str) -> Dict:
        """Get items for a specific category"""
        if category == "weapons":
            return {k: v for k, v in self.shop_items.items() if v.item_type == "weapon"}
        elif category == "echo_shards":
            return {k: v for k, v in self.shop_items.items() if v.item_type == "echo_shard"}
        elif category == "gear":
            return {k: v for k, v in self.shop_items.items() if v.item_type == "gear"}
        elif category == "utilities":
            return {k: v for k, v in self.shop_items.items() if v.item_type == "utility"}
        elif category == "barracks":
            # Regulated items (common/uncommon weapons and gear)
            return {k: v for k, v in self.shop_items.items() 
                   if v.item_type in ["weapon", "gear"] and v.rarity in ["common", "uncommon"]}
        elif category == "hollow":
            # Black market items (rare/epic/legendary + hidden items)
            regular_items = {k: v for k, v in self.shop_items.items() 
                           if v.rarity in ["rare", "epic", "legendary"]}
            regular_items.update(self.hidden_items)
            return regular_items
        else:
            return {}
    
    def _check_unlock_conditions(self, player: Dict, item: ShopItem) -> bool:
        """Check if player meets item unlock conditions"""
        if not item.unlock_conditions:
            return True
        
        for condition, requirement in item.unlock_conditions.items():
            if condition == "level" and player.get("level", 1) < requirement:
                return False
            elif condition == "valor" and player.get("valor", 0) < requirement:
                return False
            elif condition == "rank" and player.get("rank", "Cadet") != requirement:
                return False
            elif condition == "birthplace" and player.get("birthplace", "") != requirement:
                return False
            elif condition == "hollow_token" and player.get("hollow_tokens", 0) < requirement:
                return False
            elif condition == "elite_contract_complete" and player.get("elite_contracts_completed", 0) < requirement:
                return False
        
        return True
    
    async def _can_afford(self, player: Dict, item: ShopItem) -> bool:
        """Check if player can afford the item"""
        if item.currency == "marks":
            return player.get("marks", 0) >= item.price
        elif item.currency == "crystals":
            return player.get("crystal", 0) >= item.price
        elif item.currency == "valor":
            return player.get("valor", 0) >= item.price
        return False
    
    async def _get_daily_purchases(self, user_id: int, item_key: str) -> int:
        """Get number of times player purchased this item today"""
        db = await self._get_db()
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        purchases = await db.shop_purchases.count_documents({
            "user_id": user_id,
            "item_key": item_key,
            "purchase_date": {"$gte": today}
        })
        return purchases
    
    async def purchase_item(self, user_id: int, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        """Purchase an item from the shop"""
        db = await self._get_db()
        player = await db.get_player(user_id)
        
        if not player:
            return {"success": False, "message": "Player not found"}
            
        item = self.shop_items.get(item_name)
        if not item:
            return {"success": False, "message": "Item not found"}
            
        total_cost = item.price * quantity
        
        # Check if player has enough currency
        if item.currency == "marks":
            if player.marks < total_cost:
                return {"success": False, "message": "Not enough marks"}
            player.marks -= total_cost
        elif item.currency == "valor":
            if player.valor < total_cost:
                return {"success": False, "message": "Not enough valor"}
            player.valor -= total_cost
        elif item.currency == "crystal":
            if player.crystal < total_cost:
                return {"success": False, "message": "Not enough crystals"}
            player.crystal -= total_cost
            
        # Add item to inventory
        player.inventory[item_name] = player.inventory.get(item_name, 0) + quantity
        
        # Calculate and award shop EXP
        shop_exp = player.calculate_exp_gain('shop_purchase', quantity)
        player.xp += shop_exp
        player.total_xp += shop_exp
        
        # Check for level up
        level_ups = 0
        while player.xp >= player.xp_to_next_level:
            player.level_up()
            level_ups += 1
            
        # Update player in database
        await db.update_player(
            user_id=player.user_id,
            update_data={
                "marks": player.marks,
                "valor": player.valor,
                "crystal": player.crystal,
                "inventory": player.inventory,
                "xp": player.xp,
                "total_xp": player.total_xp,
                "level": player.level
            }
        )
        
        # Prepare success message
        message = f"Successfully purchased {quantity}x {item_name}"
        if shop_exp > 0:
            message += f"\nEXP gained: {shop_exp:,}"
            if level_ups > 0:
                message += f"\nLevel up! You're now level {player.level}!"
                
        return {
            "success": True,
            "message": message,
            "exp_gained": shop_exp,
            "level_ups": level_ups
        }
    
    async def exchange_currency(self, user_id: int, from_currency: str, to_currency: str, amount: int) -> str:
        """Handle currency exchange"""
        db = await self._get_db()
        player = await db.players.find_one({"user_id": user_id})
        if not player:
            return "❌ Character not found!"
        
        # Define exchange rates (as per your specification)
        rates = {
            ("crystals", "valor"): 25,    # 1 Crystal = 25 Valor
            ("valor", "marks"): 1250,     # 1 Valor = 1250 Marks
            ("crystals", "marks"): 31250  # 1 Crystal = 31250 Marks
        }
        
        if (from_currency, to_currency) not in rates:
            return "❌ Invalid currency exchange!"
        
        rate = rates[(from_currency, to_currency)]
        
        # Check if player has enough currency
        if player.get(from_currency, 0) < amount:
            return f"❌ Insufficient {from_currency}! You need {amount:,}."
        
        # Calculate exchange
        received = amount * rate
        
        # Update currencies
        update_data = {
            from_currency: player.get(from_currency, 0) - amount,
            to_currency: player.get(to_currency, 0) + received
        }
        
        await db.players.update_one(
            {"user_id": user_id},
            {"$set": update_data}
        )
        
        return (
            f"✅ **Exchange Successful!**\n\n"
            f"📤 **Exchanged:** {amount:,} {from_currency.title()}\n"
            f"📥 **Received:** {received:,} {to_currency.title()}\n"
            f"💱 **Rate:** 1 {from_currency} = {rate:,} {to_currency}"
        )
    
    async def show_shop_details(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Show shop with detailed header information including resources and buy guide"""
        db = await self._get_db()
        player = await db.players.find_one({"user_id": user_id}) or {}
        
        marks = player.get('marks', 0)
        crystals = player.get('crystal', 0)
        gas = player.get('gas', 0)
        valor = player.get('valor', 0)
        
        # Create detailed header with buy guide and resources
        header = (
            "🏪 *ATTACK ON TITAN SHOP*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💫 *Quick Buy Guide*\n"
            "🔸 `/buy gas <amount>`\n"
            "🔸 `/buy crystals <amount>`\n"
            "🔸 `/buy valor <amount>`\n"
            "🔸 `/buy marks <amount>`\n\n"
            "💰 *Your Resources*\n"
            f"• Marks: {marks:,}\n"
            f"• Crystals: {crystals:,}\n"
            f"• Gas: {gas:,}\n"
            f"• Valor: {valor:,}\n\n"
            "📦 *Exchange Rates*\n"
            "• 2 Marks ➜ 1 Gas\n"
            "• 31250 Marks ➜ 1 Crystal\n"
            "• 1250 Marks ➜ 1 Valor\n"
            "• 1 Crystal ➜ 100 Marks\n\n"
            "🛍️ *Available Items*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        # Get shop content
        shop_content, reply_markup = await self.show_shop(user_id)
        
        return header + shop_content, reply_markup
        
    def _get_rarity_icon(self, rarity: str) -> str:
        """Get colored circle emoji based on item rarity"""
        return {
            "common": "🟢",
            "uncommon": "🔵",
            "rare": "🟣",
            "epic": "🟡",
            "legendary": "🔴"
        }.get(rarity.lower(), "⚪")
    
    async def buy_currency(self, user_id: int, currency_type: str, amount: int) -> str:
        """Handle currency purchases and exchanges"""
        try:
            db = await self._get_db()
            player = await db.players.find_one({"user_id": user_id})
            if not player:
                return "❌ Character not found! Create a character first."

            marks = player.get('marks', 0)
            crystals = player.get('crystal', 0)
            valor = player.get('valor', 0)
            gas = player.get('gas', 0)

            if amount <= 0:
                return "❌ Please specify a positive amount."

            updates = {}
            msg = ""

            if currency_type == "gas":
                # 2 marks = 1 gas
                cost = amount * 2
                if marks < cost:
                    return f"❌ Insufficient marks! You need {cost:,} marks for {amount:,} gas."
                updates["$inc"] = {"marks": -cost, "gas": amount}
                msg = f"✅ Successfully purchased {amount:,} gas for {cost:,} marks."

            elif currency_type == "crystals":
                # 40 valor = 1 crystal
                valor_cost = amount * 40
                if valor < valor_cost:
                    return f"❌ Insufficient valor! You need {valor_cost:,} valor for {amount:,} crystals."
                updates["$inc"] = {"valor": -valor_cost, "crystal": amount}  # Fixed field name to match database
                msg = f"✅ Successfully purchased {amount:,} crystals for {valor_cost:,} valor."

            elif currency_type == "valor":
                # 1250 marks = 1 valor
                cost = amount * 1250
                if marks < cost:
                    return f"❌ Insufficient marks! You need {cost:,} marks for {amount:,} valor points."
                updates["$inc"] = {"marks": -cost, "valor": amount}
                msg = f"✅ Successfully purchased {amount:,} valor points for {cost:,} marks."

            elif currency_type == "marks":
                # Convert crystals to marks (1 crystal = 50000 marks)
                if crystals < amount:  # Updated variable name to match
                    return f"❌ Insufficient crystals! You need {amount:,} crystals."
                marks_gained = amount * 50000
                updates["$inc"] = {"crystal": -amount, "marks": marks_gained}  # Fixed field name to match database
                msg = f"✅ Successfully exchanged {amount:,} crystals for {marks_gained:,} marks."

            else:
                return "❌ Invalid currency type. Use: gas, crystals, valor, or marks."

            # Apply the updates
            result = await db.players.update_one(
                {"user_id": user_id},
                updates
            )

            if result.modified_count > 0:
                return msg
            else:
                return "❌ Failed to process the transaction. Please try again."

        except Exception as e:
            logger.error(f"Error in buy_currency: {e}")
            return "❌ An error occurred while processing your purchase. Please try again."

    async def _get_refresh_cost(self, user_id: int) -> int:
        """Get the current refresh cost for a user"""
        current_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if user_id not in self.refresh_costs or self.refresh_costs[user_id]["date"] != current_date:
            self.refresh_costs[user_id] = {"date": current_date, "count": 0}
        return 150 + (50 * self.refresh_costs[user_id]["count"])

    async def refresh_shop(self, user_id: int) -> str:
        """Handle manual shop refresh"""
        try:
            db = await self._get_db()
            player = await db.players.find_one({"user_id": user_id})
            if not player:
                return "❌ Character not found!"

            refresh_cost = await self._get_refresh_cost(user_id)
            valor = player.get('valor', 0)

            if valor < refresh_cost:
                return f"❌ Insufficient valor! Shop refresh costs {refresh_cost} valor."

            # Deduct valor and update refresh count
            await db.players.update_one(
                {"user_id": user_id},
                {"$inc": {"valor": -refresh_cost}}
            )

            current_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            if user_id not in self.refresh_costs:
                self.refresh_costs[user_id] = {"date": current_date, "count": 0}
            self.refresh_costs[user_id]["count"] += 1

            # Refresh shop items (you can add logic here to randomize available items)
            self.rotation_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            
            next_cost = refresh_cost + 50
            return f"✅ Shop refreshed successfully!\nSpent: {refresh_cost} valor\nNext refresh will cost: {next_cost} valor"

        except Exception as e:
            logger.error(f"Error in refresh_shop: {e}")
            return "❌ An error occurred while refreshing the shop."

    async def handle_callback(self, user_id: int, callback_data: str) -> Optional[tuple[str, InlineKeyboardMarkup]]:
        """Handle shop-related callback queries"""
        if callback_data == "shop_refresh":
            # Handle shop refresh
            refresh_result = await self.refresh_shop(user_id)
            if "✅" in refresh_result:
                # If refresh was successful, show updated shop
                return await self.show_shop(user_id)
            else:
                # If refresh failed, return the error message with an empty keyboard
                return refresh_result, InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")
                ]])
        elif callback_data.startswith("shop_"):
            # Handle other shop categories
            category = callback_data.replace("shop_", "")
            return await self.show_shop(user_id, category)
        return None

    async def check_daily_refresh(self):
        """Check and handle daily shop refresh"""
        current_time = datetime.utcnow()
        midnight = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Check if we've passed midnight since last refresh
        if current_time >= midnight and self.rotation_date < midnight:
            # Reset shop for new day
            self.rotation_date = midnight
            # Reset refresh costs for all users
            self.refresh_costs = {}

# Initialize shop system
shop_system = ShopSystem()
