"""Shared report contracts for official exchange and regulator filings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass(frozen=True)
class OfficialFiling:
    provider: str
    market: str
    symbol: str
    issuer_id: str
    filing_id: str
    published_at: str
    form_type: str
    category: str
    title: str
    source_url: str
    document_type: str
    event_type: str
    severity: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    @property
    def is_high_value(self) -> bool:
        return self.severity in {"critical", "high"}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OfficialKeyDate:
    provider: str
    market: str
    symbol: str
    event_date: str
    event_type: str
    title: str
    purpose: str
    period: str
    severity: str
    certainty: str
    source_url: str
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnifiedEvent:
    event_id: str
    market: str
    symbol: str
    source: str
    source_url: str
    published_at: str
    effective_at: str
    effective_session: str
    event_type: str
    tags: List[Dict[str, str]]
    severity: str
    certainty: str
    headline: str
    facts: List[str]
    gate_action: str
    gate_reason: str
    gate_until: str
    dedupe_key: str
    status: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateDecision:
    action: str
    priority: int
    reason: str
    gate_until: str
    scope: str = "symbol"
    affected_sectors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
