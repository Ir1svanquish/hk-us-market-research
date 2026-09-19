"""CLI for synchronizing official filing metadata used by reports."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List

from .board_calendar import HKEXBoardCalendarClient
from .hkexnews import HKEXnewsClient
from .sec_edgar import SECEdgarClient
from .storage import OfficialFilingStore
from .sync import OfficialSourceSync


REPORTING_ROOT = Path(__file__).resolve().parents[1]


def _split_symbols(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(result))


def _read_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    prefix = f"{key}="
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw.strip().startswith(prefix):
            return raw.strip().split("=", 1)[1].strip().strip("\"'")
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync official filing metadata for reports")
    parser.add_argument("--hk-symbols", action="append", default=[], help="Comma-separated HK symbols")
    parser.add_argument("--us-symbols", action="append", default=[], help="Comma-separated US symbols")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--database", type=Path, default=REPORTING_ROOT / "state" / "official_filings.sqlite3")
    parser.add_argument("--cache-dir", type=Path, default=REPORTING_ROOT / "cache")
    parser.add_argument("--output", type=Path, default=REPORTING_ROOT / "output" / "last_sync.json")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--extract-bodies",
        action="store_true",
        help="Download and extract a bounded set of high-value/generic HKEX documents",
    )
    parser.add_argument("--sec-user-agent", default=os.getenv("SEC_USER_AGENT", ""))
    parser.add_argument(
        "--sec-contact-env-file",
        type=Path,
        help="Read the SEC contact email from a local env file without exposing it on the command line",
    )
    parser.add_argument("--sec-contact-env-key", default="EMAIL_SENDER")
    parser.add_argument("--print-filings", type=int, default=10)
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    hk_symbols = _split_symbols(args.hk_symbols or [os.getenv("REPORT_HK_SYMBOLS", "")])
    us_symbols = _split_symbols(args.us_symbols or [os.getenv("REPORT_US_SYMBOLS", "")])
    if not hk_symbols and not us_symbols:
        raise SystemExit("No symbols supplied; use --hk-symbols/--us-symbols or REPORT_HK_SYMBOLS/REPORT_US_SYMBOLS")
    if args.lookback_days < 1:
        raise SystemExit("--lookback-days must be at least 1")
    sec_user_agent = args.sec_user_agent
    if not sec_user_agent and args.sec_contact_env_file:
        contact = _read_env_value(args.sec_contact_env_file, args.sec_contact_env_key)
        if contact:
            sec_user_agent = f"daily-stock-analysis-reporting {contact}"
    if us_symbols and not sec_user_agent:
        raise SystemExit("US sync requires --sec-user-agent or SEC_USER_AGENT")

    store = OfficialFilingStore(args.database)
    hkex_client = HKEXnewsClient(args.cache_dir / "hkexnews", timeout=args.timeout) if hk_symbols else None
    board_calendar_client = (
        HKEXBoardCalendarClient(args.cache_dir / "hkex_board_calendar", timeout=args.timeout)
        if hk_symbols
        else None
    )
    sec_client = (
        SECEdgarClient(
            user_agent=sec_user_agent,
            cache_dir=args.cache_dir / "sec_edgar",
            timeout=args.timeout,
        )
        if us_symbols
        else None
    )
    start_date = args.end_date - timedelta(days=args.lookback_days - 1)
    sync = OfficialSourceSync(
        store,
        hkex_client=hkex_client,
        board_calendar_client=board_calendar_client,
        sec_client=sec_client,
    )
    result = sync.sync(
        hk_symbols=hk_symbols,
        us_symbols=us_symbols,
        start_date=start_date,
        end_date=args.end_date,
        extract_bodies=args.extract_bodies,
    )
    payload = result.to_dict()
    payload["window"] = {"start": start_date.isoformat(), "end": args.end_date.isoformat()}
    payload["recent_filings"] = store.list_filings(limit=max(1, args.print_filings))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
