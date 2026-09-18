"""Standalone V2 comparison report for official HKEX events.

The renderer only reads the production Markdown report.  It never imports or
modifies production report, scheduler, notification, or state modules.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence

from weasyprint import HTML

from .storage import OfficialFilingStore


V2_ROOT = Path(__file__).resolve().parents[1]

TAG_LABELS = {
    "trading_status": "停复牌/上市状态",
    "earnings_warning": "业绩预警",
    "earnings_calendar": "业绩日历",
    "capital_dilution": "股本摊薄",
    "debt_financing": "债务融资",
    "financial_results": "财务业绩",
    "inside_information": "内幕消息",
    "major_transaction": "重大/关联交易",
    "audit_change": "核数师变更",
    "strategic_investment": "战略投资",
    "dividend": "股息",
    "share_buyback": "股份回购",
    "share_scheme": "股份激励",
    "board_or_management": "管理层变动",
    "routine_disclosure": "例行披露",
    "other_official_filing": "其他官方公告",
}

SEVERITY_LABELS = {
    "critical": "极高",
    "high": "高",
    "medium": "中",
    "low": "低",
}

DEFAULT_NAMES = {
    "06809": "澜起科技",
    "00981": "中芯国际",
    "03750": "宁德时代",
    "01810": "小米集团-W",
    "00100": "MINIMAX-W",
}


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _parse_production_summary(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    results: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r"^[^\n]*\*\*(?P<name>.+?)\(HK(?P<symbol>\d{5})\)\*\*:\s*"
        r"(?P<decision>[^|\n]+)\|\s*评分\s*(?P<score>\d+)\s*\|\s*(?P<trend>[^\n]+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        item = {key: value.strip() for key, value in match.groupdict().items()}
        results[item["symbol"]] = item
    return results


def _event_gate(decision: str, days: int | None) -> tuple[str, str]:
    if days is None:
        return decision or "待核验", "暂无已确认的近期业绩董事会日期，仍需按公告更新。"
    if days < 0:
        return decision or "待核验", "关键日期已过，需等待正式业绩公告并重新评估。"
    if days <= 3:
        adjusted = "财报前暂停新交易"
        advice = "硬事件闸门：不新开仓、不加仓；已有仓位仅按既定风险线管理。"
    elif days <= 7:
        adjusted = "财报前条件观察"
        advice = "业绩窗口不足 7 天：清空财报前主动买点，等待业绩与指引落地。"
    elif days <= 14:
        adjusted = "事件风险观察"
        advice = "两周内有确定事件：降低仓位上限，交易计划必须注明财报失效条件。"
    else:
        adjusted = decision or "待核验"
        advice = "日期已确认但不在近两周硬闸门内，保留原技术结论并持续跟踪。"
    if decision == "持有" and days <= 7:
        adjusted = "持有但禁止加仓"
    return adjusted, advice


def _dedupe_filings(items: Iterable[Mapping[str, object]]) -> List[Mapping[str, object]]:
    result = []
    seen = set()
    for item in items:
        title = str(item.get("title") or "").strip().casefold()
        key = (str(item.get("published_at") or "")[:10], title)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _rank_filing(item: Mapping[str, object]) -> tuple[int, int, str]:
    severity = {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(
        str(item.get("severity") or ""), 0
    )
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    body_ok = int(metadata.get("body_status") == "ok")
    return severity, body_ok, str(item.get("published_at") or "")


def _tag_badges(metadata: Mapping[str, object], fallback: str) -> str:
    tags = metadata.get("event_tags")
    if not isinstance(tags, list) or not tags:
        tags = [{"event_type": fallback, "severity": "medium"}]
    rendered = []
    seen = set()
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        event_type = str(tag.get("event_type") or "other_official_filing")
        if event_type in seen:
            continue
        seen.add(event_type)
        severity = str(tag.get("severity") or "medium")
        rendered.append(
            f'<span class="tag tag-{_escape(severity)}">{_escape(TAG_LABELS.get(event_type, event_type))}</span>'
        )
    return "".join(rendered)


def _is_readable_fact(value: str) -> bool:
    if not value:
        return False
    useful = 0
    for char in value:
        code = ord(char)
        category = unicodedata.category(char)
        if (
            code < 128
            or 0x00A0 <= code <= 0x024F
            or 0x3000 <= code <= 0x303F
            or 0x3400 <= code <= 0x9FFF
            or category[0] in {"N", "P", "Z"}
        ):
            useful += 1
    return useful / max(1, len(value)) >= 0.88


def _body_highlights(metadata: Mapping[str, object]) -> List[str]:
    body = re.sub(r"\s+", " ", str(metadata.get("body_excerpt") or ""))
    highlights: List[str] = []
    patterns = (
        r"(?:公司)?认缴出资金额为人民币\s*[\d,.\s]+万元，持有基金\s*[\d.\s]+%[^。]{0,100}。",
        r"回购方案首次披露日\s*\d{4}/\d{1,2}/\d{1,2}[^。]{0,260}预计回购金额\s*[\d亿元~至.-]+",
    )
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            highlights.append(match.group(0).strip()[:500])
    return highlights


def _filing_html(item: Mapping[str, object]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    title = str(item.get("title") or "未命名公告")
    date_text = str(item.get("published_at") or "")[:10]
    severity = str(item.get("severity") or "medium")
    stored_facts = metadata.get("key_facts") if isinstance(metadata.get("key_facts"), list) else []
    facts = _body_highlights(metadata)
    facts.extend(
        str(fact).strip()
        for fact in stored_facts
        if _is_readable_fact(str(fact).strip()) and str(fact).strip() not in facts
    )
    fact_html = ""
    if facts:
        fact_html = '<div class="facts"><b>官方公告要点：</b>' + "；".join(
            _escape(str(fact)[:260]) for fact in facts[:2]
        ) + "</div>"
    source_url = _escape(item.get("source_url"))
    return f"""
      <div class="filing">
        <div class="filing-head">
          <span class="filing-date">{_escape(date_text)}</span>
          <span class="severity severity-{_escape(severity)}">风险 {SEVERITY_LABELS.get(severity, severity)}</span>
          {_tag_badges(metadata, str(item.get('event_type') or 'other_official_filing'))}
        </div>
        <div class="filing-title">{_escape(title)}</div>
        {fact_html}
        <a href="{source_url}">HKEXnews 官方原文</a>
      </div>
    """


def render_report(
    *,
    store: OfficialFilingStore,
    symbols: Sequence[str],
    as_of: date,
    production_report: Path,
    output_html: Path,
    output_pdf: Path,
) -> dict:
    production = _parse_production_summary(production_report)
    dates = store.list_key_dates(
        symbols,
        start_date=as_of.isoformat(),
        end_date=date(as_of.year + 1, 12, 31).isoformat(),
    )
    dates_by_symbol = {str(item["symbol"]): item for item in dates}
    filings = store.get_filings(symbols, published_since=f"{as_of.isoformat()[:8]}01", limit=1000)
    filings_by_symbol: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for filing in filings:
        filings_by_symbol[str(filing["symbol"])].append(filing)

    cards = []
    changed_count = 0
    body_count = 0
    for symbol in symbols:
        original = production.get(symbol, {})
        name = original.get("name") or DEFAULT_NAMES.get(symbol, f"HK{symbol}")
        decision = original.get("decision") or "未读取"
        key_date = dates_by_symbol.get(symbol)
        days: int | None = None
        if key_date:
            event_date = datetime.strptime(str(key_date["event_date"]), "%Y-%m-%d").date()
            days = (event_date - as_of).days
        adjusted, gate_advice = _event_gate(decision, days)
        if adjusted != decision:
            changed_count += 1

        symbol_filings = _dedupe_filings(filings_by_symbol.get(symbol, []))
        body_count += sum(
            1
            for item in symbol_filings
            if isinstance(item.get("metadata"), dict) and item["metadata"].get("body_status") == "ok"
        )
        high_value = [
            item
            for item in symbol_filings
            if item.get("severity") in {"critical", "high"}
            or (
                isinstance(item.get("metadata"), dict)
                and item["metadata"].get("body_status") == "ok"
                and item.get("event_type") not in {"routine_disclosure", "share_buyback"}
            )
        ]
        selected = sorted(high_value or symbol_filings, key=_rank_filing, reverse=True)[:4]
        filings_html = "".join(_filing_html(item) for item in selected)
        if not filings_html:
            filings_html = '<div class="empty">本期未取得可展示的官方公告。</div>'

        if key_date:
            date_box = f"""
              <div class="key-date confirmed">
                <div class="key-date-label">确定关键日期 · 港交所董事会业绩日历</div>
                <div class="key-date-value">{_escape(key_date['event_date'])}</div>
                <div class="countdown">距报告日 <b>{days}</b> 天</div>
                <div class="purpose">用途：{_escape(key_date['purpose'])}　期间：{_escape(key_date['period'])}</div>
                <a href="{_escape(key_date['source_url'])}">核对官方日历</a>
              </div>
            """
        else:
            date_box = """
              <div class="key-date unconfirmed">
                <div class="key-date-label">未发现已确认的近期业绩董事会日期</div>
                <div class="purpose">不等于没有事件；仍需持续同步 HKEXnews。</div>
              </div>
            """

        change_class = "changed" if adjusted != decision else "unchanged"
        cards.append(
            f"""
            <section class="stock-card">
              <div class="stock-heading">
                <div><span class="symbol">HK{_escape(symbol)}</span><h2>{_escape(name)}</h2></div>
                <div class="score">V1 评分<br><b>{_escape(original.get('score') or '—')}</b></div>
              </div>
              {date_box}
              <div class="decision-grid">
                <div><span>原报告结论</span><b>{_escape(decision)}</b></div>
                <div class="{change_class}"><span>V2 官方事件闸门</span><b>{_escape(adjusted)}</b></div>
              </div>
              <div class="gate-advice">{_escape(gate_advice)}</div>
              <h3>近期官方公告与多标签</h3>
              {filings_html}
            </section>
            """
        )

    confirmed_count = len(dates_by_symbol)
    html_text = _document_html(
        as_of=as_of,
        symbols=symbols,
        confirmed_count=confirmed_count,
        changed_count=changed_count,
        body_count=body_count,
        cards="".join(cards),
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html_text, encoding="utf-8")
    HTML(string=html_text, base_url=str(output_html.parent)).write_pdf(str(output_pdf))
    return {
        "symbols": len(symbols),
        "confirmed_key_dates": confirmed_count,
        "decisions_changed": changed_count,
        "body_documents_recognized": body_count,
        "html": str(output_html),
        "pdf": str(output_pdf),
    }


def _document_html(
    *, as_of: date, symbols: Sequence[str], confirmed_count: int, changed_count: int,
    body_count: int, cards: str
) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>港股官方事件增强 V2</title>
<style>
@page {{ size: A4; margin: 16mm 14mm 18mm; @bottom-right {{ content: "第 " counter(page) " 页"; color:#64748b; font-size:9px; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:#172033; font-family:"Noto Sans CJK SC","WenQuanYi Micro Hei","Microsoft YaHei",sans-serif; font-size:10.5px; line-height:1.55; background:#fff; }}
a {{ color:#0b63ce; text-decoration:none; font-size:9px; }}
.cover {{ padding:9mm 8mm; color:white; border-radius:16px; background:linear-gradient(135deg,#101a36,#184a8c 64%,#087a78); margin-bottom:8mm; }}
.eyebrow {{ letter-spacing:1.5px; font-size:9px; color:#9fe6df; font-weight:700; }}
h1 {{ margin:4mm 0 2mm; font-size:25px; line-height:1.15; }}
.subtitle {{ color:#dbeafe; font-size:11px; }}
.metrics {{ display:flex; gap:4mm; margin-top:6mm; }}
.metric {{ flex:1; padding:3mm; border-radius:9px; background:rgba(255,255,255,.11); }}
.metric b {{ display:block; font-size:20px; }}
.note {{ margin:0 0 7mm; padding:4mm 5mm; border-left:4px solid #f59e0b; background:#fff8e7; border-radius:5px; }}
.stock-card {{ page-break-before:always; }}
.stock-heading {{ display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #173d70; padding-bottom:3mm; margin-bottom:5mm; }}
.stock-heading h2 {{ display:inline; margin:0 0 0 3mm; font-size:20px; }}
.symbol {{ color:#0b63ce; font-weight:800; letter-spacing:.5px; }}
.score {{ text-align:center; color:#64748b; padding:2mm 4mm; border-radius:8px; background:#f1f5f9; }}
.score b {{ color:#172033; font-size:17px; }}
.key-date {{ border-radius:12px; padding:5mm; margin:4mm 0; page-break-inside:avoid; }}
.key-date.confirmed {{ color:#7f1d1d; background:#fff1f2; border:2px solid #ef4444; box-shadow:0 0 0 3px #fee2e2 inset; }}
.key-date.unconfirmed {{ color:#475569; background:#f8fafc; border:1px dashed #94a3b8; }}
.key-date-label {{ font-weight:800; letter-spacing:.5px; }}
.key-date-value {{ font-size:27px; font-weight:900; line-height:1.25; margin-top:1mm; }}
.countdown {{ display:inline-block; background:#b91c1c; color:white; padding:1mm 3mm; border-radius:999px; }}
.purpose {{ margin-top:2mm; }}
.decision-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:4mm; margin:5mm 0 3mm; }}
.decision-grid > div {{ border:1px solid #cbd5e1; background:#f8fafc; border-radius:9px; padding:3mm 4mm; }}
.decision-grid span {{ display:block; color:#64748b; font-size:9px; }}
.decision-grid b {{ display:block; font-size:15px; margin-top:1mm; }}
.decision-grid .changed {{ border-color:#f59e0b; background:#fffbeb; color:#92400e; }}
.gate-advice {{ padding:3mm 4mm; border-left:4px solid #f59e0b; background:#fffbeb; margin-bottom:5mm; }}
h3 {{ color:#173d70; font-size:13px; margin:5mm 0 2mm; }}
.filing {{ border:1px solid #dbe3ef; border-radius:8px; padding:3mm 4mm; margin:2.5mm 0; page-break-inside:avoid; }}
.filing-head {{ display:flex; flex-wrap:wrap; gap:1.5mm; align-items:center; }}
.filing-date {{ font-weight:800; color:#334155; }}
.filing-title {{ font-weight:700; font-size:11px; margin:1.5mm 0; }}
.tag,.severity {{ border-radius:999px; padding:.5mm 2mm; font-size:8px; font-weight:700; }}
.tag-critical,.severity-critical {{ background:#7f1d1d;color:white; }}
.tag-high,.severity-high {{ background:#fee2e2;color:#991b1b; }}
.tag-medium,.severity-medium {{ background:#fef3c7;color:#92400e; }}
.tag-low,.severity-low {{ background:#e0f2fe;color:#075985; }}
.facts {{ background:#f1f5f9; padding:2mm 3mm; margin:2mm 0; border-radius:5px; font-size:9px; }}
.muted,.empty {{ color:#64748b; }}
.footer-note {{ page-break-before:always; padding-top:15mm; }}
.footer-note h2 {{ color:#173d70; }}
.footer-note li {{ margin:2mm 0; }}
</style></head><body>
<div class="cover"><div class="eyebrow">DAILY STOCK ANALYSIS · ISOLATED V2</div>
<h1>港股官方事件增强对照报告</h1><div class="subtitle">报告日 {_escape(as_of.isoformat())}｜头五只港股｜HKEXnews + 董事会业绩日历</div>
<div class="metrics"><div class="metric"><b>{len(symbols)}</b>只股票</div><div class="metric"><b>{confirmed_count}</b>个确定日期</div><div class="metric"><b>{changed_count}</b>项结论降档</div><div class="metric"><b>{body_count}</b>份正文已识别</div></div></div>
<div class="note"><b>阅读方法：</b>红色日期框代表港交所董事会日历已确认的日期；“V2 官方事件闸门”是对原报告交易动作的约束，不替代行情和技术分析。公告标签可同时出现多个，避免单标签丢失“业绩 + 股息 + 融资”等复合事件。</div>
{cards}
<section class="footer-note"><h2>边界与结论</h2><ul>
<li>本报告是独立 V2 样稿，未写入或改变现有 HK 正式报告链路。</li>
<li>确定日期来自港交所董事会会议通知日历；公告标题、正文与链接来自 HKEXnews。</li>
<li>正文识别为规则化抽取，不做臆测；无法识别时明确标注并退回标题级分类。</li>
<li>交易建议仅用于比较事件闸门效果，不构成投资建议。</li></ul></section>
</body></html>"""


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate isolated HK official-event V2 PDF")
    parser.add_argument("--symbols", default="06809,00981,03750,01810,00100")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--database", type=Path, default=V2_ROOT / "state" / "official_filings.sqlite3")
    parser.add_argument("--production-report", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, default=V2_ROOT / "output" / "hk_official_events_v2.html")
    parser.add_argument("--output-pdf", type=Path, default=V2_ROOT / "output" / "hk_official_events_v2.pdf")
    args = parser.parse_args(argv)
    symbols = [item.strip().removeprefix("HK").zfill(5) for item in args.symbols.split(",") if item.strip()]
    result = render_report(
        store=OfficialFilingStore(args.database),
        symbols=symbols,
        as_of=args.as_of,
        production_report=args.production_report,
        output_html=args.output_html,
        output_pdf=args.output_pdf,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
