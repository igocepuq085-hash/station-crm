from datetime import datetime
import gc
from pathlib import Path
import sqlite3

from app.core.config import settings
from app.db.database import Base, engine


REQUIRED_SCHEMA: dict[str, set[str]] = {
    "report_packages": {"id", "report_date", "status", "created_at", "comment"},
    "report_files": {
        "id",
        "package_id",
        "original_filename",
        "stored_path",
        "file_type",
        "detected_report_type",
        "file_hash",
        "status",
        "created_at",
    },
    "raw_extracted_rows": {
        "id",
        "package_id",
        "file_id",
        "sheet_name",
        "row_number",
        "row_text",
        "matched_keywords",
        "created_at",
    },
    "raw_metrics": {
        "id",
        "package_id",
        "file_id",
        "source_type",
        "sheet_name",
        "row_number",
        "metric_key",
        "metric_label",
        "metric_value",
        "unit",
        "product",
        "shift_type",
        "confidence",
        "created_at",
    },
    "daily_chain_metrics": {
        "id",
        "package_id",
        "report_date",
        "shift_type",
        "wagons_in_surgut",
        "arrived_prom",
        "processed_prom",
        "loaded_wagons",
        "documented_wagons",
        "sent_surgut",
        "created_at",
    },
    "product_wagon_metrics": {
        "id",
        "package_id",
        "report_date",
        "shift_type",
        "product",
        "wagon_group",
        "planned_loading_wagons",
        "planned_loading_tons",
        "product_stock_tons",
        "available_wagons_total",
        "available_wagons_good",
        "available_wagons_bad",
        "loaded_wagons",
        "loaded_tons",
        "documented_wagons",
        "sent_wagons",
        "wagon_balance",
        "wagon_coverage_percent",
        "main_limitation",
        "created_at",
    },
}


def _database_path() -> Path:
    return settings.storage_dir / "station_crm.db"


def _schema_is_current(path: Path) -> bool:
    if not path.exists():
        return False

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for table_name, required_columns in REQUIRED_SCHEMA.items():
            cursor = connection.execute(f"PRAGMA table_info({table_name})")
            try:
                existing_columns = {row[1] for row in cursor.fetchall()}
            finally:
                cursor.close()
            if not required_columns.issubset(existing_columns):
                return False
        return True
    finally:
        connection.close()
        gc.collect()


def init_database() -> None:
    settings.ensure_directories()
    if not settings.is_sqlite:
        Base.metadata.create_all(bind=engine)
        return

    database_path = _database_path()

    if database_path.exists() and not _schema_is_current(database_path):
        engine.dispose()
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = database_path.with_name(f"station_crm_legacy_{timestamp}.db")
        database_path.rename(backup_path)

    Base.metadata.create_all(bind=engine)
