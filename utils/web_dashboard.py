from datetime import datetime
from flask import Flask, request, jsonify, redirect, render_template, Response, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.monitor import resource_monitor
from database.db import Database
import os
import logging
import time
import requests
import asyncio
import re
from urllib.parse import urlparse

# Initialize Flask app
app = Flask(__name__)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Get TOKEN from environment
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Separate paths for webhook and dashboard
WEBHOOK_PATH = "/webhook"
DASHBOARD_PATH = "/dashboard"
WEBHOOK_SECRET_PATH = f"/{TOKEN}" if TOKEN else "/webhook"

# Global variables to store URLs
PUBLIC_WEBHOOK_URL = None
PUBLIC_DASHBOARD_URL = None
DASHBOARD_URL = None  # Initialize at module level

def validate_url(url: str) -> bool:
    """Validate if the URL is properly formatted and accessible"""
    if not url:
        return False
    
    try:
        result = urlparse(url)
        # Check if scheme and netloc are present
        return all([result.scheme in ['http', 'https'], result.netloc])
    except Exception:
        return False

# Try importing pyngrok for local development, but don't fail if not available
try:
    from pyngrok import ngrok
    PYNGROK_AVAILABLE = True
except ImportError:
    PYNGROK_AVAILABLE = False

def get_ngrok_tunnel_url(max_retries=5, retry_delay=2):
    """Get ngrok tunnel URL with retries"""
    if not PYNGROK_AVAILABLE:
        logger.warning("pyngrok is not available - this function only works in development")
        return None
        
    for attempt in range(max_retries):
        try:
            tunnels = ngrok.get_tunnels()
            if tunnels:
                tunnel_url = tunnels[0].public_url
                if tunnel_url and validate_url(tunnel_url):
                    return tunnel_url
        except Exception as e:
            logger.error(f"Error getting ngrok tunnel (attempt {attempt + 1}): {e}")
        
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
    
    return None

def find_ngrok_path():
    """Find ngrok executable path"""
    if not PYNGROK_AVAILABLE:
        logger.warning("pyngrok is not available - this function only works in development")
        return None
        
    # Check environment variable first
    env_path = os.getenv("PYNGROK_NGROK_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    
    # Check common locations
    possible_paths = [
        "./ngrok.exe",  # Current directory
        os.path.expanduser("~/.ngrok2/ngrok.exe"),  # Default pyngrok location
        "C:/Program Files/ngrok/ngrok.exe",  # Common install location
        "C:/ngrok/ngrok.exe",  # Simple install location
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    return None

async def verify_dashboard_health(url, max_retries=3, retry_delay=2):
    """Verify the dashboard is healthy by making a request to the health endpoint"""
    if not validate_url(url):
        logger.warning(f"Invalid URL format: {url}")
        return False
        
    for attempt in range(max_retries):
        try:
            health_url = f"{url}/health"
            logger.info(f"Verifying dashboard health at: {health_url}")
            
            response = await asyncio.to_thread(
                lambda: requests.get(health_url, timeout=5)
            )
            
            if response.status_code == 200:
                health_data = response.json()
                if health_data.get('status') == 'healthy':
                    logger.info("Dashboard health check successful")
                    return True
                else:
                    logger.warning(f"Dashboard reports unhealthy status: {health_data}")
            else:
                logger.warning(f"Dashboard health check failed with status {response.status_code}")
        
        except Exception as e:
            logger.warning(f"Dashboard health verification failed (attempt {attempt + 1}/{max_retries}): {str(e)}")
        
        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)
    
    return False

# Initialize dashboard URL and ngrok setup
async def setup_ngrok_dashboard(max_retries=3, retry_delay=5):
    """Setup ngrok tunnel for dashboard access with retries"""
    global PUBLIC_DASHBOARD_URL, PUBLIC_WEBHOOK_URL, DASHBOARD_URL
    
    # 1. First ensure we have a clean state
    await cleanup_existing_tunnels()
    
    for attempt in range(max_retries):
        try:
            # Configure ngrok
            ngrok_path = find_ngrok_path()
            if ngrok_path:
                logger.info(f"🔍 Found ngrok at: {ngrok_path}")
                ngrok_token = os.getenv("NGROK_AUTH_TOKEN")
                if ngrok_token:
                    ngrok.set_auth_token(ngrok_token)
                    logger.info("🔐 ngrok auth token configured")
            
            # Start ngrok tunnel
            logger.info(f"🔄 Starting ngrok tunnel on port {PORT}... (attempt {attempt + 1}/{max_retries})")
            http_tunnel = ngrok.connect(str(PORT), "http")
            
            # Verify we have a valid tunnel URL
            if not http_tunnel or not http_tunnel.public_url:
                raise RuntimeError("Failed to get valid ngrok tunnel URL")
            
            tunnel_url = http_tunnel.public_url
            if not validate_url(tunnel_url):
                raise RuntimeError(f"Invalid ngrok tunnel URL format: {tunnel_url}")
                
            # Update global URL references
            base_url = tunnel_url.rstrip('/')
            PUBLIC_DASHBOARD_URL = f"{base_url}{DASHBOARD_PATH}"
            PUBLIC_WEBHOOK_URL = f"{base_url}{WEBHOOK_PATH}"
            DASHBOARD_URL = base_url
            
            # Use the set_dashboard_url function to ensure consistent updates
            set_dashboard_url(base_url)
            
            # Verify the URL is accessible
            is_healthy = await verify_dashboard_health(PUBLIC_DASHBOARD_URL)
            if not is_healthy:
                raise RuntimeError("Dashboard health check failed")
            
            logger.info(f"🌐 Dashboard is now publicly accessible at: {PUBLIC_DASHBOARD_URL}/dashboard")
            logger.info(f"📱 Mobile dashboard: {PUBLIC_DASHBOARD_URL}/m")
            logger.info(f"🔗 API endpoint: {PUBLIC_DASHBOARD_URL}/api/players")
            logger.info("✅ ngrok tunnel established successfully!")
            
            return PUBLIC_DASHBOARD_URL
            
        except Exception as e:
            error_msg = f"Failed to setup ngrok (attempt {attempt + 1}/{max_retries}): {str(e)}"
            logger.error(error_msg)
            
            # Clean up any partial tunnel creation
            await cleanup_existing_tunnels()
            
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                raise RuntimeError(f"Failed to setup ngrok after {max_retries} attempts: {str(e)}")
            

async def cleanup_existing_tunnels():
    """Clean up any existing ngrok tunnels"""
    try:
        tunnels = ngrok.get_tunnels()
        if tunnels:
            logger.info(f"Found {len(tunnels)} existing ngrok tunnels, cleaning up...")
            for tunnel in tunnels:
                try:
                    if tunnel.public_url:
                        logger.info(f"Closing tunnel: {tunnel.public_url}")
                        ngrok.disconnect(tunnel.public_url)
                        await asyncio.sleep(1)  # Brief pause between closures
                except Exception as e:
                    logger.warning(f"Error closing tunnel {tunnel.public_url}: {e}")
            logger.info("All existing tunnels closed")
        return True
    except Exception as e:
        logger.error(f"Error cleaning up existing tunnels: {str(e)}")
        return False




# Get owner IDs from environment or use defaults
OWNERS = {5956598856, 5845254367}

# Initialize dashboard URL holder
DASHBOARD_URL = None

async def log_startup_info():
    """Log important startup information"""
    global DASHBOARD_URL, PUBLIC_DASHBOARD_URL, PUBLIC_WEBHOOK_URL
    logger.info("🚀 Attack on Titan Bot Starting...")
    logger.info(f"📱 Token configured: {'✅' if TOKEN else '❌'}")
    logger.info(f"🌐 Webhook path: {WEBHOOK_SECRET_PATH}")
    
    try:
        # Setup ngrok dashboard
        await setup_ngrok_dashboard()
        logger.info(f"🌐 Base URL: {DASHBOARD_URL}")
        logger.info(f"🌐 Webhook URL: {PUBLIC_WEBHOOK_URL}")
        
        if PUBLIC_DASHBOARD_URL and not PUBLIC_DASHBOARD_URL.startswith("http://localhost"):
            logger.info(f"📱 Public Dashboard Access: {PUBLIC_DASHBOARD_URL}")
            logger.info(f"📱 Mobile Dashboard: {DASHBOARD_URL}/m")
    except Exception as e:
        logger.error(f"Dashboard setup failed: {str(e)}")
        logger.warning("Continuing without public dashboard URL")
        DASHBOARD_URL = "http://localhost:5000"
        PUBLIC_DASHBOARD_URL = f"{DASHBOARD_URL}{DASHBOARD_PATH}"
        PUBLIC_WEBHOOK_URL = f"{DASHBOARD_URL}{WEBHOOK_PATH}"
    
    logger.info("📋 Loaded modules:")
    logger.info("   ├── Database System")
    logger.info("   ├── Character System") 
    logger.info("   ├── Exploration System")
    logger.info("   └── Callback Handlers")


def get_dashboard_url():
    """Get the correct dashboard URL (public ngrok or local)"""
    if PUBLIC_DASHBOARD_URL and not PUBLIC_DASHBOARD_URL.startswith("http://localhost"):
        logger.info(f"Using public dashboard URL: {PUBLIC_DASHBOARD_URL}")
        return PUBLIC_DASHBOARD_URL
    else:
        logger.warning("No public dashboard URL available (localhost cannot be used in Telegram buttons)")
        return None  # Return None when only localhost is available

async def owner_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monitor system stats - Owner only"""
    if not update.effective_user or not update.message:
        return
        
    user = update.effective_user
    message = update.message 

    # Silent ignore if not owner
    if user.id not in OWNERS:
        return

    try:
        if not resource_monitor:
            await message.reply_text("❌ Monitor system not available")
            return
            
        # Get live formatted status with player details
        live_status = resource_monitor.get_formatted_live_status()
        
        # Get the correct dashboard URL (ngrok or local)
        dashboard_url = get_dashboard_url()
        
        # Add dashboard link button only if we have a valid public URL
        if dashboard_url:
            keyboard = [[InlineKeyboardButton("🌐 Open Web Dashboard", url=dashboard_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await message.reply_text(live_status, parse_mode="HTML", reply_markup=reply_markup)
        else:
            # No public URL available, send message without button
            live_status += f"\n⚠️ <i>Dashboard only available locally at http://localhost:5000/dashboard</i>"
            await message.reply_text(live_status, parse_mode="HTML")
        
    except Exception as e:
        await message.reply_text(f"❌ Monitor error: {e}")


# ROUTES FOR OWNER COMMANDS
async def owner_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force cleanup system memory - Owner only"""
    if not update.effective_user or not update.message:
        return
        
    user = update.effective_user
    message = update.message 

    # Silent ignore if not owner
    if user.id not in OWNERS:
        return

    try:
        # Import cleanup functions
        from game.explore import force_cleanup_user, cleanup_stale_explore_records, active_battles
        from utils.monitor import cleanup_stale_activity, get_system_health_stats
        
        # Get health stats before cleanup
        before_stats = get_system_health_stats()
        
        # Perform cleanup
        cleanup_stale_explore_records(1)  # Clean records older than 1 hour
        cleanup_stale_activity(5)  # Clean activity older than 5 minutes
        
        # Get health stats after cleanup
        after_stats = get_system_health_stats()
        
        # Force cleanup specific user if provided
        if context.args and len(context.args) > 0:
            try:
                target_id = int(context.args[0])
                force_cleanup_user(target_id)
                cleanup_msg = f"\n🧹 Force cleaned user {target_id}"
            except ValueError:
                cleanup_msg = "\n❌ Invalid user ID provided"
        else:
            cleanup_msg = ""
        
        status_msg = (
            f"🧹 <b>System Cleanup Complete</b>\n\n"
            f"<b>Before:</b>\n"
            f"Memory: {before_stats.get('memory_mb', 0)}MB\n"
            f"Active Battles: {before_stats.get('active_battles', 0)}\n"
            f"Explore Records: {before_stats.get('explore_records', 0)}\n"
            f"Activity Records: {before_stats.get('activity_records', 0)}\n\n"
            f"<b>After:</b>\n"
            f"Memory: {after_stats.get('memory_mb', 0)}MB\n"
            f"Active Battles: {after_stats.get('active_battles', 0)}\n"
            f"Explore Records: {after_stats.get('explore_records', 0)}\n"
            f"Activity Records: {after_stats.get('activity_records', 0)}\n"
            f"{cleanup_msg}\n\n"
            f"Health: {after_stats.get('health_status', 'unknown')}"
        )
        
        if after_stats.get('warnings'):
            status_msg += f"\n⚠️ Warnings: {', '.join(after_stats['warnings'])}"
        
        await message.reply_text(status_msg, parse_mode="HTML")
        
    except Exception as e:
        await message.reply_text(f"❌ Cleanup error: {e}")

async def owner_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check system health - Owner only"""
    if not update.effective_user or not update.message:
        return
        
    user = update.effective_user
    message = update.message 

    # Silent ignore if not owner
    if user.id not in OWNERS:
        return

    try:
        from utils.monitor import get_system_health_stats
        
        health = get_system_health_stats()
        
        status_icon = "✅" if health['health_status'] == "healthy" else "⚠️" if health['health_status'] == "warning" else "❌"
        
        health_msg = (
            f"{status_icon} <b>System Health Check</b>\n\n"
            f"💾 <b>Memory:</b> {health.get('memory_mb', 0)}MB\n"
            f"⚔️ <b>Active Battles:</b> {health.get('active_battles', 0)}\n"
            f"🗺️ <b>Explore Records:</b> {health.get('explore_records', 0)}\n"
            f"📱 <b>Activity Records:</b> {health.get('activity_records', 0)}\n\n"
            f"🏥 <b>Status:</b> {health['health_status'].title()}\n"
            f"🕐 <b>Checked:</b> {health['timestamp'][:19]}"
        )
        
        if health.get('warnings'):
            health_msg += f"\n\n⚠️ <b>Warnings:</b>\n" + "\n".join(f"• {w}" for w in health['warnings'])
        
        await message.reply_text(health_msg, parse_mode="HTML")
        
    except Exception as e:
        await message.reply_text(f"❌ Health check error: {e}")


# Routes for Flask
@app.route("/")
def home():
    return "Bot is running!"

@app.route("/health")
async def health_check():
    """Health check endpoint that returns valid JSON"""
    try:
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "service": "AOT Bot Dashboard"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route("/monitor")
def monitor_stats():
    """Get detailed monitoring statistics"""
    try:
        if resource_monitor:
            live_players = resource_monitor.get_live_player_stats()
            return jsonify({"live_players": live_players})
        else:
            return jsonify({"error": "Monitor not available"}), 503
    except Exception as e:
        logger.error(f"Monitor stats failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/dashboard")
def live_dashboard():
    """Live dashboard endpoint"""
    try:
        if resource_monitor:
            stats = resource_monitor.get_formatted_live_status()
            return render_template('dashboard.html', stats=stats)
        return jsonify({
            "error": "Resource monitor not initialized",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route("/m")
def mobile_dashboard():
    """Mobile-friendly dashboard redirect"""
    try:
        return render_template('mobile.html')
    except Exception as e:
        return jsonify({
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route("/api/players")
def api_players():
    """API endpoint for player data"""
    try:
        if not resource_monitor:
            return jsonify({
                "error": "Resource monitor not initialized",
                "timestamp": datetime.now().isoformat()
            }), 503
            
        stats = resource_monitor.get_live_player_stats()
        return jsonify({
            "status": "success",
            "data": stats,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

# Global variable to store the application
application = None

def set_application(app):
    """Set the telegram application instance for webhook handling"""
    global application
    application = app

@app.post(WEBHOOK_SECRET_PATH)
async def webhook():
    try:
        if not application:
            logger.error("Application not set for webhook handling")
            return "Application not configured", 500
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
        return "OK"
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "Error", 500


@app.route("/mobile")
def mobile_redirect():
    """Quick mobile access redirect"""
    return redirect("/dashboard")

# Function to update the dashboard URL
def set_dashboard_url(url: str):
    """Update the public dashboard URL"""
    global PUBLIC_DASHBOARD_URL
    PUBLIC_DASHBOARD_URL = url
    logger.info(f"Dashboard URL updated to: {url}")
