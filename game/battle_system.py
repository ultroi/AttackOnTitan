from typing import Dict, List, Optional, Any, Tuple
from database.models import Character, Player, Titan, generate_titan_xp
from database.characters import AbilityEffect, get_character_data
from database.schemas import Ability, CharacterStats
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
from game.random_drop import get_random_drop
import asyncio
import random
from utils.monitor import track_battle_end
from database.missions import process_titan_reward_mission_progress, process_explore_mission_progress, check_mission_item_drops, add_mission_item, process_titan_defeat_mission_progress
from game.stats_command import track_explore_stats
import logging
from datetime import datetime, timezone
import time

logger = logging.getLogger(__name__)

# Global dictionary to track active battles
active_battles: Dict[str, 'BattleSystem'] = {}
active_battles_lock = asyncio.Lock()

# =========================
# BATTLE SYSTEM CLASS
# =========================

class BattleSystem:
    def get_equipped_weapon(self, shop_items):
        if self.character.equipped_weapon:
            if self.character.equipped_weapon in shop_items:
                item = shop_items[self.character.equipped_weapon]
                if hasattr(item, 'type') and item.type in ["weapon", "gear", "military"] and hasattr(item, 'attributes'):
                    return item
            else:
                pass
        return None
    """
    Manages a battle between a character and a titan, handling gas, HP, abilities, buffs, debuffs, and turn logic.
    """
    def __init__(self, character: Character, titan: Titan, player: Optional[Player] = None, team: Optional[List] = None, current_team_index: int = 0):
        self.character = character
        self.titan = titan
        self.player = player
        self.team = team or []
        self.current_team_index = current_team_index
        self.character_hp: int = character.current_hp
        self.titan_hp: int = titan.max_hp
        self.gas: int = character.gas
        self.character_gas: int = character.max_gas  
        self.max_gas: int = character.max_gas  
        self.character.max_gas = self.max_gas  
        self.character.stats = character.stats or CharacterStats()
        self.ability_cooldowns: Dict[str, int] = {
            ability.name: 0 for ability in (
                (character.active_abilities or []) +
                (character.passive_abilities or []) +
                (character.ultimate_abilities or [])
            )
        }
        # Pre-build ability lookup dictionary for O(1) access
        self.ability_lookup: Dict[str, Any] = {}
        self.ability_prefixes: Dict[str, str] = {}
        character_data = get_character_data(character.character_type)
        if character_data:
            prefixes = {
                "active_abilities": "⚔️",
                "passive_abilities": " ",
                "ultimate_abilities": "✨"
            }
            for ability_type, prefix in prefixes.items():
                abilities = getattr(character_data, ability_type, [])
                for ability in abilities:
                    if ability and ability.name:
                        self.ability_lookup[ability.name] = ability
                        self.ability_prefixes[ability.name] = prefix
        self.buffs: Dict[str, Any] = {}
        self.debuffs: Dict[str, int] = {}  
        self.titan_debuffs: Dict[str, int] = {}
        self.turn: int = 0
        self.keyboard_cache: Any = None
        self.keyboard_cache_invalid = True
        self.trigger_states: Dict[str, Any] = {
            "first_damage_taken": False,
            "dodge_count": 0,
            "fear_counter": 0,
            "focused_turns": 0,
            "ally_died": False,
            "demagogue_stacks": 0,
            "ally_death_count": 0,
            "flochs_last_standing": False,
            "pixis_buffs_distributed": False,
            "pixis_buff_targets": [],
            "pixis_buff_values": {}
        }
        self.emergency_heal_used: bool = False
        self.character.unlock_abilities()
        self.apply_passives("battle_start")
        self.timeout_task: Optional[asyncio.Task] = None
        self._is_disposed: bool = False
        self.battle_ended: bool = False
        self.initial_gas: int = character.gas  
        self.last_character_refresh: float = time.time()  
        self.passive_cache: Dict[str, List[Dict]] = {}
        self.last_passive_refresh: float = 0  
        self.participating_characters = set([self.character.name])  
        self.is_boss_battle: bool = getattr(titan, 'is_boss', False)
        self.character_cache: Dict[str, Any] = {}

    async def switch_character_on_death(self, db, user_id: str) -> bool:
        """Switch to the next available team character on defeat. Returns True if switched successfully."""
        if not self.team or len(self.team) <= 1:
            return False
        
        # Sort team by position to respect team order
        sorted_team = sorted(self.team, key=lambda m: getattr(m, 'position', 1) if hasattr(m, 'position') else (m.get('position', 1) if isinstance(m, dict) else 1))
        
        # Find next character with HP > 0
        start_index = self.current_team_index
        for i in range(1, len(sorted_team)):
            next_index = (start_index + i) % len(sorted_team)
            next_member = sorted_team[next_index]
            next_name = next_member.character_name if hasattr(next_member, 'character_name') else next_member.get('character_name', next_member)
            
            # Skip current character
            if next_name == self.character.name:
                continue
            
            # Check if character has HP
            next_character = self.character_cache.get(next_name)
            if not next_character:
                # Try to get from DB
                try:
                    next_character = await db.get_character(user_id, next_name)
                    if next_character:
                        self.character_cache[next_name] = next_character
                except:
                    continue
            
            if next_character and next_character.current_hp > 0:
                # Switch to this character
                old_name = self.character.name
                
                # Add old character to participants
                self.participating_characters.add(old_name)
                
                # Switch character
                self.character = next_character
                self.current_team_index = next_index
                self.character_hp = next_character.current_hp
                self.gas = next_character.gas
                self.character_gas = next_character.max_gas
                self.max_gas = next_character.max_gas
                
                # Reset cooldowns for new character
                character_data = get_character_data(next_character.character_type)
                if character_data:
                    self.ability_cooldowns = {
                        ability.name: 0 for ability in (
                            (character_data.active_abilities or []) +
                            (character_data.passive_abilities or []) +
                            (character_data.ultimate_abilities or [])
                        )
                    }
                    # Rebuild ability lookup
                    prefixes = {
                        "active_abilities": "⚔️",
                        "passive_abilities": " ",
                        "ultimate_abilities": "✨"
                    }
                    self.ability_lookup = {}
                    self.ability_prefixes = {}
                    for ability_type, prefix in prefixes.items():
                        abilities = getattr(character_data, ability_type, [])
                        for ability in abilities:
                            if ability and ability.name:
                                self.ability_lookup[ability.name] = ability
                                self.ability_prefixes[ability.name] = prefix
                
                # Clear buffs/debuffs and reapply passives
                self.buffs.clear()
                self.debuffs.clear()
                self.titan_debuffs.clear()
                self.apply_passives("battle_start")
                
                # Reset emergency heal
                self.emergency_heal_used = False
                
                return True
        
        return False

    # ---------- Resource Management ----------
    def dispose(self) -> None:
        """Clean up battle resources and reset state."""
        if self._is_disposed:
            return
        self._is_disposed = True
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()
        self.timeout_task = None
        self.clear_internal_caches()
        self.buffs.clear()
        self.debuffs.clear()
        self.titan_debuffs.clear()
        self.ability_cooldowns.clear()
        self.trigger_states.clear()

    def clear_internal_caches(self) -> None:
        """Clear all internal battle caches to free memory."""
        self.keyboard_cache = None
        self.keyboard_cache_invalid = True
        self.character_cache.clear()

    # ---------- Context & Effects ----------
    def build_context(self, trigger: Optional[str] = None, ability: Optional[Ability] = None) -> Dict:
        """Build context for ability effect functions."""
        base_damage = (ability.base_damage + self.character.stats.ATK) if ability and ability.base_damage else 0
        
        # NEW: Apply command_boost if Chain of Command is active
        if self.buffs.get("command_boost", 1.0) > 1.0:
            base_damage = int(base_damage * self.buffs["command_boost"])
        
        return {
            "character_stats": self.character.stats.dict() if self.character.stats else {},
            "character_hp": self.character_hp,
            "character_max_hp": self.character.stats.HP,
            "titan_hp": self.titan_hp,
            "titan_size": random.randint(5, 15),
            "is_intelligent_titan": random.random() < 0.1,
            "is_leader": random.random() < 0.05,
            "first_damage_taken": self.trigger_states.get("first_damage_taken", False),
            "dodge_count": self.trigger_states.get("dodge_count", 0),
            "fear_counter": self.trigger_states.get("fear_counter", 0),
            "focused_turns": self.trigger_states.get("focused_turns", 0),
            "ally_died": self.trigger_states.get("ally_died", False),
            "turn": self.turn,
            "gas": self.gas,
            "character_gas": self.character_gas,
            "base_damage": base_damage,
            "character_level": self.character.level,
            "target_is_self": False,
            "titan_difficulty": self.titan.difficulty,
        }

    def apply_passives(self, trigger: str) -> List[str]:
        """Apply passive abilities for a given trigger and collect messages."""
        messages = []
        character_data = get_character_data(self.character.character_type)
        if not character_data:
            return messages
        
        # Get passive abilities once
        passive_abilities = getattr(character_data, "passive_abilities", [])
        if not passive_abilities:
            return messages
        
        # Pre-build context once for all passives
        context = self.build_context(trigger)
        
        # Single loop through passives with pre-built context
        for ability in passive_abilities:
            if not ability or not ability.name:
                continue
            if self.character.level < ability.level_required:
                continue
            if not (ability.is_unlocked or self.character.unlocked_abilities.get(ability.name, False)):
                continue
            
            try:
                if ability.effect_function:
                    effect = ability.effect_function(context)
                    if effect:
                        self.apply_effect(effect)
                        if effect.message:
                            messages.append(effect.message)
            except Exception as e:
                logger.error(f"Error applying passive ability {ability.name}: {e}")
        
        return messages

    def apply_effect(self, effect: AbilityEffect) -> None:
        """Apply an ability effect to the battle state (damage, buffs, debuffs, etc)."""
        if not effect:
            return
        self.titan_hp = max(0, self.titan_hp - (effect.damage or 0))
        self.character_hp = min(self.character.stats.HP, self.character_hp + (effect.healed or 0))
        if effect.shield:
            self.buffs["shield"] = self.buffs.get("shield", 0) + effect.shield
        if effect.stun_duration:
            self.titan_debuffs["stun"] = max(self.titan_debuffs.get("stun", 0), effect.stun_duration)
        if hasattr(effect, 'counter_attack') and isinstance(effect.counter_attack, dict):
            counter_dmg = effect.counter_attack.get("damage", 0)
            if self.buffs.get("crit_damage"):
                counter_dmg *= self.buffs["crit_damage"]
            self.titan_hp = max(0, self.titan_hp - counter_dmg)
            attack_type = effect.counter_attack.get("type")
            if attack_type == "pierce":
                self.titan_debuffs["bleed"] = 3
            elif attack_type == "slash":
                self.titan_debuffs["damage_reduction"] = 1  
        if effect.buffs:
            self.buffs.update(effect.buffs)
            
            # NEW: Handle cooldown reset from Chain of Command
            if effect.buffs.get("reset_all_cooldowns") == 1:
                for ability_name in self.ability_cooldowns:
                    self.ability_cooldowns[ability_name] = 0
        
        if effect.debuffs:
            for k, v in effect.debuffs.items():
                self.titan_debuffs[k] = int(v)
        if effect.clear_debuffs:
            self.debuffs.clear()
        if effect.items_dropped:
            self.buffs["items_dropped"] = self.buffs.get("items_dropped", []) + effect.items_dropped
        if effect.target_switched:
            self.titan_debuffs["target_confusion"] = 2
        if effect.bleed_applied:
            self.titan_debuffs["bleed"] = max(self.titan_debuffs.get("bleed", 0), 3)

    # ---------- Immunity Helper ----------
    def check_immunity(self, debuff_name: str) -> bool:
        """Check if character is immune to a specific debuff."""
        immunity_map = {
            "stun": "immune_stun",
            "slow": "immune_slow",
            "confusion": "immune_confusion",
            "bleed": "immune_bleed",
            "burn": "immune_burn",
            "poison": "immune_poison",
        }
        
        immunity_flag = immunity_map.get(debuff_name)
        if immunity_flag and immunity_flag in self.buffs:
            return True
        return False

    # ---------- Titan Turn Logic ----------
    def titan_attack(self) -> Tuple[int, str]:
        """Calculate titan attack damage and effects for this turn."""
        if self.titan_debuffs.get("stun", 0) > 0:
            self.titan_debuffs["stun"] -= 1
            return 0, f"{self.get_titan_display_name()} is stunned and cannot attack this turn!"
        if self.titan_debuffs.get("delay", 0) > 0:
            self.titan_debuffs["delay"] -= 1
            return 0, f"{self.get_titan_display_name()} is delayed and cannot attack this turn!"
        if self.buffs.get("dodge", 0) > 0 or self.trigger_states["dodge_count"] > 0:
            if self.buffs.get("dodge", 0) > 0:
                del self.buffs["dodge"]
            else:
                self.trigger_states["dodge_count"] = max(0, self.trigger_states["dodge_count"] - 1)
            
            messages = self.apply_passives("dodge")
            return 0, f"{self.character.name} dodged the attack!\n" + "\n".join(messages)
        
        # --- Boss Titan AI ---
        if self.is_boss_battle:
            return self.boss_titan_attack()

        # Enhanced damage calculation using character DEF more effectively
        base_damage = max(15, self.titan.level * 8 + 10)
        difficulty_multipliers = {"Easy": 0.7, "Normal": 1.0, "Hard": 1.4}
        base_damage = int(base_damage * difficulty_multipliers.get(self.titan.difficulty, 1.0))
        special_messages = []

        # Character DEF reduces damage more significantly
        # NEW: Check if DEF is set to 0 by Iron Conviction reversal
        if self.buffs.get("def_zero", 0) > 0:
            def_reduction = 0.0  # No damage reduction when DEF=0
            special_messages.append("⚠️ Floch's defenses are completely down!")
        else:
            def_reduction = min(0.8, self.character.stats.DEF / 300)  
        
        damage = int(base_damage * (1 - def_reduction))
        
        # SPD affects dodge chance
        spd_dodge_chance = min(0.25, self.character.stats.SPD / 400)  
        if random.random() < spd_dodge_chance:
            messages = self.apply_passives("dodge")
            return 0, f"{self.character.name} dodged the attack with lightning speed!\n" + "\n".join(messages)
        
        # Ensure minimum damage
        damage = max(5, damage + random.randint(5, 15))
        
        # NEW: Apply morale damage reduction if active
        if self.buffs.get("morale_damage", 0) > 0:
            morale_reduction = self.buffs["morale_damage"]
            original_damage = damage
            damage = int(damage * (1.0 - morale_reduction))
            special_messages.append(f"🎭 Morale damage reduced attack by {int(morale_reduction*100)}% ({original_damage} → {damage})")
        
        if self.buffs.get("damage_reduction", 0):
            damage = int(damage * (1 - self.buffs["damage_reduction"]))
        if self.buffs.get("shield", 0) > 0:
            shield_absorb = min(self.buffs["shield"], damage)
            self.buffs["shield"] -= shield_absorb
            damage -= shield_absorb
            if self.buffs["shield"] <= 0:
                del self.buffs["shield"]
        
        self.character_hp = max(0, self.character_hp - damage)
        
        # Passive triggers
        if damage > 0 and not self.trigger_states["first_damage_taken"]:
            self.trigger_states["first_damage_taken"] = True
            messages = self.apply_passives("damage_taken")
            special_messages.extend(messages)
        if self.character_hp / self.character.stats.HP <= 0.3:
            self.trigger_states["fear_counter"] += 1
            messages = self.apply_passives("low_hp")
            special_messages.extend(messages)
        
        # Mission 7 Emergency Heal: +40 HP when HP < 100 (only in Titan Battles)
        if self.character_hp < 100 and self.player and not self.emergency_heal_used:
            mission_7_completed = False
            player_missions = getattr(self.player, "missions", [])
            for mission in player_missions:
                if isinstance(mission, dict):
                    mission_id = mission.get("mission_id")
                    mission_status = mission.get("status")
                else:
                    mission_id = getattr(mission, "mission_id", None)
                    mission_status = getattr(mission, "status", None)
                    
                if mission_id == 7 and mission_status == "completed":
                    mission_7_completed = True
                    break
            
            if mission_7_completed:
                heal_amount = 40
                old_hp = self.character_hp
                self.character_hp = min(self.character.stats.HP, self.character_hp + heal_amount)
                actual_heal = self.character_hp - old_hp
                if actual_heal > 0:
                    special_messages.append(f"🩹 *Emergency Heal!* Restored {actual_heal} HP from Mission 7 reward!")
                    self.emergency_heal_used = True
        
        self.trigger_states["focused_turns"] = min(3, self.trigger_states["focused_turns"] + 1)
        messages = self.apply_passives("titan_attack")
        special_messages.extend(messages)
        return damage, f"{self.get_titan_display_name()} attacks, dealing {damage} damage to {self.character.name}.\n" + "\n".join(special_messages)

    def boss_titan_attack(self) -> Tuple[int, str]:
        """Special attack logic for Boss Titans - BALANCED VERSION."""
        attack_roll = random.random()
        
        # Enraged Assault (below 30% HP) - BALANCED: Reduced from 60% to 30%
        if self.titan_hp / self.titan.max_hp < 0.3 and attack_roll < 0.15:
            damage = int(self.character.stats.HP * 0.30) # Reduced from 0.6 to 0.3
            self.character_hp = max(0, self.character_hp - damage)
            return damage, f"🔥 The Boss Titan unleashes an **Enraged Assault**, dealing {damage} damage!"

        # Devastating Slam - BALANCED: Reduced from 40% to 22%
        if attack_roll < 0.30:
            damage = int(self.character.stats.HP * 0.22) # Reduced from 0.4 to 0.22
            self.character_hp = max(0, self.character_hp - damage)
            return damage, f"💥 The Boss Titan uses **Devastating Slam**, dealing {damage} damage!"

        # Terrifying Roar - Unchanged (no damage, just debuff)
        elif attack_roll < 0.50: # Reduced chance from 0.55 to 0.50
            self.debuffs["fear"] = 2 # Apply fear for 2 turns
            return 0, f"😱 The Boss Titan lets out a **Terrifying Roar**! Your attack power is reduced for 2 turns."

        # Ground Shake - BALANCED: Reduced from 15% to 12%
        elif attack_roll < 0.75: # Reduced chance from 0.80 to 0.75
            damage = int(self.character.stats.HP * 0.12) # Reduced from 0.15 to 0.12
            self.character_hp = max(0, self.character_hp - damage)
            stun_chance = 0.35  # Reduced from 0.50 to 0.35
            if random.random() < stun_chance:
                self.debuffs["stun"] = 1
                return damage, f"🌋 The Boss Titan's **Ground Shake** deals {damage} damage and stuns you for 1 turn!"
            return damage, f"🌋 The Boss Titan's **Ground Shake** deals {damage} damage."
        
        # Default basic attack - BALANCED: Reduced from 20% to 15%
        else:
            damage = int(self.character.stats.HP * 0.15) # Reduced from 0.20 to 0.15
            self.character_hp = max(0, self.character_hp - damage)
            return damage, f"⚔️ The Boss Titan performs a swift attack, dealing {damage} damage."

    # ---------- Ability Usage ----------
    def use_ability(self, ability_name: str) -> Tuple[int, str, Dict, bool]:
        self.keyboard_cache_invalid = True
        
        # Initialize with default values for speed
        damage = 0
        message = ""
        effects = {"items_dropped": [], "target_switched": False, "bleed_applied": False}
        
        if not self.character or not self.character.stats:
            return damage, "Error: Character stats not available", effects, False
        
        # NEW: Check for mental exhaustion (blocks ultimate abilities)
        if self.debuffs.get("mental_exhaustion", 0) > 0:
            # Only block if trying to use an ultimate ability
            ability = self.ability_lookup.get(ability_name)
            if ability:
                character_data = get_character_data(self.character.character_type)
                if character_data and ability in getattr(character_data, "ultimate_abilities", []):
                    return 0, f"⚠️ {self.character.name} is mentally exhausted and cannot use ultimate abilities! ({self.debuffs['mental_exhaustion']} turns remaining)", effects, False
            
        # O(1) ability lookup using pre-built dictionary
        ability = self.ability_lookup.get(ability_name)
        if not ability:
            return damage, f"Error: Ability {ability_name} not found", effects, False
            
        # Check cooldown and gas with early returns
        cooldown = self.ability_cooldowns.get(ability_name, 0)
        if cooldown > 0:
            return damage, f"{ability_name} is on cooldown for {cooldown} turns!", effects, False
            
        gas_cost = ability.gas_cost or 20
        if self.is_boss_battle:
            gas_cost = int(gas_cost * 1.5)

        if self.gas < gas_cost:
            return damage, f"out of gas refill it by /char {self.character.name}", effects, True
        
        ctx = self.build_context("ability_use", ability)
        
        int_damage_bonus = 0
        if ability.base_damage and ability.base_damage > 0:
            int_damage_bonus = int(ability.base_damage * (self.character.stats.INT / 200))  # Max +50% at 100 INT
            ability.base_damage += int_damage_bonus
        
        try:
            if ability.effect_function:
                effect = ability.effect_function(ctx)
                if effect:
                    self.apply_effect(effect)
                    message = effect.message or f"{ability_name} used successfully!"
                    
                    # Apply Double Gas Injector buff (half gas cost)
                    actual_gas_cost = gas_cost
                    if self.player and hasattr(self.player, 'double_gas_injector_uses') and self.player.double_gas_injector_uses > 0:
                        actual_gas_cost = gas_cost // 2
                    
                    self.gas -= actual_gas_cost
                    
                    if int_damage_bonus > 0:
                        pass
                    
                    effects = {
                        "items_dropped": getattr(effect, 'items_dropped', []),
                        "target_switched": getattr(effect, 'target_switched', False),
                        "bleed_applied": getattr(effect, 'bleed_applied', False)
                    }
        except Exception as e:
            logger.error(f"Error applying ability {ability_name}: {e}")
            return damage, f"Error using {ability_name}", effects, False
            
        # Set cooldown and return
        self.ability_cooldowns[ability_name] = ability.cooldown or 1
        return damage, message, effects, False

    def has_usable_abilities(self) -> bool:
        """Check if character has any usable (off-cooldown, enough gas) abilities."""
        for ability_name, ability in self.ability_lookup.items():
            if (self.character.level >= ability.level_required and
                not ability.disabled_against_titans and
                self.ability_cooldowns.get(ability_name, 0) == 0 and
                self.gas >= (ability.gas_cost or 0)):
                return True
        return False

    # ---------- Turn & Status Updates ----------
    def update_cooldowns(self) -> None:
        self.keyboard_cache_invalid = True
        
        # Fast loop for cooldowns (minimize lookups)
        for ability_name in list(self.ability_cooldowns.keys()):
            if self.ability_cooldowns[ability_name] > 0:
                self.ability_cooldowns[ability_name] -= 1
        
        # Handle Iron Conviction reversal (Last Bastion of War aftermath)
        if "iron_conviction" in self.buffs:
            iron_conv_turns = self.buffs.get("iron_conviction_turns", 0)
            iron_conv_turns -= 1
            self.buffs["iron_conviction_turns"] = iron_conv_turns
            
            if iron_conv_turns <= 0:
                # Remove immunity buffs and apply reversal
                immunity_flags = ["immune_stun", "immune_slow", "immune_confusion", "immune_bleed"]
                for flag in immunity_flags:
                    if flag in self.buffs:
                        del self.buffs[flag]
                
                if "iron_conviction" in self.buffs:
                    del self.buffs["iron_conviction"]
                if "iron_conviction_turns" in self.buffs:
                    del self.buffs["iron_conviction_turns"]
                
                # Apply reversal debuffs
                self.debuffs["stun"] = 1  # 1-turn stun
                self.buffs["def_zero"] = 2  # DEF reduced to 0 for 2 turns
        
        # Handle DEF=0 duration
        if "def_zero" in self.buffs:
            def_zero_duration = self.buffs["def_zero"]
            def_zero_duration -= 1
            
            if def_zero_duration <= 0:
                del self.buffs["def_zero"]
            else:
                self.buffs["def_zero"] = def_zero_duration
        
        # Process titan debuffs efficiently
        to_remove = []
        for debuff, value in self.titan_debuffs.items():
            if value > 0:
                self.titan_debuffs[debuff] -= 1
                if self.titan_debuffs[debuff] <= 0:
                    to_remove.append(debuff)
        
        for debuff in to_remove:
            del self.titan_debuffs[debuff]

        if "reflex_counter" in self.buffs:
            self.buffs["reflex_counter"] -= 1
            if self.buffs["reflex_counter"] <= 0:
                del self.buffs["reflex_counter"]
                if "crit_rate" in self.buffs:
                    del self.buffs["crit_rate"]
                    
        # Process other generic buffs
        to_remove = []
        for buff, value in self.buffs.items():
            if isinstance(value, (int, float)) and buff not in ["shield", "items_dropped", "reflex_counter"]:
                if value > 1:
                    self.buffs[buff] -= 1
                    if self.buffs[buff] <= 0:
                        to_remove.append(buff)
        
        # Batch delete operations
        for buff in to_remove:
            del self.buffs[buff]
        # Burn effect
        if self.debuffs.get("burn", 0) > 0:
            burn_damage = max(5, self.titan.level * 2)
            self.character_hp = max(0, self.character_hp - burn_damage)
            self.debuffs["burn"] -= 1
            if self.debuffs["burn"] <= 0:
                del self.debuffs["burn"]
        # Bleed effect
        if self.titan_debuffs.get("bleed", 0) > 0:
            character_atk = self.character.stats.ATK or 10
            bleed_damage = max(10, character_atk)
            self.titan_hp = max(0, self.titan_hp - bleed_damage)
            self.titan_debuffs["bleed"] -= 1
            if self.titan_debuffs["bleed"] <= 0:
                del self.titan_debuffs["bleed"]

    def get_battle_status(self) -> Dict:
        """Return current battle state for UI display - ULTRA OPTIMIZED."""
        # OPTIMIZED: Use direct calculations instead of intermediate variables
        char_bar_filled = int((self.character_hp / self.character.stats.HP) * 10)
        titan_bar_filled = int((self.titan_hp / self.titan.max_hp) * 10)
        
        # OPTIMIZED: Direct string multiplication (fastest method)
        character_bar = "█" * char_bar_filled + "▒" * (10 - char_bar_filled)
        titan_bar = "█" * titan_bar_filled + "▒" * (10 - titan_bar_filled)
        
        # OPTIMIZED: Single f-string for status (faster than join)
        status_message = (
            f"Turn: {self.turn + 1}\n"
            f"Difficulty: {self.titan.difficulty}\n"
            f"⚔️ ATK: {self.character.stats.ATK} | 🛡️ DEF: {self.character.stats.DEF}\n"
            f"🎯 ACC: {self.character.stats.ACC} | 🧠 INT: {self.character.stats.INT} | ⚡ SPD: {self.character.stats.SPD}"
        )
        
        return {
            "character_hp": int(self.character_hp),
            "titan_hp": int(self.titan_hp),
            "gas": int(self.gas),
            "character_bar": character_bar,
            "titan_bar": titan_bar,
            "status_message": status_message
        }

    def calculate_rewards(self, titan: Titan, character: Character, player: Optional[Player], explore_count: int) -> Dict:
        """Calculate rewards for defeating the titan (XP, marks, valor, crystal) - FIXED ECONOMY."""
        xp = max(1, random.randint(100, 180))
        
        if player and hasattr(player, 'frenzy_elixir_uses') and player.frenzy_elixir_uses > 0:
            xp *= 3
        
        # FIXED: Reduced marks by 40% + better scaling for high levels
        base_marks = random.randint(40, 60)
        level_bonus = titan.level * 1
        if titan.level > 50:  # Diminishing returns after level 50
            level_bonus = (50 * 1) + ((titan.level - 50) * 0.3)
        marks = max(1, base_marks + int(level_bonus))
        
        if player and hasattr(player, 'mark_surge_token_uses') and player.mark_surge_token_uses > 0:
            marks *= 2
        
        # FIXED: Increased valor drops from 0.01% to 5%
        valor = 0
        if random.random() < 0.02: 
            valor = random.randint(1, 3)
        
        # FIXED: Added crystal drops (0.5% base chance + ultra-rare)
        crystal = 0
        if random.random() < 0.002:  
            crystal = random.randint(1, 2)
        elif random.random() < 0.001:  
            crystal += random.randint(3, 5)

        # Boss Rewards - BALANCED: Better rewards for harder fight
        if self.is_boss_battle:
            xp *= 7  
            marks *= 5  # Increased from 3x to 5x
            crystal += random.randint(2, 5) 
            valor += random.randint(8, 15) 
            
        return {
            "xp": xp,
            "marks": marks,
            "crystal": crystal,  # Now actually drops!
            "valor": valor,
        }

    def get_titan_display_name(self) -> str:
        return "Boss Titan" if self.is_boss_battle else self.titan.name

# =========================
# UTILITY FUNCTIONS
# =========================

def calculate_gas_consumption(titan: Titan) -> int:
    """Calculate gas consumption based on titan difficulty."""
    base_gas = 1000
    difficulty_modifiers = {"Easy": -200, "Normal": 0, "Hard": 500}
    return base_gas + difficulty_modifiers.get(titan.difficulty, 0)

async def _cancel_timeout(task: asyncio.Task) -> None:
    """Cancel timeout task in background."""
    try:
        task.cancel()
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

def cleanup_battle(user_id: str, result: str = "ended", battle: Optional['BattleSystem'] = None) -> None:
    """Clean up battle state and resources, remove from active battles."""
    user_id = str(user_id)
    
    if result == "timeout_cancelled":
        return
        
    async def _cleanup():
        async with active_battles_lock:
            battle_instance = battle or active_battles.get(user_id)
            if battle_instance:
                try:
                    username = battle_instance.character.name
                    track_battle_end(int(user_id), username, result)
                except (ImportError, AttributeError):
                    pass
                try:
                    battle_instance.battle_ended = True
                    battle_instance.dispose()
                except Exception as e:
                    pass
                if user_id in active_battles:
                    del active_battles[user_id]
        try:
            from utils.monitor import remove_player_activity
            remove_player_activity(int(user_id))
        except ImportError:
            pass
    asyncio.create_task(_cleanup())


async def generate_ability_keyboard(battle: 'BattleSystem', context: ContextTypes.DEFAULT_TYPE) -> List[List[InlineKeyboardButton]]:
    """ULTRA OPTIMIZED: Generate keyboard with aggressive caching."""
    
    # OPTIMIZED: Return cached keyboard if still valid
    if not battle.keyboard_cache_invalid and battle.keyboard_cache:
        return battle.keyboard_cache

    keyboard = []
    
    # OPTIMIZED: Fast lookup for character data
    character_data = get_character_data(battle.character.character_type)
    if not character_data:
        keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
        battle.keyboard_cache = keyboard
        battle.keyboard_cache_invalid = False
        return keyboard
    
    # OPTIMIZED: Pre-calculate gas costs once
    attack_gas_cost = 10 if (battle.player and hasattr(battle.player, 'double_gas_injector_uses') and battle.player.double_gas_injector_uses > 0) else 20
    
    # OPTIMIZED: Get weapon info once
    shop_items = context.bot_data.get("shop_items", {})
    weapon = battle.get_equipped_weapon(shop_items)
    
    # OPTIMIZED: Build ability buttons in single pass (no intermediate lists)
    ability_buttons = []
    for ability_name, ability in battle.ability_lookup.items():
        # Skip passive abilities
        if hasattr(ability, 'show_as_button') and not ability.show_as_button:
            continue

        if battle.character.level >= ability.level_required:
            cooldown = battle.ability_cooldowns.get(ability_name, 0)
            gas_cost = ability.gas_cost or 20
            
            # OPTIMIZED: Inline button creation
            if cooldown > 0:
                ability_buttons.append(InlineKeyboardButton(
                    f"{battle.ability_prefixes.get(ability_name, '')} {ability.name} ({cooldown}t)",
                    callback_data=f"cooldown_{ability.name}"
                ))
            elif battle.gas < gas_cost:
                ability_buttons.append(InlineKeyboardButton(
                    f"{battle.ability_prefixes.get(ability_name, '')} {ability.name} (Low Gas)",
                    callback_data=f"lowgas_{ability.name}"
                ))
            else:
                ability_buttons.append(InlineKeyboardButton(
                    f"{battle.ability_prefixes.get(ability_name, '')} {ability.name}",
                    callback_data=f"ability_{ability.name}"
                ))
    
    # Group ability buttons in rows of 3
    for i in range(0, len(ability_buttons), 3):
        keyboard.append(ability_buttons[i:i+3])
    
    # OPTIMIZED: Add attack button
    if battle.gas >= attack_gas_cost:
        attack_text = f"🗡️ Attack ({weapon.name})" if weapon else "🗡️ Attack"
        keyboard.append([InlineKeyboardButton(attack_text, callback_data="action_basic_attack")])
    else:
        attack_text = f"🗡️ Attack ({weapon.name}) (No Gas)" if weapon else "🗡️ Attack (No Gas)"
        keyboard.append([InlineKeyboardButton(attack_text, callback_data="lowgas_attack")])

    # OPTIMIZED: Add run and switch in same row
    run_switch_row = [InlineKeyboardButton("🏃 Run", callback_data="action_run")]
    if battle.player and hasattr(battle.player, 'team') and battle.player.team and len(battle.player.team) > 1:
        if any(team_member.character_name != battle.character.name for team_member in battle.player.team):
            run_switch_row.append(InlineKeyboardButton("🔄 Switch", callback_data="action_switch"))
    keyboard.append(run_switch_row)
    
    # Cache and return
    battle.keyboard_cache = keyboard
    battle.keyboard_cache_invalid = False
    
    return keyboard

# =========================
# ASYNC HANDLERS (TELEGRAM)
# =========================

async def handle_battle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ULTRA OPTIMIZED: Handle battle start with instant response."""
    start_time = time.time()
    
    query = update.callback_query
    if not query or not update.effective_user:
        return
    
    user_id = str(update.effective_user.id)
    callback_data = query.data
    
    # OPTIMIZED: Answer immediately in background for instant feedback
    asyncio.create_task(query.answer())
    
    # OPTIMIZED: Fast pre-checks (no locks yet)
    current_battle_id = context.bot_data.get(f"active_battle_id_{user_id}")
    
    # Quick validation - exit early if invalid
    if callback_data != current_battle_id:
        # Check if it was already used or expired
        if current_battle_id and (current_battle_id.startswith("used_") or current_battle_id.startswith("expired_")):
            asyncio.create_task(query.answer("⚠️ This titan encounter has already been used or expired. Use /explore again!", show_alert=True))
            logger.warning(f"Battle button clicked but ID already used/expired for user {user_id}")
        else:
            asyncio.create_task(query.answer("⚠️ Battle expired or invalid. Use /explore again!", show_alert=True))
            logger.warning(f"Battle button clicked but ID mismatch for user {user_id}: {callback_data} != {current_battle_id}")
        return
    
    # Check if already in battle (fast check without lock first)
    if user_id in active_battles:
        asyncio.create_task(query.answer("⚠️ You are already in a battle!", show_alert=True))
        logger.warning(f"User {user_id} already in active battle")
        return
    
    # CRITICAL FIX: Cancel timeout FIRST to prevent race condition
    titan_timeout_key = f"titan_timeout_{user_id}"
    titan_timeout_task = context.bot_data.pop(titan_timeout_key, None)
    if titan_timeout_task and not titan_timeout_task.done():
        # Cancel immediately and wait for cancellation
        titan_timeout_task.cancel()
        try:
            await titan_timeout_task
        except asyncio.CancelledError:
            pass
    
    # Also remove from global timeout tasks dictionary
    from game.explore import user_timeout_tasks
    if user_id in user_timeout_tasks:
        del user_timeout_tasks[user_id]
    
    # NOW mark battle ID as used (after timeout is fully cancelled)
    context.bot_data[f"active_battle_id_{user_id}"] = f"used_{current_battle_id}_{time.time()}"
    
    # OPTIMIZED: Get DB reference (fast)
    db = context.bot_data.get("db")
    if not db:
        asyncio.create_task(query.edit_message_text("Database error!"))
        return
    
    # OPTIMIZED: Fetch titan and player data in parallel
    titan_task = asyncio.create_task(db.get_titan(user_id))
    
    # Initialize user data cache if needed (faster than checking each time)
    if not hasattr(context, "user_data") or context.user_data is None:
        context.user_data = {}
    
    # Set up battle cache efficiently
    if "battle_cache" not in context.user_data:
        context.user_data["battle_cache"] = {}
    battle_cache = context.user_data["battle_cache"]
    
    # CRITICAL FIX: Save cached titan data BEFORE clearing it (prevent race condition)
    cached_titan_data = context.bot_data.get(f"last_titan_data_{user_id}")
    
    # Prepare player data fetch - use cached if available
    player_data_task = None
    if not battle_cache.get("player_data"):
        # Use get_player for both DB and memory
        player_data_task = db.get_player(user_id)
    
    # Wait for titan data
    titan_obj = await titan_task
    if not titan_obj:
        # Use cached titan data if available or show error
        if not cached_titan_data:
            # Titan expired or deleted
            logger.warning(f"Titan not found for user {user_id} - titan may have expired")
            
            # Check if battle_id was marked as used (race condition case)
            if current_battle_id and current_battle_id.startswith("used_"):
                # Battle was already initiated, use emergency cached data
                
                # Try to recreate titan from message context if possible
                try:
                    # Fallback: Create a basic titan for the user
                    from database.models import generate_titan_hp, generate_titan_name, generate_titan_xp
                    
                    # Get player data for titan generation (attempt to use a character level if available)
                    if player_data_task:
                        player_data = await player_data_task
                    else:
                        player_data = battle_cache.get("player_data")

                    if player_data:
                        difficulty = "Normal"  # Default difficulty
                        # Try to determine an appropriate character level to base titan on.
                        # Prefer the first team member's character level if available, otherwise fall back to player's level.
                        char_level = None
                        try:
                            # player_data may be an object or dict; handle both
                            team = getattr(player_data, 'team', None) if not isinstance(player_data, dict) else player_data.get('team')
                            if team and len(team) > 0:
                                first = team[0]
                                char_level = getattr(first, 'level', None) if not isinstance(first, dict) else first.get('level')
                        except Exception:
                            char_level = None

                        if not char_level:
                            # Use player's overall level as fallback
                            char_level = getattr(player_data, 'level', None) if not isinstance(player_data, dict) else player_data.get('level', 1)

                        if not char_level:
                            char_level = 1

                        # Choose titan level randomly between char_level-3 and char_level+3, clamped to >=1
                        min_level = max(1, int(char_level) - 3)
                        max_level = int(char_level) + 3
                        titan_level = random.randint(min_level, max_level)
                        
                        titan_data = {
                            "name": generate_titan_name(difficulty),
                            "level": titan_level,
                            "max_hp": generate_titan_hp(level=titan_level, difficulty=difficulty, character_stats=None),
                            "xp_reward": generate_titan_xp(titan_level, difficulty),
                            "difficulty": difficulty,
                            "created_at": datetime.now(timezone.utc),
                            "spawn_areas": ["Trost"],
                            "min_level_requirement": max(1, titan_level - 2),
                            "abilities": [],
                            "drop_table": {},
                            "is_boss": False
                        }
                        
                        # Emergency titan created successfully
                    else:
                        raise ValueError("Cannot recreate titan without player data")
                        
                except Exception as e:
                    logger.error(f"Emergency titan creation failed: {e}")
                    try:
                        await query.edit_message_text(
                            "⏰ <b>This titan encounter has expired!</b>\n\n"
                            "Use /explore to find a new one.",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        pass
                    return
            else:
                # Normal timeout/expiration case
                try:
                    await query.edit_message_text(
                        "⏰ <b>This titan encounter has expired!</b>\n\n"
                        "Use /explore to find a new one.",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Error editing message for expired titan: {e}")
                    # Try answering callback query if edit fails
                    try:
                        await query.answer("⚠️ This titan has expired. Use /explore again!", show_alert=True)
                    except Exception:
                        pass
                return
        else:
            titan_data = cached_titan_data
    else:
        # Fallback to bot_data cache, or use titan_obj if available
        if f"last_titan_data_{user_id}" in context.bot_data:
            titan_data = context.bot_data[f"last_titan_data_{user_id}"]
        elif titan_obj:
            titan_data = titan_obj.dict()
        else:
            # This should not happen in normal flow
            logger.error(f"No titan data available for user {user_id}")
            try:
                await query.edit_message_text(
                    "⏰ <b>This titan encounter has expired!</b>\n\n"
                    "Use /explore to find a new one.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return
    
    # Ensure titan_data has all required fields
    if not titan_data or not isinstance(titan_data, dict):
        logger.error(f"Invalid titan_data for user {user_id}: {type(titan_data)}")
        try:
            await query.edit_message_text(
                "⏰ <b>This titan encounter has expired!</b>\n\n"
                "Use /explore to find a new one.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return
    
    # Ensure all required fields are present (with sensible defaults)
    titan_data.setdefault("level", 1)
    titan_data.setdefault("abilities", [])
    titan_data.setdefault("created_at", datetime.now(timezone.utc))
    titan_data.setdefault("spawn_areas", ["Trost"])
    titan_data.setdefault("min_level_requirement", 1)
    titan_data.setdefault("drop_table", {})
    titan_data.setdefault("is_boss", False)
    
    # Create titan object
    titan = Titan(**titan_data)
    
    # NOW clear the cached titan data to prevent data leakage between battles
    if f"last_titan_data_{user_id}" in context.bot_data:
        del context.bot_data[f"last_titan_data_{user_id}"]
    
    # Get player data (either from cache or database)
    if player_data_task:
        player_data = await player_data_task
        battle_cache["player_data"] = player_data
    else:
        player_data = battle_cache["player_data"]
    
    # OPTIMIZED: Fast player validation
    if not player_data or isinstance(player_data, Exception):
        asyncio.create_task(query.edit_message_text("Player data error!"))
        return
    
    # Get team and character name
    team = player_data.team if hasattr(player_data, 'team') else player_data.get('team')
    if not team:
        asyncio.create_task(query.edit_message_text("No character in team!"))
        return
    
    # Sort team by position to respect team order
    sorted_team = sorted(team, key=lambda m: getattr(m, 'position', 1) if hasattr(m, 'position') else (m.get('position', 1) if isinstance(m, dict) else 1))
    
    team_member = sorted_team[0]
    character_name = team_member.character_name if hasattr(team_member, 'character_name') else team_member.get('character_name', team_member)
    
    # OPTIMIZED: Fetch character (use get_character which has caching)
    character = await db.get_character(user_id, character_name)
    if not character:
        asyncio.create_task(query.edit_message_text(f"Character {character_name} not found!"))
        return
    
    # OPTIMIZED: Quick HP validation
    if character.current_hp <= 0:
        character.current_hp = character.stats.HP
    
    # Create player object (fast conversion)
    player = player_data if isinstance(player_data, Player) else Player(**player_data)
    
    # OPTIMIZED: Create battle system
    battle = BattleSystem(character, titan, player, team, 0)
    
    emergency_heal_message = ""
    if player:
        mission_7_completed = False
        player_missions = getattr(player, "missions", [])
        for mission in player_missions:
            # Handle both dict and object formats for missions
            if isinstance(mission, dict):
                mission_id = mission.get("mission_id")
                mission_status = mission.get("status")
            else:
                mission_id = getattr(mission, "mission_id", None)
                mission_status = getattr(mission, "status", None)
                
            if mission_id == 7 and mission_status == "completed":
                mission_7_completed = True
                break
        
        if mission_7_completed and battle.character_hp < 100 and not battle.emergency_heal_used:
            heal_amount = 40
            old_hp = battle.character_hp
            battle.character_hp = min(battle.character.stats.HP, battle.character_hp + heal_amount)
            actual_heal = battle.character_hp - old_hp
            if actual_heal > 0:
                # Update character HP in database
                character.current_hp = battle.character_hp
                await db.update_character(character)
                emergency_heal_message = f"🩹 *Emergency Heal!* Restored {actual_heal} HP from Mission 7 reward!\n\n"
                battle.emergency_heal_used = True
    
    # OPTIMIZED: Check PVP battle (fast, no import delay)
    try:
        from game.pvp_system import active_pvp_battles
        if user_id in active_pvp_battles:
            asyncio.create_task(query.edit_message_text("⚔️ In PVP battle! Complete it first."))
            return
    except ImportError:
        pass
    
    # OPTIMIZED: Add to active battles (fast)
    active_battles[user_id] = battle
    
    # OPTIMIZED: Reset spam count and track (background)
    if "explore_spam_count" in context.bot_data:
        context.bot_data["explore_spam_count"][user_id] = 0
    asyncio.create_task(_track_battle_start(user_id, update.effective_user, battle))
    
    # OPTIMIZED: Generate UI components in parallel
    keyboard_task = asyncio.create_task(generate_ability_keyboard(battle, context))
    status = battle.get_battle_status()
    
    # OPTIMIZED: Build message while waiting for keyboard
    battle_message = (
        f"<b>⚔️ BATTLE ⚔️</b>\n"
        f"{emergency_heal_message}"
        f"\n<b>| {battle.get_titan_display_name()} ({battle.titan.level}) |</b>\n"
        f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp}</b>\n"
        f"{status['titan_bar']}\n\n"
        f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>\n"
        f"<b>HP: {status['character_hp']}/{battle.character.stats.HP}</b>\n"
        f"{status['character_bar']}\n"
        f"<b>Gas: {status['gas']}/{battle.character.max_gas}</b>"
    )
    
    # Wait for keyboard
    keyboard = await keyboard_task
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # OPTIMIZED: Get chat_id (fast)
    chat_id = query.message.chat_id if query.message else None
    
    if chat_id:
        # Send battle UI
        await context.bot.send_message(
            chat_id=chat_id,
            text=battle_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    # Start timeout in background
    battle.timeout_task = asyncio.create_task(battle_timeout(user_id, query, battle, context))
    context.bot_data[f"titan_battle_started_{user_id}"] = time.time()

# Helper function to move tracking to background
async def _track_battle_start(user_id, effective_user, battle):
    """Track battle start in the background to not slow down the main flow."""
    try:
        from utils.monitor import track_player_action
        username = effective_user.username or effective_user.first_name or "Unknown"
        track_player_action(int(user_id), username, "🔥 In Battle", {
            "character": battle.character.name,
            "titan": battle.titan.name,
            "titan_level": battle.titan.level
        })
    except (ImportError, Exception):
        pass  # Silently fail for performance

async def handle_battle_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle battle actions - ULTRA OPTIMIZED for speed."""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    user_id = str(update.effective_user.id)

    # OPTIMIZED: Answer immediately in background (don't wait)
    asyncio.create_task(query.answer())

    # OPTIMIZED: Ultra-fast anti-spam (200ms for instant feel)
    action_time_key = f"battle_action_time_{user_id}"
    last_action_time = context.bot_data.get(action_time_key, 0)
    current_time = time.time()

    if current_time - last_action_time < 0.2:
        return
    
    # Track callback ID
    callback_id = query.id
    last_callback_key = f"last_battle_callback_{user_id}"
    
    if context.bot_data.get(last_callback_key) == callback_id:
        return
    
    # Update tracking
    context.bot_data[action_time_key] = current_time
    context.bot_data[last_callback_key] = callback_id

    # OPTIMIZED: Get battle without await
    battle = active_battles.get(user_id)
    if not battle or battle.battle_ended:
        return

    # OPTIMIZED: Cancel timeout immediately
    if battle.timeout_task and not battle.timeout_task.done():
        battle.timeout_task.cancel()

    battle.keyboard_cache_invalid = True
    full_message = []
    action = query.data

    # --- OPTIMIZED: Fast Action Dispatcher ---
    if action.startswith("cooldown_") or action.startswith("lowgas_"):
        # OPTIMIZED: Non-action feedback without turn processing
        asyncio.create_task(_handle_info_action(query, action, battle))
        battle.timeout_task = asyncio.create_task(battle_timeout(user_id, query, battle, context))
        return

    # --- Process Turn-Based Actions ---
    if action == "action_run":
        ended, message = await _handle_run_action(battle, user_id, context)
        full_message.append(message)
        if ended:
            await query.edit_message_text(message)
            return
    elif action == "action_switch":
        await _handle_switch_action(query, battle, context)
        return 
    elif action.startswith("switch_to_"):
        message = await _handle_do_switch(action, battle, user_id, context)
        full_message.append(message)
    elif action == "switch_back":
        pass  # Just regenerate UI
    elif action == "action_basic_attack":
        ended, message = await _handle_basic_attack(battle, context)
        full_message.append(message)
        if ended:
            await query.edit_message_text(message, parse_mode=ParseMode.HTML)
            cleanup_battle(user_id, "out_of_gas", battle)
            return
    elif action.startswith("ability_"):
        ended, message = await _handle_ability_action(action, battle)
        full_message.append(message)
        if ended:
            await query.edit_message_text(message, parse_mode=ParseMode.HTML)
            cleanup_battle(user_id, "out_of_gas", battle)
            return

    # --- Main Battle Flow ---
    if battle.titan_hp <= 0:
        await handle_battle_end(query, battle, user_id, context)
        return

    # OPTIMIZED: Titan's turn - only process if action was taken
    if battle.character_hp > 0:
        _, titan_message = battle.titan_attack()
        full_message.append(titan_message)

    battle.turn += 1
    battle.update_cooldowns()

    if battle.character_hp <= 0:
        await handle_battle_end(query, battle, user_id, context)
        return

    await _update_battle_ui(query, battle, context, full_message)

async def _update_battle_ui(query, battle, context, full_message):
    """Helper to generate and send the updated battle UI - ULTRA OPTIMIZED."""
    # OPTIMIZED: Skip UI update if message hasn't changed (battle switch/back actions)
    if not full_message or (len(full_message) == 1 and not full_message[0]):
        keyboard = await generate_ability_keyboard(battle, context)
        reply_markup = InlineKeyboardMarkup(keyboard)
        battle.timeout_task = asyncio.create_task(battle_timeout(str(query.from_user.id), query, battle, context))
        return
    
    # OPTIMIZED: Get status first, then generate keyboard in background
    status = battle.get_battle_status()
    
    # Pre-build message immediately (don't wait for keyboard)
    battle_message = (
        f"<b>⚔️ BATTLE ⚔️</b>\n"
        f"{chr(10).join(filter(None, full_message))}\n\n"
        f"<b>| {battle.get_titan_display_name()} ({battle.titan.level}) |</b>\n"
        f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp}</b>\n"
        f"{status['titan_bar']}\n\n"
        f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>\n"
        f"<b>HP: {status['character_hp']}/{battle.character.stats.HP}</b>\n"
        f"{status['character_bar']}\n"
        f"<b>Gas: {status['gas']}/{battle.character.max_gas}</b>"
    )
    
    # Generate keyboard
    keyboard = await generate_ability_keyboard(battle, context)
    reply_markup = InlineKeyboardMarkup(keyboard)

    # OPTIMIZED: Fast edit with no error handling overhead
    try:
        await query.edit_message_text(battle_message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception:
        pass

    # Start timeout in background
    battle.timeout_task = asyncio.create_task(battle_timeout(str(query.from_user.id), query, battle, context))

async def _handle_info_action(query, action, battle):
    """Handle info-only actions (cooldown/lowgas) in background - ULTRA FAST."""
    try:
        if action.startswith("cooldown_"):
            ability_name = action.split('_', 1)[1]
            cooldown = battle.ability_cooldowns.get(ability_name, 0)
            await query.answer(f"{ability_name}: {cooldown} turns", show_alert=True)
        else:
            await query.answer(f"Low gas! /char {battle.character.name}", show_alert=True)
    except Exception:
        pass

async def _handle_run_action(battle, user_id, context):
    """Handles the logic for a player attempting to run from battle."""
    if battle.is_boss_battle:
        if random.random() < 0.20:  
            cleanup_battle(user_id, "escaped", battle)
            return True, f"🏃💨 Against all odds, {battle.character.name} successfully escaped the Boss Titan!"
        else:
            # Failed escape: Boss gets a free attack
            _, titan_message = battle.titan_attack()
            message = f"❌ {battle.character.name} failed to escape! The Boss Titan attacks!\n\n{titan_message}"
            return False, message
            
    if random.random() < 0.7:  # 70% success
        cleanup_battle(user_id, "escaped", battle)
        return True, f"🏃💨 {battle.character.name} successfully escaped!"
    else:
        return False, f"❌ {battle.character.name} failed to escape!"

async def _handle_switch_action(query, battle, context):
    """Displays the character switching UI."""
    if not battle.player or not hasattr(battle.player, 'team') or len(battle.player.team) <= 1:
        await query.answer("You don't have other characters to switch to!", show_alert=True)
        # Need to redraw the UI and restart the timer if the action is invalid
        await _update_battle_ui(query, battle, context, [])
        return

    switch_keyboard = []
    current_name = battle.character.name
    for i, member in enumerate(battle.player.team):
        char_name = member.character_name
        if char_name != current_name:
            switch_keyboard.append([InlineKeyboardButton(char_name, callback_data=f"switch_to_{i}")])
    
    switch_keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="switch_back")])
    
    await query.edit_message_text(
        "<b>Select a character to switch to:</b>",
        reply_markup=InlineKeyboardMarkup(switch_keyboard),
        parse_mode=ParseMode.HTML
    )
    # Restart timeout after showing switch menu
    battle.timeout_task = asyncio.create_task(battle_timeout(str(query.from_user.id), query, battle, context))


async def _handle_do_switch(action, battle, user_id, context):
    """Handles the actual character switch logic."""
    try:
        index = int(action.split('_')[-1])
        # Ensure player and team exist and are valid
        if not battle.player or not battle.player.team:
            return "Player data is missing."
        target_member = battle.player.team[index]
        target_name = target_member.character_name
    except (ValueError, IndexError):
        return "Invalid switch selection."

    # Check cache first, then DB
    target_character = battle.character_cache.get(target_name)
    if not target_character:
        db = context.bot_data["db"]
        target_character = await db.get_character(user_id, target_name)
        if target_character:
            battle.character_cache[target_name] = target_character
    
    if not target_character:
        return f"Could not load character: {target_name}"
    
    if target_character.current_hp <= 0:
        return f"{target_name} has 0 HP and cannot be switched in!"

    # Add both characters to participants for XP
    battle.participating_characters.add(battle.character.name)
    battle.participating_characters.add(target_name)

    old_name = battle.character.name
    battle.character = target_character
    battle.character_hp = target_character.current_hp
    battle.gas = target_character.gas
    battle.character_gas = target_character.max_gas
    battle.max_gas = target_character.max_gas
    
    # Re-initialize cooldowns for the new character
    character_data = get_character_data(target_character.character_type)
    if character_data:
        battle.ability_cooldowns = {
            ability.name: 0 for ability in (
                (character_data.active_abilities or []) +
                (character_data.passive_abilities or []) +
                (character_data.ultimate_abilities or [])
            )
        }
        # Rebuild ability lookup for new character
        prefixes = {
            "active_abilities": "⚔️",
            "passive_abilities": " ",
            "ultimate_abilities": "✨"
        }
        battle.ability_lookup = {}
        battle.ability_prefixes = {}
        for ability_type, prefix in prefixes.items():
            abilities = getattr(character_data, ability_type, [])
            for ability in abilities:
                if ability and ability.name:
                    battle.ability_lookup[ability.name] = ability
                    battle.ability_prefixes[ability.name] = prefix

    battle.apply_passives("battle_start") 

    return f"🔄 Switched from {old_name} to {target_name}!"

async def _handle_basic_attack(battle, context):
    """Handles the logic for a basic attack."""
    gas_cost = 20
    if battle.is_boss_battle:
        gas_cost = int(gas_cost * 1.5)

    if battle.player and hasattr(battle.player, 'double_gas_injector_uses') and battle.player.double_gas_injector_uses > 0:
        gas_cost = 10

    if battle.gas < gas_cost:
        return True, f"out of gas refill it by /char {battle.character.name}"

    battle.gas -= gas_cost
    
    shop_items = context.bot_data.get("shop_items", {})
    weapon = battle.get_equipped_weapon(shop_items)
    
    # Simplified damage calculation for performance
    if weapon and hasattr(weapon, 'attributes'):
        min_dmg = weapon.attributes.get("damage_min", 10)
        max_dmg = weapon.attributes.get("damage_max", 20)
        base_dmg = random.randint(min_dmg, max_dmg)
        atk_bonus = int(base_dmg * (battle.character.stats.ATK / 100))
        total_damage = base_dmg + atk_bonus
        message = f"⚔️ {battle.character.name} attacks with {weapon.name}, dealing {total_damage} damage!"
    else:
        total_damage = max(10, battle.character.stats.ATK + random.randint(15, 25))
        message = f"⚔️ {battle.character.name} attacks with a basic strike, dealing {total_damage} damage!"

    # Apply fear debuff if active
    if battle.debuffs.get("fear", 0) > 0:
        total_damage = int(total_damage * 0.7) # 30% damage reduction
        message += "\n(Attack power reduced by fear!)"

    battle.titan_hp = max(0, battle.titan_hp - total_damage)
    return False, message

async def _handle_ability_action(action, battle):
    """Handles the logic for using an ability."""
    ability_name = action.split('_', 1)[1]
    _, message, _, battle_end = battle.use_ability(ability_name)
    return battle_end, message

async def handle_battle_end(query, battle: 'BattleSystem', user_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the end of a battle, updating gas and rewards with optimized performance."""
    user_id = str(user_id)
    if battle.battle_ended:
        return
    battle.battle_ended = True

    # Cancel timeout immediately
    if battle.timeout_task and not battle.timeout_task.done():
        battle.timeout_task.cancel()

    db = context.bot_data.get("db")
    if not db:
        logger.error("Database not initialized")
        await query.edit_message_text("❌ Database error!")
        cleanup_battle(user_id, "error", battle)
        return

    player_data = await db.get_player(user_id)

    if not player_data:
        await query.edit_message_text("❌ Player data not found!")
        cleanup_battle(user_id, "error", battle)
        return

    victory = battle.titan_hp <= 0
    explore_count = getattr(player_data, "explore_count", 0)
    gas_consumed = max(0, battle.initial_gas - battle.gas)

    if victory:
        rewards = battle.calculate_rewards(
            titan=battle.titan,
            character=battle.character,
            player=battle.player,
            explore_count=explore_count
        )

        total_participating = len(battle.participating_characters)
        xp_per_character = max(1, rewards["xp"] // total_participating) if total_participating > 0 else rewards["xp"]
        player_xp = rewards["xp"]

        participating_level_infos = []
        for char_name in battle.participating_characters:
            try:
                char_to_update = await db.get_character(user_id, char_name)
                if char_to_update:
                    old_level = char_to_update.level
                    old_stats = char_to_update.stats.dict() if char_to_update.stats else {}
                    level_info = char_to_update.add_xp(xp_per_character)
                    level_info['old_level'] = old_level
                    level_info['old_stats'] = old_stats
                    participating_level_infos.append((char_to_update, level_info))
            except Exception as e:
                logger.error(f"Error updating XP for participating character {char_name}: {e}")

        # Update current character in-memory for gas calculation
        battle.character.gas = max(0, battle.character.gas - gas_consumed)
        battle.character.max_gas = battle.character.gas
        battle.character.current_hp = battle.character.stats.HP # Heal after victory

        old_player_level = player_data.level
        old_player_stats = getattr(player_data, 'stats', {}) or {}
        player_level_info = player_data.add_xp(player_xp)
        player_level_info['old_level'] = old_player_level
        player_level_info['old_stats'] = old_player_stats
        player_data.marks = max(0, player_data.marks + rewards["marks"])
        player_data.valor = max(0, player_data.valor + rewards["valor"])
        player_data.explore_count = explore_count + 1
        
        buff_updates = {}
        if hasattr(player_data, 'double_gas_injector_uses') and player_data.double_gas_injector_uses > 0:
            player_data.double_gas_injector_uses -= 1
            buff_updates["double_gas_injector_uses"] = player_data.double_gas_injector_uses
        if hasattr(player_data, 'mark_surge_token_uses') and player_data.mark_surge_token_uses > 0:
            player_data.mark_surge_token_uses -= 1
            buff_updates["mark_surge_token_uses"] = player_data.mark_surge_token_uses
        if hasattr(player_data, 'frenzy_elixir_uses') and player_data.frenzy_elixir_uses > 0:
            player_data.frenzy_elixir_uses -= 1
            buff_updates["frenzy_elixir_uses"] = player_data.frenzy_elixir_uses

        if battle.is_boss_battle:
            victory_message = f"<b>🏆 GLORIOUS VICTORY! 🏆</b>\n<b>You have slain the mighty Boss Titan!</b>\n\n"
            reward_parts = [
                victory_message,
                f"⚡ <b>XP: +{rewards['xp']}</b>",
                f"🪙 <b>Marks: +{rewards['marks']}</b>"
            ]
        else:
            reward_parts = [
                f"<b>You have defeated {battle.get_titan_display_name()}!</b>\n",
                f"⚡ <b>XP: +{rewards['xp']}</b>",
                f"🪙 <b>Marks: +{rewards['marks']}</b>"
            ]
        
        if rewards['valor'] > 0:
            reward_parts.append(f"⚔️ <b>Valor: +{rewards['valor']}</b>")

        await query.edit_message_text("\n".join(reward_parts), parse_mode=ParseMode.HTML)

        # Send levelup messages separately
        participating_chars = [char for char, _ in participating_level_infos]
        participating_level_infos_only = [level_info for _, level_info in participating_level_infos]
        char_level_info = list(zip(participating_chars, participating_level_infos_only))
        await _send_level_up_messages(char_level_info, player_level_info, player_data, query.message.chat_id if query.message else None, context.bot.send_message)

        # Random drop system
        if random.random() < 0.09:
            drop = get_random_drop()
            if drop and drop.get('type') in ['bottle', 'cylinder']:
                player_data.gas += drop['amount']
                await db.batch_update_player(str(user_id), {"gas": player_data.gas})
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"🎁 <b>Random Drop!</b>\n{drop['message']}",
                    parse_mode=ParseMode.HTML
                )

        character_update_tasks = []
        for char, _ in participating_level_infos:
            update_data = {
                "xp": char.xp, "total_xp": char.total_xp, "level": char.level,
                "stats": char.stats.dict() if char.stats else {},
                "updated_at": datetime.now(timezone.utc)
            }
            if char.name == battle.character.name:
                update_data["gas"] = battle.character.gas
                update_data["max_gas"] = battle.character.max_gas
                update_data["current_hp"] = battle.character.current_hp
            
            character_update_tasks.append(db.batch_update_character(str(user_id), char.name, update_data))
        
        player_update_task = db.batch_update_player(
            str(user_id), 
            {
                "marks": player_data.marks, "valor": player_data.valor,
                "explore_count": player_data.explore_count, "xp": player_data.xp,
                "total_xp": player_data.total_xp, "level": player_data.level,
                "updated_at": datetime.now(timezone.utc), **buff_updates
            }
        )

        await asyncio.gather(*character_update_tasks, player_update_task, return_exceptions=True)

        participating_chars = [char for char, _ in participating_level_infos]
        participating_level_infos_only = [level_info for _, level_info in participating_level_infos]
        
        # Track explore stats for daily leaderboard (fire-and-forget)
        player_name = player_data.name if hasattr(player_data, 'name') else player_data.username if hasattr(player_data, 'username') else "Player"
        asyncio.create_task(track_explore_stats(user_id, player_name, battle_completed=True))
        
        asyncio.create_task(_process_post_battle_updates(
            db, player_data, participating_chars, participating_level_infos_only, user_id, 
            query.message.chat_id if query.message else None, context.bot.send_message, 
            rewards["marks"], battle, victory=True, player_level_info=player_level_info
        ))

    else: # Defeat
        # Try to switch to next team character
        switched = await battle.switch_character_on_death(db, user_id)
        
        if switched:
            # Character switched successfully, continue battle
            switch_message = f"🔄 {battle.character.name} steps in to continue the fight!"
            
            # Update UI with switch message
            status = battle.get_battle_status()
            battle_message = (
                f"<b>⚔️ BATTLE ⚔️</b>\n"
                f"{switch_message}\n\n"
                f"<b>| {battle.get_titan_display_name()} ({battle.titan.level}) |</b>\n"
                f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp}</b>\n"
                f"{status['titan_bar']}\n\n"
                f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>\n"
                f"<b>HP: {status['character_hp']}/{battle.character.stats.HP}</b>\n"
                f"{status['character_bar']}\n"
                f"<b>Gas: {status['gas']}/{battle.character.max_gas}</b>"
            )
            
            keyboard = await generate_ability_keyboard(battle, context)
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(battle_message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            except Exception:
                pass
            
            # Restart timeout
            battle.timeout_task = asyncio.create_task(battle_timeout(user_id, query, battle, context))
            return
        
        # No switch possible, end battle
        battle.character.gas = max(0, battle.character.gas - gas_consumed)
        battle.character.max_gas = battle.character.gas
        battle.character.current_hp = 0

        buff_updates = {}
        if hasattr(player_data, 'double_gas_injector_uses') and player_data.double_gas_injector_uses > 0:
            player_data.double_gas_injector_uses -= 1
            buff_updates["double_gas_injector_uses"] = player_data.double_gas_injector_uses
        if hasattr(player_data, 'frenzy_elixir_uses') and player_data.frenzy_elixir_uses > 0:
            player_data.frenzy_elixir_uses -= 1
            buff_updates["frenzy_elixir_uses"] = player_data.frenzy_elixir_uses

        defeat_message = (
            f"💀 <b>DEFEAT</b> 💀\n{battle.character.name} was defeated by the Boss Titan!"
            if battle.is_boss_battle
            else f"💀 <b>DEFEAT</b> 💀\n{battle.character.name} was defeated by {battle.get_titan_display_name()}!"
        )

        await query.edit_message_text(defeat_message, parse_mode=ParseMode.HTML)

        character_defeat_task = db.batch_update_character(
            str(user_id), battle.character.name, 
            {"gas": battle.character.gas, "max_gas": battle.character.max_gas, "current_hp": 0}
        )
        
        player_defeat_task = db.batch_update_player(
            str(user_id), {"explore_count": explore_count + 1, **buff_updates}
        )

        await asyncio.gather(character_defeat_task, player_defeat_task, return_exceptions=True)

        asyncio.create_task(_process_defeat_updates(
            db, player_data, user_id, query.message.chat_id if query.message else None, context.bot.send_message
        ))

    db.invalidate_battle_caches(user_id)
    cleanup_battle(user_id, "completed", battle)

async def _process_post_battle_updates(db, player_obj, participating_characters, participating_level_infos, user_id, chat_id, send_func,
                                      marks_reward, battle, victory=False, player_level_info=None):
    """Process post-battle updates for characters and player, including level ups and notifications."""
    try:
        from utils.monitor import track_player_action
        # Track battle end action
        track_player_action(int(user_id), player_obj.name, "🏁 Battle Ended", {
            "result": "Victory" if victory else "Defeat",
            "titan": battle.titan.name,
            "titan_level": battle.titan.level
        })
    except (ImportError, Exception):
        pass  # Silently fail for performance

    # Level up handling
    if victory:
        # Create level info tuples for the _send_level_up_messages function
        char_level_info = list(zip(participating_characters, participating_level_infos))
        
        # Still need to save the character and player objects after level up
        for char_obj, level_info in zip(participating_characters, participating_level_infos):
            try:
                if level_info and level_info.get('total_level_ups', 0) > 0:
                    await db.update_character(char_obj)
            except Exception as e:
                logger.error(f"Error saving character {char_obj.name if char_obj else 'N/A'}: {e}")
        
        try:
            if player_level_info and player_level_info.get('total_level_ups', 0) > 0:
                await db.save_player(player_obj)
        except Exception as e:
            logger.error(f"Error saving player {user_id}: {e}")

async def _process_defeat_updates(db, player_data, user_id, chat_id, send_func):
    """Process updates specifically for defeat scenarios."""
    try:
        # Track defeat in monitor
        from utils.monitor import track_player_action
        track_player_action(int(user_id), player_data.name, "💔 Defeated by Titan", {
            "titan": "Unknown",  # Titan info may not be available on defeat
            "result": "Defeat"
        })
    except (ImportError, Exception):
        pass  # Silently fail for performance

    # Send defeat message
    await send_func(
        chat_id=chat_id,
        text=f"💀 You have been defeated! The titan was too strong.",
        parse_mode=ParseMode.HTML
    )

async def _send_level_up_messages(char_level_info, player_level_info, player_obj, chat_id, send_func):
    """Send messages for level up rewards and updates."""
    if char_level_info:
        for char, level_info in char_level_info:
            # Check if character leveled up
            if level_info and level_info.get('total_level_ups', 0) > 0:
                level_ups = level_info.get('level_ups', [])
                for lv_up in level_ups:
                    old_level = lv_up.get('old_level', char.level - 1)
                    new_level = lv_up.get('new_level', char.level)
                    message = f"🎉 <b>{char.name} leveled up from {old_level} to {new_level}!</b>"
                    
                    # Show stat increases if available
                    stat_increases = lv_up.get('stat_increases', {})
                    if stat_increases:
                        message += "\n\n<b>Stat Increases:</b>"
                        for stat, increase in stat_increases.items():
                            if increase > 0:
                                message += f"\n   • {stat}: +{int(increase)}"
                    
                    await send_func(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.HTML
                    )
    
    # Player level up messages
    if player_level_info and player_level_info.get('total_level_ups', 0) > 0:
        level_ups = player_level_info.get('level_ups', [])
        for level_up in level_ups:
            old_level = level_up.get('old_level', player_obj.level - 1)
            new_level = level_up.get('new_level', player_obj.level)
            message = f"🎊 <b>You leveled up from {old_level} to {new_level}!</b>"
            
            # Add level up rewards
            rewards = level_up.get('rewards', {})
            if rewards.get('marks', 0) > 0 or rewards.get('valor', 0) > 0:
                message += f"\n\n<b>Level Up Rewards:</b>"
                if rewards.get('marks', 0) > 0:
                    message += f"\n🪙 Marks: +{rewards['marks']}"
                if rewards.get('valor', 0) > 0:
                    message += f"\n⚔️ Valor: +{rewards['valor']}"
                
            await send_func(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML
            )

async def battle_timeout(user_id: str, query, battle: 'BattleSystem', context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle battle timeout - ends the battle if no actions are taken within the limit."""
    user_id = str(user_id)
    
    try:
        # Wait for 3 minutes (180 seconds) before timing out
        await asyncio.sleep(180)
        
        # Check if battle is still active and not ended
        async with active_battles_lock:
            battle_instance = active_battles.get(user_id)
            if battle_instance and not battle_instance.battle_ended:
                # Force cleanup and mark as ended
                cleanup_battle(user_id, "timeout", battle_instance)
                
                # Also clean up any battle tracking data
                if f"titan_battle_started_{user_id}" in context.bot_data:
                    del context.bot_data[f"titan_battle_started_{user_id}"]
                
                try:
                    await query.edit_message_text(
                        "🕰️ <b>Battle Ended - Inactivity Timeout!</b>\n\n"
                        "Use /explore to find another titan.",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
    except asyncio.CancelledError:
        # This is expected when a new action is taken
        pass
    except Exception as e:
        logger.error(f"Error in battle_timeout: {e}")