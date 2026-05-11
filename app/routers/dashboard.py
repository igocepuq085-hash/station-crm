from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.services.auth_service import require_auth
from app.services.analytics_service import get_dashboard_data


router = APIRouter()
templates = Jinja2Templates(directory=settings.templates_dir)


@router.get("/dashboard/{package_id}")
def dashboard(package_id: int, request: Request, db: Session = Depends(get_db)):
    require_auth(request)
    data = get_dashboard_data(db, package_id)
    if not data:
        raise HTTPException(status_code=404, detail="Комплект отчетов не найден")

    data["request"] = request
    return templates.TemplateResponse("dashboard.html", data)
