# Project Attack On Titan Theme Game Bot

## Setup Instructions

### Local Development (VS Code)

1. **Clone the repository** and navigate to the project directory.# ⚔️ Attack on Titan — Telegram Game Bot

<p align="center">
  <b>A feature-rich RPG game bot inspired by the world of Attack on Titan.</b>
</p>

<p align="center">
  <a href="https://t.me/YOUR_BOT_USERNAME">
    <img src="https://img.shields.io/badge/⚔️%20PLAY%20NOW-Start%20Game-red?style=for-the-badge&logo=telegram" alt="Play Now">
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python">
  <img src="https://img.shields.io/badge/MongoDB-Database-green?logo=mongodb">
  <img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram">
</p>

---

## 🎮 About

**Attack on Titan Game Bot** is a Telegram-based RPG where players can create their character, explore the world, fight Titans, complete missions, unlock abilities, and progress through the game.

The project is built with a **modular architecture** and uses MongoDB for persistent player and game data.

### ✨ Key Features

* ⚔️ **Battle System** — Fight Titans using character stats and abilities.
* 🧑‍🎮 **Character Progression** — Level up and develop your character.
* 🧟 **Titan Encounters** — Face different types of Titans and challenging battles.
* 🗺️ **Exploration** — Explore areas and encounter random events, missions, and rewards.
* 🧠 **Ability System** — Support for active and passive character abilities.
* 🎯 **Missions & Quests** — Complete objectives and progress through the game.
* 💾 **MongoDB Database** — Securely store player and game progression.
* 📊 **Web Dashboard** — Monitor bot and game activity.
* 🧪 **Safe Testing Mode** — Separate test environment and database.

---

## 🛠️ Tech Stack

**Python** · **python-telegram-bot** · **MongoDB** · **Render** · **GitHub**

---

## 🚀 Run Locally

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY

pip install -r requirements.txt
```

### 2. Configure `.env`

Create `.env` from `.env.example` and add:

```env
ENV=development
TELEGRAM_TOKEN=your_bot_token
MONGODB_URI=your_mongodb_uri
DB_NAME=attackontitan
```

### 3. Start the Bot

```bash
python main.py
```

The bot runs in **polling mode** during local development.

---

## 🧪 Safe Testing

Testing is isolated from production data.

```bash
python bot.py
```

Test specific commands:

```bash
python bot.py /start
```

Run all tests:

```bash
python bot.py all
```

The test environment uses a separate:

```text
attackontitan_test
```

database to protect production data.

---

## 🌐 Production

The bot supports **Render deployment** with Telegram webhooks.

Production environment:

```env
ENV=production
TELEGRAM_TOKEN=your_production_token
WEBHOOK_URL=https://your-app.onrender.com/webhook
MONGODB_URI=your_mongodb_uri
DB_NAME=attackontitan
```

---

## 📁 Project Structure

```text
Attack-On-Titan/
├── main.py
├── bot.py
├── game/
├── handlers/
├── database/
├── dashboard/
├── tasks/
├── tests/
├── requirements.txt
└── .env.example
```

---

## ⚔️ Start Your Journey

<p align="center">
  <a href="https://t.me/YOUR_BOT_USERNAME">
    <img src="https://img.shields.io/badge/⚔️%20ENTER%20THE%20WALLS-PLAY%20NOW-red?style=for-the-badge&logo=telegram" alt="Play Game">
  </a>
</p>

<p align="center">
  <i>Fight Titans. Master your abilities. Become a legend.</i>
</p>

---

<p align="center">
  Built with ❤️ using Python & MongoDB
</p>


2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Configuration**:
   - Copy `.env.example` to `.env`
   - Fill in your Telegram bot token and other configurations
   - Set `ENV=development` for local testing

4. **Run the bot locally**:
   ```bash
   python main.py
   ```
   The bot will run in polling mode for local development.

### Testing the Bot (Safe Testing Mode)

For safe testing without affecting production data:

1. **Use the test bot file**:
   ```bash
   python bot.py
   ```

2. **Test Configuration**:
   - The test bot automatically uses `TEST_MODE=true`
   - Uses a separate test database (`attackontitan_test`)
   - Loads token from `TEST_BOT_TOKEN` in `.env`
   - Mock responses prevent real Telegram API calls

3. **Test Commands**:
   - Interactive mode: `python bot.py`
   - Test specific command: `python bot.py /start`
   - Test callback: `python bot.py callback:start_journey`
   - Run all tests: `python bot.py all`

4. **Data Safety**:
   - No changes committed to production database
   - Test database is isolated from production
   - Sample data is copied from production for testing

### Production Deployment (Render)

1. **Deploy to Render**:
   - Connect your GitHub repository to Render
   - Set up a web service with Python environment

2. **Environment Variables on Render**:
   - `ENV=production`
   - `TELEGRAM_TOKEN=your_bot_token`
   - `WEBHOOK_URL=https://your-render-app.onrender.com/webhook` (replace with your actual Render URL)
   - `PORT=10000` (or Render's default)
   - `MONGODB_URI=your_mongodb_connection_string`
   - `DB_NAME=attackontitan`

3. **Webhook Setup**:
   - The bot will automatically set up webhooks when `ENV=production`
   - Make sure your Render URL is set correctly in the code

## Features

- Telegram bot with game mechanics
- Database integration with MongoDB
- Web dashboard for monitoring
- Scheduled tasks and maintenance

## Notes

- The `.env` file is ignored by Git for security
- Use `.env` for local development only
- Production environment variables should be set in your deployment platform
