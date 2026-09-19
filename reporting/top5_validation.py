"""Point-in-time Top5 validation and reporting-run accuracy ledger.

The analysis Top5 history is imported as a return baseline.  Structured reporting
contracts are stored separately so trigger, stop/target and score-component
statistics never get mixed with the analysis ranking.  All writes are idempotent
and forward returns only use sessions strictly after the report date.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTING_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_LEDGER = REPORTING_ROOT / "state" / "decision_accuracy.sqlite3"
DEFAULT_ANALYSIS_STATE = PROJECT_ROOT / "state" / "top5_brief_stats.json"
VALIDATION_VERSION = "top5-validation-v1"
ANALYSIS_MODEL_VERSION = "analysis-stage-top5"
HORIZONS = (1, 5, 20)
TECHNICAL_COMPONENTS = (
    "price_structure",
    "relative_strength_sector",
    "volume_confirmation",
    "volatility_risk",
    "auxiliary_indicators",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def _pct(end: float | None, start: float | None) -> float | None:
    if end is None or start is None or start <= 0:
        return None
    return round((end / start - 1) * 100, 4)


def _score_band(score: float | None) -> str:
    if score is None:
        return "未记录"
    if score >= 80:
        return "80+"
    if score >= 70:
        return "70–79"
    if score >= 60:
        return "60–69"
    return "<60"


def _record_id(as_of: str, market: str, symbol: str, source_kind: str, model_version: str) -> str:
    raw = f"{as_of}:{market}:{symbol}:{source_kind}:{model_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Top5ValidationLedger:
    """SQLite ledger for analysis baselines and report signals."""

    def __init__(self, path: Path = DEFAULT_LEDGER):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS opportunity_validation_snapshots (
                record_id TEXT PRIMARY KEY,
                as_of TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                source_kind TEXT NOT NULL,
                model_version TEXT NOT NULL,
                rank INTEGER,
                is_top5 INTEGER NOT NULL DEFAULT 0,
                opportunity_score REAL,
                score_band TEXT,
                execution_status TEXT,
                signal_type TEXT,
                setup_type TEXT,
                signal_close REAL,
                trigger_active INTEGER,
                trigger_price REAL,
                reference_entry REAL,
                stop_loss REAL,
                target_1 REAL,
                target_2 REAL,
                valid_sessions INTEGER,
                components_json TEXT,
                technical_components_json TEXT,
                available_sessions INTEGER DEFAULT 0,
                evaluated_through TEXT,
                return_1d REAL,
                return_5d REAL,
                return_20d REAL,
                max_favorable_excursion REAL,
                max_adverse_excursion REAL,
                trigger_state TEXT,
                trigger_date TEXT,
                trigger_fill_price REAL,
                trigger_return_1d REAL,
                trigger_return_5d REAL,
                trigger_return_20d REAL,
                trigger_mfe REAL,
                trigger_mae REAL,
                stop_hit INTEGER,
                target_1_hit INTEGER,
                target_2_hit INTEGER,
                first_exit TEXT,
                outcome_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(as_of, market, symbol, source_kind, model_version)
            );
            CREATE INDEX IF NOT EXISTS idx_validation_market_date
                ON opportunity_validation_snapshots(market, as_of, source_kind, is_top5);
            CREATE TABLE IF NOT EXISTS reporting_run_audits (
                run_id TEXT PRIMARY KEY,
                as_of TEXT NOT NULL,
                market TEXT NOT NULL,
                model_version TEXT NOT NULL,
                pool_size INTEGER NOT NULL,
                top5_json TEXT NOT NULL,
                analysis_top5_json TEXT,
                overlap_count INTEGER,
                overlap_ratio REAL,
                contract_valid INTEGER,
                contract_error_count INTEGER,
                contract_warning_count INTEGER,
                official_coverage REAL,
                relative_strength_coverage REAL,
                volume_coverage REAL,
                signal_close_coverage REAL,
                trade_card_coverage REAL,
                report_layout_ok INTEGER,
                report_content_ok INTEGER,
                report_pages INTEGER,
                report_geometry_issue_count INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(as_of, market, model_version)
            );
            """
        )
        self.connection.commit()

    def _upsert_snapshot(self, payload: Mapping[str, Any]) -> None:
        columns = tuple(payload)
        values = tuple(payload[column] for column in columns)
        updates = ",".join(
            f"{column}=excluded.{column}"
            for column in columns
            if column not in {"record_id", "created_at"}
        )
        sql = (
            f"INSERT INTO opportunity_validation_snapshots ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)}) "
            f"ON CONFLICT(as_of,market,symbol,source_kind,model_version) DO UPDATE SET {updates}"
        )
        self.connection.execute(sql, values)

    def import_analysis_history(self, path: Path = DEFAULT_ANALYSIS_STATE) -> int:
        if not Path(path).exists():
            return 0
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = 0
        now = _now()
        for market, node in (payload.get("regions") or {}).items():
            if market not in {"hk", "us"} or not isinstance(node, Mapping):
                continue
            for entry in node.get("entries") or []:
                if not isinstance(entry, Mapping):
                    continue
                as_of = str(entry.get("date") or "")[:10]
                for rank, symbol in enumerate(entry.get("codes") or [], 1):
                    symbol = str(symbol or "").upper().strip()
                    if not as_of or not symbol or rank > 5:
                        continue
                    self._upsert_snapshot(
                        {
                            "record_id": _record_id(as_of, market, symbol, "analysis", ANALYSIS_MODEL_VERSION),
                            "as_of": as_of,
                            "market": market,
                            "symbol": symbol,
                            "name": symbol,
                            "source_kind": "analysis",
                            "model_version": ANALYSIS_MODEL_VERSION,
                            "rank": rank,
                            "is_top5": 1,
                            "score_band": "未记录",
                            "signal_type": "正式Top5",
                            "components_json": "{}",
                            "technical_components_json": "{}",
                            "trigger_state": "not_recorded",
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    rows += 1
        self.connection.commit()
        return rows

    def record_contract(self, contract: Mapping[str, Any]) -> int:
        as_of = str(contract.get("report_date") or "")[:10]
        scoring_version = str(contract.get("scoring_version") or "unknown")
        now = _now()
        rows = 0
        by_market: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for item in contract.get("stocks") or []:
            if not isinstance(item, Mapping):
                continue
            identity = item.get("identity") if isinstance(item.get("identity"), Mapping) else {}
            opportunity = item.get("opportunity") if isinstance(item.get("opportunity"), Mapping) else {}
            execution = item.get("execution") if isinstance(item.get("execution"), Mapping) else {}
            market_data = item.get("market_data") if isinstance(item.get("market_data"), Mapping) else {}
            relative = item.get("relative_strength") if isinstance(item.get("relative_strength"), Mapping) else {}
            card = execution.get("standard_trade_card") if isinstance(execution.get("standard_trade_card"), Mapping) else {}
            market = str(identity.get("market") or "")
            symbol = str(identity.get("symbol") or "").upper()
            rank = int(opportunity.get("rank") or 999)
            score = _number(opportunity.get("score"))
            signal_type = str(execution.get("status") or "未标注")
            self._upsert_snapshot(
                {
                    "record_id": _record_id(as_of, market, symbol, "reporting", scoring_version),
                    "as_of": as_of,
                    "market": market,
                    "symbol": symbol,
                    "name": str(identity.get("name") or symbol),
                    "source_kind": "reporting",
                    "model_version": scoring_version,
                    "rank": rank,
                    "is_top5": int(rank <= 5),
                    "opportunity_score": score,
                    "score_band": _score_band(score),
                    "execution_status": signal_type,
                    "signal_type": signal_type,
                    "setup_type": str(card.get("setup_type") or ""),
                    "signal_close": _number(market_data.get("completed_close")),
                    "trigger_active": int(bool(card.get("active"))),
                    "trigger_price": _number(card.get("trigger_price")),
                    "reference_entry": _number(card.get("reference_entry")),
                    "stop_loss": _number(card.get("stop_loss")),
                    "target_1": _number(card.get("target_1")),
                    "target_2": _number(card.get("target_2")),
                    "valid_sessions": int(card.get("valid_sessions") or 0),
                    "components_json": _json(opportunity.get("components") or {}),
                    "technical_components_json": _json(opportunity.get("technical_components") or {}),
                    "trigger_state": "pending" if card.get("active") and card.get("trigger_price") else "not_applicable",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            by_market[market].append(item)
            rows += 1
        self.connection.commit()
        for market, items in by_market.items():
            self._record_reporting_run(contract, market, items)
        return rows

    def _analysis_top5(self, as_of: str, market: str) -> list[str]:
        return [
            str(row[0])
            for row in self.connection.execute(
                """
                SELECT symbol FROM opportunity_validation_snapshots
                WHERE as_of=? AND market=? AND source_kind='analysis' AND is_top5=1
                ORDER BY rank
                """,
                (as_of, market),
            )
        ]

    def _record_reporting_run(
        self,
        contract: Mapping[str, Any],
        market: str,
        items: Sequence[Mapping[str, Any]],
    ) -> None:
        as_of = str(contract.get("report_date") or "")[:10]
        model = str(contract.get("scoring_version") or "unknown")
        top5 = [
            str(item.get("identity", {}).get("symbol") or "")
            for item in sorted(items, key=lambda value: int(value.get("opportunity", {}).get("rank") or 999))[:5]
        ]
        analysis = self._analysis_top5(as_of, market)
        overlap = len(set(top5) & set(analysis)) if analysis else None
        totals = contract.get("totals") if isinstance(contract.get("totals"), Mapping) else {}
        validation = contract.get("validation") if isinstance(contract.get("validation"), Mapping) else {}
        pool_size = len(items)
        signal_close_count = sum(_number(item.get("market_data", {}).get("completed_close")) is not None for item in items)
        card_count = sum(isinstance(item.get("execution", {}).get("standard_trade_card"), Mapping) for item in items)
        market_total = int(totals.get(market) or pool_size or 1)
        coverage = lambda key: round(float(totals.get(key) or 0) / max(int(totals.get("stocks") or 1), 1) * 100, 2)
        now = _now()
        run_id = hashlib.sha256(f"{as_of}:{market}:{model}".encode()).hexdigest()[:24]
        self.connection.execute(
            """
            INSERT INTO reporting_run_audits (
                run_id,as_of,market,model_version,pool_size,top5_json,analysis_top5_json,
                overlap_count,overlap_ratio,contract_valid,contract_error_count,
                contract_warning_count,official_coverage,relative_strength_coverage,
                volume_coverage,signal_close_coverage,trade_card_coverage,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(as_of,market,model_version) DO UPDATE SET
                pool_size=excluded.pool_size,top5_json=excluded.top5_json,
                analysis_top5_json=excluded.analysis_top5_json,
                overlap_count=excluded.overlap_count,overlap_ratio=excluded.overlap_ratio,
                contract_valid=excluded.contract_valid,
                contract_error_count=excluded.contract_error_count,
                contract_warning_count=excluded.contract_warning_count,
                official_coverage=excluded.official_coverage,
                relative_strength_coverage=excluded.relative_strength_coverage,
                volume_coverage=excluded.volume_coverage,
                signal_close_coverage=excluded.signal_close_coverage,
                trade_card_coverage=excluded.trade_card_coverage,updated_at=excluded.updated_at
            """,
            (
                run_id, as_of, market, model, pool_size, _json(top5), _json(analysis),
                overlap, round(overlap / 5 * 100, 2) if overlap is not None else None,
                int(bool(validation.get("valid"))), len(validation.get("errors") or []),
                len(validation.get("warnings") or []), coverage("official_quality_coverage"),
                coverage("relative_strength_coverage"), coverage("volume_confirmation_coverage"),
                round(signal_close_count / max(pool_size, 1) * 100, 2),
                round(card_count / max(pool_size, 1) * 100, 2), now, now,
            ),
        )
        self.connection.commit()

    def update_report_audit(self, as_of: str, market: str, report_result: Mapping[str, Any]) -> None:
        self.connection.execute(
            """
            UPDATE reporting_run_audits SET report_layout_ok=?,report_content_ok=?,report_pages=?,
                report_geometry_issue_count=?,updated_at=? WHERE as_of=? AND market=?
            """,
            (
                int(bool(report_result.get("layout_ok"))),
                int(bool(report_result.get("content_ok"))),
                int(report_result.get("pages") or 0),
                len(report_result.get("layout_geometry_issues") or []),
                _now(), as_of, market,
            ),
        )
        self.connection.commit()

    @staticmethod
    def _price_rows(db: sqlite3.Connection, symbol: str, as_of: str, through: str) -> list[dict[str, Any]]:
        rows = db.execute(
            """
            SELECT date,open,high,low,close FROM stock_daily
            WHERE code=? AND date>? AND date<=? ORDER BY date
            """,
            (symbol, as_of, through),
        ).fetchall()
        return [
            {"date": str(day), "open": _number(open_), "high": _number(high),
             "low": _number(low), "close": _number(close)}
            for day, open_, high, low, close in rows if _number(close) is not None
        ]

    @staticmethod
    def _close_on_or_before(db: sqlite3.Connection, symbol: str, as_of: str) -> float | None:
        row = db.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (symbol, as_of),
        ).fetchone()
        return _number(row[0]) if row else None

    @staticmethod
    def _triggered(row: Mapping[str, Any], bar: Mapping[str, Any]) -> bool:
        trigger = _number(row.get("trigger_price"))
        if trigger is None:
            return False
        setup = str(row.get("setup_type") or "")
        if setup == "breakout":
            return _number(bar.get("close")) is not None and float(bar["close"]) >= trigger
        if setup == "pullback":
            low, high, close = (_number(bar.get(key)) for key in ("low", "high", "close"))
            return all(value is not None for value in (low, high, close)) and low <= trigger <= high and close >= trigger
        high = _number(bar.get("high"))
        return high is not None and high >= trigger

    @staticmethod
    def _trigger_fill(row: Mapping[str, Any], bar: Mapping[str, Any]) -> float | None:
        trigger = _number(row.get("trigger_price"))
        open_price = _number(bar.get("open"))
        if trigger is None:
            return None
        if str(row.get("setup_type") or "") == "breakout" and open_price is not None:
            return max(trigger, open_price)
        return trigger

    def evaluate(self, market_db: Path = DEFAULT_DB, through_date: str | None = None) -> int:
        through = through_date or date.today().isoformat()
        market_connection = sqlite3.connect(str(market_db))
        snapshots = self.connection.execute(
            "SELECT * FROM opportunity_validation_snapshots WHERE as_of<? ORDER BY as_of,market,symbol",
            (through,),
        ).fetchall()
        updated = 0
        for snapshot in snapshots:
            item = dict(snapshot)
            signal_close = _number(item.get("signal_close")) or self._close_on_or_before(
                market_connection, str(item["symbol"]), str(item["as_of"])
            )
            bars = self._price_rows(
                market_connection, str(item["symbol"]), str(item["as_of"]), through
            )
            values: dict[str, Any] = {
                "signal_close": signal_close,
                "available_sessions": len(bars),
                "evaluated_through": through,
                "updated_at": _now(),
            }
            for horizon in HORIZONS:
                values[f"return_{horizon}d"] = _pct(bars[horizon - 1]["close"], signal_close) if len(bars) >= horizon else None
            if bars and signal_close:
                highs = [bar["high"] for bar in bars[:20] if bar["high"] is not None]
                lows = [bar["low"] for bar in bars[:20] if bar["low"] is not None]
                raw_mfe = _pct(max(highs), signal_close) if highs else None
                raw_mae = _pct(min(lows), signal_close) if lows else None
                values["max_favorable_excursion"] = max(0.0, raw_mfe) if raw_mfe is not None else None
                values["max_adverse_excursion"] = min(0.0, raw_mae) if raw_mae is not None else None

            if item.get("source_kind") == "reporting" and item.get("trigger_active") and item.get("trigger_price"):
                valid_sessions = int(item.get("valid_sessions") or 0) or 20
                trigger_index = next(
                    (index for index, bar in enumerate(bars[:valid_sessions]) if self._triggered(item, bar)),
                    None,
                )
                if trigger_index is None:
                    values["trigger_state"] = "expired" if len(bars) >= valid_sessions else "pending"
                    values["outcome_status"] = values["trigger_state"]
                else:
                    trigger_bar = bars[trigger_index]
                    fill = self._trigger_fill(item, trigger_bar)
                    post = bars[trigger_index:trigger_index + 21]
                    values.update({"trigger_state": "triggered", "trigger_date": trigger_bar["date"], "trigger_fill_price": fill})
                    for horizon in HORIZONS:
                        # Horizon 1 is the first close after the trigger session.
                        values[f"trigger_return_{horizon}d"] = _pct(post[horizon]["close"], fill) if len(post) > horizon else None
                    highs = [bar["high"] for bar in post if bar["high"] is not None]
                    lows = [bar["low"] for bar in post if bar["low"] is not None]
                    raw_trigger_mfe = _pct(max(highs), fill) if highs else None
                    raw_trigger_mae = _pct(min(lows), fill) if lows else None
                    values["trigger_mfe"] = max(0.0, raw_trigger_mfe) if raw_trigger_mfe is not None else None
                    values["trigger_mae"] = min(0.0, raw_trigger_mae) if raw_trigger_mae is not None else None
                    stop, target1, target2 = (_number(item.get(key)) for key in ("stop_loss", "target_1", "target_2"))
                    stop_hit = next((bar["date"] for bar in post if stop is not None and bar["low"] is not None and bar["low"] <= stop), None)
                    target1_hit = next((bar["date"] for bar in post if target1 is not None and bar["high"] is not None and bar["high"] >= target1), None)
                    target2_hit = next((bar["date"] for bar in post if target2 is not None and bar["high"] is not None and bar["high"] >= target2), None)
                    values.update({"stop_hit": int(stop_hit is not None), "target_1_hit": int(target1_hit is not None), "target_2_hit": int(target2_hit is not None)})
                    if stop_hit and target1_hit and stop_hit == target1_hit:
                        first_exit = "ambiguous_same_bar"
                    elif stop_hit and (not target1_hit or stop_hit < target1_hit):
                        first_exit = "stop"
                    elif target1_hit:
                        first_exit = "target_1"
                    else:
                        first_exit = "open"
                    values["first_exit"] = first_exit
                    values["outcome_status"] = first_exit
            assignments = ",".join(f"{key}=?" for key in values)
            self.connection.execute(
                f"UPDATE opportunity_validation_snapshots SET {assignments} WHERE record_id=?",
                (*values.values(), item["record_id"]),
            )
            updated += 1
        market_connection.close()
        self.connection.commit()
        return updated

    @staticmethod
    def _metric_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {"signals": len(rows)}
        for horizon in HORIZONS:
            values = [_number(row.get(f"return_{horizon}d")) for row in rows]
            valid = [value for value in values if value is not None]
            output[f"sample_{horizon}d"] = len(valid)
            output[f"average_return_{horizon}d"] = round(mean(valid), 2) if valid else None
            output[f"median_return_{horizon}d"] = round(median(valid), 2) if valid else None
            output[f"win_rate_{horizon}d"] = round(sum(value > 0 for value in valid) / len(valid) * 100, 1) if valid else None
        mfe = [_number(row.get("max_favorable_excursion")) for row in rows]
        mae = [_number(row.get("max_adverse_excursion")) for row in rows]
        mfe = [value for value in mfe if value is not None]
        mae = [value for value in mae if value is not None]
        output["average_mfe"] = round(mean(mfe), 2) if mfe else None
        output["average_mae"] = round(mean(mae), 2) if mae else None
        eligible = [row for row in rows if row.get("trigger_active") and row.get("trigger_price")]
        triggered = [row for row in eligible if row.get("trigger_state") == "triggered"]
        output["trigger_eligible"] = len(eligible)
        output["triggered"] = len(triggered)
        output["trigger_rate"] = round(len(triggered) / len(eligible) * 100, 1) if eligible else None
        output["stop_hits"] = sum(int(row.get("stop_hit") or 0) for row in triggered)
        output["target_1_hits"] = sum(int(row.get("target_1_hit") or 0) for row in triggered)
        return output

    def _rows(self, market: str, source_kind: str, top5_only: bool = True) -> list[dict[str, Any]]:
        where = "AND is_top5=1" if top5_only else ""
        return [
            dict(row)
            for row in self.connection.execute(
                f"SELECT * FROM opportunity_validation_snapshots WHERE market=? AND source_kind=? {where} ORDER BY as_of,rank",
                (market, source_kind),
            )
        ]

    @staticmethod
    def _last_periods(rows: Sequence[Mapping[str, Any]], limit: int = 20) -> list[Mapping[str, Any]]:
        dates = sorted({str(row.get("as_of")) for row in rows})[-limit:]
        return [row for row in rows if str(row.get("as_of")) in dates]

    @staticmethod
    def _group_metrics(rows: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(key) or "未记录")].append(row)
        return [
            {"group": group, **Top5ValidationLedger._metric_summary(values)}
            for group, values in sorted(groups.items())
        ]

    @staticmethod
    def _correlation(pairs: Sequence[tuple[float, float]]) -> float | None:
        if len(pairs) < 10:
            return None
        xs, ys = zip(*pairs)
        xbar, ybar = mean(xs), mean(ys)
        numerator = sum((x - xbar) * (y - ybar) for x, y in pairs)
        denominator = math.sqrt(sum((x - xbar) ** 2 for x in xs) * sum((y - ybar) ** 2 for y in ys))
        return round(numerator / denominator, 3) if denominator else None

    def _weight_validation(self, market: str) -> dict[str, Any]:
        rows = self._rows(market, "reporting", top5_only=False)
        periods = len({row["as_of"] for row in rows})
        diagnostics = []
        matured_rows = 0
        for component in TECHNICAL_COMPONENTS:
            pairs: list[tuple[float, float]] = []
            for row in rows:
                forward = _number(row.get("return_5d"))
                technical = json.loads(row.get("technical_components_json") or "{}")
                value = _number(technical.get(component))
                if forward is not None and value is not None:
                    pairs.append((value, forward))
            matured_rows = max(matured_rows, len(pairs))
            diagnostics.append({"component": component, "sample": len(pairs), "correlation_5d": self._correlation(pairs)})
        ready = periods >= 20 and matured_rows >= 100
        return {
            "periods": periods,
            "matured_rows_5d": matured_rows,
            "minimum_periods": 20,
            "minimum_rows": 100,
            "ready_to_adjust": ready,
            "decision": "样本达标，可进入权重候选比较" if ready else "样本不足，维持35/25/20/15/5，不调整权重",
            "components": diagnostics,
        }

    def _comparison(self, market: str) -> dict[str, Any]:
        runs = [dict(row) for row in self.connection.execute(
            "SELECT * FROM reporting_run_audits WHERE market=? ORDER BY as_of DESC LIMIT 20", (market,)
        )]
        comparable = [row for row in runs if row.get("overlap_count") is not None]
        analysis = { (row["as_of"], row["symbol"]): row for row in self._rows(market, "analysis") }
        reporting = { (row["as_of"], row["symbol"]): row for row in self._rows(market, "reporting") }
        result: dict[str, Any] = {
            "periods": len(comparable),
            "average_overlap": round(mean([row["overlap_ratio"] for row in comparable]), 1) if comparable else None,
        }
        comparable_dates = {run["as_of"] for run in comparable}
        for horizon in HORIZONS:
            prod_values, reporting_values = [], []
            for key, row in analysis.items():
                if key[0] not in comparable_dates:
                    continue
                value = _number(row.get(f"return_{horizon}d"))
                if value is not None:
                    prod_values.append(value)
            for key, row in reporting.items():
                if key[0] not in comparable_dates:
                    continue
                value = _number(row.get(f"return_{horizon}d"))
                if value is not None:
                    reporting_values.append(value)
            result[f"analysis_average_{horizon}d"] = round(mean(prod_values), 2) if prod_values else None
            result[f"reporting_average_{horizon}d"] = round(mean(reporting_values), 2) if reporting_values else None
            paired_deltas: list[float] = []
            for day in sorted(comparable_dates):
                analysis_day = [
                    _number(row.get(f"return_{horizon}d"))
                    for (row_day, _symbol), row in analysis.items()
                    if row_day == day
                ]
                reporting_day = [
                    _number(row.get(f"return_{horizon}d"))
                    for (row_day, _symbol), row in reporting.items()
                    if row_day == day
                ]
                analysis_valid = [value for value in analysis_day if value is not None]
                reporting_valid = [value for value in reporting_day if value is not None]
                if analysis_valid and reporting_valid:
                    paired_deltas.append(mean(reporting_valid) - mean(analysis_valid))
            result[f"paired_periods_{horizon}d"] = len(paired_deltas)
            result[f"paired_delta_{horizon}d"] = round(mean(paired_deltas), 2) if paired_deltas else None
            if len(paired_deltas) >= 5:
                rng = random.Random(f"{market}:{horizon}:{VALIDATION_VERSION}")
                boot = sorted(
                    mean(rng.choice(paired_deltas) for _ in paired_deltas)
                    for _ in range(4000)
                )
                result[f"paired_delta_{horizon}d_ci90"] = [
                    round(boot[int(len(boot) * 0.05)], 2),
                    round(boot[int(len(boot) * 0.95) - 1], 2),
                ]
            else:
                result[f"paired_delta_{horizon}d_ci90"] = None
        return result

    @staticmethod
    def _quality_verdict(
        comparison: Mapping[str, Any],
        analysis: Mapping[str, Any],
        reporting: Mapping[str, Any],
        weight: Mapping[str, Any],
    ) -> dict[str, Any]:
        paired = int(comparison.get("paired_periods_5d") or 0)
        ci = comparison.get("paired_delta_5d_ci90")
        # First 20 paired periods are the research window.  Keep at least the
        # next 20 periods blind before declaring that either version wins.
        required = paired >= 40 and bool(weight.get("ready_to_adjust"))
        if not required or not isinstance(ci, list) or len(ci) != 2:
            phase = "研究窗口" if paired < 20 else "盲测窗口"
            return {
                "status": "insufficient_evidence",
                "label": "证据不足",
                "reason": (
                    f"{phase}：5日配对期次 {paired}/40"
                    f"（前20期研究+后20期盲测）；成熟报告样本 "
                    f"{weight.get('matured_rows_5d', 0)}/100"
                ),
            }
        analysis_mae = _number(analysis.get("average_mae"))
        reporting_mae = _number(reporting.get("average_mae"))
        risk_not_worse = (
            analysis_mae is None
            or reporting_mae is None
            or reporting_mae >= analysis_mae
        )
        if _number(ci[0]) is not None and float(ci[0]) > 0 and risk_not_worse:
            return {
                "status": "reporting_better",
                "label": "报告评分更优",
                "reason": "5日配对增量收益的90%区间高于0，且最大不利波动未恶化",
            }
        if (_number(ci[1]) is not None and float(ci[1]) < 0) or not risk_not_worse:
            return {
                "status": "reporting_worse",
                "label": "报告评分需调整",
                "reason": "5日配对收益显著落后，或最大不利波动恶化",
            }
        return {
            "status": "no_material_difference",
            "label": "两阶段结果持平",
            "reason": "5日配对增量收益区间仍跨过0，暂未形成稳定差异",
        }

    def build_market_summary(self, market: str, through_date: str) -> dict[str, Any]:
        analysis = self._rows(market, "analysis")
        reporting = self._rows(market, "reporting")
        baseline_rows = self._last_periods(analysis, 20)
        reporting_rows = self._last_periods(reporting, 20)
        completed_dates = sorted({row["as_of"] for row in analysis if row.get("return_1d") is not None})
        latest_date = completed_dates[-1] if completed_dates else ""
        latest = [row for row in analysis if row["as_of"] == latest_date]
        latest_run_row = self.connection.execute(
            "SELECT * FROM reporting_run_audits WHERE market=? AND as_of<=? ORDER BY as_of DESC LIMIT 1",
            (market, through_date),
        ).fetchone()
        latest_run = dict(latest_run_row) if latest_run_row else None
        rolling_metrics = {
            "periods": len({row["as_of"] for row in baseline_rows}),
            **self._metric_summary(baseline_rows),
        }
        reporting_metrics = {
            "periods": len({row["as_of"] for row in reporting_rows}),
            **self._metric_summary(reporting_rows),
        }
        comparison = self._comparison(market)
        weight_validation = self._weight_validation(market)
        return {
            "market": market,
            "through_date": through_date,
            "latest_period": {
                "date": latest_date,
                "items": [
                    {
                        "rank": row["rank"], "symbol": row["symbol"], "name": row["name"],
                        "return_1d": row["return_1d"], "return_5d": row["return_5d"],
                        "return_20d": row["return_20d"],
                        "mfe": row["max_favorable_excursion"], "mae": row["max_adverse_excursion"],
                    }
                    for row in latest
                ],
            },
            "rolling_20": rolling_metrics,
            "reporting_rolling_20": reporting_metrics,
            "by_score_band": self._group_metrics(reporting_rows, "score_band"),
            "by_signal_type": self._group_metrics(reporting_rows, "signal_type"),
            "comparison": comparison,
            "weight_validation": weight_validation,
            "quality_verdict": self._quality_verdict(
                comparison, rolling_metrics, reporting_metrics, weight_validation
            ),
            "latest_reporting_run": latest_run,
        }

    def build_summary(self, through_date: str) -> dict[str, Any]:
        return {
            "schema_version": VALIDATION_VERSION,
            "through_date": through_date,
            "generated_at": _now(),
            "markets": {
                market: self.build_market_summary(market, through_date)
                for market in ("hk", "us")
            },
        }


def render_markdown(summary: Mapping[str, Any]) -> str:
    lines = [f"# Top5验证与报告准确率账本｜{summary.get('through_date')}", ""]
    for market, label in (("hk", "港股"), ("us", "美股")):
        node = (summary.get("markets") or {}).get(market) or {}
        rolling = node.get("rolling_20") or {}
        reporting = node.get("reporting_rolling_20") or {}
        comparison = node.get("comparison") or {}
        weight = node.get("weight_validation") or {}
        lines.extend(
            [
                f"## {label}", "",
                f"- 正式基线：{rolling.get('periods', 0)}期 / {rolling.get('signals', 0)}条；1日胜率 {rolling.get('win_rate_1d')}%，平均 {rolling.get('average_return_1d')}%。",
                f"- 报告评分：{reporting.get('periods', 0)}期 / {reporting.get('signals', 0)}条；触发 {reporting.get('triggered', 0)}/{reporting.get('trigger_eligible', 0)}。",
                f"- 同期对比：{comparison.get('periods', 0)}期；Top5平均重合 {comparison.get('average_overlap')}%。",
                f"- 权重结论：{weight.get('decision')}。", "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Top5 report validation ledger")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--analysis-state", type=Path, default=DEFAULT_ANALYSIS_STATE)
    parser.add_argument("--contract", type=Path, action="append", default=[])
    parser.add_argument("--through-date", default=date.today().isoformat())
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    args = parser.parse_args(argv)
    contracts = args.contract or sorted((REPORTING_ROOT / "output").glob("full_pool_contract_*.json"))
    ledger = Top5ValidationLedger(args.ledger)
    analysis_rows = ledger.import_analysis_history(args.analysis_state)
    contract_rows = 0
    for path in contracts:
        contract_rows += ledger.record_contract(json.loads(path.read_text(encoding="utf-8")))
    evaluated = ledger.evaluate(args.db, args.through_date)
    summary = ledger.build_summary(args.through_date)
    summary["ingest"] = {"analysis_rows": analysis_rows, "contract_rows": contract_rows, "evaluated_rows": evaluated}
    output_json = args.output_json or REPORTING_ROOT / "output" / f"top5_validation_{args.through_date.replace('-', '')}.json"
    output_md = args.output_markdown or output_json.with_suffix(".md")
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render_markdown(summary), encoding="utf-8")
    ledger.close()
    print(json.dumps({"summary": str(output_json), "markdown": str(output_md), **summary["ingest"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
