"""Full-pool market foundations for the isolated V2 report.

The module provides two contracts that were previously implicit or mixed:

* exchange-calendar validation with an explicit timezone and session status;
* true stock-vs-market and stock-vs-industry excess returns.

It reads credentials only at runtime and never serializes them.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from dotenv import dotenv_values


CALENDAR_NAMES = {"hk": "XHKG", "us": "XNYS"}
MARKET_TIMEZONES = {"hk": "Asia/Hong_Kong", "us": "America/New_York"}
MARKET_BENCHMARKS = {
    "hk": ("2800.HK", "恒生指数ETF代理"),
    "us": ("SPY.US", "标普500 ETF"),
}

# The mapping is deliberately explicit and versionable.  It is preferable to
# silently guessing a sector from one day's generated prose.  Unknown symbols
# fall back to the broad-market benchmark and carry a fallback flag.
INDUSTRY_BENCHMARKS = {
    # Hong Kong technology / semiconductors / internet platform names.
    "HK00100": ("3033.HK", "恒生科技ETF代理"),
    "HK00148": ("3033.HK", "恒生科技ETF代理"),
    "HK00981": ("3033.HK", "恒生科技ETF代理"),
    "HK01300": ("3033.HK", "恒生科技ETF代理"),
    "HK01347": ("3033.HK", "恒生科技ETF代理"),
    "HK01617": ("3033.HK", "恒生科技ETF代理"),
    "HK01810": ("3033.HK", "恒生科技ETF代理"),
    "HK02476": ("3033.HK", "恒生科技ETF代理"),
    "HK02513": ("3033.HK", "恒生科技ETF代理"),
    "HK02577": ("3033.HK", "恒生科技ETF代理"),
    "HK03750": ("3033.HK", "恒生科技/新能源代理"),
    "HK03986": ("3033.HK", "恒生科技ETF代理"),
    "HK06088": ("3033.HK", "恒生科技ETF代理"),
    "HK06166": ("3033.HK", "恒生科技ETF代理"),
    "HK06809": ("3033.HK", "恒生科技ETF代理"),
    "HK00700": ("3033.HK", "恒生科技ETF代理"),
    "HK09618": ("3033.HK", "恒生科技ETF代理"),
    "HK09888": ("3033.HK", "恒生科技ETF代理"),
    "HK09992": ("3033.HK", "恒生科技ETF代理"),
    # US sector/theme ETFs confirmed available through the configured provider.
    "INTC": ("SMH.US", "半导体ETF"),
    "MU": ("SMH.US", "半导体ETF"),
    "MRVL": ("SMH.US", "半导体ETF"),
    "SNDK": ("SMH.US", "半导体ETF"),
    "IREN": ("SMH.US", "半导体/算力ETF代理"),
    "AXTI": ("SMH.US", "半导体ETF"),
    "COHR": ("SMH.US", "半导体ETF"),
    "NVDA": ("SMH.US", "半导体ETF"),
    "CRWV": ("SMH.US", "半导体/算力ETF代理"),
    "AAPL": ("QQQ.US", "纳斯达克100 ETF"),
    "NBIS": ("IGV.US", "软件ETF"),
    "PLTR": ("IGV.US", "软件ETF"),
    "GOOG": ("QQQ.US", "纳斯达克100 ETF"),
    "TSLA": ("XLY.US", "可选消费ETF"),
    "HIMS": ("XLV.US", "医疗ETF"),
    "NOK": ("IYZ.US", "通信ETF"),
    "ASTS": ("ARKX.US", "航天主题ETF"),
    "TE": ("ITA.US", "航空航天与工业ETF"),
    "CRCL": ("FINX.US", "金融科技ETF"),
}

BENCHMARK_CONTRACT_VERSION = "benchmark-map-v1"


@dataclass(frozen=True)
class CalendarCheck:
    market: str
    date: str
    timezone: str
    calendar: str
    is_trading_session: bool
    previous_session: str
    next_session: str


def validate_trading_date(day: date, market: str) -> CalendarCheck:
    if market not in CALENDAR_NAMES:
        raise ValueError(f"unsupported market: {market}")
    calendar = xcals.get_calendar(CALENDAR_NAMES[market])
    is_session = bool(calendar.is_session(day.isoformat()))
    if is_session:
        previous = calendar.previous_session(day.isoformat()).date().isoformat()
        following = calendar.next_session(day.isoformat()).date().isoformat()
    else:
        previous = calendar.date_to_session(day.isoformat(), direction="previous").date().isoformat()
        following = calendar.date_to_session(day.isoformat(), direction="next").date().isoformat()
    return CalendarCheck(
        market=market,
        date=day.isoformat(),
        timezone=MARKET_TIMEZONES[market],
        calendar=CALENDAR_NAMES[market],
        is_trading_session=is_session,
        previous_session=previous,
        next_session=following,
    )


def validate_effective_at(value: str, market: str) -> dict:
    """Validate timezone and derive the local market session date."""
    result = {"value": value, "market": market, "valid": False, "warnings": []}
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        result["warnings"].append("时间格式无法解析")
        return result
    if parsed.tzinfo is None:
        result["warnings"].append("时间缺少时区")
        return result
    local = parsed.astimezone(ZoneInfo(MARKET_TIMEZONES[market]))
    check = validate_trading_date(local.date(), market)
    result.update(
        {
            "valid": check.is_trading_session,
            "local_datetime": local.isoformat(),
            "local_date": local.date().isoformat(),
            "calendar": asdict(check),
        }
    )
    if not check.is_trading_session:
        result["warnings"].append("事件落在非交易日，需人工复核日期/时区")
    return result


def _normalize_symbol(symbol: str, market: str) -> str:
    value = str(symbol or "").strip().upper()
    if market == "hk":
        digits = "".join(character for character in value if character.isdigit())
        return f"HK{digits.zfill(5)}"
    return value.replace(".", "-")


def benchmark_spec(symbol: str, market: str) -> dict:
    normalized = _normalize_symbol(symbol, market)
    market_symbol, market_name = MARKET_BENCHMARKS[market]
    industry = INDUSTRY_BENCHMARKS.get(normalized)
    fallback = industry is None
    industry_symbol, industry_name = industry or (market_symbol, market_name)
    return {
        "mapping_version": BENCHMARK_CONTRACT_VERSION,
        "market_symbol": market_symbol,
        "market_name": market_name,
        "industry_symbol": industry_symbol,
        "industry_name": industry_name,
        "industry_fallback_to_market": fallback,
    }


def _return_pair(rows: Sequence[tuple[str, float]], report_date: date) -> dict:
    eligible = [(day, close) for day, close in rows if day <= report_date.isoformat()]
    eligible.sort(key=lambda item: item[0])
    if not eligible:
        return {"status": "missing"}
    latest_day, latest_close = eligible[-1]
    return {
        "status": "ok",
        "session_date": latest_day,
        "close": latest_close,
        "return_5d_pct": round((latest_close / eligible[-6][1] - 1) * 100, 4)
        if len(eligible) >= 6
        else None,
        "return_20d_pct": round((latest_close / eligible[-21][1] - 1) * 100, 4)
        if len(eligible) >= 21
        else None,
        "sessions": len(eligible),
    }


def load_stock_returns(
    db_path: Path, report_date: date, market: str, symbols: Iterable[str]
) -> dict[str, dict]:
    selected = {_normalize_symbol(symbol, market) for symbol in symbols}
    connection = sqlite3.connect(str(db_path))
    rows = connection.execute(
        "SELECT code,date,close FROM stock_daily WHERE date<=? ORDER BY code,date",
        (report_date.isoformat(),),
    ).fetchall()
    connection.close()
    histories: dict[str, list[tuple[str, float]]] = {}
    for raw_symbol, day, close in rows:
        normalized = _normalize_symbol(str(raw_symbol), "hk" if str(raw_symbol).startswith("HK") else "us")
        if normalized in selected and close is not None:
            histories.setdefault(normalized, []).append((str(day), float(close)))
    return {symbol: _return_pair(history, report_date) for symbol, history in histories.items()}


class LongbridgeBenchmarkClient:
    REQUIRED_KEYS = (
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
    )

    def __init__(self, env_files: Iterable[Path]):
        self.env_files = [Path(path) for path in env_files]

    def _load_environment(self) -> None:
        selected: Mapping[str, object] | None = None
        for path in self.env_files:
            if not path.exists():
                continue
            values = dotenv_values(path)
            if all(values.get(key) for key in self.REQUIRED_KEYS):
                selected = values
                break
        if selected is None:
            raise RuntimeError("Longbridge credentials are unavailable")
        for key in self.REQUIRED_KEYS:
            os.environ[key] = str(selected[key])

    def fetch(self, symbols: Iterable[str], report_date: date) -> dict[str, dict]:
        self._load_environment()
        from longbridge.openapi import AdjustType, Config, Period, QuoteContext

        factory = getattr(Config, "from_apikey_env", None) or getattr(Config, "from_env", None)
        if factory is None:
            raise RuntimeError("Installed Longbridge SDK cannot build configuration")
        context = QuoteContext(factory())
        output = {}
        for symbol in sorted(set(symbols)):
            market = "hk" if symbol.endswith(".HK") else "us"
            try:
                candles = context.candlesticks(symbol, Period.Day, 70, AdjustType.NoAdjust)
                history = []
                timezone = ZoneInfo(MARKET_TIMEZONES[market])
                for candle in candles:
                    timestamp = getattr(candle, "timestamp", None)
                    if isinstance(timestamp, datetime):
                        if timestamp.tzinfo is None:
                            from datetime import timezone as datetime_timezone

                            timestamp = timestamp.replace(tzinfo=datetime_timezone.utc)
                        timestamp = timestamp.astimezone(timezone)
                        day = timestamp.date().isoformat()
                    else:
                        continue
                    try:
                        close = float(getattr(candle, "close"))
                    except (TypeError, ValueError):
                        continue
                    history.append((day, close))
                output[symbol] = {
                    **_return_pair(history, report_date),
                    "provider": "longbridge",
                    "provider_symbol": symbol,
                }
            except Exception as exc:
                output[symbol] = {
                    "status": "error",
                    "provider": "longbridge",
                    "provider_symbol": symbol,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        return output


def build_relative_strength(
    *,
    db_path: Path,
    report_date: date,
    market: str,
    symbols: Iterable[str],
    env_files: Iterable[Path],
) -> dict[str, dict]:
    normalized = [_normalize_symbol(symbol, market) for symbol in symbols]
    stock_returns = load_stock_returns(db_path, report_date, market, normalized)
    specs = {symbol: benchmark_spec(symbol, market) for symbol in normalized}
    benchmark_symbols = {
        value
        for spec in specs.values()
        for value in (spec["market_symbol"], spec["industry_symbol"])
    }
    benchmarks = LongbridgeBenchmarkClient(env_files).fetch(benchmark_symbols, report_date)
    output = {}
    for symbol in normalized:
        stock = stock_returns.get(symbol, {"status": "missing"})
        spec = specs[symbol]
        broad = benchmarks.get(spec["market_symbol"], {"status": "missing"})
        industry = benchmarks.get(spec["industry_symbol"], {"status": "missing"})

        def excess(window: int, reference: Mapping[str, object]) -> float | None:
            stock_value = stock.get(f"return_{window}d_pct")
            reference_value = reference.get(f"return_{window}d_pct")
            if stock_value is None or reference_value is None:
                return None
            return round(float(stock_value) - float(reference_value), 4)

        market_excess_5 = excess(5, broad)
        market_excess_20 = excess(20, broad)
        industry_excess_5 = excess(5, industry)
        industry_excess_20 = excess(20, industry)
        status = (
            "ok"
            if stock.get("status") == broad.get("status") == industry.get("status") == "ok"
            else "partial"
            if stock.get("status") == "ok" and broad.get("status") == "ok"
            else "missing"
        )
        warnings = []
        if spec["industry_fallback_to_market"]:
            warnings.append("缺少可靠行业ETF映射，行业基准回退至市场基准")
        if stock.get("session_date") != report_date.isoformat():
            warnings.append("股票最新日线与报告日不一致")
        if broad.get("session_date") != report_date.isoformat():
            warnings.append("市场基准最新日线与报告日不一致")
        output[symbol] = {
            "status": status,
            "mapping_version": BENCHMARK_CONTRACT_VERSION,
            "stock": stock,
            "market_benchmark": {**spec, **broad},
            "industry_benchmark": {
                "symbol": spec["industry_symbol"],
                "name": spec["industry_name"],
                "fallback_to_market": spec["industry_fallback_to_market"],
                **industry,
            },
            "market_excess_5d_pct": market_excess_5,
            "market_excess_20d_pct": market_excess_20,
            "industry_excess_5d_pct": industry_excess_5,
            "industry_excess_20d_pct": industry_excess_20,
            "warnings": warnings,
        }
    return output
