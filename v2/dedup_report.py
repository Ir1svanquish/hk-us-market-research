"""Render an isolated, deduplicated view of the existing formal report.

The production markdown remains the source of truth.  This module only parses
that already-produced artifact and creates a shorter V2 review copy; it does
not change the formal HK/US pipeline, schedules, notifications, or state.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parent

STOCK_HEADER_RE = re.compile(
    r"^##\s+[🟢🟡⚪🟠🔴]\ufe0f?\s+(.+?)\s+\(([^()\n]+)\)\s*$",
    re.MULTILINE,
)

ACTION_RE = re.compile(
    r"\*\*[^*\n]*(买入|持有|观望|减仓(?:/观望)?|卖出)[^*\n]*\*\*\s*\|\s*([^\n]+)"
)

ACTION_PRIORITY = {"卖出": 0, "减仓": 1, "买入": 2, "持有": 3, "观望": 4}
ACTION_CLASS = {
    "买入": "buy",
    "持有": "hold",
    "减仓": "reduce",
    "卖出": "sell",
    "观望": "watch",
}


@dataclass
class StockDecision:
    name: str
    symbol: str
    action: str
    bias: str = ""
    decision: str = ""
    validity: str = ""
    current_price: str = ""
    day_change: str = ""
    volume_ratio: str = ""
    turnover_rate: str = ""
    quote_source: str = ""
    empty_advice: str = ""
    holder_advice: str = ""
    entry: str = ""
    alternate_entry: str = ""
    stop: str = ""
    target: str = ""
    position: str = ""
    risk: str = ""
    catalyst: str = ""
    checklist: list[dict[str, str]] = field(default_factory=list)


@dataclass
class DedupReport:
    report_date: str
    market: str
    market_status: str
    main_theme: str
    strategy: str
    focus_names: list[str]
    avoid_names: list[str]
    stocks: list[StockDecision]
    source: str


def _clean(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"[*_`#]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" |\n\t")
    return text


def _line_value(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}[：:]\s*(.+)$", text, re.MULTILINE)
    return _clean(match.group(1)) if match else ""


def _paragraph_value(text: str, label: str) -> str:
    match = re.search(
        rf"^{re.escape(label)}[：:]\s*(?:\n\s*)?([^\n#]+)", text, re.MULTILINE
    )
    return _clean(match.group(1)) if match else ""


def _list_between(text: str, start_label: str, end_label: str) -> list[str]:
    match = re.search(
        rf"{re.escape(start_label)}[：:]\s*\n(?P<body>.*?)(?=\n{re.escape(end_label)}[：:]|\n#|\Z)",
        text,
        re.DOTALL,
    )
    if not match:
        return []
    return [
        _clean(line[1:])
        for line in match.group("body").splitlines()
        if line.strip().startswith("*") and _clean(line[1:])
    ]


def _table_rows(block: str, required_headers: tuple[str, ...]) -> list[dict[str, str]]:
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        headers = [_clean(cell) for cell in line.strip().strip("|").split("|")]
        if not all(any(required in header for header in headers) for required in required_headers):
            continue
        rows: list[dict[str, str]] = []
        for candidate in lines[index + 2 :]:
            if not candidate.strip().startswith("|"):
                break
            values = [_clean(cell) for cell in candidate.strip().strip("|").split("|")]
            if len(values) != len(headers):
                continue
            rows.append(dict(zip(headers, values)))
        return rows
    return []


def _row_value(row: dict[str, str], contains: str) -> str:
    for key, value in row.items():
        if contains in key:
            return value
    return ""


def _first_bullet(block: str, heading: str, next_heading: str) -> str:
    match = re.search(
        rf"{re.escape(heading)}[：:]\s*(?P<body>.*?)(?={re.escape(next_heading)}|\n###|\Z)",
        block,
        re.DOTALL,
    )
    if not match:
        return ""
    for line in match.group("body").splitlines():
        if line.strip().startswith("-"):
            return _clean(line.strip()[1:])
    return ""


def _first_sentences(value: str, count: int = 2) -> str:
    text = _clean(value)
    if not text:
        return ""
    parts = [part for part in re.split(r"(?<=[。！？；])", text) if part.strip()]
    return "".join(parts[:count]) if parts else text


def _normalize_action(action: str) -> str:
    return "减仓" if action.startswith("减仓") else action


def _parse_stock(name: str, symbol: str, block: str) -> StockDecision:
    action_match = ACTION_RE.search(block)
    action = _normalize_action(action_match.group(1)) if action_match else "观望"
    bias = _clean(action_match.group(2)) if action_match else ""

    decision_match = re.search(r"\*\*一句话决策\*\*[：:]\s*(.+)", block)
    validity_match = re.search(r"\*\*时效性\*\*[：:]\s*(.+)", block)

    holding_rows = _table_rows(block, ("持仓情况", "操作建议"))
    empty_advice = ""
    holder_advice = ""
    for row in holding_rows:
        identity = _row_value(row, "持仓情况")
        advice = _row_value(row, "操作建议")
        if "空仓" in identity:
            empty_advice = _first_sentences(advice)
        elif "持仓" in identity:
            holder_advice = _first_sentences(advice)

    quote_rows = _table_rows(block, ("收盘", "涨跌幅", "成交量"))
    quote = quote_rows[0] if quote_rows else {}
    live_rows = _table_rows(block, ("当前价", "量比", "换手率", "行情来源"))
    live = live_rows[0] if live_rows else {}
    plan_rows = _table_rows(block, ("操作点位",))
    points: dict[str, str] = {}
    for row in plan_rows:
        label = _row_value(row, "操作点位")
        value = next((v for k, v in row.items() if "操作点位" not in k), "")
        for canonical in ("理想买入点", "次优买入点", "止损位", "目标位"):
            if canonical in label:
                points[canonical] = value
                break

    position_match = re.search(r"\*\*[^*\n]*仓位建议\*\*[：:]\s*(.+)", block)
    checklist: list[dict[str, str]] = []
    labels = {"1": "趋势", "2": "乖离", "3": "量能", "4": "事件", "5": "估值"}
    for status, number in re.findall(r"^-\s*([✅⚠️❌])\s*检查项(\d+)", block, re.MULTILINE):
        checklist.append({"name": labels.get(number, f"检查{number}"), "status": status})

    volume_ratio = _row_value(live, "量比")
    if volume_ratio.lower() in {"n/a", "none", "na", "—", "-"}:
        volume_ratio = "缺失"

    return StockDecision(
        name=_clean(name),
        symbol=_clean(symbol).replace(" ", ""),
        action=action,
        bias=bias,
        decision=_clean(decision_match.group(1)) if decision_match else "",
        validity=_clean(validity_match.group(1)) if validity_match else "",
        current_price=_row_value(live, "当前价") or _row_value(quote, "收盘"),
        day_change=_row_value(quote, "涨跌幅"),
        volume_ratio=volume_ratio,
        turnover_rate=_row_value(live, "换手率"),
        quote_source=_row_value(live, "行情来源"),
        empty_advice=empty_advice,
        holder_advice=holder_advice,
        entry=points.get("理想买入点", ""),
        alternate_entry=points.get("次优买入点", ""),
        stop=points.get("止损位", ""),
        target=points.get("目标位", ""),
        position=_clean(position_match.group(1)) if position_match else "",
        risk=_first_bullet(block, "**🚨 风险警报**", "**✨ 利好催化**"),
        catalyst=_first_bullet(block, "**✨ 利好催化**", "**📢 最新动态**"),
        checklist=checklist,
    )


def parse_report(path: Path, market: str | None = None) -> DedupReport:
    text = path.read_text(encoding="utf-8")
    preamble = text.split("# 附录：原始详细分析", 1)[0]
    date_match = re.search(r"#\s*🎯\s*(\d{4}-\d{2}-\d{2})\s*决策仪表盘", text)
    if not date_match:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    report_date = date_match.group(1) if date_match else ""
    inferred_market = market or ("hk" if path.stem.endswith("_hk") else "us")

    matches = list(STOCK_HEADER_RE.finditer(text))
    stocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        stocks.append(_parse_stock(match.group(1), match.group(2), text[match.start() : end]))

    return DedupReport(
        report_date=report_date,
        market=inferred_market,
        market_status=_line_value(preamble, "市场状态"),
        main_theme=_line_value(preamble, "当前主线"),
        strategy=_paragraph_value(preamble, "今日策略"),
        focus_names=_list_between(preamble, "今日最值得关注", "今日不建议重仓参与"),
        avoid_names=_list_between(preamble, "今日不建议重仓参与", "今日策略"),
        stocks=stocks,
        source=str(path),
    )


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _badges(item: StockDecision) -> str:
    if not item.checklist:
        return ""
    return "".join(
        f'<span class="check check-{_e(check["status"])}">{_e(check["name"])}{_e(check["status"])}</span>'
        for check in item.checklist
    )


def _active_card(item: StockDecision) -> str:
    action_class = ACTION_CLASS.get(item.action, "watch")
    advice = item.empty_advice if item.action == "买入" else item.holder_advice
    return f"""
    <article class="stock-card card-{action_class}">
      <div class="stock-head"><div><b>{_e(item.name)}</b><small>{_e(item.symbol)}</small></div><span>{_e(item.action)}</span></div>
      <div class="decision">{_e(item.decision or advice)}</div>
      <div class="quote"><b>{_e(item.current_price or '—')}</b><span>{_e(item.day_change)}</span><span>量比 {_e(item.volume_ratio or '缺失')}</span><span>换手 {_e(item.turnover_rate or '缺失')}</span></div>
      <div class="levels">
        <div><small>{'触发/买点' if item.action == '买入' else '管理条件'}</small><b>{_e(item.entry or advice or '—')}</b></div>
        <div><small>止损/退出</small><b>{_e(item.stop or '—')}</b></div>
        <div><small>目标/减仓</small><b>{_e(item.target or '—')}</b></div>
        <div><small>仓位</small><b>{_e(item.position or '—')}</b></div>
      </div>
      <div class="evidence"><span class="risk">风险：{_e(item.risk or '未提取到明确风险')}</span><span class="catalyst">催化：{_e(item.catalyst or '无明确新增催化')}</span></div>
      <div class="card-foot"><div>{_badges(item)}</div><span>{_e(item.validity)}</span></div>
    </article>"""


def _watch_row(item: StockDecision) -> str:
    condition = item.empty_advice or item.decision or "等待新的确认信号"
    return f"""<tr><td><b>{_e(item.name)}</b><small>{_e(item.symbol)}</small></td><td><span class="pill pill-watch">观望</span></td><td>{_e(item.decision)}</td><td>{_e(condition)}</td><td>{_e(item.current_price)}<small>{_e(item.day_change)}</small></td></tr>"""


def render_html(report: DedupReport) -> str:
    active = sorted(
        (item for item in report.stocks if item.action != "观望"),
        key=lambda item: (ACTION_PRIORITY.get(item.action, 9), -bool(item.decision)),
    )
    watches = [item for item in report.stocks if item.action == "观望"]
    counts = {action: sum(item.action == action for item in report.stocks) for action in ACTION_PRIORITY}
    focus = "".join(f"<span>{_e(name)}</span>" for name in report.focus_names)
    avoid = "".join(f"<span>{_e(name)}</span>" for name in report.avoid_names)
    active_cards = "".join(_active_card(item) for item in active)
    watch_rows = "".join(_watch_row(item) for item in watches)
    market_label = "港股" if report.market == "hk" else "美股"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{market_label}老报告去重原型</title><style>
@page{{size:A4;margin:12mm 11mm 16mm;@bottom-right{{content:"第 " counter(page) " 页";font-size:8px;color:#64748b}}}}
*{{box-sizing:border-box}}body{{margin:0;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#172033;font-size:9px;line-height:1.42}}small{{display:block;color:#64748b;font-size:7.5px}}.cover{{background:linear-gradient(135deg,#111a35,#204f8e 60%,#087d77);color:#fff;border-radius:14px;padding:8mm;margin-bottom:5mm}}.eyebrow{{color:#99f6e4;font-size:8px;font-weight:800;letter-spacing:1.2px}}h1{{margin:2mm 0;font-size:23px}}.subtitle{{color:#dbeafe}}.metrics{{display:flex;gap:2mm;margin-top:5mm}}.metric{{flex:1;background:#ffffff1f;border-radius:7px;padding:2.5mm}}.metric b{{display:block;font-size:16px}}.market{{display:grid;grid-template-columns:1fr 1fr 2fr;gap:2mm;margin-bottom:3mm}}.market div{{background:#f1f5f9;border-radius:7px;padding:2mm 3mm}}.market small{{margin-bottom:.5mm}}.strategy{{background:#ecfdf5;border-left:4px solid #10b981;border-radius:6px;padding:2.5mm 3mm;margin-bottom:3mm;font-weight:700}}.chips{{display:flex;gap:1.2mm;flex-wrap:wrap;margin:1.5mm 0 4mm}}.chips span{{background:#eef2ff;color:#3730a3;border-radius:999px;padding:.7mm 2mm;font-size:8px}}.chips.avoid span{{background:#fff1f2;color:#9f1239}}h2{{font-size:16px;color:#173d70;border-bottom:2px solid #173d70;margin:5mm 0 3mm;padding-bottom:1.5mm;break-after:avoid}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:2.5mm}}.stock-card{{border:1px solid #dbe3ef;border-left:4px solid #10b981;border-radius:8px;padding:2.5mm 3mm;break-inside:avoid}}.card-hold{{border-left-color:#3b82f6}}.card-reduce{{border-left-color:#f59e0b}}.card-sell{{border-left-color:#dc2626}}.stock-head{{display:flex;justify-content:space-between;gap:2mm}}.stock-head b{{font-size:12px;color:#173d70}}.stock-head span,.pill{{font-weight:800;border-radius:999px;padding:.6mm 2mm;background:#dcfce7;color:#166534;height:max-content}}.card-hold .stock-head span{{background:#dbeafe;color:#1d4ed8}}.card-reduce .stock-head span{{background:#fef3c7;color:#92400e}}.card-sell .stock-head span{{background:#fee2e2;color:#991b1b}}.decision{{font-weight:800;margin:1.5mm 0;background:#f8fafc;border-radius:5px;padding:1.2mm 1.8mm}}.quote{{display:flex;gap:2mm;align-items:center;color:#64748b;margin-bottom:1.5mm}}.quote b{{font-size:11px;color:#172033}}.levels{{display:grid;grid-template-columns:1fr 1fr;gap:1mm 2mm}}.levels div{{background:#f8fafc;border-radius:5px;padding:1mm 1.5mm}}.levels b{{display:block;color:#173d70;font-size:8.3px}}.evidence{{display:grid;grid-template-columns:1fr 1fr;gap:1.5mm;margin-top:1.5mm}}.evidence span{{border-radius:5px;padding:1mm 1.5mm;font-size:7.7px}}.risk{{background:#fff1f2;color:#9f1239}}.catalyst{{background:#ecfdf5;color:#166534}}.card-foot{{display:flex;justify-content:space-between;align-items:center;margin-top:1.5mm;color:#64748b}}.check{{display:inline-block;border-radius:999px;background:#f1f5f9;padding:.4mm 1mm;margin-right:.5mm;font-size:7px}}.check-⚠️,.check-❌{{background:#fef3c7;color:#92400e}}table{{width:100%;border-collapse:collapse}}thead{{display:table-header-group}}th{{text-align:left;background:#173d70;color:white;padding:1.5mm;font-size:8px}}td{{padding:1.5mm;border-bottom:1px solid #e2e8f0;vertical-align:top;font-size:8px}}td:nth-child(1){{width:26mm}}td:nth-child(2){{width:16mm}}td:nth-child(5){{width:24mm}}.pill-watch{{background:#f1f5f9;color:#475569}}.source{{margin-top:4mm;color:#94a3b8;font-size:7px}}.page-break{{break-before:page}}
</style></head><body>
<div class="cover"><div class="eyebrow">DAILY STOCK ANALYSIS · ISOLATED V2 PROTOTYPE</div><h1>{market_label}老报告去重版</h1><div class="subtitle">报告日 {_e(report.report_date)}｜仅重排已有正式报告内容，不重新分析、不影响正式链路</div><div class="metrics"><div class="metric"><b>{len(report.stocks)}</b>分析股票</div><div class="metric"><b>{counts['买入']}</b>买入</div><div class="metric"><b>{counts['持有']}</b>持有</div><div class="metric"><b>{counts['减仓'] + counts['卖出']}</b>减仓/卖出</div><div class="metric"><b>{counts['观望']}</b>观望</div></div></div>
<div class="market"><div><small>市场状态</small><b>{_e(report.market_status or '未给出')}</b></div><div><small>当前主线</small><b>{_e(report.main_theme or '未给出')}</b></div><div><small>报告原则</small><b>一个结论只展示一次；观望股不再重复完整作战计划</b></div></div>
<div class="strategy">今日策略：{_e(report.strategy)}</div>
<small>重点关注</small><div class="chips">{focus or '<span>无</span>'}</div><small>不建议重仓</small><div class="chips avoid">{avoid or '<span>无</span>'}</div>
<h2>需要动作的股票</h2><div class="grid">{active_cards or '<div>暂无需要动作的股票。</div>'}</div>
<h2 class="page-break">观察池</h2><table><thead><tr><th>股票</th><th>动作</th><th>一句话结论</th><th>重新评估条件</th><th>现价/涨跌</th></tr></thead><tbody>{watch_rows}</tbody></table>
<div class="source">来源：{_e(report.source)}。原始详细分析仍保留在正式报告中，本原型不改变原始结论。</div>
</body></html>"""


def render_report(input_md: Path, output_html: Path, output_pdf: Path, market: str | None = None) -> dict:
    from weasyprint import HTML

    report = parse_report(input_md, market=market)
    document = render_html(report)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(document, encoding="utf-8")
    HTML(string=document, base_url=str(output_html.parent)).write_pdf(str(output_pdf))
    return {
        "market": report.market,
        "report_date": report.report_date,
        "stocks": len(report.stocks),
        "actions": {
            action: sum(item.action == action for item in report.stocks)
            for action in ACTION_PRIORITY
        },
        "html": str(output_html),
        "pdf": str(output_pdf),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render isolated deduplicated formal-report prototype")
    parser.add_argument("--input-md", type=Path, required=True)
    parser.add_argument("--market", choices=("hk", "us"))
    parser.add_argument("--output-html", type=Path)
    parser.add_argument("--output-pdf", type=Path)
    args = parser.parse_args()
    market = args.market or ("hk" if args.input_md.stem.endswith("_hk") else "us")
    date_match = re.search(r"(\d{8})", args.input_md.stem)
    date_token = date_match.group(1) if date_match else "latest"
    stem = f"dedup_formal_{market}_{date_token}"
    output_html = args.output_html or V2_ROOT / "output" / f"{stem}.html"
    output_pdf = args.output_pdf or V2_ROOT / "output" / f"{stem}.pdf"
    print(json.dumps(render_report(args.input_md, output_html, output_pdf, market), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
