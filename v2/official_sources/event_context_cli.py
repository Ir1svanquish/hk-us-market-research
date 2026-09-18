"""CLI for the isolated unified event context and hard-gate engine."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, List
from zoneinfo import ZoneInfo

from .bls_calendar import BLSCalendarClient
from .decision_ledger import DecisionLedger, MODEL_VERSION
from .decision_quality import (
    assess_decisions,
    load_hk_coverage,
    load_us_coverage,
    parse_production_report,
)
from .event_context import EventContextBuilder, load_snapshots, strongest_by_symbol
from .event_context_store import UnifiedEventStore
from .hkexnews import normalize_hk_symbol
from .macro_calibration import apply_calibration, load_calibration
from .market_metrics import LongbridgeMarketMetricsClient
from .nasdaq_calendar import NasdaqMacroCalendarClient
from .sec_edgar import normalize_us_symbol
from .storage import OfficialFilingStore
from .trading_cards import build_trading_cards
from .trigger_lifecycle import LIFECYCLE_MODEL_VERSION, TriggerLifecycleLedger


V2_ROOT = Path(__file__).resolve().parents[1]

EVENT_LABELS = {
    "nonfarm_payrolls": "美国非农就业",
    "cpi": "美国 CPI",
    "ppi": "美国 PPI",
}

GATE_LABELS = {
    "info_only": "仅供参考",
    "monitor": "事件观察",
    "reassess": "重新评估",
    "conditional_only": "仅条件观察",
    "block_new_positions": "禁止新开仓/加仓",
    "risk_off": "风险关闭",
}


def _split(values: Iterable[str]) -> List[str]:
    result = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(result))


def _summary_markdown(payload: dict, as_of: date) -> str:
    lines = [
        f"# V2 统一事件上下文与硬闸门（{as_of.isoformat()}）",
        "",
        "> 独立 V2 影子输出；不改变正式 HK/US 报告和推送。",
        "",
        "## 个股最强闸门",
        "",
    ]
    gates = payload.get("symbol_gates", {})
    for symbol, item in sorted(
        gates.items(), key=lambda pair: (-int(pair[1].get("priority", 0)), pair[0])
    ):
        lines.append(
            f"- **{symbol}｜{GATE_LABELS.get(item.get('gate_action'), item.get('gate_action'))}**："
            f"{item.get('reason')}（有效至 {item.get('gate_until') or '未设定'}）"
        )
    if not gates:
        lines.append("- 暂无个股事件。")

    lines.extend(["", "## 美国完整宏观日历（未来 7 天 + 近期公布结果）", ""])
    lines.extend([
        "> 下列内容是相对市场一致预期的条件场景，不是对数据点位或市场涨跌的确定预测。",
        "",
    ])
    macro = payload.get("macro_events", [])
    for event in macro:
        event_date = str(event.get("effective_at") or "")[:10]
        try:
            days = (date.fromisoformat(event_date) - as_of).days
        except ValueError:
            days = "?"
        status = event.get("metadata", {}).get("retrieval_status", "")
        metadata = event.get("metadata", {}) if isinstance(event.get("metadata"), dict) else {}
        release_time = metadata.get("release_time") or "时间待定"
        importance = metadata.get("importance") or "medium"
        lines.append(
            f"### {event_date}｜{event.get('headline') or EVENT_LABELS.get(event.get('event_type'), event.get('event_type'))}"
        )
        lines.append("")
        lines.append(
            f"- 时间：{release_time}（距报告日 {days} 天）｜重要度：{importance}｜获取状态：{status}"
        )
        lines.append(f"- 闸门：{GATE_LABELS.get(event.get('gate_action'), event.get('gate_action'))}；{event.get('gate_reason')}")
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        if analysis.get("status") == "released":
            lines.append(f"- 公布结果：{analysis.get('summary')}")
            lines.append(
                f"- 影响分析：{analysis.get('broad_market')}；相对受益："
                f"{'、'.join(analysis.get('positive_sectors', []))}；相对承压："
                f"{'、'.join(analysis.get('negative_sectors', []))}。"
            )
        elif importance == "high":
            scenarios = metadata.get("scenarios", [])
            for scenario in scenarios:
                lines.append(
                    f"- **{scenario.get('name')}**：{scenario.get('broad_market')}；"
                    f"相对受益：{'、'.join(scenario.get('positive_sectors', []))}；"
                    f"相对承压：{'、'.join(scenario.get('negative_sectors', []))}。"
                )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build unified V2 official and macro event context")
    parser.add_argument("--hk-symbols", action="append", default=[])
    parser.add_argument("--us-symbols", action="append", default=[])
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--macro-days", type=int, default=7)
    parser.add_argument("--macro-lookback-days", type=int, default=3)
    parser.add_argument("--official-database", type=Path, default=V2_ROOT / "state" / "official_filings.sqlite3")
    parser.add_argument("--event-database", type=Path, default=V2_ROOT / "state" / "unified_events.sqlite3")
    parser.add_argument("--finnhub-snapshot", type=Path)
    parser.add_argument("--hk-production-report", type=Path)
    parser.add_argument("--us-production-report", type=Path)
    parser.add_argument("--hk-coverage", type=Path)
    parser.add_argument(
        "--longbridge-env",
        type=Path,
        action="append",
        default=[],
        help="V2-only runtime credential source; may be supplied more than once",
    )
    parser.add_argument(
        "--decision-ledger",
        type=Path,
        default=V2_ROOT / "state" / "decision_accuracy.sqlite3",
    )
    parser.add_argument("--cache-dir", type=Path, default=V2_ROOT / "cache" / "bls")
    parser.add_argument("--nasdaq-cache-dir", type=Path, default=V2_ROOT / "cache" / "nasdaq_macro")
    parser.add_argument(
        "--macro-calibration",
        type=Path,
        default=V2_ROOT / "state" / "macro_probability_calibration.json",
    )
    parser.add_argument("--bls-seed", type=Path, default=V2_ROOT / "data" / "bls_schedule_seed_2026.json")
    parser.add_argument("--output-json", type=Path, default=V2_ROOT / "output" / "event_context.json")
    parser.add_argument("--output-markdown", type=Path, default=V2_ROOT / "output" / "event_context.md")
    args = parser.parse_args(argv)

    hk_symbols = [normalize_hk_symbol(item) for item in _split(args.hk_symbols)]
    us_symbols = [normalize_us_symbol(item) for item in _split(args.us_symbols)]
    if not hk_symbols and not us_symbols:
        raise SystemExit("Supply --hk-symbols and/or --us-symbols")

    official_store = OfficialFilingStore(args.official_database)
    bls = BLSCalendarClient(args.cache_dir, args.bls_seed)
    macro_start = date.fromordinal(args.as_of.toordinal() - max(0, args.macro_lookback_days))
    macro_end = date.fromordinal(args.as_of.toordinal() + min(7, max(1, args.macro_days)))
    nasdaq = NasdaqMacroCalendarClient(args.nasdaq_cache_dir)
    calibration = load_calibration(args.macro_calibration)
    market_macro = apply_calibration(
        nasdaq.fetch_releases(macro_start, macro_end), calibration
    )
    official_macro = apply_calibration(
        bls.fetch_releases(macro_start, macro_end), calibration
    )
    macro = market_macro + official_macro
    finnhub_snapshots = load_snapshots(args.finnhub_snapshot)
    builder = EventContextBuilder(official_store, args.as_of)
    events = builder.build(
        hk_symbols=hk_symbols,
        us_symbols=us_symbols,
        finnhub_snapshots=finnhub_snapshots,
        macro_releases=macro,
    )
    event_store = UnifiedEventStore(args.event_database)
    event_store.upsert(events)

    symbol_gates = strongest_by_symbol(events)
    report_items = {}
    if args.hk_production_report and args.hk_production_report.exists():
        report_items.update(parse_production_report(args.hk_production_report, "hk"))
    if args.us_production_report and args.us_production_report.exists():
        report_items.update(parse_production_report(args.us_production_report, "us"))
    coverage = {
        **load_hk_coverage(args.hk_coverage),
        **load_us_coverage(finnhub_snapshots),
    }
    selected_report_items = {
        symbol: report_items[symbol]
        for symbol in hk_symbols + us_symbols
        if symbol in report_items
    }
    market_metrics = {}
    market_metrics_error = ""
    if selected_report_items and args.longbridge_env:
        try:
            market_metrics = LongbridgeMarketMetricsClient(args.longbridge_env).fetch(
                selected_report_items,
                default_report_date=args.as_of,
            )
        except Exception as exc:
            market_metrics_error = f"{type(exc).__name__}: {exc}"
    event_dicts = [event.to_dict() for event in events]
    quality = assess_decisions(
        report_items,
        as_of=args.as_of,
        coverage=coverage,
        symbol_gates=symbol_gates,
        events=event_dicts,
        symbols=hk_symbols + us_symbols,
        market_metrics=market_metrics,
    )
    trading_cards = build_trading_cards(quality, market_metrics, as_of=args.as_of)
    lifecycle = TriggerLifecycleLedger(args.decision_ledger)
    trading_cards = lifecycle.apply(args.as_of, quality, trading_cards, market_metrics)
    lifecycle.close()
    for symbol, card in trading_cards.items():
        quality[symbol]["standard_trade_card"] = card
    ledger = DecisionLedger(args.decision_ledger)
    ledger_rows = ledger.upsert(args.as_of.isoformat(), quality) if quality else 0
    payload = {
        "as_of": args.as_of.isoformat(),
        "information_cutoff": {
            "hk": datetime.combine(
                args.as_of, time(16, 30), ZoneInfo("Asia/Hong_Kong")
            ).isoformat(),
            "us": datetime.combine(
                args.as_of, time(16, 30), ZoneInfo("America/New_York")
            ).isoformat(),
            "rule": "regular_close_plus_30_minutes",
        },
        "totals": {
            "events": len(events),
            "official_filings": sum(event.source in {"hkexnews", "sec_edgar"} for event in events),
            "earnings_dates": sum(event.event_type == "earnings_calendar" for event in events),
            "macro_events": sum(event.symbol == "__MARKET__" for event in events),
            "hard_gates": sum(event.gate_action in {"conditional_only", "block_new_positions", "risk_off"} for event in events),
            "material_symbol_gates": sum(
                int(item.get("priority", 0)) >= 2 for item in symbol_gates.values()
            ),
            "quality_assessments": len(quality),
            "quality_downgrades": sum(
                int(item.get("final_gate", {}).get("priority", 0)) >= 3
                for item in quality.values()
            ),
            "market_metrics": len(market_metrics),
            "ready_trade_cards": sum(card.get("active") is True for card in trading_cards.values()),
            "standard_ready_trade_cards": sum(
                card.get("execution_tier") == "standard" for card in trading_cards.values()
            ),
            "cautious_ready_trade_cards": sum(
                card.get("execution_tier") == "cautious" for card in trading_cards.values()
            ),
            "pending_cross_session_confirmation": sum(
                (card.get("trigger_lifecycle") or {}).get("state") == "pending_confirmation"
                for card in trading_cards.values()
            ),
            "cancelled_trade_cards": sum(
                str(card.get("status") or "").startswith("invalid_")
                or card.get("status") == "data_conflict"
                for card in trading_cards.values()
            ),
        },
        "macro_window": {
            "start": macro_start.isoformat(),
            "end": macro_end.isoformat(),
            "report_rule": "future_7_days_plus_recent_releases",
            "nasdaq_errors": nasdaq.errors,
            "probability_calibration": {
                "as_of": calibration.get("as_of"),
                "lookback_days": calibration.get("lookback_days"),
                "source": calibration.get("source"),
            },
        },
        "symbol_gates": symbol_gates,
        "market_metrics": market_metrics,
        "market_metrics_error": market_metrics_error,
        "trading_cards": trading_cards,
        "decision_quality": quality,
        "decision_ledger": {
            "database": str(args.decision_ledger),
            "model_version": MODEL_VERSION,
            "trigger_lifecycle_version": LIFECYCLE_MODEL_VERSION,
            "rows_upserted": ledger_rows,
            "total_rows": ledger.count(),
        },
        "macro_events": [event.to_dict() for event in events if event.symbol == "__MARKET__"],
        "events": event_dicts,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_markdown.write_text(_summary_markdown(payload, args.as_of), encoding="utf-8")
    print(json.dumps(payload["totals"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
