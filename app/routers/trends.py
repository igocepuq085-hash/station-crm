from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.services.auth_service import require_auth
from app.services.trends_service import get_trends_data


router = APIRouter()
templates = Jinja2Templates(directory=settings.templates_dir)


@router.get("/trends")
def trends(
    request: Request,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    product: str | None = Query(default=None),
    shift: str = Query(default="all"),
    from_dashboard: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    require_auth(request)
    if shift not in {"all", "day", "night"}:
        shift = "all"

    if from_dashboard:
        request.session["last_dashboard_package_id"] = from_dashboard

    data = get_trends_data(
        db,
        date_from=date_from,
        date_to=date_to,
        product=product,
        shift=shift,
    )
    dashboard_package_id = request.session.get("last_dashboard_package_id")
    if not dashboard_package_id and data.get("latest_package"):
        dashboard_package_id = data["latest_package"].id
        request.session["last_dashboard_package_id"] = dashboard_package_id

    data["dashboard_package_id"] = dashboard_package_id
    data["request"] = request
    return templates.TemplateResponse("trends.html", data)
