from fastapi import Request, Form, HTTPException, Depends, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import APIKeyCookie
from database.db_instance import get_database
import time
import os
import httpx
from typing import Optional, Dict
import logging
import hashlib
import secrets
from starlette.status import HTTP_303_SEE_OTHER
from utils.owners import get_owner_ids
from utils.mod_utils import is_mod

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set up Jinja2 templates
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), '../templates'))

HCAPTCHA_TIMEOUT = 600  # 10 minutes in seconds
BAN_LOG_CHAT_ID = -1002873117075

# Dashboard authentication
DASHBOARD_ACCESS_CODE = "Attack0nTitanAdmin"  # Change this to a strong password
active_sessions: Dict[str, Dict] = {}  # {session_id: {"user_id": int, "expiry": timestamp}}
SESSION_COOKIE = "aot_dashboard_session"

# Session management
def create_session(user_id: int) -> str:
    """Create a new session for authenticated user"""
    session_id = secrets.token_hex(16)
    active_sessions[session_id] = {
        "user_id": user_id,
        "expiry": time.time() + 3600  # 1 hour session
    }
    return session_id

async def verify_session(session: Optional[str] = Cookie(None)) -> Optional[int]:
    """Verify user session, return user_id if valid or None if invalid"""
    if not session or session not in active_sessions:
        return None
    
    session_data = active_sessions[session]
    # Check if session is expired
    if session_data["expiry"] < time.time():
        del active_sessions[session]
        return None
    
    # Extend session
    session_data["expiry"] = time.time() + 3600
    return session_data["user_id"]

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
        response: Response,
        user_id: str = Form(...),
        access_code: str = Form(...)
    ):
        """Process login attempt"""
        try:
            # Validate input
            user_id_int = int(user_id)
            
            # Verify access code
            if access_code != DASHBOARD_ACCESS_CODE:
                return templates.TemplateResponse(
                    "login.html", 
                    {"request": request, "error": "Invalid access code"}, 
                    status_code=401
                )
            
            # Check if user is authorized
            authorized = await is_authorized(user_id_int)
            if not authorized:
                return templates.TemplateResponse(
                    "login.html", 
                    {"request": request, "error": "Unauthorized. Only owners and moderators can access."}, 
                    status_code=403
                )
            
            # Create session
            session_id = create_session(user_id_int)
            
            # Set session cookie
            response = RedirectResponse("/dashboard", status_code=HTTP_303_SEE_OTHER)
            response.set_cookie(
                key=SESSION_COOKIE,
                value=session_id,
                httponly=True,
                max_age=3600,
                secure=False,  # Set to True in production with HTTPS
            )
            
            return response
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
    async def logout(response: Response, session: Optional[str] = Cookie(None)):
        """Log out and clear session"""
        if session and session in active_sessions:
            del active_sessions[session]
            
        response = RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
        response.delete_cookie(SESSION_COOKIE)
        return response
            
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request, user_id: Optional[int] = Depends(verify_session)):
        """Render dashboard page with authentication"""
        if user_id is None:
            return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
            
        return templates.TemplateResponse("dashboard.html", {"request": request, "user_id": user_id})

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