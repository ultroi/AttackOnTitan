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
    def __init__(self, character: Character, titan: Titan, player: Optional[Player] = None):
        self.character = character
        self.titan = titan
        self.player = player
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
        self.participating_characters: set = {character.name}  

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

    # ---------- Context & Effects ----------
    def build_context(self, trigger: Optional[str] = None, ability: Optional[Ability] = None) -> Dict:
        """Build context for ability effect functions."""
        base_damage = (ability.base_damage + self.character.stats.ATK) if ability and ability.base_damage else 0
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
        character_data = get_character_data(self.character.character_type)
        messages = []
        if not character_data:
            return messages
        passive_abilities = getattr(character_data, "passive_abilities", [])
        for ability in passive_abilities:
            if not ability or not ability.name:
                continue
            if self.character.level < ability.level_required:
                continue
            if not (ability.is_unlocked or self.character.unlocked_abilities.get(ability.name, False)):
                continue
            try:
                if ability.effect_function:
                    context = self.build_context(trigger, ability)
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

    # ---------- Titan Turn Logic ----------
    def titan_attack(self) -> Tuple[int, str]:
        """Calculate titan attack damage and effects for this turn."""
        if self.titan_debuffs.get("stun", 0) > 0:
            self.titan_debuffs["stun"] -= 1
            return 0, f"{self.titan.name} is stunned and cannot attack this turn!"
        if self.titan_debuffs.get("delay", 0) > 0:
            self.titan_debuffs["delay"] -= 1
            return 0, f"{self.titan.name} is delayed and cannot attack this turn!"
        if self.buffs.get("dodge", 0) > 0 or self.trigger_states["dodge_count"] > 0:
            if self.buffs.get("dodge", 0) > 0:
                del self.buffs["dodge"]
            else:
                self.trigger_states["dodge_count"] = max(0, self.trigger_states["dodge_count"] - 1)
            
            messages = self.apply_passives("dodge")
            return 0, f"{self.character.name} dodged the attack!\n" + "\n".join(messages)
        
        # Enhanced damage calculation using character DEF more effectively
        base_damage = max(15, self.titan.level * 8 + 10)
        difficulty_multipliers = {"Easy": 0.7, "Normal": 1.0, "Hard": 1.4}
        base_damage = int(base_damage * difficulty_multipliers.get(self.titan.difficulty, 1.0))
        special_messages = []

        # Character DEF reduces damage more significantly
        def_reduction = min(0.8, self.character.stats.DEF / 300)  
        damage = int(base_damage * (1 - def_reduction))
        
        # SPD affects dodge chance
        spd_dodge_chance = min(0.25, self.character.stats.SPD / 400)  
        if random.random() < spd_dodge_chance:
            messages = self.apply_passives("dodge")
            return 0, f"{self.character.name} dodged the attack with lightning speed!\n" + "\n".join(messages)
        
        # Ensure minimum damage
        damage = max(5, damage + random.randint(5, 15))
        
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
        return damage, f"{self.titan.name} attacks, dealing {damage} damage to {self.character.name}.\n" + "\n".join(special_messages)

    # ---------- Ability Usage ----------
    def use_ability(self, ability_name: str) -> Tuple[int, str, Dict, bool]:
        self.keyboard_cache_invalid = True
        
        # Initialize with default values for speed
        damage = 0
        message = ""
        effects = {"items_dropped": [], "target_switched": False, "bleed_applied": False}
        
        if not self.character or not self.character.stats:
            return damage, "Error: Character stats not available", effects, False
            
        character_data = get_character_data(self.character.character_type)
        if not character_data:
            return damage, "Error: Character abilities not found", effects, False
            
        # Fast ability lookup using dictionary for speed
        ability_types = {
            "active": getattr(character_data, "active_abilities", []),
            "passive": getattr(character_data, "passive_abilities", []),
            "ultimate": getattr(character_data, "ultimate_abilities", [])
        }
        
        # Find ability quickly with optimized search
        ability = None
        for abilities in ability_types.values():
            for ab in abilities:
                if ab and ab.name == ability_name:
                    ability = ab
                    break
            if ability:
                break
                
        if not ability:
            return damage, f"Error: Ability {ability_name} not found", effects, False
            
        # Check cooldown and gas with early returns
        cooldown = self.ability_cooldowns.get(ability_name, 0)
        if cooldown > 0:
            return damage, f"{ability_name} is on cooldown for {cooldown} turns!", effects, False
            
        gas_cost = ability.gas_cost or 20
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
        character_data = get_character_data(self.character.character_type)
        if not character_data:
            return False
        for ability_type in ["active", "passive", "ultimate"]:
            abilities = getattr(character_data, f"{ability_type}_abilities", [])
            for ability in abilities:
                if not ability or not ability.name:
                    continue
                if self.character.level < ability.level_required:
                    continue
                if not (ability.is_unlocked or self.character.unlocked_abilities.get(ability.name, False)):
                    continue
                if ability.disabled_against_titans:
                    continue
                if self.ability_cooldowns.get(ability.name, 0) == 0 and self.gas >= (ability.gas_cost or 0):
                    return True
        return False

    # ---------- Turn & Status Updates ----------
    def update_cooldowns(self) -> None:
        self.keyboard_cache_invalid = True
        
        # Fast loop for cooldowns (minimize lookups)
        for ability_name in list(self.ability_cooldowns.keys()):
            if self.ability_cooldowns[ability_name] > 0:
                self.ability_cooldowns[ability_name] -= 1
        
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
        """Return current battle state for UI display (HP bars, buffs, debuffs, etc)."""
        character_max_hp = self.character.stats.HP
        titan_max_hp = self.titan.max_hp
        
        # Precompute percentages just once
        character_hp_percent = self.character_hp / character_max_hp
        titan_hp_percent = self.titan_hp / titan_max_hp
        
        # Fast bar generation (single operations instead of multiple string concatenations)
        char_bar_filled = int(character_hp_percent * 10)
        titan_bar_filled = int(titan_hp_percent * 10)
        character_bar = "█" * char_bar_filled + "▒" * (10 - char_bar_filled)
        titan_bar = "█" * titan_bar_filled + "▒" * (10 - titan_bar_filled)
        
        status_parts = [f"Turn: {self.turn + 1}", f"Difficulty: {self.titan.difficulty}"]
        
        # Add character stats display
        if self.character.stats:
            status_parts.append(f"⚔️ ATK: {self.character.stats.ATK} | 🛡️ DEF: {self.character.stats.DEF}")
            status_parts.append(f"🎯 ACC: {self.character.stats.ACC} | 🧠 INT: {self.character.stats.INT} | ⚡ SPD: {self.character.stats.SPD}")
        
        status_message = "\n".join(status_parts)
        
        return {
            "character_hp": int(self.character_hp),
            "titan_hp": int(self.titan_hp),
            "gas": int(self.gas),
            "character_bar": character_bar,
            "titan_bar": titan_bar,
            "status_message": status_message
        }

    def calculate_rewards(self, titan: Titan, character: Character, player: Optional[Player], explore_count: int) -> Dict:
        """Calculate rewards for defeating the titan (XP, marks, valor) - simplified, no difficulty system."""
        xp = max(1, random.randint(100, 180))
        
        if player and hasattr(player, 'frenzy_elixir_uses') and player.frenzy_elixir_uses > 0:
            xp *= 3
        
        marks = max(1, random.randint(70, 100) + (titan.level * 2))
        
        if player and hasattr(player, 'mark_surge_token_uses') and player.mark_surge_token_uses > 0:
            marks *= 2
        
        valor = 0
        if random.random() < 0.0001: 
            valor = 1
            
        return {
            "xp": xp,
            "marks": marks,
            "crystal": 0,  
            "valor": valor,
        }

# =========================
# UTILITY FUNCTIONS
# =========================

def calculate_gas_consumption(titan: Titan) -> int:
    """Calculate gas consumption based on titan difficulty."""
    base_gas = 1000
    difficulty_modifiers = {"Easy": -200, "Normal": 0, "Hard": 500}
    return base_gas + difficulty_modifiers.get(titan.difficulty, 0)


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
    
    if not battle.keyboard_cache_invalid and battle.keyboard_cache:
        return battle.keyboard_cache

    keyboard = []
    
    character_data = get_character_data(battle.character.character_type)
    if not character_data:
        logger.warning(f"No character data found for {battle.character.character_type}")
        keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
        battle.keyboard_cache = keyboard
        battle.keyboard_cache_invalid = False
        return keyboard
    
    shop_items = context.bot_data.get("shop_items") or {}
    
    prefixes = {
        "active": "⚔️",
        "passive": " ",
        "ultimate": "✨"
    }
    
    # Fast ability button creation in a single loop
    ability_buttons = []
    for ability_type in ["active", "passive", "ultimate"]:
        abilities = getattr(character_data, f"{ability_type}_abilities", [])
        prefix = prefixes[ability_type]
        
        for ability in abilities:
            if ability_type == "passive" and not getattr(ability, 'show_as_button', False):
                continue

            if battle.character.level >= ability.level_required:
                cooldown = battle.ability_cooldowns.get(ability.name, 0)
                gas_cost = ability.gas_cost or 20
                
                if cooldown > 0:
                    button_text = f"{prefix} {ability.name} ({cooldown}t)"
                    callback_data = f"cooldown_{ability.name}"
                elif battle.gas < gas_cost:
                    button_text = f"{prefix} {ability.name} (Low Gas)"
                    callback_data = f"lowgas_{ability.name}"
                else:
                    button_text = f"{prefix} {ability.name}"
                    callback_data = f"ability_{ability.name}"
                
                ability_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.extend(ability_buttons)

    # Get weapon info
    weapon = battle.get_equipped_weapon(shop_items)
    
    # Add attack button based on gas
    attack_gas_cost = 20
    if battle.player and hasattr(battle.player, 'double_gas_injector_uses') and battle.player.double_gas_injector_uses > 0:
        attack_gas_cost = 10
    
    attack_row = []
    if battle.gas >= attack_gas_cost:
        attack_text = "🗡️ Attack"
        if weapon:
            attack_text = f"🗡️ Attack ({weapon.get('name', 'Weapon')})"
        attack_row.append(InlineKeyboardButton(attack_text, callback_data="action_basic_attack"))
    else:
        attack_text = "🗡️ Attack (No Gas)"
        if weapon:
            attack_text = f"🗡️ Attack ({weapon.get('name', 'Weapon')}) (No Gas)"
        attack_row.append(InlineKeyboardButton(attack_text, callback_data="lowgas_attack"))
    
    if attack_row:
        keyboard.append(attack_row)

    # Add run and switch buttons in the same row
    run_switch_row = [InlineKeyboardButton("🏃 Run", callback_data="action_run")]
    if battle.player and hasattr(battle.player, 'team') and battle.player.team and len(battle.player.team) > 1:
        current_name = battle.character.name
        if any(team_member.character_name != current_name for team_member in battle.player.team):
            run_switch_row.append(InlineKeyboardButton("🔄 Switch", callback_data="action_switch"))
            
    keyboard.append(run_switch_row)
    
    # Cache the keyboard for future use
    battle.keyboard_cache = keyboard
    battle.keyboard_cache_invalid = False
    
    return keyboard

# =========================
# ASYNC HANDLERS (TELEGRAM)
# =========================

async def handle_battle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the start of a battle."""
    # Performance monitoring
    start_time = time.time()
    
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    # Immediately answer callback to improve user experience
    try:
        await query.answer()
    except Exception:
        pass
    
    # Quick validation of battle ID
    callback_data = query.data
    user_id = str(update.effective_user.id)
    
    # Check if user is already in battle
    async with active_battles_lock:
        if user_id in active_battles:
            try:
                await query.answer("You're already in a battle! Finish it first.", show_alert=True)
            except Exception:
                pass
            return
    
    current_battle_id = context.bot_data.get(f"active_battle_id_{user_id}")
    
    # Validate that this is the correct battle ID
    if callback_data != current_battle_id:
        try:
            await query.answer("This battle button has expired. Please /explore again.", show_alert=True)
        except Exception:
            pass
        logger.info(f"Battle ID mismatch for user {user_id}: {callback_data} != {current_battle_id}")
        return
    
    # Immediately invalidate the battle ID to prevent duplicate use
    context.bot_data[f"active_battle_id_{user_id}"] = f"used_{current_battle_id}_{time.time()}"
    
    # Validate battle format
    if not callback_data or not callback_data.startswith("battle_"):
        await query.edit_message_text("Invalid battle request.")
        return
    
    # Cancel any pending titan timeouts
    titan_timeout_key = f"titan_timeout_{user_id}"
    titan_timeout_task = context.bot_data.pop(titan_timeout_key, None)
    if titan_timeout_task and not titan_timeout_task.done():
        titan_timeout_task.cancel()
    
    # Get database reference
    db = context.bot_data.get("db")
    if not db:
        logger.error("Database not initialized")
        await query.edit_message_text("Internal error: Database not initialized.")
        return
    
    # Start multiple async tasks in parallel for better performance
    titan_task = db.get_titan(user_id)
    
    # Initialize user data cache if needed (faster than checking each time)
    if not hasattr(context, "user_data") or context.user_data is None:
        context.user_data = {}
    
    # Set up battle cache efficiently
    if "battle_cache" not in context.user_data:
        context.user_data["battle_cache"] = {}
    battle_cache = context.user_data["battle_cache"]
    
    # Prepare player data fetch - use cached if available
    player_data_task = None
    if not battle_cache.get("player_data"):
        # Use get_player for both DB and memory
        player_data_task = db.get_player(user_id)
    
    # Clear any existing titan cache for this user to prevent data leakage between battles
    if f"last_titan_data_{user_id}" in context.bot_data:
        del context.bot_data[f"last_titan_data_{user_id}"]
    
    # Wait for titan data
    titan_obj = await titan_task
    if not titan_obj:
        # Use cached titan data if available or show error
        cached_titan_data = context.bot_data.get(f"last_titan_data_{user_id}")
        if not cached_titan_data:
            # Minimized logging for better performance
            await query.edit_message_text("⚠️ This titan encounter has expired. Please use /explore to find a new titan.")
            return
        titan_data = cached_titan_data
    else:
        titan_data = context.bot_data.get(f"last_titan_data_{user_id}", titan_obj.dict())
    
    # Create titan object
    titan = Titan(**titan_data)
    
    # Get player data (either from cache or database)
    if player_data_task:
        player_data = await player_data_task
        battle_cache["player_data"] = player_data
    else:
        player_data = battle_cache["player_data"]
    
    # Validate player data - check if it's a dictionary or Player object
    if not player_data:
        await query.edit_message_text("Error: Player data not found.")
        return
        
    # Handle both dict and Player object cases
    if isinstance(player_data, dict):
        if not player_data.get('team'):
            await query.edit_message_text("Error: No character in your team.")
            return
        team = player_data['team']
    else:
        # It's a Player object
        if not hasattr(player_data, 'team') or not player_data.team:
            await query.edit_message_text("Error: No character in your team.")
            return
        team = player_data.team
    
    # Get character name efficiently from either dict or Player object
    team_member = team[0]
    character_name = team_member['character_name'] if isinstance(team_member, dict) else getattr(team_member, 'character_name', team_member)
    
    # Clear all battle-related caches BEFORE starting battle to ensure fresh data
    db.invalidate_battle_caches(user_id)
    
    # Get character data FRESH from database (bypass cache for battle start)
    try:
        character = await db.get_character_fresh(user_id, character_name)
        if not character:
            await query.edit_message_text(f"Error: Character {character_name} not found.")
            return
        battle_cache["character"] = character
    except Exception as e:
        logger.error(f"Failed to get character {character_name} for user {user_id}: {e}")
        await query.edit_message_text(f"Error: Could not load character data.")
        return
    
    # Validate character
    if not character:
        await query.edit_message_text(f"Error: Character {character_name} not found.")
        return
    
    # Ensure character HP is valid and not from old cache
    if character.current_hp <= 0 or character.current_hp > character.stats.HP:
        logger.warning(f"Character {character_name} has invalid HP ({character.current_hp}), resetting to max")
        character.current_hp = character.stats.HP
    
    # Create player object and battle system
    if player_data:
        if isinstance(player_data, Player):
            player = player_data
        else:
            # Sanitize player data before creating Player object
            from database.db import sanitize_player_data
            player_data = sanitize_player_data(player_data)
            player = Player(**player_data)
    else:
        player = None
    battle = BattleSystem(character, titan, player)
    
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
    
    # Add battle to active battles
    async with active_battles_lock:
        # Check if user is already in a PVP battle before starting titan battle
        try:
            from game.pvp_system import active_pvp_battles
            if user_id in active_pvp_battles:
                await query.edit_message_text("⚔️ You are currently in a PVP battle! Complete it first before battling titans.")
                return
        except ImportError:
            pass  
        
        active_battles[user_id] = battle
    
    # Reset explore spam count atomically
    if "explore_spam_count" in context.bot_data:
        context.bot_data["explore_spam_count"][user_id] = 0
    
    # Track player action in background for better performance
    asyncio.create_task(_track_battle_start(user_id, update.effective_user, battle))
    
    # Generate keyboard buttons
    keyboard = await generate_ability_keyboard(battle, context)
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Get battle status once
    status = battle.get_battle_status()
    
    # Build message efficiently with array join
    message_parts = [
        "<b>⚔️ BATTLE ⚔️</b>\n",
        emergency_heal_message,  # Add emergency heal message if any
        "",  # Empty line for spacing
        f"<b>| {battle.titan.name} ({battle.titan.level}) |</b>",
        f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp}</b>",
        f"{status['titan_bar']}",
        "",  # Empty line for spacing
        f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>",
        f"<b>HP: {status['character_hp']}/{battle.character.stats.HP}</b>",
        f"{status['character_bar']}",
        f"<b>Gas: {status['gas']}/{battle.character.max_gas}</b>"
    ]
    
    # Join all parts at once for better performance
    battle_message = "\n".join(message_parts)
    
    chat_id = None
    if query.message:
        try:
            # Try to get chat_id in a safe way
            if hasattr(query.message, 'chat_id'):
                chat_id = getattr(query.message, 'chat_id', None)
            elif hasattr(query.message, 'chat') and query.message.chat:
                chat_id = getattr(query.message.chat, 'id', None)
        except (AttributeError, TypeError):
            pass
    
    if chat_id:
        # Send the battle UI in a new message
        await context.bot.send_message(
            chat_id=chat_id,
            text=battle_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    # Start timeout in background and set it to battle
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
    """Handle battle actions with titan response. Optimized for performance and clarity."""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    user_id = str(update.effective_user.id)

    # Enhanced duplicate prevention - track both action time AND callback ID
    action_time_key = f"battle_action_time_{user_id}"
    last_action_time = context.bot_data.get(action_time_key, 0)
    current_time = time.time()

    # Stricter anti-spam: prevent actions within 500ms
    if current_time - last_action_time < 0.5:  # 500ms cooldown
        try:
            await query.answer("Please wait before performing another action...", show_alert=False)
        except Exception:
            pass
        return
    
    # Track callback ID to prevent duplicate processing of same callback
    callback_id = query.id
    last_callback_key = f"last_battle_callback_{user_id}"
    last_callback_id = context.bot_data.get(last_callback_key)
    
    if last_callback_id == callback_id:
        logger.info(f"Duplicate callback {callback_id} detected for user {user_id}, ignoring")
        try:
            await query.answer("Action already processed", show_alert=False)
        except Exception:
            pass
        return
    
    # Update tracking
    context.bot_data[action_time_key] = current_time
    context.bot_data[last_callback_key] = callback_id
    
    try:
        await query.answer()
    except Exception:
        pass

    async with active_battles_lock:
        battle = active_battles.get(user_id)
        if not battle or battle.battle_ended:
            try:
                # Use a more generic message that doesn't require editing the original
                await query.answer("This battle has already ended.", show_alert=True)
            except Exception:
                pass
            return

    # Cancel the previous timeout task
    if battle.timeout_task and not battle.timeout_task.done():
        battle.timeout_task.cancel()

    battle.keyboard_cache_invalid = True
    full_message = []
    action = query.data

    # --- Action Dispatcher ---
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
        # This action will just fall through to regenerating the UI
        pass
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
    elif action.startswith("cooldown_") or action.startswith("lowgas_"):
        # Provide feedback for non-usable actions without processing a turn
        try:
            if action.startswith("cooldown_"):
                ability_name = action.split('_', 1)[1]
                cooldown = battle.ability_cooldowns.get(ability_name, 0)
                await query.answer(f"{ability_name} is on cooldown for {cooldown} more turns!", show_alert=True)
            else:
                await query.answer(f"Not enough gas! Refill with /char {battle.character.name}", show_alert=True)
        except Exception:
            pass
        # We don't process a turn, just show an alert. The UI doesn't need a full redraw.
        # We must restart the timeout, however.
        battle.timeout_task = asyncio.create_task(battle_timeout(user_id, query, battle, context))
        return

    # --- Main Battle Flow ---
    if battle.titan_hp <= 0:
        await handle_battle_end(query, battle, user_id, context)
        return

    # Titan's turn only if an action other than just viewing cooldowns was taken
    if not (action.startswith("cooldown_") or action.startswith("lowgas_")):
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
    """Helper to generate and send the updated battle UI."""
    keyboard = await generate_ability_keyboard(battle, context)
    reply_markup = InlineKeyboardMarkup(keyboard)
    status = battle.get_battle_status()

    message_parts = [
        "<b>⚔️ BATTLE ⚔️</b>\n",
        "\n".join(filter(None, full_message)),
        "",
        f"<b>| {battle.titan.name} ({battle.titan.level}) |</b>",
        f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp}</b>",
        f"{status['titan_bar']}",
        "",
        f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>",
        f"<b>HP: {status['character_hp']}/{battle.character.stats.HP}</b>",
        f"{status['character_bar']}",
        f"<b>Gas: {status['gas']}/{battle.character.max_gas}</b>"
    ]
    battle_message = "\n".join(message_parts)

    try:
        await query.edit_message_text(
            text=battle_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Failed to edit battle message: {e}")

    # Start a new timeout task
    battle.timeout_task = asyncio.create_task(battle_timeout(str(query.from_user.id), query, battle, context))

async def _handle_run_action(battle, user_id, context):
    """Handles the logic for a player attempting to run from battle."""
    if random.random() < 0.7:  # 70% success
        cleanup_battle(user_id, "escaped", battle)
        return True, f"🏃💨 {battle.character.name} successfully escaped!"
    else:
        return False, f"❌ {battle.character.name} failed to escape! The titan blocks your path!"

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

    db = context.bot_data["db"]
    target_character = await db.get_character(user_id, target_name)
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

    battle.apply_passives("battle_start") 

    return f"🔄 Switched from {old_name} to {target_name}!"

async def _handle_basic_attack(battle, context):
    """Handles the logic for a basic attack."""
    gas_cost = 20
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
                    level_info = char_to_update.add_xp(xp_per_character)
                    participating_level_infos.append((char_to_update, level_info))
            except Exception as e:
                logger.error(f"Error updating XP for participating character {char_name}: {e}")

        # Update current character in-memory for gas calculation
        battle.character.gas = max(0, battle.character.gas - gas_consumed)
        battle.character.max_gas = battle.character.gas
        battle.character.current_hp = battle.character.stats.HP # Heal after victory

        player_level_info = player_data.add_xp(player_xp)
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

        reward_parts = [
            f"<b>You have defeated {battle.titan.name}!</b>\n",
            f"⚡ <b>XP: +{rewards['xp']}</b>",
            f"🪙 <b>Marks: +{rewards['marks']}</b>"
        ]
        if rewards['valor'] > 0:
            reward_parts.append(f"⚔️ <b>Valor: +{rewards['valor']}</b>")

        await query.edit_message_text("\n".join(reward_parts), parse_mode=ParseMode.HTML)

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
        
        asyncio.create_task(_process_post_battle_updates(
            db, player_data, participating_chars, participating_level_infos_only, user_id, 
            query.message.chat_id if query.message else None, context.bot.send_message, 
            rewards["marks"], battle, victory=True, player_level_info=player_level_info
        ))

    else: # Defeat
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

        await query.edit_message_text(
            f"💀 <b>DEFEAT</b> 💀\n{battle.character.name} was defeated by {battle.titan.name}!",
            parse_mode=ParseMode.HTML
        )

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
        level_up_messages = []
        
        # Check for character level ups
        for char_obj, level_info in participating_level_infos:
            try:
                if level_info and level_info.get('leveled_up'):
                    level_up_messages.append(f"🎉 {char_obj.name} leveled up to {char_obj.level}!")
                    # Character object is already updated by add_xp, just need to save
                    await db.update_character(char_obj)
            except Exception as e:
                logger.error(f"Error processing level up for character {char_obj.name if char_obj else 'N/A'}: {e}")
        
        # Check for player level up
        try:
            if player_level_info and player_level_info.get('leveled_up'):
                level_up_messages.append(f"🎊 You leveled up to {player_obj.level}!")
                # Player object is already updated by add_xp, just need to save
                await db.save_player(player_obj)
        except Exception as e:
            logger.error(f"Error processing player level up for user {user_id}: {e}")
        
        # Send level up messages
        if level_up_messages:
            await send_func(
                chat_id=chat_id,
                text="\n".join(level_up_messages),
                parse_mode=ParseMode.HTML
            )

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

async def _send_level_up_messages(char_level_info, player_level_info, character, player_obj, chat_id, send_func):
    """Send messages for level up rewards and updates."""
    messages = []
    if char_level_info:
        for char, level_info in char_level_info:
            if level_info:
                messages.append(f"🎉 {char.name} leveled up to {char.level}!")
                # Add stat increase details
                if hasattr(level_info, 'stat_increases'):
                    for stat, increase in level_info.stat_increases.items():
                        messages.append(f"   • {stat}: +{increase}")
    
    if player_level_info:
        messages.append(f"🎊 You leveled up to {player_obj.level}!")
        # Add player stat increases if available
        if hasattr(player_level_info, 'stat_increases'):
            for stat, increase in player_level_info.stat_increases.items():
                messages.append(f"   • {stat}: +{increase}")

    # Send all messages at once
    if messages:
        await send_func(
            chat_id=chat_id,
            text="\n".join(messages),
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
                        "You didn't take any action for 3 minutes.\n"
                        "Your character escaped from the battle.\n\n"
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
