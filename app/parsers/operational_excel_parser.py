from pathlib import Path
from typing import Any

import pandas as pd

from app.parsers.keywords import find_keywords


def _row_to_text(row: pd.Series) -> str:
    values = []
    for value in row.tolist():
        if pd.isna(value):
            continue
        value_text = str(value).strip()
        if value_text:
            values.append(value_text)
    return " | ".join(values)


def parse_operational_excel(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    return parse_excel(path, source_type="operational")


def parse_excel(path: str | Path, source_type: str) -> dict[str, list[dict[str, Any]]]:
    excel = pd.ExcelFile(path)
    sheets: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []

    for sheet_name in excel.sheet_names:
        dataframe = excel.parse(sheet_name=sheet_name, header=None, dtype=object)
        sheets.append(
            {
                "source_type": source_type,
                "sheet_name": sheet_name,
                "rows_count": int(dataframe.shape[0]),
                "columns_count": int(dataframe.shape[1]),
            }
        )

        for row_index, row in dataframe.iterrows():
            row_text = _row_to_text(row)
            if not row_text:
                continue

            matched_keywords = find_keywords(row_text)
            if matched_keywords:
                raw_rows.append(
                    {
                        "source_type": source_type,
                        "sheet_name": sheet_name,
                        "row_number": int(row_index) + 1,
                        "matched_keywords": ", ".join(matched_keywords),
                        "row_text": row_text,
                    }
                )

    return {"sheets": sheets, "raw_rows": raw_rows}
