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
    # Generate random 6-character string (uppercase letters and digits)
    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    # Create image with white background
    image = Image.new('RGB', (220, 80), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except:
        font = ImageFont.load_default()
    
    # Draw each character with heavy distortion and overlap
    x = 20
    for i, char in enumerate(captcha_text):
        color = (random.randint(0, 120), random.randint(0, 120), random.randint(0, 120))
        y = random.randint(5, 25)
        char_img = Image.new('RGBA', (40, 40), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((0, 0), char, fill=color, font=font)
        # Strong random rotation
        char_img = char_img.rotate(random.randint(-35, 35), expand=1, fillcolor=(255, 255, 255, 0))
        # Paste with overlap
        image.paste(char_img, (x, y), char_img)
        x += random.randint(25, 35)
    
    # Draw multiple colored crossing lines (strong distortion)
    for _ in range(12):
        color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        x1 = random.randint(0, 220)
        y1 = random.randint(0, 80)
        x2 = random.randint(0, 220)
        y2 = random.randint(0, 80)
        draw.line((x1, y1, x2, y2), fill=color, width=random.randint(1, 3))
        # Wavy lines
        if random.choice([True, False]):
            for i in range(1, 12):
                draw.line(
                    (x1 + (x2-x1)*i/12 + random.randint(-4, 4), 
                     y1 + (y2-y1)*i/12 + random.randint(-4, 4),
                     x1 + (x2-x1)*(i-1)/12 + random.randint(-4, 4), 
                     y1 + (y2-y1)*(i-1)/12 + random.randint(-4, 4)),
                    fill=color, width=1
                )
    # Add heavy noise
    for _ in range(300):
        color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        x = random.randint(0, 219)
        y = random.randint(0, 79)
        draw.point((x, y), fill=color)
    
    # Slight blur
    image = image.filter(ImageFilter.GaussianBlur(radius=1.2))
    # Rotate the whole image for final effect
    angle = random.randint(-25, 25)
    image = image.rotate(angle, expand=1, fillcolor=(0, 0, 0))
    # Save to bytes
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

# --- SPAWN CAPTCHA FOR EXPLORE ---
async def spawn_captcha(update, context):
    # Prevent multiple captchas at once
    if context.user_data.get('captcha_active'):
        return False
    # Always use text captcha now
    context.user_data['captcha_active'] = True
    await captcha(update, context)
    context.user_data['captcha_mode'] = 'text'
    context.user_data['verified'] = False
    return True


