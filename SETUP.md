# Attack on Titan Game Bot Setup Guide

## Prerequisites
- Python 3.8 or higher
- MongoDB Atlas account
- Telegram Bot Token (from BotFather)

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd attack-on-titan-bot
```

2. Create a virtual environment and activate it:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up MongoDB Atlas:
   - Create a MongoDB Atlas account at https://www.mongodb.com/cloud/atlas
   - Create a new cluster (free tier is sufficient)
   - Click "Connect" on your cluster
   - Choose "Connect your application"
   - Copy the connection string
   - Replace `<password>` with your database user's password
   - Add your IP address to the IP whitelist in Network Access

5. Create a `.env` file in the project root with the following content:
```
TELEGRAM_TOKEN=your_telegram_bot_token_here
MONGODB_URI=your_mongodb_atlas_connection_string_here
```

6. Run the bot:
```bash
python bot.py
```

## Available Commands

- `/start` - Start the game or view welcome message
- `/create` - Create a new character
- `/profile` - View your character profile
- `/explore` - Start exploring for titans

## Game Features

1. Character Creation
   - Choose from three character types
   - Select birthplace with unique stat bonuses
   - Customize your character's appearance

2. Combat System
   - Turn-based battles
   - Multiple attack options
   - Resource management (gas, blades)
   - XP and currency rewards

3. Progression System
   - Level up through battles
   - Rank advancement
   - Skill trees
   - Equipment upgrades

4. Exploration
   - Dynamic world map
   - Different areas to explore
   - Various titan types
   - Special events and missions

## Database Structure

The game uses MongoDB Atlas with the following collections:
- `players` - Player account information
- `characters` - Character data and stats
- `titans` - Titan types and attributes
- `equipment` - Available equipment and items

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 