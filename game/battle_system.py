from typing import Dict, List, Optional, Any, Tuple
from database.models import Character, Player, Titan, CharacterStats, generate_titan_xp
from database.characters import AbilityEffect, get_character_data, Ability
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db_instance import get_database
import asyncio
import random
import logging

logger = logging.getLogger(__name__)

# Global dictionary to track active battles
active_battles: Dict[int, 'BattleSystem'] = {}

class BattleSystem:
    def __init__(self, character: 'Character', titan: 'Titan', player: Optional['Player'] = None):
        self.character = character
        self.titan = titan
        self.player = player
        
        # Safely initialize stats with defaults if needed
        self.character_hp = getattr(character, 'current_hp', 100) or 100
        self.titan_hp = getattr(titan, 'max_hp', 100) or 100
        self.gas = getattr(character, 'gas', 1000) or 1000
        self.character_gas = self.gas  # Max gas
        
        # Ensure character stats are initialized
        if not hasattr(character, 'stats') or character.stats is None:
            from database.models import CharacterStats
            character.stats = CharacterStats(HP=650, ATK=25, DEF=10, SPD=10, ACC=10, INT=10)

        # Safely handle potentially None abilities
        active_abilities = character.active_abilities or []
        passive_abilities = character.passive_abilities or []
        self.ability_cooldowns = {ability.name: 0 for ability in active_abilities + passive_abilities}
        
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
        self.timeout_task: Optional[asyncio.Task] = None  # Track timeout task
        self._is_disposed = False  # Track if battle has been disposed
    
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
        
        # Clear timeout task reference
        self.timeout_task = None
        
        # Clear dictionaries but don't null the main objects until the very end
        if hasattr(self, 'buffs'):
            self.buffs.clear()
        if hasattr(self, 'debuffs'):
            self.debuffs.clear()
        if hasattr(self, 'titan_debuffs'):
            self.titan_debuffs.clear()
        if hasattr(self, 'ability_cooldowns'):
            self.ability_cooldowns.clear()
        if hasattr(self, 'trigger_states'):
            self.trigger_states.clear()
        
        # Mark as disposed but don't clear main references immediately
        # They will be garbage collected naturally after removal from active_battles

    def build_context(self, trigger: Optional[str] = None, ability: Optional[Ability] = None) -> Dict:
        """Build standardized battle context for ability effect functions."""
        # Safely get base damage
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
            
        passive_abilities = character_data.abilities.get("passive", {})
        if not passive_abilities:
            return messages
            
        for ability_name, ability in passive_abilities.items():
            if ability is None:
                continue
                
            # Check if passive ability is unlocked based on level requirement
            level_required = getattr(ability, 'level_required', 1)
            level_unlocked = self.character.level >= level_required
            
            # STRICTLY ENFORCE LEVEL REQUIREMENT - NO BYPASSING ALLOWED
            # An ability can ONLY be unlocked if character level meets requirement
            if not level_unlocked:
                continue  # Skip this ability entirely if level requirement not met
            
            # Only check other unlock conditions if level requirement is satisfied
            explicitly_unlocked = self.character.unlocked_abilities.get(ability_name, False) if self.character.unlocked_abilities else False
            definition_unlocked = getattr(ability, 'is_unlocked', True)  # Passives default to unlocked
            
            # Now check if it's enabled in definition or explicitly unlocked
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
            # Apply crit if available
            if self.buffs.get("crit_damage"):
                counter_dmg *= self.buffs["crit_damage"]
            self.titan_hp = max(0, self.titan_hp - counter_dmg)
            # Handle attack type effects
            attack_type = effect.counter_attack.get("type")
            if attack_type == "pierce":
                self.titan_debuffs["bleed"] = 3
            elif attack_type == "slash":
                self.titan_debuffs["damage_reduction"] = 0.1  # 10% damage reduction for titan
        
        # Apply buffs and debuffs safely
        if hasattr(effect, 'buffs') and effect.buffs:
            self.buffs.update(effect.buffs)
        if hasattr(effect, 'debuffs') and effect.debuffs:
            self.titan_debuffs.update(effect.debuffs)
        
        if hasattr(effect, 'clear_debuffs') and effect.clear_debuffs:
            self.debuffs.clear()
        
        if hasattr(effect, 'items_dropped') and effect.items_dropped:
            # Store dropped items for later pickup
            self.buffs["items_dropped"] = self.buffs.get("items_dropped", []) + effect.items_dropped
        
        if hasattr(effect, 'target_switched') and effect.target_switched:
            # Mark titan as potentially switching targets
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
            
        base_damage = max(15, self.titan.level * 8 + 10)  # Improved formula: level*8 + 10, min 15
        # Adjust damage based on difficulty
        damage_multipliers = {"Easy": 0.7, "Normal": 1.0, "Hard": 1.4}  # Slightly increased multipliers
        base_damage = int(base_damage * damage_multipliers.get(self.titan.difficulty, 1.0))
        
        # Apply special ability effects
        special_messages = []
        if self.titan.special_abilities:
            for ability in self.titan.special_abilities:
                if ability == "Armor Plating" and random.random() < 0.3:
                    base_damage = int(base_damage * 1.2)  # Increase damage instead
                    special_messages.append(f"⚔️ {self.titan.name}'s Armor Spikes deal extra damage!")
                elif ability == "Thunder Spear" and random.random() < 0.25:
                    base_damage = int(base_damage * 1.4)  # Increased from 1.2
                    special_messages.append(f"⚡ {self.titan.name} unleashes a Thunder Spear!")
                elif ability == "Regeneration" and random.random() < 0.4:
                    heal = int(self.titan.max_hp * 0.08)  # Increased from 0.05
                    self.titan_hp = min(self.titan.max_hp, self.titan_hp + heal)
                    special_messages.append(f"🩹 {self.titan.name} regenerates {heal} HP!")
                elif ability == "Berserker Rage" and self.titan_hp / self.titan.max_hp < 0.3:
                    base_damage = int(base_damage * 1.5)
                    special_messages.append(f"😡 {self.titan.name} enters berserker rage!")
                elif ability == "Steam Blast" and random.random() < 0.2:
                    # Apply burn damage over time
                    self.debuffs["burn"] = 3
                    special_messages.append(f"🔥 {self.titan.name} releases scalding steam!")
                elif ability == "Colossal Explosion" and random.random() < 0.15:
                    base_damage = int(base_damage * 2.0)
                    special_messages.append(f"💥 {self.titan.name} creates a massive explosion!")
        
        damage = int(base_damage * (1 - min(0.75, self.character.stats.DEF / 250)))  # Capped DEF reduction at 75%
        
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
        
        # Initialize defaults
        damage = 0
        message = ""
        effects = {}
        
        # Validate character and stats
        if not self.character or not self.character.stats:
            return damage, "Error: Character stats not available", effects
            
        # Get ability data
        character_data = get_character_data(self.character.character_type)
        if not character_data or not hasattr(character_data, 'abilities'):
            return damage, "Error: Character abilities not found", effects
            
        # Initialize empty effects
        effects = {
            "items_dropped": [],
            "target_switched": False,
            "bleed_applied": False
        }
        
        # Look for ability in active and ultimate abilities
        ability = None
        for ability_type in ["active", "ultimate"]:
            abilities = character_data.abilities.get(ability_type, {})
            if ability_name in abilities:
                ability = abilities[ability_name]
                break
        
        if not ability:
            return damage, f"Error: Ability {ability_name} not found", effects
            
        # Check if ability is on cooldown
        if self.ability_cooldowns.get(ability_name, 0) > 0:
            return damage, f"{ability_name} is on cooldown for {self.ability_cooldowns[ability_name]} turns!", effects
            
        # Check if enough gas is available
        gas_cost = getattr(ability, 'gas_cost', 20) or 20  # Default to 20 if not set or None
        if self.gas < gas_cost:
            return damage, f"Not enough gas to use {ability_name}!", effects
            
        # Deduct gas cost
        self.gas -= gas_cost
        
        # Build context for ability effect
        ctx = self.build_context("ability_use", ability)
        
        try:
            # Apply ability effect
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
            
        # Set cooldown if ability was used successfully
        cooldown = getattr(ability, 'cooldown', 1) or 1  # Default to 1 if not set or None
        self.ability_cooldowns[ability_name] = cooldown
        
        return damage, message, effects

    def has_usable_abilities(self) -> bool:
        """Check if the character has any usable abilities based on gas and cooldowns."""
        character_data = get_character_data(self.character.character_type)
        if character_data is None:
            return False
        for ability_type in ["active", "ultimate"]:
            abilities_of_type = character_data.abilities.get(ability_type, {})
            for ability_name, ability in abilities_of_type.items():
                if ability is None:
                    continue
                    
                # Check if ability is unlocked based on level requirement
                level_required = getattr(ability, 'level_required', 1)
                level_unlocked = self.character.level >= level_required
                
                # STRICTLY ENFORCE LEVEL REQUIREMENT - NO BYPASSING ALLOWED
                if not level_unlocked:
                    continue  # Skip this ability if level requirement not met
                
                # Only check other unlock conditions if level requirement is satisfied
                explicitly_unlocked = self.character.unlocked_abilities.get(ability_name, False) if self.character.unlocked_abilities else False
                definition_unlocked = getattr(ability, 'is_unlocked', False)
                
                # Now check if it's enabled in definition or explicitly unlocked
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
                
        # Update titan debuffs
        for debuff in list(self.titan_debuffs.keys()):
            if self.titan_debuffs[debuff] > 0:
                self.titan_debuffs[debuff] -= 1
                if self.titan_debuffs[debuff] <= 0:
                    del self.titan_debuffs[debuff]
                    
        # Update character buffs
        for buff in list(self.buffs.keys()):
            if isinstance(self.buffs[buff], (int, float)) and buff not in ["shield", "items_dropped"]:
                if self.buffs[buff] > 1:  # Duration-based buffs
                    self.buffs[buff] -= 1
                    if self.buffs[buff] <= 0:
                        del self.buffs[buff]
        
        # Apply damage over time effects
        if self.debuffs.get("burn", 0) > 0:
            burn_damage = max(5, self.titan.level * 2)
            self.character_hp = max(0, self.character_hp - burn_damage)
            self.debuffs["burn"] -= 1
            if self.debuffs["burn"] <= 0:
                del self.debuffs["burn"]
        
        # Apply bleed to titan
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
                    continue  # Skip items in status display
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

    def calculate_rewards(self, titan, character, player, explore_count, valor=0, crystal=0) -> dict:
        """Calculate rewards for defeating the titan with new random system."""
        base_xp = generate_titan_xp(titan.level, titan.difficulty)
        
        # Performance bonuses for XP
        performance_multiplier = 1.0
        if self.turn < 5:  # Quick victory bonus: +20% XP
            performance_multiplier += 0.2
        if self.character_hp / self.character.stats.HP > 0.8:  # High HP bonus: +15% XP
            performance_multiplier += 0.15
        if titan.difficulty == "Hard":  # Hard difficulty bonus: +30% XP
            performance_multiplier += 0.3
        
        rewards = {
            "xp": int(base_xp * performance_multiplier),
            "marks": random.randint(70, 120) + (titan.level * 2),  # Level-based marks
            "crystal": 0,
            "valor": 0,
        }

        # New difficulty-based bonus rewards
        difficulty_bonuses = {
            "Easy": {"mark_bonus": 10, "valor_chance": 0.35, "crystal_chance": 0.065},
            "Normal": {"mark_bonus": 25, "valor_chance": 0.40, "crystal_chance": 0.10},
            "Hard": {"mark_bonus": 50, "valor_chance": 0.60, "crystal_chance": 0.25}
        }
        
        bonus = difficulty_bonuses.get(titan.difficulty, difficulty_bonuses["Normal"])
        rewards["marks"] += bonus["mark_bonus"]

        # Valor points with new chances and Hard mode bonus
        if random.random() < bonus["valor_chance"] and player:
            required_explore = max(5, 30 - (titan.level // 2))  # Lower requirement for higher level titans
            if explore_count >= required_explore:
                valor_amount = random.randint(8, 20) + (titan.level // 2)
                if titan.difficulty == "Hard":
                    valor_amount += 1  # Fixed +1 bonus for Hard mode
                rewards["valor"] = int(valor_amount)

        # Crystals with new chances and Hard mode multiplier
        if random.random() < bonus["crystal_chance"]:
            required_explore = max(1, 15 - (titan.level // 3))
            if explore_count >= required_explore:
                crystal_amount = random.randint(1, 3) + (titan.level // 5)
                if titan.difficulty == "Hard":
                    crystal_amount = int(crystal_amount * 1.5)  # 1.5x multiplier for Hard mode
                rewards["crystal"] = crystal_amount

        return rewards
    

def cleanup_battle(user_id: int, result: str = "ended", battle: Optional[BattleSystem] = None):
    """Clean up battle state and resources"""
    battle_instance = battle or active_battles.get(user_id)
    
    if battle_instance:
        # Track battle end in monitor system
        try:
            from utils.monitor import track_battle_end
            username = getattr(battle_instance.character, 'name', 'Unknown') if battle_instance.character else 'Unknown'
            track_battle_end(user_id, username, result)
        except ImportError:
            pass
        
        # Use the dispose method for proper cleanup
        try:
            battle_instance.dispose()
        except Exception as e:
            logger.warning(f"Error disposing battle for user {user_id}: {e}")
        
        # Remove from active battles if it exists there
        if user_id in active_battles:
            del active_battles[user_id]
        
        logger.info(f"Battle {result} for user {user_id}. Active battles: {len(active_battles)}")
    else:
        # If not in active battles, still track the end
        try:
            from utils.monitor import track_battle_end
            track_battle_end(user_id, "Unknown", result)
        except ImportError:
            pass
    
    # Clean up any stale user activity tracking
    try:
        from utils.monitor import remove_player_activity
        remove_player_activity(user_id)
    except ImportError:
        pass


def generate_ability_keyboard(battle: BattleSystem) -> List[List[InlineKeyboardButton]]:
    """Generate keyboard buttons for valid abilities."""
    keyboard = []
    character_data = get_character_data(battle.character.character_type)
    
    logger.info(f"Generating abilities for {battle.character.name} (Level {battle.character.level})")
    
    if character_data is None:
        logger.warning(f"No character data found for {battle.character.character_type}")
        keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
        return keyboard
    
    # Active abilities
    active_abilities = character_data.abilities.get("active", {})
    for ability_name, ability in active_abilities.items():
        if ability is None:
            continue
            
        # Get the level requirement for this ability (default to 1 if not specified)
        level_required = getattr(ability, 'level_required', 1)
        
        # STRICTLY ENFORCE LEVEL REQUIREMENT - NO BYPASSING ALLOWED
        # Check if ability should be unlocked based on character level
        level_unlocked = battle.character.level >= level_required
        
        # Skip ability entirely if level requirement is not met
        if not level_unlocked:
            continue
        
        # Only check other unlock conditions if level requirement is satisfied
        definition_unlocked = getattr(ability, 'is_unlocked', False)
        unlocked_abilities = battle.character.unlocked_abilities or {}
        explicitly_unlocked = unlocked_abilities.get(ability_name, False)
        
        # Now check if it's enabled in definition or explicitly unlocked
        is_unlocked = definition_unlocked or explicitly_unlocked
        
        disabled_against_titans = getattr(ability, 'disabled_against_titans', False)
        gas_cost = getattr(ability, 'gas_cost', 0)
        ability_display_name = getattr(ability, 'name', ability_name)
        
        if (
            is_unlocked and
            not disabled_against_titans and
            battle.ability_cooldowns.get(ability_name, 0) == 0 and
            battle.gas >= gas_cost
        ):
            keyboard.append([InlineKeyboardButton(
                f"⚔️ {ability_display_name} ({gas_cost} gas)",
                callback_data=f"ability_{ability_name}"
            )])
        elif is_unlocked and battle.ability_cooldowns.get(ability_name, 0) > 0:
            # Show cooldown abilities but disable them
            cooldown = battle.ability_cooldowns[ability_name]
            keyboard.append([InlineKeyboardButton(
                f"⏳ {ability_display_name} (CD: {cooldown})",
                callback_data=f"cooldown_{ability_name}"
            )])
        elif is_unlocked and battle.gas < gas_cost:
            # Show low gas abilities but disable them
            keyboard.append([InlineKeyboardButton(
                f"⛽ {ability_display_name} (Need {gas_cost} gas)",
                callback_data=f"lowgas_{ability_name}"
            )])
    
    # Ultimate abilities  
    ultimate_abilities = character_data.abilities.get("ultimate", {})
    for ability_name, ability in ultimate_abilities.items():
        if ability is None:
            continue
            
        # Get the level requirement for this ability (default to 1 if not specified)
        level_required = getattr(ability, 'level_required', 1)
        
        # STRICTLY ENFORCE LEVEL REQUIREMENT - NO BYPASSING ALLOWED
        # Check if ability should be unlocked based on character level
        level_unlocked = battle.character.level >= level_required
        
        # Skip ability entirely if level requirement is not met
        if not level_unlocked:
            continue
        
        # Only check other unlock conditions if level requirement is satisfied
        definition_unlocked = getattr(ability, 'is_unlocked', False)
        unlocked_abilities = battle.character.unlocked_abilities or {}
        explicitly_unlocked = unlocked_abilities.get(ability_name, False)
        
        # Now check if it's enabled in definition or explicitly unlocked
        is_unlocked = definition_unlocked or explicitly_unlocked
        
        disabled_against_titans = getattr(ability, 'disabled_against_titans', False)
        gas_cost = getattr(ability, 'gas_cost', 0)
        ability_display_name = getattr(ability, 'name', ability_name)
        
        if (
            is_unlocked and
            not disabled_against_titans and
            battle.ability_cooldowns.get(ability_name, 0) == 0 and
            battle.gas >= gas_cost
        ):
            keyboard.append([InlineKeyboardButton(
                f"✨ {ability_display_name} ({gas_cost} gas) ✨",
                callback_data=f"ability_{ability_name}"
            )])
        elif is_unlocked and battle.ability_cooldowns.get(ability_name, 0) > 0:
            cooldown = battle.ability_cooldowns[ability_name]
            keyboard.append([InlineKeyboardButton(
                f"⏳ ✨ {ability_display_name} (CD: {cooldown}) ✨",
                callback_data=f"cooldown_{ability_name}"
            )])
    
    # Passive abilities with gas costs (can be manually triggered)
    logger.info(f"Checking passive abilities for {battle.character.name}")
    passive_abilities = character_data.abilities.get("passive", {})
    for ability_name, ability in passive_abilities.items():
        if ability is None:
            continue
            
        # Get the level requirement for this ability (default to 1 if not specified)
        level_required = getattr(ability, 'level_required', 1)
        
        # STRICTLY ENFORCE LEVEL REQUIREMENT - NO BYPASSING ALLOWED
        # Check if ability should be unlocked based on character level
        level_unlocked = battle.character.level >= level_required
        
        # Skip ability entirely if level requirement is not met
        if not level_unlocked:
            continue
        
        # Only check other unlock conditions if level requirement is satisfied
        definition_unlocked = getattr(ability, 'is_unlocked', True)  # Passives default to unlocked
        unlocked_abilities = battle.character.unlocked_abilities or {}
        explicitly_unlocked = unlocked_abilities.get(ability_name, False)
        
        # Now check if it's enabled in definition or explicitly unlocked
        is_unlocked = definition_unlocked or explicitly_unlocked
        
        gas_cost = getattr(ability, 'gas_cost', 0)
        disabled_against_titans = getattr(ability, 'disabled_against_titans', False)
        ability_display_name = getattr(ability, 'name', ability_name)
        
        logger.info(f"  {ability_name}: level_req={level_required}, char_level={battle.character.level}, "
                   f"def_unlocked={definition_unlocked}, gas_cost={gas_cost}, is_unlocked={is_unlocked}, "
                   f"level_check_passed={battle.character.level >= level_required}")
        
        # Only show passive abilities that have gas costs (can be manually activated)
        if (
            is_unlocked and
            gas_cost > 0 and
            not disabled_against_titans and
            battle.ability_cooldowns.get(ability_name, 0) == 0 and
            battle.gas >= gas_cost
        ):
            keyboard.append([InlineKeyboardButton(
                f"🔄 {ability_display_name} ({gas_cost} gas)",
                callback_data=f"ability_{ability_name}"
            )])
            logger.info(f"    ✅ Added {ability_name} to keyboard")
        elif (
            is_unlocked and
            gas_cost > 0 and
            not disabled_against_titans and
            battle.gas < gas_cost
        ):
            # Show low gas passive abilities but disable them
            keyboard.append([InlineKeyboardButton(
                f"⛽ 🔄 {ability_display_name} (Need {gas_cost} gas)",
                callback_data=f"lowgas_{ability_name}"
            )])
    
    # Always show basic attack option (costs 20 gas)
    if battle.gas >= 20:
        keyboard.append([InlineKeyboardButton("⚔️ Basic Attack (20 gas)", callback_data="action_basic_attack")])
    else:
        keyboard.append([InlineKeyboardButton("⛽ Basic Attack (Need 20 gas)", callback_data="lowgas_basic_attack")])
    
    # Always show run option
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
    
    # Handle both old and new callback formats for backward compatibility
    callback_data = query.data
    if not callback_data:
        await query.edit_message_text("Invalid battle request.")
        return
        
    logger.info(f"Battle start request: {callback_data} from user {update.effective_user.id}")
    
    if not callback_data.startswith("battle_"):
        await query.edit_message_text("Invalid battle request.")
        return
    
    # Extract user_id - handle both formats
    if callback_data.count("_") == 1 and callback_data.split("_")[1].isdigit():
        # New format: battle_{user_id}
        user_id = int(callback_data.split("_")[1])
    else:
        # Old format: battle_{titan_name} - extract user_id from update
        user_id = update.effective_user.id
        titan_name = callback_data[7:]  # Remove "battle_" prefix
        
        # For old format, check if it matches the last titan name
        last_titan = context.bot_data.get(f"last_titan_{user_id}")
        if titan_name != last_titan:
            await query.edit_message_text("⚠️ This titan encounter has expired. Please use /explore to find a new titan.")
            return
    
    # Verify this matches the user who clicked (for new format)
    if user_id != update.effective_user.id:
        await query.edit_message_text("You cannot start someone else's battle!")
        return
    
    db = await get_database()
    
    # Get the stored titan data instead of looking in database
    titan = context.bot_data.get(f"last_titan_data_{user_id}")
    if not titan:
        await query.edit_message_text("Error: Titan data not found. Please use /explore to find a new titan.")
        return
    
    player = await db.players.find_one({"user_id": user_id})
    if not player or 'team' not in player or not player['team']:
        await query.edit_message_text("Error: No character in your team.")
        return
    
    character_name = player['team'][0].get('character_name') if isinstance(player['team'][0], dict) else player['team'][0]
    character = await db.get_character(user_id, character_name)

    if not character:
        await query.edit_message_text(f"Error: Character {character_name} not found.")
        return
    
    player_obj = Player(**player) if player else None
    battle = BattleSystem(character, titan, player_obj)
    active_battles[user_id] = battle
    
    # Track battle start
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
    
    # Log battle start for monitoring
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
            f"<b>Gas: {status['gas']}/{battle.character.gas}</b>\n\n"
            f"{status['status_message']}\n"
            f"<b>Choose your action:</b>"
        ),
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    asyncio.create_task(battle_timeout(user_id, query, battle))

async def handle_battle_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle battle actions with immediate titan response."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if user_id not in active_battles:
        await query.edit_message_text("Titan has ran away")
        return
    
    battle = active_battles[user_id]
    action = query.data
    if not action:
        return
        
    full_message = []
    effects = {}
    
    if battle.timeout_task:
        battle.timeout_task.cancel()  # Cancel previous timeout
        battle.timeout_task = None  # Clear the reference
    
    if action == "action_run":
        escape_chance = 0.7  # 70% chance to escape
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
        if (battle.gas or 0) >= 20:  # Handle None gas value
            battle.gas = (battle.gas or 0) - 20  # Handle None gas value
            try:
                # Calculate basic attack damage: ATK stat + random factor
                stats = getattr(battle.character, 'stats', None)
                base_damage = 25  # Default base damage
                if stats and hasattr(stats, 'ATK') and stats.ATK is not None:
                    base_damage = stats.ATK or 25  # Use 25 if ATK is None
                    
                damage_variance = random.randint(-2, 3)  # -2 to +3 damage variance
                total_damage = max(1, base_damage + damage_variance)  # Minimum 1 damage
                
                # Handle None titan_hp
                current_titan_hp = battle.titan_hp if battle.titan_hp is not None else 0
                battle.titan_hp = max(0, current_titan_hp - total_damage)
            except Exception as e:
                logger.error(f"Error calculating basic attack damage: {e}")
                total_damage = 10  # Fallback damage if something goes wrong
                current_titan_hp = battle.titan_hp if battle.titan_hp is not None else 0
                battle.titan_hp = max(0, current_titan_hp - total_damage)
            
            full_message.append(f"⚔️ {battle.character.name} attacks with basic strike, dealing {total_damage} damage!")
        else:
            full_message.append(f"❌ {battle.character.name} doesn't have enough gas for basic attack!")
    elif action.startswith("ability_"):
        ability_name = action[8:]
        damage, message, effects = battle.use_ability(ability_name)
        full_message.append(message)
        if effects.get("items_dropped"):
            full_message.append(f"Dropped item: {', '.join(effects['items_dropped'])}")
        if effects.get("target_switched"):
            full_message.append("Titan switched targets!")
        if effects.get("bleed_applied"):
            full_message.append("Titan is bleeding!")
    
    if battle.titan_hp <= 0:
        await handle_battle_end(query, battle, user_id, context)
        return

    unlocked_passives = [
        ability for ability in battle.character.passive_abilities 
        if getattr(ability, 'unlocked', False) and getattr(ability, 'gas_cost', 0) > 0
    ]
    if unlocked_passives and battle.gas < min(getattr(ability, 'gas_cost', float('inf')) for ability in unlocked_passives):
        min_cost = min(getattr(ability, 'gas_cost', float('inf')) for ability in unlocked_passives)
        message =(
            f"{battle.character.name} is out of gas and cannot continue the battle!"
        )
    # Only retreat if gas is less than the CHEAPEST ability's cost
        await query.edit_message_text(message        )
        cleanup_battle(user_id, "out_of_gas")
        return
    
    if battle.character_hp > 0:
        titan_damage, titan_message = battle.titan_attack()
        full_message.append(titan_message)
    
    battle.turn += 1
    battle.update_cooldowns()
    
    db = await get_database()
    await db.characters.update_one(
        {"user_id": user_id, "name": battle.character.name},
        {"$set": {
            "current_hp": battle.character_hp,
            "gas": battle.gas,
            "ability_cooldowns": battle.ability_cooldowns
        }}
    )
    
    if battle.character_hp <= 0:
        await handle_battle_end(query, battle, user_id, context)
        return
    if battle.titan_hp <= 0:
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
        f"<b>Gas: {status['gas']}/{battle.character.gas}</b>\n\n"
        f"{status['status_message']}\n"
    )
    
    await query.edit_message_text(
        text=battle_message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    asyncio.create_task(battle_timeout(user_id, query, battle))

async def handle_battle_end(query, battle: BattleSystem, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Handle battle end with rewards or defeat message."""
    try:
        if battle.timeout_task and not battle.timeout_task.done():
            battle.timeout_task.cancel()
        
        db = await get_database()

        player_data = await db.players.find_one({"user_id": user_id})
        if not player_data:
            await query.edit_message_text("❌ Player data not found!")
            cleanup_battle(user_id, "error", battle)
            return

        explore_count = player_data.get("explore_count", 0)
        
        # Always increment explore_count regardless of victory or defeat
        await db.players.update_one(
            {"user_id": user_id},
            {"$inc": {"explore_count": 1}}
        )
        
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
            
            # Add XP to character and get level up info
            char_level_info = battle.character.add_xp(character_xp)
            
            # Add XP to player and get level up info
            player_obj = Player(**player_data)
            player_level_info = player_obj.add_xp(player_xp)
            
            # Update rewards but don't include explore_count since we already incremented it
            reward_updates = {
                "$inc": {
                    "crystal": rewards["crystal"],
                    "valor": rewards["valor"],
                    "xp": player_xp,
                    "marks": rewards["marks"]
                }
            }
            
            # Add any bonus rewards from player level up
            if player_level_info["leveled_up"]:
                if player_level_info["bonus_marks"] > 0:
                    reward_updates["$inc"]["marks"] += player_level_info["bonus_marks"]
                if player_level_info["bonus_crystals"] > 0:
                    reward_updates["$inc"]["crystal"] += player_level_info["bonus_crystals"]
                if player_level_info["bonus_valor"] > 0:
                    reward_updates["$inc"]["valor"] += player_level_info["bonus_valor"]
            
            await db.players.update_one(
                {"user_id": user_id},
                reward_updates
            )
            
            battle.character.current_hp = battle.character.stats.HP
            battle.character.gas = battle.character_gas
            await db.update_character(battle.character)
            
            reward_msg = [
                f"🎉 {battle.character.name} defeated {battle.titan.name}! 🎉",
                f"\nRewards:",
                f"XP: {rewards['xp']}",
                f"Marks: {rewards['marks']}"
            ]
            if rewards['crystal'] > 0:
                reward_msg.append(f"✨ Titan Crystals: {rewards['crystal']} ✨")
            if rewards['valor'] > 0:
                reward_msg.append(f"🔥 Valor Points: {rewards['valor']} 🔥")
            
            # Add character level up notifications
            if char_level_info and isinstance(char_level_info, dict) and char_level_info.get("leveled_up", False):
                reward_msg.append(f"\n🎊 {battle.character.name} leveled up! 🎊")
                reward_msg.append(f"Level: {char_level_info.get('old_level', 0)} → {char_level_info.get('new_level', 0)}")
                
                if char_level_info.get("hp_increase", 0) > 0:
                    reward_msg.append(f"💖 HP increased by {char_level_info['hp_increase']}!")
                
                # Show newly unlocked abilities
                if char_level_info.get("new_abilities"):
                    reward_msg.append(f"\n🌟 New abilities unlocked:")
                    for ability in char_level_info["new_abilities"]:
                        ability_type = "🔥" if ability.get("type") == "ultimate" else "⚡" if ability.get("type") == "active" else "🛡️"
                        reward_msg.append(f"{ability_type} {ability.get('name')} (Level {ability.get('level_required')})")
            
            # Add player level up notifications
            if player_level_info and isinstance(player_level_info, dict) and player_level_info.get("leveled_up", False):
                reward_msg.append(f"\n🎉 Player leveled up! 🎉")
                reward_msg.append(f"Player Level: {player_level_info.get('old_level', 0)} → {player_level_info.get('new_level', 0)}")
                
                # Show bonus rewards from player level up
                bonus_rewards = []
                if player_level_info.get("bonus_marks", 0) > 0:
                    bonus_rewards.append(f"Marks: +{player_level_info['bonus_marks']}")
                if player_level_info.get("bonus_crystals", 0) > 0:
                    bonus_rewards.append(f"Crystals: +{player_level_info['bonus_crystals']}")
                if player_level_info.get("bonus_valor", 0) > 0:
                    bonus_rewards.append(f"Valor: +{player_level_info['bonus_valor']}")
                
                if bonus_rewards:
                    reward_msg.append(f"🎁 Level up bonus: {', '.join(bonus_rewards)}")
            
            await query.edit_message_text("\n".join(reward_msg))
            
            # Track victory
            try:
                from utils.monitor import track_battle_end
                track_battle_end(user_id, battle.character.name, "victory")
            except ImportError:
                pass
        else:
            battle.character.current_hp = 0
            await db.update_character(battle.character)
            await query.edit_message_text(
                f"💀 {battle.character.name} was defeated by {battle.titan.name}! 💀\n\n"
            )
            
            # Track defeat
            try:
                from utils.monitor import track_battle_end
                track_battle_end(user_id, battle.character.name, "defeat")
            except ImportError:
                pass
        
        # Clean up stored titan data to prevent memory leaks
        if f"last_titan_{user_id}" in context.bot_data:
            del context.bot_data[f"last_titan_{user_id}"]
        if f"last_titan_data_{user_id}" in context.bot_data:
            del context.bot_data[f"last_titan_data_{user_id}"]
        
        # Remove from active battles using cleanup with battle instance
        cleanup_battle(user_id, "completed", battle)
        
    except Exception as e:
        logger.error(f"Error in handle_battle_end for user {user_id}: {e}")
        try:
            await query.edit_message_text("❌ An error occurred ending the battle. Please try /explore again.")
        except:
            pass
        cleanup_battle(user_id, "error", battle)

async def battle_timeout(user_id: int, query, battle: BattleSystem):
    """Handle battle timeout after 1 minute of inactivity."""
    try:
        battle.timeout_task = asyncio.current_task()
        await asyncio.sleep(60)
        
        if user_id in active_battles:
            db = await get_database()
            
            # Save character state before cleanup
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
            
            cleanup_battle(user_id, "timeout", battle)
            
    except asyncio.CancelledError:
        # This is expected when the timeout is cancelled
        logger.debug(f"Battle timeout cancelled for user {user_id}")
    except Exception as e:
        logger.error(f"Error in battle_timeout for user {user_id}: {e}")
        cleanup_battle(user_id, "timeout_error", battle)

    async def handle_victory(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle victory and reward EXP"""
        db = get_database()
        
        # Calculate Character Combat EXP
        damage_taken = self.character_hp < self.character.stats.HP
        overkill_damage = abs(self.titan_hp) if self.titan_hp < 0 else 0
        
        # Check if this is the first time killing this type of titan
        is_first_kill = False
        if self.player:
            killed_titans = self.player.inventory.get('killed_titans', {})
            if str(self.titan.titan_id) not in killed_titans:
                is_first_kill = True
                killed_titans[str(self.titan.titan_id)] = 1
                self.player.inventory['killed_titans'] = killed_titans
                await db.update_player(
                    user_id=self.player.user_id,
                    update_data={"inventory": self.player.inventory}
                )

        # Calculate and award Character EXP
        combat_exp = self.character.calculate_combat_exp(
            turns=self.turn,
            damage_taken=damage_taken,
            overkill_damage=overkill_damage,
            is_first_kill=is_first_kill
        )
        
        self.character.xp += combat_exp
        self.character.total_xp += combat_exp
        
        # Level up the character if needed
        level_ups = 0
        while self.character.xp >= self.character.xp_to_next_level and self.character.level < 125:
            self.character.level_up()
            level_ups += 1
        
        # Calculate and award Player EXP if applicable
        player_exp = 0
        player_level_ups = 0
        if self.player:
            player_exp = self.player.calculate_exp_gain('titan_kill')
            self.player.xp += player_exp
            self.player.total_xp += player_exp
            
            # Level up the player if needed
            while self.player.xp >= self.player.xp_to_next_level:
                self.player.level_up()
                player_level_ups += 1
            
            if player_level_ups > 0:
                await db.update_player(
                    user_id=self.player.user_id,
                    update_data={
                        "level": self.player.level,
                        "xp": self.player.xp,
                        "total_xp": self.player.total_xp
                    }
                )
        
        # Update character in database
        await db.update_character(
            user_id=self.character.user_id,
            character_name=self.character.name,
            update_data={
                "level": self.character.level,
                "xp": self.character.xp,
                "total_xp": self.character.total_xp,
                "stats": self.character.stats.dict()
            }
        )
        
        # Prepare victory message
        victory_text = (
            f"Victory! 🎉\n"
            f"Character EXP gained: {combat_exp:,}"
        )
        
        if level_ups > 0:
            victory_text += f"\nCharacter leveled up {level_ups} times! 🆙"
            
        if self.player and player_exp > 0:
            victory_text += f"\nPlayer EXP gained: {player_exp:,}"
            if player_level_ups > 0:
                victory_text += f"\nPlayer leveled up {player_level_ups} times! ⭐"
        
        # Send message with proper error handling
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.edit_text(
                    victory_text,
                    reply_markup=None
                )
        except Exception as e:
            logger.error(f"Failed to send victory message: {e}")
            # Try sending as new message if edit fails
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=victory_text
                )
            except Exception as e:
                logger.error(f"Failed to send new victory message: {e}")
