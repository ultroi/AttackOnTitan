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
    """Enhanced Civilian Shell with Wake-Up Protocol and Titan-specific reactions"""
    spd_bonus = ctx.character_stats.SPD * 0.02
    
    if not ctx.first_damage_taken:
        damage_reduction = 0.7 if ctx.titan_hp > 75 else 0.5
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
            "SPD": 1.25 + (ctx.character_stats.SPD * 0.01),
            "ACC": 1.15 + (ctx.character_stats.ACC * 0.005),
            "wake_up_active": 1.0
        }
        message = f"Wake-Up Protocol: +{int((1.25 + ctx.character_stats.SPD * 0.01 - 1)*100)}% Speed, +{int((1.15 + ctx.character_stats.ACC * 0.005 - 1)*100)}% Awareness"
        if ctx.titan_hp < 50:
            buffs["crit_rate"] = 1.25
            message += ", +25% Crit Rate vs Easy Titans"
        elif ctx.titan_hp < 100:
            buffs["auto_dodge_counter"] = 1.0
            message += ", Auto-Dodge first counterattack vs Normal Titans"
        elif ctx.titan_hp < 125:
            message += ", Applies Dazed Focus to enemy"
            return create_effect(
                message=message,
                buffs=buffs,
                debuffs={"ACC": 10, "SPD": 5}
            )
        return create_effect(message=message, buffs=buffs)

def mocking_delay_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Mocking Delay with morale stagger for Difficult Titans"""
    int_bonus = int(ctx.character_stats.INT * 0.1)
    is_intelligent = ctx.is_intelligent_titan or ctx.titan_hp > 90
    base_delay = 1
    if is_intelligent:
        delay = 3 + int_bonus
        message = f"Mocking Delay: Enemy action delayed by {delay} turns (Intelligent/Boss Titan, +{int_bonus} from INT)"
    else:
        delay = base_delay + int_bonus
        message = f"Mocking Delay: Enemy action delayed by {delay} turn (+{int_bonus} from INT)"
    debuffs = {"delay": delay}
    if ctx.titan_hp > 100:
        spd_reduction = 15 + (ctx.character_stats.ACC * 0.5)
        debuffs["SPD"] = int(spd_reduction)
        debuffs["morale_stagger"] = 3
        message += f", applies morale stagger (-{int(spd_reduction)} SPD for 3 turns, +{ctx.character_stats.ACC*0.5:.1f} from ACC)"
    return create_effect(message=message, debuffs=debuffs)

def arc_net_trap_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Arc Net Trap with variable effects based on Titan difficulty"""
    if ctx.titan_hp < 50:
        stun_duration = 2
        agility_penalty = 0.3 + (ctx.character_stats.INT * 0.01)
        tier = "Easy"
    elif ctx.titan_hp < 100:
        stun_duration = 1 + int(ctx.character_stats.INT * 0.05)
        agility_penalty = 0.4 + (ctx.character_stats.INT * 0.01)
        tier = "Normal"
    else:
        stun_duration = 1 + int(ctx.character_stats.INT * 0.03)
        agility_penalty = 0.5 + (ctx.character_stats.INT * 0.01)
        tier = "Difficult"
    base_damage = ctx.base_damage * 1.2
    dodge_bonus = 0.2 + (ctx.character_stats.SPD * 0.005)
    buffs = {
        "dodge_rate": dodge_bonus,
        "crit_evasion": 0.05 + (ctx.character_stats.SPD * 0.002)
    }
    debuffs = {"SPD": agility_penalty}
    if tier == "Difficult":
        miss_chance = 0.2 + (ctx.character_stats.INT * 0.005)
        debuffs["entangled_core"] = miss_chance
        message = f"Arc Net Trap ({tier}): {stun_duration} turn stun, -{int(agility_penalty*100):.1f}% Agility, {int(miss_chance*100):.1f}% Entangled Core (INT: +{ctx.character_stats.INT*0.005*100:.1f}%)"
    else:
        message = f"Arc Net Trap ({tier}): {stun_duration} turn stun, -{int(agility_penalty*100):.1f}% Agility (INT: +{ctx.character_stats.INT*0.01*100:.1f}%)"
    return create_effect(
        message=message,
        damage=int(base_damage),
        stun_duration=stun_duration,
        debuffs=debuffs,
        buffs=buffs
    )

def stimulant_injection_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Stimulant Injection with Cold Edge and Titan-tier scaling"""
    if ctx.target_is_self:
        heal_amount = int(ctx.character_max_hp * 0.1)
        buffs = {"cold_edge_active": 1.0}
        message = f"Stimulant Injection (Self): {heal_amount} HP healed, Cold Edge activated"
        if ctx.titan_hp < 50:
            buffs.update({
                "crit_chance": 2.0,
                "morale_damage": 0.1
            })
            message += " (vs Easy: 2x Crit, +10% morale damage)"
        elif ctx.titan_hp < 100:
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
        heal_amount = int(ctx.character_max_hp * 0.15)
        return create_effect(
            message=f"Stimulant Injection (Ally): {heal_amount} HP healed, all debuffs cleared",
            healed=heal_amount,
            clear_debuffs=True
        )

def bunker_descent_effect(ctx: BattleContext) -> AbilityEffect:
    """Enhanced Bunker Descent - Ultimate battlefield control ability"""
    heal_amount = int(ctx.character_max_hp * 0.25)
    buffs = {
        "stealth": 1.0,
        "evasion": 0.5,
        "morale_resistance": 0.3,
        "enemy_accuracy": 0.5,
        "bunker_descent_duration": 3
    }
    message = f"Bunker Descent: All allies healed {heal_amount} HP, gain Stealth, +50% Evasion, +30% Morale Resistance, enemy accuracy halved for 3 turns"
    if ctx.titan_hp > 100:
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
    else:
        fear_counter = ctx.fear_counter
        def_value = ctx.character_stats.DEF
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
    if ctx.turns_not_focused >= 2:
        heal_boost = ctx.character_stats.INT * 0.1
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
    base_heal = ctx.character_stats.INT * 0.5
    healed = int(base_heal * 3)
    if ctx.target_hp_percent < 0.3:
        shield_amount = 500 + ctx.character_stats.DEF * 2
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
    items = ["Gas Canister", "Blades", "Ration Pack", "Repair Kit", "Fear Syringe"]
    selected_item = random.choice(items)
    effect = create_effect(
        message=f"Supply Dump: Dropped {selected_item}",
        items_dropped=[selected_item]
    )
    if selected_item == "Fear Syringe":
        effect.stun_duration = 2
        effect.damage = ctx.base_damage * 0.8
    elif selected_item == "Gas Canister":
        effect.buffs = {"gas_regen": 50}
    elif selected_item == "Blades":
        effect.buffs = {"ATK": 0.2}
    return effect

def survival_override_effect(ctx: BattleContext) -> AbilityEffect:
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
    if ctx.attack_count <= 1:
        buffs = {
            "dodge": 1.0,
            "crit_rate": 1.1,
            "reflex_counter": 2
        }
        message = "⚡ Golden Reflex! Dodged attack! +10% Crit for next 2 moves"
        if 75 < ctx.titan_hp <= 100:
            buffs["bonus_dash"] = 1.0
            message += ", gained Bonus Dash to weak spot"
        elif ctx.titan_hp > 100:
            buffs["defense_ignore"] = 0.15
            message += ", Strike Window activated (15% Defense ignore)"
        return create_effect(
            message=message,
            buffs=buffs,
            counter_attack={
                "damage": ctx.character_stats.ATK * 1.5,
                "type": "slash",
                "message": "🔥 Mina strikes back instantly!"
            }
        )
    return create_effect(
        message="Golden Reflex: Ready... +5% Evasion",
        buffs={"evasion": 0.05}
    )

def rookie_courage_effect(ctx: BattleContext) -> AbilityEffect:
    if ctx.ally_died_in_range:
        buffs = {
            "actions_per_turn": 2.0,
            "titan_damage": 1.2
        }
        message = "Rookie Courage: Rapid Focus Mode - Double action, +20% titan damage"
        if ctx.titan_hp > 100:
            buffs.update({
                "crit_rate": 1.15,
                "INT": 10
            })
            message += ", +15% Crit Rate, +10 INT vs Strong Titan"
        if ctx.allies_died_this_turn > 1:
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
    base_dmg = ctx.base_damage * (2.0 if ctx.gas_full else 1.0)
    message = "Nape Cutter Dash"
    debuffs = {}
    if ctx.titan_hp < 50 and ctx.titan_hp_percent < 0.25:
        base_dmg *= 2.0
        message += " (Auto-Critical vs Low HP Easy Titan)"
    elif ctx.titan_hp <= 100:
        debuffs["SPD"] = 10
        message += " (-10 Agility)"
    elif ctx.titan_hp > 100 and (ctx.just_dodged or ctx.just_killed):
        base_dmg *= 1.5
        message += " (+50% damage after dodge/kill)"
    return create_effect(
        message=f"{message}: Dealt {int(base_dmg)} damage",
        damage=int(base_dmg),
        debuffs=debuffs
    )

def emergency_pulse_beacon_effect(ctx: BattleContext) -> AbilityEffect:
    buffs = {
        "DEF": 1.2,
        "ACC": 1.15
    }
    debuffs = {}
    message = "Emergency Pulse Beacon: +20% DEF, +15% ACC to allies"
    if random.random() < 0.25:
        if ctx.titan_hp < 75:
            debuffs["unstable"] = 1.0
            message += ", Titan became Unstable (no charge/grapple)"
        message += ", target switched"
    if ctx.rapid_focus_active:
        buffs["clear_fear"] = 1.0
        message += ", cleared Fear debuffs"
    return create_effect(
        message=message,
        buffs=buffs,
        debuffs=debuffs,
        target_switched=bool(debuffs)
    )

def flicker_instinct_effect(ctx: BattleContext) -> AbilityEffect:
    base_dmg = ctx.base_damage
    total_dmg = 0
    bleed_count = 0
    buffs = {"evasion": 0.8}
    debuffs = {}
    message = []
    for i in range(3):
        attack_dmg = base_dmg * (0.9 + random.random() * 0.2)
        total_dmg += attack_dmg
        if random.random() < 0.4:
            bleed_count += 1
    if ctx.titan_hp < 50:
        if ctx.titan_hp_percent < 0.35:
            total_dmg *= 1.5
            if ctx.titan_hp_percent < 0.15:
                total_dmg *= 2.0
                message.append("Execution strike!")
    elif ctx.titan_hp <= 100:
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
                "name": "Golden Hour Reflex",
                "description": "First 2 attacks from Titans < 10m are auto-dodged. Each dodge +10% Crit Rate for 2 moves. VS Normal: Dodge adds Bonus Dash. VS Difficult: Dodge triggers Strike Window (next ODM attack ignores 15% Titan Defense).",
                "type": "passive",
                "gas_cost": 20,
                "is_unlocked": True,
                "effect_function": golden_hour_reflex_effect
            },
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

# ======================
# MANAGEMENT FUNCTIONS
# ======================

def get_character_data(character_name: str) -> Optional[CharacterData]:
    """Safe character data retrieval"""
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