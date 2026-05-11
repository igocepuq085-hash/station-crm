from collections import defaultdict
from datetime import date
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import DailyChainMetric, ProductWagonMetric, RawExtractedRow, ReportPackage
from app.services.product_wagon_service import PRODUCT_GROUPS


def _package_date(package: ReportPackage) -> date:
    return package.report_date or package.created_at.date()


def _safe_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100, 1)


def _numbers(text: str) -> list[int]:
    values = []
    for match in re.findall(r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[,.]\d+)?(?!\d)", text):
        try:
            values.append(int(float(match.replace(" ", "").replace("\u00a0", "").replace(",", "."))))
        except ValueError:
            continue
    return values


def _extract_row_value(rows: list[RawExtractedRow], phrases: tuple[str, ...]) -> int | None:
    for row in rows:
        text = row.row_text.casefold()
        if any(phrase.casefold() in text for phrase in phrases):
            values = _numbers(row.row_text)
            if values:
                return values[-1]
    return None


def _sum_optional(values: list[int | None]) -> int | None:
    clean_values = [value for value in values if value is not None]
    if not clean_values:
        return None
    return sum(clean_values)


def _format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _empty_chart(labels: list[str]) -> dict:
    return {"has_data": False, "labels": labels}


def get_trends_data(
    db: Session,
    date_from: date | None = None,
    date_to: date | None = None,
    product: str | None = None,
    shift: str = "all",
) -> dict:
    packages = db.scalars(select(ReportPackage).order_by(ReportPackage.created_at)).all()
    filtered_packages = []
    for package in packages:
        current_date = _package_date(package)
        if date_from and current_date < date_from:
            continue
        if date_to and current_date > date_to:
            continue
        filtered_packages.append(package)

    package_ids = [package.id for package in filtered_packages]
    package_dates = {package.id: _package_date(package) for package in filtered_packages}
    labels_by_date = sorted(set(package_dates.values()))
    chart_labels = [_format_date(value) for value in labels_by_date]
    has_enough_dates = len(labels_by_date) > 1

    products = [item for values in PRODUCT_GROUPS.values() for item in values]
    if not package_ids:
        return {
            "filters": {
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
                "product": product or "",
                "shift": shift,
            },
            "products": products,
            "has_enough_dates": False,
            "charts": {
                "movement": _empty_chart(chart_labels),
                "ratios": _empty_chart(chart_labels),
                "park": _empty_chart(chart_labels),
                "products": _empty_chart(chart_labels),
            },
        }

    chain_query = select(DailyChainMetric).where(DailyChainMetric.package_id.in_(package_ids))
    if shift != "all":
        chain_query = chain_query.where(DailyChainMetric.shift_type == shift)
    chain_metrics = db.scalars(chain_query).all()

    chain_by_date: dict[date, list[DailyChainMetric]] = defaultdict(list)
    for metric in chain_metrics:
        chain_by_date[package_dates[metric.package_id]].append(metric)

    movement = {
        "arrived": [],
        "loaded": [],
        "documented": [],
        "sent": [],
    }
    ratios = {
        "throughput": [],
        "documentation": [],
        "dispatch": [],
    }
    for current_date in labels_by_date:
        metrics = chain_by_date.get(current_date, [])
        wagons_in_surgut = _sum_optional([metric.wagons_in_surgut for metric in metrics])
        arrived = _sum_optional([metric.arrived_prom for metric in metrics])
        loaded = _sum_optional([metric.loaded_wagons for metric in metrics])
        documented = _sum_optional([metric.documented_wagons for metric in metrics])
        sent = _sum_optional([metric.sent_surgut for metric in metrics])

        movement["arrived"].append(arrived or 0)
        movement["loaded"].append(loaded or 0)
        movement["documented"].append(documented or 0)
        movement["sent"].append(sent or 0)
        ratios["throughput"].append(_safe_ratio(sent, wagons_in_surgut) or 0)
        ratios["documentation"].append(_safe_ratio(documented, loaded) or 0)
        ratios["dispatch"].append(_safe_ratio(sent, documented) or 0)

    rows = db.scalars(
        select(RawExtractedRow)
        .options(selectinload(RawExtractedRow.file))
        .where(RawExtractedRow.package_id.in_(package_ids))
        .order_by(RawExtractedRow.package_id, RawExtractedRow.file_id, RawExtractedRow.row_number)
    ).all()
    rows_by_date: dict[date, list[RawExtractedRow]] = defaultdict(list)
    for row in rows:
        rows_by_date[package_dates[row.package_id]].append(row)

    park = {"loaded": [], "empty": [], "bad": [], "not_in_summary": []}
    for current_date in labels_by_date:
        date_rows = rows_by_date.get(current_date, [])
        loaded = _extract_row_value(date_rows, ("груженые",))
        empty = _extract_row_value(date_rows, ("порожние",))
        bad = _extract_row_value(date_rows, ("негодные",))
        total = _extract_row_value(date_rows, ("парк всего", "вагоны всего"))
        not_in_summary = _extract_row_value(date_rows, ("не включенные в сводку", "не включено в сводку"))
        if not_in_summary is None and total is not None:
            not_in_summary = max(total - sum(value or 0 for value in (loaded, empty, bad)), 0)

        park["loaded"].append(loaded or 0)
        park["empty"].append(empty or 0)
        park["bad"].append(bad or 0)
        park["not_in_summary"].append(not_in_summary or 0)

    product_query = select(ProductWagonMetric).where(ProductWagonMetric.package_id.in_(package_ids))
    if shift != "all":
        product_query = product_query.where(ProductWagonMetric.shift_type == shift)
    if product:
        product_query = product_query.where(ProductWagonMetric.product == product)
    product_metrics = db.scalars(product_query).all()

    product_by_date: dict[date, list[ProductWagonMetric]] = defaultdict(list)
    for metric in product_metrics:
        product_by_date[package_dates[metric.package_id]].append(metric)

    loaded_by_product = []
    documented_by_product = []
    for current_date in labels_by_date:
        metrics = product_by_date.get(current_date, [])
        loaded_by_product.append(_sum_optional([metric.loaded_wagons for metric in metrics]) or 0)
        documented_by_product.append(_sum_optional([metric.documented_wagons for metric in metrics]) or 0)

    return {
        "filters": {
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "product": product or "",
            "shift": shift,
        },
        "products": products,
        "has_enough_dates": has_enough_dates,
        "charts": {
            "movement": {
                "has_data": has_enough_dates and any(any(values) for values in movement.values()),
                "labels": chart_labels,
                **movement,
            },
            "ratios": {
                "has_data": has_enough_dates and any(any(values) for values in ratios.values()),
                "labels": chart_labels,
                **ratios,
            },
            "park": {
                "has_data": has_enough_dates and any(any(values) for values in park.values()),
                "labels": chart_labels,
                **park,
            },
            "products": {
                "has_data": has_enough_dates and (any(loaded_by_product) or any(documented_by_product)),
                "labels": chart_labels,
                "loaded": loaded_by_product,
                "documented": documented_by_product,
            },
        },
    }
