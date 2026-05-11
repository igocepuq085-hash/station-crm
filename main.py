from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import settings
from app.db.init_db import init_database
from app.routers import auth, dashboard, raw, trends, upload
from app.services.auth_service import LoginRequired, current_user, redirect_to_login


settings.ensure_directories()
init_database()

app = FastAPI(title=settings.app_name)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

templates = Jinja2Templates(directory=settings.templates_dir)

app.include_router(auth.router)
app.include_router(upload.router)
app.include_router(dashboard.router)
app.include_router(raw.router)
app.include_router(trends.router)


@app.exception_handler(LoginRequired)
def login_required_handler(request: Request, exc: LoginRequired):
    return redirect_to_login(request)


@app.get("/")
def index(request: Request):
    if not current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/upload", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
