from datetime import datetime, timezone, timedelta
import random
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from database.characters import CharacterData, get_character_data, AbilityEffect

class CharacterStats(BaseModel):
    ATK: int = 10
    DEF: int = 10
    ACC: int = 10
    INT: int = 10
    SPD: int = 10
    HP: int = 650  # Increased default max HP for better balance

class AbilityInfo(BaseModel):
    name: str
    type: str  # "active", "passive", or "ultimate"
    description: str
    level_required: int
    unlocked: bool = False

class Equipment(BaseModel):
    name: str
    type: str
    item_type: str = ""  # Add item_type for shop filtering compatibility
    rarity: str
    durability: int
    weight: float
    attributes: Dict[str, float]
    currency: str = "marks"  # Default currency
    price: int = 0  # Default price
    stock_limit: int = -1  # -1 means unlimited
    cooldown_hours: int = 0  # Default no cooldown
    description: str = ""  # Default empty description
    unlock_conditions: Dict[str, Any] = Field(default_factory=dict)  # Ensure unlock_conditions always exists
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Character(BaseModel):
    user_id: str
    name: str
    character_type: str
    current_hp: int
    level: int = 1
    xp: int = 0
    total_xp: int = 0
    rank: str = "Cadet"
    stats: CharacterStats = Field(default_factory=CharacterStats)
    gas: int = 5000
    max_gas: int = 5000  # Added max_gas attribute
    crystals: int = 0
    valor: int = 0
    marks: int = 0
    explore_count: int = 0
    active_abilities: List[AbilityInfo] = Field(default_factory=list)
    passive_abilities: List[AbilityInfo] = Field(default_factory=list)
    ultimate_abilities: List[AbilityInfo] = Field(default_factory=list)
    unlocked_abilities: Dict[str, bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __init__(self, **data):
        super().__init__(**data)
        # Initialize max HP and current HP based on character_type
        character_data = get_character_data(self.character_type)
        if character_data:
            self.stats.HP = character_data.get_max_hp(self.level)
            self.current_hp = self.stats.HP

    @property
    def xp_to_next_level(self) -> int:
        """Calculate XP needed for next level based on the new system"""
        if self.level < 50:
            base = 500 + (self.level * 100)  # Start at 600, increase by 100 per level
            return int(base * (1 + (self.level // 10) * 0.5))  # 50% increase every 10 levels
        elif self.level < 100:
            # Mid levels need tens of thousands
            base = 10000 + ((self.level - 50) * 500)
            return int(base * (1 + ((self.level - 50) // 10) * 0.2))
        else:
            # High levels need over 100,000
            return int(100000 + ((self.level - 100) * 2000))

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
        character_data = get_character_data(self.character_type)
        if character_data is None:
            return {}
        return {
            "active": {a.name: a for a in getattr(character_data, "active_abilities", [])},
            "passive": {a.name: a for a in getattr(character_data, "passive_abilities", [])},
            "ultimate": {a.name: a for a in getattr(character_data, "ultimate_abilities", [])},
        }

    def level_up(self) -> None:
        if self.level < 125:
            self.level += 1
            self.xp -= self.xp_to_next_level
            # Update max HP and current HP
            character_data = get_character_data(self.character_type)
            if character_data:
                new_max_hp = character_data.get_max_hp(self.level)
                hp_increase = new_max_hp - self.stats.HP
                self.stats.HP = new_max_hp
                self.current_hp = min(self.current_hp + hp_increase, new_max_hp)
            # Update rank
            if self.level >= 25:
                self.rank = "Veteran"
            elif self.level >= 15:
                self.rank = "Elite"
            elif self.level >= 5:
                self.rank = "Soldier"
            # Check for ability unlocks
            self._check_ability_unlocks()

    def _check_ability_unlocks(self) -> None:
        for ability in self.active_abilities + self.passive_abilities + self.ultimate_abilities:
            if not ability.unlocked and self.level >= ability.level_required:
                ability.unlocked = True
                self.unlocked_abilities[ability.name] = True

    def add_xp(self, amount: int) -> Dict[str, Any]:
        """Add XP and return level up information."""
        self.xp += amount
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
                "new_rank": self.rank,
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
        character_data = get_character_data(self.character_type)
        if character_data:
            old_hp = character_data.get_max_hp(old_level)
            new_hp = character_data.get_max_hp(new_level)
            return new_hp - old_hp
        return 0

    def unlock_abilities(self) -> None:
        # Unlock abilities based on level requirements for all types
        abilities = self.get_abilities()
        
        for ability_type in ["passive", "active", "ultimate"]:
            for ability_name, ability in abilities.get(ability_type, {}).items():
                level_required = getattr(ability, 'level_required', 1)
                if self.level >= level_required:
                    self.unlocked_abilities[ability_name] = True
                    # Add to appropriate ability list if not already present
                    ability_info = AbilityInfo(
                        name=ability_name,
                        type=ability_type,
                        description=ability.description,
                        level_required=level_required,
                        unlocked=True
                    )
                    if ability_type == "passive" and ability_info not in self.passive_abilities:
                        self.passive_abilities.append(ability_info)
                    elif ability_type == "active" and ability_info not in self.active_abilities:
                        self.active_abilities.append(ability_info)
                    elif ability_type == "ultimate" and ability_info not in self.ultimate_abilities:
                        self.ultimate_abilities.append(ability_info)

class TeamMember(BaseModel):
    character_name: str
    position: int

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

    @property
    def xp_to_next_level(self) -> int:
        """Calculate XP needed for next level (no cap)"""
        if self.level < 100:
            return 500 + (self.level * 50)  # Start at 550, increase by 50 per level
        elif self.level < 1000:
            return 5000 + (self.level * 100)  # Start at ~15k, increase by 100 per level
        else:
            return 50000 + (self.level * 200)  # Start at ~250k, increase by 200 per level

    def calculate_exp_gain(self, action: str, amount: int = 1) -> int:
        """Calculate EXP gain from various actions"""
        base_exp = {
            'titan_kill': random.randint(10, 20),
            'pvp_win': 30,
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

    def level_up(self) -> None:
        self.level += 1
        self.xp -= self.xp_to_next_level

    def add_xp(self, amount: int) -> Dict[str, Any]:
        """Add XP and return level up information."""
        self.xp += amount
        self.total_xp += amount
        
        level_ups = []
        while self.xp >= self.xp_to_next_level:
            old_level = self.level
            self.level_up()
            new_level = self.level
            
            # Calculate level up rewards
            bonus_marks = new_level * 50  # 50 marks per level
            bonus_crystals = 1 if new_level % 5 == 0 else 0  # Crystal every 5 levels
            bonus_valor = 1 if new_level % 10 == 0 else 0  # Valor every 10 levels
            
            level_ups.append({
                "old_level": old_level,
                "new_level": new_level,
                "bonus_marks": bonus_marks,
                "bonus_crystals": bonus_crystals,
                "bonus_valor": bonus_valor
            })
        
        return {
            "level_ups": level_ups,
            "total_level_ups": len(level_ups),
            "current_level": self.level,
            "current_xp": self.xp,
            "xp_to_next": self.xp_to_next_level
        }

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
    special_abilities: Optional[List[str]] = None
    spawn_areas: List[str]
    min_level_requirement: int = 1
    internal_name: Optional[str] = None
    drop_table: Dict[str, Any] = Field(default_factory=dict)
    xp_reward: int = 0

# Anime-accurate titan names by difficulty
TITAN_NAME_VARIANTS = {
    "Easy": [
        "Bearded", "Potbellied", "Goofy Grinning", "Tiny-Armed", "Long-Nosed",
        "Small Round", "Thin-Legged", "One-Eyed", "Crawling Lizard-Like", "Tree Hanger",
        "Gaping Mouth", "Tall Toothless", "Double-Jawed", "Half-Faced", "Wall Climber"
    ],
    "Normal": [
        "Abnormal", "Frenzied", "Swift", "Heavy", "Agile",
        "Stealth", "Regenerating", "Berserker", "Savage", "Wild",
        "Cunning", "Hunter", "Stalker", "Lurker", "Predator"
    ],
    "Hard": [
        "Armored", "Colossal", "Female", "Beast", "Jaw",
        "Warhammer", "Cart", "Attack", "Founding", "War Chief",
        "Ancient", "Primal", "Apex", "Elite", "Legendary"
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
SPECIAL_ABILITIES = {
    "Easy": [
        "Stumble", "Slow Movement", "Poor Vision", "Weak Grip", "Clumsy Steps",
        "Distracted", "Dizzy Spells", "Poor Balance", "Sluggish Reflexes"
    ],
    "Normal": [
        "Charge", "Ground Slam", "Roar", "Regeneration", "Berserker Rage",
        "Territorial Instinct", "Pack Hunt", "Surprise Attack", "Endurance Boost",
        "Defensive Stance", "Quick Recovery", "Intimidating Presence"
    ],
    "Hard": [
        "Titan Shift", "Armor Plating", "Steam Blast", "Crystal Armor", "Thunder Spear",
        "Hardening", "Coordinate Power", "Founding Will", "Beast Control", "War Hammer Creation",
        "Jaw Crush", "Cart Endurance", "Female Agility", "Colossal Explosion", "Primal Scream"
    ]
}

# Base HP ranges by difficulty - Rebalanced for better gameplay
HP_RANGES = {
    "Easy": (80, 160),      # Increased from (60, 180)
    "Normal": (150, 320),   # Slightly reduced from (120, 380) 
    "Hard": (280, 480)      # Reduced from (250, 550) for better balance
}

def generate_titan_name(difficulty: str) -> str:
    """Generate a unique titan name based on difficulty with anime-accurate names."""
    # 60% chance for basic titan name, 25% chance for descriptive prefix, 15% chance for unique combination
    rand = random.random()
    
    if rand < 0.60:
        # Basic titan name
        titan_type = random.choice(TITAN_NAME_VARIANTS[difficulty])
        return f"{titan_type} Titan"
    elif rand < 0.85:
        # Descriptive prefix
        descriptor = random.choice(TITAN_DESCRIPTORS[difficulty])
        titan_type = random.choice(TITAN_NAME_VARIANTS[difficulty])
        return f"{descriptor} {titan_type} Titan"
    else:
        # Unique combination (mix difficulties for variety)
        all_descriptors = TITAN_DESCRIPTORS[difficulty]
        if difficulty != "Easy":
            all_descriptors += TITAN_DESCRIPTORS["Easy"][:3]  # Add some easier descriptors for variety
        
        descriptor = random.choice(all_descriptors)
        titan_type = random.choice(TITAN_NAME_VARIANTS[difficulty])
        
        # Small chance for double descriptor
        if random.random() < 0.3:
            second_descriptor = random.choice(TITAN_DESCRIPTORS[difficulty])
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