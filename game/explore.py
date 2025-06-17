from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.db_instance import get_database
from database.characters import get_character_data
from database.db import Database
import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Tuple
import random
from telegram.constants import ParseMode
from database.models import Character, Player
from database.characters import get_character_data, AbilityEffect, CharacterData, Ability
import logging
import uuid
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class BattleSystem:
    def __init__(self, character: 'Character', titan: 'Titan'):
        self.character = character
        self.titan = titan
        self.character_hp = character.current_hp
        self.titan_hp = titan.max_hp
        self.gas = character.gas
        self.character_gas = character.gas  # Max gas
        self.ability_cooldowns = {ability.name: 0 for ability in character.active_abilities + character.passive_abilities}
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
        self.timeout_task = None  # Track timeout task

    def build_context(self, trigger: Optional[str] = None, ability: Optional[Ability] = None) -> Dict:
        """Build standardized battle context for ability effect functions."""
        return {
            "character_stats": self.character.stats.dict(),
            "character_hp": self.character_hp,
            "character_max_hp": self.character.stats.HP,
            "titan_hp": self.titan_hp,
            "titan_size": random.randint(5, 15),
            "is_intelligent_titan": random.random() < 0.1,
            "is_leader": random.random() < 0.05,
            "first_damage_taken": self.trigger_states["first_damage_taken"],
            "dodge_count": self.trigger_states["dodge_count"],
            "fear_counter": self.trigger_states["fear_counter"],
            "focused_turns": self.trigger_states["focused_turns"],
            "ally_died": self.trigger_states["ally_died"],
            "turn": self.turn,
            "gas": self.gas,
            "character_gas": self.character_gas,
            "base_damage": ability.base_damage + self.character.stats.ATK if ability else 0,
            "character_level": self.character.level,
            "target_is_self": False,
            "titan_difficulty": self.titan.difficulty,
            "titan_special_abilities": self.titan.special_abilities or []
        }

    def apply_passives(self, trigger: str) -> List[str]:
        """Apply passive abilities for a given trigger, returning messages."""
        character_data = get_character_data(self.character.character_type)
        messages = []
        for ability_name, ability in character_data.abilities.get("passive", {}).items():
            if (self.character.unlocked_abilities.get(ability_name, False) or ability.is_unlocked) and ability.effect_function:
                context = self.build_context(trigger, ability)
                effect = ability.effect_function(context)
                self.apply_effect(effect)
                if effect.message:
                    messages.append(effect.message)
        return messages

    def apply_effect(self, effect: AbilityEffect) -> None:
        """Apply an AbilityEffect to the battle state."""
        self.titan_hp = max(0, self.titan_hp - effect.damage)
        self.character_hp = min(self.character.stats.HP, self.character_hp + effect.healed)
        if effect.shield:
            self.buffs["shield"] = self.buffs.get("shield", 0) + effect.shield
        if effect.stun_duration:
            self.titan_debuffs["stun"] = max(self.titan_debuffs.get("stun", 0), effect.stun_duration)
        self.buffs.update(effect.buffs)
        self.titan_debuffs.update(effect.debuffs)
        if effect.clear_debuffs:
            self.debuffs.clear()
        if effect.items_dropped:
            self.buffs["items_dropped"] = self.buffs.get("items_dropped", []) + effect.items_dropped
        if effect.target_switched:
            self.titan_debuffs["target_switched"] = 1
        if effect.bleed_applied:
            self.titan_debuffs["bleed"] = self.titan_debuffs.get("bleed", 0) + 1

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
            
        base_damage = max(10, self.titan.level * 1)
        # Adjust damage based on difficulty
        damage_multipliers = {"Easy": 0.5, "Normal": 1.0, "Hard": 1.3}
        base_damage = int(base_damage * damage_multipliers.get(self.titan.difficulty, 1.0))
        
        # Apply special ability effects
        special_messages = []
        if self.titan.special_abilities:
            for ability in self.titan.special_abilities:
                if ability == "Armor Plating" and self.titan.difficulty == "Hard":
                    base_damage = int(base_damage * 0.9)  # Reduce incoming damage
                    special_messages.append(f"{self.titan.name}'s Armor Plating reduces damage!")
                elif ability == "Thunder Spear" and self.titan.difficulty == "Hard":
                    base_damage = int(base_damage * 1.2)  # Increase damage
                    special_messages.append(f"{self.titan.name} unleashes a Thunder Spear!")
                elif ability == "Regeneration" and self.titan.difficulty == "Normal":
                    heal = int(self.titan.max_hp * 0.05)
                    self.titan_hp = min(self.titan.max_hp, self.titan_hp + heal)
                    special_messages.append(f"{self.titan.name} regenerates {heal} HP!")
        
        damage = int(base_damage * (1 - self.character.stats.DEF / 200))
        
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

    def use_ability(self, ability_name: str, target_is_self: bool = False) -> Tuple[int, str, Dict]:
        """Use a character ability, returning damage, message, and effects."""
        character_data = get_character_data(self.character.character_type)
        effects = {
            "healed": 0, 
            "shield": 0, 
            "stun_duration": 0, 
            "items_dropped": [], 
            "target_switched": False, 
            "bleed_applied": False
        }
        
        logger.debug(f"Attempting to use ability {ability_name} for {self.character.name}")
        
        for ability_type in character_data.abilities:
            if ability_name in character_data.abilities[ability_type]:
                ability = character_data.abilities[ability_type][ability_name]
                
                if not (self.character.unlocked_abilities.get(ability_name, False) or ability.is_unlocked):
                    logger.warning(f"Ability {ability_name} is locked for {self.character.name}")
                    return 0, f"Ability {ability_name} is locked.", effects
                if ability.disabled_against_titans:
                    logger.warning(f"Ability {ability_name} is disabled against titans")
                    return 0, f"{ability_name} cannot be used against titans.", effects
                if self.ability_cooldowns.get(ability_name, 0) > 0:
                    logger.info(f"Ability {ability_name} on cooldown: {self.ability_cooldowns[ability_name]} turns")
                    return 0, f"{ability_name} is on cooldown for {self.ability_cooldowns[ability_name]} turns.", effects
                if self.gas < ability.gas_cost:
                    logger.warning(f"Insufficient gas for {ability_name}: {self.gas}/{ability.gas_cost}")
                    return 0, f"Not enough gas to use {ability_name} (requires {ability.gas_cost}).", effects
                
                logger.info(f"Using ability {ability_name} with gas cost {ability.gas_cost}")
                self.gas -= ability.gas_cost
                self.ability_cooldowns[ability_name] = ability.cooldown or 1
                context = self.build_context("ability_use", ability)
                context["target_is_self"] = target_is_self
                effect = ability.effect_function(context) if ability.effect_function else AbilityEffect()
                
                self.apply_effect(effect)
                effects.update({
                    "healed": effect.healed,
                    "shield": effect.shield,
                    "stun_duration": effect.stun_duration,
                    "items_dropped": effect.items_dropped,
                    "target_switched": effect.target_switched,
                    "bleed_applied": effect.bleed_applied
                })
                
                return effect.damage, effect.message or f"{self.character.name} used {ability_name}!", effects
        
        logger.error(f"Ability {ability_name} not found for {self.character.character_type}")
        return 0, f"Ability {ability_name} not found.", effects

    def has_usable_abilities(self) -> bool:
        """Check if the character has any usable abilities based on gas and cooldowns."""
        character_data = get_character_data(self.character.character_type)
        for ability_type in ["active", "ultimate"]:
            for ability_name, ability in character_data.abilities.get(ability_type, {}).items():
                if (
                    (self.character.unlocked_abilities.get(ability_name, False) or ability.is_unlocked) and
                    not ability.disabled_against_titans and
                    self.ability_cooldowns.get(ability_name, 0) == 0 and
                    self.gas >= ability.gas_cost
                ):
                    return True
        return False

    def update_cooldowns(self) -> None:
        """Decrease ability cooldowns and temporary effects."""
        for ability_name in list(self.ability_cooldowns.keys()):
            if self.ability_cooldowns[ability_name] > 0:
                self.ability_cooldowns[ability_name] -= 1
                
        for debuff in list(self.titan_debuffs.keys()):
            if isinstance(self.titan_debuffs[debuff], (int, float)):
                self.titan_debuffs[debuff] = max(0, self.titan_debuffs[debuff] - 1)
                if self.titan_debuffs[debuff] <= 0:
                    del self.titan_debuffs[debuff]
                    
        for buff in list(self.buffs.keys()):
            if isinstance(self.buffs[buff], (int, float)) and buff not in ["shield", "items_dropped"]:
                self.buffs[buff] = max(0, self.buffs[buff] - 1)
                if self.buffs[buff] <= 0:
                    del self.buffs[buff]

    def get_battle_status(self) -> Dict:
        """Return current battle state."""
        character_hp_percent = self.character_hp / self.character.stats.HP
        titan_hp_percent = self.titan_hp / self.titan.max_hp
        character_bar = "█" * int(character_hp_percent * 10) + "▒" * (10 - int(character_hp_percent * 10))
        titan_bar = "█" * int(titan_hp_percent * 10) + "▒" * (10 - int(titan_hp_percent * 10))
        
        status_message = f"Turn: {self.turn + 1}\n"
        status_message += f"Difficulty: {self.titan.difficulty}\n"
        if self.titan.special_abilities:
            status_message += f"Special Abilities: {', '.join(self.titan.special_abilities)}\n"
        
        if self.titan_debuffs:
            status_message += f"Titan debuffs: {', '.join([f'{k}({v})' for k, v in self.titan_debuffs.items()])}\n"
            
        if self.buffs:
            buffs_display = []
            for k, v in self.buffs.items():
                if k == "items_dropped":
                    continue
                buffs_display.append(f"{k}({v})")
            if buffs_display:
                status_message += f"Buffs: {', '.join(buffs_display)}\n"
                
        if self.buffs.get("items_dropped"):
            status_message += f"Items dropped: {', '.join(self.buffs['items_dropped'])}\n"
            
        return {
            "character_hp": self.character_hp,
            "titan_hp": self.titan_hp,
            "gas": self.gas,
            "character_bar": character_bar,
            "titan_bar": titan_bar,
            "status_message": status_message
        }

    def calculate_rewards(self) -> dict:
        """Calculate rewards for defeating the titan."""
        reward_multipliers = {"Easy": 0.8, "Normal": 1.0, "Hard": 1.3}
        base_rewards = {
            "xp": self.titan.xp_reward,
            "marks": max(1, self.titan.level * 2),
            "titan_crystals": max(1, self.titan.level // 2),
            "valor_points": max(1, self.titan.level)
        }
        scaled_rewards = {k: int(v * reward_multipliers.get(self.titan.difficulty, 1.0)) for k, v in base_rewards.items()}
        return scaled_rewards

active_battles = {}

def generate_ability_keyboard(battle: BattleSystem) -> List[List[InlineKeyboardButton]]:
    """Generate keyboard buttons for valid abilities."""
    keyboard = []
    character_data = get_character_data(battle.character.character_type)
    
    for ability_name, ability in character_data.abilities.get("active", {}).items():
        is_unlocked = (
            battle.character.unlocked_abilities.get(ability_name, False) or 
            ability.is_unlocked or
            (hasattr(ability, 'level_required') and ability.level_required <= battle.character.level)
        )
        
        if (
            is_unlocked and
            not ability.disabled_against_titans and
            battle.ability_cooldowns.get(ability_name, 0) == 0 and
            battle.gas >= ability.gas_cost
        ):
            keyboard.append([InlineKeyboardButton(
                f"{ability.name} ({ability.gas_cost} gas)",
                callback_data=f"ability_{ability_name}"
            )])
    
    for ability_name, ability in character_data.abilities.get("ultimate", {}).items():
        is_unlocked = (
            battle.character.unlocked_abilities.get(ability_name, False) or 
            ability.is_unlocked or
            (hasattr(ability, 'level_required') and ability.level_required <= battle.character.level)
        )
        
        if (
            is_unlocked and
            not ability.disabled_against_titans and
            battle.ability_cooldowns.get(ability_name, 0) == 0 and
            battle.gas >= ability.gas_cost
        ):
            keyboard.append([InlineKeyboardButton(
                f"✨ {ability.name} ({ability.gas_cost} gas) ✨",
                callback_data=f"ability_{ability_name}"
            )])
    
    for ability_name, ability in character_data.abilities.get("passive", {}).items():
        is_unlocked = (
            battle.character.unlocked_abilities.get(ability_name, False) or 
            ability.is_unlocked or
            (hasattr(ability, 'level_required') and ability.level_required <= battle.character.level)
        )
        
        if is_unlocked:
            keyboard.append([InlineKeyboardButton(
                f"✦ {ability.name}",
                callback_data=f"ability_{ability_name}"
            )])
    
    keyboard.append([InlineKeyboardButton("🏃 Run", callback_data="action_run")])
    
    return keyboard

async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /explore command to find titans."""
    user_id = update.effective_user.id
    db = await get_database()
    
    player_data = await db.players.find_one({"user_id": user_id})
    if not player_data:
        await update.message.reply_text("You haven't created a player account yet! Use /start to begin.")
        return

    player = Player(**player_data)
    if not player.team or len(player.team) == 0:
        await update.message.reply_text("You need to have at least one character in your team. Use /team to manage your team.")
        return

    # Check if user is already in an active battle
    if user_id in active_battles:
        await update.message.reply_text("⚔️ You're already in an active battle! Finish it before exploring again.")
        return

    team_sorted = sorted(player.team, key=lambda x: x.position)
    character_name = team_sorted[0].character_name
    character = await db.get_character(user_id, character_name)

    if not character:
        await update.message.reply_text(f"Error: Your character {character_name} was not found.")
        return

    if character.gas < 100:
        await update.message.reply_text(f"{character_name} doesn't have enough gas to explore (needs at least 100). Use /profile to refill gas.")
        return

    character.gas -= 100
    await db.update_character(character)
    
    titan = await db.get_random_titan(
        max(1, character.level - 2),
        character.level + 2,
        target_level=character.level,
        unlocked_areas=player.unlocked_areas or ["Trost District", "Karanes District", "Shiganshina District", "Wall Maria", "Wall Rose"]
    )
    
    if not titan:
        await update.message.reply_text("No titans found in your level range.")
        return
    
    context.bot_data[f"last_titan_{user_id}"] = titan.name  # Store last titan
    keyboard = [[InlineKeyboardButton("⚔️ Battle", callback_data=f"battle_{titan.name}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    titan_bar = "█" * 10
    special_abilities_text = f"\nSpecial Abilities: {', '.join(titan.special_abilities)}" if titan.special_abilities else ""
    await update.message.reply_text(
        text=(
            f"<b>🛑 Titan Appeared 🛑</b>\n\n"
            f"<b>| {titan.name} (Lv. {titan.level}) |</b>\n"
            f"Difficulty: {titan.difficulty}\n"
            f"<b>HP: {titan.max_hp}/{titan.max_hp} [{titan_bar}]</b>\n\n<i>{special_abilities_text}</i>\n"
        ),
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def handle_battle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the start of a battle when user clicks the Battle button."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    
    titan_name = query.data[7:]
    user_id = update.effective_user.id
    db = await get_database()
    
    last_titan = context.bot_data.get(f"last_titan_{user_id}")
    if titan_name != last_titan:
        await query.edit_message_text(f"Error: You can only battle the last titan encountered ({last_titan}). Use /explore to find a new titan.")
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
    
    titan = await db.get_titan(titan_name)
    if not titan:
        normalized_titan_name = titan_name.lower().replace(" ", "_")
        titan = await db.get_titan(normalized_titan_name)
        if not titan:
            await query.edit_message_text(f"Error: Titan {titan_name} not found.")
            return
    
    battle = BattleSystem(character, titan)
    active_battles[user_id] = battle
    
    keyboard = generate_ability_keyboard(battle)
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    status = battle.get_battle_status()
    await query.edit_message_text(
        text=(
            f"<b>⚔️ BATTLE ⚔️</b>\n\n"
            f"<b>| {battle.titan.name} (Lv. {battle.titan.level}) |</b>\n"
            f"<b>HP: {status['titan_hp']}/{battle.titan.max_hp} [{status['titan_bar']}]</b>\n\n"
            f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>\n"
            f"<b>HP: {status['character_hp']}/{battle.character.stats.HP} [{status['character_bar']}]</b>\n"
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
    await query.answer()
    
    user_id = update.effective_user.id
    if user_id not in active_battles:
        await query.edit_message_text("Titan has ran away")
        return
    
    battle = active_battles[user_id]
    action = query.data
    full_message = []
    effects = {}
    
    if battle.timeout_task:
        battle.timeout_task.cancel()  # Cancel previous timeout
    
    if action == "action_run":
        if random.random() < 0.5:
            await query.edit_message_text(f"{battle.character.name} successfully escaped from the battle!")
            del active_battles[user_id]
            return
        else:
            full_message.append(f"{battle.character.name} failed to escape!")
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
        await handle_battle_end(query, battle, user_id)
        return

    unlocked_passives = [
        ability for ability in battle.character.passive_abilities 
        if getattr(ability, 'unlocked', False) and getattr(ability, 'gas_cost', 0) > 0
    ]
    if unlocked_passives and battle.gas < min(ability.gas_cost for ability in unlocked_passives):
        min_cost = min(ability.gas_cost for ability in unlocked_passives)
        message =(
            f"{battle.character.name} is out of gas and cannot continue the battle!"
        )
    # Only retreat if gas is less than the CHEAPEST ability's cost
        await query.edit_message_text(message)
        del active_battles[user_id]
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
        await handle_battle_end(query, battle, user_id)
        return
    if battle.titan_hp <= 0:
        await handle_battle_end(query, battle, user_id)
        return
    
    keyboard = generate_ability_keyboard(battle)
    reply_markup = InlineKeyboardMarkup(keyboard)
    status = battle.get_battle_status()
    
    battle_message = (
        f"<b>⚔️ BATTLE ⚔️</b>\n\n"
        f"{' '.join(full_message)}\n\n"
        f"<b>| {battle.titan.name} (Lv. {battle.titan.level}) |</b>\n"
        f"HP: {status['titan_hp']}/{battle.titan.max_hp} [{status['titan_bar']}]\n\n"
        f"<b>| {battle.character.name} (Lv. {battle.character.level}) |</b>\n"
        f"HP: {status['character_hp']}/{battle.character.stats.HP} [{status['character_bar']}]\n"
        f"Gas: {status['gas']}/{battle.character.gas}\n\n"
        f"{status['status_message']}\n"
        f"<b>Choose your action:</b>"
    )
    
    await query.edit_message_text(
        text=battle_message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    asyncio.create_task(battle_timeout(user_id, query, battle))

async def handle_battle_end(query, battle: BattleSystem, user_id: int):
    """Handle battle end with rewards or defeat message."""
    if battle.timeout_task:
        battle.timeout_task.cancel()
    
    db = await get_database()
    
    if battle.titan_hp <= 0:
        rewards = battle.calculate_rewards()
        reward_updates = {
            "marks": rewards["marks"],
            "$inc": {
                "crystal": rewards["crystal"],
                "valor": rewards["valor"],
                "xp": rewards["xp"]
            }
        }
        
        await db.players.update_one(
            {"user_id": user_id},
            {"$inc": reward_updates["$inc"]}
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
        
        await query.edit_message_text("\n".join(reward_msg))
    else:
        battle.character.current_hp = 0
        await db.update_character(battle.character)
        await query.edit_message_text(
            f"💀 {battle.character.name} was defeated by {battle.titan.name}! 💀\n\n"
        )
    
    del active_battles[user_id]

async def battle_timeout(user_id: int, query, battle: BattleSystem):
    """Handle battle timeout after 1 minute of inactivity."""
    battle.timeout_task = asyncio.current_task()
    await asyncio.sleep(60)
    if user_id in active_battles:
        db = await get_database()
        
        await db.characters.update_one(
            {"user_id": user_id, "name": battle.character.name},
            {"$set": {
                "current_hp": battle.character_hp,
                "gas": battle.gas,
                "ability_cooldowns": battle.ability_cooldowns
            }}
        )
        
        await query.edit_message_text(
            "⏰ Battle Expired ⏰\n\n"
            "You didn't respond in time. The battle has expired.\n"
            "Use /explore to find another titan."
        )
        del active_battles[user_id]
