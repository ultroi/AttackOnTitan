from typing import Dict, Optional, List, Any
from pydantic import BaseModel, Field
from database.schemas import Ability, CharacterStats
import random
import logging

logger = logging.getLogger(__name__)

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
    buffs: Dict[str, float] = Field(default_factory=dict)
    debuffs: Dict[str, float] = Field(default_factory=dict)
    items_dropped: List[str] = Field(default_factory=list)
    target_switched: bool = False
    bleed_applied: bool = False
    clear_debuffs: bool = False
    counter_attack: Optional[Dict[str, Any]] = None

    model_config = {"arbitrary_types_allowed": True}

class BattleContext(BaseModel):
    """Context for ability effect functions"""
    character_stats: CharacterStats
    character_max_hp: int = 100
    titan_hp: int = 0
    titan_hp_percent: float = 1.0
    base_damage: int = 0
    first_damage_taken: bool = False
    is_intelligent_titan: bool = False
    target_is_self: bool = False
    fear_counter: int = 0
    turns_not_focused: int = 0
    target_hp_percent: float = 1.0
    attack_count: int = 0
    gas_full: bool = False
    just_dodged: bool = False
    just_killed: bool = False
    ally_died_in_range: bool = False
    allies_died_this_turn: int = 0
    rapid_focus_active: bool = False
    is_pvp: bool = False
    opponent_stats: Optional[CharacterStats] = None
    opponent_hp: int = 0
    opponent_max_hp: int = 100

class CharacterData(BaseModel):
    name: str
    quote: str
    role: str
    archetype: str
    core_trait: str
    base_stats: CharacterStats
    active_abilities: List[Ability] = Field(default_factory=list)
    passive_abilities: List[Ability] = Field(default_factory=list)
    ultimate_abilities: List[Ability] = Field(default_factory=list)
    is_unlocked_by_default: bool = True
    requirements: Dict[str, int] = Field(default_factory=dict)
    max_potential: Optional[Dict[str, int]] = None

    def get_max_hp(self, level: int) -> int:
        """Progressive HP scaling formula"""
        hp = self.base_stats.HP
        if level <= 50:
            hp += (level - 1) * 8
        elif level <= 100:
            hp += 392 + (level - 50) * 4
        else:
            hp += 592 + (level - 100) * 3
        return hp

# ======================
# EFFECT FUNCTIONS
# ======================

def create_effect(**kwargs) -> AbilityEffect:
    """Helper to create standardized effect objects"""
    return AbilityEffect(**kwargs)

def civilian_shell_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Civilian Shell with Wake-Up Protocol and Titan/PVP-specific reactions"""
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_stats = ctx.get("character_stats", {})
        spd = character_stats.get("SPD", 0)
        first_damage_taken = ctx.get("first_damage_taken", False)
        titan_hp = ctx.get("titan_hp", 0)
        acc = character_stats.get("ACC", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        spd = ctx.character_stats.SPD
        acc = ctx.character_stats.ACC
        first_damage_taken = ctx.first_damage_taken
        titan_hp = ctx.titan_hp
        is_pvp = getattr(ctx, "is_pvp", False)

    spd_bonus = spd * 0.02
    
    # PVP specific behavior
    if is_pvp:
        if not first_damage_taken:
            # Initial activation in PVP
            return create_effect(
                message=f"Civilian Shell (PVP): +20% damage reduction for first hit, +15% evasion",
                buffs={
                    "damage_reduction": 0.2,
                    "evasion": 0.15,
                    "civilian_shell_active": 1.0
                }
            )
        else:
            # Wake-up protocol in PVP - balanced for player vs player
            buffs = {
                "SPD": 1.15,  # Reduced from 1.25 for PVP balance
                "ACC": 1.10,  # Reduced from 1.15 for PVP balance
                "wake_up_active": 1.0,
                "crit_rate": 1.10  # Consistent crit bonus in PVP
            }
            return create_effect(
                message=f"Wake-Up Protocol (PVP): +15% Speed, +10% Accuracy, +10% Crit Rate",
                buffs=buffs
            )
    
    # Original Titan battle behavior
    if not first_damage_taken:
        damage_reduction = 0.7 if titan_hp > 75 else 0.5
        aggro_avoidance = 0.5 + spd_bonus
        return create_effect(
            message=f"Civilian Shell: {int(aggro_avoidance*100)}% aggro avoidance, {int(damage_reduction*100)}% damage reduction",
            buffs={
                "aggro_reduction": aggro_avoidance,
                "damage_reduction": damage_reduction,
                "civilian_shell_active": 1.0
            }
        )
    else:
        buffs = {
            "SPD": 1.25 + (spd * 0.01),
            "ACC": 1.15 + (acc * 0.005),
            "wake_up_active": 1.0
        }
        message = f"Wake-Up Protocol: +{int((1.25 + spd * 0.01 - 1)*100)}% Speed, +{int((1.15 + acc * 0.005 - 1)*100)}% Awareness"
        if titan_hp < 50:
            buffs["crit_rate"] = 1.25
            message += ", +25% Crit Rate vs Easy Titans"
        elif titan_hp < 100:
            buffs["auto_dodge_counter"] = 1.0
            message += ", Auto-Dodge first counterattack vs Normal Titans"
        elif titan_hp < 125:
            message += ", Applies Dazed Focus to enemy"
            return create_effect(
                message=message,
                buffs=buffs,
                debuffs={"ACC": 10, "SPD": 5}
            )
        return create_effect(message=message, buffs=buffs)

def mocking_delay_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Mocking Delay with morale stagger for Difficult Titans and balanced for PVP"""
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_stats = ctx.get("character_stats", {})
        int_ = character_stats.get("INT", 0)
        acc = character_stats.get("ACC", 0)
        is_intelligent = ctx.get("is_intelligent_titan", False) or ctx.get("titan_hp", 0) > 90
        titan_hp = ctx.get("titan_hp", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        int_ = ctx.character_stats.INT
        acc = ctx.character_stats.ACC
        is_intelligent = ctx.is_intelligent_titan or ctx.titan_hp > 90
        titan_hp = ctx.titan_hp
        is_pvp = getattr(ctx, "is_pvp", False)

    # PVP specific behavior - more balanced for player vs player
    if is_pvp:
        # In PVP, stun is more powerful, so we balance it by reducing duration
        int_bonus = int(int_ * 0.05)  # reduced bonus in PVP
        delay = 1 + int_bonus
        
        debuffs = {
            "stun": 1,  # Apply a 1-turn stun in PVP
            "SPD": 10,  # Apply a fixed SPD reduction
            "ACC": 10   # Apply a fixed ACC reduction
        }
        
        return create_effect(
            message=f"Mocking Delay (PVP): Enemy is stunned for 1 turn, -10 Speed, -10 Accuracy",
            debuffs=debuffs
        )
    
    # Original Titan battle behavior
    int_bonus = int(int_ * 0.1)
    base_delay = 1
    if is_intelligent:
        delay = 3 + int_bonus
        message = f"Mocking Delay: Enemy action delayed by {delay} turns (Intelligent/Boss Titan, +{int_bonus} from INT)"
    else:
        delay = base_delay + int_bonus
        message = f"Mocking Delay: Enemy action delayed by {delay} turn (+{int_bonus} from INT)"
    debuffs = {"delay": delay}
    if titan_hp > 100:
        spd_reduction = 15 + (acc * 0.5)
        debuffs["SPD"] = int(spd_reduction)
        debuffs["morale_stagger"] = 3
        message += f", applies morale stagger (-{int(spd_reduction)} SPD for 3 turns, +{acc*0.5:.1f} from ACC)"
    return create_effect(message=message, debuffs=debuffs)

def arc_net_trap_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Arc Net Trap with variable effects based on Titan difficulty or PVP setting"""
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_stats = ctx.get("character_stats", {})
        int_ = character_stats.get("INT", 0)
        spd = character_stats.get("SPD", 0)
        base_damage = ctx.get("base_damage", 0)
        titan_hp = ctx.get("titan_hp", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        int_ = ctx.character_stats.INT
        spd = ctx.character_stats.SPD
        base_damage = ctx.base_damage
        titan_hp = ctx.titan_hp
        is_pvp = getattr(ctx, "is_pvp", False)
    
    # PVP specific behavior - more balanced for player vs player
    if is_pvp:
        # In PVP, stun and agility effects are powerful but should be balanced
        stun_duration = 1  # Fixed 1 turn stun in PVP
        base_damage_val = base_damage * 1.3  # Slightly more damage in PVP
        
        # More modest buffs for self in PVP
        buffs = {
            "dodge_rate": 0.15,
            "crit_evasion": 0.10
        }
        
        # More modest debuffs for opponent in PVP
        debuffs = {
            "SPD": 15,
            "ACC": 10
        }
        
        return create_effect(
            message=f"Arc Net Trap (PVP): 1 turn stun, -15 Speed, -10 Accuracy to enemy, +15% Dodge Rate for self",
            damage=int(base_damage_val),
            stun_duration=stun_duration,
            debuffs=debuffs,
            buffs=buffs
        )
    
    # Original Titan battle behavior
    if titan_hp < 50:
        stun_duration = 2
        agility_penalty = 0.3 + (int_ * 0.01)
        tier = "Easy"
    elif titan_hp < 100:
        stun_duration = 1 + int(int_ * 0.05)
        agility_penalty = 0.4 + (int_ * 0.01)
        tier = "Normal"
    else:
        stun_duration = 1 + int(int_ * 0.03)
        agility_penalty = 0.5 + (int_ * 0.01)
        tier = "Difficult"
    base_damage_val = base_damage * 1.2
    dodge_bonus = 0.2 + (spd * 0.005)
    buffs = {
        "dodge_rate": dodge_bonus,
        "crit_evasion": 0.05 + (spd * 0.002)
    }
    debuffs = {"SPD": agility_penalty}
    if tier == "Difficult":
        miss_chance = 0.2 + (int_ * 0.005)
        debuffs["entangled_core"] = miss_chance
        message = f"Arc Net Trap ({tier}): {stun_duration} turn stun, -{int(agility_penalty*100):.1f}% Agility, {int(miss_chance*100):.1f}% Entangled Core (INT: +{int_*0.005*100:.1f}%)"
    else:
        message = f"Arc Net Trap ({tier}): {stun_duration} turn stun, -{int(agility_penalty*100):.1f}% Agility (INT: +{int_*0.01*100:.1f}%)"
    return create_effect(
        message=message,
        damage=int(base_damage_val),
        stun_duration=stun_duration,
        debuffs=debuffs,
        buffs=buffs
    )

def stimulant_injection_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Stimulant Injection with Cold Edge and Titan-tier scaling or PVP balance"""
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_max_hp = ctx.get("character_max_hp", 100)
        titan_hp = ctx.get("titan_hp", 0)
        target_is_self = ctx.get("target_is_self", False)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
        opponent_hp = ctx.get("opponent_hp", 0)
        opponent_max_hp = ctx.get("opponent_max_hp", 100)
    else:
        character_max_hp = ctx.character_max_hp
        titan_hp = ctx.titan_hp
        target_is_self = ctx.target_is_self
        is_pvp = getattr(ctx, "is_pvp", False)
        opponent_hp = getattr(ctx, "opponent_hp", 0)
        opponent_max_hp = getattr(ctx, "opponent_max_hp", 100)
    
    # PVP specific behavior
    if is_pvp:
        if target_is_self:
            # Self-heal in PVP - more conservative values for balance
            heal_amount = int(character_max_hp * 0.12)
            opponent_hp_percent = opponent_hp / opponent_max_hp if opponent_max_hp > 0 else 0
            
            buffs = {
                "cold_edge_active": 1.0,
                "crit_chance": 1.15,  # 15% crit bonus in PVP
                "ACC": 1.10  # 10% accuracy boost
            }
            
            # If opponent is low on health, get additional bonus
            if opponent_hp_percent < 0.25:
                buffs["crit_chance"] = 1.3  # 30% crit bonus
                return create_effect(
                    message=f"Stimulant Injection (Self/PVP): {heal_amount} HP healed, +30% Crit Rate vs low HP enemy, +10% Accuracy",
                    healed=heal_amount,
                    buffs=buffs,
                    clear_debuffs=True
                )
            
            return create_effect(
                message=f"Stimulant Injection (Self/PVP): {heal_amount} HP healed, +15% Crit Rate, +10% Accuracy",
                healed=heal_amount,
                buffs=buffs,
                clear_debuffs=True
            )
        else:
            # Ally heal in PVP - same as non-PVP
            heal_amount = int(character_max_hp * 0.15)
            return create_effect(
                message=f"Stimulant Injection (Ally/PVP): {heal_amount} HP healed, all debuffs cleared",
                healed=heal_amount,
                clear_debuffs=True
            )
    
    # Original Titan battle behavior
    if target_is_self:
        heal_amount = int(character_max_hp * 0.1)
        buffs = {"cold_edge_active": 1.0}
        message = f"Stimulant Injection (Self): {heal_amount} HP healed, Cold Edge activated"
        if titan_hp < 50:
            buffs.update({
                "crit_chance": 2.0,
                "morale_damage": 0.1
            })
            message += " (vs Easy: 2x Crit, +10% morale damage)"
        elif titan_hp < 100:
            buffs.update({
                "crit_chance": 2.0,
                "morale_damage": 0.05
            })
            return create_effect(
                message=message + " (vs Normal: 2x Crit, +5% morale damage, -15 DEF to enemy)",
                healed=heal_amount,
                buffs=buffs,
                debuffs={"DEF": 15},
                clear_debuffs=True
            )
        else:
            buffs.update({
                "crit_chance": 2.5,
                "defense_ignore": 0.25,
                "morale_damage": 0.05,
                "int_drain": 10
            })
            return create_effect(
                message=message + " (vs Difficult: 2.5x Crit, ignores 25% DEF, morale damage, INT drain)",
                healed=heal_amount,
                buffs=buffs,
                debuffs={"INT": 10},
                clear_debuffs=True
            )
        return create_effect(
            message=message,
            healed=heal_amount,
            buffs=buffs,
            clear_debuffs=True
        )
    else:
        heal_amount = int(character_max_hp * 0.15)
        return create_effect(
            message=f"Stimulant Injection (Ally): {heal_amount} HP healed, all debuffs cleared",
            healed=heal_amount,
            clear_debuffs=True
        )

def bunker_descent_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Bunker Descent - Ultimate battlefield control ability for both PVP and Titan battles"""
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_max_hp = ctx.get("character_max_hp", 100)
        titan_hp = ctx.get("titan_hp", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        character_max_hp = ctx.character_max_hp
        titan_hp = ctx.titan_hp
        is_pvp = getattr(ctx, "is_pvp", False)
    
    # PVP specific behavior - more balanced for player vs player
    if is_pvp:
        heal_amount = int(character_max_hp * 0.20)  # 20% HP heal in PVP
        
        # Balanced buffs for PVP
        buffs = {
            "stealth": 1.0,
            "evasion": 0.3,  # 30% evasion in PVP
            "dodge_rate": 0.2,  # 20% dodge rate
            "shield": int(character_max_hp * 0.15)  # 15% max HP as shield
        }
        
        # Debuffs for opponent in PVP
        debuffs = {
            "ACC": 15,  # -15 accuracy
            "crit_rate": 0.5  # 50% reduced crit rate
        }
        
        return create_effect(
            message=f"Bunker Descent (PVP): Self healed for {heal_amount} HP, gained stealth, +30% Evasion, +20% Dodge Rate, shield for {buffs['shield']} damage. Opponent suffers -15 Accuracy and 50% reduced crit rate",
            healed=heal_amount,
            shield=buffs["shield"],
            buffs=buffs,
            debuffs=debuffs
        )
    
    # Original Titan battle behavior
    heal_amount = int(character_max_hp * 0.25)
    buffs = {
        "stealth": 1.0,
        "evasion": 0.5,
        "morale_resistance": 0.3,
        "enemy_accuracy": 0.5,
        "bunker_descent_duration": 3
    }
    message = f"Bunker Descent: All allies healed {heal_amount} HP, gain Stealth, +50% Evasion, +30% Morale Resistance, enemy accuracy halved for 3 turns"
    if titan_hp > 100:
        buffs.update({
            "surveillance_disruption": 0.5,
            "grab_immunity": 1.0
        })
        message += ". High-HP Titans suffer Surveillance Disruption (50% AoE failure), allies gain grab immunity"
    return create_effect(
        message=message,
        healed=heal_amount,
        buffs=buffs
    )

def panic_engine_effect(ctx: BattleContext) -> AbilityEffect:
    # Use dict-style access for context to support bot usage
    if isinstance(ctx, dict):
        fear_counter = ctx.get("fear_counter", 0)
        char_stats = ctx.get("character_stats", {})
        def_value = char_stats.get("DEF", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        fear_counter = ctx.fear_counter
        def_value = ctx.character_stats.DEF
        is_pvp = getattr(ctx, "is_pvp", False)
        
    # PVP specific behavior - more balanced
    if is_pvp:
        # In PVP, fear counter is simplified and effects are more balanced
        if fear_counter >= 3:  # Harder to reach maximum in PVP
            return create_effect(
                message="Panic Engine (PVP): Maximum fear reached! +30% DEF, +25% SPD, +20% ATK",
                buffs={
                    "DEF": 1.3,
                    "SPD": 1.25,
                    "ATK": 1.2
                }
            )
        elif fear_counter >= 2:
            return create_effect(
                message="Panic Engine (PVP): +20% DEF, +15% Crit Rate",
                buffs={
                    "DEF": 1.2,
                    "crit_rate": 1.15
                }
            )
        return create_effect(
            message="Panic Engine (PVP): Building fear... +10% DEF",
            buffs={"DEF": 1.1}
        )
    
    # Original Titan battle behavior
    def_boost = def_value * 0.2 * fear_counter
    if fear_counter >= 5:
        return create_effect(
            message="Panic Engine: Heart Overload, massive DEF/SPD boost",
            buffs={
                "DEF": 2.0 + def_boost,
                "SPD": 2.0,
                "ATK": 1.5
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

def cowards_fortitude_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_stats = ctx.get("character_stats", {})
        int_ = character_stats.get("INT", 0)
        turns_not_focused = ctx.get("turns_not_focused", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        int_ = ctx.character_stats.INT
        turns_not_focused = ctx.turns_not_focused
        is_pvp = getattr(ctx, "is_pvp", False)
    
    # PVP specific behavior
    if is_pvp:
        # In PVP, this is always active without waiting and provides balanced bonuses
        return create_effect(
            message="Coward's Fortitude (PVP): +15% Healing Efficiency, +15% Dodge Rate",
            buffs={
                "healing_efficiency": 1.15,
                "dodge_rate": 0.15
            }
        )
    
    # Original Titan battle behavior
    if turns_not_focused >= 2:
        heal_boost = int_ * 0.1
        return create_effect(
            message="Coward's Fortitude: +30% Healing, Cover Aura active",
            buffs={
                "healing_efficiency": 1.3 + heal_boost,
                "aoe_damage_reduction": 0.35
            }
        )
    return create_effect(
        message="Coward's Fortitude: Waiting for safety... +5% Dodge",
        buffs={"dodge_rate": 0.05}
    )

def field_patch_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_stats = ctx.get("character_stats", {})
        int_ = character_stats.get("INT", 0)
        def_ = character_stats.get("DEF", 0)
        target_hp_percent = ctx.get("target_hp_percent", 1.0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
        opponent_hp = ctx.get("opponent_hp", 0)
        opponent_max_hp = ctx.get("opponent_max_hp", 100)
    else:
        int_ = ctx.character_stats.INT
        def_ = ctx.character_stats.DEF
        target_hp_percent = ctx.target_hp_percent
        is_pvp = getattr(ctx, "is_pvp", False)
        opponent_hp = getattr(ctx, "opponent_hp", 0)
        opponent_max_hp = getattr(ctx, "opponent_max_hp", 100)
    
    # PVP specific behavior
    if is_pvp:
        # Base heal is scaled for PVP balance
        base_heal = int_ * 0.4  # Lower heal coefficient for PVP
        healed = int(base_heal * 2)  # Lower total heal for PVP
        
        # Check opponent's HP to determine if we should grant shield
        opponent_hp_percent = opponent_hp / opponent_max_hp if opponent_max_hp > 0 else 0
        
        if opponent_hp_percent > 0.5:
            # Against healthy opponents, provide larger shield
            shield_amount = int(def_ * 1.5)
            return create_effect(
                message=f"Field Patch (PVP): {healed} HP healed, Defensive Shield ({shield_amount}) granted",
                healed=healed,
                shield=shield_amount
            )
        else:
            # Against weakened opponents, provide modest heal and offensive bonus
            return create_effect(
                message=f"Field Patch (PVP): {healed} HP healed, +10% Crit Rate for 2 turns",
                healed=healed,
                buffs={
                    "crit_rate": 1.1,
                    "crit_duration": 2
                }
            )
    
    # Original Titan battle behavior
    base_heal = int_ * 0.5
    healed = int(base_heal * 3)
    if target_hp_percent < 0.3:
        shield_amount = 500 + def_ * 2
        return create_effect(
            message=f"Field Patch: {healed} HP healed, Survivor's Shield ({shield_amount}) granted",
            healed=healed,
            shield=shield_amount
        )
    return create_effect(
        message=f"Field Patch: {healed} HP healed over 3 turns",
        healed=healed
    )

def supply_dump_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        base_damage = ctx.get("base_damage", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        base_damage = ctx.base_damage
        is_pvp = getattr(ctx, "is_pvp", False)
    
    # PVP specific behavior
    if is_pvp:
        # In PVP, we control what drops instead of randomizing
        # This makes the ability more strategic and balanced
        
        # Calculate current health percentage to determine optimal drop
        if isinstance(ctx, dict):
            character_hp = ctx.get("character_hp", 100)
            character_max_hp = ctx.get("character_max_hp", 100)
            gas = ctx.get("gas", 100)
        else:
            character_hp = getattr(ctx, "character_hp", 100)
            character_max_hp = ctx.character_max_hp
            gas = getattr(ctx, "gas", 100)
        
        hp_percent = character_hp / character_max_hp if character_max_hp > 0 else 1.0
        
        # Choose item based on current state
        if hp_percent < 0.4:
            # Low health - drop healing
            selected_item = "Ration Pack"
            effect = create_effect(
                message=f"Supply Dump (PVP): Tactical drop - {selected_item}",
                healed=int(character_max_hp * 0.15),  # Heal 15% max HP
                buffs={"DEF": 1.1}  # Small DEF bonus
            )
        elif gas < 100:
            # Low gas - drop gas
            selected_item = "Gas Canister"
            effect = create_effect(
                message=f"Supply Dump (PVP): Tactical drop - {selected_item}",
                buffs={"gas_regen": 30}  # Restore 30 gas
            )
        else:
            # Otherwise drop offensive item
            selected_item = "Blades"
            effect = create_effect(
                message=f"Supply Dump (PVP): Tactical drop - {selected_item}",
                buffs={"ATK": 1.15, "crit_rate": 1.1}  # +15% ATK, +10% crit
            )
        
        return effect
    
    # Original Titan battle behavior
    items = ["Gas Canister", "Blades", "Ration Pack", "Repair Kit", "Fear Syringe"]
    selected_item = random.choice(items)
    effect = create_effect(
        message=f"Supply Dump: Dropped {selected_item}",
        items_dropped=[selected_item]
    )
    if selected_item == "Fear Syringe":
        effect.stun_duration = 2
        effect.damage = int(base_damage * 0.8)
    elif selected_item == "Gas Canister":
        effect.buffs = {"gas_regen": 50}
    elif selected_item == "Blades":
        effect.buffs = {"ATK": 0.2}
    return effect

def survival_override_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    is_pvp = False
    if isinstance(ctx, dict):
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        is_pvp = getattr(ctx, "is_pvp", False)
    
    # PVP specific behavior - more balanced
    if is_pvp:
        return create_effect(
            message="Survival Override (PVP): Last stand mode activated! +20% ATK, +20% SPD, +15% Crit Rate, 100% Damage Reflection for 2 turns",
            buffs={
                "ATK": 1.2,
                "SPD": 1.2,
                "crit_rate": 1.15,
                "damage_reflection": 1.0,
                "reflection_duration": 2
            }
        )
    
    # Original Titan battle behavior
    return create_effect(
        message="Survival Override: Movement costs negated, 2 actions/turn, enhanced passives",
        buffs={
            "movement_cost": 0.0,
            "actions_per_turn": 2.0,
            "passive_enhance": 1.0,
            "ATK": 1.25,
            "SPD": 1.25
        }
    )

def golden_hour_reflex_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        character_stats = ctx.get("character_stats", {})
        atk = character_stats.get("ATK", 0)
        titan_hp = ctx.get("titan_hp", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        atk = ctx.character_stats.ATK
        titan_hp = ctx.titan_hp
        is_pvp = getattr(ctx, "is_pvp", False)
    
    # PVP specific behavior
    if is_pvp:
        # In PVP, dodge is powerful but should be balanced
        buffs = {
            "dodge": 1.0,  # Will dodge the NEXT attack only
            "crit_rate": 1.1,  # +10% crit rate in PVP
            "reflex_counter": 1  # Only lasts for 1 turn in PVP
        }
        
        # Counter attack is reduced in PVP
        counter_attack = {
            "damage": int(atk * 1.2),  # 1.2x ATK damage in PVP
            "type": "slash",
            "message": "🔥 Counter strike!"
        }
        
        return create_effect(
            message="⚡ Golden Hour Reflex (PVP): Will dodge the next attack! +10% Crit for next turn",
            buffs=buffs,
            counter_attack=counter_attack
        )
    
    # Original Titan battle behavior
    buffs = {
        "dodge": 1.0,  # Will dodge the NEXT attack only
        "crit_rate": 1.05,  # +5% crit rate
        "reflex_counter": 2  # Counter for tracking the crit rate bonus duration
    }
    message = "⚡ Golden Hour Reflex activated! Will dodge the next attack! +5% Crit for next 2 moves"
    
    return create_effect(
        message=message,
        buffs=buffs,
        counter_attack={
            "damage": atk * 1.5,
            "type": "slash",
            "message": "🔥 Mina strikes back instantly!"
        }
    )

def rookie_courage_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        ally_died_in_range = ctx.get("ally_died_in_range", False)
        titan_hp = ctx.get("titan_hp", 0)
        allies_died_this_turn = ctx.get("allies_died_this_turn", 0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
        opponent_hp = ctx.get("opponent_hp", 0)
        opponent_max_hp = ctx.get("opponent_max_hp", 100)
    else:
        ally_died_in_range = ctx.ally_died_in_range
        titan_hp = ctx.titan_hp
        allies_died_this_turn = ctx.allies_died_this_turn
        is_pvp = getattr(ctx, "is_pvp", False)
        opponent_hp = getattr(ctx, "opponent_hp", 0)
        opponent_max_hp = getattr(ctx, "opponent_max_hp", 100)
    
    # PVP specific behavior
    if is_pvp:
        # In PVP, this is a passive that activates based on opponent's health
        opponent_hp_percent = opponent_hp / opponent_max_hp if opponent_max_hp > 0 else 1.0
        
        if opponent_hp_percent > 0.7:
            # Against strong opponents - defensive bonus
            return create_effect(
                message="Rookie Courage (PVP): Defensive stance against strong opponent, +15% Dodge, +10% Damage Reduction",
                buffs={
                    "dodge_rate": 0.15,
                    "damage_reduction": 0.1
                }
            )
        elif opponent_hp_percent < 0.3:
            # Against weakened opponents - offensive bonus
            return create_effect(
                message="Rookie Courage (PVP): Offensive momentum against weakened opponent, +20% ATK, +15% Crit Rate",
                buffs={
                    "ATK": 1.2,
                    "crit_rate": 1.15
                }
            )
        else:
            # Balanced bonus
            return create_effect(
                message="Rookie Courage (PVP): Balanced stance, +10% ATK, +10% DEF",
                buffs={
                    "ATK": 1.1,
                    "DEF": 1.1
                }
            )
    
    # Original Titan battle behavior
    if ally_died_in_range:
        buffs = {
            "actions_per_turn": 2.0,
            "titan_damage": 1.2
        }
        message = "Rookie Courage: Rapid Focus Mode - Double action, +20% titan damage"
        if titan_hp > 100:
            buffs.update({
                "crit_rate": 1.15,
                "INT": 10
            })
            message += ", +15% Crit Rate, +10 INT vs Strong Titan"
        if allies_died_this_turn > 1:
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

def nape_cutter_dash_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        base_damage = ctx.get("base_damage", 0)
        gas_full = ctx.get("gas_full", False)
        titan_hp = ctx.get("titan_hp", 0)
        titan_hp_percent = ctx.get("titan_hp_percent", 1.0)
        just_dodged = ctx.get("just_dodged", False)
        just_killed = ctx.get("just_killed", False)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
        opponent_hp = ctx.get("opponent_hp", 0)
        opponent_max_hp = ctx.get("opponent_max_hp", 100)
        character_stats = ctx.get("character_stats", {})
        spd = character_stats.get("SPD", 0)
    else:
        base_damage = ctx.base_damage
        gas_full = ctx.gas_full
        titan_hp = ctx.titan_hp
        titan_hp_percent = ctx.titan_hp_percent
        just_dodged = ctx.just_dodged
        just_killed = ctx.just_killed
        is_pvp = getattr(ctx, "is_pvp", False)
        opponent_hp = getattr(ctx, "opponent_hp", 0)
        opponent_max_hp = getattr(ctx, "opponent_max_hp", 100)
        spd = ctx.character_stats.SPD
    
    # PVP specific behavior
    if is_pvp:
        # Base damage calculation for PVP
        base_dmg = base_damage * 1.5  # Always 1.5x base damage in PVP
        
        opponent_hp_percent = opponent_hp / opponent_max_hp if opponent_max_hp > 0 else 1.0
        message = "Nape Cutter Dash (PVP)"
        debuffs = {"ACC": 10}  # -10 Accuracy debuff in PVP
        
        # Bonus damage against low HP opponents
        if opponent_hp_percent < 0.3:
            base_dmg *= 1.3
            message += ": Critical strike against weakened opponent!"
        else:
            message += ": Swift strike!"
            
        # Speed buff after attack
        buffs = {"SPD": 10 + int(spd * 0.1)}  # +10 SPD plus 10% of current SPD
        
        return create_effect(
            message=f"{message} Dealt {int(base_dmg)} damage, gained +{buffs['SPD']} Speed",
            damage=int(base_dmg),
            debuffs=debuffs,
            buffs=buffs
        )
    
    # Original Titan battle behavior
    base_dmg = base_damage * (2.0 if gas_full else 1.0)
    message = "Nape Cutter Dash"
    debuffs = {}
    if titan_hp < 50 and titan_hp_percent < 0.25:
        base_dmg *= 2.0
        message += " (Auto-Critical vs Low HP Easy Titan)"
    elif titan_hp <= 100:
        debuffs["SPD"] = 10
        message += " (-10 Agility)"
    elif titan_hp > 100 and (just_dodged or just_killed):
        base_dmg *= 1.5
        message += " (+50% damage after dodge/kill)"
    return create_effect(
        message=f"{message}: Dealt {int(base_dmg)} damage",
        damage=int(base_dmg),
        debuffs=debuffs
    )

def emergency_pulse_beacon_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        titan_hp = ctx.get("titan_hp", 0)
        rapid_focus_active = ctx.get("rapid_focus_active", False)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
    else:
        titan_hp = ctx.titan_hp
        rapid_focus_active = ctx.rapid_focus_active
        is_pvp = getattr(ctx, "is_pvp", False)
    
    # PVP specific behavior
    if is_pvp:
        # In PVP, provide balanced buffs and debuffs
        buffs = {
            "DEF": 1.15,  # +15% DEF
            "ACC": 1.1,   # +10% ACC
            "dodge_rate": 0.1  # +10% dodge
        }
        
        # Debuffs for opponent
        debuffs = {
            "ACC": 10,  # -10 ACC
            "crit_rate": 0.9  # -10% crit rate
        }
        
        return create_effect(
            message="Emergency Pulse Beacon (PVP): +15% DEF, +10% ACC, +10% Dodge for self. Opponent suffers -10 Accuracy and -10% Crit Rate",
            buffs=buffs,
            debuffs=debuffs
        )
    
    # Original Titan battle behavior
    buffs = {
        "DEF": 1.2,
        "ACC": 1.15
    }
    debuffs = {}
    message = "Emergency Pulse Beacon: +20% DEF, +15% ACC to allies"
    if random.random() < 0.25:
        if titan_hp < 75:
            debuffs["unstable"] = 1.0
            message += ", Titan became Unstable (no charge/grapple)"
        message += ", target switched"
    if rapid_focus_active:
        buffs["clear_fear"] = 1.0
        message += ", cleared Fear debuffs"
    return create_effect(
        message=message,
        buffs=buffs,
        debuffs=debuffs,
        target_switched=bool(debuffs)
    )

def flicker_instinct_effect(ctx: BattleContext) -> AbilityEffect:
    # Support both BattleContext object and dict
    if isinstance(ctx, dict):
        base_damage = ctx.get("base_damage", 0)
        titan_hp = ctx.get("titan_hp", 0)
        titan_hp_percent = ctx.get("titan_hp_percent", 1.0)
        is_pvp = ctx.get("is_pvp", False) or ctx.get("pvp", False)
        opponent_hp = ctx.get("opponent_hp", 0)
        opponent_max_hp = ctx.get("opponent_max_hp", 100)
    else:
        base_damage = ctx.base_damage
        titan_hp = ctx.titan_hp
        titan_hp_percent = ctx.titan_hp_percent
        is_pvp = getattr(ctx, "is_pvp", False)
        opponent_hp = getattr(ctx, "opponent_hp", 0)
        opponent_max_hp = getattr(ctx, "opponent_max_hp", 100)
    
    # PVP specific behavior
    if is_pvp:
        # For PVP, set a fixed number of hits to make it more predictable
        hits = 3
        hit_damage = base_damage * 0.4  # 40% of base damage per hit for balance
        total_dmg = hit_damage * hits
        
        # Calculate opponent HP percent
        opponent_hp_percent = opponent_hp / opponent_max_hp if opponent_max_hp > 0 else 1.0
        
        buffs = {
            "evasion": 0.4,  # 40% evasion in PVP
            "dodge_rate": 0.2,  # 20% dodge rate
            "SPD": 1.2  # +20% speed
        }
        
        # Debuffs for opponent
        debuffs = {
            "bleed": 2,  # 2 turn bleed
            "SPD": 10  # -10 speed
        }
        
        # If opponent is low on health, increase damage
        if opponent_hp_percent < 0.25:
            total_dmg *= 1.4
            message = f"Flicker Instinct (PVP): Execution sequence! {hits} strikes dealt {int(total_dmg)} damage!"
        else:
            message = f"Flicker Instinct (PVP): {hits} rapid strikes dealt {int(total_dmg)} damage!"
        
        return create_effect(
            message=message,
            damage=int(total_dmg),
            bleed_applied=True,
            buffs=buffs,
            debuffs=debuffs
        )
    
    # Original Titan battle behavior
    total_dmg = 0
    bleed_count = 0
    buffs = {"evasion": 0.8}
    debuffs = {}
    message = []
    for i in range(3):
        attack_dmg = base_damage * (0.9 + random.random() * 0.2)
        total_dmg += attack_dmg
        if random.random() < 0.4:
            bleed_count += 1
    if titan_hp < 50:
        if titan_hp_percent < 0.35:
            total_dmg *= 1.5
            if titan_hp_percent < 0.15:
                total_dmg *= 2.0
                message.append("Execution strike!")
    elif titan_hp <= 100:
        if bleed_count > 0:
            debuffs.update({
                "DEF": 0.9,
                "deep_bleed": 2
            })
            message.append("Deep Bleed applied")
    else:
        buffs.update({
            "reset_nape_cutter": 1.0,
            "SPD": 1.3
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
    active_abilities: List[Dict] = [],
    passive_abilities: List[Dict] = [],
    ultimate_abilities: List[Dict] = [],
    **kwargs
) -> CharacterData:
    """Factory function for standardized character creation"""
    return CharacterData(
        name=name,
        quote=quote,
        role=role,
        archetype=archetype,
        core_trait=core_trait,
        base_stats=CharacterStats(**base_stats),
        active_abilities=[Ability(**data) for data in active_abilities],
        passive_abilities=[Ability(**data) for data in passive_abilities],
        ultimate_abilities=[Ability(**data) for data in ultimate_abilities],
        **kwargs
    )

CHARACTERS: Dict[str, CharacterData] = {
    "Hitch Dreyse": create_character(
        name="Hitch Dreyse",
        quote="Guarded the walls. Mocked the world. Then saw it burn.",
        role="Wall Garrison Officer",
        archetype="Tactical Support / Debuff Specialist",
        core_trait="Apathy → Awakening (Echo Trait Bias: Loyalty → Desperation)",
        base_stats={"HP": 650, "ATK": 12, "DEF": 11, "ACC": 10, "INT": 12, "SPD": 13},
        passive_abilities=[
            {
                "name": "Civilian Shell",
                "description": "Starts with 50% aggro avoidance. First hit reduces damage by 70% if attacker is a Titan >75 HP. Triggers Wake-Up Protocol: permanent 1.25x Speed, +15 Awareness, and Titan-specific reactions based on enemy HP.",
                "type": "passive",
                "gas_cost": 0,
                "is_unlocked": True,
                "effect_function": civilian_shell_effect
            },
            {
                "name": "Mocking Delay",
                "description": "Enemies targeted by Hitch have their next action delayed by 1 turn. If targeting an Intelligent Titan or Boss (HP > 90), delay increases to 3 turns. Against Difficult Titans (HP > 100), applies morale stagger: -15 SPD for 3 turns.",
                "type": "passive",
                "gas_cost": 70,
                "level_required": 25,
                "effect_function": mocking_delay_effect
            }
        ],
        active_abilities=[
            {
                "name": "Arc Net Trap",
                "description": "Deploys an electric tripwire system for 4 turns. Stun duration and agility penalties scale with Titan tier: Easy (2 turn stun, -30%), Normal (1 turn, -40%), Difficult (1 turn, -50% + Entangled Core). Allies gain +20% Dodge Rate and +5% Crit Evasion.",
                "type": "active",
                "gas_cost": 100,
                "cooldown": 4,
                "level_required": 50,
                "base_damage": 45,
                "effect_function": arc_net_trap_effect
            },
            {
                "name": "Stimulant Injection",
                "description": "Ally: 15% HP heal + full debuff cleanse. Self: 10% HP heal + Cold Edge with Titan-tier scaling. Easy: 2x Crit + 10% morale damage. Normal: 2x Crit + 5% morale + -15 enemy DEF. Difficult: 2.5x Crit + ignores 25% DEF + morale + INT drain.",
                "type": "active",
                "gas_cost": 120,
                "cooldown": 3,
                "level_required": 75,
                "base_damage": 0,
                "effect_function": stimulant_injection_effect
            }
        ],
        ultimate_abilities=[
            {
                "name": "Bunker Descent",
                "description": "Ultimate battlefield control. Heals all allies 25% HP. All allies gain: Stealth, +50% Evasion, +30% Morale Resistance, enemy accuracy halved for 3 turns. If enemy Titans have >100 HP: Surveillance Disruption (50% AoE failure) + allies gain grab immunity.",
                "type": "ultimate",
                "gas_cost": 400,
                "cooldown": 1,
                "level_required": 125,
                "base_damage": 0,
                "effect_function": bunker_descent_effect
            }
        ]
    ),
    "Daz": create_character(
        name="Daz",
        quote="Fear was his truth. But fear... is still a form of courage.",
        role="NPC-Ally / Emergency Support",
        archetype="RNG-dependent Last-Resort Utility",
        core_trait="Despair → Spark",
        base_stats={"HP": 650, "ATK": 13, "DEF": 13, "ACC": 12, "INT": 12, "SPD": 11},
        max_potential={"HP": 1250, "ATK": 125, "DEF": 125, "ACC": 115, "INT": 115, "SPD": 115},
        passive_abilities=[
            {
                "name": "Panic Engine",
                "description": "Fear Counter: +1 when ally dies, +1 when below 50% HP, +1 when hit by Titan >75 HP. At 3 stacks: gains bonus action with 1.5x movement vs Normal Titans, ignores overwatch vs Difficult Titans. At 5 stacks: Heart Overload (+60% DEF, +40% SPD). VS Titans >100 HP: Next ability becomes AoE at 80% power (once per combat).",
                "type": "passive",
                "gas_cost": 20,
                "is_unlocked": True,
                "effect_function": panic_engine_effect
            },
            {
                "name": "Coward's Fortitude",
                "description": "If not attacked for 3 turns, gains +20% Healing Efficiency and creates 'Cover Aura' reducing AoE damage to nearby allies by 25%.",
                "type": "passive",
                "gas_cost": 120,
                "level_required": 25,
                "effect_function": cowards_fortitude_effect
            }
        ],
        active_abilities=[
            {
                "name": "Field Patch",
                "description": "Heals 5% HP/second for 3 turns. If target HP < 30%, applies Survivor's Shield: 500 damage absorption (1000 vs Easy Titans, 750 vs Normal). VS Difficult Titans: enemy breaking shield loses 10 ACC.",
                "type": "active",
                "gas_cost": 150,
                "cooldown": 3,
                "level_required": 50,
                "base_damage": 0,
                "effect_function": field_patch_effect
            },
            {
                "name": "Supply Dump",
                "description": "Tosses random support item: Gas (resets movement), Blades (+15% Crit), Ration (20% HP), or Repair Kit (removes status effects). With Fear Counter ≥3: effect doubles/becomes AoE. 20% chance for Fear Syringe (stuns Titan, causes 30% AoE miss vs Normal/Difficult).",
                "type": "active",
                "gas_cost": 75,
                "cooldown": 2,
                "level_required": 100,
                "base_damage": 20,
                "effect_function": supply_dump_effect
            }
        ],
        ultimate_abilities=[
            {
                "name": "Survival Override",
                "description": "Auto-triggers if last alive. 3 turns: Zero movement cost, +2 actions/turn, enhanced passives. Field Patch becomes AoE, Supply Dump drops 2 items, Heart Overload recastable. VS Titans >100 HP: abilities apply -20% Titan Morale Resist. Final action deals True Damage = 10% highest Titan HP. Collapses after 3 turns (bypasses revive).",
                "type": "ultimate",
                "gas_cost": 400,
                "cooldown": 1,
                "level_required": 125,
                "base_damage": 80,
                "effect_function": survival_override_effect
            }
        ]
    ),
    "Mina Carolina": create_character(
        name="Mina Carolina",
        quote="She smiled before being eaten. The kind that shines even in silence.",
        role="Early Scout Cadet",
        archetype="Burst Damage + Agility Hybrid",
        core_trait="Innocence → Resilience",
        base_stats={"HP": 650, "ATK": 14, "DEF": 12, "ACC": 13, "INT": 11, "SPD": 13},
        max_potential={"HP": 1250, "ATK": 130, "DEF": 120, "ACC": 120, "INT": 110, "SPD": 125},
        passive_abilities=[
            {
                "name": "Rookie Courage",
                "description": "+5% Movement Speed to allies within 15m. If ally dies in range: Double Action Round, +20% Titan Damage (3 moves). VS Titans >100 HP: +15% Crit Rate, +10 INT. Multiple ally deaths: ODM cooldowns reset.",
                "type": "passive",
                "gas_cost": 100,
                "level_required": 25,
                "effect_function": rookie_courage_effect
            }
        ],
        active_abilities=[
            {
                "name": "Golden Hour Reflex",
                "description": "When activated, allows dodging only the next attack (one time) and grants +5% Crit Rate for 2 turns.",
                "type": "active",
                "gas_cost": 20,
                "cooldown": 5,
                "is_unlocked": True,
                "effect_function": golden_hour_reflex_effect
            },
            {
                "name": "Nape Cutter Dash",
                "description": "High-speed ODM slice. 2x damage at Full Gas. Easy Titans: Auto-Crit if HP <25%. Normal: -10 Agility for 1 turn. Difficult: +50% damage after dodge/kill.",
                "type": "active",
                "gas_cost": 150,
                "cooldown": 2,
                "level_required": 50,
                "base_damage": 60,
                "effect_function": nape_cutter_dash_effect
            },
            {
                "name": "Emergency Pulse Beacon",
                "description": "Allies: +20% DEF, +15% ACC (3 turns). Titans (30m): 25% target switch. Titans <75 HP become Unstable on switch. Under Rapid Focus: clears Fear debuffs on allies.",
                "type": "active",
                "gas_cost": 80,
                "cooldown": 3,
                "level_required": 100,
                "base_damage": 0,
                "effect_function": emergency_pulse_beacon_effect
            }
        ],
        ultimate_abilities=[
            {
                "name": "Flicker Instinct",
                "description": "3 strikes on multiple/single target. 40% Bleed Chance. 80% Evasion after final hit. Easy: Execute <15% HP. Normal: Deep Bleed (-10% DEF, HP loss). Difficult: Refreshes Nape Cutter + Speed Surge.",
                "type": "ultimate",
                "gas_cost": 450,
                "cooldown": 1,
                "level_required": 125,
                "base_damage": 120,
                "effect_function": flicker_instinct_effect
            }
        ]
    )
}

# Character image URLs
CHARACTER_IMAGES = {
    "Hitch Dreyse": "https://i.ibb.co/BM7pq4z/image.jpg",
    "Mina Carolina": "https://i.ibb.co/wZN4Zwvd/image.jpg",
    "Daz": "https://i.ibb.co/B5sPkmZJ/image.jpg"
}

# ======================
# MANAGEMENT FUNCTIONS
# ======================

def get_character_data(character_name: str) -> Optional[CharacterData]:
    """Safe character data retrieval"""
    if not character_name or not str(character_name).strip():
        return None
    character = CHARACTERS.get(character_name)
    if not character:
        logger.warning(f"Character data not found for: {character_name}")
    return character

def add_new_character(character_data: Dict) -> None:
    """Safe method to add new characters"""
    try:
        character = CharacterData(**character_data)
        if character.name in CHARACTERS:
            raise ValueError(f"Character {character.name} already exists")
        CHARACTERS[character.name] = character
        logger.info(f"Added new character: {character.name}")
    except Exception as e:
        logger.error(f"Failed to add character: {e}")
        raise

def get_ability(character_name: str, ability_name: str) -> Optional[Ability]:
    """Get specific ability from any character"""
    char = get_character_data(character_name)
    if not char:
        logger.warning(f"Character not found: {character_name}")
        return None
    for abilities in [char.active_abilities, char.passive_abilities, char.ultimate_abilities]:
        for ability in abilities:
            if ability.name == ability_name:
                return ability
    logger.warning(f"Ability {ability_name} not found for character {character_name}")
    return None

def list_all_abilities() -> Dict[str, List[str]]:
    """Get all abilities organized by character"""
    return {
        char_name: [
            f"{ability.name} ({ability.type})"
            for abilities in [char_data.active_abilities, char_data.passive_abilities, char_data.ultimate_abilities]
            for ability in abilities
        ]
        for char_name, char_data in CHARACTERS.items()
    }