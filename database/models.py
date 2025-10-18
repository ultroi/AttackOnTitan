from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict
from datetime import datetime, timezone
import random

class CharacterStats(BaseModel):
    HP: int = Field(default=650, alias='HP')
    ATK: int = Field(default=25, alias='attack')
    DEF: int = Field(default=10, alias='DEF')
    SPD: int = Field(default=10, alias='agility')
    ACC: int = Field(default=10, alias='precision')
    INT: int = Field(default=10, alias='INT')

    class Config:
        populate_by_name = True

class Character(BaseModel):
    user_id: str
    name: str
    character_type: str
    current_hp: int = 0
    level: int = 1
    xp: int = 0
    total_xp: int = 0
    stats: CharacterStats = Field(default_factory=CharacterStats)
    gas: int = 0
    max_gas: int = 100
    active_abilities: List[Dict] = Field(default_factory=list)
    passive_abilities: List[Dict] = Field(default_factory=list)
    ultimate_abilities: List[Dict] = Field(default_factory=list)
    unlocked_abilities: Dict[str, bool] = Field(default_factory=dict)
    equipped_weapon: Optional[str] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def unlock_abilities(self):
        # Placeholder for ability unlocking logic
        pass

class TeamMember(BaseModel):
    character_name: str
    position: int

class Player(BaseModel):
    user_id: str
    username: str
    name: str
    level: int = 1
    xp: int = 0
    total_xp: int = 0
    gas: int = 100
    valor: int = 0
    crystal: int = 0
    marks: int = 0
    explore_count: int = 0
    owned_characters: List[str] = Field(default_factory=list)
    team: List[TeamMember] = Field(default_factory=list)
    referral_code: str = ""
    referred_by: Optional[str] = None
    referral_count: int = 0
    referral_milestones: Dict[str, bool] = Field(default_factory=dict)
    location: str = "Trost District"
    travel: Optional[Dict] = None
    daily_explores: List[Dict] = Field(default_factory=list)
    unlocked_areas: List[str] = Field(default_factory=lambda: ["Trost District"])
    shop_refresh_date: Optional[datetime] = None
    shop_refresh_count: int = 0
    hcaptcha_verified: bool = False
    hcaptcha_start_time: Optional[datetime] = None
    explore_start_time: Optional[datetime] = None
    last_explore_time: Optional[datetime] = None
    inventory: Dict[str, int] = Field(default_factory=dict)
    missions: Dict = Field(default_factory=dict)
    pvp_wins: int = 0
    pvp_losses: int = 0
    battle_rating: int = 1000
    pvp_matches: List[Dict] = Field(default_factory=list)
    tax_history: List[Dict] = Field(default_factory=list)
    guild_id: Optional[str] = None
    daily_streak: int = 0
    last_daily_claim: Optional[datetime] = None
    double_exp_end: Optional[datetime] = None
    completed_quests: List[str] = Field(default_factory=list)
    mission14_area_counts: Dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @validator("missions", pre=True)
    def validate_missions(cls, v):
        if not isinstance(v, dict):
            return {}
        return v

    def increment_daily_explores(self, timestamp: datetime):
        """Adds a record of a daily explore."""
        self.daily_explores.append({"timestamp": timestamp})

class Titan(BaseModel):
    name: str
    level: int
    max_hp: int
    abilities: List[Dict]
    created_at: datetime
    difficulty: str
    spawn_areas: List[str]
    drop_table: Dict
    xp_reward: int
    min_level_requirement: int
    user_id: Optional[str] = None
    internal_name: Optional[str] = None

class Equipment(BaseModel):
    name: str
    type: str
    attributes: Dict
    rarity: str
    price: int
    currency: str
    description: str

class BankAccount(BaseModel):
    user_id: str
    balance: int = 0
    transaction_history: List[Dict] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

def generate_titan_name(difficulty: str) -> str:
    names = {
        "Easy": ["Small Titan", "Crawler Titan", "Stray Titan"],
        "Normal": ["Armored Titan", "Female Titan", "Beast Titan"],
        "Hard": ["Colossal Titan", "War Hammer Titan", "Founding Titan"]
    }
    return random.choice(names.get(difficulty, ["Mysterious Titan"]))

def generate_titan_hp(level: int, difficulty: str) -> int:
    hp_multipliers = {
        "Easy": 80,
        "Normal": 120,
        "Hard": 200
    }
    return level * hp_multipliers.get(difficulty, 100)

def generate_titan_xp(level: int, difficulty: str) -> int:
    xp_multipliers = {
        "Easy": 8,
        "Normal": 12,
        "Hard": 20
    }
    return level * xp_multipliers.get(difficulty, 10)
