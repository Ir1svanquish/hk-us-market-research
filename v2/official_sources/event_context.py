"""Build one deduplicated event context across HKEX, SEC, Finnhub, and BLS."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from .bls_calendar import MacroRelease
from .classifier import SEVERITY_RANK, classify_hkex_events
from .gates import ACTION_PRIORITY, evaluate_event_gate
from .models import UnifiedEvent
from .storage import OfficialFilingStore


CERTAINTY_RANK = {
    "inferred": 0,
    "provider_calendar": 1,
    "market_calendar": 1,
    "confirmed": 2,
    "official_confirmed": 3,
}


def _event_id(dedupe_key: str) -> str:
    return hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]


def _session_for(published_at: str, market: str) -> str:
    try:
        value = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        target = ZoneInfo("Asia/Hong_Kong" if market == "hk" else "America/New_York")
        if value.tzinfo is None:
            value = value.replace(tzinfo=target)
        value = value.astimezone(target)
        current = value.time()
        if current < time(9, 30):
            return "premarket"
        if current >= time(16, 0):
            return "afterhours"
        return "intraday"
    except (ValueError, TypeError):
        return "all_day"


def _status(effective_at: str, gate_until: str, as_of: date) -> str:
    effective = str(effective_at or "")[:10]
    until = str(gate_until or "")[:10]
    if effective:
        try:
            if date.fromisoformat(effective) > as_of:
                return "upcoming"
        except ValueError:
            pass
    if until:
        try:
            return "active" if date.fromisoformat(until) >= as_of else "expired"
        except ValueError:
            pass
    return "active"


class EventContextBuilder:
    def __init__(self, store: OfficialFilingStore, as_of: date):
        self.store = store
        self.as_of = as_of

    def build(
        self,
        *,
        hk_symbols: Sequence[str],
        us_symbols: Sequence[str],
        finnhub_snapshots: Optional[Mapping[str, Mapping[str, object]]] = None,
        macro_releases: Iterable[MacroRelease] = (),
        filing_lookback_days: int = 30,
    ) -> List[UnifiedEvent]:
        events: List[UnifiedEvent] = []
        all_symbols = list(hk_symbols) + list(us_symbols)
        start = date.fromordinal(self.as_of.toordinal() - max(1, filing_lookback_days)).isoformat()
        for row in self.store.get_filings(all_symbols, published_since=start, limit=5000):
            if self._available_by_report_cutoff(row):
                events.append(self._from_filing(row))
        for row in self.store.list_key_dates(
            hk_symbols,
            start_date=date.fromordinal(self.as_of.toordinal() - 2).isoformat(),
            end_date=date.fromordinal(self.as_of.toordinal() + 120).isoformat(),
        ):
            events.append(self._from_key_date(row))
        for symbol, snapshot in (finnhub_snapshots or {}).items():
            event = self._from_finnhub_calendar(symbol, snapshot)
            if event:
                events.append(event)
        for release in macro_releases:
            events.append(self._from_macro(release))
        return self._dedupe(events)

    def _available_by_report_cutoff(self, row: Mapping[str, object]) -> bool:
        """Prevent later-fetched filings from leaking into historical reports.

        The legacy HK/US reports are generated shortly after the regular close.
        A store sync performed days later can contain filings accepted later on
        the same date or on subsequent dates.  The historical shadow context
        therefore uses 16:30 exchange-local time as the deterministic cutoff.
        """
        published = str(row.get("published_at") or "")
        market = str(row.get("market") or "")
        if not published or market not in {"hk", "us"}:
            return True
        try:
            value = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return False
        target = ZoneInfo("Asia/Hong_Kong" if market == "hk" else "America/New_York")
        if value.tzinfo is None:
            value = value.replace(tzinfo=target)
        local = value.astimezone(target)
        cutoff = datetime.combine(self.as_of, time(16, 30), target)
        return local <= cutoff

    def _make(self, raw: dict) -> UnifiedEvent:
        gate = evaluate_event_gate(raw, self.as_of)
        dedupe_key = str(raw["dedupe_key"])
        return UnifiedEvent(
            event_id=_event_id(dedupe_key),
            market=str(raw["market"]),
            symbol=str(raw["symbol"]),
            source=str(raw["source"]),
            source_url=str(raw.get("source_url") or ""),
            published_at=str(raw.get("published_at") or ""),
            effective_at=str(raw.get("effective_at") or ""),
            effective_session=str(raw.get("effective_session") or "all_day"),
            event_type=str(raw["event_type"]),
            tags=list(raw.get("tags") or []),
            severity=str(raw.get("severity") or "medium"),
            certainty=str(raw.get("certainty") or "inferred"),
            headline=str(raw.get("headline") or ""),
            facts=[str(item) for item in raw.get("facts") or [] if str(item).strip()],
            gate_action=gate.action,
            gate_reason=gate.reason,
            gate_until=gate.gate_until,
            dedupe_key=dedupe_key,
            status=_status(str(raw.get("effective_at") or ""), gate.gate_until, self.as_of),
            metadata={**dict(raw.get("metadata") or {}), "gate_scope": gate.scope, "affected_sectors": gate.affected_sectors},
        )

    def _from_filing(self, row: Mapping[str, object]) -> UnifiedEvent:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        published = str(row.get("published_at") or "")
        tags = metadata.get("event_tags") if isinstance(metadata.get("event_tags"), list) else []
        facts = metadata.get("key_facts") if isinstance(metadata.get("key_facts"), list) else []
        provider = str(row.get("provider") or "")
        original_event_type = str(row.get("event_type") or "")
        severity = str(row.get("severity") or "medium")
        title = str(row.get("title") or "")
        category = str(row.get("category") or "")
        if provider == "hkexnews" and any(
            marker.casefold() in f"{title} {category}".casefold()
            for marker in (
                "monthly return",
                "next day disclosure return",
                "general mandate to issue shares",
                "general mandates to issue shares",
                "月報表",
                "月报表",
                "翌日披露報表",
                "翌日披露报表",
                "發行股份之一般授權",
                "发行股份之一般授权",
            )
        ):
            # Re-evaluate generic routine returns at read time as well, so
            # already-cached rows cannot retain an old false dilution tag.
            tags = classify_hkex_events(title, category)
            primary = max(
                enumerate(tags),
                key=lambda value: (SEVERITY_RANK.get(str(value[1].get("severity") or "medium"), 1), -value[0]),
            )[1]
            original_event_type = str(primary.get("event_type") or "routine_disclosure")
            severity = str(primary.get("severity") or "low")
        # A board-meeting announcement is evidence that an earnings date exists,
        # but its publication timestamp is not the earnings date itself.  The
        # actual date comes from the dedicated HKEX board calendar/key-date row.
        event_type = (
            "earnings_calendar_notice"
            if original_event_type == "earnings_calendar"
            else original_event_type
        )
        return self._make(
            {
                "market": row.get("market"),
                "symbol": row.get("symbol"),
                "source": provider,
                "source_url": row.get("source_url"),
                "published_at": published,
                "effective_at": published,
                "effective_session": _session_for(published, str(row.get("market") or "")),
                "event_type": event_type,
                "tags": tags,
                "severity": severity,
                "certainty": "official_confirmed",
                "headline": title,
                "facts": facts[:4],
                "dedupe_key": f"filing:{provider}:{row.get('filing_id')}",
                "metadata": {
                    "form_type": row.get("form_type"),
                    "category": row.get("category"),
                    "items": metadata.get("items", ""),
                    "filing_id": row.get("filing_id"),
                    "original_event_type": original_event_type,
                },
            }
        )

    def _from_key_date(self, row: Mapping[str, object]) -> UnifiedEvent:
        event_date = str(row.get("event_date") or "")
        market = str(row.get("market") or "hk")
        effective = datetime.combine(
            date.fromisoformat(event_date), time(0, 0), ZoneInfo("Asia/Hong_Kong")
        ).isoformat()
        symbol = str(row.get("symbol") or "")
        return self._make(
            {
                "market": market,
                "symbol": symbol,
                "source": row.get("provider"),
                "source_url": row.get("source_url"),
                "published_at": row.get("fetched_at"),
                "effective_at": effective,
                "effective_session": "all_day",
                "event_type": "earnings_calendar",
                "tags": [{"event_type": "earnings_calendar", "severity": "high"}],
                "severity": row.get("severity") or "high",
                "certainty": "official_confirmed",
                "headline": row.get("title"),
                "facts": [f"用途：{row.get('purpose')}", f"期间：{row.get('period')}"],
                "dedupe_key": f"earnings:{market}:{symbol}:{event_date}",
                "metadata": {"purpose": row.get("purpose"), "period": row.get("period")},
            }
        )

    def _from_finnhub_calendar(
        self, symbol: str, snapshot: Mapping[str, object]
    ) -> Optional[UnifiedEvent]:
        item = snapshot.get("next_earnings") if isinstance(snapshot.get("next_earnings"), dict) else {}
        history = snapshot.get("earnings_history") if isinstance(snapshot.get("earnings_history"), list) else []
        recommendation = snapshot.get("recommendation") if isinstance(snapshot.get("recommendation"), dict) else {}
        event_date = str(item.get("date") or "")
        if not event_date:
            return None
        hour = str(item.get("hour") or "dmh").lower()
        release_time = time(8, 0) if hour == "bmo" else time(16, 5) if hour == "amc" else time(0, 0)
        effective = datetime.combine(date.fromisoformat(event_date), release_time, ZoneInfo("America/New_York")).isoformat()
        session = {"bmo": "premarket", "amc": "afterhours"}.get(hour, "all_day")
        return self._make(
            {
                "market": "us",
                "symbol": symbol,
                "source": "finnhub",
                "source_url": "https://finnhub.io/docs/api/earnings-calendar",
                "published_at": f"{snapshot.get('as_of') or self.as_of.isoformat()}T00:00:00+00:00",
                "effective_at": effective,
                "effective_session": session,
                "event_type": "earnings_calendar",
                "tags": [{"event_type": "earnings_calendar", "severity": "high"}],
                "severity": "high",
                "certainty": "provider_calendar",
                "headline": f"{symbol} earnings calendar window",
                "facts": [f"EPS estimate: {item.get('epsEstimate')}", f"Session: {hour}"],
                "dedupe_key": f"earnings:us:{symbol}:{event_date}",
                "metadata": {
                    "hour": hour,
                    "quarter": item.get("quarter"),
                    "year": item.get("year"),
                    "eps_estimate": item.get("epsEstimate"),
                    "revenue_estimate": item.get("revenueEstimate"),
                    "earnings_history": [dict(value) for value in history[:4] if isinstance(value, dict)],
                    "recommendation": dict(recommendation),
                },
            }
        )

    def _from_macro(self, release: MacroRelease) -> UnifiedEvent:
        title_key = hashlib.sha256(release.title.lower().encode("utf-8")).hexdigest()[:10]
        severity = {"high": "high", "medium": "medium", "low": "low"}.get(
            release.importance, "medium"
        )
        facts = [
            f"Reference period: {release.reference_period}",
            f"Release time: {release.release_time}",
        ]
        if release.actual:
            facts.append(
                f"Actual / consensus / previous: {release.actual} / {release.estimate or '—'} / {release.previous or '—'}"
            )
        return self._make(
            {
                "market": "us",
                "symbol": "__MARKET__",
                "source": release.provider,
                "source_url": release.source_url,
                "published_at": f"{self.as_of.isoformat()}T00:00:00+00:00",
                "effective_at": release.effective_at,
                "effective_session": "premarket",
                "event_type": release.event_type,
                "tags": [{"event_type": release.event_type, "severity": severity}],
                "severity": severity,
                "certainty": release.certainty,
                "headline": release.title,
                "facts": facts,
                "dedupe_key": f"macro:us:{release.release_date}:{release.event_type}:{title_key}",
                "metadata": {
                    "is_macro": True,
                    "category": release.category,
                    "importance": release.importance,
                    "reference_period": release.reference_period,
                    "release_time": release.release_time,
                    "retrieval_status": release.retrieval_status,
                    "scenarios": release.scenarios,
                    "actual": release.actual,
                    "estimate": release.estimate,
                    "previous": release.previous,
                    "unit": release.unit,
                    "analysis": release.analysis,
                },
            }
        )

    @staticmethod
    def _dedupe(events: Iterable[UnifiedEvent]) -> List[UnifiedEvent]:
        selected: Dict[str, UnifiedEvent] = {}
        for event in events:
            previous = selected.get(event.dedupe_key)
            if previous is None or CERTAINTY_RANK.get(event.certainty, 0) > CERTAINTY_RANK.get(previous.certainty, 0):
                selected[event.dedupe_key] = event
        return sorted(selected.values(), key=lambda item: (item.effective_at, item.symbol, item.event_type))


def load_snapshots(path: Optional[Path]) -> Dict[str, Mapping[str, object]]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def strongest_by_symbol(events: Iterable[UnifiedEvent]) -> Dict[str, dict]:
    grouped: Dict[str, list] = {}
    for event in events:
        if event.symbol == "__MARKET__":
            continue
        grouped.setdefault(event.symbol, []).append(event)
    result = {}
    for symbol, items in grouped.items():
        priority = max(ACTION_PRIORITY.get(event.gate_action, 0) for event in items)
        if priority == 0:
            result[symbol] = {
                "gate_action": "info_only",
                "priority": 0,
                "reason": f"未触发硬性交易限制；{len(items)} 项官方事件仅保留为分析依据。",
                "gate_until": "",
                "events": len(items),
                "triggered_events": [],
            }
            continue
        selected = [
            event for event in items if ACTION_PRIORITY.get(event.gate_action, 0) == priority
        ]
        reasons = list(
            dict.fromkeys(event.gate_reason.rstrip("。；") for event in selected if event.gate_reason)
        )
        gate_dates = sorted(event.gate_until for event in selected if event.gate_until)
        result[symbol] = {
            "gate_action": selected[0].gate_action,
            "priority": priority,
            "reason": "；".join(reasons) + ("。" if reasons else ""),
            "gate_until": gate_dates[-1] if gate_dates else "",
            "events": len(items),
            "triggered_events": [
                {
                    "event_type": event.event_type,
                    "headline": event.headline,
                    "source": event.source,
                    "gate_until": event.gate_until,
                }
                for event in selected
            ],
        }
    return result
