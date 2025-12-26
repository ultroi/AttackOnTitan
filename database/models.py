from datetime import datetime, timezone, timedelta
import random
from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field
from database.characters import get_character_data, AbilityEffect
from database.schemas import Ability  

class BankAccount(BaseModel):
    user_id: str
    opened: bool = False
    opened_at: Optional[datetime] = None
    marks_balance: int = 0
    valor_balance: int = 0
    crystal_balance: int = 0
    last_deposit: Optional[datetime] = None
    penalty_applied: bool = False
    penalty_rate: float = 2.5
    penalty_start_date: Optional[datetime] = None
    penalty_warning_date: Optional[datetime] = None
    penalty_warning_sent: bool = False
    total_wealth: Optional[int] = None  # Added for central bank stats
    last_tax_check: Optional[datetime] = None  # Track when tax was last checked
    tax_history: List[Dict] = Field(default_factory=list)  # Track tax collection history

class CharacterStats(BaseModel):
    ATK: int = 10
    DEF: int = 10
    ACC: int = 10
    INT: int = 10
    SPD: int = 10
    HP: int = 650  # Increased default max HP for better balance

class Equipment(BaseModel):
    name: str
    type: str
    item_type: str = ""  # Add item_type for shop filtering compatibility
    rarity: str
    attributes: Dict[str, Any]
    currency: str = "marks"  # Default currency
    price: int = 0  # Default price
    description: str = ""  # Default empty description
    unlock_conditions: Dict[str, Any] = Field(default_factory=dict) 
    # unlock_conditions removed
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Character(BaseModel):
    user_id: str
    name: str
    character_type: str
    current_hp: int
    level: int = 1
    xp: int = 0
    total_xp: int = 0
    stats: Union[CharacterStats, Dict[str, Any]] = Field(default_factory=CharacterStats)
    gas: int = 5000
    max_gas: int = 5000 
    equipped_weapon: Optional[str] = None  
    active_abilities: List[Ability] = Field(default_factory=list)
    passive_abilities: List[Ability] = Field(default_factory=list)
    ultimate_abilities: List[Ability] = Field(default_factory=list)
    unlocked_abilities: Dict[str, bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __init__(self, **data):
        # Convert stats dict to CharacterStats if needed, or handle CharacterStats objects from different modules
        if 'stats' in data:
            stats_value = data['stats']
            if isinstance(stats_value, dict):
                data['stats'] = CharacterStats(**stats_value)
            elif hasattr(stats_value, '__dict__') and hasattr(stats_value, 'ATK'):  # It's a CharacterStats-like object
                # Convert to dict first, then to our CharacterStats
                stats_dict = {k: getattr(stats_value, k, 0) for k in ['ATK', 'DEF', 'ACC', 'INT', 'SPD', 'HP']}
                data['stats'] = CharacterStats(**stats_dict)
        
        super().__init__(**data)
        
        # Initialize max HP and current HP based on character_type
        # Only do this if character_type is set and stats need initialization
        if hasattr(self, 'character_type') and self.character_type:
            character_data = get_character_data(self.character_type)
            if character_data:
                # For all characters, ensure stats match their level
                base_stats = character_data.base_stats.dict() if hasattr(character_data, 'base_stats') else {}
                if base_stats:
                    for stat in ['ATK', 'DEF', 'ACC', 'INT', 'SPD']:
                        base_value = base_stats.get(stat, getattr(self.stats, stat, 10))
                        # Calculate current level's stat value
                        if hasattr(character_data, 'max_potential') and character_data.max_potential:
                            max_val = character_data.max_potential.get(stat, base_value)
                            # Linear scaling from base to max_potential over 125 levels
                            stat_increase = (max_val - base_value) / (125 - 1)
                            current_value = base_value + (stat_increase * (self.level - 1))
                            setattr(self.stats, stat, int(round(min(current_value, max_val))))
                        else:
                            setattr(self.stats, stat, base_value)
                
                # Set HP from character data
                expected_max_hp = character_data.get_max_hp(self.level)
                self.stats.HP = expected_max_hp
                # Ensure current_hp doesn't exceed max HP
                if not hasattr(self, 'current_hp') or self.current_hp > expected_max_hp:
                    self.current_hp = expected_max_hp

    @property
    def xp_to_next_level(self) -> int:
        max_level = 125
        level = min(self.level, max_level)
        base_exp = 3500
        multiplier = 1.35 ** (level - 1)
        xp = int(base_exp * multiplier)
        return min(xp, 125000)

    def calculate_combat_exp(self, turns: int, damage_taken: bool, overkill_damage: int, is_first_kill: bool) -> int:
        """Calculate combat EXP with bonuses"""
        # Base EXP (40-60)
        base_exp = random.randint(40, 60)
        
        # Apply bonuses
        total_exp = base_exp
        
        # Fast kill bonus (under 5 turns)
        if turns <= 5:
            total_exp *= 1.20  # +20%
            
        # No damage bonus
        if not damage_taken:
            total_exp *= 1.15  # +15%
            
        # Overkill bonus
        if overkill_damage > 0:
            total_exp *= 1.10  # +10%
            
        # First kill bonus
        if is_first_kill:
            total_exp *= 1.25  # +25%
            
        return int(total_exp)

    def get_abilities(self) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self, 'character_type') or not self.character_type:
            return {"active": {}, "passive": {}, "ultimate": {}}
            
        character_data = get_character_data(self.character_type)
        if character_data is None:
            return {"active": {}, "passive": {}, "ultimate": {}}
            
        return {
            "active": {a.name: a for a in getattr(character_data, "active_abilities", [])},
            "passive": {a.name: a for a in getattr(character_data, "passive_abilities", [])},
            "ultimate": {a.name: a for a in getattr(character_data, "ultimate_abilities", [])},
        }

    def level_up(self) -> Dict[str, Any]:
        """Level up character and return stat increases"""
        stat_increases = {}
        
        if self.level < 125:
            # Track initial stats for comparison
            old_stats = {}
            if hasattr(self, 'stats'):
                for stat in ['HP', 'ATK', 'DEF', 'ACC', 'INT', 'SPD']:
                    old_stats[stat] = getattr(self.stats, stat, 0)
                
            # Level up core logic
            self.level += 1
            # XP subtraction is now handled in add_xp method
            if self.xp < 0:
                self.xp = 0
                
            if hasattr(self, 'character_type') and self.character_type:
                character_data = get_character_data(self.character_type)
                if character_data:
                    # --- Progressive stat scaling toward max_potential ---
                    max_potential = getattr(character_data, 'max_potential', None)
                    base_stats = character_data.base_stats.dict() if hasattr(character_data, 'base_stats') else {}
                    if max_potential:
                        for stat in ['HP', 'ATK', 'DEF', 'ACC', 'INT', 'SPD']:
                            base = base_stats.get(stat, getattr(self.stats, stat, 0))
                            max_val = max_potential.get(stat, base)
                            # Linear scaling: stat increases each level to reach max_potential at level 125
                            stat_increase = (max_val - base) / (125 - 1)
                            old_val = getattr(self.stats, stat, 0)
                            new_val = old_val + stat_increase
                            
                            # Cap at max_potential
                            if stat == 'HP':
                                setattr(self.stats, stat, int(round(min(new_val, max_val))))
                            else:
                                setattr(self.stats, stat, int(round(min(new_val, max_val))))
                                
                    # Update max HP and current HP
                    new_max_hp = character_data.get_max_hp(self.level)
                    hp_increase = new_max_hp - self.stats.HP
                    self.stats.HP = new_max_hp
                    self.current_hp = min(self.current_hp + hp_increase, new_max_hp)
                    
            # Calculate stat increases by comparing old and new values
            if hasattr(self, 'stats'):
                for stat in ['HP', 'ATK', 'DEF', 'ACC', 'INT', 'SPD']:
                    new_val = getattr(self.stats, stat, 0)
                    old_val = old_stats.get(stat, 0)
                    if new_val > old_val:
                        stat_increases[stat] = new_val - old_val
                
            self.max_gas += 250
            self.gas = self.max_gas 
            # Check for ability unlocks
            self._check_ability_unlocks()
        
        return stat_increases

    def _check_ability_unlocks(self) -> None:
        if not hasattr(self, 'character_type') or not self.character_type:
            return
            
        character_data = get_character_data(self.character_type)
        if character_data:
            all_abilities = []
            for ability_list_name in ["active_abilities", "passive_abilities", "ultimate_abilities"]:
                ability_list = getattr(character_data, ability_list_name, [])
                all_abilities.extend(ability_list)
            
            for ability in all_abilities:
                if self.level >= ability.level_required:
                    ability.is_unlocked = True
                    self.unlocked_abilities[ability.name] = True

    def add_xp(self, amount: int) -> Dict[str, Any]:
        if amount < 0 and abs(amount) > self.xp:
            amount = -self.xp
        
        self.xp += amount
        if self.xp < 0:
            self.xp = 0
            
        # Only add to total_xp if amount is positive
        if amount > 0:
            self.total_xp += amount
        
        level_ups = []
        while self.xp >= self.xp_to_next_level:
            old_level = self.level
            
            # Store the current xp_to_next before leveling up
            current_xp_to_next = self.xp_to_next_level
            # Perform level up and get stat increases
            stat_increases = self.level_up()
            # Subtract the xp_to_next that was used for the check
            self.xp -= current_xp_to_next
            new_level = self.level
            
            # Track what was unlocked at this level
            newly_unlocked = self._get_newly_unlocked_abilities(new_level)
            hp_increase = self._get_hp_increase(old_level, new_level)
            
            # If HP increase is not in stat_increases, add it
            if 'HP' not in stat_increases and hp_increase > 0:
                stat_increases['HP'] = hp_increase
                
            level_ups.append({
                "old_level": old_level,
                "new_level": new_level,
                "newly_unlocked_abilities": newly_unlocked,
                "hp_increase": hp_increase,
                "stat_increases": stat_increases
            })
        
        return {
            "level_ups": level_ups,
            "total_level_ups": len(level_ups),
            "current_level": self.level,
            "current_xp": self.xp,
            "xp_to_next": self.xp_to_next_level
        }

    def _get_newly_unlocked_abilities(self, level: int) -> List[Dict]:
        """Get abilities that were unlocked at this specific level."""
        newly_unlocked = []
        abilities = self.get_abilities()
        
        for ability_type, abilities_dict in abilities.items():
            for ability_name, ability in abilities_dict.items():
                if hasattr(ability, 'level_required') and ability.level_required == level:
                    newly_unlocked.append({
                        "name": getattr(ability, 'name', ability_name),
                        "type": ability_type,
                        "description": getattr(ability, 'description', 'No description available')
                    })
        
        return newly_unlocked
    
    def _get_hp_increase(self, old_level: int, new_level: int) -> int:
        """Calculate HP increase from level up."""
        if not hasattr(self, 'character_type') or not self.character_type:
            return 0
            
        character_data = get_character_data(self.character_type)
        if character_data:
            old_hp = character_data.get_max_hp(old_level)
            new_hp = character_data.get_max_hp(new_level)
            return new_hp - old_hp
        return 0

    def unlock_abilities(self) -> None:
        # Unlock abilities based on level requirements for all types
        if not hasattr(self, 'character_type') or not self.character_type:
            return
            
        abilities = self.get_abilities()
        
        for ability_type in ["passive", "active", "ultimate"]:
            for ability_name, ability in abilities.get(ability_type, {}).items():
                level_required = getattr(ability, 'level_required', 1)
                if self.level >= level_required:
                    self.unlocked_abilities[ability_name] = True
                    # Add to appropriate ability list if not already present
                    ability_info = Ability(
                        name=ability_name,
                        type=ability_type,
                        description=ability.description,
                        level_required=level_required,
                        is_unlocked=True,
                        unlocked=True  # Add both fields for compatibility with schema validation
                    )
                    if ability_type == "passive" and ability_info not in self.passive_abilities:
                        self.passive_abilities.append(ability_info)
                    elif ability_type == "active" and ability_info not in self.active_abilities:
                        self.active_abilities.append(ability_info)
                    elif ability_type == "ultimate" and ability_info not in self.ultimate_abilities:
                        self.ultimate_abilities.append(ability_info)

    
    def refill_gas(self) -> None:
        """Refill character's gas to maximum capacity."""
        self.gas = 5000
        self.max_gas = 5000
        self.updated_at = datetime.now(timezone.utc)

    def dict(self, *args, **kwargs):
        # Ensure all abilities in all ability lists have both 'unlocked' and 'is_unlocked' fields
        data = super().dict(*args, **kwargs)
        for ability_type in ['active_abilities', 'passive_abilities', 'ultimate_abilities']:
            for ability in data.get(ability_type, []):
                if 'unlocked' not in ability and 'is_unlocked' not in ability:
                    ability['unlocked'] = False
                    ability['is_unlocked'] = False
                elif 'unlocked' not in ability and 'is_unlocked' in ability:
                    ability['unlocked'] = ability['is_unlocked']
                elif 'is_unlocked' not in ability and 'unlocked' in ability:
                    ability['is_unlocked'] = ability['unlocked']
        return data


class TeamMember(BaseModel):
    character_name: str
    position: int
    
    def dict(self, *args, **kwargs):
        """Explicitly define dict method to ensure proper serialization for MongoDB"""
        return {
            "character_name": self.character_name,
            "position": self.position
        }

class Player(BaseModel):
    # Basic player information
    user_id: str
    username: str
    name: str
    level: int = 1
    xp: int = 0
    total_xp: int = 0
    
    # Currencies
    marks: int = 0
    valor: int = 0
    crystal: int = 0
    gas: int = 1000
    max_gas: int = 10000 
    
    # Location and travel
    location: str = ""
    travel: dict = Field(default_factory=dict)
    unlocked_areas: List[str] = Field(default_factory=list)
    
    # Characters and team
    owned_characters: List[str] = Field(default_factory=list)
    team: List[TeamMember] = Field(default_factory=list)
    
    # Progression tracking
    explore_count: int = 0
    daily_explores: Dict[str, int] = Field(default_factory=dict)
    completed_quests: List[str] = Field(default_factory=list)
    missions: List[Dict[str, Any]] = Field(default_factory=list)
    mission14_area_counts: Dict[str, int] = Field(default_factory=dict) 
    
    # Inventory and shop
    inventory: Dict[str, Any] = Field(default_factory=dict)
    shop_refresh_date: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    shop_refresh_count: int = 0
    
    # Daily bonuses
    daily_streak: int = 0
    last_daily_claim: Optional[datetime] = None
    double_exp_end: Optional[datetime] = None
    
    # Guild
    guild_id: Optional[int] = None
    
    # Referral system
    referral_code: Optional[str] = None
    referred_by: Optional[str] = None
    referral_count: int = 0
    referral_milestones: Dict[str, bool] = Field(default_factory=dict)
    
    # PvP related fields
    pvp_wins: int = 0
    pvp_losses: int = 0
    battle_rating: int = 1000
    pvp_matches: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Anti-abuse and taxation
    tax_history: List[dict] = Field(default_factory=list)
    hcaptcha_verified: Optional[bool] = False
    hcaptcha_start_time: Optional[float] = None
    explore_start_time: Optional[float] = None
    last_explore_time: Optional[float] = None
    
    # Spin System
    spin_pity_counter: int = 0
    spin_medals: int = 0
    last_spin_time: Optional[datetime] = None
    
    # Active Buffs
    double_gas_injector_uses: int = 0  
    mark_surge_token_uses: int = 0     
    frenzy_elixir_uses: int = 0        
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def xp_to_next_level(self) -> int:
        """Calculate XP needed for next level (no cap)"""
        if self.level < 50:
            return 2400 + (self.level * 150)  
        elif self.level < 100:
            return 20000 + (self.level * 300) 
        elif self.level < 500:
            return 50000 + (self.level * 500)
        else:
            return 90000 + (self.level * 1000)

    @property
    def gas_limit(self) -> int:
        """Calculate gas limit based on player level"""
        return 10000 + ((self.level - 1) * 250)

    def add_xp(self, amount: int) -> Dict[str, Any]:
        """Add XP and return level up information."""
        if amount < 0 and abs(amount) > self.xp:
            amount = -self.xp
        
        self.xp += amount
        
        if self.xp < 0:
            self.xp = 0
            
        if amount > 0:
            self.total_xp += amount
    
        level_ups = []
        while self.xp >= self.xp_to_next_level:
            # Store the current xp_to_next before leveling up
            current_xp_to_next = self.xp_to_next_level
            level_up_data = self.level_up()
            # Subtract the xp_to_next that was used for the check
            self.xp -= current_xp_to_next
            level_ups.append(level_up_data)
    
        return {
            "level_ups": level_ups,
            "total_level_ups": len(level_ups),
            "current_level": self.level,
            "current_xp": self.xp,
            "xp_to_next": self.xp_to_next_level
        }
        
    def level_up(self, db=None, context=None) -> dict:
        old_level = self.level
        self.level += 1
        # XP subtraction is now handled in add_xp method

        # Apply rewards
        rewards = self.get_level_up_rewards(self.level)
        self.marks += rewards["marks"]
        self.valor += rewards["valor"]
        # Removed crystal rewards from level up
        
        # Cap gas at new limit if it exceeds it
        gas_limit = self.gas_limit
        if self.gas > gas_limit:
            self.gas = gas_limit
        
        # Only run if db and context are provided (for async update)
        import asyncio
        async def _referral_levelup():
            if self.referred_by and db is not None and context is not None:
                ref_player = await db.players.find_one({"$or": [
                    {"referral_code": self.referred_by},
                    {"user_id": self.referred_by}
                ]})
                if ref_player:
                    milestones = getattr(ref_player, 'referral_milestones', {}) or {}
                    milestone_updates = {}
                    milestone_msgs = []
                    if self.level == 20 and not milestones.get(f"ref_{self.user_id}_lv20"):
                        milestone_updates[f'referral_milestones.ref_{self.user_id}_lv20'] = True
                        milestone_updates['valor'] = getattr(ref_player, 'valor', 0) + 50
                        milestone_msgs.append(f'🎉 <b>Referral Reward:</b> You received <b>50 Valor</b> because your referral {self.name} reached level 20!')
                    if self.level == 50 and not milestones.get(f"ref_{self.user_id}_lv50"):
                        milestone_updates[f'referral_milestones.ref_{self.user_id}_lv50'] = True
                        milestone_updates['crystal'] = getattr(ref_player, 'crystal', 0) + 2
                        milestone_msgs.append(f'🎉 <b>Referral Reward:</b> You received <b>2 Titan Crystals</b> because your referral {self.name} reached level 50!')
                    if milestone_updates:
                        await db.players.update_one({"user_id": str(getattr(ref_player, "user_id", None))}, {"$set": milestone_updates})
                        bot = getattr(context, 'bot', None)
                        if bot and milestone_msgs:
                            await bot.send_message(chat_id=str(getattr(ref_player, "user_id", None)), text='\n'.join(milestone_msgs), parse_mode="HTML")
        
        if db is not None and context is not None:
            asyncio.create_task(_referral_levelup())

        return {
            "old_level": old_level,
            "new_level": self.level,
            "rewards": rewards
        }

    def get_level_up_rewards(self, new_level: int) -> dict:
        """Calculate rewards based on player's new level tier"""
        rewards = {
            "marks": 0,
            "valor": 0,
            "crystals": 0
        }
    
        # Valor tier system based on level
        if new_level <= 50:
            # Below level 50: every 7 levels, 1 valor
            if new_level % 7 == 0:
                rewards["valor"] = 1
        elif new_level <= 100:
            # After 50: every 5 levels, 1 valor
            if new_level % 5 == 0:
                rewards["valor"] = 1
        else:
            # After 100: every 4 levels, 1 valor
            if new_level % 4 == 0:
                rewards["valor"] = 1
    
        # Tier 1: Onboarding Phase (1-10)
        if 1 <= new_level <= 10:
            rewards["marks"] = 200 + (new_level * 50)  # 250, 300, 350, 400, 450, 500, 550, 600, 650, 700

        # Tier 2: Core Progression (11-20)
        elif 11 <= new_level <= 20:
            rewards["marks"] = 600 + ((new_level - 10) * 80)  # 680, 760, 840, 920, 1000, 1080, 1160, 1240, 1320, 1400

        # Tier 3: Customization (21-30)
        elif 21 <= new_level <= 30:
            rewards["marks"] = 1400 + ((new_level - 20) * 120)  # 1520, 1640, 1760, 1880, 2000, 2120, 2240, 2360, 2480, 2600

        # Tier 4: Prestige (31-40)
        elif 31 <= new_level <= 40:
            rewards["marks"] = 2600 + ((new_level - 30) * 150)  # 2750, 2900, 3050, 3200, 3350, 3500, 3650, 3800, 3950, 4100

        # Tier 5: Apex (41+)
        else:
            rewards["marks"] = 4100 + ((new_level - 40) * 200)  # 4300, 4500, 4700, etc.

        if new_level > 50:
            # Progressive scaling (3% increase per level beyond 50 for better motivation)
            scale = 1 + (new_level - 50) * 0.03
            rewards["marks"] = int(rewards["marks"] * scale)
            
        return rewards

    def calculate_exp_gain(self, action: str, amount: int = 1) -> int:
        """Calculate EXP gain from various actions"""
        base_exp = {
            'titan_kill': random.randint(10, 20),
            'pvp_win': 30,
            'pvp_loss': 15,
            'shop_purchase': random.randint(10, 15),
            'daily_explore': 50,
            'achievement': random.randint(100, 500)
        }.get(action, 0) * amount
        
        # Apply boosts
        total_exp = base_exp
        
        # Double EXP weekend
        if self.double_exp_end and datetime.now(timezone.utc) < self.double_exp_end:
            total_exp *= 2
            
        # Guild bonus
        if self.guild_id is not None:
            total_exp *= 1.15  # +15%
            
        return int(total_exp)

    # Character management methods
    def add_character(self, character_name: str) -> None:
        if character_name not in self.owned_characters:
            self.owned_characters.append(character_name)

    def remove_character(self, character_name: str) -> None:
        if character_name in self.owned_characters:
            self.owned_characters.remove(character_name)

    # Daily exploration tracking methods
    def get_daily_explores_count(self, date: datetime) -> int:
        """Get the number of explores for a specific date"""
        date_str = date.strftime('%Y-%m-%d')
        return self.daily_explores.get(date_str, 0)

    def increment_daily_explores(self, date: datetime) -> int:
        """Increment the daily explores count and return the new count"""
        date_str = date.strftime('%Y-%m-%d')
        
        self.daily_explores[date_str] = self.daily_explores.get(date_str, 0) + 1
        
        # Clean up old records (keep only last 7 days)
        current_date = datetime.now(timezone.utc)
        cutoff_date = (current_date - timedelta(days=7)).strftime('%Y-%m-%d')
        
        self.daily_explores = {k: v for k, v in self.daily_explores.items() if k >= cutoff_date}
        
        return self.daily_explores[date_str]

class Titan(BaseModel):
    name: str
    level: int
    max_hp: int
    abilities: List[str]
    created_at: datetime
    difficulty: str = "Normal"
    spawn_areas: List[str]
    min_level_requirement: int = 1
    internal_name: Optional[str] = None
    drop_table: Dict[str, Any] = Field(default_factory=dict)
    xp_reward: int = 0
    is_boss: bool = False

# Anime-accurate titan names by difficulty
TITAN_NAME_VARIANTS = {
    "Easy": [
        "Bearded", "Potbellied", "Goofy Grinning", "Gaping Mouth",  "Small Jogger", "Female",
        "Leaper", "Bloated", "Staggering Creepers", "Wailing", "Cart", "Dancing", "Smiling",
        "Beast", "Ancient Beast", "Abnormal"
    ],
    "Normal": [
        "Bearded", "Potbellied", "Goofy Grinning", "Gaping Mouth",  "Small Jogger", "Female",
        "Leaper", "Bloated", "Staggering Creepers", "Wailing", "Cart", "Dancing", "Smiling",
        "Beast", "Ancient Beast", "Abnormal"
    ],
    "Hard": [
        "Bearded", "Potbellied", "Goofy Grinning", "Gaping Mouth",  "Small Jogger", "Female",
        "Leaper", "Bloated", "Staggering Creepers", "Wailing", "Cart", "Dancing", "Smiling",
        "Beast", "Ancient Beast", "Abnormal"
    ]
}

# Base HP ranges by difficulty - Rebalanced for better gameplay
HP_RANGES = {
    "Easy": (80, 160),      # Increased from (60, 180)
    "Normal": (150, 320),   # Slightly reduced from (120, 380) 
    "Hard": (280, 480)      # Reduced from (250, 550) for better balance
}

def generate_titan_name(difficulty: str) -> str:
    # Create a timestamp seed to ensure randomness - microseconds add unpredictability
    current_time_micros = datetime.now(timezone.utc).microsecond
    # Use a new random instance with time-based seed for better uniqueness
    name_random = random.Random(current_time_micros)
    
    variants = TITAN_NAME_VARIANTS[difficulty]
    return f"{name_random.choice(variants)} Titan"

def generate_titan_hp(level: int, difficulty: str, character_stats: Optional[CharacterStats] = None) -> int:
    # Dynamic scaling based on active character's stats
    if character_stats:
        # Fix: Handle both CharacterStats object and dict
        if isinstance(character_stats, dict):
            hp_value = character_stats.get('HP', 650)
        else:
            hp_value = character_stats.HP
            
        # Titan HP is approximately 20% of character's HP with randomization
        base_hp = hp_value * 0.2
        
        difficulty_multipliers = {
            "Easy": 0.5,  
            "Normal": 1.5,  
            "Hard": 2.5  
        }
        
        scaled_hp = base_hp * difficulty_multipliers.get(difficulty, 1.0)
        
        # Add more variation for randomness
        variation = random.uniform(0.8, 1.2)
        final_hp = scaled_hp * variation
        
        return max(int(final_hp), 50)  

    # Fallback for pre-generation pool (less aggressive scaling)
    min_hp, max_hp = HP_RANGES[difficulty]
    
    # Reduced level scaling
    level_multiplier = 1 + (level * 0.08)
    
    variation = random.uniform(0.85, 1.15)
    base_hp = random.randint(min_hp, max_hp)
    final_hp = base_hp * level_multiplier * variation
    
    min_final_hp = {
        "Easy": 50,
        "Normal": 100,
        "Hard": 200
    }[difficulty]
    
    return max(int(final_hp), min_final_hp)

def generate_titan_xp(level: int, difficulty: str) -> int:
    """Generate XP reward based on level and difficulty"""
    base_xp = {
        "Easy": 50,
        "Normal": 100,
        "Hard": 200
    }[difficulty]
    
    return int(base_xp * (1 + (level * 0.1)))
