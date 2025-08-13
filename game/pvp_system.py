import asyncio
import random
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram import error
from database.db import Database
from database.models import Character, Player, TeamMember
from database.schemas import Ability, CharacterStats
from game.battle_system import BattleSystem, active_battles, active_battles_lock, cleanup_battle
from utils.monitor import track_battle_end

logger = logging.getLogger(__name__)

# Global dictionaries to track PVP challenges and battles
pvp_challenges: Dict[str, Dict[str, Any]] = {}
active_pvp_battles: Dict[str, 'PvPBattleSystem'] = {}

# Rate limiting parameters
MAX_RETRIES = 5
BASE_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 30.0

async def safe_api_call(api_func, *args, **kwargs):
    retries = 0
    delay = BASE_RETRY_DELAY
    
    while True:
        try:
            return await api_func(*args, **kwargs)
        except error.RetryAfter as e:
            retries += 1
            if retries > MAX_RETRIES:
                logger.error(f"Max retries exceeded for {api_func.__name__}: {e}")
                raise
                
            # Calculate delay with exponential backoff
            retry_after = float(e.retry_after)
            # Use the greater of suggested retry time or calculated backoff
            delay = max(retry_after, min(delay * 2, MAX_RETRY_DELAY))
            
            logger.info(f"Rate limited. Retrying {api_func.__name__} after {delay:.1f}s (retry {retries}/{MAX_RETRIES})")
            await asyncio.sleep(delay)
        except error.TimedOut:
            retries += 1
            if retries > MAX_RETRIES:
                logger.error(f"Max retries exceeded for {api_func.__name__}: Timed out")
                raise
                
            delay = min(delay * 2, MAX_RETRY_DELAY)
            logger.info(f"Request timed out. Retrying {api_func.__name__} after {delay:.1f}s (retry {retries}/{MAX_RETRIES})")
            await asyncio.sleep(delay)
        except error.BadRequest as e:
            # Special handling for callback query expired errors
            if "Query is too old" in str(e) or "query id is invalid" in str(e):
                logger.warning(f"Callback query expired: {e}")
                # Raise a specific exception that can be caught and handled appropriately
                raise error.BadRequest(f"Query is too old and response timeout expired or query id is invalid")
            # For other BadRequest errors, don't retry
            logger.error(f"Bad request in {api_func.__name__}: {e}")
            raise
        except Exception as e:
            # For other exceptions, don't retry
            logger.error(f"Error in {api_func.__name__}: {e}")
            raise

class PvPBattleSystem:
    """
    Manages a battle between two player characters, handling turns, abilities, and battle logic.
    """
    def __init__(self, 
                 challenger: Character, 
                 defender: Character, 
                 challenger_player: Player, 
                 defender_player: Player,
                 challenge_id: str):
        self.challenger = challenger
        self.defender = defender
        self.challenger_player = challenger_player
        self.defender_player = defender_player
        self.challenge_id = challenge_id
        
        # Initialize battle state
        self.challenger_hp: int = challenger.current_hp
        self.defender_hp: int = defender.current_hp
        self.challenger_gas: int = challenger.gas
        self.defender_gas: int = defender.gas
        
        # Track initial values
        self.initial_challenger_gas: int = challenger.gas
        self.initial_defender_gas: int = defender.gas
        
        # Battle state tracking
        self.current_turn: str = challenger.name  # Challenger goes first
        self.turn_count: int = 0
        self.battle_ended: bool = False
        self.timeout_task: Optional[asyncio.Task] = None
        self._is_disposed: bool = False
        self.winner: Optional[str] = None
        self.winner_char: Optional[Character] = None
        
        # Ability cooldowns for both characters - ensure clean ability names
        self.challenger_cooldowns: Dict[str, int] = {
            ability.name.strip().lstrip('_'): 0 for ability in (
                (challenger.active_abilities or []) +
                (challenger.passive_abilities or []) +
                (challenger.ultimate_abilities or [])
            )
        }
        self.defender_cooldowns: Dict[str, int] = {
            ability.name.strip().lstrip('_'): 0 for ability in (
                (defender.active_abilities or []) +
                (defender.passive_abilities or []) +
                (defender.ultimate_abilities or [])
            )
        }
        
        # Buffs and debuffs
        self.challenger_buffs: Dict[str, Any] = {}
        self.defender_buffs: Dict[str, Any] = {}
        self.challenger_debuffs: Dict[str, int] = {}
        self.defender_debuffs: Dict[str, int] = {}
        
        # Character switched count (for PVP balancing)
        self.switches_remaining: int = 10  # Default max switches

    def dispose(self) -> None:
        """Clean up battle resources and reset state."""
        if self._is_disposed:
            return
        self._is_disposed = True
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()
        self.timeout_task = None
        self.challenger_buffs.clear()
        self.defender_buffs.clear()
        self.challenger_debuffs.clear()
        self.defender_debuffs.clear()
        self.challenger_cooldowns.clear()
        self.defender_cooldowns.clear()
    
    def get_equipped_weapon(self, character: Character, shop_items: Dict):
        """Get character's equipped weapon from shop items"""
        if character.equipped_weapon and character.equipped_weapon in shop_items:
            return shop_items[character.equipped_weapon]
        return None
        
    def switch_turn(self) -> None:
        """Switch turn between challenger and defender"""
        self.turn_count += 1
        if self.current_turn == self.challenger.name:
            self.current_turn = self.defender.name
        else:
            self.current_turn = self.challenger.name
        
        # Update cooldowns for current player
        self.update_cooldowns()
    
    def update_cooldowns(self) -> None:
        """Update cooldowns for the current player"""
        if self.current_turn == self.challenger.name:
            # Update challenger cooldowns
            for ability_name in list(self.challenger_cooldowns.keys()):
                if self.challenger_cooldowns[ability_name] > 0:
                    self.challenger_cooldowns[ability_name] -= 1
            
            # Update challenger debuffs
            for debuff in list(self.challenger_debuffs.keys()):
                if self.challenger_debuffs[debuff] > 0:
                    self.challenger_debuffs[debuff] -= 1
                    if self.challenger_debuffs[debuff] <= 0:
                        del self.challenger_debuffs[debuff]
            
            # Update buffs
            for buff in list(self.challenger_buffs.keys()):
                if isinstance(self.challenger_buffs[buff], (int, float)) and buff != "shield":
                    if self.challenger_buffs[buff] > 1:
                        self.challenger_buffs[buff] -= 1
                        if self.challenger_buffs[buff] <= 0:
                            del self.challenger_buffs[buff]
            
            # Handle bleed effect
            if self.challenger_debuffs.get("bleed", 0) > 0:
                bleed_damage = max(10, self.defender.stats.ATK // 2)
                self.challenger_hp = max(0, self.challenger_hp - bleed_damage)
        else:
            # Update defender cooldowns
            for ability_name in list(self.defender_cooldowns.keys()):
                if self.defender_cooldowns[ability_name] > 0:
                    self.defender_cooldowns[ability_name] -= 1
            
            # Update defender debuffs
            for debuff in list(self.defender_debuffs.keys()):
                if self.defender_debuffs[debuff] > 0:
                    self.defender_debuffs[debuff] -= 1
                    if self.defender_debuffs[debuff] <= 0:
                        del self.defender_debuffs[debuff]
            
            # Update buffs
            for buff in list(self.defender_buffs.keys()):
                if isinstance(self.defender_buffs[buff], (int, float)) and buff != "shield":
                    if self.defender_buffs[buff] > 1:
                        self.defender_buffs[buff] -= 1
                        if self.defender_buffs[buff] <= 0:
                            del self.defender_buffs[buff]
            
            # Handle bleed effect
            if self.defender_debuffs.get("bleed", 0) > 0:
                bleed_damage = max(10, self.challenger.stats.ATK // 2)
                self.defender_hp = max(0, self.defender_hp - bleed_damage)
    
    async def use_ability(self, ability_name: str, context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, Dict]:
        """
        Use an ability in PvP battle. Returns message and effects.
        """
        message = ""
        effects = {}
        
        # Determine current character and opponent
        if self.current_turn == self.challenger.name:
            current_char = self.challenger
            opponent_char = self.defender
            cooldowns = self.challenger_cooldowns
            gas = self.challenger_gas
        else:
            current_char = self.defender
            opponent_char = self.challenger
            cooldowns = self.defender_cooldowns
            gas = self.defender_gas
        
        # Find the ability in character abilities
        from database.characters import get_character_data
        character_data = get_character_data(current_char.character_type)
        if not character_data:
            return "Error: Character abilities not found", {}
        
        ability = None
        # Clean any potential leading or trailing whitespace and any leading underscores from ability name
        cleaned_ability_name = ability_name.strip().lstrip('_')
        
        # Debug logging to help diagnose the issue
        logger.debug(f"Looking for ability: '{cleaned_ability_name}' (original: '{ability_name}')")
        
        for ability_type in ["active", "passive", "ultimate"]:
            abilities = getattr(character_data, f"{ability_type}_abilities", [])
            for ab in abilities:
                if ab.name == cleaned_ability_name:
                    ability = ab
                    break
            if ability:
                break
                
        if not ability:
            return f"Error: Ability {cleaned_ability_name} not found", {}
            
        # Check cooldown
        if cooldowns.get(ability_name, 0) > 0:
            return f"{ability_name} is on cooldown for {cooldowns[ability_name]} turns!", {}
            
        # Check gas cost
        gas_cost = ability.gas_cost or 20
        if gas < gas_cost:
            return f"Not enough gas to use {ability_name}!", {}
            
        # Deduct gas
        if self.current_turn == self.challenger.name:
            self.challenger_gas -= gas_cost
        else:
            self.defender_gas -= gas_cost
            
        # Build context for ability effect
        ctx = {
            "character_stats": current_char.stats.dict() if current_char.stats else {},
            "opponent_stats": opponent_char.stats.dict() if opponent_char.stats else {},
            "character_hp": self.challenger_hp if self.current_turn == self.challenger.name else self.defender_hp,
            "opponent_hp": self.defender_hp if self.current_turn == self.challenger.name else self.challenger_hp,
            "character_max_hp": current_char.stats.HP,
            "opponent_max_hp": opponent_char.stats.HP,
            "pvp": True,  # Flag to indicate PvP context
            "turn": self.turn_count,
            "gas": gas - gas_cost,
            "character_level": current_char.level,
            "opponent_level": opponent_char.level,
            "is_pvp": True,
        }
        
        # Apply ability effect
        try:
            if ability.effect_function:
                from database.characters import AbilityEffect
                effect = ability.effect_function(ctx)
                if effect:
                    # Apply the effect
                    damage = getattr(effect, 'damage', 0) or 0
                    heal = getattr(effect, 'healed', 0) or 0
                    message = getattr(effect, 'message', f"{ability_name} used successfully!")
                    
                    # Apply damage to opponent
                    if self.current_turn == self.challenger.name:
                        self.defender_hp = max(0, self.defender_hp - damage)
                        if heal > 0:
                            self.challenger_hp = min(self.challenger.stats.HP, self.challenger_hp + heal)
                            
                        # Apply other effects
                        if getattr(effect, 'shield', 0):
                            self.challenger_buffs["shield"] = self.challenger_buffs.get("shield", 0) + effect.shield
                        if getattr(effect, 'stun_duration', 0):
                            self.defender_debuffs["stun"] = max(self.defender_debuffs.get("stun", 0), effect.stun_duration)
                        if getattr(effect, 'bleed_applied', False):
                            self.defender_debuffs["bleed"] = max(self.defender_debuffs.get("bleed", 0), 3)
                    else:
                        self.challenger_hp = max(0, self.challenger_hp - damage)
                        if heal > 0:
                            self.defender_hp = min(self.defender.stats.HP, self.defender_hp + heal)
                            
                        # Apply other effects
                        if getattr(effect, 'shield', 0):
                            self.defender_buffs["shield"] = self.defender_buffs.get("shield", 0) + effect.shield
                        if getattr(effect, 'stun_duration', 0):
                            self.challenger_debuffs["stun"] = max(self.challenger_debuffs.get("stun", 0), effect.stun_duration)
                        if getattr(effect, 'bleed_applied', False):
                            self.challenger_debuffs["bleed"] = max(self.challenger_debuffs.get("bleed", 0), 3)
                    
                    # Return effects for UI updates
                    effects = {
                        "damage": damage,
                        "heal": heal,
                        "shield": getattr(effect, 'shield', 0) or 0,
                        "stun": getattr(effect, 'stun_duration', 0) or 0,
                        "bleed": getattr(effect, 'bleed_applied', False)
                    }
        except Exception as e:
            logger.error(f"Error applying ability {ability_name}: {e}")
            return f"Error using {ability_name}: {str(e)}", {}
            
        # Apply cooldown - use the cleaned ability name
        cooldowns[cleaned_ability_name] = ability.cooldown or 1
        
        # Check if battle has ended
        if self.challenger_hp <= 0 or self.defender_hp <= 0:
            self.battle_ended = True
            if self.challenger_hp <= 0:
                self.winner = self.defender.name
                self.winner_char = self.defender
            else:
                self.winner = self.challenger.name
                self.winner_char = self.challenger
                
        return message, effects
        
    async def use_basic_attack(self, context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, Dict]:
        """
        Use basic attack in PvP battle. Returns message and effects.
        """
        message = ""
        effects = {"damage": 0}
        
        # Determine current character and opponent
        if self.current_turn == self.challenger.name:
            current_char = self.challenger
            opponent_char = self.defender
            gas = self.challenger_gas
        else:
            current_char = self.defender
            opponent_char = self.challenger
            gas = self.defender_gas
            
        # Check gas cost - basic attacks cost 20 gas
        if gas < 20:
            return f"Not enough gas to perform a basic attack!", {}
            
        # Deduct gas
        if self.current_turn == self.challenger.name:
            self.challenger_gas -= 20
        else:
            self.defender_gas -= 20
            
        # Get equipped weapon for damage calculation
        shop_items = context.bot_data.get("shop_items") or {}
        weapon = self.get_equipped_weapon(current_char, shop_items)
        
        # Calculate damage
        if weapon:
            damage_min = weapon.attributes.get("damage_min", 10)
            damage_max = weapon.attributes.get("damage_max", 20)
            total_damage = random.randint(int(damage_min), int(damage_max))
            weapon_name = weapon.name
        else:
            try:
                base_damage = current_char.stats.ATK or 25
                total_damage = max(1, base_damage + random.randint(-2, 3))
                weapon_name = "basic strike"
            except Exception as e:
                logger.error(f"Error calculating basic attack damage: {e}")
                total_damage = 10
                weapon_name = "basic strike"
                
        # Apply damage
        if self.current_turn == self.challenger.name:
            self.defender_hp = max(0, self.defender_hp - total_damage)
        else:
            self.challenger_hp = max(0, self.challenger_hp - total_damage)
            
        message = f"⚔️ {current_char.name} attacks with {weapon_name}, dealing {total_damage} damage!"
        effects["damage"] = total_damage
        
        # Check if battle has ended
        if self.challenger_hp <= 0 or self.defender_hp <= 0:
            self.battle_ended = True
            if self.challenger_hp <= 0:
                self.winner = self.defender.name
                self.winner_char = self.defender
            else:
                self.winner = self.challenger.name
                self.winner_char = self.challenger
                
        return message, effects
        
    def surrender(self) -> str:
        """Handle surrender action"""
        if self.current_turn == self.challenger.name:
            self.winner = self.defender.name
            self.winner_char = self.defender
            message = f"🏳️ {self.challenger.name} has surrendered! {self.defender.name} wins!"
        else:
            self.winner = self.challenger.name
            self.winner_char = self.challenger
            message = f"🏳️ {self.defender.name} has surrendered! {self.challenger.name} wins!"
            
        self.battle_ended = True
        return message
        
    def switch_character(self) -> str:
        """Not implemented in base PvP - could be expanded in future versions"""
        if self.switches_remaining <= 0:
            return f"No more character switches allowed in this battle."
            
        self.switches_remaining -= 1
        return f"Character switch feature is not implemented yet. Switches remaining: {self.switches_remaining}"
        
    def get_battle_status(self) -> Dict:
        """Return current battle state for UI display (HP bars, buffs, debuffs, etc)."""
        challenger_hp_percent = self.challenger_hp / self.challenger.stats.HP
        defender_hp_percent = self.defender_hp / self.defender.stats.HP
        
        challenger_bar = "█" * int(challenger_hp_percent * 10) + "▒" * (10 - int(challenger_hp_percent * 10))
        defender_bar = "█" * int(defender_hp_percent * 10) + "▒" * (10 - int(defender_hp_percent * 10))
        
        # Get player info for linking current turn player's name
        if self.current_turn == self.challenger.name:
            current_player_id = self.challenger_player.user_id
            current_player_first_name = self.challenger_player.name
        else:
            current_player_id = self.defender_player.user_id
            current_player_first_name = self.defender_player.name
            
        # Create status message with hyperlinked player name for current turn
        challenger_player_name = self.challenger_player.name
        defender_player_name = self.defender_player.name
        
        # Clearly display which player controls which character
        # Add "«" symbol next to the current turn player
        challenger_indicator = " « Turn" if self.current_turn == self.challenger.name else ""
        defender_indicator = " « Turn" if self.current_turn == self.defender.name else ""
        
        status_message = (
            f"Turn: {self.turn_count + 1}\n"
            f"Current Turn: <a href='tg://user?id={current_player_id}'>{current_player_first_name}'s {self.current_turn}</a>\n\n"
            f"👤 {challenger_player_name} controls {self.challenger.name}{challenger_indicator}\n"
            f"👤 {defender_player_name} controls {self.defender.name}{defender_indicator}\n\n"
        )
        
        # Add buffs and debuffs to status message
        if self.challenger_debuffs:
            debuffs_display = [f"{k}({int(v)})" if isinstance(v, (int, float)) else k for k, v in self.challenger_debuffs.items()]
            status_message += f"🔽 {self.challenger.name} debuffs: {', '.join(debuffs_display)}\n"
            
        if self.challenger_buffs:
            buffs_display = [
                f"{k}({int(v)})" if isinstance(v, (int, float)) and v > 1 and k != "items_dropped" else k
                for k, v in self.challenger_buffs.items() if k != "items_dropped"
            ]
            if buffs_display:
                status_message += f"🔼 {self.challenger.name} buffs: {', '.join(buffs_display)}\n"
                
        if self.defender_debuffs:
            debuffs_display = [f"{k}({int(v)})" if isinstance(v, (int, float)) else k for k, v in self.defender_debuffs.items()]
            status_message += f"🔽 {self.defender.name} debuffs: {', '.join(debuffs_display)}\n"
            
        if self.defender_buffs:
            buffs_display = [
                f"{k}({int(v)})" if isinstance(v, (int, float)) and v > 1 and k != "items_dropped" else k
                for k, v in self.defender_buffs.items() if k != "items_dropped"
            ]
            if buffs_display:
                status_message += f"🔼 {self.defender.name} buffs: {', '.join(buffs_display)}\n"
                
        if self.switches_remaining > 0:
            status_message += f"🔄 Switches left: {self.switches_remaining}\n"
            
        return {
            "challenger_hp": int(self.challenger_hp),
            "defender_hp": int(self.defender_hp),
            "challenger_gas": int(self.challenger_gas),
            "defender_gas": int(self.defender_gas),
            "challenger_bar": challenger_bar,
            "defender_bar": defender_bar,
            "status_message": status_message,
            "current_turn": self.current_turn,
            "current_player_first_name": current_player_first_name,
            "current_player_id": current_player_id
        }
        
    async def calculate_rewards(self, db: Database) -> Dict:
        """Calculate rewards for winning a PvP battle"""
        rewards = {
            "winner": {
                "xp": 0,
                "marks": 0,
                "valor": 0,
            },
            "loser": {
                "xp": 0,
                "marks": 0,
                "valor": 0,
            }
        }
        
        if not self.winner:
            return rewards
            
        # Determine winner and loser
        if self.winner == self.challenger.name:
            winner = self.challenger
            loser = self.defender
            winner_player = self.challenger_player
            loser_player = self.defender_player
        else:
            winner = self.defender
            loser = self.challenger
            winner_player = self.defender_player
            loser_player = self.challenger_player
            
        # Base rewards
        level_diff = winner.level - loser.level
        
        # Calculate XP based on level difference (winner gets more if they beat a higher level player)
        if level_diff < 0:
            # Beating a higher level opponent gives bonus XP
            winner_xp = 60 + (abs(level_diff) * 10)
        else:
            # Beating a lower level opponent gives less XP
            winner_xp = max(30, 60 - (level_diff * 5))
            
        # Loser always gets some XP for participating
        loser_xp = max(15, 25 - (abs(level_diff) * 2) if level_diff > 0 else 25)
        
        # Marks calculation - winner gets more
        winner_marks = random.randint(50, 100) + (winner.level * 3)
        loser_marks = random.randint(20, 40)  # Consolation prize
        
        # Valor calculation - only winner gets valor
        winner_valor = random.randint(1, 3)
        if level_diff < 0:
            winner_valor += abs(level_diff) // 5  # Bonus for beating higher level players
            
        # Set rewards
        rewards["winner"]["xp"] = winner_xp
        rewards["winner"]["marks"] = winner_marks
        rewards["winner"]["valor"] = winner_valor
        
        rewards["loser"]["xp"] = loser_xp
        rewards["loser"]["marks"] = loser_marks
        rewards["loser"]["valor"] = 0
        
        return rewards


async def generate_pvp_ability_keyboard(battle: PvPBattleSystem, context: ContextTypes.DEFAULT_TYPE) -> List[List[InlineKeyboardButton]]:
    """Generate keyboard buttons for valid abilities and actions in PvP."""
    keyboard = []
    
    # Determine current character
    if battle.current_turn == battle.challenger.name:
        current_char = battle.challenger
        cooldowns = battle.challenger_cooldowns
        gas = battle.challenger_gas
    else:
        current_char = battle.defender
        cooldowns = battle.defender_cooldowns
        gas = battle.defender_gas
        
    from database.characters import get_character_data
    character_data = get_character_data(current_char.character_type)
    
    if not character_data:
        keyboard.append([InlineKeyboardButton("🏳️ Surrender", callback_data="pvp_surrender")])
        return keyboard
        
    # Add abilities based on their types
    for ability_type in ["active", "passive", "ultimate"]:
        abilities = getattr(character_data, f"{ability_type}_abilities", [])
        for ability in abilities:
            if not ability or not ability.name:
                continue
            if current_char.level < ability.level_required:
                continue
                
            is_unlocked = ability.is_unlocked or current_char.unlocked_abilities.get(ability.name, False)
            if not is_unlocked:
                continue
                
            # Skip abilities that are disabled in PvP
            if getattr(ability, 'disabled_in_pvp', False):
                continue
                
            gas_cost = ability.gas_cost or 0
            prefix = "⚔️" if ability_type == "active" else "✨" if ability_type == "ultimate" else " "
            
            # Make sure ability name has no leading/trailing whitespace or underscores
            clean_ability_name = ability.name.strip().lstrip('_')
            
            # Add available abilities
            if cooldowns.get(clean_ability_name, 0) == 0 and gas >= gas_cost:
                keyboard.append([InlineKeyboardButton(
                    f"{prefix} {clean_ability_name}",
                    callback_data=f"pvp_ability_{clean_ability_name}"
                )])
            elif cooldowns.get(clean_ability_name, 0) > 0:
                keyboard.append([InlineKeyboardButton(
                    f"⏳ {prefix} {clean_ability_name} (CD: {cooldowns[clean_ability_name]})",
                    callback_data=f"pvp_cooldown_{clean_ability_name}"
                )])
            elif gas < gas_cost and gas_cost > 0:
                keyboard.append([InlineKeyboardButton(
                    f"⛽ {prefix} {clean_ability_name} (Need {gas_cost} gas)",
                    callback_data=f"pvp_lowgas_{clean_ability_name}"
                )])
                
    # Add basic attack button
    shop_items = context.bot_data.get("shop_items") or {}
    weapon = battle.get_equipped_weapon(current_char, shop_items)
    
    if gas >= 20:
        if weapon:
            keyboard.append([InlineKeyboardButton(f"⚔️ {weapon.name}", callback_data="pvp_basic_attack")])
        else:
            keyboard.append([InlineKeyboardButton("⚔️ Basic Attack", callback_data="pvp_basic_attack")])
    else:
        if weapon:
            keyboard.append([InlineKeyboardButton(f"⛽ {weapon.name} (Low Gas)", callback_data="pvp_lowgas_basic_attack")])
        else:
            keyboard.append([InlineKeyboardButton("⛽ Basic Attack (Low Gas)", callback_data="pvp_lowgas_basic_attack")])
    
    # Add switch and surrender buttons
    if battle.switches_remaining > 0:
        keyboard.append([InlineKeyboardButton(f"🔄 Switch ({battle.switches_remaining})", callback_data="pvp_switch")])
        
    keyboard.append([InlineKeyboardButton("🏳️ Surrender", callback_data="pvp_surrender")])
    
    return keyboard


async def pvp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /pvp command to challenge another player or see PvP status."""
    if not update.effective_user or not update.message:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is currently in a battle
    async with active_battles_lock:
        if user_id in active_battles:
            await update.message.reply_text("You are currently in a battle with a titan. Complete it first!")
            return
    
    # Check if user is in an active PVP battle
    if user_id in active_pvp_battles:
        await update.message.reply_text("You are already in a PVP battle!")
        return
        
    # Get DB instance
    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Database not available. Try again later.")
        return
        
    # Check if the user replied to another user's message
    replied_to = update.message.reply_to_message
    if replied_to and hasattr(replied_to, 'from_user') and replied_to.from_user:
        # User is replying to someone - this is a challenge
        target_user = replied_to.from_user
        target_id = str(target_user.id)
        target_username = target_user.username or str(target_user.id)
        
        # Find the target user in database
        target_player = None
        try:
            target_player = await db.players.find_one({"user_id": target_id})
        except Exception as e:
            logger.error(f"Error finding target player: {e}")
            await update.message.reply_text("Error finding target player.")
            return
            
        if not target_player:
            await update.message.reply_text(f"Player '{target_username}' has not started the game yet.")
            return
            
        # Check if user is challenging themselves
        if target_id == user_id:
            await update.message.reply_text("You can't challenge yourself to a PVP battle!")
            return
            
        # Check if target is already in a battle
        target_id = str(target_player["user_id"])
        if target_id in active_battles:
            await update.message.reply_text(f"{target_player['name']} is currently in a battle with a titan!")
            return
            
        if target_id in active_pvp_battles:
            await update.message.reply_text(f"{target_player['name']} is already in a PVP battle!")
            return
            
        # Get challenger's player and character data
        challenger_player = await db.get_player(user_id)
        if not challenger_player or not challenger_player.team:
            await update.message.reply_text("You don't have any characters in your team!")
            return
        
        # Handle the TeamMember object or other formats
        if hasattr(challenger_player.team[0], 'character_name'):
            challenger_char_name = challenger_player.team[0].character_name
        elif isinstance(challenger_player.team[0], dict):
            challenger_char_name = challenger_player.team[0].get("character_name")
        else:
            challenger_char_name = challenger_player.team[0]
            
        challenger_char = await db.get_character(user_id, challenger_char_name)
        if not challenger_char:
            await update.message.reply_text("Your primary character was not found!")
            return
            
        # Get defender's character data
        defender_player = Player(**target_player)
        if not defender_player.team:
            await update.message.reply_text(f"{defender_player.name} doesn't have any characters in their team!")
            return
            
        # Handle the TeamMember object or other formats for defender
        if hasattr(defender_player.team[0], 'character_name'):
            defender_char_name = defender_player.team[0].character_name
        elif isinstance(defender_player.team[0], dict):
            defender_char_name = defender_player.team[0].get("character_name")
        else:
            defender_char_name = defender_player.team[0]
            
        try:
            defender_char = await db.get_character(target_id, defender_char_name)
            if not defender_char:
                await update.message.reply_text(f"{defender_player.name}'s primary character was not found!")
                return
        except Exception as e:
            logger.error(f"Error getting defender character: {e}")
            await update.message.reply_text(f"Error retrieving {defender_player.name}'s character. They may need to set up their team properly.")
            return
        
        # Generate a unique challenge ID
        challenge_id = f"pvp_{user_id}_{target_id}_{int(datetime.now().timestamp())}"
        
        # Store challenge data
        pvp_challenges[challenge_id] = {
            "challenger_id": user_id,
            "defender_id": target_id,
            "challenger_name": challenger_player.name,
            "defender_name": defender_player.name,
            "challenger_char": challenger_char,
            "defender_char": defender_char,
            "challenger_player": challenger_player,
            "defender_player": defender_player,
            "timestamp": datetime.now(timezone.utc),
            "challenge_id": challenge_id
        }
        
        # Create challenge buttons
        keyboard = [
            [
                InlineKeyboardButton("Accept 👍", callback_data=f"pvp_accept_{challenge_id}"),
                InlineKeyboardButton("Decline 👎", callback_data=f"pvp_decline_{challenge_id}")
            ],
            [
                InlineKeyboardButton("CombatDome", callback_data=f"pvp_dome_{challenge_id}")
            ],
            [
                InlineKeyboardButton("Cancel", callback_data=f"pvp_cancel_{challenge_id}")
            ]
        ]
        
        # Send challenge message
        try:
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"Challenge Issued:\n"
                    f"{challenger_player.name} vs {defender_player.name}\n\n"
                    f"{defender_player.name}, the challenge is set\n"
                    f"The battlefield awaits your decision\n\n"
                    f"⚔️ Switches Allowed : {10}\n"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            # Schedule challenge expiration
            asyncio.create_task(expire_challenge(challenge_id, message, context))
            
        except Exception as e:
            logger.error(f"Error sending challenge message: {e}")
            await update.message.reply_text("Error sending challenge.")
            if challenge_id in pvp_challenges:
                del pvp_challenges[challenge_id]
    else:
        # Show PVP stats and info
        player = await db.get_player(user_id)
        if not player:
            await update.message.reply_text("You need to start your journey first! Use /start")
            return
            
        await update.message.reply_text(
            "PVP SYSTEM\n\n"
            "Challenge other players to battles!\n"
            "Reply to another player's message with /pvp to challenge them.\n\n"
            "Your PVP Stats:\n"
            f"Wins: {getattr(player, 'pvp_wins', 0)}\n"
            f"Losses: {getattr(player, 'pvp_losses', 0)}\n"
            f"Battle Rating: {getattr(player, 'battle_rating', 1000)}\n"
        )


async def expire_challenge(challenge_id: str, message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Expire a PVP challenge after 2 minutes."""
    try:
        await asyncio.sleep(120)  # Wait for 2 minutes
        if challenge_id in pvp_challenges:
            del pvp_challenges[challenge_id]
            try:
                await message.edit_text(
                    "⏱️ Challenge expired!\n"
                    "The challenge has not been accepted within the time limit."
                )
            except Exception as e:
                logger.error(f"Error updating expired challenge message: {e}")
    except asyncio.CancelledError:
        logger.debug(f"Challenge {challenge_id} expiration task cancelled")
    except Exception as e:
        logger.error(f"Error in challenge expiration task: {e}")


async def pvp_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle PvP-related callback queries."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    
    try:
        # Acknowledge the button press with retry logic
        try:
            await safe_api_call(query.answer)
        except Exception as e:
            # Handle expired callback queries gracefully
            logger.error(f"Error in answer: {e}")
            # Continue processing even if the acknowledgment fails
            # This allows the bot to still process the action even if the UI can't be updated
        
        callback_data = query.data
        user_id = str(update.effective_user.id)
        
        if callback_data.startswith("pvp_accept_"):
            await handle_pvp_accept(update, context)
        elif callback_data.startswith("pvp_decline_"):
            await handle_pvp_decline(update, context)
        elif callback_data.startswith("pvp_cancel_"):
            await handle_pvp_cancel(update, context)
        elif callback_data.startswith("pvp_dome_"):
            await handle_pvp_accept(update, context, dome=True)
        elif callback_data == "pvp_basic_attack":
            await handle_pvp_basic_attack(update, context)
        elif callback_data.startswith("pvp_ability_"):
            ability_name = callback_data[11:].strip()  # Remove "pvp_ability_" prefix and any whitespace
            logger.debug(f"Received ability callback: {ability_name}")
            await handle_pvp_ability(update, context, ability_name)
        elif callback_data == "pvp_surrender":
            await handle_pvp_surrender(update, context)
        elif callback_data == "pvp_switch":
            await handle_pvp_switch(update, context)
        elif callback_data.startswith("pvp_cooldown_") or callback_data.startswith("pvp_lowgas_"):
            # Just show a message for abilities on cooldown or with insufficient gas
            try:
                await safe_api_call(query.answer, "This ability is not available right now.", show_alert=True)
            except Exception as e:
                # Just log the error if the callback query is already expired
                logger.debug(f"Could not answer callback query for cooldown/lowgas: {e}")
    except Exception as e:
        logger.error(f"Error in pvp_callback_handler: {e}")
        # If we get here, something went wrong with handling the callback
        # We'll silently fail since the user might try again


async def handle_pvp_accept(update: Update, context: ContextTypes.DEFAULT_TYPE, dome: bool = False) -> None:
    """Handle acceptance of a PVP challenge."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    challenge_id = query.data.replace("pvp_accept_", "").replace("pvp_dome_", "")
    
    # Verify challenge exists
    if challenge_id not in pvp_challenges:
        await safe_api_call(query.edit_message_text, "This challenge is no longer available.")
        return
        
    challenge_data = pvp_challenges[challenge_id]
    user_id = str(update.effective_user.id)
    
    # Only the defender can accept
    if user_id != challenge_data["defender_id"]:
        await safe_api_call(query.answer, "Only the challenged player can accept this battle!")
        return
        
    # Check if either player is already in a battle
    challenger_id = challenge_data["challenger_id"]
    
    async with active_battles_lock:
        if challenger_id in active_battles:
            await safe_api_call(query.edit_message_text, f"{challenge_data['challenger_name']} is now in a battle with a titan!")
            del pvp_challenges[challenge_id]
            return
            
        if user_id in active_battles:
            await safe_api_call(query.edit_message_text, "You are now in a battle with a titan!")
            del pvp_challenges[challenge_id]
            return
    
    # Create PvP battle instance
    battle = PvPBattleSystem(
        challenger=challenge_data["challenger_char"],
        defender=challenge_data["defender_char"],
        challenger_player=challenge_data["challenger_player"],
        defender_player=challenge_data["defender_player"],
        challenge_id=challenge_id
    )
    
    # Store battle in active PvP battles
    active_pvp_battles[challenger_id] = battle
    active_pvp_battles[user_id] = battle
    
    # Clean up challenge
    del pvp_challenges[challenge_id]
    
    try:
        # Start battle
        await safe_api_call(
            query.edit_message_text,
            "BATTLE BEGINS,",
            reply_markup=None
        )
        
        # Send battle display
        status = battle.get_battle_status()
        keyboard = await generate_pvp_ability_keyboard(battle, context)
        
        # Get player names for display
        challenger_player_name = battle.challenger_player.name
        defender_player_name = battle.defender_player.name
        
        # Add the "«" symbol to indicate whose turn it is
        challenger_turn_indicator = " « Turn" if battle.current_turn == battle.challenger.name else ""
        defender_turn_indicator = " « Turn" if battle.current_turn == battle.defender.name else ""
        
        await safe_api_call(
            query.message.reply_text,
            text=(
            f"<b>⚔️ PVP BATTLE ⚔️</b>\n\n"
            f"<blockquote><b>| {challenger_player_name}  |</b>{challenger_turn_indicator}</blockquote>\n"
            f"<blockquote><b>{battle.challenger.name}</b>\n"
            f"<b>HP:</b> {status['challenger_bar']} {status['challenger_hp']}/{battle.challenger.stats.HP}\n"
            f"<b>Gas: {status['challenger_gas']}/{battle.challenger.max_gas}</b></blockquote>\n\n"
            f"<blockquote><b>| {defender_player_name}  |</b>{defender_turn_indicator}</blockquote>\n"
            f"<blockquote><b>{battle.defender.name}</b>\n"
            f"<b>HP:</b> {status['defender_bar']} {status['defender_hp']}/{battle.defender.stats.HP}\n"
            f"<b>Gas: {status['defender_gas']}/{battle.defender.max_gas}</b></blockquote>\n\n"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error sending battle start messages: {e}")
    
    # Set timeout task to end battle if inactive
    battle.timeout_task = asyncio.create_task(
        pvp_battle_timeout(challenger_id, user_id, battle, context, query.message.chat_id)
    )


async def handle_pvp_decline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle declining of a PVP challenge."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    challenge_id = query.data.replace("pvp_decline_", "")
    
    # Verify challenge exists
    if challenge_id not in pvp_challenges:
        await safe_api_call(query.edit_message_text, "This challenge is no longer available.")
        return
        
    challenge_data = pvp_challenges[challenge_id]
    user_id = str(update.effective_user.id)
    
    # Only the defender can decline
    if user_id != challenge_data["defender_id"]:
        await safe_api_call(query.answer, "Only the challenged player can decline this battle!")
        return
        
    # Clean up challenge
    del pvp_challenges[challenge_id]
    
    # Update message
    try:
        await safe_api_call(
            query.edit_message_text,
            f"{challenge_data['defender_name']} declined the challenge from {challenge_data['challenger_name']}."
        )
    except Exception as e:
        logger.error(f"Error updating message on challenge decline: {e}")


async def handle_pvp_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancellation of a PVP challenge."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    challenge_id = query.data.replace("pvp_cancel_", "")
    
    # Verify challenge exists
    if challenge_id not in pvp_challenges:
        await safe_api_call(query.edit_message_text, "This challenge is no longer available.")
        return
        
    challenge_data = pvp_challenges[challenge_id]
    user_id = str(update.effective_user.id)
    
    # Only the challenger can cancel
    if user_id != challenge_data["challenger_id"]:
        await safe_api_call(query.answer, "Only the challenger can cancel this battle request!")
        return
        
    # Clean up challenge
    del pvp_challenges[challenge_id]
    
    # Update message
    try:
        await safe_api_call(
            query.edit_message_text,
            f"{challenge_data['challenger_name']} cancelled the challenge to {challenge_data['defender_name']}."
        )
    except Exception as e:
        logger.error(f"Error updating message on challenge cancel: {e}")


async def handle_pvp_ability(update: Update, context: ContextTypes.DEFAULT_TYPE, ability_name: str) -> None:
    """Handle using an ability in PVP."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        await safe_api_call(query.edit_message_text, "You are not in an active PVP battle.")
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn
    if (user_id == battle.challenger.user_id and battle.current_turn != battle.challenger.name) or \
       (user_id == battle.defender.user_id and battle.current_turn != battle.defender.name):
        # Apply a small penalty for trying to act out of turn - reduce gas slightly
        if user_id == battle.challenger.user_id:
            battle.challenger_gas = max(0, battle.challenger_gas - 5)  # -5 gas penalty
            current_name = battle.challenger.name
            turn_name = battle.defender.name
        else:
            battle.defender_gas = max(0, battle.defender_gas - 5)  # -5 gas penalty
            current_name = battle.defender.name
            turn_name = battle.challenger.name
            
        await safe_api_call(query.answer, f"Wait! It's {turn_name}'s turn now! (-5 gas for impatience)", show_alert=True)
        return
        
    # Use the ability
    message, effects = await battle.use_ability(ability_name, context)
    
    if battle.timeout_task:
        battle.timeout_task.cancel()
        battle.timeout_task = None
    
    # Check if battle has ended
    if battle.battle_ended:
        await handle_pvp_battle_end(update, context, battle, message)
        return
        
    # Switch turn
    battle.switch_turn()
    
    # Update battle display
    status = battle.get_battle_status()
    keyboard = await generate_pvp_ability_keyboard(battle, context)
    
    try:
        # Create a more clear display showing player names with their characters
        challenger_player_name = battle.challenger_player.name
        defender_player_name = battle.defender_player.name
        
        # Add the "«" symbol to indicate whose turn it is
        challenger_turn_indicator = " « Turn" if battle.current_turn == battle.challenger.name else ""
        defender_turn_indicator = " « Turn" if battle.current_turn == battle.defender.name else ""
        
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"<b>⚔️ PVP BATTLE ⚔️</b>\n\n"
                f"<code>{message}</code>\n\n"
                f"<blockquote><b>| {challenger_player_name}  |</b>{challenger_turn_indicator}</blockquote>\n"
                f"<blockquote><b>{battle.challenger.name}</b>\n"
                f"<b>HP:</b> {status['challenger_bar']} {status['challenger_hp']}/{battle.challenger.stats.HP}\n"
                f"<b>Gas: {status['challenger_gas']}/{battle.challenger.max_gas}</b></blockquote>\n\n"
                f"<blockquote><b>| {defender_player_name}  |</b>{defender_turn_indicator}</blockquote>\n"
                f"<blockquote><b>{battle.defender.name}</b>\n"
                f"<b>HP:</b> {status['defender_bar']} {status['defender_hp']}/{battle.defender.stats.HP}\n"
                f"<b>Gas: {status['defender_gas']}/{battle.defender.max_gas}</b></blockquote>\n\n"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to update battle message: {e}")
        # We'll still set the timeout task even if updating the message fails
    
    # Set timeout task
    battle.timeout_task = asyncio.create_task(
        pvp_battle_timeout(battle.challenger.user_id, battle.defender.user_id, battle, context, query.message.chat_id)
    )


async def handle_pvp_basic_attack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle basic attack in PVP."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        await safe_api_call(query.edit_message_text, "You are not in an active PVP battle.")
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn
    if (user_id == battle.challenger.user_id and battle.current_turn != battle.challenger.name) or \
       (user_id == battle.defender.user_id and battle.current_turn != battle.defender.name):
        # Apply a small penalty for trying to act out of turn - reduce gas slightly
        if user_id == battle.challenger.user_id:
            battle.challenger_gas = max(0, battle.challenger_gas - 5)  # -5 gas penalty
            current_name = battle.challenger.name
            turn_name = battle.defender.name
        else:
            battle.defender_gas = max(0, battle.defender_gas - 5)  # -5 gas penalty
            current_name = battle.defender.name
            turn_name = battle.challenger.name
            
        await safe_api_call(query.answer, f"Wait! It's {turn_name}'s turn now! (-5 gas for impatience)", show_alert=True)
        return
    
    if battle.timeout_task:
        battle.timeout_task.cancel()
        battle.timeout_task = None
    
    # Use basic attack
    message, effects = await battle.use_basic_attack(context)
    
    # Check if battle has ended
    if battle.battle_ended:
        await handle_pvp_battle_end(update, context, battle, message)
        return
        
    # Switch turn
    battle.switch_turn()
    
    # Update battle display
    status = battle.get_battle_status()
    keyboard = await generate_pvp_ability_keyboard(battle, context)
    
    # Use safe API call with retry logic for rate limiting
    try:
        # Create a more clear display showing player names with their characters
        challenger_player_name = battle.challenger_player.name
        defender_player_name = battle.defender_player.name
        
        # Add the "«" symbol to indicate whose turn it is
        challenger_turn_indicator = " « Turn" if battle.current_turn == battle.challenger.name else ""
        defender_turn_indicator = " « Turn" if battle.current_turn == battle.defender.name else ""
        
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"<b>⚔️ PVP BATTLE ⚔️</b>\n\n"
                f"{message}\n\n"
                f"<blockquote><b>| {challenger_player_name}  |</b>{challenger_turn_indicator}</blockquote>\n"
                f"<blockquote><b>{battle.challenger.name}</b>\n"
                f"<b>HP:</b> {status['challenger_bar']} {status['challenger_hp']}/{battle.challenger.stats.HP}\n"
                f"<b>Gas: {status['challenger_gas']}/{battle.challenger.max_gas}</b></blockquote>\n\n"
                f"<blockquote><b>| {defender_player_name}  |</b>{defender_turn_indicator}</blockquote>\n"
                f"<blockquote><b>{battle.defender.name}</b>\n"
                f"<b>HP:</b> {status['defender_bar']} {status['defender_hp']}/{battle.defender.stats.HP}\n"
                f"<b>Gas: {status['defender_gas']}/{battle.defender.max_gas}</b></blockquote>\n\n"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to update battle message: {e}")
        # We'll still set the timeout task even if updating the message fails
    
    # Set timeout task
    battle.timeout_task = asyncio.create_task(
        pvp_battle_timeout(battle.challenger.user_id, battle.defender.user_id, battle, context, query.message.chat_id)
    )


async def handle_pvp_surrender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle surrender in PVP."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        try:
            await safe_api_call(query.edit_message_text, "You are not in an active PVP battle.")
        except Exception as e:
            logger.error(f"Error editing message during surrender: {e}")
        return
        
    battle = active_pvp_battles[user_id]
    
    try:
        # Only allow surrender on your turn
        if (user_id == battle.challenger.user_id and battle.current_turn != battle.challenger.name) or \
        (user_id == battle.defender.user_id and battle.current_turn != battle.defender.name):
            # Apply a small penalty for trying to act out of turn - reduce gas slightly
            if user_id == battle.challenger.user_id:
                battle.challenger_gas = max(0, battle.challenger_gas - 5)  # -5 gas penalty
                current_name = battle.challenger.name
                turn_name = battle.defender.name
            else:
                battle.defender_gas = max(0, battle.defender_gas - 5)  # -5 gas penalty
                current_name = battle.defender.name
                turn_name = battle.challenger.name
                
            await safe_api_call(query.answer, f"Wait! It's {turn_name}'s turn now! You can only surrender on your turn! (-5 gas for impatience)", show_alert=True)
            return
    except Exception as e:
        logger.warning(f"Could not answer callback query during surrender check: {e}")
        # Continue even if answering the callback fails - the user might still want to surrender
    
    if battle.timeout_task:
        battle.timeout_task.cancel()
        battle.timeout_task = None
    
    # Process surrender
    message = battle.surrender()
    
    # Handle battle end
    try:
        await handle_pvp_battle_end(update, context, battle, message)
    except Exception as e:
        logger.error(f"Error handling battle end after surrender: {e}")
        # Clean up battle data even if there was an error
        if user_id in active_pvp_battles:
            del active_pvp_battles[user_id]
        opponent_id = battle.challenger.user_id if user_id != battle.challenger.user_id else battle.defender.user_id
        if opponent_id in active_pvp_battles:
            del active_pvp_battles[opponent_id]
        battle.dispose()


async def handle_pvp_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle character switch in PVP."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        await safe_api_call(query.edit_message_text, "You are not in an active PVP battle.")
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn
    if (user_id == battle.challenger.user_id and battle.current_turn != battle.challenger.name) or \
       (user_id == battle.defender.user_id and battle.current_turn != battle.defender.name):
        await safe_api_call(query.answer, "It's not your turn!", show_alert=True)
        return
    
    # Handle switch (placeholder for future implementation)
    message = battle.switch_character()
    await safe_api_call(query.answer, message, show_alert=True)


async def handle_pvp_battle_end(update: Update, context: ContextTypes.DEFAULT_TYPE, battle: PvPBattleSystem, message: str) -> None:
    """Handle the end of a PVP battle."""
    query = update.callback_query
    if not query:
        return
        
    # Clean up timeout task
    if battle.timeout_task and not battle.timeout_task.done():
        battle.timeout_task.cancel()
        
    # Get DB instance
    db = context.bot_data.get("db")
    if not db:
        await safe_api_call(
            query.edit_message_text, 
            f"{message}\n\nError: Database not available."
        )
        return
        
    # Calculate rewards
    rewards = await battle.calculate_rewards(db)
    
    # Update message with battle results
    winner_name = battle.winner if battle.winner else "Nobody"
    
    # Get player names to show clearly who won
    if battle.winner == battle.challenger.name:
        winner_player_name = battle.challenger_player.name
    else:
        winner_player_name = battle.defender_player.name
        
    try:
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"<b>⚔️ PVP BATTLE ENDED ⚔️</b>\n\n"
                f"{message}\n\n"
                f"<b>Winner:</b> {winner_player_name}'s {winner_name}\n\n"
                f"<b>Rewards:</b>\n"
                f"Winner: {rewards['winner']['xp']} XP, {rewards['winner']['marks']} Marks, {rewards['winner']['valor']} Valor\n"
                f"Loser: {rewards['loser']['xp']} XP, {rewards['loser']['marks']} Marks"
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to update battle end message: {e}")
        # Continue with database updates even if message update fails
    
    # Apply rewards and update statistics
    challenger_id = battle.challenger.user_id
    defender_id = battle.defender.user_id
    
    # Determine winner and loser IDs
    if battle.winner == battle.challenger.name:
        winner_id = challenger_id
        loser_id = defender_id
    else:
        winner_id = defender_id
        loser_id = challenger_id
        
    # Update winner statistics and rewards
    if winner_id:
        await db.players.update_one(
            {"user_id": winner_id},
            {
                "$inc": {
                    "pvp_wins": 1,
                    "xp": rewards["winner"]["xp"],
                    "marks": rewards["winner"]["marks"],
                    "valor": rewards["winner"]["valor"],
                    "total_xp": rewards["winner"]["xp"]
                }
            }
        )
        
    # Update loser statistics and rewards
    if loser_id:
        await db.players.update_one(
            {"user_id": loser_id},
            {
                "$inc": {
                    "pvp_losses": 1,
                    "xp": rewards["loser"]["xp"],
                    "marks": rewards["loser"]["marks"],
                    "total_xp": rewards["loser"]["xp"]
                }
            }
        )
        
    # Update characters with final HP and gas
    # Create a copy of the character dictionary and update the HP and gas values
    challenger_data = battle.challenger.dict()
    challenger_data['current_hp'] = battle.challenger_hp  # Replace the existing current_hp
    challenger_data['gas'] = battle.challenger_gas        # Replace the existing gas
    await db.update_character(Character(**challenger_data))
    
    # Do the same for the defender
    defender_data = battle.defender.dict()
    defender_data['current_hp'] = battle.defender_hp
    defender_data['gas'] = battle.defender_gas
    await db.update_character(Character(**defender_data))
    
    # Clean up battle data
    if challenger_id in active_pvp_battles:
        del active_pvp_battles[challenger_id]
    if defender_id in active_pvp_battles:
        del active_pvp_battles[defender_id]
        
    # Dispose battle resources
    battle.dispose()
    
    try:
        # Make sure battle.winner is not None before passing to track_battle_end
        if battle.winner and winner_id:
            track_battle_end(int(winner_id), battle.winner, "pvp_victory")
    except Exception as e:
        logger.error(f"Error in track_battle_end: {e}")
        pass


async def pvp_battle_timeout(challenger_id: str, defender_id: str, battle: PvPBattleSystem, 
                            context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Handle PVP battle timeout."""
    try:
        battle.timeout_task = asyncio.current_task()
        await asyncio.sleep(180)  # 3 minutes
        
        if challenger_id in active_pvp_battles and defender_id in active_pvp_battles:
            db = context.bot_data.get("db")
            if not db:
                logger.error("Database not initialized")
                return
                
            # Determine who timed out (whose turn it was)
            if battle.current_turn == battle.challenger.name:
                # Challenger timed out, defender wins
                battle.winner = battle.defender.name
                battle.winner_char = battle.defender
                message = f"⏰ {battle.challenger_player.name}'s {battle.challenger.name} took too long to move. {battle.defender_player.name}'s {battle.defender.name} wins by default!"
            else:
                # Defender timed out, challenger wins
                battle.winner = battle.challenger.name
                battle.winner_char = battle.challenger
                message = f"⏰ {battle.defender_player.name}'s {battle.defender.name} took too long to move. {battle.challenger_player.name}'s {battle.challenger.name} wins by default!"
                
            battle.battle_ended = True
            
            # Calculate rewards
            rewards = await battle.calculate_rewards(db)
            
            # Send timeout message
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"<b>⚔️ PVP BATTLE TIMED OUT ⚔️</b>\n\n"
                        f"{message}\n\n"
                        f"<b>Winner:</b> {battle.challenger_player.name if battle.winner == battle.challenger.name else battle.defender_player.name}'s {battle.winner}\n\n"
                        f"<b>Rewards:</b>\n"
                        f"Winner: {rewards['winner']['xp']} XP, {rewards['winner']['marks']} Marks, {rewards['winner']['valor']} Valor\n"
                        f"Loser: {rewards['loser']['xp']} XP, {rewards['loser']['marks']} Marks"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to send PVP timeout message: {e}")
                
            # Apply rewards and update statistics
            if battle.winner == battle.challenger.name:
                winner_id = challenger_id
                loser_id = defender_id
            else:
                winner_id = defender_id
                loser_id = challenger_id
                
            # Update winner statistics and rewards
            await db.players.update_one(
                {"user_id": winner_id},
                {
                    "$inc": {
                        "pvp_wins": 1,
                        "xp": rewards["winner"]["xp"],
                        "marks": rewards["winner"]["marks"],
                        "valor": rewards["winner"]["valor"],
                        "total_xp": rewards["winner"]["xp"]
                    }
                }
            )
                
            # Update loser statistics and rewards
            await db.players.update_one(
                {"user_id": loser_id},
                {
                    "$inc": {
                        "pvp_losses": 1,
                        "xp": rewards["loser"]["xp"],
                        "marks": rewards["loser"]["marks"],
                        "total_xp": rewards["loser"]["xp"]
                    }
                }
            )
                
            # Update characters with final HP and gas - avoid duplicate parameters
            challenger_data = battle.challenger.dict()
            challenger_data['current_hp'] = battle.challenger_hp
            challenger_data['gas'] = battle.challenger_gas
            await db.update_character(Character(**challenger_data))
                
            defender_data = battle.defender.dict()
            defender_data['current_hp'] = battle.defender_hp
            defender_data['gas'] = battle.defender_gas
            await db.update_character(Character(**defender_data))
                
            # Clean up battle data
            if challenger_id in active_pvp_battles:
                del active_pvp_battles[challenger_id]
            if defender_id in active_pvp_battles:
                del active_pvp_battles[defender_id]
                
            # Dispose battle resources
            battle.dispose()
                
    except asyncio.CancelledError:
        logger.debug(f"PVP battle timeout cancelled for battle {battle.challenge_id}")
    except Exception as e:
        logger.error(f"Error in pvp_battle_timeout: {e}")
        if challenger_id in active_pvp_battles:
            del active_pvp_battles[challenger_id]
        if defender_id in active_pvp_battles:
            del active_pvp_battles[defender_id]
