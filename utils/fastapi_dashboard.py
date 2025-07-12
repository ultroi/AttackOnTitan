from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from database.db_instance import get_database
from fastapi import Form, HTTPException
import time
import os
import httpx
from typing import Optional


# Set up Jinja2 templates (Flask uses templates/, FastAPI can use same)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), '../templates'))

HCAPTCHA_TIMEOUT = 600  # 10 minutes in seconds
BAN_LOG_CHAT_ID = -1002873117075 

# Add this route to main.py:
def include_dashboard_route(app):
    @app.get("/hcaptcha_timeout", response_class=HTMLResponse)
    async def hcaptcha_timeout(request: Request):
        return templates.TemplateResponse("hcaptcha_timeout.html", {"request": request})
    
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        # Just render dashboard.html, JS will fetch /monitor for live data
        return templates.TemplateResponse("dashboard.html", {"request": request})

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

        # Check if already verified (within timeout window)
        if player and player.get("hcaptcha_verified"):
            start_time = player.get("hcaptcha_start_time", 0)
            if now - start_time <= HCAPTCHA_TIMEOUT:
                return templates.TemplateResponse(
                    "already_verified.html",
                    {"request": request, "user_id": user_id}
                )

        # Initialize verification timer if not set
        if not player or not player.get("hcaptcha_start_time"):
            await db["players"].update_one(
                {"user_id": str(user_id)},
                {"$set": {"hcaptcha_start_time": now}},
                upsert=True
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

        # Get h-captcha-response from form if not provided
        if h_captcha_response is None:
            form = await request.form()
            h_captcha_response = form.get("h-captcha-response")

        if not h_captcha_response:
            return RedirectResponse(
                f"/hcaptcha?user_id={user_id}&error=Captcha+response+missing"
            )

        # Check timeout first
        player = await db["players"].find_one({"user_id": str(user_id)})
        now = int(time.time())
        start_time = player.get("hcaptcha_start_time", 0) if player else now

        if now - start_time > HCAPTCHA_TIMEOUT:
            await handle_verification_timeout(db, user_id, player)
            return RedirectResponse("/hcaptcha_timeout")

        # Verify with hCaptcha API
        secret = os.getenv("HCAPTCHA_SECRET")  # Move secret to environment
        if not secret:
            raise HTTPException(status_code=500, detail="Server configuration error")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://hcaptcha.com/siteverify",
                data={
                    "response": h_captcha_response,
                    "secret": secret
                },
                timeout=10.0  # Add timeout
            )
        result = response.json()

        if not result.get("success"):
            return RedirectResponse(
                f"/hcaptcha?user_id={user_id}&error=Verification+failed"
            )

        # Successful verification
        try:
            await db["players"].update_one(
                {"user_id": str(user_id)},
                {
                    "$set": {
                        "hcaptcha_verified": True,
                        "last_verified": now
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

        return RedirectResponse("/verification_success")

async def handle_verification_timeout(db, user_id: str, player: Optional[dict]):
    """Handle timeout scenario with ban and logging."""
    now = int(time.time())
    
    # Update ban record
    await db["bans"].update_one(
        {"user_id": str(user_id)},
        {
            "$set": {
                "reason": "hCaptcha timeout",
                "banned_by": "system",
                "banned_at": now,
                "expiry": None  # Permanent ban
            }
        },
        upsert=True
    )

    # Log to Telegram channel
    bot_token = os.getenv("TELEGRAM_TOKEN")
    if not bot_token:
        return

    user_name = (player.get("username") or 
                player.get("name") or 
                str(user_id)) if player else str(user_id)

    message = (
        f"⚠️ <b>hCaptcha Timeout Ban</b>\n\n"
        f"• User: <a href='tg://user?id={user_id}'>{user_name}</a>\n"
        f"• ID: <code>{user_id}</code>\n"
        f"• Reason: Failed to complete verification in time"
    )

    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={  # Use json instead of data for better encoding
                "chat_id": BAN_LOG_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
        )

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