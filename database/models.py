from datetime import datetime
import random
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from database.characters import CharacterData, get_character_data, Ability

class CharacterStats(BaseModel):
    ATK: int = 10
    DEF: int = 10
    ACC: int = 10
    INT: int = 10
    SPD: int = 10
    HP: int = 650  # Increased default max HP for better balance

class AbilityInfo(BaseModel):
    name: str
    type: str  # "active" or "passive"
    description: str
    level_required: int
    unlocked: bool = False

class Equipment(BaseModel):
    name: str
    type: str
    rarity: str
    durability: int
    weight: float
    attributes: Dict[str, float]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Character(BaseModel):
    user_id: int
    name: str
    character_type: str
    birthplace: str
    current_hp: int
    level: int = 1
    xp: int = 0
    total_xp: int = 0
    rank: str = "Cadet"
    stats: CharacterStats = Field(default_factory=CharacterStats)
    gas: int = 5000
    crystals: int = 0
    valor: int = 0
    marks: int = 0
    explore_count: int = 0
    active_abilities: List[AbilityInfo] = Field(default_factory=list)
    passive_abilities: List[AbilityInfo] = Field(default_factory=list)
    unlocked_abilities: Dict[str, bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def __init__(self, **data):
        super().__init__(**data)
        # Initialize max HP and current HP based on character_type
        character_data = get_character_data(self.character_type)
        if character_data:
            self.stats.HP = character_data.get_max_hp(self.level)
            self.current_hp = self.stats.HP

    @property
    def xp_to_next_level(self) -> int:
        """Calculate XP needed for next level"""
        if self.level < 50:
            return self.level * 1000
        elif self.level < 100:
            return 50000 + (self.level - 50) * 2000
        else:
            return 150000 + (self.level - 100) * 3000

    def get_abilities(self) -> Dict[str, Dict[str, 'Ability']]:
        character_data = get_character_data(self.character_type)
        if character_data is None:
            return {}
        return character_data.abilities

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
        for ability in self.active_abilities + self.passive_abilities:
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
        from database.characters import get_character_data
        character_data = get_character_data(self.character_type)
        if character_data:
            old_hp = character_data.get_max_hp(old_level)
            new_hp = character_data.get_max_hp(new_level)
            return new_hp - old_hp
        return 0

    def unlock_abilities(self) -> None:
        # Unlock abilities based on level requirements for both passive and active
        abilities = self.get_abilities()
        
        # Check passive abilities
        for ability_name, ability in abilities.get("passive", {}).items():
            level_required = getattr(ability, 'level_required', 1)
            if self.level >= level_required:
                self.unlocked_abilities[ability_name] = True

        # Check active abilities
        for ability_name, ability in abilities.get("active", {}).items():
            level_required = getattr(ability, 'level_required', 1)
            if self.level >= level_required:
                self.unlocked_abilities[ability_name] = True

        # Finally unlock ultimate abilities
        for ability in self.get_abilities().get("ultimate", {}).values():
            if self.level >= ability.level_required and not ability.is_unlocked:
                ability.is_unlocked = True  # Unlock the ultimate ability


class TeamMember(BaseModel):
    character_name: str
    position: int


class Player(BaseModel):
    user_id: int
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    daily_streak: int = 0
    last_daily_claim: Optional[datetime] = None
    inventory: Dict[str, int] = Field(default_factory=dict)
    unlocked_areas: List[str] = Field(default_factory=list)
    completed_quests: List[str] = Field(default_factory=list)
    team: List[TeamMember] = Field(default_factory=list)

    @property
    def xp_to_next_level(self) -> int:
        """Calculate XP needed for next level"""
        if self.level < 50:
            return self.level * 1000
        elif self.level < 100:
            return 50000 + (self.level - 50) * 2000
        else:
            return 150000 + (self.level - 100) * 3000

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
    import random
    
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
    import random
    
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
