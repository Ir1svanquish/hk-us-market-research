"""Render the formal research report from the daily analysis artifacts.

The report keeps the decision-relevant analysis inputs (market/sector
research, Longbridge quotes, relative volume, technical structure, US social
sentiment, dated catalysts/risks, and separate flat/holding plans) while
removing repeated prose. It overlays the generated quality, event, and trade-card
review for covered symbols.

This module reads the analysis database and existing analysis artifacts, then
writes the formal report to ``reporting/output`` (or explicit output paths).
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from reporting.dedup_report import DedupReport, parse_report
from src.data.stock_mapping import normalize_stock_name


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTING_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_REVIEW = REPORTING_ROOT / "output" / "event_context_top10_trading_cards_20260821.json"

ACTION_ORDER = {"卖出": 0, "减仓": 1, "买入": 2, "持有": 3, "观望": 4}
ACTION_CLASS = {"买入": "buy", "持有": "hold", "减仓": "reduce", "卖出": "sell", "观望": "watch"}


def _clean(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[*_`#]", "", text)
    return re.sub(r"\s+", " ", text).strip(" |\n\t")


def _limit(value: Any, chars: int) -> str:
    text = _clean(value)
    if len(text) <= chars:
        return text
    cut = text[:chars].rstrip("，、；;：: ")
    return cut + "…"


def _sentences(value: Any, count: int = 2, chars: int = 260) -> str:
    text = _clean(value)
    parts = [part.strip() for part in re.split(r"(?<=[。！？；])", text) if part.strip()]
    return _limit("".join(parts[:count]) if parts else text, chars)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_action(value: Any) -> str:
    text = _clean(value)
    if text.startswith("减仓"):
        return "减仓"
    return text if text in ACTION_ORDER else "观望"


def _format_num(value: Any, digits: int = 2, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.{digits}f}{suffix}"


def _symbol_key(symbol: str, market: str) -> str:
    value = _clean(symbol).replace(" ", "")
    if market == "hk":
        return re.sub(r"^HK", "", value).zfill(5)
    return value.upper()


@dataclass
class ResearchStock:
    symbol: str
    name: str
    market: str
    action: str
    score: int
    bias: str
    confidence: str
    conclusion: str
    validity: str
    flat_advice: str
    holding_advice: str
    open_price: str
    high_price: str
    low_price: str
    current_price: str
    previous_close: str
    change_pct: str
    amplitude: str
    volume: str
    amount: str
    turnover_rate: str
    quote_source: str
    relative_volume: float | None
    relative_volume_source: str
    technical: dict[str, Any]
    volume_meaning: str
    sector_position: str
    earnings_outlook: str
    sentiment_summary: str
    social_lines: list[str]
    risks: list[str]
    catalysts: list[str]
    latest_news: str
    hot_topics: str
    data_sources: str
    entry: str
    secondary_entry: str
    stop: str
    target: str
    position: str
    entry_plan: str
    risk_control: str
    checklist: list[str]
    display_completeness: int
    review: dict[str, Any] = field(default_factory=dict)
    evidence_sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MarketResearch:
    one_sentence: str = ""
    regime: str = ""
    overview: str = ""
    structure: str = ""
    sectors: str = ""
    flow_sentiment: str = ""
    catalysts: str = ""
    plan: str = ""
    risks: list[str] = field(default_factory=list)
    ai_crowding: list[str] = field(default_factory=list)
    index_headers: list[str] = field(default_factory=list)
    index_rows: list[list[str]] = field(default_factory=list)


def _extract_heading_body(text: str, heading_pattern: str) -> str:
    header = re.search(rf"^###\s*{heading_pattern}\s*$", text, re.MULTILINE)
    if not header:
        return ""
    start = header.end()
    next_header = re.search(r"^###\s|^#\s", text[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(text)
    return text[start:end].strip()


def _plain_markdown(value: str) -> str:
    lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|") or stripped.startswith("---"):
            continue
        stripped = re.sub(r"^[-*>\d.\s]+", "", stripped)
        if stripped:
            lines.append(_clean(stripped))
    return " ".join(lines)


def _extract_bullets(value: str, limit: int = 5) -> list[str]:
    results = []
    for line in value.splitlines():
        match = re.match(r"\s*(?:[-*]|\d+[.)])\s*(.+)", line)
        if match:
            cleaned = _clean(match.group(1))
            if cleaned:
                results.append(cleaned)
    return results[:limit]


def _extract_first_table(value: str) -> tuple[list[str], list[list[str]]]:
    lines = value.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        headers = [_clean(cell) for cell in line.strip().strip("|").split("|")]
        if "指数" not in headers:
            continue
        rows: list[list[str]] = []
        for candidate in lines[index + 2 :]:
            if not candidate.strip().startswith("|"):
                break
            cells = [_clean(cell) for cell in candidate.strip().strip("|").split("|")]
            if len(cells) == len(headers):
                rows.append(cells)
        return headers, rows
    return [], []


def parse_market_research(report_text: str) -> MarketResearch:
    start = report_text.find("# 🎯 大盘复盘")
    end_match = re.search(r"^#\s*🎯\s*\d{4}-\d{2}-\d{2}\s*决策仪表盘", report_text, re.MULTILINE)
    market_text = report_text[start : end_match.start()] if start >= 0 and end_match else ""
    one_match = re.search(r"^>\s*(?:一句话(?:结论)?[：:]\s*)?(.+)$", market_text, re.MULTILINE)
    overview_body = _extract_heading_body(market_text, r"一、盘面总览.*")
    structure_body = _extract_heading_body(market_text, r"二、指数结构.*")
    sectors_body = _extract_heading_body(market_text, r"三、板块主线.*")
    flow_body = _extract_heading_body(market_text, r"四、资金与情绪.*")
    catalyst_body = _extract_heading_body(market_text, r"五、消息催化.*")
    plan_body = _extract_heading_body(market_text, r"六、明日交易计划.*")
    risk_body = _extract_heading_body(market_text, r"七、风险提示.*")
    ai_body = _extract_heading_body(market_text, r"六、AI拥挤度监控.*")
    headers, rows = _extract_first_table(market_text)
    overview = _plain_markdown(overview_body)
    regime_match = re.search(r"(?:属于|定性为)[“\"]([^”\"]+)[”\"]", overview)
    return MarketResearch(
        one_sentence=_limit(one_match.group(1), 360) if one_match else "",
        regime=_clean(regime_match.group(1)) if regime_match else "",
        overview=_sentences(_plain_markdown(overview_body), 3, 420),
        structure=_sentences(_plain_markdown(structure_body), 4, 600),
        sectors=_sentences(_plain_markdown(sectors_body), 4, 620),
        flow_sentiment=_sentences(_plain_markdown(flow_body), 3, 420),
        catalysts=_sentences(_plain_markdown(catalyst_body), 3, 460),
        plan=_sentences(_plain_markdown(plan_body), 4, 520),
        risks=_extract_bullets(risk_body, 5),
        ai_crowding=_extract_bullets(ai_body, 8),
        index_headers=headers,
        index_rows=rows,
    )


def _load_review(path: Path | None, market: str) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    quality = _as_dict(payload.get("decision_quality"))
    cards = _as_dict(payload.get("trading_cards"))
    result: dict[str, dict[str, Any]] = {}
    for raw_symbol, item in quality.items():
        if _clean(item.get("market")) != market:
            continue
        key = _symbol_key(raw_symbol, market)
        result[key] = dict(item)
        result[key]["trade_card"] = cards.get(raw_symbol) or _as_dict(item.get("standard_trade_card"))
    return result


def _display_completeness(raw: dict[str, Any], relative_volume: Any, market: str) -> int:
    dashboard = _as_dict(raw.get("dashboard"))
    data = _as_dict(dashboard.get("data_perspective"))
    intel = _as_dict(dashboard.get("intelligence"))
    plan = _as_dict(dashboard.get("battle_plan"))
    checks = [
        bool(raw.get("market_snapshot")),
        bool(data.get("trend_status")),
        bool(data.get("price_position")),
        relative_volume is not None,
        bool(intel.get("risk_alerts") or intel.get("positive_catalysts")),
        bool(intel.get("earnings_outlook") and "缺失" not in _clean(intel.get("earnings_outlook"))),
        bool(plan.get("sniper_points")),
        bool(plan.get("position_strategy")),
    ]
    if market == "us":
        checks.append(bool(intel.get("social_sentiment_summary")))
    return round(sum(checks) / len(checks) * 100)


def load_stocks(
    db_path: Path,
    report_date: str,
    market: str,
    review_path: Path | None = DEFAULT_REVIEW,
) -> list[ResearchStock]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    market_clause = "h.code like 'HK%'" if market == "hk" else "h.code not like 'HK%'"
    rows = con.execute(
        f"""
        SELECT h.*, d.volume_ratio AS historical_relative_volume,
               d.data_source AS relative_volume_source
        FROM analysis_history h
        LEFT JOIN stock_daily d ON d.code = h.code AND d.date = ?
        WHERE date(h.created_at) = ? AND {market_clause}
        ORDER BY h.created_at, h.id
        """,
        (report_date, report_date),
    ).fetchall()
    evidence_by_symbol: dict[str, list[dict[str, Any]]] = {}
    try:
        evidence_rows = con.execute(
            f"""
            SELECT code, dimension, provider, title, snippet, url, source,
                   published_date, fetched_at
            FROM news_intel
            WHERE date(fetched_at) = ? AND {market_clause.replace('h.code', 'code')}
              AND url IS NOT NULL AND trim(url) != ''
            ORDER BY code, published_date DESC, fetched_at DESC
            """,
            (report_date,),
        ).fetchall()
        for evidence in evidence_rows:
            key = _symbol_key(_clean(evidence["code"]), market)
            bucket = evidence_by_symbol.setdefault(key, [])
            if len(bucket) >= 24:
                continue
            bucket.append(
                {
                    "dimension": _clean(evidence["dimension"]),
                    "provider": _clean(evidence["provider"]),
                    "title": _clean(evidence["title"]),
                    "snippet": _clean(evidence["snippet"]),
                    "url": _clean(evidence["url"]),
                    "source": _clean(evidence["source"]),
                    "published_at": _clean(evidence["published_date"]),
                    "fetched_at": _clean(evidence["fetched_at"]),
                }
            )
    except sqlite3.OperationalError:
        # Older or deliberately minimal databases do not contain news_intel.
        # Rendering remains supported; those reports simply have no news refs.
        evidence_by_symbol = {}
    review = _load_review(review_path, market)
    stocks: list[ResearchStock] = []
    for row in rows:
        raw = json.loads(row["raw_result"] or "{}")
        dashboard = _as_dict(raw.get("dashboard"))
        core = _as_dict(dashboard.get("core_conclusion"))
        advice = _as_dict(core.get("position_advice"))
        data = _as_dict(dashboard.get("data_perspective"))
        trend = _as_dict(data.get("trend_status"))
        prices = _as_dict(data.get("price_position"))
        volume = _as_dict(data.get("volume_analysis"))
        intel = _as_dict(dashboard.get("intelligence"))
        plan = _as_dict(dashboard.get("battle_plan"))
        points = _as_dict(plan.get("sniper_points"))
        position = _as_dict(plan.get("position_strategy"))
        snapshot = _as_dict(raw.get("market_snapshot"))
        relative_volume = row["historical_relative_volume"]
        symbol = _clean(row["code"])
        technical = dict(prices)
        technical.update({"ma_alignment": trend.get("ma_alignment"), "trend_score": trend.get("trend_score")})
        stocks.append(
            ResearchStock(
                symbol=symbol,
                name=normalize_stock_name(symbol, _clean(row["name"] or raw.get("name"))),
                market=market,
                action=_normalize_action(row["operation_advice"] or raw.get("operation_advice")),
                score=int(raw.get("sentiment_score") or row["sentiment_score"] or 0),
                bias=_clean(raw.get("trend_prediction")),
                confidence=_clean(raw.get("confidence_level")),
                conclusion=_clean(core.get("one_sentence") or raw.get("analysis_summary")),
                validity=_clean(core.get("time_sensitivity")),
                flat_advice=_clean(advice.get("no_position")),
                holding_advice=_clean(advice.get("has_position")),
                open_price=_clean(snapshot.get("open")),
                high_price=_clean(snapshot.get("high")),
                low_price=_clean(snapshot.get("low")),
                current_price=_clean(snapshot.get("close") or snapshot.get("price") or raw.get("current_price")),
                previous_close=_clean(snapshot.get("prev_close")),
                change_pct=_clean(snapshot.get("pct_chg") or raw.get("change_pct")),
                amplitude=_clean(snapshot.get("amplitude")),
                volume=_clean(snapshot.get("volume")),
                amount=_clean(snapshot.get("amount")),
                turnover_rate=_clean(snapshot.get("turnover_rate") or volume.get("turnover_rate")),
                quote_source=_clean(snapshot.get("source")),
                relative_volume=float(relative_volume) if relative_volume is not None else None,
                relative_volume_source=_clean(row["relative_volume_source"]),
                technical=technical,
                volume_meaning=_clean(volume.get("volume_meaning") or raw.get("volume_analysis")),
                sector_position=_clean(raw.get("sector_position")),
                earnings_outlook=_clean(intel.get("earnings_outlook")),
                sentiment_summary=_clean(intel.get("sentiment_summary") or raw.get("market_sentiment")),
                social_lines=[_clean(item) for item in _as_list(intel.get("social_sentiment_summary")) if _clean(item)],
                risks=[_clean(item) for item in _as_list(intel.get("risk_alerts")) if _clean(item)][:2],
                catalysts=[_clean(item) for item in _as_list(intel.get("positive_catalysts")) if _clean(item)][:2],
                latest_news=_clean(intel.get("latest_news") or raw.get("news_summary")),
                hot_topics=_clean(raw.get("hot_topics")),
                data_sources=_clean(raw.get("data_sources")),
                entry=_clean(points.get("ideal_buy")),
                secondary_entry=_clean(points.get("secondary_buy")),
                stop=_clean(points.get("stop_loss")),
                target=_clean(points.get("take_profit")),
                position=_clean(position.get("suggested_position")),
                entry_plan=_clean(position.get("entry_plan")),
                risk_control=_clean(position.get("risk_control")),
                checklist=[_clean(item) for item in _as_list(plan.get("action_checklist")) if _clean(item)],
                display_completeness=_display_completeness(raw, relative_volume, market),
                review=review.get(_symbol_key(symbol, market), {}),
                evidence_sources=evidence_by_symbol.get(_symbol_key(symbol, market), []),
            )
        )
    con.close()
    return sorted(stocks, key=lambda item: (ACTION_ORDER.get(item.action, 9), -item.score, item.symbol))


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _action_pill(action: str) -> str:
    return f'<span class="pill pill-{ACTION_CLASS.get(action, "watch")}">{_e(action)}</span>'


def _status_badges(item: ResearchStock) -> str:
    badges: list[str] = []
    labels = {"1": "趋势", "2": "乖离", "3": "量能", "4": "事件", "5": "估值"}
    for raw in item.checklist:
        match = re.search(r"([✅⚠️❌]).*?检查项(\d+)", raw)
        if not match:
            continue
        status, number = match.groups()
        badges.append(f'<span class="check check-{_e(status)}">{_e(labels.get(number, number))}{_e(status)}</span>')
    return "".join(badges)


def _detail_rows(item: ResearchStock) -> str:
    tech = item.technical
    values = [
        ("现价", item.current_price or "—"),
        ("日涨跌", item.change_pct or "—"),
        ("振幅", item.amplitude or "—"),
        ("换手率", item.turnover_rate or "—"),
        ("相对成交量", _format_num(item.relative_volume, 2, "×")),
        ("趋势分", f"{tech.get('trend_score', '—')}/100"),
        ("MA5", _format_num(tech.get("ma5"))),
        ("MA10", _format_num(tech.get("ma10"))),
        ("MA20", _format_num(tech.get("ma20"))),
        ("MA5乖离", _format_num(tech.get("bias_ma5"), 2, "%")),
        ("支撑", _format_num(tech.get("support_level"))),
        ("压力", _format_num(tech.get("resistance_level"))),
    ]
    return "".join(f'<div><small>{_e(label)}</small><b>{_e(value)}</b></div>' for label, value in values)


def _review_overlay(item: ResearchStock) -> str:
    if not item.review:
        return '<div class="review-empty">官方事件与质量复核尚未覆盖本标的；当前仅展示分析阶段结论。</div>'
    card = _as_dict(item.review.get("trade_card"))
    gate = _as_dict(item.review.get("final_gate"))
    rr = card.get("risk_reward")
    rr_text = f"1:{float(rr):.2f}" if isinstance(rr, (int, float)) else "—"
    volume_live = card.get("volume_ratio_live")
    live_text = _format_num(volume_live, 2, "×") if volume_live is not None else "—"
    return f"""
      <div class="review-grid">
        <div><small>最终动作</small><b>{_e(item.review.get('final_action') or card.get('label') or '—')}</b></div>
        <div><small>数据完整度</small><b>{_e(item.review.get('data_completeness', '—'))}</b></div>
        <div><small>信号置信度</small><b>{_e(item.review.get('signal_confidence', '—'))}</b></div>
        <div><small>实时量比</small><b>{_e(live_text)}</b></div>
        <div><small>ATR14</small><b>{_e(_format_num(card.get('atr14')))}</b></div>
        <div><small>预期盈亏比</small><b>{_e(rr_text)}</b></div>
      </div>
      <div class="gate"><b>闸门：</b>{_e(_limit(gate.get('reason') or '未触发硬性限制', 220))}</div>
      <div class="gap"><b>跳空/失效：</b>{_e(_limit(card.get('gap_rule') or '', 180))}</div>"""


def _active_page(item: ResearchStock, report_date: str) -> str:
    risks = "".join(f"<li>{_e(_limit(value, 170))}</li>" for value in item.risks) or "<li>未提取到明确新增风险</li>"
    catalysts = "".join(f"<li>{_e(_limit(value, 170))}</li>" for value in item.catalysts) or "<li>无明确新增催化</li>"
    socials = "".join(f"<li>{_e(_limit(value, 130))}</li>" for value in item.social_lines)
    social_block = f'<div class="social"><b>美股社媒舆情</b><ul>{socials}</ul></div>' if socials else ""
    return f"""
    <section class="page stock-page">
      <div class="page-title"><div><span class="eyebrow">重点行动卡 · { _e(report_date) }</span><h2>{_e(item.name)} <small>{_e(item.symbol)}</small></h2></div>{_action_pill(item.action)}</div>
      <div class="headline"><b>{_e(item.score)}分 · {_e(item.bias)} · {_e(item.confidence or '未标注置信度')}</b><span>{_e(item.conclusion)}</span></div>
      <div class="data-strip">{_detail_rows(item)}</div>
      <div class="source-line">行情 {_e(item.quote_source or '未标注')}｜相对成交量 {_e(item.relative_volume_source or '未标注')}｜字段完整度 {_e(item.display_completeness)}%｜时效 {_e(item.validity or '未明确')}</div>

      <div class="two-col">
        <div class="panel"><h3>技术与量价</h3><p><b>结构：</b>{_e(_limit(item.technical.get('ma_alignment'), 230))}</p><p><b>量能：</b>{_e(_limit(item.volume_meaning, 260))}</p><p><b>板块地位：</b>{_e(_sentences(item.sector_position, 3, 360))}</p></div>
        <div class="panel"><h3>事件、业绩与舆情</h3><p><b>业绩：</b>{_e(_limit(item.earnings_outlook, 300))}</p><p><b>舆情：</b>{_e(_limit(item.sentiment_summary, 250))}</p>{social_block}<p><b>主题：</b>{_e(_limit(item.hot_topics, 180))}</p></div>
      </div>

      <div class="evidence-grid">
        <div class="risk-box"><h3>主要风险</h3><ul>{risks}</ul></div>
        <div class="catalyst-box"><h3>主要催化</h3><ul>{catalysts}</ul></div>
      </div>
      <div class="latest"><b>合并后的最新动态：</b>{_e(_sentences(item.latest_news, 2, 360))}</div>

      <div class="plan"><h3>分析阶段交易计划</h3>
        <div class="plan-grid"><div><small>空仓者</small>{_e(_limit(item.flat_advice, 260))}</div><div><small>持仓者</small>{_e(_limit(item.holding_advice, 260))}</div></div>
        <div class="levels"><div><small>理想/触发</small>{_e(_limit(item.entry, 145))}</div><div><small>次优</small>{_e(_limit(item.secondary_entry, 145))}</div><div><small>止损</small>{_e(_limit(item.stop, 145))}</div><div><small>目标</small>{_e(_limit(item.target, 145))}</div></div>
        <div class="plan-foot"><span><b>仓位：</b>{_e(_limit(item.position, 100))}</span><span>{_status_badges(item)}</span></div>
      </div>

      <div class="review"><h3>质量、事件与执行复核</h3>{_review_overlay(item)}</div>
      <div class="footnote">数据来源：{_e(_limit(item.data_sources, 260))}</div>
    </section>"""


def _watch_card(item: ResearchStock) -> str:
    warnings = [line for line in item.checklist if line.startswith(("⚠️", "❌"))]
    abnormal = "；".join(_limit(line, 85) for line in warnings[:2]) or "无额外异常项"
    social = "；".join(_limit(line, 75) for line in item.social_lines[:3])
    review_action = item.review.get("final_action") if item.review else "待补跑"
    return f"""
    <article class="watch-card">
      <div class="watch-head"><div><b>{_e(item.name)}</b><small>{_e(item.symbol)} · {item.score}分 · {_e(item.bias)}</small></div>{_action_pill(item.action)}</div>
      <div class="watch-quote"><span>{_e(item.current_price)} / {_e(item.change_pct)}</span><span>相对量 {_e(_format_num(item.relative_volume, 2, '×'))}</span><span>换手 {_e(item.turnover_rate or '—')}</span><span>完整 {_e(item.display_completeness)}%</span></div>
      <p><b>为什么观察：</b>{_e(_limit(item.conclusion, 200))}</p>
      <p><b>重新评估：</b>{_e(_limit(item.flat_advice or item.entry_plan, 230))}</p>
      <div class="mini-grid"><div><b>风险</b>{_e(_limit(item.risks[0] if item.risks else item.risk_control, 150))}</div><div><b>催化</b>{_e(_limit(item.catalysts[0] if item.catalysts else item.latest_news, 150))}</div></div>
      {f'<p class="social-line"><b>社媒：</b>{_e(social)}</p>' if social else ''}
      <div class="watch-foot"><span>{_status_badges(item)}</span><span>复核：{_e(review_action)}</span></div>
      <div class="abnormal">仅展开异常项：{_e(abnormal)}</div>
    </article>"""


def _chunks(items: list[ResearchStock], size: int) -> Iterable[list[ResearchStock]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _index_table(research: MarketResearch) -> str:
    if not research.index_headers or not research.index_rows:
        return '<div class="empty">指数结构表未提取到。</div>'
    keep = [index for index, value in enumerate(research.index_headers) if value in {"指数", "最新", "涨跌幅", "振幅", "成交额(亿)"}]
    headers = "".join(f"<th>{_e(research.index_headers[index])}</th>" for index in keep)
    rows = "".join("<tr>" + "".join(f"<td>{_e(row[index])}</td>" for index in keep) + "</tr>" for row in research.index_rows)
    return f"<table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"


def _decision_rows(stocks: list[ResearchStock]) -> str:
    rows = []
    for item in stocks:
        review_action = item.review.get("final_action") if item.review else "待复核"
        rows.append(
            f"<tr><td><b>{_e(item.name)}</b><small>{_e(item.symbol)}</small></td><td>{_action_pill(item.action)}</td>"
            f"<td>{item.score}</td><td>{_e(item.bias)}</td><td>{_e(item.current_price)}<small>{_e(item.change_pct)}</small></td>"
            f"<td>{_e(_format_num(item.relative_volume, 2, '×'))}</td><td>{_e(item.turnover_rate or '—')}</td>"
            f"<td>{_e(item.display_completeness)}%</td><td>{_e(review_action)}</td></tr>"
        )
    return "".join(rows)


def render_html(formal: DedupReport, market_research: MarketResearch, stocks: list[ResearchStock]) -> str:
    market_label = "港股" if formal.market == "hk" else "美股"
    active = [item for item in stocks if item.action != "观望"]
    watches = [item for item in stocks if item.action == "观望"]
    counts = {action: sum(item.action == action for item in stocks) for action in ACTION_ORDER}
    effective_regime = market_research.regime or formal.market_status or "未明确"
    focus = "".join(f"<span>{_e(item)}</span>" for item in formal.focus_names)
    avoid = "".join(f"<span>{_e(item)}</span>" for item in formal.avoid_names)
    risks = "".join(f"<li>{_e(_limit(item, 150))}</li>" for item in market_research.risks[:5])
    ai = "".join(f"<li>{_e(_limit(item, 170))}</li>" for item in market_research.ai_crowding)
    active_pages = "".join(_active_page(item, formal.report_date) for item in active)
    watch_pages = ""
    groups = list(_chunks(watches, 3))
    for page_no, group in enumerate(groups, 1):
        cards = "".join(_watch_card(item) for item in group)
        watch_pages += f'<section class="page"><div class="page-title"><div><span class="eyebrow">观察池 · {page_no}/{len(groups)}</span><h2>等待条件确认</h2></div><span class="count">{len(group)} 只</span></div><div class="watch-stack">{cards}</div></section>'
    ai_block = f'<div class="panel"><h3>AI 拥挤度监控</h3><ul>{ai}</ul></div>' if ai else ""
    consistency = ""
    if market_research.regime and formal.market_status and market_research.regime != formal.market_status:
        consistency = f'<div class="consistency"><b>结论统一：</b>旧首页“{_e(formal.market_status)}”与详细复盘“{_e(market_research.regime)}”不一致；本报告采用有指数证据支撑的“{_e(effective_regime)}”，并保留数据缺口提示。</div>'
    estimated_pages = 3 + len(active) + math.ceil(len(watches) / 3)
    css = """
@page{size:A4;margin:10mm 11mm 13mm;@bottom-right{content:"第 " counter(page) " 页";font-size:8px;color:#64748b}}
*{box-sizing:border-box}body{margin:0;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#172033;font-size:9px;line-height:1.45}.page{break-after:page;min-height:270mm;position:relative}.page:last-child{break-after:auto}.eyebrow{font-size:8px;font-weight:800;letter-spacing:1px;color:#0f766e;text-transform:uppercase}h1{font-size:27px;margin:3mm 0 2mm}h2{font-size:18px;color:#173d70;margin:1mm 0}h2 small{font-size:9px;color:#64748b}h3{font-size:10px;margin:0 0 1.5mm;color:#173d70}small{display:block;color:#64748b;font-size:7.5px}.cover{background:linear-gradient(135deg,#101a34,#245899 58%,#0f766e);color:#fff;border-radius:14px;padding:10mm}.cover .eyebrow{color:#99f6e4}.cover p{color:#dbeafe}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:2mm;margin-top:7mm}.metric{background:#ffffff1f;padding:3mm;border-radius:7px}.metric b{display:block;font-size:17px}.summary-grid{display:grid;grid-template-columns:1fr 1fr 2fr;gap:2mm;margin:5mm 0}.summary-grid>div,.panel{border:1px solid #dbe3ef;background:#f8fafc;border-radius:8px;padding:3mm}.summary-grid b{font-size:11px}.strategy{background:#ecfdf5;border-left:4px solid #10b981;padding:3mm;border-radius:6px}.chips{display:flex;gap:1mm;flex-wrap:wrap;margin:1mm 0 3mm}.chips span{background:#eef2ff;color:#3730a3;border-radius:999px;padding:.8mm 2mm}.chips.avoid span{background:#fff1f2;color:#9f1239}.principles{display:grid;grid-template-columns:repeat(3,1fr);gap:2mm;margin-top:5mm}.principles div{border:1px solid #dbe3ef;border-radius:7px;padding:3mm}.page-title{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid #173d70;padding-bottom:2mm;margin-bottom:3mm}.count{font-size:12px;font-weight:800;color:#173d70}.market-grid{display:grid;grid-template-columns:1.05fr .95fr;gap:3mm}.market-grid .panel{break-inside:avoid}.market-lead{font-size:11px;background:#eef6ff;border-left:4px solid #2563eb;padding:3mm;border-radius:6px;margin-bottom:3mm}.consistency{background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;border-radius:6px;padding:2.5mm;margin-bottom:3mm}p{margin:1.5mm 0}ul{margin:1mm 0;padding-left:5mm}li{margin:.7mm 0}table{width:100%;border-collapse:collapse}thead{display:table-header-group}th{background:#173d70;color:white;text-align:left;padding:1.5mm;font-size:7.5px}td{border-bottom:1px solid #e2e8f0;padding:1.35mm;vertical-align:top;font-size:7.6px}.decision-table td:first-child{width:27mm}.pill{display:inline-block;border-radius:999px;padding:.6mm 2mm;font-weight:800;white-space:nowrap}.pill-buy{background:#dcfce7;color:#166534}.pill-hold{background:#dbeafe;color:#1d4ed8}.pill-reduce{background:#fef3c7;color:#92400e}.pill-sell{background:#fee2e2;color:#991b1b}.pill-watch{background:#f1f5f9;color:#475569}.legend{display:flex;gap:2mm;margin-bottom:2mm;color:#64748b}.stock-page{font-size:8.6px}.headline{display:flex;justify-content:space-between;gap:4mm;background:#eef6ff;border-radius:7px;padding:2.5mm 3mm;margin-bottom:2.5mm}.headline b{color:#173d70;white-space:nowrap}.data-strip{display:grid;grid-template-columns:repeat(6,1fr);gap:1mm}.data-strip>div{background:#f8fafc;border:1px solid #e2e8f0;border-radius:5px;padding:1.4mm}.data-strip b{font-size:8px}.source-line{font-size:7px;color:#64748b;margin:1mm 0 2mm}.two-col,.evidence-grid{display:grid;grid-template-columns:1fr 1fr;gap:2mm;margin-bottom:2mm}.panel p{font-size:8.2px}.social{background:#eef2ff;border-radius:5px;padding:1.5mm;margin-top:1mm}.social ul{display:grid;grid-template-columns:1fr;gap:.4mm;margin:.6mm 0}.risk-box,.catalyst-box{border-radius:7px;padding:2.3mm}.risk-box{background:#fff1f2;color:#881337}.catalyst-box{background:#ecfdf5;color:#14532d}.latest{background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:2mm;margin-bottom:2mm}.plan{border:1px solid #cbd5e1;border-radius:7px;padding:2.5mm;margin-bottom:2mm}.plan-grid,.levels{display:grid;grid-template-columns:1fr 1fr;gap:1.5mm}.plan-grid>div,.levels>div{background:#f8fafc;border-radius:5px;padding:1.5mm}.levels{grid-template-columns:repeat(4,1fr);margin-top:1.5mm}.plan-foot{display:flex;justify-content:space-between;align-items:center;margin-top:1.5mm}.check{display:inline-block;background:#f1f5f9;border-radius:999px;padding:.4mm 1mm;margin-left:.5mm;font-size:7px}.check-⚠️,.check-❌{background:#fef3c7;color:#92400e}.review{border:1px solid #99f6e4;background:#f0fdfa;border-radius:7px;padding:2.3mm}.review-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:1mm}.review-grid>div{background:#fff;border-radius:5px;padding:1.2mm}.gate,.gap,.review-empty{margin-top:1.2mm;font-size:7.6px}.footnote{position:absolute;bottom:0;color:#94a3b8;font-size:6.8px}.watch-stack{display:grid;grid-template-rows:repeat(3,1fr);gap:3mm;height:250mm}.watch-card{border:1px solid #dbe3ef;border-left:4px solid #64748b;border-radius:8px;padding:3mm;overflow:hidden}.watch-head,.watch-foot,.watch-quote{display:flex;justify-content:space-between;gap:2mm}.watch-head b{font-size:12px;color:#173d70}.watch-quote{background:#f8fafc;padding:1.5mm;margin:1.5mm 0}.mini-grid{display:grid;grid-template-columns:1fr 1fr;gap:2mm}.mini-grid>div{border-radius:5px;padding:1.5mm;background:#fff1f2}.mini-grid>div+div{background:#ecfdf5}.mini-grid b{display:block}.watch-foot{margin-top:1.5mm}.abnormal{font-size:7.4px;color:#92400e;margin-top:1mm}.social-line{background:#eef2ff;padding:1mm;border-radius:4px}.quality-note{background:#f8fafc;border-left:4px solid #64748b;padding:2mm;margin-top:2mm}.empty{color:#64748b;padding:4mm}
"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{market_label}每日研究报告</title><style>{css}</style></head><body>
    <section class="page">
      <div class="cover"><div class="eyebrow">DAILY STOCK ANALYSIS · FORMAL REPORT</div><h1>{market_label}每日研究报告</h1><p>报告日 {_e(formal.report_date)}｜分析结果整合 + 质量/事件复核 + 执行条件｜预计约 {estimated_pages} 页</p>
      <div class="metrics"><div class="metric"><b>{len(stocks)}</b>股票</div><div class="metric"><b>{counts['买入']}</b>买入</div><div class="metric"><b>{counts['持有']}</b>持有</div><div class="metric"><b>{counts['减仓']}</b>减仓</div><div class="metric"><b>{counts['卖出']}</b>卖出</div><div class="metric"><b>{counts['观望']}</b>观望</div></div></div>
      <div class="summary-grid"><div><small>统一市场状态</small><b>{_e(effective_regime)}</b></div><div><small>当前主线</small><b>{_e(formal.main_theme)}</b></div><div><small>报告定位</small><b>保留高成本数据与证据，只删除重复结论和固定套话</b></div></div>
      {consistency}<div class="strategy"><b>今日策略：</b>{_e(formal.strategy)}</div>
      <h3 style="margin-top:4mm">重点关注</h3><div class="chips">{focus}</div><h3>不建议重仓</h3><div class="chips avoid">{avoid}</div>
      <div class="principles"><div><b>量价不丢</b><p>长桥实时字段与历史相对成交量分开标注，保留来源和时间口径。</p></div><div><b>研究不丢</b><p>板块网页研究、事件证据、美股社媒舆情继续进入决策卡。</p></div><div><b>结论唯一</b><p>分析动作与质量复核分层展示；发生冲突时明确标注，不静默覆盖。</p></div></div>
    </section>

    <section class="page"><div class="page-title"><div><span class="eyebrow">市场与板块研究</span><h2>大盘、风格与风险环境</h2></div><span class="count">分析阶段研究摘要</span></div>
      <div class="market-lead">{_e(market_research.one_sentence)}</div>{_index_table(market_research)}
      <div class="market-grid" style="margin-top:3mm"><div class="panel"><h3>指数结构</h3><p>{_e(market_research.structure)}</p></div><div class="panel"><h3>板块主线（网页研究保留）</h3><p>{_e(market_research.sectors)}</p></div><div class="panel"><h3>资金与情绪</h3><p>{_e(market_research.flow_sentiment)}</p></div><div class="panel"><h3>消息催化</h3><p>{_e(market_research.catalysts)}</p></div><div class="panel"><h3>下一交易日计划</h3><p>{_e(market_research.plan)}</p></div><div class="panel"><h3>主要市场风险</h3><ul>{risks}</ul></div>{ai_block}</div>
    </section>

    <section class="page"><div class="page-title"><div><span class="eyebrow">决策总表</span><h2>动作、量价与质量复核</h2></div><span class="count">{len(stocks)} 只</span></div>
      <div class="legend"><span>相对量＝当日成交量/历史均量口径</span><span>｜</span><span>“待复核”表示官方事件或质量校验尚未覆盖</span></div>
      <table class="decision-table"><thead><tr><th>股票</th><th>分析动作</th><th>分数</th><th>方向</th><th>现价/涨跌</th><th>相对量</th><th>换手</th><th>字段完整</th><th>质量复核</th></tr></thead><tbody>{_decision_rows(stocks)}</tbody></table>
      <div class="quality-note"><b>阅读说明：</b>报告分层显示分析结论与质量复核，避免把事件闸门、完整度或盈亏比校验前后的动作混为一个结论。量比缺失时优先展示数据库中同日相对成交量，并标明计算来源。</div>
    </section>
    {active_pages}{watch_pages}</body></html>"""


def render_report(
    input_md: Path,
    db_path: Path,
    output_html: Path,
    output_pdf: Path,
    market: str,
    review_path: Path | None = DEFAULT_REVIEW,
) -> dict[str, Any]:
    from weasyprint import HTML

    formal = parse_report(input_md, market=market)
    report_text = input_md.read_text(encoding="utf-8")
    market_research = parse_market_research(report_text)
    stocks = load_stocks(db_path, formal.report_date, market, review_path)
    document = render_html(formal, market_research, stocks)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(document, encoding="utf-8")
    HTML(string=document, base_url=str(output_html.parent)).write_pdf(str(output_pdf))
    return {
        "market": market,
        "report_date": formal.report_date,
        "stocks": len(stocks),
        "active": sum(item.action != "观望" for item in stocks),
        "watch": sum(item.action == "观望" for item in stocks),
        "planned_pages": 3 + sum(item.action != "观望" for item in stocks) + math.ceil(sum(item.action == "观望" for item in stocks) / 3),
        "social_stocks": sum(bool(item.social_lines) for item in stocks),
        "relative_volume_stocks": sum(item.relative_volume is not None for item in stocks),
        "reviewed": sum(bool(item.review) for item in stocks),
        "html": str(output_html),
        "pdf": str(output_pdf),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the formal daily research report")
    parser.add_argument("--input-md", type=Path, required=True)
    parser.add_argument("--market", choices=("hk", "us"), required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output-html", type=Path)
    parser.add_argument("--output-pdf", type=Path)
    args = parser.parse_args()
    date_match = re.search(r"(\d{8})", args.input_md.stem)
    date_token = date_match.group(1) if date_match else "latest"
    stem = f"daily_research_{args.market}_{date_token}"
    output_html = args.output_html or REPORTING_ROOT / "output" / f"{stem}.html"
    output_pdf = args.output_pdf or REPORTING_ROOT / "output" / f"{stem}.pdf"
    result = render_report(args.input_md, args.db, output_html, output_pdf, args.market, args.review)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
