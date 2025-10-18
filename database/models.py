from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone
import random

class CharacterStats(BaseModel):
    HP: int = 0
    attack: int = 0
    agility: int = 0
    luck: int = 0
    precision: int = 0

class Character(BaseModel):
    user_id: str
    name: str
    character_type: str
    current_hp: int
    level: int
    xp: int
    total_xp: int
    stats: CharacterStats
    gas: int
    max_gas: int
    active_abilities: List[Dict] = []
    passive_abilities: List[Dict] = []
    ultimate_abilities: List[Dict] = []
    unlocked_abilities: Dict[str, bool] = {}
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
    level: int
    xp: int
    total_xp: int
    gas: int
    valor: int
    crystal: int
    marks: int
    explore_count: int
    owned_characters: List[str] = []
    team: List[TeamMember] = []
    referral_code: str
    referred_by: Optional[str] = None
    referral_count: int = 0
    referral_milestones: Dict[str, bool] = {}
    location: str = "Trost District"
    travel: Optional[Dict] = None
    daily_explores: List[Dict] = []
    unlocked_areas: List[str] = ["Trost District"]
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
    pvp_matches: List[Dict] = []
    tax_history: List[Dict] = []
    guild_id: Optional[str] = None
    daily_streak: int = 0
    last_daily_claim: Optional[datetime] = None
    double_exp_end: Optional[datetime] = None
    completed_quests: List[str] = []
    mission14_area_counts: Dict[str, int] = {}
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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
