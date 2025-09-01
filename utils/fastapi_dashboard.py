from fastapi import Request, Form, HTTPException, Depends, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import APIKeyCookie
from typing import Optional
from database.db_instance import get_database
import time
import os
import json
from datetime import datetime
from typing import Optional, Dict, List
import logging
import secrets

import random
import string

from starlette.status import HTTP_303_SEE_OTHER
from telegram import Bot
import httpx
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a file handler for security logs
os.makedirs("logs", exist_ok=True)
security_log_file = os.path.join("logs", "dashboard_access.log")
security_handler = logging.FileHandler(security_log_file)
security_handler.setLevel(logging.INFO)
security_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
security_handler.setFormatter(security_formatter)
logger.addHandler(security_handler)

# Set up Jinja2 templates
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), '../templates'))

HCAPTCHA_TIMEOUT = 600 
BAN_LOG_CHAT_ID = -1002873117075

SESSION_COOKIE = "aot_dashboard_session"
VERIFICATION_CODE_EXPIRY = 300  # 5 minutes
ADMIN_GROUP_ID = -1002463105932
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
verification_codes = {}  # {code: (timestamp, ip)}

# MongoDB session helpers
import asyncio

async def save_dashboard_session(session_id: str, user_id: int, ip_address: str, expiry: float, created_at: float, last_activity: float):
    db = await get_database()
    if db is None:
        return
    await db["dashboard_sessions"].update_one(
        {"session_id": session_id},
        {"$set": {"user_id": user_id, "ip_address": ip_address, "expiry": expiry, "created_at": created_at, "last_activity": last_activity}},
        upsert=True
    )

async def get_dashboard_session(session_id: str):
    db = await get_database()
    if db is None:
        return None
    return await db["dashboard_sessions"].find_one({"session_id": session_id})

async def delete_dashboard_session(session_id: str):
    db = await get_database()
    if db is None:
        return
    await db["dashboard_sessions"].delete_one({"session_id": session_id})

# Access log tracking
dashboard_access_log: List[Dict] = []  # Keeps track of recent dashboard accesses

def log_dashboard_access(user_id: int, action: str, ip_address: str, details: Optional[Dict] = None):
    """Log dashboard access attempt"""
    timestamp = datetime.now().isoformat()
    log_entry = {
        "timestamp": timestamp,
        "user_id": user_id,
        "action": action,  # login, access, logout, etc.
        "ip_address": ip_address,
        "details": details or {}
    }
    
    # Save to memory log (recent entries)
    dashboard_access_log.append(log_entry)
    if len(dashboard_access_log) > 100:  # Keep only last 100 entries in memory
        dashboard_access_log.pop(0)
    
    # Log to security log file
    logger.info(f"DASHBOARD_ACCESS: user_id={user_id}, action={action}, ip={ip_address}, details={json.dumps(details or {})}")
    
    # For critical actions, could also notify owners via Telegram

# In-memory session cache
active_sessions = {}  # {session_id: {"user_id": user_id, "ip_address": ip, "expiry": expiry, "created_at": timestamp, "last_activity": timestamp}}

# Session management
def create_session(user_id: int, ip_address: str = "unknown") -> str:
    """Create a new session for authenticated user"""
    session_id = secrets.token_hex(16)
    timestamp = time.time()
    expiry = timestamp + 3600
    # Store in memory cache
    active_sessions[session_id] = {
        "user_id": user_id,
        "ip_address": ip_address,
        "expiry": expiry,
        "created_at": timestamp,
        "last_activity": timestamp
    }
    # Also save to database
    asyncio.create_task(save_dashboard_session(session_id, user_id, ip_address, expiry, timestamp, timestamp))
    log_dashboard_access(
        user_id=user_id,
        action="login",
        ip_address=ip_address,
        details={"session_id": session_id}
    )
    return session_id

async def verify_session(session: Optional[str] = Cookie(None)) -> Optional[int]:
    """Verify user session, return user_id if valid or None if invalid"""
    if not session:
        return None
    
    # First check in-memory active_sessions
    if session in active_sessions:
        session_data = active_sessions[session]
        current_time = time.time()
        
        if session_data["expiry"] < current_time:
            # Remove expired session from memory
            del active_sessions[session]
            # Also remove from database
            await delete_dashboard_session(session)
            return None
            
        # Extend session and update last activity
        new_expiry = current_time + 3600
        session_data["expiry"] = new_expiry
        session_data["last_activity"] = current_time
        
        # Also update in database asynchronously
        asyncio.create_task(save_dashboard_session(
            session, 
            session_data["user_id"], 
            session_data.get("ip_address", "unknown"), 
            new_expiry, 
            session_data.get("created_at", current_time), 
            current_time
        ))
        
        return session_data["user_id"]
    
    # If not found in memory, try database
    session_data = await get_dashboard_session(session)
    if not session_data:
        return None
        
    current_time = time.time()
    if session_data["expiry"] < current_time:
        await delete_dashboard_session(session)
        return None
        
    # Extend session, update last activity and restore to memory
    new_expiry = current_time + 3600
    await save_dashboard_session(
        session, 
        session_data["user_id"], 
        session_data.get("ip_address", "unknown"), 
        new_expiry, 
        session_data.get("created_at", current_time), 
        current_time
    )
    
    # Also add to in-memory cache
    active_sessions[session] = {
        "user_id": session_data["user_id"],
        "ip_address": session_data.get("ip_address", "unknown"),
        "expiry": new_expiry,
        "created_at": session_data.get("created_at", current_time),
        "last_activity": current_time
    }
    
    return session_data["user_id"]

async def track_session_activity(session: str, request: Request) -> None:
    """Track session activity for logging purposes"""
    session_data = await get_dashboard_session(session)
    if session_data:
        current_time = time.time()
        client_ip = "unknown"
        path = "unknown"
        if request:
            if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
                client_ip = request.client.host
            if hasattr(request, 'url') and request.url and hasattr(request.url, 'path'):
                path = request.url.path
        # Track activity every 5 minutes
        last_logged = session_data.get("last_logged", 0)
        if current_time - last_logged > 300:
            log_dashboard_access(
                user_id=session_data["user_id"],
                action="access",
                ip_address=client_ip,
                details={"path": path}
            )
            await save_dashboard_session(session, session_data["user_id"], client_ip, session_data["expiry"], session_data.get("created_at", current_time), current_time)

async def is_authorized(user_id: int) -> bool:
    """Check if user is authorized (owner or mod)"""
    if user_id in get_owner_ids():
        return True
    return await is_mod(user_id)

def include_dashboard_route(app):
    @app.get("/error", response_class=HTMLResponse)
    async def error_page(request: Request, error: str = "An error occurred"):
        return templates.TemplateResponse("error.html", {"request": request, "error": error})

    @app.get("/already_verified", response_class=HTMLResponse)
    async def already_verified_page(request: Request, user_id: str = ""):
        return templates.TemplateResponse("already_verified.html", {"request": request, "user_id": user_id})

    @app.get("/verification_success", response_class=HTMLResponse)
    async def verification_success_page(request: Request):
        return templates.TemplateResponse("verification_success.html", {"request": request})

    @app.get("/hcaptcha_timeout", response_class=HTMLResponse)
    async def hcaptcha_timeout(request: Request):
        return templates.TemplateResponse("hcaptcha_timeout.html", {"request": request})
        
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: Optional[str] = None):
        """Automatically redirect to dashboard without login"""
        # Simply redirect to dashboard without any verification
        return RedirectResponse("/dashboard", status_code=HTTP_303_SEE_OTHER)

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(request: Request):
        """Automatically redirect to dashboard without verification"""
        # Get client IP
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host
            
        # Create a new session using our session management function
        user_id = 123456789  # Default admin user ID
        session_id = create_session(user_id, ip_address=client_ip)
        
        # Create a redirect response to the dashboard
        response = RedirectResponse("/dashboard", status_code=HTTP_303_SEE_OTHER)
        response.set_cookie(key=SESSION_COOKIE, value=session_id, httponly=True, max_age=3600)
        return response
    
    @app.get("/logout")
    async def logout(request: Request, response: Response, session: Optional[str] = Cookie(None)):
        """Log out and redirect back to dashboard instead of login page"""
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host
        
        # Clean up session if provided
        if session:
            # Remove from in-memory cache if exists
            if session in active_sessions:
                user_id = active_sessions[session].get("user_id", 123456789)
                del active_sessions[session]
            else:
                user_id = 123456789
                
            # Also remove from database
            await delete_dashboard_session(session)
            
            # Log the logout action
            log_dashboard_access(
                user_id=user_id,
                action="logout",
                ip_address=client_ip,
                details={"session_id": session}
            )
        else:
            # Log the action but don't actually require login to access dashboard
            log_dashboard_access(
                user_id=123456789,
                action="logout_redirect",
                ip_address=client_ip,
                details={"direct_access": True}
            )
        
        # Redirect directly back to dashboard instead of login
        response = RedirectResponse("/dashboard", status_code=HTTP_303_SEE_OTHER)
        return response
            
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request, session: Optional[str] = Cookie(None)):
        """Render dashboard page without authentication"""
        # Get client IP
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host

        # Set default admin user ID for direct access without authentication
        user_id = 123456789  # Default admin user ID
        
        # Log the access for monitoring purposes
        log_dashboard_access(
            user_id=user_id,
            action="dashboard_view",
            ip_address=client_ip,
            details={"direct_access": True}
        )
        
        # Always set user role as Owner for full access
        user_role = "Owner"
        
        # Return the dashboard template directly without authentication
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user_id": user_id,
                "user_role": user_role,
                "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        )
    
    @app.get("/access_logs", response_class=HTMLResponse)
    async def access_logs_page(request: Request):
        """View access logs page without authentication"""
        # Get client IP
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host
        
        # Set default admin user ID for direct access
        user_id = 123456789
        
        # Track access logs page view
        log_dashboard_access(
            user_id=user_id,
            action="logs_page_view",
            ip_address=client_ip,
            details={"direct_access": True}
        )
        
        return templates.TemplateResponse("access_logs.html", {"request": request, "user_id": user_id})
        
    @app.get("/access_logs_data")
    async def access_logs_data(request: Request):
        """API endpoint for access logs data without authentication"""
        # Return the logs data without any authentication checks
        return {
            "status": "success",
            "logs": dashboard_access_log
        }

    @app.get("/hcaptcha", response_class=HTMLResponse)
    async def hcaptcha_page(
        request: Request,
        user_id: str,
        error: Optional[str] = None
    ):
        """Render hCaptcha verification page with user context."""
        db = await get_database()
        if db is None:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "error": "Database unavailable"}
            )

        if not user_id:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "error": "User ID required"}
            )

        player = await db["players"].find_one({"user_id": str(user_id)})
        now = int(time.time())

        # Check if already verified
        if player and getattr(player, "hcaptcha_verified", False):
            start_time = getattr(player, "hcaptcha_start_time", None)
            # Add None check before comparison
            if start_time is not None and now - start_time <= HCAPTCHA_TIMEOUT:
                return templates.TemplateResponse(
                    "already_verified.html",
                    {"request": request, "user_id": user_id}
                )

        if not player or not getattr(player, "hcaptcha_start_time", None):
            # This is the *first* time captcha is being prompted, set the timer
            await db["players"].update_one(
                {"user_id": str(user_id)},
                {
                    "$set": {
                        "hcaptcha_start_time": now,
                        "hcaptcha_verified": False
                    }
                },
                upsert=True
            )
        else:
            # Do not reset the timer if expired
            if now - getattr(player, "hcaptcha_start_time", 0) > HCAPTCHA_TIMEOUT:
                # Ban and notify user if timeout
                await handle_verification_timeout(db, user_id, player)
                await db["players"].update_one(
                    {"user_id": str(user_id)},
                    {"$set": {"hcaptcha_verified": False}}
                )
                return templates.TemplateResponse(
                    "hcaptcha_timeout.html",
                    {"request": request}
                )


        return templates.TemplateResponse(
            "hcaptcha.html",
            {
                "request": request,
                "user_id": user_id,
                "error": error,
                "site_key": os.getenv("HCAPTCHA_SITE_KEY")
            }
        )

    @app.post("/verify_hcaptcha")
    async def verify_hcaptcha(
        request: Request,
        user_id: str = Form(...),
        h_captcha_response: str = Form(None)
    ):
        """Process hCaptcha verification."""
        db = await get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database unavailable")

        if not user_id:
            return RedirectResponse(f"/hcaptcha?error=User+ID+required")

        if h_captcha_response is None:
            form = await request.form()
            h_captcha_response = form.get("h-captcha-response")

        if not h_captcha_response:
            return RedirectResponse(
                f"/hcaptcha?user_id={user_id}&error=Captcha+response+missing"
            )

        player = await db["players"].find_one({"user_id": str(user_id)})
        now = int(time.time())
        start_time = getattr(player, "hcaptcha_start_time", now) if player else now

        # Check timeout
        if now - start_time > HCAPTCHA_TIMEOUT:
            # Ban user and prevent verification
            await handle_verification_timeout(db, user_id, player)
            # Also set hcaptcha_verified to False to prevent future verification
            await db["players"].update_one(
                {"user_id": str(user_id)},
                {"$set": {"hcaptcha_verified": False}}
            )
            return RedirectResponse("/hcaptcha_timeout", status_code=303)

        # Verify with hCaptcha API
        secret = os.getenv("HCAPTCHA_SECRET")
        if not secret:
            raise HTTPException(status_code=500, detail="Server configuration error")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://hcaptcha.com/siteverify",
                data={
                    "response": h_captcha_response,
                    "secret": secret
                },
                timeout=10.0
            )
        result = response.json()

        if not result.get("success"):
            return RedirectResponse(
                f"/hcaptcha?user_id={user_id}&error=Verification+failed"
            )

        # Successful verification
        try:
            # Double check timeout before updating (race condition prevention)
            player = await db["players"].find_one({"user_id": str(user_id)})
            start_time = getattr(player, "hcaptcha_start_time", now) if player else now
            if now - start_time > HCAPTCHA_TIMEOUT:
                await handle_verification_timeout(db, user_id, player)
                await db["players"].update_one(
                    {"user_id": str(user_id)},
                    {"$set": {"hcaptcha_verified": False}}
                )
                return RedirectResponse("/hcaptcha_timeout", status_code=303)

            # Important: Update database with verification status FIRST
            await db["players"].update_one(
                {"user_id": str(user_id)},
                {
                    "$set": {
                        "hcaptcha_verified": True,
                        "last_verified": now,
                        "explore_start_time": now,  # Reset exploration time
                        "hcaptcha_start_time": None  # Clear the start time to prevent timeout checks
                    }
                }
            )
            
            # Wait a moment to ensure database update is processed
            await asyncio.sleep(0.5)
            
            # Now send notification to user
            await notify_user_success(user_id, player)
        except Exception as e:
            logger.error(f"Failed to update verification status: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to update verification status"
            )

        return RedirectResponse("/verification_success", status_code=303)

async def handle_verification_timeout(db, user_id: str, player: Optional[dict]):
    """Handle timeout scenario with ban and logging."""
    now = int(time.time())
    # Ensure user_id is int for ban logic compatibility
    user_id_int = int(user_id)
    # Check if already banned
    existing_ban = await db["bans"].find_one({"user_id": user_id_int})
    if existing_ban:
        # Already banned, do not send notification again
        return
    # Ban user
    await db["bans"].update_one(
        {"user_id": user_id_int},
        {
            "$set": {
                "user_id": user_id_int,
                "expiry": None,
                "reason": "hCaptcha timeout",
                "banned_by": "system",
                "banned_at": now
            }
        },
        upsert=True
    )

    # Reset player settings so user can explore after unban
    await db["players"].update_one(
        {"user_id": str(user_id_int)},
        {"$set": {
            "hcaptcha_verified": False,
            "hcaptcha_start_time": None,
            "explore_start_time": None,
            "last_explore_time": None,
        }}
    )

    bot_token = os.getenv("TELEGRAM_TOKEN")
    if bot_token:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": user_id_int,
                        "text": "You have been permanently banned due to hCaptcha timeout.",
                        "parse_mode": "HTML"
                    }
                )
        except Exception:
            pass

    first_name = (getattr(player, "first_name", None) or getattr(player, "name", None) or str(user_id_int)) if player else str(user_id_int)
    msg = (
        f"<b>#BanEvent</b>\n\n"
        f"<b>Target</b> : <a href='tg://user?id={user_id_int}'>{first_name}</a>\n"
        f"<b>Target ID</b> : <code>{user_id_int}</code>\n"
        f"<b>By</b> : <code>system</code>\n"
        f"<b>Reason</b> : <code>hCaptcha timeout</code>\n"
        f"<b>Time</b> : <code>Permanent</code>"
    )
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": BAN_LOG_CHAT_ID,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
            )
    except Exception:
        pass

async def notify_user_success(user_id: str, player: Optional[dict]):
    """Notify user in Telegram about successful verification."""
    bot_token = os.getenv("TELEGRAM_TOKEN")
    if not bot_token:
        return

    user_name = getattr(player, "name", "Explorer") if player else "Explorer"
    message = (
        f"✅ <b>Verification Successful!</b>\n\n"
        f"Hello {user_name}, wait a few seconds then explore! "
    )

    # Try multiple times to ensure the message is delivered
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": user_id,
                        "text": message,
                        "parse_mode": "HTML"
                    },
                    timeout=10.0  # Set a reasonable timeout
                )
            # If successful, break the retry loop
            logger.info(f"Successfully sent verification success notification to user {user_id}")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                # If not the last attempt, wait and retry
                await asyncio.sleep(1)
            else:
                # Log final failure
                logger.error(f"Failed to send verification success notification after {max_retries} attempts: {e}")
                pass