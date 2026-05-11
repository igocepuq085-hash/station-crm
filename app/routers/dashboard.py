from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.services.auth_service import require_auth
from app.services.analytics_service import get_dashboard_data
from app.services.ai_extraction_service import run_ai_extraction


router = APIRouter()
templates = Jinja2Templates(directory=settings.templates_dir)


@router.get("/dashboard/{package_id}")
def dashboard(package_id: int, request: Request, db: Session = Depends(get_db)):
    require_auth(request)
    data = get_dashboard_data(db, package_id)
    if not data:
        raise HTTPException(status_code=404, detail="Комплект отчетов не найден")

    request.session["last_dashboard_package_id"] = package_id
    data["request"] = request
    return templates.TemplateResponse("dashboard.html", data)


@router.post("/dashboard/{package_id}/ai-extract")
def ai_extract(package_id: int, request: Request, db: Session = Depends(get_db)):
    require_auth(request)
    if get_dashboard_data(db, package_id) == {}:
        raise HTTPException(status_code=404, detail="Комплект отчетов не найден")

    run_ai_extraction(db, package_id)
    return RedirectResponse(url=f"/dashboard/{package_id}", status_code=303)
