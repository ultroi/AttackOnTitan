import asyncio
from telegram.constants import ParseMode
from utils.ban_utils import ban_user
from utils.owners import get_owner_ids
from database.db_instance import get_database
import random
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
import time

def generate_captcha():
    # Generate random 6-character string (uppercase letters and digits)
    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    # Image size
    width, height = 240, 90
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Load font
    try:
        font = ImageFont.truetype("arial.ttf", size=60)
    except:
        font = ImageFont.load_default()

    # Draw background noise lines
    for _ in range(8):
        color = tuple(random.randint(100, 180) for _ in range(3))
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=color, width=2)

    # Draw characters (always black color)
    x = 10
    for char in captcha_text:
        y = random.randint(10, 25)
        char_img = Image.new('RGBA', (60, 60), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((5, 5), char, font=font, fill=(0, 0, 0))  # Black color

        rotated = char_img.rotate(random.randint(-25, 25), resample=Image.Resampling.BICUBIC, expand=1)
        image.paste(rotated, (x, y), rotated)
        x += 35

    # Add noise dots
    for _ in range(200):
        color = tuple(random.randint(0, 255) for _ in range(3))
        px = random.randint(0, width - 1)
        py = random.randint(0, height - 1)
        draw.point((px, py), fill=color)

    # Slight blur
    image = image.filter(ImageFilter.GaussianBlur(radius=0.8))

    # More crossing lines for distortion
    for _ in range(5):
        color = tuple(random.randint(0, 150) for _ in range(3))
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=color, width=2)

    # Save to BytesIO
    img_byte_arr = BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)

    return captcha_text, img_byte_arr

# --- TEXT CAPTCHA WITH TRIES ---
async def captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # --- Captcha Timeout Task ---
    async def captcha_timeout():
        await asyncio.sleep(480)  # 8 minutes
        if context.user_data is not None and context.user_data.get('captcha_active', False):
            # Ban user for captcha timeout
            user_id = update.effective_user.id if update.effective_user is not None else None
            db = await get_database()
            if db is not None and user_id is not None:
                await db["bans"].update_one(
                    {"user_id": user_id},
                    {"$set": {"user_id": user_id, "expiry": None, "reason": "Captcha Timeout", "banned_by": user_id, "banned_at": int(time.time())}},
                    upsert=True
                )
            # Log to channel
            msg = (
                f"<b>#CaptchaTimeout</b>\n\n"
                f"<b>User</b> : <a href=\"tg://user?id={user_id}\">{update.effective_user.first_name}</a>\n"
                f"<b>ID</b> : <code>{user_id}</code>"
            )
            await context.bot.send_message(-1002873117075, msg, parse_mode=ParseMode.HTML)
            # Notify user
            if update.effective_message is not None:
                await update.effective_message.reply_text("Captcha timeout! You are banned.")
            elif update.message is not None:
                await update.message.reply_text("Captcha timeout! You are banned.")
            context.user_data['captcha_active'] = False
    # Ensure user_data is a dict
    if context.user_data is None:
        context.user_data = {}
    # Start timeout task
    context.user_data['captcha_timeout_task'] = asyncio.create_task(captcha_timeout())
    captcha_text, captcha_image = generate_captcha()
    context.user_data['captcha_answer'] = captcha_text
    context.user_data['captcha_tries'] = 3
    context.user_data['captcha_active'] = True

    # Always generate options with same length as captcha_text
    options = [captcha_text]
    while len(options) < 9:
        random_option = ''.join(random.choices(string.ascii_uppercase + string.digits, k=len(captcha_text)))
        # Ensure not too similar to answer (at least 2 chars different)
        if random_option not in options and sum(a != b for a, b in zip(random_option, captcha_text)) >= 2:
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
    user_id = update.effective_user.id if update.effective_user else None
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
        try:
            # Delete the original photo message
            await query.message.delete()
        except Exception:
            pass
        # Give XP reward
        xp_reward = random.randint(100, 150)
        db = await get_database()
        if db is not None and user_id:
            await db["players"].update_one({"user_id": str(user_id)}, {"$inc": {"xp": xp_reward, "total_xp": xp_reward}})
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ CAPTCHA passed! You gained {xp_reward} XP."
        )
        context.user_data['verified'] = True
        context.user_data['captcha_active'] = False
        # Cancel timeout task
        timeout_task = context.user_data.get('captcha_timeout_task')
        if timeout_task:
            timeout_task.cancel()
    else:
        tries -= 1
        context.user_data['captcha_tries'] = tries
        if tries > 0:
            await query.answer(f"Incorrect! {tries} tries left.")
            try:
                await query.edit_message_caption(
                    caption=f"❌ Incorrect! {tries} tries left.\nPlease select the correct CAPTCHA text:",
                    reply_markup=query.message.reply_markup
                )
            except Exception:
                pass
        else:
            await query.answer("❌ Failed all tries!")
            try:
                await query.edit_message_caption(caption=f"❌ CAPTCHA failed. Please try /explore again.")
            except Exception:
                pass
            # Ban user for failing captcha
            if db is not None and user_id:
                await db["bans"].update_one(
                    {"user_id": user_id},
                    {"$set": {"user_id": user_id, "expiry": None, "reason": "Captcha Failed", "banned_by": user_id, "banned_at": int(time.time())}},
                    upsert=True
                )
            # Log to channel
            msg = (
                f"<b>#CaptchaTimeout</b>\n\n"
                f"<b>User</b> : <a href=\"tg://user?id={user_id}\">{update.effective_user.first_name}</a>\n"
                f"<b>ID</b> : <code>{user_id}</code>"
            )
            await context.bot.send_message(-1002873117075, msg, parse_mode=ParseMode.HTML)
            # Notify user
            if query.message is not None:
                await query.message.reply_text("You failed to solve captcha. You are banned.")
            context.user_data['verified'] = False
            context.user_data['captcha_active'] = False
            # Cancel timeout task
            timeout_task = context.user_data.get('captcha_timeout_task')
            if timeout_task:
                timeout_task.cancel()

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


