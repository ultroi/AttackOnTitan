from typing import Dict, Optional, Callable, List, Any
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
    counter_attack: Optional[Dict[str, Any]] = None
    class Config:
        arbitrary_types_allowed = True

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
    base_max_hp: int = 650  # Increased from 520 for better survivability

    def get_max_hp(self, level: int) -> int:
        """Progressive HP scaling formula"""
        hp = self.base_max_hp
        if level <= 50:
            hp += (level - 1) * 8  # Increased from 5 to 8
        elif level <= 100:
            hp += 392 + (level - 50) * 4  # Increased scaling
        else:
            hp += 592 + (level - 100) * 3  # Better high-level scaling
        return hp

# ======================
# EFFECT FUNCTIONS
# ======================

def create_effect(**kwargs) -> AbilityEffect:
    """Helper to create standardized effect objects"""
    return AbilityEffect(**kwargs)

# Hitch Dreyse Effects
def civilian_shell_effect(ctx: Dict) -> AbilityEffect:
    """Enhanced Civilian Shell with Wake-Up Protocol and Titan-specific reactions"""
    character_stats = ctx.get('character_stats', {})
    spd_bonus = character_stats.get('SPD', 13) * 0.02  # SPD scaling for aggro avoidance
    
    if not ctx.get('first_damage_taken', False):
        # Check if attacker is a Titan with >75 HP
        damage_reduction = 0.7 if ctx.get('titan_hp', 0) > 75 else 0.5
        # Add SPD-based aggro avoidance bonus
        aggro_avoidance = 0.5 + spd_bonus  # Base 50% + SPD scaling
        
        return create_effect(
            message=f"Civilian Shell: {int(aggro_avoidance*100)}% aggro avoidance, {int(damage_reduction*100)}% damage reduction",
            buffs={
                "aggro_reduction": aggro_avoidance,
                "damage_reduction": damage_reduction,
                "civilian_shell_active": 1.0
            }
        )
    else:
        # Wake-Up Protocol triggered - apply Titan-specific reactions
        titan_hp = ctx.get('titan_hp', 0)
        spd_stat = character_stats.get('SPD', 13)
        acc_stat = character_stats.get('ACC', 10)
        
        buffs = {
            "SPD": 1.25 + (spd_stat * 0.01),  # SPD scaling for speed boost
            "ACC": 1.15 + (acc_stat * 0.005),  # ACC scaling for awareness
            "wake_up_active": 1.0
        }
        
        message = f"Wake-Up Protocol: +{int((1.25 + spd_stat * 0.01 - 1)*100)}% Speed, +{int((1.15 + acc_stat * 0.005 - 1)*100)}% Awareness"
        
        # Titan-specific reactions
        if titan_hp < 50:  # Easy Titans
            buffs["crit_rate"] = 1.25
            message += ", +25% Crit Rate vs Easy Titans"
        elif titan_hp < 100:  # Normal Titans  
            buffs["auto_dodge_counter"] = 1.0
            message += ", Auto-Dodge first counterattack vs Normal Titans"
        elif titan_hp < 125:  # Difficult Titans
            message += ", Applies Dazed Focus to enemy"
            return create_effect(
                message=message,
                buffs=buffs,
                debuffs={"ACC": 10, "SPD": 5}  # Enemy gets debuffed
            )
        
        return create_effect(message=message, buffs=buffs)


def mocking_delay_effect(ctx: Dict) -> AbilityEffect:
    """Enhanced Mocking Delay with morale stagger for Difficult Titans"""
    titan_hp = ctx.get('titan_hp', 0)
    character_stats = ctx.get('character_stats', {})
    int_stat = character_stats.get('INT', 12)
    acc_stat = character_stats.get('ACC', 10)
    
    is_intelligent = ctx.get('is_intelligent_titan', False) or titan_hp > 90
    
    # INT scaling for delay effectiveness
    base_delay = 1
    int_bonus = int(int_stat * 0.1)  # INT provides delay bonus
    
    if is_intelligent:
        delay = 3 + int_bonus  # Base 3 + INT scaling
        message = f"Mocking Delay: Enemy action delayed by {delay} turns (Intelligent/Boss Titan, +{int_bonus} from INT)"
    else:
        delay = base_delay + int_bonus
        message = f"Mocking Delay: Enemy action delayed by {delay} turn (+{int_bonus} from INT)"
    
    debuffs = {"delay": delay}
    
    # Against Difficult Titans (assuming >100 HP), apply morale stagger
    if titan_hp > 100:
        # ACC scaling for debuff effectiveness
        spd_reduction = 15 + (acc_stat * 0.5)  # ACC improves targeting precision
        debuffs["SPD"] = int(spd_reduction)
        debuffs["morale_stagger"] = 3
        message += f", applies morale stagger (-{int(spd_reduction)} SPD for 3 turns, +{acc_stat*0.5:.1f} from ACC)"
    
    return create_effect(
        message=message,
        debuffs=debuffs
    )

def arc_net_trap_effect(ctx: Dict) -> AbilityEffect:
    """Enhanced Arc Net Trap with variable effects based on Titan difficulty"""
    titan_hp = ctx.get('titan_hp', 0)
    character_stats = ctx.get('character_stats', {})
    int_stat = character_stats.get('INT', 12)
    spd_stat = character_stats.get('SPD', 13)
    
    # Determine Titan tier and apply appropriate effects
    if titan_hp < 50:  # Easy Titans
        stun_duration = 2  # Extended to 2 turns
        agility_penalty = 0.3 + (int_stat * 0.01)  # INT improves trap effectiveness
        tier = "Easy"
    elif titan_hp < 100:  # Normal Titans
        stun_duration = 1 + int(int_stat * 0.05)  # INT can extend stun
        agility_penalty = 0.4 + (int_stat * 0.01)
        tier = "Normal"
    else:  # Difficult Titans
        stun_duration = 1 + int(int_stat * 0.03)  # Reduced INT scaling for difficult
        agility_penalty = 0.5 + (int_stat * 0.01)
        tier = "Difficult"
        
    base_damage = ctx.get('base_damage', 0) * 1.2
    
    # SPD affects ally bonuses
    dodge_bonus = 0.2 + (spd_stat * 0.005)  # SPD improves team coordination
    
    buffs = {
        "dodge_rate": dodge_bonus,  # SPD-scaled Dodge Rate for allies
        "crit_evasion": 0.05 + (spd_stat * 0.002)  # SPD-scaled Crit Evasion
    }
    
    debuffs = {"SPD": agility_penalty}
    
    # Add Entangled Core for Difficult Titans
    if tier == "Difficult":
        miss_chance = 0.2 + (int_stat * 0.005)  # INT improves entanglement
        debuffs["entangled_core"] = miss_chance
        message = f"Arc Net Trap ({tier}): {stun_duration} turn stun, -{int(agility_penalty*100):.1f}% Agility, {int(miss_chance*100):.1f}% Entangled Core (INT: +{int_stat*0.005*100:.1f}%)"
    else:
        message = f"Arc Net Trap ({tier}): {stun_duration} turn stun, -{int(agility_penalty*100):.1f}% Agility (INT: +{int_stat*0.01*100:.1f}%)"
    
    return create_effect(
        message=message,
        damage=int(base_damage),
        stun_duration=stun_duration,
        debuffs=debuffs,
        buffs=buffs
    )

def stimulant_injection_effect(ctx: Dict) -> AbilityEffect:
    """Enhanced Stimulant Injection with Cold Edge and Titan-tier scaling"""
    if ctx.get('target_is_self', False):
        # Self-use: 10% HP heal + Cold Edge with Titan-tier effects
        heal_amount = int(ctx['character_max_hp'] * 0.1)
        titan_hp = ctx.get('titan_hp', 0)
        
        # Base Cold Edge effect
        buffs = {"cold_edge_active": 1.0}
        message = f"Stimulant Injection (Self): {heal_amount} HP healed, Cold Edge activated"
        
        # Titan-tier specific effects
        if titan_hp < 50:  # Easy Titan
            buffs.update({
                "crit_chance": 2.0,  # 2x Crit chance
                "morale_damage": 0.1  # 10% of enemy HP as morale damage
            })
            message += " (vs Easy: 2x Crit, +10% morale damage)"
        elif titan_hp < 100:  # Normal Titan
            buffs.update({
                "crit_chance": 2.0,  # 2x Crit chance
                "morale_damage": 0.05  # 5% HP morale damage
            })
            # Apply defense reduction to enemy
            return create_effect(
                message=message + " (vs Normal: 2x Crit, +5% morale damage, -15 DEF to enemy)",
                healed=heal_amount,
                buffs=buffs,
                debuffs={"DEF": 15},  # Enemy gets -15 Defense
                clear_debuffs=True
            )
        else:  # Difficult Titan (>100 HP)
            buffs.update({
                "crit_chance": 2.5,  # 2.5x Crit chance
                "defense_ignore": 0.25,  # Ignores 25% Defense
                "morale_damage": 0.05,  # 5% HP morale damage
                "int_drain": 10  # 10 INT Drain to enemy
            })
            return create_effect(
                message=message + " (vs Difficult: 2.5x Crit, ignores 25% DEF, morale damage, INT drain)",
                healed=heal_amount,
                buffs=buffs,
                debuffs={"INT": 10},  # Enemy gets -10 INT
                clear_debuffs=True
            )
        
        return create_effect(
            message=message,
            healed=heal_amount,
            buffs=buffs,
            clear_debuffs=True
        )
    else:
        # Ally use: 15% HP heal + full debuff cleanse
        heal_amount = int(ctx['character_max_hp'] * 0.15)
        return create_effect(
            message=f"Stimulant Injection (Ally): {heal_amount} HP healed, all debuffs cleared",
            healed=heal_amount,
            clear_debuffs=True
        )

def bunker_descent_effect(ctx: Dict) -> AbilityEffect:
    """Enhanced Bunker Descent - Ultimate battlefield control ability"""
    heal_amount = int(ctx['character_max_hp'] * 0.25)  # 25% heal for all allies
    titan_hp = ctx.get('titan_hp', 0)
    
    # Base effects for all allies
    buffs = {
        "stealth": 1.0,
        "evasion": 0.5,  # +50% Evasion
        "morale_resistance": 0.3,  # +30% Morale Resistance (new)
        "enemy_accuracy": 0.5,  # Enemy accuracy halved
        "bunker_descent_duration": 3  # Lasts 3 turns
    }
    
    message = f"Bunker Descent: All allies healed {heal_amount} HP, gain Stealth, +50% Evasion, +30% Morale Resistance, enemy accuracy halved for 3 turns"
    
    # Additional effects if enemy Titans have >100 HP
    if titan_hp > 100:
        buffs.update({
            "surveillance_disruption": 0.5,  # 50% chance to skip AoE targeting
            "grab_immunity": 1.0  # +1 turn immunity to grab attacks
        })
        message += ". High-HP Titans suffer Surveillance Disruption (50% AoE failure), allies gain grab immunity"
    
    return create_effect(
        message=message,
        healed=heal_amount,
        buffs=buffs
    )

# Daz Enhanced Effects
def panic_engine_effect(ctx: Dict) -> AbilityEffect:
    fear_counter = ctx.get('fear_counter', 0)
    def_boost = ctx['character_stats']['DEF'] * 0.2 * fear_counter  # Scaling with DEF
    if fear_counter >= 5:
        return create_effect(
            message="Panic Engine: Heart Overload, massive DEF/SPD boost",
            buffs={
                "DEF": 2.0 + def_boost,  # From 1.5 to 2.0 plus scaling
                "SPD": 2.0,
                "ATK": 1.5  # New attack boost
            }
        )
    elif fear_counter >= 3:
        return create_effect(
            message="Panic Engine: Extra move granted, +15% ATK",
            buffs={
                "extra_move": 1.0,
                "ATK": 1.15
            }
        )
    return create_effect(
        message="Panic Engine: Building fear... +5% DEF per stack",
        buffs={"DEF": 1.0 + (fear_counter * 0.05)}
    )

def cowards_fortitude_effect(ctx: Dict) -> AbilityEffect:
    if ctx.get('turns_not_focused', 0) >= 2:  # Reduced from 3 turns
        heal_boost = ctx['character_stats']['INT'] * 0.1  # INT scaling
        return create_effect(
            message="Coward's Fortitude: +30% Healing, Cover Aura active",
            buffs={
                "healing_efficiency": 1.3 + heal_boost,  # From 1.2 to 1.3 + scaling
                "aoe_damage_reduction": 0.35  # From 0.25 to 0.35
            }
        )
    return create_effect(
        message="Coward's Fortitude: Waiting for safety... +5% Dodge",
        buffs={"dodge_rate": 0.05}
    )

def field_patch_effect(ctx: Dict) -> AbilityEffect:
    base_heal = ctx['character_stats']['INT'] * 0.5  # INT scaling
    healed = int(base_heal * 3)  # Over 3 turns
    if ctx.get('target_hp_percent', 1.0) < 0.3:
        shield_amount = 500 + ctx['character_stats']['DEF'] * 2  # DEF scaling
        return create_effect(
            message=f"Field Patch: {healed} HP healed, Survivor's Shield ({shield_amount}) granted",
            healed=healed,
            shield=shield_amount
        )
    return create_effect(
        message=f"Field Patch: {healed} HP healed over 3 turns",
        healed=healed
    )

def supply_dump_effect(ctx: Dict) -> AbilityEffect:
    items = ["Gas Canister", "Blades", "Ration Pack", "Repair Kit", "Fear Syringe"]
    selected_item = random.choice(items)
    effect = create_effect(
        message=f"Supply Dump: Dropped {selected_item}",
        items_dropped=[selected_item]
    )
    
    # Enhanced item effects
    if selected_item == "Fear Syringe":
        effect.stun_duration = 2  # From 1 to 2 turns
        effect.damage = ctx['base_damage'] * 0.8  # 80% of base damage
    elif selected_item == "Gas Canister":
        effect.buffs = {"gas_regen": 50}  # Restore 50 gas
    elif selected_item == "Blades":
        effect.buffs = {"ATK": 0.2}  # +20% ATK
    return effect

def survival_override_effect(ctx: Dict) -> AbilityEffect:
    return create_effect(
        message="Survival Override: Movement costs negated, 2 actions/turn, enhanced passives",
        buffs={
            "movement_cost": 0.0, 
            "actions_per_turn": 2.0,  # Reduced from 3 to 2
            "passive_enhance": 1.0,
            "ATK": 1.25,  # Reduced from 1.5 to 1.25
            "SPD": 1.25   # Reduced from 1.5 to 1.25
        }
    )

# Mina Carolina Enhanced Effects
def golden_hour_reflex_effect(ctx: Dict) -> AbilityEffect:
    attack_count = ctx.get('attack_count', 0)
    spd_boost = ctx['character_stats']['SPD'] * 0.25  # 25% of SPD as bonus
    atk = ctx['character_stats']['ATK']
    
    if attack_count <= 1:
        return create_effect(
            message="⚡ Golden Reflex! Dodged attack + Counter! +30% Crit for next 2 hits",
            buffs={
                "dodge": 1.0,
                "crit_damage": 1.3,
                "reflex_counter": 2,
                "SPD": spd_boost
            },
            counter_attack={
                "damage": atk * 1.5,  # 150% ATK counter strike
                "type": "slash",     # You can customize: slash/pierce/blunt
                "message": "🗡️ Mina strikes back instantly!"
            }
        )
    elif ctx.get('hp_percent', 1.0) < 0.3 and ctx.get('reflex_available', True):
        return create_effect(
            message="💢 Last Resort Dodge! (Low HP) + Temporary Invincibility",
            buffs={
                "dodge": 1.0,
                "damage_reduction": 0.8
            },
            clear_buffs=["reflex_available"],
            counter_attack={
                "damage": atk * 2.5,
                "type": "pierce",
                "message": "💥 Mina pierces the Titan's eye in desperation!"
            }
        )
    return create_effect(
        message="Golden Reflex: Ready... +5% Evasion",
        buffs={"evasion": 0.05}
    )

def rookie_courage_effect(ctx: Dict) -> AbilityEffect:
    if ctx.get('ally_died_in_range', False):
        return create_effect(
            message="Rookie Courage: Rapid Focus Mode, triple action, +30% titan damage",
            buffs={
                "actions_per_turn": 3.0,  # From 2 to 3
                "titan_damage": 1.3,     # From 1.2 to 1.3
                "ATK": 1.2                # Additional ATK boost
            }
        )
    return create_effect(
        message="Rookie Courage: +10% movement speed and +5% ATK to nearby allies",
        buffs={
            "movement_speed": 1.1,  # From 1.05 to 1.1
            "ATK": 1.05
        }
    )

def nape_cutter_dash_effect(ctx: Dict) -> AbilityEffect:
    base_dmg = ctx['base_damage'] * (2.5 if ctx.get('gas_full', False) else 1.8)  # Increased multipliers
    crit_chance = min(0.5, ctx['character_stats']['ACC'] * 0.01)  # ACC affects crit chance
    if random.random() < crit_chance:
        base_dmg *= 2.0
        return create_effect(
            message=f"⚡ CRITICAL Nape Cutter Dash: Dealt {base_dmg} damage",
            damage=base_dmg
        )
    return create_effect(
        message=f"Nape Cutter Dash: Dealt {base_dmg} damage",
        damage=base_dmg
    )

def emergency_pulse_beacon_effect(ctx: Dict) -> AbilityEffect:
    def_boost = 0.25 + ctx['character_stats']['DEF'] * 0.002  # DEF scaling
    if random.random() < 0.35:  # Increased from 0.25
        return create_effect(
            message=f"Emergency Pulse Beacon: +{def_boost*100:.0f}% DEF, +20% ACC, titans may switch targets",
            buffs={
                "DEF": 1.0 + def_boost,
                "ACC": 1.2
            },
            target_switched=True
        )
    return create_effect(
        message=f"Emergency Pulse Beacon: +{def_boost*100:.0f}% DEF, +20% ACC",
        buffs={
            "DEF": 1.0 + def_boost,
            "ACC": 1.2
        }
    )

def flicker_instinct_effect(ctx: Dict) -> AbilityEffect:
    base_dmg = ctx['base_damage'] * 1.2  # Reduced from 1.5 to 1.2
    attacks = 3  # Reduced from 4 to 3
    total_dmg = 0
    bleed_count = 0
    
    for _ in range(attacks):
        attack_dmg = base_dmg * (0.8 + random.random() * 0.4)  # 80-120% variation
        total_dmg += attack_dmg
        if random.random() < 0.35:  # Reduced from 0.5 to 0.35
            bleed_count += 1
    
    return create_effect(
        message=f"Flicker Instinct: {attacks} rapid strikes, 75% Evasion on final hit",
        damage=int(total_dmg),
        bleed_applied=bleed_count > 0,
        buffs={
            "evasion": 0.75,  # Reduced from 0.9 to 0.75
            "SPD": 0.15       # Reduced from 0.2 to 0.15
        }
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
    # No need to add Strike ability since Basic Attack is handled in battle system
    
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
                    "description": "Starts with 50% aggro avoidance. First hit reduces damage by 70% if attacker is a Titan >75 HP. Triggers Wake-Up Protocol: permanent 1.25x Speed, +15 Awareness, and Titan-specific reactions based on enemy HP.",
                    "type": "passive",
                    "gas_cost": 0,
                    "is_unlocked": True,
                    "effect_function": civilian_shell_effect
                },
                "mocking_delay": {
                    "name": "Mocking Delay",
                    "description": "Enemies targeted by Hitch have their next action delayed by 1 turn. If targeting an Intelligent Titan or Boss (HP > 90), delay increases to 3 turns. Against Difficult Titans (HP > 100), applies morale stagger: -15 SPD for 3 turns.",
                    "type": "passive",
                    "gas_cost": 70,
                    "level_required": 25,
                    "effect_function": mocking_delay_effect
                }
            },
            "active": {
                "arc_net_trap": {
                    "name": "Arc Net Trap",
                    "description": "Deploys an electric tripwire system for 4 turns. Stun duration and agility penalties scale with Titan tier: Easy (2 turn stun, -30%), Normal (1 turn, -40%), Difficult (1 turn, -50% + Entangled Core). Allies gain +20% Dodge Rate and +5% Crit Evasion.",
                    "type": "active",
                    "gas_cost": 100,
                    "cooldown": 4,
                    "level_required": 50,
                    "base_damage": 45,
                    "effect_function": arc_net_trap_effect
                },
                "stimulant_injection": {
                    "name": "Stimulant Injection",
                    "description": "Ally: 15% HP heal + full debuff cleanse. Self: 10% HP heal + Cold Edge with Titan-tier scaling. Easy: 2x Crit + 10% morale damage. Normal: 2x Crit + 5% morale + -15 enemy DEF. Difficult: 2.5x Crit + ignores 25% DEF + morale + INT drain.",
                    "type": "active",
                    "gas_cost": 120,
                    "cooldown": 3,
                    "level_required": 75,
                    "base_damage": 0,
                    "effect_function": stimulant_injection_effect
                }
            },
            "ultimate": {
                "bunker_descent": {
                    "name": "Bunker Descent",
                    "description": "Ultimate battlefield control. Heals all allies 25% HP. All allies gain: Stealth, +50% Evasion, +30% Morale Resistance, enemy accuracy halved for 3 turns. If enemy Titans have >100 HP: Surveillance Disruption (50% AoE failure) + allies gain grab immunity.",
                    "type": "ultimate",
                    "gas_cost": 400,
                    "cooldown": 1,
                    "level_required": 125,
                    "base_damage": 0,
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
        base_stats={"ATK": 15, "DEF": 16, "ACC": 14, "INT": 13, "SPD": 12},
        abilities={
            "passive": {
                "panic_engine": {
                    "name": "Panic Engine",
                    "description": "Has a 'Fear Counter' that rises with teammate deaths or low HP. At 3 stacks: gains 1 extra move. At 5 stacks: unleashes 'Heart Overload' — massive buff to Defense and Speed.",
                    "type": "passive",
                    "gas_cost": 20,
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
        base_stats={"ATK": 16, "DEF": 14, "ACC": 15, "INT": 12, "SPD": 17},
        abilities={
            "passive": {
                "golden_hour_reflex": {
                    "name": "Golden Hour Reflex",
                    "description": "First 2 enemy attacks against Mina are auto-dodged if from titans below 10 meters. Dodging increases Crit Rate by 10% for next 2 moves.",
                    "type": "passive",
                    "gas_cost": 20,
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
