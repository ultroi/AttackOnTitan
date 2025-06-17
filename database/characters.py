from typing import Dict, Optional, Callable, List
from pydantic import BaseModel
import random

# ======================
# CORE MODELS
# ======================

class AbilityEffect(BaseModel):
    """Container for ability effect results"""
    damage: int = 0
    healed: int = 0
    shield: int = 0
    stun_duration: int = 0
    message: str = ""
    buffs: Dict[str, float] = {}
    debuffs: Dict[str, float] = {}
    items_dropped: List[str] = []
    target_switched: bool = False
    bleed_applied: bool = False
    clear_debuffs: bool = False

class Ability(BaseModel):
    name: str
    description: str
    type: str  # "passive", "active", "ultimate"
    gas_cost: int = 0
    cooldown: Optional[int] = None
    level_required: int = 1
    is_unlocked: bool = False
    base_damage: int = 0
    disabled_against_titans: bool = False
    effect_function: Optional[Callable[[Dict], AbilityEffect]] = None

    def __init__(self, **data):
        # Set smart defaults based on ability type
        if 'gas_cost' not in data:
            data['gas_cost'] = {
                'ultimate': 300,
                'active': 50,
                'passive': 0
            }.get(data.get('type', 'passive'), 0)
            
        # Auto-unlock level 1 abilities
        if 'is_unlocked' not in data and data.get('level_required', 1) == 1:
            data['is_unlocked'] = True
            
        super().__init__(**data)

class CharacterData(BaseModel):
    name: str
    quote: str
    role: str
    archetype: str
    core_trait: str
    base_stats: Dict[str, int]
    abilities: Dict[str, Dict[str, Ability]]
    is_unlocked_by_default: bool = True
    requirements: Dict[str, int] = {}
    base_max_hp: int = 520  # Base HP at level 1

    def get_max_hp(self, level: int) -> int:
        """Progressive HP scaling formula"""
        hp = self.base_max_hp
        if level <= 50:
            hp += (level - 1) * 5
        elif level <= 100:
            hp += 245 + (level - 50) * 2  # 49*5 up to 50, then +2
        else:
            hp += 345 + (level - 100) * 2  # Up to level 125
        return hp

# ======================
# EFFECT FUNCTIONS
# ======================

def create_effect(**kwargs) -> AbilityEffect:
    """Helper to create standardized effect objects"""
    return AbilityEffect(**kwargs)

# Hitch Dreyse Effects
def civilian_shell_effect(ctx: Dict) -> AbilityEffect:
    if not ctx.get('first_damage_taken', False):
        return create_effect(
            message="Civilian Shell: 50% aggro avoidance, first damage halved",
            buffs={"aggro_reduction": 0.5, "damage_reduction": 0.5}
        )
    return create_effect(
        message="Wake-Up Protocol: +25% Speed/Awareness",
        buffs={"SPD": 1.25, "ACC": 1.25}
    )

def mocking_delay_effect(ctx: Dict) -> AbilityEffect:
    delay = 2 if ctx.get('is_intelligent_titan', False) or ctx.get('is_leader', False) else 1
    return create_effect(
        message=f"Mocking Delay: Enemy action delayed by {delay} turn(s)",
        debuffs={"delay": delay}
    )

def arc_net_trap_effect(ctx: Dict) -> AbilityEffect:
    return create_effect(
        message="Arc Net Trap: Stuns enemies, -30% Agility, allies gain +20% Dodge",
        stun_duration=1,
        debuffs={"SPD": 0.7},
        buffs={"dodge_rate": 0.2}
    )

def stimulant_injection_effect(ctx: Dict) -> AbilityEffect:
    if ctx.get('target_is_self', False):
        return create_effect(
            message="Stimulant Injection: 10% HP healed, Cold Edge granted",
            healed=int(ctx.get('max_hp', 100) * 0.1),
            buffs={"crit_chance": 2.0},
            damage=int(ctx.get('enemy_max_hp', 100) * 0.05),
            clear_debuffs=True
        )
    return create_effect(
        message="Stimulant Injection: 15% HP healed, debuffs cleared",
        healed=int(ctx.get('max_hp', 100) * 0.15),
        clear_debuffs=True
    )

def bunker_descent_effect(ctx: Dict) -> AbilityEffect:
    return create_effect(
        message="Bunker Descent: Allies healed 25%, gain Stealth, enemy accuracy halved",
        healed=int(ctx.get('max_hp', 100) * 0.25),
        buffs={"stealth": 1.0, "enemy_accuracy": 0.5}
    )

# Daz Effects
def panic_engine_effect(ctx: Dict) -> AbilityEffect:
    fear_counter = ctx.get('fear_counter', 0)
    if fear_counter >= 5:
        return create_effect(
            message="Panic Engine: Heart Overload, massive DEF/SPD boost",
            buffs={"DEF": 1.5, "SPD": 1.5}
        )
    elif fear_counter >= 3:
        return create_effect(
            message="Panic Engine: Extra move granted",
            buffs={"extra_move": 1.0}
        )
    return create_effect(message="Panic Engine: Building fear...")

def cowards_fortitude_effect(ctx: Dict) -> AbilityEffect:
    if ctx.get('turns_not_focused', 0) >= 3:
        return create_effect(
            message="Coward's Fortitude: +20% Healing, Cover Aura active",
            buffs={"healing_efficiency": 1.2, "aoe_damage_reduction": 0.25}
        )
    return create_effect(message="Coward's Fortitude: Waiting for safety...")

def field_patch_effect(ctx: Dict) -> AbilityEffect:
    healed = int(ctx.get('max_hp', 100) * 0.05 * 3)  # 5% per second for 3 turns
    if ctx.get('target_hp_percent', 1.0) < 0.3:
        return create_effect(
            message="Field Patch: 15% HP healed, Survivor's Shield granted",
            healed=healed,
            shield=500
        )
    return create_effect(
        message="Field Patch: 15% HP healed over 3 turns",
        healed=healed
    )

def supply_dump_effect(ctx: Dict) -> AbilityEffect:
    items = ["Gas Canister", "Blades", "Ration Pack", "Repair Kit", "Fear Syringe"]
    selected_item = random.choice(items)
    effect = create_effect(
        message=f"Supply Dump: Dropped {selected_item}",
        items_dropped=[selected_item]
    )
    if selected_item == "Fear Syringe":
        effect.stun_duration = 1
    return effect

def survival_override_effect(ctx: Dict) -> AbilityEffect:
    return create_effect(
        message="Survival Override: Movement costs negated, 2 actions/turn, enhanced passives",
        buffs={"movement_cost": 0.0, "actions_per_turn": 2.0, "passive_enhance": 1.0}
    )

# Mina Carolina Effects
def golden_hour_reflex_effect(ctx: Dict) -> AbilityEffect:
    if ctx.get('titan_height', 15) < 10 and ctx.get('attack_count', 0) <= 2:
        return create_effect(
            message="Golden Hour Reflex: Auto-dodge, +10% Crit Rate",
            buffs={"dodge": 1.0, "crit_rate": 1.1}
        )
    return create_effect(message="Golden Hour Reflex: Ready to dodge...")

def rookie_courage_effect(ctx: Dict) -> AbilityEffect:
    if ctx.get('ally_died_in_range', False):
        return create_effect(
            message="Rookie Courage: Rapid Focus Mode, double action, +20% titan damage",
            buffs={"actions_per_turn": 2.0, "titan_damage": 1.2}
        )
    return create_effect(
        message="Rookie Courage: +5% movement speed to nearby allies",
        buffs={"movement_speed": 1.05}
    )

def nape_cutter_dash_effect(ctx: Dict) -> AbilityEffect:
    damage = ctx.get('base_damage', 60) * (2 if ctx.get('gas_full', False) else 1)
    return create_effect(
        message=f"Nape Cutter Dash: Dealt {damage} damage",
        damage=damage
    )

def emergency_pulse_beacon_effect(ctx: Dict) -> AbilityEffect:
    if random.random() < 0.25:
        return create_effect(
            message="Emergency Pulse Beacon: +20% DEF, +15% ACC, titans may switch targets",
            buffs={"DEF": 1.2, "ACC": 1.15},
            target_switched=True
        )
    return create_effect(
        message="Emergency Pulse Beacon: +20% DEF, +15% ACC",
        buffs={"DEF": 1.2, "ACC": 1.15}
    )

def flicker_instinct_effect(ctx: Dict) -> AbilityEffect:
    damage = ctx.get('base_damage', 120)
    bleed = random.random() < 0.4
    return create_effect(
        message="Flicker Instinct: 3 rapid strikes, 80% Evasion on final hit",
        damage=damage,
        bleed_applied=bleed,
        buffs={"evasion": 0.8}
    )

# ======================
# CHARACTER DEFINITIONS
# ======================

def create_character(
    name: str,
    quote: str,
    role: str,
    archetype: str,
    core_trait: str,
    base_stats: Dict[str, int],
    abilities: Dict[str, Dict[str, Dict]],
    **kwargs
) -> CharacterData:
    """Factory function for standardized character creation"""
    processed_abilities = {}
    for ability_type, ability_dict in abilities.items():
        processed_abilities[ability_type] = {
            name: Ability(**data) for name, data in ability_dict.items()
        }
    
    return CharacterData(
        name=name,
        quote=quote,
        role=role,
        archetype=archetype,
        core_trait=core_trait,
        base_stats=base_stats,
        abilities=processed_abilities,
        **kwargs
    )

CHARACTERS: Dict[str, CharacterData] = {
    "Hitch Dreyse": create_character(
        name="Hitch Dreyse",
        quote="Guarded the walls. Mocked the world. Then saw it burn.",
        role="Wall Garrison Officer",
        archetype="Tactical Support / Debuff Specialist",
        core_trait="Apathy → Awakening (Echo Trait Bias: Loyalty → Desperation)",
        base_stats={"ATK": 12, "DEF": 11, "ACC": 10, "INT": 12, "SPD": 13},
        abilities={
            "passive": {
                "civilian_shell": {
                    "name": "Civilian Shell",
                    "description": "Hitch begins every encounter with 50% aggro avoidance. First damage taken is halved and forces a 'Wake-Up Protocol'—a permanent 1.25x boost to Speed and Awareness.",
                    "type": "passive",
                    "gas_cost": 80,
                    "is_unlocked": True,
                    "effect_function": civilian_shell_effect
                },
                "mocking_delay": {
                    "name": "Mocking Delay",
                    "description": "Enemies targeted by Hitch have their next action delayed by 1 turn. If targeting an Intelligent Titan or enemy leader, delay increases to 2 turns. Works once per enemy.",
                    "type": "passive",
                    "gas_cost": 70,
                    "level_required": 25,
                    "effect_function": mocking_delay_effect
                }
            },
            "active": {
                "arc_net_trap": {
                    "name": "Arc Net Trap",
                    "description": "Deploys an electric tripwire system over a targeted zone for 4 turns. Enemies entering are stunned for 1 turn and receive -30% Agility. Allies inside receive +20% Dodge Rate.",
                    "type": "active",
                    "gas_cost": 100,
                    "cooldown": 3,
                    "level_required": 50,
                    "base_damage": 50,
                    "effect_function": arc_net_trap_effect
                },
                "stimulant_injection": {
                    "name": "Stimulant Injection",
                    "description": "Targets an ally to recover 15% HP and clear all debuffs. If used on herself, heals 10% HP but grants 'Cold Edge' — next attack has 2x Critical chance and deals additional 5% of enemy max HP as morale damage.",
                    "type": "active",
                    "gas_cost": 50,
                    "cooldown": 2,
                    "level_required": 100,
                    "base_damage": 30,
                    "effect_function": stimulant_injection_effect
                }
            },
            "ultimate": {
                "bunker_descent": {
                    "name": "Bunker Descent",
                    "description": "Once per mission, activates a hidden bunker in the battlefield. All allies within 10 meters are healed for 25%, gain Stealth, and enemy accuracy is halved for 3 turns.",
                    "type": "ultimate",
                    "gas_cost": 320,
                    "cooldown": 1,
                    "level_required": 125,
                    "base_damage": 100,
                    "effect_function": bunker_descent_effect
                }
            }
        }
    ),
    "Daz": create_character(
        name="Daz",
        quote="Fear was his truth. But fear... is still a form of courage.",
        role="NPC-Ally / Emergency Support",
        archetype="RNG-dependent Last-Resort Utility",
        core_trait="Despair → Spark",
        base_stats={"ATK": 13, "DEF": 13, "ACC": 12, "INT": 12, "SPD": 11},
        abilities={
            "passive": {
                "panic_engine": {
                    "name": "Panic Engine",
                    "description": "Has a 'Fear Counter' that rises with teammate deaths or low HP. At 3 stacks: gains 1 extra move. At 5 stacks: unleashes 'Heart Overload' — massive buff to Defense and Speed.",
                    "type": "passive",
                    "gas_cost": 80,
                    "is_unlocked": True,
                    "effect_function": panic_engine_effect
                },
                "cowards_fortitude": {
                    "name": "Coward's Fortitude",
                    "description": "If not focused for 3 turns, gains +20% Healing Efficiency and creates a 'Cover Aura' reducing AoE damage to nearby allies by 25%.",
                    "type": "passive",
                    "gas_cost": 120,
                    "level_required": 25,
                    "effect_function": cowards_fortitude_effect
                }
            },
            "active": {
                "field_patch": {
                    "name": "Field Patch",
                    "description": "Heals 5% HP every second for 3 turns to a target ally. If target is below 30%, adds a 'Survivor's Shield' (absorbs 500 damage).",
                    "type": "active",
                    "gas_cost": 150,
                    "cooldown": 3,
                    "level_required": 50,
                    "base_damage": 0,
                    "effect_function": field_patch_effect
                },
                "supply_dump": {
                    "name": "Supply Dump",
                    "description": "Tosses a gear pack into the field. RNG selects one: Gas Canister, Blades, Ration Pack, or Repair Kit. Chance of dropping 'Fear Syringe' (stuns one enemy for 1 turn).",
                    "type": "active",
                    "gas_cost": 75,
                    "cooldown": 2,
                    "level_required": 100,
                    "base_damage": 20,
                    "effect_function": supply_dump_effect
                }
            },
            "ultimate": {
                "survival_override": {
                    "name": "Survival Override",
                    "description": "Triggers automatically if last alive. All movement costs negated. Gains 2 actions per turn. All passives enhanced (cooldown-free). Lasts 3 turns.",
                    "type": "ultimate",
                    "gas_cost": 400,
                    "cooldown": 1,
                    "level_required": 125,
                    "base_damage": 80,
                    "effect_function": survival_override_effect
                }
            }
        }
    ),
    "Mina Carolina": create_character(
        name="Mina Carolina",
        quote="She smiled before being eaten. The kind that shines even in silence.",
        role="Early Scout Cadet",
        archetype="Burst Damage + Agility Hybrid",
        core_trait="Innocence → Resilience",
        base_stats={"ATK": 14, "DEF": 12, "ACC": 13, "INT": 11, "SPD": 13},
        abilities={
            "passive": {
                "golden_hour_reflex": {
                    "name": "Golden Hour Reflex",
                    "description": "First 2 enemy attacks against Mina are auto-dodged if from titans below 10 meters. Dodging increases Crit Rate by 10% for next 2 moves.",
                    "type": "passive",
                    "gas_cost": 60,
                    "is_unlocked": True,
                    "effect_function": golden_hour_reflex_effect
                },
                "rookie_courage": {
                    "name": "Rookie Courage",
                    "description": "Allies gain +5% movement speed when near. If ally dies in 15m radius, enters 'Rapid Focus Mode': gains double-action round and +20% damage against titans for 3 moves.",
                    "type": "passive",
                    "gas_cost": 100,
                    "level_required": 25,
                    "effect_function": rookie_courage_effect
                }
            },
            "active": {
                "nape_cutter_dash": {
                    "name": "Nape Cutter Dash",
                    "description": "High-speed ODM slice targeting titan's weak spot. If executed at full gas, deals 2x normal damage.",
                    "type": "active",
                    "gas_cost": 150,
                    "cooldown": 2,
                    "level_required": 50,
                    "base_damage": 60,
                    "effect_function": nape_cutter_dash_effect
                },
                "emergency_pulse_beacon": {
                    "name": "Emergency Pulse Beacon",
                    "description": "Sends shock beacon; allies gain 20% Defense and 15% Accuracy for 3 turns. Titans in 30m radius have 25% chance to switch targets.",
                    "type": "active",
                    "gas_cost": 80,
                    "cooldown": 3,
                    "level_required": 100,
                    "base_damage": 0,
                    "effect_function": emergency_pulse_beacon_effect
                }
            },
            "ultimate": {
                "flicker_instinct": {
                    "name": "Flicker Instinct",
                    "description": "Performs 3 rapid ODM strikes across different titans. Each hit has 40% chance to inflict Bleed. On final hit, gains 80% Evasion for 1 round.",
                    "type": "ultimate",
                    "gas_cost": 450,
                    "cooldown": 1,
                    "level_required": 125,
                    "base_damage": 120,
                    "effect_function": flicker_instinct_effect
                }
            }
        }
    )
}

# ======================
# MANAGEMENT FUNCTIONS
# ======================

def get_character_data(character_name: str) -> Optional[CharacterData]:
    """Safe character data retrieval"""
    return CHARACTERS.get(character_name)

def add_new_character(character_data: Dict) -> None:
    """Safe method to add new characters"""
    if character_data['name'] in CHARACTERS:
        raise ValueError(f"Character {character_data['name']} already exists")
    CHARACTERS[character_data['name']] = create_character(**character_data)

def get_ability(character_name: str, ability_name: str) -> Optional[Ability]:
    """Get specific ability from any character"""
    char = get_character_data(character_name)
    if not char:
        return None
        
    for ability_type in char.abilities.values():
        if ability_name in ability_type:
            return ability_type[ability_name]
    return None

def list_all_abilities() -> Dict[str, List[str]]:
    """Get all abilities organized by character"""
    return {
        char_name: [
            f"{ability.name} ({ability.type})" 
            for ability_type in char_data.abilities.values() 
            for ability in ability_type.values()
        ]
        for char_name, char_data in CHARACTERS.items()
    }