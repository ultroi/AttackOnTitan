from datetime import datetime
import random
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from database.characters import CharacterData, get_character_data, Ability

class CharacterStats(BaseModel):
    ATK: int = 10
    DEF: int = 10
    ACC: int = 10
    INT: int = 10
    SPD: int = 10
    HP: int = 520  # Default max HP

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
        return get_character_data(self.character_type).abilities

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

    def add_xp(self, amount: int) -> None:
        self.xp += amount
        self.total_xp += amount
        while self.xp >= self.xp_to_next_level:
            self.level_up()

    def unlock_abilities(self) -> None:
        # Unlock passive abilities first
        for ability in self.get_abilities().get("passive", {}).values():
            if not ability.is_unlocked:
                ability.is_unlocked = True  # Unlock the passive ability

        # Then unlock active abilities based on level
        for ability in self.get_abilities().get("active", {}).values():
            if self.level >= ability.level_required and not ability.is_unlocked:
                ability.is_unlocked = True  # Unlock the active ability

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

    def add_xp(self, amount: int) -> None:
        self.xp += amount
        self.total_xp += amount
        while self.xp >= self.xp_to_next_level:
            self.level_up()

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
    xp_reward: int
    abilities: List[str]
    created_at: datetime
    difficulty: str = "Normal"
    special_abilities: Optional[List[str]] = None
    spawn_areas: List[str]
    min_level_requirement: int = 1
    internal_name: Optional[str] = None

# Titan name variations by type and difficulty
TITAN_NAME_VARIANTS = {
    "Easy": [
        "Small", "Weak", "Young", "Dwarfed", "Frail", 
        "Tiny", "Scrawny", "Puny", "Miniature", "Underdeveloped"
    ],
    "Normal": [
        "Abnormal", "Standard", "Common", "Regular", "Average",
        "Typical", "Ordinary", "Usual", "Routine", "Conventional"
    ],
    "Hard": [
        "Armored", "Colossal", "Warhammer", "Beast", "Jaw",
        "Female", "Founding", "Attack", "Cart", "Flying"
    ]
}

# Special abilities by difficulty
SPECIAL_ABILITIES = {
    "Easy": ["Stumble", "Slow Movement", "Poor Vision"],
    "Normal": ["Charge", "Ground Slam", "Roar", "Regeneration"],
    "Hard": ["Titan Shift", "Armor Plating", "Steam Blast", "Crystal Armor", "Thunder Spear"]
}

# Base HP ranges by difficulty
HP_RANGES = {
    "Easy": (150, 300),
    "Normal": (200, 500),
    "Hard": (400, 800)
}

def generate_titan_name(difficulty: str) -> str:
    """Generate a unique titan name based on difficulty."""
    prefix = random.choice(TITAN_NAME_VARIANTS[difficulty])
    suffix = random.choice(["Titan", "Titan", "Titan", "Abnormal", "Creature", "Monster"])
    return f"{prefix} {suffix}"

def generate_titan_hp(level: int, difficulty: str) -> int:
    """Generate HP within specified ranges with level scaling"""
    min_hp, max_hp = HP_RANGES[difficulty]
    
    # Scale HP based on level (1-100)
    level_scale = 1 + (level / 100)
    
    # Randomize within range
    base_hp = random.randint(min_hp, max_hp)
    
    # Apply level scaling with diminishing returns
    scaled_hp = base_hp * (1 + (level ** 0.7) * 0.02)
    
    return int(scaled_hp)

def generate_titan_xp(level: int, difficulty: str) -> int:
    """Generate XP reward based on level and difficulty"""
    base_xp = {
        "Easy": 50,
        "Normal": 100,
        "Hard": 200
    }[difficulty]
    
    return int(base_xp * (1 + (level * 0.1)))
