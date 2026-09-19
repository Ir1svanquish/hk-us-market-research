import unittest
from types import SimpleNamespace

from reporting.market_foundation import benchmark_spec, validate_trading_date
from reporting.opportunity_scoring import (
    TECHNICAL_WEIGHTS,
    WEIGHTS,
    _auxiliary_indicator_score,
    _catalyst_score,
    _execution,
    _percentile,
    _price_structure_score,
    _relative_strength_score,
    _real_catalysts,
    _theme_names,
    _volume_confirmation_score,
    _volatility_risk_score,
)


class OpportunityScoringTests(unittest.TestCase):
    def test_placeholder_catalyst_does_not_add_catalyst_points(self):
        stock = SimpleNamespace(
            catalysts=["暂无直接利好催化"],
            earnings_outlook="",
        )
        self.assertEqual(_real_catalysts(stock), [])
        self.assertEqual(_catalyst_score(stock), 4.0)

    def test_consumer_electronics_theme_covers_smartphone_language(self):
        self.assertEqual(_theme_names("智能手机、可折叠 iPhone 与终端硬件"), ["消费电子"])

    def test_opportunity_weights_sum_to_100(self):
        self.assertEqual(sum(WEIGHTS.values()), 100)
        self.assertEqual(sum(TECHNICAL_WEIGHTS.values()), 100)
        self.assertEqual(
            TECHNICAL_WEIGHTS,
            {
                "price_structure": 35,
                "relative_strength_sector": 25,
                "volume_confirmation": 20,
                "volatility_risk": 15,
                "auxiliary_indicators": 5,
            },
        )

    def test_percentile_is_deterministic_and_neutral_for_missing(self):
        result = _percentile({"A": 1.0, "B": 3.0, "C": 2.0, "D": None})
        self.assertEqual(result["A"], 0.0)
        self.assertEqual(result["B"], 1.0)
        self.assertEqual(result["C"], 0.5)
        self.assertEqual(result["D"], 0.5)

    def test_relative_strength_uses_market_and_industry_excess(self):
        strong = _relative_strength_score(
            {"return_5d_percentile": 0.1, "return_20d_percentile": 0.1},
            {
                "status": "ok",
                "market_excess_5d_pct": 10,
                "market_excess_20d_pct": 10,
                "industry_excess_5d_pct": 10,
                "industry_excess_20d_pct": 10,
            },
        )
        weak = _relative_strength_score(
            {"return_5d_percentile": 0.9, "return_20d_percentile": 0.9},
            {
                "status": "ok",
                "market_excess_5d_pct": -10,
                "market_excess_20d_pct": -10,
                "industry_excess_5d_pct": -10,
                "industry_excess_20d_pct": -10,
            },
        )
        self.assertEqual(strong, 15.0)
        self.assertEqual(weak, 0.0)

    def test_price_structure_dominates_auxiliary_indicators(self):
        strong = _price_structure_score(
            {
                "completed_close": 112,
                "atr14": 2,
                "price_structure": {
                    "status": "ok",
                    "trend": "higher_high_higher_low",
                    "breakout_state": "above_range",
                    "retest_level": 105,
                },
            }
        )
        weak = _price_structure_score(
            {
                "completed_close": 90,
                "atr14": 4,
                "price_structure": {
                    "status": "ok",
                    "trend": "lower_high_lower_low",
                    "breakout_state": "inside_range",
                    "range_position_pct": 20,
                    "retest_level": 95,
                    "next_resistance": 94,
                },
            }
        )
        stock = SimpleNamespace(
            technical={"trend_score": 100, "ma_alignment": "强势多头排列"},
            conclusion="",
            risks=[],
        )
        self.assertGreater(strong, weak)
        self.assertEqual(strong, 35.0)
        self.assertLessEqual(_auxiliary_indicator_score(stock), 5.0)

    def test_volume_and_volatility_reward_confirmed_stable_structure(self):
        stock = SimpleNamespace(relative_volume=None, amplitude="3.0%")
        healthy = {
            "relative_volume_5d": 1.4,
            "return_5d": 0.08,
            "amount_percentile": 0.8,
            "completed_close": 100,
            "atr14": 2,
            "price_structure": {"breakout_state": "testing_range_high", "gap_state": "none"},
        }
        fragile = {
            "relative_volume_5d": 1.4,
            "return_5d": -0.08,
            "amount_percentile": 0.8,
            "completed_close": 100,
            "atr14": 12,
            "price_structure": {"breakout_state": "inside_range", "gap_state": "gap_down"},
        }
        self.assertGreater(
            _volume_confirmation_score(stock, healthy),
            _volume_confirmation_score(stock, fragile),
        )
        self.assertGreater(
            _volatility_risk_score(stock, healthy),
            _volatility_risk_score(stock, fragile),
        )

    def test_cautious_trade_card_maps_to_distinct_execution_status(self):
        stock = SimpleNamespace(
            review={
                "trade_card": {
                    "status": "ready_cautious",
                    "label": "谨慎可执行",
                    "risk_reward": 1.8,
                },
                "event_gate": {"priority": 0},
            },
            current_price="100",
            entry="100",
            checklist=[],
            amplitude="3%",
            action="买入",
            earnings_outlook="",
            risks=[],
            relative_volume=1.0,
        )
        _, status, note, _ = _execution(stock, {"amount_percentile": 0.5}, 5.0, "2026-09-10")
        self.assertEqual(status, "谨慎可执行")
        self.assertEqual(note, "谨慎可执行")

    def test_calendar_and_benchmark_contracts_are_explicit(self):
        check = validate_trading_date(__import__("datetime").date(2026, 8, 21), "us")
        self.assertTrue(check.is_trading_session)
        self.assertEqual(check.timezone, "America/New_York")
        mapped = benchmark_spec("MU", "us")
        self.assertEqual(mapped["market_symbol"], "SPY.US")
        self.assertEqual(mapped["industry_symbol"], "SMH.US")
        fallback = benchmark_spec("HK00883", "hk")
        self.assertTrue(fallback["industry_fallback_to_market"])


if __name__ == "__main__":
    unittest.main()
