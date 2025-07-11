import random
import string
import asyncio
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from io import BytesIO
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

def generate_captcha():
    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    image = Image.new('RGB', (150, 50), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()
    
    x = 10
    for i, char in enumerate(captcha_text):
        color = (random.randint(0, 100), random.randint(0, 100), random.randint(0, 100))
        y = random.randint(0, 15)
        char_image = Image.new('RGBA', (30, 30))
        char_draw = ImageDraw.Draw(char_image)
        char_draw.text((0, 0), char, fill=color, font=font)
        char_image = char_image.rotate(random.randint(-30, 30), expand=1, fillcolor=(255, 255, 255, 0))
        image.paste(char_image, (x, y), char_image)
        x += 25 + random.randint(-5, 5)
    
    for _ in range(8):
        color = (random.randint(0, 200), random.randint(0, 200), random.randint(0, 200))
        x1, y1, x2, y2 = random.randint(0, 150), random.randint(0, 50), random.randint(0, 150), random.randint(0, 50)
        draw.line((x1, y1, x2, y2), fill=color, width=1)
        if random.choice([True, False]):
            for i in range(1, 10):
                draw.line(
                    (
                        x1 + (x2 - x1)*i/10 + random.randint(-3,3),
                        y1 + (y2 - y1)*i/10 + random.randint(-3,3),
                        x1 + (x2 - x1)*(i-1)/10 + random.randint(-3,3),
                        y1 + (y2 - y1)*(i-1)/10 + random.randint(-3,3)
                    ),
                    fill=color, width=1
                )
    
    for _ in range(800):
        color = (random.randint(0,255), random.randint(0,255), random.randint(0,255))
        x, y = random.randint(0,150), random.randint(0,50)
        draw.point((x,y), fill=color)

    image = image.filter(ImageFilter.GaussianBlur(radius=0.8))
    
    img_byte_arr = BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return captcha_text, img_byte_arr

# --- TEXT CAPTCHA WITH TRIES ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    captcha_text, captcha_image = generate_captcha()
    context.user_data['captcha_answer'] = captcha_text
    context.user_data['captcha_tries'] = 3

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
        reply_markup=reply_markup
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_answer = query.data
    correct_answer = context.user_data.get('captcha_answer','')
    tries = context.user_data.get('captcha_tries', 3)

    if user_answer == correct_answer:
        await query.answer("Correct! You're verified.")
        await query.edit_message_caption(caption="✅ CAPTCHA passed!")
        context.user_data['verified'] = True
    else:
        tries -= 1
        context.user_data['captcha_tries'] = tries
        if tries > 0:
            await query.answer(f"Incorrect! {tries} tries left.")
            await send_new_captcha(update, context)
        else:
            await query.answer("❌ Failed all tries!")
            await query.edit_message_caption(caption=f"❌ CAPTCHA failed. Please try /explore again.")
            context.user_data['verified'] = False

# --- SEQUENCE CAPTCHA WITH TIMER AND TRIES ---
async def sequence_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbols = [
        "⭐","❤️","⚡","🎯","🔔","🌟","🔥","💎","🍀","🎲","🧩","🦄","🌈","🌻","🍎","🍕","🍔","🍟","🍩","🍪",
        "🍉","🍓","🍒","🍇","🍌","🥑","🥕","🥦","🌶️","🍄","🧀","🥨","🍤","🍣","🍦","🍰","🎂","🍫","🍬",
        "🦴","🥚","🍳","🧈","🧇","🥞","🥯","🥐","🍞","🥖","🥨","🧀","🥚","🍳","🥓","🥩","🍗","🍖","🦴"
    ]
    sequence = random.choices(symbols, k=3)
    context.user_data['captcha'] = {'type': 'sequence', 'answer': sequence, 'tries': 3, 'start_time': None, 'user_sequence': []}

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

async def verify_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_data = context.user_data.get('captcha', {})
    if not user_data:
        await query.answer("Session expired. Start again with /start")
        return

    # Check time limit
    now = asyncio.get_event_loop().time()
    if user_data['start_time'] and now - user_data['start_time'] > 180:
        await query.answer("⏰ Time's up! Sequence input expired.")
        await query.edit_message_text("❌ CAPTCHA failed. Please try /explore again.")
        context.user_data['verified'] = False
        return

    user_answer = query.data.split('_')[1]
    captcha_type = user_data['type']
    correct_answer = user_data['answer']
    tries = user_data.get('tries', 3)

    # Special handling for sequence CAPTCHA
    if captcha_type == 'sequence':
        user_data['user_sequence'].append(user_answer)
        if len(user_data['user_sequence']) < len(correct_answer):
            await query.answer(f"Selected: {user_answer}")
            return
        else:
            is_correct = user_data['user_sequence'] == correct_answer
    else:
        is_correct = user_answer == correct_answer

    if is_correct:
        await query.answer("✅ Verification successful!")
        await query.edit_message_text("✅ CAPTCHA passed!")
        context.user_data['verified'] = True
    else:
        tries -= 1
        user_data['tries'] = tries
        if tries > 0:
            await query.answer(f"❌ Wrong answer! {tries} tries left.")
            # Reset sequence for retry
            user_data['user_sequence'] = []
            await sequence_memory(update, context)
        else:
            await query.answer("❌ Failed all tries!")
            await query.edit_message_text("❌ CAPTCHA failed. Please try /explore again.")
            context.user_data['verified'] = False

# --- SPAWN CAPTCHA FOR EXPLORE ---
async def spawn_captcha(update, context):
    if random.random() < 0.6:
        captcha_type = random.choice(["text", "sequence"])
        if captcha_type == "text":
            await start(update, context)
            context.user_data['captcha_mode'] = 'text'
        else:
            await sequence_memory(update, context)
            context.user_data['captcha_mode'] = 'sequence'
        context.user_data['verified'] = False
        return True
    return False


