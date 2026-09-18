"""Comprehensive US macro calendar with actual/consensus/previous values from Nasdaq."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests
import re

from .bls_calendar import MacroRelease
from .cache import JsonFileCache
from .macro_catalog import classify_macro_event
from .macro_scenarios import analyze_release, scenarios_for


class NasdaqMacroCalendarClient:
    URL = "https://api.nasdaq.com/api/calendar/economicevents"

    def __init__(
        self,
        cache_dir: Path,
        timeout: float = 12.0,
        cache_ttl_seconds: int = 21600,
        session: Optional[requests.Session] = None,
        max_workers: int = 4,
    ):
        self.cache = JsonFileCache(Path(cache_dir))
        self.timeout = max(1.0, float(timeout))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.max_workers = max(1, min(8, int(max_workers)))
        self.session = session or requests.Session()
        if session is None:
            self.session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://www.nasdaq.com",
                    "Referer": "https://www.nasdaq.com/market-activity/economic-calendar",
                }
            )
        self.errors: List[str] = []

    def fetch_releases(self, start_date: date, end_date: date) -> List[MacroRelease]:
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        # Nasdaq's date parameter is one calendar day ahead of the US release
        # date represented by its rows. Query the shifted range, then normalize
        # every row back to the actual US calendar day below.
        days = []
        current = start_date + timedelta(days=1)
        query_end = end_date + timedelta(days=1)
        while current <= query_end:
            days.append(current)
            current += timedelta(days=1)

        rows_by_day = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(days) or 1)) as executor:
            futures = {executor.submit(self._fetch_day, day): day for day in days}
            for future in as_completed(futures):
                day = futures[future]
                try:
                    rows_by_day[day] = future.result()
                except Exception as exc:
                    self.errors.append(f"{day.isoformat()}: {type(exc).__name__}")
                    rows_by_day[day] = []

        releases = []
        for day in sorted(rows_by_day):
            release_day = day - timedelta(days=1)
            periodicity = self._periodicity_labels(rows_by_day[day])
            for row_index, row in enumerate(rows_by_day[day]):
                if str(row.get("country") or "").strip().lower() not in {
                    "united states",
                    "us",
                    "usa",
                }:
                    continue
                title = str(row.get("eventName") or "").strip()
                if not title:
                    continue
                frequency = periodicity.get(row_index, "")
                display_title = f"{title} ({frequency})" if frequency else title
                classification = classify_macro_event(title)
                actual = str(row.get("actual") or "").strip()
                estimate = str(row.get("consensus") or "").strip()
                previous = str(row.get("previous") or "").strip()
                releases.append(
                    MacroRelease(
                        provider="nasdaq_macro_calendar",
                        event_type=classification.event_type,
                        release_date=release_day.isoformat(),
                        release_time=f"{str(row.get('gmt') or '00:00').strip()} ET",
                        effective_at=self._effective_at(release_day, row.get("gmt")),
                        title=display_title,
                        reference_period="",
                        source_url=f"{self.URL}?date={day.isoformat()}",
                        certainty="market_calendar",
                        retrieval_status="live_market_calendar",
                        scenarios=scenarios_for(classification.event_type, classification.category),
                        category=classification.category,
                        importance=classification.importance,
                        actual=actual,
                        estimate=estimate,
                        previous=previous,
                        unit=frequency,
                        analysis=analyze_release(
                            event_type=classification.event_type,
                            category=classification.category,
                            actual=actual,
                            estimate=estimate,
                            previous=previous,
                        ),
                    )
                )
        return sorted(releases, key=lambda item: (item.effective_at, item.title))

    @staticmethod
    def _percent_magnitude(row: dict) -> float | None:
        values = []
        for field in ("actual", "consensus", "previous"):
            match = re.search(r"[-+]?\d+(?:\.\d+)?", str(row.get(field) or ""))
            if match:
                values.append(abs(float(match.group(0))))
        return max(values) if values else None

    @classmethod
    def _periodicity_labels(cls, rows: List[dict]) -> dict[int, str]:
        """Distinguish duplicated PPI month-over-month and year-over-year rows.

        Nasdaq returns both frequencies with the same eventName and description.
        For PPI families, the annual rate is materially larger than the monthly
        change in normal releases; if that distinction is not clear, leave both
        unlabeled rather than inventing a frequency.
        """
        groups: dict[tuple[str, str, str], list[int]] = {}
        labels: dict[int, str] = {}
        for index, row in enumerate(rows):
            if str(row.get("country") or "").strip().lower() not in {
                "united states",
                "us",
                "usa",
            }:
                continue
            title = str(row.get("eventName") or "").strip()
            explicit = re.search(r"\b(mom|yoy)\b", title, flags=re.IGNORECASE)
            if explicit:
                labels[index] = explicit.group(1).title()
                continue
            if not re.search(r"\bppi\b", title, flags=re.IGNORECASE):
                continue
            key = (
                str(row.get("country") or "").strip().lower(),
                str(row.get("gmt") or "").strip(),
                title.casefold(),
            )
            groups.setdefault(key, []).append(index)
        for indexes in groups.values():
            if len(indexes) != 2:
                continue
            scored = [
                (cls._percent_magnitude(rows[index]), index)
                for index in indexes
            ]
            if any(value is None for value, _ in scored):
                continue
            scored.sort()
            if float(scored[1][0]) - float(scored[0][0]) < 0.75:
                continue
            labels[scored[0][1]] = "MoM"
            labels[scored[1][1]] = "YoY"
        return labels

    def _fetch_day(self, day: date) -> List[dict]:
        cache_key = f"nasdaq_macro_{day.isoformat()}"
        cached = self.cache.get(cache_key, self.cache_ttl_seconds)
        if isinstance(cached, list):
            return [dict(item) for item in cached if isinstance(item, dict)]
        response = self.session.get(self.URL, params={"date": day.isoformat()}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("rows") if isinstance(data, dict) else None
        result = [dict(item) for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
        self.cache.set(cache_key, result)
        return result

    @staticmethod
    def _effective_at(day: date, value: object) -> str:
        text = str(value or "").strip()
        try:
            parsed = datetime.strptime(text, "%H:%M").time()
        except ValueError:
            parsed = time(0, 0)
        eastern = datetime.combine(day, parsed, ZoneInfo("America/New_York"))
        return eastern.astimezone(timezone.utc).isoformat()
