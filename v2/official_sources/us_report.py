"""Generate an isolated five-stock US official-event comparison PDF."""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from weasyprint import HTML

from .finnhub import FinnhubV2Client
from .storage import OfficialFilingStore


V2_ROOT = Path(__file__).resolve().parents[1]

TAG_LABELS = {
    "critical_corporate_event": "重大公司事件",
    "cybersecurity_incident": "网络安全事件",
    "financial_results": "财务业绩",
    "restructuring": "重组",
    "audit_change": "审计变更",
    "board_or_management": "管理层变动",
    "current_report": "8-K 临时报告",
    "foreign_issuer_report": "境外发行人报告",
    "insider_transaction": "内部人交易",
    "planned_insider_sale": "拟出售证券",
    "capital_dilution": "潜在融资/摊薄",
    "beneficial_ownership": "大股东持仓",
    "proxy_governance": "股东会/治理",
    "other_official_filing": "其他申报",
}


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _read_env_keys(path: Path) -> List[str]:
    if not path.exists():
        return []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw.strip().startswith("FINNHUB_API_KEYS="):
            value = raw.split("=", 1)[1].strip().strip("\"'")
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _parse_summary(path: Path) -> Dict[str, Dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    result: Dict[str, Dict[str, str]] = {}
    pattern = re.compile(
        r"^[^\n]*\*\*(?P<name>.+?)\((?P<symbol>[A-Z][A-Z0-9.-]{0,9})\)\*\*:\s*"
        r"(?P<decision>[^|\n]+)\|\s*评分\s*(?P<score>\d+)\s*\|\s*(?P<trend>[^\n]+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        item = {key: value.strip() for key, value in match.groupdict().items()}
        result[item["symbol"]] = item
    return result


def _gate(decision: str, days: int | None, filings: object = None) -> tuple[str, str]:
    filing_items = filings if isinstance(filings, list) else []
    has_dilution = any(
        isinstance(tag, dict) and tag.get("event_type") == "capital_dilution"
        for filing in filing_items if isinstance(filing, dict)
        for tag in filing.get("event_tags", []) if isinstance(filing.get("event_tags"), list)
    )
    if has_dilution and decision == "买入" and days is not None and days <= 7:
        return (
            "财报/融资双重观察",
            "财报不足 7 天，且近 30 天出现融资招股文件索引：取消主动买点，先核验发行条款与潜在摊薄。",
        )
    if has_dilution and decision == "买入":
        return "融资摊薄待核验", "近 30 天出现融资招股文件索引：在 EDGAR 原文核验规模、用途和摊薄前暂停主动买入。"
    if days is None:
        return decision, "未发现未来 45 天财报窗口；维持原结论，但仍需持续更新公司日历。"
    if days <= 3:
        return "财报前暂停新交易", "财报不足 3 天：不新开仓、不加仓，等待盘前/盘后结果及指引落地。"
    if days <= 7:
        if decision == "持有":
            return "持有但禁止加仓", "财报不足 7 天：已有仓位只做风险管理，空仓者等待财报后再评估。"
        return "财报前条件观察", "财报不足 7 天：取消主动买点，等待结果、指引和盘后跳空风险释放。"
    if days <= 14:
        return "事件风险观察", "两周内存在财报窗口：降低仓位上限，并注明财报后的计划失效条件。"
    return decision, "财报窗口超过两周，暂不触发硬闸门；继续跟踪日期是否变更。"


def _tag_html(tags: object) -> str:
    rendered = []
    for tag in tags if isinstance(tags, list) else []:
        if not isinstance(tag, dict):
            continue
        event_type = str(tag.get("event_type") or "other_official_filing")
        severity = str(tag.get("severity") or "medium")
        rendered.append(
            f'<span class="tag tag-{_e(severity)}">{_e(TAG_LABELS.get(event_type, event_type))}</span>'
        )
    return "".join(rendered)


def _filings_html(filings: object) -> str:
    if not isinstance(filings, list) or not filings:
        return '<div class="empty">近 30 天未取得可展示的 SEC 文件索引。</div>'
    ranked = sorted(
        (item for item in filings if isinstance(item, dict)),
        key=lambda item: (
            {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(item.get("severity")), 0),
            str(item.get("filed_date") or ""),
        ),
        reverse=True,
    )[:5]
    cards = []
    for item in ranked:
        url = item.get("report_url") or item.get("filing_url") or ""
        source_badge = (
            '<span class="direct-badge">SEC 直连</span>'
            if item.get("sec_direct")
            else '<span class="index-badge">Finnhub 索引</span>'
        )
        cards.append(
            f"""<div class="filing"><div class="filing-head">
            <span class="filing-date">{_e(str(item.get('filed_date') or '')[:10])}</span>
            <b class="form">{_e(item.get('form'))}</b>{source_badge}{_tag_html(item.get('event_tags'))}</div>
            <a href="{_e(url)}">查看 SEC 申报文件</a></div>"""
        )
    return "".join(cards)


def _stats_html(snapshot: Mapping[str, object]) -> str:
    history = snapshot.get("earnings_history") if isinstance(snapshot.get("earnings_history"), list) else []
    latest = history[0] if history and isinstance(history[0], dict) else {}
    positive = 0
    negative = 0
    for item in history:
        if not isinstance(item, dict) or item.get("surprisePercent") is None:
            continue
        try:
            if float(item["surprisePercent"]) >= 0:
                positive += 1
            else:
                negative += 1
        except (TypeError, ValueError):
            pass
    recommendation = snapshot.get("recommendation") if isinstance(snapshot.get("recommendation"), dict) else {}
    return f"""<div class="data-grid">
      <div><span>最近财报期</span><b>{_e(latest.get('period') or '—')}</b></div>
      <div><span>实际 / 预期 EPS</span><b>{_e(latest.get('actual') if latest else '—')} / {_e(latest.get('estimate') if latest else '—')}</b></div>
      <div><span>近四季超 / 低预期</span><b>{positive} / {negative}</b></div>
      <div><span>机构 Strong Buy / Buy / Hold</span><b>{_e(recommendation.get('strongBuy') or '—')} / {_e(recommendation.get('buy') or '—')} / {_e(recommendation.get('hold') or '—')}</b></div>
    </div>"""


def render(
    *, symbols: Sequence[str], as_of: date, production_report: Path, snapshots: Mapping[str, Mapping[str, object]],
    output_html: Path, output_pdf: Path, snapshot_output: Path, sec_direct: bool = False
) -> Dict[str, object]:
    summary = _parse_summary(production_report)
    cards = []
    upcoming = 0
    changed = 0
    filing_count = 0
    for symbol in symbols:
        original = summary.get(symbol, {"name": symbol, "decision": "未读取", "score": "—", "trend": "—"})
        snapshot = snapshots.get(symbol, {})
        next_item = snapshot.get("next_earnings") if isinstance(snapshot.get("next_earnings"), dict) else {}
        days: int | None = None
        if next_item.get("date"):
            try:
                days = (date.fromisoformat(str(next_item["date"])) - as_of).days
            except ValueError:
                days = None
        if days is not None:
            upcoming += 1
        filings = snapshot.get("filings") if isinstance(snapshot.get("filings"), list) else []
        adjusted, advice = _gate(original["decision"], days, filings)
        if adjusted != original["decision"]:
            changed += 1
        filing_count += len(filings)

        if days is not None:
            hour = str(next_item.get("hour") or "dmh").lower()
            hour_label = {"bmo": "盘前 BMO", "amc": "盘后 AMC", "dmh": "时段待定"}.get(hour, hour.upper())
            date_box = f"""<div class="key-date estimated"><div class="key-date-label">财报关键窗口 · Finnhub 日历（待公司/SEC确认）</div>
            <div class="key-date-value">{_e(next_item.get('date'))}</div><div class="countdown">距报告日 <b>{days}</b> 天 · {_e(hour_label)}</div>
            <div class="purpose">预估 EPS：{_e(next_item.get('epsEstimate') if next_item.get('epsEstimate') is not None else '—')}　季度：{_e(next_item.get('year'))}Q{_e(next_item.get('quarter'))}</div></div>"""
        else:
            date_box = """<div class="key-date unconfirmed"><div class="key-date-label">未来 45 天未取得财报日期</div>
            <div class="purpose">不等于没有事件；继续跟踪公司 IR、Finnhub 与 SEC。</div></div>"""

        change_class = "changed" if adjusted != original["decision"] else "unchanged"
        cards.append(f"""<section class="stock-card"><div class="stock-heading"><div><span class="symbol">{_e(symbol)}</span><h2>{_e(original['name'])}</h2></div>
        <div class="score">V1 评分<br><b>{_e(original['score'])}</b></div></div>{date_box}
        <div class="decision-grid"><div><span>原报告结论</span><b>{_e(original['decision'])}</b></div>
        <div class="{change_class}"><span>V2 官方事件闸门</span><b>{_e(adjusted)}</b></div></div>
        <div class="gate-advice">{_e(advice)}</div><h3>财报与机构数据</h3>{_stats_html(snapshot)}
        <h3>近期 SEC 文件索引与多标签</h3>{_filings_html(filings)}</section>""")

    doc = _document(
        as_of=as_of,
        production_date=production_report.stem.replace("report_", "").replace("_us", ""),
        upcoming=upcoming,
        changed=changed,
        filing_count=filing_count,
        cards="".join(cards),
        sec_direct=sec_direct,
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    snapshot_output.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(doc, encoding="utf-8")
    snapshot_output.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    HTML(string=doc, base_url=str(output_html.parent)).write_pdf(str(output_pdf))
    return {
        "symbols": len(symbols), "upcoming_earnings": upcoming, "decisions_changed": changed,
        "filings_indexed": filing_count, "pdf": str(output_pdf), "html": str(output_html)
    }


def _document(*, as_of: date, production_date: str, upcoming: int, changed: int, filing_count: int, cards: str, sec_direct: bool) -> str:
    source_title = "Finnhub + SEC EDGAR 直连" if sec_direct else "Finnhub + SEC 文件索引"
    boundary = (
        "橙色日期卡来自 Finnhub，属于关键窗口但尚未由公司 IR/SEC 财报通知确认；SEC 表单元数据、Item 编号和原文链接已通过 EDGAR 直连核验。报告前台不展示正文解析过程。"
        if sec_direct
        else "橙色日期卡来自 Finnhub，属于关键窗口但尚未由公司 IR/SEC 直连确认；SEC 文件仅为索引，尚未直连 EDGAR 核验。"
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>美股官方事件增强 V2</title><style>
@page {{ size:A4; margin:16mm 14mm 18mm; @bottom-right {{ content:"第 " counter(page) " 页"; color:#64748b; font-size:9px; }} }}
*{{box-sizing:border-box}} body{{margin:0;color:#172033;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;font-size:10.5px;line-height:1.55}} a{{color:#0b63ce;text-decoration:none;font-size:9px}}
.cover{{padding:9mm 8mm;color:white;border-radius:16px;background:linear-gradient(135deg,#101a36,#184a8c 64%,#087a78);margin-bottom:8mm}} .eyebrow{{letter-spacing:1.5px;font-size:9px;color:#9fe6df;font-weight:700}} h1{{margin:4mm 0 2mm;font-size:25px;line-height:1.15}} .subtitle{{color:#dbeafe;font-size:11px}} .metrics{{display:flex;gap:4mm;margin-top:6mm}} .metric{{flex:1;padding:3mm;border-radius:9px;background:rgba(255,255,255,.11)}} .metric b{{display:block;font-size:20px}}
.note{{margin:0 0 7mm;padding:4mm 5mm;border-left:4px solid #f59e0b;background:#fff8e7;border-radius:5px}} .stock-card{{page-break-before:always}} .stock-heading{{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #173d70;padding-bottom:3mm;margin-bottom:5mm}} .stock-heading h2{{display:inline;margin:0 0 0 3mm;font-size:20px}} .symbol{{color:#0b63ce;font-weight:800}} .score{{text-align:center;color:#64748b;padding:2mm 4mm;border-radius:8px;background:#f1f5f9}} .score b{{color:#172033;font-size:17px}}
.key-date{{border-radius:12px;padding:5mm;margin:4mm 0;page-break-inside:avoid}} .key-date.estimated{{color:#7c2d12;background:#fff7ed;border:2px solid #f97316;box-shadow:0 0 0 3px #ffedd5 inset}} .key-date.unconfirmed{{color:#475569;background:#f8fafc;border:1px dashed #94a3b8}} .key-date-label{{font-weight:800}} .key-date-value{{font-size:27px;font-weight:900;line-height:1.25;margin-top:1mm}} .countdown{{display:inline-block;background:#c2410c;color:white;padding:1mm 3mm;border-radius:999px}} .purpose{{margin-top:2mm}}
.decision-grid{{display:grid;grid-template-columns:1fr 1fr;gap:4mm;margin:5mm 0 3mm}} .decision-grid>div{{border:1px solid #cbd5e1;background:#f8fafc;border-radius:9px;padding:3mm 4mm}} .decision-grid span,.data-grid span{{display:block;color:#64748b;font-size:9px}} .decision-grid b{{display:block;font-size:15px;margin-top:1mm}} .decision-grid .changed{{border-color:#f59e0b;background:#fffbeb;color:#92400e}} .gate-advice{{padding:3mm 4mm;border-left:4px solid #f59e0b;background:#fffbeb;margin-bottom:5mm}} h3{{color:#173d70;font-size:13px;margin:5mm 0 2mm}}
.data-grid{{display:grid;grid-template-columns:1fr 1fr;gap:2mm}} .data-grid>div{{border:1px solid #dbe3ef;background:#f8fafc;border-radius:7px;padding:2.5mm 3mm}} .data-grid b{{font-size:11px}} .filing{{border:1px solid #dbe3ef;border-radius:8px;padding:3mm 4mm;margin:2.5mm 0;page-break-inside:avoid}} .filing-head{{display:flex;flex-wrap:wrap;gap:1.5mm;align-items:center;margin-bottom:1.5mm}} .filing-date{{font-weight:800}} .form{{font-size:11px}} .direct-badge,.index-badge{{border-radius:999px;padding:.5mm 2mm;font-size:8px;font-weight:700}} .direct-badge{{background:#dcfce7;color:#166534}} .index-badge{{background:#e2e8f0;color:#475569}} .tag{{border-radius:999px;padding:.5mm 2mm;font-size:8px;font-weight:700}} .tag-critical{{background:#7f1d1d;color:white}} .tag-high{{background:#fee2e2;color:#991b1b}} .tag-medium{{background:#fef3c7;color:#92400e}} .tag-low{{background:#e0f2fe;color:#075985}} .empty{{color:#64748b}}
</style></head><body><div class="cover"><div class="eyebrow">DAILY STOCK ANALYSIS · ISOLATED US V2</div><h1>美股官方事件增强对照报告</h1><div class="subtitle">快照日 {_e(as_of)}｜基准正式报告 {_e(production_date)}｜{_e(source_title)}</div><div class="metrics"><div class="metric"><b>5</b>只股票</div><div class="metric"><b>{upcoming}</b>个财报窗口</div><div class="metric"><b>{changed}</b>项结论降档</div><div class="metric"><b>{filing_count}</b>份 SEC 直连申报</div></div></div>
<div class="note"><b>重要边界：</b>{_e(boundary)}</div>{cards}</body></html>"""


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate isolated US V2 comparison PDF")
    parser.add_argument("--symbols", default="MU,IREN,NOK,SNDK,PLTR")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--production-report", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=V2_ROOT / "cache" / "finnhub")
    parser.add_argument("--database", type=Path, default=V2_ROOT / "state" / "official_filings.sqlite3")
    parser.add_argument("--output-html", type=Path, default=V2_ROOT / "output" / "us_top5_official_events_v2.html")
    parser.add_argument("--output-pdf", type=Path, default=V2_ROOT / "output" / "us_top5_official_events_v2.pdf")
    parser.add_argument("--snapshot-output", type=Path, default=V2_ROOT / "output" / "us_top5_snapshot.json")
    args = parser.parse_args(argv)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    client = FinnhubV2Client(_read_env_keys(args.env_file), args.cache_dir)
    snapshots = {symbol: client.fetch_snapshot(symbol, args.as_of) for symbol in symbols}
    store = OfficialFilingStore(args.database)
    direct_rows = store.get_filings(
        symbols,
        published_since=(date.fromordinal(args.as_of.toordinal() - 30)).isoformat(),
        limit=1000,
    )
    direct_count = 0
    for symbol in symbols:
        rows = [row for row in direct_rows if row.get("provider") == "sec_edgar" and row.get("symbol") == symbol]
        if not rows:
            continue
        direct_count += len(rows)
        snapshots[symbol]["filings"] = [
            {
                "accession": row.get("filing_id"),
                "form": row.get("form_type"),
                "filed_date": row.get("metadata", {}).get("filing_date") or str(row.get("published_at") or "")[:10],
                "accepted_date": row.get("published_at"),
                "report_url": row.get("source_url"),
                "filing_url": row.get("source_url"),
                "event_tags": row.get("metadata", {}).get("event_tags", []),
                "severity": row.get("severity"),
                "items": row.get("metadata", {}).get("items", ""),
                "sec_direct": True,
            }
            for row in rows
        ]
        snapshots[symbol]["sec_direct_status"] = "ok"
    result = render(symbols=symbols, as_of=args.as_of, production_report=args.production_report,
                    snapshots=snapshots, output_html=args.output_html, output_pdf=args.output_pdf,
                    snapshot_output=args.snapshot_output, sec_direct=direct_count > 0)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
