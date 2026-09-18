# -*- coding: utf-8 -*-
"""Regression tests for same-source quote snapshots and market currencies."""

import unittest

from src.analyzer import AnalysisResult, GeminiAnalyzer


class MarketSnapshotConsistencyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        self.analyzer._skill_instructions_override = ""
        self.analyzer._default_skill_policy_override = ""
        self.analyzer._use_legacy_default_prompt_override = False

    def test_hk_snapshot_prefers_realtime_prev_close_and_recomputes_change(self) -> None:
        snapshot = self.analyzer._build_market_snapshot(
            {
                "code": "HK00700",
                "report_language": "zh",
                "date": "2026-08-20",
                "today": {
                    "close": 451.4,
                    "open": 454.6,
                    "high": 457.4,
                    "low": 448.8,
                    "pct_chg": 0.94,
                    "amount": 8_901_000_000,
                },
                "yesterday": {"close": 442.4},
                "realtime": {
                    "price": 451.4,
                    "prev_close": 447.2,
                    "open": 454.6,
                    "high": 457.4,
                    "low": 448.8,
                    "change_pct": 0.94,
                    "amount": 8_901_000_000,
                    "source": "longbridge",
                },
            }
        )

        self.assertEqual(snapshot["prev_close"], "447.20 港元")
        self.assertEqual(snapshot["close"], "451.40 港元")
        self.assertEqual(snapshot["pct_chg"], "0.94%")
        self.assertEqual(snapshot["amount"], "89.01 亿港元")
        self.assertTrue(snapshot["data_consistent"])
        self.assertIsNone(snapshot["consistency_warning"])

    def test_conflicting_quote_downgrades_actionable_advice(self) -> None:
        snapshot = self.analyzer._build_market_snapshot(
            {
                "code": "HK06166",
                "report_language": "zh",
                "today": {"close": 95.1, "pct_chg": 6.67},
                "yesterday": {"close": 101.0},
                "realtime": {
                    "price": 95.1,
                    "prev_close": 101.0,
                    "change_pct": 6.67,
                    "source": "longbridge",
                },
            }
        )
        result = AnalysisResult(
            code="HK06166",
            name="剑桥科技",
            sentiment_score=76,
            trend_prediction="看多",
            operation_advice="买入",
            decision_type="buy",
            confidence_level="高",
            dashboard={
                "core_conclusion": {"one_sentence": "现价可买入"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"ideal_buy": "95元"}},
            },
        )
        result.market_snapshot = snapshot

        self.analyzer._apply_market_snapshot_guard(result, "zh")

        self.assertFalse(snapshot["data_consistent"])
        self.assertEqual(snapshot["pct_chg"], "-5.84%")
        self.assertEqual(result.operation_advice, "观望")
        self.assertEqual(result.decision_type, "hold")
        self.assertEqual(result.confidence_level, "低")
        self.assertLessEqual(result.sentiment_score, 50)
        self.assertEqual(
            result.dashboard["battle_plan"]["sniper_points"]["ideal_buy"],
            "N/A",
        )

    def test_us_snapshot_and_trade_text_use_usd(self) -> None:
        snapshot = self.analyzer._build_market_snapshot(
            {
                "code": "NVDA",
                "report_language": "zh",
                "today": {"close": 217.56, "amount": 21_192_000_000},
                "yesterday": {"close": 219.74},
            }
        )
        result = AnalysisResult(
            code="NVDA",
            name="英伟达",
            sentiment_score=70,
            trend_prediction="看多",
            operation_advice="买入",
            analysis_summary="217.5元附近轻仓",
            dashboard={
                "core_conclusion": {
                    "one_sentence": "217.5元附近轻仓",
                    "position_advice": {"no_position": "回踩212元再买"},
                },
                "intelligence": {"earnings_outlook": "季度营收约2048亿元"},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "217.5元",
                        "stop_loss": "210元",
                    }
                },
            },
        )

        self.analyzer._normalize_result_trade_currency(result, "NVDA", "zh")

        self.assertEqual(snapshot["close"], "217.56 美元")
        self.assertEqual(snapshot["amount"], "211.92 亿美元")
        self.assertIn("217.5美元", result.analysis_summary)
        self.assertIn("212美元", result.dashboard["core_conclusion"]["position_advice"]["no_position"])
        self.assertEqual(
            result.dashboard["battle_plan"]["sniper_points"]["stop_loss"],
            "210美元",
        )
        self.assertEqual(
            result.dashboard["intelligence"]["earnings_outlook"],
            "季度营收约2048亿元",
        )

    def test_market_prompt_examples_use_listing_currency(self) -> None:
        us_prompt = self.analyzer._get_analysis_system_prompt("zh", "NVDA")
        hk_prompt = self.analyzer._get_analysis_system_prompt("zh", "HK00700")

        self.assertIn("XX美元", us_prompt)
        self.assertIn("所有股价和交易点位使用美元", us_prompt)
        self.assertIn("XX港元", hk_prompt)
        self.assertIn("所有股价和交易点位使用港元", hk_prompt)


if __name__ == "__main__":
    unittest.main()
