import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from database.db_instance import get_database
from database.models import Equipment

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
        self.rotation_date = None
        self.hidden_items = {}  # Items that appear under special conditions
    
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
        db = await self._get_db()
        player = await db.players.find_one({"user_id": user_id})
        if not player:
            return "❌ Character not found! Create a character first.", None
        
        if category == "main":
            return await self._show_main_shop(player)
        else:
            return await self._show_category(player, category)
    
    async def _show_main_shop(self, player: Dict) -> tuple:
        """Show main shop interface with categories"""
        currencies = (
            f"💰 **Your Currencies:**\n"
            f"🎯 Marks: `{player.get('marks', 0):,}`\n"
            f"💎 Titan Crystals: `{player.get('crystals', 0):,}`\n"
            f"⚡ Valor Points: `{player.get('valor', 0):,}`\n\n"
        )
        
        message = (
            f"🏪 **ATTACK ON TITAN SHOP**\n"
            f"═══════════════════════\n\n"
            f"{currencies}"
            f"🛒 **Shop Categories:**\n"
            f"Choose a category to browse items:\n\n"
            f"💱 **Exchange Rates:**\n"
            f"• 1 Titan Crystal = 125 Valor Points\n"
            f"• 1 Valor Point = 1,000 Marks\n\n"
            f"🔄 **Shop rotates every 3-4 days!**"
        )
        
        keyboard = [
            [InlineKeyboardButton("⚔️ Weapons", callback_data="shop_weapons"),
             InlineKeyboardButton("🔷 Echo Shards", callback_data="shop_echo_shards")],
            [InlineKeyboardButton("🛡️ Gear", callback_data="shop_gear"),
             InlineKeyboardButton("🌀 Utilities", callback_data="shop_utilities")],
            [InlineKeyboardButton("🏛️ Barracks Quartermaster", callback_data="shop_barracks"),
             InlineKeyboardButton("💀 Hollow Exchange", callback_data="shop_hollow")],
            [InlineKeyboardButton("💱 Currency Exchange", callback_data="shop_exchange"),
             InlineKeyboardButton("🔄 Refresh Shop", callback_data="shop_refresh")]
        ]
        
        return message, InlineKeyboardMarkup(keyboard)
    
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
            if await self._can_afford(player, item) and (item.stock_limit == -1 or remaining > 0):
                keyboard.append([InlineKeyboardButton(f"🛒 Buy {item.name}", callback_data=f"buy_{item_key}")])
        
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
            return player.get("crystals", 0) >= item.price
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
    
    async def purchase_item(self, user_id: int, item_key: str) -> str:
        """Handle item purchase"""
        db = await self._get_db()
        player = await db.players.find_one({"user_id": user_id})
        if not player:
            return "❌ Character not found!"
        
        # Check if item exists
        item = self.shop_items.get(item_key) or self.hidden_items.get(item_key)
        if not item:
            return "❌ Item not found!"
        
        # Check unlock conditions
        if not self._check_unlock_conditions(player, item):
            return "❌ You don't meet the requirements for this item!"
        
        # Check affordability
        if not await self._can_afford(player, item):
            return f"❌ Insufficient {item.currency}! You need {item.price:,} {item.currency}."
        
        # Check stock limits
        if item.stock_limit > 0:
            purchases_today = await self._get_daily_purchases(user_id, item_key)
            if purchases_today >= item.stock_limit:
                return f"❌ Daily purchase limit reached for {item.name}!"
        
        # Check cooldowns
        if item.cooldown_hours > 0:
            last_purchase = await db.shop_purchases.find_one({
                "user_id": user_id,
                "item_key": item_key
            }, sort=[("purchase_date", -1)])
            
            if last_purchase:
                cooldown_end = last_purchase["purchase_date"] + timedelta(hours=item.cooldown_hours)
                if datetime.utcnow() < cooldown_end:
                    remaining = cooldown_end - datetime.utcnow()
                    hours = int(remaining.total_seconds() // 3600)
                    minutes = int((remaining.total_seconds() % 3600) // 60)
                    return f"❌ Item on cooldown! {hours}h {minutes}m remaining."
        
        # Process purchase
        update_data = {}
        if item.currency == "marks":
            update_data["marks"] = player.get("marks", 0) - item.price
        elif item.currency == "crystals":
            update_data["crystals"] = player.get("crystals", 0) - item.price
        elif item.currency == "valor":
            update_data["valor"] = player.get("valor", 0) - item.price
        
        # Add item to inventory
        if item.item_type in ["weapon", "gear"]:
            # Create equipment entry
            equipment = Equipment(
                name=item.name,
                type=item.item_type,
                rarity=item.rarity,
                durability=item.durability,
                weight=item.weight,
                attributes=item.attributes
            )
            
            # Add to player's equipment
            if "equipment" not in player:
                player["equipment"] = []
            player["equipment"].append(equipment.dict())
            update_data["equipment"] = player["equipment"]
        
        elif item.item_type == "echo_shard":
            update_data["echo_shards"] = player.get("echo_shards", 0) + 1
        
        elif item.item_type == "utility":
            # Add to utility items
            if "utility_items" not in player:
                player["utility_items"] = {}
            player["utility_items"][item_key] = player["utility_items"].get(item_key, 0) + 1
            update_data["utility_items"] = player["utility_items"]
        
        # Update player data
        await db.players.update_one(
            {"user_id": user_id},
            {"$set": update_data}
        )
        
        # Record purchase
        await db.shop_purchases.insert_one({
            "user_id": user_id,
            "item_key": item_key,
            "item_name": item.name,
            "price": item.price,
            "currency": item.currency,
            "purchase_date": datetime.utcnow()
        })
        
        success_msg = f"✅ **Purchase Successful!**\n\n"
        success_msg += f"🛒 **Item:** {item.name}\n"
        success_msg += f"💰 **Cost:** {item.price:,} {item.currency.title()}\n"
        
        if item.damage_range:
            success_msg += f"⚔️ **Damage:** {item.damage_range}\n"
        
        if item.cooldown_hours > 0:
            success_msg += f"⏰ **Cooldown:** {item.cooldown_hours} hours\n"
        
        success_msg += f"\n📝 *{item.description}*"
        
        return success_msg
    
    async def exchange_currency(self, user_id: int, from_currency: str, to_currency: str, amount: int) -> str:
        """Handle currency exchange"""
        db = await self._get_db()
        player = await db.players.find_one({"user_id": user_id})
        if not player:
            return "❌ Character not found!"
        
        # Define exchange rates (as per your specification)
        rates = {
            ("crystals", "valor"): 125,  # 1 Crystal = 125 Valor
            ("valor", "marks"): 1000,    # 1 Valor = 1000 Marks
            ("crystals", "marks"): 125000  # 1 Crystal = 125,000 Marks (via valor)
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

# Initialize shop system
shop_system = ShopSystem()
