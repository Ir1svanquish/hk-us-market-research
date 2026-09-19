from __future__ import annotations

import json
from datetime import date, timedelta
import sqlite3
import tempfile
import unittest
from pathlib import Path

from reporting.top5_validation import Top5ValidationLedger


class Top5ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.market_db = self.root / "market.sqlite3"
        connection = sqlite3.connect(self.market_db)
        connection.execute(
            """
            CREATE TABLE stock_daily (
                code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL
            )
            """
        )
        connection.commit()
        connection.close()
        self.ledger = Top5ValidationLedger(self.root / "ledger.sqlite3")

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def _insert_bars(self, symbol: str, bars: list[tuple]) -> None:
        connection = sqlite3.connect(self.market_db)
        connection.executemany("INSERT INTO stock_daily VALUES (?,?,?,?,?,?)", [(symbol, *bar) for bar in bars])
        connection.commit()
        connection.close()

    def test_analysis_top5_tracks_forward_returns_and_excursions(self) -> None:
        bars = [("2026-01-02", 100, 101, 99, 100)]
        for index in range(1, 21):
            close = 100 + index
            bars.append((f"2026-01-{index + 2:02d}", close - 0.5, close + 1, 98 - index / 10, close))
        self._insert_bars("TEST", bars)
        state = self.root / "top5.json"
        state.write_text(
            json.dumps({"regions": {"us": {"entries": [{"date": "2026-01-02", "codes": ["TEST"]}]}}}),
            encoding="utf-8",
        )
        self.assertEqual(self.ledger.import_analysis_history(state), 1)
        self.ledger.evaluate(self.market_db, "2026-01-31")
        summary = self.ledger.build_market_summary("us", "2026-01-31")
        rolling = summary["rolling_20"]
        self.assertEqual(rolling["sample_1d"], 1)
        self.assertEqual(rolling["average_return_1d"], 1.0)
        self.assertEqual(rolling["average_return_5d"], 5.0)
        self.assertEqual(rolling["average_return_20d"], 20.0)
        self.assertEqual(rolling["average_mfe"], 21.0)
        self.assertLess(rolling["average_mae"], 0)

    def test_structured_breakout_records_trigger_fill_target_and_next_session_return(self) -> None:
        self._insert_bars(
            "TEST",
            [
                ("2026-01-02", 100, 101, 99, 100),
                ("2026-01-03", 102, 106, 101, 104),
                ("2026-01-04", 106, 111, 104, 107),
                ("2026-01-05", 107, 109, 106, 108),
            ],
        )
        contract = {
            "report_date": "2026-01-02",
            "scoring_version": "structured-test",
            "totals": {
                "stocks": 1,
                "us": 1,
                "official_quality_coverage": 1,
                "relative_strength_coverage": 1,
                "volume_confirmation_coverage": 1,
            },
            "validation": {"valid": True, "errors": [], "warnings": []},
            "stocks": [
                {
                    "identity": {"market": "us", "symbol": "TEST", "name": "Test Co"},
                    "market_data": {"completed_close": 100},
                    "opportunity": {
                        "rank": 1,
                        "score": 75,
                        "components": {"technical_composite": 35},
                        "technical_components": {
                            "price_structure": 25,
                            "relative_strength_sector": 20,
                            "volume_confirmation": 15,
                            "volatility_risk": 10,
                            "auxiliary_indicators": 5,
                        },
                    },
                    "execution": {
                        "status": "可执行",
                        "standard_trade_card": {
                            "active": True,
                            "setup_type": "breakout",
                            "trigger_price": 105,
                            "reference_entry": 105,
                            "stop_loss": 100,
                            "target_1": 110,
                            "target_2": 115,
                            "valid_sessions": 3,
                        },
                    },
                }
            ],
        }
        self.ledger.record_contract(contract)
        self.ledger.evaluate(self.market_db, "2026-01-05")
        row = dict(
            self.ledger.connection.execute(
                "SELECT * FROM opportunity_validation_snapshots WHERE source_kind='reporting'"
            ).fetchone()
        )
        self.assertEqual(row["trigger_state"], "triggered")
        self.assertEqual(row["trigger_date"], "2026-01-04")
        self.assertEqual(row["trigger_fill_price"], 106)
        self.assertEqual(row["target_1_hit"], 1)
        self.assertEqual(row["first_exit"], "target_1")
        self.assertAlmostEqual(row["trigger_return_1d"], 1.8868, places=4)

    def test_weight_changes_remain_blocked_before_twenty_periods(self) -> None:
        summary = self.ledger.build_market_summary("hk", "2026-01-31")
        weight = summary["weight_validation"]
        self.assertFalse(weight["ready_to_adjust"])
        self.assertIn("维持35/25/20/15/5", weight["decision"])
        self.assertEqual(summary["quality_verdict"]["status"], "insufficient_evidence")

    def test_database_summary_uses_all_periods_for_quality_verdict(self) -> None:
        """The 20-period display window must not cap the 40-period validation gate."""
        components = json.dumps({
            "price_structure": 1,
            "relative_strength_sector": 1,
            "volume_confirmation": 1,
            "volatility_risk": 1,
            "auxiliary_indicators": 1,
        })
        for period in range(60):
            as_of = (date(2025, 1, 1) + timedelta(days=period)).isoformat()
            self.ledger.connection.execute(
                """
                INSERT INTO reporting_run_audits (
                    run_id, as_of, market, model_version, pool_size, top5_json,
                    overlap_count, overlap_ratio, created_at, updated_at
                ) VALUES (?, ?, 'hk', 'test-model', 2, '[]', 1, 50.0, ?, ?)
                """,
                (f"run-{period}", as_of, as_of, as_of),
            )
            for symbol in ("AAA", "BBB"):
                for source_kind, return_5d, mae in (
                    ("analysis", 0.0, -2.0),
                    ("reporting", 1.0, -1.0),
                ):
                    self.ledger.connection.execute(
                        """
                        INSERT INTO opportunity_validation_snapshots (
                            record_id, as_of, market, symbol, source_kind,
                            model_version, is_top5, technical_components_json,
                            return_5d, max_adverse_excursion, created_at, updated_at
                        ) VALUES (?, ?, 'hk', ?, ?, 'test-model', 1, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"{source_kind}-{period}-{symbol}", as_of, symbol,
                            source_kind, components, return_5d, mae, as_of, as_of,
                        ),
                    )
        self.ledger.connection.commit()

        summary = self.ledger.build_market_summary("hk", "2025-03-31")

        self.assertEqual(summary["comparison"]["periods"], 20)
        self.assertEqual(summary["validation_comparison"]["periods"], 60)
        self.assertEqual(summary["validation_comparison"]["paired_periods_5d"], 60)
        self.assertEqual(summary["quality_verdict"]["status"], "reporting_better")

    def test_quality_verdict_requires_positive_interval_and_non_worse_risk(self) -> None:
        verdict = self.ledger._quality_verdict(
            {"paired_periods_5d": 40, "paired_delta_5d_ci90": [0.2, 1.4]},
            {"average_mae": -8.0},
            {"average_mae": -7.5},
            {"ready_to_adjust": True, "matured_rows_5d": 100},
        )
        self.assertEqual(verdict["status"], "reporting_better")

        worse_risk = self.ledger._quality_verdict(
            {"paired_periods_5d": 40, "paired_delta_5d_ci90": [0.2, 1.4]},
            {"average_mae": -8.0},
            {"average_mae": -9.0},
            {"ready_to_adjust": True, "matured_rows_5d": 100},
        )
        self.assertEqual(worse_risk["status"], "reporting_worse")

        preliminary = self.ledger._quality_verdict(
            {"paired_periods_5d": 20, "paired_delta_5d_ci90": [0.2, 1.4]},
            {"average_mae": -8.0},
            {"average_mae": -7.5},
            {"ready_to_adjust": True, "matured_rows_5d": 100},
        )
        self.assertEqual(preliminary["status"], "insufficient_evidence")
        self.assertIn("盲测窗口", preliminary["reason"])


if __name__ == "__main__":
    unittest.main()
