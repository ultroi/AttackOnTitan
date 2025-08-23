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
import logging
from datetime import datetime, timezone
import time

logger = logging.getLogger(__name__)

# =========================
# GLOBALS & UTILITIES
# =========================

# Global dictionary to track active battles
active_battles: Dict[str, 'BattleSystem'] = {}
active_battles_lock = asyncio.Lock()

# =========================
# BATTLE SYSTEM CLASS
# =========================

class BattleSystem:
    def get_equipped_weapon(self, shop_items):
        if self.character.equipped_weapon and self.character.equipped_weapon in shop_items:
            return shop_items[self.character.equipped_weapon]
        return None
    """
    Manages a battle between a character and a titan, handling gas, HP, abilities, buffs, debuffs, and turn logic.
    """
    def __init__(self, character: Character, titan: Titan, player: Optional[Player] = None):
        self.character = character
        self.titan = titan
        self.player = player
        # Initialize battle state
        self.character_hp: int = character.current_hp
        self.titan_hp: int = titan.max_hp
        self.gas: int = character.gas
        self.character_gas: int = character.max_gas  # Max gas for display and checks
        self.max_gas: int = min(character.max_gas, 5000)  # Cap max gas at 5000
        self.character.max_gas = self.max_gas  # Sync with character
        self.character.stats = character.stats or CharacterStats(HP=650, ATK=25, DEF=10, SPD=10, ACC=10, INT=10)
        self.ability_cooldowns: Dict[str, int] = {
            ability.name: 0 for ability in (
                (character.active_abilities or []) +
                (character.passive_abilities or []) +
                (character.ultimate_abilities or [])
            )
        }
        self.buffs: Dict[str, Any] = {}
        self.debuffs: Dict[str, int] = {}  # Character debuffs
        self.titan_debuffs: Dict[str, int] = {}  # Titan debuffs
        self.turn: int = 0
        # Add keyboard cache for performance optimization
        self.keyboard_cache = None
        self.keyboard_cache_invalid = True
        self.trigger_states: Dict[str, Any] = {
            "first_damage_taken": False,
            "dodge_count": 0,
            "fear_counter": 0,
            "focused_turns": 0,
            "ally_died": False
        }
        # Auto-unlock abilities based on current level
        self.character._check_ability_unlocks()
        self.apply_passives("battle_start")
        self.timeout_task: Optional[asyncio.Task] = None
        self._is_disposed: bool = False
        self.battle_ended: bool = False
        self.initial_gas: int = character.gas  # Store initial gas at battle start

    # ---------- Resource Management ----------
    def dispose(self) -> None:
        """Clean up battle resources and reset state."""
        if self._is_disposed:
            return
        self._is_disposed = True
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()
        self.timeout_task = None
        self.buffs.clear()
        self.debuffs.clear()
        self.titan_debuffs.clear()
        self.ability_cooldowns.clear()
        self.trigger_states.clear()

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
            # ...existing code...
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
                self.titan_debuffs["damage_reduction"] = 1  # Use int instead of float
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
            # Process dodge from Golden Hour Reflex
            if self.buffs.get("dodge", 0) > 0:
                # Remove dodge after use (one-time dodge)
                del self.buffs["dodge"]
            else:
                self.trigger_states["dodge_count"] = max(0, self.trigger_states["dodge_count"] - 1)
            
            # Apply any passive abilities triggered by dodge
            messages = self.apply_passives("dodge")
            return 0, f"{self.character.name} dodged the attack!\n" + "\n".join(messages)
        base_damage = max(15, self.titan.level * 8 + 10)
        damage_multipliers = {"Easy": 0.7, "Normal": 1.0, "Hard": 1.4}
        base_damage = int(base_damage * damage_multipliers.get(self.titan.difficulty, 1.0))
        special_messages = []

        # Calculate final damage
        damage = int(base_damage * (1 - min(0.75, self.character.stats.DEF / 250))) + 25  # Ensure at least 25 extra damage
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
        self.trigger_states["focused_turns"] = min(3, self.trigger_states["focused_turns"] + 1)
        messages = self.apply_passives("titan_attack")
        special_messages.extend(messages)
        return damage, f"{self.titan.name} attacks, dealing {damage} damage to {self.character.name}.\n" + "\n".join(special_messages)

    # ---------- Ability Usage ----------
    def use_ability(self, ability_name: str) -> Tuple[int, str, Dict]:
        """Use a character ability with gas cost and apply its effect."""
        # Invalidate keyboard cache since ability usage changes state
        self.keyboard_cache_invalid = True
        
        # Initialize with default values for speed
        damage = 0
        message = ""
        effects = {"items_dropped": [], "target_switched": False, "bleed_applied": False}
        
        # Fast-fail checks for critical dependencies
        if not self.character or not self.character.stats:
            return damage, "Error: Character stats not available", effects
            
        # Get character data with early return for failures
        character_data = get_character_data(self.character.character_type)
        if not character_data:
            return damage, "Error: Character abilities not found", effects
            
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
            return damage, f"Error: Ability {ability_name} not found", effects
            
        # Check cooldown and gas with early returns
        cooldown = self.ability_cooldowns.get(ability_name, 0)
        if cooldown > 0:
            return damage, f"{ability_name} is on cooldown for {cooldown} turns!", effects
            
        gas_cost = ability.gas_cost or 20
        if self.gas < gas_cost:
            return damage, f"Not enough gas to use {ability_name}!", effects
            
        # Apply gas cost
        self.gas -= gas_cost
        self.character_gas = self.gas  # Sync with character
        
        # Build context and apply effect
        ctx = self.build_context("ability_use", ability)
        try:
            if ability.effect_function:
                effect = ability.effect_function(ctx)
                if effect:
                    self.apply_effect(effect)
                    message = effect.message or f"{ability_name} used successfully!"
                    
                    # Optimized effects extraction with defaults
                    effects = {
                        "items_dropped": getattr(effect, 'items_dropped', []),
                        "target_switched": getattr(effect, 'target_switched', False),
                        "bleed_applied": getattr(effect, 'bleed_applied', False)
                    }
        except Exception as e:
            logger.error(f"Error applying ability {ability_name}: {e}")
            return damage, f"Error using {ability_name}", effects
            
        # Set cooldown and return
        self.ability_cooldowns[ability_name] = ability.cooldown or 1
        return damage, message, effects

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
        """Update ability cooldowns, buffs, debuffs, and apply periodic effects (burn, bleed, etc)."""
        # Mark keyboard cache as invalid since state will change
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
        
        # Batch delete operations for better performance
        for debuff in to_remove:
            del self.titan_debuffs[debuff]
                    
        # Process special buffs that need to be tracked
        if "reflex_counter" in self.buffs:
            self.buffs["reflex_counter"] -= 1
            if self.buffs["reflex_counter"] <= 0:
                del self.buffs["reflex_counter"]
                # Remove crit_rate buff when reflex_counter expires
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
            bleed_damage = max(10, character_atk // 2)
            self.titan_hp = max(0, self.titan_hp - bleed_damage)
            self.titan_debuffs["bleed"] -= 1
            if self.titan_debuffs["bleed"] <= 0:
                del self.titan_debuffs["bleed"]

    def get_battle_status(self) -> Dict:
        """Return current battle state for UI display (HP bars, buffs, debuffs, etc)."""
        # Fast path calculations - avoid unnecessary divisions and operations
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
        
        # Build status message efficiently with array join (faster than string concatenation)
        status_parts = [f"Turn: {self.turn + 1}", f"Difficulty: {self.titan.difficulty}"]
        
        # Add status info conditionally - only if they exist
        if self.titan_debuffs:
            # Optimize list comprehension for speed
            debuffs = []
            for k, v in self.titan_debuffs.items():
                debuffs.append(f"{k}({int(v)})" if isinstance(v, (int, float)) else k)
            status_parts.append(f"🔽 Titan debuffs: {', '.join(debuffs)}")
        
        if self.buffs:
            buffs = []
            for k, v in self.buffs.items():
                if k != "items_dropped":
                    if isinstance(v, (int, float)) and v > 1:
                        buffs.append(f"{k}({int(v)})")
                    else:
                        buffs.append(k)
            if buffs:
                status_parts.append(f"🔼 Character buffs: {', '.join(buffs)}")
        
        items_dropped = self.buffs.get("items_dropped")
        if items_dropped:
            status_parts.append(f"💎 Items available: {', '.join(items_dropped)}")
        
        if self.debuffs:
            debuffs_char = []
            for k, v in self.debuffs.items():
                debuffs_char.append(f"{k}({int(v)})" if isinstance(v, (int, float)) else k)
            status_parts.append(f"🔥 Character debuffs: {', '.join(debuffs_char)}")
        
        # Join all parts at once - more efficient than incremental additions
        status_message = "\n".join(status_parts)
        
        # Return optimized dictionary with integer casting done once
        return {
            "character_hp": int(self.character_hp),
            "titan_hp": int(self.titan_hp),
            "gas": int(self.gas),
            "character_bar": character_bar,
            "titan_bar": titan_bar,
            "status_message": status_message
        }

    def calculate_rewards(self, titan: Titan, character: Character, player: Optional[Player], explore_count: int) -> Dict:
        """Calculate rewards for defeating the titan (XP, marks, crystals, valor)."""
        base_xp = generate_titan_xp(titan.level, titan.difficulty)
        performance_multiplier = 1.0 + (0.2 if self.turn < 5 else 0) + (0.15 if self.character_hp / character.stats.HP > 0.8 else 0) + (0.3 if titan.difficulty == "Hard" else 0)
        rewards = {
            "xp": int(base_xp * performance_multiplier),
            "marks": random.randint(70, 120) + (titan.level * 2),
            "crystal": 0,
            "valor": 0,
        }
        difficulty_bonuses = {
            "Easy": {"mark_bonus": 10, "valor_chance": 0.35, "crystal_chance": 0.065},
            "Normal": {"mark_bonus": 25, "valor_chance": 0.40, "crystal_chance": 0.10},
            "Hard": {"mark_bonus": 50, "valor_chance": 0.60, "crystal_chance": 0.25}
        }
        bonus = difficulty_bonuses.get(titan.difficulty, difficulty_bonuses["Normal"])
        rewards["marks"] += bonus["mark_bonus"]
        if random.random() < bonus["valor_chance"] and player:
            required_explore = max(5, 30 - (titan.level // 2))
            if explore_count >= required_explore:
                valor_amount = random.randint(8, 20) + (titan.level // 2)
                if titan.difficulty == "Hard":
                    valor_amount += 1
                rewards["valor"] = int(valor_amount)
        if random.random() < bonus["crystal_chance"]:
            required_explore = max(1, 15 - (titan.level // 3))
            if explore_count >= required_explore:
                crystal_amount = random.randint(1, 3) + (titan.level // 5)
                if titan.difficulty == "Hard":
                    crystal_amount = int(crystal_amount * 1.5)
                rewards["crystal"] = crystal_amount
        return rewards

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
                    # Mark battle as ended before disposal
                    battle_instance.battle_ended = True
                    battle_instance.dispose()
                except Exception as e:
                    logger.warning(f"Error disposing battle for user {user_id}: {e}")
                if user_id in active_battles:
                    del active_battles[user_id]
                logger.info(f"Battle {result} for user {user_id}. Active battles: {len(active_battles)}")
        try:
            from utils.monitor import remove_player_activity
            remove_player_activity(int(user_id))
        except ImportError:
            pass
    asyncio.create_task(_cleanup())


async def generate_ability_keyboard(battle: 'BattleSystem', context: ContextTypes.DEFAULT_TYPE) -> List[List[InlineKeyboardButton]]:
    """Generate keyboard buttons for valid abilities and actions."""
    # Use cached keyboard if valid (fast path)
    if not battle.keyboard_cache_invalid and battle.keyboard_cache:
        return battle.keyboard_cache
    
    keyboard = []
    def obfuscate_text(text):
        # Insert zero-width space (\u200B) between each character - optimized for speed
        # Pre-allocate array instead of concatenation for better performance
        chars = []
        for char in text:
            chars.append(char)
            chars.append('\u200B')
        return ''.join(chars)
        
    # Get character data with fast path for invalid data
    character_data = get_character_data(battle.character.character_type)
    if not character_data:
        logger.warning(f"No character data found for {battle.character.character_type}")
        keyboard.append([InlineKeyboardButton(obfuscate_text("🏃 Run"), callback_data="action_run")])
        battle.keyboard_cache = keyboard
        battle.keyboard_cache_invalid = False
        return keyboard
    
    # Get shop items once
    shop_items = context.bot_data.get("shop_items") or {}
    equipped_weapon = getattr(battle.character, 'equipped_weapon', None)
    
    # Reduce debug logging for better performance
    if random.random() < 0.1:  # Only log 10% of the time
        logger.info(f"[DEBUG] Equipped weapon: {equipped_weapon}")
        logger.info(f"[DEBUG] Shop items count: {len(shop_items)}")
    
    # Prefetch common ability attributes for faster access
    prefixes = {
        "active": "⚔️",
        "passive": " ",
        "ultimate": "✨"
    }
    
    # Fast ability button creation
    for ability_type in ["active", "passive", "ultimate"]:
        abilities = getattr(character_data, f"{ability_type}_abilities", [])
        prefix = prefixes[ability_type]
        
        for ability in abilities:
            # Quick filtering
            if not ability or not ability.name:
                continue
                
            if battle.character.level < ability.level_required:
                continue
                
            is_unlocked = ability.is_unlocked or battle.character.unlocked_abilities.get(ability.name, False)
            if not is_unlocked or ability.disabled_against_titans:
                continue
                
            gas_cost = ability.gas_cost or 0
            ability_display_name = ability.name
            cooldown = battle.ability_cooldowns.get(ability.name, 0)
            
            # Optimized button creation - fewer conditionals
            if cooldown == 0 and battle.gas >= gas_cost:
                keyboard.append([InlineKeyboardButton(
                    obfuscate_text(f"{prefix} {ability_display_name}"),
                    callback_data=f"ability_{ability.name}"
                )])
            elif cooldown > 0:
                keyboard.append([InlineKeyboardButton(
                    obfuscate_text(f"⏳ {prefix} {ability_display_name} (CD: {cooldown})"),
                    callback_data=f"cooldown_{ability.name}"
                )])
            elif gas_cost > 0:  # Implies battle.gas < gas_cost from earlier condition
                keyboard.append([InlineKeyboardButton(
                    obfuscate_text(f"⛽ {prefix} {ability_display_name} (Need {gas_cost} gas)"),
                    callback_data=f"lowgas_{ability.name}"
                )])
    
    # Only refresh character from DB if really needed - check if too long since last refresh
    now = time.time()
    last_refresh = getattr(battle, 'last_character_refresh', 0)
    if now - last_refresh > 60:  # Only refresh once per minute max
        db = context.bot_data.get("db") or Database()
        refreshed_character = None
        try:
            if hasattr(battle.character, 'user_id') and hasattr(battle.character, 'name'):
                refreshed_character = await db.get_character(int(battle.character.user_id), battle.character.name)
        except Exception:
            pass
        if refreshed_character:
            battle.character = refreshed_character
            battle.last_character_refresh = now
    
    # Get weapon info
    weapon = battle.get_equipped_weapon(shop_items)
    
    # Add attack button based on gas
    if battle.gas >= 20:
        if weapon:
            keyboard.append([InlineKeyboardButton(obfuscate_text(f"⚔️ {weapon.name}"), callback_data="action_basic_attack")])
        else:
            keyboard.append([InlineKeyboardButton(obfuscate_text("⚔️ Basic Attack"), callback_data="action_basic_attack")])
    else:
        if weapon:
            keyboard.append([InlineKeyboardButton(obfuscate_text(f"⛽ {weapon.name} (Low Gas)"), callback_data="lowgas_basic_attack")])
        else:
            keyboard.append([InlineKeyboardButton(obfuscate_text("⛽ Basic Attack (Low Gas)"), callback_data="lowgas_basic_attack")])
    
    # Add run button
    keyboard.append([InlineKeyboardButton(obfuscate_text("🏃 Run"), callback_data="action_run")])
    
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
    await query.answer()
    
    # Quick validation of battle ID
    callback_data = query.data
    user_id = str(update.effective_user.id)
    current_battle_id = context.bot_data.get(f"active_battle_id_{user_id}")
    
    # Fast path for mismatched battle IDs
    if callback_data != current_battle_id:
        return 
    
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
        player_data_task = db.players.find_one({"user_id": user_id})
    
    # Wait for titan data
    titan_obj = await titan_task
    if not titan_obj:
        # Use cached titan data if available or show error
        titan_data_debug = context.bot_data.get(f"last_titan_data_{user_id}")
        if not titan_data_debug:
            # Minimized logging for better performance
            logger.warning(f"[BATTLE_START] No titan found for user_id: {user_id}")
            await query.edit_message_text("⚠️ This titan encounter has expired. Please use /explore to find a new titan.")
            return
        titan_data = titan_data_debug
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
    
    # Validate player data
    if not player_data or not player_data.get('team'):
        await query.edit_message_text("Error: No character in your team.")
        return
    
    # Get character name efficiently
    team_member = player_data['team'][0]
    character_name = team_member['character_name'] if isinstance(team_member, dict) else team_member
    
    # Get character data (from cache or database)
    character_task = None
    if not battle_cache.get("character"):
        character_task = db.get_character(user_id, character_name)
    
    if character_task:
        character = await character_task
        battle_cache["character"] = character
    else:
        character = battle_cache["character"]
    
    # Validate character
    if not character:
        await query.edit_message_text(f"Error: Character {character_name} not found.")
        return
    
    # Ensure character HP doesn't exceed maximum
    if character.current_hp > character.stats.HP:
        character.current_hp = character.stats.HP
    
    # Create player object and battle system
    player = Player(**player_data) if player_data else None
    battle = BattleSystem(character, titan, player)
    
    # Add battle to active battles
    async with active_battles_lock:
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
    
    # Edit message with complete content
    await query.edit_message_text(
        text=battle_message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    # Start timeout in background
    asyncio.create_task(battle_timeout(user_id, query, battle, context))
    
    # Log performance metrics occasionally
    if random.random() < 0.1:  # Log only 10% of the time to reduce overhead
        end_time = time.time()
        logger.info(f"[PERF] Battle start took {end_time - start_time:.3f}s")

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
    """Handle battle actions with titan response."""
    # Start timing for performance monitoring
    start_time = time.time()
    
    query = update.callback_query
    if not query or not update.effective_user:
        return
    
    user_id = str(update.effective_user.id)
    
    # Get battle with lock, using fast path for common errors
    async with active_battles_lock:
        battle = active_battles.get(user_id)
        if not battle or battle.battle_ended:
            await query.answer("This battle has already ended or doesn't exist.")
            return
    
    action = query.data
    if not action:
        return
        
    # Fast path handlers for cooldown and low gas notifications
    if action.startswith("cooldown_"):
        ability_name = action[9:]  # Extract ability name from cooldown_AbilityName
        cooldown = battle.ability_cooldowns.get(ability_name, 0)
        await query.answer(f"{ability_name} is on cooldown for {cooldown} more turns!", show_alert=True)
        return
    elif action.startswith("lowgas_"):
        ability_name = action[7:]  # Extract ability name from lowgas_AbilityName
        if ability_name == "basic_attack":
            await query.answer("Not enough gas for basic attack! You need at least 20 gas.", show_alert=True)
        else:
            # Optimize ability gas cost lookup
            gas_cost = 20  # Default value
            character_data = get_character_data(battle.character.character_type)
            if character_data:
                # Flatten the search
                all_abilities = []
                for ability_type in ["active", "passive", "ultimate"]:
                    all_abilities.extend(getattr(character_data, f"{ability_type}_abilities", []))
                
                # Find the ability directly
                for ability in all_abilities:
                    if ability and ability.name == ability_name:
                        gas_cost = ability.gas_cost or 20
                        break
            
            await query.answer(f"Not enough gas to use {ability_name}! You need {gas_cost} gas.", show_alert=True)
        return
        
    # Answer callback query immediately to prevent Telegram timeout
    await query.answer()
    
    # Initialize battle tracking variables
    full_message = []
    effects = {}
    
    # Cancel timeout if exists
    if battle.timeout_task:
        battle.timeout_task.cancel()
        battle.timeout_task = None
    
    # Mark keyboard cache as invalid since state will change
    battle.keyboard_cache_invalid = True
    
    # Handle different action types
    if action == "action_run":
        # Handle run action (fast path)
        if random.random() < 0.7:  # Successful escape
            await query.edit_message_text(
                f"🏃💨 {battle.character.name} successfully escaped from the battle!\n\n"
                f"You live to fight another day. Use /explore to find another titan."
            )
            cleanup_battle(user_id, "escaped")
            return
        else:
            full_message.append(f"❌ {battle.character.name} failed to escape! The titan blocks your path!")
    
    elif action == "action_basic_attack":
        # Optimize basic attack with less DB access
        shop_items = context.bot_data.get("shop_items") or {}
        
        # Only refresh character if needed (use a timestamp to limit refreshes)
        now = time.time()
        last_refresh = getattr(battle, 'last_character_refresh', 0)
        if now - last_refresh > 30:  # Refresh only every 30 seconds max
            db = context.bot_data.get("db") or Database()
            try:
                refreshed_character = await db.get_character(int(battle.character.user_id), battle.character.name)
                if refreshed_character:
                    battle.character = refreshed_character
                    battle.last_character_refresh = now
            except Exception as e:
                logger.debug(f"Character refresh error (non-critical): {e}")
        
        # Process attack
        weapon = battle.get_equipped_weapon(shop_items)
        if battle.gas >= 20:
            battle.gas -= 20
            battle.character_gas = battle.gas
            
            if weapon:
                # Fast weapon attack calculation
                damage_min = int(weapon.attributes.get("damage_min", 10))
                damage_max = int(weapon.attributes.get("damage_max", 20))
                total_damage = random.randint(damage_min, damage_max)
                battle.titan_hp = max(0, battle.titan_hp - total_damage)
                full_message.append(f"⚔️ {battle.character.name} attacks with {weapon.name}, dealing {total_damage} damage!")
            else:
                # Fast basic attack calculation
                try:
                    base_damage = battle.character.stats.ATK or 25
                    total_damage = max(1, base_damage + random.randint(25, 35))
                    battle.titan_hp = max(0, battle.titan_hp - total_damage)
                except Exception:
                    # Simplified error handling for speed
                    total_damage = 10
                    battle.titan_hp = max(0, battle.titan_hp - total_damage)
                full_message.append(f"⚔️ {battle.character.name} attacks with basic strike, dealing {total_damage} damage!")
        else:
            # Not enough gas case
            message = f"❌ {battle.character.name} doesn't have enough gas for {'weapon' if weapon else 'basic'} attack!"
            full_message.append(message)
    
    elif action.startswith("ability_"):
        # Handle ability use with optimized function
        damage, message, effects = battle.use_ability(action[8:])
        battle.character_gas = battle.gas
        full_message.append(message)
        
        # Process effects concisely
        if effects.get("items_dropped"):
            full_message.append(f"Dropped item: {', '.join(effects['items_dropped'])}")
        if effects.get("target_switched"):
            full_message.append("Titan switched targets!")
        if effects.get("bleed_applied"):
            full_message.append("Titan is bleeding!")
    
    # Check for battle end conditions - titan defeated
    if battle.titan_hp <= 0:
        battle.battle_ended = True
        await handle_battle_end(query, battle, user_id, context)
        return
    
    # Check for out-of-gas condition (optimized with simplified logic)
    min_ability_cost = float('inf')
    has_unlocked_passive = False
    
    for ability in battle.character.passive_abilities or []:
        if getattr(ability, 'unlocked', False):
            has_unlocked_passive = True
            gas_cost = getattr(ability, 'gas_cost', float('inf'))
            min_ability_cost = min(min_ability_cost, gas_cost)
    
    if has_unlocked_passive and battle.gas < min_ability_cost:
        await query.edit_message_text(f"{battle.character.name} is out of gas and cannot continue the battle!")
        cleanup_battle(user_id, "out_of_gas")
        return
    
    # Process titan's turn if character still alive
    if battle.character_hp > 0:
        titan_damage, titan_message = battle.titan_attack()
        full_message.append(titan_message)
    
    # Update battle state
    battle.turn += 1
    battle.update_cooldowns()
    
    # Verify database access for next steps
    db = context.bot_data.get("db")
    if not db:
        logger.error("Database not initialized")
        await query.edit_message_text("Internal error: Database not initialized.")
        return
    
    # Check for battle end conditions - character defeated or titan defeated
    if battle.character_hp <= 0 or battle.titan_hp <= 0:
        battle.battle_ended = True
        await handle_battle_end(query, battle, user_id, context)
        return
    
    # Generate keyboard and prepare UI (using cache when possible)
    keyboard = await generate_ability_keyboard(battle, context)
    
    # Fast validation with simplified logic
    if not keyboard or not isinstance(keyboard, list):
        keyboard = [[InlineKeyboardButton("🏃 Run", callback_data="action_run")]]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Get battle status once
    status = battle.get_battle_status()
    
    # Build message with array join (faster than string concatenation)
    message_parts = [
        "<b>⚔️ BATTLE ⚔️</b>\n",
        " ".join(full_message),  # Join messages once
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
    
    # Join all parts at once (much faster than += concatenation)
    battle_message = "\n".join(message_parts)
    
    # Edit message with all content at once
    await query.edit_message_text(
        text=battle_message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    # Start timeout in background
    asyncio.create_task(battle_timeout(user_id, query, battle, context))
    
    # Log performance metrics for optimization tracking (only 5% of the time)
    if random.random() < 0.05:
        end_time = time.time()
        logger.info(f"[PERF] Battle action took {end_time - start_time:.3f}s")

async def handle_battle_end(query, battle: 'BattleSystem', user_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the end of a battle, updating gas and rewards."""
    user_id = str(user_id)
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
    # Use cached player/team info if available
    if not hasattr(context, "user_data") or context.user_data is None:
        context.user_data = {}
    battle_cache = context.user_data.get("battle_cache", {})
    player_data = battle_cache.get("player_data")
    if not player_data:
        player_data = await db.players.find_one({"user_id": user_id})
        battle_cache["player_data"] = player_data
    if not player_data:
        await query.edit_message_text("❌ Player data not found!")
        cleanup_battle(user_id, "error", battle)
        return
    explore_count = player_data.get("explore_count", 0)
    send = context.bot.send_message
    chat_id = query.message.chat_id if hasattr(query.message, 'chat_id') else query.message.chat.id
    # Batch DB updates for character and player
    if battle.titan_hp <= 0:
        rewards = battle.calculate_rewards(
            titan=battle.titan,
            character=battle.character,
            player=battle.player,
            explore_count=explore_count
        )
        character_xp = rewards["xp"] // 2
        player_xp = rewards["xp"] - character_xp
        char_level_info = battle.character.add_xp(character_xp)
        player_obj = Player(**player_data)
        player_level_info = player_obj.add_xp(player_xp)
        gas_consumed = max(0, battle.initial_gas - battle.character.gas)
        battle.character.gas = max(0, battle.character_gas - gas_consumed)
        battle.character.max_gas = battle.character.gas
        battle.character.current_hp = battle.character_hp
        
        # Invalidate character cache to ensure fresh HP values in next explore
        db.invalidate_character_cache(user_id, battle.character.name)
        
        # Batch update: character and player
        updates = []
        updates.append(db.update_character(battle.character))
        reward_updates = {
            "$inc": {
                "crystal": rewards["crystal"],
                "valor": rewards["valor"],
                "xp": player_xp,
                "marks": rewards["marks"],
                "total_xp": player_xp,
                "explore_count": 1
            },
            "$set": {
                "level": player_obj.level,
                "updated_at": datetime.now(timezone.utc)
            }
        }
        updates.append(db.players.update_one({"user_id": user_id}, reward_updates))
        await asyncio.gather(*updates)
        reward_msg = [
            f"<b>You have defeated {battle.titan.name}!</b>\n",
            f"⚡ <b>XP: +{rewards['xp']}</b>",
            f"🪙 <b>Marks: +{rewards['marks']}</b>",
        ]
        if rewards['crystal'] > 0:
            reward_msg.append(f"✨ Titan Crystals: {rewards['crystal']} ✨")
        if rewards['valor'] > 0:
            reward_msg.append(f"🔥 Valor Points: {rewards['valor']} 🔥")
        await query.edit_message_text("\n".join(reward_msg), parse_mode=ParseMode.HTML)

        # Always send level up messages instantly
        if char_level_info["total_level_ups"] > 0:
            for level_up in char_level_info["level_ups"]:
                stat_names = ["ATK", "DEF", "ACC", "INT", "SPD"]
                stat_lines = []
                for stat in stat_names:
                    old = level_up.get(f"old_{stat.lower()}")
                    new = level_up.get(f"new_{stat.lower()}")
                    if old is not None and new is not None and old != new:
                        stat_lines.append(f"{stat}: {old} ➜ {new}")
                msg = [
                    f"🎊 <b>{battle.character.name} leveled Up !!</b>",
                    f"<b>Level :</b> {level_up['old_level']} ➜ {level_up['new_level']}",
                ]
                if stat_lines:
                    msg.append("<b>Stats:</b>")
                    msg.extend(stat_lines)
                else:
                    msg.append("<i>No stat increases.</i>")
                if level_up.get("newly_unlocked_abilities"):
                    msg.append(f"\n🌟 New abilities unlocked:")
                    for ability in level_up["newly_unlocked_abilities"]:
                        ability_type = "🔥" if ability["type"] == "ultimate" else "⚡" if ability["type"] == "active" else "🛡️"
                        msg.append(f"{ability_type} {ability['name']} ({ability['description']})")
                await send(chat_id, "\n".join(msg), parse_mode=ParseMode.HTML)

        if player_level_info["total_level_ups"] > 0:
            

            # Reset player XP to 0 after level up !!
            player_obj.xp = 0
            for lvl_up in player_level_info["level_ups"]:
                msg = [
                    f"PLAYER LEVEL UP! ",
                    f"Level: {lvl_up['old_level']} → {lvl_up['new_level']}"
                ]
                rewards = lvl_up.get("rewards", {})
                if rewards.get("marks", 0) > 0:
                    msg.append(f"🪙 Marks: +{rewards['marks']}")
                if rewards.get("valor", 0) > 0:
                    msg.append(f"⚔️ Valor: +{rewards['valor']}")
                if rewards.get("crystals", 0) > 0:
                    msg.append(f"💠 Crystals: +{rewards['crystals']}")
                await send(chat_id, "\n".join(msg), parse_mode=ParseMode.HTML)
            # Update player fields as before
            total_marks = sum(lvl["rewards"].get("marks", 0) for lvl in player_level_info["level_ups"])
            total_valor = sum(lvl["rewards"].get("valor", 0) for lvl in player_level_info["level_ups"])
            total_crystals = sum(lvl["rewards"].get("crystals", 0) for lvl in player_level_info["level_ups"])
            all_unlocks = []
            update_fields = {"$inc": {}, "$set": {"level": player_obj.level}}
            if total_marks:
                update_fields["$inc"]["marks"] = total_marks
            if total_valor:
                update_fields["$inc"]["valor"] = total_valor
            if total_crystals:
                update_fields["$inc"]["crystal"] = total_crystals
            # Ensure XP is set to 0 after level up
            update_fields["$set"]["xp"] = 0
            await db.players.update_one({"user_id": user_id}, update_fields)
        try:
            track_battle_end(int(user_id), battle.character.name, "victory")
        except ImportError:
            pass
    else:
        gas_consumed = max(0, battle.initial_gas - battle.character.gas)
        battle.character.gas = max(0, battle.character_gas - gas_consumed)
        battle.character.max_gas = battle.character.gas
        battle.character.current_hp = 0
        
        # Invalidate character cache to ensure fresh HP values in next explore
        db.invalidate_character_cache(user_id, battle.character.name)
        
        # Batch update: character and player (explore_count)
        updates = []
        updates.append(db.update_character(battle.character))
        updates.append(db.players.update_one({"user_id": user_id}, {"$inc": {"explore_count": 1}}))
        await asyncio.gather(*updates)
        await query.edit_message_text(f"{battle.character.name} was defeated by {battle.titan.name}!")
        try:
            track_battle_end(int(user_id), battle.character.name, "defeat")
        except ImportError:
            pass

    # Add random drop after battle end
    try:
        if random.random() < 0.07:
            drop = get_random_drop()
            # Get player object
            player_obj = await db.get_player(user_id)
            if player_obj:
                inv = player_obj.inventory or {}
                update_data = {}
                if drop['type'] in ['bottle', 'cylinder']:
                    inv['gas'] = inv.get('gas', 0) + drop['amount']
                    update_data['gas'] = getattr(player_obj, 'gas', 0) + drop['amount']
                    await query.message.reply_photo(
                        photo=drop['image'],
                        caption=drop['message'],
                        parse_mode=ParseMode.HTML
                    )
                elif drop['type'] == 'valors':
                    inv['valor'] = inv.get('valor', 0) + drop['amount']
                    update_data['valor'] = getattr(player_obj, 'valor', 0) + drop['amount']
                    await query.message.reply_text(
                        drop['message'],
                        parse_mode=ParseMode.HTML
                    )
                elif drop['type'] == 'crystals':
                    inv['crystal'] = inv.get('crystal', 0) + drop['amount']
                    update_data['crystal'] = getattr(player_obj, 'crystal', 0) + drop['amount']
                    await query.message.reply_text(
                        drop['message'],
                        parse_mode=ParseMode.HTML
                    )
                update_data['inventory'] = inv
                await db.update_player(user_id, update_data)
            else:
                # Fallback: just send message
                if drop.get('image'):
                    await query.message.reply_photo(
                        photo=drop['image'],
                        caption=drop['message'],
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await query.message.reply_text(
                        drop['message'],
                        parse_mode=ParseMode.HTML
                    )
    except Exception:
        pass

    # Clean up titan and travel state quickly
    if f"last_titan_{user_id}" in context.bot_data:
        del context.bot_data[f"last_titan_{user_id}"]
    if f"last_titan_data_{user_id}" in context.bot_data:
        del context.bot_data[f"last_titan_data_{user_id}"]
    await db.delete_titan(user_id)
    travel = player_data.get("travel", {})
    location = player_data.get("location", None)
    arrived = False
    decision_point = False
    if travel.get("in_progress"):
        travel["progress"] += 1
        if travel["progress"] >= travel["required"]:
            new_location = travel["to"]
            from game.travel_map import TRAVEL_MAP
            if new_location.startswith("Decision_") and new_location in TRAVEL_MAP:
                await db.players.update_one({"user_id": user_id}, {"$set": {"location": new_location, "travel": {}}})
                decision_point = True
                location = new_location
            else:
                await db.players.update_one({"user_id": user_id}, {"$set": {"location": new_location, "travel": {}}})
                arrived = True
        else:
            await db.players.update_one({"user_id": user_id}, {"$set": {"travel": travel}})
        if decision_point:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            directions = TRAVEL_MAP[location]
            keyboard = [
                [InlineKeyboardButton(dir, callback_data=f"travel_decision_{dir}")] for dir in directions.keys()
            ]
            msg = f"<b>Decision Point Reached:</b> {location}\nChoose a direction to continue your journey:"
            try:
                await send(chat_id, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            except Exception:
                await send(chat_id, msg)
            return
        elif arrived:
            try:
                await send(chat_id, f"You have arrived at <b>{location}</b>!", parse_mode="HTML")
            except Exception:
                pass
    # # Clear active battle id so user can explore again
    if f"active_battle_id_{user_id}" in context.bot_data:
        del context.bot_data[f"active_battle_id_{user_id}"]
    # Remove cached battle data after battle ends
    if hasattr(context, "user_data") and isinstance(context.user_data, dict):
        context.user_data.pop("battle_cache", None)
    cleanup_battle(user_id, "completed", battle)

async def battle_timeout(user_id: str, query, battle: 'BattleSystem', context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle battle timeout."""
    user_id = str(user_id)
    try:
        battle.timeout_task = asyncio.current_task()
        await asyncio.sleep(180)
        async with active_battles_lock:
            if user_id in active_battles:
                db = context.bot_data.get("db")
                if not db:
                    logger.error("Database not initialized")
                    return
                try:
                    await db.characters.update_one(
                        {"user_id": user_id, "name": battle.character.name},
                        {"$set": {
                            "current_hp": battle.character_hp,
                            "gas": battle.character_gas,
                            "max_gas": battle.character.max_gas,
                            "ability_cooldowns": battle.ability_cooldowns
                        }}
                    )
                except Exception as e:
                    logger.error(f"Failed to save character state on timeout for user {user_id}: {e}")
                try:
                    await query.edit_message_text(
                        "⏰ Battle Expired ⏰\n\n"
                        "You didn't respond in time. The battle has expired.\n"
                        "Use /explore to find another titan."
                    )
                except Exception as e:
                    logger.warning(f"Failed to update message on timeout for user {user_id}: {e}")
                await db.delete_titan(user_id)
                cleanup_battle(user_id, "timeout", battle)
    except asyncio.CancelledError:
        logger.debug(f"Battle timeout cancelled for user {user_id}")
    except Exception as e:
        logger.error(f"Error in battle_timeout for user {user_id}: {e}")
        cleanup_battle(user_id, "timeout_error", battle)