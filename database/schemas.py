from typing import Dict, List, Optional, Any, Callable
from pydantic import BaseModel, Field
from datetime import datetime

class CharacterStats(BaseModel):
    ATK: int = 10
    DEF: int = 10
    ACC: int = 10
    INT: int = 10
    SPD: int = 10
    HP: int = 650

class Ability(BaseModel):
    name: str
    description: str
    type: str  # "passive", "active", "ultimate"
    gas_cost: int = 0
    cooldown: Optional[int] = None
    level_required: int = 1
    is_unlocked: bool = False
    unlocked: bool = False  # Add this field to match MongoDB validation schema
    base_damage: int = 0
    disabled_against_titans: bool = False
    effect_function: Optional[Callable[[Dict], Any]] = None

    def __init__(self, **data):
        if 'gas_cost' not in data:
            data['gas_cost'] = {
                'ultimate': 300,
                'active': 50,
                'passive': 0
            }.get(data.get('type', 'passive'), 0)
        if 'is_unlocked' not in data and data.get('level_required', 1) == 1:
            data['is_unlocked'] = True
            data['unlocked'] = True  # Set both fields for compatibility
        if 'is_unlocked' in data and 'unlocked' not in data:
            data['unlocked'] = data['is_unlocked']  # Ensure unlocked is set whenever is_unlocked is set
        elif 'unlocked' in data and 'is_unlocked' not in data:
            data['is_unlocked'] = data['unlocked']  # Ensure is_unlocked is set whenever unlocked is set
        super().__init__(**data) 