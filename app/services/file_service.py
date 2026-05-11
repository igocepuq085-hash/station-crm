from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
import hashlib
import pandas as pd

from app.core.config import settings


ALLOWED_EXCEL_SUFFIXES = {".xlsx", ".xls"}
ALLOWED_PDF_SUFFIXES = {".pdf"}
OPERATIONAL_EXCEL_SHEETS = {
    "Сводка диспетчера ДО",
    "Приложение №3а",
    "Перевалка",
    "УПГ",
    "Томск",
    "Новый Уренгой",
    "ПЕРЕХОДЯЩИЕ",
    "6. Выполнение плана",
}
WAGONS_EXCEL_SHEETS = {
    "Сводка ПРОМ",
    "Отчет НС",
    "Наличие в Сургуте",
    "Лист1",
}


def validate_file_suffix(file: UploadFile, allowed_suffixes: set[str]) -> None:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"Файл {file.filename!r} должен иметь расширение: {allowed}")


async def save_upload(file: UploadFile, package_dir: Path, prefix: str) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    original_name = Path(file.filename or f"{prefix}.bin").name
    destination = package_dir / f"{prefix}_{uuid4().hex[:8]}_{original_name}"

    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            output.write(chunk)

    await file.seek(0)
    return destination


def new_package_upload_dir() -> Path:
    settings.ensure_directories()
    return settings.upload_dir / uuid4().hex


def calculate_file_hash(path: str | Path) -> str:
    sha256 = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def detect_file_type(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in ALLOWED_EXCEL_SUFFIXES:
        return "excel"
    if suffix in ALLOWED_PDF_SUFFIXES:
        return "pdf"
    return "unknown"


def _normalize_sheet_name(sheet_name: str) -> str:
    return " ".join(sheet_name.split()).casefold()


def detect_report_type(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in ALLOWED_PDF_SUFFIXES:
        return "scanned_pdf"
    if suffix not in ALLOWED_EXCEL_SUFFIXES:
        return "unknown"

    excel = pd.ExcelFile(path)
    sheet_names = {_normalize_sheet_name(sheet_name) for sheet_name in excel.sheet_names}
    operational_names = {_normalize_sheet_name(sheet_name) for sheet_name in OPERATIONAL_EXCEL_SHEETS}
    wagons_names = {_normalize_sheet_name(sheet_name) for sheet_name in WAGONS_EXCEL_SHEETS}

    if sheet_names & operational_names:
        return "operational_excel"
    if sheet_names & wagons_names:
        return "wagons_excel"
    return "unknown_excel"
