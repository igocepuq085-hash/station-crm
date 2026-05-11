from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import DailyChainMetric, ProductWagonMetric, RawMetric, ReportFile, ReportPackage
PRODUCT_GROUPS_RU: dict[str, list[str]] = {
    "СУГ": ["ПБТ", "ПБА", "ПТ", "ПТ ГОСТ", "ПА", "ФБ", "БТ", "ШФЛУ", "ПГФ", "УВФ"],
    "СНП": ["ДТ", "ТС", "АИ-92", "АИ-95", "ДГКЛ"],
    "ТНП": ["СК", "темные нефтепродукты"],
    "МЕТ": ["Метанол"],
    "МТБЭ": ["МТБЭ"],
}


PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    "ПБТ": ("ПБТ", "ПБТ СУГ"),
    "ПБА": ("ПБА",),
    "ПТ": ("ПТ", "ПТ(ТУ)"),
    "ПТ ГОСТ": ("ПТ ГОСТ", "ПТ(ГОСТ)", "ПТ ГОСТ", "ПТ (ГОСТ)"),
    "ПА": ("ПА",),
    "ФБ": ("ФБ",),
    "БТ": ("БТ",),
    "ШФЛУ": ("ШФЛУ",),
    "ПГФ": ("ПГФ",),
    "УВФ": ("УВФ",),
    "ДТ": ("ДТ", "Дизельное топливо"),
    "ТС": ("ТС", "ТС-1"),
    "АИ-92": ("АИ-92", "АИ-92(Н)"),
    "АИ-95": ("АИ-95", "АИ-95(Н)"),
    "ДГКЛ": ("ДГКЛ", "ДГКл", "ДГКЛ (МАРКИ Б)"),
    "СК": ("СК", "СК (НЕФТЬ)", "СК ПРК", "СК ЗСК"),
    "Метанол": ("МЕТАНОЛ", "МТ"),
    "МТБЭ": ("МТБЭ",),
}

GROUP_ALIASES = {
    "СУГ": "СУГ",
    "СНП": "СНП",
    "ТНП": "ТНП",
    "МЕТ": "МЕТ",
    "МТ": "МЕТ",
    "МТБЭ": "МТБЭ",
    "ПРОЧИЕ": "Прочие",
}

SKIP_PRODUCT_TOKENS = ("ИТОГО", "ВСЕГО", "ПРОДУКТ", "СПБ")


@dataclass
class ProductFact:
    product: str
    group: str
    planned_loading_tons: float | None = None
    loaded_tons: float | None = None
    product_stock_tons: float | None = None
    documented_wagons: int | None = None
    documented_tons: float | None = None
    source_notes: list[tuple[str, str, int | None, str, float | None]] = field(default_factory=list)


@dataclass
class StructuredData:
    chain: dict[str, int | None] = field(default_factory=dict)
    shifts: dict[str, dict[str, int | None]] = field(default_factory=dict)
    park: dict[str, int | None] = field(default_factory=dict)
    group_park: dict[str, dict[str, int | None]] = field(default_factory=dict)
    products: dict[str, ProductFact] = field(default_factory=dict)
    raw_metrics: list[dict] = field(default_factory=list)


def rebuild_structured_metrics(db: Session, package: ReportPackage) -> StructuredData:
    """Build the management layer from known report sheets.

    The raw keyword layer is intentionally kept. This layer reads the saved Excel
    files again and extracts only metrics that have stable positions or labels in
    the station reports.
    """

    data = StructuredData()
    files = list(package.files)
    db.execute(delete(DailyChainMetric).where(DailyChainMetric.package_id == package.id))
    db.execute(delete(ProductWagonMetric).where(ProductWagonMetric.package_id == package.id))
    db.execute(delete(RawMetric).where(RawMetric.package_id == package.id))

    for report_file in files:
        path = Path(report_file.stored_path)
        if not path.exists() or report_file.file_type != "excel":
            continue
        if report_file.detected_report_type == "operational_excel":
            _extract_operational_excel(path, report_file, package, data)
        elif report_file.detected_report_type == "wagons_excel":
            _extract_wagons_excel(path, report_file, package, data)

    _persist_chain_metrics(db, package, data)
    _persist_product_metrics(db, package, data)
    _persist_raw_metrics(db, package, data)
    db.commit()
    return data


def _extract_operational_excel(path: Path, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    try:
        workbook = pd.ExcelFile(path)
    except Exception:
        return

    if "Сводка диспетчера ДО" in workbook.sheet_names:
        df = pd.read_excel(path, sheet_name="Сводка диспетчера ДО", header=None)
        _extract_plan_fact(df, report_file, package, data)
        _extract_product_stock(df, report_file, package, data)

    if "Приложение №3а" in workbook.sheet_names:
        df = pd.read_excel(path, sheet_name="Приложение №3а", header=None)
        _extract_documentation(df, report_file, package, data)


def _extract_wagons_excel(path: Path, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    try:
        workbook = pd.ExcelFile(path)
    except Exception:
        return

    if "Сводка ПРОМ" in workbook.sheet_names:
        df = pd.read_excel(path, sheet_name="Сводка ПРОМ", header=None)
        _extract_park_summary(df, report_file, package, data)

    if "Отчет НС" in workbook.sheet_names:
        df = pd.read_excel(path, sheet_name="Отчет НС", header=None)
        _extract_train_exchange(df, report_file, package, data)

    if "Наличие в Сургуте" in workbook.sheet_names:
        df = pd.read_excel(path, sheet_name="Наличие в Сургуте", header=None)
        _extract_surgut_presence(df, report_file, package, data)


def _extract_plan_fact(df: pd.DataFrame, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    header_idx = _find_row(df, lambda cells: _norm(cells[0]) == "ПРОДУКТ" if cells else False)
    if header_idx is None or header_idx + 2 >= len(df):
        return

    header = df.iloc[header_idx].tolist()
    plan_row = df.iloc[header_idx + 1].tolist()
    fact_row = df.iloc[header_idx + 2].tolist()
    for col, header_cell in enumerate(header):
        product = _product_from_text(header_cell)
        if not product:
            continue
        fact = _product_fact(data, product)
        plan_value = _number(plan_row[col] if col < len(plan_row) else None)
        loaded_value = _number(fact_row[col] if col < len(fact_row) else None)
        if plan_value is not None:
            fact.planned_loading_tons = plan_value
            fact.source_notes.append(("planned_loading_tons", "Сводка диспетчера ДО", header_idx + 2, "сутки план", plan_value))
            _add_raw_metric(data, package, report_file, "planned_loading_tons", "План налива, т", plan_value, "т", product, "Сводка диспетчера ДО", header_idx + 2)
        if loaded_value is not None:
            fact.loaded_tons = loaded_value
            fact.source_notes.append(("loaded_tons", "Сводка диспетчера ДО", header_idx + 3, "сутки факт", loaded_value))
            _add_raw_metric(data, package, report_file, "loaded_tons", "Факт налива, т", loaded_value, "т", product, "Сводка диспетчера ДО", header_idx + 3)


def _extract_product_stock(df: pd.DataFrame, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    for row_idx in range(len(df)):
        first_cell = _norm(df.iat[row_idx, 0] if df.shape[1] else None)
        if first_cell != "ТОНН":
            continue
        for col in range(df.shape[1]):
            value = _number(df.iat[row_idx, col])
            if value is None:
                continue
            product = _product_above(df, row_idx, col)
            if not product:
                continue
            fact = _product_fact(data, product)
            fact.product_stock_tons = (fact.product_stock_tons or 0) + value
            fact.source_notes.append(("product_stock_tons", "Сводка диспетчера ДО", row_idx + 1, "остатки в парках", value))
    for fact in data.products.values():
        if fact.product_stock_tons is not None:
            _add_raw_metric(
                data,
                package,
                report_file,
                "product_stock_tons",
                "Остаток продукта на ЗСК, т",
                fact.product_stock_tons,
                "т",
                fact.product,
                "Сводка диспетчера ДО",
                None,
            )


def _extract_documentation(df: pd.DataFrame, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    for row_idx in range(len(df)):
        row = df.iloc[row_idx].tolist()
        product = _row_product(row)
        if not product:
            continue
        numbers = [_number(value) for value in row]
        numbers = [value for value in numbers if value is not None]
        if not numbers:
            continue
        fact = _product_fact(data, product)
        documented_wagons = int(numbers[0])
        documented_tons = numbers[1] if len(numbers) > 1 else None
        fact.documented_wagons = documented_wagons
        fact.documented_tons = documented_tons
        fact.source_notes.append(("documented_wagons", "Приложение №3а", row_idx + 1, "оформлено за истекшие сутки", documented_wagons))
        _add_raw_metric(data, package, report_file, "documented_wagons", "Оформлено, ваг", documented_wagons, "ваг", product, "Приложение №3а", row_idx + 1)
        if documented_tons is not None:
            fact.source_notes.append(("documented_tons", "Приложение №3а", row_idx + 1, "оформлено за истекшие сутки", documented_tons))
            _add_raw_metric(data, package, report_file, "documented_tons", "Оформлено, т", documented_tons, "т", product, "Приложение №3а", row_idx + 1)


def _extract_park_summary(df: pd.DataFrame, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    rows = {
        "total": _find_row(df, lambda cells: bool(cells) and _norm(cells[0]) == "ПАРК"),
        "bad": _find_row(df, lambda cells: bool(cells) and "НЕ ГОДНЫЕ" in _norm(cells[0])),
        "good": _find_row(df, lambda cells: bool(cells) and _norm(cells[0]) == "ГОДНЫЕ"),
        "loaded": _find_row(df, lambda cells: bool(cells) and _norm(cells[0]) == "ГРУЖЕНЫЕ"),
    }
    if rows["total"] is not None:
        row = df.iloc[rows["total"]].tolist()
        data.park["total"] = _next_number(row, 0)
        _add_raw_metric(data, package, report_file, "park_total", "Парк всего", data.park["total"], "ваг", None, "Сводка ПРОМ", rows["total"] + 1)
    for label, idx in rows.items():
        if idx is None:
            continue
        row = df.iloc[idx].tolist()
        groups = _group_values(row)
        for group, value in groups.items():
            data.group_park.setdefault(group, {})[label] = value
        if label in ("bad", "good", "loaded"):
            total = sum(groups.values()) if groups else None
            key = {"bad": "park_bad", "good": "park_good", "loaded": "park_loaded"}[label]
            title = {"bad": "Негодные вагоны", "good": "Годные вагоны", "loaded": "Груженые вагоны"}[label]
            data.park[label] = total
            _add_raw_metric(data, package, report_file, key, title, total, "ваг", None, "Сводка ПРОМ", idx + 1)


def _extract_train_exchange(df: pd.DataFrame, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    current_shift: str | None = None
    current_stage: str | None = None
    for row_idx in range(len(df)):
        row = [_cell_text(value) for value in df.iloc[row_idx].tolist()]
        row_text = " ".join(value for value in row if value)
        norm = _norm(row_text)
        if "ДНЕВНАЯ СМЕНА" in norm:
            current_shift = "day"
            current_stage = None
            continue
        if "НОЧНАЯ СМЕНА" in norm:
            current_shift = "night"
            current_stage = None
            continue
        if not current_shift:
            continue
        first = _norm(next((value for value in row if value), ""))
        if first == "ОТПРАВЛЕНИЕ":
            current_stage = "sent_surgut"
        elif first == "ПРИБЫТИЕ":
            current_stage = "arrived_prom"
        elif first == "НАЗНАЧЕНИЕ ПОЕЗДА" or not row_text:
            continue
        count = _wagon_weight_count(row_text)
        if current_stage and count is not None:
            data.shifts.setdefault(current_shift, {})
            data.shifts[current_shift][current_stage] = (data.shifts[current_shift].get(current_stage) or 0) + count
            data.chain[current_stage] = (data.chain.get(current_stage) or 0) + count
            _add_raw_metric(data, package, report_file, current_stage, _chain_label(current_stage), count, "ваг", None, "Отчет НС", row_idx + 1, current_shift)


def _extract_surgut_presence(df: pd.DataFrame, report_file: ReportFile, package: ReportPackage, data: StructuredData) -> None:
    total = 0
    in_surgut_section = False
    for row_idx in range(len(df)):
        cells = [_cell_text(value) for value in df.iloc[row_idx].tolist()]
        text = " ".join(cells)
        norm = _norm(text)
        first = _norm(next((value for value in cells if value), ""))
        if first.startswith("СТ. СУРГУТ"):
            in_surgut_section = True
        elif first.startswith("ПРОМЫШЛЕННАЯ") or first.startswith("ВСЕГО"):
            in_surgut_section = False
        if not in_surgut_section or not first.startswith("ИТОГО"):
            continue
        value = _next_number(df.iloc[row_idx].tolist(), 0)
        if value is not None:
            total += value
    if total:
        data.chain["wagons_in_surgut"] = total
        _add_raw_metric(data, package, report_file, "wagons_in_surgut", "Вагоны в Сургуте", total, "ваг", None, "Наличие в Сургуте", None)

    last_row = len(df) - 1
    not_included = _next_number(df.iloc[last_row].tolist(), 0)
    if not_included is not None:
        data.park["not_included"] = not_included
        _add_raw_metric(data, package, report_file, "park_not_included", "Не включены в сводку и Сургут", not_included, "ваг", None, "Наличие в Сургуте", last_row + 1)


def _persist_chain_metrics(db: Session, package: ReportPackage, data: StructuredData) -> None:
    documented = sum(
        fact.documented_wagons or 0
        for fact in data.products.values()
        if fact.documented_wagons is not None
    )
    if documented:
        data.chain["documented_wagons"] = documented
    db.add(
        DailyChainMetric(
            package_id=package.id,
            report_date=package.report_date,
            shift_type="сутки",
            wagons_in_surgut=data.chain.get("wagons_in_surgut"),
            arrived_prom=data.chain.get("arrived_prom"),
            processed_prom=data.chain.get("processed_prom"),
            loaded_wagons=data.chain.get("loaded_wagons"),
            documented_wagons=data.chain.get("documented_wagons"),
            sent_surgut=data.chain.get("sent_surgut"),
        )
    )
    for shift_type, values in data.shifts.items():
        db.add(
            DailyChainMetric(
                package_id=package.id,
                report_date=package.report_date,
                shift_type=shift_type,
                arrived_prom=values.get("arrived_prom"),
                sent_surgut=values.get("sent_surgut"),
            )
        )


def _persist_product_metrics(db: Session, package: ReportPackage, data: StructuredData) -> None:
    for product, fact in sorted(data.products.items(), key=lambda item: (item[1].group, item[0])):
        if _empty_product(fact):
            continue
        status = _product_status(fact)
        db.add(
            ProductWagonMetric(
                package_id=package.id,
                report_date=package.report_date,
                shift_type="day",
                product=product,
                wagon_group=fact.group,
                planned_loading_wagons=None,
                planned_loading_tons=fact.planned_loading_tons,
                product_stock_tons=fact.product_stock_tons,
                available_wagons_total=None,
                available_wagons_good=None,
                available_wagons_bad=None,
                loaded_wagons=None,
                loaded_tons=fact.loaded_tons,
                documented_wagons=fact.documented_wagons,
                documented_tons=fact.documented_tons,
                sent_wagons=None,
                wagon_balance=None,
                wagon_coverage_percent=None,
                main_limitation=status,
            )
        )


def _persist_raw_metrics(db: Session, package: ReportPackage, data: StructuredData) -> None:
    for item in data.raw_metrics:
        if item["metric_value"] is None:
            continue
        db.add(RawMetric(package_id=package.id, **item))


def _product_status(fact: ProductFact) -> str:
    has_any = any(
        value is not None
        for value in (
            fact.planned_loading_tons,
            fact.loaded_tons,
            fact.product_stock_tons,
            fact.documented_wagons,
            fact.documented_tons,
        )
    )
    if not has_any:
        return "нет данных"
    if fact.planned_loading_tons and fact.product_stock_tons is not None and fact.product_stock_tons <= 0:
        return "дефицит продукта"
    if fact.planned_loading_tons and fact.loaded_tons is not None and fact.loaded_tons < fact.planned_loading_tons * 0.85:
        return "отстает погрузка"
    if fact.loaded_tons and fact.documented_tons is not None and fact.documented_tons < fact.loaded_tons * 0.85:
        return "отстает оформление"
    return "норма"


def _empty_product(fact: ProductFact) -> bool:
    return all(
        value is None
        for value in (
            fact.planned_loading_tons,
            fact.loaded_tons,
            fact.product_stock_tons,
            fact.documented_wagons,
            fact.documented_tons,
        )
    )


def _product_fact(data: StructuredData, product: str) -> ProductFact:
    if product not in data.products:
        data.products[product] = ProductFact(product=product, group=_product_group(product))
    return data.products[product]


def _product_group(product: str) -> str:
    for group, products in PRODUCT_GROUPS_RU.items():
        normalized_products = {_norm(item) for item in products}
        if _norm(product) in normalized_products:
            return group
    return "прочие"


def _add_raw_metric(
    data: StructuredData,
    package: ReportPackage,
    report_file: ReportFile,
    metric_key: str,
    metric_label: str,
    metric_value: float | int | None,
    unit: str,
    product: str | None,
    sheet_name: str,
    row_number: int | None,
    shift_type: str | None = None,
) -> None:
    data.raw_metrics.append(
        {
            "file_id": report_file.id,
            "source_type": report_file.detected_report_type,
            "sheet_name": sheet_name,
            "row_number": row_number,
            "metric_key": metric_key,
            "metric_label": metric_label,
            "metric_value": metric_value,
            "unit": unit,
            "product": product,
            "shift_type": shift_type,
            "confidence": 0.95,
        }
    )


def _find_row(df: pd.DataFrame, predicate) -> int | None:
    for idx in range(len(df)):
        cells = [_cell_text(value) for value in df.iloc[idx].tolist()]
        if predicate(cells):
            return idx
    return None


def _group_values(row: list) -> dict[str, int]:
    result: dict[str, int] = {}
    for idx, value in enumerate(row):
        group = GROUP_ALIASES.get(_norm(value))
        if not group:
            continue
        next_value = _next_number(row, idx + 1)
        if next_value is not None:
            result[group] = next_value
    return result


def _next_number(row: list, start: int) -> int | None:
    for value in row[start : start + 6]:
        parsed = _int_number(value)
        if parsed is not None:
            return parsed
    return None


def _row_product(row: list) -> str | None:
    text = " ".join(_cell_text(value) for value in row[:4] if _cell_text(value))
    return _product_from_text(text)


def _product_above(df: pd.DataFrame, row_idx: int, col: int) -> str | None:
    for prev_idx in range(row_idx - 1, max(row_idx - 5, -1), -1):
        product = _product_from_text(df.iat[prev_idx, col])
        if product:
            return product
    return None


def _product_from_text(value) -> str | None:
    text = _norm(value)
    if not text or any(token in text for token in SKIP_PRODUCT_TOKENS):
        return None
    if "/" in text:
        return None
    for product, aliases in sorted(PRODUCT_ALIASES.items(), key=lambda item: -max(len(alias) for alias in item[1])):
        for alias in aliases:
            alias_norm = _norm(alias)
            if re.search(rf"(?<![А-ЯA-Z0-9]){re.escape(alias_norm)}(?![А-ЯA-Z0-9])", text):
                return product
    return None


def _wagon_weight_count(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,3})\s*/\s*\d{3,6}(?!\d)", text)
    if not match:
        return None
    return int(match.group(1))


def _chain_label(key: str) -> str:
    return {
        "arrived_prom": "Прибыло на Промышленную",
        "sent_surgut": "Отправлено в Сургут",
        "wagons_in_surgut": "Вагоны в Сургуте",
    }.get(key, key)


def _number(value) -> float | None:
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _cell_text(value).replace("\u00a0", " ").replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"-?\d+(?:[,.]\d+)?", text):
        return float(text.replace(",", "."))
    return None


def _int_number(value) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(round(number))


def _cell_text(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _norm(value) -> str:
    return re.sub(r"\s+", " ", _cell_text(value).replace("№", "N")).strip().upper()
