from datetime import date
import unittest

from src.services.finnhub_service import FinnhubService


class FinnhubServiceTests(unittest.TestCase):
    def test_earnings_summary_excludes_future_and_undisclosed_periods(self):
        summary = FinnhubService._build_earnings_summary(
            [
                {
                    "period": "2027-06-30",
                    "actual": 0.94,
                    "estimate": 0.93,
                    "surprisePercent": 1.1,
                },
                {
                    "period": "2026-09-30",
                    "actual": None,
                    "estimate": 1.05,
                    "surprisePercent": None,
                },
                {
                    "period": "2026-06-30",
                    "actual": 0.82,
                    "estimate": 0.80,
                    "surprisePercent": 2.5,
                },
                {
                    "period": "2026-03-31",
                    "actual": 0.75,
                    "estimate": 0.77,
                    "surprisePercent": -2.6,
                },
            ],
            as_of=date(2026, 8, 28),
        )

        self.assertEqual(summary["latest_period"], "2026-06-30")
        self.assertEqual(summary["latest_actual"], 0.82)
        self.assertEqual(summary["positive_surprise_quarters"], 1)
        self.assertEqual(summary["negative_surprise_quarters"], 1)

    def test_earnings_summary_returns_empty_without_a_completed_actual_period(self):
        summary = FinnhubService._build_earnings_summary(
            [{"period": "2026-09-30", "actual": None, "estimate": 1.05}],
            as_of=date(2026, 8, 28),
        )

        self.assertEqual(summary, {})


if __name__ == "__main__":
    unittest.main()
