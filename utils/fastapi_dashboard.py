from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
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
        # Render hcaptcha.html with user_id
        return templates.TemplateResponse("hcaptcha.html", {"request": request, "user_id": user_id})

    @app.post("/verify_hcaptcha")
    async def verify_hcaptcha(request: Request):
        form = await request.form()
        hcaptcha_response = form.get("h-captcha-response")
        user_id = form.get("user_id")
        import httpx
        secret = "ES_661bbcca8a9d4bccb6d84c1a591b4ef0" 
        data = {
            "response": hcaptcha_response,
            "secret": secret,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://hcaptcha.com/siteverify", data=data)
            result = resp.json()
        if result.get("success"):
            # Store verification status in DB
            try:
                from database.db_instance import get_database
                db = await get_database()
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
