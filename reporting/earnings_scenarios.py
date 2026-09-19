"""Deterministic per-stock earnings scenarios for the formal report.

This is a scenario engine, not a point forecast.  It combines an official or
provider calendar event, available consensus/history fields, the existing
earnings outlook, and price-structure levels.  It never invents an estimate
when the source does not provide one.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

try:
    import exchange_calendars as xcals
except ImportError:  # pragma: no cover - production dependency, guarded fallback
    xcals = None


POSITIVE_WORDS = (
    "超预期",
    "上调",
    "增长",
    "改善",
    "强劲",
    "稳健",
    "受益",
    "高景气",
)
NEGATIVE_WORDS = (
    "低于预期",
    "下调",
    "下滑",
    "亏损",
    "承压",
    "放缓",
    "恶化",
)


def _day(value: object) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _fmt_price(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _fmt_revenue(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}bn"
    if absolute >= 1_000_000:
        return f"{number / 1_000_000:.2f}m"
    return _fmt(number)


def _clean_outlook(value: object) -> str:
    text = str(value or "")
    clauses = [part.strip() for part in re.split(r"[。；;]", text) if part.strip()]
    kept = [
        part
        for part in clauses
        if not any(marker in part for marker in ("数据缺失", "无法判断", "具体数据待披露"))
    ]
    return "；".join(kept)[:150]


def _stock_event(event: Mapping[str, Any], symbol: str, market: str) -> bool:
    raw = str(event.get("symbol") or "").upper()
    if market == "hk":
        left = "".join(character for character in raw if character.isdigit()).zfill(5)
        right = "".join(character for character in symbol if character.isdigit()).zfill(5)
        return left == right
    return raw.replace(".", "-") == symbol.upper().replace(".", "-")


def _nearest_calendar_event(
    events: Iterable[Mapping[str, Any]], symbol: str, market: str, report_date: date
) -> tuple[Mapping[str, Any] | None, int | None]:
    candidates: list[tuple[int, Mapping[str, Any]]] = []
    for event in events:
        if event.get("event_type") != "earnings_calendar" or not _stock_event(event, symbol, market):
            continue
        effective = _day(event.get("effective_at"))
        if effective is None:
            continue
        days = (effective - report_date).days
        if 0 <= days <= 14:
            candidates.append((days, event))
    if not candidates:
        return None, None
    days, event = min(candidates, key=lambda value: value[0])
    return event, days


def _history_stats(metadata: Mapping[str, Any]) -> dict[str, Any]:
    history = metadata.get("earnings_history") if isinstance(metadata.get("earnings_history"), list) else []
    surprises = []
    details = []
    for item in history[:4]:
        if not isinstance(item, Mapping):
            continue
        surprise = _number(item.get("surprisePercent"))
        if surprise is not None:
            surprises.append(surprise)
            outcome = "超预期" if surprise > 0.01 else "低于预期" if surprise < -0.01 else "符合预期"
            details.append(
                {
                    "period": str(item.get("period") or ""),
                    "actual": _number(item.get("actual")),
                    "estimate": _number(item.get("estimate")),
                    "surprise_pct": round(surprise, 2),
                    "outcome": outcome,
                }
            )
    return {
        "total": len(surprises),
        "beats": sum(value > 0.01 for value in surprises),
        "meets": sum(abs(value) <= 0.01 for value in surprises),
        "misses": sum(value < -0.01 for value in surprises),
        "average_surprise_pct": round(sum(surprises) / len(surprises), 2) if surprises else None,
        "details": details,
    }


def _trading_sessions_until(report_date: date, event_date: date, market: str) -> int:
    """Count exchange sessions strictly after the report date through the event date."""
    if event_date <= report_date:
        return 0
    exchange = "XHKG" if market == "hk" else "XNYS"
    if xcals is not None:
        try:
            calendar = xcals.get_calendar(exchange)
            return int(len(calendar.sessions_in_range(report_date + timedelta(days=1), event_date)))
        except Exception:
            pass
    cursor = report_date + timedelta(days=1)
    count = 0
    while cursor <= event_date:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def _research_history(default: Mapping[str, Any], research: Mapping[str, Any]) -> dict[str, Any]:
    supplied = research.get("surprise_history")
    if not isinstance(supplied, Mapping):
        return dict(default)
    details = [dict(item) for item in supplied.get("details", []) if isinstance(item, Mapping)]
    total = int(supplied.get("sample_size") or supplied.get("total") or len(details) or 0)
    beats = int(supplied.get("beats") or 0)
    meets = int(supplied.get("meets") or 0)
    misses = int(supplied.get("misses") or max(0, total - beats - meets))
    return {
        "total": total,
        "beats": beats,
        "meets": meets,
        "misses": misses,
        "average_surprise_pct": _number(supplied.get("average_surprise_pct")),
        "details": details,
        "basis": str(supplied.get("basis") or "可比口径相对公开预期"),
        "coverage_note": str(supplied.get("coverage_note") or ""),
    }


def _scenario_probabilities(
    *,
    history: Mapping[str, Any],
    prior: str,
    research: Mapping[str, Any],
) -> tuple[list[int], str]:
    supplied = research.get("scenario_probabilities")
    if isinstance(supplied, Mapping):
        values = [int(supplied.get(key) or 0) for key in ("upside", "base", "downside")]
        if sum(values) == 100 and all(value >= 0 for value in values):
            return values, str(
                supplied.get("method")
                or "结合历史兑现、已披露指引与当前一致预期的条件概率估计"
            )

    upside, base, downside = 30, 45, 25
    total = int(history.get("total") or 0)
    if total:
        net = int(history.get("beats") or 0) - int(history.get("misses") or 0)
        adjustment = max(-12, min(12, net * 4))
        upside += adjustment
        downside -= adjustment
        average = _number(history.get("average_surprise_pct"))
        if average is not None:
            surprise_adjustment = max(-6, min(6, round(average / 2)))
            upside += surprise_adjustment
            downside -= surprise_adjustment
    if prior == "偏积极":
        upside += 3
        base -= 2
        downside -= 1
    elif prior == "偏谨慎":
        upside -= 2
        base -= 1
        downside += 3

    values = [max(12, upside), max(25, base), max(12, downside)]
    scale = 100 / sum(values)
    rounded = [round(value * scale) for value in values]
    rounded[1] += 100 - sum(rounded)
    return rounded, "历史超预期频率经小样本收缩，并结合公开业绩线索；非期权市场隐含概率"


def _research_is_usable(research: Mapping[str, Any], report_date: date) -> bool:
    as_of = _day(research.get("as_of"))
    return as_of is None or as_of <= report_date


def _prior(
    earnings_outlook: str,
    history: Mapping[str, Any],
    recommendation: Mapping[str, Any],
) -> tuple[str, str]:
    score = 0.0
    total = int(history.get("total") or 0)
    beats = int(history.get("beats") or 0)
    if total:
        score += (beats / total - 0.5) * 2.4
        average = _number(history.get("average_surprise_pct"))
        if average is not None:
            score += max(-0.8, min(0.8, average / 5))
    text = str(earnings_outlook or "")
    score += min(2, sum(word in text for word in POSITIVE_WORDS)) * 0.35
    score -= min(2, sum(word in text for word in NEGATIVE_WORDS)) * 0.45

    buy = sum(_number(recommendation.get(key)) or 0 for key in ("strongBuy", "buy"))
    all_ratings = buy + sum(_number(recommendation.get(key)) or 0 for key in ("hold", "sell", "strongSell"))
    if all_ratings:
        score += max(-0.5, min(0.5, (buy / all_ratings - 0.5) * 1.5))

    label = "偏积极" if score >= 0.65 else "偏谨慎" if score <= -0.65 else "中性"
    if total >= 3 and all_ratings:
        confidence = "中"
    elif total >= 3 or earnings_outlook:
        confidence = "中低"
    else:
        confidence = "低"
    return label, confidence


def _level(card: Mapping[str, Any], structure: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = card.get(key) if key in card else structure.get(key)
        number = _number(value)
        if number is not None:
            return number
    return None


def build_earnings_scenario(
    *,
    symbol: str,
    market: str,
    report_date: date,
    earnings_outlook: str,
    events: Iterable[Mapping[str, Any]],
    trade_card: Mapping[str, Any],
    research: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    research = research if isinstance(research, Mapping) and _research_is_usable(research, report_date) else {}
    event, days = _nearest_calendar_event(events, symbol, market, report_date)
    confirmed_event_day = _day(research.get("event_date"))
    if confirmed_event_day is not None and 0 <= (confirmed_event_day - report_date).days <= 14:
        event = dict(event or {})
        event["effective_at"] = confirmed_event_day.isoformat()
        event["effective_session"] = str(
            research.get("effective_session") or event.get("effective_session") or "all_day"
        )
        event["certainty"] = str(research.get("certainty") or "official_confirmed")
        event["source"] = str(research.get("event_source") or event.get("source") or "")
        days = (confirmed_event_day - report_date).days
    if event is None:
        return {"status": "not_applicable"}

    metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    history = _research_history(_history_stats(metadata), research)
    recommendation = metadata.get("recommendation") if isinstance(metadata.get("recommendation"), Mapping) else {}
    prior, confidence = _prior(earnings_outlook, history, recommendation)
    if research.get("prior") in {"偏积极", "中性", "偏谨慎"}:
        prior = str(research["prior"])
    if research.get("confidence") in {"高", "中高", "中", "中低", "低"}:
        confidence = str(research["confidence"])
    structure = trade_card.get("price_structure") if isinstance(trade_card.get("price_structure"), Mapping) else {}
    upside_level = _level(trade_card, structure, "target_1", "next_resistance", "breakout_level")
    base_level = _level(trade_card, structure, "reference_entry", "retest_level", "support_level")
    downside_level = _level(trade_card, structure, "stop_loss", "support_level", "range_low_20d")
    eps_estimate = metadata.get("eps_estimate")
    revenue_estimate = metadata.get("revenue_estimate")
    consensus = research.get("consensus") if isinstance(research.get("consensus"), Mapping) else {}
    if consensus.get("eps") is not None:
        eps_estimate = consensus.get("eps")
    if consensus.get("revenue") is not None:
        revenue_estimate = consensus.get("revenue")
    event_day = _day(event.get("effective_at"))
    trading_days = _trading_sessions_until(report_date, event_day, market) if event_day else None
    timing = {"premarket": "盘前", "afterhours": "盘后", "all_day": "时段待定"}.get(
        str(event.get("effective_session") or ""), "时段待定"
    )
    if research.get("timing"):
        timing = str(research.get("timing"))
    estimate_bits = []
    if eps_estimate is not None:
        estimate_bits.append(f"EPS一致预期 {_fmt(eps_estimate)}")
    if revenue_estimate is not None:
        estimate_bits.append(f"营收一致预期 {_fmt_revenue(revenue_estimate)}")
    if history["total"]:
        estimate_bits.append(
            f"近{history['total']}次：超预期{history['beats']}、符合{history.get('meets', 0)}、低于{history.get('misses', 0)}"
        )

    probabilities, probability_method = _scenario_probabilities(
        history=history,
        prior=prior,
        research=research,
    )
    thresholds = research.get("scenario_thresholds") if isinstance(research.get("scenario_thresholds"), Mapping) else {}
    previous_report = research.get("previous_report") if isinstance(research.get("previous_report"), Mapping) else {}
    company_guidance = [
        dict(item) for item in research.get("company_guidance", []) if isinstance(item, Mapping)
    ]
    key_checks = [str(value) for value in research.get("key_checks", []) if str(value).strip()]
    sources = [dict(item) for item in research.get("sources", []) if isinstance(item, Mapping)]
    scenario_specs = (
        (
            "超预期",
            "upside",
            "核心指标高于一致预期且管理层指引上调或保持强劲",
            f"事件后放量站稳 {_fmt_price(upside_level)}，机会成熟度上调；未站稳不追高",
        ),
        (
            "符合预期",
            "base",
            "核心指标大致符合预期，指引未明显上修或下修",
            f"观察 {_fmt_price(base_level)} 附近承接与相对强弱，确认后沿用结构计划",
        ),
        (
            "低于预期",
            "downside",
            "核心指标低于预期、指引下调或关键业务增速明显放缓",
            f"若收盘跌破 {_fmt_price(downside_level)}，原机会逻辑失效；不因低开直接抄底",
        ),
    )
    scenarios = []
    for index, (name, key, default_condition, decision) in enumerate(scenario_specs):
        supplied = thresholds.get(key)
        if isinstance(supplied, Mapping):
            condition = str(supplied.get("condition") or default_condition)
            result = str(supplied.get("result") or "")
        else:
            condition = str(supplied or default_condition)
            result = ""
        scenarios.append(
            {
                "name": name,
                "probability_pct": probabilities[index],
                "condition": condition,
                "result": result,
                "decision": decision,
            }
        )

    return {
        "status": "upcoming",
        "event_date": str(event.get("effective_at") or "")[:10],
        "days_to_event": days,
        "calendar_days_to_event": days,
        "trading_days_to_event": trading_days,
        "detailed": trading_days is not None and 0 <= trading_days <= 3,
        "timing": timing,
        "certainty": event.get("certainty"),
        "event_source": str(event.get("source") or ""),
        "period": str(research.get("period") or metadata.get("period") or ""),
        "event_kind": str(research.get("event_kind") or "full_release"),
        "prior": prior,
        "confidence": confidence,
        "consensus_eps": _number(eps_estimate),
        "consensus_revenue": _number(revenue_estimate),
        "consensus": dict(consensus),
        "history": history,
        "previous_report": dict(previous_report),
        "company_guidance": company_guidance,
        "key_checks": key_checks,
        "sources": sources,
        "research_as_of": str(research.get("as_of") or ""),
        "probability_method": probability_method,
        "baseline": "；".join(estimate_bits) or _clean_outlook(earnings_outlook) or "重点验证收入、利润、管理层指引与事件后价格反应",
        "scenarios": scenarios,
    }
