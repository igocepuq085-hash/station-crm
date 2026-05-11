import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import DailyChainMetric, RawExtractedRow, RawMetric, ReportPackage


@dataclass(frozen=True)
class MetricRule:
    metric_key: str
    metric_label: str
    source_type: str
    phrases: tuple[str, ...]


METRIC_RULES = [
    MetricRule(
        "wagons_in_surgut",
        "Вагоны в Сургуте",
        "wagons_excel",
        ("Наличие в Сургуте", "Парк всего"),
    ),
    MetricRule(
        "arrived_prom",
        "Прибыло на Промышленную",
        "wagons_excel",
        ("Прибыло за смену", "Прибыло за сутки"),
    ),
    MetricRule(
        "processed_prom",
        "Обработано на Промышленной",
        "wagons_excel",
        ("Парк всего", "Груженые", "Порожние", "Негодные"),
    ),
    MetricRule(
        "loaded_wagons",
        "Погружено",
        "operational_excel",
        ("Суточный факт", "До конца месяца налить"),
    ),
    MetricRule(
        "documented_wagons",
        "Оформлено",
        "operational_excel",
        ("Суточное оформление РЖД",),
    ),
    MetricRule(
        "sent_surgut",
        "Отправлено в Сургут",
        "wagons_excel",
        ("Отправлено за смену", "Отправлено за сутки"),
    ),
]


def _contains_phrase(text: str, phrase: str) -> bool:
    return phrase.casefold() in text.casefold()


def _extract_number(text: str) -> int | None:
    numbers = re.findall(r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[,.]\d+)?(?!\d)", text)
    if not numbers:
        return None

    value = numbers[-1].replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return int(float(value))
    except ValueError:
        return None


def build_daily_chain_metrics(db: Session, package: ReportPackage) -> DailyChainMetric:
    rows = db.scalars(
        select(RawExtractedRow)
        .options(selectinload(RawExtractedRow.file))
        .where(RawExtractedRow.package_id == package.id)
        .order_by(RawExtractedRow.file_id, RawExtractedRow.sheet_name, RawExtractedRow.row_number)
    ).all()

    values: dict[str, int | None] = {}
    for rule in METRIC_RULES:
        matched_rows = [
            row
            for row in rows
            if row.file.detected_report_type == rule.source_type
            and any(_contains_phrase(row.row_text, phrase) for phrase in rule.phrases)
        ]

        values[rule.metric_key] = None
        for row in matched_rows:
            value = _extract_number(row.row_text)
            db.add(
                RawMetric(
                    package_id=package.id,
                    file_id=row.file_id,
                    source_type=rule.source_type,
                    sheet_name=row.sheet_name,
                    row_number=row.row_number,
                    metric_key=rule.metric_key,
                    metric_label=rule.metric_label,
                    metric_value=float(value) if value is not None else None,
                    unit="вагон",
                    product=None,
                    shift_type="day",
                    confidence=0.6 if value is not None else 0.25,
                )
            )
            if values[rule.metric_key] is None and value is not None:
                values[rule.metric_key] = value

    metric = DailyChainMetric(
        package_id=package.id,
        report_date=package.report_date,
        shift_type="day",
        wagons_in_surgut=values.get("wagons_in_surgut"),
        arrived_prom=values.get("arrived_prom"),
        processed_prom=values.get("processed_prom"),
        loaded_wagons=values.get("loaded_wagons"),
        documented_wagons=values.get("documented_wagons"),
        sent_surgut=values.get("sent_surgut"),
    )
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric
