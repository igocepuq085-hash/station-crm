from pathlib import Path
from typing import Any

import pdfplumber

from app.parsers.keywords import find_keywords


def parse_pdf(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    sheets: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    extracted_lines = 0

    try:
        with pdfplumber.open(path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                sheets.append(
                    {
                        "source_type": "scanned_pdf",
                        "sheet_name": f"Страница {page_index}",
                        "rows_count": len(lines),
                        "columns_count": 1,
                    }
                )
                extracted_lines += len(lines)

                for line_index, line_text in enumerate(lines, start=1):
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
            if not raw_rows:
                message = (
                    f"PDF сохранен, страниц: {len(pdf.pages)}. "
                    "Текстовый слой не найден или ключевые строки не распознаны. "
                    "Вероятно, это скан; нужен OCR-слой для полного разбора PDF."
                    if extracted_lines == 0
                    else f"PDF сохранен, страниц: {len(pdf.pages)}, извлечено строк: {extracted_lines}. Ключевые строки не найдены."
                )
                raw_rows.append(
                    {
                        "source_type": "pdf",
                        "sheet_name": "PDF",
                        "row_number": None,
                        "matched_keywords": "PDF_STATUS",
                        "row_text": message,
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

    return {"sheets": sheets, "raw_rows": raw_rows}
