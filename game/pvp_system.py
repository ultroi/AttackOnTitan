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
from database.missions import process_pvp_mission_progress, process_item_use_mission_progress

logger = logging.getLogger(__name__)

# Global dictionaries to track PVP challenges and battles
pvp_challenges: Dict[str, Dict[str, Any]] = {}
active_pvp_battles: Dict[str, 'PvPBattleSystem'] = {}

# Rate limiting parameters
MAX_RETRIES = 5
BASE_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 30.0

# Anti-spam protection: track last interaction time for each user
# CRITICAL: Limited to prevent memory leaks
pvp_button_cooldowns: Dict[str, Dict[str, float]] = {}
MAX_PVP_COOLDOWN_USERS = 1000
PVP_COOLDOWN_EXPIRY = 3600  # 1 hour

def cleanup_pvp_cooldowns():
    """Remove old pvp_button_cooldowns to prevent memory leak"""
    current_time = datetime.now(timezone.utc).timestamp()
    expired_users = []
    
    for user_id, actions in list(pvp_button_cooldowns.items()):
        # Remove if user hasn't had any actions in 1 hour
        if actions:
            max_action_time = max(actions.values())
            if current_time - max_action_time > PVP_COOLDOWN_EXPIRY:
                expired_users.append(user_id)
        else:
            expired_users.append(user_id)
    
    for user_id in expired_users:
        del pvp_button_cooldowns[user_id]
    
    # If still too many, remove oldest
    if len(pvp_button_cooldowns) > MAX_PVP_COOLDOWN_USERS:
        # Find oldest users
        oldest_users = sorted(
            pvp_button_cooldowns.items(),
            key=lambda x: max(x[1].values()) if x[1] else 0
        )[:100]
        for user_id, _ in oldest_users:
            del pvp_button_cooldowns[user_id]
    
    return len(expired_users)

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
                 challenger_team: List[Character], 
                 defender_team: List[Character], 
                 challenger_player: Player, 
                 defender_player: Player,
                 challenge_id: str):
        self.challenger_team = challenger_team
        self.defender_team = defender_team
        self.challenger_player = challenger_player
        self.defender_player = defender_player
        self.challenge_id = challenge_id
        
        # Track current active character indices (start with first character)
        self.challenger_current_index: int = 0
        self.defender_current_index: int = 0
        
        # Get current active characters
        self.challenger = challenger_team[0] if challenger_team else None
        self.defender = defender_team[0] if defender_team else None
        
        # Reset HP to full for fair PVP battles
        self.challenger_hp: int = self.challenger.stats.HP if self.challenger else 0
        self.defender_hp: int = self.defender.stats.HP if self.defender else 0
        
        # Initialize gas values for PvP battle
        self.challenger_gas: int = self.challenger.max_gas if self.challenger else 0
        self.defender_gas: int = self.defender.max_gas if self.defender else 0
        
        # Determine first turn based on character speed
        challenger_speed = self.challenger.stats.SPD if self.challenger and hasattr(self.challenger, 'stats') and hasattr(self.challenger.stats, 'SPD') else 10
        defender_speed = self.defender.stats.SPD if self.defender and hasattr(self.defender, 'stats') and hasattr(self.defender.stats, 'SPD') else 10
        
        if challenger_speed > defender_speed:
            # Challenger is faster, goes first
            self.current_turn_user_id: str = str(challenger_player.user_id)
            self.current_turn: str = self.challenger.name if self.challenger else ""
        elif defender_speed > challenger_speed:
            # Defender is faster, goes first
            self.current_turn_user_id: str = str(defender_player.user_id)
            self.current_turn: str = self.defender.name if self.defender else ""
        else:
            # Speeds are equal, challenger goes first (or could randomize)
            self.current_turn_user_id: str = str(challenger_player.user_id)
            self.current_turn: str = self.challenger.name if self.challenger else ""
        self.turn_count = 0
        self.battle_ended: bool = False
        self.timeout_task: Optional[asyncio.Task] = None
        self._is_disposed: bool = False
        self.winner: Optional[str] = None
        self.winner_char: Optional[Character] = None
        
        # Ability cooldowns for both teams - ensure clean ability names
        self.challenger_cooldowns: Dict[str, int] = {}
        self.defender_cooldowns: Dict[str, int] = {}
        
        if self.challenger:
            self.challenger_cooldowns = {
                ability.name.strip().lstrip('_'): 0 for ability in (
                    (self.challenger.active_abilities or []) +
                    (self.challenger.passive_abilities or []) +
                    (self.challenger.ultimate_abilities or [])
                )
            }
        
        if self.defender:
            self.defender_cooldowns = {
                ability.name.strip().lstrip('_'): 0 for ability in (
                    (self.defender.active_abilities or []) +
                    (self.defender.passive_abilities or []) +
                    (self.defender.ultimate_abilities or [])
                )
            }
        
        # Buffs and debuffs
        self.challenger_buffs: Dict[str, Any] = {}
        self.defender_buffs: Dict[str, Any] = {}
        self.challenger_debuffs: Dict[str, int] = {}
        self.defender_debuffs: Dict[str, int] = {}
        
        # Character switched count (for PVP balancing)
        self.switches_remaining: int = 10  # Default max switches
        
        # Track used items
        self.challenger_used_item: bool = False
        self.defender_used_item: bool = False
        self.challenger_active_items: Dict[str, Any] = {}
        self.defender_active_items: Dict[str, Any] = {}

        # Store current battle message ID for timeout handling
        self.current_message_id: Optional[int] = None

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
        self.challenger_active_items.clear()
        self.defender_active_items.clear()
    
    def get_equipped_weapon(self, character: Character, shop_items: Dict):
        """Get character's equipped weapon from shop items"""
        if character.equipped_weapon and character.equipped_weapon in shop_items:
            item = shop_items[character.equipped_weapon]
            # Allow using gear and military items as weapons
            if hasattr(item, 'type') and item.type in ["weapon", "gear", "military"]:
                return item
        return None
        
    def switch_turn(self) -> None:
        """Switch turn between challenger and defender using user_id for clarity.
        If the current player has an `extra_action` buff, consume one and keep the turn."""
        # consume extra_action BEFORE switching (gives immediate extra move)
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            extra = self.challenger_buffs.get("extra_action", 0)
            if extra:
                if isinstance(extra, (int, float)):
                    if extra > 1:
                        self.challenger_buffs["extra_action"] = extra - 1
                    else:
                        del self.challenger_buffs["extra_action"]
                return
        else:
            extra = self.defender_buffs.get("extra_action", 0)
            if extra:
                if isinstance(extra, (int, float)):
                    if extra > 1:
                        self.defender_buffs["extra_action"] = extra - 1
                    else:
                        del self.defender_buffs["extra_action"]
                return

        # Normal switch behavior
        self.turn_count += 1
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            self.current_turn_user_id = str(self.defender_player.user_id)
            self.current_turn = self.defender.name
            self.defender_used_item = False
        else:
            self.current_turn_user_id = str(self.challenger_player.user_id)
            self.current_turn = self.challenger.name
            self.challenger_used_item = False
        self.update_cooldowns()
    
    def update_cooldowns(self) -> None:
        """Update cooldowns for the current player using user_id for clarity"""
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            # ...existing code for challenger...
            for ability_name in list(self.challenger_cooldowns.keys()):
                if self.challenger_cooldowns[ability_name] > 0:
                    self.challenger_cooldowns[ability_name] -= 1
            for debuff in list(self.challenger_debuffs.keys()):
                if self.challenger_debuffs[debuff] > 0:
                    self.challenger_debuffs[debuff] -= 1
                    if self.challenger_debuffs[debuff] <= 0:
                        del self.challenger_debuffs[debuff]
            # Decrement any explicit turn-counters (keys that end with _turns)
            for buff in list(self.challenger_buffs.keys()):
                if buff.endswith("_turns") and isinstance(self.challenger_buffs[buff], (int, float)):
                    self.challenger_buffs[buff] -= 1
                    if self.challenger_buffs[buff] <= 0:
                        # remove the paired stat (e.g. hp_regen_turns -> hp_regen)
                        stat_key = buff[:-6]
                        self.challenger_buffs.pop(stat_key, None)
                        del self.challenger_buffs[buff]
                elif isinstance(self.challenger_buffs[buff], (int, float)) and buff != "shield":
                    # legacy handling for numeric-duration-style buffs (preserve existing behavior)
                    if self.challenger_buffs[buff] > 1:
                        self.challenger_buffs[buff] -= 1
                        if self.challenger_buffs[buff] <= 0:
                            del self.challenger_buffs[buff]

            # Apply HP regen (percent or flat) if present
            if 'hp_regen' in self.challenger_buffs:
                regen_val = self.challenger_buffs['hp_regen']
                heal_amount = int(self.challenger.stats.HP * regen_val) if isinstance(regen_val, float) and regen_val < 1 else int(regen_val)
                self.challenger_hp = min(self.challenger.stats.HP, self.challenger_hp + heal_amount)

            if self.challenger_debuffs.get("bleed", 0) > 0:
                bleed_damage = max(10, self.defender.stats.ATK // 2)
                self.challenger_hp = max(0, self.challenger_hp - bleed_damage)
        else:
            for ability_name in list(self.defender_cooldowns.keys()):
                if self.defender_cooldowns[ability_name] > 0:
                    self.defender_cooldowns[ability_name] -= 1
            for debuff in list(self.defender_debuffs.keys()):
                if self.defender_debuffs[debuff] > 0:
                    self.defender_debuffs[debuff] -= 1
                    if self.defender_debuffs[debuff] <= 0:
                        del self.defender_debuffs[debuff]
            # Decrement any explicit turn-counters (keys that end with _turns)
            for buff in list(self.defender_buffs.keys()):
                if buff.endswith("_turns") and isinstance(self.defender_buffs[buff], (int, float)):
                    self.defender_buffs[buff] -= 1
                    if self.defender_buffs[buff] <= 0:
                        stat_key = buff[:-6]
                        self.defender_buffs.pop(stat_key, None)
                        del self.defender_buffs[buff]
                elif isinstance(self.defender_buffs[buff], (int, float)) and buff != "shield":
                    if self.defender_buffs[buff] > 1:
                        self.defender_buffs[buff] -= 1
                        if self.defender_buffs[buff] <= 0:
                            del self.defender_buffs[buff]

            # Apply HP regen (percent or flat) if present
            if 'hp_regen' in self.defender_buffs:
                regen_val = self.defender_buffs['hp_regen']
                heal_amount = int(self.defender.stats.HP * regen_val) if isinstance(regen_val, float) and regen_val < 1 else int(regen_val)
                self.defender_hp = min(self.defender.stats.HP, self.defender_hp + heal_amount)

            if self.defender_debuffs.get("bleed", 0) > 0:
                bleed_damage = max(10, self.challenger.stats.ATK // 2)
                self.defender_hp = max(0, self.defender_hp - bleed_damage)
    
    async def use_ability(self, ability_name: str, context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, Dict]:
        """
        Use an ability in PvP battle. Returns message and effects.
        """
        message = ""
        effects = {}
        # Use user_id for turn logic
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            current_char = self.challenger
            opponent_char = self.defender
            cooldowns = self.challenger_cooldowns
            gas = self.challenger_gas
            current_player = self.challenger_player
            opponent_player = self.defender_player
        else:
            current_char = self.defender
            opponent_char = self.challenger
            cooldowns = self.defender_cooldowns
            gas = self.defender_gas
            current_player = self.defender_player
            opponent_player = self.challenger_player
        
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
            
        # Check cooldown (always use cleaned name)
        if cooldowns.get(cleaned_ability_name, 0) > 0:
            return f"{cleaned_ability_name} is on cooldown for {cooldowns[cleaned_ability_name]} turns!", {}
            
        # Check gas cost
        gas_cost = ability.gas_cost or 20
        if gas < gas_cost:
            message = f"Not enough gas to use {ability_name}! Battle automatically ends."
            self.battle_ended = True
            # Set the opponent as winner using user_id
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                self.winner = self.defender.name
                self.winner_char = self.defender
            else:
                self.winner = self.challenger.name
                self.winner_char = self.challenger
            return message, {"insufficient_gas": True}
        # Deduct gas
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            self.challenger_gas -= gas_cost
        else:
            self.defender_gas -= gas_cost
        
        # Calculate opponent HP percent
        opponent_hp = self.defender_hp if self.current_turn_user_id == str(self.challenger_player.user_id) else self.challenger_hp
        opponent_max_hp = opponent_char.stats.HP if hasattr(opponent_char, 'stats') and hasattr(opponent_char.stats, 'HP') else (opponent_char.stats.get('HP', 0) if isinstance(opponent_char.stats, dict) else 100)
        target_hp_percent = opponent_hp / opponent_max_hp if opponent_max_hp > 0 else 1.0
            
        # Build context for ability effect (apply any active buff modifiers to stats)
        from utils.stats import apply_stat_buffs

        char_stats = current_char.stats.dict() if getattr(current_char, 'stats', None) else {}
        opp_stats = opponent_char.stats.dict() if getattr(opponent_char, 'stats', None) else {}

        # Apply per-side stat buffs using centralized helper (floats = multiplier, ints = additive)
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            char_stats = apply_stat_buffs(char_stats, self.challenger_buffs)
            opp_stats = apply_stat_buffs(opp_stats, self.defender_buffs)
        else:
            char_stats = apply_stat_buffs(char_stats, self.defender_buffs)
            opp_stats = apply_stat_buffs(opp_stats, self.challenger_buffs)

        # PvP is 1v1, so ally tracking works differently:
        # - In 1v1, ally_death_count = 0 (no team), flochs_last_standing = always True if playing
        # - demagogue_stacks track damage milestones instead of ally deaths
        # Track demagogue stacks based on damage taken (25% HP lost = 1 stack)
        char_hp = self.challenger_hp if self.current_turn_user_id == str(self.challenger_player.user_id) else self.defender_hp
        char_max_hp = current_char.stats.HP if hasattr(current_char, 'stats') and hasattr(current_char.stats, 'HP') else (current_char.stats.get('HP', 0) if isinstance(current_char.stats, dict) else 100)
        hp_lost_percent = 1.0 - (char_hp / char_max_hp) if char_max_hp > 0 else 0
        demagogue_stacks_from_damage = int(hp_lost_percent / 0.25)  # 1 stack per 25% HP lost
        
        ctx = {
            "character_stats": char_stats,
            "opponent_stats": opp_stats,
            "character_hp": char_hp,
            "opponent_hp": opponent_hp,
            "character_max_hp": char_max_hp,
            "opponent_max_hp": opponent_max_hp,
            "target_hp_percent": target_hp_percent,
            "pvp": True,
            "turn": self.turn_count,
            "gas": gas - gas_cost,
            "character_level": getattr(current_char, 'level', 1),
            "opponent_level": getattr(opponent_char, 'level', 1),
            "is_pvp": True,
            # Floch ability context - PvP adapts team mechanics to 1v1
            "ally_death_count": demagogue_stacks_from_damage,  # Use damage milestones as "fallen allies"
            "demagogue_stacks": demagogue_stacks_from_damage,  # Same tracking for demagogue's aura
            "flochs_last_standing": hp_lost_percent >= 0.5,  # Trigger "last stand" when below 50% HP
            # Base damage for abilities that scale with it
            "base_damage": char_stats.get("ATK", 50) + char_stats.get("INT", 0) // 2,
        }
        
        # Apply ability effect
        try:
            if ability.effect_function:
                from database.characters import AbilityEffect
                # Apply any item effects to the context before executing ability
                if self.current_turn_user_id == str(self.challenger_player.user_id):
                    attack_boost = self.challenger_buffs.get("attack_boost", 1.0)
                    if attack_boost > 1.0 and "character_stats" in ctx and "ATK" in ctx["character_stats"]:
                        ctx["character_stats"]["ATK"] = int(ctx["character_stats"]["ATK"] * attack_boost)
                    accuracy_boost = self.challenger_buffs.get("accuracy_boost", 0)
                    if accuracy_boost > 0 and "character_stats" in ctx and "ACC" in ctx["character_stats"]:
                        ctx["character_stats"]["ACC"] = ctx["character_stats"]["ACC"] + accuracy_boost
                    defense_boost = self.challenger_buffs.get("defense_boost", 0)
                    if defense_boost > 0 and "character_stats" in ctx and "DEF" in ctx["character_stats"]:
                        ctx["character_stats"]["DEF"] = ctx["character_stats"]["DEF"] + defense_boost
                else:
                    attack_boost = self.defender_buffs.get("attack_boost", 1.0)
                    if attack_boost > 1.0 and "character_stats" in ctx and "ATK" in ctx["character_stats"]:
                        ctx["character_stats"]["ATK"] = int(ctx["character_stats"]["ATK"] * attack_boost)
                    accuracy_boost = self.defender_buffs.get("accuracy_boost", 0)
                    if accuracy_boost > 0 and "character_stats" in ctx and "ACC" in ctx["character_stats"]:
                        ctx["character_stats"]["ACC"] = ctx["character_stats"]["ACC"] + accuracy_boost
                    defense_boost = self.defender_buffs.get("defense_boost", 0)
                    if defense_boost > 0 and "character_stats" in ctx and "DEF" in ctx["character_stats"]:
                        ctx["character_stats"]["DEF"] = ctx["character_stats"]["DEF"] + defense_boost
                # Prevent action if stunned
                if self.current_turn_user_id == str(self.challenger_player.user_id) and self.challenger_debuffs.get("stun", 0) > 0:
                    return f"{self.challenger.name} (Player 1) is stunned and cannot act this turn!", {}
                if self.current_turn_user_id == str(self.defender_player.user_id) and self.defender_debuffs.get("stun", 0) > 0:
                    return f"{self.defender.name} (Player 2) is stunned and cannot act this turn!", {}

                effect = ability.effect_function(ctx)
                if effect:
                    # Apply the effect
                    damage = getattr(effect, 'damage', 0) or 0
                    heal = getattr(effect, 'healed', 0) or 0
                    message = getattr(effect, 'message', f"{ability_name} used successfully!")
                    # Apply damage to opponent
                    if self.current_turn_user_id == str(self.challenger_player.user_id):
                        # apply damage/heal to defender and attacker respectively
                        self.defender_hp = max(0, self.defender_hp - damage)
                        if heal > 0:
                            self.challenger_hp = min(getattr(self.challenger.stats, 'HP', self.challenger_hp), self.challenger_hp + heal)

                        # explicit single-field effects
                        if getattr(effect, 'shield', 0):
                            self.challenger_buffs["shield"] = self.challenger_buffs.get("shield", 0) + effect.shield
                        if getattr(effect, 'stun_duration', 0):
                            self.defender_debuffs["stun"] = max(self.defender_debuffs.get("stun", 0), effect.stun_duration)
                        if getattr(effect, 'bleed_applied', False):
                            self.defender_debuffs["bleed"] = max(self.defender_debuffs.get("bleed", 0), 3)

                        # reflect damage if defender has damage_reflection
                        refl_pct = self.defender_buffs.get("damage_reflection", 0)
                        if refl_pct and damage > 0:
                            reflected = int(damage * refl_pct)
                            self.challenger_hp = max(0, self.challenger_hp - reflected)
                            effects['reflected'] = reflected
                            message += f"\n↺ {self.defender.name} reflected {reflected} damage back!"

                        # store any generic buffs/debuffs returned by ability
                        for buff_name, buff_value in getattr(effect, 'buffs', {}) .items():
                            self.challenger_buffs[buff_name] = buff_value
                        for debuff_name, debuff_value in getattr(effect, 'debuffs', {}) .items():
                            self.defender_debuffs[debuff_name] = debuff_value
                    else:
                        # attacker is defender in this branch
                        self.challenger_hp = max(0, self.challenger_hp - damage)
                        if heal > 0:
                            self.defender_hp = min(getattr(self.defender.stats, 'HP', self.defender_hp), self.defender_hp + heal)
                        if getattr(effect, 'shield', 0):
                            self.defender_buffs["shield"] = self.defender_buffs.get("shield", 0) + effect.shield
                        if getattr(effect, 'stun_duration', 0):
                            self.challenger_debuffs["stun"] = max(self.challenger_debuffs.get("stun", 0), effect.stun_duration)
                        if getattr(effect, 'bleed_applied', False):
                            self.challenger_debuffs["bleed"] = max(self.challenger_debuffs.get("bleed", 0), 3)

                        # reflect damage if challenger has damage_reflection
                        refl_pct = self.challenger_buffs.get("damage_reflection", 0)
                        if refl_pct and damage > 0:
                            reflected = int(damage * refl_pct)
                            self.defender_hp = max(0, self.defender_hp - reflected)
                            effects['reflected'] = reflected
                            message += f"\n↺ {self.challenger.name} reflected {reflected} damage back!"

                        # store any generic buffs/debuffs returned by ability
                        for buff_name, buff_value in getattr(effect, 'buffs', {}) .items():
                            self.defender_buffs[buff_name] = buff_value
                        for debuff_name, debuff_value in getattr(effect, 'debuffs', {}) .items():
                            self.challenger_debuffs[debuff_name] = debuff_value

                    # Return effects for UI updates
                    effects = {
                        "damage": damage,
                        "heal": heal,
                        "shield": getattr(effect, 'shield', 0) or 0,
                        "stun": getattr(effect, 'stun_duration', 0) or 0,
                        "bleed": getattr(effect, 'bleed_applied', False),
                        "reflected": effects.get('reflected', 0) if isinstance(effects, dict) else 0
                    }
        except Exception as e:
            logger.error(f"Error applying ability {ability_name}: {e}")
            return f"Error using {ability_name}: {str(e)}", {}
            
        # Apply cooldown - use the cleaned ability name
        cooldowns[cleaned_ability_name] = ability.cooldown or 1
        
        # Check for character death and switch if possible
        switched = self.switch_character_on_death()
        if switched:
            message += f"\n🔄 Character switched due to defeat!"
        
        return message, effects
        
    async def use_basic_attack(self, context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, Dict]:
        """
        Use basic attack in PvP battle. Returns message and effects.
        """
        message = ""
        effects = {"damage": 0}

        # Determine current character and opponent by user_id
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            current_char = self.challenger
            opponent_char = self.defender
            gas = self.challenger_gas
            # Check for stun debuff
            if self.challenger_debuffs.get("stun", 0) > 0:
                return f"{self.challenger.name} is stunned and cannot act this turn!", {}
        else:
            current_char = self.defender
            opponent_char = self.challenger
            gas = self.defender_gas
            # Check for stun debuff
            if self.defender_debuffs.get("stun", 0) > 0:
                return f"{self.defender.name} is stunned and cannot act this turn!", {}

        # Check gas cost - basic attacks cost 20 gas
        if gas < 20:
            # End the battle automatically if player doesn't have enough gas
            message = f"Not enough gas to perform a basic attack! Battle automatically ends."
            self.battle_ended = True
            # Set the opponent as winner
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                self.winner = self.defender.name
                self.winner_char = self.defender
            else:
                self.winner = self.challenger.name
                self.winner_char = self.challenger
            return message, {"insufficient_gas": True}

        # Deduct gas
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            self.challenger_gas -= 20
        else:
            self.defender_gas -= 20

        # Get equipped weapon for damage calculation
        shop_items = context.bot_data.get("shop_items") or {}

        # Try to refresh character data from DB first
        db = context.bot_data.get("db") or Database()
        refreshed_character = None
        try:
            if hasattr(current_char, 'user_id') and hasattr(current_char, 'name'):
                refreshed_character = await db.get_character(int(current_char.user_id), current_char.name)
                if refreshed_character:
                    # Update current character with refreshed data
                    if self.current_turn_user_id == str(self.challenger_player.user_id):
                        self.challenger = refreshed_character
                        current_char = refreshed_character
                    else:
                        self.defender = refreshed_character
                        current_char = refreshed_character
        except Exception as e:
            logger.error(f"Error refreshing character data in use_basic_attack: {e}")

        # Debug log to see equipped weapon
        logger.debug(f"Equipped weapon for {current_char.name}: {current_char.equipped_weapon}")

        # Now get the weapon with latest data
        weapon = self.get_equipped_weapon(current_char, shop_items)
        logger.debug(f"Found weapon: {weapon.name if weapon else 'None'}")

        # Calculate damage
        if weapon:
            damage_min = weapon.attributes.get("damage_min", 10)
            damage_max = weapon.attributes.get("damage_max", 20)
            base_damage = random.randint(int(damage_min), int(damage_max))
            weapon_name = weapon.name
        else:
            try:
                base_atk = current_char.stats.ATK or 25
                base_damage = max(1, base_atk + random.randint(25, 35))
                weapon_name = "basic strike"
            except Exception as e:
                logger.error(f"Error calculating basic attack damage: {e}")
                base_damage = 10
                weapon_name = "basic strike"

        # Check for mission-based PvP damage bonus
        try:
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                player = self.challenger_player
            else:
                player = self.defender_player
            pvp_bonuses = getattr(player, "pvp_bonuses", {})
            if pvp_bonuses:
                damage_bonus = pvp_bonuses.get("damage_bonus", 0)
                if damage_bonus:
                    base_damage += damage_bonus
        except Exception as e:
            logger.error(f"Error applying PvP damage bonus: {e}")

        # Apply item effects
        damage_multiplier = 1.0

        # Check for attack boost from items
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            attack_boost = self.challenger_buffs.get("attack_boost", 1.0)
            if attack_boost > 1.0:
                damage_multiplier *= attack_boost
            # Check for item effects on critical hits
            crit_boost = self.challenger_buffs.get("crit_boost", 0)
            if crit_boost > 0 and random.randint(1, 100) <= crit_boost:
                damage_multiplier *= 1.5
                weapon_name = f"{weapon_name} (CRITICAL)"
        else:
            attack_boost = self.defender_buffs.get("attack_boost", 1.0)
            if attack_boost > 1.0:
                damage_multiplier *= attack_boost
            # Check for item effects on critical hits
            crit_boost = self.defender_buffs.get("crit_boost", 0)
            if crit_boost > 0 and random.randint(1, 100) <= crit_boost:
                damage_multiplier *= 1.5
                weapon_name = f"{weapon_name} (CRITICAL)"

        # incorporate any explicit damage_multiplier buff
        damage_multiplier = damage_multiplier * (self.challenger_buffs.get('damage_multiplier', 1.0) if self.current_turn_user_id == str(self.challenger_player.user_id) else self.defender_buffs.get('damage_multiplier', 1.0))

        # Apply the multiplier
        total_damage = int(base_damage * damage_multiplier)

        # Apply damage
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            self.defender_hp = max(0, self.defender_hp - total_damage)
        else:
            self.challenger_hp = max(0, self.challenger_hp - total_damage)

        # Check if this was a critical hit
        is_critical = "CRITICAL" in weapon_name
        if is_critical:
            effects["critical"] = True
            weapon_name = weapon_name.replace(" (CRITICAL)", "")

        message = f"⚔️ {current_char.name} attacks with {weapon_name}, dealing {total_damage} damage!"
        effects["damage"] = total_damage

        # Check for character death and switch if possible
        switched = self.switch_character_on_death()
        if switched:
            message += f"\n🔄 Character switched due to defeat!"

        return message, effects
        
    def surrender(self) -> str:
        """Handle surrender action"""
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            self.winner = self.defender.name
            self.winner_char = self.defender
            surrendered_player = self.challenger_player.name
            surrendered_char = self.challenger.name
            winner_player = self.defender_player.name
            winner_char = self.defender.name
        else:
            self.winner = self.challenger.name
            self.winner_char = self.challenger
            surrendered_player = self.defender_player.name
            surrendered_char = self.defender.name
            winner_player = self.challenger_player.name
            winner_char = self.challenger.name
            
        # The actual message will be formatted in handle_pvp_battle_end
        # This is just a basic message that will be enhanced with player mentions
        message = f"🏳️ {surrendered_player}'s {surrendered_char} has surrendered! {winner_player}'s {winner_char} wins the battle!"
            
        self.battle_ended = True
        return message
        
    def switch_character_on_death(self) -> bool:
        """
        Switch to the next available character when current character dies.
        Returns True if successfully switched, False if no characters available.
        """
        switched = False
        
        # Check challenger character
        if self.challenger_hp <= 0 and self.challenger_current_index < len(self.challenger_team) - 1:
            # Try to switch to next character
            self.challenger_current_index += 1
            new_char = self.challenger_team[self.challenger_current_index]
            self.challenger = new_char
            self.challenger_hp = new_char.stats.HP
            self.challenger_gas = new_char.max_gas
            
            # Reset cooldowns for new character
            self.challenger_cooldowns = {
                ability.name.strip().lstrip('_'): 0 for ability in (
                    (new_char.active_abilities or []) +
                    (new_char.passive_abilities or []) +
                    (new_char.ultimate_abilities or [])
                )
            }
            
            # Clear buffs and debuffs for new character
            self.challenger_buffs.clear()
            self.challenger_debuffs.clear()
            self.challenger_active_items.clear()
            self.challenger_used_item = False
            
            switched = True
            logger.info(f"Challenger switched to character: {new_char.name}")
        
        # Check defender character
        if self.defender_hp <= 0 and self.defender_current_index < len(self.defender_team) - 1:
            # Try to switch to next character
            self.defender_current_index += 1
            new_char = self.defender_team[self.defender_current_index]
            self.defender = new_char
            self.defender_hp = new_char.stats.HP
            self.defender_gas = new_char.max_gas
            
            # Reset cooldowns for new character
            self.defender_cooldowns = {
                ability.name.strip().lstrip('_'): 0 for ability in (
                    (new_char.active_abilities or []) +
                    (new_char.passive_abilities or []) +
                    (new_char.ultimate_abilities or [])
                )
            }
            
            # Clear buffs and debuffs for new character
            self.defender_buffs.clear()
            self.defender_debuffs.clear()
            self.defender_active_items.clear()
            self.defender_used_item = False
            
            switched = True
            logger.info(f"Defender switched to character: {new_char.name}")
        
        # Check if battle should end (no more characters available)
        challenger_has_characters = self.challenger_current_index < len(self.challenger_team) - 1 or self.challenger_hp > 0
        defender_has_characters = self.defender_current_index < len(self.defender_team) - 1 or self.defender_hp > 0
        
        if not challenger_has_characters or not defender_has_characters:
            self.battle_ended = True
            if not challenger_has_characters:
                self.winner = self.defender.name
                self.winner_char = self.defender
            elif not defender_has_characters:
                self.winner = self.challenger.name
                self.winner_char = self.challenger
        
        # Update turn order based on current characters' SPD after switch
        if switched and not self.battle_ended:
            self._update_turn_order()
        
        return switched
    
    def manual_switch_character(self, target_index: int) -> str:
        """
        Manually switch to a specific character in the team.
        Returns success message or error message.
        """
        if self.switches_remaining <= 0:
            return "No more character switches allowed in this battle."
        
        # Determine which player is switching
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            team = self.challenger_team
            current_index = self.challenger_current_index
            player_name = self.challenger_player.name
        else:
            team = self.defender_team
            current_index = self.defender_current_index
            player_name = self.defender_player.name
        
        # Validate target index
        if target_index < 0 or target_index >= len(team):
            return "Invalid character position."
        
        if target_index == current_index:
            return "That's your current character."
        
        # Check if target character is still alive (not defeated)
        if target_index <= current_index:
            return "Cannot switch to a defeated character."
        
        # Perform the switch
        old_char = team[current_index]
        new_char = team[target_index]
        
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            self.challenger_current_index = target_index
            self.challenger = new_char
            self.challenger_hp = new_char.stats.HP
            self.challenger_gas = new_char.max_gas
            
            # Reset cooldowns for new character
            self.challenger_cooldowns = {
                ability.name.strip().lstrip('_'): 0 for ability in (
                    (new_char.active_abilities or []) +
                    (new_char.passive_abilities or []) +
                    (new_char.ultimate_abilities or [])
                )
            }
            
            # Clear buffs and debuffs for new character
            self.challenger_buffs.clear()
            self.challenger_debuffs.clear()
            self.challenger_active_items.clear()
            self.challenger_used_item = False
        else:
            self.defender_current_index = target_index
            self.defender = new_char
            self.defender_hp = new_char.stats.HP
            self.defender_gas = new_char.max_gas
            
            # Reset cooldowns for new character
            self.defender_cooldowns = {
                ability.name.strip().lstrip('_'): 0 for ability in (
                    (new_char.active_abilities or []) +
                    (new_char.passive_abilities or []) +
                    (new_char.ultimate_abilities or [])
                )
            }
            
            # Clear buffs and debuffs for new character
            self.defender_buffs.clear()
            self.defender_debuffs.clear()
            self.defender_active_items.clear()
            self.defender_used_item = False
        
        self.switches_remaining -= 1
        
        # Update turn order based on current characters' SPD after switch
        self._update_turn_order()
        
        return f"🔄 {player_name} switched from {old_char.name} to {new_char.name}! (Switches left: {self.switches_remaining})"
    
    def _update_turn_order(self) -> None:
        """Update turn order based on current active characters' SPD."""
        challenger_speed = self.challenger.stats.SPD if self.challenger and hasattr(self.challenger, 'stats') and hasattr(self.challenger.stats, 'SPD') else 10
        defender_speed = self.defender.stats.SPD if self.defender and hasattr(self.defender, 'stats') and hasattr(self.defender.stats, 'SPD') else 10
        
        if challenger_speed > defender_speed:
            # Challenger is faster, goes first
            self.current_turn_user_id = str(self.challenger_player.user_id)
            self.current_turn = self.challenger.name
        elif defender_speed > challenger_speed:
            # Defender is faster, goes first
            self.current_turn_user_id = str(self.defender_player.user_id)
            self.current_turn = self.defender.name
        else:
            # Speeds are equal, challenger goes first (or could randomize)
            self.current_turn_user_id = str(self.challenger_player.user_id)
            self.current_turn = self.challenger.name
        
    async def use_item(self, item_key: str, context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, Dict]:
        """
        Use an item in PvP battle. Returns message and effects.
        """
        message = ""
        effects = {}
        
        # Determine current character and player
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            current_char = self.challenger
            opponent_char = self.defender
            current_player = self.challenger_player
            used_item = self.challenger_used_item
            active_items = self.challenger_active_items
        else:
            current_char = self.defender
            opponent_char = self.challenger
            current_player = self.defender_player
            used_item = self.defender_used_item
            active_items = self.defender_active_items
        
        # Check if player already used an item this turn
        if used_item:
            return "You've already used an item this turn!", {}
        
        # Get DB instance and refresh player
        db = context.bot_data.get("db") or Database()
        try:
            updated_player = await db.get_player(str(current_player.user_id))
            if not updated_player:
                return "Error: Player not found!", {}
                
            # Check if player has the item
            if item_key not in updated_player.inventory or updated_player.inventory[item_key] <= 0:
                return f"You don't have any {item_key} in your inventory!", {}
        except Exception as e:
            logger.error(f"Error refreshing player data: {e}")
            return "Error retrieving player data.", {}
        
        # Get the item from shop_items
        shop_items = context.bot_data.get("shop_items", {})
        item = shop_items.get(item_key)
        
        if not item:
            return f"Item {item_key} not found in shop database!", {}
        
        # Check if the item is a utility
        if item.type != "utility":
            return f"{item.name} cannot be used in battle!", {}
        
        # Apply item effect based on its attributes
        buff_name = item.attributes.get("buff_name", "Unknown Effect")
        
        # Apply different effects based on the item
        if item_key == "time_contract":
            # Reduce cooldowns
            cooldown_reduction = item.attributes.get("cooldown_reduction", 1)
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                for ability_name in self.challenger_cooldowns:
                    if self.challenger_cooldowns[ability_name] > 0:
                        self.challenger_cooldowns[ability_name] = max(0, self.challenger_cooldowns[ability_name] - cooldown_reduction)
            else:
                for ability_name in self.defender_cooldowns:
                    if self.defender_cooldowns[ability_name] > 0:
                        self.defender_cooldowns[ability_name] = max(0, self.defender_cooldowns[ability_name] - cooldown_reduction)
            message = f"⏱️ {current_char.name} used a Time Contract! All ability cooldowns reduced by {cooldown_reduction} turns!"
            effects["cooldown_reduction"] = cooldown_reduction
            active_items[item_key] = {"name": buff_name, "effect": "cooldown_reduction", "value": cooldown_reduction}
            
            # Add time_contract to player's active effects for Mission 15: Temporal Gambit
            try:
                active_effects = getattr(current_player, "active_effects", {})
                if not active_effects:
                    active_effects = {}
                active_effects["time_contract"] = {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                }
                
                # Update player with active effect
                updated_player_data = {"active_effects": active_effects}
                await db.update_player(int(current_player.user_id), updated_player_data)
            except Exception as e:
                logger.error(f"Failed to update player active effects: {e}")
            
        elif item_key == "training_dummy":
            # Increase attack and add buffs
            attack_multiplier = item.attributes.get("attack_multiplier", 1.05)
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                self.challenger_buffs["attack_boost"] = attack_multiplier
            else:
                self.defender_buffs["attack_boost"] = attack_multiplier
            message = f"🎯 {current_char.name} used a Training Dummy! Attack power increased by {int((attack_multiplier-1)*100)}%!"
            effects["attack_boost"] = attack_multiplier
            active_items[item_key] = {"name": buff_name, "effect": "attack_boost", "value": attack_multiplier}
            
        elif item_key == "battle_journal":
            # Increase accuracy and defense
            accuracy_bonus = item.attributes.get("accuracy_bonus", 5)
            defense_bonus = item.attributes.get("defense_bonus", 5)
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                self.challenger_buffs["accuracy_boost"] = accuracy_bonus
                self.challenger_buffs["defense_boost"] = defense_bonus
            else:
                self.defender_buffs["accuracy_boost"] = accuracy_bonus
                self.defender_buffs["defense_boost"] = defense_bonus
            message = f"📖 {current_char.name} used a Battle Journal! Accuracy +{accuracy_bonus} and Defense +{defense_bonus}!"
            effects["accuracy_boost"] = accuracy_bonus
            effects["defense_boost"] = defense_bonus
            active_items[item_key] = {"name": buff_name, "effect": "stat_boost", "accuracy": accuracy_bonus, "defense": defense_bonus}
            
        elif item_key == "titan_biology_manual":
            # Critical rate boost in PvP
            crit_bonus = 15  # 15% crit chance increase in PvP
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                self.challenger_buffs["crit_boost"] = crit_bonus
            else:
                self.defender_buffs["crit_boost"] = crit_bonus
            message = f"📚 {current_char.name} used a Titan Biology Manual! Critical hit chance +{crit_bonus}%!"
            effects["crit_boost"] = crit_bonus
            active_items[item_key] = {"name": buff_name, "effect": "crit_boost", "value": crit_bonus}
            
        elif item_key == "bounty_permit":
            # Special case for bounty permit in PvP - small HP recovery
            heal_amount = int(current_char.stats.HP * 0.15)  # 15% HP recovery
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                self.challenger_hp = min(self.challenger.stats.HP, self.challenger_hp + heal_amount)
            else:
                self.defender_hp = min(self.defender.stats.HP, self.defender_hp + heal_amount)
            message = f"📜 {current_char.name} used a Bounty Permit for emergency aid! Recovered {heal_amount} HP!"
            effects["heal"] = heal_amount
            active_items[item_key] = {"name": buff_name, "effect": "heal", "value": heal_amount}
        
        else:
            # Generic effect for unknown utility items
            if self.current_turn_user_id == str(self.challenger_player.user_id):
                self.challenger_buffs["item_boost"] = 1
            else:
                self.defender_buffs["item_boost"] = 1
            message = f"🧪 {current_char.name} used {item.name}! Applied {buff_name} effect."
            effects["unknown_item"] = True
            active_items[item_key] = {"name": buff_name, "effect": "unknown"}
        
        # Consume the item (reduce quantity by 1)
        updated_inventory = updated_player.inventory.copy()
        updated_inventory[item_key] -= 1
        if updated_inventory[item_key] <= 0:
            del updated_inventory[item_key]
        
        # Update player inventory in database
        try:
            # Convert to int if needed, some DB functions expect int user_id
            user_id = int(current_player.user_id) if current_player.user_id else 0
            await db.update_player(user_id, {"inventory": updated_inventory})
        except Exception as e:
            logger.error(f"Error updating player inventory: {e}")
            # Still continue with the battle even if DB update fails
        
        # Mark that player used an item this turn
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            self.challenger_used_item = True
        else:
            self.defender_used_item = True
            
        # Update mission progress for item usage
        try:
            from database.missions import process_item_use_mission_progress
            # Get updated player object after inventory change
            updated_player = await db.get_player(str(current_player.user_id))
            if updated_player:
                notifications = await process_item_use_mission_progress(db, updated_player, item_key)
                if notifications:
                    # Send mission notification privately instead of adding to battle message
                    try:
                        await context.bot.send_message(
                            chat_id=int(current_player.user_id),
                            text=notifications[0],
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception as e:
                        logger.error(f"Failed to send private mission notification for item use: {e}")
        except Exception as e:
            logger.error(f"Error updating mission progress for item use: {e}")
        
        return message, effects
    

        
    def get_battle_status(self) -> Dict:
        """Return current battle state for UI display (HP bars, buffs, debuffs, etc)."""
        challenger_hp_percent = self.challenger_hp / self.challenger.stats.HP
        defender_hp_percent = self.defender_hp / self.defender.stats.HP
        challenger_bar = "■" * int(challenger_hp_percent * 10) + "□" * (10 - int(challenger_hp_percent * 10))
        defender_bar = "■" * int(defender_hp_percent * 10) + "□" * (10 - int(defender_hp_percent * 10))
        # Use user_id to determine current turn
        if self.current_turn_user_id == str(self.challenger_player.user_id):
            current_player_id = self.challenger_player.user_id
            current_player_first_name = self.challenger_player.name
        else:
            current_player_id = self.defender_player.user_id
            current_player_first_name = self.defender_player.name
        challenger_player_name = self.challenger_player.name
        defender_player_name = self.defender_player.name
        # Add "«" symbol next to the current turn player (ensure only one player has the indicator)
        challenger_indicator = " « Turn" if self.current_turn_user_id == str(self.challenger_player.user_id) else ""
        defender_indicator = " « Turn" if self.current_turn_user_id == str(self.defender_player.user_id) else ""
        
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
            
        # Add active items to status message
        if self.challenger_active_items:
            items_display = [f"{item_data['name']}" for _, item_data in self.challenger_active_items.items()]
            if items_display:
                status_message += f"🎒 {self.challenger.name} active items: {', '.join(items_display)}\n"
                
        if self.defender_active_items:
            items_display = [f"{item_data['name']}" for _, item_data in self.defender_active_items.items()]
            if items_display:
                status_message += f"🎒 {self.defender.name} active items: {', '.join(items_display)}\n"
            
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
        """Calculate rewards for a PvP battle.

        New balanced rules:
        - Winner marks reduced and scaled by team average level (base 150-300)
        - Loser receives modest XP (30-60) to encourage participation but no large marks

        This lowers PvP inflation while keeping PvP attractive. No valor/crystal rewards are granted here.
        """
        rewards = {
            "winner": {"xp": 0, "marks": 0, "valor": 0, "crystal": 0},
            "loser": {"xp": 0, "marks": 0, "valor": 0, "crystal": 0}
        }

        if not self.winner:
            return rewards

        # Determine winner/loser objects
        if self.winner == self.challenger.name:
            winner_char = self.challenger
            loser_char = self.defender
            winner_player = self.challenger_player
            loser_player = self.defender_player
        else:
            winner_char = self.defender
            loser_char = self.challenger
            winner_player = self.defender_player
            loser_player = self.challenger_player

        # Determine a team-average level to scale rewards reasonably (if teams are same, use  single char level)
        try:
            def average_level(team):
                if not team:
                    return 1
                levels = [getattr(m, 'level', 1) if hasattr(m, 'level') else (m.get('level', 1) if isinstance(m, dict) else 1) for m in team]
                return sum(levels) / max(1, len(levels))
        except Exception:
            average_level = lambda t: getattr((t[0] if t else None), 'level', 1) if t else 1

        winner_team_level = average_level(self.challenger_team if self.winner == self.challenger.name else self.defender_team)
        loser_team_level = average_level(self.defender_team if self.winner == self.challenger.name else self.challenger_team)

        # XP rewards - modest
        # Winner XP: scales with team level slightly
        winner_xp_base = int(max(30, min(150, 30 + winner_team_level * 0.6)))
        rewards["winner"]["xp"] = random.randint(max(30, winner_xp_base - 10), min(120, winner_xp_base + 10))
        # Loser XP: small consolation
        loser_xp_base = int(max(20, min(80, 20 + loser_team_level * 0.5)))
        rewards["loser"]["xp"] = random.randint(max(20, loser_xp_base - 5), min(70, loser_xp_base + 10))

        # Marks reward: LOWERED significantly - base 150-300 scaled by level ratio
        # Compute a level multiplier to scale slightly for stronger players but avoid runaway inflation
        level_multiplier = max(0.5, min(2.0, (winner_team_level + 1) / (max(1, loser_team_level) + 1)))
        base_marks_min = 150
        base_marks_max = 300
        scaled_min = int(base_marks_min * level_multiplier)
        scaled_max = int(base_marks_max * level_multiplier)
        rewards["winner"]["marks"] = random.randint(scaled_min, scaled_max)

        # Loser receives no marks in PvP (prevents repeated farming), only xp
        rewards["loser"]["marks"] = 0

        # Keep valor and crystal as zero (no new reward types added)
        rewards["winner"]["valor"] = 0
        rewards["winner"]["crystal"] = 0

        return rewards


async def generate_pvp_ability_keyboard(battle: PvPBattleSystem, context: ContextTypes.DEFAULT_TYPE) -> List[List[InlineKeyboardButton]]:
    """Generate keyboard buttons for valid abilities and actions in PvP."""
    keyboard = []
    
    # Determine current character
    if battle.current_turn_user_id == str(battle.challenger_player.user_id):
        current_char = battle.challenger
        cooldowns = battle.challenger_cooldowns
        gas = battle.challenger_gas
    else:
        current_char = battle.defender
        cooldowns = battle.defender_cooldowns
        gas = battle.defender_gas
    
    # Always refresh character from DB before showing abilities and weapon
    db = context.bot_data.get("db") or Database()
    refreshed_character = None
    try:
        if hasattr(current_char, 'user_id') and hasattr(current_char, 'name'):
            refreshed_character = await db.get_character(int(current_char.user_id), current_char.name)
            if refreshed_character:
                # Update the character in the battle system with refreshed data
                if battle.current_turn_user_id == str(battle.challenger_player.user_id):
                    battle.challenger = refreshed_character
                    current_char = refreshed_character
                else:
                    battle.defender = refreshed_character
                    current_char = refreshed_character
    except Exception as e:
        logger.error(f"Error refreshing character data: {e}")
        
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
            # Ensure is_unlocked is always a boolean
            is_unlocked = bool(getattr(ability, 'is_unlocked', False)) or current_char.unlocked_abilities.get(ability.name, False)
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
                    f"⛽ {prefix} {clean_ability_name} (Need {gas_cost} gas - will lose battle)",
                    callback_data=f"pvp_lowgas_{clean_ability_name}"
                )])
                
    # Add basic attack button
    shop_items = context.bot_data.get("shop_items") or {}
    # Log equipped weapon for debugging
    logger.debug(f"Equipped weapon for {current_char.name}: {current_char.equipped_weapon}")
    weapon = battle.get_equipped_weapon(current_char, shop_items)
    logger.debug(f"Found weapon object: {weapon.name if weapon else 'None'}")
    
    if gas >= 20:
        if weapon:
            keyboard.append([InlineKeyboardButton(f"⚔️ {weapon.name}", callback_data="pvp_basic_attack")])
        else:
            keyboard.append([InlineKeyboardButton("⚔️ Basic Attack", callback_data="pvp_basic_attack")])
    else:
        if weapon:
            keyboard.append([InlineKeyboardButton(f"⛽ {weapon.name} (Low Gas - will lose battle)", callback_data="pvp_lowgas_basic_attack")])
        else:
            keyboard.append([InlineKeyboardButton("⛽ Basic Attack (Low Gas - will lose battle)", callback_data="pvp_lowgas_basic_attack")])
    
    # Get player's utility items info for the "Use Item" button
    db = context.bot_data.get("db") or Database()
    shop_items = context.bot_data.get("shop_items") or {}
    
    # Get player's utility items
    if battle.current_turn_user_id == str(battle.challenger_player.user_id):
        used_item = battle.challenger_used_item
        player_id = battle.challenger.user_id
    else:
        used_item = battle.defender_used_item
        player_id = battle.defender.user_id
    
    # Add switch and surrender buttons (without item button for now)
    # Check if player has multiple characters available for switching
    current_team = battle.challenger_team if battle.current_turn_user_id == str(battle.challenger_player.user_id) else battle.defender_team
    current_index = battle.challenger_current_index if battle.current_turn_user_id == str(battle.challenger_player.user_id) else battle.defender_current_index
    
    # Check if there are characters available to switch to
    available_switches = []
    for i in range(current_index + 1, len(current_team)):
        if i < len(current_team):  # Make sure index is valid
            char = current_team[i]
            available_switches.append((i, char))
    
    # Create the bottom row with switches, items, and surrender
    bottom_row = []
    
    # If there are switches available, add a single "Switch" button
    if available_switches:
        bottom_row.append(InlineKeyboardButton("🔄 Switch", callback_data="pvp_show_switches"))
    
    # Add item button if not used this turn
    if not used_item:
        bottom_row.append(InlineKeyboardButton("🎒 Use Item", callback_data="pvp_show_items"))
    
    # Always add surrender button
    bottom_row.append(InlineKeyboardButton("🏳️ Surrender", callback_data="pvp_surrender"))
    
    if bottom_row:
        keyboard.append(bottom_row)
    
    return keyboard


async def pvp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /pvp command to challenge another player or see PvP status."""
    if not update.effective_user or not update.message:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is currently in a battle
    async with active_battles_lock:
        if user_id in active_battles:
            await update.message.reply_text("⚔️ You are currently in a titan battle! Complete it first before challenging others.")
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
        
        # Find the target user in database (use sanitized get_player)
        try:
            defender_player = await db.get_player(target_id)
        except Exception as e:
            logger.error(f"Error finding target player: {e}")
            await update.message.reply_text("Error finding target player.")
            return

        if not defender_player:
            await update.message.reply_text(f"Player '{target_username}' has not started the game yet.")
            return

        # Check if user is challenging themselves
        if target_id == user_id:
            await update.message.reply_text("You can't challenge yourself to a PVP battle!")
            return
            
        # Check if target is already in a battle
        defender_id = str(defender_player.user_id) if hasattr(defender_player, 'user_id') else str(defender_player.get("user_id", target_id))
        if defender_id in active_battles:
            await update.message.reply_text(f"{defender_player.name if hasattr(defender_player, 'name') else defender_player.get('name', target_username)} is currently in a battle with a titan!")
            return
            
        if defender_id in active_pvp_battles:
            await update.message.reply_text(f"{defender_player.name if hasattr(defender_player, 'name') else defender_player.get('name', target_username)} is already in a PVP battle!")
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

        # Get defender's character data (already loaded above)
        
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
                    f"<b>Challenge Issued:</b>\n"
                    f"<b><a href='tg://user?id={challenger_player.user_id}'>{challenger_player.name}</a> vs <a href='tg://user?id={defender_player.user_id}'>{defender_player.name}</a></b>\n\n"
                    f"<b><a href='tg://user?id={defender_player.user_id}'>{defender_player.name}</a>, the challenge is set</b>\n"
                    f"<b>The battlefield awaits your decision</b>\n\n"
                    f"<b>⚔️ Switches Allowed : {10}</b>\n"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
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
            "Challenge other players to battles!\n"
            "Reply to another player's message with /pvp to challenge them."
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


async def generate_pvp_items_keyboard(battle: PvPBattleSystem, context: ContextTypes.DEFAULT_TYPE) -> List[List[InlineKeyboardButton]]:
    """Generate keyboard buttons for available items in PvP."""
    keyboard = []
    
    # Determine current character and player
    if battle.current_turn_user_id == str(battle.challenger_player.user_id):
        current_player = battle.challenger_player
    else:
        current_player = battle.defender_player
    
    # Get DB instance and refresh player
    db = context.bot_data.get("db") or Database()
    shop_items = context.bot_data.get("shop_items") or {}
    
    try:
        player = await db.get_player(str(current_player.user_id))
        if not player:
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="pvp_back_to_battle")])
            return keyboard
            
        # Check for utility items in inventory
        has_items = False
        utility_items = []
        
        for item_key, quantity in player.inventory.items():
            if item_key in shop_items and shop_items[item_key].type == "utility" and quantity > 0:
                item = shop_items[item_key]
                utility_items.append((item_key, item.name, quantity))
                has_items = True
        
        # Sort items by name
        utility_items.sort(key=lambda x: x[1])
        
        # Add buttons for each item (2 per row)
        row = []
        for idx, (item_key, item_name, quantity) in enumerate(utility_items):
            row.append(InlineKeyboardButton(f"{item_name} ({quantity})", callback_data=f"pvp_use_item_{item_key}"))
            if len(row) == 2 or idx == len(utility_items) - 1:
                keyboard.append(row)
                row = []
        
        if not has_items:
            keyboard.append([InlineKeyboardButton("No items available", callback_data="pvp_no_items")])
    except Exception as e:
        logger.error(f"Error generating items keyboard: {e}")
    
    # Always add back button in a new row
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="pvp_back_to_battle")])
    return keyboard

async def handle_pvp_show_switches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle showing available characters to switch to."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        try:
            await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        except:
            pass
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn
    if user_id != battle.current_turn_user_id:
        try:
            await safe_api_call(query.answer, "It's not your turn!", show_alert=True)
        except:
            pass
        return
    
    # Determine current team and index
    if battle.current_turn_user_id == str(battle.challenger_player.user_id):
        current_team = battle.challenger_team
        current_index = battle.challenger_current_index
    else:
        current_team = battle.defender_team
        current_index = battle.defender_current_index
    
    # Get available characters to switch to (only future team members)
    available_switches = []
    for i in range(current_index + 1, len(current_team)):
        char = current_team[i]
        available_switches.append((i, char))
    
    if not available_switches:
        try:
            await safe_api_call(query.answer, "No characters available to switch to.", show_alert=True)
        except:
            pass
        return
    
    # Build keyboard with available characters
    keyboard = []
    for index, char in available_switches:
        keyboard.append([InlineKeyboardButton(f"🔄 {char.name} (HP: {char.stats.HP})", callback_data=f"pvp_switch_{index}")])
    
    # Add back button
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="pvp_back_to_battle")])
    
    # Show switch menu
    switch_text = "🔄 <b>Available Characters to Switch:</b>\n\n"
    for index, char in available_switches:
        switch_text += f"• <b>{char.name}</b>\n"
        switch_text += f"  HP: {char.stats.HP} | ATK: {char.stats.ATK} | DEF: {char.stats.DEF}\n\n"
    
    try:
        await safe_api_call(
            query.edit_message_text,
            text=switch_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.debug(f"Error showing switches menu: {e}")

async def handle_pvp_show_items(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle showing available items in PvP."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn by comparing with current_turn_user_id
    if user_id != battle.current_turn_user_id:
        await safe_api_call(query.answer, "It's not your turn!", show_alert=True)
        return
        
    # Check if player already used an item this turn
    if (user_id == battle.challenger.user_id and battle.challenger_used_item) or \
       (user_id == battle.defender.user_id and battle.defender_used_item):
        await safe_api_call(query.answer, "You've already used an item this turn!", show_alert=True)
        return
    
    # Get DB instance and refresh player
    db = context.bot_data.get("db") or Database()
    shop_items = context.bot_data.get("shop_items") or {}
    
    try:
        # Determine current player
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):
            current_player = battle.challenger_player
        else:
            current_player = battle.defender_player
            
        player = await db.get_player(str(current_player.user_id))
        if not player:
            # Generate keyboard with just back button
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="pvp_back_to_battle")]]
            
            await safe_api_call(
                query.edit_message_text,
                "🎒 <b>Select an item to use in battle:</b>\n\n"
                "You don't have any items to use.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            return
            
        # Check for utility items in inventory
        utility_items = []
        item_list_text = ""
        
        for item_key, quantity in player.inventory.items():
            if item_key in shop_items and shop_items[item_key].type == "utility" and quantity > 0:
                item = shop_items[item_key]
                utility_items.append((item_key, item.name, quantity))
                
        # Sort items by name
        utility_items.sort(key=lambda x: x[1])
        
        # Create text list of items and their quantities
        if utility_items:
            item_list_text = "Available items:\n"
            for item_key, item_name, quantity in utility_items:
                item_list_text += f"• <b>{item_name}</b> - {quantity} available\n"
            item_list_text += "\n"
        else:
            item_list_text = "You don't have any utility items to use.\n\n"
        
        # Generate items keyboard
        keyboard = await generate_pvp_items_keyboard(battle, context)
        
        # Update message with items menu
        await safe_api_call(
            query.edit_message_text,
            f"🎒 <b>Select an item to use in battle:</b>\n\n{item_list_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error showing items menu: {e}")

async def handle_pvp_use_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle using an item in PvP."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn by comparing with current_turn_user_id
    if user_id != battle.current_turn_user_id:
        await safe_api_call(query.answer, "It's not your turn!", show_alert=True)
        return
    
    # Check if player already used an item this turn
    if (user_id == battle.challenger.user_id and battle.challenger_used_item) or \
       (user_id == battle.defender.user_id and battle.defender_used_item):
        await safe_api_call(query.answer, "You've already used an item this turn!", show_alert=True)
        return
        
    # Extract item key from callback data
    item_key = query.data.replace("pvp_use_item_", "")
    
    # Use the item
    message, effects = await battle.use_item(item_key, context)
    
    if battle.timeout_task:
        battle.timeout_task.cancel()
        battle.timeout_task = None
    
    # Switch turn after using item (item usage is considered a full turn)
    battle.switch_turn()  # Use the proper turn switching method that handles both current_turn and current_turn_user_id
    
    # Update battle display
    status = battle.get_battle_status()
    keyboard = await generate_pvp_ability_keyboard(battle, context)
    
    try:
        # Create a more clear display showing player names with their characters
        challenger_player_name = battle.challenger_player.name
        defender_player_name = battle.defender_player.name
        
        # Get first names for display
        challenger_first_name = challenger_player_name.split()[0] if challenger_player_name else "Player 1"
        defender_first_name = defender_player_name.split()[0] if defender_player_name else "Player 2"
        
        # Add the "«" symbol to indicate whose turn it is
        challenger_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.challenger_player.user_id) else ""
        defender_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.defender_player.user_id) else ""
        
        # Format battle message with player's first name instead of character name
        battle_message = message
        
        # Replace character name with player's first name in the message
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):  
            battle_message = battle_message.replace(battle.defender.name, defender_first_name)
        else:
            battle_message = battle_message.replace(battle.challenger.name, challenger_first_name)
        
        # Format effects display
        effect_text = ""
        if effects:
            effect_lines = []
            if effects.get("heal", 0) > 0:
                effect_lines.append(f"💚 Healing: {effects['heal']}")
            if effects.get("attack_boost", 0) > 0:
                effect_lines.append(f"💪 Attack +{int((effects['attack_boost']-1)*100)}%")
            if effects.get("cooldown_reduction", 0) > 0:
                effect_lines.append(f"⏱️ Cooldowns reduced by {effects['cooldown_reduction']} turn(s)")
            if effects.get("accuracy_boost", 0) > 0:
                effect_lines.append(f"🎯 Accuracy +{effects['accuracy_boost']}")
            if effects.get("defense_boost", 0) > 0:
                effect_lines.append(f"🛡️ Defense +{effects['defense_boost']}")
            if effects.get("crit_boost", 0) > 0:
                effect_lines.append(f"✨ Critical chance +{effects['crit_boost']}%")
                
            if effect_lines:
                effect_text = "\n" + "\n".join(effect_lines)
        
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"<b>⚔️ PVP BATTLE ⚔️</b>\n\n"
                f"<code>{battle_message}{effect_text}</code>\n\n"
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
        
        # Store the current message ID for timeout handling
        if query.message:
            battle.current_message_id = query.message.message_id
        else:
            battle.current_message_id = None
    except Exception as e:
        logger.error(f"Failed to update battle message after using item: {e}")
    
    # Set timeout task
    battle.timeout_task = asyncio.create_task(
        pvp_battle_timeout(battle.challenger.user_id, battle.defender.user_id, battle, context, query.message.chat_id)
    )

async def handle_pvp_back_to_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle going back to battle from items menu."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        return
        
    battle = active_pvp_battles[user_id]
    
    # Update battle display
    status = battle.get_battle_status()
    keyboard = await generate_pvp_ability_keyboard(battle, context)
    
    try:
        # Create a more clear display showing player names with their characters
        challenger_player_name = battle.challenger_player.name
        defender_player_name = battle.defender_player.name
        
        # Add the "«" symbol to indicate whose turn it is (ensure only one player has the indicator)
        challenger_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.challenger_player.user_id) else ""
        defender_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.defender_player.user_id) else ""
        
        # Ensure only one turn indicator is shown
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):
            defender_turn_indicator = ""
        else:
            challenger_turn_indicator = ""
        
        await safe_api_call(
            query.edit_message_text,
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
        
        # Store the current message ID for timeout handling
        battle.current_message_id = query.message.message_id
    except Exception as e:
        logger.error(f"Failed to update battle message: {e}")

async def check_button_cooldown(user_id: str, action: str, cooldown_secs: float = 1.0) -> bool:
    """
    Check if a user's action is on cooldown.
    Returns True if the action can proceed (not on cooldown), False otherwise.
    CRITICAL: Cleanup called to prevent unbounded growth
    """
    # CRITICAL: Cleanup before checking to prevent unbounded growth
    cleanup_pvp_cooldowns()
    
    current_time = datetime.now(timezone.utc).timestamp()
    
    # Initialize user's cooldown dict if not exists
    if user_id not in pvp_button_cooldowns:
        pvp_button_cooldowns[user_id] = {}
    
    # Check if this specific action is on cooldown
    if action in pvp_button_cooldowns[user_id]:
        last_time = pvp_button_cooldowns[user_id][action]
        if current_time - last_time < cooldown_secs:
            return False
    
    # Update the last action time
    pvp_button_cooldowns[user_id][action] = current_time
    return True

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
            # Silently ignore query expired errors - user won't see them
            if "Query is too old" in str(e) or "query id is invalid" in str(e):
                logger.debug(f"Query expired (silently ignored): {type(e).__name__}")
                return  # Exit gracefully without logging error
            logger.debug(f"Could not answer callback query: {e}")
        
        callback_data = getattr(query, 'data', None)
        if not callback_data or not isinstance(callback_data, str):
            try:
                await safe_api_call(query.answer, "Invalid callback data.", show_alert=True)
            except:
                pass  # Ignore if query expired
            return
        
        user_id = str(update.effective_user.id)
        cooldown = 0.5
        if callback_data.startswith("pvp_ability_") or callback_data == "pvp_basic_attack":
            cooldown = 0.8
        elif callback_data in ["pvp_show_items", "pvp_back_to_battle"]:
            cooldown = 0.5
        elif callback_data.startswith("pvp_use_item_"):
            cooldown = 1.0
        elif callback_data == "pvp_surrender":
            cooldown = 2.0
        if not callback_data.startswith("pvp_cooldown_") and not callback_data.startswith("pvp_lowgas_") and not callback_data == "pvp_no_items":
            if not await check_button_cooldown(user_id, callback_data, cooldown):
                return
        
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
            ability_name = callback_data[11:].strip()
            if not ability_name:
                try:
                    await safe_api_call(query.answer, "Invalid ability name.", show_alert=True)
                except:
                    pass
                return
            logger.debug(f"Received ability callback: {ability_name}")
            await handle_pvp_ability(update, context, ability_name)
        elif callback_data == "pvp_surrender":
            await handle_pvp_surrender(update, context)
        elif callback_data == "pvp_show_switches":
            await handle_pvp_show_switches(update, context)
        elif callback_data == "pvp_switch":
            try:
                await safe_api_call(query.answer, "Switching characters is not implemented yet.", show_alert=True)
            except:
                pass
        elif callback_data.startswith("pvp_switch_"):
            target_index = int(callback_data[11:])  # Extract target index from "pvp_switch_1"
            await handle_pvp_manual_switch(update, context, target_index)
        elif callback_data == "pvp_show_items":
            await handle_pvp_show_items(update, context)
        elif callback_data.startswith("pvp_use_item_"):
            await handle_pvp_use_item(update, context)
        elif callback_data == "pvp_back_to_battle":
            await handle_pvp_back_to_battle(update, context)
        elif callback_data == "pvp_no_items":
            try:
                await safe_api_call(query.answer, "You don't have any utility items to use.", show_alert=True)
            except:
                pass
        elif callback_data.startswith("pvp_cooldown_") or callback_data.startswith("pvp_lowgas_"):
            try:
                if callback_data.startswith("pvp_lowgas_"):
                    await safe_api_call(query.answer, "Not enough gas! Using an ability without sufficient gas will end the battle and you will lose.", show_alert=True)
                else:
                    await safe_api_call(query.answer, "This ability is not available right now.", show_alert=True)
            except:
                pass  # Silently ignore query expired errors
    except Exception as e:
        # Check if it's a query expired error
        if "Query is too old" in str(e) or "query id is invalid" in str(e):
            logger.debug(f"PVP callback query expired (silently handled)")
            return  # Exit gracefully without logging as error
        logger.error(f"Error in pvp_callback_handler: {e}")


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
            await safe_api_call(query.edit_message_text, f"{challenge_data['challenger_name']} is now in a titan battle!")
            del pvp_challenges[challenge_id]
            return
            
        if user_id in active_battles:
            await safe_api_call(query.edit_message_text, "You are now in a titan battle!")
            del pvp_challenges[challenge_id]
            return
    
    # Check if either player is already in an active PVP battle
    if challenger_id in active_pvp_battles:
        await safe_api_call(query.edit_message_text, f"{challenge_data['challenger_name']} is already in a PVP battle!")
        del pvp_challenges[challenge_id]
        return
        
    if user_id in active_pvp_battles:
        await safe_api_call(query.edit_message_text, "You are already in a PVP battle!")
        del pvp_challenges[challenge_id]
        return
    
    # Get full team lists for both players
    challenger_team = []
    defender_team = []
    # Get DB instance
    db = context.bot_data.get("db")
    if not db:
        await safe_api_call(query.edit_message_text, "Error: Database not available. Cannot start PVP battle.")
        if challenge_id in pvp_challenges:
            del pvp_challenges[challenge_id]
        return
    
    # Get challenger's full team
    challenger_player = challenge_data["challenger_player"]
    if hasattr(challenger_player, 'team') and challenger_player.team:
        for team_member in challenger_player.team:
            if hasattr(team_member, 'character_name'):
                char_name = team_member.character_name
            elif isinstance(team_member, dict):
                char_name = team_member.get("character_name")
            else:
                char_name = team_member
            
            try:
                # Normalize challenger_id to int when possible
                try:
                    db_challenger_id = int(challenger_id)
                except Exception:
                    db_challenger_id = challenger_id

                logger.debug(f"Loading challenger character '{char_name}' for user_id={db_challenger_id}")
                char = await db.get_character(db_challenger_id, char_name)
                if char:
                    challenger_team.append(char)
                    logger.debug(f"Loaded challenger character '{char_name}' for user_id={db_challenger_id}")
                else:
                    logger.warning(f"Challenger character not found: user_id={db_challenger_id}, name={char_name}")
            except Exception as e:
                logger.exception(f"Error getting challenger character {char_name} for user_id={challenger_id}: {e}")
    
    # Get defender's full team
    defender_player = challenge_data["defender_player"]
    if hasattr(defender_player, 'team') and defender_player.team:
        for team_member in defender_player.team:
            if hasattr(team_member, 'character_name'):
                char_name = team_member.character_name
            elif isinstance(team_member, dict):
                char_name = team_member.get("character_name")
            else:
                char_name = team_member
            
            try:
                # Normalize defender user id to int when possible
                try:
                    db_defender_id = int(user_id)
                except Exception:
                    db_defender_id = user_id

                logger.debug(f"Loading defender character '{char_name}' for user_id={db_defender_id}")
                char = await db.get_character(db_defender_id, char_name)
                if char:
                    defender_team.append(char)
                    logger.debug(f"Loaded defender character '{char_name}' for user_id={db_defender_id}")
                else:
                    logger.warning(f"Defender character not found: user_id={db_defender_id}, name={char_name}")
            except Exception as e:
                logger.exception(f"Error getting defender character {char_name} for user_id={user_id}: {e}")
    
    # Ensure at least one character per team
    if not challenger_team:
        # Get the first character as fallback
        if hasattr(challenger_player, 'team') and challenger_player.team:
            if hasattr(challenger_player.team[0], 'character_name'):
                char_name = challenger_player.team[0].character_name
            elif isinstance(challenger_player.team[0], dict):
                char_name = challenger_player.team[0].get("character_name")
            else:
                char_name = challenger_player.team[0]
            try:
                try:
                    db_challenger_id = int(challenger_id)
                except Exception:
                    db_challenger_id = challenger_id

                logger.debug(f"Loading fallback challenger character '{char_name}' for user_id={db_challenger_id}")
                char = await db.get_character(db_challenger_id, char_name)
                if char:
                    challenger_team = [char]
                    logger.debug(f"Loaded fallback challenger character '{char_name}' for user_id={db_challenger_id}")
                else:
                    logger.warning(f"Fallback challenger character not found: user_id={db_challenger_id}, name={char_name}")
            except Exception as e:
                logger.exception(f"Error getting fallback challenger character {char_name} for user_id={challenger_id}: {e}")
    
    if not defender_team:
        # Get the first character as fallback
        if hasattr(defender_player, 'team') and defender_player.team:
            if hasattr(defender_player.team[0], 'character_name'):
                char_name = defender_player.team[0].character_name
            elif isinstance(defender_player.team[0], dict):
                char_name = defender_player.team[0].get("character_name")
            else:
                char_name = defender_player.team[0]
            try:
                try:
                    db_defender_id = int(user_id)
                except Exception:
                    db_defender_id = user_id

                logger.debug(f"Loading fallback defender character '{char_name}' for user_id={db_defender_id}")
                char = await db.get_character(db_defender_id, char_name)
                if char:
                    defender_team = [char]
                    logger.debug(f"Loaded fallback defender character '{char_name}' for user_id={db_defender_id}")
                else:
                    logger.warning(f"Fallback defender character not found: user_id={db_defender_id}, name={char_name}")
            except Exception as e:
                logger.exception(f"Error getting fallback defender character {char_name} for user_id={user_id}: {e}")
    
    # If still no teams, we can't proceed
    if not challenger_team or not defender_team:
        await safe_api_call(query.edit_message_text, "Error: Could not load team data for battle.")
        del pvp_challenges[challenge_id]
        return
    
    # Create PvP battle instance with full teams
    battle = PvPBattleSystem(
        challenger_team=challenger_team,
        defender_team=defender_team,
        challenger_player=challenge_data["challenger_player"],
        defender_player=challenge_data["defender_player"],
        challenge_id=challenge_id
    )
    
    # Ensure shop_items are available in context
    if "shop_items" not in context.bot_data and hasattr(context, 'bot_data'):
        from game.shop_system import shop_system
        context.bot_data["shop_items"] = shop_system.shop_items
    
    # Store battle in active PvP battles
    active_pvp_battles[challenger_id] = battle
    active_pvp_battles[user_id] = battle
    
    # Clean up challenge
    del pvp_challenges[challenge_id]
    
    try:
        # Start battle
        await safe_api_call(
            query.edit_message_text,
            "!! BATTLE BEGINS !!",
            reply_markup=None
        )
        
        # Send battle display
        status = battle.get_battle_status()
        keyboard = await generate_pvp_ability_keyboard(battle, context)
        
        # Get player names for display
        challenger_player_name = battle.challenger_player.name
        defender_player_name = battle.defender_player.name
        
        # Add the "«" symbol to indicate whose turn it is (only one at a time)
        challenger_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.challenger_player.user_id) else ""
        defender_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.defender_player.user_id) else ""
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):
            defender_turn_indicator = ""
        else:
            challenger_turn_indicator = ""
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
        
        # Store the current message ID for timeout handling
        # Since we sent a reply, we need to get the message_id from the sent message
        # For now, we'll use the original message ID as a fallback
        battle.current_message_id = query.message.message_id
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
        await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn by comparing with current_turn_user_id
    if user_id != battle.current_turn_user_id:
        # Apply a small penalty for trying to act out of turn - reduce gas slightly
        if user_id == str(battle.challenger.user_id):
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
    
    # Check if battle has ended due to insufficient gas or other reasons
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
        
        # Get first names for display
        challenger_first_name = challenger_player_name.split()[0] if challenger_player_name else "Player 1"
        defender_first_name = defender_player_name.split()[0] if defender_player_name else "Player 2"
        
        # Add the "«" symbol to indicate whose turn it is (ensure only one player has the indicator)
        challenger_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.challenger_player.user_id) else ""
        defender_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.defender_player.user_id) else ""
        
        # Ensure only one turn indicator is shown
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):
            defender_turn_indicator = ""
        else:
            challenger_turn_indicator = ""
            
        # Determine which player used the ability
        player_first_name = ""
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):
            player_first_name = defender_first_name  
        else:
            player_first_name = challenger_first_name  
        
        # Format battle message with effects on separate lines
        battle_message = message
        
        # Replace character name with player's first name in the message
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):  # Turn just switched, so look at the opposite
            battle_message = battle_message.replace(battle.defender.name, defender_first_name)
        else:
            battle_message = battle_message.replace(battle.challenger.name, challenger_first_name)
        
        # Format effects display
        effect_text = ""
        if effects:
            effect_lines = []
            if effects.get("damage", 0) > 0:
                effect_lines.append(f"⚔️ Damage: {effects['damage']}")
            if effects.get("heal", 0) > 0:
                effect_lines.append(f"💚 Healing: {effects['heal']}")
            if effects.get("shield", 0) > 0:
                effect_lines.append(f"🛡️ Shield: {effects['shield']}")
            if effects.get("stun", 0) > 0:
                effect_lines.append(f"⚡ Stun: {effects['stun']} turns")
            if effects.get("bleed"):
                effect_lines.append(f"🩸 Bleeding applied")
            if effects.get("attack_boost", 0) > 0:
                effect_lines.append(f"💪 Attack +{int((effects['attack_boost']-1)*100)}%")
            if effects.get("accuracy_boost", 0) > 0:
                effect_lines.append(f"🎯 Accuracy +{effects['accuracy_boost']}")
            if effects.get("defense_boost", 0) > 0:
                effect_lines.append(f"🛡️ Defense +{effects['defense_boost']}")
            if effects.get("crit_boost", 0) > 0:
                effect_lines.append(f"✨ Critical chance +{effects['crit_boost']}%")
            if effects.get("cooldown_reduction", 0) > 0:
                effect_lines.append(f"⏱️ Cooldowns -${effects['cooldown_reduction']} turn(s)")
                
            if effect_lines:
                effect_text = "\n" + "\n".join(effect_lines)
        
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"<b>⚔️ PVP BATTLE ⚔️</b>\n\n"
                f"<code>{battle_message}{effect_text}</code>\n\n"
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
        
        # Store the current message ID for timeout handling
        battle.current_message_id = query.message.message_id
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
        await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn by comparing with current_turn_user_id
    if user_id != battle.current_turn_user_id:
        # Apply a small penalty for trying to act out of turn - reduce gas slightly
        if user_id == str(battle.challenger.user_id):
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
    
    # Refresh character data for weapon information
    db = context.bot_data.get("db") or Database()
    if battle.current_turn_user_id == str(battle.challenger_player.user_id):
        try:
            refreshed_character = await db.get_character(int(battle.challenger.user_id), battle.challenger.name)
            if refreshed_character:
                battle.challenger = refreshed_character
        except Exception as e:
            logger.error(f"Error refreshing challenger character: {e}")
    else:
        try:
            refreshed_character = await db.get_character(int(battle.defender.user_id), battle.defender.name)
            if refreshed_character:
                battle.defender = refreshed_character
        except Exception as e:
            logger.error(f"Error refreshing defender character: {e}")
    
    # Check for stun debuffs before allowing the attack
    if battle.current_turn_user_id == str(battle.challenger_player.user_id) and battle.challenger_debuffs.get("stun", 0) > 0:
        await safe_api_call(query.answer, f"{battle.challenger.name} is stunned and cannot act this turn!", show_alert=True)
        return
    if battle.current_turn_user_id == str(battle.defender_player.user_id) and battle.defender_debuffs.get("stun", 0) > 0:
        await safe_api_call(query.answer, f"{battle.defender.name} is stunned and cannot act this turn!", show_alert=True)
        return
        
    # Use basic attack with refreshed character data
    message, effects = await battle.use_basic_attack(context)
    
    # Check if battle has ended due to insufficient gas or other reasons
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
        
        # Get first names for display
        challenger_first_name = challenger_player_name.split()[0] if challenger_player_name else "Player 1"
        defender_first_name = defender_player_name.split()[0] if defender_player_name else "Player 2"
        
        # Add the "«" symbol to indicate whose turn it is
        challenger_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.challenger_player.user_id) else ""
        defender_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.defender_player.user_id) else ""
        
        # Format battle message with player's first name instead of character name
        battle_message = message
        
        # Replace character name with player's first name in the message
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):  # Turn just switched, so look at the opposite
            battle_message = battle_message.replace(battle.defender.name, defender_first_name)
        else:
            battle_message = battle_message.replace(battle.challenger.name, challenger_first_name)
        
        # Format effects display
        effect_text = ""
        if effects:
            effect_lines = []
            if effects.get("damage", 0) > 0:
                effect_lines.append(f"⚔️ Damage: {effects['damage']}")
            if effects.get("critical", False):
                effect_lines.append(f"✨ Critical hit!")
                
            if effect_lines:
                effect_text = "\n" + "\n".join(effect_lines)
        
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"<b>⚔️ PVP BATTLE ⚔️</b>\n\n"
                f"<code>{battle_message}{effect_text}</code>\n\n"
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
        
        # Store the current message ID for timeout handling
        battle.current_message_id = query.message.message_id
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
        if user_id != battle.current_turn_user_id:
            # Apply a small penalty for trying to act out of turn - reduce gas slightly
            if user_id == str(battle.challenger.user_id):
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
    
    # Create custom surrender message
    if user_id == battle.challenger.user_id:
        surrendering_player_name = battle.challenger_player.name
        surrendering_char_name = battle.challenger.name
        winner_player_name = battle.defender_player.name
        winner_char_name = battle.defender.name
    else:
        surrendering_player_name = battle.defender_player.name
        surrendering_char_name = battle.defender.name
        winner_player_name = battle.challenger_player.name
        winner_char_name = battle.challenger.name
    
    # Enhanced surrender message with emojis and formatting
    surrender_message = (
        f"<b>🏳️ SURRENDER 🏳️</b>\n\n"
        f"{surrendering_player_name}'s <b>{surrendering_char_name}</b> has waved the white flag!\n"
        f"{winner_player_name}'s <b>{winner_char_name}</b> claims victory without further combat!"
    )
    
    # Handle battle end with the custom surrender message
    try:
        await handle_pvp_battle_end(update, context, battle, surrender_message)
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
        await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn
    if (user_id == str(battle.challenger.user_id) and battle.current_turn_user_id != str(battle.challenger_player.user_id)) or \
       (user_id == str(battle.defender.user_id) and battle.current_turn_user_id != str(battle.defender_player.user_id)):
        await safe_api_call(query.answer, "It's not your turn!", show_alert=True)
        return
    
    # Handle switch (placeholder for future implementation)
    message = battle.switch_character()
    await safe_api_call(query.answer, message, show_alert=True)


async def handle_pvp_manual_switch(update: Update, context: ContextTypes.DEFAULT_TYPE, target_index: int) -> None:
    """Handle manual character switch in PVP."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
        
    user_id = str(update.effective_user.id)
    
    # Check if user is in a PVP battle
    if user_id not in active_pvp_battles:
        await safe_api_call(query.answer, "You are not in an active PVP battle.", show_alert=True)
        return
        
    battle = active_pvp_battles[user_id]
    
    # Check if it's user's turn
    if user_id != battle.current_turn_user_id:
        await safe_api_call(query.answer, "It's not your turn!", show_alert=True)
        return
    
    if battle.timeout_task:
        battle.timeout_task.cancel()
        battle.timeout_task = None
    
    # Perform the manual switch
    message = battle.manual_switch_character(target_index)
    
    if battle.battle_ended:
        await handle_pvp_battle_end(update, context, battle, message)
        return
    
    # Switch turn after manual switch (switching counts as a full turn)
    battle.switch_turn()
    
    # Update battle display
    status = battle.get_battle_status()
    keyboard = await generate_pvp_ability_keyboard(battle, context)
    
    try:
        # Create a more clear display showing player names with their characters
        challenger_player_name = battle.challenger_player.name
        defender_player_name = battle.defender_player.name
        
        # Get first names for display
        challenger_first_name = challenger_player_name.split()[0] if challenger_player_name else "Player 1"
        defender_first_name = defender_player_name.split()[0] if defender_player_name else "Player 2"
        
        # Add the "«" symbol to indicate whose turn it is
        challenger_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.challenger_player.user_id) else ""
        defender_turn_indicator = " « Turn" if battle.current_turn_user_id == str(battle.defender_player.user_id) else ""
        
        # Format battle message with player's first name instead of character name
        battle_message = message
        
        # Replace character name with player's first name in the message
        if battle.current_turn_user_id == str(battle.challenger_player.user_id):
            battle_message = battle_message.replace(battle.defender.name, defender_first_name)
        else:
            battle_message = battle_message.replace(battle.challenger.name, challenger_first_name)
        
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"<b>⚔️ PVP BATTLE ⚔️</b>\n\n"
                f"<code>{battle_message}</code>\n\n"
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
        
        # Store the current message ID for timeout handling
        battle.current_message_id = query.message.message_id
    except Exception as e:
        logger.error(f"Failed to update battle message after manual switch: {e}")
    
    # Set timeout task
    battle.timeout_task = asyncio.create_task(
        pvp_battle_timeout(battle.challenger.user_id, battle.defender.user_id, battle, context, query.message.chat_id)
    )


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
        # Get player IDs and names for proper hyperlinks
        challenger_id = battle.challenger.user_id
        defender_id = battle.defender.user_id
        challenger_mention = f'<a href="tg://user?id={challenger_id}">{battle.challenger_player.name}</a>'
        defender_mention = f'<a href="tg://user?id={defender_id}">{battle.defender_player.name}</a>'
        
        # Determine winner and loser mentions
        if battle.winner == battle.challenger.name:
            winner_mention = challenger_mention
            loser_mention = defender_mention
            winner_char_name = battle.challenger.name
            loser_char_name = battle.defender.name
        else:
            winner_mention = defender_mention
            loser_mention = challenger_mention
            winner_char_name = battle.defender.name
            loser_char_name = battle.challenger.name
        
        # Check if message contains surrender indicators - if so, we'll use the custom surrender message
        is_surrender = "surrender" in message.lower() or "🏳️" in message
        
        # Format the victory message with hyperlinks
        if is_surrender:
            # Replace player names with hyperlinked mentions in the surrender message
            victory_message = message.replace(battle.challenger_player.name, challenger_mention).replace(battle.defender_player.name, defender_mention)
        elif battle.winner:  # If there is a winner (not a draw)
            victory_message = f"🏆 {winner_mention}'s {winner_char_name} defeated {loser_mention}'s {loser_char_name}!"
        else:
            victory_message = f"🔄 The battle between {challenger_mention}'s {battle.challenger.name} and {defender_mention}'s {battle.defender.name} ended in a draw!"
        
        # Create the appropriate header based on whether it's a surrender or regular battle end
        header = "<b>🏳️ PVP BATTLE SURRENDER 🏳️</b>" if is_surrender else "<b>⚔️ PVP BATTLE ENDED ⚔️</b>"
        
        # Format the battle outcome message according to the requested format
        if is_surrender:
            # For surrender, maintain the custom surrender message
            battle_outcome = victory_message
        else:
            # New format with blockquote for player names and all text in bold
            if battle.winner:
                # Extract first names for blockquote
                winner_first_name = battle.challenger_player.name.split()[0] if battle.winner == battle.challenger.name else battle.defender_player.name.split()[0]
                loser_first_name = battle.defender_player.name.split()[0] if battle.winner == battle.challenger.name else battle.challenger_player.name.split()[0]
                
                battle_outcome = (
                    f"<b>🏆 {winner_mention} <i>defeated</i> {loser_mention} !</b>\n\n"
                    f"<blockquote><b>{winner_first_name}</b></blockquote>\n"
                    f"<b>Gain: {rewards['winner']['xp']} XP, {rewards['winner']['marks']} Marks</b>\n\n"
                    f"<blockquote><b>{loser_first_name}</b></blockquote>\n"
                    f"<b>Gain: {rewards['loser']['xp']} XP, {rewards['loser']['marks']} Marks</b>"
                )
            else:
                # Handle draws (though this may be rare)
                battle_outcome = (
                    f"<b>🔄 The battle between {challenger_mention} and {defender_mention} ended in a draw!</b>\n\n"
                    f"<b>Both players receive:</b>\n"
                    f"<b>XP: {rewards['winner']['xp']}, Marks: {rewards['winner']['marks']}</b>"
                )
        
        await safe_api_call(
            query.edit_message_text,
            text=(
                f"{header}\n\n"
                f"{battle_outcome}"
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
    # Check if challenger is already a Character object
    if isinstance(battle.challenger, Character):
        challenger_data = battle.challenger.dict()
    else:
        # Handle case where it might be a dictionary or another object
        challenger_data = battle.challenger.dict() if hasattr(battle.challenger, 'dict') else battle.challenger
        
    challenger_data['current_hp'] = battle.challenger_hp  # Replace the existing current_hp
    # Safely get gas value with fallback
    challenger_gas = getattr(battle, 'challenger_gas', battle.challenger.gas if hasattr(battle.challenger, 'gas') else 0)
    challenger_data['gas'] = challenger_gas
    
    # Create Character object only if not already a Character
    character_obj = battle.challenger if isinstance(battle.challenger, Character) else Character(**challenger_data)
    character_obj.current_hp = battle.challenger_hp
    character_obj.gas = challenger_gas
    await db.update_character(character_obj)
    
    # Do the same for the defender
    # Check if defender is already a Character object
    if isinstance(battle.defender, Character):
        defender_data = battle.defender.dict()
    else:
        # Handle case where it might be a dictionary or another object
        defender_data = battle.defender.dict() if hasattr(battle.defender, 'dict') else battle.defender
        
    defender_data['current_hp'] = battle.defender_hp
    # Safely get gas value with fallback
    defender_gas = getattr(battle, 'defender_gas', battle.defender.gas if hasattr(battle.defender, 'gas') else 0)
    defender_data['gas'] = defender_gas
    
    # Create Character object only if not already a Character
    character_obj = battle.defender if isinstance(battle.defender, Character) else Character(**defender_data)
    character_obj.current_hp = battle.defender_hp
    character_obj.gas = defender_gas
    await db.update_character(character_obj)
    
    # Clean up battle data
    if challenger_id in active_pvp_battles:
        del active_pvp_battles[challenger_id]
    if defender_id in active_pvp_battles:
        del active_pvp_battles[defender_id]
        
    # Clear all item effects before disposing
    battle.challenger_active_items.clear()
    battle.defender_active_items.clear()
    
    # Dispose battle resources
    battle.dispose()
    
    try:
        # Make sure battle.winner is not None before passing to track_battle_end
        if battle.winner and winner_id:
            track_battle_end(int(winner_id), battle.winner, "pvp_victory")
            
            # Process mission progress for PvP battles
            winner_player = await db.get_player(winner_id)
            if winner_player and hasattr(winner_player, "missions"):
                # Check and update mission progress for PvP victories
                mission_notifications = await process_pvp_mission_progress(db, winner_player, won=True, opponent_id=str(loser_id))
                
                # Send mission notifications if any
                if mission_notifications:
                    for notification in mission_notifications[:1]:  # Limit to first notification
                        # Send mission notification privately to the winner instead of in PVP chat
                        try:
                            await context.bot.send_message(
                                chat_id=int(winner_id),
                                text=notification,
                                parse_mode=ParseMode.MARKDOWN
                            )
                        except Exception as e:
                            logger.error(f"Failed to send private mission notification to winner: {e}")
    except Exception as e:
        logger.error(f"Error in track_battle_end or mission processing: {e}")
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
            if battle.current_turn_user_id == str(battle.challenger_player.user_id):
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
            
            # Calculate rewards (set to 0 for timeout)
            rewards = {
                "winner": {"xp": 0, "marks": 0, "valor": 0},
                "loser": {"xp": 0, "marks": 0, "valor": 0}
            }
            
            # Edit existing battle message with timeout info
            try:
                # First, try to delete the existing battle message if we have its ID
                if hasattr(battle, 'current_message_id') and battle.current_message_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=chat_id,
                            message_id=battle.current_message_id
                        )
                    except Exception as e:
                        logger.debug(f"Could not delete existing battle message: {e}")
                
                # Get player mentions for proper hyperlinks
                challenger_mention = f'<a href="tg://user?id={challenger_id}">{battle.challenger_player.name}</a>'
                defender_mention = f'<a href="tg://user?id={defender_id}">{battle.defender_player.name}</a>'
                
                # Determine winner and loser mentions
                if battle.winner == battle.challenger.name:
                    winner_mention = challenger_mention
                    loser_mention = defender_mention
                    winner_char_name = battle.challenger.name
                    loser_char_name = battle.defender.name
                else:
                    winner_mention = defender_mention
                    loser_mention = challenger_mention
                    winner_char_name = battle.defender.name
                    loser_char_name = battle.challenger.name
                
                # Extract first names for blockquote
                winner_first_name = battle.challenger_player.name.split()[0] if battle.winner == battle.challenger.name else battle.defender_player.name.split()[0]
                loser_first_name = battle.defender_player.name.split()[0] if battle.winner == battle.challenger.name else battle.challenger_player.name.split()[0]
                
                # Format timeout message
                timeout_header = f"<b>⏰ PVP BATTLE TIMED OUT ⏰</b>"
                timeout_message = f"<b>🏆 {winner_mention} <i>defeated</i> {loser_mention} by timeout!</b>"
                
                # Format the battle outcome with blockquotes and bold text
                battle_outcome = (
                    f"{timeout_message}\n"
                )
                
                # Send the timeout message as a new message
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{timeout_header}\n\n"
                        f"{battle_outcome}"
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
                
            # Update winner statistics (no win count for timeout)
            await db.players.update_one(
                {"user_id": winner_id},
                {
                    "$inc": {
                        "xp": rewards["winner"]["xp"],
                        "marks": rewards["winner"]["marks"],
                        "total_xp": rewards["winner"]["xp"]
                    }
                }
            )
                
            # Update loser statistics (no loss count for timeout)
            await db.players.update_one(
                {"user_id": loser_id},
                {
                    "$inc": {
                        "xp": rewards["loser"]["xp"],
                        "marks": rewards["loser"]["marks"],
                        "total_xp": rewards["loser"]["xp"]
                    }
                }
            )
            
            # Update characters with final HP and gas - avoid duplicate parameters
            try:
                # Check if challenger is already a Character object
                if isinstance(battle.challenger, Character):
                    challenger_data = battle.challenger.dict()
                else:
                    # Handle case where it might be a dictionary or another object
                    challenger_data = battle.challenger.dict() if hasattr(battle.challenger, 'dict') else battle.challenger
                    
                challenger_data['current_hp'] = battle.challenger_hp
                # Safely get gas value with fallback
                challenger_gas = getattr(battle, 'challenger_gas', battle.challenger.gas if hasattr(battle.challenger, 'gas') else 0)
                challenger_data['gas'] = challenger_gas
                
                # Create Character object only if not already a Character
                character_obj = battle.challenger if isinstance(battle.challenger, Character) else Character(**challenger_data)
                character_obj.current_hp = battle.challenger_hp
                character_obj.gas = challenger_gas
                await db.update_character(character_obj)

                # Check if defender is already a Character object
                if isinstance(battle.defender, Character):
                    defender_data = battle.defender.dict()
                else:
                    # Handle case where it might be a dictionary or another object
                    defender_data = battle.defender.dict() if hasattr(battle.defender, 'dict') else battle.defender
                    
                defender_data['current_hp'] = battle.defender_hp
                # Safely get gas value with fallback
                defender_gas = getattr(battle, 'defender_gas', battle.defender.gas if hasattr(battle.defender, 'gas') else 0)
                defender_data['gas'] = defender_gas
                
                # Create Character object only if not already a Character
                character_obj = battle.defender if isinstance(battle.defender, Character) else Character(**defender_data)
                character_obj.current_hp = battle.defender_hp
                character_obj.gas = defender_gas
                await db.update_character(character_obj)
            except Exception as e:
                logger.error(f"Error updating character data in timeout: {e}")
                # Continue with cleanup even if character update fails         
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
