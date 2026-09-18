"""BLS release calendar with an explicitly marked verified-official fallback seed."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

import requests

from .cache import JsonFileCache
from .macro_catalog import classify_macro_event
from .macro_scenarios import scenarios_for


@dataclass(frozen=True)
class MacroRelease:
    provider: str
    event_type: str
    release_date: str
    release_time: str
    effective_at: str
    title: str
    reference_period: str
    source_url: str
    certainty: str
    retrieval_status: str
    scenarios: List[dict]
    category: str = "other"
    importance: str = "medium"
    actual: str = ""
    estimate: str = ""
    previous: str = ""
    unit: str = ""
    analysis: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag.lower() in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


class BLSCalendarClient:
    URL_TEMPLATE = "https://www.bls.gov/schedule/{year}/{month:02d}_sched_list.htm"

    def __init__(
        self,
        cache_dir: Path,
        seed_path: Path,
        timeout: float = 8.0,
        session: Optional[requests.Session] = None,
        cache_ttl_seconds: int = 21600,
    ):
        self.cache = JsonFileCache(Path(cache_dir))
        self.seed_path = Path(seed_path)
        self.timeout = max(1.0, float(timeout))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.session = session or requests.Session()
        if session is None:
            self.session.headers.update(
                {"User-Agent": "daily-stock-analysis-v2 macro calendar (BLS schedule reader)"}
            )

    def fetch_releases(self, start_date: date, end_date: date) -> List[MacroRelease]:
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        live_rows: List[dict] = []
        live_failed = False
        for year, month in self._months(start_date, end_date):
            cache_key = f"bls_schedule_{year}_{month:02d}"
            rows = self.cache.get(cache_key, self.cache_ttl_seconds)
            if rows is None:
                url = self.URL_TEMPLATE.format(year=year, month=month)
                try:
                    response = self.session.get(url, timeout=self.timeout)
                    response.raise_for_status()
                    rows = self._parse_html(response.text, url)
                    self.cache.set(cache_key, rows)
                except Exception:
                    live_failed = True
                    break
            if isinstance(rows, list):
                live_rows.extend(item for item in rows if isinstance(item, dict))

        if live_failed or not live_rows:
            rows = self._load_seed()
            status = "verified_official_seed"
        else:
            rows = live_rows
            status = "live_official_schedule"

        releases = []
        for item in rows:
            try:
                release_date = date.fromisoformat(str(item["release_date"]))
            except (KeyError, ValueError):
                continue
            if not start_date <= release_date <= end_date:
                continue
            classification = classify_macro_event(str(item.get("title") or item.get("event_type") or ""))
            event_type = str(item.get("event_type") or classification.event_type)
            category = str(item.get("category") or classification.category)
            importance = str(item.get("importance") or classification.importance)
            release_time = str(item.get("release_time") or "08:30 AM")
            effective_at = self._effective_at(release_date, release_time)
            releases.append(
                MacroRelease(
                    provider="bls",
                    event_type=event_type,
                    release_date=release_date.isoformat(),
                    release_time=release_time,
                    effective_at=effective_at,
                    title=str(item.get("title") or event_type),
                    reference_period=str(item.get("reference_period") or ""),
                    source_url=str(item.get("source_url") or "https://www.bls.gov/schedule/"),
                    certainty="official_confirmed",
                    retrieval_status=status,
                    scenarios=scenarios_for(event_type, category),
                    category=category,
                    importance=importance,
                    analysis={"status": "scheduled", "summary": "尚未公布实际值。"},
                )
            )
        return sorted(releases, key=lambda item: item.effective_at)

    @classmethod
    def _parse_html(cls, text: str, source_url: str) -> List[dict]:
        parser = _TableParser()
        parser.feed(text)
        result = []
        for row in parser.rows:
            if len(row) < 3 or row[0].lower() == "date":
                continue
            classification = classify_macro_event(row[2])
            try:
                parsed_date = datetime.strptime(row[0], "%A, %B %d, %Y").date()
            except ValueError:
                continue
            reference = ""
            match = re.search(r" for (.+)$", row[2])
            if match:
                reference = match.group(1).strip()
            result.append(
                {
                    "event_type": classification.event_type,
                    "category": classification.category,
                    "importance": classification.importance,
                    "release_date": parsed_date.isoformat(),
                    "release_time": row[1] or "08:30 AM",
                    "reference_period": reference,
                    "title": row[2],
                    "source_url": source_url,
                }
            )
        return result

    def _load_seed(self) -> List[dict]:
        payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            raise RuntimeError("invalid BLS fallback seed")
        return [dict(item) for item in events if isinstance(item, dict)]

    @staticmethod
    def _effective_at(day: date, value: str) -> str:
        parsed = datetime.strptime(value.strip().upper(), "%I:%M %p").time()
        return datetime.combine(day, parsed, ZoneInfo("America/New_York")).isoformat()

    @staticmethod
    def _months(start_date: date, end_date: date) -> Iterable[tuple[int, int]]:
        year, month = start_date.year, start_date.month
        while (year, month) <= (end_date.year, end_date.month):
            yield year, month
            month += 1
            if month == 13:
                year += 1
                month = 1
