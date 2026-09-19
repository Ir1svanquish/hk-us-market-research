"""Backfill a historical macro-surprise calibration ledger."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from .macro_calibration import build_calibration, save_calibration
from .nasdaq_calendar import NasdaqMacroCalendarClient


REPORTING_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build macro scenario calibration for reports")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--cache-dir", type=Path, default=REPORTING_ROOT / "cache" / "nasdaq_macro")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTING_ROOT / "state" / "macro_probability_calibration.json",
    )
    args = parser.parse_args()
    lookback_days = max(30, int(args.lookback_days))
    end_date = args.as_of - timedelta(days=1)
    start_date = args.as_of - timedelta(days=lookback_days)
    client = NasdaqMacroCalendarClient(args.cache_dir, max_workers=4)
    releases = client.fetch_releases(start_date, end_date)
    payload = build_calibration(releases, as_of=args.as_of, lookback_days=lookback_days)
    payload["window_start"] = start_date.isoformat()
    payload["window_end"] = end_date.isoformat()
    payload["fetch_errors"] = client.errors
    save_calibration(payload, args.output)
    summary = {
        "window": [start_date.isoformat(), end_date.isoformat()],
        "released_rows": payload["released_rows"],
        "family_series": len(payload["family"]),
        "categories": len(payload["category"]),
        "fetch_errors": len(client.errors),
        "output": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
