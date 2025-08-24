from datetime import datetime, timezone, timedelta
import random
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from database.characters import CharacterData, get_character_data, AbilityEffect
from motor.motor_asyncio import AsyncIOMotorClient
from database.schemas import Ability  # <-- Import Ability

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
    stats: CharacterStats = Field(default_factory=CharacterStats)
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
        super().__init__(**data)
        # Initialize max HP and current HP based on character_type
        if hasattr(self, 'character_type') and self.character_type:
            character_data = get_character_data(self.character_type)
            if character_data:
                self.stats.HP = character_data.get_max_hp(self.level)
                self.current_hp = self.stats.HP

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

    def level_up(self) -> None:
        if self.level < 125:
            self.level += 1
            self.xp -= self.xp_to_next_level
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
                            new_val = getattr(self.stats, stat, 0) + stat_increase
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
                
            self.max_gas += 250
            self.gas = self.max_gas 
            # Check for ability unlocks
            self._check_ability_unlocks()

    def _check_ability_unlocks(self) -> None:
        # Get character data to ensure we have all possible abilities
        if not hasattr(self, 'character_type') or not self.character_type:
            return
            
        character_data = get_character_data(self.character_type)
        if character_data:
            all_abilities = []
            # Add abilities from character data
            for ability_list_name in ["active_abilities", "passive_abilities", "ultimate_abilities"]:
                ability_list = getattr(character_data, ability_list_name, [])
                all_abilities.extend(ability_list)
            
            # Check each ability for unlocks
            for ability in all_abilities:
                if self.level >= ability.level_required:
                    ability.is_unlocked = True
                    self.unlocked_abilities[ability.name] = True

    def add_xp(self, amount: int) -> Dict[str, Any]:
        """Add XP and return level up information."""
        self.xp += amount
        if self.xp < 0:
            self.xp = 0
        self.total_xp += amount
        
        level_ups = []
        while self.xp >= self.xp_to_next_level:
            old_level = self.level
            self.level_up()
            new_level = self.level
            
            # Track what was unlocked at this level
            newly_unlocked = self._get_newly_unlocked_abilities(new_level)
            
            level_ups.append({
                "old_level": old_level,
                "new_level": new_level,
                "newly_unlocked_abilities": newly_unlocked,
                "hp_increase": self._get_hp_increase(old_level, new_level)
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

class DailyExplores(BaseModel):
    """Model to track daily explores with dates"""
    date: str  # ISO format date string
    count: int

class Player(BaseModel):
    user_id: str
    username: str
    name: str
    level: int = 1
    xp: int = 0
    total_xp: int = 0
    gas: int = 1000
    crystal: int = 0
    valor: int = 0
    marks: int = 0
    explore_count: int = 0
    owned_characters: List[str] = Field(default_factory=list)
    location: str = ""  # Add location to Player, set on creation
    travel: dict = Field(default_factory=dict)  # Add travel state
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    daily_streak: int = 0
    last_daily_claim: Optional[datetime] = None
    inventory: Dict[str, Any] = Field(default_factory=dict)
    unlocked_areas: List[str] = Field(default_factory=list)
    completed_quests: List[str] = Field(default_factory=list)
    team: List[TeamMember] = Field(default_factory=list)
    guild_id: Optional[int] = None
    double_exp_end: Optional[datetime] = None
    daily_explores: List[DailyExplores] = Field(default_factory=list)
    shop_refresh_date: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    shop_refresh_count: int = 0  # Added to track manual shop refreshes
    referral_code: Optional[str] = None  # Unique code for sharing
    referred_by: Optional[str] = None    # Referral code of the referrer
    referral_count: int = 0              # Number of successful referrals
    tax_history: List[dict] = []
    referral_milestones: Dict[str, bool] = Field(default_factory=dict)  # Track milestone rewards
    hcaptcha_verified: Optional[bool] = False
    hcaptcha_start_time: Optional[float] = None
    explore_start_time: Optional[float] = None
    last_explore_time: Optional[float] = None
    # PvP related fields
    pvp_wins: int = 0
    pvp_losses: int = 0
    battle_rating: int = 1000
    pvp_matches: List[Dict[str, Any]] = Field(default_factory=list)  # Store recent match history

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
            return 90000 + (self.level * 1000)  # Start at ~250k, increase by 200 per level

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

    def level_up(self) -> dict:
        old_level = self.level
        self.level += 1
        self.xp -= self.xp_to_next_level

        # Apply rewards
        rewards = self.get_level_up_rewards(self.level)
        self.marks += rewards["marks"]
        self.valor += rewards["valor"]
        self.crystal += rewards["crystals"]
    
        return {
            "old_level": old_level,
            "new_level": self.level,
            "rewards": rewards
        }

    def add_xp(self, amount: int) -> Dict[str, Any]:
        """Add XP and return level up information."""
        self.xp += amount
        self.total_xp += amount
    
        level_ups = []
        while self.xp >= self.xp_to_next_level:
            level_up_data = self.level_up()
            level_ups.append(level_up_data)
    
        return {
            "level_ups": level_ups,
            "total_level_ups": len(level_ups),
            "current_level": self.level,
            "current_xp": self.xp,
            "xp_to_next": self.xp_to_next_level
        }

    def get_level_up_rewards(self, new_level: int) -> dict:
        """Calculate rewards based on player's new level tier"""
        rewards = {
            "marks": 0,
            "valor": 0,
            "crystals": 0,
            "unlocks": []
        }
    
        # Tier 1: Onboarding Phase (1-10)
        if 1 <= new_level <= 10:
            rewards["marks"] = random.randint(250, 500)
            if new_level % 2 == 0:
                rewards["valor"] = random.randint(1, 2)
        
            if new_level == 5:
                rewards["unlocks"].append("First Echo Trait Slot")
            elif new_level == 8:
                rewards["unlocks"].append("Second Weapon Slot")

        # Tier 2: Core Progression (11-20)
        elif 11 <= new_level <= 20:
            rewards["marks"] = random.randint(600, 1000)
            rewards["valor"] = 2
            if new_level == 15:
                rewards["crystals"] = 1
            
            if new_level == 12:
                rewards["unlocks"].append("Hollow Exchange")
            elif new_level == 18:
                rewards["unlocks"].append("Second Echo Trait Slot")

        # Tier 3: Customization (21-30)
        elif 21 <= new_level <= 30:
            rewards["marks"] = random.randint(1500, 2000)
            rewards["valor"] = 3
            if new_level % 2 == 0:
                rewards["crystals"] = 1
            
            if new_level == 22:
                rewards["unlocks"].append("Respec Token")
            elif new_level == 25:
                rewards["unlocks"].append("Echo Shard Crafting")

        # Tier 4: Prestige (31-40)
        elif 31 <= new_level <= 40:
            rewards["marks"] = random.randint(2000, 2500)
            rewards["valor"] = 5
            rewards["crystals"] = random.randint(1, 2)
        
            if new_level == 35:
                rewards["unlocks"].append("Echo Trait Enhancement")
            elif new_level == 40:
                rewards["unlocks"].append("Legacy Hall")

        # Tier 5: Apex (41+)
        else:
            rewards["marks"] = 3000 + (min(new_level, 50) * 100)
            rewards["valor"] = 6
            rewards["crystals"] = 2
        
            if new_level == 45:
                rewards["unlocks"].append("Elite Clan Access")
            elif new_level == 50:
                rewards["unlocks"].append("Titan Lord Title")


        if new_level > 50:
            # Progressive scaling (2% increase per level beyond 50)
            scale = 1 + (new_level - 50) * 0.02
            rewards["marks"] = int(rewards["marks"] * scale)
            rewards["valor"] = max(rewards["valor"], int(6 * scale))
            rewards["crystals"] = min(rewards["crystals"], 5)  # Cap crystals at 5
            
        return rewards

    def add_character(self, character_name: str) -> None:
        if character_name not in self.owned_characters:
            self.owned_characters.append(character_name)

    def remove_character(self, character_name: str) -> None:
        if character_name in self.owned_characters:
            self.owned_characters.remove(character_name)

    def get_daily_explores_count(self, date: datetime) -> int:
        """Get the number of explores for a specific date"""
        date_str = date.strftime('%Y-%m-%d')
        for daily in self.daily_explores:
            if daily.date == date_str:
                return daily.count
        return 0

    def increment_daily_explores(self, date: datetime) -> int:
        """Increment the daily explores count and return the new count"""
        date_str = date.strftime('%Y-%m-%d')
        
        # Find existing record
        for daily in self.daily_explores:
            if daily.date == date_str:
                daily.count += 1
                return daily.count
        
        # Create new record if not found
        new_daily = DailyExplores(date=date_str, count=1)
        self.daily_explores.append(new_daily)
        
        # Clean up old records (keep only last 7 days)
        current_date = datetime.now(timezone.utc)
        cutoff_date = (current_date - timedelta(days=7)).strftime('%Y-%m-%d')
        self.daily_explores = [d for d in self.daily_explores if d.date >= cutoff_date]
        
        return 1

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

# Anime-accurate titan names by difficulty
TITAN_NAME_VARIANTS = {
    "Easy": [
        "Bearded", "Potbellied", "Goofy Grinning", "Gaping Mouth",  "Small Jogger",
        "Leaper", "Bloated", "Staggering Creepers", "Wailing"
    ],
    "Normal": [
        "Bearded", "Potbellied", "Goofy Grinning", "Gaping Mouth",  "Small Jogger",
        "Leaper", "Bloated", "Staggering Creepers", "Wailing"
    ],
    "Hard": [
        "Bearded", "Potbellied", "Goofy Grinning", "Gaping Mouth",  "Small Jogger",
        "Leaper", "Bloated", "Staggering Creepers", "Wailing"
    ]
}

# Additional descriptive prefixes for variety
TITAN_DESCRIPTORS = {
    "Easy": [
        "Clumsy", "Stumbling", "Bumbling", "Sluggish", "Wandering",
        "Lost", "Confused", "Limping", "Shambling", "Drooling"
    ],
    "Normal": [
        "Fierce", "Hungry", "Raging", "Prowling", "Charging",
        "Brutal", "Menacing", "Territorial", "Aggressive", "Bloodthirsty"
    ],
    "Hard": [
        "Devastating", "Catastrophic", "Apocalyptic", "Nightmare", "Terror",
        "Godlike", "Primordial", "Mythical", "Legendary", "Supreme"
    ]
}

# Special abilities by difficulty - Enhanced with anime references
# ...existing code...

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
    
    rand = name_random.random()
    variants = TITAN_NAME_VARIANTS[difficulty]
    descriptors = TITAN_DESCRIPTORS[difficulty]
    if rand < 0.60:
        return f"{name_random.choice(variants)} Titan"
    elif rand < 0.85:
        return f"{name_random.choice(descriptors)} {name_random.choice(variants)} Titan"
    else:
        # Precompute combined descriptors only once per call
        if difficulty != "Easy":
            all_descriptors = descriptors + TITAN_DESCRIPTORS["Easy"][:3]
        else:
            all_descriptors = descriptors
        descriptor = name_random.choice(all_descriptors)
        titan_type = name_random.choice(variants)
        if name_random.random() < 0.3:
            second_descriptor = name_random.choice(descriptors)
            if second_descriptor != descriptor:
                return f"{descriptor} {second_descriptor} {titan_type} Titan"
        return f"{descriptor} {titan_type} Titan"

def generate_titan_hp(level: int, difficulty: str) -> int:
    """Generate HP within specified ranges with level scaling and randomization"""
    min_hp, max_hp = HP_RANGES[difficulty]
    
    # Better level scaling formula - more moderate
    level_multiplier = 1 + (level * 0.12)  # Reduced from 15% to 12% per level
    
    # Add randomization for variety (±15% variation)
    variation = random.uniform(0.85, 1.15)
    
    # Randomize within base range
    base_hp = random.randint(min_hp, max_hp)
    
    # Apply level scaling and variation
    final_hp = base_hp * level_multiplier * variation
    
    # Ensure minimum HP based on difficulty
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