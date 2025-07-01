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
    character_stats = ctx.get('character_stats', {})
    base_def = character_stats.get('DEF', 10)  # Default DEF of 10 if not found
    
    def_boost = base_def * 0.2 * fear_counter  # Scaling with DEF
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
    titan_hp = ctx.get('titan_hp', 0)
    atk = ctx['character_stats']['ATK']
    
    if attack_count <= 1:
        buffs = {
            "dodge": 1.0,
            "crit_rate": 1.1,  # +10% Crit Rate
            "reflex_counter": 2
        }
        message = "⚡ Golden Reflex! Dodged attack! +10% Crit for next 2 moves"
        
        # Titan-specific enhancements
        if 75 < titan_hp <= 100:  # Normal Titans
            buffs["bonus_dash"] = 1.0
            message += ", gained Bonus Dash to weak spot"
        elif titan_hp > 100:  # Difficult Titans
            buffs["defense_ignore"] = 0.15  # 15% Defense ignore
            message += ", Strike Window activated (15% Defense ignore)"
            
        return create_effect(
            message=message,
            buffs=buffs,
            counter_attack={
                "damage": atk * 1.5,
                "type": "slash",
                "message": "�️ Mina strikes back instantly!"
            }
        )
    return create_effect(
        message="Golden Reflex: Ready... +5% Evasion",
        buffs={"evasion": 0.05}
    )

def rookie_courage_effect(ctx: Dict) -> AbilityEffect:
    if ctx.get('ally_died_in_range', False):
        titan_hp = ctx.get('titan_hp', 0)
        allies_died = ctx.get('allies_died_this_turn', 1)
        
        buffs = {
            "actions_per_turn": 2.0,  # Double action
            "titan_damage": 1.2      # +20% Titan damage
        }
        message = "Rookie Courage: Rapid Focus Mode - Double action, +20% titan damage"
        
        if titan_hp > 100:
            buffs.update({
                "crit_rate": 1.15,  # +15% Crit Rate
                "INT": 10           # +10 INT
            })
            message += ", +15% Crit Rate, +10 INT vs Strong Titan"
            
        if allies_died > 1:
            buffs["reset_odm_cooldowns"] = 1.0
            message += ", ODM cooldowns reset"
            
        return create_effect(
            message=message,
            buffs=buffs
        )
        
    return create_effect(
        message="Rookie Courage: +5% movement speed to nearby allies",
        buffs={"movement_speed": 1.05}
    )

def nape_cutter_dash_effect(ctx: Dict) -> AbilityEffect:
    titan_hp = ctx.get('titan_hp', 0)
    base_dmg = ctx['base_damage'] * (2.0 if ctx.get('gas_full', False) else 1.0)
    message = "Nape Cutter Dash"
    debuffs = {}
    
    # Easy Titan auto-crit
    if titan_hp < 50 and ctx.get('titan_hp_percent', 1.0) < 0.25:
        base_dmg *= 2.0
        message += " (Auto-Critical vs Low HP Easy Titan)"
    # Normal Titan agility reduction
    elif titan_hp <= 100:
        debuffs["SPD"] = 10
        message += " (-10 Agility)"
    # Difficult Titan post-dodge/kill bonus
    elif titan_hp > 100 and (ctx.get('just_dodged', False) or ctx.get('just_killed', False)):
        base_dmg *= 1.5
        message += " (+50% damage after dodge/kill)"
    
    return create_effect(
        message=f"{message}: Dealt {int(base_dmg)} damage",
        damage=int(base_dmg),
        debuffs=debuffs
    )

def emergency_pulse_beacon_effect(ctx: Dict) -> AbilityEffect:
    buffs = {
        "DEF": 1.2,  # +20% Defense
        "ACC": 1.15  # +15% Accuracy
    }
    debuffs = {}
    message = "Emergency Pulse Beacon: +20% DEF, +15% ACC to allies"
    
    # Target switch chance
    if random.random() < 0.25:
        titan_hp = ctx.get('titan_hp', 0)
        if titan_hp < 75:
            debuffs["unstable"] = 1.0  # Cannot charge or grapple next turn
            message += ", Titan became Unstable (no charge/grapple)"
        message += ", target switched"
    
    # Clear fear under Rapid Focus
    if ctx.get('rapid_focus_active', False):
        buffs["clear_fear"] = 1.0
        message += ", cleared Fear debuffs"
    
    return create_effect(
        message=message,
        buffs=buffs,
        debuffs=debuffs,
        target_switched=bool(debuffs)
    )

def flicker_instinct_effect(ctx: Dict) -> AbilityEffect:
    titan_hp = ctx.get('titan_hp', 0)
    titan_hp_percent = ctx.get('titan_hp_percent', 1.0)
    base_dmg = ctx['base_damage']
    total_dmg = 0
    bleed_count = 0
    buffs = {"evasion": 0.8}  # 80% Evasion after final hit
    debuffs = {}
    message = []
    
    for i in range(3):  # 3 strikes
        attack_dmg = base_dmg * (0.9 + random.random() * 0.2)  # 90-110% variation
        total_dmg += attack_dmg
        if random.random() < 0.4:  # 40% Bleed chance
            bleed_count += 1
    
    # Titan tier enhancements
    if titan_hp < 50:  # Easy Titans
        if titan_hp_percent < 0.35:
            total_dmg *= 1.5  # More damage to low HP targets
            if titan_hp_percent < 0.15:
                total_dmg *= 2.0  # Execution damage
                message.append("Execution strike!")
    elif titan_hp <= 100:  # Normal Titans
        if bleed_count > 0:
            debuffs.update({
                "DEF": 0.9,  # -10% Defense
                "deep_bleed": 2  # 2 turns of HP loss
            })
            message.append("Deep Bleed applied")
    else:  # Difficult Titans
        buffs.update({
            "reset_nape_cutter": 1.0,  # Reset Nape Cutter cooldown
            "SPD": 1.3  # +30% Speed
        })
        message.append("Nape Cutter refreshed, Speed Surge gained")
    
    message_str = f"Flicker Instinct: {3} strikes dealt {int(total_dmg)} damage"
    if message:
        message_str += f" ({', '.join(message)})"
    
    return create_effect(
        message=message_str,
        damage=int(total_dmg),
        bleed_applied=bleed_count > 0,
        buffs=buffs,
        debuffs=debuffs
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
        base_stats={"ATK": 13, "DEF": 13, "ACC": 12, "INT": 12, "SPD": 11},
        max_potential={"ATK": 125, "DEF": 125, "ACC": 115, "INT": 115, "SPD": 115},
        abilities={
            "passive": {
                "panic_engine": {
                    "name": "Panic Engine",
                    "description": "Fear Counter: +1 when ally dies, +1 when below 50% HP, +1 when hit by Titan >75 HP. At 3 stacks: gains bonus action with 1.5x movement vs Normal Titans, ignores overwatch vs Difficult Titans. At 5 stacks: Heart Overload (+60% DEF, +40% SPD). VS Titans >100 HP: Next ability becomes AoE at 80% power (once per combat).",
                    "type": "passive",
                    "gas_cost": 20,
                    "is_unlocked": True,
                    "effect_function": panic_engine_effect
                },
                "cowards_fortitude": {
                    "name": "Coward's Fortitude",
                    "description": "If not attacked for 3 turns, gains +20% Healing Efficiency and creates 'Cover Aura' reducing AoE damage to nearby allies by 25%.",
                    "type": "passive",
                    "gas_cost": 120,
                    "level_required": 25,
                    "effect_function": cowards_fortitude_effect
                }
            },
            "active": {
                "field_patch": {
                    "name": "Field Patch",
                    "description": "Heals 5% HP/second for 3 turns. If target HP < 30%, applies Survivor's Shield: 500 damage absorption (1000 vs Easy Titans, 750 vs Normal). VS Difficult Titans: enemy breaking shield loses 10 ACC.",
                    "type": "active",
                    "gas_cost": 150,
                    "cooldown": 3,
                    "level_required": 50,
                    "base_damage": 0,
                    "effect_function": field_patch_effect
                },
                "supply_dump": {
                    "name": "Supply Dump",
                    "description": "Tosses random support item: Gas (resets movement), Blades (+15% Crit), Ration (20% HP), or Repair Kit (removes status effects). With Fear Counter ≥3: effect doubles/becomes AoE. 20% chance for Fear Syringe (stuns Titan, causes 30% AoE miss vs Normal/Difficult).",
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
                    "description": "Auto-triggers if last alive. 3 turns: Zero movement cost, +2 actions/turn, enhanced passives. Field Patch becomes AoE, Supply Dump drops 2 items, Heart Overload recastable. VS Titans >100 HP: abilities apply -20% Titan Morale Resist. Final action deals True Damage = 10% highest Titan HP. Collapses after 3 turns (bypasses revive).",
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
        max_potential={"ATK": 130, "DEF": 120, "ACC": 120, "INT": 110, "SPD": 125},
        abilities={
            "passive": {
                "golden_hour_reflex": {
                    "name": "Golden Hour Reflex",
                    "description": "First 2 attacks from Titans < 10m are auto-dodged. Each dodge +10% Crit Rate for 2 moves. VS Normal: Dodge adds Bonus Dash. VS Difficult: Dodge triggers Strike Window (next ODM attack ignores 15% Titan Defense).",
                    "type": "passive",
                    "gas_cost": 20,
                    "is_unlocked": True,
                    "effect_function": golden_hour_reflex_effect
                },
                "rookie_courage": {
                    "name": "Rookie Courage",
                    "description": "+5% Movement Speed to allies within 15m. If ally dies in range: Double Action Round, +20% Titan Damage (3 moves). VS Titans >100 HP: +15% Crit Rate, +10 INT. Multiple ally deaths: ODM cooldowns reset.",
                    "type": "passive",
                    "gas_cost": 100,
                    "level_required": 25,
                    "effect_function": rookie_courage_effect
                }
            },
            "active": {
                "nape_cutter_dash": {
                    "name": "Nape Cutter Dash",
                    "description": "High-speed ODM slice. 2x damage at Full Gas. Easy Titans: Auto-Crit if HP <25%. Normal: -10 Agility for 1 turn. Difficult: +50% damage after dodge/kill.",
                    "type": "active",
                    "gas_cost": 150,
                    "cooldown": 2,
                    "level_required": 50,
                    "base_damage": 60,
                    "effect_function": nape_cutter_dash_effect
                },
                "emergency_pulse_beacon": {
                    "name": "Emergency Pulse Beacon",
                    "description": "Allies: +20% DEF, +15% ACC (3 turns). Titans (30m): 25% target switch. Titans <75 HP become Unstable on switch. Under Rapid Focus: clears Fear debuffs on allies.",
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
                    "description": "3 strikes on multiple/single target. 40% Bleed Chance. 80% Evasion after final hit. Easy: Execute <15% HP. Normal: Deep Bleed (-10% DEF, HP loss). Difficult: Refreshes Nape Cutter + Speed Surge.",
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
