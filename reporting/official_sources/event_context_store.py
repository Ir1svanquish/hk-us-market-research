"""SQLite persistence for the unified report event context."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .models import UnifiedEvent


class UnifiedEventStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS unified_events (
                    event_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    effective_session TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    certainty TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    gate_action TEXT NOT NULL,
                    gate_reason TEXT NOT NULL,
                    gate_until TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_unified_events_symbol_effective
                ON unified_events (market, symbol, effective_at);
                CREATE INDEX IF NOT EXISTS idx_unified_events_gate
                ON unified_events (gate_action, effective_at);
                """
            )

    def upsert(self, events: Iterable[UnifiedEvent]) -> int:
        items = list(events)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self._connect() as connection:
            for item in items:
                connection.execute(
                    """
                    INSERT INTO unified_events (
                        event_id, market, symbol, source, source_url, published_at,
                        effective_at, effective_session, event_type, tags_json,
                        severity, certainty, headline, facts_json, gate_action,
                        gate_reason, gate_until, dedupe_key, status, metadata_json,
                        generated_at, updated_at
                    ) VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    ) ON CONFLICT(event_id) DO UPDATE SET
                        market=excluded.market, symbol=excluded.symbol, source=excluded.source,
                        source_url=excluded.source_url, published_at=excluded.published_at,
                        effective_at=excluded.effective_at, effective_session=excluded.effective_session,
                        event_type=excluded.event_type, tags_json=excluded.tags_json,
                        severity=excluded.severity, certainty=excluded.certainty,
                        headline=excluded.headline, facts_json=excluded.facts_json,
                        gate_action=excluded.gate_action, gate_reason=excluded.gate_reason,
                        gate_until=excluded.gate_until, dedupe_key=excluded.dedupe_key,
                        status=excluded.status, metadata_json=excluded.metadata_json,
                        generated_at=excluded.generated_at, updated_at=excluded.updated_at
                    """,
                    (
                        item.event_id, item.market, item.symbol, item.source, item.source_url,
                        item.published_at, item.effective_at, item.effective_session, item.event_type,
                        json.dumps(item.tags, ensure_ascii=False), item.severity, item.certainty,
                        item.headline, json.dumps(item.facts, ensure_ascii=False), item.gate_action,
                        item.gate_reason, item.gate_until, item.dedupe_key, item.status,
                        json.dumps(item.metadata, ensure_ascii=False), item.generated_at, now,
                    ),
                )
        return len(items)

    def list_events(self, symbols: Sequence[str] = (), status: Optional[str] = None) -> List[dict]:
        where = []
        params: List[object] = []
        if symbols:
            where.append("symbol IN (%s)" % ",".join("?" for _ in symbols))
            params.extend(symbols)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM unified_events{clause} ORDER BY effective_at, symbol", params
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json"))
            item["facts"] = json.loads(item.pop("facts_json"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result
