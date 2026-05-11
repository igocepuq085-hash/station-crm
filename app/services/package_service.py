from datetime import date
import re

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.db.models import ReportFile, ReportPackage
from app.services.file_service import (
    ALLOWED_EXCEL_SUFFIXES,
    ALLOWED_PDF_SUFFIXES,
    calculate_file_hash,
    detect_file_type,
    detect_report_type,
    new_package_upload_dir,
    save_upload,
    validate_file_suffix,
)
from app.services.parser_service import parse_package


def infer_report_date_from_filename(*filenames: str | None) -> date | None:
    for filename in filenames:
        if not filename:
            continue
        match = re.search(r"(20\d{2})(\d{2})(\d{2})", filename)
        if not match:
            continue
        year, month, day = map(int, match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


async def create_report_package(
    db: Session,
    operational_file: UploadFile,
    wagons_file: UploadFile,
    pdf_file: UploadFile | None = None,
    report_date: date | None = None,
    comment: str | None = None,
) -> ReportPackage:
    validate_file_suffix(operational_file, ALLOWED_EXCEL_SUFFIXES)
    validate_file_suffix(wagons_file, ALLOWED_EXCEL_SUFFIXES)
    has_pdf = bool(pdf_file and pdf_file.filename)
    if has_pdf:
        validate_file_suffix(pdf_file, ALLOWED_PDF_SUFFIXES)

    package_dir = new_package_upload_dir()
    operational_path = await save_upload(operational_file, package_dir, "operational")
    wagons_path = await save_upload(wagons_file, package_dir, "wagons")
    pdf_path = await save_upload(pdf_file, package_dir, "pdf") if has_pdf else None

    detected_report_date = report_date or infer_report_date_from_filename(
        operational_file.filename,
        wagons_file.filename,
        pdf_file.filename if has_pdf else None,
    )

    package = ReportPackage(
        report_date=detected_report_date,
        status="uploaded",
        comment=comment or "Комплект загружен через веб-форму.",
    )
    db.add(package)
    db.commit()
    db.refresh(package)

    file_specs = [
        (operational_file, operational_path),
        (wagons_file, wagons_path),
    ]
    if has_pdf and pdf_path is not None:
        file_specs.append((pdf_file, pdf_path))

    for upload, path in file_specs:
        db.add(
            ReportFile(
                package_id=package.id,
                original_filename=upload.filename or path.name,
                stored_path=str(path),
                file_type=detect_file_type(path),
                detected_report_type=detect_report_type(path),
                file_hash=calculate_file_hash(path),
                status="uploaded",
            )
        )
    db.commit()
    db.refresh(package)

    parse_package(db, package)
    db.refresh(package)
    return package
