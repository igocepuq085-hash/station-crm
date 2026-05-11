from collections import Counter
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    DailyChainMetric,
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
    return round(numerator / denominator * 100, 1)


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value}%"


def _build_chain_view(chain_metric: DailyChainMetric | None) -> dict:
    if chain_metric is None:
        steps = [
            ("Сургут", None),
            ("Прибыло на Промышленную", None),
            ("Обработано", None),
            ("Погружено", None),
            ("Оформлено", None),
            ("Отправлено в Сургут", None),
        ]
        ratios = {
            "throughput": None,
            "loading": None,
            "documentation": None,
            "dispatch": None,
        }
    else:
        steps = [
            ("Сургут", chain_metric.wagons_in_surgut),
            ("Прибыло на Промышленную", chain_metric.arrived_prom),
            ("Обработано", chain_metric.processed_prom),
            ("Погружено", chain_metric.loaded_wagons),
            ("Оформлено", chain_metric.documented_wagons),
            ("Отправлено в Сургут", chain_metric.sent_surgut),
        ]
        ratios = {
            "throughput": _safe_ratio(chain_metric.sent_surgut, chain_metric.wagons_in_surgut),
            "loading": _safe_ratio(chain_metric.loaded_wagons, chain_metric.processed_prom),
            "documentation": _safe_ratio(chain_metric.documented_wagons, chain_metric.loaded_wagons),
            "dispatch": _safe_ratio(chain_metric.sent_surgut, chain_metric.documented_wagons),
        }

    return {
        "metric": chain_metric,
        "steps": [{"label": label, "value": value} for label, value in steps],
        "kpis": [
            {
                "label": "Сквозной коэффициент обработки",
                "value": _format_ratio(ratios["throughput"]),
                "hint": "отправлено / вагоны в Сургуте",
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
) -> dict:
    funnel_values = []
    if chain_metric is not None:
        funnel_values = [
            chain_metric.wagons_in_surgut,
            chain_metric.arrived_prom,
            chain_metric.processed_prom,
            chain_metric.loaded_wagons,
            chain_metric.documented_wagons,
            chain_metric.sent_surgut,
        ]

    shift_by_type = {metric.shift_type: metric for metric in all_chain_metrics if metric.shift_type}
    day_metric = shift_by_type.get("day")
    night_metric = shift_by_type.get("night")
    shift_values = {
        "day": [
            day_metric.arrived_prom if day_metric else None,
            day_metric.loaded_wagons if day_metric else None,
            day_metric.documented_wagons if day_metric else None,
            day_metric.sent_surgut if day_metric else None,
        ],
        "night": [
            night_metric.arrived_prom if night_metric else None,
            night_metric.loaded_wagons if night_metric else None,
            night_metric.documented_wagons if night_metric else None,
            night_metric.sent_surgut if night_metric else None,
        ],
    }

    visible_product_metrics = [
        metric
        for metric in product_metrics
        if any(
            value is not None
            for value in (
                metric.planned_loading_wagons,
                metric.available_wagons_good,
                metric.loaded_wagons,
                metric.documented_wagons,
                metric.sent_wagons,
            )
        )
    ]
    product_labels = [metric.product for metric in visible_product_metrics]

    loaded = _extract_row_value(raw_rows, ("груженые",))
    empty = _extract_row_value(raw_rows, ("порожние",))
    bad = _extract_row_value(raw_rows, ("негодные",))
    total = _extract_row_value(raw_rows, ("парк всего", "вагоны всего"))
    other = None
    if total is not None:
        known = sum(value or 0 for value in (loaded, empty, bad))
        other = max(total - known, 0)
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
                _non_empty_numbers([metric.loaded_wagons for metric in visible_product_metrics])
                or _non_empty_numbers([metric.documented_wagons for metric in visible_product_metrics])
            ),
            "labels": product_labels,
            "loaded": [metric.loaded_wagons or 0 for metric in visible_product_metrics],
            "documented": [metric.documented_wagons or 0 for metric in visible_product_metrics],
        },
        "park_structure": {
            "has_data": _non_empty_numbers(park_values),
            "labels": ["Груженые", "Порожние", "Негодные", "Прочие"],
            "values": [value if value is not None else 0 for value in park_values],
        },
        "product_matrix": {
            "has_data": bool(product_labels)
            and (
                _non_empty_numbers([metric.planned_loading_wagons for metric in visible_product_metrics])
                or _non_empty_numbers([metric.available_wagons_good for metric in visible_product_metrics])
                or _non_empty_numbers([metric.loaded_wagons for metric in visible_product_metrics])
            ),
            "labels": product_labels,
            "planned": [metric.planned_loading_wagons or 0 for metric in visible_product_metrics],
            "good": [metric.available_wagons_good or 0 for metric in visible_product_metrics],
            "loaded": [metric.loaded_wagons or 0 for metric in visible_product_metrics],
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
        "chain": _build_chain_view(chain_metric),
        "metric_sources": metric_sources,
        "product_metrics": product_metrics,
        "false_coverage_warnings": false_park_coverage_warnings(product_metrics),
        "management_insight": build_ai_or_rule_based_summary(db, package_id),
        "charts": _build_chart_data(chain_metric, product_metrics, raw_rows, all_chain_metrics),
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
