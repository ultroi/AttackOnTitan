# config.py
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Global configuration
PORT = 8080  
DEBUG = False  

# Database configuration
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")  # Updated to match .env file
DB_NAME = os.getenv("DB_NAME", "attackontitan")  # Default database name
MAX_CONNECTION_RETRIES = 3  # Number of times to retry database connection
CONNECTION_TIMEOUT = 5000  # 5 seconds timeout for database operations