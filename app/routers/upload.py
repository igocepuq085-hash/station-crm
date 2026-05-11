from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.services.auth_service import require_auth
from app.services.package_service import create_report_package


router = APIRouter()
templates = Jinja2Templates(directory=settings.templates_dir)


@router.get("/upload")
def upload_form(request: Request):
    require_auth(request)
    return templates.TemplateResponse("upload.html", {"request": request, "is_home": False})


@router.post("/upload")
async def upload_reports(
    request: Request,
    operational_file: UploadFile = File(...),
    wagons_file: UploadFile = File(...),
    pdf_file: UploadFile = File(...),
    report_date: date | None = Form(default=None),
    comment: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    require_auth(request)
    try:
        package = await create_report_package(
            db,
            operational_file,
            wagons_file,
            pdf_file,
            report_date=report_date,
            comment=comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Не удалось обработать комплект: {exc}") from exc

    return RedirectResponse(url=f"/dashboard/{package.id}", status_code=303)
