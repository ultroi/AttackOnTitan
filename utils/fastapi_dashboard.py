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

# Usage: in main.py, after app = FastAPI(), call include_dashboard_route(app)
