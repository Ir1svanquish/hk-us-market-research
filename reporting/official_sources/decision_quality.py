"""Deterministic report data-completeness and signal-confidence assessment."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence

from .gates import ACTION_PRIORITY


MIN_EXECUTION_CONFIDENCE = 60


COMPONENT_CAPS = {
    "market_snapshot": 25,
    "technical": 20,
    "financial_valuation": 20,
    "news_announcements": 15,
    "official_events": 10,
    "liquidity": 10,
}

ACTION_LABELS = {
    "info_only": "可执行计划",
    "monitor": "事件观察",
    "reassess": "重新评估",
    "conditional_only": "仅条件观察",
    "block_new_positions": "仅观察，禁止新开仓",
    "risk_off": "风险关闭",
}


def _normalize_symbol(symbol: str, market: str) -> str:
    value = str(symbol or "").strip().upper()
    if market == "hk":
        value = value.removeprefix("HK").removesuffix(".HK")
        digits = re.sub(r"\D", "", value)
        return digits.zfill(5) if digits else value
    return value.replace(".", "-")


def _report_date(text: str) -> Optional[date]:
    match = re.search(r"^# .*?(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
    try:
        return date.fromisoformat(match.group(1)) if match else None
    except ValueError:
        return None


def _section_map(text: str, market: str) -> Dict[str, str]:
    matches = list(
        re.finditer(
            r"^## [^\n]*\((?P<symbol>HK\d{5}|[A-Z][A-Z0-9.-]{0,9})\)\s*$",
            text,
            re.MULTILINE,
        )
    )
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[_normalize_symbol(match.group("symbol"), market)] = text[match.start() : end]
    return result


def parse_analysis_report(path: Path, market: str) -> Dict[str, dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = _section_map(text, market)
    result: Dict[str, dict] = {}
    pattern = re.compile(
        r"^[^\n]*\*\*(?P<name>.+?)\((?P<symbol>HK\d{5}|[A-Z][A-Z0-9.-]{0,9})\)\*\*:\s*"
        r"(?P<decision>[^|\n]+)\|\s*评分\s*(?P<score>\d+)\s*\|\s*(?P<trend>[^\n]+)$",
        re.MULTILINE,
    )
    report_day = _report_date(text)
    for match in pattern.finditer(text):
        symbol = _normalize_symbol(match.group("symbol"), market)
        result[symbol] = {
            "market": market,
            "symbol": symbol,
            "name": match.group("name").strip(),
            "decision": match.group("decision").strip(),
            "score": int(match.group("score")),
            "trend": match.group("trend").strip(),
            "report_date": report_day.isoformat() if report_day else "",
            "section": sections.get(symbol, ""),
            "report_path": str(path),
        }
    return result


def load_hk_coverage(path: Optional[Path]) -> Dict[str, str]:
    if path is None or not path.exists():
        return {}
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else []
    return {
        _normalize_symbol(str(item.get("symbol") or ""), "hk"): str(item.get("status") or "")
        for item in items
        if isinstance(item, dict)
    }


def load_us_coverage(snapshots: Mapping[str, Mapping[str, object]]) -> Dict[str, str]:
    result = {}
    for symbol, item in snapshots.items():
        status = str(item.get("sec_direct_status") or item.get("status") or "")
        result[_normalize_symbol(symbol, "us")] = status
    return result


def _table(section: str, leading_header: str) -> Dict[str, str]:
    lines = section.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("|") or leading_header not in line:
            continue
        headers = [item.strip() for item in line.strip("|").split("|")]
        for row in lines[index + 1 : index + 4]:
            if not row.startswith("|") or set(row.replace("|", "").strip()) <= {"-", ":", " "}:
                continue
            values = [item.strip() for item in row.strip("|").split("|")]
            return dict(zip(headers, values))
    return {}


def _available(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and not any(
        marker in text for marker in ("n/a", "数据缺失", "无法获取", "未取得", "—")
    )


def _line_after(section: str, marker: str) -> str:
    match = re.search(re.escape(marker) + r"[^\n]*", section)
    return match.group(0) if match else ""


def _direction(text: str, *, technical: bool = False) -> int:
    value = str(text or "").lower()
    if technical:
        if any(word in value for word in ("强烈看多", "看多", "多头")):
            return 1
        if any(word in value for word in ("看空", "空头")):
            return -1
        return 0
    positive = sum(
        value.count(word)
        for word in ("超预期", "增长", "上调", "改善", "强劲", "合理", "低估", "偏多", "乐观", "积极")
    )
    negative = sum(
        value.count(word)
        for word in ("低于预期", "亏损", "下调", "恶化", "承压", "高估", "偏高", "偏空", "悲观", "负面")
    )
    return 1 if positive >= negative + 2 else -1 if negative >= positive + 2 else 0


def _sentiment_direction(text: str) -> int:
    value = str(text or "").lower()
    if any(word in value for word in ("偏多", "乐观", "积极", "正面", "看好")):
        return 1
    if any(word in value for word in ("偏空", "悲观", "消极", "负面", "看淡")):
        return -1
    return 0


def _trade_plan(section: str) -> dict:
    def cell(label: str) -> str:
        match = re.search(rf"\|\s*[^|]*{re.escape(label)}[^|]*\|\s*([^|\n]+)", section)
        return match.group(1).strip() if match else ""

    position = _line_after(section, "**💰 仓位建议**")
    quote = _table(section, "收盘")
    return {
        "snapshot_price": quote.get("收盘", ""),
        "entry": cell("理想买入点"),
        "stop": cell("止损位"),
        "target": cell("目标位"),
        "position": position.split(":", 1)[-1].strip() if position else "",
    }


def _freshness(report_date: str, as_of: date, market: str) -> float:
    try:
        age = max(0, (as_of - date.fromisoformat(report_date)).days)
    except ValueError:
        return 0.0
    # Before the next US close, the prior US trading-day report is still the
    # latest completed session and must not be treated as stale.
    if age == 0 or (market == "us" and age == 1):
        return 1.0
    return 0.8 if age == 1 else 0.5 if age <= 3 else 0.0


def _completeness(
    item: Mapping[str, object], *, as_of: date, coverage_status: str, official_event_count: int,
    market_metric: Optional[Mapping[str, object]] = None,
) -> tuple[int, dict, list]:
    section = str(item.get("section") or "")
    quote = _table(section, "收盘")
    source = _table(section, "当前价")
    fresh = _freshness(
        str(item.get("report_date") or ""), as_of, str(item.get("market") or "")
    )
    quote_weights = {
        "收盘": 4,
        "昨收": 3,
        "开盘": 2,
        "最高": 2,
        "最低": 2,
        "涨跌幅": 3,
        "成交量": 3,
        "成交额": 3,
    }
    market_raw = sum(weight for key, weight in quote_weights.items() if _available(quote.get(key)))
    market_raw += 3 if _available(source.get("行情来源")) else 0
    market_score = round(min(25, market_raw) * fresh)

    def indicator(label: str) -> bool:
        match = re.search(rf"\|\s*{re.escape(label)}[^|]*\|\s*([^|\n]+)", section)
        return bool(match and _available(match.group(1)) and re.search(r"\d", match.group(1)))

    technical_raw = 0
    technical_raw += 3 if indicator("MA5") or re.search(r"\bMA5\b[^\n|]*\d", section) else 0
    technical_raw += 3 if indicator("MA10") or re.search(r"\bMA10\b[^\n|]*\d", section) else 0
    technical_raw += 3 if indicator("MA20") or re.search(r"\bMA20\b[^\n|]*\d", section) else 0
    technical_raw += 3 if indicator("乖离率") or re.search(r"乖离率[^\n|]*[-+]?\d", section) else 0
    technical_raw += 3 if re.search(r"趋势强度:\s*\d+", section) else 0
    technical_raw += 2 if indicator("支撑位") else 0
    technical_raw += 2 if indicator("压力位") else 0
    technical_raw += 1 if re.search(r"均线排列[^\n]*(?:多头|空头|震荡|缠绕)", section) else 0
    technical_score = round(min(20, technical_raw) * fresh)

    earnings = _line_after(section, "**📊 业绩预期**")
    financial_score = 0
    if earnings and not any(word in earnings for word in ("数据缺失", "无法获取", "暂无")):
        financial_score += 8
    if re.search(r"\b(?:PE|PB|EPS)\b[^\n]*[-+]?\d", section, re.IGNORECASE):
        financial_score += 6
    if re.search(r"(?:实际EPS|预期EPS|营收|毛利率|财报|业绩|指引)[^\n]*\d", section):
        financial_score += 6

    news_score = 0
    news_score += 3 if _line_after(section, "**💭 舆情情绪**") else 0
    news_score += 3 if "**🚨 风险警报**" in section and re.search(r"风险点\d|^- ", section, re.MULTILINE) else 0
    news_score += 3 if "**✨ 利好催化**" in section and ("利好" in section or re.search(r"^- ", section, re.MULTILINE)) else 0
    news_score += 3 if _line_after(section, "**📢 最新动态**") else 0
    news_score += 3 if re.search(r"2026-\d{2}-\d{2}|2026年\d{1,2}月", section) else 0

    coverage_ok = coverage_status.lower() in {"ok", "success", "live", "sec_direct"}
    official_score = 10 if coverage_ok else 6 if official_event_count else 0

    liquidity_score = 0
    liquidity_score += 3 if _available(quote.get("成交量")) else 0
    liquidity_score += 3 if _available(quote.get("成交额")) else 0
    metric = market_metric or {}
    liquidity_score += 2 if _available(source.get("换手率")) or metric.get("turnover_rate") is not None else 0
    liquidity_score += 2 if _available(source.get("量比")) or metric.get("volume_ratio") is not None else 0

    components = {
        "market_snapshot": market_score,
        "technical": technical_score,
        "financial_valuation": min(20, financial_score),
        "news_announcements": min(15, news_score),
        "official_events": official_score,
        "liquidity": liquidity_score,
    }
    gaps = []
    labels = {
        "market_snapshot": "行情快照不完整或过期",
        "technical": "技术指标不完整或过期",
        "financial_valuation": "财务/估值依据不足",
        "news_announcements": "新闻与公告摘要不足",
        "official_events": "官方事件覆盖未确认",
        "liquidity": "量比、换手率或成交数据缺失",
    }
    for key, cap in COMPONENT_CAPS.items():
        if components[key] < cap * 0.75:
            gaps.append(labels[key])
    return sum(components.values()), components, gaps


def _alignment_score(values: Sequence[int]) -> int:
    nonzero = [value for value in values if value]
    if not nonzero:
        return 25
    if len(nonzero) == 1:
        return 24
    if len(set(nonzero)) == 1:
        return 45 if len(nonzero) == 3 else 37
    if len(nonzero) == 3 and abs(sum(nonzero)) == 1:
        return 23
    return 16


def _confidence(item: Mapping[str, object], existing_gate: Mapping[str, object]) -> tuple[int, dict]:
    section = str(item.get("section") or "")
    technical_direction = _direction(str(item.get("trend") or ""), technical=True)
    fundamental_text = " ".join(
        (
            _line_after(section, "**📊 业绩预期**"),
            _line_after(section, "检查项5"),
            _line_after(section, "**📢 最新动态**"),
        )
    )
    fundamental_direction = _direction(fundamental_text)
    news_direction = _sentiment_direction(_line_after(section, "**💭 舆情情绪**"))
    alignment = _alignment_score(
        [technical_direction, fundamental_direction, news_direction]
    )
    strength_match = re.search(r"趋势强度:\s*(\d+)", section)
    strength = min(100, int(strength_match.group(1))) if strength_match else 50
    technical_stability = round(strength * 0.25)
    priority = int(existing_gate.get("priority") or 0)
    event_stability = {0: 20, 1: 16, 2: 11, 3: 6, 4: 1, 5: 0}.get(priority, 0)
    decision = str(item.get("decision") or "")
    decision_direction = 1 if decision == "买入" else -1 if decision in {"卖出", "减仓"} else 0
    if decision_direction == 0:
        plan_consistency = 7
    elif technical_direction == decision_direction:
        plan_consistency = 10
    elif technical_direction == 0:
        plan_consistency = 6
    else:
        plan_consistency = 2
    components = {
        "direction_alignment": alignment,
        "technical_stability": technical_stability,
        "event_stability": event_stability,
        "plan_consistency": plan_consistency,
    }
    return min(100, sum(components.values())), {
        **components,
        "signals": {
            "technical": technical_direction,
            "fundamental": fundamental_direction,
            "news": news_direction,
        },
    }


def _quality_gate(completeness: int, confidence: int, as_of: date) -> dict:
    if completeness < 60:
        return {
            "gate_action": "block_new_positions",
            "priority": ACTION_PRIORITY["block_new_positions"],
            "reason": "数据完整度低于 60，只能观察并清空交易点位与仓位计划。",
            "gate_until": as_of.isoformat(),
        }
    if completeness < 80:
        return {
            "gate_action": "conditional_only",
            "priority": ACTION_PRIORITY["conditional_only"],
            "reason": "数据完整度为 60–79，只允许条件观察，不提供主动买点。",
            "gate_until": as_of.isoformat(),
        }
    if confidence < 45:
        return {
            "gate_action": "block_new_positions",
            "priority": ACTION_PRIORITY["block_new_positions"],
            "reason": "信号置信度低于 45，多维信号冲突，仅保留观察。",
            "gate_until": as_of.isoformat(),
        }
    if confidence < MIN_EXECUTION_CONFIDENCE:
        return {
            "gate_action": "conditional_only",
            "priority": ACTION_PRIORITY["conditional_only"],
            "reason": f"信号置信度低于 {MIN_EXECUTION_CONFIDENCE}，只允许等待条件触发。",
            "gate_until": as_of.isoformat(),
        }
    return {
        "gate_action": "info_only",
        "priority": 0,
        "reason": "完整度与置信度达到可执行阈值。",
        "gate_until": "",
    }


def assess_decisions(
    report_items: Mapping[str, Mapping[str, object]],
    *,
    as_of: date,
    coverage: Mapping[str, str],
    symbol_gates: Mapping[str, Mapping[str, object]],
    events: Iterable[Mapping[str, object]],
    symbols: Optional[Sequence[str]] = None,
    market_metrics: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> Dict[str, dict]:
    event_map: Dict[str, list] = {}
    for event in events:
        symbol = str(event.get("symbol") or "")
        if symbol and symbol != "__MARKET__":
            event_map.setdefault(symbol, []).append(event)
    selected = list(symbols or report_items.keys())
    result = {}
    for symbol in selected:
        item = report_items.get(symbol)
        if not item:
            continue
        existing = symbol_gates.get(symbol, {})
        completeness, completeness_components, gaps = _completeness(
            item,
            as_of=as_of,
            coverage_status=coverage.get(symbol, ""),
            official_event_count=len(event_map.get(symbol, [])),
            market_metric=(market_metrics or {}).get(symbol, {}),
        )
        confidence, confidence_components = _confidence(item, existing)
        quality_gate = _quality_gate(completeness, confidence, as_of)
        existing_priority = int(existing.get("priority") or 0)
        final_gate = dict(existing) if existing_priority > quality_gate["priority"] else quality_gate
        if existing_priority == quality_gate["priority"] and existing_priority > 0:
            final_gate = {
                **quality_gate,
                "reason": "；".join(
                    value.rstrip("。；")
                    for value in (str(existing.get("reason") or ""), quality_gate["reason"])
                    if value
                )
                + "。",
                "gate_until": max(
                    str(existing.get("gate_until") or ""), quality_gate["gate_until"]
                ),
            }
        plan = _trade_plan(str(item.get("section") or ""))
        suppressed = []
        if completeness < 60:
            suppressed = ["entry", "stop", "target", "position"]
        elif int(final_gate.get("priority") or 0) >= ACTION_PRIORITY["conditional_only"]:
            suppressed = ["entry", "position"]
        effective_plan = {
            key: "" if key in suppressed else value for key, value in plan.items()
        }
        event_tags = sorted(
            {
                str(tag.get("event_type") or event.get("event_type") or "")
                for event in event_map.get(symbol, [])
                for tag in (event.get("tags") if isinstance(event.get("tags"), list) else [{}])
                if isinstance(tag, dict)
            }
            - {""}
        )
        result[symbol] = {
            "market": item.get("market"),
            "name": item.get("name"),
            "v1_action": item.get("decision"),
            "v1_score": item.get("score"),
            "data_completeness": completeness,
            "completeness_components": completeness_components,
            "signal_confidence": confidence,
            "confidence_components": confidence_components,
            "major_gaps": gaps[:3],
            "quality_gate": quality_gate,
            "event_gate": dict(existing),
            "final_gate": final_gate,
            "final_action": ACTION_LABELS.get(
                str(final_gate.get("gate_action") or "info_only"),
                str(final_gate.get("gate_action") or ""),
            ),
            "raw_trade_plan": plan,
            "effective_trade_plan": effective_plan,
            "suppressed_fields": suppressed,
            "event_tags": event_tags,
            "report_path": item.get("report_path"),
            "report_date": item.get("report_date"),
        }
    return result
