from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services.auth_service import authenticate, login_user, logout_user


router = APIRouter()
templates = Jinja2Templates(directory=settings.templates_dir)


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next": next, "error": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/"),
):
    if not authenticate(username, password):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": next,
                "error": "Неверный логин или пароль.",
            },
            status_code=401,
        )

    login_user(request, username)
    return RedirectResponse(url=next or "/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/login", status_code=303)
