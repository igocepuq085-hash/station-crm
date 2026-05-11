from pathlib import Path
from typing import Any

from app.parsers.operational_excel_parser import parse_excel


def parse_wagons_excel(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    return parse_excel(path, source_type="wagons")
