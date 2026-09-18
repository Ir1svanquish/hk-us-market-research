"""Render the isolated unified-event and macro-scenario V2 PDF."""

from __future__ import annotations

import argparse
import html
import json
from datetime import date
from pathlib import Path
from typing import Mapping

from weasyprint import HTML

from .macro_catalog import (
    macro_market_score,
    translate_macro_title,
)


V2_ROOT = Path(__file__).resolve().parents[1]

GATE_LABELS = {
    "info_only": "仅供参考",
    "monitor": "事件观察",
    "reassess": "重新评估",
    "conditional_only": "仅条件观察",
    "block_new_positions": "禁止新开仓/加仓",
    "risk_off": "风险关闭",
}

EVENT_LABELS = {
    "nonfarm_payrolls": "美国非农就业",
    "cpi": "美国 CPI",
    "ppi": "美国 PPI",
}

SOURCE_LABELS = {
    "hkex_board_calendar": "HKEX 董事会日历",
    "hkexnews": "HKEXnews",
    "sec_edgar": "SEC EDGAR",
    "finnhub": "Finnhub 日历",
    "bls": "美国劳工统计局 BLS",
    "nasdaq_macro_calendar": "Nasdaq 宏观日历",
}

IMPORTANCE_LABELS = {"high": "高影响", "medium": "中影响", "low": "低影响"}


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _days(effective_at: object, as_of: date) -> int | None:
    try:
        return (date.fromisoformat(str(effective_at)[:10]) - as_of).days
    except ValueError:
        return None


def _event_lines(item: Mapping[str, object]) -> str:
    events = item.get("triggered_events") if isinstance(item.get("triggered_events"), list) else []
    if not events:
        return ""
    labels = {
        "earnings_calendar": "财报日期临近",
        "capital_dilution": "融资/摊薄待核验",
        "financial_results": "正式业绩已发布",
        "trading_status": "交易状态异常",
        "critical_corporate_event": "重大公司事件",
    }
    rows = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        rows.append(
            '<span class="trigger">%s · %s</span>'
            % (
                _e(labels.get(event_type, "事件触发")),
                _e(f"有效至 {event.get('gate_until')}" if event.get("gate_until") else "持续跟踪"),
            )
        )
    return '<div class="trigger-row">' + " ｜ ".join(rows) + "</div>"


def _gate_cards(payload: Mapping[str, object]) -> str:
    quality = payload.get("decision_quality") if isinstance(payload.get("decision_quality"), dict) else {}
    if quality:
        cards = []
        for symbol, item in sorted(
            quality.items(),
            key=lambda pair: (
                -int(pair[1].get("final_gate", {}).get("priority", 0)),
                int(pair[1].get("data_completeness", 0)),
                pair[0],
            ),
        ):
            final_gate = item.get("final_gate") if isinstance(item.get("final_gate"), dict) else {}
            action = str(final_gate.get("gate_action") or "info_only")
            gaps = item.get("major_gaps") if isinstance(item.get("major_gaps"), list) else []
            gap_text = "、".join(str(value) for value in gaps[:2]) or "无明显数据缺口"
            reason = str(final_gate.get("reason") or "")
            cards.append(
                f"""<div class="quality-card gate-{_e(action)}">
                  <div class="quality-head"><b class="ticker">{_e(symbol)}</b><span class="quality-action">{_e(item.get('final_action'))}</span></div>
                  <div class="score-row"><span>数据完整度 <b>{_e(item.get('data_completeness'))}</b></span><span>信号置信度 <b>{_e(item.get('signal_confidence'))}</b></span></div>
                  <div class="quality-gap">主要缺口：{_e(gap_text)}</div>
                  {f'<div class="quality-reason">{_e(reason)}</div>' if int(final_gate.get('priority') or 0) >= 3 else ''}
                </div>"""
            )
        return '<div class="quality-grid">' + "".join(cards) + "</div>"
    gates = payload.get("symbol_gates") if isinstance(payload.get("symbol_gates"), dict) else {}
    material = [
        (symbol, item)
        for symbol, item in gates.items()
        if isinstance(item, dict) and int(item.get("priority") or 0) >= 2
    ]
    material.sort(key=lambda pair: (-int(pair[1].get("priority") or 0), pair[0]))
    cards = []
    for symbol, item in material:
        action = str(item.get("gate_action") or "info_only")
        cards.append(
            f"""<div class="gate-card gate-{_e(action)}">
              <div class="gate-head"><b class="ticker">{_e(symbol)}</b><span>{_e(GATE_LABELS.get(action, action))}</span></div>
              <div class="gate-reason">{_e(item.get('reason'))}</div>
              {_event_lines(item)}
            </div>"""
        )
    return "".join(cards) or '<div class="empty">本轮没有触发重新评估或更高等级的个股闸门。</div>'


def _number(value: object, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.3f}".rstrip("0").rstrip(".") + suffix


def _trade_cards(payload: Mapping[str, object]) -> str:
    cards = payload.get("trading_cards") if isinstance(payload.get("trading_cards"), dict) else {}
    rows = []
    for symbol, card in sorted(
        cards.items(),
        key=lambda pair: (
            0 if pair[1].get("active") else 1,
            pair[0],
        ),
    ):
        status = str(card.get("status") or "watch")
        volume = card.get("volume_confirmation") if isinstance(card.get("volume_confirmation"), dict) else {}
        metric_line = (
            f"量比 {_number(card.get('volume_ratio_live'), '×')}｜"
            f"5日相对量 {_number(card.get('relative_volume_5d'), '×')}｜"
            f"ATR14 {_number(card.get('atr14'))}"
        )
        if card.get("active"):
            body = f"""<div class="trade-trigger">{_e(card.get('lifecycle_message') or card.get('trigger_condition'))}</div>
              <div class="trade-levels"><span>止损 <b>{_number(card.get('stop_loss'))}</b></span><span>目标1 <b>{_number(card.get('target_1'))}</b></span><span>目标2 <b>{_number(card.get('target_2'))}</b></span><span>盈亏比 <b>1:{_number(card.get('risk_reward'))}</b></span></div>
              <div class="trade-foot">有效至 {_e(card.get('expires_at'))}｜{_e(card.get('gap_rule'))}</div>"""
        elif status == "holding":
            body = f"""<div class="trade-trigger">持仓管理，不新增主动买点</div>
              <div class="trade-levels"><span>参考止损 <b>{_number(card.get('stop_loss'))}</b></span><span>目标1 <b>{_number(card.get('target_1'))}</b></span><span>目标2 <b>{_number(card.get('target_2'))}</b></span><span>盈亏比 <b>1:{_number(card.get('risk_reward'))}</b></span></div>"""
        else:
            invalidation = card.get("invalidation_conditions") if isinstance(card.get("invalidation_conditions"), list) else []
            concise_reason = str(
                card.get("lifecycle_message")
                or (invalidation[0] if invalidation else "当前条件不满足，不启用主动买点")
            )
            body = f'<div class="trade-disabled">{_e(concise_reason)}</div>'
        rows.append(
            f"""<div class="trade-card status-{_e(status)}">
              <div class="trade-head"><div><b>{_e(symbol)}</b><small>{_e(card.get('name'))}</small></div><span>{_e(card.get('label'))}</span></div>
              <div class="trade-metrics">{_e(metric_line)}｜{_e(volume.get('rule'))}</div>{body}
            </div>"""
        )
    return '<div class="trade-grid">' + "".join(rows) + "</div>" if rows else '<div class="empty">暂无标准化交易卡。</div>'


def _scenario_card(group: list, as_of: date) -> str:
    event = group[0]
    metadata = event.get("metadata", {}) if isinstance(event.get("metadata"), dict) else {}
    scenarios = metadata.get("scenarios", [])
    rows = []
    for scenario in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(scenario, dict):
            continue
        positive = _short_sectors(scenario.get("positive_sectors", []))
        negative = _short_sectors(scenario.get("negative_sectors", []))
        rows.append(
            f"""<div class="scenario-row"><span class="probability">{_e(scenario.get('probability_pct'))}%</span>
              <div><b>{_e(scenario.get('name'))}：</b><span>{_e(scenario.get('broad_market'))}</span>
              <small>受益：{_e(positive)}　｜　承压：{_e(negative)}</small></div></div>"""
        )
    event_date = str(event.get("effective_at") or "")[:10]
    days = _days(event.get("effective_at"), as_of)
    release_time = metadata.get("release_time") or "时间待定"
    chinese_titles = _group_title(group)
    first_scenario = scenarios[0] if isinstance(scenarios, list) and scenarios else {}
    sample_size = (
        first_scenario.get("probability_sample_size", 0)
        if isinstance(first_scenario, dict)
        else 0
    )
    sample_label = f"历史样本 n={sample_size}" if sample_size else "中性先验"
    return f"""<section class="macro-card">
      <div class="macro-head"><div><h2>{_e(chinese_titles or EVENT_LABELS.get(str(event.get('event_type')), event.get('event_type')))}</h2><small class="sample">{_e(sample_label)}</small></div>
      <div class="date-box"><b>{_e(event_date)}</b><span>{_e(release_time)} · 距报告日 {_e(days)} 天</span></div></div>
      <div class="scenarios">{''.join(rows)}</div>
    </section>"""


def _released_card(group: list) -> str:
    group = _released_display_items(group)
    event = group[0]
    metadata = event.get("metadata", {}) if isinstance(event.get("metadata"), dict) else {}
    value_rows = "".join(
        f"""<div class="released-value-row"><b>{_e(translate_macro_title(str(item.get('headline') or '')))}</b>
        <span>{_e(item.get('metadata', {}).get('actual') or '—')}</span>
        <span>{_e(item.get('metadata', {}).get('estimate') or '—')}</span>
        <span>{_e(item.get('metadata', {}).get('previous') or '—')}</span></div>"""
        for item in group
    )
    chinese_titles = _group_title(group)
    return f"""<div class="released-card">
      <div class="released-head"><div><h3>{_e(chinese_titles)}</h3></div>
      <b>{_e(str(event.get('effective_at'))[:10])}</b></div>
      <div class="released-table-head"><b>指标</b><span>实际</span><span>预期</span><span>前值</span></div>{value_rows}
      <div class="takeaway">{_e(_released_group_takeaway(group))}</div>
    </div>"""


def _calendar_html(events: list, as_of: date) -> str:
    grouped = {}
    for event in events:
        grouped.setdefault(str(event.get("effective_at") or "")[:10], []).append(event)
    sections = []
    for event_date in sorted(grouped):
        rows = []
        day_events = sorted(grouped[event_date], key=lambda item: str(item.get("effective_at")))
        low_events = [
            item for item in day_events
            if item.get("metadata", {}).get("importance") == "low"
        ]
        shown_events = [item for item in day_events if item not in low_events]
        for event in shown_events:
            metadata = event.get("metadata", {}) if isinstance(event.get("metadata"), dict) else {}
            importance = str(metadata.get("importance") or "medium")
            analysis = metadata.get("analysis", {}) if isinstance(metadata.get("analysis"), dict) else {}
            values = ""
            if analysis.get("status") == "released":
                values = (
                    f'<div class="calendar-result">实际 {_e(metadata.get("actual") or "—")}｜'
                    f'预期 {_e(metadata.get("estimate") or "—")}｜前值 {_e(metadata.get("previous") or "—")}</div>'
                )
            rows.append(
                f"""<div class="calendar-row"><span class="importance importance-{_e(importance)}">{_e(IMPORTANCE_LABELS.get(importance, importance))}</span>
                <span class="calendar-time">{_e(metadata.get('release_time') or '')}</span><b>{_e(event.get('headline'))}</b>{values}</div>"""
            )
        day_count = _days(event_date, as_of)
        day_label = "报告日" if day_count == 0 else f"{abs(day_count)} 天前" if day_count and day_count < 0 else f"{day_count} 天后"
        low_summary = ""
        if low_events:
            titles = "、".join(str(item.get("headline") or "") for item in low_events[:8])
            suffix = "等" if len(low_events) > 8 else ""
            low_summary = (
                f'<div class="low-summary">另有 {len(low_events)} 项低影响数据已纳入上下文并折叠：'
                f'{_e(titles)}{suffix}</div>'
            )
        sections.append(
            f'<div class="calendar-day"><h3>{_e(event_date)} <span>{_e(day_label)}</span></h3>{"".join(rows)}{low_summary}</div>'
        )
    return "".join(sections) or '<div class="empty">未来 7 天及近期没有取得美国宏观事件。</div>'


def _group_macro(events: list) -> list:
    grouped = {}
    for event in events:
        metadata = event.get("metadata", {}) if isinstance(event.get("metadata"), dict) else {}
        key = (
            str(event.get("effective_at") or "")[:10],
            str(metadata.get("release_time") or ""),
            str(metadata.get("category") or "other"),
        )
        grouped.setdefault(key, []).append(event)
    return [grouped[key] for key in sorted(grouped)]


def _english_titles(group: list, limit: int = 3) -> str:
    titles = list(dict.fromkeys(str(item.get("headline") or "") for item in group))
    suffix = " / …" if len(titles) > limit else ""
    return " / ".join(titles[:limit]) + suffix


def _short_sectors(values: object, limit: int = 2) -> str:
    items = [str(item) for item in values] if isinstance(values, list) else []
    return "、".join(items[:limit]) + ("等" if len(items) > limit else "")


def _group_title(group: list) -> str:
    titles = " ".join(str(item.get("headline") or "").lower() for item in group)
    if "pce" in titles:
        return "PCE 通胀数据"
    if "gdp" in titles:
        return "GDP、耐用品订单与个人收支"
    if "s&p global" in titles and "pmi" in titles:
        return "标普全球 PMI 初值"
    if "jobless" in titles:
        return "每周失业救济申请"
    if "consumer confidence" in titles:
        return "美国消费者信心指数"
    if "phil" in titles:
        return "费城联储制造业调查"
    category = str(group[0].get("metadata", {}).get("category") or "")
    if category == "energy_inventory":
        return "美国能源库存"
    translated = list(
        dict.fromkeys(translate_macro_title(str(item.get("headline") or "")) for item in group)
    )
    suffix = f"等 {len(translated)} 项" if len(translated) > 3 else ""
    return " / ".join(translated[:3]) + suffix


def _released_display_items(group: list) -> list:
    category = str(group[0].get("metadata", {}).get("category") or "")
    if len(group) <= 3:
        return group
    if category == "survey":
        patterns = ("manufacturing index", "new orders", "prices paid")
    elif category == "energy_inventory":
        patterns = ("crude oil inventories", "distillates stocks", "gasoline inventories")
    else:
        return group[:3]
    selected = []
    for pattern in patterns:
        match = next(
            (item for item in group if pattern in str(item.get("headline") or "").lower()), None
        )
        if match is not None and match not in selected:
            selected.append(match)
    return selected or group[:3]


def _released_group_takeaway(group: list) -> str:
    titles = " ".join(str(item.get("headline") or "").lower() for item in group)

    def comparison(fragment: str) -> str:
        match = next(
            (item for item in group if fragment in str(item.get("headline") or "").lower()),
            None,
        )
        return str(match.get("metadata", {}).get("analysis", {}).get("comparison") or "") if match else ""

    if "jobless" in titles:
        initial = comparison("initial jobless")
        continuing = comparison("continuing jobless")
        if initial == "below" and continuing == "above":
            return "初请低于预期，说明新增裁员压力不大；但续请偏高，显示再就业略弱，整体属于就业仍稳但边际分化。"
        if initial == "below":
            return "初请低于预期，就业市场偏稳，利好周期板块，但可能令降息预期后移。"
        if initial == "above":
            return "初请高于预期，就业降温信号增强；利率可能回落，但周期和消费板块需防增长担忧。"
    if "phil" in titles:
        headline = comparison("manufacturing index")
        if headline == "above":
            return "总指数明显强于预期，工业景气偏正面；但新订单和价格分项未同步走强，不能解读为全面升温。"
        if headline == "below":
            return "总指数弱于预期，制造业动能偏弱；需结合新订单和就业分项判断是否继续恶化。"
    if "crude oil" in titles:
        crude = comparison("crude oil inventories")
        gasoline = comparison("gasoline inventories")
        if crude == "above" or gasoline == "above":
            return "原油或汽油库存高于预期，短线偏压制油价与能源股；馏分油去库可抵消部分压力。"
        return "库存整体低于预期，偏利好油价与上游能源股，但会增加运输和通胀端成本压力。"
    comparisons = {
        str(item.get("metadata", {}).get("analysis", {}).get("comparison") or "")
        for item in group
    }
    if comparisons == {"above"}:
        return "主要分项均高于预期，按该指标的常规传导方向处理。"
    if comparisons == {"below"}:
        return "主要分项均低于预期，按该指标的反向传导方向处理。"
    return "分项信号不一致，维持中性判断，等待市场价格与后续数据确认。"


def _group_score(group: list) -> int:
    return max(
        macro_market_score(
            str(item.get("event_type") or ""),
            str(item.get("metadata", {}).get("category") or "other"),
        )
        for item in group
    )


def _select_groups(groups: list, limit: int) -> list:
    ranked = sorted(
        groups,
        key=lambda group: (
            -_group_score(group),
            str(group[0].get("effective_at") or ""),
        ),
    )
    return ranked[: max(0, limit)]


def render(payload: Mapping[str, object], output_html: Path, output_pdf: Path) -> dict:
    as_of = date.fromisoformat(str(payload.get("as_of")))
    totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
    gates = payload.get("symbol_gates") if isinstance(payload.get("symbol_gates"), dict) else {}
    quality = payload.get("decision_quality") if isinstance(payload.get("decision_quality"), dict) else {}
    material_count = (
        sum(
            isinstance(item, dict)
            and int(item.get("final_gate", {}).get("priority") or 0) >= 3
            for item in quality.values()
        )
        if quality
        else sum(
            isinstance(item, dict) and int(item.get("priority") or 0) >= 2
            for item in gates.values()
        )
    )
    macro = [item for item in payload.get("macro_events", []) if isinstance(item, dict)] if isinstance(payload.get("macro_events"), list) else []
    visible = [item for item in macro if (days := _days(item.get("effective_at"), as_of)) is not None and -1 <= days <= 7]
    upcoming = [item for item in visible if (_days(item.get("effective_at"), as_of) or 0) >= 0]
    released = [
        item for item in visible
        if item.get("metadata", {}).get("analysis", {}).get("status") == "released"
        and item.get("metadata", {}).get("importance") in {"high", "medium"}
    ]
    scenario_events = [
        item for item in upcoming
        if item.get("metadata", {}).get("importance") == "high"
        and item.get("metadata", {}).get("analysis", {}).get("status") != "released"
    ]
    released_groups = _select_groups(_group_macro(released), 2)
    scenario_groups = _select_groups(_group_macro(scenario_events), 4)
    trading_cards = payload.get("trading_cards") if isinstance(payload.get("trading_cards"), dict) else {}
    ready_count = sum(
        isinstance(item, dict) and item.get("active") is True
        for item in trading_cards.values()
    )
    document = _document(
        as_of=as_of,
        totals=totals,
        material_count=material_count,
        gate_cards=_gate_cards(payload),
        macro_count=len(visible),
        selected_count=len(released_groups) + len(scenario_groups),
        ready_count=ready_count,
        trade_cards=_trade_cards(payload),
        released_cards="".join(_released_card(group) for group in released_groups),
        scenario_cards="".join(_scenario_card(group, as_of) for group in scenario_groups),
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(document, encoding="utf-8")
    HTML(string=document, base_url=str(output_html.parent)).write_pdf(str(output_pdf))
    return {
        "events": totals.get("events", 0),
        "material_symbol_gates": material_count,
        "standard_trade_cards": len(trading_cards),
        "ready_trade_cards": ready_count,
        "macro_events_in_report_window": len(visible),
        "released_impact_analysis_groups_available": len(_group_macro(released)),
        "released_impact_analysis_groups_selected": len(released_groups),
        "upcoming_high_impact_scenario_groups_available": len(_group_macro(scenario_events)),
        "upcoming_high_impact_scenario_groups_selected": len(scenario_groups),
        "pdf": str(output_pdf),
    }


def _document(*, as_of: date, totals: Mapping[str, object], material_count: int, gate_cards: str, macro_count: int, selected_count: int, ready_count: int, trade_cards: str, released_cards: str, scenario_cards: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>V2 统一事件闸门与宏观场景</title><style>
@page {{size:A4;margin:15mm 14mm 18mm;@bottom-right{{content:"第 " counter(page) " 页";font-size:9px;color:#64748b}}}}
*{{box-sizing:border-box}}body{{margin:0;color:#172033;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;font-size:10px;line-height:1.55}}
.cover{{padding:10mm 9mm;color:white;border-radius:16px;background:linear-gradient(135deg,#111a35,#204f8e 58%,#087d77);margin-bottom:7mm}}.eyebrow{{color:#9fe6df;font-weight:800;letter-spacing:1.4px;font-size:9px}}h1{{font-size:25px;line-height:1.15;margin:4mm 0 2mm}}.subtitle{{color:#dbeafe;font-size:11px}}.metrics{{display:flex;gap:3mm;margin-top:6mm}}.metric{{flex:1;background:rgba(255,255,255,.11);border-radius:8px;padding:3mm}}.metric b{{display:block;font-size:19px}}
.note{{padding:4mm 5mm;background:#fff8e7;border-left:4px solid #f59e0b;border-radius:5px;margin-bottom:6mm}}h2.section{{font-size:18px;color:#173d70;border-bottom:2px solid #173d70;padding-bottom:2mm;margin:7mm 0 4mm;break-after:avoid}}
.gate-card{{border:1px solid #dbe3ef;border-left:5px solid #f59e0b;border-radius:9px;padding:3mm 4mm;margin:2.5mm 0;page-break-inside:avoid}}.gate-reassess{{border-left-color:#3b82f6}}.gate-block_new_positions,.gate-risk_off{{border-left-color:#dc2626}}.gate-head{{display:flex;align-items:center;gap:3mm;margin-bottom:1mm}}.gate-head span{{background:#fff7ed;color:#9a3412;font-weight:800;border-radius:999px;padding:.8mm 2.5mm}}.ticker{{font-size:16px;color:#173d70}}.gate-reason{{font-weight:700}}.trigger-row{{color:#64748b;font-size:8.5px;margin-top:1mm}}.trigger{{white-space:nowrap}}
.quality-grid{{display:grid;grid-template-columns:1fr 1fr;gap:2.5mm}}.quality-card{{border:1px solid #dbe3ef;border-left:4px solid #10b981;border-radius:8px;padding:2.5mm 3mm;break-inside:avoid}}.quality-card.gate-conditional_only{{border-left-color:#f59e0b}}.quality-card.gate-block_new_positions,.quality-card.gate-risk_off{{border-left-color:#dc2626}}.quality-head{{display:flex;justify-content:space-between;align-items:center}}.quality-head .ticker{{font-size:13px}}.quality-action{{font-weight:800;color:#173d70}}.score-row{{display:flex;gap:2mm;margin:1mm 0}}.score-row span{{flex:1;background:#f1f5f9;border-radius:5px;padding:1mm 1.5mm;color:#475569}}.score-row b{{font-size:12px;color:#172033}}.quality-gap{{font-size:8.5px;color:#64748b}}.quality-reason{{font-size:8.5px;color:#92400e;margin-top:.8mm}}
.trade-grid{{display:grid;grid-template-columns:1fr 1fr;gap:2.5mm}}.trade-card{{border:1px solid #dbe3ef;border-left:4px solid #10b981;border-radius:9px;padding:2.5mm 3mm;break-inside:avoid}}.trade-card:not(.status-ready):not(.status-holding){{border-left-color:#f59e0b}}.trade-card.status-blocked,.trade-card.status-data_conflict,.trade-card.status-invalid_structure,.trade-card.status-invalid_risk_reward{{border-left-color:#dc2626}}.trade-head{{display:flex;justify-content:space-between;gap:2mm;align-items:start}}.trade-head b{{font-size:13px;color:#173d70}}.trade-head small{{display:block;color:#64748b;font-size:8px}}.trade-head span{{background:#eef2ff;color:#3730a3;border-radius:999px;padding:.7mm 2mm;font-weight:800;font-size:8px;white-space:nowrap}}.trade-metrics{{font-size:8px;color:#64748b;margin:1mm 0}}.trade-trigger{{font-weight:800;color:#172033;background:#ecfdf5;border-radius:5px;padding:1.3mm 2mm}}.trade-levels{{display:grid;grid-template-columns:1fr 1fr;gap:.7mm 2mm;margin-top:1mm}}.trade-levels span{{font-size:8.5px}}.trade-levels b{{color:#173d70}}.trade-foot,.trade-disabled{{font-size:8px;color:#92400e;margin-top:1mm}}.trade-disabled{{background:#fff7ed;border-radius:5px;padding:1.3mm 2mm}}
.macro-card{{break-inside:avoid;border:1px solid #dbe3ef;border-radius:9px;padding:3mm;margin:3mm 0}}.macro-head{{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #173d70;padding-bottom:2mm}}.macro-head h2{{font-size:15px;margin:.5mm 0 0}}.sample{{color:#64748b;font-size:8px}}.macro-label{{color:#b91c1c;font-weight:800;font-size:8.5px}}.date-box{{color:#7f1d1d;background:#fef2f2;border:1px solid #ef4444;border-radius:8px;padding:2mm 3mm;text-align:center;min-width:37mm}}.date-box b{{display:block;font-size:14px}}.date-box span{{font-size:8px}}.scenarios{{display:grid;gap:1mm;margin-top:2mm}}.scenario-row{{display:grid;grid-template-columns:13mm 1fr;gap:2mm;border-bottom:1px solid #e2e8f0;padding:1.5mm 0;break-inside:avoid}}.scenario-row:last-child{{border-bottom:0}}.probability{{display:inline-block;background:#e0e7ff;color:#3730a3;border-radius:999px;text-align:center;font-size:11px;font-weight:900;padding:1mm}}.scenario-row b{{display:inline-block;margin-right:2mm;color:#173d70}}.scenario-row span{{font-size:9px}}.scenario-row small{{display:block;color:#64748b;margin-top:.5mm}}
.calendar-day{{margin:3mm 0 5mm}}.calendar-day h3{{color:#173d70;border-bottom:1px solid #cbd5e1;padding-bottom:1mm;margin:0 0 2mm;font-size:13px;break-after:avoid}}.calendar-day h3 span{{font-size:9px;color:#64748b;margin-left:2mm}}.calendar-row{{display:grid;grid-template-columns:19mm 26mm 1fr;gap:2mm;border-bottom:1px solid #edf2f7;padding:1.5mm 0;align-items:start;break-inside:avoid}}.calendar-time{{color:#475569;font-size:9px}}.importance{{font-size:8px;font-weight:800;border-radius:999px;text-align:center;padding:.5mm 1mm}}.importance-high{{background:#fee2e2;color:#991b1b}}.importance-medium{{background:#fef3c7;color:#92400e}}.importance-low{{background:#e2e8f0;color:#475569}}.calendar-result{{grid-column:3;color:#475569;font-size:9px}}.low-summary{{color:#64748b;background:#f8fafc;border-radius:6px;padding:2mm 3mm;margin-top:1mm;font-size:8.5px}}
.released-card{{border:1px solid #bfdbfe;border-left:4px solid #2563eb;border-radius:8px;padding:3mm;margin:2mm 0;page-break-inside:avoid}}.released-head{{display:flex;justify-content:space-between;align-items:center}}.released-head h3{{font-size:12px;margin:.5mm 0}}.released-label{{color:#1d4ed8;font-weight:800;font-size:8.5px}}.released-table-head,.released-value-row{{display:grid;grid-template-columns:1.6fr .55fr .55fr .55fr;gap:2mm;align-items:start}}.released-table-head{{background:#dbeafe;padding:1mm 2mm;margin-top:1.5mm}}.released-value-row{{padding:1mm 2mm;border-bottom:1px solid #dbeafe}}.takeaway{{background:#ecfdf5;border-left:3px solid #10b981;padding:2mm 3mm;margin-top:1.5mm;font-weight:700}}.empty{{color:#64748b;padding:4mm;background:#f8fafc;border-radius:8px}}
</style></head><body>
<div class="cover"><div class="eyebrow">DAILY STOCK ANALYSIS · ISOLATED V2</div><h1>统一事件闸门与交易卡</h1><div class="subtitle">快照日 {_e(as_of)}｜适合嵌入正式日报的精简版</div><div class="metrics"><div class="metric"><b>{_e(totals.get('events'))}</b>统一事件</div><div class="metric"><b>{material_count}</b>只股票实质降档</div><div class="metric"><b>{ready_count}</b>张可执行卡</div><div class="metric"><b>{selected_count}</b>组宏观重点</div></div></div>
<div class="note">后台保留窗口内全部 {macro_count} 项数据；前台仅显示刚公布 2 组和未来一周 4 组。情景概率按过去 180 天同类数据相对预期的结果平滑计算；卡片标注有效样本数。</div>
<h2 class="section">个股硬性事件闸门</h2>{gate_cards}
<h2 class="section">标准化交易卡</h2>{trade_cards}
<h2 class="section">刚公布</h2>{released_cards or '<div class="empty">暂无重点更新。</div>'}
<h2 class="section">未来 7 天情景</h2>{scenario_cards or '<div class="empty">未来一周暂无重点数据。</div>'}
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render isolated V2 unified event PDF")
    parser.add_argument("--context-json", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, default=V2_ROOT / "output" / "event_context_v2.html")
    parser.add_argument("--output-pdf", type=Path, default=V2_ROOT / "output" / "event_context_v2.pdf")
    args = parser.parse_args()
    payload = json.loads(args.context_json.read_text(encoding="utf-8"))
    print(json.dumps(render(payload, args.output_html, args.output_pdf), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
