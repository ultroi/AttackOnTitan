# # import random
# # import string
# # from PIL import Image, ImageDraw, ImageFont, ImageFilter
# # from io import BytesIO
# # from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
# # from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# # def generate_captcha():
# #     captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
# #     image = Image.new('RGB', (150, 50), color=(255, 255, 255))
# #     draw = ImageDraw.Draw(image)
    
# #     try:
# #         font = ImageFont.truetype("arial.ttf", 24)
# #     except:
# #         font = ImageFont.load_default()
    
# #     x = 10
# #     for i, char in enumerate(captcha_text):
# #         color = (random.randint(0, 100), random.randint(0, 100), random.randint(0, 100))
# #         y = random.randint(0, 15)
# #         char_image = Image.new('RGBA', (30, 30))
# #         char_draw = ImageDraw.Draw(char_image)
# #         char_draw.text((0, 0), char, fill=color, font=font)
# #         char_image = char_image.rotate(random.randint(-30, 30), expand=1, fillcolor=(255, 255, 255, 0))
# #         image.paste(char_image, (x, y), char_image)
# #         x += 25 + random.randint(-5, 5)
    
# #     for _ in range(8):
# #         color = (random.randint(0, 200), random.randint(0, 200), random.randint(0, 200))
# #         x1, y1, x2, y2 = random.randint(0, 150), random.randint(0, 50), random.randint(0, 150), random.randint(0, 50)
# #         draw.line((x1, y1, x2, y2), fill=color, width=1)
# #         if random.choice([True, False]):
# #             for i in range(1, 10):
# #                 draw.line(
# #                     (
# #                         x1 + (x2 - x1)*i/10 + random.randint(-3,3),
# #                         y1 + (y2 - y1)*i/10 + random.randint(-3,3),
# #                         x1 + (x2 - x1)*(i-1)/10 + random.randint(-3,3),
# #                         y1 + (y2 - y1)*(i-1)/10 + random.randint(-3,3)
# #                     ),
# #                     fill=color, width=1
# #                 )
    
# #     for _ in range(800):
# #         color = (random.randint(0,255), random.randint(0,255), random.randint(0,255))
# #         x, y = random.randint(0,150), random.randint(0,50)
# #         draw.point((x,y), fill=color)

# #     image = image.filter(ImageFilter.GaussianBlur(radius=0.8))
    
# #     img_byte_arr = BytesIO()
# #     image.save(img_byte_arr, format='PNG')
# #     img_byte_arr.seek(0)
    
# #     return captcha_text, img_byte_arr

# # async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
# #     captcha_text, captcha_image = generate_captcha()
# #     context.user_data['captcha_answer'] = captcha_text

# #     options = [captcha_text]
# #     while len(options) < 9:
# #         random_option = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
# #         if random_option not in options:
# #             options.append(random_option)
# #     random.shuffle(options)

# #     # Arrange buttons 3 in a row
# #     keyboard = [
# #         [InlineKeyboardButton(opt, callback_data=opt) for opt in options[i:i+3]]
# #         for i in range(0, 9, 3)
# #     ]
# #     reply_markup = InlineKeyboardMarkup(keyboard)

# #     await update.message.reply_photo(
# #         photo=captcha_image,
# #         caption="Please select the correct CAPTCHA text:",
# #         reply_markup=reply_markup
# #     )

# # async def send_new_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
# #     """Helper to generate and send a new CAPTCHA when incorrect."""
# #     captcha_text, captcha_image = generate_captcha()
# #     context.user_data['captcha_answer'] = captcha_text

# #     options = [captcha_text]
# #     while len(options) < 9:
# #         random_option = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
# #         if random_option not in options:
# #             options.append(random_option)
# #     random.shuffle(options)

# #     keyboard = [
# #         [InlineKeyboardButton(opt, callback_data=opt) for opt in options[i:i+3]]
# #         for i in range(0, 9, 3)
# #     ]
# #     reply_markup = InlineKeyboardMarkup(keyboard)

# #     await update.effective_chat.send_photo(
# #         photo=captcha_image,
# #         caption="❌ Incorrect. Try again with a new CAPTCHA:",
# #         reply_markup=reply_markup
# #     )

# # async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
# #     query = update.callback_query
# #     user_answer = query.data
# #     correct_answer = context.user_data.get('captcha_answer','')

# #     if user_answer == correct_answer:
# #         await query.answer("Correct! You're verified.")
# #         await query.edit_message_caption(caption="✅ Correct! You're verified.")
# #     else:
# #         await query.answer("Incorrect! New CAPTCHA sent.")
# #         await query.edit_message_caption(caption=f"❌ Incorrect. The answer was: {correct_answer}")
# #         await send_new_captcha(update, context)


# # # Math operations with their string representations
# # MATH_OPERATIONS = {
# #     '+': {'func': operator.add, 'word': 'plus'},
# #     '-': {'func': operator.sub, 'word': 'minus'},
# #     '*': {'func': operator.mul, 'word': 'multiplied by'},
# #     '/': {'func': operator.truediv, 'word': 'divided by'}
# # }

# # # Different question formats
# # QUESTION_FORMATS = [
# #     lambda a, op, b: f"What is {a} {op} {b}?",
# #     lambda a, op, b: f"Calculate: {a} {op} {b}",
# #     lambda a, op, b: f"{a} {op} {b} = ?",
# #     lambda a, op, b: f"Solve: {a} {op} {b}",
# #     lambda a, op, b: f"Please compute {a} {op} {b}",
# #     lambda a, op, b: f"The result of {a} {op} {b} is?",
# #     lambda a, op, b: f"Add these numbers: {a} and {b}" if op == '+' else None,
# #     lambda a, op, b: f"Subtract {b} from {a}" if op == '-' else None,
# #     lambda a, op, b: f"Product of {a} and {b}" if op == '*' else None,
# #     lambda a, op, b: f"{a} {MATH_OPERATIONS[op]['word']} {b} equals?"
# # ]

# # def generate_math_captcha():
# #     # Generate random numbers and operation
# #     a = random.randint(1, 20)
# #     b = random.randint(1, 20)
# #     op = random.choice(list(MATH_OPERATIONS.keys()))
    
# #     # Ensure division problems have integer results
# #     if op == '/':
# #         a = a * b
    
# #     # Calculate correct answer
# #     answer = MATH_OPERATIONS[op]['func'](a, b)
    
# #     # Select a random question format that works for this operation
# #     valid_formats = [f for f in QUESTION_FORMATS if f(a, op, b) is not None]
# #     question = random.choice(valid_formats)(a, op, b)
    
# #     return question, str(answer)



# import random
# import asyncio
# from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
# from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# # CAPTCHA 1: Emoji Math
# async def emoji_math(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     emoji_numbers = ["0️⃣","1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
#     a = random.randint(1,9)
#     b = random.randint(1,9)
#     op = random.choice(["×","÷","+"])
    
#     # Calculate answer
#     if op == "×":
#         answer = a * b
#     elif op == "÷":
#         # Ensure whole number division
#         a = a * b
#         answer = a // b
#     else:
#         answer = a + b
    
#     # Format question with proper spacing
#     question = f"🔢 Solve this emoji math:\n\n{emoji_numbers[a]} {op} {emoji_numbers[b]} = ?\n\n"
    
#     # Create number pad with 3x3 grid + 0 at bottom
#     buttons = [
#         [InlineKeyboardButton(str(i), callback_data=f"math_{i}") for i in range(1,4)],
#         [InlineKeyboardButton(str(i), callback_data=f"math_{i}") for i in range(4,7)],
#         [InlineKeyboardButton(str(i), callback_data=f"math_{i}") for i in range(7,10)],
#         [InlineKeyboardButton("0", callback_data="math_0")]
#     ]
    
#     # Add timestamp to prevent replay attacks
#     timestamp = int(time.time())
#     context.user_data['captcha'] = {
#         'type': 'math',
#         'answer': str(answer),
#         'expires': timestamp + 120  # 2 minutes expiry
#     }
    
#     await update.message.reply_text(
#         question,
#         reply_markup=InlineKeyboardMarkup(buttons)
#     )

# # CAPTCHA 2: Color Word
# async def color_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     colors = {"🔴":"RED","🟢":"GREEN","🔵":"BLUE","🟡":"YELLOW"}
#     color_icon, color_name = random.choice(list(colors.items()))
    
#     question = f"🎨 Type the color name for: {color_icon}"
#     buttons = []
#     for color in colors.values():
#         buttons.append([InlineKeyboardButton(color, callback_data=f"captcha_{color}")])
    
#     await update.message.reply_text(
#         question,
#         reply_markup=InlineKeyboardMarkup(buttons)
#     )
#     context.user_data['captcha'] = {'type': 'color', 'answer': color_name}

# # CAPTCHA 3: Sequence Memory
# async def sequence_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     symbols = ["⭐","❤️","⚡","🎯","🔔"]
#     sequence = random.choices(symbols, k=3)
#     context.user_data['captcha'] = {'type': 'sequence', 'answer': sequence}
    
#     msg = await update.message.reply_text(
#         "🧠 Memorize this sequence:\n" + " ".join(sequence)
#     )
#     await asyncio.sleep(20)
#     await msg.delete()
    
#     shuffled = random.sample(symbols, len(symbols))
#     buttons = [[InlineKeyboardButton(s, callback_data=f"seq_{s}") for s in shuffled]]
#     await update.message.reply_text(
#         "Now tap the symbols in correct order:",
#         reply_markup=InlineKeyboardMarkup(buttons)
#     )

# # CAPTCHA 4: Orientation Puzzle
# async def orientation_puzzle(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     arrows = ["⬆️","➡️","⬇️","⬅️"]
#     correct = random.choice(arrows)
    
#     buttons = []
#     for arrow in arrows:
#         rotated = random.choice([0, 90, 180, 270])
#         buttons.append([InlineKeyboardButton(
#             text=f"🔄 {arrow}", 
#             callback_data=f"orient_{arrow}"
#         )])
    
#     await update.message.reply_text(
#         f"🧭 Tap the {correct} arrow",
#         reply_markup=InlineKeyboardMarkup(buttons)
#     )
#     context.user_data['captcha'] = {'type': 'orientation', 'answer': correct}

# # CAPTCHA 5: Mini Riddle
# async def mini_riddle(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     riddles = [
#         {"q":"What has keys but no locks?","a":"PIANO"},
#         {"q":"I'm tall when young, short when old. What am I?","a":"CANDLE"},
#         {"q":"What gets wetter as it dries?","a":"TOWEL"}
#     ]
#     riddle = random.choice(riddles)
    
#     buttons = [
#         [InlineKeyboardButton(riddle["a"], callback_data=f"riddle_{riddle['a']}")],
#         [InlineKeyboardButton("KEYBOARD", callback_data="riddle_WRONG")],
#         [InlineKeyboardButton("UMBRELLA", callback_data="riddle_WRONG")]
#     ]
#     random.shuffle(buttons)
    
#     await update.message.reply_text(
#         f"🤔 Riddle:\n{riddle['q']}",
#         reply_markup=InlineKeyboardMarkup(buttons)
#     )
#     context.user_data['captcha'] = {'type': 'riddle', 'answer': riddle["a"]}

# # Main CAPTCHA handler
# async def start_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     captcha_types = [emoji_math, color_word, sequence_memory, orientation_puzzle, mini_riddle]
#     await random.choice(captcha_types)(update, context)

# # Verification handler
# async def verify_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.callback_query
#     user_data = context.user_data.get('captcha', {})
    
#     if not user_data:
#         await query.answer("Session expired. Start again with /start")
#         return
    
#     user_answer = query.data.split('_')[1]
#     captcha_type = user_data['type']
#     correct_answer = user_data['answer']
    
#     # Special handling for sequence CAPTCHA
#     if captcha_type == 'sequence':
#         if 'user_sequence' not in user_data:
#             user_data['user_sequence'] = []
#         user_data['user_sequence'].append(user_answer)
        
#         if len(user_data['user_sequence']) < len(correct_answer):
#             await query.answer(f"Selected: {user_answer}")
#             return
#         else:
#             is_correct = user_data['user_sequence'] == correct_answer
#     else:
#         is_correct = user_answer == correct_answer
    
#     if is_correct:
#         await query.answer("✅ Verification successful!")
#         await query.edit_message_text("Nice Solved  Captcha!!")
#         context.user_data['verified'] = True
#     else:
#         await query.answer("❌ Wrong answer!")
#         await start_captcha(update, context)

# # Game command (after verification)
# async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if context.user_data.get('verified'):
#         await update.message.reply_text("🎮 Game started! ...")
#     else:
#         await update.message.reply_text("Please complete verification first:")
#         await start_captcha(update, context)

# def main():
#     app = Application.builder().token("7667322334:AAFaoSzzTQyK2ujik5ejQiGS_BkfUec90J4").build()
    
#     app.add_handler(CommandHandler("start", start_captcha))
#     app.add_handler(CommandHandler("game", start_game))
#     app.add_handler(CallbackQueryHandler(verify_captcha, pattern="^(captcha|seq|orient|riddle)_"))
    
#     app.run_polling()

# if __name__ == "__main__":
#     main()
