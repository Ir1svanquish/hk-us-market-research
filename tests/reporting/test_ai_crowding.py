import unittest

from reporting.ai_crowding import enrich_ai_crowding_trend, score_ai_crowding


class AICrowdingTests(unittest.TestCase):
    def test_hot_social_with_price_reversal_is_not_called_observation_only(self):
        raw = {
            "layers": {
                "leaders": {
                    "avg_return_5d": -4.2,
                    "avg_excess_vs_qqq_20d": -4.3,
                    "above_ma20_ratio": 0.6,
                    "above_ma50_ratio": 0.4,
                    "social_heat_avg": 69.5,
                },
                "platforms": {
                    "avg_return_5d": -6.8,
                    "avg_excess_vs_qqq_20d": 16.2,
                    "above_ma20_ratio": 0.71,
                    "above_ma50_ratio": 0.57,
                    "social_heat_avg": 68.7,
                },
                "high_beta": {
                    "avg_return_5d": -3.0,
                    "avg_excess_vs_qqq_20d": 11.9,
                    "above_ma20_ratio": 0.75,
                    "above_ma50_ratio": 0.0,
                    "social_heat_avg": 71.5,
                },
            },
            "macro": {
                "TNX": 4.74,
                "VIX": {"price": 15.1},
                "HYG": {"change_pct": 0.06},
                "LQD": {"change_pct": -0.13},
            },
            "risk_triggers": ["QQQ 跌破关键位"],
        }
        result = score_ai_crowding(raw)
        self.assertGreaterEqual(result.crowding_index, 60)
        self.assertGreaterEqual(result.break_risk, 60)
        self.assertNotEqual(result.status, "观察期")
        self.assertEqual(len(result.signals), 4)

    def test_history_enrichment_adds_session_changes_and_percentile(self):
        current = {"as_of": "2026-08-21", "crowding_index": 70, "break_risk": 50}
        history = [
            {"as_of": "2026-08-14", "crowding_index": 60},
            {"as_of": "2026-08-20", "crowding_index": 66},
        ]
        result = enrich_ai_crowding_trend(current, history)
        self.assertEqual(result["change_1d"], 4)
        self.assertEqual(result["change_5d"], 10)
        self.assertEqual(result["percentile_1y"], 100)
        self.assertEqual(result["history_samples"], 3)


if __name__ == "__main__":
    unittest.main()
