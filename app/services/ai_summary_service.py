import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DailyChainMetric, ProductWagonMetric, ReportPackage
from app.services.insight_service import build_management_insight
from app.services.product_wagon_service import false_park_coverage_warnings


SUMMARY_KEYS = {
    "main_gap",
    "product_risk",
    "wagon_risk",
    "documentation_risk",
    "next_shift_recommendation",
}


SYSTEM_PROMPT = """
Ты управленческий аналитик железнодорожной станции.
Используй только переданные JSON-данные.
Не придумывай цифры, факты, даты, продукты или причины.
Если данных недостаточно, прямо напиши "недостаточно данных".
Пиши кратко, по-деловому, без художественных формулировок.
Ответ верни строго JSON-объектом с ключами:
main_gap, product_risk, wagon_risk, documentation_risk, next_shift_recommendation.
""".strip()


def _safe_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100, 1)


def _latest_chain_metric(db: Session, package_id: int) -> DailyChainMetric | None:
    metric = db.scalars(
        select(DailyChainMetric)
        .where(DailyChainMetric.package_id == package_id)
        .where(DailyChainMetric.shift_type == "сутки")
        .order_by(DailyChainMetric.created_at.desc())
    ).first()
    if metric is not None:
        return metric
    return db.scalars(
        select(DailyChainMetric)
        .where(DailyChainMetric.package_id == package_id)
        .order_by(DailyChainMetric.created_at.desc())
    ).first()


def _calculated_payload(db: Session, package_id: int) -> dict[str, Any]:
    package = db.get(ReportPackage, package_id)
    chain = _latest_chain_metric(db, package_id)
    product_metrics = db.scalars(
        select(ProductWagonMetric)
        .where(ProductWagonMetric.package_id == package_id)
        .order_by(ProductWagonMetric.wagon_group, ProductWagonMetric.product)
    ).all()

    product_payload = [
        {
            "product": metric.product,
            "wagon_group": metric.wagon_group,
            "planned_loading_wagons": metric.planned_loading_wagons,
            "planned_loading_tons": metric.planned_loading_tons,
            "product_stock_tons": metric.product_stock_tons,
            "available_wagons_total": metric.available_wagons_total,
            "available_wagons_good": metric.available_wagons_good,
            "available_wagons_bad": metric.available_wagons_bad,
            "loaded_wagons": metric.loaded_wagons,
            "loaded_tons": metric.loaded_tons,
            "documented_wagons": metric.documented_wagons,
            "documented_tons": metric.documented_tons,
            "sent_wagons": metric.sent_wagons,
            "wagon_balance": metric.wagon_balance,
            "wagon_coverage_percent": metric.wagon_coverage_percent,
            "main_limitation": metric.main_limitation,
        }
        for metric in product_metrics
        if metric.main_limitation != "нет данных"
    ]

    return {
        "package": {
            "id": package.id if package else package_id,
            "report_date": package.report_date.isoformat() if package and package.report_date else None,
            "status": package.status if package else None,
        },
        "chain_metrics": None
        if chain is None
        else {
            "shift_type": chain.shift_type,
            "wagons_in_surgut": chain.wagons_in_surgut,
            "arrived_prom": chain.arrived_prom,
            "processed_prom": chain.processed_prom,
            "loaded_wagons": chain.loaded_wagons,
            "documented_wagons": chain.documented_wagons,
            "sent_surgut": chain.sent_surgut,
            "documentation_ratio_percent": _safe_ratio(chain.documented_wagons, chain.loaded_wagons),
            "dispatch_ratio_percent": _safe_ratio(chain.sent_surgut, chain.documented_wagons),
            "throughput_ratio_percent": _safe_ratio(chain.sent_surgut, chain.wagons_in_surgut),
        },
        "product_wagon_metrics": product_payload,
        "false_park_coverage_warnings": false_park_coverage_warnings(product_metrics),
    }


def _fallback(db: Session, package_id: int, source: str = "rule_based") -> dict:
    insight = build_management_insight(db, package_id)
    return {
        "source": source,
        "main_gap": insight["main_gap"],
        "product_risk": insight["reason"],
        "wagon_risk": insight["risk"],
        "documentation_risk": insight["risk"],
        "next_shift_recommendation": insight["next_shift_control"],
    }


def _normalize_ai_result(result: dict[str, Any]) -> dict:
    normalized = {key: str(result.get(key) or "недостаточно данных") for key in SUMMARY_KEYS}
    normalized["source"] = "ai"
    return normalized


def _looks_too_technical(result: dict[str, Any]) -> bool:
    text = " ".join(str(result.get(key) or "") for key in SUMMARY_KEYS)
    technical_tokens = (
        "processed_prom",
        "loaded_wagons",
        "documented_wagons",
        "sent_wagons",
        "available_wagons",
        "wagon_coverage",
        "null",
    )
    return any(token in text for token in technical_tokens)


def build_ai_or_rule_based_summary(db: Session, package_id: int) -> dict:
    if not settings.use_ai_summary or not settings.openai_api_key:
        return _fallback(db, package_id)

    try:
        from openai import OpenAI
    except ImportError:
        return _fallback(db, package_id, source="rule_based_openai_sdk_missing")

    payload = _calculated_payload(db, package_id)
    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Сформируй краткий управленческий вывод по этим рассчитанным JSON-данным:\n"
                    + json.dumps(payload, ensure_ascii=False),
                },
            ],
            temperature=0,
        )
        parsed = json.loads(response.output_text)
        if _looks_too_technical(parsed):
            return _fallback(db, package_id, source="rule_based_ai_too_technical")
        return _normalize_ai_result(parsed)
    except Exception:
        return _fallback(db, package_id, source="rule_based_ai_error")
