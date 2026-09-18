"""Render the complete public-facing report from the unified full-pool contract.

The report intentionally contains only reader-facing market research, opportunity
ranking, events, conditions and risks.  Engineering rationale, provider/source
lists, fallback notes and validation-state prose remain in the JSON contract.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from v2.dedup_report import DedupReport, parse_report
from v2.hk_key_variables import INTEGRATED_PAGE_CSS, render_integrated_page
from v2.official_sources.macro_catalog import translate_macro_title
from v2.official_sources.macro_scenarios import analyze_release
from v2.research_report import (
    DEFAULT_DB,
    MarketResearch,
    ResearchStock,
    load_stocks,
    parse_market_research,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = Path(__file__).resolve().parent

FORBIDDEN_TEXT = (
    "数据来源",
    "来源：",
    "fallback",
    "待验证",
    "待复核",
    "待补跑",
    "V2",
    "旧版",
    "新增改进",
    "为什么这样做",
    "为什么观察",
    "正式启用前",
    "调试用",
    "调试信息",
    "debug",
    "影子评分",
    "新旧对比",
    "测试通过",
    "审计通过",
    "后台数据",
)

READER_OMIT_TEXT = (
    "系统未提供",
    "数据缺失",
    "无成交额",
    "无涨跌",
    "由于系统",
    "以下判断基于有限信息",
    "虽然可能指a股",
    "新闻中大量a股复盘内容",
)

COMPONENT_LABELS = {
    "technical_composite": "结构化技术分",
    "catalyst_expectation": "催化/预期差",
    "fundamental_valuation": "基本面/估值",
    "event_news_value": "事件/新闻",
    "sentiment_crowding": "舆情/拥挤",
    "data_trust": "数据质量",
}

COMPONENT_CAPS = {
    "technical_composite": 45,
    "catalyst_expectation": 20,
    "fundamental_valuation": 15,
    "event_news_value": 10,
    "sentiment_crowding": 5,
    "data_trust": 5,
}

TECHNICAL_COMPONENT_LABELS = {
    "price_structure": "价格结构",
    "relative_strength_sector": "相对强弱/板块",
    "volume_confirmation": "量价确认",
    "volatility_risk": "波动风险",
    "auxiliary_indicators": "辅助指标",
}

EVENT_LABELS = {
    "earnings_calendar": "业绩日程",
    "earnings_calendar_notice": "业绩日程公告",
    "financial_results": "财务业绩",
    "earnings_warning": "业绩预警",
    "capital_dilution": "融资与摊薄",
    "debt_financing": "债务融资",
    "share_buyback": "股份回购",
    "insider_transaction": "内部人交易",
    "critical_corporate_event": "重大公司事件",
    "current_report": "重大事项报告",
    "foreign_issuer_report": "境外发行人报告",
    "board_or_management": "管理层变动",
    "strategic_investment": "战略投资",
    "dividend": "股息安排",
    "beneficial_ownership": "重要股东持仓",
    "restructuring": "重组事项",
}

STATUS_CLASS = {
    "可执行": "ready",
    "谨慎可执行": "cautious",
    "等待触发": "wait",
    "事件观察": "event",
    "研究关注": "research",
    "风险失效": "risk",
}

PUBLIC_NAME_ALIASES = {
    "华虹半导体": "华虹宏力",
    "Alibaba": "阿里巴巴",
    "Shein": "希音（SHEIN）",
    "new CEO debut": "新任 CEO 首次公开亮相",
}

NO_CATALYST_PATTERNS = (
    "暂无直接利好催化",
    "暂无明确催化",
    "无明确新增催化",
    "未提取到明确新增催化",
    "未发现明确催化",
    "当前无新增公司催化",
)

FILING_HEADLINE_LABELS = {
    "10-Q": "季度报告（10-Q）",
    "8-K": "重大事项报告（8-K）",
    "4": "内部人交易申报（Form 4）",
    "144": "拟出售证券申报（Form 144）",
    "424B2": "证券发行说明文件（424B2）",
    "424B3": "证券发行说明文件（424B3）",
    "424B5": "证券发行说明文件（424B5）",
    "424B7": "证券发行说明文件（424B7）",
    "SCHEDULE 13G/A": "重要股东持仓申报（Schedule 13G/A）",
}

EVIDENCE_ALIASES = {
    "AAPL": ("aapl", "apple"),
    "NVDA": ("nvda", "nvidia"),
    "MRVL": ("mrvl", "marvell"),
    "PLTR": ("pltr", "palantir"),
    "GOOG": ("goog", "google", "alphabet"),
    "GOOGL": ("googl", "google", "alphabet"),
}

RECENT_MACRO_LOOKBACK_DAYS = 7
RELEASED_MACRO_SURPRISE_THRESHOLDS = {
    "high": 1.0,
    "medium": 3.0,
}


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _plain(value: object, limit: int = 260) -> str:
    text = re.sub(r"[*_`#]", "", str(value or ""))
    for previous, current in PUBLIC_NAME_ALIASES.items():
        text = text.replace(previous, current)
    text = re.sub(r"\s+", " ", text).strip(" |\n\t")
    clauses = [part.strip() for part in re.split(r"(?<=[。！？；])", text) if part.strip()]
    kept = [part for part in clauses if not any(term.lower() in part.lower() for term in FORBIDDEN_TEXT)]
    cleaned = "".join(kept) if kept else ("" if any(term.lower() in text.lower() for term in FORBIDDEN_TEXT) else text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip("，、；;：: ") + "…"


def _meaningful(value: object) -> bool:
    text = _plain(value, 1000).strip().lower()
    return bool(text and text not in {"—", "--", "n/a", "none", "null"})


def _reader_text(value: object, limit: int = 260) -> str:
    text = _plain(value, 2000)
    clauses = [part.strip() for part in re.split(r"(?<=[。！？；])", text) if part.strip()]
    kept = [part for part in clauses if not any(term in part.lower() for term in READER_OMIT_TEXT)]
    return _plain("".join(kept), limit)


def _is_no_catalyst(value: object) -> bool:
    text = _plain(value, 300).lower()
    return not text or any(pattern.lower() in text for pattern in NO_CATALYST_PATTERNS)


def _translated_event_headline(value: object) -> str:
    text = _plain(value, 160)
    normalized = re.sub(r"\s+filing$", "", text, flags=re.I).strip()
    for raw, label in sorted(FILING_HEADLINE_LABELS.items(), key=lambda item: -len(item[0])):
        if normalized.upper() == raw.upper():
            return label
    return text


class _SourceRegistry:
    """Collect only reader-visible evidence and assign stable report-local IDs."""

    def __init__(self, information_cutoff: object = "") -> None:
        self.information_cutoff = str(information_cutoff or "")
        self.items: list[dict[str, str]] = []
        self._by_url: dict[str, int] = {}

    def add(
        self,
        *,
        label: object,
        url: object,
        source: object = "",
        published_at: object = "",
        fetched_at: object = "",
    ) -> int | None:
        clean_url = str(url or "").strip()
        if not clean_url.startswith(("http://", "https://")):
            return None
        if clean_url in self._by_url:
            return self._by_url[clean_url]
        number = len(self.items) + 1
        self._by_url[clean_url] = number
        source_label = _plain(source, 80)
        source_label = {
            "sec_edgar": "SEC EDGAR",
            "secedgar": "SEC EDGAR",
            "hkexnews": "港交所披露易",
        }.get(source_label.lower(), source_label)
        published_text = str(published_at or "")
        fetched_text = str(fetched_at or self.information_cutoff)
        fetched_text = re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", fetched_text)
        self.items.append(
            {
                "id": str(number),
                "label": _plain(label, 150) or "公开资料",
                "url": clean_url,
                "source": source_label,
                "published_at": published_text[:10],
                "fetched_at": fetched_text[:32].replace("T", " "),
            }
        )
        return number

    def marker(self, number: int | None) -> str:
        if number is None:
            return ""
        return f"<a class='source-marker' href='#source-{number}'>[S{number}]</a>"


def _event_source_marker(
    event: Mapping[str, Any], registry: _SourceRegistry | None
) -> str:
    if registry is None:
        return ""
    number = registry.add(
        label=_translated_event_headline(event.get("headline"))
        or EVENT_LABELS.get(str(event.get("event_type") or ""), "公司事项"),
        url=event.get("source_url"),
        source=event.get("source"),
        published_at=event.get("published_at") or event.get("effective_at"),
        fetched_at=event.get("fetched_at"),
    )
    return registry.marker(number)


def _best_stock_evidence(
    stock: ResearchStock, text: object, *, kind: str
) -> Mapping[str, Any] | None:
    candidates = getattr(stock, "evidence_sources", []) or []
    symbol = str(stock.symbol or "").upper().replace("US.", "")
    aliases = EVIDENCE_ALIASES.get(symbol, (symbol.lower(),))
    dimensions = {
        "catalyst": ("latest_news", "industry", "market_analysis"),
        "risk": ("risk_check", "latest_news", "market_analysis"),
        "earnings": ("earnings", "market_analysis", "latest_news"),
        "target": ("market_analysis", "earnings", "latest_news"),
    }.get(kind, ("latest_news", "market_analysis", "risk_check", "earnings"))
    statement = str(text or "").lower()
    best: tuple[float, Mapping[str, Any]] | None = None
    for evidence in candidates:
        haystack = " ".join(
            str(evidence.get(key) or "").lower() for key in ("title", "snippet", "url")
        )
        source_name = " ".join(
            str(evidence.get(key) or "").lower() for key in ("source", "provider")
        )
        if any(value in source_name or value in haystack for value in ("facebook", "instagram", "tiktok", "google.com.hk/goto")):
            continue
        if aliases and not any(alias and alias in haystack for alias in aliases):
            continue
        dimension = str(evidence.get("dimension") or "")
        if dimension not in dimensions:
            continue
        score = float(len(dimensions) - dimensions.index(dimension))
        published_day = str(evidence.get("published_at") or "")[:10]
        if published_day and published_day in statement:
            score += 4
        for token in re.findall(r"[a-z]{4,}|20\d{2}-\d{2}-\d{2}|\d+(?:\.\d+)?%", statement):
            if token in haystack:
                score += 0.5
        if best is None or score > best[0]:
            best = (score, evidence)
    return best[1] if best else None


def _stock_source_marker(
    stock: ResearchStock,
    text: object,
    registry: _SourceRegistry | None,
    *,
    kind: str,
) -> str:
    if registry is None:
        return ""
    evidence = _best_stock_evidence(stock, text, kind=kind)
    if evidence is None:
        return ""
    number = registry.add(
        label=evidence.get("title") or text,
        url=evidence.get("url"),
        source=evidence.get("source") or evidence.get("provider"),
        published_at=evidence.get("published_at"),
        fetched_at=evidence.get("fetched_at"),
    )
    return registry.marker(number)


def _bullet_points(value: object, max_points: int = 4, limit_each: int = 115) -> list[str]:
    max_points = min(max_points, 3)
    limit_each = min(limit_each, 90)
    text = _reader_text(value, 2400)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？；])|(?=其[一二三四五]，)|(?=第一层|第二层|第三层)", text)
    output: list[str] = []
    for part in parts:
        point = re.sub(r"^(?:其[一二三四五]，|第一层是|第二层是|第三层是)", "", part.strip())
        point = _plain(point, limit_each)
        if not _meaningful(point) or point in output:
            continue
        output.append(point)
        if len(output) >= max_points:
            break
    return output


def _bullets_html(value: object, max_points: int = 4, limit_each: int = 115) -> str:
    points = _bullet_points(value, max_points=max_points, limit_each=limit_each)
    return "<ul class='reader-bullets'>" + "".join(f"<li>{_e(point)}</li>" for point in points) + "</ul>" if points else ""


def _market_digest(value: object, max_points: int = 4, limit_each: int = 180) -> str:
    """Render market analysis as complete prose instead of synthetic labels.

    The source often contains several connected sentences rather than a true
    checklist.  Turning each sentence into a keyword badge made ordinary prose
    look like machine-generated notes (for example, badges named ``此外`` or
    ``关键信号``).  Keep the original semantic units as readable paragraphs;
    real lists remain the responsibility of the section that owns them, such as
    the discrete market-risk list.
    """
    text = _reader_text(value, 1800)
    if not text:
        return ""
    text = re.sub(
        r"^(?:由于)?缺乏板块明细数据[，,]?(?:仅能)?结合指数特征推断[：:]?",
        "从指数相对表现看，",
        text,
    )
    text = re.sub(
        r"^[^。！？；]{0,40}(?:有|分为|包括)(?:两|三|四|五|六|几)(?:层|点|条|项|类)[：:]\s*",
        "",
        text,
    )
    raw_parts = [
        part.strip()
        for part in re.split(
            r"(?<=[。！？；])|(?=其[一二三四五六][，、])|(?=[一二三四五六]是)|(?=第[一二三四五六]层)",
            text,
        )
        if _meaningful(part)
    ]
    parts: list[str] = []
    for part in raw_parts:
        cleaned = re.sub(
            r"^(?:其[一二三四五六][，、]|[一二三四五六]是|第[一二三四五六]层(?:是|为)?[：:]?)",
            "",
            part,
        ).strip()
        cleaned = _plain(cleaned, limit_each)
        if not cleaned or cleaned in parts:
            continue
        if cleaned[-1:] not in "。！？；":
            cleaned += "。"
        parts.append(cleaned)
        if len(parts) >= max_points:
            break
    if not parts:
        parts = [_plain(text, limit_each)]
    return "<div class='market-copy market-prose'>" + "".join(
        f"<p>{_e(part)}</p>" for part in parts
    ) + "</div>"


def _labeled_points(items: Sequence[tuple[str, object]], limit_each: int = 150) -> str:
    rows = []
    for label, value in items:
        text = _reader_text(value, limit_each)
        if _meaningful(text):
            rows.append(f"<li><b>{_e(label)}：</b>{_e(text)}</li>")
    return "<ul class='reader-bullets'>" + "".join(rows) + "</ul>" if rows else ""


def _public_plan(value: object, limit: int = 220) -> str:
    text = _plain(value, 1000)
    clauses = [part.strip() for part in re.split(r"(?<=[。；])", text) if part.strip()]
    kept = [part for part in clauses if not re.search(r"(?:建议)?仓位|\d+\s*成", part)]
    return _plain("".join(kept), limit) or "结合触发条件与个人风险预算评估。"


def _num(value: object, digits: int = 2, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.{digits}f}{suffix}"


def _price_num(value: object, market: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if market != "hk":
        return f"{number:.2f}"
    magnitude = abs(number)
    if magnitude <= 0.25:
        tick = 0.001
    elif magnitude <= 0.5:
        tick = 0.005
    elif magnitude <= 10:
        tick = 0.01
    elif magnitude <= 20:
        tick = 0.02
    elif magnitude <= 100:
        tick = 0.05
    elif magnitude <= 200:
        tick = 0.1
    elif magnitude <= 500:
        tick = 0.2
    elif magnitude <= 1000:
        tick = 0.5
    elif magnitude <= 2000:
        tick = 1.0
    elif magnitude <= 5000:
        tick = 2.0
    else:
        tick = 5.0
    rounded = round(number / tick) * tick
    digits = 3 if tick == 0.001 else 2 if tick < 0.1 else 1 if tick < 1 else 0
    return f"{rounded:.{digits}f}"


def _reader_price_text(value: object, market: str, limit: int = 220) -> str:
    text = _reader_text(value, max(limit, 600))

    def replace(match: re.Match[str]) -> str:
        return _price_num(match.group(0), market)

    normalized = re.sub(r"(?<![\w.])\d+\.\d{3,6}(?![\w.])", replace, text)
    return _plain(normalized, limit)


def _canonical(symbol: str, market: str) -> str:
    value = str(symbol or "").strip().upper()
    if market == "hk":
        digits = "".join(character for character in value if character.isdigit())
        return f"HK{digits.zfill(5)}"
    return value.replace(".", "-")


def _contract_map(payload: Mapping[str, Any], market: str) -> dict[str, dict]:
    return {
        item["identity"]["symbol"]: item
        for item in payload.get("stocks", [])
        if item.get("identity", {}).get("market") == market
    }


def _stock_map(stocks: Sequence[ResearchStock], market: str) -> dict[str, ResearchStock]:
    return {_canonical(item.symbol, market): item for item in stocks}


def _event_map(payload: Mapping[str, Any], market: str) -> dict[str, list[dict]]:
    output: dict[str, list[dict]] = {}
    for item in payload.get("events", []):
        if item.get("symbol") == "__MARKET__" or item.get("market") != market:
            continue
        symbol = _canonical(str(item.get("symbol") or ""), market)
        output.setdefault(symbol, []).append(item)
    severity = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    for items in output.values():
        items.sort(
            key=lambda item: (
                item.get("status") == "upcoming",
                severity.get(str(item.get("severity")), 0),
                str(item.get("effective_at") or ""),
            ),
            reverse=True,
        )
    return output


def _index_table(research: MarketResearch) -> str:
    if not research.index_headers or not research.index_rows:
        return ""
    candidates = [
        index
        for index, value in enumerate(research.index_headers)
        if value in {"指数", "最新", "涨跌幅", "振幅", "成交额(亿)"}
    ]
    keep = []
    for index in candidates:
        header = research.index_headers[index]
        values = [row[index] for row in research.index_rows if index < len(row)]
        if header != "指数" and values and not any(_meaningful(value) for value in values):
            continue
        keep.append(index)
    headers = "".join(f"<th>{_e(research.index_headers[index])}</th>" for index in keep)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_e(row[index])}</td>" for index in keep) + "</tr>"
        for row in research.index_rows
    )
    return f'<table class="index-table"><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>'


def _status(status: str) -> str:
    return f'<span class="status status-{STATUS_CLASS.get(status, "research")}">{_e(status)}</span>'


STATUS_GUIDE = {
    "可执行": "价格、形态与主要确认项均已满足；仍需遵守风险线与有效窗口。",
    "谨慎可执行": "价格条件已满足，但置信度、量能或观察级事件仍有软约束；按较低确定性跟踪。",
    "等待触发": "没有硬事件阻挡，但价格、量能或跨交易日守稳确认尚未完成。",
    "事件观察": "财报、融资或政策事件尚未落地；事件后重新评估，不能用普通技术触发替代。",
    "研究关注": "逻辑值得跟踪，但证据或交易结构尚不足以形成明确触发。",
    "风险失效": "原机会逻辑已经破坏；等待新的独立逻辑重建。",
}


def _event_checkpoint(events: Sequence[Mapping[str, Any]]) -> str:
    for event in events:
        if str(event.get("status") or "") != "upcoming":
            continue
        day = str(event.get("effective_at") or "")[:10]
        label = EVENT_LABELS.get(str(event.get("event_type") or ""), "公司事项")
        return (
            f"{day} {label}落地后，以首个完整交易日收盘和量价结构重估"
            if day
            else f"{label}落地后，以首个完整交易日收盘和量价结构重估"
        )
    gated = [event for event in events if str(event.get("gate_until") or "")]
    if gated:
        event = max(gated, key=lambda value: str(value.get("gate_until") or ""))
        day = str(event.get("effective_at") or event.get("published_at") or "")[:10]
        gate_until = str(event.get("gate_until") or "")[:10]
        label = EVENT_LABELS.get(str(event.get("event_type") or ""), "公司事项")
        return f"{day} {label}闸门有效至 {gate_until}；到期后复核条款与价格消化"
    if events:
        event = max(
            events,
            key=lambda value: str(value.get("effective_at") or value.get("published_at") or ""),
        )
        day = str(event.get("effective_at") or event.get("published_at") or "")[:10]
        label = EVENT_LABELS.get(str(event.get("event_type") or ""), "公司事项")
        prefix = f"{day} {label}" if day else label
        return f"{prefix}后，待首个完整交易日收盘及量价确认再重估"
    return "尚无可核验事件日期，当前不启用主动触发"


def _status_explanation(status: str, events: Sequence[Mapping[str, Any]] = ()) -> str:
    if status != "事件观察":
        return STATUS_GUIDE.get(status, "等待条件进一步明确。")
    checkpoint = _event_checkpoint(events)
    if any(str(event.get("status") or "") == "upcoming" for event in events):
        return f"事件尚未落地，普通技术触发暂停；{checkpoint}。"
    return f"重大事件刚发生或闸门仍在生效，普通技术触发暂停；{checkpoint}。"


def _next_checkpoint(item: Mapping[str, Any], events: Sequence[Mapping[str, Any]] = ()) -> str:
    execution = item["execution"]
    status = str(execution.get("status") or "研究关注")
    card = execution.get("standard_trade_card") or {}
    if status == "事件观察":
        return _event_checkpoint(events)
    if status == "风险失效":
        return "当前不设触发点，等待逻辑重建"
    market = str(item.get("identity", {}).get("market") or "")
    condition = _reader_price_text(
        card.get("lifecycle_message")
        or card.get("watch_condition")
        or card.get("trigger_condition")
        or execution.get("note"),
        market,
        125,
    )
    if condition and condition != "当前不启用主动买入触发条件":
        return condition
    trigger = card.get("trigger_price") or card.get("reference_entry")
    if trigger is not None and status in {"可执行", "谨慎可执行", "等待触发"}:
        return f"关注 {_price_num(trigger, str(item.get('identity', {}).get('market') or ''))} 附近的价格与量能确认"
    return _reader_price_text(execution.get("note"), market, 125) or STATUS_GUIDE.get(status, "等待条件进一步明确")


def _top5_cards(
    ranked: Sequence[Mapping[str, Any]],
    event_map: Mapping[str, Sequence[Mapping[str, Any]]],
    stock_map: Mapping[str, ResearchStock] | None = None,
    registry: _SourceRegistry | None = None,
) -> str:
    cards = []
    for item in ranked[:5]:
        identity = item["identity"]
        opportunity = item["opportunity"]
        execution = item["execution"]
        checkpoint = _next_checkpoint(item, event_map.get(identity["symbol"], ()))
        catalyst = _plain(opportunity.get("primary_catalyst"), 58)
        no_catalyst = _is_no_catalyst(catalyst)
        catalyst = "当前无新增公司催化" if no_catalyst else catalyst
        risk = _plain(opportunity.get("primary_risk"), 52) or "—"
        stock = (stock_map or {}).get(identity["symbol"])
        catalyst_marker = (
            _stock_source_marker(stock, catalyst, registry, kind="catalyst")
            if stock is not None and not no_catalyst
            else ""
        )
        risk_marker = (
            _stock_source_marker(stock, risk, registry, kind="risk")
            if stock is not None
            else ""
        )
        cards.append(
            f"<article class='top5-card'><div class='top5-rank'>{opportunity['rank']}</div>"
            f"<div class='top5-identity'><b>{_e(identity['name'])}</b><small>{_e(identity['symbol'])}</small></div>"
            f"<div class='top5-state'>{_status(execution['status'])}</div>"
            f"<div class='top5-decision'><small>下一确认点</small><strong>{_e(_plain(checkpoint, 105))}</strong></div>"
            f"<div class='top5-score'><b>{_num(opportunity['score'], 1)}</b><span>机会分</span></div>"
            f"<div class='top5-context {'top5-neutral' if no_catalyst else 'top5-catalyst'}'><b>催化</b>{_e(catalyst)}{catalyst_marker}</div>"
            f"<div class='top5-context top5-risk'><b>风险</b>{_e(risk)}{risk_marker}</div></article>"
        )
    return "".join(cards)


def _macro_time_display(value: object) -> str:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    except (TypeError, ValueError):
        return "时间待确认"
    beijing = moment.astimezone(ZoneInfo("Asia/Shanghai"))
    eastern = moment.astimezone(ZoneInfo("America/New_York"))
    return f"北京时间 {beijing:%m-%d %H:%M}｜美东 {eastern:%m-%d %H:%M}"


def _macro_effective_at(item: Mapping[str, Any]) -> tuple[date, str]:
    """Normalize macro dates to the actual US release day.

    Older Nasdaq snapshots stored the API query date and treated the row's ET
    clock as UTC. They are detected when the source URL date equals the raw
    effective date. Newly normalized snapshots have a source URL one day after
    the effective date and pass through unchanged.
    """
    raw = str(item.get("effective_at") or "")
    moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    source_url = str(item.get("source_url") or "")
    source_day_match = re.search(r"[?&]date=(\d{4}-\d{2}-\d{2})", source_url)
    raw_day = moment.date()
    legacy_nasdaq = (
        item.get("source") == "nasdaq_macro_calendar"
        and source_day_match is not None
        and source_day_match.group(1) == raw_day.isoformat()
    )
    if legacy_nasdaq:
        release_day = raw_day - timedelta(days=1)
        eastern = datetime.combine(
            release_day,
            moment.time().replace(tzinfo=None),
            ZoneInfo("America/New_York"),
        )
        normalized = eastern.astimezone(ZoneInfo("UTC"))
        return release_day, normalized.isoformat()
    eastern = moment.astimezone(ZoneInfo("America/New_York"))
    return eastern.date(), moment.isoformat()


def _macro_sensitive_assets(category: str) -> str:
    mapping = {
        "inflation": "美债收益率、美元、高估值成长股",
        "labor": "美债、美元、小盘股与周期股",
        "labor_weakness": "美债、美元、小盘股与周期股",
        "growth": "美债、美元、周期股与小盘股",
        "survey": "美债、美元、周期股与小盘股",
        "pmi": "美债、美元、工业与科技成长",
        "housing": "美债、REITs、金融与地产链",
        "consumer": "美债、零售、可选消费与小盘股",
        "retail": "美债、零售、可选消费与小盘股",
    }
    return mapping.get(category, "美债、美元及高贝塔权益资产")


def _macro_surprise_pct(item: Mapping[str, Any]) -> float | None:
    """Return the absolute actual-versus-consensus deviation in percent."""
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    actual = _parse_macro_number(metadata.get("actual"))
    estimate = _parse_macro_number(metadata.get("estimate"))
    if actual is None or estimate is None:
        return None
    scale = max(abs(actual), abs(estimate), 1.0)
    return abs(actual - estimate) / scale * 100


def _material_released_macro_group(items: Sequence[Mapping[str, Any]]) -> tuple[bool, float]:
    """Require meaningful market importance and a material consensus miss."""
    maximum = 0.0
    qualified = False
    for item in items:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        threshold = RELEASED_MACRO_SURPRISE_THRESHOLDS.get(
            str(metadata.get("importance") or "low")
        )
        surprise = _macro_surprise_pct(item)
        if surprise is None or threshold is None:
            continue
        maximum = max(maximum, surprise)
        qualified = qualified or surprise >= threshold
    return qualified, maximum


def _macro_display_items(items: Sequence[Mapping[str, Any]], limit: int = 4) -> list[Mapping[str, Any]]:
    """Keep headline/core PPI MoM and YoY rows ahead of narrower sub-series."""
    if not any("ppi" in str(item.get("headline") or "").casefold() for item in items):
        return list(items[:3])

    def priority(item: Mapping[str, Any]) -> tuple[int, float]:
        title = str(item.get("headline") or "").casefold()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        unit = str(metadata.get("unit") or "").casefold()
        headline_rank = 3 if title.startswith("ppi (") else 2 if title.startswith("core ppi") else 1
        frequency_rank = 1 if unit == "yoy" or "(yoy)" in title else 0
        return headline_rank * 10 + frequency_rank, _macro_surprise_pct(item) or 0.0

    return sorted(items, key=priority, reverse=True)[:limit]


def _ppi_released_group_analysis(items: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Add absolute YoY level and prior-period direction to PPI surprise analysis."""
    yoy_rows = []
    comparisons = set()
    for item in items:
        title = str(item.get("headline") or "")
        if "ppi" not in title.casefold():
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        comparison = str(analysis.get("comparison") or "")
        if comparison:
            comparisons.add(comparison)
        unit = str(metadata.get("unit") or "").casefold()
        if unit != "yoy" and "(yoy)" not in title.casefold():
            continue
        actual = _parse_macro_number(metadata.get("actual"))
        previous = _parse_macro_number(metadata.get("previous"))
        if actual is None or previous is None:
            continue
        yoy_rows.append((title, actual, previous))

    accelerating = [row for row in yoy_rows if row[1] > row[2] + 0.05]
    if not accelerating:
        return None

    def row_priority(row: tuple[str, float, float]) -> int:
        title = row[0].casefold()
        return 3 if title.startswith("ppi (") else 2 if title.startswith("core ppi") else 1

    def value_text(value: float) -> str:
        return f"{value:g}%"

    details = []
    for title, actual, previous in sorted(accelerating, key=row_priority, reverse=True)[:3]:
        label = translate_macro_title(title).replace(" (YoY)", "同比")
        details.append(f"{label} {value_text(actual)}（前值 {value_text(previous)}）")
    prefix = "环比与同比/预期信号分化；" if {"above", "below"} <= comparisons else ""
    level_text = "生产端通胀仍处高位" if max(row[1] for row in accelerating) >= 3.0 else "生产端价格压力回升"
    return {
        "summary": f"{prefix}{'、'.join(details)}均较前值回升；{level_text}，不能因单一环比分项偏弱而解读为全面降温。",
        "market": "通胀预期与美债收益率仍有上行风险，降息预期可能后移；企业成本和高估值资产的折现率压力增加。",
        "positive": ["能源", "原材料", "具备强定价权的龙头"],
        "negative": ["高估值科技", "零售/运输", "低毛利制造", "REITs/公用事业"],
    }


def _macro_groups(payload: Mapping[str, Any], report_day: date) -> tuple[list[dict], list[dict]]:
    recent: dict[tuple[str, str], list[dict]] = {}
    upcoming: dict[tuple[str, str], list[dict]] = {}
    for item in payload.get("macro_events", []):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        try:
            effective, _normalized_effective = _macro_effective_at(item)
        except (TypeError, ValueError):
            continue
        importance = str(metadata.get("importance") or "low")
        if importance not in {"high", "medium"}:
            continue
        key = (effective.isoformat(), str(metadata.get("category") or item.get("event_type")))
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        if (
            report_day - timedelta(days=RECENT_MACRO_LOOKBACK_DAYS) <= effective <= report_day
            and analysis.get("status") == "released"
        ):
            # Keep the complete same-day family (for example PPI MoM/YoY and
            # core/headline rows); materiality is evaluated on the packed group.
            recent.setdefault(key, []).append(item)
        elif report_day < effective <= report_day + timedelta(days=7) and importance == "high":
            upcoming.setdefault(key, []).append(item)

    def pack(groups: Mapping[tuple[str, str], list[dict]], is_upcoming: bool) -> list[dict]:
        result = []
        for (day, _), items in groups.items():
            surprise_pct = 0.0
            if not is_upcoming:
                is_material, surprise_pct = _material_released_macro_group(items)
                if not is_material:
                    continue
            display_items = _macro_display_items(items)
            titles = list(
                dict.fromkeys(
                    translate_macro_title(str(item.get("headline") or ""))
                    for item in display_items
                )
            )
            importance_rank = {"high": 2, "medium": 1, "low": 0}
            primary = max(
                items,
                key=lambda item: (
                    _macro_surprise_pct(item) or 0,
                    importance_rank.get(str((item.get("metadata") or {}).get("importance")), 0),
                ),
            )
            metadata = primary.get("metadata") or {}
            analysis = metadata.get("analysis") or {}
            if not is_upcoming:
                analysis = analyze_release(
                    event_type=str(primary.get("event_type") or ""),
                    category=str(metadata.get("category") or ""),
                    actual=metadata.get("actual"),
                    estimate=metadata.get("estimate"),
                    previous=metadata.get("previous"),
                )
            scenarios = list(metadata.get("scenarios") or [])
            values = []
            released_values = []
            for grouped_item in display_items:
                grouped_meta = grouped_item.get("metadata") or {}
                actual = _plain(grouped_meta.get("actual"), 22)
                estimate = _plain(grouped_meta.get("estimate"), 22)
                previous = _plain(grouped_meta.get("previous"), 22)
                title = translate_macro_title(str(grouped_item.get("headline") or ""))
                if estimate or previous:
                    values.append(f"{title}：预期 {estimate or '—'}｜前值 {previous or '—'}")
                if actual or estimate or previous:
                    released_values.append(
                        f"{title}：实际 {actual or '—'}｜预期 {estimate or '—'}｜前值 {previous or '—'}"
                    )
            category = str(metadata.get("category") or items[0].get("event_type") or "")
            summary = _plain(analysis.get("summary"), 150)
            group_analysis = _ppi_released_group_analysis(items) if not is_upcoming else None
            if group_analysis:
                summary = str(group_analysis["summary"])
                analysis = {
                    **analysis,
                    "broad_market": group_analysis["market"],
                    "positive_sectors": group_analysis["positive"],
                    "negative_sectors": group_analysis["negative"],
                }
            elif not is_upcoming and len(items) > 1:
                primary_title = translate_macro_title(str(primary.get("headline") or ""))
                directions = {
                    str(((item.get("metadata") or {}).get("analysis") or {}).get("comparison") or "")
                    for item in items
                }
                prefix = "分项表现分化" if {"above", "below"} <= directions else "各分项方向一致"
                summary = f"{prefix}；最大偏差来自{primary_title}：{summary}"
            group_importance = max(
                (str((item.get("metadata") or {}).get("importance") or "low") for item in items),
                key=lambda value: importance_rank.get(value, 0),
            )
            result.append(
                {
                    "date": day,
                    "time": metadata.get("release_time") or "",
                    "time_display": _macro_time_display(_macro_effective_at(items[0])[1]),
                    "title": " / ".join(titles),
                    "summary": summary,
                    "market": _plain(analysis.get("broad_market"), 180),
                    "positive": analysis.get("positive_sectors") or [],
                    "negative": analysis.get("negative_sectors") or [],
                    "scenarios": scenarios[:3] if is_upcoming else [],
                    "forecast": values,
                    "released_values": released_values,
                    "baseline": _macro_baseline_context(category, items),
                    "sensitive_assets": _macro_sensitive_assets(category),
                    "importance": group_importance,
                    "surprise_pct": round(surprise_pct, 1) if not is_upcoming else None,
                }
            )
        if is_upcoming:
            return sorted(result, key=lambda item: item["date"])
        importance_rank = {"high": 2, "medium": 1}
        selected = sorted(
            result,
            key=lambda item: (
                importance_rank.get(str(item.get("importance")), 0),
                float(item.get("surprise_pct") or 0),
                item["date"],
            ),
            reverse=True,
        )[:2]
        return sorted(selected, key=lambda item: item["date"])

    return pack(recent, False), pack(upcoming, True)[:5]


def _parse_macro_number(value: object) -> float | None:
    text = _plain(value, 30).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _macro_baseline_context(category: str, items: Sequence[Mapping[str, Any]]) -> str:
    """Explain when an in-line result still matters because its absolute level is unusual."""
    estimates = [
        (_plain((item.get("metadata") or {}).get("estimate"), 30), str(item.get("headline") or "").lower())
        for item in items
    ]
    if category == "inflation":
        annual = [(_parse_macro_number(value), title) for value, title in estimates if value]
        if any(number is not None and number >= 3 for number, title in annual if "price" in title or "pce" in title):
            return "即使符合预期，通胀绝对水平仍偏高，利率与高估值成长股压力未必解除。"
        return "符合预期时仍要看核心与服务分项；黏性通胀可能压过表面一致。"
    if category in {"growth", "survey"}:
        for value, title in estimates:
            number = _parse_macro_number(value)
            if number is None:
                continue
            if "gdp" in title and number <= 1.5:
                return "即使符合预期，增长中枢仍偏弱；周期股修复空间可能受限。"
            if "consumer confidence" in title and number < 95:
                return "即使符合预期，信心水平仍偏弱；消费与小盘股未必获得正面推动。"
        return "符合预期不等于中性：还要比较绝对水平、前值修订与分项结构。"
    if category == "labor_weakness":
        return "符合预期时仍要结合四周均值和续请人数；趋势恶化可强于单周意外。"
    if category == "housing":
        return "符合预期时仍要看按揭利率与前值修订；绝对水平偏弱会限制地产链反弹。"
    return "符合预期时仍需比较绝对水平、前值修订和市场已计价程度。"


def _macro_recent_page(items: Sequence[Mapping[str, Any]]) -> str:
    cards = []
    for item in items:
        released_values = "<br>".join(_e(value) for value in item.get("released_values") or [])
        cards.append(
            f"<article class='macro-card'><div class='macro-head'><b>{_e(item['date'])}｜{_e(item['title'])}</b><span>{_e(item.get('time_display') or item['time'])}</span></div>"
            f"<div class='released-facts'><small>实际 / 预期 / 前值</small><b>{released_values or '结果数值待核验'}</b></div>"
            f"<p class='macro-result'>{_e(item['summary'] or '结果已公布。')}</p><p><b>最大预期偏差：</b>{_num(item.get('surprise_pct'), 1, '%')}</p><p><b>对市场：</b>{_e(item['market'] or '市场影响以板块分化为主。')}</p>"
            f"<div class='impact'><span class='positive'>相对受益：{_e('、'.join(item['positive']) or '—')}</span>"
            f"<span class='negative'>相对承压：{_e('、'.join(item['negative']) or '—')}</span></div></article>"
        )
    return "".join(cards) or "<div class='no-content'>最近七日无同时满足影响与预期偏差门槛的数据。</div>"


def _macro_overview_page(
    recent: Sequence[Mapping[str, Any]], upcoming: Sequence[Mapping[str, Any]]
) -> str:
    recent_html = _macro_recent_page(recent[:3])
    schedule = []
    for item in upcoming[:5]:
        forecast = "<br>".join(_e(value) for value in item.get("forecast") or [])
        schedule.append(
            f"<article class='calendar-card'><div class='macro-head'><b>{_e(item['date'])}｜{_e(item['title'])}</b>"
            f"<span>{_e(item['time'])}</span></div><p>{forecast or '关注市场一致预期与前值修订'}</p>"
            f"<small>{_e(item.get('baseline') or '')}</small></article>"
        )
    return (
        "<div class='macro-overview-grid'><div><h3>刚公布：市场如何消化</h3>"
        + recent_html
        + "</div><div><h3>未来七日：先看预期与绝对水平</h3>"
        + ("".join(schedule) or "<div class='no-content'>未来七日无重点数据。</div>")
        + "</div></div>"
    )


def _market_macro_strip(
    recent: Sequence[Mapping[str, Any]], upcoming: Sequence[Mapping[str, Any]]
) -> str:
    recent_rows = "".join(
        f"<li><b>{_e(item['date'])} {_e(item['title'])}</b><span>{_e(item.get('summary') or item.get('market') or '市场已进入消化阶段')}</span></li>"
        for item in recent[:2]
    ) or "<li><span>近三日无新增重点数据。</span></li>"
    upcoming_rows = "".join(
        f"<li><b>{_e(item['date'])} {_e(item['title'])}</b><span>{_e((item.get('forecast') or ['关注一致预期与前值修订'])[0])}</span></li>"
        for item in upcoming[:4]
    ) or "<li><span>未来七日无新增重点日程。</span></li>"
    return f"<div class='market-macro-strip'><div><h3>刚公布</h3><ul>{recent_rows}</ul></div><div><h3>未来七日</h3><ul>{upcoming_rows}</ul></div></div>"


def _macro_probability_meta(scenario: Mapping[str, Any]) -> tuple[str, str]:
    try:
        sample_size = max(0, int(scenario.get("probability_sample_size") or 0))
    except (TypeError, ValueError):
        sample_size = 0
    level = str(scenario.get("probability_calibration_level") or "")
    basis = str(scenario.get("probability_basis") or "")
    if sample_size >= 50 and level not in {"neutral_prior", ""}:
        confidence = "较高"
    elif sample_size >= 20 and level not in {"neutral_prior", ""}:
        confidence = "中等"
    else:
        confidence = "较低"
    basis_label = {
        "historical_category": "同类历史",
        "historical_family": "同系列历史",
        "neutral_prior": "中性先验",
    }.get(level) or (
        "中性先验" if "neutral_prior" in basis else "历史校准" if sample_size else "中性先验"
    )
    sample_label = f"有效样本 n={sample_size}" if sample_size else "无历史有效样本"
    return confidence, f"{basis_label} · {sample_label} · 置信度{confidence}"


def _macro_upcoming_page(items: Sequence[Mapping[str, Any]]) -> str:
    cards = []
    for item in items:
        scenarios = []
        baseline = _plain(item.get("baseline"), 150)
        for scenario_index, scenario in enumerate(item["scenarios"]):
            analysis = _plain(scenario.get("broad_market"), 125)
            confidence, probability_meta = _macro_probability_meta(scenario)
            probability = scenario.get("probability_pct")
            probability_label = f"{'约' if confidence == '较低' else ''}{probability}%"
            is_baseline_scenario = (
                scenario_index == 1 or "符合预期" in str(scenario.get("name") or "")
            )
            if is_baseline_scenario and baseline and baseline not in analysis:
                analysis = _plain(f"{analysis}；还要看：{baseline}", 210)
            scenarios.append(
                f"<div class='scenario'><b>{_e(scenario.get('name'))} · {_e(probability_label)}</b>"
                f"<strong>{_e(analysis)}</strong>"
                f"<small>受益：{_e('、'.join(scenario.get('positive_sectors') or []))}　承压：{_e('、'.join(scenario.get('negative_sectors') or []))}</small>"
                f"<small class='probability-meta'>{_e(probability_meta)}</small></div>"
            )
        forecasts = "<br>".join(_e(value) for value in item.get("forecast") or []) or "市场预期与前值待确认"
        cards.append(
            f"<article class='upcoming-card'><div class='macro-head'><b>{_e(item['date'])}｜{_e(item['title'])}</b><span>{_e(item.get('time_display') or item['time'])}</span></div>"
            f"<div class='macro-facts'><div><small>市场预期 / 前值</small><b>{forecasts}</b></div>"
            f"<div><small>最敏感资产</small><b>{_e(item.get('sensitive_assets'))}</b></div></div>"
            f"<div class='scenario-grid'>{''.join(scenarios)}</div></article>"
        )
    return "".join(cards) or "<div class='no-content'>未来七日无需要展开的重点数据。</div>"


def _relative_strength_signal(relative_strength: Mapping[str, Any]) -> str:
    market = relative_strength.get("market_excess_20d_pct")
    industry = relative_strength.get("industry_excess_20d_pct")
    try:
        market_value = float(market)
        industry_value = float(industry)
    except (TypeError, ValueError):
        return "待确认"
    if market_value >= 3 and industry_value >= 3:
        return "双重领先"
    if market_value > 0 and industry_value > 0:
        return "相对领先"
    if market_value > 0 or industry_value > 0:
        return "局部领先"
    return "相对偏弱"


def _volume_signal(confirmation: Mapping[str, Any]) -> tuple[str, str]:
    value = confirmation.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待确认", "—"
    if number >= 1.35:
        label = "明显放量"
    elif number >= 1.05:
        label = "温和放量"
    elif number >= 0.75:
        label = "量能正常"
    else:
        label = "量能偏弱"
    return label, _num(number, 2, "×")


def _ranking_rows(ranked: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for item in ranked:
        identity = item["identity"]
        opp = item["opportunity"]
        execution = item["execution"]
        rs = item["relative_strength"]
        volume = item["market_data"]["volume"]["confirmation"]
        volume_label, volume_value = _volume_signal(volume)
        rows.append(
            f"<tr class='{'top-row' if opp['rank'] <= 5 else ''}'><td>{opp['rank']}</td><td><b>{_e(identity['name'])}</b><small>{_e(identity['symbol'])}</small></td>"
            f"<td class='ranking-score'><b>{_num(opp['score'], 1)}</b></td><td>{_status(execution['status'])}</td>"
            f"<td><span class='ranking-signal'>{_e(_relative_strength_signal(rs))}</span></td>"
            f"<td><b>{_e(volume_label)}</b><small>{_e(volume_value)}</small></td>"
            f"<td class='ranking-condition'>{_e(_reader_price_text(execution.get('note'), str(identity.get('market') or ''), 82) or '—')}</td></tr>"
        )
    return "".join(rows)


def _score_int(value: object) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return 0


def _semicircle_gauge(value: object, status: object) -> str:
    score = _score_int(value)
    angle = math.pi - math.pi * score / 100
    needle_x = 100 + 54 * math.cos(angle)
    needle_y = 92 - 54 * math.sin(angle)
    return f"""
    <div class='crowding-gauge'>
      <svg viewBox='0 0 200 118' role='img' aria-label='AI 拥挤度 {score}'>
        <defs><linearGradient id='crowding-gradient' x1='0%' y1='0%' x2='100%' y2='0%'><stop offset='0%' stop-color='#16a34a'/><stop offset='45%' stop-color='#facc15'/><stop offset='72%' stop-color='#f97316'/><stop offset='100%' stop-color='#dc2626'/></linearGradient></defs>
        <path d='M 28 92 A 72 72 0 0 1 172 92' fill='none' stroke='#e2e8f0' stroke-width='18' stroke-linecap='round'/>
        <path d='M 28 92 A 72 72 0 0 1 172 92' fill='none' stroke='url(#crowding-gradient)' stroke-width='14' stroke-linecap='round'/>
        <line x1='100' y1='92' x2='{needle_x:.1f}' y2='{needle_y:.1f}' stroke='#172033' stroke-width='4' stroke-linecap='round'/>
        <circle cx='100' cy='92' r='7' fill='#172033'/>
        <text x='25' y='111' font-size='12' font-weight='700' fill='#15803d'>冷静</text><text x='147' y='111' font-size='12' font-weight='700' fill='#b91c1c'>极热</text>
      </svg>
      <div class='gauge-score'><strong>{score}</strong><span>{_e(status)}</span></div>
    </div>"""


def _pointer_bar(label: str, value: object, *, healthy_high: bool = False) -> str:
    score = _score_int(value)
    direction = "healthy" if healthy_high else "risk"
    return f"""
    <div class='pointer-metric'><div><b>{_e(label)}</b><strong>{score}</strong></div>
      <div class='pointer-track pointer-{direction}'><i style='left:{score}%'></i></div>
    </div>"""


def _trend_value(value: object, *, suffix: str = "", signed: bool = True) -> str:
    if not isinstance(value, (int, float)):
        return "积累中"
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{round(float(value))}{suffix}"


def _ai_trend_strip(snapshot: Mapping[str, Any]) -> str:
    samples = int(snapshot.get("history_samples") or 0)
    percentile = snapshot.get("percentile_1y") if samples >= 2 else None
    return f"""
    <div class='ai-trend-strip'>
      <div><small>较前一交易日</small><b>{_trend_value(snapshot.get('change_1d'))}</b><span>{_e(snapshot.get('comparison_1d_date'))}</span></div>
      <div><small>较5日前</small><b>{_trend_value(snapshot.get('change_5d'))}</b><span>{_e(snapshot.get('comparison_5d_date'))}</span></div>
      <div><small>近一年历史分位</small><b>{_trend_value(percentile, suffix='%', signed=False)}</b><span>{samples}期样本</span></div>
    </div>"""


def _ai_crowding_panel(snapshot: Mapping[str, Any] | None) -> str:
    if not snapshot:
        return ""
    bars = "".join(
        (
            _pointer_bar("回撤脆弱度", snapshot.get("break_risk")),
            _pointer_bar("趋势热度", snapshot.get("momentum_heat")),
            _pointer_bar("投机扩散", snapshot.get("speculation_heat")),
            _pointer_bar("广度健康", snapshot.get("breadth_health"), healthy_high=True),
        )
    )
    risks = "".join(
        f"<span>{_e(_reader_text(value, 78))}</span>" for value in (snapshot.get("risk_lines") or [])[:2]
    )
    return f"""
    <div class="panel ai-crowding-panel"><div class='ai-dashboard-title'><div><small>AI 主题风险温度</small><h3>AI 拥挤度</h3></div><b>热度与脆弱度分开看</b></div>
      <div class='ai-dashboard-body'>{_semicircle_gauge(snapshot.get('crowding_index'), snapshot.get('status'))}<div class='ai-dashboard-copy'><p>{_e(snapshot.get('interpretation'))}</p>{_ai_trend_strip(snapshot)}<div class='pointer-grid'>{bars}</div><div class='ai-risk-tags'>{risks}</div></div></div>
    </div>"""


def _ai_crowding_page(snapshot: Mapping[str, Any] | None) -> str:
    if not snapshot:
        return ""
    component_html = "".join(
        (
            _pointer_bar("趋势热度", snapshot.get("momentum_heat")),
            _pointer_bar("社媒热度", snapshot.get("social_heat")),
            _pointer_bar("投机扩散", snapshot.get("speculation_heat")),
            _pointer_bar("集中风险", snapshot.get("concentration_risk")),
            _pointer_bar("宏观压力", snapshot.get("macro_pressure")),
            _pointer_bar("广度健康", snapshot.get("breadth_health"), healthy_high=True),
        )
    )
    return f"""
    <section class="page ai-page"><div class="page-title"><div><span class="eyebrow">专题风险仪表盘</span><h2>美股 AI 拥挤度</h2></div><span class="count">热度与脆弱度分开计算</span></div>
      {_ai_crowding_panel(snapshot)}
      <div class="ai-component-grid">{component_html}</div>
    </section>"""


def _component_grid(item: Mapping[str, Any]) -> str:
    output = []
    for key, value in item["opportunity"]["components"].items():
        cap = COMPONENT_CAPS[key]
        width = max(0, min(100, float(value) / cap * 100))
        output.append(
            f"<div class='component'><div><span>{_e(COMPONENT_LABELS[key])}</span><b>{_num(value,1)}/{cap}</b></div>"
            f"<i><em style='width:{width:.1f}%'></em></i></div>"
        )
    technical = item["opportunity"].get("technical_components") or {}
    if technical:
        details = " · ".join(
            f"{TECHNICAL_COMPONENT_LABELS.get(key, key)} {_num(value, 1)}"
            for key, value in technical.items()
        )
        output.append(
            "<div class='technical-weight-note'><b>技术分 "
            f"{_num(item['opportunity'].get('technical_score'), 1)}/100</b>"
            f"<span>{_e(details)}</span></div>"
        )
    return "".join(output)


def _event_items(
    events: Sequence[Mapping[str, Any]], registry: _SourceRegistry | None = None
) -> str:
    items = []
    seen = set()
    for event in events:
        event_type = str(event.get("event_type") or "")
        label = EVENT_LABELS.get(event_type, "公司事项")
        day = str(event.get("effective_at") or event.get("published_at") or "")[:10]
        key = (label, day)
        if key in seen:
            continue
        seen.add(key)
        headline = _translated_event_headline(event.get("headline"))
        marker = _event_source_marker(event, registry)
        items.append(
            f"<li><b>{_e(day)} · {_e(label)}</b><span>{_e(headline)}{marker}</span></li>"
        )
        if len(items) >= 3:
            break
    return "".join(items) or "<li><b>本期无重大公司日程变化</b></li>"


def _earnings_outlook_periods(value: object) -> list[date]:
    """Return period-end dates claimed as already disclosed in reader copy."""
    text = str(value or "")
    periods: list[date] = []
    for match in re.finditer(
        r"最近已披露(?:季度|业绩期)[^；。]*?(?:截止|期末)\s*(20\d{2}-\d{2}-\d{2})",
        text,
    ):
        try:
            periods.append(date.fromisoformat(match.group(1)))
        except ValueError:
            continue
    return periods


def _earnings_temporal_issues(
    stocks: Sequence[ResearchStock], report_day: date
) -> list[str]:
    issues = []
    for stock in stocks:
        for period_day in _earnings_outlook_periods(stock.earnings_outlook):
            if period_day > report_day:
                issues.append(
                    f"{stock.symbol} 已披露业绩期 {period_day.isoformat()} 晚于报告日 {report_day.isoformat()}"
                )
    return issues


def _reader_earnings_outlook(value: object, report_day: date | None) -> str:
    """Normalize fiscal-period wording and hide time-inconsistent facts."""
    text = str(value or "").strip()
    if not text or report_day is None:
        return text
    invalid_period = any(period_day > report_day for period_day in _earnings_outlook_periods(text))
    clauses = [part.strip() for part in re.split(r"[；。]", text) if part.strip()]
    if invalid_period:
        clauses = [
            part
            for part in clauses
            if not re.search(r"最近已披露(?:季度|业绩期)|实际\s*EPS|近\s*4\s*季", part, re.I)
        ]
    normalized = "；".join(clauses)
    normalized = re.sub(
        r"最近已披露季度\s+\d{4}Q[1-4]\s*（截止\s*(20\d{2}-\d{2}-\d{2})）",
        r"最近已披露业绩期（期末 \1）",
        normalized,
    )
    return normalized + ("。" if normalized else "")


def _earnings_scenario_block(
    item: Mapping[str, Any],
    stock: ResearchStock,
    report_day: date | None = None,
    registry: _SourceRegistry | None = None,
) -> str:
    scenario = item.get("earnings_scenario") if isinstance(item.get("earnings_scenario"), Mapping) else {}
    if scenario.get("status") != "upcoming":
        outlook = _reader_earnings_outlook(stock.earnings_outlook, report_day)
        marker = _stock_source_marker(
            stock,
            outlook,
            registry,
            kind="target" if "目标价" in outlook or "机构推荐" in outlook else "earnings",
        )
        block = _labeled_points(
            [
                ("业绩", outlook),
                ("舆情", stock.sentiment_summary),
                ("主题", stock.hot_topics),
            ],
            180,
        )
        return block + marker
    history = scenario.get("history") if isinstance(scenario.get("history"), Mapping) else {}
    baseline = _reader_text(scenario.get("baseline"), 125)
    distance = scenario.get("trading_days_to_event")
    distance_text = (
        f"距报告日 {distance} 个交易日"
        if distance is not None
        else f"距报告日 {scenario.get('days_to_event')} 天"
    )
    facts = [
        f"{scenario.get('event_date')} {scenario.get('timing')}，{distance_text}",
        f"基准倾向 {scenario.get('prior')}（置信度{scenario.get('confidence')}）",
    ]
    if baseline:
        facts.append(baseline)
    elif history.get("total"):
        facts.append(f"近{history.get('total')}季超预期 {history.get('beats')} 次")
    rows = []
    for value in scenario.get("scenarios") or []:
        if not isinstance(value, Mapping):
            continue
        probability = value.get("probability_pct")
        case_label = str(value.get("name") or "")
        if probability is not None:
            case_label += f" {int(probability)}%"
        rows.append(
            f"<div class='earnings-case'><b>{_e(case_label)}</b>"
            f"<span>{_e(_plain(value.get('decision'), 105))}</span></div>"
        )
    return (
        "<div class='earnings-summary'>" + "<span>" + "</span><span>".join(_e(value) for value in facts) + "</span></div>"
        + "<div class='earnings-cases'>" + "".join(rows) + "</div>"
    )


def _earnings_scenario_brief(item: Mapping[str, Any]) -> str:
    scenario = item.get("earnings_scenario") if isinstance(item.get("earnings_scenario"), Mapping) else {}
    if scenario.get("status") != "upcoming":
        return ""
    cases = [value for value in scenario.get("scenarios") or [] if isinstance(value, Mapping)]
    upside = _plain(cases[0].get("decision"), 58) if cases else ""
    downside = _plain(cases[-1].get("decision"), 58) if cases else ""
    return (
        f"<p class='earnings-brief'><b>财报推演：</b>{_e(scenario.get('event_date'))} "
        f"· 基准{_e(scenario.get('prior'))}；超预期：{_e(upside)}；低于预期：{_e(downside)}</p>"
    )


def _earnings_metric_cells(
    metrics: object,
    limit: int = 4,
    empty_text: str = "暂无可靠公开口径",
) -> str:
    rows = []
    for item in metrics if isinstance(metrics, Sequence) and not isinstance(metrics, (str, bytes)) else []:
        if not isinstance(item, Mapping):
            continue
        change = _plain(item.get("change") or item.get("range"), 42)
        rows.append(
            f"<div><small>{_e(item.get('label'))}</small><b>{_e(item.get('value'))}</b>"
            f"{f'<span>{_e(change)}</span>' if change else ''}</div>"
        )
        if len(rows) >= limit:
            break
    return "".join(rows) or f"<div><small>数据覆盖</small><b>{_e(empty_text)}</b></div>"


def _earnings_history_line(history: Mapping[str, Any]) -> str:
    total = int(history.get("total") or 0)
    if not total:
        return _plain(history.get("coverage_note") or "暂无稳定可比一致预期序列", 110)
    return (
        f"近{total}次：超预期 {int(history.get('beats') or 0)} / "
        f"符合 {int(history.get('meets') or 0)} / 低于 {int(history.get('misses') or 0)}"
    )


def _earnings_history_details(history: Mapping[str, Any]) -> str:
    rows = []
    for item in history.get("details") if isinstance(history.get("details"), list) else []:
        if not isinstance(item, Mapping):
            continue
        comparison = " / ".join(
            value for value in (_plain(item.get("actual"), 24), _plain(item.get("estimate"), 24)) if value
        )
        rows.append(
            f"<span><b>{_e(item.get('period') or '—')}</b>{_e(item.get('outcome') or '—')}"
            f"{f' · {_e(comparison)}' if comparison else ''}</span>"
        )
        if len(rows) >= 4:
            break
    return "".join(rows)


def _earnings_deep_card(
    item: Mapping[str, Any], registry: _SourceRegistry | None = None
) -> str:
    identity = item.get("identity") or {}
    scenario = item.get("earnings_scenario") or {}
    previous = scenario.get("previous_report") if isinstance(scenario.get("previous_report"), Mapping) else {}
    consensus = scenario.get("consensus") if isinstance(scenario.get("consensus"), Mapping) else {}
    history = scenario.get("history") if isinstance(scenario.get("history"), Mapping) else {}
    event_kind = str(scenario.get("event_kind") or "")
    event_badge = (
        "核心数据已预披露"
        if event_kind
        in {
            "formal_confirmation_after_quarterly_release",
            "formal_confirmation_after_preannouncement",
        }
        else "财报待发布"
    )
    previous_heading = "已披露核心数据" if event_kind == "formal_confirmation_after_quarterly_release" else "上一次财报"
    cases = []
    for value in scenario.get("scenarios") or []:
        if not isinstance(value, Mapping):
            continue
        cases.append(
            f"<div class='earnings-deep-case'><div><b>{_e(value.get('name'))}</b>"
            f"<strong>{_e(value.get('probability_pct'))}%</strong></div>"
            f"<p>{_e(_plain(value.get('condition'), 105))}</p>"
            f"<span>{_e(_plain(value.get('result'), 72))}</span>"
            f"<small>{_e(_plain(value.get('decision'), 82))}</small></div>"
        )
    checks = "".join(
        f"<li>{_e(_plain(value, 54))}</li>" for value in (scenario.get("key_checks") or [])[:5]
    )
    source_markers = []
    for value in (scenario.get("sources") or [])[:4]:
        if not isinstance(value, Mapping) or not value.get("url"):
            continue
        if registry is None:
            source_markers.append(
                f"<a href='{_e(value.get('url'))}'>{_e(value.get('label'))}</a>"
            )
            continue
        number = registry.add(
            label=value.get("label") or "公司财报资料",
            url=value.get("url"),
            source=value.get("source"),
            published_at=value.get("published_at") or value.get("date"),
            fetched_at=value.get("fetched_at"),
        )
        source_markers.append(registry.marker(number))
    sources = " ".join(source_markers)
    previous_summary = _plain(previous.get("summary"), 105)
    consensus_summary = _plain(consensus.get("summary"), 105)
    consensus_metrics = [
        value for value in (consensus.get("metrics") or []) if isinstance(value, Mapping)
    ]
    guidance_metrics = [
        value for value in (scenario.get("company_guidance") or []) if isinstance(value, Mapping)
    ]
    forecast_metrics = [*consensus_metrics[:2], *guidance_metrics[:2]] or consensus_metrics
    trading_days = scenario.get("trading_days_to_event")
    trading_day_text = (
        "今日"
        if trading_days == 0
        else f"{trading_days}个交易日"
        if trading_days is not None
        else "日期待确认"
    )
    return f"""
    <article class="earnings-deep-card">
      <div class="earnings-deep-head"><div class="earnings-identity"><b>{_e(identity.get('name'))}</b><span>{_e(identity.get('symbol'))} · {_e(scenario.get('period'))}</span></div><div class="earnings-event"><strong>{_e(trading_day_text)}</strong><span>{_e(scenario.get('event_date'))} {_e(scenario.get('timing'))} · {_e(event_badge)}</span></div></div>
      <div class="earnings-fact-grid">
        <section><h3>{_e(previous_heading)} · {_e(previous.get('period') or '可比期')}</h3><div class="earnings-mini-grid">{_earnings_metric_cells(previous.get('metrics'), empty_text='未取得可核验的上一期数值')}</div>{f'<p>{_e(previous_summary)}</p>' if previous_summary else ''}</section>
        <section><h3>本次公开预测 / 公司指引</h3><div class="earnings-mini-grid">{_earnings_metric_cells(forecast_metrics, empty_text='暂无可靠公开一致预期或公司指引')}</div>{f'<p>{_e(consensus_summary)}</p>' if consensus_summary else ''}</section>
        <section><h3>历史兑现</h3><b class="earnings-history-total">{_e(_earnings_history_line(history))}</b><div class="earnings-history-list">{_earnings_history_details(history)}</div></section>
      </div>
      <div class="earnings-deep-cases">{''.join(cases)}</div>
      <div class="earnings-deep-foot"><div><b>财报最重要验证项</b><ul>{checks or '<li>收入、利润、指引与价格反应</li>'}</ul></div><div><b>概率口径</b><p>{_e(_plain(scenario.get('probability_method'), 145))}</p><small>{sources}</small></div></div>
    </article>"""


def _earnings_deep_pages(
    ranked: Sequence[Mapping[str, Any]],
    market_label: str,
    registry: _SourceRegistry | None = None,
) -> str:
    upcoming = [
        item
        for item in ranked
        if (item.get("earnings_scenario") or {}).get("status") == "upcoming"
        and (item.get("earnings_scenario") or {}).get("detailed")
    ]
    if not upcoming:
        return ""
    # Decision cards are capped at two per page for both markets. Two-card
    # pages use a dedicated balanced layout; an odd single-card tail keeps its
    # natural height and is never stretched to a full page.
    groups = _balanced_chunks(upcoming, 2)
    pages = []
    for index, group in enumerate(groups, 1):
        cards = "".join(_earnings_deep_card(item, registry) for item in group)
        pages.append(
            f'<section class="page earnings-focus-page" id="earnings-focus-{index}"><div class="page-title"><div><span class="eyebrow">{_e(market_label)}财报前瞻</span><h2>未来3个交易日 · 个股财报决策卡</h2></div><span class="count">{len(upcoming)}只 · {index}/{len(groups)}</span></div><p class="earnings-prob-note">概率为基于历史兑现、公开预测与公司指引的条件估计，不是市场隐含概率；历史回看严格使用报告截止日前信息。</p><div class="earnings-deep-stack cards-{len(group)}">{cards}</div></section>'
        )
    return "".join(pages)


def _data_cell(label: str, value: object) -> str:
    if not _meaningful(value):
        return ""
    return f"<div><small>{_e(label)}</small><b>{_e(value)}</b></div>"


def _quality_level(value: object) -> tuple[str, str, int]:
    try:
        score = max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return "待确认", "unknown", 0
    if score >= 80:
        return "高", "high", score
    if score >= 60:
        return "中", "medium", score
    return "低", "low", score


def _quality_panel(quality: Mapping[str, Any]) -> str:
    completeness_label, completeness_class, completeness = _quality_level(quality.get("data_completeness"))
    confidence_label, confidence_class, confidence = _quality_level(quality.get("signal_confidence"))
    gaps = [
        _reader_text(value, 42)
        for value in quality.get("major_gaps") or []
        if _meaningful(_reader_text(value, 42))
    ]
    gap_html = "".join(f"<span>{_e(value)}</span>" for value in gaps[:3]) or "<span class='quality-complete'>关键维度覆盖完整</span>"
    return f"""
    <div class='quality-panel'>
      <div class='quality-score quality-{completeness_class}'><div class='quality-head'><small>数据完整度</small><b>{completeness}<em>{completeness_label}</em></b></div><div class='quality-track'><span style='width:{completeness}%'></span></div></div>
      <div class='quality-score quality-{confidence_class}'><div class='quality-head'><small>信号置信度</small><b>{confidence}<em>{confidence_label}</em></b></div><div class='quality-track'><span style='width:{confidence}%'></span></div></div>
      <div class='quality-gaps'><small>需补强的证据</small><div>{gap_html}</div></div>
    </div>"""


def _detail_data_grid(
    stock: ResearchStock, item: Mapping[str, Any]
) -> str:
    rs = item["relative_strength"]
    market_data = item["market_data"]
    volume = market_data["volume"]
    rows = [
        ("现价", stock.current_price or _num(market_data.get("current_price"))),
        ("日涨跌", stock.change_pct or _num(market_data.get("change_pct"), 2, "%")),
        ("相对成交量", _num(volume["confirmation"].get("value"), 2, "×") if volume["confirmation"].get("value") is not None else ""),
        ("换手率", _num(volume.get("turnover_rate"), 2, "%") if volume.get("turnover_rate") is not None else ""),
        ("ATR14", _num(market_data.get("atr14"), 2) if market_data.get("atr14") is not None else ""),
        ("5日 / 20日", f"{_num(rs.get('stock',{}).get('return_5d_pct'),1,'%')} / {_num(rs.get('stock',{}).get('return_20d_pct'),1,'%')}"),
        ("市场超额 5日 / 20日", f"{_num(rs.get('market_excess_5d_pct'),1,'%')} / {_num(rs.get('market_excess_20d_pct'),1,'%')}"),
        ("行业超额 5日 / 20日", f"{_num(rs.get('industry_excess_5d_pct'),1,'%')} / {_num(rs.get('industry_excess_20d_pct'),1,'%')}"),
    ]
    return "".join(_data_cell(label, value) for label, value in rows)


def _price_structure_payload(item: Mapping[str, Any]) -> Mapping[str, Any]:
    execution = item.get("execution") if isinstance(item.get("execution"), Mapping) else {}
    card = execution.get("standard_trade_card") if isinstance(execution.get("standard_trade_card"), Mapping) else {}
    card_structure = card.get("price_structure") if isinstance(card.get("price_structure"), Mapping) else {}
    if card_structure.get("status") == "ok":
        return card_structure
    market_data = item.get("market_data") if isinstance(item.get("market_data"), Mapping) else {}
    market_structure = market_data.get("price_structure") if isinstance(market_data.get("price_structure"), Mapping) else {}
    return market_structure


def _trend_stage(item: Mapping[str, Any], stock: ResearchStock) -> str:
    structure = _price_structure_payload(item)
    trend = str(structure.get("trend") or "")
    breakout_state = str(structure.get("breakout_state") or "")
    if breakout_state == "above_range":
        stage = "已越过近20日平台上沿，处于突破确认阶段"
    elif breakout_state == "testing_range_high":
        stage = "正在测试近20日平台上沿，尚需收盘确认"
    elif trend == "higher_high_higher_low":
        stage = "近期高点与低点同步抬高，上升结构延续"
    elif trend == "lower_high_lower_low":
        stage = "近期高点与低点同步下移，下降结构尚未扭转"
    elif trend:
        stage = "高低点交错，当前处于区间或趋势过渡阶段"
    else:
        score = stock.technical.get("trend_score")
        if isinstance(score, (int, float)) and score >= 75:
            stage = "中期方向偏上，但仍需价格结构与量能继续确认"
        elif isinstance(score, (int, float)) and score <= 35:
            stage = "中期方向偏弱，尚未形成可靠止跌结构"
        else:
            stage = "趋势方向未充分确认，按区间或过渡结构处理"
    position = structure.get("range_position_pct")
    if isinstance(position, (int, float)):
        stage += f"；价格位于近20日区间约 {position:.0f}% 位置"
    return stage


def _key_structure_levels(item: Mapping[str, Any], stock: ResearchStock) -> str:
    structure = _price_structure_payload(item)
    market = str(item.get("identity", {}).get("market") or getattr(stock, "market", "") or "")
    parts = []
    labels = (
        ("平台上沿", structure.get("breakout_level")),
        ("结构回踩位", structure.get("retest_level")),
        ("最近有效低点", structure.get("last_swing_low") or structure.get("support_level")),
        ("下一阻力", structure.get("next_resistance")),
    )
    for label, value in labels:
        if value is not None:
            parts.append(f"{label} {_price_num(value, market)}")
    if not parts:
        support = stock.technical.get("support_level")
        resistance = stock.technical.get("resistance_level")
        if support is not None:
            parts.append(f"参考支撑 {_price_num(support, market)}")
        if resistance is not None:
            parts.append(f"参考压力 {_price_num(resistance, market)}")
    return "；".join(parts) or "尚无足够日线结构生成可靠关键位"


def _relative_strength_copy(item: Mapping[str, Any]) -> str:
    rs = item.get("relative_strength") if isinstance(item.get("relative_strength"), Mapping) else {}
    stock_rs = rs.get("stock") if isinstance(rs.get("stock"), Mapping) else {}
    label = _relative_strength_signal(rs)
    return (
        f"{label}；个股5/20日 {_num(stock_rs.get('return_5d_pct'), 1, '%')} / "
        f"{_num(stock_rs.get('return_20d_pct'), 1, '%')}，市场超额 "
        f"{_num(rs.get('market_excess_5d_pct'), 1, '%')} / {_num(rs.get('market_excess_20d_pct'), 1, '%')}，"
        f"行业超额 {_num(rs.get('industry_excess_5d_pct'), 1, '%')} / "
        f"{_num(rs.get('industry_excess_20d_pct'), 1, '%')}"
    )


def _volume_confirmation_copy(item: Mapping[str, Any], stock: ResearchStock) -> str:
    market_data = item.get("market_data") if isinstance(item.get("market_data"), Mapping) else {}
    volume_data = market_data.get("volume") if isinstance(market_data.get("volume"), Mapping) else {}
    confirmation = volume_data.get("confirmation") if isinstance(volume_data.get("confirmation"), Mapping) else {}
    label, value = _volume_signal(confirmation)
    meaning = _reader_text(stock.volume_meaning, 145)
    base = f"{label}（{value}）"
    return f"{base}；{meaning}" if meaning else f"{base}；仅将量能作为价格结构的确认条件"


def _public_flat_scenario(item: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    execution = item.get("execution") if isinstance(item.get("execution"), Mapping) else {}
    card = execution.get("standard_trade_card") if isinstance(execution.get("standard_trade_card"), Mapping) else {}
    status = str(execution.get("status") or "研究关注")
    if status == "事件观察":
        return "事件落地前不启用普通技术触发；事件后按事件日区间和首个整理结构重新评估。"
    if status == "风险失效":
        return "当前不设置新触发点，等待形成新的独立机会逻辑与价格结构。"
    checkpoint = _next_checkpoint(item, events)
    if not checkpoint:
        return "等待价格结构、相对强弱和量能形成一致确认。"
    if card.get("price_condition_met"):
        return f"{checkpoint}；空仓者不追价，等待守稳或回踩后的新确认。"
    return f"{checkpoint}；条件未确认前不追价。"


def _public_holding_scenario(item: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    execution = item.get("execution") if isinstance(item.get("execution"), Mapping) else {}
    card = execution.get("standard_trade_card") if isinstance(execution.get("standard_trade_card"), Mapping) else {}
    if str(execution.get("status") or "") == "事件观察":
        return "事件前不依据普通技术信号加仓；按个人风险预算管理，事件落地后重设风险边界。"
    invalidation = _reader_price_text(
        card.get("invalidation_condition"),
        str(item.get("identity", {}).get("market") or ""),
        150,
    )
    if invalidation:
        return f"重点观察结构是否保持；{invalidation}。"
    return "重点观察关键结构位与量价是否保持；原逻辑破坏时重新评估。"


def _technical_structure_points(item: Mapping[str, Any], stock: ResearchStock) -> list[tuple[str, str]]:
    return [
        ("趋势阶段", _trend_stage(item, stock)),
        ("关键结构位", _key_structure_levels(item, stock)),
        ("相对强弱", _relative_strength_copy(item)),
        ("量价确认", _volume_confirmation_copy(item, stock)),
    ]


def _detail_page(
    item: Mapping[str, Any],
    stock: ResearchStock,
    events: Sequence[Mapping[str, Any]],
    market_label: str,
    report_day: date | None = None,
    registry: _SourceRegistry | None = None,
) -> str:
    identity = item["identity"]
    opp = item["opportunity"]
    execution = item["execution"]
    quality = item["quality"]
    rs = item["relative_strength"]
    market_data = item["market_data"]
    volume = market_data["volume"]
    card = execution.get("standard_trade_card") or {}
    risk_rows = []
    for value in stock.risks:
        text = _reader_text(value, 145)
        if _meaningful(text):
            risk_rows.append(
                f"<li>{_e(text)}{_stock_source_marker(stock, text, registry, kind='risk')}</li>"
            )
    risks = "".join(risk_rows)
    real_catalysts = [
        _reader_text(value, 145)
        for value in stock.catalysts
        if _meaningful(_reader_text(value, 145)) and not _is_no_catalyst(value)
    ]
    catalysts = "".join(
        f"<li>{_e(value)}{_stock_source_marker(stock, value, registry, kind='catalyst')}</li>"
        for value in real_catalysts
    )
    socials = "".join(
        f"<li>{_e(_reader_text(value, 110))}</li>"
        for value in stock.social_lines[:3]
        if _meaningful(_reader_text(value, 110))
    )
    rr = execution.get("risk_reward")
    trigger = card.get("reference_entry")
    stop = card.get("stop_loss")
    target1 = card.get("target_1")
    target2 = card.get("target_2")
    valid_sessions = card.get("valid_sessions")
    validity = f"{int(valid_sessions)}个交易日" if valid_sessions else "条件变化即重估"
    item_market = str(identity.get("market") or "")
    target_display = f"{_price_num(target1, item_market)} / {_price_num(target2, item_market)}"
    rr_display = "1:" + _num(rr, 2) if rr is not None else "—"
    invalidation_values = card.get("invalidation_conditions")
    if execution.get("status") == "事件观察":
        invalidation_source = (
            invalidation_values[0]
            if isinstance(invalidation_values, Sequence) and not isinstance(invalidation_values, (str, bytes)) and invalidation_values
            else ""
        )
    else:
        invalidation_source = card.get("invalidation_condition") or (
            invalidation_values[0]
            if isinstance(invalidation_values, Sequence) and not isinstance(invalidation_values, (str, bytes)) and invalidation_values
            else ""
        )
    invalidation = _reader_price_text(invalidation_source, item_market, 115)
    condition = _reader_price_text(
        card.get("watch_condition") or card.get("trigger_condition") or execution.get("note"),
        item_market,
        190,
    )
    if condition == "当前不启用主动买入触发条件":
        condition = _reader_price_text(execution.get("note"), item_market, 190)
    checkpoint = _next_checkpoint(item, events)
    status_explanation = _status_explanation(str(execution.get("status")), events)
    trigger_display = _price_num(card.get("trigger_price") or card.get("reference_entry"), item_market)
    stop_display = _price_num(stop, item_market)
    if execution.get("status") == "事件观察":
        trigger_display = "事件后重估"
        stop_display = "事件后重设"
        target_display = "事件后重设"
        rr_display = "—"
        validity = "事件落地后重估"
    elif execution.get("status") in {"风险失效", "研究关注"} and not card.get("active"):
        trigger_display = "暂不设置"
    anchor = "stock-" + re.sub(r"[^a-zA-Z0-9_-]+", "-", str(identity["symbol"]))
    return f"""
    <section class="page stock-detail unified-stock-detail" id="{_e(anchor)}">
      <div class="page-title"><div><span class="eyebrow">{_e(market_label)}机会 #{opp['rank']}</span><h2>{_e(identity['name'])}<small>{_e(identity['symbol'])}</small></h2></div><div class="score-orb"><b>{_num(opp['score'],1)}</b><span>机会分</span></div></div>
      <div class="headline"><div>{_status(execution['status'])}<b>{_e((_plain(opp['primary_catalyst'], 230) if not _is_no_catalyst(opp.get('primary_catalyst')) else '') or _plain(stock.conclusion, 230))}</b></div><span>{_e(_plain(execution['note'], 170))}</span></div>
      <div class="state-guide"><div><small>这个状态表示</small><b>{_e(status_explanation)}</b></div><div class="next-checkpoint"><small>下一确认点</small><strong>{_e(checkpoint)}</strong></div></div>
      <div class="component-grid">{_component_grid(item)}</div>
      {_quality_panel(quality)}
      <div class="data-grid">{_detail_data_grid(stock, item)}</div>
      <div class="two-col">
        <div class="panel"><h3>价格结构与确认</h3>{_labeled_points(_technical_structure_points(item, stock), 205)}</div>
        <div class="panel earnings-panel"><h3>{'财报场景推演' if item.get('earnings_scenario', {}).get('status') == 'upcoming' else '公司与预期'}</h3>{_earnings_scenario_block(item, stock, report_day, registry)}</div>
      </div>
      <div class="event-box"><h3>关键公司事件</h3><ul>{_event_items(events, registry)}</ul></div>
      <div class="evidence-grid"><div class="{'catalyst-neutral' if not catalysts else 'catalyst'}"><h3>主要催化</h3><ul>{catalysts or '<li>当前无新增公司催化</li>'}</ul></div><div class="risk"><h3>主要风险</h3><ul>{risks or '<li>—</li>'}</ul></div></div>
      {f'<div class="social"><h3>社媒与预期</h3><ul>{socials}</ul></div>' if socials else ''}
      <div class="execution-box"><h3>条件与风险边界</h3><div class="level-grid"><div class="trigger-focus"><small>{_e(card.get('setup_label') or '结构确认')} · {'已突破 / 守稳观察位' if card.get('price_condition_met') else '下一触发 / 重估点'}</small><strong>{_e(trigger_display)}</strong><span>{_e(checkpoint)}</span></div><div class="invalidation-focus"><small>结构失效条件</small><b>{_e(stop_display)}</b><span>{_e(invalidation or '价格结构破坏或出现新重大事件')}</span></div><div><small>目标区间</small><b>{_e(target_display)}</b></div><div><small>参考盈亏比</small><b>{_e(rr_display)}</b></div><div><small>有效窗口</small><b>{_e(validity)}</b></div></div>
      <div class="scenario-plan"><div><small>空仓情景</small>{_e(_public_flat_scenario(item, events))}</div><div><small>持有情景</small>{_e(_public_holding_scenario(item, events))}</div></div></div>
    </section>"""


def _compact_card(
    item: Mapping[str, Any],
    stock: ResearchStock,
    events: Sequence[Mapping[str, Any]] = (),
    registry: _SourceRegistry | None = None,
) -> str:
    identity = item["identity"]
    opp = item["opportunity"]
    execution = item["execution"]
    rs = item["relative_strength"]
    volume = item["market_data"]["volume"]["confirmation"]
    checkpoint = _next_checkpoint(item, events)
    quote_cells = (
        ("开盘", stock.open_price),
        ("最高", stock.high_price),
        ("最低", stock.low_price),
        ("收盘", stock.current_price or _num(item["market_data"].get("current_price"))),
    )
    market_cells = (
        ("日涨跌", stock.change_pct or _num(item["market_data"].get("change_pct"), 2, "%")),
        ("振幅", stock.amplitude),
        ("成交量", stock.volume),
        ("成交额", stock.amount),
        ("换手率", stock.turnover_rate),
        ("相对成交量", _num(volume.get("value"), 2, "×")),
    )
    quote_html = "".join(
        f"<div><small>{_e(label)}</small><b>{_e(value or '—')}</b></div>" for label, value in quote_cells
    )
    market_html = "".join(
        f"<div><small>{_e(label)}</small><b>{_e(value or '—')}</b></div>" for label, value in market_cells
    )
    catalyst = next(
        (_plain(value, 125) for value in stock.catalysts if not _is_no_catalyst(value)),
        "当前无新增公司催化",
    )
    catalyst_marker = (
        ""
        if _is_no_catalyst(catalyst)
        else _stock_source_marker(stock, catalyst, registry, kind="catalyst")
    )
    risk = _plain(stock.risks[0] if stock.risks else stock.risk_control, 125) or "—"
    risk_marker = _stock_source_marker(stock, risk, registry, kind="risk")
    return f"""
    <article class="compact-card"><div class="compact-head"><div><b>{_e(identity['name'])}</b><small>#{opp['rank']} · {_e(identity['symbol'])}</small></div><div class="compact-score">{_num(opp['score'],1)}</div>{_status(execution['status'])}</div>
      <div class="compact-quote-grid">{quote_html}</div>
      <div class="compact-market-grid">{market_html}</div>
      <div class="compact-performance"><span><b>个股 5/20日</b>{_num(rs.get('stock',{}).get('return_5d_pct'),1,'%')} / {_num(rs.get('stock',{}).get('return_20d_pct'),1,'%')}</span><span><b>市场超额 5/20日</b>{_num(rs.get('market_excess_5d_pct'),1,'%')} / {_num(rs.get('market_excess_20d_pct'),1,'%')}</span><span><b>行业超额 5/20日</b>{_num(rs.get('industry_excess_5d_pct'),1,'%')} / {_num(rs.get('industry_excess_20d_pct'),1,'%')}</span></div>
      <p><b>机会：</b>{_e(_plain(opp.get('primary_catalyst'),170) or _plain(stock.conclusion,170) or '—')}</p>
      {_earnings_scenario_brief(item)}
      <p class="compact-trigger"><b>下一确认点：</b>{_e(checkpoint)}</p>
      <p><b>状态含义：</b>{_e(_status_explanation(str(execution.get('status')), events))}</p>
      <div class="compact-two"><div class="{'compact-neutral' if _is_no_catalyst(catalyst) else ''}"><b>催化</b>{_e(catalyst)}{catalyst_marker}</div><div><b>风险</b>{_e(risk)}{risk_marker}</div></div>
      <div class="compact-plan"><span><b>空仓：</b>{_e(_public_flat_scenario(item, events))}</span><span><b>持有：</b>{_e(_public_holding_scenario(item, events))}</span></div>
    </article>"""


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _source_appendix(registry: _SourceRegistry) -> str:
    if not registry.items:
        return ""
    pages = []
    groups = list(_chunks(registry.items, 15))
    for index, group in enumerate(groups, 1):
        rows = []
        for item in group:
            published = item.get("published_at") or "未标注"
            fetched = item.get("fetched_at") or registry.information_cutoff or "未标注"
            source = item.get("source") or "公开网页"
            rows.append(
                f"<li id='source-{_e(item['id'])}'><div><b>[S{_e(item['id'])}] {_e(item['label'])}</b>"
                f"<span>{_e(source)}｜发布日期 {_e(published)}｜采集/信息截止 {_e(fetched)}</span></div>"
                f"<a href='{_e(item['url'])}'>{_e(item['url'])}</a></li>"
            )
        pages.append(
            f"<section class='page source-page' id='evidence-index-{index}'><div class='page-title'><div>"
            f"<span class='eyebrow'>证据追溯</span><h2>证据索引与时间口径</h2></div>"
            f"<span class='count'>{index}/{len(groups)}</span></div>"
            f"<p class='source-cutoff'>报告信息截止：{_e(registry.information_cutoff or '以报告日收盘后采集记录为准')}。正文中的 [S#] 可跳转至对应公开资料。</p>"
            f"<ol class='source-list'>{''.join(rows)}</ol></section>"
        )
    return "".join(pages)


def _balanced_chunks(items: Sequence[Any], max_size: int) -> list[Sequence[Any]]:
    if not items:
        return []
    page_count = math.ceil(len(items) / max_size)
    base, remainder = divmod(len(items), page_count)
    result = []
    cursor = 0
    for index in range(page_count):
        size = base + (1 if index < remainder else 0)
        result.append(items[cursor : cursor + size])
        cursor += size
    return result


def _adaptive_chunks(
    items: Sequence[Any],
    weight: Callable[[Any], int],
    *,
    max_size: int = 4,
    preferred_min_size: int = 2,
    max_weight: int = 52,
) -> list[list[Any]]:
    """Pack reader cards by estimated rendered lines, not only item count.

    Rich cards normally use two or three slots per page. Short cards may still
    share a four-card page, and a one-card tail is merged when space permits.
    """
    groups: list[list[Any]] = []
    current: list[Any] = []
    current_weight = 0
    for item in items:
        item_weight = max(1, int(weight(item)))
        full_by_count = len(current) >= max_size
        full_by_content = len(current) >= preferred_min_size and current_weight + item_weight > max_weight
        if current and (full_by_count or full_by_content):
            groups.append(current)
            current = []
            current_weight = 0
        current.append(item)
        current_weight += item_weight
    if current:
        groups.append(current)

    if len(groups) >= 2 and len(groups[-1]) == 1:
        combined = groups[-2] + groups[-1]
        combined_weight = sum(max(1, int(weight(item))) for item in combined)
        if len(combined) <= max_size and combined_weight <= max_weight:
            groups[-2].extend(groups.pop())
        elif len(groups[-2]) >= preferred_min_size + 2:
            groups[-1].insert(0, groups[-2].pop())
    return groups


def _compact_card_line_estimate(
    item: Mapping[str, Any],
    stock: ResearchStock,
    events: Sequence[Mapping[str, Any]] = (),
) -> int:
    """Estimate the vertical lines used by one observation-pool card."""
    opportunity = item["opportunity"]
    execution = item["execution"]

    def lines(value: object, width: int, limit: int) -> int:
        text = _plain(value, limit)
        return max(1, math.ceil(len(text) / width)) if text else 1

    return (
        13
        + lines(opportunity.get("primary_catalyst") or stock.conclusion, 62, 170)
        + (2 if (item.get("earnings_scenario") or {}).get("status") == "upcoming" else 0)
        + lines(_next_checkpoint(item, events), 60, 125)
        + lines(_status_explanation(str(execution.get("status")), events), 62, 100)
        + max(
            lines(stock.catalysts[0] if stock.catalysts else stock.latest_news, 31, 125),
            lines(stock.risks[0] if stock.risks else stock.risk_control, 31, 125),
        )
        + max(
            lines(_public_flat_scenario(item, events), 31, 150),
            lines(_public_holding_scenario(item, events), 31, 150),
        )
    )


def _logical_page_count(document: str) -> int:
    return len(re.findall(r'<section\s+class=["\']page(?:\s|["\'])', document))


def _layout_geometry_issues(rendered_document: Any) -> list[str]:
    """Catch visually broken boxes that page-count and text audits cannot see."""
    issues: list[str] = []
    for page_number, page in enumerate(rendered_document.pages, 1):
        page_width = float(getattr(page._page_box, "width", 0) or 0)

        def walk(box: Any) -> None:
            element = getattr(box, "element", None)
            classes = set(str(element.get("class", "")).split()) if element is not None else set()
            box_type = type(box).__name__
            width = float(getattr(box, "width", 0) or 0)
            height = float(getattr(box, "height", 0) or 0)

            if box_type == "GridBox" and "component-grid" in classes:
                if width < page_width * 0.8 or height > 120:
                    issues.append(
                        f"第{page_number}页 component-grid 几何异常 "
                        f"({width:.1f}×{height:.1f})"
                    )
            elif box_type in {"BlockBox", "FlexBox"} and "component" in classes:
                if width < 40:
                    issues.append(f"第{page_number}页 component 宽度异常 ({width:.1f})")
            elif box_type == "FlexBox" and "technical-weight-note" in classes:
                if width < page_width * 0.75 or height > 80:
                    issues.append(
                        f"第{page_number}页 technical-weight-note 几何异常 "
                        f"({width:.1f}×{height:.1f})"
                    )
            elif box_type == "FlexBox" and "status-legend" in classes:
                wrapped = [
                    child
                    for child in getattr(box, "children", ())
                    if float(getattr(child, "height", 0) or 0) > 20
                ]
                if wrapped:
                    issues.append(f"第{page_number}页 status-legend 存在单字/短尾换行")
            elif box_type in {"BlockBox", "GridBox"} and "ai-crowding-panel" in classes:
                if width < page_width * 0.75:
                    issues.append(f"第{page_number}页 ai-crowding-panel 宽度异常 ({width:.1f})")
            elif box_type in {"BlockBox", "GridBox", "FlexBox"} and "earnings-deep-card" in classes:
                if height > 650:
                    issues.append(
                        f"第{page_number}页 earnings-deep-card 高度异常 ({height:.1f})"
                    )
                border_height = getattr(box, "border_height", None)
                card_outer_height = float(border_height() if callable(border_height) else height)
                card_bottom = float(getattr(box, "position_y", 0) or 0) + card_outer_height
                card_children = list(getattr(box, "children", ()))

                def content_bottom(child: Any) -> float:
                    bottom = (
                        float(getattr(child, "position_y", 0) or 0)
                        + float(getattr(child, "height", 0) or 0)
                    )
                    return max(
                        [bottom, *(content_bottom(grandchild) for grandchild in getattr(child, "children", ()))],
                    )

                for current, following in zip(card_children, card_children[1:]):
                    current_bottom = content_bottom(current)
                    following_top = float(getattr(following, "position_y", 0) or 0)
                    if current_bottom > following_top + 2:
                        issues.append(
                            f"第{page_number}页 earnings-deep-card 内部内容重叠 "
                            f"({current_bottom - following_top:.1f})"
                        )
                        break
                if card_children:
                    overflow = content_bottom(card_children[-1]) - card_bottom
                    if overflow > 2:
                        issues.append(
                            f"第{page_number}页 earnings-deep-card 内容越界 ({overflow:.1f})"
                        )
            elif box_type == "GridBox" and "hk-metric-grid" in classes:
                if width < page_width * 0.8 or height > 130:
                    issues.append(
                        f"第{page_number}页 hk-metric-grid 几何异常 "
                        f"({width:.1f}×{height:.1f})"
                    )
            elif box_type == "GridBox" and "hk-driver-grid" in classes:
                if width < page_width * 0.8 or height < 300 or height > 650:
                    issues.append(
                        f"第{page_number}页 hk-driver-grid 几何异常 "
                        f"({width:.1f}×{height:.1f})"
                    )
            elif box_type == "GridBox" and "hk-analysis-grid" in classes:
                if width < page_width * 0.8 or height < 190 or height > 430:
                    issues.append(
                        f"第{page_number}页 hk-analysis-grid 几何异常 "
                        f"({width:.1f}×{height:.1f})"
                    )
            elif box_type == "FlexBox" and "hk-variables-page" in classes:
                section_bottom = float(getattr(box, "position_y", 0) or 0) + height
                content_bottom = max(
                    (
                        float(getattr(child, "position_y", 0) or 0)
                        + float(getattr(child, "height", 0) or 0)
                        for child in getattr(box, "children", ())
                    ),
                    default=section_bottom,
                )
                if content_bottom > section_bottom + 2:
                    issues.append(
                        f"第{page_number}页 hk-variables-page 内容越界 "
                        f"({content_bottom - section_bottom:.1f})"
                    )
            elif box_type == "BlockBox" and "unified-stock-detail" in classes:
                section_bottom = float(getattr(box, "position_y", 0) or 0) + height
                content_bottom = max(
                    (
                        float(getattr(child, "position_y", 0) or 0)
                        + float(getattr(child, "height", 0) or 0)
                        for child in getattr(box, "children", ())
                    ),
                    default=section_bottom,
                )
                if section_bottom - content_bottom < 24:
                    issues.append(
                        f"第{page_number}页 unified-stock-detail 底部留白不足 "
                        f"({section_bottom - content_bottom:.1f})"
                    )

            for child in getattr(box, "children", ()):
                walk(child)

        walk(page._page_box)
    return list(dict.fromkeys(issues))


def _required_content_gaps(formal: DedupReport, research: MarketResearch) -> list[str]:
    """Return reader-facing sections that should never silently render blank."""
    checks = {
        "市场状态": formal.market_status,
        "主线方向": formal.main_theme,
        "核心策略": formal.strategy,
        "市场一句话结论": research.one_sentence,
        "指数结构": research.structure,
        "板块主线": research.sectors,
        "资金与情绪": research.flow_sentiment,
        "消息催化": research.catalysts,
    }
    return [label for label, value in checks.items() if not _meaningful(value)]


def _outline_titles(items: Sequence[Any]) -> list[str]:
    titles: list[str] = []
    for item in items:
        if isinstance(item, list):
            titles.extend(_outline_titles(item))
            continue
        title = getattr(item, "title", None) or (item.get("/Title") if isinstance(item, Mapping) else None)
        if title:
            titles.append(str(title))
    return titles


DESIGN_TOKENS_CSS = """
:root{--ink:#172033;--navy:#173d70;--blue:#2563eb;--teal:#0f766e;--muted:#64748b;--line:#dbe3ef;--panel:#f8fafc;--positive:#166534;--negative:#991b1b}
"""

BASE_CSS = """
@page{size:A4;margin:10mm 11mm 13mm;@bottom-right{content:"第 " counter(page) " 页";font-size:8px;color:#64748b}}
*{box-sizing:border-box}body{margin:0;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#172033;font-size:8.5px;line-height:1.45}.page{break-after:page;min-height:270mm;position:relative}.page:last-child{break-after:auto}.eyebrow{font-size:7.5px;font-weight:800;letter-spacing:1px;color:#0f766e}h1{font-size:27px;margin:3mm 0 2mm}h2{font-size:18px;color:#173d70;margin:1mm 0}h2 small{display:block;font-size:8px;color:#64748b;margin-top:1mm}h3{font-size:10px;margin:0 0 1.5mm;color:#173d70}small{display:block;color:#64748b;font-size:7px}p{margin:1.3mm 0}ul{margin:1mm 0;padding-left:5mm}li{margin:.6mm 0}.cover{background:linear-gradient(135deg,#101a34,#245899 58%,#0f766e);color:white;border-radius:14px;padding:9mm}.cover .eyebrow{color:#99f6e4}.cover p{color:#dbeafe}.cover-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:2mm;margin-top:6mm}.cover-metrics div{background:#ffffff1f;padding:3mm;border-radius:7px}.cover-metrics b{display:block;font-size:16px}.market-banner{display:grid;grid-template-columns:1fr 1fr 2fr;gap:2mm;margin:4mm 0}.market-banner>div{border:1px solid #dbe3ef;background:#f8fafc;border-radius:7px;padding:2.5mm}.market-banner b{font-size:10px}.strategy{background:#ecfdf5;border-left:4px solid #10b981;border-radius:6px;padding:3mm;margin-bottom:3mm}.page-title{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid #173d70;padding-bottom:2mm;margin-bottom:3mm}.count{font-size:11px;font-weight:800;color:#173d70}table{width:100%;border-collapse:collapse}thead{display:table-header-group}th{background:#173d70;color:white;text-align:left;padding:1.4mm;font-size:7.2px}td{border-bottom:1px solid #e2e8f0;padding:1.25mm;vertical-align:top;font-size:7.3px}td small{margin-top:.4mm}.rank{font-size:12px;font-weight:900;color:#173d70}.top5-table td{padding:1.6mm 1.2mm}.status{display:inline-block;border-radius:999px;padding:.5mm 1.6mm;font-size:7px;font-weight:800;white-space:nowrap}.status-ready{background:#dcfce7;color:#166534}.status-cautious{background:#fef3c7;color:#92400e}.status-wait{background:#dbeafe;color:#1d4ed8}.status-event{background:#fef3c7;color:#92400e}.status-research{background:#f1f5f9;color:#475569}.status-risk{background:#fee2e2;color:#991b1b}.market-lead{font-size:10.5px;background:#eef6ff;border-left:4px solid #2563eb;padding:3mm;border-radius:6px;margin-bottom:3mm}.market-grid{display:grid;grid-template-columns:1fr 1fr;gap:2.5mm;margin-top:3mm}.panel{border:1px solid #dbe3ef;background:#f8fafc;border-radius:7px;padding:3mm}.index-table td,.index-table th{font-size:8px}.macro-stack{display:grid;grid-template-rows:repeat(4,1fr);gap:3mm;height:250mm}.macro-card,.upcoming-card{border:1px solid #dbe3ef;border-left:4px solid #2563eb;border-radius:8px;padding:3mm;overflow:hidden}.macro-head{display:flex;justify-content:space-between;gap:2mm;color:#173d70}.macro-result{font-size:10px;font-weight:700}.impact{display:grid;grid-template-columns:1fr 1fr;gap:2mm;margin-top:2mm}.impact span{border-radius:5px;padding:1.5mm}.positive{background:#ecfdf5;color:#14532d}.negative{background:#fff1f2;color:#881337}.upcoming-stack{display:grid;grid-template-rows:repeat(5,1fr);gap:2.5mm;height:250mm}.scenario-grid{display:grid;grid-template-columns:1fr 1fr;gap:2mm;margin-top:2mm}.scenario{background:#f8fafc;border-radius:6px;padding:1.8mm}.scenario b,.scenario span,.scenario small{display:block}.scenario small{margin-top:1mm}.ranking-table td,.ranking-table th{font-size:6.8px;padding:1mm}.top-row{background:#f0fdfa}.headline{display:grid;grid-template-columns:1.15fr .85fr;gap:3mm;background:#eef6ff;border-radius:7px;padding:2.5mm 3mm;margin-bottom:2.5mm}.headline>div{display:flex;align-items:center;gap:2mm}.headline b{color:#173d70}.score-orb{text-align:center;border-radius:9px;background:#173d70;color:white;padding:2mm 4mm}.score-orb b{display:block;font-size:19px}.score-orb span{font-size:7px}.component-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1.5mm;margin-bottom:2.5mm}.component{background:#f8fafc;border-radius:5px;padding:1.3mm}.component>div{display:flex;justify-content:space-between}.component i{display:block;height:1.3mm;background:#e2e8f0;border-radius:999px;margin-top:.7mm}.component em{display:block;height:100%;background:#14b8a6;border-radius:999px}.data-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1mm;margin-bottom:2.5mm}.data-grid>div{border:1px solid #e2e8f0;border-radius:5px;padding:1.4mm}.data-grid b{font-size:8px}.two-col,.evidence-grid{display:grid;grid-template-columns:1fr 1fr;gap:2mm;margin-bottom:2mm}.event-box{border:1px solid #fde68a;background:#fffbeb;border-radius:7px;padding:2.2mm;margin-bottom:2mm}.event-box li{display:grid;grid-template-columns:35mm 1fr;gap:2mm}.catalyst,.risk{border-radius:7px;padding:2.3mm}.catalyst{background:#ecfdf5;color:#14532d}.risk{background:#fff1f2;color:#881337}.social{background:#eef2ff;border-radius:7px;padding:2mm;margin-bottom:2mm}.execution-box{border:1px solid #99f6e4;background:#f0fdfa;border-radius:7px;padding:2.3mm}.level-grid{display:grid;grid-template-columns:2fr repeat(4,1fr);gap:1mm}.level-grid>div{background:white;border-radius:5px;padding:1.3mm}.scenario-plan{display:grid;grid-template-columns:1fr 1fr;gap:1.5mm;margin-top:1.5mm}.scenario-plan>div{background:white;border-radius:5px;padding:1.4mm}.compact-stack{display:grid;grid-template-rows:repeat(3,1fr);gap:3mm;height:250mm}.compact-card{border:1px solid #dbe3ef;border-left:4px solid #64748b;border-radius:8px;padding:3mm;overflow:hidden}.compact-head{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:2mm}.compact-head b{font-size:12px;color:#173d70}.compact-score{font-size:16px;font-weight:900;color:#173d70}.compact-data{display:flex;gap:4mm;background:#f8fafc;padding:1.3mm;margin:1.5mm 0}.compact-two,.compact-plan{display:grid;grid-template-columns:1fr 1fr;gap:2mm}.compact-two>div{border-radius:5px;padding:1.4mm;background:#ecfdf5}.compact-two>div+div{background:#fff1f2}.compact-two b{display:block}.compact-plan{margin-top:1.5mm;border-top:1px solid #e2e8f0;padding-top:1.3mm}.no-content{padding:8mm;color:#64748b}
"""
LAYOUT_CSS = """
body{font-size:9.1px;line-height:1.5}
h3{font-size:11px}.top5-table td{font-size:7.8px;padding:1.8mm 1.25mm}.top5-table .checkpoint{min-width:34mm;color:#173d70;background:#eff6ff}
.status-legend{display:flex;flex-wrap:nowrap;justify-content:space-between;gap:1mm;margin-top:3mm;padding:2.4mm;background:#f8fafc;border-radius:7px}.status-legend>span{display:flex;flex:none;align-items:center;gap:1mm;font-size:7.3px;white-space:nowrap}
.macro-overview-grid{display:grid;grid-template-columns:1fr 1fr;gap:4mm}.macro-overview-grid>div>h3{font-size:12px;margin-bottom:2mm}.macro-overview-grid .macro-card{margin-bottom:2.5mm;padding:3.2mm}.calendar-card{border:1px solid #dbe3ef;border-left:4px solid #0f766e;border-radius:8px;padding:3mm;margin-bottom:2.5mm;background:#f8fafc}.calendar-card p{font-weight:700;color:#173d70}.calendar-card small{font-size:7.7px;color:#475569}
.upcoming-stack{display:grid;grid-template-rows:repeat(4,auto);gap:3mm;height:auto}.upcoming-card{padding:3.3mm}.baseline-note{padding:1.6mm 2mm;background:#fffbeb;border-radius:5px;color:#78350f;margin:1.5mm 0 2mm}.scenario-grid{grid-template-columns:repeat(3,1fr);gap:2mm}.scenario{padding:2.2mm}.scenario b{font-size:8.5px;color:#173d70}.scenario span{margin-top:1mm}.scenario strong{display:block;margin-top:1mm;font-size:7.6px}.scenario small{font-size:6.8px}
.state-guide{display:grid;grid-template-columns:1fr 1.15fr;gap:2mm;margin-bottom:2.5mm}.state-guide>div{border:1px solid #dbe3ef;border-radius:7px;padding:2.2mm;background:#f8fafc}.state-guide b{font-size:8px}.state-guide .next-checkpoint{background:#eff6ff;border:2px solid #60a5fa}.state-guide strong{display:block;font-size:11px;color:#173d70;line-height:1.35}
.level-grid{grid-template-columns:2.4fr repeat(4,1fr)}.trigger-focus{border:2px solid #2563eb!important;background:#eff6ff!important}.trigger-focus strong{display:block;font-size:15px;color:#173d70}.trigger-focus span{display:block;margin-top:.8mm;font-size:7px;color:#475569}
.compact-stack{grid-template-rows:repeat(4,1fr);gap:2.2mm;height:250mm}.compact-card{padding:2.5mm}.compact-card p{margin:.8mm 0}.compact-card .compact-trigger{font-size:9.2px;color:#173d70;background:#eff6ff;padding:1mm 1.5mm;border-radius:5px}.compact-data{margin:1mm 0}.compact-plan{margin-top:1mm;padding-top:1mm}
.cover{padding:12mm;min-height:58mm}.cover h1{font-size:31px}.cover-metrics div{padding:4mm}.market-banner>div{padding:3.2mm}.strategy{font-size:10px;padding:3.5mm}.top5-table td{padding:2.8mm 1.4mm}.status-legend{padding:3.2mm;margin-top:4mm}
.reader-bullets{margin:0;padding-left:5mm}.reader-bullets li{margin:1.1mm 0}.market-grid .panel{padding:3.5mm}.market-grid{gap:3mm}.index-table td,.index-table th{font-size:8.7px;padding:1.8mm}.market-lead{font-size:11px;padding:3.8mm}
.macro-overview-grid .macro-card,.calendar-card{padding:4mm}.calendar-card{margin-bottom:3.5mm}.macro-overview-grid .macro-head b{font-size:9px}.macro-overview-grid small{line-height:1.55}
.ranking-table td,.ranking-table th{font-size:7.6px;padding:1.65mm 1.1mm}.ranking-table .status{font-size:6.8px}.ranking-table td:last-child{font-weight:700;color:#173d70}
.ai-crowding-panel{grid-column:1 / span 2;background:linear-gradient(135deg,#eef2ff,#f8fafc);border:1px solid #c7d2fe}.ai-head{display:flex;align-items:center;gap:3mm}.ai-icon{width:11mm;height:11mm;border-radius:50%;background:#312e81;color:white;display:flex;align-items:center;justify-content:center;font-size:18px}.ai-head b{font-size:12px;color:#312e81}.ai-score-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:2mm;margin:2mm 0}.ai-score-grid>div{background:white;border-radius:7px;padding:2mm}.ai-score-grid strong{font-size:18px;color:#173d70}.icon-meter{display:flex;gap:.7mm;margin-top:1mm}.icon-meter i{display:block;width:6mm;height:2mm;border-radius:999px;background:#f97316}.icon-meter .off{background:#e2e8f0}.meter-risk i{background:#dc2626}.meter-breadth i{background:#0f766e}.ai-interpretation{font-weight:700;font-size:9.3px;color:#312e81}.ai-lists{display:grid;grid-template-columns:1fr 1fr;gap:3mm}.ai-lists ul{margin:0;padding-left:5mm}.ai-lists li{margin:.7mm 0}
.market-grid{gap:2mm}.market-grid .panel{padding:2.5mm}.market-grid .reader-bullets{font-size:8.1px}.market-grid .reader-bullets li{margin:.65mm 0}.ai-crowding-panel{padding:2.7mm}.ai-score-grid{margin:1.2mm 0}.ai-score-grid>div{padding:1.4mm}.ai-score-grid strong{font-size:15px}.ai-interpretation{margin:.8mm 0}.ai-signals{display:flex;gap:1mm;flex-wrap:wrap}.ai-signals span{background:white;border-radius:999px;padding:.7mm 1.4mm;font-size:7px}.ai-risks{display:grid;grid-template-columns:1fr 1fr;gap:2mm;margin:.8mm 0 0;padding-left:5mm;font-size:7.2px}
.ai-page .ai-crowding-panel{padding:5mm}.ai-page .ai-score-grid>div{padding:3mm}.ai-page .ai-score-grid strong{font-size:24px}.ai-page .icon-meter i{height:3mm}.ai-component-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:3mm;margin-top:4mm}.ai-component-card{display:grid;grid-template-columns:12mm 1fr;gap:3mm;align-items:center;border:1px solid #dbe3ef;border-radius:9px;padding:4mm;background:#f8fafc}.ai-component-card>span{width:11mm;height:11mm;border-radius:50%;background:#173d70;color:white;display:flex;align-items:center;justify-content:center;font-size:17px}.ai-component-card strong{display:block;font-size:20px;color:#173d70}.ai-component-card p{margin:.5mm 0;color:#475569}.ai-reading{margin-top:4mm;background:#fffbeb;border-radius:9px;padding:4mm}.ai-reading>div{display:grid;grid-template-columns:35mm 1fr;gap:3mm;margin:2mm 0}.ai-reading b{color:#78350f}.ai-reading span{color:#475569}
.cover{min-height:72mm}.cover-metrics{margin-top:8mm}.top5-table td{padding:3.2mm 1.4mm}.market-macro-strip{display:grid;grid-template-columns:.8fr 1.2fr;gap:3mm;margin-top:3mm;border-top:2px solid #dbe3ef;padding-top:3mm}.market-macro-strip>div{background:#f8fafc;border-radius:7px;padding:2.5mm}.market-macro-strip ul{margin:0;padding-left:4mm}.market-macro-strip li{margin:.7mm 0}.market-macro-strip b,.market-macro-strip span{display:block}.market-macro-strip b{color:#173d70;font-size:7.4px}.market-macro-strip span{font-size:6.9px;color:#475569}.ranking-table td,.ranking-table th{padding:2mm 1.1mm}.cards-3{grid-template-rows:repeat(3,1fr);gap:3mm}.cards-3 .compact-card{padding:3.5mm}.cards-3 .compact-head b{font-size:13px}.cards-3 .compact-card p{margin:1.2mm 0}.cards-3 .compact-two>div{padding:2mm}.cards-3 .compact-plan{font-size:8.2px}
.cover{min-height:82mm}.top5-table td{padding:4mm 1.5mm}.status-legend{padding:4mm}.market-page .market-grid .panel{padding:3.2mm}.market-page .reader-bullets{font-size:8.8px}.market-page .reader-bullets li{margin:.9mm 0}.market-macro-strip{margin-top:4mm;padding-top:4mm}.market-macro-strip>div{padding:3.5mm}.market-macro-strip li{margin:1mm 0}.market-macro-strip b{font-size:7.8px}.market-macro-strip span{font-size:7.3px}.ai-page .ai-crowding-panel{padding:6mm}.ai-page .ai-component-card{padding:6mm}.ai-page .ai-reading{padding:6mm}.ai-page .ai-reading>div{margin:3mm 0}.upcoming-card{padding:5mm}.baseline-note{padding:2.2mm 2.5mm}.scenario{padding:3.2mm}.scenario strong{font-size:8px}.ranking-table td,.ranking-table th{padding:2.4mm 1.2mm}
"""
DASHBOARD_CSS = """
.market-copy{margin:0;color:#334155;line-height:1.55}.market-intro{font-weight:700;color:#173d70;margin-bottom:1.2mm}
.digest-list{display:grid;gap:.9mm}.digest-row{display:block;color:#334155;line-height:1.45}.digest-row b{display:inline-block;color:#0f5a8a;background:#eaf4fb;border-radius:4px;padding:.45mm 1mm;margin-right:1mm}.digest-row span{color:#334155}
.market-page .market-grid{margin-top:2.5mm}.market-page .market-grid .panel{padding:2.7mm}.market-page .market-grid h3{margin-bottom:1mm}.market-page .market-lead{margin-bottom:2.5mm}.market-page .index-table td,.market-page .index-table th{padding:1.5mm}
.ai-crowding-panel{padding:3mm!important;margin-top:2.5mm;background:linear-gradient(135deg,#f0fdf4 0%,#fffbeb 52%,#fff1f2 100%);border:1px solid #cbd5e1}
.ai-dashboard-title{display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #dbe3ef;padding-bottom:1.2mm}.ai-dashboard-title h3{margin:0;font-size:13px}.ai-dashboard-title b{font-size:7.5px;color:#475569}
.ai-dashboard-body{display:grid;grid-template-columns:46mm 1fr;gap:3mm;align-items:center}.crowding-gauge{position:relative;height:34mm}.crowding-gauge svg{display:block;width:45mm;height:30mm}.gauge-score{position:absolute;left:0;right:0;bottom:0;text-align:center}.gauge-score strong{font-size:19px;color:#173d70;margin-right:1.5mm}.gauge-score span{font-weight:800;color:#7c2d12}
.ai-dashboard-copy>p{font-size:8.5px;font-weight:700;color:#312e81;margin:0 0 1.5mm}.pointer-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.6mm 3mm}.pointer-metric>div:first-child{display:flex;justify-content:space-between;align-items:center}.pointer-metric b{font-size:7.2px;color:#334155}.pointer-metric strong{font-size:8px;color:#173d70}.pointer-track{height:2.6mm;border-radius:999px;position:relative;margin-top:.6mm;background:linear-gradient(90deg,#16a34a 0%,#facc15 52%,#f97316 72%,#dc2626 100%)}.pointer-track.pointer-healthy{background:linear-gradient(90deg,#dc2626 0%,#f97316 28%,#facc15 52%,#16a34a 100%)}.pointer-track i{position:absolute;top:-1.1mm;width:0;height:0;border-left:1.7mm solid transparent;border-right:1.7mm solid transparent;border-top:2.4mm solid #172033;transform:translateX(-50%);filter:drop-shadow(0 .3mm .2mm #fff)}
.ai-risk-tags{display:flex;gap:1mm;flex-wrap:wrap;margin-top:1.4mm}.ai-risk-tags span{font-size:6.5px;background:#ffffffb8;border:1px solid #e2e8f0;border-radius:999px;padding:.5mm 1.2mm;color:#475569}
"""
READABILITY_CSS = """
/* Readability pass: use available whitespace instead of shrinking reader-facing copy. */
.cover{min-height:72mm}.top5-list{gap:1.2mm}.top5-card{padding:1.3mm 1.8mm}.status-legend{padding:3mm;margin-top:3mm}
.technical-weight-note{grid-column:1 / span 6;display:flex;align-items:center;gap:2mm;background:#eef6ff;border-radius:5px;padding:1mm 1.5mm;color:#334155}.technical-weight-note b{color:#173d70;white-space:nowrap}.technical-weight-note span{font-size:8px}
body{font-size:9.6px;line-height:1.52}small{font-size:8.2px;line-height:1.4}.eyebrow{font-size:8.2px}h2 small{font-size:8.8px}
th{font-size:8px}td{font-size:8.2px}.top5-table td{font-size:8.6px}.status{font-size:7.8px}.status-legend>span{font-size:8.1px}.score-orb span{font-size:8px}
.market-copy,.digest-row{font-size:9.2px}.market-macro-strip b{font-size:8.5px}.market-macro-strip span{font-size:8.2px}.calendar-card small{font-size:8.4px}
.market-prose p{margin:0 0 1.2mm}.market-prose p:last-child{margin-bottom:0}
.hk-market-page .market-grid{grid-template-columns:1fr 1fr;gap:3.2mm;margin-top:3.2mm;grid-auto-rows:auto;align-content:start}.hk-market-page .market-grid .panel{padding:4mm}.hk-market-page .market-grid h3{font-size:12.5px;margin-bottom:2mm}.hk-market-page .market-copy,.hk-market-page .reader-bullets{font-size:11.2px;line-height:1.68}.hk-market-page .market-prose p{margin:0 0 1.8mm}.hk-market-page .market-prose p:last-child{margin-bottom:0}.hk-market-page .reader-bullets{padding-left:5.5mm}.hk-market-page .reader-bullets li{margin:0 0 1.5mm}.hk-market-page .index-table td,.hk-market-page .index-table th{font-size:9.8px;padding:1.9mm}.hk-market-page .market-lead{font-size:12px;line-height:1.55;padding:4mm}
.scenario strong{font-size:8.6px}.scenario small{font-size:8px}.ranking-table td,.ranking-table th{font-size:8px}.ranking-table .status{font-size:7.5px}
.headline>span{font-size:9px;line-height:1.45}.state-guide b{font-size:9.2px}.trigger-focus span{font-size:8.4px}.data-grid b{font-size:8.7px}.compact-plan{font-size:8.8px}
.ai-dashboard-title small{font-size:8.5px}.ai-dashboard-title b{font-size:8.5px}.ai-dashboard-copy>p{font-size:9.6px;line-height:1.45}.pointer-metric b{font-size:8.5px}.pointer-metric strong{font-size:9.2px}.gauge-score span{font-size:9.5px}.ai-risk-tags span{font-size:8.3px;line-height:1.35;padding:.7mm 1.5mm}
.top5-list{display:grid;gap:1.5mm}.top5-card{display:grid;grid-template-columns:8mm 29mm 23mm 52mm 17mm 1fr 1fr;gap:1.5mm;align-items:center;border:1px solid #dbe3ef;border-left:4px solid #2563eb;border-radius:7px;padding:1.6mm 2mm;background:#fff;break-inside:avoid-page}.top5-rank{font-size:16px;font-weight:900;color:#173d70;text-align:center}.top5-identity b{display:block;font-size:10.2px;color:#173d70}.top5-state{text-align:center}.top5-decision{background:#eff6ff;border-radius:5px;padding:1.2mm 1.5mm}.top5-decision small{font-size:8.1px}.top5-decision strong{display:block;font-size:9.8px;line-height:1.32;color:#173d70}.top5-score{text-align:center}.top5-score b{display:block;font-size:15px;color:#173d70}.top5-score span{font-size:8px;color:#64748b}.top5-context{align-self:stretch;border-radius:4px;padding:1mm 1.2mm;font-size:8.1px;line-height:1.35;color:#475569}.top5-context b{display:block;margin-bottom:.4mm}.top5-catalyst{background:#ecfdf5}.top5-catalyst b{color:#166534}.top5-risk{background:#fff1f2}.top5-risk b{color:#991b1b}.top5-neutral,.catalyst-neutral,.compact-neutral{background:#f1f5f9!important;color:#475569!important}.top5-neutral b,.catalyst-neutral h3,.compact-neutral b{color:#475569!important}.catalyst-neutral{border-radius:7px;padding:2.3mm}.source-marker{display:inline-block;margin-left:.8mm;color:#2563eb;font-size:7px;font-weight:800;text-decoration:none;white-space:nowrap}.probability-meta{margin-top:.8mm!important;padding-top:.6mm;border-top:1px dashed #cbd5e1;color:#64748b!important}
.ranking-table{table-layout:fixed}.ranking-table th:nth-child(1){width:5%}.ranking-table th:nth-child(2){width:16%}.ranking-table th:nth-child(3){width:9%}.ranking-table th:nth-child(4){width:13%}.ranking-table th:nth-child(5){width:13%}.ranking-table th:nth-child(6){width:12%}.ranking-table th:nth-child(7){width:32%}.ranking-table td,.ranking-table th{font-size:8.7px;padding:2.5mm 1.4mm}.ranking-score b{font-size:11px;color:#173d70}.ranking-signal{display:inline-block;border-radius:4px;background:#eef6ff;color:#173d70;font-weight:800;padding:.7mm 1.2mm}.ranking-condition{font-weight:700;color:#173d70;line-height:1.4}
.compact-stack{height:auto;min-height:250mm;grid-template-rows:none;grid-auto-rows:minmax(min-content,1fr)}.compact-stack.cards-2,.compact-stack.cards-3,.compact-stack.cards-4{grid-template-rows:none}.compact-card{overflow:visible;break-inside:avoid-page}.macro-card,.upcoming-card{overflow:visible;break-inside:avoid-page}
.stock-detail .page-title{margin-bottom:1.5mm}.stock-detail .headline{padding:1.8mm 2.5mm;margin-bottom:1mm}.stock-detail .state-guide{margin-bottom:1mm}.stock-detail .state-guide>div{padding:1.4mm 2mm}.stock-detail .component-grid{grid-template-columns:repeat(6,1fr);margin-bottom:1mm}.stock-detail .component{padding:.9mm 1.2mm}.stock-detail .quality-panel{margin-bottom:1mm}.stock-detail .quality-panel>div{padding:1.1mm 1.5mm}.stock-detail .data-grid{margin-bottom:1mm}.stock-detail .data-grid>div{padding:1mm 1.3mm}.stock-detail .two-col,.stock-detail .evidence-grid{gap:1.3mm;margin-bottom:1mm}.stock-detail .panel{padding:1.6mm 2mm}.stock-detail .reader-bullets li{margin:.35mm 0}.stock-detail .event-box{padding:1.4mm 2mm;margin-bottom:1mm}.stock-detail .catalyst,.stock-detail .risk{padding:1.6mm 2mm}.stock-detail .social{padding:1.3mm 2mm;margin-bottom:1mm}.stock-detail .execution-box{padding:1.6mm 2mm}.stock-detail .scenario-plan{margin-top:.8mm}.stock-detail .scenario-plan>div{padding:1mm 1.3mm}
.unified-stock-detail .data-grid{grid-template-columns:repeat(4,1fr)}
.page-title h2{bookmark-level:1}.stock-detail .page-title h2{bookmark-level:2}h3{bookmark-level:none}
.ai-trend-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:1.2mm;margin:1.2mm 0 1.5mm}.ai-trend-strip>div{background:#ffffffc9;border:1px solid #e2e8f0;border-radius:5px;padding:1mm 1.3mm}.ai-trend-strip b{font-size:11px;color:var(--navy)}.ai-trend-strip span{display:block;font-size:7.5px;color:var(--muted)}
.upcoming-card{padding:3.2mm}.macro-facts{display:grid;grid-template-columns:1.25fr .75fr;gap:1.5mm;margin-top:.8mm}.macro-facts>div{background:#eef6ff;border-radius:5px;padding:1mm 1.4mm}.macro-facts b{font-size:8.3px;color:var(--navy)}.upcoming-card .baseline-note{padding:1.4mm 1.8mm;margin:1mm 0}.upcoming-card .scenario{padding:2.5mm}.upcoming-card .scenario strong{display:block;margin-top:.7mm}.macro-head span{font-weight:800;color:#475569}
.quality-panel{display:grid;grid-template-columns:42mm 42mm 1fr;gap:1.5mm;margin-bottom:1.5mm}.quality-panel>div{border:1px solid var(--line);border-radius:6px;padding:1.6mm 2mm;background:var(--panel)}.quality-score{display:block}.quality-head{display:flex;align-items:center;justify-content:space-between;gap:2mm}.quality-head small{white-space:nowrap}.quality-head b{display:flex;align-items:baseline;gap:1.2mm;font-size:15px;color:var(--navy)}.quality-head b em{font-size:7.8px;font-style:normal;font-weight:800;border-radius:999px;padding:.35mm 1mm}.quality-track{display:block;width:100%;height:2mm;margin-top:1.2mm;background:#e2e8f0;border-radius:999px;overflow:hidden}.quality-track span{display:block;height:100%;border-radius:999px}.quality-high .quality-track span,.quality-high .quality-head b em{background:#16a34a}.quality-medium .quality-track span,.quality-medium .quality-head b em{background:#f59e0b}.quality-low .quality-track span,.quality-low .quality-head b em{background:#dc2626}.quality-unknown .quality-track span,.quality-unknown .quality-head b em{background:#94a3b8}.quality-head b em{color:white}.quality-gaps>div{display:flex;flex-wrap:wrap;gap:.8mm;margin-top:.8mm}.quality-gaps span{font-size:7.8px;background:#fff1f2;color:#991b1b;border-radius:999px;padding:.55mm 1.2mm}.quality-gaps .quality-complete{background:#ecfdf5;color:#166534}.released-facts{background:#eef6ff;border-radius:5px;padding:1.2mm 1.6mm;margin:1.2mm 0}.released-facts b{display:block;color:var(--navy);font-size:8.4px;line-height:1.45}.macro-recent-page .macro-stack{height:250mm}.macro-recent-page .macro-card{padding:4mm}.macro-recent-page .macro-result{margin:1.4mm 0}.level-grid{grid-template-columns:2.15fr 1.45fr repeat(3,1fr)}.invalidation-focus{border:1px solid #fca5a5;background:#fff1f2!important}.invalidation-focus b{display:block;color:#991b1b}.invalidation-focus span{display:block;margin-top:.6mm;font-size:7.7px;line-height:1.35;color:#7f1d1d}
.macro-recent-page .macro-stack{height:auto;min-height:0;grid-template-rows:none;grid-auto-rows:auto;align-content:start}.macro-recent-page .macro-stack.cards-1{max-width:150mm}.macro-recent-page .macro-card{min-height:48mm}
.macro-combined-page .macro-section-title{display:flex;align-items:baseline;justify-content:space-between;margin:0 0 1.2mm}.macro-combined-page .macro-section-title h3{font-size:11.5px;margin:0}.macro-combined-page .macro-section-title small{font-size:9px}.macro-combined-page .macro-stack{display:grid;grid-template-rows:none;grid-auto-rows:auto;grid-template-columns:repeat(2,1fr);height:auto;gap:2.2mm;margin-bottom:2mm}.macro-combined-page .macro-stack.cards-1{grid-template-columns:1fr;max-width:none}.macro-combined-page .macro-card{min-height:0;padding:2.5mm;font-size:9.8px}.macro-combined-page .macro-card p{margin:.65mm 0}.macro-combined-page .macro-head{font-size:9.2px}.macro-combined-page .macro-head span{font-size:8.4px}.macro-combined-page .released-facts{padding:1mm 1.3mm;margin:.8mm 0}.macro-combined-page .released-facts b{font-size:9px;line-height:1.32}.macro-combined-page .macro-result{font-size:9.8px;margin:.65mm 0}.macro-combined-page .impact{gap:1.2mm;margin-top:.8mm}.macro-combined-page .impact span{padding:1mm;font-size:8.7px}.macro-combined-page .upcoming-stack{height:auto;min-height:0;grid-template-rows:none;grid-auto-rows:auto;gap:1.5mm}.macro-combined-page .upcoming-card{padding:2.1mm;font-size:9px}.macro-combined-page .upcoming-card .macro-head{font-size:9px}.macro-combined-page .macro-facts{margin-top:.6mm;gap:1mm}.macro-combined-page .macro-facts>div{padding:.8mm 1.2mm}.macro-combined-page .macro-facts b{font-size:8.8px;line-height:1.28}.macro-combined-page .upcoming-card .baseline-note{padding:.8mm 1.2mm;margin:.6mm 0;font-size:8.6px}.macro-combined-page .scenario-grid{grid-template-columns:repeat(3,1fr);gap:1mm;margin-top:.6mm}.macro-combined-page .upcoming-card .scenario{padding:1mm}.macro-combined-page .scenario b{font-size:8.3px}.macro-combined-page .scenario strong{font-size:8.2px;line-height:1.28;margin-top:.3mm}.macro-combined-page .scenario small{font-size:7.8px;line-height:1.25;margin-top:.4mm}
.compact-quote-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1mm;margin:1.5mm 0 1mm}.compact-quote-grid>div,.compact-market-grid>div{border:1px solid #dbe3ef;border-radius:5px;background:#f8fafc;padding:1.2mm 1.5mm}.compact-quote-grid b,.compact-market-grid b{display:block;font-size:8.8px;color:var(--navy)}.compact-market-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:1mm;margin-bottom:1mm}.compact-performance{display:grid;grid-template-columns:repeat(3,1fr);gap:1mm;margin-bottom:1.4mm}.compact-performance span{background:#eef6ff;border-radius:5px;padding:1mm 1.4mm;color:#334155}.compact-performance b{display:block;color:var(--navy);margin-bottom:.3mm}.cards-2 .compact-card,.cards-3 .compact-card{padding:3.2mm}.cards-2 .compact-head b,.cards-3 .compact-head b{font-size:13px}.cards-2 .compact-card p,.cards-3 .compact-card p{margin:1mm 0}.level-grid{grid-template-columns:2.15fr 1.45fr repeat(3,1fr)}.invalidation-focus{border:1px solid #fca5a5;background:#fff1f2!important}.invalidation-focus b{display:block;color:#991b1b}.invalidation-focus span{display:block;margin-top:.6mm;font-size:7.7px;line-height:1.35;color:#7f1d1d}
.earnings-summary{display:flex;flex-wrap:wrap;gap:.7mm 1.4mm;margin-bottom:1mm}.earnings-summary span{font-size:7.4px;color:#475569}.earnings-summary span:first-child{font-weight:800;color:#173d70}.earnings-cases{display:grid;gap:.6mm}.earnings-case{display:grid;grid-template-columns:13mm 1fr;gap:1mm;border-radius:4px;background:#fff;padding:.7mm 1mm}.earnings-case b{color:#173d70}.earnings-case:first-child{background:#ecfdf5}.earnings-case:last-child{background:#fff1f2}.earnings-brief{border-left:3px solid #8b5cf6;background:#f5f3ff;border-radius:4px;padding:.8mm 1.3mm;color:#4c1d95}
.validation-metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:1.5mm;margin-bottom:2mm}.validation-metrics>div{border:1px solid #dbe3ef;background:#f8fafc;border-radius:7px;padding:2.2mm}.validation-metrics small{font-size:8px}.validation-metrics b{display:block;font-size:15px;color:#173d70;margin-top:.5mm}.validation-latest{margin-bottom:2.2mm}.validation-latest th,.validation-latest td{font-size:8.5px;padding:1.6mm}.validation-grid{display:grid;grid-template-columns:1fr 1fr;gap:2mm}.validation-panel{border:1px solid #dbe3ef;border-radius:7px;padding:2.5mm;background:#f8fafc}.validation-panel h3{margin-bottom:1mm}.validation-panel table th,.validation-panel table td{font-size:8px;padding:1.2mm}.validation-note{border-left:4px solid #7c3aed;background:#f5f3ff;border-radius:6px;padding:2mm 2.5mm;margin-top:2mm;color:#4c1d95}.validation-run{display:grid;grid-template-columns:repeat(5,1fr);gap:1mm;margin-top:1.5mm}.validation-run span{background:white;border-radius:5px;padding:1.3mm;font-size:8px}.validation-run b{display:block;color:#173d70;font-size:10px}.validation-muted{color:#94a3b8}
"""

EARNINGS_CSS = """
.earnings-prob-note{margin:-1.2mm 0 2mm;padding:1.3mm 2mm;border-radius:5px;background:#f5f3ff;color:#5b21b6;font-size:8px}.earnings-deep-stack{display:grid;gap:2.2mm;height:auto;grid-auto-rows:auto;align-content:start}.earnings-deep-stack.cards-3,.earnings-deep-stack.cards-2,.earnings-deep-stack.cards-1{grid-template-rows:none}.earnings-deep-card{border:1px solid #cbd5e1;border-left:4px solid #7c3aed;border-radius:8px;background:#fff;padding:2.3mm;overflow:hidden;break-inside:avoid-page}.earnings-deep-head{display:grid;grid-template-columns:minmax(0,1fr) minmax(43mm,auto);align-items:start;gap:3mm;border-bottom:1px solid #e2e8f0;padding-bottom:1mm;margin-bottom:1mm}.earnings-identity,.earnings-event{min-width:0}.earnings-identity b{display:block;font-size:12px;color:#173d70;line-height:1.25;overflow-wrap:anywhere}.earnings-deep-head span{display:block;font-size:7.3px;color:#64748b;line-height:1.35;overflow-wrap:anywhere}.earnings-event{max-width:74mm;text-align:right}.earnings-deep-head strong{font-size:10px;color:#7c3aed}.earnings-fact-grid{display:grid;grid-template-columns:1fr 1fr .72fr;gap:1.2mm}.earnings-fact-grid>section{background:#f8fafc;border-radius:5px;padding:1.2mm}.earnings-fact-grid h3{font-size:8.3px;margin-bottom:.6mm}.earnings-fact-grid p{font-size:6.8px;line-height:1.35;color:#475569;margin:.5mm 0 0}.earnings-mini-grid{display:grid;grid-template-columns:1fr 1fr;gap:.5mm}.earnings-mini-grid>div{background:white;border-radius:4px;padding:.55mm .8mm}.earnings-mini-grid small{font-size:6.2px}.earnings-mini-grid b{display:block;font-size:7px;color:#173d70}.earnings-mini-grid span{font-size:6.1px;color:#64748b}.earnings-history-total{display:block;font-size:7.3px;color:#173d70}.earnings-history-list{display:grid;grid-template-columns:1fr 1fr;gap:.45mm;margin-top:.6mm}.earnings-history-list span{display:block;background:white;border-radius:3px;padding:.5mm .7mm;font-size:6.1px;color:#475569}.earnings-history-list b{display:block;color:#173d70}.earnings-deep-cases{display:grid;grid-template-columns:repeat(3,1fr);gap:1mm;margin-top:1mm}.earnings-deep-case{border-radius:5px;background:#f8fafc;padding:1mm}.earnings-deep-case:first-child{background:#ecfdf5}.earnings-deep-case:last-child{background:#fff1f2}.earnings-deep-case>div{display:flex;align-items:center;justify-content:space-between}.earnings-deep-case b{color:#173d70}.earnings-deep-case strong{font-size:12px;color:#7c3aed}.earnings-deep-case p{font-size:6.7px;line-height:1.3;margin:.4mm 0}.earnings-deep-case span{display:block;font-size:6.5px;font-weight:700;color:#334155}.earnings-deep-case small{font-size:6.1px;margin-top:.35mm}.earnings-deep-foot{display:grid;grid-template-columns:1fr 1fr;gap:1.2mm;margin-top:1mm}.earnings-deep-foot>div{background:#eef6ff;border-radius:5px;padding:.8mm 1.1mm}.earnings-deep-foot b{font-size:7px;color:#173d70}.earnings-deep-foot ul{display:flex;flex-wrap:wrap;gap:.4mm 2.5mm;margin:.3mm 0 0;padding-left:3.5mm}.earnings-deep-foot li{font-size:6.3px;margin:0}.earnings-deep-foot p{font-size:6.4px;line-height:1.3;margin:.3mm 0}.earnings-deep-foot small{font-size:5.8px}.earnings-deep-foot a{color:#2563eb;text-decoration:none}.earnings-deep-stack.cards-1 .earnings-deep-card{padding:5mm}.earnings-deep-stack.cards-1 .earnings-identity b{font-size:18px}.earnings-deep-stack.cards-1 .earnings-fact-grid>section{padding:3mm}.earnings-deep-stack.cards-1 .earnings-mini-grid>div{padding:1.5mm}.earnings-deep-stack.cards-1 .earnings-mini-grid b{font-size:9px}.earnings-deep-stack.cards-1 .earnings-deep-case{padding:3mm}.earnings-deep-stack.cards-1 .earnings-deep-case p,.earnings-deep-stack.cards-1 .earnings-deep-case span{font-size:8px}.earnings-deep-stack.cards-1 .earnings-deep-foot>div{padding:2.5mm}
.earnings-deep-stack.cards-2{height:auto;grid-template-rows:none;align-content:start;gap:3mm}.earnings-deep-stack.cards-2 .earnings-deep-card{display:block;height:auto;padding:3mm}.earnings-deep-stack.cards-2 .earnings-deep-head>div:first-child b{font-size:14px}.earnings-deep-stack.cards-2 .earnings-deep-head span{font-size:8.4px}.earnings-deep-stack.cards-2 .earnings-deep-head strong{font-size:12px}.earnings-deep-stack.cards-2 .earnings-fact-grid{gap:1.8mm}.earnings-deep-stack.cards-2 .earnings-fact-grid>section{padding:2mm}.earnings-deep-stack.cards-2 .earnings-fact-grid h3{font-size:9.5px}.earnings-deep-stack.cards-2 .earnings-fact-grid p{font-size:8.2px}.earnings-deep-stack.cards-2 .earnings-mini-grid{gap:.8mm}.earnings-deep-stack.cards-2 .earnings-mini-grid>div{padding:1mm 1.2mm}.earnings-deep-stack.cards-2 .earnings-mini-grid small{font-size:7.4px}.earnings-deep-stack.cards-2 .earnings-mini-grid b{font-size:8.5px}.earnings-deep-stack.cards-2 .earnings-mini-grid span{font-size:7.4px}.earnings-deep-stack.cards-2 .earnings-history-total{font-size:8.5px}.earnings-deep-stack.cards-2 .earnings-history-list span{padding:.8mm 1mm;font-size:7.4px}.earnings-deep-stack.cards-2 .earnings-deep-cases{gap:1.5mm}.earnings-deep-stack.cards-2 .earnings-deep-case{padding:1.6mm}.earnings-deep-stack.cards-2 .earnings-deep-case b{font-size:8.5px}.earnings-deep-stack.cards-2 .earnings-deep-case strong{font-size:13px}.earnings-deep-stack.cards-2 .earnings-deep-case p{font-size:8.2px}.earnings-deep-stack.cards-2 .earnings-deep-case span{font-size:7.8px}.earnings-deep-stack.cards-2 .earnings-deep-case small{font-size:7.5px}.earnings-deep-stack.cards-2 .earnings-deep-foot{gap:1.8mm}.earnings-deep-stack.cards-2 .earnings-deep-foot>div{padding:1.5mm 1.8mm}.earnings-deep-stack.cards-2 .earnings-deep-foot b{font-size:8.5px}.earnings-deep-stack.cards-2 .earnings-deep-foot li,.earnings-deep-stack.cards-2 .earnings-deep-foot p{font-size:7.8px}.earnings-deep-stack.cards-2 .earnings-deep-foot small{font-size:7.2px}
"""

SOURCE_CSS = """
.source-page{font-size:8.4px}.source-cutoff{background:#eef6ff;border-left:4px solid #2563eb;border-radius:6px;padding:2.5mm 3mm;color:#334155}.source-list{padding-left:7mm;margin-top:3mm}.source-list li{padding:2.2mm 0;border-bottom:1px solid #e2e8f0;break-inside:avoid-page}.source-list li>div{display:flex;align-items:baseline;justify-content:space-between;gap:3mm}.source-list b{color:#173d70;font-size:8.8px}.source-list span{color:#64748b;font-size:7.2px;white-space:nowrap}.source-list a{display:block;margin-top:.7mm;color:#2563eb;font-size:6.8px;overflow-wrap:anywhere;text-decoration:none}
"""

CSS = "\n".join((DESIGN_TOKENS_CSS, BASE_CSS, LAYOUT_CSS, DASHBOARD_CSS, READABILITY_CSS, EARNINGS_CSS, SOURCE_CSS, INTEGRATED_PAGE_CSS))


def _hk_pool_coverage(ranked: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    coverage: dict[str, Any] = {
        "total": len(ranked),
        "advancers": 0,
        "decliners": 0,
        "strong_advancers": 0,
        "volume_confirmed": 0,
        "volume_weak": 0,
        "relative_strength_positive": 0,
    }
    changes: list[float] = []
    volume_ratios: list[float] = []
    movers: list[tuple[float, str]] = []
    for item in ranked:
        try:
            change = float(item["market_data"]["change_pct"])
        except (KeyError, TypeError, ValueError):
            change = 0.0
        changes.append(change)
        movers.append((change, str(item.get("identity", {}).get("name") or "—")))
        if change > 0:
            coverage["advancers"] += 1
        elif change < 0:
            coverage["decliners"] += 1
        if change >= 2:
            coverage["strong_advancers"] += 1
        try:
            volume_ratio = float(item["market_data"]["volume"]["confirmation"]["value"])
        except (KeyError, TypeError, ValueError):
            volume_ratio = None
        if volume_ratio is not None:
            volume_ratios.append(volume_ratio)
            if volume_ratio >= 1.05:
                coverage["volume_confirmed"] += 1
            if volume_ratio < 0.75:
                coverage["volume_weak"] += 1
        try:
            if float(item["relative_strength"]["market_excess_20d_pct"]) > 0:
                coverage["relative_strength_positive"] += 1
        except (KeyError, TypeError, ValueError):
            pass
    coverage["median_change_pct"] = statistics.median(changes) if changes else None
    coverage["median_volume_ratio"] = statistics.median(volume_ratios) if volume_ratios else None
    coverage["leaders"] = [name for _change, name in sorted(movers, reverse=True)[:3]]
    coverage["laggards"] = [name for _change, name in sorted(movers)[:2]]
    return coverage


def _hk_next_session_focus(
    ranked: Sequence[Mapping[str, Any]],
    key_variables: Mapping[str, Any],
) -> str:
    coverage = _hk_pool_coverage(ranked)
    total = coverage["total"]
    flat = total - coverage["advancers"] - coverage["decliners"]
    southbound = float(key_variables["southbound"]["net_hkd_bn"])
    direction = "净买入" if southbound >= 0 else "净卖出"
    southbound_hkd_bn = abs(southbound) * 10
    return (
        "下一交易日可以维持偏进攻但不追高的应对思路。"
        f"当日观察池{total}只股票中，{coverage['advancers']}只上涨、"
        f"{flat}只平盘、{coverage['decliners']}只下跌，价格表现整体偏强；"
        f"但只有{coverage['volume_confirmed']}只股票的量比达到1.05倍以上，"
        "说明上涨尚未获得普遍成交量确认。"
        f"南向资金当日{direction}{southbound_hkd_bn:.1f}亿港元，"
        "优先等待回踩后出现承接，或突破时成交量同步放大，再提高交易强度。"
    )


def _hk_flow_sentiment(
    ranked: Sequence[Mapping[str, Any]],
    key_variables: Mapping[str, Any],
) -> str:
    """Replace stale production prose with same-day audited HK flow facts."""
    coverage = _hk_pool_coverage(ranked)
    total = coverage["total"]
    flat = total - coverage["advancers"] - coverage["decliners"]
    southbound = float(key_variables["southbound"]["net_hkd_bn"])
    direction = "净买入" if southbound >= 0 else "净卖出"
    southbound_hkd_bn = abs(southbound) * 10
    median_change = float(coverage.get("median_change_pct") or 0)
    median_volume = float(coverage.get("median_volume_ratio") or 0)
    return (
        f"当日观察池{total}只股票中，{coverage['advancers']}只上涨、"
        f"{flat}只平盘、{coverage['decliners']}只下跌，中位涨跌幅为"
        f"{median_change:+.2f}%，市场广度整体偏正面。"
        f"量能方面，只有{coverage['volume_confirmed']}只股票的量比达到"
        f"1.05倍以上，观察池中位量比为{median_volume:.2f}倍，"
        "价格回升尚未得到普遍成交量确认。"
        f"南向资金当日{direction}{southbound_hkd_bn:.1f}亿港元，"
        "方向偏正面，但仍需结合后续流入的连续性判断资金力度。"
    )


_STALE_HK_DATA_CLAIM = re.compile(
    r"(?:缺少|缺乏).{0,28}(?:南向|量能|广度)|"
    r"(?:南向|量能|广度).{0,28}(?:缺少|缺乏)"
)


def _is_stale_hk_data_claim(value: object) -> bool:
    return bool(_STALE_HK_DATA_CLAIM.search(str(value or "")))


def _stale_hk_data_claims(document: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", document)
    text = html.unescape(re.sub(r"\s+", " ", text))
    claims = []
    for sentence in re.split(r"(?<=[。！？；])", text):
        if not _is_stale_hk_data_claim(sentence):
            continue
        claims.append(sentence.strip())
    return list(dict.fromkeys(claims))


def _has_empty_market_risk_panel(document: str) -> bool:
    """Reject a rendered market-risk card that has a heading but no items."""
    return bool(
        re.search(
            r"<h3>主要市场风险</h3>\s*<ul[^>]*>\s*</ul>",
            document,
            flags=re.IGNORECASE,
        )
    )


def _hk_market_risks(
    risks: Sequence[object],
    ranked: Sequence[Mapping[str, Any]],
    key_variables: Mapping[str, Any],
) -> list[str]:
    """Merge audited HK facts into risk copy that predates those facts."""
    cleaned: list[str] = []
    replaced_stale_claim = False
    for risk in risks:
        kept_sentences = []
        for sentence in re.split(r"(?<=[。！？；])", str(risk or "")):
            if _is_stale_hk_data_claim(sentence):
                replaced_stale_claim = True
            else:
                kept_sentences.append(sentence)
        remaining = "".join(kept_sentences).strip()
        if remaining:
            cleaned.append(remaining)

    if replaced_stale_claim:
        coverage = _hk_pool_coverage(ranked)
        total = coverage["total"]
        flat = total - coverage["advancers"] - coverage["decliners"]
        southbound = float(key_variables["southbound"]["net_hkd_bn"])
        direction = "净买入" if southbound >= 0 else "净卖出"
        cleaned.insert(
            0,
            (
                f"观察池广度为{coverage['advancers']}只上涨、{flat}只平盘、"
                f"{coverage['decliners']}只下跌，仅代表覆盖的{total}只股票；"
                f"南向资金当日{direction}{abs(southbound) * 10:.1f}亿港元，"
                "仍需观察资金连续性与全市场扩散程度。"
            ),
        )
    return list(dict.fromkeys(cleaned))


def _fallback_market_risks(
    market: str,
    ranked: Sequence[Mapping[str, Any]],
    risk_count: int,
) -> list[str]:
    """Build factual reader-facing risks when the upstream risk block is empty."""
    coverage = _hk_pool_coverage(ranked)
    total = coverage["total"]
    flat = total - coverage["advancers"] - coverage["decliners"]
    risks = [
        (
            f"观察池广度为{coverage['advancers']}只上涨、{flat}只平盘、"
            f"{coverage['decliners']}只下跌；若上涨广度转弱，短线反弹存在回吐风险。"
        ),
        (
            f"观察池仅{coverage['volume_confirmed']}只股票的量比达到1.05倍以上；"
            "若成交量未能继续扩散，指数上涨的持续性仍需验证。"
        ),
    ]
    if risk_count:
        risks.append(
            f"当前有{risk_count}只股票处于事件或风险观察状态，"
            "财报、公司事件或外部市场波动可能放大个股与指数波动。"
        )
    elif market == "us":
        risks.append("需关注美债收益率、美元及隔夜风险偏好变化对成长股估值的影响。")
    else:
        risks.append("需关注人民币汇率、南向资金连续性及隔夜外盘波动对港股风险偏好的影响。")
    return risks


def _hk_key_variable_page_gaps(page_texts: Sequence[str]) -> list[str]:
    required = (
        "港股关键变量",
        "观察池广度与量能分布",
        "定价拆解与次日验证",
        "量能扩散",
        "南向流向",
        "外部映射",
        "汇率边界",
        "下一交易日重点",
    )
    page_text = next((text for text in page_texts if "港股关键变量" in text), "")
    return [value for value in required if value not in page_text]


def _market_macro_pages(
    market: str,
    recent_macro: Sequence[Mapping[str, Any]],
    upcoming_macro: Sequence[Mapping[str, Any]],
) -> str:
    """Keep the US macro calendar in the US report only.

    The current macro feed is US-centric. Reusing it in the Hong Kong report
    adds two pages without supplying China/Hong Kong policy, liquidity or flow
    data, so the HK report omits these pages until a reliable local feed exists.
    """
    if market != "us":
        return ""
    recent = list(recent_macro[:4])
    upcoming = list(upcoming_macro[:4])
    if not recent and not upcoming:
        return ""
    if recent:
        upcoming_section = ""
        if upcoming:
            upcoming_section = f"""
      <div class="macro-section-title"><h3>未来七日高影响宏观情景</h3><small>{len(upcoming)}组 · 高于 / 符合 / 低于预期</small></div>
      <div class="upcoming-stack cards-{len(upcoming)}">{_macro_upcoming_page(upcoming)}</div>"""
        return f"""
    <section class="page macro-recent-page macro-combined-page" id="macro-combined"><div class="page-title"><div><span class="eyebrow">宏观重点</span><h2>公布后影响与未来七日情景</h2></div><span class="count">精选 {len(recent) + len(upcoming)}组</span></div>
      <div class="macro-section-title"><h3>已公布：高影响且显著偏离预期</h3><small>最近七日 · 高影响偏差≥1%，中等影响偏差≥3%</small></div>
      <div class="macro-stack cards-{len(recent)}">{_macro_recent_page(recent)}</div>{upcoming_section}
    </section>"""
    return f"""
    <section class="page" id="macro-upcoming"><div class="page-title"><div><span class="eyebrow">宏观前瞻</span><h2>未来七日高影响宏观情景</h2></div><span class="count">高于 / 符合 / 低于预期</span></div><div class="upcoming-stack cards-{len(upcoming)}">{_macro_upcoming_page(upcoming)}</div></section>"""


def _validation_value(
    value: Any,
    suffix: str = "",
    digits: int = 1,
    *,
    signed: bool = False,
) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{digits}f}{suffix}"


def _validation_group_rows(groups: Sequence[Mapping[str, Any]]) -> str:
    if not groups:
        return "<tr><td colspan='4'>样本积累中</td></tr>"
    return "".join(
        "<tr>"
        f"<td>{_e(item.get('group'))}</td>"
        f"<td>{_e(item.get('signals'))}</td>"
        f"<td>{_e(_validation_value(item.get('win_rate_1d'), '%'))}</td>"
        f"<td>{_e(_validation_value(item.get('average_return_1d'), '%', signed=True))}</td>"
        "</tr>"
        for item in groups
    )


def _top5_validation_page(
    summary: Mapping[str, Any] | None,
    market: str,
    ranked: Sequence[Mapping[str, Any]],
) -> str:
    if not summary:
        return ""
    node = (summary.get("markets") or {}).get(market)
    if not isinstance(node, Mapping):
        return ""
    latest = node.get("latest_period") if isinstance(node.get("latest_period"), Mapping) else {}
    rolling = node.get("rolling_20") if isinstance(node.get("rolling_20"), Mapping) else {}
    shadow = node.get("shadow_rolling_20") if isinstance(node.get("shadow_rolling_20"), Mapping) else {}
    comparison = node.get("comparison") if isinstance(node.get("comparison"), Mapping) else {}
    weight = node.get("weight_validation") if isinstance(node.get("weight_validation"), Mapping) else {}
    run = node.get("latest_shadow_run") if isinstance(node.get("latest_shadow_run"), Mapping) else {}
    name_map = {
        str(item.get("identity", {}).get("symbol") or ""): str(item.get("identity", {}).get("name") or "")
        for item in ranked
    }
    latest_rows = "".join(
        "<tr>"
        f"<td>{_e(item.get('rank'))}</td>"
        f"<td><b>{_e(name_map.get(str(item.get('symbol')), item.get('name') or item.get('symbol')))}</b><small>{_e(item.get('symbol'))}</small></td>"
        f"<td>{_e(_validation_value(item.get('return_1d'), '%', signed=True))}</td>"
        f"<td>{_e(_validation_value(item.get('return_5d'), '%', signed=True))}</td>"
        f"<td>{_e(_validation_value(item.get('return_20d'), '%', signed=True))}</td>"
        f"<td>{_e(_validation_value(item.get('mfe'), '%', signed=True))}</td>"
        f"<td>{_e(_validation_value(item.get('mae'), '%', signed=True))}</td>"
        "</tr>"
        for item in latest.get("items") or []
    ) or "<tr><td colspan='7'>尚无完成至少1个交易日的历史期次</td></tr>"
    run_metrics = (
        ("股票池", run.get("pool_size"), "只"),
        ("官方覆盖", run.get("official_coverage"), "%"),
        ("相对强弱", run.get("relative_strength_coverage"), "%"),
        ("量能覆盖", run.get("volume_coverage"), "%"),
        ("同日重合", run.get("overlap_ratio"), "%"),
    )
    return f"""
    <section class="page validation-page" id="top5-validation"><div class="page-title"><div><span class="eyebrow">历史复盘</span><h2>Top5 最近一期与滚动20期</h2></div><span class="count">截至 {_e(node.get('through_date'))}</span></div>
      <div class="validation-metrics">
        <div><small>统计期数</small><b>{_e(rolling.get('periods') or 0)}期</b></div>
        <div><small>1日胜率 / 平均</small><b>{_e(_validation_value(rolling.get('win_rate_1d'), '%'))} / {_e(_validation_value(rolling.get('average_return_1d'), '%', signed=True))}</b></div>
        <div><small>5日胜率 / 平均</small><b>{_e(_validation_value(rolling.get('win_rate_5d'), '%'))} / {_e(_validation_value(rolling.get('average_return_5d'), '%', signed=True))}</b></div>
        <div><small>20日胜率 / 平均</small><b>{_e(_validation_value(rolling.get('win_rate_20d'), '%'))} / {_e(_validation_value(rolling.get('average_return_20d'), '%', signed=True))}</b></div>
        <div><small>平均最大有利波动</small><b>{_e(_validation_value(rolling.get('average_mfe'), '%', signed=True))}</b></div>
        <div><small>平均最大不利波动</small><b>{_e(_validation_value(rolling.get('average_mae'), '%', signed=True))}</b></div>
      </div>
      <h3>最近完成期 · {_e(latest.get('date') or '—')}</h3>
      <table class="validation-latest"><thead><tr><th>#</th><th>股票</th><th>1日</th><th>5日</th><th>20日</th><th>最大有利</th><th>最大不利</th></tr></thead><tbody>{latest_rows}</tbody></table>
      <div class="validation-grid">
        <div class="validation-panel"><h3>影子评分 · 分数区间</h3><table><thead><tr><th>区间</th><th>样本</th><th>1日胜率</th><th>1日平均</th></tr></thead><tbody>{_validation_group_rows(node.get('by_score_band') or [])}</tbody></table></div>
        <div class="validation-panel"><h3>影子评分 · 信号类型</h3><table><thead><tr><th>类型</th><th>样本</th><th>1日胜率</th><th>1日平均</th></tr></thead><tbody>{_validation_group_rows(node.get('by_signal_type') or [])}</tbody></table></div>
      </div>
      <div class="validation-note"><b>权重检查：</b>{_e(weight.get('decision') or '样本积累中')}。影子评分累计 {_e(str(shadow.get('periods') or 0))}期，符合触发条件 {_e(str(shadow.get('triggered') or 0))}/{_e(str(shadow.get('trigger_eligible') or 0))}，目标/止损命中 {_e(str(shadow.get('target_1_hits') or 0))}/{_e(str(shadow.get('stop_hits') or 0))}；同期 Top5 平均重合 {_e(_validation_value(comparison.get('average_overlap'), '%'))}，1日平均为正式 {_e(_validation_value(comparison.get('production_average_1d'), '%', signed=True))} / 影子 {_e(_validation_value(comparison.get('shadow_average_1d'), '%', signed=True))}。</div>
      <div class="validation-run">{''.join(f'<span><small>{_e(label)}</small><b>{_e(_validation_value(value, suffix, 0))}</b></span>' for label, value, suffix in run_metrics)}</div>
    </section>"""


def render_html(
    *,
    formal: DedupReport,
    research: MarketResearch,
    stocks: Sequence[ResearchStock],
    contract_payload: Mapping[str, Any],
    event_payload: Mapping[str, Any],
    ai_crowding: Mapping[str, Any] | None = None,
    hk_key_variables: Mapping[str, Any] | None = None,
    validation_summary: Mapping[str, Any] | None = None,
    include_validation_page: bool = False,
) -> str:
    market = formal.market
    market_label = "港股" if market == "hk" else "美股"
    contracts = _contract_map(contract_payload, market)
    stock_by_symbol = _stock_map(stocks, market)
    events = _event_map(event_payload, market)
    cutoff_node = event_payload.get("information_cutoff")
    information_cutoff = (
        cutoff_node.get(market, "")
        if isinstance(cutoff_node, Mapping)
        else str(cutoff_node or "")
    )
    registry = _SourceRegistry(information_cutoff)
    ranked = sorted(contracts.values(), key=lambda item: item["opportunity"]["rank"])
    top5 = ranked[:5]
    report_day = date.fromisoformat(formal.report_date)
    recent_macro, upcoming_macro = _macro_groups(event_payload, report_day)
    macro_pages = _market_macro_pages(market, recent_macro, upcoming_macro)
    earnings_pages = _earnings_deep_pages(ranked, market_label, registry)
    validation_page = (
        _top5_validation_page(validation_summary, market, ranked)
        if include_validation_page
        else ""
    )
    regime = research.regime or formal.market_status or "分化整理"
    risk_count = sum(item["execution"]["status"] in {"事件观察", "风险失效"} for item in ranked)

    detail_pages = "".join(
        _detail_page(
            item,
            stock_by_symbol[item["identity"]["symbol"]],
            events.get(item["identity"]["symbol"], []),
            market_label,
            report_day,
            registry,
        )
        for item in top5
    )
    compact_pages = []
    remaining = ranked[5:]
    groups = _adaptive_chunks(
        remaining,
        lambda item: _compact_card_line_estimate(
            item,
            stock_by_symbol[item["identity"]["symbol"]],
            events.get(item["identity"]["symbol"], ()),
        ),
    )
    for index, group in enumerate(groups, 1):
        cards = "".join(
            _compact_card(
                item,
                stock_by_symbol[item["identity"]["symbol"]],
                events.get(item["identity"]["symbol"], []),
                registry,
            )
            for item in group
        )
        compact_pages.append(
            f'<section class="page" id="observation-{index}"><div class="page-title"><div><span class="eyebrow">机会观察池 {index}/{len(groups)}</span><h2>观察池 {index}/{len(groups)} · 条件、催化与风险</h2></div><span class="count">{len(group)}只</span></div><div class="compact-stack cards-{len(group)}">{cards}</div></section>'
        )

    market_risks = (
        _hk_market_risks(research.risks, ranked, hk_key_variables)
        if market == "hk" and hk_key_variables
        else list(research.risks)
    )
    risk_limit = 220 if market == "hk" else 150
    risk_items = [
        _reader_text(item, risk_limit)
        for item in market_risks[:3]
        if _meaningful(_reader_text(item, 150))
    ]
    if not risk_items:
        risk_items = _fallback_market_risks(market, ranked, risk_count)
    risks = "".join(f"<li>{_e(item)}</li>" for item in risk_items[:3])
    ai_panel = _ai_crowding_panel(ai_crowding) if market == "us" else ""
    hk_variables_page = (
        render_integrated_page(hk_key_variables, _hk_pool_coverage(ranked))
        if market == "hk" and hk_key_variables
        else ""
    )
    next_session_focus = (
        _hk_next_session_focus(ranked, hk_key_variables)
        if market == "hk" and hk_key_variables
        else _public_plan(research.plan, 900)
    )
    flow_sentiment = (
        _hk_flow_sentiment(ranked, hk_key_variables)
        if market == "hk" and hk_key_variables
        else research.flow_sentiment
    )
    market_page_class = "market-page hk-market-page" if market == "hk" else "market-page"
    market_digest_args = {"max_points": 5, "limit_each": 220} if market == "hk" else {}
    top5_cards_html = _top5_cards(top5, events, stock_by_symbol, registry)
    source_pages = _source_appendix(registry)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{market_label}机会研究报告</title><style>{CSS}</style></head><body>
    <section class="page" id="cover"><div class="cover"><div class="eyebrow">DAILY OPPORTUNITY RESEARCH</div><h1>{market_label}机会研究报告</h1><p>{_e(formal.report_date)}｜市场机会、执行状态与风险边界</p><div class="cover-metrics"><div><b>{len(ranked)}</b>覆盖股票</div><div><b>5</b>重点机会</div><div><b>{sum(i['execution']['status'] in {'可执行', '谨慎可执行'} for i in ranked)}</b>条件成熟</div><div><b>{risk_count}</b>事件/风险观察</div></div></div>
      <div class="market-banner"><div><small>市场状态</small><b>{_e(regime)}</b></div><div><small>主线方向</small><b>{_e(_plain(formal.main_theme,90))}</b></div><div><small>核心策略</small><b>{_e(_plain(formal.strategy,190))}</b></div></div>
      <div class="strategy">{_e(_plain(research.one_sentence or formal.strategy,280))}</div>
      <h3>最值得关注 Top5</h3><div class="top5-list">{top5_cards_html}</div>
      <div class="status-legend"><span>{_status('可执行')} 标准确认</span><span>{_status('谨慎可执行')} 有软约束</span><span>{_status('等待触发')} 等价格/量能/跨日确认</span><span>{_status('事件观察')} 等事件落地或消化后重估</span><span>{_status('研究关注')} 逻辑尚未形成触发</span><span>{_status('风险失效')} 原逻辑已破坏</span></div>
    </section>
    <section class="page {market_page_class}" id="market"><div class="page-title"><div><span class="eyebrow">市场与板块</span><h2>指数结构、风格与主线</h2></div><span class="count">{_e(regime)}</span></div><div class="market-lead">{_e(_reader_text(research.one_sentence,420 if market == 'hk' else 300))}</div>{_index_table(research)}<div class="market-grid"><div class="panel"><h3>指数结构</h3>{_market_digest(research.structure, **market_digest_args)}</div><div class="panel"><h3>板块主线</h3>{_market_digest(research.sectors, **market_digest_args)}</div><div class="panel"><h3>资金与情绪</h3>{_market_digest(flow_sentiment, **market_digest_args)}</div><div class="panel"><h3>消息催化</h3>{_market_digest(research.catalysts, **market_digest_args)}</div><div class="panel"><h3>下一交易日重点</h3>{_market_digest(next_session_focus, **market_digest_args)}</div><div class="panel"><h3>主要市场风险</h3><ul class="reader-bullets">{risks}</ul></div></div>{ai_panel}</section>
    {hk_variables_page}{macro_pages}
    <section class="page" id="ranking"><div class="page-title"><div><span class="eyebrow">全池机会排序</span><h2>机会分与执行状态</h2></div><span class="count">{len(ranked)}只</span></div><table class="ranking-table"><thead><tr><th>#</th><th>股票</th><th>机会分</th><th>状态</th><th>相对强弱</th><th>量能</th><th>下一条件</th></tr></thead><tbody>{_ranking_rows(ranked)}</tbody></table></section>
    {validation_page}{earnings_pages}{detail_pages}{''.join(compact_pages)}{source_pages}</body></html>"""


def render_report(
    *,
    input_md: Path,
    market: str,
    db_path: Path,
    contract_path: Path,
    event_context_path: Path,
    output_html: Path,
    output_pdf: Path,
    hk_key_variables_path: Path | None = None,
    validation_summary_path: Path | None = None,
    accuracy_ledger_path: Path | None = None,
    include_validation_page: bool = False,
) -> dict:
    from pypdf import PdfReader
    from weasyprint import HTML
    from v2.ai_crowding import (
        enrich_ai_crowding_trend,
        load_ai_crowding_history,
        save_ai_crowding_history,
        score_ai_crowding,
    )

    formal = parse_report(input_md, market=market)
    research = parse_market_research(input_md.read_text(encoding="utf-8"))
    missing_required_content = _required_content_gaps(formal, research)
    stocks = load_stocks(db_path, formal.report_date, market, event_context_path)
    temporal_earnings_issues = _earnings_temporal_issues(
        stocks, date.fromisoformat(formal.report_date)
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    events = json.loads(event_context_path.read_text(encoding="utf-8"))
    hk_key_variables = None
    if market == "hk" and hk_key_variables_path is not None:
        hk_key_variables = json.loads(hk_key_variables_path.read_text(encoding="utf-8"))
        if str(hk_key_variables.get("as_of") or "") != formal.report_date:
            raise ValueError(
                "HK key-variable snapshot date must match the report date: "
                f"{hk_key_variables.get('as_of')} != {formal.report_date}"
            )
        if hk_key_variables.get("quality", {}).get("status") != "complete":
            raise ValueError("HK key-variable snapshot is not complete")
    validation_summary = None
    if validation_summary_path is not None and validation_summary_path.exists():
        validation_summary = json.loads(validation_summary_path.read_text(encoding="utf-8"))
        if str(validation_summary.get("through_date") or "") != formal.report_date:
            raise ValueError(
                "Top5 validation summary date must match the report date: "
                f"{validation_summary.get('through_date')} != {formal.report_date}"
            )
    ai_crowding = None
    if market == "us":
        from src.services.ai_bubble_monitor import AIBubbleMonitorService

        raw_ai = AIBubbleMonitorService(as_of=formal.report_date).run().to_dict()
        current_ai = score_ai_crowding(raw_ai).to_dict()
        current_ai["as_of"] = str(raw_ai.get("as_of") or formal.report_date)[:10]
        history_path = V2_ROOT / "state" / "ai_crowding_history.json"
        history = load_ai_crowding_history(history_path)
        history.append(current_ai)
        ai_crowding = enrich_ai_crowding_trend(current_ai, history)
        save_ai_crowding_history(history_path, history)
    document = render_html(
        formal=formal,
        research=research,
        stocks=stocks,
        contract_payload=contract,
        event_payload=events,
        ai_crowding=ai_crowding,
        hk_key_variables=hk_key_variables,
        validation_summary=validation_summary,
        include_validation_page=include_validation_page,
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(document, encoding="utf-8")
    rendered_document = HTML(string=document, base_url=str(output_html.parent)).render()
    geometry_issues = _layout_geometry_issues(rendered_document)
    rendered_document.write_pdf(str(output_pdf))
    reader = PdfReader(str(output_pdf))
    physical_pages = len(reader.pages)
    logical_pages = _logical_page_count(document)
    empty_pages = [
        index
        for index, page in enumerate(reader.pages, 1)
        if len((page.extract_text() or "").strip()) < 20
    ]
    page_texts = [(page.extract_text() or "") for page in reader.pages]
    hk_key_variable_page_gaps = (
        _hk_key_variable_page_gaps(page_texts) if hk_key_variables else []
    )
    bookmark_titles = _outline_titles(reader.outline)
    forbidden_hits = [term for term in FORBIDDEN_TEXT if term.lower() in document.lower()]
    stale_hk_data_claims = _stale_hk_data_claims(document) if hk_key_variables else []
    empty_market_risk_panel = _has_empty_market_risk_panel(document)
    result = {
        "market": market,
        "report_date": formal.report_date,
        "stocks": len(stocks),
        "pages": physical_pages,
        "logical_pages": logical_pages,
        "layout_ok": physical_pages == logical_pages and not empty_pages and not geometry_issues,
        "layout_overflow_pages": max(0, physical_pages - logical_pages),
        "empty_pages": empty_pages,
        "layout_geometry_issues": geometry_issues,
        "content_ok": (
            not missing_required_content
            and not stale_hk_data_claims
            and not empty_market_risk_panel
            and not hk_key_variable_page_gaps
            and not forbidden_hits
            and not temporal_earnings_issues
        ),
        "missing_required_content": missing_required_content,
        "stale_hk_data_claims": stale_hk_data_claims,
        "empty_market_risk_panel": empty_market_risk_panel,
        "hk_key_variable_page_gaps": hk_key_variable_page_gaps,
        "temporal_earnings_issues": temporal_earnings_issues,
        "bookmark_count": len(bookmark_titles),
        "bookmark_titles": bookmark_titles,
        "forbidden_text_hits": forbidden_hits,
        "html": str(output_html),
        "pdf": str(output_pdf),
    }
    if accuracy_ledger_path is not None:
        from v2.top5_validation import Top5ValidationLedger

        ledger = Top5ValidationLedger(accuracy_ledger_path)
        ledger.update_report_audit(formal.report_date, market, result)
        ledger.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render complete public-facing opportunity report")
    parser.add_argument("--input-md", type=Path, required=True)
    parser.add_argument("--market", choices=("hk", "us"), required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--event-context", type=Path, required=True)
    parser.add_argument("--hk-key-variables", type=Path)
    parser.add_argument("--validation-summary", type=Path)
    parser.add_argument("--accuracy-ledger", type=Path)
    parser.add_argument(
        "--include-validation-page",
        action="store_true",
        help="Internal preview only: include the Top5 validation page in the PDF",
    )
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    args = parser.parse_args(argv)
    result = render_report(
        input_md=args.input_md,
        market=args.market,
        db_path=args.db,
        contract_path=args.contract,
        event_context_path=args.event_context,
        output_html=args.output_html,
        output_pdf=args.output_pdf,
        hk_key_variables_path=args.hk_key_variables,
        validation_summary_path=args.validation_summary,
        accuracy_ledger_path=args.accuracy_ledger,
        include_validation_page=args.include_validation_page,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["forbidden_text_hits"] and result["layout_ok"] and result["content_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
