# Project Attack On Titan Theme Game Bot

## Setup Instructions

### Local Development (VS Code)

1. **Clone the repository** and navigate to the project directory.

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