"""Public-report opportunity ranking with a separate execution review.

This module is intentionally part of the formal report pipeline.  It
uses the structured 2026 report inputs already consumed by ``research_report``
and writes only explicit reporting output artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from reporting.official_sources.market_metrics import calculate_history_metrics
from reporting.research_report import (
    DEFAULT_DB,
    DEFAULT_REVIEW,
    MarketResearch,
    ResearchStock,
    load_stocks,
    parse_market_research,
)


WEIGHTS = {
    "technical_composite": 45,
    "catalyst_expectation": 20,
    "fundamental_valuation": 15,
    "event_news_value": 10,
    "sentiment_crowding": 5,
    "data_trust": 5,
}

TECHNICAL_WEIGHTS = {
    "price_structure": 35,
    "relative_strength_sector": 25,
    "volume_confirmation": 20,
    "volatility_risk": 15,
    "auxiliary_indicators": 5,
}

THEMES = {
    "半导体/算力": ("半导体", "芯片", "存储", "dram", "hbm", "光模块", "光通信", "ai算力", "ai硬件", "数据中心"),
    "平台/软件": ("互联网", "平台", "软件", "云计算", "智能体", "大数据"),
    "消费电子": ("消费电子", "智能手机", "iphone", "可穿戴", "终端硬件", "折叠屏"),
    "防御/价值": ("金融", "银行", "保险", "能源", "石油", "工业", "高股息", "防御", "蓝筹"),
    "消费": ("消费", "零售", "电商", "广告"),
    "新能源": ("新能源", "汽车", "电池", "太阳能", "储能"),
    "医疗": ("医疗", "医药", "glp-1"),
    "通信": ("通信", "移动核心网", "卫星"),
    "加密金融": ("加密", "稳定币", "区块链"),
}

POSITIVE = ("超预期", "上调", "增长", "强劲", "向好", "高景气", "回购", "受益", "领先", "龙头", "突破", "改善")
NEGATIVE = ("低于预期", "下滑", "亏损", "承压", "走弱", "回调", "稀释", "减持", "缺失", "无法判断")
MARKET_POSITIVE = ("主线", "最强", "独立行情", "重点关注", "转向", "走强", "受益", "防御", "景气")
MARKET_NEGATIVE = ("回避", "回调", "压制", "转弱", "调整", "获利了结")
OFFICIAL_WORDS = ("公告", "财报", "业绩", "指引", "回购", "合同", "监管", "sec", "评级", "omdia", "finnhub")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _number(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _first_price(value: Any) -> float | None:
    text = str(value or "").split("（", 1)[0].split("(", 1)[0].split("或", 1)[0]
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)", text)
    if range_match:
        return (float(range_match.group(1)) + float(range_match.group(2))) / 2
    return _number(text)


def _percentile(values: dict[str, float | None]) -> dict[str, float]:
    valid = sorted((value, symbol) for symbol, value in values.items() if value is not None)
    if not valid:
        return {symbol: 0.5 for symbol in values}
    if len(valid) == 1:
        return {symbol: 0.5 for symbol in values}
    ranks = {symbol: index / (len(valid) - 1) for index, (_, symbol) in enumerate(valid)}
    return {symbol: ranks.get(symbol, 0.5) for symbol in values}


def _price_context(db_path: Path, report_date: str, market: str) -> dict[str, dict[str, Any]]:
    con = sqlite3.connect(str(db_path))
    market_clause = "code like 'HK%'" if market == "hk" else "code not like 'HK%'"
    rows = con.execute(
        f"""
        SELECT code,date,open,high,low,close,volume,amount
        FROM stock_daily
        WHERE date<=? AND {market_clause}
        ORDER BY code,date
        """,
        (report_date,),
    ).fetchall()
    con.close()
    histories: dict[str, list[dict[str, Any]]] = {}
    for symbol, day, open_price, high, low, close, volume, amount in rows:
        if close is not None:
            histories.setdefault(symbol, []).append(
                {
                    "date": str(day),
                    "open": float(open_price) if open_price is not None else None,
                    "high": float(high) if high is not None else None,
                    "low": float(low) if low is not None else None,
                    "close": float(close),
                    "volume": float(volume) if volume is not None else None,
                    "amount": float(amount) if amount is not None else None,
                }
            )
    ret5: dict[str, float | None] = {}
    ret20: dict[str, float | None] = {}
    amounts: dict[str, float | None] = {}
    for symbol, history in histories.items():
        latest = history[-1]
        ret5[symbol] = latest["close"] / history[-6]["close"] - 1 if len(history) >= 6 else None
        ret20[symbol] = latest["close"] / history[-21]["close"] - 1 if len(history) >= 21 else None
        amounts[symbol] = latest["amount"]
    p5, p20, pa = _percentile(ret5), _percentile(ret20), _percentile(amounts)
    result: dict[str, dict[str, Any]] = {}
    report_day = date.fromisoformat(report_date)
    for symbol, history in histories.items():
        metrics = calculate_history_metrics(
            [
                SimpleNamespace(
                    timestamp=item["date"],
                    open=item["open"],
                    high=item["high"],
                    low=item["low"],
                    close=item["close"],
                    volume=item["volume"],
                )
                for item in history
            ],
            report_day,
            market,
        )
        result[symbol] = {
            "return_5d": ret5.get(symbol),
            "return_20d": ret20.get(symbol),
            "return_5d_percentile": p5.get(symbol, 0.5),
            "return_20d_percentile": p20.get(symbol, 0.5),
            "amount_percentile": pa.get(symbol, 0.5),
            "completed_close": metrics.get("completed_close"),
            "relative_volume_5d": metrics.get("relative_volume_5d"),
            "relative_volume_20d": metrics.get("relative_volume_20d"),
            "atr14": metrics.get("atr14"),
            "price_structure": metrics.get("price_structure") or {},
        }
    return result


def _keyword_balance(text: str, positive: Iterable[str] = POSITIVE, negative: Iterable[str] = NEGATIVE) -> float:
    lower = text.lower()
    plus = sum(word in lower for word in positive)
    minus = sum(word in lower for word in negative)
    return plus - minus


def _theme_names(text: str) -> list[str]:
    lower = text.lower()
    return [name for name, words in THEMES.items() if any(word in lower for word in words)]


def _sector_score(stock: ResearchStock, market: MarketResearch) -> tuple[float, str]:
    stock_text = f"{stock.sector_position} {stock.hot_topics}".lower()
    themes = _theme_names(stock_text)
    base = 7.5 + _clamp(_keyword_balance(stock_text) * 0.7, -3, 3)
    market_text = f"{market.sectors} {market.plan}".lower()
    best_alignment = 0.0
    for theme in themes:
        words = THEMES[theme]
        sentences = [part for part in re.split(r"[。；;]", market_text) if any(word in part for word in words)]
        if not sentences:
            continue
        balance = max(_keyword_balance(part, MARKET_POSITIVE, MARKET_NEGATIVE) for part in sentences)
        best_alignment = max(best_alignment, _clamp(balance * 1.8, -3.5, 4.5))
    score = round(_clamp(base + best_alignment, 0, 15), 1)
    return score, "、".join(themes[:2]) or "未识别明确板块标签"


def _check_icon(stock: ResearchStock, number: int) -> str:
    for line in stock.checklist:
        if f"检查项{number}" in line:
            return line[:1]
    return ""


NO_CATALYST_PATTERNS = (
    "暂无直接利好催化",
    "暂无明确催化",
    "无明确新增催化",
    "未提取到明确新增催化",
    "未发现明确催化",
    "当前无新增公司催化",
)


def _real_catalysts(stock: ResearchStock) -> list[str]:
    return [
        item
        for item in stock.catalysts
        if item and not any(pattern in item for pattern in NO_CATALYST_PATTERNS)
    ]


def _catalyst_score(stock: ResearchStock) -> float:
    catalysts = _real_catalysts(stock)
    text = f"{' '.join(catalysts)} {stock.earnings_outlook}"
    dated = sum(bool(re.search(r"20\d{2}[-年/]\d{1,2}", item)) for item in catalysts)
    score = 4 + min(len(catalysts), 2) * 2.5 + min(dated, 2) * 1.0
    score += _clamp(_keyword_balance(text) * 0.8, -4, 5)
    return round(_clamp(score, 0, 20), 1)


def _fundamental_score(stock: ResearchStock) -> float:
    icon = _check_icon(stock, 5)
    valuation = {"✅": 10.0, "⚠": 6.5, "❌": 2.5}.get(icon, 5.0)
    balance = _keyword_balance(stock.earnings_outlook)
    outlook = _clamp(2.5 + balance * 0.65, 0, 5)
    return round(_clamp(valuation + outlook, 0, 15), 1)


def _volume_score(stock: ResearchStock, context: dict[str, float]) -> float:
    relative = _clamp((stock.relative_volume or 0) / 1.5, 0, 1) * 7
    liquidity = context.get("amount_percentile", 0.5) * 3
    return round(relative + liquidity, 1)


def _price_structure_score(context: Mapping[str, Any]) -> float:
    structure = context.get("price_structure")
    if not isinstance(structure, Mapping) or structure.get("status") != "ok":
        return TECHNICAL_WEIGHTS["price_structure"] / 2

    trend_points = {
        "higher_high_higher_low": 15.0,
        "range_or_transition": 8.0,
        "lower_high_lower_low": 2.0,
    }.get(str(structure.get("trend") or ""), 7.5)

    breakout = str(structure.get("breakout_state") or "")
    if breakout == "above_range":
        location_points = 12.0
    elif breakout == "testing_range_high":
        location_points = 10.0
    else:
        position = structure.get("range_position_pct")
        location_points = (
            _clamp(float(position) / 100, 0, 1) * 8 if isinstance(position, (int, float)) else 4.0
        )

    close = context.get("completed_close")
    retest = structure.get("retest_level")
    support_points = 3.0 if all(isinstance(value, (int, float)) for value in (close, retest)) and float(close) >= float(retest) else 1.0
    next_resistance = structure.get("next_resistance")
    atr = context.get("atr14")
    if all(isinstance(value, (int, float)) for value in (close, next_resistance, atr)) and float(atr) > 0:
        room_atr = (float(next_resistance) - float(close)) / float(atr)
        room_points = 5.0 if room_atr >= 2 else 3.5 if room_atr >= 1 else 2.0 if room_atr > 0 else 0.5
    elif breakout == "above_range":
        room_points = 5.0
    else:
        room_points = 2.5
    return round(_clamp(trend_points + location_points + support_points + room_points, 0, 35), 1)


def _relative_strength_score(
    fallback_context: Mapping[str, Any],
    relative: Mapping[str, Any] | None,
) -> float:
    if relative and relative.get("status") in {"ok", "partial"}:
        weighted = (
            ("market_excess_5d_pct", 4.5),
            ("market_excess_20d_pct", 3.0),
            ("industry_excess_5d_pct", 4.5),
            ("industry_excess_20d_pct", 3.0),
        )
        score = 0.0
        available = 0.0
        for key, weight in weighted:
            value = relative.get(key)
            if value is None:
                continue
            score += _clamp(0.5 + float(value) / 20, 0, 1) * weight
            available += weight
        if available:
            score += (15 - available) * 0.5
            return round(_clamp(score, 0, 15), 1)
    return round(
        _clamp(
            fallback_context.get("return_5d_percentile", 0.5) * 9
            + fallback_context.get("return_20d_percentile", 0.5) * 6,
            0,
            15,
        ),
        1,
    )


def _volume_confirmation_score(stock: ResearchStock, context: Mapping[str, Any]) -> float:
    relative = context.get("relative_volume_5d")
    if not isinstance(relative, (int, float)):
        relative = stock.relative_volume
    if relative is None:
        volume_points = 8.0
    elif relative < 0.5:
        volume_points = 3.0
    elif relative < 0.8:
        volume_points = 6.0
    elif relative < 1.0:
        volume_points = 9.0
    elif relative < 1.2:
        volume_points = 12.0
    elif relative < 1.5:
        volume_points = 14.0
    elif relative <= 2.0:
        volume_points = 14.0
    else:
        volume_points = 12.0

    return_5d = context.get("return_5d")
    breakout = str((context.get("price_structure") or {}).get("breakout_state") or "")
    if isinstance(relative, (int, float)) and relative >= 1.0 and breakout in {"above_range", "testing_range_high"}:
        alignment_points = 4.0
    elif isinstance(return_5d, (int, float)) and return_5d > 0 and (relative is None or relative >= 0.8):
        alignment_points = 3.0
    elif isinstance(return_5d, (int, float)) and return_5d < 0 and isinstance(relative, (int, float)) and relative >= 1.0:
        alignment_points = 0.5
    else:
        alignment_points = 2.0
    liquidity_points = _clamp(float(context.get("amount_percentile", 0.5)), 0, 1) * 2
    return round(_clamp(volume_points + alignment_points + liquidity_points, 0, 20), 1)


def _volatility_risk_score(stock: ResearchStock, context: Mapping[str, Any]) -> float:
    atr = context.get("atr14")
    close = context.get("completed_close")
    atr_pct = float(atr) / float(close) * 100 if all(isinstance(value, (int, float)) for value in (atr, close)) and float(close) > 0 else None
    if atr_pct is None:
        atr_points = 6.0
    elif atr_pct <= 1:
        atr_points = 8.0
    elif atr_pct <= 2.5:
        atr_points = 10.0
    elif atr_pct <= 4:
        atr_points = 9.0
    elif atr_pct <= 6:
        atr_points = 7.0
    elif atr_pct <= 10:
        atr_points = 4.0
    else:
        atr_points = 2.0

    amplitude = _number(stock.amplitude)
    amplitude_points = 2.5 if amplitude is None else 5.0 if amplitude <= 3 else 4.0 if amplitude <= 6 else 2.5 if amplitude <= 10 else 1.0
    gap_state = str((context.get("price_structure") or {}).get("gap_state") or "")
    penalty = 2.0 if gap_state == "gap_down" else 0.0
    return round(_clamp(atr_points + amplitude_points - penalty, 0, 15), 1)


def _auxiliary_indicator_score(stock: ResearchStock) -> float:
    trend = float(stock.technical.get("trend_score") or 50)
    trend_points = _clamp(trend, 0, 100) / 100 * 2.5
    alignment = str(stock.technical.get("ma_alignment") or "").lower()
    if any(token in alignment for token in ("强势多头", "均线多头", "多头排列")):
        ma_points = 1.5
    elif "多头" in alignment:
        ma_points = 1.2
    elif any(token in alignment for token in ("强势空头", "发散下行")):
        ma_points = 0.0
    elif "空头" in alignment:
        ma_points = 0.3
    else:
        ma_points = 0.75

    rsi_text = " ".join([stock.conclusion, *stock.risks, json.dumps(stock.technical, ensure_ascii=False)])
    rsi_match = re.search(r"RSI(?:指标)?(?:达|为|[:：])?\s*(\d+(?:\.\d+)?)", rsi_text, re.IGNORECASE)
    if not rsi_match:
        rsi_points = 0.5
    else:
        rsi = float(rsi_match.group(1))
        rsi_points = 1.0 if 45 <= rsi <= 65 else 0.7 if 35 <= rsi <= 75 else 0.2
    return round(_clamp(trend_points + ma_points + rsi_points, 0, 5), 1)


def _technical_score(
    stock: ResearchStock,
    context: Mapping[str, Any],
    relative: Mapping[str, Any] | None,
    market: MarketResearch,
) -> tuple[float, dict[str, float], str]:
    sector, themes = _sector_score(stock, market)
    components = {
        "price_structure": _price_structure_score(context),
        "relative_strength_sector": round(
            _relative_strength_score(context, relative) + sector / 15 * 10,
            1,
        ),
        "volume_confirmation": _volume_confirmation_score(stock, context),
        "volatility_risk": _volatility_risk_score(stock, context),
        "auxiliary_indicators": _auxiliary_indicator_score(stock),
    }
    return round(sum(components.values()), 1), components, themes


def _event_score(stock: ResearchStock) -> float:
    catalysts = _real_catalysts(stock)
    combined = " ".join(catalysts + [stock.latest_news]).lower()
    dated = sum(bool(re.search(r"20\d{2}[-年/]\d{1,2}", item)) for item in catalysts)
    score = min(dated, 2) * 2 + min(len(catalysts), 2)
    score += min(sum(word in combined for word in OFFICIAL_WORDS), 3)
    event_gate = stock.review.get("event_gate") if isinstance(stock.review.get("event_gate"), dict) else {}
    if event_gate.get("events"):
        score += 1
    return round(_clamp(score, 0, 10), 1)


def _sentiment_score(stock: ResearchStock) -> float:
    buzz: list[float] = []
    tones: list[float] = []
    for line in stock.social_lines:
        if match := re.search(r"热度\s*([\d.]+)", line):
            buzz.append(float(match.group(1)))
        if match := re.search(r"情绪\s*(-?[\d.]+)", line):
            tones.append(float(match.group(1)))
        elif match := re.search(r"预期\s*(-?[\d.]+)", line):
            value = float(match.group(1))
            tones.append(value - 0.5 if 0 <= value <= 1 else value)
    if buzz:
        buzz_points = sum(buzz) / len(buzz) / 100 * 3
        tone = sum(tones) / len(tones) if tones else 0
        return round(_clamp(buzz_points + 1 + tone * 5, 0, 5), 1)
    text = stock.sentiment_summary
    balance = _keyword_balance(text, ("偏多", "正面", "积极", "乐观", "看好", "升温"), ("偏空", "负面", "悲观", "谨慎", "承压"))
    return round(_clamp(2.5 + balance * 0.6, 0, 5), 1)


def _data_score(stock: ResearchStock) -> float:
    completeness = float(stock.display_completeness)
    review_completeness = stock.review.get("data_completeness")
    if isinstance(review_completeness, (int, float)):
        completeness = (completeness + float(review_completeness)) / 2
    return round(_clamp(completeness / 20, 0, 5), 1)


def _risk_reward(stock: ResearchStock) -> float | None:
    card = stock.review.get("trade_card") if isinstance(stock.review.get("trade_card"), dict) else {}
    if isinstance(card.get("risk_reward"), (int, float)):
        return float(card["risk_reward"])
    entry, stop, target = _first_price(stock.entry), _first_price(stock.stop), _first_price(stock.target)
    if all(value is not None for value in (entry, stop, target)) and stop < entry < target:
        return (target - entry) / (entry - stop)
    return None


def _event_priority(stock: ResearchStock) -> int:
    gate = stock.review.get("event_gate") if isinstance(stock.review.get("event_gate"), dict) else {}
    return int(gate.get("priority") or 0)


def _upcoming_event(stock: ResearchStock, report_date: str) -> tuple[int | None, str]:
    as_of = date.fromisoformat(report_date)
    text = "\n".join([stock.earnings_outlook, *stock.risks])
    candidates: list[tuple[int, str]] = []
    future_markers = ("下一次", "财报", "业绩说明会", "披露", "窗口", "将于", "即将", "预定", "到期", "发布会")
    for clause in re.split(r"[；;。\n]", text):
        if not any(marker in clause for marker in future_markers):
            continue
        for raw_date in re.findall(r"20\d{2}-\d{2}-\d{2}", clause):
            try:
                days = (date.fromisoformat(raw_date) - as_of).days
            except ValueError:
                continue
            if 0 <= days <= 14:
                candidates.append((days, raw_date))
    return min(candidates) if candidates else (None, "")


def _execution(
    stock: ResearchStock,
    context: dict[str, float],
    data_points: float,
    report_date: str,
) -> tuple[float, str, str, float | None]:
    card = stock.review.get("trade_card") if isinstance(stock.review.get("trade_card"), dict) else {}
    card_status = str(card.get("status") or "")
    rr = _risk_reward(stock)
    rr_points = 12.0 if rr is None else _clamp(rr / 2.0, 0, 1) * 30

    current, entry = _number(stock.current_price), _first_price(stock.entry)
    if current and entry:
        distance = abs(current / entry - 1)
        trigger_points = 20 if distance <= 0.03 else 15 if distance <= 0.08 else 8 if distance <= 0.15 else 4
    else:
        trigger_points = 8
    trend_icon = _check_icon(stock, 1)
    if trend_icon == "❌":
        trigger_points = min(trigger_points, 7)
    elif trend_icon == "⚠":
        trigger_points = min(trigger_points, 12)

    # Structured official/provider events are authoritative. The free-form text
    # date parser is only a fallback for uncovered stocks; otherwise a date in
    # narrative earnings copy can duplicate a harmless 4–7 day monitor event
    # and incorrectly turn it back into a full event-observation state.
    event_days, event_date = (None, "") if stock.review else _upcoming_event(stock, report_date)
    upcoming_event = event_days is not None and event_days <= 2
    priority = _event_priority(stock)
    event_points = {0: 20, 1: 18, 2: 12, 3: 6, 4: 0}.get(priority, 8)
    if upcoming_event:
        event_points = min(event_points, 6)
    volume_points = _volume_score(stock, context) / 10 * 15
    volume_icon = _check_icon(stock, 3)
    if volume_icon == "❌":
        volume_points = min(volume_points, 4)
    elif volume_icon == "⚠":
        volume_points = min(volume_points, 9)
    amplitude = _number(stock.amplitude)
    volatility_points = 5 if amplitude is None else 10 if amplitude <= 3 else 8 if amplitude <= 6 else 6 if amplitude <= 10 else 3
    total = round(rr_points + trigger_points + event_points + volume_points + volatility_points + data_points, 1)

    if stock.action in {"卖出", "减仓"} or card_status in {"blocked", "data_conflict", "invalid_structure"}:
        return total, "风险失效", str(card.get("label") or "原机会逻辑已破坏"), rr
    if card_status in {"event_wait", "reassess"} or priority >= 3:
        return total, "事件观察", str(card.get("label") or "等待事件落地后重新确认"), rr
    if upcoming_event:
        return total, "事件观察", f"{event_date} 有临近事件（距报告日 {event_days} 天），等待落地后确认", rr
    if card_status == "ready":
        return total, "可执行", str(card.get("label") or "执行条件成熟"), rr
    if card_status == "ready_cautious":
        return total, "谨慎可执行", str(card.get("label") or "条件已满足但仍有软确认项"), rr
    if card_status == "quality_wait":
        return total, "等待触发", str(card.get("label") or "信号条件尚未完全确认"), rr
    if card_status == "trigger_wait":
        return total, "等待触发", str(card.get("label") or "等待价格与量能确认"), rr
    if card_status == "invalid_risk_reward":
        return total, "等待触发", f"当前盈亏比约1:{rr:.2f}，等待价格或目标空间改善" if rr is not None else "当前交易结构不成熟", rr
    if total >= 52:
        note = "机会逻辑保留，等待点位、趋势或量能确认"
        if not stock.review:
            note += "；官方事件链尚未补核，不标为可执行"
        return total, "等待触发", note, rr
    return total, "研究关注", "逻辑值得跟踪，但当前执行条件尚不完整", rr


@dataclass
class OpportunityResult:
    symbol: str
    name: str
    market: str
    old_score: int
    old_action: str
    opportunity_score: float
    execution_score: float
    execution_status: str
    execution_note: str
    risk_reward: float | None
    components: dict[str, float]
    technical_score: float
    technical_components: dict[str, float]
    themes: str
    return_5d: float | None
    return_20d: float | None
    relative_strength_status: str
    market_excess_5d: float | None
    market_excess_20d: float | None
    industry_excess_5d: float | None
    industry_excess_20d: float | None
    market_benchmark: str
    industry_benchmark: str
    primary_catalyst: str
    primary_risk: str


def _trend_relative_score(
    trend: float,
    fallback_context: Mapping[str, float],
    relative: Mapping[str, Any] | None,
) -> float:
    technical_points = _clamp(trend, 0, 100) * 0.10
    if relative and relative.get("status") in {"ok", "partial"}:
        # Positive ten-percentage-point excess earns the full subcomponent;
        # negative ten-point excess earns zero.  Five-day readings carry a
        # little more weight because this is a daily opportunity report.
        weighted = (
            ("market_excess_5d_pct", 3.0),
            ("market_excess_20d_pct", 2.0),
            ("industry_excess_5d_pct", 3.0),
            ("industry_excess_20d_pct", 2.0),
        )
        relative_points = 0.0
        available = 0.0
        for key, weight in weighted:
            value = relative.get(key)
            if value is None:
                continue
            relative_points += _clamp(0.5 + float(value) / 20, 0, 1) * weight
            available += weight
        if available:
            # Keep a neutral prior for any unavailable benchmark window.
            relative_points += (10 - available) * 0.5
            return round(_clamp(technical_points + relative_points, 0, 20), 1)
    return round(
        _clamp(
            technical_points
            + fallback_context.get("return_5d_percentile", 0.5) * 6
            + fallback_context.get("return_20d_percentile", 0.5) * 4,
            0,
            20,
        ),
        1,
    )


def score_market(
    db_path: Path,
    report_date: str,
    market_name: str,
    market_research: MarketResearch,
    review_path: Path | None = DEFAULT_REVIEW,
    relative_strength: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[OpportunityResult]:
    stocks = load_stocks(db_path, report_date, market_name, review_path)
    context = _price_context(db_path, report_date, market_name)
    output: list[OpportunityResult] = []
    for stock in stocks:
        ctx = context.get(stock.symbol, {})
        relative = (relative_strength or {}).get(stock.symbol)
        technical_score, technical_components, themes = _technical_score(
            stock,
            ctx,
            relative,
            market_research,
        )
        components = {
            "technical_composite": round(
                technical_score / 100 * WEIGHTS["technical_composite"],
                1,
            ),
            "catalyst_expectation": _catalyst_score(stock),
            "fundamental_valuation": _fundamental_score(stock),
            "event_news_value": _event_score(stock),
            "sentiment_crowding": _sentiment_score(stock),
            "data_trust": _data_score(stock),
        }
        opportunity = round(sum(components.values()), 1)
        execution, status, note, rr = _execution(stock, ctx, components["data_trust"], report_date)
        output.append(
            OpportunityResult(
                symbol=stock.symbol,
                name=stock.name,
                market=market_name,
                old_score=stock.score,
                old_action=stock.action,
                opportunity_score=opportunity,
                execution_score=execution,
                execution_status=status,
                execution_note=note,
                risk_reward=round(rr, 2) if rr is not None else None,
                components=components,
                technical_score=technical_score,
                technical_components=technical_components,
                themes=themes,
                return_5d=round(ctx["return_5d"] * 100, 2) if ctx.get("return_5d") is not None else None,
                return_20d=round(ctx["return_20d"] * 100, 2) if ctx.get("return_20d") is not None else None,
                relative_strength_status=str((relative or {}).get("status") or "cross_section_fallback"),
                market_excess_5d=(relative or {}).get("market_excess_5d_pct"),
                market_excess_20d=(relative or {}).get("market_excess_20d_pct"),
                industry_excess_5d=(relative or {}).get("industry_excess_5d_pct"),
                industry_excess_20d=(relative or {}).get("industry_excess_20d_pct"),
                market_benchmark=str(
                    ((relative or {}).get("market_benchmark") or {}).get("provider_symbol") or ""
                ),
                industry_benchmark=str(
                    ((relative or {}).get("industry_benchmark") or {}).get("symbol") or ""
                ),
                primary_catalyst=(
                    _real_catalysts(stock)[0]
                    if _real_catalysts(stock)
                    else "当前无新增公司催化"
                ),
                primary_risk=stock.risks[0] if stock.risks else "未提取到明确新增风险",
            )
        )
    return sorted(output, key=lambda item: (-item.opportunity_score, -item.execution_score, item.symbol))


def render_markdown(report_date: str, market: str, results: list[OpportunityResult]) -> str:
    label = "港股" if market == "hk" else "美股"
    lines = [
        f"# {label}机会雷达｜{report_date}",
        "",
        "> 机会分负责发现值得跟踪的方向；执行成熟度只描述当前条件，不构成统一买卖或仓位指令。",
        "",
        "## 机会 Top5",
        "",
        "| 排名 | 股票 | 机会分 | 执行状态 | 执行成熟度 | 5日/20日 | 核心催化 |",
        "|---:|---|---:|---|---:|---|---|",
    ]
    for index, item in enumerate(results[:5], 1):
        returns = f"{item.return_5d if item.return_5d is not None else '—'}% / {item.return_20d if item.return_20d is not None else '—'}%"
        catalyst = item.primary_catalyst.replace("|", "｜")[:72]
        lines.append(f"| {index} | {item.name} `{item.symbol}` | {item.opportunity_score:.1f} | {item.execution_status} | {item.execution_score:.1f} | {returns} | {catalyst} |")
    lines.extend(["", "## Top5 解释", ""])
    for item in results[:5]:
        comp = " / ".join(f"{key} {value:g}" for key, value in item.components.items())
        technical = " / ".join(
            f"{key} {value:g}/{TECHNICAL_WEIGHTS[key]}"
            for key, value in item.technical_components.items()
        )
        rr = f"1:{item.risk_reward:.2f}" if item.risk_reward is not None else "未可靠计算"
        lines.extend(
            [
                f"### {item.name}（{item.symbol}）｜{item.opportunity_score:.1f}｜{item.execution_status}",
                "",
                f"- 主题：{item.themes}",
                f"- 执行说明：{item.execution_note}；参考盈亏比 {rr}",
                f"- 主要催化：{item.primary_catalyst}",
                f"- 主要风险：{item.primary_risk}",
                f"- 分项：{comp}",
                f"- 技术分 {item.technical_score:.1f}/100：{technical}",
                "",
            ]
        )
    risk = [item for item in results if item.execution_status == "风险失效"]
    lines.extend(["## 风险观察", ""])
    if risk:
        lines.extend(f"- {item.name}（{item.symbol}）：{item.execution_note}" for item in risk)
    else:
        lines.append("- 本次未识别到需要单列的严重逻辑失效标的。")
    uses_benchmarks = any(item.relative_strength_status in {"ok", "partial"} for item in results)
    relative_note = (
        "相对强弱使用个股相对市场基准及行业ETF代理的 5/20 日超额收益；行业映射回退会明确标记。"
        if uses_benchmarks
        else "相对强弱暂用股票池内 5/20 日收益百分位回退口径。"
    )
    lines.extend(["", "## 口径说明", "", f"- {relative_note}", "- 财报临近、融资核验和盈亏比不足不删除机会，只影响执行状态。", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recalculate public-report opportunity Top5")
    parser.add_argument("--date", default="2026-08-21")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "output")
    args = parser.parse_args()
    date_token = args.date.replace("-", "")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "report_date": args.date,
        "weights": WEIGHTS,
        "technical_weights": TECHNICAL_WEIGHTS,
        "markets": {},
    }
    for market in ("hk", "us"):
        report_path = Path(__file__).resolve().parents[1] / "reports" / f"compact_report_{date_token}_{market}.md"
        research = parse_market_research(report_path.read_text(encoding="utf-8"))
        results = score_market(args.db, args.date, market, research, args.review)
        json_path = args.output_dir / f"opportunity_top5_{market}_{date_token}.json"
        md_path = args.output_dir / f"opportunity_top5_{market}_{date_token}.md"
        json_path.write_text(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(render_markdown(args.date, market, results), encoding="utf-8")
        summary["markets"][market] = {
            "top5": [{"symbol": item.symbol, "score": item.opportunity_score, "status": item.execution_status} for item in results[:5]],
            "json": str(json_path),
            "markdown": str(md_path),
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
