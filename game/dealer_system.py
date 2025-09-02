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

# Common image URL for dealer encounters
DEALER_IMAGE_URL = "https://i.ibb.co/3Yy111LY/image.jpg"

# Dealer types and spawn chances
DEALER_TYPES = [
    {
        "id": "scouting_legion",
        "name": "The Scouting Legion's Gamble",
        "description": "Marks to resources",
        "spawn_chance": 0.40,
        "messages": [
            "A scouting report just came in. A supply cache has been spotted outside the walls.",
            "The coordinates point to an abandoned outpost near Wall Rose. Proceed with caution.",
            "You've stumbled upon a hidden Scouting Legion supply cache! It's a risk to open it—you never know what a squad left behind, but the potential gains for the war effort are immense. It will cost you 20,000 Marks to secure the goods and bring them back safely. Do you gamble on the Scouts' luck?"
        ],
        "buttons": ["Gamble on the Scouts' Luck", "Leave it"],
        "cost_type": "marks",
        "cost_amount": 20000,
        "outcomes": [
            {
                "name": "Epic Success",
                "chance": 0.03,
                "text": "By some miracle, you've found a secret stash intended for a high-priority mission. The Survey Corps will be indebted to you!",
                "rewards": {"crystals": 2, "valor": 10}
            },
            {
                "name": "Valuable Discovery",
                "chance": 0.20,
                "text": "The supplies were mostly in the form of old military payroll. A huge amount of marks for your valor!",
                "rewards": {"marks": 50000}
            },
            {
                "name": "Solid Gain",
                "chance": 0.50,
                "text": "Your squad made it back from the mission! You found a stash of valors.",
                "rewards": {"valor": 25}
            },
            {
                "name": "Break-Even Deal",
                "chance": 0.15,
                "text": "The expedition was cut short, and you could only recover a few scraps. You break even on the mission, but gain a small amount of useful gas.",
                "rewards": {"marks": 20000, "gas": 5000}
            },
            {
                "name": "Failure",
                "chance": 0.12,
                "text": "The expedition was a bust. You encountered an Abnormal Titan and had to retreat, leaving most of the supplies behind. You manage to recover a few remaining rations and gas canisters.",
                "rewards": {"gas": 2000, "marks": 5000}
            }
        ]
    },
    {
        "id": "garrison",
        "name": "The Garrison's Black Market",
        "description": "Valor to resources",
        "spawn_chance": 0.30,
        "messages": [
            "A suspicious package has been left for you. It's from a member of the Garrison Regiment, with a note promising 'a way to make a fortune'.",
            "The instructions lead you to a secluded alleyway in the Stohess District. The air is thick with the scent of secrecy and old secrets.",
            "You meet with a hooded figure from the Garrison. They claim to have access to a large stash of funds but need your help to get them out of the city. For a cost of 100 Valor, you can take part in their operation. Do you risk your honor for riches?"
        ],
        "buttons": ["Risk your Valor", "Stay true to your honor"],
        "cost_type": "valor",
        "cost_amount": 100,
        "outcomes": [
            {
                "name": "Epic Success",
                "chance": 0.03,
                "text": "The Garrison member was a true ally! They've not only made good on their word, but have given you access to a cache of high-value goods.",
                "rewards": {"crystals": 1, "valor": 100}
            },
            {
                "name": "Valuable Discovery",
                "chance": 0.20,
                "text": "The deal was a great success. The Garrison member hands you a heavy satchel full of Marks and a bonus for your help.",
                "rewards": {"valor": 85, "marks": 20000}
            },
            {
                "name": "Solid Gain",
                "chance": 0.50,
                "text": "The transaction is completed without a hitch. The Garrison member honors their side of the deal.",
                "rewards": {"valor": 60, "marks": 15000}
            },
            {
                "name": "Break-Even Deal",
                "chance": 0.15,
                "text": "The deal was less profitable than promised, but you manage to walk away without a loss.",
                "rewards": {"valor": 50, "marks": 5000}
            },
            {
                "name": "Failure",
                "chance": 0.12,
                "text": "You've been swindled. The Garrison member vanishes with most of your funds before you can react.",
                "rewards": {"valor": 20, "marks": 5000}
            }
        ]
    },
    {
        "id": "titan_shifter",
        "name": "The Titan Shifter's Secret",
        "description": "Crystals to Valor",
        "spawn_chance": 0.20,
        "messages": [
            "A strange, unnatural tremor rattles the ground. A voice, whispering directly into your mind, offers a meeting at a desolate location outside the walls.",
            "You arrive at a desolate, fog-shrouded gorge near Shiganshina. The air feels heavy with power, and the ground is scorched as if from a recent transformation.",
            "A mysterious figure emerges from the fog, their eyes glowing with a faint, unnatural light. 'I can grant you the power to change the tides of war,' the voice whispers. They offer you a chance to acquire a vast supply of resources in exchange for a single Crystal. Do you accept the Titan Shifter's bargain?"
        ],
        "buttons": ["Accept the Titan's Bargain", "Reject the Forbidden Deal"],
        "cost_type": "crystals",
        "cost_amount": 1,
        "outcomes": [
            {
                "name": "Epic Success",
                "chance": 0.05,
                "text": "The mysterious figure smiles. Their power surges through you, infusing you with a monumental supply of resources. This is a secret that could turn the entire war.",
                "rewards": {"valor": 300, "marks": 75000}
            },
            {
                "name": "Valuable Discovery",
                "chance": 0.20,
                "text": "The ground quakes, and a hidden fissure opens, revealing a bounty of military supplies. It seems the Titan's power has a tangible effect.",
                "rewards": {"valor": 200, "marks": 40000}
            },
            {
                "name": "Solid Gain",
                "chance": 0.50,
                "text": "The figure nods. A bounty of supplies emerges from a rock fissure. It seems the Titan's power holds some merit.",
                "rewards": {"valor": 100, "marks": 20000}
            },
            {
                "name": "Break-Even Deal",
                "chance": 0.15,
                "text": "The figure frowns. The exchange is stable, but nothing more. You feel an ancient power test your will, but it retreats, leaving you with what you started.",
                "rewards": {"crystals": 1}
            },
            {
                "name": "Failure",
                "chance": 0.10,
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
        "messages": [
            "A flash of light pierces your vision. Time itself seems to stop as you find yourself in a strange, otherworldly dimension.",
            "Paths of light stretch out in all directions, connecting to countless points in space and time. A presence, ancient and immeasurable, draws near.",
            "Before you stands a figure emanating pure power—the essence of the Founding Titan itself. It offers a once-in-a-lifetime gamble, the stakes higher than anything you've encountered. Will you risk everything for the chance at unimaginable power?"
        ],
        "buttons": ["Accept the Ultimate Gamble", "Decline the Founder's Offer"],
        "cost_type": "special",
        "cost_amount": 0,  # Special case, will be handled separately
        "outcomes": []  # This dealer is under construction
    }
]

# Active dealer encounters
active_dealer_encounters = {}

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
    if user_id_str in active_dealer_encounters:
        if update.message:
            await update.message.reply_text("You are already in a dealer encounter. Finish that one first.")
        return
    
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
    
    # Track active encounter
    active_dealer_encounters[user_id_str] = {
        "dealer_type": selected_dealer["id"],
        "start_time": time.time()
    }
    
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
                    delay=3.0
                )
            )
            
            # Schedule delete first message
            asyncio.create_task(
                delete_message_with_delay(
                    context=context,
                    chat_id=update.effective_chat.id,
                    message_id=first_message.message_id,
                    delay=6.0
                )
            )
            
            # Schedule send third message with image after delay
            await asyncio.sleep(6.5)
            
            # Create keyboard for dealer options
            keyboard = [
                [InlineKeyboardButton(selected_dealer["buttons"][0], callback_data=f"dealer_{selected_dealer['id']}_accept")],
                [InlineKeyboardButton(selected_dealer["buttons"][1], callback_data=f"dealer_{selected_dealer['id']}_decline")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send dealer message with image
            await update.message.reply_photo(
                photo=DEALER_IMAGE_URL,
                caption=f"<b>{selected_dealer['messages'][2]}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error showing dealer: {e}")
        if user_id_str in active_dealer_encounters:
            del active_dealer_encounters[user_id_str]
        if update.message:
            await update.message.reply_text("An error occurred while showing the dealer. Please try again later.")

async def handle_dealer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle dealer callback queries
    """
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return
    
    await query.answer()
    
    user_id = query.from_user.id
    user_id_str = str(user_id)
    
    if not user_id_str in active_dealer_encounters:
        await query.edit_message_caption(
            caption="This dealer encounter has expired. Use /dealer to find another dealer.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Parse callback data
    # Format: dealer_[dealer_id]_[action]
    try:
        _, dealer_id, action = query.data.split("_")
    except ValueError:
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
        if user_id_str in active_dealer_encounters:
            del active_dealer_encounters[user_id_str]
        
        await query.edit_message_caption(
            caption=f"<b>You decided not to take the {dealer_data['name']}'s offer. Perhaps another time.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        return
    
    # Handle accept action
    if action == "accept":
        # Get database reference
        db = context.bot_data.get("db")
        if not db:
            await query.edit_message_caption(
                caption="Database not available. Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            return
        
        # Get player data
        player = await db.get_player(user_id_str)
        if not player:
            await query.edit_message_caption(
                caption="Player data not found. Please use /start to begin your adventure.",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            return
        
        # Check if player has enough resources to accept the offer
        cost_type = dealer_data["cost_type"]
        cost_amount = dealer_data["cost_amount"]
        
        if cost_type == "marks":
            if not hasattr(player, "marks") or player.marks < cost_amount:
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Marks to accept this offer. You need {cost_amount} Marks.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                return
        elif cost_type == "valor":
            if not hasattr(player, "valor") or player.valor < cost_amount:
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Valor to accept this offer. You need {cost_amount} Valor.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                return
        elif cost_type == "crystals":
            if not hasattr(player, "crystals") or player.crystals < cost_amount:
                await query.edit_message_caption(
                    caption=f"<b>You don't have enough Crystals to accept this offer. You need {cost_amount} Crystals.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None
                )
                return
        
        # Special handler for Founding Titan's Offer - not implemented yet
        if dealer_id == "founding_titan":
            await query.edit_message_caption(
                caption="<b>The Founding Titan's power is too great to harness just yet. This offer will be available soon.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            if user_id_str in active_dealer_encounters:
                del active_dealer_encounters[user_id_str]
            return
        
        # Deduct cost
        player_updates = {}
        
        if cost_type == "marks":
            player_updates["marks"] = player.marks - cost_amount
        elif cost_type == "valor":
            player_updates["valor"] = player.valor - cost_amount
        elif cost_type == "crystals":
            player_updates["crystals"] = player.crystals - cost_amount
        
        # Select outcome
        outcome = select_outcome(dealer_data["outcomes"])
        
        # Apply rewards
        for reward_type, reward_amount in outcome["rewards"].items():
            if reward_type == "marks":
                current = getattr(player, "marks", 0)
                player_updates["marks"] = current + reward_amount
            elif reward_type == "valor":
                current = getattr(player, "valor", 0)
                player_updates["valor"] = current + reward_amount
            elif reward_type == "crystals":
                current = getattr(player, "crystals", 0)
                player_updates["crystals"] = current + reward_amount
            elif reward_type == "gas":
                current = getattr(player, "gas", 0)
                player_updates["gas"] = current + reward_amount
        
        # Update player in database
        try:
            await db.update_player(user_id_str, player_updates)
        except Exception as e:
            logger.error(f"Error updating player: {e}")
            await query.edit_message_caption(
                caption="An error occurred while processing the offer. Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            if user_id_str in active_dealer_encounters:
                del active_dealer_encounters[user_id_str]
            return
        
        # Build reward message
        reward_text = ""
        for reward_type, reward_amount in outcome["rewards"].items():
            if reward_type == "marks":
                reward_text += f"• {reward_amount} Marks\n"
            elif reward_type == "valor":
                reward_text += f"• {reward_amount} Valor\n"
            elif reward_type == "crystals":
                reward_text += f"• {reward_amount} Crystals\n"
            elif reward_type == "gas":
                reward_text += f"• {reward_amount} Gas\n"
        
        # Edit message with outcome
        await query.edit_message_caption(
            caption=f"<b>{outcome['text']}</b>\n\n<b>You received:</b>\n{reward_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        
        # Remove from active encounters
        if user_id_str in active_dealer_encounters:
            del active_dealer_encounters[user_id_str]

@mod_only
async def test_dealer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mod-only command to test dealers
    """
    if not update.effective_user or not context.args:
        if update.message:
            available_dealers = ", ".join(dealer["id"] for dealer in DEALER_TYPES)
            await update.message.reply_text(
                f"Usage: /testdealer [dealer_type]\nAvailable dealer types: {available_dealers}"
            )
        return
    
    dealer_type = context.args[0].lower()
    
    # Check if dealer type is valid
    valid_dealer = False
    for dealer in DEALER_TYPES:
        if dealer["id"] == dealer_type:
            valid_dealer = True
            break
    
    if not valid_dealer:
        if update.message:
            available_dealers = ", ".join(dealer["id"] for dealer in DEALER_TYPES)
            await update.message.reply_text(
                f"Invalid dealer type: {dealer_type}\nAvailable dealer types: {available_dealers}"
            )
        return
    
    # Show dealer
    await show_dealer(update, context, dealer_type)
