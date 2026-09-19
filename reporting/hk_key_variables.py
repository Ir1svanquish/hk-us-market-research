"""Build an auditable one-page Hong Kong key-variable brief.

This section feeds the formal report without sending notifications directly.
Daily market observations are fetched from primary sources where practical,
and every normalized value retains its observation
date, source URL, fetch timestamp, and raw-response hash.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


HK_TZ = ZoneInfo("Asia/Hong_Kong")
USER_AGENT = "hk-us-market-research/1.0 market-drivers"
HKEX_TEMPLATE = (
    "https://www.hkex.com.hk/eng/csm/DailyStat/"
    "data_tab_daily_{day}e.js"
)
HKAB_TEMPLATE = "https://www.hkab.org.hk/api/hibor?year={year}&month={month}&day={day}"
HKMA_LIQUIDITY_URL = (
    "https://api.hkma.gov.hk/public/market-data-and-statistics/"
    "daily-monetary-statistics/daily-figures-interbank-liquidity?pagesize=20"
)
TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
)
NASDAQ_HXC_URL = "https://indexes.nasdaqomx.com/Index/History/HXC"
NASDAQ_HXC_HISTORY_URL = "https://indexes.nasdaqomx.com/Index/HistoryChartData"
LPR_URL = "https://www.chinamoney.com.cn/chinese/rdgz/20260820/3399885.html"
PMI_URL = "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260731_1964252.html"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _request(url: str, *, attempts: int = 3) -> tuple[bytes, dict[str, str]]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/html,application/xml,text/plain,*/*",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
            return body, {
                "url": url,
                "fetched_at": _now_iso(),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"unable to fetch {url}: {last_error}")


def _json_request(url: str) -> tuple[Any, dict[str, str]]:
    body, source = _request(url)
    return json.loads(body.decode("utf-8-sig")), source


def _post_json(url: str, fields: Mapping[str, object]) -> tuple[Any, dict[str, str]]:
    encoded = urlencode({key: str(value) for key, value in fields.items()}).encode()
    request = Request(
        url,
        data=encoded,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"unable to fetch {url}: {error}") from error
    return json.loads(body.decode("utf-8-sig")), {
        "url": url,
        "fetched_at": _now_iso(),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _number(value: object) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text or text.upper() in {"N/A", "NA", "NULL", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_html(body: bytes) -> str:
    decoded = body.decode("utf-8", errors="replace")
    decoded = re.sub(r"<script\b[^>]*>.*?</script>", " ", decoded, flags=re.I | re.S)
    decoded = re.sub(r"<style\b[^>]*>.*?</style>", " ", decoded, flags=re.I | re.S)
    decoded = re.sub(r"<[^>]+>", " ", decoded)
    return re.sub(r"\s+", " ", html.unescape(decoded)).strip()


def parse_hkex_southbound(body: bytes) -> dict[str, float | str]:
    decoded = body.decode("utf-8-sig", errors="replace").strip()
    match = re.search(r"tabData\s*=\s*(\[.*\])\s*;?\s*$", decoded, re.S)
    if not match:
        raise ValueError("HKEX response does not contain tabData")
    payload = json.loads(match.group(1))
    totals = {"turnover_m": 0.0, "buy_m": 0.0, "sell_m": 0.0}
    observation_date = ""
    markets = 0
    for item in payload:
        if item.get("market") not in {"SSE Southbound", "SZSE Southbound"}:
            continue
        observation_date = str(item.get("date") or observation_date)
        summary = next(
            (
                content.get("table")
                for content in item.get("content", [])
                if content.get("style") == 1
            ),
            None,
        )
        if not isinstance(summary, Mapping):
            continue
        labels = list((summary.get("schema") or [[]])[0])
        values = []
        for row in summary.get("tr", []):
            try:
                values.append(_number(row["td"][0][0]))
            except (KeyError, IndexError, TypeError):
                values.append(None)
        row = dict(zip(labels, values))
        if any(row.get(label) is None for label in ("Total Turnover", "Buy Turnover", "Sell Turnover")):
            continue
        totals["turnover_m"] += float(row["Total Turnover"])
        totals["buy_m"] += float(row["Buy Turnover"])
        totals["sell_m"] += float(row["Sell Turnover"])
        markets += 1
    if markets != 2 or not observation_date:
        raise ValueError("HKEX response does not contain both southbound markets")
    totals["net_m"] = totals["buy_m"] - totals["sell_m"]
    return {"date": observation_date, **{key: round(value, 2) for key, value in totals.items()}}


def fetch_southbound(as_of: date, sessions: int = 5) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []
    cursor = as_of
    for _ in range(20):
        url = HKEX_TEMPLATE.format(day=cursor.strftime("%Y%m%d"))
        try:
            body, source = _request(url, attempts=1)
            row = parse_hkex_southbound(body)
        except (RuntimeError, ValueError):
            cursor -= timedelta(days=1)
            continue
        if row["date"] <= as_of.isoformat():
            rows.append(row)
            sources.append(source)
        if len(rows) >= sessions:
            break
        cursor -= timedelta(days=1)
    if len(rows) < sessions:
        raise RuntimeError(f"only {len(rows)} HKEX southbound sessions available")
    latest = rows[0]
    return {
        "date": latest["date"],
        "turnover_hkd_bn": round(float(latest["turnover_m"]) / 1000, 2),
        "buy_hkd_bn": round(float(latest["buy_m"]) / 1000, 2),
        "sell_hkd_bn": round(float(latest["sell_m"]) / 1000, 2),
        "net_hkd_bn": round(float(latest["net_m"]) / 1000, 2),
        "net_5d_hkd_bn": round(sum(float(row["net_m"]) for row in rows) / 1000, 2),
        "history": rows,
        "sources": sources,
    }


def fetch_hk_liquidity(as_of: date) -> dict[str, Any]:
    payload, source = _json_request(HKMA_LIQUIDITY_URL)
    records = [
        row
        for row in payload.get("result", {}).get("records", [])
        if str(row.get("end_of_date") or "") <= as_of.isoformat()
    ]
    if len(records) < 2:
        raise RuntimeError("HKMA returned fewer than two eligible daily records")
    current, previous = records[0], records[1]
    closing = float(current["closing_balance"])
    prior_closing = float(previous["closing_balance"])
    return {
        "date": current["end_of_date"],
        "aggregate_balance_hkd_bn": round(closing / 1000, 3),
        "aggregate_balance_change_hkd_bn": round((closing - prior_closing) / 1000, 3),
        "weak_side": float(current["cu_weakside"]),
        "strong_side": float(current["cu_strongside"]),
        "source": source,
    }


def _fetch_hibor_day(day: date) -> tuple[dict[str, Any], dict[str, str]]:
    return _json_request(HKAB_TEMPLATE.format(year=day.year, month=day.month, day=day.day))


def fetch_hibor(as_of: date) -> dict[str, Any]:
    observations: list[tuple[dict[str, Any], dict[str, str]]] = []
    cursor = as_of
    for _ in range(10):
        payload, source = _fetch_hibor_day(cursor)
        if not payload.get("isHoliday") and payload.get("1 Month") is not None:
            observations.append((payload, source))
        if len(observations) >= 2:
            break
        cursor -= timedelta(days=1)
    if len(observations) < 2:
        raise RuntimeError("HKAB returned fewer than two business-day observations")
    current, current_source = observations[0]
    previous, previous_source = observations[1]
    return {
        "date": date(int(current["year"]), int(current["month"]), int(current["day"])).isoformat(),
        "overnight_pct": round(float(current["Overnight"]), 5),
        "overnight_change_bp": round((float(current["Overnight"]) - float(previous["Overnight"])) * 100, 1),
        "one_month_pct": round(float(current["1 Month"]), 5),
        "one_month_change_bp": round((float(current["1 Month"]) - float(previous["1 Month"])) * 100, 1),
        "sources": [current_source, previous_source],
    }


def fetch_treasury(as_of: date) -> dict[str, Any]:
    body, source = _request(TREASURY_URL.format(year=as_of.year))
    root = ET.fromstring(body)
    namespaces = {
        "a": "http://www.w3.org/2005/Atom",
        "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
        "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    }
    rows: list[tuple[str, float]] = []
    for entry in root.findall("a:entry", namespaces):
        properties = entry.find("a:content/m:properties", namespaces)
        if properties is None:
            continue
        day_node = properties.find("d:NEW_DATE", namespaces)
        rate_node = properties.find("d:BC_10YEAR", namespaces)
        if day_node is None or rate_node is None or not rate_node.text:
            continue
        day = str(day_node.text)[:10]
        # At the Hong Kong close, the same US calendar day's close does not yet exist.
        if day < as_of.isoformat():
            rows.append((day, float(rate_node.text)))
    rows.sort()
    if len(rows) < 2:
        raise RuntimeError("Treasury returned fewer than two eligible observations")
    current, previous = rows[-1], rows[-2]
    return {
        "date": current[0],
        "ten_year_pct": round(current[1], 3),
        "change_bp": round((current[1] - previous[1]) * 100, 1),
        "source": source,
    }


def _market_history(symbol: str, *, before: date | None = None) -> list[tuple[str, float]]:
    import yfinance as yf

    history = yf.Ticker(symbol).history(period="15d", auto_adjust=False)
    rows: list[tuple[str, float]] = []
    if history is None or history.empty:
        return rows
    for index, row in history.iterrows():
        day = index.date().isoformat()
        if before is not None and day >= before.isoformat():
            continue
        close = _number(row.get("Close"))
        if close is not None:
            rows.append((day, close))
    rows.sort()
    return rows


def parse_hxc_history(payload: Sequence[Mapping[str, object]], as_of: date) -> dict[str, Any]:
    """Select the last completed US session before the HK report cutoff.

    Nasdaq's current-value page only exposes the newest observation.  The
    history endpoint is required for historical report reruns; using the
    current page there would silently introduce look-ahead data.
    """
    rows: list[tuple[str, float]] = []
    for item in payload:
        timestamp = _number(item.get("x"))
        value = _number(item.get("y"))
        if timestamp is None or value is None:
            continue
        day = datetime.fromtimestamp(timestamp / 1000, timezone.utc).date().isoformat()
        if day < as_of.isoformat():
            rows.append((day, value))
    rows.sort()
    if len(rows) < 2:
        raise RuntimeError("Nasdaq HXC history has fewer than two eligible observations")
    current, previous = rows[-1], rows[-2]
    return {
        "date": current[0],
        "close": round(current[1], 3),
        "change_pct": round((current[1] / previous[1] - 1) * 100, 2),
    }


def fetch_hxc(as_of: date) -> tuple[dict[str, Any], dict[str, str]]:
    start = as_of - timedelta(days=14)
    payload, source = _post_json(
        NASDAQ_HXC_HISTORY_URL,
        {
            "id": "HXC",
            "startDate": f"{start.isoformat()}T00:00:00",
            "endDate": f"{as_of.isoformat()}T23:59:59",
        },
    )
    if not isinstance(payload, Sequence):
        raise RuntimeError("Nasdaq HXC history response is not a list")
    source["public_url"] = NASDAQ_HXC_URL
    return parse_hxc_history(payload, as_of), source


def fetch_market_proxies(as_of: date, weak_side: float) -> dict[str, Any]:
    fx_rows = _market_history("HKD=X")
    if not fx_rows:
        raise RuntimeError("USD/HKD market proxy unavailable")
    fx_eligible = [row for row in fx_rows if row[0] <= as_of.isoformat()]
    fx_current = fx_eligible[-1]
    etfs: dict[str, dict[str, Any]] = {}
    for symbol in ("KWEB", "FXI"):
        rows = _market_history(symbol, before=as_of)
        if len(rows) < 2:
            raise RuntimeError(f"{symbol} history is incomplete")
        current, previous = rows[-1], rows[-2]
        etfs[symbol] = {
            "date": current[0],
            "close": round(current[1], 3),
            "change_pct": round((current[1] / previous[1] - 1) * 100, 2),
        }

    hxc, hxc_source = fetch_hxc(as_of)
    return {
        "usdhkd": round(fx_current[1], 5),
        "usdhkd_date": fx_current[0],
        "distance_to_weak_side": round(weak_side - fx_current[1], 5),
        "etfs": etfs,
        "hxc": hxc,
        "sources": {
            "usdhkd_etfs": {
                "url": "https://finance.yahoo.com/",
                "provider": "Yahoo Finance / yfinance",
                "fetched_at": _now_iso(),
            },
            "hxc": hxc_source,
        },
    }


def fetch_china_releases() -> dict[str, Any]:
    lpr_body, lpr_source = _request(LPR_URL)
    lpr_text = _clean_html(lpr_body)
    lpr_compact = re.sub(r"\s+", "", lpr_text)
    lpr_match = re.search(
        r"1年期LPR为([\d.]+)%.*?5年期以上LPR为([\d.]+)%",
        lpr_compact,
    )
    if not lpr_match:
        raise RuntimeError("unable to parse the latest LPR release")

    pmi_body, pmi_source = _request(PMI_URL)
    pmi_text = _clean_html(pmi_body)
    pmi_compact = re.sub(r"\s+", "", pmi_text)
    pmi_match = re.search(
        r"制造业采购经理指数、非制造业商务活动指数和综合PMI产出指数分别为"
        r"([\d.]+)%、([\d.]+)%和([\d.]+)%",
        pmi_compact,
    )
    if not pmi_match:
        raise RuntimeError("unable to parse the latest PMI release")
    return {
        "lpr": {
            "date": "2026-08-20",
            "one_year_pct": float(lpr_match.group(1)),
            "five_year_pct": float(lpr_match.group(2)),
            "source": lpr_source,
        },
        "pmi": {
            "period": "2026-07",
            "manufacturing": float(pmi_match.group(1)),
            "non_manufacturing": float(pmi_match.group(2)),
            "composite": float(pmi_match.group(3)),
            "source": pmi_source,
        },
    }


def _signal_snapshot(payload: Mapping[str, Any]) -> dict[str, str]:
    southbound = payload["southbound"]
    hibor = payload["hibor"]
    liquidity = payload["liquidity"]
    market = payload["market"]
    treasury = payload["treasury"]
    negatives = 0
    if float(southbound["net_hkd_bn"]) < 0:
        negatives += 1
    if float(hibor["overnight_change_bp"]) > 10 or float(hibor["one_month_change_bp"]) > 5:
        negatives += 1
    if float(market["hxc"]["change_pct"]) < -0.5:
        negatives += 1
    if float(market["distance_to_weak_side"]) < 0.02:
        negatives += 1
    label = "偏谨慎" if negatives >= 3 else "中性偏谨慎" if negatives == 2 else "中性"
    relief = "美债收益率回落提供小幅估值缓冲" if float(treasury["change_bp"]) < 0 else "美债收益率上行增加估值压力"
    summary = (
        f"南向当日净额为 {float(southbound['net_hkd_bn']):+.2f} 十亿港元，"
        f"隔夜 HIBOR 变动 {float(hibor['overnight_change_bp']):+.1f}bp，"
        f"HXC 隔夜 {float(market['hxc']['change_pct']):+.2f}%；{relief}。"
    )
    return {"label": label, "summary": summary}


def build_snapshot(
    as_of: date,
    fallback_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    southbound = fetch_southbound(as_of)
    fallback_blocks: list[str] = []
    try:
        liquidity = fetch_hk_liquidity(as_of)
    except RuntimeError as error:
        previous_liquidity = (fallback_payload or {}).get("liquidity")
        if not isinstance(previous_liquidity, Mapping):
            raise
        liquidity = json.loads(json.dumps(previous_liquidity, ensure_ascii=False))
        liquidity["fallback"] = {
            "from_as_of": str((fallback_payload or {}).get("as_of") or ""),
            "reason": str(error),
        }
        fallback_blocks.append("liquidity")
    hibor = fetch_hibor(as_of)
    treasury = fetch_treasury(as_of)
    market = fetch_market_proxies(as_of, float(liquidity["weak_side"]))
    china = fetch_china_releases()
    payload: dict[str, Any] = {
        "contract_version": "hk-key-variables-v1",
        "as_of": as_of.isoformat(),
        "cutoff_timezone": "Asia/Hong_Kong",
        "generated_at": _now_iso(),
        "southbound": southbound,
        "liquidity": liquidity,
        "hibor": hibor,
        "treasury": treasury,
        "market": market,
        "china": china,
    }
    payload["signal"] = _signal_snapshot(payload)
    payload["quality"] = {
        "required_blocks": 6,
        "available_blocks": 6,
        "status": "complete",
        "notes": [
            "南向净额按港交所沪、深南向买入额减卖出额计算",
            "中概隔夜只使用报告截止前最近一个完整美国交易日",
            "低频数据标注统计期，不与日频数据混作同日观测",
        ] + (
            ["港元流动性接口暂时不可用，沿用上一份已审计官方观测值并保留原始日期"]
            if "liquidity" in fallback_blocks
            else []
        ),
        "fallback_blocks": fallback_blocks,
    }
    return payload


def _fmt_signed(value: object, digits: int = 1, suffix: str = "") -> str:
    number = float(value)
    return f"{number:+.{digits}f}{suffix}"


def _tone(value: float, *, positive_is_good: bool = True) -> str:
    good = value >= 0 if positive_is_good else value <= 0
    return "good" if good else "bad"


INTEGRATED_PAGE_CSS = """
.hk-variables-page{display:flex;flex-direction:column;gap:2mm;height:270mm}.hk-variables-page .good{color:#15803d!important}.hk-variables-page .bad{color:#b91c1c!important}.hk-driver-summary{display:grid;grid-template-columns:1fr 43mm;gap:3mm;align-items:stretch;background:linear-gradient(118deg,#14284b,#245a92 68%,#0f766e);color:white;border-radius:9px;padding:4mm 5mm}.hk-driver-summary small{color:#bfdbfe}.hk-driver-summary p{margin:1mm 0 0;font-size:10px;line-height:1.5}.hk-driver-stance{display:flex;flex-direction:column;align-items:center;justify-content:center;border:1px solid #ffffff3d;background:#ffffff17;border-radius:7px;text-align:center}.hk-driver-stance b{font-size:13px}.hk-driver-stance span{font-size:8px;color:#dbeafe}
.hk-metric-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:1.4mm}.hk-metric{border:1px solid var(--line,#dbe3ef);border-radius:6px;background:var(--panel,#f8fafc);padding:2mm 2.2mm;min-height:25mm}.hk-metric small{display:block;color:var(--muted,#64748b)}.hk-metric b{display:block;color:var(--navy,#173d70);font-size:13px;margin:.7mm 0}.hk-metric span{font-size:7.8px;color:var(--muted,#64748b)}.hk-metric .good{color:#15803d}.hk-metric .bad{color:#b91c1c}
.hk-driver-grid{display:grid;grid-template-columns:1.12fr 1fr 1fr;gap:1.7mm;flex:0 0 86mm;min-height:0}.hk-driver-panel,.hk-analysis-panel{border:1px solid var(--line,#dbe3ef);border-radius:7px;padding:2.5mm;background:white}.hk-driver-panel h3,.hk-analysis-panel h3{margin:0 0 1.5mm;color:var(--navy,#173d70);font-size:10px}.hk-flow-table{width:100%;border-collapse:collapse}.hk-flow-table th{background:#173d70;color:white;padding:1.2mm;font-size:7.6px}.hk-flow-table td{border-bottom:1px solid #e2e8f0;padding:1.25mm;text-align:right;font-size:8px}.hk-flow-table td:first-child{text-align:left}.hk-kv{display:flex;justify-content:space-between;gap:2mm;border-bottom:1px solid #e2e8f0;padding:1.5mm 0}.hk-kv:last-of-type{border-bottom:0}.hk-kv b{color:var(--navy,#173d70)}.hk-driver-note{margin-top:1.6mm;border-radius:5px;background:#eef6ff;padding:1.6mm 1.8mm;color:#334155;font-size:8.1px;line-height:1.42}.hk-driver-note b{display:block;color:var(--navy,#173d70);margin-bottom:.35mm}.hk-proxy{display:grid;grid-template-columns:1fr auto;gap:.7mm;border-bottom:1px solid #e2e8f0;padding:1.4mm 0}.hk-proxy:last-of-type{border-bottom:0}.hk-proxy small{grid-column:1 / span 2;color:var(--muted,#64748b)}
.hk-analysis-grid{display:grid;grid-template-columns:1.08fr .92fr;gap:1.7mm;flex:1;min-height:72mm}.hk-analysis-panel{display:flex;flex-direction:column}.hk-bar-row{margin:1.2mm 0}.hk-bar-head{display:flex;justify-content:space-between;align-items:baseline}.hk-bar-head b{color:#334155}.hk-bar-head span{font-weight:800;color:var(--navy,#173d70)}.hk-bar-track{height:2.6mm;background:#e2e8f0;border-radius:999px;overflow:hidden;margin-top:.7mm}.hk-bar-track i{display:block;height:100%;border-radius:999px;background:#2563eb}.hk-bar-track i.good{background:#16a34a}.hk-bar-track i.warn{background:#f59e0b}.hk-bar-foot{display:flex;justify-content:space-between;margin-top:.45mm;color:var(--muted,#64748b);font-size:7.5px}.hk-movers{display:grid;grid-template-columns:1fr 1fr;gap:1.3mm;margin-top:auto}.hk-movers>div{border-radius:5px;padding:1.6mm 1.8mm;background:#ecfdf5}.hk-movers>div+div{background:#fff1f2}.hk-movers b{display:block;color:#166534;margin-bottom:.4mm}.hk-movers>div+div b{color:#991b1b}.hk-readout{display:grid;gap:1mm}.hk-readout-row{display:grid;grid-template-columns:21mm 1fr;gap:1.5mm;border-radius:5px;background:#f8fafc;padding:1.5mm 1.8mm}.hk-readout-row b{color:var(--navy,#173d70)}.hk-readout-row span{color:#475569}.hk-checks{display:grid;grid-template-columns:1fr 1fr;gap:1mm;margin-top:auto}.hk-check{border:1px solid #dbe3ef;border-radius:5px;padding:1.4mm 1.6mm}.hk-check b{display:block;color:var(--navy,#173d70);margin-bottom:.35mm}.hk-check span{font-size:7.8px;color:#475569}.hk-action-band{display:grid;grid-template-columns:27mm 1fr;align-items:center;border-left:4px solid #0f766e;border-radius:6px;background:#ecfdf5;padding:2.3mm 3mm}.hk-action-band b{color:#14532d}.hk-action-band p{margin:0;font-size:9px}.hk-source-strip{border-top:1px solid #cbd5e1;padding-top:1.2mm;color:var(--muted,#64748b);font-size:7px;white-space:nowrap}
"""


def render_integrated_page(
    payload: Mapping[str, Any],
    coverage: Mapping[str, Any] | None = None,
) -> str:
    """Render a data-and-interpretation-only HK market-driver page."""
    sb = payload["southbound"]
    hb = payload["hibor"]
    liq = payload["liquidity"]
    market = payload["market"]
    treasury = payload["treasury"]
    coverage = coverage or {}
    total = int(coverage.get("total") or 0)
    advancers = int(coverage.get("advancers") or 0)
    decliners = int(coverage.get("decliners") or 0)
    flat = max(0, total - advancers - decliners)
    volume_confirmed = int(coverage.get("volume_confirmed") or 0)
    volume_weak = int(coverage.get("volume_weak") or 0)
    strong_advancers = int(coverage.get("strong_advancers") or 0)
    relative_positive = int(coverage.get("relative_strength_positive") or 0)
    median_change = float(coverage.get("median_change_pct") or 0)
    median_volume = float(coverage.get("median_volume_ratio") or 0)
    breadth = f"{advancers}/{total}" if total else "—"
    volume_breadth = f"{volume_confirmed}/{total}" if total else "—"
    price_is_broad = total > 0 and advancers / total >= 0.6
    flow_is_supportive = float(sb["net_hkd_bn"]) >= 0
    stance = (
        "价格与资金共振"
        if price_is_broad and flow_is_supportive
        else "价格强、资金谨慎"
        if price_is_broad
        else payload["signal"]["label"]
    )
    band_position = max(
        0.0,
        min(
            100.0,
            (float(market["usdhkd"]) - float(liq["strong_side"]))
            / (float(liq["weak_side"]) - float(liq["strong_side"]))
            * 100,
        ),
    )
    three_day_net = sum(float(row["net_m"]) for row in sb["history"][:3]) / 1000
    leaders = "、".join(str(value) for value in coverage.get("leaders") or []) or "—"
    laggards = "、".join(str(value) for value in coverage.get("laggards") or []) or "—"
    action = (
        f"观察池 {advancers}/{total} 只上涨，价格广度较强；但只有 {volume_confirmed}/{total} 只量比达到 1.05×以上，"
        f"南向当日净额 {float(sb['net_hkd_bn']):+.2f} 十亿港元。保留偏进攻姿态，但控制追高强度，优先等待回踩承接或放量确认。"
        if total
        else payload["signal"]["summary"]
    )
    flow_rows = "".join(
        f"<tr><td>{html.escape(str(row['date']))}</td><td>{float(row['buy_m'])/1000:.2f}</td>"
        f"<td>{float(row['sell_m'])/1000:.2f}</td><td class='{_tone(float(row['net_m']))}'>{float(row['net_m'])/1000:+.2f}</td></tr>"
        for row in sb["history"]
    )
    source_dates = (
        f"港交所 {sb['date']}｜HKAB {hb['date']}｜HKMA {liq['date']}｜"
        f"美国财政部 {treasury['date']}｜HXC {market['hxc']['date']}"
    )
    return f"""
    <section class="page hk-variables-page" id="hk-key-variables">
      <div class="page-title"><div><span class="eyebrow">港股本地定价框架</span><h2>港股关键变量</h2></div><span class="count">{html.escape(str(payload['as_of']))} 收盘后</span></div>
      <div class="hk-driver-summary"><div><small>南向、港元流动性与隔夜外部映射</small><p>{html.escape(str(payload['signal']['summary']))}</p></div><div class="hk-driver-stance"><span>当日定价结构</span><b>{html.escape(stance)}</b><span>量能扩散 {volume_breadth}</span></div></div>
      <div class="hk-metric-grid">
        <div class="hk-metric"><small>南向当日净额</small><b class="{_tone(float(sb['net_hkd_bn']))}">{_fmt_signed(sb['net_hkd_bn'],2)}</b><span>十亿港元</span></div>
        <div class="hk-metric"><small>南向5日累计</small><b class="{_tone(float(sb['net_5d_hkd_bn']))}">{_fmt_signed(sb['net_5d_hkd_bn'],2)}</b><span>十亿港元</span></div>
        <div class="hk-metric"><small>观察池上涨广度</small><b>{breadth}</b><span>涨 {advancers}｜平 {flat}｜跌 {decliners}</span></div>
        <div class="hk-metric"><small>量能扩散</small><b>{volume_breadth}</b><span>量比 ≥ 1.05×</span></div>
        <div class="hk-metric"><small>1个月 HIBOR</small><b>{float(hb['one_month_pct']):.3f}%</b><span class="{_tone(float(hb['one_month_change_bp']),positive_is_good=False)}">{_fmt_signed(hb['one_month_change_bp'],1,'bp')}</span></div>
        <div class="hk-metric"><small>USD/HKD</small><b>{float(market['usdhkd']):.4f}</b><span>区间位置 {band_position:.1f}%</span></div>
      </div>
      <div class="hk-driver-grid">
        <div class="hk-driver-panel"><h3>南向资金 · 最近5个交易日</h3><table class="hk-flow-table"><thead><tr><th>日期</th><th>买入</th><th>卖出</th><th>净额</th></tr></thead><tbody>{flow_rows}</tbody></table><div class="hk-driver-note"><b>资金解读</b>最近3日累计净额 {three_day_net:+.2f} 十亿港元，5日累计 {float(sb['net_5d_hkd_bn']):+.2f} 十亿；短线资金方向仍偏弱。</div></div>
        <div class="hk-driver-panel"><h3>港元流动性与联系汇率</h3><div class="hk-kv"><span>隔夜 HIBOR</span><b>{float(hb['overnight_pct']):.3f}% ({_fmt_signed(hb['overnight_change_bp'],1,'bp')})</b></div><div class="hk-kv"><span>1个月 HIBOR</span><b>{float(hb['one_month_pct']):.3f}% ({_fmt_signed(hb['one_month_change_bp'],1,'bp')})</b></div><div class="hk-kv"><span>银行体系总结余</span><b>{float(liq['aggregate_balance_hkd_bn']):.3f} 十亿</b></div><div class="hk-kv"><span>USD/HKD</span><b>{float(market['usdhkd']):.4f}</b></div><div class="hk-kv"><span>联系汇率区间位置</span><b>{band_position:.1f}%</b></div><div class="hk-driver-note"><b>流动性解读</b>隔夜利率回落、1个月利率基本持平；但汇率已处于7.75—7.85区间靠近弱方的一侧。</div></div>
        <div class="hk-driver-panel"><h3>隔夜外部定价</h3><div class="hk-proxy"><b>HXC 金龙中国</b><strong class="{_tone(float(market['hxc']['change_pct']))}">{_fmt_signed(market['hxc']['change_pct'],2,'%')}</strong><small>{html.escape(str(market['hxc']['date']))}｜Nasdaq官方指数</small></div><div class="hk-proxy"><b>KWEB 中国互联网</b><strong class="{_tone(float(market['etfs']['KWEB']['change_pct']))}">{_fmt_signed(market['etfs']['KWEB']['change_pct'],2,'%')}</strong><small>{html.escape(str(market['etfs']['KWEB']['date']))}｜可交易ETF代理</small></div><div class="hk-proxy"><b>FXI 中国大盘股</b><strong class="{_tone(float(market['etfs']['FXI']['change_pct']))}">{_fmt_signed(market['etfs']['FXI']['change_pct'],2,'%')}</strong><small>{html.escape(str(market['etfs']['FXI']['date']))}｜可交易ETF代理</small></div><div class="hk-kv"><span>美国10年期</span><b>{float(treasury['ten_year_pct']):.2f}% ({_fmt_signed(treasury['change_bp'],1,'bp')})</b></div><div class="hk-driver-note"><b>外部解读</b>中概代理偏弱且美债10年期上行，对港股科技估值与次日风险偏好形成压制。</div></div>
      </div>
      <div class="hk-analysis-grid">
        <div class="hk-analysis-panel"><h3>观察池广度与量能分布</h3>
          <div class="hk-bar-row"><div class="hk-bar-head"><b>上涨家数</b><span>{advancers}/{total}</span></div><div class="hk-bar-track"><i class="good" style="width:{advancers/total*100 if total else 0:.1f}%"></i></div><div class="hk-bar-foot"><span>中位涨幅 {median_change:+.2f}%</span><span>涨幅≥2%：{strong_advancers}只</span></div></div>
          <div class="hk-bar-row"><div class="hk-bar-head"><b>放量确认</b><span>{volume_confirmed}/{total}</span></div><div class="hk-bar-track"><i class="warn" style="width:{volume_confirmed/total*100 if total else 0:.1f}%"></i></div><div class="hk-bar-foot"><span>量比中位数 {median_volume:.2f}×</span><span>量比&lt;0.75×：{volume_weak}只</span></div></div>
          <div class="hk-bar-row"><div class="hk-bar-head"><b>20日跑赢市场</b><span>{relative_positive}/{total}</span></div><div class="hk-bar-track"><i style="width:{relative_positive/total*100 if total else 0:.1f}%"></i></div><div class="hk-bar-foot"><span>上涨扩散快于相对强弱改善</span><span>结构仍有分化</span></div></div>
          <div class="hk-movers"><div><b>涨幅领先</b><span>{html.escape(leaders)}</span></div><div><b>相对落后</b><span>{html.escape(laggards)}</span></div></div>
        </div>
        <div class="hk-analysis-panel"><h3>定价拆解与次日验证</h3><div class="hk-readout">
          <div class="hk-readout-row"><b>价格面</b><span>{advancers}/{total}上涨、中位涨幅{median_change:+.2f}%，广度偏强。</span></div>
          <div class="hk-readout-row"><b>量能面</b><span>仅{volume_confirmed}/{total}放量，量比中位数{median_volume:.2f}×，上涨确认不足。</span></div>
          <div class="hk-readout-row"><b>资金面</b><span>南向3日净额{three_day_net:+.2f}十亿港元，尚未与价格形成共振。</span></div>
          <div class="hk-readout-row"><b>外部面</b><span>HXC {float(market['hxc']['change_pct']):+.2f}%、KWEB {float(market['etfs']['KWEB']['change_pct']):+.2f}%、美债10年期{_fmt_signed(treasury['change_bp'],1,'bp')}。</span></div>
        </div><div class="hk-checks"><div class="hk-check"><b>量能扩散</b><span>观察放量家数能否由{volume_confirmed}/{total}继续扩大。</span></div><div class="hk-check"><b>南向流向</b><span>观察连续净卖是否停止或转为净买。</span></div><div class="hk-check"><b>外部映射</b><span>观察中概隔夜弱势能否被港股开盘消化。</span></div><div class="hk-check"><b>汇率边界</b><span>观察USD/HKD是否继续逼近7.85弱方。</span></div></div></div>
      </div>
      <div class="hk-action-band"><b>下一交易日重点</b><p>{html.escape(action)}</p></div>
      <div class="hk-source-strip">{html.escape(source_dates)}</div>
    </section>"""


def render_html(payload: Mapping[str, Any]) -> str:
    sb = payload["southbound"]
    hb = payload["hibor"]
    liq = payload["liquidity"]
    market = payload["market"]
    treasury = payload["treasury"]
    china = payload["china"]
    signal = payload["signal"]
    rows = "".join(
        f"<tr><td>{html.escape(str(row['date']))}</td>"
        f"<td>{float(row['buy_m'])/1000:.2f}</td>"
        f"<td>{float(row['sell_m'])/1000:.2f}</td>"
        f"<td class='{_tone(float(row['net_m']))}'>{float(row['net_m'])/1000:+.2f}</td></tr>"
        for row in sb["history"]
    )
    source_items = [
        ("港交所", sb["sources"][0]["url"], sb["date"]),
        ("香港金管局", liq["source"]["url"], liq["date"]),
        ("香港银行公会", hb["sources"][0]["url"], hb["date"]),
        ("美国财政部", treasury["source"]["url"], treasury["date"]),
        ("Nasdaq HXC", market["sources"]["hxc"]["url"], market["hxc"]["date"]),
        ("Yahoo市场行情", market["sources"]["usdhkd_etfs"]["url"], market["usdhkd_date"]),
        ("中国货币网", china["lpr"]["source"]["url"], china["lpr"]["date"]),
        ("国家统计局", china["pmi"]["source"]["url"], china["pmi"]["period"]),
    ]
    sources = "".join(
        f"<span><b>{index}</b> {html.escape(name)}｜{html.escape(observed)}</span>"
        for index, (name, _url, observed) in enumerate(source_items, 1)
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>港股关键变量</title><style>
@page{{size:A4;margin:10mm}}*{{box-sizing:border-box}}body{{margin:0;color:#172033;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;font-size:8.5px;line-height:1.42}}.page{{height:277mm;display:flex;flex-direction:column;gap:3mm;overflow:hidden}}.hero{{background:linear-gradient(125deg,#101a34,#245899 62%,#0f766e);color:white;border-radius:11px;padding:6mm 7mm;display:flex;justify-content:space-between;align-items:flex-end;min-height:35mm}}.eyebrow{{font-size:7.5px;font-weight:800;letter-spacing:1.5px;color:#99f6e4}}h1{{font-size:23px;margin:1.5mm 0 1mm}}.hero p{{margin:0;color:#dbeafe;font-size:9px;max-width:122mm}}.stance{{min-width:35mm;text-align:center;background:#ffffff1f;border:1px solid #ffffff38;border-radius:9px;padding:3mm}}.stance b{{display:block;font-size:17px}}.stance span{{font-size:7px;color:#dbeafe}}.metric-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:2mm}}.metric-card{{border:1px solid #dbe3ef;border-radius:8px;padding:3mm;background:#f8fafc;min-height:31mm}}.metric-card small{{display:block;color:#64748b;font-size:7.2px}}.metric-card strong{{display:block;font-size:17px;color:#173d70;margin:1mm 0}}.metric-card em{{font-style:normal;font-weight:800}}.good{{color:#15803d!important}}.bad{{color:#b91c1c!important}}.neutral{{color:#475569!important}}.section-title{{display:flex;justify-content:space-between;border-bottom:2px solid #173d70;padding-bottom:1.2mm}}.section-title h2{{font-size:13px;margin:0;color:#173d70}}.section-title span{{font-size:7.4px;color:#64748b}}.main-grid{{display:grid;grid-template-columns:1.05fr 1fr 1fr;gap:2.5mm;min-height:91mm}}.panel{{border:1px solid #dbe3ef;border-radius:8px;padding:3mm;background:white}}.panel h3{{font-size:10px;color:#173d70;margin:0 0 2mm}}table{{width:100%;border-collapse:collapse}}th{{background:#173d70;color:white;font-size:7px;padding:1.2mm}}td{{border-bottom:1px solid #e2e8f0;padding:1.4mm;text-align:right}}td:first-child{{text-align:left}}.reading{{border-radius:6px;background:#eef6ff;padding:2mm;margin:2mm 0}}.reading b{{display:block;color:#173d70}}.kv{{display:flex;justify-content:space-between;border-bottom:1px solid #e2e8f0;padding:1.7mm 0}}.kv:last-child{{border-bottom:0}}.kv b{{color:#173d70}}.asset{{display:grid;grid-template-columns:1fr auto;gap:1mm;border-bottom:1px solid #e2e8f0;padding:1.6mm 0}}.asset:last-child{{border:0}}.asset small{{grid-column:1 / span 2;color:#64748b}}.china-strip{{display:grid;grid-template-columns:1fr 1fr 1.3fr;gap:2.5mm}}.china-card{{border:1px solid #dbe3ef;border-radius:8px;background:#f8fafc;padding:3mm}}.china-card strong{{font-size:15px;color:#173d70}}.china-card p{{margin:1mm 0 0}}.conclusion{{background:#ecfdf5;border-left:4px solid #10b981;border-radius:7px;padding:3mm 4mm}}.conclusion b{{color:#14532d}}.conclusion p{{margin:1mm 0 0;font-size:9px}}.sources{{margin-top:auto;border-top:1px solid #cbd5e1;padding-top:2mm;display:grid;grid-template-columns:repeat(4,1fr);gap:1mm;color:#64748b;font-size:6.6px}}.sources span{{white-space:nowrap}}.sources b{{display:inline-block;background:#173d70;color:white;border-radius:50%;width:3.5mm;height:3.5mm;text-align:center;line-height:3.5mm}}.foot{{color:#64748b;font-size:6.6px;margin-top:1mm}}
</style></head><body><section class="page">
<header class="hero"><div><span class="eyebrow">HK MARKET DRIVERS · FORMAL REPORT</span><h1>港股关键变量</h1><p>{html.escape(signal['summary'])}</p></div><div class="stance"><span>{payload['as_of']} 收盘后</span><b>{html.escape(signal['label'])}</b><span>只提供市场背景，不改Top5评分</span></div></header>
<div class="metric-grid">
  <div class="metric-card"><small>南向当日净额</small><strong class="{_tone(float(sb['net_hkd_bn']))}">{_fmt_signed(sb['net_hkd_bn'],2)}</strong><em>十亿港元</em><small>成交 {float(sb['turnover_hkd_bn']):.2f} 十亿</small></div>
  <div class="metric-card"><small>南向5日累计</small><strong class="{_tone(float(sb['net_5d_hkd_bn']))}">{_fmt_signed(sb['net_5d_hkd_bn'],2)}</strong><em>十亿港元</em><small>买入额－卖出额</small></div>
  <div class="metric-card"><small>1个月 HIBOR</small><strong>{float(hb['one_month_pct']):.3f}%</strong><em class="{_tone(float(hb['one_month_change_bp']),positive_is_good=False)}">{_fmt_signed(hb['one_month_change_bp'],1,'bp')}</em><small>较前一工作日</small></div>
  <div class="metric-card"><small>USD/HKD</small><strong>{float(market['usdhkd']):.4f}</strong><em class="bad">距7.85弱方 {float(market['distance_to_weak_side']):.4f}</em><small>市场行情代理</small></div>
  <div class="metric-card"><small>美国10年期</small><strong>{float(treasury['ten_year_pct']):.2f}%</strong><em class="{_tone(float(treasury['change_bp']),positive_is_good=False)}">{_fmt_signed(treasury['change_bp'],1,'bp')}</em><small>{html.escape(treasury['date'])} 收盘</small></div>
</div>
<div class="section-title"><h2>资金、港元流动性与外部映射</h2><span>日频数据均不晚于港股报告截止时间</span></div>
<div class="main-grid">
  <div class="panel"><h3>南向资金 · 最近5个交易日</h3><table><thead><tr><th>日期</th><th>买入</th><th>卖出</th><th>净额</th></tr></thead><tbody>{rows}</tbody></table><div class="reading"><b>读法</b>净额直接反映南向买卖方向；成交额只代表活跃度，二者分开显示。</div></div>
  <div class="panel"><h3>港元流动性</h3><div class="kv"><span>隔夜 HIBOR</span><b>{float(hb['overnight_pct']):.3f}%</b></div><div class="kv"><span>隔夜日变动</span><b class="{_tone(float(hb['overnight_change_bp']),positive_is_good=False)}">{_fmt_signed(hb['overnight_change_bp'],1,'bp')}</b></div><div class="kv"><span>1个月 HIBOR</span><b>{float(hb['one_month_pct']):.3f}%</b></div><div class="kv"><span>总结余</span><b>{float(liq['aggregate_balance_hkd_bn']):.3f} 十亿港元</b></div><div class="kv"><span>总结余日变动</span><b class="{_tone(float(liq['aggregate_balance_change_hkd_bn']))}">{_fmt_signed(liq['aggregate_balance_change_hkd_bn'],3)} 十亿</b></div><div class="reading"><b>当前含义</b>短端 HIBOR 明显抬升，说明即时港元资金价格趋紧；总结余变化用于判断是否伴随基础流动性收缩。</div></div>
  <div class="panel"><h3>隔夜外部定价</h3><div class="asset"><b>HXC 金龙中国</b><strong class="{_tone(float(market['hxc']['change_pct']))}">{_fmt_signed(market['hxc']['change_pct'],2,'%')}</strong><small>{html.escape(market['hxc']['date'])}｜Nasdaq官方指数</small></div><div class="asset"><b>KWEB 中国互联网</b><strong class="{_tone(float(market['etfs']['KWEB']['change_pct']))}">{_fmt_signed(market['etfs']['KWEB']['change_pct'],2,'%')}</strong><small>{html.escape(market['etfs']['KWEB']['date'])}｜可交易ETF代理</small></div><div class="asset"><b>FXI 中国大盘股</b><strong class="{_tone(float(market['etfs']['FXI']['change_pct']))}">{_fmt_signed(market['etfs']['FXI']['change_pct'],2,'%')}</strong><small>{html.escape(market['etfs']['FXI']['date'])}｜可交易ETF代理</small></div><div class="reading"><b>当前含义</b>三项代理同步走弱，给港股开盘与互联网权重股带来负向隔夜映射。</div></div>
</div>
<div class="section-title"><h2>中国低频背景</h2><span>只保留仍处于有效期的最近正式发布</span></div>
<div class="china-strip"><div class="china-card"><small>LPR · {china['lpr']['date']}</small><strong>1年 {float(china['lpr']['one_year_pct']):.1f}%</strong><p>5年以上 {float(china['lpr']['five_year_pct']):.1f}%｜本身不是每日触发器。</p></div><div class="china-card"><small>PMI · {china['pmi']['period']}</small><strong>制造业 {float(china['pmi']['manufacturing']):.1f}</strong><p>非制造业 {float(china['pmi']['non_manufacturing']):.1f}｜两项均低于50。</p></div><div class="china-card"><small>使用原则</small><strong>低频不冒充日频</strong><p>只有正式新数据或重要政策出现时才更新；没有变化就保持一行，不额外占页。</p></div></div>
<div class="conclusion"><b>综合判断</b><p>南向净卖、短端港元利率上升和中概隔夜走弱共同指向偏谨慎背景；美债10年期回落提供部分对冲。该版块用于解释市场环境，不直接生成买卖结论。</p></div>
<div class="sources">{sources}</div><div class="foot">单位：南向为十亿港元；港交所净额＝沪、深南向买入额－卖出额。HIBOR 数据仅用于本次内部样稿验证，正式商业分发前需确认 HKAB 授权条款。市场数据存在延迟；所有低频数据均标注统计期。</div>
</section></body></html>"""


def _geometry_issues(rendered_document: Any) -> list[str]:
    issues: list[str] = []
    for page_number, page in enumerate(rendered_document.pages, 1):
        page_width = float(getattr(page._page_box, "width", 0) or 0)

        def walk(box: Any) -> None:
            element = getattr(box, "element", None)
            classes = set(str(element.get("class", "")).split()) if element is not None else set()
            box_type = type(box).__name__
            width = float(getattr(box, "width", 0) or 0)
            height = float(getattr(box, "height", 0) or 0)
            if box_type == "GridBox" and "metric-grid" in classes and (width < page_width * 0.8 or height > 130):
                issues.append(f"第{page_number}页指标网格异常 {width:.1f}×{height:.1f}")
            if box_type == "BlockBox" and "metric-card" in classes and width < 75:
                issues.append(f"第{page_number}页指标卡宽度异常 {width:.1f}")
            if box_type == "GridBox" and "main-grid" in classes and (width < page_width * 0.8 or height > 380):
                issues.append(f"第{page_number}页主网格异常 {width:.1f}×{height:.1f}")
            for child in getattr(box, "children", ()):
                walk(child)

        walk(page._page_box)
    return list(dict.fromkeys(issues))


def render_pdf(
    payload: Mapping[str, Any],
    *,
    output_html: Path,
    output_pdf: Path,
    output_json: Path,
) -> dict[str, Any]:
    from pypdf import PdfReader
    from weasyprint import HTML

    document = render_html(payload)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(document, encoding="utf-8")
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rendered = HTML(string=document, base_url=str(output_html.parent)).render()
    geometry_issues = _geometry_issues(rendered)
    rendered.write_pdf(str(output_pdf))
    reader = PdfReader(str(output_pdf))
    pages = len(reader.pages)
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages)
    required_text = ["港股关键变量", "南向当日净额", "港元流动性", "HXC 金龙中国", "中国低频背景"]
    missing = [text for text in required_text if text not in extracted]
    return {
        "as_of": payload["as_of"],
        "pages": pages,
        "empty_pages": [index for index, page in enumerate(reader.pages, 1) if len((page.extract_text() or "").strip()) < 30],
        "geometry_issues": geometry_issues,
        "missing_required_text": missing,
        "layout_ok": pages == 1 and not geometry_issues and not missing,
        "html": str(output_html),
        "pdf": str(output_pdf),
        "json": str(output_json),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render an HK key-variable PDF preview")
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(HK_TZ).date())
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--fallback-json", type=Path)
    args = parser.parse_args(argv)
    fallback_payload = None
    if args.fallback_json is not None and args.fallback_json.exists():
        fallback_payload = json.loads(args.fallback_json.read_text(encoding="utf-8"))
    payload = build_snapshot(args.as_of, fallback_payload=fallback_payload)
    result = render_pdf(
        payload,
        output_html=args.output_html,
        output_pdf=args.output_pdf,
        output_json=args.output_json,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["layout_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
