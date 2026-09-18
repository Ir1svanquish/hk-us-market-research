"""SQLite persistence isolated from the current production database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .models import OfficialFiling, OfficialKeyDate


@dataclass(frozen=True)
class UpsertStats:
    inserted: int = 0
    updated: int = 0


class OfficialFilingStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS official_filings (
                    provider TEXT NOT NULL,
                    filing_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    issuer_id TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    form_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, filing_id)
                );

                CREATE INDEX IF NOT EXISTS idx_official_filings_symbol_time
                ON official_filings (market, symbol, published_at DESC);

                CREATE INDEX IF NOT EXISTS idx_official_filings_severity
                ON official_filings (severity, published_at DESC);

                CREATE TABLE IF NOT EXISTS official_sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fetched_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS official_key_dates (
                    provider TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    period TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    certainty TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, symbol, event_date, event_type)
                );

                CREATE INDEX IF NOT EXISTS idx_official_key_dates_symbol_date
                ON official_key_dates (market, symbol, event_date);
                """
            )

    def upsert_filings(self, filings: Iterable[OfficialFiling]) -> UpsertStats:
        items = list(filings)
        if not items:
            return UpsertStats()
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        inserted = 0
        updated = 0
        with self._connect() as connection:
            existing = {
                (row["provider"], row["filing_id"])
                for row in connection.execute(
                    "SELECT provider, filing_id FROM official_filings"
                ).fetchall()
            }
            for filing in items:
                key = (filing.provider, filing.filing_id)
                if key in existing:
                    updated += 1
                else:
                    inserted += 1
                    existing.add(key)
                connection.execute(
                    """
                    INSERT INTO official_filings (
                        provider, filing_id, market, symbol, issuer_id, published_at,
                        form_type, category, title, source_url, document_type,
                        event_type, severity, metadata_json, fetched_at,
                        first_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, filing_id) DO UPDATE SET
                        market=excluded.market,
                        symbol=excluded.symbol,
                        issuer_id=excluded.issuer_id,
                        published_at=excluded.published_at,
                        form_type=excluded.form_type,
                        category=excluded.category,
                        title=excluded.title,
                        source_url=excluded.source_url,
                        document_type=excluded.document_type,
                        event_type=excluded.event_type,
                        severity=excluded.severity,
                        metadata_json=excluded.metadata_json,
                        fetched_at=excluded.fetched_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        filing.provider,
                        filing.filing_id,
                        filing.market,
                        filing.symbol,
                        filing.issuer_id,
                        filing.published_at,
                        filing.form_type,
                        filing.category,
                        filing.title,
                        filing.source_url,
                        filing.document_type,
                        filing.event_type,
                        filing.severity,
                        json.dumps(filing.metadata, ensure_ascii=False, sort_keys=True),
                        filing.fetched_at,
                        now,
                        now,
                    ),
                )
        return UpsertStats(inserted=inserted, updated=updated)

    def upsert_key_dates(self, key_dates: Iterable[OfficialKeyDate]) -> UpsertStats:
        items = list(key_dates)
        if not items:
            return UpsertStats()
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        inserted = 0
        updated = 0
        with self._connect() as connection:
            existing = {
                (row["provider"], row["symbol"], row["event_date"], row["event_type"])
                for row in connection.execute(
                    "SELECT provider, symbol, event_date, event_type FROM official_key_dates"
                ).fetchall()
            }
            for item in items:
                key = (item.provider, item.symbol, item.event_date, item.event_type)
                if key in existing:
                    updated += 1
                else:
                    inserted += 1
                    existing.add(key)
                connection.execute(
                    """
                    INSERT INTO official_key_dates (
                        provider, market, symbol, event_date, event_type, title,
                        purpose, period, severity, certainty, source_url,
                        fetched_at, first_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, symbol, event_date, event_type) DO UPDATE SET
                        market=excluded.market,
                        title=excluded.title,
                        purpose=excluded.purpose,
                        period=excluded.period,
                        severity=excluded.severity,
                        certainty=excluded.certainty,
                        source_url=excluded.source_url,
                        fetched_at=excluded.fetched_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        item.provider,
                        item.market,
                        item.symbol,
                        item.event_date,
                        item.event_type,
                        item.title,
                        item.purpose,
                        item.period,
                        item.severity,
                        item.certainty,
                        item.source_url,
                        item.fetched_at,
                        now,
                        now,
                    ),
                )
        return UpsertStats(inserted=inserted, updated=updated)

    def record_sync_run(
        self,
        *,
        provider: str,
        market: str,
        symbol: str,
        started_at: str,
        status: str,
        fetched_count: int,
        error_message: str = "",
    ) -> None:
        completed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO official_sync_runs (
                    provider, market, symbol, started_at, completed_at,
                    status, fetched_count, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    market,
                    symbol,
                    started_at,
                    completed_at,
                    status,
                    int(fetched_count),
                    error_message[:1000],
                ),
            )

    def list_filings(self, limit: int = 100) -> List[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT provider, filing_id, market, symbol, issuer_id, published_at,
                       form_type, category, title, source_url, document_type,
                       event_type, severity, metadata_json, fetched_at
                FROM official_filings
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def get_filings(
        self,
        symbols: Sequence[str],
        *,
        published_since: Optional[str] = None,
        limit: int = 1000,
    ) -> List[dict]:
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        params: List[object] = list(symbols)
        where = f"symbol IN ({placeholders})"
        if published_since:
            where += " AND published_at >= ?"
            params.append(published_since)
        params.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT provider, filing_id, market, symbol, issuer_id, published_at,
                       form_type, category, title, source_url, document_type,
                       event_type, severity, metadata_json, fetched_at
                FROM official_filings
                WHERE {where}
                ORDER BY published_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def list_key_dates(
        self,
        symbols: Sequence[str],
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[dict]:
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        params: List[object] = list(symbols)
        where = f"symbol IN ({placeholders})"
        if start_date:
            where += " AND event_date >= ?"
            params.append(start_date)
        if end_date:
            where += " AND event_date <= ?"
            params.append(end_date)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT provider, market, symbol, event_date, event_type, title,
                       purpose, period, severity, certainty, source_url, fetched_at
                FROM official_key_dates
                WHERE {where}
                ORDER BY event_date, symbol
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]
