from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.services.auth_service import require_auth
from app.services.analytics_service import get_raw_data


router = APIRouter()
templates = Jinja2Templates(directory=settings.templates_dir)


@router.get("/raw/{package_id}")
def raw_layer(
    package_id: int,
    request: Request,
    keyword: str | None = Query(default=None),
    sheet_name: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    require_auth(request)
    data = get_raw_data(db, package_id, keyword=keyword, sheet_name=sheet_name)
    if not data:
        raise HTTPException(status_code=404, detail="Комплект отчетов не найден")

    data["request"] = request
    return templates.TemplateResponse("raw.html", data)
