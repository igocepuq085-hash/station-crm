import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ProductWagonMetric, RawExtractedRow, ReportPackage


PRODUCT_GROUPS: dict[str, list[str]] = {
    "СУГ": ["ПБТ", "ПБА", "ПТ", "ПТ ГОСТ", "ПА", "ФБ", "БТ", "ШФЛУ", "ПГФ", "УВФ"],
    "СНП": ["ДТ", "ТС", "АИ-92", "АИ-95", "ДГКЛ"],
    "ТНП": ["СК", "темные нефтепродукты"],
    "МЕТ": ["Метанол"],
    "МТБЭ": ["МТБЭ"],
}


@dataclass
class ProductSignals:
    planned_loading_wagons: int | None = None
    planned_loading_tons: float | None = None
    product_stock_tons: float | None = None
    available_wagons_total: int | None = None
    available_wagons_good: int | None = None
    available_wagons_bad: int | None = None
    loaded_wagons: int | None = None
    loaded_tons: float | None = None
    documented_wagons: int | None = None
    sent_wagons: int | None = None


FIELD_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("planned_loading_wagons", ("план ваг", "план налива", "план")),
    ("planned_loading_tons", ("план тонн", "план тн", "план т")),
    ("product_stock_tons", ("зск", "остаток", "наличие продукта", "продукт")),
    ("available_wagons_total", ("вагоны всего", "парк всего", "есть вагонов", "наличие вагонов")),
    ("available_wagons_good", ("годные", "годн")),
    ("available_wagons_bad", ("негодные", "негодн")),
    ("loaded_wagons", ("погружено", "налито ваг", "налито")),
    ("loaded_tons", ("погружено тонн", "налито тонн", "тонн")),
    ("documented_wagons", ("оформлено", "оформление")),
    ("sent_wagons", ("отправлено", "отправление")),
)


def _all_products() -> list[tuple[str, str]]:
    return [(product, group) for group, products in PRODUCT_GROUPS.items() for product in products]


def _contains_token(text: str, token: str) -> bool:
    pattern = rf"(?<![\w-]){re.escape(token)}(?![\w-])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _contains_product(text: str, product: str) -> bool:
    if product == "ПТ" and _contains_token(text, "ПТ ГОСТ"):
        return False
    return _contains_token(text, product)


def _numbers(text: str) -> list[float]:
    values = []
    for match in re.findall(r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[,.]\d+)?(?!\d)", text):
        try:
            values.append(float(match.replace(" ", "").replace("\u00a0", "").replace(",", ".")))
        except ValueError:
            continue
    return values


def _number_after_marker(text: str, markers: tuple[str, ...]) -> float | None:
    text_lower = text.casefold()
    for marker in markers:
        marker_pos = text_lower.find(marker.casefold())
        if marker_pos == -1:
            continue
        tail = text[marker_pos + len(marker) :]
        values = _numbers(tail)
        if values:
            return values[0]
    return None


def _set_signal(signals: ProductSignals, field: str, value: float) -> None:
    if getattr(signals, field) is not None:
        return
    if field.endswith("_tons") or field == "wagon_coverage_percent":
        setattr(signals, field, value)
    else:
        setattr(signals, field, int(value))


def _extract_signals(rows: list[RawExtractedRow]) -> ProductSignals:
    signals = ProductSignals()
    for row in rows:
        for field, markers in FIELD_MARKERS:
            value = _number_after_marker(row.row_text, markers)
            if value is not None:
                _set_signal(signals, field, value)
    return signals


def _group_available_total(rows: list[RawExtractedRow], group: str) -> int | None:
    for row in rows:
        if _contains_token(row.row_text, group) and any(
            _contains_token(row.row_text, marker)
            for marker in ("парк всего", "вагоны всего", "есть вагонов", "наличие вагонов")
        ):
            values = _numbers(row.row_text)
            if values:
                return int(values[-1])
    return None


def _coverage(good: int | None, planned: int | None) -> float | None:
    if good is None or planned in (None, 0):
        return None
    return round(good / planned * 100, 1)


def _balance(good: int | None, planned: int | None) -> int | None:
    if good is None or planned is None:
        return None
    return good - planned


def _limitation(signals: ProductSignals, balance: int | None) -> str:
    values = [
        signals.planned_loading_wagons,
        signals.product_stock_tons,
        signals.available_wagons_total,
        signals.available_wagons_good,
        signals.loaded_wagons,
        signals.documented_wagons,
        signals.sent_wagons,
    ]
    if all(value is None for value in values):
        return "нет данных"
    if signals.product_stock_tons is not None and signals.product_stock_tons <= 0:
        return "дефицит продукта"
    if balance is not None and balance < 0:
        return "дефицит подходящих вагонов"
    if (
        signals.available_wagons_bad is not None
        and signals.available_wagons_total
        and signals.available_wagons_bad / signals.available_wagons_total >= 0.25
    ):
        return "много негодных"
    if (
        signals.loaded_wagons is not None
        and signals.documented_wagons is not None
        and signals.documented_wagons < signals.loaded_wagons
    ):
        return "отстает оформление"
    if (
        signals.documented_wagons is not None
        and signals.sent_wagons is not None
        and signals.sent_wagons < signals.documented_wagons
    ):
        return "отстает отправление"
    return "норма"


def build_product_wagon_metrics(db: Session, package: ReportPackage) -> list[ProductWagonMetric]:
    rows = db.scalars(
        select(RawExtractedRow)
        .options(selectinload(RawExtractedRow.file))
        .where(RawExtractedRow.package_id == package.id)
        .order_by(RawExtractedRow.file_id, RawExtractedRow.sheet_name, RawExtractedRow.row_number)
    ).all()

    metrics: list[ProductWagonMetric] = []
    for product, group in _all_products():
        product_rows = [row for row in rows if _contains_product(row.row_text, product)]
        signals = _extract_signals(product_rows)
        group_total = _group_available_total(rows, group)
        if product_rows and signals.available_wagons_total is None:
            signals.available_wagons_total = group_total

        balance = _balance(signals.available_wagons_good, signals.planned_loading_wagons)
        coverage = _coverage(signals.available_wagons_good, signals.planned_loading_wagons)
        limitation = _limitation(signals, balance)

        metric = ProductWagonMetric(
            package_id=package.id,
            report_date=package.report_date,
            shift_type="day",
            product=product,
            wagon_group=group,
            planned_loading_wagons=signals.planned_loading_wagons,
            planned_loading_tons=signals.planned_loading_tons,
            product_stock_tons=signals.product_stock_tons,
            available_wagons_total=signals.available_wagons_total,
            available_wagons_good=signals.available_wagons_good,
            available_wagons_bad=signals.available_wagons_bad,
            loaded_wagons=signals.loaded_wagons,
            loaded_tons=signals.loaded_tons,
            documented_wagons=signals.documented_wagons,
            sent_wagons=signals.sent_wagons,
            wagon_balance=balance,
            wagon_coverage_percent=coverage,
            main_limitation=limitation,
        )
        db.add(metric)
        metrics.append(metric)

    db.commit()
    return metrics


def false_park_coverage_warnings(metrics: list[ProductWagonMetric]) -> list[str]:
    warnings = []
    for metric in metrics:
        group_total = metric.available_wagons_total
        planned = metric.planned_loading_wagons
        good = metric.available_wagons_good
        if group_total and planned and good is not None and group_total >= planned and good < planned:
            warnings.append(
                f"{metric.product}: по группе {metric.wagon_group} парк {group_total}, "
                f"но годных под продукт {good} при плане {planned}."
            )
    return warnings
