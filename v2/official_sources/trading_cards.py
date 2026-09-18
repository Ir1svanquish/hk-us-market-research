"""Deterministic standardized trade cards for the isolated V2 report."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping


MIN_RISK_REWARD = 1.5
ATR_MULTIPLIER = 1.5
STRUCTURE_BUFFER_ATR = 0.25

SETUP_LABELS = {
    "breakout": "平台/前高突破",
    "pullback": "结构位回踩",
    "reversal": "止跌反转",
    "event_reassessment": "事件后重建",
    "legacy_plan": "原计划兼容",
}


def _first_price(value: object) -> float | None:
    text = str(value or "").split("（", 1)[0].split("(", 1)[0].split("或", 1)[0]
    text = re.sub(
        r"(?:MA|EMA|SMA|RSI|ATR)\s*(?:低于|高于|回落至|升至|>|<)?\s*\d+(?:\.\d+)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)", text)
    if range_match:
        return (float(range_match.group(1)) + float(range_match.group(2))) / 2
    for match in re.finditer(r"\d+(?:\.\d+)?", text):
        if text[match.end() :].lstrip().startswith("%"):
            continue
        return float(match.group(0))
    return None


def _target_prices(value: object) -> list[float]:
    text = str(value or "")
    if "行情" in text or "###" in text:
        return []
    prices = []
    for part in re.split(r"[，,；;]|突破后|第二目标", text):
        price = _first_price(part)
        if price is not None and "%" not in part:
            prices.append(price)
    return list(dict.fromkeys(prices))[:2]


def _next_trading_day(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def expiry_after_sessions(day: date, sessions: int = 2) -> str:
    result = day
    for _ in range(max(0, sessions)):
        result = _next_trading_day(result)
    return result.isoformat()


def _volume_state(metric: Mapping[str, object]) -> dict:
    contracted = metric.get("volume_confirmation_value")
    contracted_source = str(metric.get("volume_confirmation_source") or "")
    live = metric.get("volume_ratio")
    relative5 = metric.get("relative_volume_5d")
    try:
        contracted_value = float(contracted) if contracted is not None else None
    except (TypeError, ValueError):
        contracted_value = None
    try:
        live_value = float(live) if live is not None else None
    except (TypeError, ValueError):
        live_value = None
    try:
        relative_value = float(relative5) if relative5 is not None else None
    except (TypeError, ValueError):
        relative_value = None
    chosen = contracted_value
    source = contracted_source
    if chosen is None:
        chosen = live_value if live_value is not None else relative_value
        source = "longbridge_live" if live_value is not None else "daily_relative_5d" if relative_value is not None else "missing"
    if chosen is None:
        return {
            "status": "missing",
            "source": source,
            "rule": "仅允许收盘确认，量能缺失不作为否决条件",
        }
    if chosen >= 1.3:
        status, rule = "expanded", "量能达到 1.3×；仍以收盘价格确认"
    elif chosen >= 1.0:
        status, rule = "neutral", "量能达到常态水平；以收盘价格确认"
    elif chosen >= 0.8:
        status, rule = "light", "量能略低于常态；突破/反转需下一交易日守稳"
    else:
        status, rule = "contracted", "量能低于 0.8×；突破/反转需连续两日确认"
    return {"status": status, "source": source, "value": round(chosen, 2), "rule": rule}


def _price_tick(value: float, market: str) -> Decimal:
    if str(market).lower() != "hk":
        return Decimal("0.0001") if abs(value) < 1 else Decimal("0.01")
    magnitude = abs(value)
    if magnitude <= 0.25:
        return Decimal("0.001")
    if magnitude <= 0.5:
        return Decimal("0.005")
    if magnitude <= 10:
        return Decimal("0.01")
    if magnitude <= 20:
        return Decimal("0.02")
    if magnitude <= 100:
        return Decimal("0.05")
    if magnitude <= 200:
        return Decimal("0.1")
    if magnitude <= 500:
        return Decimal("0.2")
    if magnitude <= 1000:
        return Decimal("0.5")
    if magnitude <= 2000:
        return Decimal("1")
    if magnitude <= 5000:
        return Decimal("2")
    return Decimal("5")


def _fmt(value: float | None, market: str = "") -> str:
    if value is None:
        return "—"
    tick = _price_tick(value, market)
    decimal_value = Decimal(str(value))
    rounded = (decimal_value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    digits = max(0, -tick.as_tuple().exponent)
    return f"{rounded:.{digits}f}"


def _as_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _confirmation_suffix(
    volume: Mapping[str, object],
    *,
    price_condition_met: bool = False,
    setup_type: str = "",
) -> str:
    status = str(volume.get("status") or "missing")
    if setup_type == "pullback":
        if price_condition_met:
            if status == "contracted":
                return "；缩量回踩不作否决，下一交易日观察支撑能否继续守稳"
            if status == "expanded":
                return "；回踩承接与量能均已确认，下一交易日观察能否守稳"
            if status == "neutral":
                return "；回踩承接已确认，量能达到常态水平，下一交易日观察能否守稳"
            if status == "light":
                return "；温和缩量回踩不作否决，下一交易日观察支撑能否继续守稳"
            return "；回踩承接已确认；量能数据缺失，下一交易日复核"
        if status in {"contracted", "light"}:
            return "；缩量回踩不作否决，仍需收盘重新转强"
        return "；仍需回踩支撑后收盘重新转强"
    if price_condition_met:
        if status == "contracted":
            return "；价格条件已满足但量能偏弱，需下一交易日守稳后确认"
        if status == "expanded":
            return "；价格与量能条件已满足，下一交易日观察能否守稳"
        if status == "neutral":
            return "；价格与常态量能条件已满足，下一交易日观察能否守稳"
        if status == "light":
            return "；价格条件已满足但量能略弱，需下一交易日守稳后确认"
        return "；价格条件已满足；量能数据缺失，下一交易日复核是否守稳"
    if status == "contracted":
        return "；当前量能偏弱，价格触发后需连续两日确认"
    if status == "light":
        return "；当前量能略弱，价格触发后需下一交易日守稳确认"
    if status == "expanded":
        return "；量能已达到确认要求，仍以收盘有效为准"
    if status == "neutral":
        return "；等待收盘确认"
    return "；量能数据缺失，仅允许收盘后复核"


def _volume_confirms_setup(volume: Mapping[str, object], setup_type: str) -> bool:
    """Apply volume confirmation according to the price-action setup.

    Breakouts and reversals need at least neutral volume. Pullbacks may be
    confirmed on contracted volume because supply drying up near support is a
    valid confirmation rather than a reason to demand expansion. Missing
    volume never overrides an otherwise valid completed-close signal.
    """
    status = str(volume.get("status") or "missing")
    if setup_type == "pullback":
        return status in {"expanded", "neutral", "light", "contracted", "missing"}
    return status in {"expanded", "neutral", "missing"}


def _legacy_levels(
    entry: float | None,
    existing_stop: float | None,
    targets: list[float],
    atr: float | None,
    market: str = "",
) -> dict:
    """Preserve the previous plan only when price-structure data is unavailable."""
    atr_stop = entry - ATR_MULTIPLIER * atr if entry is not None and atr is not None else None
    valid_existing_stop = existing_stop if entry is not None and existing_stop is not None and existing_stop < entry else None
    valid_atr_stop = atr_stop if entry is not None and atr_stop is not None and atr_stop < entry else None
    stop = max(value for value in (valid_existing_stop, valid_atr_stop) if value is not None) if any(
        value is not None for value in (valid_existing_stop, valid_atr_stop)
    ) else None
    risk = entry - stop if entry is not None and stop is not None else None
    target1 = next((value for value in targets if entry is not None and value > entry), None)
    target_basis = "report_target"
    if target1 is None and entry is not None and risk is not None and risk > 0:
        target1 = entry + MIN_RISK_REWARD * risk
        target_basis = "atr_r_multiple_fallback"
    target2 = None
    if entry is not None and risk is not None:
        target2 = next((value for value in targets[1:] if target1 is not None and value > target1), None)
        target2 = max(target2 or 0, entry + 2 * risk, (target1 + risk) if target1 is not None else 0)
    return {
        "setup_type": "legacy_plan",
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "target_basis": target_basis,
        "trigger_basis": "原报告点位；缺少可验证价格结构，仅作兼容参考",
        "invalidation_basis": "原报告风险线与 ATR 缓冲",
        "invalidation_condition": (
            f"收盘跌破 {_fmt(stop, market)} 后原计划失效" if stop is not None else "点位结构不完整，当前计划失效"
        ),
    }


def _structure_levels(
    plan: Mapping[str, object],
    metric: Mapping[str, object],
    source_entry: float | None,
    source_stop: float | None,
    source_targets: list[float],
    completed_close: float | None,
    atr: float | None,
    market: str = "",
) -> dict:
    structure = metric.get("price_structure") if isinstance(metric.get("price_structure"), Mapping) else {}
    if structure.get("status") != "ok":
        return _legacy_levels(source_entry, source_stop, source_targets, atr, market)

    entry_text = str(plan.get("entry") or "")
    breakout_words = ("突破", "站上", "上穿", "平台上沿", "前高")
    pullback_words = ("回踩", "低吸", "接近", "附近", "承接")
    reversal_words = ("反转", "止跌", "收复", "不再创新低")
    close = completed_close
    retest = _as_float(structure.get("retest_level"))
    breakout = _as_float(structure.get("breakout_level"))
    reversal = _as_float(structure.get("recent_high_5d"))
    swing_low = _as_float(structure.get("last_swing_low"))
    support = _as_float(structure.get("support_level"))
    trend = str(structure.get("trend") or "")
    breakout_state = str(structure.get("breakout_state") or "")

    if any(word in entry_text for word in breakout_words) or breakout_state in {"above_range", "testing_range_high"}:
        setup = "breakout"
    elif any(word in entry_text for word in pullback_words):
        setup = "pullback"
    elif any(word in entry_text for word in reversal_words) or trend == "lower_high_lower_low":
        setup = "reversal"
    elif trend == "higher_high_higher_low" and None not in (close, retest, atr) and abs(close - retest) <= atr:
        setup = "pullback"
    else:
        setup = "breakout"

    if setup == "breakout":
        entry = breakout or source_entry or close
        anchor = entry
        buffer_atr = 0.5
        trigger_basis = "近20日平台上沿/前高"
        price_condition_met = entry is not None and close is not None and close >= entry
        condition = (
            f"已突破 {_fmt(entry, market)}（{trigger_basis}）"
            if price_condition_met
            else f"收盘站上 {_fmt(entry, market)}（{trigger_basis}）"
            if entry is not None
            else "等待形成可验证的平台突破位"
        )
        invalidation_basis = "突破位下方 ATR 缓冲"
    elif setup == "pullback":
        entry = retest or source_entry or support or close
        anchor = entry
        buffer_atr = 0.35
        latest_open = _as_float(structure.get("latest_open"))
        latest_low = _as_float(structure.get("latest_low"))
        previous_close = _as_float(structure.get("previous_close"))
        tolerance = max((atr or 0) * 0.5, (entry or 0) * 0.005)
        tested_support = (
            entry is not None
            and latest_low is not None
            and latest_low <= entry + tolerance
        )
        held_support = entry is not None and close is not None and close >= entry
        turned_stronger = close is not None and (
            (latest_open is not None and close > latest_open)
            or (previous_close is not None and close >= previous_close)
        )
        price_condition_met = tested_support and held_support and turned_stronger
        trigger_basis = "原突破位、最近枢轴或成交承接区"
        condition = (
            f"已回踩 {_fmt(entry, market)}（{trigger_basis}）并收盘重新转强"
            if price_condition_met
            else f"回踩 {_fmt(entry, market)}（{trigger_basis}）不破并收盘转强"
            if entry is not None else "等待回踩结构支撑后出现承接"
        )
        invalidation_basis = "回踩结构位下方 ATR 缓冲"
    else:
        entry = reversal or source_entry or close
        anchor = swing_low or support
        buffer_atr = STRUCTURE_BUFFER_ATR
        price_condition_met = entry is not None and close is not None and close >= entry
        trigger_basis = "近5日反转确认位"
        condition = (
            f"已收盘站上 {_fmt(entry, market)}（{trigger_basis}），继续确认低点不再下移"
            if price_condition_met
            else f"停止创新低并收盘站上 {_fmt(entry, market)}（{trigger_basis}）"
            if entry is not None else "等待停止创新低并收复最近反转确认位"
        )
        invalidation_basis = "最近有效低点下方 ATR 缓冲"

    buffer = max((atr or 0) * buffer_atr, (entry or close or 0) * 0.005)
    stop = anchor - buffer if anchor is not None and buffer > 0 else None
    if entry is not None and (stop is None or stop >= entry):
        fallback_risk = max((atr or 0) * 0.75, entry * 0.01)
        stop = entry - fallback_risk if fallback_risk > 0 else None
    risk = entry - stop if entry is not None and stop is not None else None

    next_resistance = _as_float(structure.get("next_resistance"))
    target1 = next_resistance if entry is not None and next_resistance is not None and next_resistance > entry else None
    target_basis = "next_structural_resistance"
    if target1 is None and entry is not None and risk is not None and risk > 0:
        target1 = entry + MIN_RISK_REWARD * risk
        target_basis = "structure_r_multiple_fallback"
    target2 = entry + 2 * risk if entry is not None and risk is not None else None
    if target1 is not None and risk is not None:
        target2 = max(target2 or 0, target1 + risk)

    invalidation_condition = (
        f"收盘跌破 {_fmt(stop, market)}（{invalidation_basis}），原{SETUP_LABELS[setup]}逻辑失效"
        if stop is not None else "无法形成可验证风险线，当前计划失效"
    )
    return {
        "setup_type": setup,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "target_basis": target_basis,
        "trigger_basis": trigger_basis,
        "trigger_condition": condition,
        "price_condition_met": price_condition_met,
        "invalidation_basis": invalidation_basis,
        "invalidation_condition": invalidation_condition,
        "price_structure": dict(structure),
    }


def build_trade_card(
    symbol: str,
    assessment: Mapping[str, object],
    metric: Mapping[str, object],
    *,
    as_of: date,
) -> dict:
    plan = assessment.get("raw_trade_plan") if isinstance(assessment.get("raw_trade_plan"), dict) else {}
    gate = assessment.get("final_gate") if isinstance(assessment.get("final_gate"), dict) else {}
    event_gate = assessment.get("event_gate") if isinstance(assessment.get("event_gate"), dict) else {}
    gate_priority = int(gate.get("priority") or 0)
    event_priority = int(event_gate.get("priority") or 0)
    market = str(assessment.get("market") or "").lower()
    action = str(assessment.get("v1_action") or "")
    entry = _first_price(plan.get("entry"))
    existing_stop = _first_price(plan.get("stop"))
    targets = _target_prices(plan.get("target"))
    snapshot = _first_price(plan.get("snapshot_price"))
    completed_close = metric.get("completed_close")
    atr = metric.get("atr14")
    try:
        completed_close = float(completed_close) if completed_close is not None else None
    except (TypeError, ValueError):
        completed_close = None
    try:
        atr = float(atr) if atr is not None else None
    except (TypeError, ValueError):
        atr = None

    checks: list[dict] = []
    warnings: list[str] = []
    price_consistent = True
    if snapshot and completed_close:
        price_gap = abs(snapshot / completed_close - 1)
        price_consistent = price_gap <= 0.15
        checks.append({"name": "行情价格一致", "passed": price_consistent, "detail": f"偏差 {price_gap:.1%}"})
        if not price_consistent:
            warnings.append("正式报告价格与长桥完整日线偏差超过 15%，交易点位暂停使用")

    volume = _volume_state(metric)
    levels = _structure_levels(
        plan,
        metric,
        entry,
        existing_stop,
        targets,
        completed_close,
        atr,
        market,
    )
    entry = levels.get("entry")
    stop = levels.get("stop")
    target1 = levels.get("target1")
    target2 = levels.get("target2")
    target_basis = str(levels.get("target_basis") or "")
    risk = entry - stop if entry is not None and stop is not None else None
    reward = target1 - entry if entry is not None and target1 is not None else None
    risk_reward = reward / risk if risk and reward is not None and risk > 0 else None

    structure_ok = all(value is not None for value in (entry, stop, target1)) and stop < entry < target1
    rr_ok = risk_reward is not None and risk_reward >= MIN_RISK_REWARD
    price_condition_met = bool(levels.get("price_condition_met"))
    setup_type = str(levels.get("setup_type") or "legacy_plan")
    volume_ok = _volume_confirms_setup(volume, setup_type)
    checks.extend(
        [
            {"name": "止损/触发/目标方向", "passed": structure_ok},
            {
                "name": "预期盈亏比",
                "passed": rr_ok,
                "detail": f"1:{risk_reward:.2f}" if risk_reward is not None else "无法计算",
            },
            {
                "name": "价格触发条件已满足",
                "passed": price_condition_met,
                "detail": str(levels.get("trigger_condition") or "等待价格确认"),
            },
            {
                "name": "量能适配当前形态",
                "passed": volume_ok,
                "detail": str(volume.get("rule") or "等待量能确认"),
            },
            {"name": "事件与质量闸门允许", "passed": gate_priority < 3},
        ]
    )

    trigger_condition = str(levels.get("trigger_condition") or "")
    if not trigger_condition:
        if entry is None:
            trigger_condition = "原报告没有可解析的触发价"
        elif levels.get("setup_type") == "legacy_plan" and any(
            word in str(plan.get("entry") or "") for word in ("回踩", "附近", "低吸", "接近")
        ):
            trigger_condition = f"回踩 {_fmt(entry, market)} 附近后收盘重新站稳"
        else:
            trigger_condition = f"收盘站上 {_fmt(entry, market)}"
    trigger_condition += _confirmation_suffix(
        volume,
        price_condition_met=price_condition_met,
        setup_type=setup_type,
    )

    if gate_priority >= 5:
        status, label = "blocked", "重大风险，禁止新开仓"
    elif action == "持有":
        status, label = "holding", "持有管理，不加仓" if gate_priority >= 3 else "持有管理"
    elif action in {"卖出", "减仓"}:
        status, label = "reduce", "减仓/退出管理"
    elif event_priority >= 3 and event_priority >= gate_priority:
        status, label = "event_wait", "事件前暂停新开仓" if event_priority >= 4 else "事件前条件观察"
    elif gate_priority == 2:
        status, label = "reassess", "旧计划失效，重新评估"
    elif gate_priority >= 3:
        status, label = "quality_wait", "等待条件确认"
    elif action != "买入":
        status, label = "watch", "观察"
    elif not price_consistent:
        status, label = "data_conflict", "行情冲突，暂停计划"
    elif not structure_ok:
        status, label = "invalid_structure", "点位不完整，取消计划"
    elif not rr_ok:
        status, label = "invalid_risk_reward", "盈亏比不足，取消计划"
    elif not price_condition_met:
        status, label = "trigger_wait", "等待价格确认"
    elif not volume_ok:
        status, label = "trigger_wait", "等待量能确认"
    else:
        status = "ready"
        if setup_type == "pullback" and volume.get("status") in {"light", "contracted"}:
            label = "缩量回踩确认"
        elif volume.get("status") == "neutral":
            label = "条件买入（量能中性）"
        elif volume.get("status") == "missing":
            label = "条件买入（量能待复核）"
        else:
            label = "条件买入"

    active = status == "ready"
    invalidation = []
    if gate_priority >= 2:
        invalidation.append(str(gate.get("reason") or "官方事件要求重新评估"))
    if gate.get("gate_until"):
        invalidation.append(f"闸门有效至 {gate.get('gate_until')}")
    invalidation.extend(warnings)
    structural_invalidation = str(levels.get("invalidation_condition") or "技术结构破坏时原逻辑失效")
    invalidation.append(structural_invalidation)
    invalidation.append("出现新的重大公告或数据冲突时重新评估")
    setup_type = "event_reassessment" if event_priority >= 3 else setup_type
    if setup_type == "event_reassessment":
        watch_condition = "事件落地并被价格消化后，以事件日高低点和首个整理区间重建触发与风险线"
        trigger_basis = "事件日区间；事件落地前不启用普通技术触发"
    else:
        watch_condition = trigger_condition
        trigger_basis = str(levels.get("trigger_basis") or "")
    gap_rule = (
        f"高开超过 {_fmt(entry + 0.5 * atr, market)} 不追；低开跌破 {_fmt(stop, market)} 不加仓并执行风险退出"
        if entry is not None and atr is not None and stop is not None
        else "跳空越过触发/止损位时不追价、不摊平，等待重新评估"
    )
    return {
        "symbol": symbol,
        "market": assessment.get("market"),
        "name": assessment.get("name"),
        "status": status,
        "label": label,
        "active": active,
        "setup_type": setup_type,
        "setup_label": SETUP_LABELS.get(setup_type, setup_type),
        "trigger_price": round(entry, 6) if active and entry is not None else None,
        "reference_entry": round(entry, 6) if entry is not None else None,
        "source_reference_entry": round(_first_price(plan.get("entry")), 6) if _first_price(plan.get("entry")) is not None else None,
        "trigger_condition": trigger_condition if active else "当前不启用主动买入触发条件",
        "watch_condition": watch_condition,
        "trigger_basis": trigger_basis,
        "price_condition_met": price_condition_met,
        "volume_confirmation": volume,
        "volume_ratio_live": metric.get("volume_ratio"),
        "relative_volume_5d": metric.get("relative_volume_5d"),
        "relative_volume_20d": metric.get("relative_volume_20d"),
        "atr14": round(atr, 6) if atr is not None else None,
        "atr_multiplier": ATR_MULTIPLIER,
        "stop_loss": round(stop, 6) if stop is not None else None,
        "target_1": round(target1, 6) if target1 is not None else None,
        "target_2": round(target2, 6) if target2 is not None else None,
        "target_basis": target_basis,
        "invalidation_condition": structural_invalidation,
        "invalidation_basis": levels.get("invalidation_basis"),
        "price_structure": levels.get("price_structure") or {},
        "risk_reward": round(risk_reward, 3) if risk_reward is not None else None,
        "minimum_risk_reward": MIN_RISK_REWARD,
        "valid_sessions": 2,
        "expires_at": expiry_after_sessions(as_of, 2) if active else "",
        "position_plan": plan.get("position") if active or status == "holding" else "",
        "gap_rule": gap_rule,
        "invalidation_conditions": invalidation,
        "checks": checks,
        "metric_status": metric.get("status") or "missing",
        "metric_fetched_at": metric.get("fetched_at"),
    }


def build_trading_cards(
    assessments: Mapping[str, Mapping[str, object]],
    market_metrics: Mapping[str, Mapping[str, object]],
    *,
    as_of: date,
) -> dict[str, dict]:
    return {
        symbol: build_trade_card(
            symbol,
            assessment,
            market_metrics.get(symbol, {}),
            as_of=as_of,
        )
        for symbol, assessment in assessments.items()
    }
