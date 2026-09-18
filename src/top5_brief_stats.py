from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional

from src.analyzer import AnalysisResult
from src.services.history_loader import get_frozen_target_date

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state" / "top5_brief_stats.json"
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOCK = RLock()
_MAX_ENTRIES_PER_REGION = 30


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _resolve_report_date(report_date: Optional[str] = None) -> str:
    if report_date:
        return str(report_date)[:10]
    frozen = get_frozen_target_date()
    if frozen:
        return frozen.strftime("%Y-%m-%d")
    return _today_utc()


def _load() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"regions": {}, "updated_at": None}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"regions": {}, "updated_at": None}
        data.setdefault("regions", {})
        return data
    except Exception:
        logger.warning("top5 brief stats state load failed", exc_info=True)
        return {"regions": {}, "updated_at": None}


def _save(data: Dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    STATE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _normalize_entries(entries: Any) -> List[Dict[str, Any]]:
    if not isinstance(entries, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_date = str(entry.get("date") or "").strip()[:10]
        codes = [str(code).strip() for code in (entry.get("codes") or []) if str(code).strip()]
        if not entry_date or not codes:
            continue
        normalized.append({"date": entry_date, "codes": codes})
    normalized.sort(key=lambda item: item["date"])
    return normalized[-_MAX_ENTRIES_PER_REGION:]


def _parse_day(value: Optional[str]) -> Optional[date]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _history_close_on_or_before(rows: List[Dict[str, Any]], target: date) -> Optional[float]:
    matched_price: Optional[float] = None
    matched_date: Optional[date] = None
    for row in rows:
        row_date = _parse_day(row.get("date"))
        if row_date is None or row_date > target:
            continue
        close_price = _safe_float(row.get("close"))
        if close_price is None:
            continue
        if matched_date is None or row_date >= matched_date:
            matched_date = row_date
            matched_price = close_price
    return matched_price


def _history_close_after(rows: List[Dict[str, Any]], target: date) -> Optional[float]:
    matched_price: Optional[float] = None
    matched_date: Optional[date] = None
    for row in rows:
        row_date = _parse_day(row.get("date"))
        if row_date is None or row_date <= target:
            continue
        close_price = _safe_float(row.get("close"))
        if close_price is None:
            continue
        if matched_date is None or row_date < matched_date:
            matched_date = row_date
            matched_price = close_price
    return matched_price


def _resolve_current_price(result: AnalysisResult, report_end: date, history_rows: List[Dict[str, Any]]) -> Optional[float]:
    current_price = _safe_float(getattr(result, "current_price", None))
    if current_price and current_price > 0:
        return current_price

    snapshot = getattr(result, "market_snapshot", None) or {}
    for key in ("price", "close"):
        price = _safe_float(snapshot.get(key))
        if price and price > 0:
            return price

    return _history_close_on_or_before(history_rows, report_end)


def _compute_cumulative_change(
    code: str,
    first_seen: date,
    report_end: date,
    current_result: AnalysisResult,
) -> Optional[float]:
    calendar_days = max((report_end - first_seen).days + 10, 30)
    try:
        from src.services.history_loader import load_history_df

        history_df, _source = load_history_df(code, days=calendar_days, target_date=report_end)
    except Exception:
        logger.warning("top5 backtest history load failed for %s", code, exc_info=True)
        return None

    if history_df is None or history_df.empty:
        return None

    history_rows = history_df.to_dict(orient="records")
    # Use the first trading day's close after the report date to avoid same-day look-ahead bias.
    start_price = _history_close_after(history_rows, first_seen)
    if start_price is None:
        start_price = _history_close_on_or_before(history_rows, first_seen)
    end_price = _resolve_current_price(current_result, report_end, history_rows)
    if start_price is None or end_price is None or start_price <= 0:
        return None

    return ((end_price - start_price) / start_price) * 100


def _collect_backtest_changes(
    current_codes: List[str],
    current_results: Dict[str, AnalysisResult],
    entries: List[Dict[str, Any]],
    frequency: Counter[str],
    report_date: str,
    *,
    limit: Optional[int] = None,
) -> List[tuple[str, str, float]]:
    report_end = _parse_day(report_date)
    if report_end is None:
        return []

    changes: List[tuple[str, str, float]] = []
    for code in current_codes:
        if frequency.get(code, 0) < 2:
            continue

        first_seen_text = next((entry["date"] for entry in entries if code in entry["codes"]), "")
        first_seen = _parse_day(first_seen_text)
        current_result = current_results.get(code)
        if first_seen is None or current_result is None:
            continue

        change_pct = _compute_cumulative_change(code, first_seen, report_end, current_result)
        if change_pct is None:
            continue

        changes.append((code, first_seen_text, change_pct))
        if limit is not None and len(changes) >= limit:
            break
    return changes


def _build_backtest_summary_line(changes: List[tuple[str, str, float]]) -> Optional[str]:
    if not changes:
        return None
    total = len(changes)
    winners = sum(1 for _code, _first_seen, change in changes if change > 0)
    win_rate = (winners / total) * 100
    avg_change = sum(change for _code, _first_seen, change in changes) / total
    avg_sign = "+" if avg_change >= 0 else ""
    return f"高频股胜率：{win_rate:.1f}%（{winners}/{total}）｜平均累计涨跌（次日收盘口径）：{avg_sign}{avg_change:.1f}%"


def _build_backtest_line(
    current_codes: List[str],
    current_results: Dict[str, AnalysisResult],
    entries: List[Dict[str, Any]],
    frequency: Counter[str],
    report_date: str,
    name_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    if name_map is None:
        name_map = {}
    changes = _collect_backtest_changes(
        current_codes,
        current_results,
        entries,
        frequency,
        report_date,
        limit=3,
    )
    parts = []
    for code, first_seen_text, change_pct in changes:
        sign = "+" if change_pct >= 0 else ""
        parts.append(f"{_code_display(code, name_map)}(首次 {first_seen_text}, {sign}{change_pct:.1f}%)")

    if not parts:
        return None
    return f"首次上榜至今（次日收盘口径）：{' / '.join(parts)}"


def _current_codes(results: Iterable[AnalysisResult], limit: int = 5) -> List[str]:
    codes: List[str] = []
    for item in results:
        code = str(getattr(item, "code", "") or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
        if len(codes) >= limit:
            break
    return codes


def update_top5_history(
    region: str,
    results: Iterable[AnalysisResult],
    *,
    report_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    region_key = str(region or "cn").strip().lower()
    current_date = _resolve_report_date(report_date)
    current_codes = _current_codes(results)
    if not current_codes:
        return []

    with _LOCK:
        data = _load()
        regions = data.setdefault("regions", {})
        node = regions.setdefault(region_key, {})
        entries = _normalize_entries(node.get("entries"))

        updated = False
        for entry in entries:
            if entry["date"] == current_date:
                entry["codes"] = current_codes
                updated = True
                break
        if not updated:
            entries.append({"date": current_date, "codes": current_codes})
        entries.sort(key=lambda item: item["date"])
        node["entries"] = entries[-_MAX_ENTRIES_PER_REGION:]
        try:
            _save(data)
        except Exception:
            logger.warning("top5 brief stats state save failed", exc_info=True)
        return list(node["entries"])


def _code_to_name_map(results: Iterable[AnalysisResult]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in results:
        code = str(getattr(item, "code", "") or "").strip().upper()
        name = str(getattr(item, "name", "") or "").strip()
        if code and name:
            mapping[code] = name
    return mapping


def _code_display(code: str, name_map: dict[str, str]) -> str:
    name = name_map.get(code, "")
    if name:
        return name
    # Fallback 1: try STOCK_NAME_MAP (strip market prefix if present, e.g. HK03750 → 03750)
    from src.data.stock_mapping import STOCK_NAME_MAP as _STOCK_NAME_MAP
    clean = code.upper()
    for prefix in ('HK', 'US.', 'SH', 'SZ'):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    if clean in _STOCK_NAME_MAP:
        return _STOCK_NAME_MAP[clean]
    if code in _STOCK_NAME_MAP:
        return _STOCK_NAME_MAP[code]
    # Fallback 2: try stocks.index.json
    try:
        _index_name = _get_stock_name_from_index(code)
        if _index_name:
            return _index_name
    except Exception:
        pass
    return code


def _get_stock_name_from_index(code: str) -> str:
    """Look up stock name from stocks.index.json cache."""
    clean = code.upper()
    for prefix in ('HK', 'US.', 'SH', 'SZ'):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    index = _load_stock_index()
    for entry in index:
        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
            if str(entry[1]).strip().upper() == clean:
                return str(entry[2])
    return ""


_stock_index_cache = None


def _load_stock_index():
    global _stock_index_cache
    if _stock_index_cache is not None:
        return _stock_index_cache
    import json
    index_path = ROOT / "apps" / "dsa-web" / "public" / "stocks.index.json"
    if index_path.exists():
        try:
            _stock_index_cache = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            _stock_index_cache = []
    else:
        _stock_index_cache = []
    return _stock_index_cache


def build_top5_brief_lines(
    region: str,
    results: Iterable[AnalysisResult],
    *,
    lookback: int = 10,
    report_date: Optional[str] = None,
) -> List[str]:
    try:
        entries = update_top5_history(region, results, report_date=report_date)
    except Exception:
        logger.warning("top5 brief stats update failed", exc_info=True)
        return []

    current_codes = _current_codes(results)
    if not current_codes or not entries:
        return []

    current_results = {
        str(getattr(item, "code", "") or "").strip().upper(): item
        for item in results
        if str(getattr(item, "code", "") or "").strip()
    }
    name_map = _code_to_name_map(results)
    recent = entries[-max(1, lookback):]
    frequency = Counter(code for entry in recent for code in entry["codes"])

    ranked = sorted(
        frequency.items(),
        key=lambda item: (-item[1], current_codes.index(item[0]) if item[0] in current_codes else 999, item[0]),
    )
    highlighted = [(code, count) for code, count in ranked if count >= 4]
    frequency_text = " / ".join(f"{_code_display(code, name_map)}({count})" for code, count in highlighted[:3])

    streak_parts: List[str] = []
    for code in current_codes:
        streak = 0
        for entry in reversed(entries):
            if code in entry["codes"]:
                streak += 1
            else:
                break
        if streak >= 2:
            streak_parts.append(f"{_code_display(code, name_map)}({streak})")
        if len(streak_parts) >= 3:
            break

    lines = [f"近{len(recent)}期Top5高频：{frequency_text or '暂无'}"]
    lines.append(f"连续上榜：{' / '.join(streak_parts) if streak_parts else '暂无'}")
    all_changes = _collect_backtest_changes(
        current_codes,
        current_results,
        entries,
        frequency,
        _resolve_report_date(report_date),
    )
    summary_line = _build_backtest_summary_line(all_changes)
    if summary_line:
        lines.append(summary_line)
    backtest_line = _build_backtest_line(current_codes, current_results, entries, frequency, _resolve_report_date(report_date), name_map=name_map)
    if backtest_line:
        lines.append(backtest_line)
    return lines
