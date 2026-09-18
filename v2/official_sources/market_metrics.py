"""Isolated Longbridge volume and daily-volatility metrics for V2.

The adapter deliberately keeps the live intraday volume ratio separate from
completed-session relative volume.  Credentials are read at runtime only and
are never included in returned payloads.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from dotenv import dotenv_values


def to_longbridge_symbol(symbol: str, market: str) -> str:
    value = str(symbol or "").strip().upper()
    if market == "hk":
        digits = "".join(character for character in value if character.isdigit())
        return f"{int(digits)}.HK" if digits else value
    return f"{value.replace('-', '.')}.US"


def _float(value: object) -> float | None:
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


MARKET_TIMEZONES = {
    "hk": ZoneInfo("Asia/Hong_Kong"),
    "us": ZoneInfo("America/New_York"),
}


def _candle_date(value: object, market: str = "") -> date | None:
    timestamp = getattr(value, "timestamp", None)
    if isinstance(timestamp, datetime):
        # Longbridge daily candles are timestamped in UTC.  Taking the raw UTC
        # date shifts Hong Kong sessions back by one day (16:00 UTC is the next
        # local calendar day), which previously made a completed 2026-08-21 HK
        # candle look like 2026-08-20.  Always convert aware timestamps to the
        # exchange timezone before deriving the session date.
        if market in MARKET_TIMEZONES:
            # The SDK currently returns naive datetimes that nevertheless carry
            # UTC clock values (HK daily candles arrive as 16:00 on the prior
            # UTC date).  Treat them as UTC before exchange conversion.
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamp = timestamp.astimezone(MARKET_TIMEZONES[market])
        return timestamp.date()
    try:
        return datetime.fromisoformat(str(timestamp)).date()
    except ValueError:
        return None


def _last_pivot(rows: Sequence[Mapping[str, float]], field: str) -> float | None:
    """Return the latest confirmed two-bar pivot, excluding the newest bar."""
    if len(rows) < 6:
        return None
    for index in range(len(rows) - 3, 1, -1):
        value = rows[index][field]
        neighbours = [rows[offset][field] for offset in (index - 2, index - 1, index + 1, index + 2)]
        if field == "high" and value > max(neighbours):
            return value
        if field == "low" and value < min(neighbours):
            return value
    return None


def _price_structure(rows: Sequence[Mapping[str, float]], atr14: float | None) -> dict:
    """Build price-action levels without using moving averages as trade levels."""
    if len(rows) < 21:
        return {"status": "insufficient_history", "history_sessions": len(rows)}

    latest = rows[-1]
    previous = rows[-2]
    prior = list(rows[-21:-1])
    recent5 = list(rows[-6:-1])
    previous5 = list(rows[-11:-6])
    prior_high = max(item["high"] for item in prior)
    prior_low = min(item["low"] for item in prior)
    recent_low = min(item["low"] for item in rows[-11:-1])
    recent_high = max(item["high"] for item in recent5)
    last_swing_high = _last_pivot(rows, "high")
    last_swing_low = _last_pivot(rows, "low")

    pivot_highs = []
    for index in range(2, len(rows) - 2):
        value = rows[index]["high"]
        neighbours = [rows[offset]["high"] for offset in (index - 2, index - 1, index + 1, index + 2)]
        if value > max(neighbours):
            pivot_highs.append(value)

    support_candidates = [recent_low]
    if last_swing_low is not None and last_swing_low < latest["close"]:
        support_candidates.append(last_swing_low)
    support_candidates.extend(value for value in pivot_highs if value < latest["close"])
    retest_level = max(support_candidates)
    next_resistances = sorted(
        {value for value in [prior_high, last_swing_high, *pivot_highs] if value is not None and value > latest["close"]}
    )

    recent_high_window = max(item["high"] for item in recent5)
    recent_low_window = min(item["low"] for item in recent5)
    previous_high_window = max(item["high"] for item in previous5)
    previous_low_window = min(item["low"] for item in previous5)
    if recent_high_window > previous_high_window and recent_low_window > previous_low_window:
        trend = "higher_high_higher_low"
    elif recent_high_window < previous_high_window and recent_low_window < previous_low_window:
        trend = "lower_high_lower_low"
    else:
        trend = "range_or_transition"

    width = prior_high - prior_low
    range_position = (latest["close"] - prior_low) / width * 100 if width > 0 else None
    tolerance = 0.35 * atr14 if atr14 is not None else max(latest["close"] * 0.01, 0.01)
    if latest["close"] > prior_high:
        breakout_state = "above_range"
    elif abs(latest["close"] - prior_high) <= tolerance:
        breakout_state = "testing_range_high"
    else:
        breakout_state = "inside_range"

    gap = "none"
    if latest.get("open") is not None and len(rows) >= 2:
        previous = rows[-2]
        if latest["open"] > previous["high"]:
            gap = "gap_up"
        elif latest["open"] < previous["low"]:
            gap = "gap_down"

    return {
        "status": "ok",
        "as_of": str(latest["date"]),
        # Keep the latest completed candle in the structure contract so a
        # pullback can be confirmed deterministically. A retest is not the
        # same setup as a breakout: contracting volume is often healthy when
        # price probes support and then closes back above it.
        "latest_open": round(latest["open"], 6) if latest.get("open") is not None else None,
        "latest_high": round(latest["high"], 6),
        "latest_low": round(latest["low"], 6),
        "latest_close": round(latest["close"], 6),
        "previous_close": round(previous["close"], 6),
        "trend": trend,
        "breakout_state": breakout_state,
        "breakout_level": round(prior_high, 6),
        "range_low_20d": round(prior_low, 6),
        "range_position_pct": round(range_position, 2) if range_position is not None else None,
        "recent_high_5d": round(recent_high, 6),
        "support_level": round(recent_low, 6),
        "retest_level": round(retest_level, 6),
        "last_swing_high": round(last_swing_high, 6) if last_swing_high is not None else None,
        "last_swing_low": round(last_swing_low, 6) if last_swing_low is not None else None,
        "next_resistance": round(next_resistances[0], 6) if next_resistances else None,
        "gap_state": gap,
        "history_sessions": len(rows),
    }


def calculate_history_metrics(
    candles: Sequence[object], report_date: date, market: str = ""
) -> dict:
    """Calculate ATR and complete-day relative volume from SDK-like candles."""
    completed = [
        item for item in candles if (_candle_date(item, market) or date.max) <= report_date
    ]
    completed.sort(key=lambda item: _candle_date(item, market) or date.min)
    rows = []
    for item in completed:
        open_price = _float(getattr(item, "open", None))
        close = _float(getattr(item, "close", None))
        high = _float(getattr(item, "high", None))
        low = _float(getattr(item, "low", None))
        volume = _float(getattr(item, "volume", None))
        if None not in (close, high, low, volume):
            rows.append(
                {
                    "date": (_candle_date(item, market) or report_date).isoformat(),
                    "open": open_price,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                }
            )
    if not rows:
        return {"history_status": "missing"}

    latest = rows[-1]

    def relative_volume(window: int) -> float | None:
        history = rows[-(window + 1) : -1]
        if len(history) < window:
            return None
        average = fmean(item["volume"] for item in history)
        return latest["volume"] / average if average > 0 else None

    true_ranges = []
    for index in range(1, len(rows)):
        current = rows[index]
        previous_close = rows[index - 1]["close"]
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous_close),
                abs(current["low"] - previous_close),
            )
        )
    atr14 = fmean(true_ranges[-14:]) if len(true_ranges) >= 14 else None
    structure = _price_structure(rows, atr14)
    return {
        "history_status": "ok",
        "completed_session_date": latest["date"],
        "completed_close": round(latest["close"], 6),
        "completed_volume": int(latest["volume"]),
        "relative_volume_5d": round(value, 4) if (value := relative_volume(5)) is not None else None,
        "relative_volume_20d": round(value, 4) if (value := relative_volume(20)) is not None else None,
        "atr14": round(atr14, 6) if atr14 is not None else None,
        "price_structure": structure,
        "history_sessions": len(rows),
    }


def normalize_volume_contract(metric: Mapping[str, object], report_date: date, market: str) -> dict:
    """Return an explicit, non-ambiguous volume confirmation contract.

    A Longbridge calc-index value is only usable as a live ratio when it was
    fetched on the report's local exchange date.  Historical reconstruction
    and weekend reruns must use the completed-session relative-volume value.
    """

    live_value = _float(metric.get("volume_ratio"))
    history_value = _float(metric.get("relative_volume_5d"))
    completed_day = str(metric.get("completed_session_date") or "")
    fetched_at = str(metric.get("fetched_at") or "")
    fetched_local_day = ""
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        fetched_local_day = fetched.astimezone(MARKET_TIMEZONES[market]).date().isoformat()
    except (KeyError, ValueError):
        pass

    live_applicable = (
        live_value is not None
        and str(metric.get("live_status") or "") == "ok"
        and fetched_local_day == report_date.isoformat()
    )
    history_applicable = history_value is not None and completed_day == report_date.isoformat()
    if live_applicable:
        chosen, source, scope = live_value, "longbridge_calc_index", "same_session_live"
    elif history_applicable:
        chosen, source, scope = history_value, "longbridge_daily_candles", "completed_session_5d"
    elif history_value is not None:
        chosen, source, scope = history_value, "longbridge_daily_candles", "stale_completed_session_5d"
    else:
        chosen, source, scope = None, "missing", "missing"

    warnings = []
    if live_value is not None and not live_applicable:
        warnings.append("实时量比与报告交易日不一致，未用于确认")
    if completed_day and completed_day != report_date.isoformat():
        warnings.append(f"最新完整日线为 {completed_day}，与报告日 {report_date.isoformat()} 不一致")
    return {
        "live_volume_ratio": round(live_value, 4) if live_value is not None else None,
        "live_applicable": live_applicable,
        "live_fetched_local_date": fetched_local_day,
        "historical_relative_volume_5d": round(history_value, 4) if history_value is not None else None,
        "historical_relative_volume_20d": metric.get("relative_volume_20d"),
        "completed_session_date": completed_day,
        "confirmation_value": round(chosen, 4) if chosen is not None else None,
        "confirmation_source": source,
        "confirmation_scope": scope,
        "warnings": warnings,
    }


class LongbridgeMarketMetricsClient:
    """Fetch a whole report pool's live ratio plus completed daily bars."""

    REQUIRED_KEYS = (
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
    )

    def __init__(self, env_files: Iterable[Path]):
        self.env_files = [Path(path) for path in env_files]

    def _load_runtime_environment(self) -> None:
        selected: Mapping[str, object] | None = None
        for path in self.env_files:
            if not path.exists():
                continue
            values = dotenv_values(path)
            if all(values.get(key) for key in self.REQUIRED_KEYS):
                selected = values
                break
        if selected is None:
            raise RuntimeError("Longbridge credentials are unavailable in the supplied env files")
        for key in self.REQUIRED_KEYS:
            os.environ[key] = str(selected[key])
        # Only copy non-secret connection preferences. Empty SDK URL values are
        # invalid and must not override the SDK defaults.
        for key in (
            "LONGBRIDGE_HTTP_URL",
            "LONGBRIDGE_QUOTE_WS_URL",
            "LONGBRIDGE_TRADE_WS_URL",
            "LONGBRIDGE_REGION",
            "LONGPORT_REGION",
        ):
            value = selected.get(key)
            if value:
                os.environ[key] = str(value)
            elif not os.environ.get(key):
                os.environ.pop(key, None)

    def fetch(
        self,
        items: Mapping[str, Mapping[str, object]],
        *,
        default_report_date: date,
    ) -> dict[str, dict]:
        self._load_runtime_environment()
        from longbridge.openapi import AdjustType, CalcIndex, Config, Period, QuoteContext

        factory = getattr(Config, "from_apikey_env", None) or getattr(Config, "from_env", None)
        if factory is None:
            raise RuntimeError("Installed Longbridge SDK cannot build configuration from environment")
        context = QuoteContext(factory())
        symbol_map = {
            symbol: to_longbridge_symbol(symbol, str(item.get("market") or ""))
            for symbol, item in items.items()
        }
        live_by_symbol = {}
        live_error = ""
        try:
            rows = context.calc_indexes(
                list(symbol_map.values()),
                [CalcIndex.VolumeRatio, CalcIndex.Volume, CalcIndex.TurnoverRate],
            )
            live_by_symbol = {str(row.symbol): row for row in rows}
        except Exception as exc:  # one failed batch must not remove daily metrics
            live_error = f"{type(exc).__name__}: {exc}"

        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        result: dict[str, dict] = {}
        for symbol, item in items.items():
            provider_symbol = symbol_map[symbol]
            try:
                report_day = date.fromisoformat(str(item.get("report_date") or ""))
            except ValueError:
                report_day = default_report_date
            metric = {
                "symbol": symbol,
                "market": item.get("market"),
                "provider_symbol": provider_symbol,
                "fetched_at": fetched_at,
                "volume_ratio_scope": "live_intraday",
                "volume_ratio_source": "longbridge_calc_index",
            }
            row = live_by_symbol.get(provider_symbol)
            if row is not None:
                metric.update(
                    {
                        "volume_ratio": _float(getattr(row, "volume_ratio", None)),
                        "live_volume": _float(getattr(row, "volume", None)),
                        "turnover_rate": _float(getattr(row, "turnover_rate", None)),
                        "live_status": "ok",
                    }
                )
            else:
                metric.update({"volume_ratio": None, "live_status": "missing", "live_error": live_error})
            try:
                candles = context.candlesticks(provider_symbol, Period.Day, 45, AdjustType.NoAdjust)
                metric.update(
                    calculate_history_metrics(
                        candles, report_day, str(item.get("market") or "")
                    )
                )
            except Exception as exc:
                metric.update({"history_status": "error", "history_error": f"{type(exc).__name__}: {exc}"})
            metric["status"] = (
                "ok"
                if metric.get("live_status") == "ok" and metric.get("history_status") == "ok"
                else "partial"
                if metric.get("live_status") == "ok" or metric.get("history_status") == "ok"
                else "error"
            )
            volume_contract = normalize_volume_contract(
                metric, report_day, str(item.get("market") or "")
            )
            metric["volume_contract"] = volume_contract
            metric["volume_confirmation_value"] = volume_contract["confirmation_value"]
            metric["volume_confirmation_source"] = volume_contract["confirmation_source"]
            metric["volume_confirmation_scope"] = volume_contract["confirmation_scope"]
            result[symbol] = metric
        return result
