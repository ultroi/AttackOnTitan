from typing import Dict, List, Optional, Any, Tuple
from database.models import Character, Player, Titan, CharacterStats, generate_titan_xp
from database.characters import AbilityEffect, get_character_data, Ability
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import Database
import asyncio
import random
from utils.monitor import track_battle_end
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Global dictionary to track active battles
active_battles: Dict[str, 'BattleSystem'] = {}

class BattleSystem:
    def __init__(self, character: Character, titan: Titan, player: Optional[Player] = None):
        self.character = character
        self.titan = titan
        self.player = player
        # Initialize HP and gas
        self.character_hp = getattr(character, 'current_hp')
        self.titan_hp = getattr(titan, 'max_hp')
        # Always use character's current gas for battle
        self.gas = getattr(character, 'gas') 
        # Ensure max_gas never exceeds 5000 unless intentionally upgraded
        max_gas = getattr(character, 'max_gas', self.gas)
        if max_gas > 5000:
            max_gas = 5000
            self.character.max_gas = 5000
        self.character_gas = max_gas

        # Ensure character stats are initialized
        if not hasattr(character, 'stats') or character.stats is None:
            self.character.stats = CharacterStats(HP=650, ATK=25, DEF=10, SPD=10, ACC=10, INT=10)

        # Safely handle potentially None abilities
        active_abilities = character.active_abilities or []
        passive_abilities = character.passive_abilities or []
        ultimate_abilities = character.ultimate_abilities or []
        self.ability_cooldowns = {ability.name: 0 for ability in active_abilities + passive_abilities + ultimate_abilities}
        
        self.buffs = {}
        self.debuffs = {}  # Character debuffs
        self.titan_debuffs = {}  # Titan debuffs
        self.turn = 0
        self.trigger_states = {
            "first_damage_taken": False,
            "dodge_count": 0,
            "fear_counter": 0,
            "focused_turns": 0,
            "ally_died": False
        }
        self.apply_passives("battle_start")
        self.timeout_task: Optional[asyncio.Task] = None
        self._is_disposed = False
        self.battle_ended = False  # Flag to prevent post-end actions
    
    def dispose(self):
        """Properly dispose of the battle system and clean up resources"""
        if self._is_disposed:
            return
        
        self._is_disposed = True
        
        # Cancel timeout task
        if self.timeout_task and not self.timeout_task.done():
            try:
                self.timeout_task.cancel()
            except Exception:
                pass
        
        self.timeout_task = None
        self.buffs.clear()
        self.debuffs.clear()
        self.titan_debuffs.clear()
        self.ability_cooldowns.clear()
        self.trigger_states.clear()

    def build_context(self, trigger: Optional[str] = None, ability: Optional[Ability] = None) -> Dict:
        """Build standardized battle context for ability effect functions."""
        base_damage = 0
        if ability and hasattr(ability, 'base_damage') and ability.base_damage:
            base_damage = ability.base_damage + (self.character.stats.ATK if self.character.stats else 0)
        
        return {
            "character_stats": self.character.stats.dict() if self.character.stats else {},
            "character_hp": self.character_hp,
            "character_max_hp": self.character.stats.HP if self.character.stats else 100,
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
            "titan_difficulty": getattr(self.titan, 'difficulty', 'Normal'),
            "titan_special_abilities": getattr(self.titan, 'special_abilities', None) or []
        }

    def apply_passives(self, trigger: str) -> List[str]:
        """Apply passive abilities for a given trigger, returning messages."""
        character_data = get_character_data(self.character.character_type)
        messages = []
        if character_data is None:
            return messages
            
        passive_abilities = getattr(character_data, "passive_abilities", [])
        if not passive_abilities:
            return messages
            
        for ability in passive_abilities:
            ability_name = getattr(ability, 'name', None)
            if ability is None or not ability_name:
                continue
                
            level_required = getattr(ability, 'level_required', 1)
            level_unlocked = self.character.level >= level_required
            if not level_unlocked:
                continue
            
            explicitly_unlocked = self.character.unlocked_abilities.get(ability_name, False) if self.character.unlocked_abilities else False
            definition_unlocked = getattr(ability, 'is_unlocked', True)
            is_unlocked = definition_unlocked or explicitly_unlocked
            
            if is_unlocked and hasattr(ability, 'effect_function') and ability.effect_function:
                try:
                    context = self.build_context(trigger, ability)
                    effect = ability.effect_function(context)
                    if effect:
                        self.apply_effect(effect)
                        if hasattr(effect, 'message') and effect.message:
                            messages.append(effect.message)
                except Exception as e:
                    logger.error(f"Error applying passive ability {ability_name}: {e}")
                    continue
        return messages

    def apply_effect(self, effect: AbilityEffect) -> None:
        """Apply an AbilityEffect to the battle state."""
        if effect is None:
            return
            
        self.titan_hp = max(0, self.titan_hp - (effect.damage or 0))
        self.character_hp = min(self.character.stats.HP, self.character_hp + (effect.healed or 0))
        
        if hasattr(effect, 'shield') and effect.shield:
            self.buffs["shield"] = self.buffs.get("shield", 0) + effect.shield
        if hasattr(effect, 'stun_duration') and effect.stun_duration:
            self.titan_debuffs["stun"] = max(self.titan_debuffs.get("stun", 0), effect.stun_duration)
            
        counter_dmg = 0
        if hasattr(effect, 'counter_attack') and isinstance(effect.counter_attack, dict):
            counter_dmg = effect.counter_attack.get("damage", 0)
            if self.buffs.get("crit_damage"):
                counter_dmg *= self.buffs["crit_damage"]
            self.titan_hp = max(0, self.titan_hp - counter_dmg)
            attack_type = effect.counter_attack.get("type")
            if attack_type == "pierce":
                self.titan_debuffs["bleed"] = 3
            elif attack_type == "slash":
                self.titan_debuffs["damage_reduction"] = 0.1
        
        if hasattr(effect, 'buffs') and effect.buffs:
            self.buffs.update(effect.buffs)
        if hasattr(effect, 'debuffs') and effect.debuffs:
            self.titan_debuffs.update(effect.debuffs)
        
        if hasattr(effect, 'clear_debuffs') and effect.clear_debuffs:
            self.debuffs.clear()
        
        if hasattr(effect, 'items_dropped') and effect.items_dropped:
            self.buffs["items_dropped"] = self.buffs.get("items_dropped", []) + effect.items_dropped
        
        if hasattr(effect, 'target_switched') and effect.target_switched:
            self.titan_debuffs["target_confusion"] = 2
        
        if hasattr(effect, 'bleed_applied') and effect.bleed_applied:
            self.titan_debuffs["bleed"] = max(self.titan_debuffs.get("bleed", 0), 3)

    def titan_attack(self) -> Tuple[int, str]:
        """Calculate damage dealt by titan, respecting debuffs, buffs, and special abilities."""
        if self.titan_debuffs.get("stun", 0) > 0:
            self.titan_debuffs["stun"] -= 1
            return 0, f"{self.titan.name} is stunned and cannot attack this turn!"
        if self.titan_debuffs.get("delay", 0) > 0:
            self.titan_debuffs["delay"] -= 1
            return 0, f"{self.titan.name} is delayed and cannot attack this turn!"
            
        if self.buffs.get("dodge", 0) > 0 or self.trigger_states["dodge_count"] > 0:
            self.trigger_states["dodge_count"] = max(0, self.trigger_states["dodge_count"] - 1)
            messages = self.apply_passives("dodge")
            return 0, f"{self.character.name} dodged the attack!\n" + "\n".join(messages)
            
        base_damage = max(15, self.titan.level * 8 + 10)
        damage_multipliers = {"Easy": 0.7, "Normal": 1.0, "Hard": 1.4}
        base_damage = int(base_damage * damage_multipliers.get(self.titan.difficulty, 1.0))
        
        special_messages = []
        if self.titan.special_abilities:
            for ability in self.titan.special_abilities:
                if ability == "Armor Plating" and random.random() < 0.3:
                    base_damage = int(base_damage * 1.2)
                    special_messages.append(f"⚔️ {self.titan.name}'s Armor Spikes deal extra damage!")
                elif ability == "Thunder Spear" and random.random() < 0.25:
                    base_damage = int(base_damage * 1.4)
                    special_messages.append(f"⚡ {self.titan.name} unleashes a Thunder Spear!")
                elif ability == "Regeneration" and random.random() < 0.4:
                    heal = int(self.titan.max_hp * 0.08)
                    self.titan_hp = min(self.titan.max_hp, self.titan_hp + heal)
                    special_messages.append(f"🩹 {self.titan.name} regenerates {heal} HP!")
                elif ability == "Berserker Rage" and self.titan_hp / self.titan.max_hp < 0.3:
                    base_damage = int(base_damage * 1.5)
                    special_messages.append(f"😡 {self.titan.name} enters berserker rage!")
                elif ability == "Steam Blast" and random.random() < 0.2:
                    self.debuffs["burn"] = 3
                    special_messages.append(f"🔥 {self.titan.name} releases scalding steam!")
                elif ability == "Colossal Explosion" and random.random() < 0.15:
                    base_damage = int(base_damage * 2.0)
                    special_messages.append(f"💥 {self.titan.name} creates a massive explosion!")
        
        damage = int(base_damage * (1 - min(0.75, self.character.stats.DEF / 250)))
        
        if self.buffs.get("damage_reduction", 0):
            damage = int(damage * (1 - self.buffs["damage_reduction"]))
            
        if self.buffs.get("shield", 0) > 0:
            shield_absorb = min(self.buffs["shield"], damage)
            self.buffs["shield"] -= shield_absorb
            damage -= shield_absorb
            if self.buffs["shield"] <= 0:
                del self.buffs["shield"]
                
        self.character_hp = max(0, self.character_hp - damage)
        
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

    def use_ability(self, ability_name: str) -> Tuple[int, str, Dict]:
        """Use a character ability."""
        logger.info(f"Using ability {ability_name} with gas cost 20")
        
        damage = 0
        message = ""
        effects = {}
        
        if not self.character or not self.character.stats:
            return damage, "Error: Character stats not available", effects
            
        character_data = get_character_data(self.character.character_type)
        if not character_data or not hasattr(character_data, 'abilities'):
            return damage, "Error: Character abilities not found", effects
            
        effects = {
            "items_dropped": [],
            "target_switched": False,
            "bleed_applied": False
        }
        
        ability = None
        for ability_type in ["active", "passive", "ultimate"]:
            abilities = getattr(character_data, f"{ability_type}_abilities", [])
            for ab in abilities:
                ab_name = getattr(ab, "name", None)
                if ab_name == ability_name:
                    ability = ab
                    break
            if ability:
                break
        
        if not ability:
            return damage, f"Error: Ability {ability_name} not found", effects
            
        if self.ability_cooldowns.get(ability_name, 0) > 0:
            return damage, f"{ability_name} is on cooldown for {self.ability_cooldowns[ability_name]} turns!", effects
            
        gas_cost = getattr(ability, 'gas_cost', 20) or 20
        if self.gas < gas_cost:
            return damage, f"Not enough gas to use {ability_name}!", effects
            
        self.gas -= gas_cost
        ctx = self.build_context("ability_use", ability)
        
        try:
            if ability.effect_function:
                effect = ability.effect_function(ctx)
                if effect:
                    self.apply_effect(effect)
                    message = effect.message or f"{ability_name} used successfully!"
                    effects = {
                        "items_dropped": getattr(effect, 'items_dropped', []),
                        "target_switched": getattr(effect, 'target_switched', False),
                        "bleed_applied": getattr(effect, 'bleed_applied', False)
                    }
        except Exception as e:
            logger.error(f"Error applying ability {ability_name}: {e}")
            return damage, f"Error using {ability_name}", effects
            
        cooldown = getattr(ability, 'gas_cost', 1) or 1
        self.ability_cooldowns[ability_name] = cooldown
        
        return damage, message, effects

    def has_usable_abilities(self) -> bool:
        """Check if the character has any usable abilities based on gas and cooldowns."""
        character_data = get_character_data(self.character.character_type)
        if character_data is None:
            return False
        for ability_type in ["active", "passive", "ultimate"]:
            abilities_of_type = getattr(character_data, f"{ability_type}_abilities", [])
            for ability in abilities_of_type:
                ability_name = getattr(ability, "name", None)
                if ability is None or not ability_name:
                    continue
                level_required = getattr(ability, 'level_required', 1)
                level_unlocked = self.character.level >= level_required
                if not level_unlocked:
                    continue
                explicitly_unlocked = self.character.unlocked_abilities.get(ability_name, False) if self.character.unlocked_abilities else False
                definition_unlocked = getattr(ability, 'is_unlocked', False)
                is_unlocked = definition_unlocked or explicitly_unlocked
                disabled_against_titans = getattr(ability, 'disabled_against_titans', False)
                gas_cost = getattr(ability, 'gas_cost', 0)
                if (
                    is_unlocked and
                    not disabled_against_titans and
                    self.ability_cooldowns.get(ability_name, 0) == 0 and
                    self.gas >= gas_cost
                ):
                    return True
        return False

    def update_cooldowns(self) -> None:
        """Decrease ability cooldowns and temporary effects."""
        for ability_name in list(self.ability_cooldowns.keys()):
            if self.ability_cooldowns[ability_name] > 0:
                self.ability_cooldowns[ability_name] -= 1
                
        for debuff in list(self.titan_debuffs.keys()):
            if self.titan_debuffs[debuff] > 0:
                self.titan_debuffs[debuff] -= 1
                if self.titan_debuffs[debuff] <= 0:
                    del self.titan_debuffs[debuff]
                    
        for buff in list(self.buffs.keys()):
            if isinstance(self.buffs[buff], (int, float)) and buff not in ["shield", "items_dropped"]:
                if self.buffs[buff] > 1:
                    self.buffs[buff] -= 1
                    if self.buffs[buff] <= 0:
                        del self.buffs[buff]
        
        if self.debuffs.get("burn", 0) > 0:
            burn_damage = max(5, self.titan.level * 2)
            self.character_hp = max(0, self.character_hp - burn_damage)
            self.debuffs["burn"] -= 1
            if self.debuffs["burn"] <= 0:
                del self.debuffs["burn"]
        
        if self.titan_debuffs.get("bleed", 0) > 0:
            character_atk = self.character.stats.ATK if self.character.stats else 10
            bleed_damage = max(10, character_atk // 2)
            self.titan_hp = max(0, self.titan_hp - bleed_damage)
            self.titan_debuffs["bleed"] -= 1
            if self.titan_debuffs["bleed"] <= 0:
                del self.titan_debuffs["bleed"]

    def get_battle_status(self) -> Dict:
        """Return current battle state."""
        character_max_hp = self.character.stats.HP if self.character.stats else 100
        titan_max_hp = getattr(self.titan, 'max_hp', 100)
        
        character_hp_percent = self.character_hp / character_max_hp
        titan_hp_percent = self.titan_hp / titan_max_hp
        character_bar = "█" * int(character_hp_percent * 10) + "▒" * (10 - int(character_hp_percent * 10))
        titan_bar = "█" * int(titan_hp_percent * 10) + "▒" * (10 - int(titan_hp_percent * 10))
        
        status_message = f"Turn: {self.turn + 1}\n"
        titan_difficulty = getattr(self.titan, 'difficulty', 'Normal')
        status_message += f"Difficulty: {titan_difficulty}\n"
        
        titan_special_abilities = getattr(self.titan, 'special_abilities', None)
        if titan_special_abilities:
            status_message += f"Special Abilities: {', '.join(titan_special_abilities)}\n"
        
        if self.titan_debuffs:
            debuffs_display = []
            for k, v in self.titan_debuffs.items():
                if isinstance(v, (int, float)):
                    debuffs_display.append(f"{k}({int(v)})")
                else:
                    debuffs_display.append(f"{k}")
            status_message += f"🔽 Titan debuffs: {', '.join(debuffs_display)}\n"
            
        if self.buffs:
            buffs_display = []
            for k, v in self.buffs.items():
                if k == "items_dropped":
                    continue
                if isinstance(v, (int, float)) and v > 1:
                    buffs_display.append(f"{k}({int(v)})")
                else:
                    buffs_display.append(f"{k}")
            if buffs_display:
                status_message += f"🔼 Character buffs: {', '.join(buffs_display)}\n"
                
        if self.buffs.get("items_dropped"):
            status_message += f"💎 Items available: {', '.join(self.buffs['items_dropped'])}\n"
        
        if self.debuffs:
            debuffs_char_display = []
            for k, v in self.debuffs.items():
                if isinstance(v, (int, float)):
                    debuffs_char_display.append(f"{k}({int(v)})")
                else:
                    debuffs_char_display.append(f"{k}")
            status_message += f"🔥 Character debuffs: {', '.join(debuffs_char_display)}\n"
            
        return {
            "character_hp": int(self.character_hp),
            "titan_hp": int(self.titan_hp),
            "gas": int(self.gas),
            "character_bar": character_bar,
            "titan_bar": titan_bar,
            "status_message": status_message
        }

    def calculate_rewards(self, titan: Titan, character: Character, player: Optional[Player], explore_count: int, valor: int = 0, crystal: int = 0) -> dict:
        """Calculate rewards for defeating the titan with new random system."""
        base_xp = generate_titan_xp(titan.level, titan.difficulty)
        
        performance_multiplier = 1.0
        if self.turn < 5:
            performance_multiplier += 0.2
        if self.character_hp / self.character.stats.HP > 0.8:
            performance_multiplier += 0.15
        if titan.difficulty == "Hard":
            performance_multiplier += 0.3
        
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

def cleanup_battle(user_id: str, result: str = "ended", battle: Optional['BattleSystem'] = None):
    """Clean up battle state and resources"""
    battle_instance = battle or active_battles.get(user_id)
    
    if battle_instance:
        try:
            from utils.monitor import track_battle_end
            username = getattr(battle_instance.character, 'name', 'Unknown') if battle_instance.character else 'Unknown'
            track_battle_end(int(user_id), username, result)
        except ImportError:
            pass
        
        try:
            battle_instance.dispose()
        except Exception as e:
            logger.warning(f"Error disposing battle for user {user_id}: {e}")
        
        if user_id in active_battles:
            del active_battles[user_id]
        
        logger.info(f"Battle {result} for user {user_id}. Active battles: {len(active_battles)}")
    else:
        try:
            from utils.monitor import track_battle_end
            track_battle_end(int(user_id), "Unknown", result)
        except ImportError:
            pass
    
    try:
        from utils.monitor import remove_player_activity
        remove_player_activity(int(user_id))
    except ImportError:
        pass

def generate_ability_keyboard(battle: 'BattleSystem') -> List[List[InlineKeyboardButton]]:
    """Generate keyboard buttons for valid abilities."""
    keyboard = []
    character_data = get_character_data(battle.character.character_type)
    logger.info(f"Generating abilities for {battle.character.name} (Level {battle.character.level})")
    if character_data is None:
        logger.warning(f"No character data found for {battle.character.character_type}")
        keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
        return keyboard
    for ability_type in ["active", "passive", "ultimate"]:
        abilities = getattr(character_data, f"{ability_type}_abilities", [])
        for ability in abilities:
            ability_name = getattr(ability, "name", None)
            if ability is None or not ability_name:
                continue
            level_required = getattr(ability, 'level_required', 1)
            level_unlocked = battle.character.level >= level_required
            if not level_unlocked:
                continue
            definition_unlocked = getattr(ability, 'is_unlocked', ability_type != "ultimate")
            explicitly_unlocked = battle.character.unlocked_abilities.get(ability_name, False)
            is_unlocked = definition_unlocked or explicitly_unlocked
            disabled_against_titans = getattr(ability, 'disabled_against_titans', False)
            gas_cost = getattr(ability, 'gas_cost', 0)
            ability_display_name = getattr(ability, 'name', ability_name)
            prefix = "⚔️" if ability_type == "active" else "✨" if ability_type == "ultimate" else "🔄"
            if (
                is_unlocked and
                not disabled_against_titans and
                battle.ability_cooldowns.get(ability_name, 0) == 0 and
                battle.gas >= gas_cost
            ):
                keyboard.append([InlineKeyboardButton(
                    f"{prefix} {ability_display_name} ({gas_cost} gas)",
                    callback_data=f"ability_{ability_name}"
                )])
            elif is_unlocked and battle.ability_cooldowns.get(ability_name, 0) > 0:
                cooldown = battle.ability_cooldowns[ability_name]
                keyboard.append([InlineKeyboardButton(
                    f"⏳ {prefix} {ability_display_name} (CD: {cooldown})",
                    callback_data=f"cooldown_{ability_name}"
                )])
            elif is_unlocked and battle.gas < gas_cost and gas_cost > 0:
                keyboard.append([InlineKeyboardButton(
                    f"⛽ {prefix} {ability_display_name} (Need {gas_cost} gas)",
                    callback_data=f"lowgas_{ability_name}"
                )])
    if battle.gas >= 20:
        keyboard.append([InlineKeyboardButton("⚔️ Basic Attack (20 gas)", callback_data="action_basic_attack")])
    else:
        keyboard.append([InlineKeyboardButton("⛽ Basic Attack (Need 20 gas)", callback_data="lowgas_basic_attack")])
    keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
    return keyboard

async def handle_battle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the start of a battle when user clicks the Battle button."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    
    if not update.effective_user:
        await query.edit_message_text("Cannot identify user.")
        return
    
    callback_data = query.data
    if not callback_data or not callback_data.startswith("battle_"):
        await query.edit_message_text("Invalid battle request.")
        return
    
    user_id = str(update.effective_user.id)  # Always use string
    if user_id in active_battles:
        await query.edit_message_text("⚠️ You already have an active battle in progress! Finish it before starting a new one.")
        return
    logger.info(f"[BATTLE_START] user_id: {user_id} (type: {type(user_id)})")
    # Cancel titan encounter timeout if it exists
    titan_timeout_key = f"titan_timeout_{user_id}"
    titan_timeout_task = context.bot_data.pop(titan_timeout_key, None)
    if titan_timeout_task and not titan_timeout_task.done():
        titan_timeout_task.cancel()
        logger.info(f"[BATTLE_START] Cancelled titan timeout for user_id: {user_id}")
    else:
        logger.info(f"[BATTLE_START] No titan timeout to cancel for user_id: {user_id}")
    # Check if titan still exists in DB
    db = context.bot_data.get("db")
    if db is None:
        logger.error("Database not initialized in context.bot_data")
        await query.edit_message_text("Internal error: Database not initialized.")
        return
    
    logger.info(f"[BATTLE_START] Fetching titan for user_id: {user_id}")
    titan_obj = await db.get_titan(user_id)
    if not titan_obj:
        logger.warning(f"[BATTLE_START] No titan found in DB for user_id: {user_id}")
        await query.edit_message_text("⚠️ This titan encounter has expired. Please use /explore to find a new titan.")
        return
    logger.info(f"[BATTLE_START] Titan found for user_id: {user_id}: {getattr(titan_obj, 'name', None)}")
    titan_data = context.bot_data.get(f"last_titan_data_{user_id}")
    if not titan_data:
        # Try to fetch from database if not in memory
        titan_data = titan_obj.dict()
    titan = Titan(**titan_data)
    
    player_data = await db.players.find_one({"user_id": user_id})
    if not player_data or not player_data.get('team'):
        await query.edit_message_text("Error: No character in your team.")
        return
    
    team_member = player_data['team'][0]
    character_name = team_member['character_name'] if isinstance(team_member, dict) else team_member
    character = await db.get_character(user_id, character_name)
    if not character:
        await query.edit_message_text(f"Error: Character {character_name} not found.")
        return
    # Ensure current_hp never exceeds max HP and max HP never decreases
    if hasattr(character, 'current_hp') and hasattr(character, 'stats') and hasattr(character.stats, 'HP'):
        if character.current_hp is None or character.current_hp > character.stats.HP:
            character.current_hp = character.stats.HP
    
    player = Player(**player_data) if player_data else None
    battle = BattleSystem(character, titan, player)
    active_battles[user_id] = battle
    
    try:
        from utils.monitor import track_player_action
        username = update.effective_user.username or update.effective_user.first_name or "Unknown"
        track_player_action(user_id, username, "🔥 In Battle", {
            "character": character.name,
            "titan": titan.name,
            "titan_level": titan.level
        })
    except ImportError:
        pass
    
    logger.info(f"Battle started for user {user_id}. Active battles: {len(active_battles)}")
    
    keyboard = generate_ability_keyboard(battle)
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    status = battle.get_battle_status()
    await query.edit_message_text(
        text=(
            f"<b>⚔️ BATTLE ⚔️</b>\n\n"
            f"<b>| {battle.titan.name} ({battle.titan.level}) |</b>\n"
            f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp}</b>\n"
            f"{status['titan_bar']}\n\n"
            f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>\n"
            f"<b>HP: {status['character_hp']}/{battle.character.stats.HP}</b>\n"
            f"{status['character_bar']}\n"
            f"<b>Gas: {status['gas']}/{battle.character.max_gas}</b>\n\n"
            f"{status['status_message']}\n"
            f"<b>Choose your action:</b>"
        ),
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    asyncio.create_task(battle_timeout(user_id, query, battle, context))

async def handle_battle_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle battle actions with immediate titan response."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    
    if not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    if user_id not in active_battles:
        # Do not send any message if battle is not active (already ended)
        return
    battle = active_battles[user_id]
    # Prevent further actions if battle is already ended
    if getattr(battle, 'battle_ended', False):
        # Do not send any message if battle is already ended
        return
    
    action = query.data
    if not action:
        return
        
    full_message = []
    effects = {}
    
    if battle.timeout_task:
        battle.timeout_task.cancel()
        battle.timeout_task = None
    
    if action == "action_run":
        escape_chance = 0.7
        if random.random() < escape_chance:
            await query.edit_message_text(
                f"🏃💨 {battle.character.name} successfully escaped from the battle!\n\n"
                f"You live to fight another day. Use /explore to find another titan."
            )
            cleanup_battle(user_id, "escaped")
            return
        else:
            full_message.append(f"❌ {battle.character.name} failed to escape! The titan blocks your path!")
    elif action == "action_basic_attack":
        logger.info(f"[Basic Attack] Before: gas={battle.gas}, titan_hp={battle.titan_hp}")
        if (battle.gas or 0) >= 20:
            battle.gas = (battle.gas or 0) - 20
            battle.character.gas = battle.gas  # Sync character gas after basic attack
            try:
                stats = getattr(battle.character, 'stats', None)
                base_damage = 40
                if stats and hasattr(stats, 'ATK') and stats.ATK is not None:
                    base_damage = stats.ATK or 25

                damage_variance = random.randint(-2, 3)
                total_damage = max(1, base_damage + damage_variance)

                current_titan_hp = battle.titan_hp if battle.titan_hp is not None else 0
                battle.titan_hp = max(0, current_titan_hp - total_damage)
                logger.info(f"[Basic Attack] {battle.character.name} dealt {total_damage} damage. Titan HP now {battle.titan_hp}")
            except Exception as e:
                logger.error(f"Error calculating basic attack damage: {e}")
                total_damage = 10
                current_titan_hp = battle.titan_hp if battle.titan_hp is not None else 0
                battle.titan_hp = max(0, current_titan_hp - total_damage)
                logger.info(f"[Basic Attack] Fallback: {battle.character.name} dealt {total_damage} damage. Titan HP now {battle.titan_hp}")

            full_message.append(f"⚔️ {battle.character.name} attacks with basic strike, dealing {total_damage} damage!")
        else:
            full_message.append(f"❌ {battle.character.name} doesn't have enough gas for basic attack!")
        logger.info(f"[Basic Attack] After: gas={battle.gas}, titan_hp={battle.titan_hp}")
    elif action.startswith("ability_"):
        ability_name = action[8:]
        damage, message, effects = battle.use_ability(ability_name)
        battle.character.gas = battle.gas  # Sync character gas after ability use
        full_message.append(message)
        if effects.get("items_dropped"):
            full_message.append(f"Dropped item: {', '.join(effects['items_dropped'])}")
        if effects.get("target_switched"):
            full_message.append("Titan switched targets!")
        if effects.get("bleed_applied"):
            full_message.append("Titan is bleeding!")
    
    if battle.titan_hp <= 0:
        battle.battle_ended = True
        await handle_battle_end(query, battle, user_id, context)
        return

    unlocked_passives = [
        ability for ability in battle.character.passive_abilities 
        if getattr(ability, 'unlocked', False) and getattr(ability, 'gas_cost', 0) > 0
    ]
    if unlocked_passives and battle.gas < min(getattr(ability, 'gas_cost', float('inf')) for ability in unlocked_passives):
        min_cost = min(getattr(ability, 'gas_cost', float('inf')) for ability in unlocked_passives)
        message = f"{battle.character.name} is out of gas and cannot continue the battle!"
        await query.edit_message_text(message)
        cleanup_battle(user_id, "out_of_gas")
        return
    
    if battle.character_hp > 0:
        titan_damage, titan_message = battle.titan_attack()
        full_message.append(titan_message)
    
    battle.turn += 1
    battle.update_cooldowns()
    
    db = context.bot_data.get("db")
    if db is None:
        logger.error("Database not initialized in context.bot_data")
        await query.edit_message_text("Internal error: Database not initialized.")
        return
    
    if battle.character_hp <= 0 or battle.titan_hp <= 0:
        battle.battle_ended = True
        await handle_battle_end(query, battle, user_id, context)
        return
    
    keyboard = generate_ability_keyboard(battle)
    reply_markup = InlineKeyboardMarkup(keyboard)
    status = battle.get_battle_status()
    
    battle_message = (
        f"<b>⚔️ BATTLE ⚔️</b>\n\n"
        f"{' '.join(full_message)}\n\n"
        f"<b>| {battle.titan.name} ({battle.titan.level}) |</b>\n"
        f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp}</b>\n"
        f"{status['titan_bar']}\n\n"
        f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>\n"
        f"<b>HP: {status['character_hp']}/{battle.character.stats.HP}</b>\n"
        f"{status['character_bar']}\n"
        f"<b>Gas: {status['gas']}/{battle.character.max_gas}</b>\n\n"
        f"{status['status_message']}\n"
    )
    
    await query.edit_message_text(
        text=battle_message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    asyncio.create_task(battle_timeout(user_id, query, battle, context))

async def handle_battle_end(query, battle: 'BattleSystem', user_id: str, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(user_id)
    # Mark battle as ended to prevent further actions
    if hasattr(battle, 'battle_ended'):
        battle.battle_ended = True
    try:
        if battle.timeout_task and not battle.timeout_task.done():
            battle.timeout_task.cancel()
        # Use shared db instance from context.bot_data
        db = context.bot_data.get("db")
        if db is None:
            logger.error("Database not initialized in context.bot_data")
            return
        player_data = await db.players.find_one({"user_id": user_id})
        if not player_data:
            await query.edit_message_text("❌ Player data not found!")
            cleanup_battle(user_id, "error", battle)
            return
        explore_count = player_data.get("explore_count", 0)
        await db.players.update_one(
            {"user_id": user_id},
            {"$inc": {"explore_count": 1}}
        )
        send = context.bot.send_message
        chat_id = query.message.chat_id if hasattr(query.message, 'chat_id') else query.message.chat.id
        if battle.titan_hp <= 0:
            rewards = battle.calculate_rewards(
                titan=battle.titan,
                character=battle.character,
                player=battle.player,
                explore_count=explore_count,
                valor=0,
                crystal=0
            )
            character_xp = rewards["xp"] // 2
            player_xp = rewards["xp"] - character_xp
            char_level_info = battle.character.add_xp(character_xp)
            player_obj = Player(**player_data)
            player_level_info = player_obj.add_xp(player_xp)
            reward_updates = {
                "$inc": {
                    "crystal": rewards["crystal"],
                    "valor": rewards["valor"],
                    "xp": player_xp,
                    "marks": rewards["marks"],
                    "total_xp": player_xp
                },
                "$set": {
                    "level": player_obj.level,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
            await db.players.update_one(
                {"user_id": user_id},
                reward_updates
            )
            battle.character.gas = battle.character_gas
            battle.character.max_gas = battle.character.gas  # Set max_gas to current gas after battle
            await db.update_character(battle.character)
            
            # GAS LOGIC: Calculate gas consumption based on battle conditions
            def calculate_gas_consumption(battle):
                # Example: base 1000, but can be changed by titan difficulty, etc.
                base = 1000
                if getattr(battle.titan, 'difficulty', 'Normal') == 'Hard':
                    return base + 500
                elif getattr(battle.titan, 'difficulty', 'Normal') == 'Easy':
                    return base - 200
                # Add more conditions as needed
                return base

            gas_consumed = calculate_gas_consumption(battle)
            new_gas = max(0, battle.character.gas - gas_consumed)
            new_max_gas = max(0, battle.character.max_gas - gas_consumed)
            battle.character.gas = new_gas
            battle.character.max_gas = new_max_gas
            await db.update_character(battle.character)
            # --- NEW LEVEL-UP REWARD SYSTEM (MATCHES EXPLORE) ---
            reward_msg = [
                f"<b>You have defeated {battle.titan.name}!</b>\n",
                f"⚡ <b>XP: +{rewards['xp']}</b>",
                f"🪙 <b>Marks: +{rewards['marks']}</b>"
            ]
            if rewards['crystal'] > 0:
                reward_msg.append(f"✨ Titan Crystals: {rewards['crystal']} ✨")
            if rewards['valor'] > 0:
                reward_msg.append(f"🔥 Valor Points: {rewards['valor']} 🔥")

            await query.edit_message_text("\n".join(reward_msg), parse_mode=ParseMode.HTML)

            # Character level up info (send as new messages)
            if char_level_info["total_level_ups"] > 0:
                for level_up in char_level_info["level_ups"]:
                    msg = [
                        f"🎊 {battle.character.name} leveled up! 🎊",
                        f"Level: {level_up['old_level']} → {level_up['new_level']}"
                    ]
                    if level_up['hp_increase'] > 0:
                        msg.append(f"💖 HP increased by {level_up['hp_increase']}!")
                    if level_up["newly_unlocked_abilities"]:
                        msg.append(f"\n🌟 New abilities unlocked:")
                        for ability in level_up["newly_unlocked_abilities"]:
                            ability_type = "🔥" if ability["type"] == "ultimate" else "⚡" if ability["type"] == "active" else "🛡️"
                            msg.append(f"{ability_type} {ability['name']} ({ability['description']})")
                    await send(chat_id, "\n".join(msg), parse_mode=ParseMode.HTML)

            # Player level up info (send as new messages for each level-up)
            if player_level_info["total_level_ups"] > 0:
                for level_up in player_level_info["level_ups"]:
                    rewards_new = level_up["rewards"]
                    reward_text = [
                        f"✨ LEVEL UP! ({level_up['old_level']} → {level_up['new_level']}) ✨",
                        "═══════════════════════",
                        f"🪙 Marks: +{rewards_new['marks']}",
                        f"⚔️ Valor: +{rewards_new['valor']}",
                        f"💠 Crystals: +{rewards_new['crystals']}"
                    ]
                    if rewards_new["unlocks"]:
                        reward_text.append("\n🔓 UNLOCKS:")
                        reward_text.extend(f"• {item}" for item in rewards_new["unlocks"])
                    await send(chat_id, "\n".join(reward_text), parse_mode=ParseMode.HTML)

            # --- Add player level-up rewards to player data ---
            if player_level_info["total_level_ups"] > 0:
                total_marks = 0
                total_valor = 0
                total_crystals = 0
                all_unlocks = []
                for lvl in player_level_info["level_ups"]:
                    r = lvl["rewards"]
                    total_marks += r.get("marks", 0)
                    total_valor += r.get("valor", 0)
                    total_crystals += r.get("crystals", 0)
                    all_unlocks.extend(r.get("unlocks", []))
                # Update player with all rewards from level-ups
                update_fields = {"$inc": {}}
                if total_marks:
                    update_fields["$inc"]["marks"] = update_fields["$inc"].get("marks", 0) + total_marks
                if total_valor:
                    update_fields["$inc"]["valor"] = update_fields["$inc"].get("valor", 0) + total_valor
                if total_crystals:
                    update_fields["$inc"]["crystal"] = update_fields["$inc"].get("crystal", 0) + total_crystals
                if all_unlocks:
                    # Add unlocks to a set to avoid duplicates
                    player_unlocks = set(getattr(player_obj, 'unlocks', []) or [])
                    player_unlocks.update(all_unlocks)
                    update_fields["$set"] = {"unlocks": list(player_unlocks)}
                if update_fields["$inc"] or update_fields.get("$set"):
                    await db.players.update_one({"user_id": user_id}, update_fields)

            try:
                track_battle_end(int(user_id), battle.character.name, "victory")
            except ImportError:
                pass
        else:
            battle.character.current_hp = 0  # Only current_hp is set to 0 on defeat, max HP remains unchanged
            battle.character.gas = battle.gas  
            await db.update_character(battle.character)
            await query.edit_message_text(
                f" {battle.character.name} was defeated by {battle.titan.name}!\n\n"
            )
            
            try:
                from utils.monitor import track_battle_end
                track_battle_end(int(user_id), battle.character.name, "defeat")
            except ImportError:
                pass
        
        if f"last_titan_{user_id}" in context.bot_data:
            del context.bot_data[f"last_titan_{user_id}"]
        if f"last_titan_data_{user_id}" in context.bot_data:
            del context.bot_data[f"last_titan_data_{user_id}"]
        await db.delete_titan(user_id)
        
        # --- Travel Progress Integration ---
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
                    # At a decision point: update player location and clear travel
                    await db.players.update_one(
                        {"user_id": user_id},
                        {"$set": {"location": new_location, "travel": {}}}
                    )
                    decision_point = True
                    location = new_location
                else:
                    # Arrived at normal location
                    await db.players.update_one(
                        {"user_id": user_id},
                        {"$set": {"location": new_location, "travel": {}}}
                    )
                    arrived = True
            else:
                # Save updated travel progress
                await db.players.update_one(
                    {"user_id": user_id},
                    {"$set": {"travel": travel}}
                )
            # If at a decision point, prompt for direction
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
                # Optionally notify arrival
                try:
                    await send(chat_id, f"You have arrived at <b>{location}</b>!", parse_mode="HTML")
                except Exception:
                    pass
        # --- End Travel Progress Integration ---
        cleanup_battle(user_id, "completed", battle)
    except Exception as e:
        logger.error(f"Error in handle_battle_end for user {user_id}: {e}")
        try:
            await query.edit_message_text("❌ An error occurred ending the battle. Please try /explore again.")
        except:
            pass
        cleanup_battle(user_id, "error", battle)

async def battle_timeout(user_id: str, query, battle: 'BattleSystem', context):
    user_id = str(user_id)
    try:
        battle.timeout_task = asyncio.current_task()
        await asyncio.sleep(60)
        if user_id in active_battles:
            db = context.bot_data.get("db")
            if db is None:
                logger.error("Database not initialized in context.bot_data")
                return
            try:
                await db.characters.update_one(
                    {"user_id": user_id, "name": battle.character.name},
                    {"$set": {
                        "current_hp": battle.character_hp,
                        "gas": battle.gas,
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
            # --- Titan cleanup on timeout ---
            try:
                await db.delete_titan(user_id)
            except Exception as e:
                logger.error(f"Failed to delete titan on timeout for user {user_id}: {e}")
            # --- End titan cleanup ---
            cleanup_battle(user_id, "timeout", battle)
    except asyncio.CancelledError:
        logger.debug(f"Battle timeout cancelled for user {user_id}")
    except Exception as e:
        logger.error(f"Error in battle_timeout for user {user_id}: {e}")
        cleanup_battle(user_id, "timeout_error", battle)
