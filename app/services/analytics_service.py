from collections import Counter
import json
import re
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    DailyChainMetric,
    AIExtractedMetric,
    AIExtractionRun,
    ParsedSheet,
    ProductWagonMetric,
    RawExtractedRow,
    RawMetric,
    ReportFile,
    ReportPackage,
)
from app.services.ai_summary_service import build_ai_or_rule_based_summary
from app.services.product_wagon_service import false_park_coverage_warnings


REQUIRED_REPORT_TYPES = {
    "operational_excel": "Оперативный Excel",
    "wagons_excel": "Вагонный Excel",
    "scanned_pdf": "PDF",
}


def _build_completeness(files: list[ReportFile]) -> dict:
    detected_types = {file.detected_report_type for file in files}
    checks = [
        {
            "key": key,
            "label": label,
            "found": key in detected_types,
        }
        for key, label in REQUIRED_REPORT_TYPES.items()
    ]
    return {
        "checks": checks,
        "is_complete": all(item["found"] for item in checks),
        "missing_labels": [item["label"] for item in checks if not item["found"]],
    }


def _safe_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    if numerator > denominator * 1.2:
        return None
    return round(numerator / denominator * 100, 1)


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value}%"


def _build_chain_view(chain_metric: DailyChainMetric | None, ai_metrics: list[AIExtractedMetric] | None = None) -> dict:
    values = _chain_values(chain_metric, ai_metrics or [])
    steps = [
        ("Сургут", values["wagons_in_surgut"]),
        ("Прибыло на Промышленную", values["arrived_prom"]),
        ("Обработано", values["processed_prom"]),
        ("Погружено", values["loaded_wagons"]),
        ("Оформлено", values["documented_wagons"]),
        ("Отправлено в Сургут", values["sent_surgut"]),
    ]
    ratios = {
        "throughput": _safe_ratio(values["sent_surgut"], values["arrived_prom"]),
        "loading": _safe_ratio(values["loaded_wagons"], values["processed_prom"]),
        "documentation": _safe_ratio(values["documented_wagons"], values["loaded_wagons"]),
        "dispatch": _safe_ratio(values["sent_surgut"], values["documented_wagons"]),
    }

    return {
        "metric": chain_metric,
        "steps": [{"label": label, "value": value} for label, value in steps],
        "kpis": [
            {
                "label": "Сквозной коэффициент обработки",
                "value": _format_ratio(ratios["throughput"]),
                "hint": "отправлено / прибыло на Промышленную",
            },
            {
                "label": "Коэффициент погрузки",
                "value": _format_ratio(ratios["loading"]),
                "hint": "погружено / обработано",
            },
            {
                "label": "Коэффициент оформления",
                "value": _format_ratio(ratios["documentation"]),
                "hint": "оформлено / погружено",
            },
            {
                "label": "Коэффициент вывоза",
                "value": _format_ratio(ratios["dispatch"]),
                "hint": "отправлено / оформлено",
            },
        ],
    }


def _ai_value(ai_metrics: list[AIExtractedMetric], metric_group: str, metric_key: str, *, product: str | None = None, shift_type: str | None = None) -> int | None:
    for metric in ai_metrics:
        if metric.metric_group != metric_group or metric.metric_key != metric_key:
            continue
        if product is not None and metric.product != product:
            continue
        if shift_type is not None and metric.shift_type != shift_type:
            continue
        if metric.metric_value is not None:
            if metric.confidence is not None and metric.confidence < 0.6:
                continue
            if metric_group in {"chain_metrics", "shift_metrics"} and metric.metric_value == 0:
                continue
            return int(metric.metric_value)
    return None


def _chain_values(chain_metric: DailyChainMetric | None, ai_metrics: list[AIExtractedMetric]) -> dict[str, int | None]:
    values = {
        "wagons_in_surgut": chain_metric.wagons_in_surgut if chain_metric else None,
        "arrived_prom": chain_metric.arrived_prom if chain_metric else None,
        "processed_prom": chain_metric.processed_prom if chain_metric else None,
        "loaded_wagons": chain_metric.loaded_wagons if chain_metric else None,
        "documented_wagons": chain_metric.documented_wagons if chain_metric else None,
        "sent_surgut": chain_metric.sent_surgut if chain_metric else None,
    }
    for key, value in values.items():
        if value is None:
            values[key] = _ai_value(ai_metrics, "chain_metrics", key)
    return values


def _ai_string(metric: AIExtractedMetric) -> str | None:
    try:
        raw = json.loads(metric.raw_json)
    except Exception:
        return None
    if isinstance(raw, dict):
        value = raw.get("value")
        if isinstance(value, str):
            return value
        nested = raw.get("raw")
        if isinstance(nested, dict) and isinstance(nested.get("value"), str):
            return nested["value"]
    return None


def _merge_ai_product_metrics(product_metrics: list[ProductWagonMetric], ai_metrics: list[AIExtractedMetric]) -> list:
    by_product = {metric.product: metric for metric in product_metrics}
    ai_products = sorted({metric.product for metric in ai_metrics if metric.metric_group == "product_metrics" and metric.product})
    merged = list(product_metrics)
    for product in ai_products:
        existing = by_product.get(product)
        product_group = next((metric.product_group for metric in ai_metrics if metric.product == product and metric.product_group), None)
        values = {
            "planned_loading_wagons": _ai_value(ai_metrics, "product_metrics", "planned_loading_wagons", product=product),
            "planned_loading_tons": _ai_value(ai_metrics, "product_metrics", "planned_loading_tons", product=product),
            "product_stock_tons": _ai_value(ai_metrics, "product_metrics", "product_stock_tons", product=product),
            "available_wagons_total": _ai_value(ai_metrics, "product_metrics", "available_wagons_total", product=product),
            "available_wagons_good": _ai_value(ai_metrics, "product_metrics", "available_wagons_good", product=product),
            "available_wagons_bad": _ai_value(ai_metrics, "product_metrics", "available_wagons_bad", product=product),
            "loaded_wagons": _ai_value(ai_metrics, "product_metrics", "loaded_wagons", product=product),
            "loaded_tons": _ai_value(ai_metrics, "product_metrics", "loaded_tons", product=product),
            "documented_wagons": _ai_value(ai_metrics, "product_metrics", "documented_wagons", product=product),
            "documented_tons": _ai_value(ai_metrics, "product_metrics", "documented_tons", product=product),
            "sent_wagons": _ai_value(ai_metrics, "product_metrics", "sent_wagons", product=product),
        }
        limitation_metric = next(
            (metric for metric in ai_metrics if metric.metric_group == "product_metrics" and metric.product == product and metric.metric_key == "main_limitation"),
            None,
        )
        main_limitation = _ai_string(limitation_metric) if limitation_metric else None

        if existing:
            for key, value in values.items():
                if getattr(existing, key, None) is None and value is not None:
                    setattr(existing, key, value)
            if existing.main_limitation == "нет данных" and main_limitation:
                existing.main_limitation = main_limitation
        else:
            planned = values["planned_loading_wagons"]
            good = values["available_wagons_good"]
            merged.append(
                SimpleNamespace(
                    product=product,
                    wagon_group=product_group or "AI",
                    planned_loading_wagons=planned,
                    planned_loading_tons=values["planned_loading_tons"],
                    product_stock_tons=values["product_stock_tons"],
                    available_wagons_total=values["available_wagons_total"],
                    available_wagons_good=good,
                    available_wagons_bad=values["available_wagons_bad"],
                    loaded_wagons=values["loaded_wagons"],
                    loaded_tons=values["loaded_tons"],
                    documented_wagons=values["documented_wagons"],
                    documented_tons=values["documented_tons"],
                    sent_wagons=values["sent_wagons"],
                    wagon_balance=(good - planned) if good is not None and planned is not None else None,
                    wagon_coverage_percent=round(good / planned * 100, 1) if good is not None and planned not in (None, 0) else None,
                    main_limitation=main_limitation or "нет данных",
                )
            )
    return merged


def _ai_extraction_view(run: AIExtractionRun | None, metric_count: int) -> dict:
    if run is None:
        return {
            "status": "not_started",
            "model": None,
            "metric_count": metric_count,
            "error_text": None,
        }
    return {
        "status": run.status,
        "model": run.model,
        "metric_count": metric_count,
        "error_text": run.error_text,
    }


def _extract_row_value(rows: list[RawExtractedRow], phrases: tuple[str, ...]) -> int | None:
    for row in rows:
        text = row.row_text.casefold()
        if any(phrase.casefold() in text for phrase in phrases):
            numbers = re.findall(r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[,.]\d+)?(?!\d)", row.row_text)
            if numbers:
                value = numbers[-1].replace(" ", "").replace("\u00a0", "").replace(",", ".")
                try:
                    return int(float(value))
                except ValueError:
                    return None
    return None


def _non_empty_numbers(values: list[int | float | None]) -> bool:
    return any(value is not None for value in values)


def _build_chart_data(
    chain_metric: DailyChainMetric | None,
    product_metrics: list[ProductWagonMetric],
    raw_rows: list[RawExtractedRow],
    all_chain_metrics: list[DailyChainMetric],
    ai_metrics: list[AIExtractedMetric],
) -> dict:
    chain_values = _chain_values(chain_metric, ai_metrics)
    funnel_values = [
        chain_values["wagons_in_surgut"],
        chain_values["arrived_prom"],
        chain_values["processed_prom"],
        chain_values["loaded_wagons"],
        chain_values["documented_wagons"],
        chain_values["sent_surgut"],
    ]

    shift_by_type = {metric.shift_type: metric for metric in all_chain_metrics if metric.shift_type}
    day_metric = shift_by_type.get("day")
    night_metric = shift_by_type.get("night")
    shift_values = {
        "day": [
            (day_metric.arrived_prom if day_metric else None) or _ai_value(ai_metrics, "shift_metrics", "arrived_prom", shift_type="day"),
            (day_metric.loaded_wagons if day_metric else None) or _ai_value(ai_metrics, "shift_metrics", "loaded_wagons", shift_type="day"),
            (day_metric.documented_wagons if day_metric else None) or _ai_value(ai_metrics, "shift_metrics", "documented_wagons", shift_type="day"),
            (day_metric.sent_surgut if day_metric else None) or _ai_value(ai_metrics, "shift_metrics", "sent_surgut", shift_type="day"),
        ],
        "night": [
            (night_metric.arrived_prom if night_metric else None) or _ai_value(ai_metrics, "shift_metrics", "arrived_prom", shift_type="night"),
            (night_metric.loaded_wagons if night_metric else None) or _ai_value(ai_metrics, "shift_metrics", "loaded_wagons", shift_type="night"),
            (night_metric.documented_wagons if night_metric else None) or _ai_value(ai_metrics, "shift_metrics", "documented_wagons", shift_type="night"),
            (night_metric.sent_surgut if night_metric else None) or _ai_value(ai_metrics, "shift_metrics", "sent_surgut", shift_type="night"),
        ],
    }

    visible_product_metrics = [
        metric
        for metric in product_metrics
        if any(
            value is not None
            for value in (
                metric.planned_loading_wagons,
                metric.planned_loading_tons,
                metric.product_stock_tons,
                metric.available_wagons_good,
                metric.loaded_wagons,
                metric.loaded_tons,
                metric.documented_wagons,
                getattr(metric, "documented_tons", None),
                metric.sent_wagons,
            )
        )
    ]
    product_labels = [metric.product for metric in visible_product_metrics]

    loaded = _extract_row_value(raw_rows, ("груженые",)) or _ai_value(ai_metrics, "wagon_park", "loaded")
    empty = _extract_row_value(raw_rows, ("порожние",)) or _ai_value(ai_metrics, "wagon_park", "empty")
    bad = _extract_row_value(raw_rows, ("негодные",)) or _ai_value(ai_metrics, "wagon_park", "bad_order")
    total = _extract_row_value(raw_rows, ("парк всего", "вагоны всего")) or _ai_value(ai_metrics, "wagon_park", "total")
    other = None
    if total is not None:
        known = sum(value or 0 for value in (loaded, empty, bad))
        other = _ai_value(ai_metrics, "wagon_park", "other") or max(total - known, 0)
    park_values = [loaded, empty, bad, other]

    reasons = Counter(
        metric.main_limitation
        for metric in product_metrics
        if metric.main_limitation not in ("норма", "нет данных")
    )
    top_reasons = reasons.most_common(5)

    return {
        "funnel": {
            "has_data": _non_empty_numbers(funnel_values),
            "labels": ["Сургут", "Прибыло", "Обработано", "Погружено", "Оформлено", "Отправлено"],
            "values": [value if value is not None else 0 for value in funnel_values],
        },
        "shift": {
            "has_data": _non_empty_numbers(shift_values["day"]) and _non_empty_numbers(shift_values["night"]),
            "labels": ["Прибыло", "Погружено", "Оформлено", "Отправлено"],
            "day": [value if value is not None else 0 for value in shift_values["day"]],
            "night": [value if value is not None else 0 for value in shift_values["night"]],
        },
        "loaded_documented": {
            "has_data": bool(product_labels)
            and (
                _non_empty_numbers([metric.loaded_tons for metric in visible_product_metrics])
                or _non_empty_numbers([getattr(metric, "documented_tons", None) for metric in visible_product_metrics])
                or _non_empty_numbers([metric.loaded_wagons for metric in visible_product_metrics])
                or _non_empty_numbers([metric.documented_wagons for metric in visible_product_metrics])
            ),
            "labels": product_labels,
            "loaded": [(metric.loaded_tons if metric.loaded_tons is not None else metric.loaded_wagons) or 0 for metric in visible_product_metrics],
            "documented": [
                (
                    getattr(metric, "documented_tons", None)
                    if getattr(metric, "documented_tons", None) is not None
                    else metric.documented_wagons
                )
                or 0
                for metric in visible_product_metrics
            ],
        },
        "park_structure": {
            "has_data": _non_empty_numbers(park_values),
            "labels": ["Груженые", "Порожние", "Негодные", "Прочие"],
            "values": [value if value is not None else 0 for value in park_values],
        },
        "product_matrix": {
            "has_data": bool(product_labels)
            and (
                _non_empty_numbers([metric.planned_loading_tons for metric in visible_product_metrics])
                or _non_empty_numbers([metric.product_stock_tons for metric in visible_product_metrics])
                or _non_empty_numbers([metric.loaded_tons for metric in visible_product_metrics])
                or _non_empty_numbers([metric.planned_loading_wagons for metric in visible_product_metrics])
                or _non_empty_numbers([metric.available_wagons_good for metric in visible_product_metrics])
                or _non_empty_numbers([metric.loaded_wagons for metric in visible_product_metrics])
            ),
            "labels": product_labels,
            "planned": [(metric.planned_loading_tons if metric.planned_loading_tons is not None else metric.planned_loading_wagons) or 0 for metric in visible_product_metrics],
            "good": [(metric.product_stock_tons if metric.product_stock_tons is not None else metric.available_wagons_good) or 0 for metric in visible_product_metrics],
            "loaded": [(metric.loaded_tons if metric.loaded_tons is not None else metric.loaded_wagons) or 0 for metric in visible_product_metrics],
        },
        "reasons": {
            "has_data": bool(top_reasons),
            "labels": [reason for reason, _ in top_reasons],
            "values": [count for _, count in top_reasons],
        },
    }


def get_dashboard_data(db: Session, package_id: int) -> dict:
    package = db.get(ReportPackage, package_id)
    if package is None:
        return {}

    files = db.scalars(
        select(ReportFile)
        .where(ReportFile.package_id == package_id)
        .order_by(ReportFile.detected_report_type, ReportFile.original_filename)
    ).all()
    sheets = db.scalars(
        select(ParsedSheet)
        .where(ParsedSheet.package_id == package_id)
        .order_by(ParsedSheet.source_type, ParsedSheet.sheet_name)
    ).all()
    raw_rows = db.scalars(
        select(RawExtractedRow)
        .options(selectinload(RawExtractedRow.file))
        .where(RawExtractedRow.package_id == package_id)
        .order_by(RawExtractedRow.file_id, RawExtractedRow.sheet_name, RawExtractedRow.row_number)
    ).all()
    chain_metric = db.scalars(
        select(DailyChainMetric)
        .where(DailyChainMetric.package_id == package_id)
        .where(DailyChainMetric.shift_type == "сутки")
        .order_by(DailyChainMetric.created_at.desc())
    ).first()
    if chain_metric is None:
        chain_metric = db.scalars(
            select(DailyChainMetric)
            .where(DailyChainMetric.package_id == package_id)
            .order_by(DailyChainMetric.created_at.desc())
        ).first()
    all_chain_metrics = db.scalars(
        select(DailyChainMetric)
        .where(DailyChainMetric.package_id == package_id)
        .order_by(DailyChainMetric.shift_type, DailyChainMetric.created_at.desc())
    ).all()
    metric_sources = db.scalars(
        select(RawMetric)
        .options(selectinload(RawMetric.file))
        .where(RawMetric.package_id == package_id)
        .order_by(RawMetric.metric_key, RawMetric.file_id, RawMetric.sheet_name, RawMetric.row_number)
    ).all()
    raw_row_by_source = {
        (row.file_id, row.sheet_name, row.row_number): row.row_text
        for row in raw_rows
    }
    for metric_source in metric_sources:
        metric_source.source_row_text = raw_row_by_source.get(
            (metric_source.file_id, metric_source.sheet_name, metric_source.row_number)
        )
    product_metrics = db.scalars(
        select(ProductWagonMetric)
        .where(ProductWagonMetric.package_id == package_id)
        .order_by(ProductWagonMetric.wagon_group, ProductWagonMetric.product)
    ).all()
    ai_metrics = db.scalars(
        select(AIExtractedMetric)
        .where(AIExtractedMetric.package_id == package_id)
        .order_by(AIExtractedMetric.metric_group, AIExtractedMetric.product, AIExtractedMetric.metric_key)
    ).all()
    ai_run = db.scalars(
        select(AIExtractionRun)
        .where(AIExtractionRun.package_id == package_id)
        .order_by(AIExtractionRun.created_at.desc())
    ).first()
    product_metrics_view = _merge_ai_product_metrics(product_metrics, ai_metrics)

    source_counts: dict[str, int] = {}
    keyword_counts: dict[str, int] = {}
    for row in raw_rows:
        source_type = row.file.detected_report_type
        source_counts[source_type] = source_counts.get(source_type, 0) + 1
        for keyword in [item.strip() for item in row.matched_keywords.split(",") if item.strip()]:
            keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1

    return {
        "package": package,
        "files": files,
        "completeness": _build_completeness(files),
        "sheets": sheets,
        "raw_rows": raw_rows,
        "chain": _build_chain_view(chain_metric, ai_metrics),
        "metric_sources": metric_sources,
        "product_metrics": product_metrics_view,
        "false_coverage_warnings": false_park_coverage_warnings(product_metrics_view),
        "management_insight": build_ai_or_rule_based_summary(db, package_id),
        "ai_extraction": _ai_extraction_view(ai_run, len(ai_metrics)),
        "charts": _build_chart_data(chain_metric, product_metrics_view, raw_rows, all_chain_metrics, ai_metrics),
        "source_counts": source_counts,
        "keyword_counts": dict(sorted(keyword_counts.items(), key=lambda item: item[1], reverse=True)),
    }


def get_raw_data(
    db: Session,
    package_id: int,
    keyword: str | None = None,
    sheet_name: str | None = None,
) -> dict:
    package = db.get(ReportPackage, package_id)
    if package is None:
        return {}

    files = db.scalars(
        select(ReportFile)
        .where(ReportFile.package_id == package_id)
        .order_by(ReportFile.detected_report_type, ReportFile.original_filename)
    ).all()
    sheets = db.scalars(
        select(ParsedSheet)
        .options(selectinload(ParsedSheet.file))
        .where(ParsedSheet.package_id == package_id)
        .order_by(ParsedSheet.source_type, ParsedSheet.sheet_name)
    ).all()

    rows_query = (
        select(RawExtractedRow)
        .options(selectinload(RawExtractedRow.file))
        .where(RawExtractedRow.package_id == package_id)
    )
    if keyword:
        rows_query = rows_query.where(RawExtractedRow.matched_keywords.ilike(f"%{keyword}%"))
    if sheet_name:
        rows_query = rows_query.where(RawExtractedRow.sheet_name == sheet_name)

    raw_rows = db.scalars(
        rows_query.order_by(RawExtractedRow.file_id, RawExtractedRow.sheet_name, RawExtractedRow.row_number)
    ).all()

    unique_keywords = sorted(
        {
            item.strip()
            for row in db.scalars(
                select(RawExtractedRow).where(RawExtractedRow.package_id == package_id)
            ).all()
            for item in row.matched_keywords.split(",")
            if item.strip()
        },
        key=str.lower,
    )
    sheet_names = sorted({sheet.sheet_name for sheet in sheets}, key=str.lower)

    return {
        "package": package,
        "files": files,
        "completeness": _build_completeness(files),
        "sheets": sheets,
        "raw_rows": raw_rows,
        "unique_keywords": unique_keywords,
        "sheet_names": sheet_names,
        "selected_keyword": keyword or "",
        "selected_sheet_name": sheet_name or "",
    }
