from typing import Dict, List, Optional, Any, Union
from database.models import Player
from database.db import Database
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from utils.mod_utils import mod_only
from utils.ban_utils import ban_protected
from utils.maintenance import maintenance_protected
import random
import asyncio
import logging
from datetime import datetime, timezone
import time

logger = logging.getLogger(__name__)

# Dealer types and spawn chances
DEALER_TYPES = [
    {
        "id": "scouting_legion",
        "name": "The Scouting Legion's Gamble",
        "description": "Marks to resources",
        "spawn_chance": 0.40,
        "image_url": "https://i.ibb.co/JNLZVBK/image.jpg",
        "messages": [
            "A scouting report just came in. A supply cache has been spotted outside the walls.",
            "The coordinates point to an abandoned outpost near Wall Rose. Proceed with caution.",
            "You've stumbled upon a hidden <b>Scouting Legion</b> supply cache! It's a risk to open it—you never know what a squad left behind, but the potential gains for the war effort are immense.\n\n<blockquote>It will cost you 20,000 Marks to secure the goods and bring them back safely.</blockquote>\n\nDo you gamble on the <b>Scouts' luck</b>?"
        ],
        "buttons": ["Gamble on the Scouts' Luck", "Leave it"],
        "cost_type": "marks",
        "cost_amount": 20000,
        "outcomes": [
            {
                "name": "Epic Success",
                "chance": 0.01,
                "text": "By some miracle, you've found a secret stash intended for a high-priority mission. The Survey Corps will be indebted to you!",
                "rewards": {"valor": 5, "marks": 50000}
            },
            {
                "name": "Valuable Discovery",
                "chance": 0.04,
                "text": "The supplies were mostly in the form of old military payroll. A huge amount of marks for your valor!",
                "rewards": {"valor": 1, "marks": 25000}
            },
            {
                "name": "Solid Gain",
                "chance": 0.35,
                "text": "Your squad made it back from the mission! You found a stash of marks.",
                "rewards": {"marks": 20000}
            },
            {
                "name": "Break-Even Deal",
                "chance": 0.10,
                "text": "The expedition was cut short, and you could only recover a few scraps. You gain some marks.",
                "rewards": {"marks": 50000}
            },
            {
                "name": "Failure",
                "chance": 0.50,
                "text": "The expedition was a bust. You encountered an Abnormal Titan and had to retreat, leaving most of the supplies behind. You manage to recover a few remaining rations and gas canisters.",
                "rewards": {"marks": 5000, "gas": 2000}
            }
        ]
    },
    {
        "id": "garrison",
        "name": "The Garrison's Black Market",
        "description": "Valor to resources",
        "spawn_chance": 0.30,
        "image_url": "https://i.ibb.co/8gcBkvkT/image.jpg",
        "messages": [
            "A suspicious package has been left for you. It's from a member of the Garrison Regiment, with a note promising 'a way to make a fortune'.",
            "The instructions lead you to a secluded alleyway in the Stohess District. The air is thick with the scent of secrecy and old secrets.",
            "You meet with a hooded figure from the <b>Garrison</b>.\n\nThey claim to have access to a large stash of funds but need your help to get them out of the city.\n\n<blockquote>For a cost of 100 Valor, you can take part in their operation.</blockquote>\n\nDo you risk your <b>honor for riches</b>?"
        ],
        "buttons": ["Risk your Valor", "Stay true to your honor"],
        "cost_type": "valor",
        "cost_amount": 50,
        "outcomes": [
            {
                "name": "Epic Success",
                "chance": 0.005,
                "text": "The Garrison member was a true ally! They've not only made good on their word, but have given you access to a cache of high-value goods.",
                "rewards": {"crystals": 1, "marks": 10000}
            },
            {
                "name": "Valuable Discovery",
                "chance": 0.085,
                "text": "The deal was a great success. The Garrison member hands you a heavy satchel full of Marks and a bonus for your help.",
                "rewards": {"valor": 75, "marks": 10000}
            },
            {
                "name": "Solid Gain",
                "chance": 0.30,
                "text": "The transaction is completed without a hitch. The Garrison member honors their side of the deal.",
                "rewards": {"valor": 50, "marks": 10000}
            },
            {
                "name": "Break-Even Deal",
                "chance": 0.11,
                "text": "The deal was less profitable than promised, but you manage to walk away with some gains.",
                "rewards": {"valor": 60, "marks": 5000}
            },
            {
                "name": "Failure",
                "chance": 0.50,
                "text": "You've been swindled. The Garrison member vanishes with most of your funds before you can react.",
                "rewards": {"valor": 10, "marks": 2500}
            }
        ]
    },
    {
        "id": "titan_shifter",
        "name": "The Titan Shifter's Secret",
        "description": "Crystals to Valor",
        "spawn_chance": 0.20,
        "image_url": "https://i.ibb.co/yF7mzSf1/image.jpg",
        "messages": [
            "A strange, unnatural tremor rattles the ground. A voice, whispering directly into your mind, offers a meeting at a desolate location outside the walls.",
            "You arrive at a desolate, fog-shrouded gorge near Shiganshina. The air feels heavy with power, and the ground is scorched as if from a recent transformation.",
            "A mysterious figure emerges from the fog, their eyes glowing with a faint, unnatural light. 'I can grant you the power to change the tides of war,' the voice whispers.\n\n<blockquote>They offer you a chance to acquire a vast supply of resources in exchange for a single Crystal.</blockquote>\n\nDo you accept the <b>Titan Shifter's bargain</b>?"
        ],
        "buttons": ["Accept the Titan's Bargain", "Reject the Forbidden Deal"],
        "cost_type": "crystals",
        "cost_amount": 1,
        "outcomes": [
            {
                "name": "Epic Success",
                "chance": 0.015,
                "text": "The mysterious figure smiles. Their power surges through you, infusing you with a monumental supply of resources. This is a secret that could turn the entire war.",
                "rewards": {"valor": 250, "marks": 75000}
            },
            {
                "name": "Valuable Discovery",
                "chance": 0.10,
                "text": "The ground quakes, and a hidden fissure opens, revealing a bounty of military supplies. It seems the Titan's power has a tangible effect.",
                "rewards": {"crystals": 1}
            },
            {
                "name": "Solid Gain",
                "chance": 0.35,
                "text": "The figure nods. A bounty of supplies emerges from a rock fissure. It seems the Titan's power holds some merit.",
                "rewards": {"valor": 150, "marks": 25000}
            },
            {
                "name": "Break-Even Deal",
                "chance": 0.085,
                "text": "The figure frowns. The exchange is stable, but nothing more. You feel an ancient power test your will, but it retreats, leaving you with what you started.",
                "rewards": {"valor": 200, "marks": 50000}
            },
            {
                "name": "Failure",
                "chance": 0.45,
                "text": "The figure's eyes glow with malicious intent. The ground beneath you cracks, but no supplies emerge. The voice laughs as your Crystal is consumed. 'A fool's bargain,' it whispers as the figure vanishes.",
                "rewards": {"valor": 100}
            }
        ]
    },
    {
        "id": "founding_titan",
        "name": "The Founding Titan's Offer",
        "description": "Ultimate Gamble",
        "spawn_chance": 0.10,
        "image_url": "https://i.ibb.co/B5Mdmvj7/image.jpg",
        "messages": [
            "A voice echoes from the depths of your mind, a primordial call from the very 'paths' that connect all Eldians.",
            "You feel an irresistible pull towards a place that exists outside of time. All around you, the ethereal sand of the Founding Titan's realm drifts endlessly.",
            "Before you, a colossal figure of bone and sinew coalesces from the swirling sands. 'This is the moment,' a chorus of voices whispers, 'the moment you can change everything. Offer your most valuable tokens to the progenitor of all power.'\n\n<blockquote>Cost: 1 Crystal and 50 Valor</blockquote>\n\nDo you accept the Founding Titan's offer?"
        ],
        "buttons": ["Accept the Forbidden Offer", "Reject the Offer"],
        "cost_type": "special",
        "cost_amount": 0,
        "outcomes": [
            {
                "name": "Epic Success",
                "chance": 0.005,
                "text": "The Founding Titan recognizes your will. Its power surges through you, reshaping reality itself. You are granted a bounty that will change the course of the war.",
                "rewards": {"crystals": 1, "valor": 350, "marks": 75000}
            },
            {
                "name": "Valuable Discovery",
                "chance": 0.125,
                "text": "The will of the Founding Titan has smiled upon you. You are granted access to a massive cache of resources that will strengthen your position in the coming war.",
                "rewards": {"crystals": 1, "valor": 50}
            },
            {
                "name": "Solid Gain",
                "chance": 0.32,
                "text": "Your tribute is accepted, and you are given a portion of its immense power. You feel the ground shake as resources manifest before you.",
                "rewards": {"crystals": 1, "valor": 200, "marks": 25000}
            },
            {
                "name": "Break-Even Deal",
                "chance": 0.08,
                "text": "The will of the Titans is not swayed. You manage to escape the 'paths' with your essence intact, but nothing more.",
                "rewards": {"crystals": 1, "valor": 275, "marks": 50000}
            },
            {
                "name": "Failure",
                "chance": 0.45,
                "text": "The voice laughs, a sound that echoes across time. You have been tested and found wanting. The power consumes your offering, leaving you with nothing but a single, haunting thought.",
                "rewards": {"marks": 25000, "gas": 10000}
            }
        ]
    }
]

# Active dealer encounters - stored in bot_data for persistence
# active_dealer_encounters = {}  # Removed global variable

def select_dealer_type() -> dict:
    """
    Select a dealer type based on spawn chances
    """
    # Calculate total chance
    total_chance = sum(dealer["spawn_chance"] for dealer in DEALER_TYPES)
    
    # Roll a random value between 0 and total chance
    roll = random.random() * total_chance
    
    # Find which dealer type was selected
    cumulative = 0
    for dealer in DEALER_TYPES:
        cumulative += dealer["spawn_chance"]
        if roll < cumulative:
            return dealer
    
    # Default to first dealer if something goes wrong
    return DEALER_TYPES[0]

async def edit_message_with_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, text: str, delay: float = 3.0):
    """Edit a message with a delay"""
    await asyncio.sleep(delay)
    try:
        await context.bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")

async def delete_message_with_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: float = 5.0):
    """Delete a message with a delay"""
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Failed to delete message: {e}")

def select_outcome(outcomes: List[dict]) -> dict:
    """
    Select a random outcome based on the chances
    """
    # Roll a random value between 0 and 1
    roll = random.random()
    
    # Find which outcome was selected
    cumulative = 0
    for outcome in outcomes:
        cumulative += outcome["chance"]
        if roll < cumulative:
            return outcome
    
    # Default to last outcome if something goes wrong
    return outcomes[-1]

async def show_dealer(update: Update, context: ContextTypes.DEFAULT_TYPE, dealer_type: Optional[str] = None):
    """
    Show a dealer to the user
    """
    if not update.effective_user or not update.effective_chat:
        return
    
    if update.effective_chat.type != "private":
        if update.message:
            await update.message.reply_text("This command can only be used in private chats.")
        return
    
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Check if player is already in a dealer encounter
    active_dealers = context.bot_data.get("active_dealer_encounters", {})
    if user_id_str in active_dealers:
        # Clear the existing dealer encounter instead of blocking
        del active_dealers[user_id_str]
        logger.info(f"Cleared existing dealer encounter for user {user_id_str} due to new explore")
    
    # Get database reference
    db = context.bot_data.get("db")
    if not db:
        if update.message:
            await update.message.reply_text("Database not available. Please try again later.")
        return
    
    # Get player data
    player = await db.get_player(user_id_str)
    if not player:
        if update.message:
            await update.message.reply_text("You need to start the game first! Use /start to begin your adventure.")
        return
    
    # Select dealer type
    selected_dealer = None
    if dealer_type:
        # For mod testing - select specific dealer type
        for dealer in DEALER_TYPES:
            if dealer["id"] == dealer_type:
                selected_dealer = dealer
                break
        if not selected_dealer:
            if update.message:
                await update.message.reply_text(f"Invalid dealer type: {dealer_type}")
            return
    else:
        # Random selection based on spawn chances
        selected_dealer = select_dealer_type()
    
    # Track active encounter BEFORE showing dealer
    if "active_dealer_encounters" not in context.bot_data:
        context.bot_data["active_dealer_encounters"] = {}
    
    context.bot_data["active_dealer_encounters"][user_id_str] = {
        "dealer_type": selected_dealer["id"],
        "start_time": time.time()
    }
    
    logger.info(f"Dealer {selected_dealer['id']} activated for user {user_id_str}")
    logger.info(f"Active dealer encounters: {list(context.bot_data['active_dealer_encounters'].keys())}")
    
    try:
        # Send first message
        if update.message:
            first_message = await update.message.reply_text(
                f"<b>{selected_dealer['messages'][0]}</b>",
                parse_mode=ParseMode.HTML
            )
            
            # Schedule edit for second message
            asyncio.create_task(
                edit_message_with_delay(
                    context=context,
                    chat_id=update.effective_chat.id,
                    message_id=first_message.message_id,
                    text=f"<b>{selected_dealer['messages'][1]}</b>",
                    delay=1.5
                )
            )
            
            # Schedule delete first message
            asyncio.create_task(
                delete_message_with_delay(
                    context=context,
                    chat_id=update.effective_chat.id,
                    message_id=first_message.message_id,
                    delay=3.0
                )
            )
            
            # Schedule send third message with image after delay
            await asyncio.sleep(3.2)
            
            # Create keyboard for dealer options
            keyboard = [
                [InlineKeyboardButton(selected_dealer["buttons"][0], callback_data=f"dealer_{selected_dealer['id']}_accept")],
                [InlineKeyboardButton(selected_dealer["buttons"][1], callback_data=f"dealer_{selected_dealer['id']}_decline")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send dealer message with image
            await update.message.reply_photo(
                photo=selected_dealer["image_url"],
                caption=f"<b>{selected_dealer['messages'][2]}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error showing dealer: {e}")
        active_dealers = context.bot_data.get("active_dealer_encounters", {})
        if user_id_str in active_dealers:
            del active_dealers[user_id_str]
        if update.message:
            await update.message.reply_text("An error occurred while showing the dealer. Please try again later.")

async def handle_dealer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle dealer callback queries
    """
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        logger.error("Invalid callback query")
        return

    await query.answer()

    user_id = query.from_user.id
    user_id_str = str(user_id)

    logger.info(f"Processing dealer callback for user {user_id_str}: {query.data}")

    active_dealers = context.bot_data.get("active_dealer_encounters", {})
    if user_id_str not in active_dealers:
        logger.warning(f"User {user_id_str} not in active dealer encounters")
        logger.warning(f"Current active dealers: {list(active_dealers.keys())}")
        await query.edit_message_caption(
            caption="This dealer encounter has expired. Use /explore to find another dealer.",
            parse_mode=ParseMode.HTML
        )
        return

    # Parse callback data
    # Format: dealer_[dealer_id]_[action]
    try:
        if not query.data.startswith("dealer_"):
            logger.error(f"Invalid callback data format: {query.data}")
            return

        # Remove "dealer_" prefix and split the rest
        callback_parts = query.data[7:].rsplit("_", 1)  # Split from right, max 1 split
        if len(callback_parts) != 2:
            logger.error(f"Failed to parse callback data: {query.data}, parts: {callback_parts}")
            return

        dealer_id, action = callback_parts
        logger.info(f"Parsed callback: dealer_id={dealer_id}, action={action}")
    except Exception as e:
        logger.error(f"Failed to parse callback data: {query.data}, error: {e}")
        return
    
    # Get dealer data
    dealer_data = None
    for dealer in DEALER_TYPES:
        if dealer["id"] == dealer_id:
            dealer_data = dealer
            break
    
    if not dealer_data:
        return
    
    # Handle decline action
    if action == "decline":
        logger.info(f"User {user_id_str} declined dealer offer: {dealer_id}")
        active_dealers = context.bot_data.get("active_dealer_encounters", {})
        if user_id_str in active_dealers:
            del active_dealers[user_id_str]
        
        await query.edit_message_caption(
            caption=f"<b>You decided not to take the {dealer_data['name']}'s offer. Perhaps another time.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        return
    
    # Handle accept action
    if action == "accept":
        logger.info(f"User {user_id_str} accepted dealer offer: {dealer_id}")

        # Get database reference
        db = context.bot_data.get("db")
        if not db:
            logger.error("Database not available")
            await query.edit_message_caption(
                caption="Database not available. Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            return

        # Get player data
        player = await db.get_player(user_id_str)
        if not player:
            logger.error(f"Player {user_id_str} not found")
            await query.edit_message_caption(
                caption="Player data not found. Please use /start to begin your adventure.",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            return

        logger.info(f"Player {user_id_str} data retrieved successfully")

        # Check if player has enough resources to accept the offer
        cost_type = dealer_data["cost_type"]
        cost_amount = dealer_data["cost_amount"]

        logger.info(f"Checking resources: {cost_type} = {cost_amount}")

        resource_check_passed = True
        if cost_type == "marks":
            current_marks = getattr(player, "marks", 0)
            logger.info(f"Player marks: {current_marks}, required: {cost_amount}")
            if current_marks < cost_amount:
                resource_check_passed = False
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Marks to accept this offer. You need {cost_amount} Marks.</b>\n\nThe dealer disappears into the shadows.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
        elif cost_type == "valor":
            current_valor = getattr(player, "valor", 0)
            logger.info(f"Player valor: {current_valor}, required: {cost_amount}")
            if current_valor < cost_amount:
                resource_check_passed = False
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Valor to accept this offer. You need {cost_amount} Valor.</b>\n\nThe dealer disappears into the shadows.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
        elif cost_type == "crystals":
            current_crystals = getattr(player, "crystal", 0)
            logger.info(f"Player crystal: {current_crystals}, required: {cost_amount}")
            if current_crystals < cost_amount:
                resource_check_passed = False
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Crystals to accept this offer. You need {cost_amount} Crystals.</b>\n\nThe dealer disappears into the shadows.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )

        if not resource_check_passed:
            logger.info(f"Resource check failed for user {user_id_str}")
            active_dealers = context.bot_data.get("active_dealer_encounters", {})
            if user_id_str in active_dealers:
                del active_dealers[user_id_str]
            return
        
        # Special handler for Founding Titan's Offer
        if dealer_id == "founding_titan":
            logger.info(f"Founding titan offer accepted by {user_id_str}")
            
            # Check for special cost: 1 Crystal and 50 Valor
            current_crystals = getattr(player, "crystal", 0)
            current_valor = getattr(player, "valor", 0)
            
            if current_crystals < 1:
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Crystals to accept this offer. You need 1 Crystal.</b>\n\nThe Founding Titan's presence fades away.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                active_dealers = context.bot_data.get("active_dealer_encounters", {})
                if user_id_str in active_dealers:
                    del active_dealers[user_id_str]
                return
            
            if current_valor < 50:
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Valor to accept this offer. You need 50 Valor.</b>\n\nThe Founding Titan's presence fades away.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                active_dealers = context.bot_data.get("active_dealer_encounters", {})
                if user_id_str in active_dealers:
                    del active_dealers[user_id_str]
                return
            
            # Deduct special cost
            player_updates = {
                "crystal": player.crystal - 1,
                "valor": player.valor - 50
            }
            
            # Select outcome
            outcome = select_outcome(dealer_data["outcomes"])
            logger.info(f"Selected outcome for {user_id_str}: {outcome['name']}")
            
            # Apply rewards
            for reward_type, reward_amount in outcome["rewards"].items():
                if reward_type == "marks":
                    current = getattr(player, "marks", 0)
                    player_updates["marks"] = current + reward_amount
                elif reward_type == "valor":
                    current = getattr(player, "valor", 0)
                    player_updates["valor"] = current + reward_amount
                elif reward_type == "crystal":
                    current = getattr(player, "crystal", 0)
                    player_updates["crystal"] = current + reward_amount
            
            # Update player in database
            try:
                await db.update_player(user_id_str, player_updates)
                logger.info(f"Successfully updated player {user_id_str} in database")
            except Exception as e:
                logger.error(f"Error updating player {user_id_str}: {e}")
                await query.edit_message_caption(
                    caption="An error occurred while processing the Founding Titan's offer. Please try again later.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                active_dealers = context.bot_data.get("active_dealer_encounters", {})
                if user_id_str in active_dealers:
                    del active_dealers[user_id_str]
                return
            
            # Build reward message
            reward_text = ""
            for reward_type, reward_amount in outcome["rewards"].items():
                if reward_type == "marks":
                    reward_text += f"• {reward_amount} Marks\n"
                elif reward_type == "valor":
                    reward_text += f"• {reward_amount} Valor\n"
                elif reward_type == "crystal":
                    reward_text += f"• {reward_amount} Crystal\n"
            
            # Edit message with outcome
            await query.edit_message_caption(
                caption=f"<b>{outcome['text']}</b>\n\n<b>You received:</b>\n{reward_text}",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            
            # Remove from active encounters
            active_dealers = context.bot_data.get("active_dealer_encounters", {})
            if user_id_str in active_dealers:
                del active_dealers[user_id_str]
            return        logger.info(f"Processing transaction for user {user_id_str}")

        # Deduct cost
        player_updates = {}

        if cost_type == "marks":
            player_updates["marks"] = player.marks - cost_amount
        elif cost_type == "valor":
            player_updates["valor"] = player.valor - cost_amount
        elif cost_type == "crystals":
            player_updates["crystal"] = player.crystal - cost_amount

        # Select outcome
        outcome = select_outcome(dealer_data["outcomes"])
        logger.info(f"Selected outcome for {user_id_str}: {outcome['name']}")

        # Apply rewards
        for reward_type, reward_amount in outcome["rewards"].items():
            if reward_type == "marks":
                current = getattr(player, "marks", 0)
                player_updates["marks"] = current + reward_amount
            elif reward_type == "valor":
                current = getattr(player, "valor", 0)
                player_updates["valor"] = current + reward_amount
            elif reward_type == "crystal":
                current = getattr(player, "crystal", 0)
                player_updates["crystal"] = current + reward_amount
            elif reward_type == "gas":
                current = getattr(player, "gas", 0)
                player_updates["gas"] = current + reward_amount

        logger.info(f"Player updates for {user_id_str}: {player_updates}")

        # Update player in database
        try:
            await db.update_player(user_id_str, player_updates)
            logger.info(f"Successfully updated player {user_id_str} in database")
        except Exception as e:
            logger.error(f"Error updating player {user_id_str}: {e}")
            await query.edit_message_caption(
                caption="An error occurred while processing the offer. Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            active_dealers = context.bot_data.get("active_dealer_encounters", {})
            if user_id_str in active_dealers:
                del active_dealers[user_id_str]
            return
        
        # Build reward message
        reward_text = ""
        for reward_type, reward_amount in outcome["rewards"].items():
            if reward_type == "marks":
                reward_text += f"• {reward_amount} Marks\n"
            elif reward_type == "valor":
                reward_text += f"• {reward_amount} Valor\n"
            elif reward_type == "crystal":
                reward_text += f"• {reward_amount} Crystal\n"
            elif reward_type == "gas":
                reward_text += f"• {reward_amount} Gas\n"
        
        logger.info(f"Final reward text for {user_id_str}: {reward_text.strip()}")

        # Edit message with outcome
        await query.edit_message_caption(
            caption=f"<b>{outcome['text']}</b>\n\n<b>You received:</b>\n{reward_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        
        logger.info(f"Successfully completed dealer transaction for user {user_id_str}")

        # Remove from active encounters
        active_dealers = context.bot_data.get("active_dealer_encounters", {})
        if user_id_str in active_dealers:
            del active_dealers[user_id_str]


