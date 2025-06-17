from datetime import datetime
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
    abilities: List[str]
    drop_table: Dict[str, float]
    xp_reward: int
    created_at: datetime
    difficulty: str = "Normal"
    special_abilities: Optional[List[str]] = None
    weakness: Optional[str] = None
    resistance: Optional[str] = None
    spawn_areas: List[str]
    min_level_requirement: int = 1


def scale_titan(titan_data: dict, target_level: int) -> dict:
    """Scale a titan template to the target level."""
    base_level = titan_data["level"]
    level_diff = target_level - base_level
    scaled_data = titan_data.copy()
    
    # 🧼 Remove existing MongoDB ID to avoid duplicate key error
    scaled_data.pop("_id", None)
    scaled_data.pop("is_template", None)  # Remove template flag for scaled titans

    scaled_data["level"] = target_level
    scaled_data["max_hp"] = int(titan_data["max_hp"] * (1 + level_diff * 0.1))
    scaled_data["xp_reward"] = int(titan_data["xp_reward"] * (1 + level_diff * 0.05))
    scaled_data["drop_table"] = {
        k: v * (1 + level_diff * 0.03) for k, v in titan_data["drop_table"].items()
    }

    # Make name unique by adding timestamp internally
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    base_name = titan_data['name'].replace("Generic Titan", "").strip()
    scaled_data["name"] = f"Level {target_level} Generic Titan"
    scaled_data["internal_name"] = f"level_{target_level}_generic_{timestamp}"
    scaled_data["min_level_requirement"] = max(1, target_level - 2)
    scaled_data["created_at"] = datetime.utcnow()
    scaled_data["is_scaled"] = True  # Mark as scaled titan

    # Set difficulty
    if target_level >= 50:
        scaled_data["difficulty"] = "Hard"
    elif target_level >= 20:
        scaled_data["difficulty"] = "Normal"
    else:
        scaled_data["difficulty"] = "Easy"

    return scaled_data
