from fastapi import Request, Form, HTTPException, Depends, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import APIKeyCookie
from typing import Optional
from database.db_instance import get_database
import time
import os
import httpx
import json
from datetime import datetime
from typing import Optional, Dict, List
import logging
import hashlib
import secrets
from starlette.status import HTTP_303_SEE_OTHER
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

# Dashboard authentication
DASHBOARD_ACCESS_CODE = "Attack0nTitanAdmin"  
active_sessions: Dict[str, Dict] = {}
SESSION_COOKIE = "aot_dashboard_session"

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

# Session management
def create_session(user_id: int, ip_address: str = "unknown") -> str:
    """Create a new session for authenticated user"""
    session_id = secrets.token_hex(16)
    timestamp = time.time()
    active_sessions[session_id] = {
        "user_id": user_id,
        "expiry": timestamp + 3600,  # 1 hour session
        "created_at": timestamp,
        "ip_address": ip_address,
        "last_activity": timestamp
    }
    
    # Log new session creation
    log_dashboard_access(
        user_id=user_id, 
        action="login", 
        ip_address=ip_address, 
        details={"session_id": session_id}
    )
    return session_id

async def verify_session(session: Optional[str] = Cookie(None)) -> Optional[int]:
    """Verify user session, return user_id if valid or None if invalid"""
    if not session or session not in active_sessions:
        return None
    
    session_data = active_sessions[session]
    current_time = time.time()
    
    # Check if session is expired
    if session_data["expiry"] < current_time:
        del active_sessions[session]
        return None
    
    # Extend session and update last activity
    session_data["expiry"] = current_time + 3600
    session_data["last_activity"] = current_time
    
    return session_data["user_id"]

async def track_session_activity(session: str, request: Request) -> None:
    """Track session activity for logging purposes"""
    if session in active_sessions:
        session_data = active_sessions[session]
        current_time = time.time()
        client_ip = "unknown"
        path = "unknown"
        
        # Safely get client IP and path
        if request:
            if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
                client_ip = request.client.host
            if hasattr(request, 'url') and request.url and hasattr(request.url, 'path'):
                path = request.url.path
        
        # Track activity every 5 minutes
        if current_time - session_data.get("last_logged", 0) > 300:
            log_dashboard_access(
                user_id=session_data["user_id"],
                action="access",
                ip_address=client_ip,
                details={"path": path}
            )
            session_data["last_logged"] = current_time

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
        """Render login page"""
        return templates.TemplateResponse("login.html", {"request": request, "error": error})
        
    @app.post("/login", response_class=HTMLResponse)
    async def login_post(
        request: Request,
        user_id: str = Form(...),
        access_code: str = Form(...)
    ):
        """Process login attempt"""
        try:
            # Validate input
            user_id_int = int(user_id)
            client_ip = "unknown"
            if hasattr(request, 'client') and request.client:
                if hasattr(request.client, 'host'):
                    client_ip = request.client.host

            # Log the login attempt
            log_details = {"attempt": "login", "success": False}

            # Verify access code
            if access_code != DASHBOARD_ACCESS_CODE:
                log_dashboard_access(
                    user_id=user_id_int,
                    action="login_failed",
                    ip_address=client_ip,
                    details={"reason": "invalid_code"}
                )
                return templates.TemplateResponse(
                    "login.html", 
                    {"request": request, "error": "Invalid access code"}, 
                    status_code=401
                )

            # Check if user is authorized
            authorized = await is_authorized(user_id_int)
            if not authorized:
                log_dashboard_access(
                    user_id=user_id_int,
                    action="login_failed",
                    ip_address=client_ip,
                    details={"reason": "unauthorized_user"}
                )
                return templates.TemplateResponse(
                    "login.html", 
                    {"request": request, "error": "Unauthorized. Only owners and moderators can access."}, 
                    status_code=403
                )

            # Create session with IP address
            session_id = create_session(user_id_int, client_ip)

            # Set session cookie on the actual response object being returned
            redirect_response = RedirectResponse("/dashboard", status_code=HTTP_303_SEE_OTHER)
            redirect_response.set_cookie(
                key=SESSION_COOKIE,
                value=session_id,
                httponly=True,
                max_age=3600,
                secure=False,  # Set to True in production with HTTPS
                samesite="lax",  # More permissive SameSite policy
                path="/"        # Ensure cookie is available for all paths
            )

            logger.info(f"🔐 User {user_id_int} successfully logged in from IP {client_ip}")
            return redirect_response
        except ValueError:
            return templates.TemplateResponse(
                "login.html", 
                {"request": request, "error": "Invalid user ID format. Must be a number."}, 
                status_code=400
            )
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return templates.TemplateResponse(
                "login.html", 
                {"request": request, "error": f"Login failed: {str(e)}"}, 
                status_code=500
            )
    
    @app.get("/logout")
    async def logout(request: Request, response: Response, session: Optional[str] = Cookie(None)):
        """Log out and clear session"""
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host
            
        if session and session in active_sessions:
            user_id = active_sessions[session].get("user_id", 0)
            # Log the logout only if we have a valid user_id
            if user_id:
                log_dashboard_access(
                    user_id=user_id,
                    action="logout",
                    ip_address=client_ip,
                    details={"session_id": session}
                )
            logger.info(f"👋 User {user_id} logged out from IP {client_ip}")
            del active_sessions[session]
            
        response = RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
        response.delete_cookie(SESSION_COOKIE)
        return response
            
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request, session: Optional[str] = Cookie(None)):
        """Render dashboard page with authentication"""
        # Get client IP
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host
            
        # Manual session verification to avoid dependency issues
        user_id = None
        if session and session in active_sessions:
            session_data = active_sessions[session]
            current_time = time.time()
            
            # Check if session is expired
            if session_data["expiry"] < current_time:
                del active_sessions[session]
            else:
                # Extend session and update last activity
                session_data["expiry"] = current_time + 3600
                session_data["last_activity"] = current_time
                user_id = session_data["user_id"]
                
                # Track session activity
                await track_session_activity(session, request)
        
        if user_id is None:
            # Log unauthorized access
            log_dashboard_access(
                user_id=0,
                action="dashboard_access_denied",
                ip_address=client_ip,
                details={"reason": "invalid_session"}
            )
            return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
            
        # Track dashboard access
        log_dashboard_access(
            user_id=user_id,
            action="dashboard_view",
            ip_address=client_ip,
            details={}
        )
        
        # Get user role (owner or mod)
        user_role = "Owner" if user_id in get_owner_ids() else "Moderator"
        
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
    async def access_logs_page(request: Request, user_id: Optional[int] = Depends(verify_session)):
        """View access logs page (only for owners)"""
        if user_id is None:
            return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
        
        # Only owners can access the logs page
        if user_id not in get_owner_ids():
            return RedirectResponse("/dashboard", status_code=HTTP_303_SEE_OTHER)
        
        # Get client IP
        client_ip = "unknown"
        if hasattr(request, 'client') and request.client and hasattr(request.client, 'host'):
            client_ip = request.client.host
            
        # Track access logs page view
        log_dashboard_access(
            user_id=user_id,
            action="logs_page_view",
            ip_address=client_ip,
            details={}
        )
        
        return templates.TemplateResponse("access_logs.html", {"request": request, "user_id": user_id})
        
    @app.get("/access_logs_data")
    async def access_logs_data(request: Request, user_id: Optional[int] = Depends(verify_session)):
        """API endpoint for access logs data (only for owners)"""
        if user_id is None:
            return {"error": "Unauthorized", "status": "error"}
        
        # Only owners can access the logs
        if user_id not in get_owner_ids():
            return {"error": "Forbidden", "status": "error"}
        
        # Return the logs data
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
        if player and player.get("hcaptcha_verified"):
            start_time = player.get("hcaptcha_start_time", 0)
            if now - start_time <= HCAPTCHA_TIMEOUT:
                return templates.TemplateResponse(
                    "already_verified.html",
                    {"request": request, "user_id": user_id}
                )

    
        if not player or not player.get("hcaptcha_start_time"):
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
            if now - player.get("hcaptcha_start_time", 0) > HCAPTCHA_TIMEOUT:
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
        start_time = player.get("hcaptcha_start_time", now) if player else now

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
            start_time = player.get("hcaptcha_start_time", now) if player else now
            if now - start_time > HCAPTCHA_TIMEOUT:
                await handle_verification_timeout(db, user_id, player)
                await db["players"].update_one(
                    {"user_id": str(user_id)},
                    {"$set": {"hcaptcha_verified": False}}
                )
                return RedirectResponse("/hcaptcha_timeout", status_code=303)

            await db["players"].update_one(
                {"user_id": str(user_id)},
                {
                    "$set": {
                        "hcaptcha_verified": True,
                        "last_verified": now,
                        "explore_start_time": now  # Reset exploration time
                    }
                }
            )
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

        first_name = (player.get("first_name") or player.get("name") or str(user_id_int)) if player else str(user_id_int)
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

    user_name = player.get("name", "Explorer") if player else "Explorer"
    message = (
        f"✅ <b>Verification Successful!</b>\n\n"
        f"Hello {user_name}, you can now continue exploring!"
    )

    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": user_id,
                "text": message,
                "parse_mode": "HTML"
            }
        )