from sqlalchemy.orm import Session

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import ParsedSheet, RawExtractedRow, ReportFile, ReportPackage
from app.parsers.operational_excel_parser import parse_excel
from app.parsers.pdf_parser import parse_pdf
from app.services.structured_report_service import rebuild_structured_metrics


def parse_package(db: Session, package: ReportPackage) -> None:
    package.status = "processing"
    db.commit()
    package = db.scalars(
        select(ReportPackage)
        .where(ReportPackage.id == package.id)
        .options(selectinload(ReportPackage.files))
    ).one()

    try:
        for report_file in package.files:
            report_file.status = "processing"
            db.commit()

            if report_file.file_type == "pdf":
                result = parse_pdf(report_file.stored_path)
            elif report_file.file_type == "excel":
                result = parse_excel(report_file.stored_path, source_type=report_file.detected_report_type)
            else:
                report_file.status = "skipped"
                continue

            for sheet in result["sheets"]:
                db.add(ParsedSheet(package_id=package.id, file_id=report_file.id, **sheet))

            for row in result["raw_rows"]:
                row.pop("source_type", None)
                db.add(RawExtractedRow(package_id=package.id, file_id=report_file.id, **row))

            report_file.status = "parsed"

        package.status = "parsed"
        package.comment = "Комплект обработан: сырой слой сохранен, структурные показатели рассчитаны."
        db.commit()
        rebuild_structured_metrics(db, package)
    except Exception as exc:
        db.rollback()
        package.status = "error"
        package.comment = f"Ошибка обработки: {exc}"
        db.add(package)
        db.commit()
        raise
