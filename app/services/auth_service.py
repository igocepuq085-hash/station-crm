from fastapi import Request
from fastapi.responses import RedirectResponse

from app.core.config import settings


SESSION_USER_KEY = "admin_user"


def authenticate(username: str, password: str) -> bool:
    return username == settings.admin_username and password == settings.admin_password


def login_user(request: Request, username: str) -> None:
    request.session[SESSION_USER_KEY] = username


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def require_auth(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise LoginRequired()
    return user


class LoginRequired(Exception):
    pass


def redirect_to_login(request: Request) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
