"""Persistent cross-session confirmation for report trade cards.

The ledger stores one deterministic snapshot per market/symbol/report date. Same-day
reruns rebuild from the prior report date, so they cannot accidentally advance a
confirmation state twice.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping

from .trading_cards import expiry_after_sessions


LIFECYCLE_MODEL_VERSION = "trigger-lifecycle-v1"
STANDARD_CONFIDENCE = 70
FOLLOW_THROUGH_VOLUME_STATES = {"light", "contracted"}
TRACKED_STATES = {"pending_confirmation", "first_trigger", "confirmed"}


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class TriggerLifecycleLedger:
    """Persist and apply cross-session trigger confirmation states."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trigger_lifecycle_snapshots (
                record_id TEXT PRIMARY KEY,
                as_of TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                model_version TEXT NOT NULL,
                setup_type TEXT,
                reference_entry REAL,
                stop_loss REAL,
                atr14 REAL,
                state TEXT NOT NULL,
                execution_tier TEXT NOT NULL,
                observed_sessions INTEGER NOT NULL DEFAULT 0,
                first_trigger_date TEXT,
                confirmation_date TEXT,
                expires_at TEXT,
                reason_json TEXT NOT NULL,
                status_before TEXT,
                status_after TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(as_of, market, symbol, model_version)
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trigger_lifecycle_previous
            ON trigger_lifecycle_snapshots(market, symbol, model_version, as_of)
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _previous(self, as_of: str, market: str, symbol: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM trigger_lifecycle_snapshots
            WHERE market=? AND symbol=? AND model_version=? AND as_of<?
            ORDER BY as_of DESC LIMIT 1
            """,
            (market, symbol, LIFECYCLE_MODEL_VERSION, as_of),
        ).fetchone()

    @staticmethod
    def _same_setup(previous: sqlite3.Row, card: Mapping[str, object]) -> bool:
        setup_type = str(card.get("setup_type") or "")
        if str(previous["setup_type"] or "") != setup_type:
            return False
        old_entry = _number(previous["reference_entry"])
        new_entry = _number(card.get("reference_entry"))
        if old_entry is None or new_entry is None:
            return old_entry == new_entry
        atr = _number(card.get("atr14")) or _number(previous["atr14"]) or 0.0
        tolerance = max(0.5 * atr, 0.005 * max(abs(old_entry), abs(new_entry)))
        return abs(old_entry - new_entry) <= tolerance

    @staticmethod
    def _soft_eligible(card: Mapping[str, object], assessment: Mapping[str, object]) -> bool:
        gate = assessment.get("final_gate") if isinstance(assessment.get("final_gate"), Mapping) else {}
        return (
            str(assessment.get("v1_action") or "") == "买入"
            and int(gate.get("priority") or 0) < 3
            and str(card.get("status") or "") not in {
                "blocked",
                "data_conflict",
                "invalid_structure",
                "invalid_risk_reward",
                "event_wait",
                "quality_wait",
                "reassess",
            }
            and _number(card.get("reference_entry")) is not None
            and _number(card.get("stop_loss")) is not None
            and _number(card.get("risk_reward")) is not None
        )

    @staticmethod
    def _candidate(card: Mapping[str, object], assessment: Mapping[str, object]) -> bool:
        return TriggerLifecycleLedger._soft_eligible(card, assessment) and bool(
            card.get("price_condition_met")
        )

    @staticmethod
    def _caution_reasons(
        card: Mapping[str, object],
        assessment: Mapping[str, object],
        *,
        first_session: bool,
    ) -> list[str]:
        reasons = []
        confidence = int(assessment.get("signal_confidence") or 0)
        if confidence < STANDARD_CONFIDENCE:
            reasons.append(f"信号置信度 {confidence}，处于 60–69 的谨慎区间")
        volume = card.get("volume_confirmation") if isinstance(card.get("volume_confirmation"), Mapping) else {}
        volume_state = str(volume.get("status") or "missing")
        setup_type = str(card.get("setup_type") or "")
        if volume_state == "missing":
            reasons.append("量能数据缺失，需后续复核")
        elif setup_type in {"breakout", "reversal"} and volume_state in FOLLOW_THROUGH_VOLUME_STATES:
            reasons.append("突破/反转量能偏弱，仅在跨日守稳后谨慎确认")
        elif setup_type == "pullback" and volume_state in FOLLOW_THROUGH_VOLUME_STATES and first_session:
            reasons.append("缩量回踩为首个确认日，需观察后续守稳")
        gate = assessment.get("final_gate") if isinstance(assessment.get("final_gate"), Mapping) else {}
        if 0 < int(gate.get("priority") or 0) < 3:
            reasons.append("仍有观察级事件或重估提示")
        return reasons

    @staticmethod
    def _hold_confirmed(previous: sqlite3.Row, metric: Mapping[str, object]) -> bool:
        close = _number(metric.get("completed_close"))
        entry = _number(previous["reference_entry"])
        stop = _number(previous["stop_loss"])
        return close is not None and entry is not None and stop is not None and close >= entry and close > stop

    @staticmethod
    def _stop_broken(previous: sqlite3.Row, metric: Mapping[str, object]) -> bool:
        close = _number(metric.get("completed_close"))
        stop = _number(previous["stop_loss"])
        return close is not None and stop is not None and close <= stop

    @staticmethod
    def _apply_tier(
        card: dict,
        *,
        state: str,
        tier: str,
        reasons: list[str],
        first_trigger_date: str,
        confirmation_date: str,
        observed_sessions: int,
        expires_at: str,
        previous_state: str,
    ) -> dict:
        card["base_status"] = str(card.get("status") or "")
        card["execution_tier"] = tier
        if state == "pending_confirmation":
            lifecycle_message = "价格已首次触发，等待下一交易日收盘守稳"
        elif state == "first_trigger":
            lifecycle_message = "首个确认日，当前谨慎可执行；下一交易日守稳后可升级"
        elif state == "confirmed" and previous_state in {"pending_confirmation", "first_trigger", "confirmed"}:
            lifecycle_message = "跨交易日守稳确认已完成；继续遵守有效窗口与风险线"
        elif state == "confirmed":
            lifecycle_message = "价格与主要确认项已同步满足；继续遵守有效窗口与风险线"
        elif state == "invalidated":
            lifecycle_message = "结构风险线已失守，等待新的独立结构重建"
        elif state == "expired":
            lifecycle_message = "原触发窗口已过期，等待重新触发"
        else:
            lifecycle_message = "等待价格条件首次触发"
        card["lifecycle_message"] = lifecycle_message
        card["trigger_lifecycle"] = {
            "version": LIFECYCLE_MODEL_VERSION,
            "state": state,
            "previous_state": previous_state,
            "first_trigger_date": first_trigger_date,
            "confirmation_date": confirmation_date,
            "observed_sessions": observed_sessions,
            "required_follow_through_sessions": 1 if state in {"pending_confirmation", "first_trigger"} else 0,
            "expires_at": expires_at,
            "reasons": reasons,
        }
        if tier == "standard":
            card["status"] = "ready"
            card["label"] = "标准可执行"
            card["active"] = True
            card["trigger_price"] = card.get("reference_entry")
            card["expires_at"] = expires_at
        elif tier == "cautious":
            card["status"] = "ready_cautious"
            card["label"] = "谨慎可执行"
            card["active"] = True
            card["trigger_price"] = card.get("reference_entry")
            card["expires_at"] = expires_at
        else:
            card["active"] = False
            card["trigger_price"] = None
            card["position_plan"] = ""
            if state == "pending_confirmation":
                card["status"] = "trigger_wait"
                card["label"] = "等待跨日守稳"
                card["expires_at"] = expires_at
            elif state == "expired":
                card["status"] = "trigger_wait"
                card["label"] = "触发窗口已过期"
                card["expires_at"] = ""
            elif state == "invalidated":
                card["status"] = "invalid_structure"
                card["label"] = "结构失效，等待重建"
                card["expires_at"] = ""
        return card

    def apply(
        self,
        as_of: date,
        assessments: Mapping[str, Mapping[str, object]],
        cards: Mapping[str, Mapping[str, object]],
        market_metrics: Mapping[str, Mapping[str, object]],
    ) -> dict[str, dict]:
        as_of_text = as_of.isoformat()
        output: dict[str, dict] = {}
        now = _now()
        for symbol, source_card in cards.items():
            card = deepcopy(dict(source_card))
            assessment = assessments.get(symbol, {})
            metric = market_metrics.get(symbol, {})
            market = str(assessment.get("market") or card.get("market") or "")
            previous = self._previous(as_of_text, market, symbol)
            same_setup = previous is not None and self._same_setup(previous, card)
            previous_state = str(previous["state"] or "") if same_setup else ""
            state = "waiting_trigger"
            tier = "wait"
            reasons: list[str] = []
            first_trigger_date = ""
            confirmation_date = ""
            observed_sessions = 0
            expires_at = ""

            if same_setup and previous_state in TRACKED_STATES and self._soft_eligible(card, assessment):
                first_trigger_date = str(previous["first_trigger_date"] or "")
                confirmation_date = str(previous["confirmation_date"] or "")
                observed_sessions = int(previous["observed_sessions"] or 0) + 1
                expires_at = str(previous["expires_at"] or "")
                if self._stop_broken(previous, metric):
                    state = "invalidated"
                    reasons = ["完整交易日收盘跌破结构失效线"]
                elif observed_sessions > 2 or (expires_at and as_of_text > expires_at):
                    state = "expired"
                    reasons = ["触发计划已超过两个后续交易日的有效窗口"]
                elif self._hold_confirmed(previous, metric):
                    state = "confirmed"
                    confirmation_date = confirmation_date or as_of_text
                    reasons = self._caution_reasons(card, assessment, first_session=False)
                    tier = "cautious" if reasons else "standard"
                else:
                    state = "waiting_trigger"
                    reasons = ["跨日收盘未能继续守住原触发位，等待重新触发"]
                    first_trigger_date = ""
                    confirmation_date = ""
                    observed_sessions = 0
                    expires_at = ""
            elif self._candidate(card, assessment):
                first_trigger_date = as_of_text
                expires_at = expiry_after_sessions(as_of, 2)
                setup_type = str(card.get("setup_type") or "")
                volume = card.get("volume_confirmation") if isinstance(card.get("volume_confirmation"), Mapping) else {}
                volume_state = str(volume.get("status") or "missing")
                requires_follow_through = (
                    setup_type in {"breakout", "reversal"}
                    and volume_state in FOLLOW_THROUGH_VOLUME_STATES
                )
                if requires_follow_through:
                    state = "pending_confirmation"
                    tier = "wait"
                    reasons = ["价格首次触发但量能偏弱，等待下一交易日收盘守稳"]
                else:
                    reasons = self._caution_reasons(card, assessment, first_session=True)
                    if reasons:
                        state = "first_trigger"
                        tier = "cautious"
                    else:
                        state = "confirmed"
                        tier = "standard"
                        confirmation_date = as_of_text
            elif str(card.get("status") or "") in {"blocked", "data_conflict", "invalid_structure"}:
                state = "invalidated"
                reasons = [str(card.get("label") or "交易结构或数据已失效")]
            else:
                reasons = [str(card.get("label") or "等待价格条件首次触发")]

            card = self._apply_tier(
                card,
                state=state,
                tier=tier,
                reasons=reasons,
                first_trigger_date=first_trigger_date,
                confirmation_date=confirmation_date,
                observed_sessions=observed_sessions,
                expires_at=expires_at,
                previous_state=previous_state,
            )
            output[symbol] = card

            record_id = hashlib.sha256(
                f"{as_of_text}:{market}:{symbol}:{LIFECYCLE_MODEL_VERSION}".encode("utf-8")
            ).hexdigest()[:24]
            self.connection.execute(
                """
                INSERT INTO trigger_lifecycle_snapshots (
                    record_id, as_of, market, symbol, model_version, setup_type,
                    reference_entry, stop_loss, atr14, state, execution_tier,
                    observed_sessions, first_trigger_date, confirmation_date,
                    expires_at, reason_json, status_before, status_after,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(as_of, market, symbol, model_version) DO UPDATE SET
                    setup_type=excluded.setup_type,
                    reference_entry=excluded.reference_entry,
                    stop_loss=excluded.stop_loss,
                    atr14=excluded.atr14,
                    state=excluded.state,
                    execution_tier=excluded.execution_tier,
                    observed_sessions=excluded.observed_sessions,
                    first_trigger_date=excluded.first_trigger_date,
                    confirmation_date=excluded.confirmation_date,
                    expires_at=excluded.expires_at,
                    reason_json=excluded.reason_json,
                    status_before=excluded.status_before,
                    status_after=excluded.status_after,
                    updated_at=excluded.updated_at
                """,
                (
                    record_id,
                    as_of_text,
                    market,
                    symbol,
                    LIFECYCLE_MODEL_VERSION,
                    card.get("setup_type"),
                    card.get("reference_entry"),
                    card.get("stop_loss"),
                    card.get("atr14"),
                    state,
                    tier,
                    observed_sessions,
                    first_trigger_date,
                    confirmation_date,
                    expires_at,
                    json.dumps(reasons, ensure_ascii=False),
                    card.get("base_status"),
                    card.get("status"),
                    now,
                    now,
                ),
            )
        self.connection.commit()
        return output
