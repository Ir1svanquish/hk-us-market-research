from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from v2.official_sources.bls_calendar import BLSCalendarClient, MacroRelease
from v2.official_sources.board_calendar import HKEXBoardCalendarClient
from v2.official_sources.classifier import (
    classify_hkex_event,
    classify_hkex_events,
    classify_sec_event,
    classify_sec_events,
)
from v2.official_sources.hkexnews import HKEXnewsClient, normalize_hk_symbol
from v2.official_sources.event_context import EventContextBuilder, strongest_by_symbol
from v2.official_sources.event_context_store import UnifiedEventStore
from v2.official_sources.decision_ledger import DecisionLedger
from v2.official_sources.decision_quality import _quality_gate, assess_decisions, parse_production_report
from v2.official_sources.gates import evaluate_event_gate
from v2.official_sources.macro_calibration import (
    apply_calibration,
    build_calibration,
    select_history,
)
from v2.official_sources.macro_catalog import (
    classify_macro_event,
    macro_market_score,
    simple_macro_explanation,
    translate_macro_title,
)
from v2.official_sources.macro_scenarios import analyze_release, scenario_probabilities
from v2.official_sources.market_metrics import (
    calculate_history_metrics,
    normalize_volume_contract,
    to_longbridge_symbol,
)
from v2.official_sources.models import OfficialFiling, OfficialKeyDate, UnifiedEvent
from v2.official_sources.nasdaq_calendar import NasdaqMacroCalendarClient
from v2.official_sources.sec_edgar import SECEdgarClient, normalize_us_symbol
from v2.official_sources.storage import OfficialFilingStore
from v2.official_sources.trading_cards import _first_price, _fmt, build_trade_card, expiry_after_sessions
from v2.earnings_scenarios import build_earnings_scenario


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.responses.pop(0))


class OfficialSourceTests(unittest.TestCase):
    def test_reader_price_precision_follows_market_tick_size(self):
        self.assertEqual(_fmt(317.2614, "hk"), "317.2")
        self.assertEqual(_fmt(1372.6607, "hk"), "1373")
        self.assertEqual(_fmt(224.7564, "us"), "224.76")

    def test_price_parser_does_not_treat_indicator_period_or_percentage_as_price(self):
        self.assertEqual(_first_price("72.00港元（接近MA5）"), 72.0)
        self.assertIsNone(_first_price("跌破MA20或4%止损"))
        self.assertIsNone(_first_price("RSI低于30后重估"))

    def test_historical_event_context_enforces_local_report_cutoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            builder = EventContextBuilder(
                OfficialFilingStore(Path(temp_dir) / "official.sqlite3"),
                date(2026, 8, 21),
            )
            self.assertTrue(
                builder._available_by_report_cutoff(
                    {"market": "us", "published_at": "2026-08-21T20:20:00Z"}
                )
            )
            self.assertFalse(
                builder._available_by_report_cutoff(
                    {"market": "us", "published_at": "2026-08-22T00:32:00Z"}
                )
            )
            self.assertTrue(
                builder._available_by_report_cutoff(
                    {"market": "hk", "published_at": "2026-08-21T08:20:00Z"}
                )
            )
            self.assertFalse(
                builder._available_by_report_cutoff(
                    {"market": "hk", "published_at": "2026-08-21T09:00:00Z"}
                )
            )

    def test_completed_daily_metrics_separate_relative_volume_and_atr(self):
        candles = []
        start = date(2026, 7, 20)
        for index in range(25):
            close = 100 + index
            candles.append(
                SimpleNamespace(
                    timestamp=datetime.combine(start + timedelta(days=index), datetime.min.time()),
                    close=str(close),
                    high=str(close + 2),
                    low=str(close - 2),
                    volume=str(200 if index == 24 else 100),
                )
            )
        metrics = calculate_history_metrics(candles, date(2026, 8, 20))
        self.assertEqual(metrics["history_status"], "ok")
        self.assertEqual(metrics["relative_volume_5d"], 2.0)
        self.assertEqual(metrics["relative_volume_20d"], 2.0)
        self.assertEqual(metrics["atr14"], 4.0)
        self.assertEqual(metrics["price_structure"]["status"], "ok")
        self.assertIn("breakout_level", metrics["price_structure"])
        self.assertIn("retest_level", metrics["price_structure"])
        self.assertEqual(metrics["price_structure"]["latest_low"], 122.0)
        self.assertEqual(metrics["price_structure"]["latest_close"], 124.0)
        self.assertEqual(metrics["price_structure"]["previous_close"], 123.0)
        self.assertEqual(to_longbridge_symbol("00981", "hk"), "981.HK")
        self.assertEqual(to_longbridge_symbol("BRK-B", "us"), "BRK.B.US")

    def test_hk_daily_candle_uses_exchange_date_not_raw_utc_date(self):
        candles = []
        for index in range(21):
            # 16:00 UTC belongs to the following Hong Kong calendar date.
            timestamp = datetime(2026, 7, 31, 16, tzinfo=timezone.utc) + timedelta(days=index)
            candles.append(
                SimpleNamespace(
                    timestamp=timestamp,
                    close=str(100 + index),
                    high=str(102 + index),
                    low=str(98 + index),
                    volume="100",
                )
            )
        metrics = calculate_history_metrics(candles, date(2026, 8, 21), "hk")
        self.assertEqual(metrics["completed_session_date"], "2026-08-21")
        naive = [
            SimpleNamespace(
                timestamp=item.timestamp.replace(tzinfo=None),
                close=item.close,
                high=item.high,
                low=item.low,
                volume=item.volume,
            )
            for item in candles
        ]
        naive_metrics = calculate_history_metrics(naive, date(2026, 8, 21), "hk")
        self.assertEqual(naive_metrics["completed_session_date"], "2026-08-21")

    def test_historical_volume_is_used_when_live_ratio_is_from_another_date(self):
        metric = {
            "volume_ratio": 1.8,
            "live_status": "ok",
            "relative_volume_5d": 0.9,
            "relative_volume_20d": 1.1,
            "completed_session_date": "2026-08-21",
            "fetched_at": "2026-08-23T12:00:00+00:00",
        }
        contract = normalize_volume_contract(metric, date(2026, 8, 21), "us")
        self.assertFalse(contract["live_applicable"])
        self.assertEqual(contract["confirmation_value"], 0.9)
        self.assertEqual(contract["confirmation_scope"], "completed_session_5d")

    def test_standard_trade_card_is_executable_only_after_all_checks(self):
        assessment = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {
                "snapshot_price": "100美元",
                "entry": "100美元（回踩附近）",
                "stop": "95美元",
                "target": "110美元，突破后看115美元",
                "position": "建议仓位：2成",
            },
            "final_gate": {"gate_action": "info_only", "priority": 0},
        }
        metric = {
            "status": "ok",
            "completed_close": 100,
            "atr14": 2,
            "volume_ratio": 1.4,
            "relative_volume_5d": 1.2,
            "relative_volume_20d": 1.1,
            "price_structure": {
                "status": "ok",
                "trend": "higher_high_higher_low",
                "breakout_state": "above_range",
                "breakout_level": 100,
                "retest_level": 97,
                "support_level": 95,
                "recent_high_5d": 100,
                "last_swing_low": 95,
                "next_resistance": 110,
            },
        }
        card = build_trade_card("TEST", assessment, metric, as_of=date(2026, 8, 21))
        self.assertEqual(card["status"], "ready")
        self.assertTrue(card["active"])
        self.assertEqual(card["stop_loss"], 99.0)
        self.assertGreaterEqual(card["risk_reward"], 1.5)
        self.assertGreater(card["target_2"], card["target_1"])
        self.assertEqual(card["expires_at"], "2026-08-25")
        self.assertEqual(expiry_after_sessions(date(2026, 8, 21)), "2026-08-25")

    def test_standard_trade_card_requires_price_and_setup_appropriate_volume(self):
        assessment = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {
                "snapshot_price": "100",
                "entry": "突破100",
                "stop": "95",
                "target": "110",
            },
            "final_gate": {"gate_action": "info_only", "priority": 0},
        }
        structure = {
            "status": "ok",
            "trend": "higher_high_higher_low",
            "breakout_state": "inside_range",
            "breakout_level": 100,
            "retest_level": 97,
            "support_level": 95,
            "recent_high_5d": 99,
            "last_swing_low": 95,
            "next_resistance": 110,
        }
        waiting_price = build_trade_card(
            "TEST",
            assessment,
            {
                "status": "ok",
                "completed_close": 99,
                "atr14": 2,
                "relative_volume_5d": 1.4,
                "price_structure": structure,
            },
            as_of=date(2026, 8, 21),
        )
        self.assertEqual(waiting_price["status"], "trigger_wait")
        self.assertEqual(waiting_price["label"], "等待价格确认")
        self.assertFalse(waiting_price["active"])

        waiting_volume = build_trade_card(
            "TEST",
            assessment,
            {
                "status": "ok",
                "completed_close": 101,
                "atr14": 2,
                "relative_volume_5d": 1.0,
                "price_structure": {**structure, "breakout_state": "above_range"},
            },
            as_of=date(2026, 8, 21),
        )
        self.assertEqual(waiting_volume["status"], "ready")
        self.assertEqual(waiting_volume["label"], "条件买入（量能中性）")
        self.assertTrue(waiting_volume["active"])

        weak_breakout = build_trade_card(
            "TEST",
            assessment,
            {
                "status": "ok",
                "completed_close": 101,
                "atr14": 2,
                "relative_volume_5d": 0.7,
                "price_structure": {**structure, "breakout_state": "above_range"},
            },
            as_of=date(2026, 8, 21),
        )
        self.assertEqual(weak_breakout["status"], "trigger_wait")
        self.assertEqual(weak_breakout["label"], "等待量能确认")
        self.assertFalse(weak_breakout["active"])

    def test_pullback_reclaim_can_confirm_on_contracted_volume(self):
        assessment = {
            "market": "hk",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {
                "snapshot_price": "25.02",
                "entry": "24.64 附近低吸",
                "stop": "24.0",
                "target": "25.54",
            },
            "final_gate": {"gate_action": "info_only", "priority": 0},
        }
        card = build_trade_card(
            "TEST",
            assessment,
            {
                "status": "ok",
                "completed_close": 25.02,
                "atr14": 0.63,
                "relative_volume_5d": 0.77,
                "price_structure": {
                    "status": "ok",
                    "trend": "range_or_transition",
                    "breakout_state": "inside_range",
                    "breakout_level": 26.0,
                    "retest_level": 24.64,
                    "support_level": 24.64,
                    "recent_high_5d": 26.0,
                    "last_swing_low": 24.64,
                    "next_resistance": 25.54,
                    "latest_open": 25.24,
                    "latest_low": 24.88,
                    "latest_close": 25.02,
                    "previous_close": 24.88,
                },
            },
            as_of=date(2026, 9, 10),
        )
        self.assertEqual(card["setup_type"], "pullback")
        self.assertTrue(card["price_condition_met"])
        self.assertEqual(card["status"], "ready")
        self.assertEqual(card["label"], "缩量回踩确认")
        self.assertIn("缩量回踩不作否决", card["watch_condition"])

    def test_quality_gate_uses_moderate_confidence_for_conditional_plan(self):
        self.assertEqual(_quality_gate(90, 59, date(2026, 9, 10))["gate_action"], "conditional_only")
        self.assertEqual(_quality_gate(90, 60, date(2026, 9, 10))["gate_action"], "info_only")

    def test_soft_quality_and_earnings_blocks_are_wait_states_not_risk_failure(self):
        base = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {"snapshot_price": "100", "entry": "100", "stop": "95", "target": "110"},
        }
        metric = {"status": "ok", "completed_close": 100, "atr14": 2, "relative_volume_5d": 1.3}
        quality_wait = build_trade_card(
            "TEST",
            {
                **base,
                "event_gate": {"gate_action": "info_only", "priority": 0},
                "final_gate": {"gate_action": "block_new_positions", "priority": 4, "reason": "数据不足。"},
            },
            metric,
            as_of=date(2026, 9, 10),
        )
        self.assertEqual(quality_wait["status"], "quality_wait")

        earnings_wait = build_trade_card(
            "TEST",
            {
                **base,
                "event_gate": {"gate_action": "block_new_positions", "priority": 4, "reason": "财报临近。"},
                "final_gate": {"gate_action": "block_new_positions", "priority": 4, "reason": "财报临近。"},
            },
            metric,
            as_of=date(2026, 9, 10),
        )
        self.assertEqual(earnings_wait["status"], "event_wait")
        self.assertEqual(earnings_wait["label"], "事件前暂停新开仓")

        critical = build_trade_card(
            "TEST",
            {
                **base,
                "event_gate": {"gate_action": "risk_off", "priority": 5, "reason": "停牌。"},
                "final_gate": {"gate_action": "risk_off", "priority": 5, "reason": "停牌。"},
            },
            metric,
            as_of=date(2026, 9, 10),
        )
        self.assertEqual(critical["status"], "blocked")

    def test_standard_trade_card_cancels_low_reward_and_respects_event_gate(self):
        base = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {
                "snapshot_price": "100",
                "entry": "100",
                "stop": "95",
                "target": "102",
                "position": "2成",
            },
            "final_gate": {"gate_action": "info_only", "priority": 0},
        }
        metric = {"status": "ok", "completed_close": 100, "atr14": 2, "relative_volume_5d": 0.7}
        low_reward = build_trade_card("TEST", base, metric, as_of=date(2026, 8, 21))
        self.assertEqual(low_reward["status"], "invalid_risk_reward")
        self.assertFalse(low_reward["active"])
        gated = build_trade_card(
            "TEST",
            {
                **base,
                "final_gate": {
                    "gate_action": "conditional_only",
                    "priority": 3,
                    "reason": "财报临近。",
                    "gate_until": "2026-08-27",
                },
                "event_gate": {
                    "gate_action": "conditional_only",
                    "priority": 3,
                    "reason": "财报临近。",
                    "gate_until": "2026-08-27",
                },
            },
            metric,
            as_of=date(2026, 8, 21),
        )
        self.assertEqual(gated["status"], "event_wait")
        self.assertIsNone(gated["trigger_price"])
        self.assertIn("连续两日", low_reward["volume_confirmation"]["rule"])

    def test_trade_card_prefers_price_structure_over_ma_based_source_entry(self):
        assessment = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {
                "snapshot_price": "100",
                "entry": "100（MA5附近低吸）",
                "stop": "跌破MA20止损 92",
                "target": "MA10附近 104",
                "position": "2成",
            },
            "final_gate": {"gate_action": "info_only", "priority": 0},
        }
        metric = {
            "status": "ok",
            "completed_close": 100,
            "atr14": 2,
            "relative_volume_5d": 1.4,
            "price_structure": {
                "status": "ok",
                "trend": "higher_high_higher_low",
                "breakout_state": "inside_range",
                "breakout_level": 108,
                "retest_level": 96,
                "support_level": 94,
                "recent_high_5d": 103,
                "last_swing_low": 94,
                "next_resistance": 110,
            },
        }
        card = build_trade_card("TEST", assessment, metric, as_of=date(2026, 8, 21))
        self.assertEqual(card["setup_type"], "pullback")
        self.assertEqual(card["reference_entry"], 96)
        self.assertIn("原突破位", card["watch_condition"])
        self.assertNotIn("MA", card["watch_condition"])
        self.assertIn("结构位", card["invalidation_condition"])
        self.assertNotEqual(card["source_reference_entry"], card["reference_entry"])

    def test_breakout_already_above_level_is_described_as_hold_confirmation(self):
        assessment = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {
                "snapshot_price": "110",
                "entry": "突破105",
                "stop": "100",
                "target": "120",
            },
            "final_gate": {"gate_action": "info_only", "priority": 0},
        }
        metric = {
            "status": "ok",
            "completed_close": 110,
            "atr14": 2,
            "relative_volume_5d": 1.0,
            "price_structure": {
                "status": "ok",
                "trend": "higher_high_higher_low",
                "breakout_state": "above_range",
                "breakout_level": 105,
                "retest_level": 105,
                "support_level": 100,
                "recent_high_5d": 110,
                "last_swing_low": 100,
                "next_resistance": 120,
            },
        }
        card = build_trade_card("TEST", assessment, metric, as_of=date(2026, 8, 21))
        self.assertTrue(card["price_condition_met"])
        self.assertIn("已突破 105", card["watch_condition"])
        self.assertIn("观察能否守稳", card["watch_condition"])
        self.assertNotIn("收盘站上 105", card["watch_condition"])

        pending = build_trade_card(
            "TEST",
            {**assessment, "raw_trade_plan": {**assessment["raw_trade_plan"], "snapshot_price": "100"}},
            {**metric, "completed_close": 100, "price_structure": {**metric["price_structure"], "breakout_state": "inside_range"}},
            as_of=date(2026, 8, 21),
        )
        self.assertFalse(pending["price_condition_met"])
        self.assertIn("收盘站上 105", pending["watch_condition"])

    def test_event_gate_requires_post_event_range_rebuild(self):
        assessment = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {"snapshot_price": "100", "entry": "100", "stop": "95", "target": "110"},
            "final_gate": {
                "gate_action": "conditional_only",
                "priority": 3,
                "reason": "财报临近。",
            },
            "event_gate": {
                "gate_action": "conditional_only",
                "priority": 3,
                "reason": "财报临近。",
            },
        }
        metric = {
            "status": "ok",
            "completed_close": 100,
            "atr14": 2,
            "relative_volume_5d": 1.0,
            "price_structure": {
                "status": "ok",
                "trend": "range_or_transition",
                "breakout_state": "inside_range",
                "breakout_level": 105,
                "retest_level": 96,
                "support_level": 94,
                "recent_high_5d": 103,
            },
        }
        card = build_trade_card("TEST", assessment, metric, as_of=date(2026, 8, 21))
        self.assertEqual(card["setup_type"], "event_reassessment")
        self.assertIn("事件日高低点", card["watch_condition"])
        self.assertIsNone(card["trigger_price"])

    def test_quality_only_conditional_gate_is_not_labeled_as_event_wait(self):
        assessment = {
            "market": "us",
            "name": "Example",
            "v1_action": "买入",
            "raw_trade_plan": {"snapshot_price": "100", "entry": "100", "stop": "95", "target": "110"},
            "event_gate": {"gate_action": "info_only", "priority": 0},
            "final_gate": {
                "gate_action": "conditional_only",
                "priority": 3,
                "reason": "信号置信度低于 65，只允许等待条件触发。",
            },
        }
        metric = {"status": "ok", "completed_close": 100, "atr14": 2, "relative_volume_5d": 1.0}
        card = build_trade_card("TEST", assessment, metric, as_of=date(2026, 8, 21))
        self.assertEqual(card["status"], "quality_wait")
        self.assertNotEqual(card["setup_type"], "event_reassessment")
        self.assertNotIn("事件日高低点", card["watch_condition"])

    def test_symbol_normalization(self):
        self.assertEqual(normalize_hk_symbol("hk700"), "00700")
        self.assertEqual(normalize_hk_symbol("00700.HK"), "00700")
        self.assertEqual(normalize_us_symbol("brk.b"), "BRK-B")

    def test_event_classification(self):
        self.assertEqual(
            classify_hkex_event("PROFIT WARNING", "Inside Information"),
            ("earnings_warning", "high"),
        )
        self.assertEqual(
            classify_hkex_event("Next Day Disclosure Return", "Share Buyback"),
            ("share_buyback", "low"),
        )
        routine_tags = classify_hkex_events(
            "Next Day Disclosure Return - Changes in issued shares and share buybacks",
            "Share Buyback",
        )
        self.assertNotIn({"event_type": "capital_dilution", "severity": "high"}, routine_tags)
        self.assertIn({"event_type": "share_buyback", "severity": "low"}, routine_tags)
        mandate_tags = classify_hkex_events(
            "PROPOSED GRANTING OF GENERAL MANDATES TO ISSUE SHARES AND PROPOSED ADOPTION OF THE 2026 SHARE INCENTIVE PLAN",
            "General Meeting",
        )
        self.assertNotIn({"event_type": "capital_dilution", "severity": "high"}, mandate_tags)
        self.assertIn({"event_type": "share_scheme", "severity": "medium"}, mandate_tags)
        self.assertEqual(classify_sec_event("8-K", "1.03"), ("critical_corporate_event", "critical"))
        self.assertEqual(classify_sec_event("10-Q"), ("financial_results", "high"))
        self.assertEqual(classify_sec_event("4"), ("insider_transaction", "low"))
        self.assertEqual(classify_sec_event("424B7"), ("capital_dilution", "high"))
        self.assertEqual(
            classify_sec_events("SCHEDULE 13G"),
            [{"event_type": "beneficial_ownership", "severity": "medium"}],
        )
        tags = classify_hkex_events(
            "PROPOSED ISSUE OF BONDS AND A SHARES REPURCHASE PLAN",
            "Issue of Debt Securities / Repurchase of Shares",
        )
        self.assertIn({"event_type": "debt_financing", "severity": "high"}, tags)
        self.assertIn({"event_type": "share_buyback", "severity": "low"}, tags)
        self.assertEqual(
            classify_hkex_event(
                "PROPOSED ISSUE OF BONDS AND A SHARES REPURCHASE PLAN",
                "Issue of Debt Securities / Repurchase of Shares",
            ),
            ("debt_financing", "high"),
        )

    def test_earnings_scenario_uses_history_consensus_and_structure(self):
        scenario = build_earnings_scenario(
            symbol="NVDA",
            market="us",
            report_date=date(2026, 8, 21),
            earnings_outlook="近四季持续超预期，AI需求强劲。",
            events=[
                {
                    "symbol": "NVDA",
                    "event_type": "earnings_calendar",
                    "effective_at": "2026-08-26T16:05:00-04:00",
                    "effective_session": "afterhours",
                    "certainty": "provider_calendar",
                    "metadata": {
                        "eps_estimate": 2.1283,
                        "earnings_history": [
                            {"surprisePercent": 4.3},
                            {"surprisePercent": 3.6},
                            {"surprisePercent": 2.0},
                            {"surprisePercent": 2.1},
                        ],
                        "recommendation": {"strongBuy": 23, "buy": 41, "hold": 3, "sell": 1},
                    },
                }
            ],
            trade_card={
                "reference_entry": 215.66,
                "stop_loss": 213.65,
                "target_1": 224.76,
                "price_structure": {"next_resistance": 224.76, "support_level": 215.66},
            },
        )
        self.assertEqual(scenario["status"], "upcoming")
        self.assertEqual(scenario["prior"], "偏积极")
        self.assertEqual(scenario["history"]["beats"], 4)
        self.assertEqual(scenario["history"]["misses"], 0)
        self.assertEqual(scenario["trading_days_to_event"], 3)
        self.assertTrue(scenario["detailed"])
        self.assertEqual(sum(item["probability_pct"] for item in scenario["scenarios"]), 100)
        self.assertIn("2.1283", scenario["baseline"])
        self.assertIn("224.76", scenario["scenarios"][0]["decision"])
        self.assertIn("213.65", scenario["scenarios"][2]["decision"])

    def test_detailed_earnings_research_adds_previous_report_and_numeric_scenarios(self):
        scenario = build_earnings_scenario(
            symbol="NVDA",
            market="us",
            report_date=date(2026, 8, 21),
            earnings_outlook="AI需求强劲。",
            events=[
                {
                    "symbol": "NVDA",
                    "event_type": "earnings_calendar",
                    "effective_at": "2026-08-26T16:05:00-04:00",
                    "effective_session": "afterhours",
                    "metadata": {},
                }
            ],
            trade_card={"reference_entry": 215.66, "stop_loss": 213.65, "target_1": 224.76},
            research={
                "as_of": "2026-08-21",
                "period": "FY2027 Q2",
                "previous_report": {
                    "period": "FY2027 Q1",
                    "metrics": [{"label": "营业收入", "value": "816.15亿美元"}],
                },
                "surprise_history": {"sample_size": 4, "beats": 4, "misses": 0},
                "consensus": {
                    "eps": 2.09,
                    "revenue": 92070000000,
                    "metrics": [{"label": "EPS", "value": "2.09美元"}],
                },
                "scenario_probabilities": {"upside": 44, "base": 41, "downside": 15},
                "scenario_thresholds": {
                    "upside": {"condition": "收入高于930亿美元", "result": "需求确认"}
                },
            },
        )
        self.assertEqual(scenario["previous_report"]["period"], "FY2027 Q1")
        self.assertEqual(scenario["consensus_eps"], 2.09)
        self.assertEqual(scenario["scenarios"][0]["probability_pct"], 44)
        self.assertIn("930亿美元", scenario["scenarios"][0]["condition"])

    def test_official_research_date_recovers_missing_provider_calendar_event(self):
        scenario = build_earnings_scenario(
            symbol="IREN",
            market="us",
            report_date=date(2026, 8, 25),
            earnings_outlook="AI云合同交付进入验证期。",
            events=[],
            trade_card={"reference_entry": 40, "stop_loss": 37, "target_1": 45},
            research={
                "as_of": "2026-08-21",
                "event_date": "2026-08-27",
                "effective_session": "afterhours",
                "timing": "8月27日美股盘后",
                "certainty": "official_confirmed",
                "event_source": "iren_investor_relations",
                "period": "FY2026 Q4 / 全年",
            },
        )
        self.assertEqual(scenario["status"], "upcoming")
        self.assertEqual(scenario["event_date"], "2026-08-27")
        self.assertEqual(scenario["trading_days_to_event"], 2)
        self.assertTrue(scenario["detailed"])
        self.assertEqual(scenario["certainty"], "official_confirmed")
        self.assertEqual(scenario["event_source"], "iren_investor_relations")

    def test_hkex_nested_result_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_map = [{"i": 7609, "c": "00700", "n": "TENCENT", "s": 15496}]
            row = {
                "FILE_INFO": "729KB",
                "NEWS_ID": "12280990",
                "SHORT_TEXT": "Announcements and Notices - [Interim Results]<br/>",
                "STOCK_NAME": "TENCENT<br/>TENCENT-R",
                "TITLE": "ANNOUNCEMENT OF THE RESULTS FOR THE SIX MONTHS ENDED 30 JUNE 2026",
                "FILE_TYPE": "PDF",
                "DATE_TIME": "12/08/2026 16:31",
                "LONG_TEXT": "Announcements and Notices - [Interim Results]",
                "STOCK_CODE": "00700<br/>80700",
                "FILE_LINK": "/listedco/listconews/sehk/2026/0812/example.pdf",
            }
            search_payload = {
                "result": json.dumps([row]),
                "hasNextRow": False,
                "rowRange": 100,
                "loadedRecord": 1,
                "recordCnt": 1,
            }
            session = FakeSession([stock_map, search_payload])
            client = HKEXnewsClient(cache_dir=root / "cache", session=session)

            issuer_map = client.resolve_stock_ids(["hk00700"])
            filings = client.fetch_filings(
                "hk00700",
                issuer_map["00700"]["stock_id"],
                date(2026, 8, 1),
                date(2026, 8, 21),
                include_chinese=False,
            )

            self.assertEqual(len(filings), 1)
            self.assertEqual(filings[0].filing_id, "12280990")
            self.assertEqual(filings[0].symbol, "00700")
            self.assertEqual(filings[0].event_type, "financial_results")
            self.assertEqual(filings[0].severity, "high")
            self.assertEqual(filings[0].published_at, "2026-08-12T16:31:00+08:00")

    def test_chinese_placeholder_is_replaced_by_real_title_and_pdf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_map = [{"i": 9999, "c": "03750", "n": "CATL"}]
            english = {
                "NEWS_ID": "en-1",
                "STOCK_CODE": "03750",
                "STOCK_NAME": "CATL",
                "TITLE": "An announcement has just been published by the issuer in the Chinese section of this website, a corresponding version of which may or may not be published",
                "SHORT_TEXT": "Overseas Regulatory Announcement",
                "LONG_TEXT": "Overseas Regulatory Announcement",
                "FILE_INFO": "1KB",
                "FILE_TYPE": "HTM",
                "DATE_TIME": "12/08/2026 21:35",
                "FILE_LINK": "/placeholder.htm",
            }
            chinese = dict(english)
            chinese.update(
                {
                    "NEWS_ID": "zh-1",
                    "TITLE": "海外監管公告 - 關於參與投資海南時代綠色產業投資基金的公告",
                    "FILE_INFO": "247KB",
                    "FILE_TYPE": "PDF",
                    "FILE_LINK": "/real_c.pdf",
                }
            )
            payload_en = {"result": json.dumps([english]), "hasNextRow": False}
            payload_zh = {"result": json.dumps([chinese]), "hasNextRow": False}
            session = FakeSession([stock_map, payload_en, payload_zh])
            client = HKEXnewsClient(cache_dir=root / "cache", session=session)
            issuer = client.resolve_stock_ids(["03750"])["03750"]
            filing = client.fetch_filings(
                "03750", issuer["stock_id"], date(2026, 8, 12), date(2026, 8, 12)
            )[0]

            self.assertIn("海南時代綠色產業投資基金", filing.title)
            self.assertTrue(filing.source_url.endswith("/real_c.pdf"))
            self.assertEqual(filing.event_type, "strategic_investment")
            self.assertEqual(filing.metadata["chinese_news_id"], "zh-1")

    def test_board_calendar_extracts_confirmed_key_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = """
            <table>
              <tr><td>BM Date</td><td></td><td>Stock Short Name</td><td>Code</td><td>Purpose</td><td>Period</td></tr>
              <tr><td>26/08/2026</td><td></td><td>MINIMAX-W</td><td>&nbsp;&nbsp;100</td><td>INT RES</td><td>6-MTH-ENDED30/06/26</td></tr>
            </table>
            """
            response = FakeResponse(None)
            response.text = html
            session = FakeSession([])
            session.responses = [None]

            def get(url, **kwargs):
                session.calls.append((url, kwargs))
                return response

            session.get = get
            client = HKEXBoardCalendarClient(root / "cache", session=session)
            events = client.fetch_key_dates(["hk00100"])

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_date, "2026-08-26")
            self.assertEqual(events[0].certainty, "confirmed")
            self.assertEqual(events[0].severity, "high")

    def test_sec_recent_filings_are_filtered_and_urls_are_official(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ticker_map = {
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            }
            submissions = {
                "name": "Apple Inc.",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000320193-26-000020", "0000320193-26-000019"],
                        "filingDate": ["2026-08-01", "2026-08-02"],
                        "reportDate": ["2026-06-27", "2026-08-02"],
                        "acceptanceDateTime": [
                            "2026-08-01T16:30:00.000Z",
                            "2026-08-02T16:30:00.000Z",
                        ],
                        "form": ["10-Q", "S-8"],
                        "fileNumber": ["001-36743", "333-00000"],
                        "filmNumber": ["261234567", "261234568"],
                        "items": ["", ""],
                        "primaryDocument": ["aapl-20260627.htm", "registration.htm"],
                        "primaryDocDescription": ["10-Q", "S-8"],
                    }
                },
            }
            session = FakeSession([ticker_map, submissions])
            client = SECEdgarClient(
                "daily-stock-analysis-v2 tests@example.com",
                cache_dir=root / "cache",
                session=session,
                min_request_interval=0,
            )

            issuer_map = client.resolve_ciks(["AAPL"])
            filings = client.fetch_filings(
                "AAPL",
                issuer_map["AAPL"]["cik"],
                date(2026, 7, 20),
                date(2026, 8, 21),
            )

            self.assertEqual(len(filings), 1)
            self.assertEqual(filings[0].form_type, "10-Q")
            self.assertEqual(filings[0].severity, "high")
            self.assertEqual(
                filings[0].source_url,
                "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm",
            )

    def test_sqlite_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = OfficialFilingStore(root / "state" / "official.sqlite3")
            filing = OfficialFiling(
                provider="hkexnews",
                market="hk",
                symbol="00700",
                issuer_id="7609",
                filing_id="news-1",
                published_at="2026-08-12T16:31:00+08:00",
                form_type="PDF",
                category="Interim Results",
                title="Interim Results",
                source_url="https://www1.hkexnews.hk/example.pdf",
                document_type="PDF",
                event_type="financial_results",
                severity="high",
                metadata={"file_info": "729KB"},
            )

            first = store.upsert_filings([filing])
            second = store.upsert_filings([filing])

            self.assertEqual((first.inserted, first.updated), (1, 0))
            self.assertEqual((second.inserted, second.updated), (0, 1))
            rows = store.list_filings()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["metadata"]["file_info"], "729KB")

    def test_hard_gate_boundaries_and_low_value_forms(self):
        as_of = date(2026, 8, 21)
        base = {
            "event_type": "earnings_calendar",
            "severity": "high",
            "certainty": "official_confirmed",
            "metadata": {},
        }
        self.assertEqual(
            evaluate_event_gate({**base, "effective_at": "2026-08-23"}, as_of).action,
            "block_new_positions",
        )
        self.assertEqual(
            evaluate_event_gate({**base, "effective_at": "2026-08-24"}, as_of).action,
            "conditional_only",
        )
        self.assertEqual(
            evaluate_event_gate({**base, "effective_at": "2026-08-27"}, as_of).action,
            "monitor",
        )
        self.assertEqual(
            evaluate_event_gate(
                {
                    "event_type": "capital_dilution",
                    "severity": "high",
                    "published_at": "2026-08-04",
                    "metadata": {},
                },
                as_of,
            ).action,
            "conditional_only",
        )
        self.assertEqual(
            evaluate_event_gate(
                {"event_type": "insider_transaction", "severity": "low", "metadata": {}}, as_of
            ).action,
            "info_only",
        )
        self.assertEqual(
            evaluate_event_gate(
                {
                    "event_type": "retail_sales",
                    "severity": "high",
                    "effective_at": "2026-08-26",
                    "metadata": {"is_macro": True, "importance": "high", "analysis": {}},
                },
                as_of,
            ).action,
            "monitor",
        )
        self.assertEqual(
            evaluate_event_gate(
                {
                    "event_type": "financial_results",
                    "severity": "high",
                    "published_at": "2026-08-01",
                    "metadata": {},
                },
                as_of,
            ).action,
            "info_only",
        )

    def test_bls_calendar_parses_official_list_and_attaches_scenarios(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed = root / "seed.json"
            seed.write_text('{"events": []}', encoding="utf-8")
            html = """
            <table>
              <tr><th>Date</th><th>Time</th><th>Release</th></tr>
              <tr><td>Friday, September 4, 2026</td><td>08:30 AM</td><td>Employment Situation for August 2026</td></tr>
              <tr><td>Tuesday, September 8, 2026</td><td>10:00 AM</td><td>Job Openings and Labor Turnover Survey for July 2026</td></tr>
              <tr><td>Thursday, September 10, 2026</td><td>08:30 AM</td><td>Producer Price Index for August 2026</td></tr>
              <tr><td>Friday, September 11, 2026</td><td>08:30 AM</td><td>Consumer Price Index for August 2026</td></tr>
            </table>
            """
            response = FakeResponse(None)
            response.text = html
            session = FakeSession([])
            session.get = lambda *args, **kwargs: response
            client = BLSCalendarClient(root / "cache", seed, session=session)
            releases = client.fetch_releases(date(2026, 9, 1), date(2026, 9, 30))

            self.assertEqual(
                [item.event_type for item in releases],
                ["nonfarm_payrolls", "jolts", "ppi", "cpi"],
            )
            self.assertTrue(all(item.certainty == "official_confirmed" for item in releases))
            self.assertTrue(all(len(item.scenarios) == 3 for item in releases))
            self.assertEqual(releases[0].effective_at, "2026-09-04T08:30:00-04:00")

    def test_comprehensive_macro_classification_and_post_release_analysis(self):
        self.assertEqual(classify_macro_event("Retail Sales MoM").event_type, "retail_sales")
        self.assertEqual(classify_macro_event("Initial Jobless Claims").category, "labor_weakness")
        self.assertEqual(classify_macro_event("EIA Crude Oil Inventories").event_type, "oil_inventories")
        analysis = analyze_release(
            event_type="labor_slack",
            category="labor_weakness",
            actual="255K",
            estimate="240K",
            previous="238K",
        )
        self.assertEqual(analysis["status"], "released")
        self.assertEqual(analysis["comparison"], "above")
        self.assertIn("高于市场预期", analysis["summary"])
        self.assertIn("银行", analysis["negative_sectors"])
        self.assertEqual(translate_macro_title("Core PCE Price Index"), "核心 PCE 物价指数")
        self.assertEqual(translate_macro_title("House Price Index"), "FHFA 房价指数")
        self.assertGreater(
            macro_market_score("core_pce", "inflation"),
            macro_market_score("housing_construction", "housing"),
        )
        self.assertIn("美联储", simple_macro_explanation("inflation", "Core PCE Price Index"))
        self.assertEqual(scenario_probabilities()["percentages"], [30, 40, 30])
        calibrated = scenario_probabilities([8, 4, 2])
        self.assertEqual(sum(calibrated["percentages"]), 100)
        self.assertEqual(calibrated["sample_size"], 14)

    def test_nasdaq_calendar_keeps_all_us_events_and_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {
                "data": {
                    "rows": [
                        {
                            "gmt": "08:30",
                            "country": "United States",
                            "eventName": "Initial Jobless Claims",
                            "actual": "255K",
                            "consensus": "240K",
                            "previous": "238K",
                        },
                        {
                            "gmt": "10:00",
                            "country": "United States",
                            "eventName": "Existing Home Sales",
                            "actual": "4.1M",
                            "consensus": "4.0M",
                            "previous": "3.9M",
                        },
                        {
                            "gmt": "09:00",
                            "country": "Germany",
                            "eventName": "German CPI",
                            "actual": "2.0%",
                            "consensus": "2.0%",
                            "previous": "2.1%",
                        },
                        {
                            "gmt": "14:00",
                            "country": "United States",
                            "eventName": "Experimental Macro Series XYZ",
                            "actual": "1.0",
                            "consensus": "",
                            "previous": "0.9",
                        },
                    ]
                }
            }
            client = NasdaqMacroCalendarClient(
                Path(temp_dir) / "cache",
                session=FakeSession([payload]),
                max_workers=1,
            )
            releases = client.fetch_releases(date(2026, 8, 21), date(2026, 8, 21))
            self.assertEqual(len(releases), 3)
            self.assertEqual(releases[0].actual, "255K")
            self.assertEqual(releases[0].release_date, "2026-08-21")
            self.assertEqual(releases[0].release_time, "08:30 ET")
            self.assertEqual(releases[0].effective_at, "2026-08-21T12:30:00+00:00")
            self.assertEqual(releases[0].analysis["status"], "released")
            self.assertEqual(
                {item.category for item in releases}, {"labor_weakness", "housing", "other"}
            )

    def test_macro_history_calibration_groups_batches_and_falls_back(self):
        releases = []
        outcomes = ["above"] * 7 + ["inline"] * 3 + ["below"] * 2
        for index, outcome in enumerate(outcomes, start=1):
            for suffix in ("MoM", "YoY"):
                releases.append(
                    MacroRelease(
                        provider="nasdaq_macro_calendar",
                        event_type="retail_sales",
                        release_date=f"2026-07-{index:02d}",
                        release_time="08:30 GMT",
                        effective_at=f"2026-07-{index:02d}T08:30:00+00:00",
                        title=f"Retail Sales {suffix}",
                        reference_period="",
                        source_url="https://api.nasdaq.com/",
                        certainty="market_calendar",
                        retrieval_status="live_market_calendar",
                        scenarios=[],
                        category="growth",
                        importance="high",
                        actual="1",
                        estimate="1",
                        analysis={
                            "status": "released",
                            "comparison": outcome,
                            "comparison_basis": "consensus",
                        },
                    )
                )
        calibration = build_calibration(
            releases, as_of=date(2026, 8, 21), lookback_days=180
        )
        self.assertEqual(calibration["family"]["retail_sales"]["counts"], [7, 3, 2])
        self.assertEqual(calibration["family"]["retail_sales"]["sample_size"], 12)
        counts, level, sample_size = select_history(
            calibration, "retail_sales", "growth"
        )
        self.assertEqual(counts, [7, 3, 2])
        self.assertEqual(level, "historical_family")
        self.assertEqual(sample_size, 12)

        future = replace(
            releases[0],
            release_date="2026-08-25",
            effective_at="2026-08-25T08:30:00+00:00",
            analysis={"status": "scheduled"},
        )
        calibrated = apply_calibration([future], calibration)[0]
        self.assertEqual(sum(item["probability_pct"] for item in calibrated.scenarios), 100)
        self.assertEqual(calibrated.scenarios[0]["probability_sample_size"], 12)

        category_counts, category_level, _ = select_history(
            calibration, "durable_goods", "growth"
        )
        self.assertEqual(category_counts, [7, 3, 2])
        self.assertEqual(category_level, "historical_category")

    def test_quality_scores_and_ledger_preserve_hard_gates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "report.md"
            report.write_text(
                """# 🎯 2026-08-21 决策仪表盘

## 📊 分析结果摘要
🟢 **示例公司(TEST)**: 买入 | 评分 70 | 看多

## 🟢 示例公司 (TEST)
**💭 舆情情绪**: 市场情绪积极偏多
**📊 业绩预期**: 实际EPS 2.1，预期EPS 2.0，营收增长10%，PE 20
**🚨 风险警报**:
- 暂无重大风险
**✨ 利好催化**:
- 2026-08-20 业绩超预期
**📢 最新动态**: 2026-08-20 公司上调盈利指引
### 📈 当日行情
| 收盘 | 昨收 | 开盘 | 最高 | 最低 | 涨跌幅 | 涨跌额 | 振幅 | 成交量 | 成交额 |
|---|---|---|---|---|---|---|---|---|---|
| 100 | 98 | 99 | 102 | 97 | 2.04% | 2 | 5% | 100万股 | 1亿元 |
| 当前价 | 量比 | 换手率 | 行情来源 |
|---|---|---|---|
| 100 | 1.2 | 2.1% | 长桥 |
### 📊 数据透视
**均线排列**: MA5 > MA10 > MA20，多头排列 | 趋势强度: 90/100
| 当前价 | MA5 | MA10 | MA20 | 乖离率 | 支撑位 | 压力位 |
|---|---|---|---|---|---|---|
| 100 | 99 | 97 | 95 | 1.0% | 98 | 105 |
| 🎯 理想买入点 | 99 |
| 🛑 止损位 | 95 |
| 🎊 目标位 | 110 |
**💰 仓位建议**: 建议仓位：2成
- ✅ 检查项5：PE 20，估值合理
""",
                encoding="utf-8",
            )
            items = parse_production_report(report, "us")
            self.assertIn("TEST", items)
            quality = assess_decisions(
                items,
                as_of=date(2026, 8, 21),
                coverage={"TEST": "ok"},
                symbol_gates={
                    "TEST": {
                        "gate_action": "conditional_only",
                        "priority": 3,
                        "reason": "财报临近。",
                        "gate_until": "2026-08-27",
                    }
                },
                events=[],
            )
            item = quality["TEST"]
            self.assertGreaterEqual(item["data_completeness"], 80)
            self.assertEqual(item["final_gate"]["gate_action"], "conditional_only")
            self.assertIn("entry", item["suppressed_fields"])
            self.assertEqual(item["effective_trade_plan"]["entry"], "")
            self.assertTrue(item["effective_trade_plan"]["stop"])

            ledger = DecisionLedger(root / "ledger.sqlite3")
            self.assertEqual(ledger.upsert("2026-08-21", quality), 1)
            self.assertEqual(ledger.upsert("2026-08-21", quality), 1)
            self.assertEqual(ledger.count(), 1)

    def test_low_completeness_clears_entire_trade_plan(self):
        report_items = {
            "MISS": {
                "market": "us",
                "symbol": "MISS",
                "name": "Missing",
                "decision": "买入",
                "score": 80,
                "trend": "看多",
                "report_date": "2026-08-21",
                "section": "| 🎯 理想买入点 | 10 |\n| 🛑 止损位 | 9 |\n| 🎊 目标位 | 12 |",
                "report_path": "report.md",
            }
        }
        quality = assess_decisions(
            report_items,
            as_of=date(2026, 8, 21),
            coverage={},
            symbol_gates={},
            events=[],
        )["MISS"]
        self.assertLess(quality["data_completeness"], 60)
        self.assertEqual(quality["final_gate"]["gate_action"], "block_new_positions")
        self.assertEqual(quality["effective_trade_plan"]["stop"], "")
        self.assertEqual(quality["effective_trade_plan"]["target"], "")

    def test_unified_context_prefers_official_earnings_date_and_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            official = OfficialFilingStore(root / "official.sqlite3")
            official.upsert_key_dates(
                [
                    OfficialKeyDate(
                        provider="hkex_board_calendar",
                        market="hk",
                        symbol="00981",
                        event_date="2026-08-27",
                        event_type="earnings_release",
                        title="SMIC 業績董事會日期",
                        purpose="INT RES",
                        period="6-MTH-ENDED30/06/26",
                        severity="high",
                        certainty="confirmed",
                        source_url="https://www.hkex-is.hk/wwwroot/link/ebmn.htm",
                    )
                ]
            )
            macro = MacroRelease(
                provider="bls",
                event_type="nonfarm_payrolls",
                release_date="2026-09-04",
                release_time="08:30 AM",
                effective_at="2026-09-04T08:30:00-04:00",
                title="Employment Situation for August 2026",
                reference_period="August 2026",
                source_url="https://www.bls.gov/schedule/2026/09_sched_list.htm",
                certainty="official_confirmed",
                retrieval_status="verified_official_seed",
                scenarios=[],
            )
            events = EventContextBuilder(official, date(2026, 8, 21)).build(
                hk_symbols=["00981"],
                us_symbols=[],
                finnhub_snapshots={},
                macro_releases=[macro],
            )
            earnings = [item for item in events if item.event_type == "earnings_calendar"]
            self.assertEqual(len(earnings), 1)
            self.assertEqual(earnings[0].source, "hkex_board_calendar")
            self.assertEqual(earnings[0].certainty, "official_confirmed")
            self.assertEqual(earnings[0].gate_action, "monitor")
            provider_copy = replace(
                earnings[0], source="finnhub", certainty="provider_calendar"
            )
            deduped = EventContextBuilder._dedupe([provider_copy, earnings[0]])
            self.assertEqual(len(deduped), 1)
            self.assertEqual(deduped[0].source, "hkex_board_calendar")

            store = UnifiedEventStore(root / "events.sqlite3")
            self.assertEqual(store.upsert(events), len(events))
            self.assertEqual(store.upsert(events), len(events))
            self.assertEqual(len(store.list_events()), len(events))

    def test_filing_calendar_notice_is_not_treated_as_actual_earnings_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OfficialFilingStore(Path(temp_dir) / "official.sqlite3")
            store.upsert_filings(
                [
                    OfficialFiling(
                        provider="hkexnews",
                        market="hk",
                        symbol="00981",
                        issuer_id="issuer",
                        filing_id="notice-1",
                        published_at="2026-08-19T18:00:00+08:00",
                        form_type="PDF",
                        category="Board Meeting",
                        title="DATE OF BOARD MEETING",
                        source_url="https://www1.hkexnews.hk/notice.pdf",
                        document_type="PDF",
                        event_type="earnings_calendar",
                        severity="high",
                        metadata={},
                    )
                ]
            )
            events = EventContextBuilder(store, date(2026, 8, 21)).build(
                hk_symbols=["00981"], us_symbols=[]
            )
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, "earnings_calendar_notice")
            self.assertNotEqual(events[0].gate_action, "block_new_positions")

    def test_equal_priority_gates_are_combined(self):
        base = UnifiedEvent(
            event_id="one",
            market="us",
            symbol="IREN",
            source="finnhub",
            source_url="",
            published_at="2026-08-21T00:00:00+00:00",
            effective_at="2026-08-27T16:05:00-04:00",
            effective_session="afterhours",
            event_type="earnings_calendar",
            tags=[],
            severity="high",
            certainty="provider_calendar",
            headline="Earnings",
            facts=[],
            gate_action="conditional_only",
            gate_reason="财报临近",
            gate_until="2026-08-27",
            dedupe_key="one",
            status="upcoming",
            metadata={},
        )
        dilution = replace(
            base,
            event_id="two",
            source="sec_edgar",
            event_type="capital_dilution",
            headline="424B7",
            gate_reason="融资待核验",
            gate_until="2026-09-03",
            dedupe_key="two",
        )
        gate = strongest_by_symbol([base, dilution])["IREN"]
        self.assertEqual(gate["gate_until"], "2026-09-03")
        self.assertIn("财报临近", gate["reason"])
        self.assertIn("融资待核验", gate["reason"])
        self.assertEqual(len(gate["triggered_events"]), 2)

    def test_info_only_summary_does_not_expand_every_event_reason(self):
        event = UnifiedEvent(
            event_id="info",
            market="us",
            symbol="MU",
            source="sec_edgar",
            source_url="",
            published_at="2026-08-01T00:00:00+00:00",
            effective_at="2026-08-01T00:00:00+00:00",
            effective_session="afterhours",
            event_type="insider_transaction",
            tags=[],
            severity="low",
            certainty="official_confirmed",
            headline="Form 4",
            facts=[],
            gate_action="info_only",
            gate_reason="内部人交易仅作辅助。",
            gate_until="",
            dedupe_key="info",
            status="active",
            metadata={},
        )
        gate = strongest_by_symbol([event])["MU"]
        self.assertEqual(gate["triggered_events"], [])
        self.assertIn("1 项官方事件", gate["reason"])


if __name__ == "__main__":
    unittest.main()
