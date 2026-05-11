import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.db.models import AIExtractedMetric, AIExtractionRun, RawExtractedRow


SYSTEM_PROMPT = """
Ты аналитический модуль CRM станции.

Тебе переданы сырые строки из ежедневных отчетов станции:
- оперативный Excel
- вагонный Excel
- PDF/скан, если текст удалось извлечь

Твоя задача:
извлечь только те показатели, которые явно присутствуют в переданных строках.

Главная технологическая цепочка:
Сургут → прибыло на Промышленную → обработано на Промышленной → погружено → оформлено → отправлено в Сургут.

Особое правило:
нельзя считать общий парк вагонов достаточным.
Нужно учитывать продукт и вагоны под конкретный продукт.
Может быть много газовых вагонов, но под нужный продукт вагонов не хватает.

Верни строго JSON по схеме:
{
  "chain_metrics": {
    "wagons_in_surgut": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "arrived_prom": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "processed_prom": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "loaded_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "documented_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "sent_surgut": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0}
  },
  "wagon_park": {
    "total": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "loaded": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "empty": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "bad_order": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "other": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "not_included": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
    "in_surgut": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0}
  },
  "shift_metrics": {
    "day": {
      "arrived_prom": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "loaded_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "documented_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "sent_surgut": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0}
    },
    "night": {
      "arrived_prom": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "loaded_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "documented_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "sent_surgut": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0}
    }
  },
  "product_metrics": [
    {
      "product": "ПБТ",
      "product_group": "СУГ",
      "planned_loading_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "planned_loading_tons": {"value": null, "unit": "tons", "source_rows": [], "confidence": 0},
      "available_wagons_total": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "available_wagons_good": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "available_wagons_bad": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "loaded_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "loaded_tons": {"value": null, "unit": "tons", "source_rows": [], "confidence": 0},
      "documented_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "documented_tons": {"value": null, "unit": "tons", "source_rows": [], "confidence": 0},
      "sent_wagons": {"value": null, "unit": "wagons", "source_rows": [], "confidence": 0},
      "product_stock_tons": {"value": null, "unit": "tons", "source_rows": [], "confidence": 0},
      "main_limitation": "нет данных"
    }
  ],
  "reasons": [
    {
      "stage": "documentation",
      "product": null,
      "reason": "ожидание оформления в АС ЭТРАН",
      "wagons_affected": null,
      "delay_minutes": null,
      "source_rows": [],
      "confidence": 0
    }
  ],
  "warnings": []
}

Правила:
- Не придумывай значения.
- Если показатель не найден явно, ставь null.
- Не суммируй строки, если не уверен, что это итог.
- Если видишь строку "Итого", отдавай ее с более высокой confidence.
- Обязательно указывай source_rows.
- Сохраняй разницу между вагонами и тоннами.
- Сохраняй разницу между продуктом и группой продукта.
- Сохраняй разницу между "погружено", "оформлено" и "отправлено".
""".strip()


NUMERIC_UNITS = {
    "planned_loading_tons": "т",
    "loaded_tons": "т",
    "documented_tons": "т",
    "product_stock_tons": "т",
}


def _rows_payload(rows: list[RawExtractedRow]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "file_type": row.file.detected_report_type,
            "sheet_name": row.sheet_name,
            "row_number": row.row_number,
            "matched_keywords": row.matched_keywords,
            "row_text": row.row_text[:1800],
        }
        for row in rows
    ]


def _metric_value(item: Any) -> float | None:
    if isinstance(item, dict):
        value = item.get("value")
    else:
        value = item
    if value is None or isinstance(value, str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_value(item: Any) -> str | None:
    if isinstance(item, dict):
        value = item.get("value")
    else:
        value = item
    return value if isinstance(value, str) and value.strip() else None


def _source_rows(item: Any) -> str:
    if isinstance(item, dict) and isinstance(item.get("source_rows"), list):
        return json.dumps(item["source_rows"], ensure_ascii=False)
    return "[]"


def _confidence(item: Any) -> float | None:
    if not isinstance(item, dict):
        return None
    value = item.get("confidence")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _add_metric(
    db: Session,
    package_id: int,
    metric_group: str,
    metric_key: str,
    item: Any,
    *,
    unit: str | None = None,
    product: str | None = None,
    product_group: str | None = None,
    shift_type: str | None = None,
) -> None:
    raw_json = json.dumps(item, ensure_ascii=False)
    if isinstance(item, dict) and item.get("unit"):
        unit = str(item["unit"])
    string_value = _string_value(item)
    metric_value = _metric_value(item)
    db.add(
        AIExtractedMetric(
            package_id=package_id,
            metric_group=metric_group,
            metric_key=metric_key,
            metric_value=metric_value,
            unit=unit,
            product=product,
            product_group=product_group,
            shift_type=shift_type,
            confidence=_confidence(item),
            source_rows_json=_source_rows(item),
            raw_json=raw_json if string_value is None else json.dumps({"value": string_value, "raw": item}, ensure_ascii=False),
        )
    )


def _save_metrics(db: Session, package_id: int, data: dict[str, Any]) -> int:
    db.execute(delete(AIExtractedMetric).where(AIExtractedMetric.package_id == package_id))
    count = 0

    for key, item in (data.get("chain_metrics") or {}).items():
        _add_metric(db, package_id, "chain_metrics", key, item, unit="вагон")
        count += 1

    for key, item in (data.get("wagon_park") or {}).items():
        _add_metric(db, package_id, "wagon_park", key, item, unit="вагон")
        count += 1

    for product_item in data.get("product_metrics") or []:
        if not isinstance(product_item, dict) or not product_item.get("product"):
            continue
        product = str(product_item["product"])
        product_group = product_item.get("product_group")
        for key, item in product_item.items():
            if key in {"product", "product_group"}:
                continue
            _add_metric(
                db,
                package_id,
                "product_metrics",
                key,
                item,
                unit=NUMERIC_UNITS.get(key, "вагон" if key.endswith("_wagons") or key.startswith("available_wagons") or key == "sent_wagons" else None),
                product=product,
                product_group=product_group,
            )
            count += 1

    for shift_type, shift_values in (data.get("shift_metrics") or {}).items():
        if not isinstance(shift_values, dict):
            continue
        for key, item in shift_values.items():
            _add_metric(db, package_id, "shift_metrics", key, item, unit="вагон", shift_type=shift_type)
            count += 1

    for index, reason in enumerate(data.get("reasons") or [], start=1):
        if not isinstance(reason, dict):
            continue
        _add_metric(
            db,
            package_id,
            "reasons",
            f"reason_{index}",
            {"value": reason.get("wagons_affected"), "source_rows": reason.get("source_rows") or [], "confidence": reason.get("confidence")},
            unit="вагон",
            product=reason.get("product"),
        )
        db.add(
            AIExtractedMetric(
                package_id=package_id,
                metric_group="reasons",
                metric_key=f"reason_text_{index}",
                metric_value=None,
                unit=None,
                product=reason.get("product"),
                product_group=None,
                shift_type=None,
                confidence=None,
                source_rows_json=json.dumps(reason.get("source_rows") or [], ensure_ascii=False),
                raw_json=json.dumps(reason, ensure_ascii=False),
            )
        )
        count += 2

    db.commit()
    return count


def run_ai_extraction(db: Session, package_id: int) -> AIExtractionRun:
    model = settings.openai_model
    if not settings.use_ai_extraction or not settings.openai_api_key:
        run = AIExtractionRun(
            package_id=package_id,
            model=model,
            status="skipped",
            error_text="AI extraction disabled or OPENAI_API_KEY is missing.",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    run = AIExtractionRun(package_id=package_id, model=model, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    rows = db.scalars(
        select(RawExtractedRow)
        .options(selectinload(RawExtractedRow.file))
        .where(RawExtractedRow.package_id == package_id)
        .order_by(RawExtractedRow.file_id, RawExtractedRow.sheet_name, RawExtractedRow.row_number)
    ).all()

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        payload = {"package_id": package_id, "raw_extracted_rows": _rows_payload(rows)}
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        usage = getattr(response, "usage", None)
        run.prompt_tokens = getattr(usage, "input_tokens", None)
        run.output_tokens = getattr(usage, "output_tokens", None)

        try:
            data = json.loads(response.output_text)
        except json.JSONDecodeError as exc:
            run.status = "error"
            run.error_text = f"AI returned invalid JSON: {exc}; output={response.output_text[:2000]}"
            db.commit()
            return run

        _save_metrics(db, package_id, data)
        run.status = "success"
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        run.status = "error"
        run.error_text = str(exc)
        db.commit()
        db.refresh(run)
        return run
