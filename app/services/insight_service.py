from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyChainMetric, ProductWagonMetric
from app.services.product_wagon_service import false_park_coverage_warnings


def _safe_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100, 1)


def _latest_day_metric(metrics: list[DailyChainMetric]) -> DailyChainMetric | None:
    day_metrics = [metric for metric in metrics if metric.shift_type in (None, "day")]
    if day_metrics:
        return day_metrics[-1]
    return metrics[-1] if metrics else None


def _shift_gap(metrics: list[DailyChainMetric]) -> bool:
    by_shift = {metric.shift_type: metric for metric in metrics if metric.shift_type in ("day", "night")}
    day = by_shift.get("day")
    night = by_shift.get("night")
    if not day or not night:
        return False

    pairs = [
        (day.arrived_prom, night.arrived_prom),
        (day.loaded_wagons, night.loaded_wagons),
        (day.documented_wagons, night.documented_wagons),
        (day.sent_surgut, night.sent_surgut),
    ]
    for day_value, night_value in pairs:
        if day_value is None or night_value is None:
            continue
        larger = max(day_value, night_value)
        smaller = min(day_value, night_value)
        if larger > 0 and smaller / larger < 0.6:
            return True
    return False


def build_management_insight(db: Session, package_id: int) -> dict:
    chain_metrics = db.scalars(
        select(DailyChainMetric)
        .where(DailyChainMetric.package_id == package_id)
        .order_by(DailyChainMetric.created_at)
    ).all()
    product_metrics = db.scalars(
        select(ProductWagonMetric)
        .where(ProductWagonMetric.package_id == package_id)
        .order_by(ProductWagonMetric.wagon_group, ProductWagonMetric.product)
    ).all()

    metric = _latest_day_metric(chain_metrics)
    false_coverage = false_park_coverage_warnings(product_metrics)

    main_gap = "Недостаточно данных для определения главного разрыва."
    risk = "Нет выраженного риска по доступным данным."
    reason = "Данных недостаточно, требуется сверка сырых строк и продуктовых показателей."
    next_shift_control = "Проверить комплектность отчетов, продукт на ЗСК, годность вагонов и статусы оформления/отправления."

    if metric is not None:
        if (
            metric.loaded_wagons is not None
            and metric.documented_wagons is not None
            and metric.loaded_wagons > metric.documented_wagons
        ):
            main_gap = "Главный разрыв на этапе оформления: погружено больше, чем оформлено."
            reason = "Оформление не успевает за фактической погрузкой."
            next_shift_control = "Контролировать оформление РЖД по уже погруженным вагонам."
        if (
            metric.documented_wagons is not None
            and metric.sent_surgut is not None
            and metric.documented_wagons > metric.sent_surgut
        ):
            main_gap = "Главный разрыв на этапе отправления: оформлено больше, чем отправлено."
            reason = "Отправление отстает от оформленных вагонов."
            next_shift_control = "Контролировать готовность отправления и вывоз оформленных вагонов в Сургут."
        if (
            metric.arrived_prom is not None
            and metric.loaded_wagons is not None
            and metric.arrived_prom >= 10
            and metric.loaded_wagons / metric.arrived_prom < 0.6
        ):
            main_gap = "Ограничение на этапе обработки / налива: прибыло много, погружено мало."
            reason = "Подход вагонов не переходит в достаточный факт погрузки."
            next_shift_control = "Проверить обработку на Промышленной, наличие продукта и готовность налива."

        documentation_ratio = _safe_ratio(metric.documented_wagons, metric.loaded_wagons)
        dispatch_ratio = _safe_ratio(metric.sent_surgut, metric.documented_wagons)
        if documentation_ratio is not None and documentation_ratio < 80:
            risk = f"Критично: коэффициент оформления {documentation_ratio}% ниже 80%."
        if dispatch_ratio is not None and dispatch_ratio < 80:
            risk = f"Риск накопления: коэффициент вывоза {dispatch_ratio}% ниже 80%."

    if false_coverage:
        reason = "Есть ложная обеспеченность парком: общий парк группы выглядит достаточным, но по конкретному продукту не хватает годных вагонов."
        next_shift_control = "Контролировать не общий парк, а годные вагоны под конкретный продукт."

    if _shift_gap(chain_metrics):
        risk = "Есть неравномерность смен: день и ночь заметно отличаются по ключевым этапам."

    return {
        "main_gap": main_gap,
        "risk": risk,
        "reason": reason,
        "next_shift_control": next_shift_control,
    }
