from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from v2.official_sources.trigger_lifecycle import TriggerLifecycleLedger


class TriggerLifecycleTest(unittest.TestCase):
    def _assessment(self, confidence: int = 80, priority: int = 0) -> dict:
        return {
            "market": "us",
            "v1_action": "买入",
            "signal_confidence": confidence,
            "final_gate": {"priority": priority, "gate_action": "info_only"},
        }

    def _card(
        self,
        *,
        setup: str = "breakout",
        status: str = "ready",
        price_met: bool = True,
        volume: str = "expanded",
        entry: float = 100,
        stop: float = 95,
    ) -> dict:
        return {
            "market": "us",
            "status": status,
            "label": "条件买入" if status == "ready" else "等待量能确认",
            "active": status == "ready",
            "setup_type": setup,
            "reference_entry": entry,
            "trigger_price": entry if status == "ready" else None,
            "stop_loss": stop,
            "target_1": 110,
            "risk_reward": 2.0,
            "atr14": 2.0,
            "price_condition_met": price_met,
            "volume_confirmation": {"status": volume},
            "position_plan": "2成",
            "valid_sessions": 2,
        }

    def test_weak_breakout_requires_one_later_completed_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = TriggerLifecycleLedger(Path(temp_dir) / "ledger.sqlite3")
            card = self._card(status="trigger_wait", volume="contracted")
            day1 = ledger.apply(
                date(2026, 9, 10),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 101}},
            )["TEST"]
            self.assertEqual(day1["status"], "trigger_wait")
            self.assertEqual(day1["label"], "等待跨日守稳")
            self.assertEqual(day1["trigger_lifecycle"]["state"], "pending_confirmation")
            self.assertIn("等待下一交易日", day1["lifecycle_message"])
            self.assertFalse(day1["active"])

            day2 = ledger.apply(
                date(2026, 9, 11),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 102}},
            )["TEST"]
            self.assertEqual(day2["status"], "ready_cautious")
            self.assertEqual(day2["trigger_lifecycle"]["state"], "confirmed")
            self.assertEqual(day2["trigger_lifecycle"]["confirmation_date"], "2026-09-11")
            self.assertIn("跨交易日守稳确认已完成", day2["lifecycle_message"])
            self.assertTrue(day2["active"])

            rerun = ledger.apply(
                date(2026, 9, 11),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 102}},
            )["TEST"]
            self.assertEqual(rerun["trigger_lifecycle"]["observed_sessions"], 1)
            rows = ledger.connection.execute(
                "SELECT COUNT(*) FROM trigger_lifecycle_snapshots"
            ).fetchone()[0]
            self.assertEqual(rows, 2)

            expired = ledger.apply(
                date(2026, 9, 15),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 103}},
            )["TEST"]
            self.assertEqual(expired["trigger_lifecycle"]["state"], "expired")
            self.assertFalse(expired["active"])
            ledger.close()

    def test_contracted_pullback_starts_cautious_then_upgrades_after_hold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = TriggerLifecycleLedger(Path(temp_dir) / "ledger.sqlite3")
            first = ledger.apply(
                date(2026, 9, 10),
                {"TEST": self._assessment(confidence=75)},
                {"TEST": self._card(setup="pullback", volume="contracted")},
                {"TEST": {"completed_close": 101}},
            )["TEST"]
            self.assertEqual(first["status"], "ready_cautious")
            self.assertEqual(first["trigger_lifecycle"]["state"], "first_trigger")

            held_card = self._card(
                setup="pullback",
                status="trigger_wait",
                price_met=False,
                volume="contracted",
            )
            second = ledger.apply(
                date(2026, 9, 11),
                {"TEST": self._assessment(confidence=75)},
                {"TEST": held_card},
                {"TEST": {"completed_close": 101.5}},
            )["TEST"]
            self.assertEqual(second["status"], "ready")
            self.assertEqual(second["label"], "标准可执行")
            self.assertEqual(second["trigger_lifecycle"]["state"], "confirmed")
            ledger.close()

    def test_moderate_confidence_remains_cautious(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = TriggerLifecycleLedger(Path(temp_dir) / "ledger.sqlite3")
            card = ledger.apply(
                date(2026, 9, 10),
                {"TEST": self._assessment(confidence=65)},
                {"TEST": self._card()},
                {"TEST": {"completed_close": 101}},
            )["TEST"]
            self.assertEqual(card["status"], "ready_cautious")
            self.assertIn("置信度", card["trigger_lifecycle"]["reasons"][0])
            ledger.close()

    def test_failed_hold_resets_and_can_trigger_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = TriggerLifecycleLedger(Path(temp_dir) / "ledger.sqlite3")
            card = self._card(status="trigger_wait", volume="contracted")
            ledger.apply(
                date(2026, 9, 10),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 101}},
            )
            reset = ledger.apply(
                date(2026, 9, 11),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 99}},
            )["TEST"]
            self.assertEqual(reset["trigger_lifecycle"]["state"], "waiting_trigger")
            self.assertEqual(reset["trigger_lifecycle"]["first_trigger_date"], "")

            retriggered = ledger.apply(
                date(2026, 9, 14),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 101}},
            )["TEST"]
            self.assertEqual(retriggered["trigger_lifecycle"]["state"], "pending_confirmation")
            self.assertEqual(retriggered["trigger_lifecycle"]["first_trigger_date"], "2026-09-14")
            ledger.close()

    def test_follow_through_breaks_on_structural_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = TriggerLifecycleLedger(Path(temp_dir) / "ledger.sqlite3")
            card = self._card(status="trigger_wait", volume="contracted")
            ledger.apply(
                date(2026, 9, 10),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 101}},
            )
            broken = ledger.apply(
                date(2026, 9, 11),
                {"TEST": self._assessment()},
                {"TEST": card},
                {"TEST": {"completed_close": 94}},
            )["TEST"]
            self.assertEqual(broken["status"], "invalid_structure")
            self.assertEqual(broken["trigger_lifecycle"]["state"], "invalidated")
            self.assertFalse(broken["active"])
            ledger.close()

    def test_strong_confirmation_is_standard_on_first_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = TriggerLifecycleLedger(Path(temp_dir) / "ledger.sqlite3")
            card = ledger.apply(
                date(2026, 9, 10),
                {"TEST": self._assessment(confidence=80)},
                {"TEST": self._card(volume="neutral")},
                {"TEST": {"completed_close": 101}},
            )["TEST"]
            self.assertEqual(card["status"], "ready")
            self.assertEqual(card["execution_tier"], "standard")
            self.assertEqual(card["trigger_lifecycle"]["state"], "confirmed")
            ledger.close()


if __name__ == "__main__":
    unittest.main()
