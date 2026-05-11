from pathlib import Path
from typing import Any

import pdfplumber

from app.parsers.keywords import find_keywords


def parse_pdf(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    raw_rows: list[dict[str, Any]] = []

    try:
        with pdfplumber.open(path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                for line_index, line in enumerate(text.splitlines(), start=1):
                    line_text = line.strip()
                    if not line_text:
                        continue

                    matched_keywords = find_keywords(line_text)
                    if matched_keywords:
                        raw_rows.append(
                            {
                                "source_type": "pdf",
                                "sheet_name": f"Страница {page_index}",
                                "row_number": line_index,
                                "matched_keywords": ", ".join(matched_keywords),
                                "row_text": line_text,
                            }
                        )
    except Exception as exc:
        raw_rows.append(
            {
                "source_type": "pdf",
                "sheet_name": None,
                "row_number": None,
                "matched_keywords": "PDF_PARSE_ERROR",
                "row_text": f"Не удалось извлечь текст из PDF: {exc}",
            }
        )

    return {"sheets": [], "raw_rows": raw_rows}
