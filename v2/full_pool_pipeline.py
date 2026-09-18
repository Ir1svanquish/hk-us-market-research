"""Build the isolated, fully-covered V2 stock-pool snapshot.

This command never sends messages, emails, or edits the production scheduler.
Official-source sync is intentionally a separate prerequisite so a slow or
failed network refresh can be inspected before the scoring run starts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from v2.data_contract import build_pool_contract, write_pool_contract
from v2.market_foundation import build_relative_strength
from v2.official_sources.event_context_cli import main as build_event_context
from v2.official_sources.finnhub import FinnhubV2Client
from v2.official_sources.storage import OfficialFilingStore
from v2.opportunity_scoring import render_markdown, score_market
from v2.research_report import DEFAULT_DB, load_stocks, parse_market_research
from v2.top5_validation import (
    DEFAULT_LEDGER,
    DEFAULT_LEGACY_STATE,
    Top5ValidationLedger,
    render_markdown as render_validation_markdown,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = Path(__file__).resolve().parent


def load_earnings_research(path: Path) -> dict[str, dict]:
    """Load a dated research snapshot, including an optional prior snapshot.

    Small dated backfills can extend the last audited snapshot instead of
    copying every stock into a new file.  The newer file always wins on a
    symbol collision, and recursive/cyclic inheritance is rejected.
    """
    merged: dict[str, dict] = {}
    seen: set[Path] = set()

    def visit(current: Path) -> None:
        resolved = current.resolve()
        if resolved in seen:
            raise ValueError(f"cyclic earnings research snapshot: {current}")
        seen.add(resolved)
        payload = json.loads(current.read_text(encoding="utf-8"))
        parent = str(payload.get("extends") or "").strip()
        if parent:
            visit((current.parent / parent).resolve())
        for symbol, value in (payload.get("stocks") or {}).items():
            if isinstance(value, dict):
                merged[str(symbol)] = dict(value)

    visit(path)
    return merged


def _read_env_keys(path: Path, key: str) -> list[str]:
    if not path.exists():
        return []
    prefix = f"{key}="
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw.strip().startswith(prefix):
            value = raw.strip().split("=", 1)[1].strip().strip("\"'")
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _successful_sync_symbols(path: Path, provider: str) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item.get("symbol") or "").upper()
        for item in payload.get("items", [])
        if isinstance(item, dict)
        and item.get("provider") == provider
        and item.get("status") == "ok"
    }


def validate_sync_coverage(path: Path, hk_symbols: list[str], us_symbols: list[str]) -> dict:
    hk = _successful_sync_symbols(path, "hkexnews")
    us = _successful_sync_symbols(path, "sec_edgar")
    expected_hk = {symbol.removeprefix("HK") for symbol in hk_symbols}
    expected_us = set(us_symbols)
    missing_hk = sorted(expected_hk - hk)
    missing_us = sorted(expected_us - us)
    result = {
        "expected": len(expected_hk) + len(expected_us),
        "covered": len(expected_hk & hk) + len(expected_us & us),
        "missing_hk": missing_hk,
        "missing_us": missing_us,
    }
    if missing_hk or missing_us:
        raise RuntimeError(f"official sync coverage incomplete: {result}")
    return result


def render_audit(summary: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    totals = contract.get("totals") or {}
    validation = contract.get("validation") or {}
    lines = [
        f"# 全股票池基础能力与数据契约审计｜{contract.get('report_date')}",
        "",
        "> V2 隔离输出；正式报告、定时任务、Telegram 与邮件均未改动。",
        "",
        "## 覆盖结果",
        "",
        f"- 股票池：{totals.get('stocks')} 只（港股 {totals.get('hk')} / 美股 {totals.get('us')}）",
        f"- 官方事件与质量评估：{totals.get('official_quality_coverage')}/{totals.get('stocks')}",
        f"- 指数/行业相对强弱：{totals.get('relative_strength_coverage')}/{totals.get('stocks')}",
        f"- 统一量比确认：{totals.get('volume_confirmation_coverage')}/{totals.get('stocks')}",
        f"- 数据契约校验：{'通过' if validation.get('valid') else '失败'}",
        "",
        "## 影子机会 Top5",
        "",
    ]
    for market, label in (("hk", "港股"), ("us", "美股")):
        lines.append(f"### {label}")
        lines.append("")
        for index, item in enumerate((summary.get("scores") or {}).get(market, {}).get("top5", []), 1):
            lines.append(
                f"{index}. {item.get('symbol')}｜{item.get('score')}｜{item.get('status')}"
            )
        lines.append("")
    lines.extend(
        [
            "## 口径",
            "",
            "- 长桥实时量比与历史相对成交量分开保存；历史重跑只使用报告日完整日线口径。",
            "- 港股日线按 Asia/Hong_Kong、美国日线按 America/New_York 转换交易日。",
            "- 相对强弱使用个股相对市场ETF与行业ETF代理的 5/20 日超额收益；无法可靠映射时明确回退市场基准。",
            "- 每只股票保存契约版本、评分版本、机会分八项、执行成熟度、事件、质量、来源和警告。",
            "",
        ]
    )
    return "\n".join(lines)


def build_us_snapshots(
    *,
    symbols: list[str],
    as_of: date,
    env_file: Path,
    cache_dir: Path,
    official_database: Path,
    sync_output: Path,
) -> dict[str, dict]:
    client = FinnhubV2Client(_read_env_keys(env_file, "FINNHUB_API_KEYS"), cache_dir)
    snapshots = {symbol: client.fetch_snapshot(symbol, as_of) for symbol in symbols}
    covered = _successful_sync_symbols(sync_output, "sec_edgar")
    store = OfficialFilingStore(official_database)
    rows = store.get_filings(
        symbols,
        published_since=date.fromordinal(as_of.toordinal() - 30).isoformat(),
        limit=5000,
    )
    for symbol in symbols:
        direct = [
            row
            for row in rows
            if row.get("provider") == "sec_edgar" and row.get("symbol") == symbol
        ]
        if symbol in covered:
            # Successful zero-result queries are valid coverage, not missing
            # data.  Direct filing rows themselves are sourced by the official
            # store in EventContextBuilder.
            snapshots[symbol]["sec_direct_status"] = "ok"
        snapshots[symbol]["sec_direct_filing_count"] = len(direct)
    return snapshots


def _symbol_lists(db: Path, report_date: str) -> tuple[list[str], list[str]]:
    hk = [item.symbol for item in load_stocks(db, report_date, "hk", None)]
    us = [item.symbol for item in load_stocks(db, report_date, "us", None)]
    return hk, us


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build isolated full-pool V2 contract")
    parser.add_argument("--date", default="2026-08-21")
    parser.add_argument("--market", choices=("both", "hk", "us"), default="both")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--env-hk", type=Path, default=PROJECT_ROOT / ".env.hk")
    parser.add_argument("--env-us", type=Path, default=PROJECT_ROOT / ".env.us")
    parser.add_argument(
        "--earnings-research",
        type=Path,
        help="Optional point-in-time detailed earnings research snapshot",
    )
    parser.add_argument(
        "--official-sync",
        type=Path,
        default=V2_ROOT / "output" / "full_pool_official_sync_20260821.json",
    )
    parser.add_argument(
        "--official-database",
        type=Path,
        default=V2_ROOT / "state" / "official_filings.sqlite3",
    )
    parser.add_argument("--accuracy-ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--legacy-top5-state", type=Path, default=DEFAULT_LEGACY_STATE)
    parser.add_argument("--output-dir", type=Path, default=V2_ROOT / "output")
    args = parser.parse_args(argv)
    report_day = date.fromisoformat(args.date)
    token = args.date.replace("-", "")
    market_suffix = "" if args.market == "both" else f"_{args.market}"
    args.output_dir.mkdir(parents=True, exist_ok=True)

    hk_symbols, us_symbols = _symbol_lists(args.db, args.date)
    selected_markets = ("hk", "us") if args.market == "both" else (args.market,)
    if "hk" not in selected_markets:
        hk_symbols = []
    if "us" not in selected_markets:
        us_symbols = []
    sync_coverage = validate_sync_coverage(args.official_sync, hk_symbols, us_symbols)
    snapshots = (
        build_us_snapshots(
            symbols=us_symbols,
            as_of=report_day,
            env_file=args.env_us,
            cache_dir=V2_ROOT / "cache" / "finnhub",
            official_database=args.official_database,
            sync_output=args.official_sync,
        )
        if us_symbols
        else {}
    )
    snapshot_path = args.output_dir / f"full_pool_us_snapshot_{token}.json"
    snapshot_path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")

    event_json = args.output_dir / f"full_pool_event_context_{token}{market_suffix}.json"
    event_md = args.output_dir / f"full_pool_event_context_{token}{market_suffix}.md"
    context_args = [
        "--as-of",
        args.date,
        "--official-database",
        str(args.official_database),
        "--finnhub-snapshot",
        str(snapshot_path),
        "--output-json",
        str(event_json),
        "--output-markdown",
        str(event_md),
    ]
    if hk_symbols:
        context_args.extend(
            [
                "--hk-symbols", ",".join(hk_symbols),
                "--hk-production-report", str(PROJECT_ROOT / "reports" / f"report_{token}_hk.md"),
                "--hk-coverage", str(args.official_sync),
                "--longbridge-env", str(args.env_hk),
            ]
        )
    if us_symbols:
        context_args.extend(
            [
                "--us-symbols", ",".join(us_symbols),
                "--us-production-report", str(PROJECT_ROOT / "reports" / f"report_{token}_us.md"),
                "--longbridge-env", str(args.env_us),
            ]
        )
    if build_event_context(context_args) != 0:
        raise RuntimeError("full-pool event context failed")
    event_payload = json.loads(event_json.read_text(encoding="utf-8"))
    earnings_research: dict[str, dict] = {}
    if args.earnings_research:
        research_payload = json.loads(args.earnings_research.read_text(encoding="utf-8"))
        if str(research_payload.get("as_of") or "") > args.date:
            raise ValueError("earnings research snapshot is newer than the report date")
        earnings_research = load_earnings_research(args.earnings_research)

    relative = {}
    for market, symbols in (("hk", hk_symbols), ("us", us_symbols)):
        if market not in selected_markets:
            continue
        relative.update(
            build_relative_strength(
                db_path=args.db,
                report_date=report_day,
                market=market,
                symbols=symbols,
                env_files=[args.env_hk, args.env_us],
            )
        )

    all_stocks = []
    all_opportunities = []
    score_outputs: dict[str, Any] = {}
    for market in selected_markets:
        compact = PROJECT_ROOT / "reports" / f"compact_report_{token}_{market}.md"
        research = parse_market_research(compact.read_text(encoding="utf-8"))
        market_relative = {
            symbol: value
            for symbol, value in relative.items()
            if (symbol.startswith("HK")) == (market == "hk")
        }
        results = score_market(
            args.db,
            args.date,
            market,
            research,
            event_json,
            market_relative,
        )
        all_opportunities.extend(results)
        all_stocks.extend(load_stocks(args.db, args.date, market, event_json))
        json_path = args.output_dir / f"full_pool_opportunity_{market}_{token}.json"
        markdown_path = args.output_dir / f"full_pool_opportunity_{market}_{token}.md"
        json_path.write_text(
            json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown_path.write_text(render_markdown(args.date, market, results), encoding="utf-8")
        score_outputs[market] = {
            "top5": [
                {
                    "symbol": item.symbol,
                    "score": item.opportunity_score,
                    "status": item.execution_status,
                }
                for item in results[:5]
            ],
            "json": str(json_path),
            "markdown": str(markdown_path),
        }

    contract = build_pool_contract(
        report_date=report_day,
        stocks=all_stocks,
        event_payload=event_payload,
        relative_strength=relative,
        opportunities=all_opportunities,
        earnings_research=earnings_research,
    )
    contract_path = args.output_dir / f"full_pool_contract_{token}{market_suffix}.json"
    write_pool_contract(contract, contract_path)
    accuracy_ledger = Top5ValidationLedger(args.accuracy_ledger)
    legacy_rows = accuracy_ledger.import_legacy_history(args.legacy_top5_state)
    shadow_rows = accuracy_ledger.record_contract(contract)
    evaluated_rows = accuracy_ledger.evaluate(args.db, args.date)
    accuracy_summary = accuracy_ledger.build_summary(args.date)
    accuracy_summary["ingest"] = {
        "legacy_rows": legacy_rows,
        "shadow_rows": shadow_rows,
        "evaluated_rows": evaluated_rows,
    }
    accuracy_json = args.output_dir / f"top5_validation_{token}{market_suffix}.json"
    accuracy_markdown = args.output_dir / f"top5_validation_{token}{market_suffix}.md"
    accuracy_json.write_text(
        json.dumps(accuracy_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    accuracy_markdown.write_text(
        render_validation_markdown(accuracy_summary),
        encoding="utf-8",
    )
    accuracy_ledger.close()
    summary = {
        "report_date": args.date,
        "formal_pipeline_modified": False,
        "event_totals": event_payload.get("totals"),
        "official_sync_coverage": sync_coverage,
        "contract_totals": contract.get("totals"),
        "validation": contract.get("validation"),
        "scores": score_outputs,
        "contract": str(contract_path),
        "top5_validation": {
            "ledger": str(args.accuracy_ledger),
            "json": str(accuracy_json),
            "markdown": str(accuracy_markdown),
        },
    }
    summary_path = args.output_dir / f"full_pool_build_summary_{token}{market_suffix}.json"
    audit_path = args.output_dir / f"full_pool_audit_{token}{market_suffix}.md"
    audit_path.write_text(render_audit(summary, contract), encoding="utf-8")
    summary["audit_markdown"] = str(audit_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if contract.get("validation", {}).get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
