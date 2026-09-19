"""Minimal accuracy-ledger schema for report decision snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


MODEL_VERSION = "reporting-stateful-tiered-confirmation-20260910"


class DecisionLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_snapshots (
                record_id TEXT PRIMARY KEY,
                as_of TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                model_version TEXT NOT NULL,
                v1_action TEXT,
                final_action TEXT,
                gate_action TEXT,
                gate_until TEXT,
                snapshot_price TEXT,
                trigger_price TEXT,
                stop_loss TEXT,
                target_price TEXT,
                position_plan TEXT,
                data_completeness INTEGER,
                signal_confidence INTEGER,
                event_tags_json TEXT,
                gaps_json TEXT,
                suppressed_fields_json TEXT,
                card_status TEXT,
                atr14 REAL,
                risk_reward REAL,
                expires_at TEXT,
                volume_ratio REAL,
                relative_volume_5d REAL,
                setup_type TEXT,
                trigger_basis TEXT,
                watch_condition TEXT,
                invalidation_condition TEXT,
                execution_tier TEXT,
                trigger_lifecycle_state TEXT,
                first_trigger_date TEXT,
                confirmation_date TEXT,
                return_1d REAL,
                return_5d REAL,
                return_20d REAL,
                max_favorable_excursion REAL,
                max_adverse_excursion REAL,
                stop_hit INTEGER,
                target_hit INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(as_of, market, symbol, model_version)
            )
            """
        )
        self._ensure_columns()
        self.connection.commit()

    def _ensure_columns(self) -> None:
        existing = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(decision_snapshots)")
        }
        additions = {
            "card_status": "TEXT",
            "atr14": "REAL",
            "risk_reward": "REAL",
            "expires_at": "TEXT",
            "volume_ratio": "REAL",
            "relative_volume_5d": "REAL",
            "setup_type": "TEXT",
            "trigger_basis": "TEXT",
            "watch_condition": "TEXT",
            "invalidation_condition": "TEXT",
            "execution_tier": "TEXT",
            "trigger_lifecycle_state": "TEXT",
            "first_trigger_date": "TEXT",
            "confirmation_date": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in existing:
                self.connection.execute(
                    f"ALTER TABLE decision_snapshots ADD COLUMN {name} {column_type}"
                )

    def upsert(self, as_of: str, assessments: Mapping[str, Mapping[str, object]]) -> int:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = 0
        for symbol, item in assessments.items():
            market = str(item.get("market") or "")
            record_id = hashlib.sha256(
                f"{as_of}:{market}:{symbol}:{MODEL_VERSION}".encode("utf-8")
            ).hexdigest()[:24]
            gate = item.get("final_gate") if isinstance(item.get("final_gate"), dict) else {}
            plan = item.get("effective_trade_plan") if isinstance(item.get("effective_trade_plan"), dict) else {}
            card = item.get("standard_trade_card") if isinstance(item.get("standard_trade_card"), dict) else {}
            self.connection.execute(
                """
                INSERT INTO decision_snapshots (
                    record_id, as_of, market, symbol, model_version, v1_action,
                    final_action, gate_action, gate_until, snapshot_price,
                    trigger_price, stop_loss, target_price, position_plan,
                    data_completeness, signal_confidence, event_tags_json,
                    gaps_json, suppressed_fields_json, card_status, atr14,
                    risk_reward, expires_at, volume_ratio, relative_volume_5d,
                    setup_type, trigger_basis, watch_condition,
                    invalidation_condition, execution_tier,
                    trigger_lifecycle_state, first_trigger_date,
                    confirmation_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) 
                ON CONFLICT(as_of, market, symbol, model_version) DO UPDATE SET
                    v1_action=excluded.v1_action,
                    final_action=excluded.final_action,
                    gate_action=excluded.gate_action,
                    gate_until=excluded.gate_until,
                    snapshot_price=excluded.snapshot_price,
                    trigger_price=excluded.trigger_price,
                    stop_loss=excluded.stop_loss,
                    target_price=excluded.target_price,
                    position_plan=excluded.position_plan,
                    data_completeness=excluded.data_completeness,
                    signal_confidence=excluded.signal_confidence,
                    event_tags_json=excluded.event_tags_json,
                    gaps_json=excluded.gaps_json,
                    suppressed_fields_json=excluded.suppressed_fields_json,
                    card_status=excluded.card_status,
                    atr14=excluded.atr14,
                    risk_reward=excluded.risk_reward,
                    expires_at=excluded.expires_at,
                    volume_ratio=excluded.volume_ratio,
                    relative_volume_5d=excluded.relative_volume_5d,
                    setup_type=excluded.setup_type,
                    trigger_basis=excluded.trigger_basis,
                    watch_condition=excluded.watch_condition,
                    invalidation_condition=excluded.invalidation_condition,
                    execution_tier=excluded.execution_tier,
                    trigger_lifecycle_state=excluded.trigger_lifecycle_state,
                    first_trigger_date=excluded.first_trigger_date,
                    confirmation_date=excluded.confirmation_date,
                    created_at=excluded.created_at
                """,
                (
                    record_id,
                    as_of,
                    market,
                    symbol,
                    MODEL_VERSION,
                    item.get("v1_action"),
                    item.get("final_action"),
                    gate.get("gate_action"),
                    gate.get("gate_until"),
                    plan.get("snapshot_price"),
                    card.get("trigger_price") if card else plan.get("entry"),
                    card.get("stop_loss") if card else plan.get("stop"),
                    card.get("target_1") if card else plan.get("target"),
                    card.get("position_plan") if card else plan.get("position"),
                    item.get("data_completeness"),
                    item.get("signal_confidence"),
                    json.dumps(item.get("event_tags") or [], ensure_ascii=False),
                    json.dumps(item.get("major_gaps") or [], ensure_ascii=False),
                    json.dumps(item.get("suppressed_fields") or [], ensure_ascii=False),
                    card.get("status"),
                    card.get("atr14"),
                    card.get("risk_reward"),
                    card.get("expires_at"),
                    card.get("volume_ratio_live"),
                    card.get("relative_volume_5d"),
                    card.get("setup_type"),
                    card.get("trigger_basis"),
                    card.get("watch_condition"),
                    card.get("invalidation_condition"),
                    card.get("execution_tier"),
                    (card.get("trigger_lifecycle") or {}).get("state"),
                    (card.get("trigger_lifecycle") or {}).get("first_trigger_date"),
                    (card.get("trigger_lifecycle") or {}).get("confirmation_date"),
                    now,
                ),
            )
            rows += 1
        self.connection.commit()
        return rows

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0])
