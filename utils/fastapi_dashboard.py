from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from database.db_instance import get_database
import time
import os

# Set up Jinja2 templates (Flask uses templates/, FastAPI can use same)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), '../templates'))


# Add this route to main.py:
def include_dashboard_route(app):
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        # Just render dashboard.html, JS will fetch /monitor for live data
        return templates.TemplateResponse("dashboard.html", {"request": request})

    @app.get("/hcaptcha", response_class=HTMLResponse)
    async def hcaptcha_page(request: Request, user_id: str):
        db = await get_database()
        if db is None:
            return HTMLResponse("<h2>Database unavailable. Please try again later.</h2>")
        if not user_id:
            return HTMLResponse("<h2>User ID missing. Cannot verify.</h2>")
        player = await db["players"].find_one({"user_id": str(user_id)})
        # If already verified, show message
        if player and player.get("hcaptcha_verified"):
            return HTMLResponse("<h2>You are already verified! You may return to Telegram.</h2>")
        # Set hCaptcha start time if not already set
        if not player or not player.get("hcaptcha_start_time"):
            await db["players"].update_one(
                {"user_id": str(user_id)},
                {"$set": {"hcaptcha_start_time": int(time.time())}},
                upsert=True
            )
        return templates.TemplateResponse("hcaptcha.html", {"request": request, "user_id": user_id})

    @app.post("/verify_hcaptcha")
    async def verify_hcaptcha(request: Request):
        form = await request.form()
        hcaptcha_response = form.get("h-captcha-response")
        user_id = form.get("user_id")
        if not user_id:
            return HTMLResponse("<h2>User ID missing. Cannot verify.</h2>")
        import httpx
        secret = "ES_661bbcca8a9d4bccb6d84c1a591b4ef0" 
        data = {
            "response": hcaptcha_response,
            "secret": secret,
        }
        db = await get_database()
        if db is None:
            return HTMLResponse("<h2>Database unavailable. Please try again later.</h2>")
        player = await db["players"].find_one({"user_id": str(user_id)})
        start_time = player.get("hcaptcha_start_time") if player else None
        now = int(time.time())
        # Check for timeout
        if start_time and now - start_time > 600:
            # Ban user permanently
            try:
                await db["bans"].update_one(
                    {"user_id": str(user_id)},
                    {"$set": {"user_id": str(user_id), "expiry": None, "reason": "hCaptcha timeout", "banned_by": "system", "banned_at": now}},
                    upsert=True
                )
            except Exception:
                return HTMLResponse("<h2>Error banning user. Please try again later.</h2>")
            return HTMLResponse("<h2>Timeout! You did not complete hCaptcha in 10 minutes. You are now banned.</h2>")
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://hcaptcha.com/siteverify", data=data)
            result = resp.json()
        if result.get("success"):
            try:
                await db["players"].update_one(
                    {"user_id": str(user_id)},
                    {"$set": {"hcaptcha_verified": True}},
                    upsert=True
                )
            except Exception as e:
                print(f"DB error storing hCaptcha verification: {e}")
            return HTMLResponse("<h2>Verification successful! You may return to Telegram.</h2>")
        else:
            return HTMLResponse("<h2>Verification failed. Please try again.</h2>")
