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
        secret = "93e66168-0084-4490-a700-1ffcc1f631e0" 
        data = {
            "response": hcaptcha_response,
            "secret": secret,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://hcaptcha.com/siteverify", data=data)
            result = resp.json()
        if result.get("success"):
            # Mark user as verified (implement your logic, e.g., update DB or cache)
            # For demo, just show success
            return HTMLResponse("<h2>Verification successful! You may return to Telegram.</h2>")
        else:
            return HTMLResponse("<h2>Verification failed. Please try again.</h2>")
