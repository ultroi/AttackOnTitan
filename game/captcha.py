import random
import string
import asyncio
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from io import BytesIO
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.ext import ContextTypes

import random
import string
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from io import BytesIO

def generate_captcha():
    # Generate random 5-character text
    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    
    # Create a blank image with white background
    image = Image.new('RGB', (150, 50), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # Try to load a font (use default if not found)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()
    
    # Draw each character with slight variations
    x = 10
    for char in captcha_text:
        # Use darker colors for better visibility
        color = (random.randint(0, 100), random.randint(0, 100), random.randint(0, 100))
        y = random.randint(0, 15)
        
        # Draw the character directly on the main image with slight rotation
        char_image = Image.new('RGBA', (30, 30))
        char_draw = ImageDraw.Draw(char_image)
        char_draw.text((0, 0), char, fill=color, font=font)
        char_image = char_image.rotate(random.randint(-15, 15), expand=1, fillcolor=(255, 255, 255, 0))
        image.paste(char_image, (x, y), char_image)
        x += 25 + random.randint(-3, 3)
    
    # Add fewer and simpler interference lines
    for _ in range(5):  # Reduced from 8
        color = (random.randint(150, 200), random.randint(150, 200), random.randint(150, 200))  # Lighter lines
        x1, y1 = random.randint(0, 150), random.randint(0, 50)
        x2, y2 = random.randint(0, 150), random.randint(0, 50)
        draw.line((x1, y1, x2, y2), fill=color, width=1)
    
    # Reduce the number of noise points significantly
    for _ in range(100):  # Reduced from 800
        color = (random.randint(200, 255), random.randint(200, 255), random.randint(200, 255))  # Very light noise
        x, y = random.randint(0, 150), random.randint(0, 50)
        draw.point((x, y), fill=color)
    
    # Use lighter blur or remove it completely
    image = image.filter(ImageFilter.GaussianBlur(radius=0.3))  # Reduced from 0.8
    
    # Convert image to byte array
    img_byte_arr = BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return captcha_text, img_byte_arr

# --- TEXT CAPTCHA WITH TRIES ---
async def captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    captcha_text, captcha_image = generate_captcha()
    if context.user_data is None:
        context.user_data = {}
    context.user_data['captcha_answer'] = captcha_text
    context.user_data['captcha_tries'] = 3
    context.user_data['captcha_active'] = True

    options = [captcha_text]
    while len(options) < 9:
        random_option = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        if random_option not in options:
            options.append(random_option)
    random.shuffle(options)

    keyboard = [
        [InlineKeyboardButton(opt, callback_data=opt) for opt in options[i:i+3]]
        for i in range(0, 9, 3)
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Spoiler image (Telegram supports <spoiler> in HTML for text, not images, so fallback to normal image)
    await update.message.reply_photo(
        photo=captcha_image,
        caption="Please select the correct CAPTCHA text:",
        reply_markup=reply_markup,
        has_spoiler=True
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or context.user_data is None:
        if query:
            await query.answer("Session expired. Start again with /start")
        return

    user_answer = getattr(query, "data", None)
    correct_answer = context.user_data.get('captcha_answer', '')
    tries = context.user_data.get('captcha_tries', 3)

    if user_answer == correct_answer:
        await query.answer("Correct! You're verified.")
        await query.edit_message_caption(caption="✅ CAPTCHA passed!")
        context.user_data['verified'] = True
        context.user_data['captcha_active'] = False
    else:
        tries -= 1
        context.user_data['captcha_tries'] = tries
        if tries > 0:
            await query.answer(f"Incorrect! {tries} tries left.")
            # Regenerate a new captcha and send it again
            captcha_text, captcha_image = generate_captcha()
            context.user_data['captcha_answer'] = captcha_text
            options = [captcha_text]
            while len(options) < 9:
                random_option = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
                if random_option not in options:
                    options.append(random_option)
            random.shuffle(options)
            keyboard = [
                [InlineKeyboardButton(opt, callback_data=opt) for opt in options[i:i+3]]
                for i in range(0, 9, 3)
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            try:
                await query.edit_message_media(
                    media=InputMediaPhoto(captcha_image, has_spoiler=True),
                    reply_markup=reply_markup
                )
                await query.edit_message_caption(caption="Please select the correct CAPTCHA text:")
            except Exception:
                # If edit fails, send a new message
                await query.message.reply_photo(
                    photo=captcha_image,
                    caption="Please select the correct CAPTCHA text:",
                    reply_markup=reply_markup,
                    has_spoiler=True
                )
        else:
            await query.answer("❌ Failed all tries!")
            await query.edit_message_caption(caption=f"❌ CAPTCHA failed. Please try /explore again.")
            context.user_data['verified'] = False
            context.user_data['captcha_active'] = False

# --- SEQUENCE CAPTCHA WITH TIMER AND TRIES ---
async def sequence_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbols = [
        "⭐","❤️","⚡","🎯","🔔","🌟","🔥","💎","🍀","🎲","🧩","🦄","🌈","🌻","🍎","🍕","🍔","🍟","🍩","🍪",
        "🍉","🍓","🍒","🍇","🍌","🥑","🥕","🥦","🌶️","🍄","🧀","🥨","🍤","🍣","🍦","🍰","🎂","🍫","🍬",
        "🦴","🥚","🍳","🧈","🧇","🥞","🥯","🥐","🍞","🥖","🥨","🧀","🥚","🍳","🥓","🥩","🍗","🍖","🦴"
    ]
    sequence = random.choices(symbols, k=3)
    if context.user_data is None:
        context.user_data = {}
    context.user_data['captcha'] = {'type': 'sequence', 'answer': sequence, 'tries': 3, 'start_time': None, 'user_sequence': []}
    context.user_data['captcha_active'] = True

    if update.message is not None:
        msg = await update.message.reply_text(
            "🧠 Memorize this sequence (1 min):\n" + " ".join(sequence)
        )
        context.user_data['captcha']['start_time'] = asyncio.get_event_loop().time()
        await asyncio.sleep(60)
        await msg.delete()

        shuffled = random.sample(symbols, len(symbols))
        buttons = [[InlineKeyboardButton(s, callback_data=f"seq_{s}") for s in shuffled]]
        await update.message.reply_text(
            "Now tap the symbols in correct order (3 min, 3 tries):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await update.effective_chat.send_message(
            "🧠 Memorize this sequence (1 min):\n" + " ".join(sequence)
        )
        context.user_data['captcha']['start_time'] = asyncio.get_event_loop().time()
        await asyncio.sleep(60)
        shuffled = random.sample(symbols, len(symbols))
        buttons = [[InlineKeyboardButton(s, callback_data=f"seq_{s}") for s in shuffled]]
        await update.effective_chat.send_message(
            "Now tap the symbols in correct order (3 min, 3 tries):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def verify_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = getattr(update, "callback_query", None)
    user_data = getattr(context, "user_data", None)
    if query is None or user_data is None:
        if query:
            await query.answer("Session expired. Start again with /start")
        return

    captcha = user_data.get('captcha', {})
    if not captcha:
        await query.answer("Session expired. Start again with /start")
        return

    # Check time limit
    now = asyncio.get_event_loop().time()
    if captcha.get('start_time') and now - captcha['start_time'] > 180:
        await query.answer("⏰ Time's up! Sequence input expired.")
        await query.edit_message_text("❌ CAPTCHA failed. Please try /explore again.")
        user_data['verified'] = False
        user_data['captcha_active'] = False
        return

    query_data = getattr(query, "data", None)
    if not query_data or "_" not in query_data:
        await query.answer("Invalid input.")
        return

    user_answer = query_data.split('_')[1]
    captcha_type = captcha.get('type')
    correct_answer = captcha.get('answer')
    tries = captcha.get('tries', 3)

    # Special handling for sequence CAPTCHA
    if captcha_type == 'sequence':
        captcha['user_sequence'].append(user_answer)
        if len(captcha['user_sequence']) < len(correct_answer):
            await query.answer(f"Selected: {user_answer}")
            return
        else:
            is_correct = captcha['user_sequence'] == correct_answer
    else:
        is_correct = user_answer == correct_answer

    if is_correct:
        await query.answer("✅ Verification successful!")
        await query.edit_message_text("✅ CAPTCHA passed!")
        user_data['verified'] = True
        user_data['captcha_active'] = False
    else:
        tries -= 1
        captcha['tries'] = tries
        if tries > 0:
            await query.answer(f"❌ Wrong answer! {tries} tries left.")
            # Reset sequence for retry
            captcha['user_sequence'] = []
            await sequence_memory(update, context)
        else:
            await query.answer("❌ Failed all tries!")
            await query.edit_message_text("❌ CAPTCHA failed. Please try /explore again.")
            user_data['verified'] = False
            user_data['captcha_active'] = False

# --- SPAWN CAPTCHA FOR EXPLORE ---
async def spawn_captcha(update, context):
    # Prevent multiple captchas at once
    if context.user_data.get('captcha_active'):
        return False
    if random.random() < 0.6:
        captcha_type = random.choice(["text", "sequence"])
        context.user_data['captcha_active'] = True
        if captcha_type == "text":
            await captcha(update, context)
            context.user_data['captcha_mode'] = 'text'
        else:
            await sequence_memory(update, context)
            context.user_data['captcha_mode'] = 'sequence'
        context.user_data['verified'] = False
        return True
    return False


