from datetime import date, datetime, timezone
import json
import unittest
from unittest import mock

from v2.hk_key_variables import (
    _signal_snapshot,
    build_snapshot,
    parse_hkex_southbound,
    parse_hxc_history,
    render_html,
    render_integrated_page,
)


class HKKeyVariablesTests(unittest.TestCase):
    def test_parse_hkex_combines_shanghai_and_shenzhen_southbound(self):
        payload = [
            self._market("SSE Southbound", "2026-08-25", 10_000, 6_000, 4_000),
            self._market("SZSE Southbound", "2026-08-25", 8_000, 3_000, 5_000),
        ]
        body = f"tabData = {json.dumps(payload)};".encode()
        parsed = parse_hkex_southbound(body)
        self.assertEqual(parsed["turnover_m"], 18_000)
        self.assertEqual(parsed["buy_m"], 9_000)
        self.assertEqual(parsed["sell_m"], 9_000)
        self.assertEqual(parsed["net_m"], 0)

    def test_signal_uses_daily_drivers_not_low_frequency_releases(self):
        payload = self._payload()
        signal = _signal_snapshot(payload)
        self.assertEqual(signal["label"], "偏谨慎")
        self.assertIn("南向当日净额", render_html(payload))

    def test_render_keeps_source_dates_and_methodology(self):
        rendered = render_html(self._payload())
        self.assertIn("买入额－卖出额", rendered)
        self.assertIn("2026-08-24", rendered)
        self.assertIn("低频不冒充日频", rendered)
        self.assertNotIn("grid-column:1/-1", rendered.replace(" ", ""))

    def test_hxc_history_uses_only_sessions_before_hk_cutoff(self):
        def stamp(day):
            return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)

        parsed = parse_hxc_history(
            [
                {"x": stamp("2026-08-19"), "y": 6300},
                {"x": stamp("2026-08-20"), "y": 6237},
                {"x": stamp("2026-08-21"), "y": 6400},
            ],
            date(2026, 8, 21),
        )
        self.assertEqual(parsed["date"], "2026-08-20")
        self.assertEqual(parsed["change_pct"], -1.0)

    def test_integrated_page_adds_pool_breadth_and_volume_context(self):
        rendered = render_integrated_page(
            self._payload(),
            {"total": 20, "advancers": 18, "decliners": 1, "volume_confirmed": 5},
        )
        self.assertIn("观察池上涨广度", rendered)
        self.assertIn(">18/20<", rendered)
        self.assertIn("量比 ≥ 1.05×", rendered)
        self.assertIn("观察池广度与量能分布", rendered)
        self.assertIn("定价拆解与次日验证", rendered)
        self.assertNotIn("不直接改Top5评分", rendered)
        self.assertNotIn("口径边界", rendered)
        self.assertNotIn("LPR", rendered)
        self.assertNotIn("PMI", rendered)

    @mock.patch("v2.hk_key_variables.fetch_china_releases")
    @mock.patch("v2.hk_key_variables.fetch_market_proxies")
    @mock.patch("v2.hk_key_variables.fetch_treasury")
    @mock.patch("v2.hk_key_variables.fetch_hibor")
    @mock.patch("v2.hk_key_variables.fetch_hk_liquidity")
    @mock.patch("v2.hk_key_variables.fetch_southbound")
    def test_snapshot_reuses_prior_liquidity_when_hkma_times_out(
        self,
        fetch_southbound,
        fetch_liquidity,
        fetch_hibor,
        fetch_treasury,
        fetch_market,
        fetch_china,
    ):
        prior = self._payload()
        fetch_southbound.return_value = prior["southbound"]
        fetch_liquidity.side_effect = RuntimeError("HKMA timeout")
        fetch_hibor.return_value = prior["hibor"]
        fetch_treasury.return_value = prior["treasury"]
        fetch_market.return_value = prior["market"]
        fetch_china.return_value = prior["china"]

        snapshot = build_snapshot(date(2026, 8, 27), fallback_payload=prior)

        self.assertEqual(snapshot["as_of"], "2026-08-27")
        self.assertEqual(snapshot["liquidity"]["date"], prior["liquidity"]["date"])
        self.assertEqual(snapshot["quality"]["fallback_blocks"], ["liquidity"])
        self.assertEqual(snapshot["quality"]["status"], "complete")

    @staticmethod
    def _market(name, day, total, buy, sell):
        values = [total, buy, sell, 1, 1, 1, 0]
        return {
            "market": name,
            "date": day,
            "content": [
                {
                    "style": 1,
                    "table": {
                        "schema": [["Total Turnover", "Buy Turnover", "Sell Turnover", "Total Trade Count", "Buy Trade Count", "Sell Trade Count", "ETF Turnover"]],
                        "tr": [{"td": [[f"{value:,}"]]} for value in values],
                    },
                }
            ],
        }

    @staticmethod
    def _payload():
        source = {"url": "https://example.test", "fetched_at": "2026-08-25T00:00:00+00:00", "sha256": "abc"}
        history = [
            {"date": f"2026-08-{25-index:02d}", "buy_m": 40_000, "sell_m": 45_000, "net_m": -5_000}
            for index in range(5)
        ]
        payload = {
            "as_of": "2026-08-25",
            "southbound": {"date": "2026-08-25", "net_hkd_bn": -5, "net_5d_hkd_bn": -25, "turnover_hkd_bn": 85, "history": history, "sources": [source]},
            "hibor": {"date": "2026-8-25", "overnight_pct": 3.125, "overnight_change_bp": 36.9, "one_month_pct": 2.783, "one_month_change_bp": 8.8, "sources": [source]},
            "liquidity": {"date": "2026-08-25", "aggregate_balance_hkd_bn": 54.049, "aggregate_balance_change_hkd_bn": -0.839, "weak_side": 7.85, "strong_side": 7.75, "source": source},
            "market": {"usdhkd": 7.8375, "usdhkd_date": "2026-08-25", "distance_to_weak_side": 0.0125, "hxc": {"date": "2026-08-24", "close": 6199.19, "change_pct": -1.66}, "etfs": {"KWEB": {"date": "2026-08-24", "change_pct": -1.54}, "FXI": {"date": "2026-08-24", "change_pct": -0.86}}, "sources": {"hxc": source, "usdhkd_etfs": source}},
            "treasury": {"date": "2026-08-24", "ten_year_pct": 4.7, "change_bp": -4, "source": source},
            "china": {"lpr": {"date": "2026-08-20", "one_year_pct": 3.0, "five_year_pct": 3.5, "source": source}, "pmi": {"period": "2026-07", "manufacturing": 49.2, "non_manufacturing": 49.0, "composite": 49.3, "source": source}},
        }
        payload["signal"] = _signal_snapshot(payload)
        return payload


if __name__ == "__main__":
    unittest.main()
