# config.py
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Global configuration
PORT = 8080  
DEBUG = False  
# Flag to enable/disable captcha (temporarily disabled)
ENABLE_CAPTCHA = False
ENABLE_HCAPTCHA = False

# Database configuration
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")  # Updated to match .env file
DB_NAME = os.getenv("DB_NAME", "attackontitan")  # Default database name
MAX_CONNECTION_RETRIES = 3  # Number of times to retry database connection
CONNECTION_TIMEOUT = 5000  # 5 seconds timeout for database operations

# Channel IDs
TRANSACTION_LOG_CHANNEL = -1002686338026  # Transaction logs channel ID
# Broadcast settings
# Maximum length for a custom vote option label. Telegram button text is limited (recommend <=64).
BROADCAST_OPTION_MAX_LENGTH = int(os.getenv("BROADCAST_OPTION_MAX_LENGTH", 64))