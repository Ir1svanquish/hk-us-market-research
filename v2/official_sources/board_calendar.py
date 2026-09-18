"""HKEX official board-meeting calendar adapter."""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

from .cache import JsonFileCache
from .hkexnews import normalize_hk_symbol
from .models import OfficialKeyDate


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() == "td" and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "td" and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


class HKEXBoardCalendarClient:
    URL = "https://www.hkex-is.hk/wwwroot/link/ebmn.htm"

    def __init__(
        self,
        cache_dir: Path,
        timeout: float = 8.0,
        session: Optional[requests.Session] = None,
        cache_ttl_seconds: int = 21600,
    ):
        self.cache = JsonFileCache(Path(cache_dir))
        self.timeout = max(1.0, float(timeout))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        if session is None:
            self.session = requests.Session()
            self.session.headers.update(
                {"User-Agent": "daily-stock-analysis-v2/0.1 (board calendar)"}
            )
        else:
            self.session = session

    def fetch_key_dates(self, symbols: Iterable[str]) -> List[OfficialKeyDate]:
        target = {normalize_hk_symbol(symbol) for symbol in symbols}
        rows = self.cache.get("hkex_board_calendar", self.cache_ttl_seconds)
        if rows is None:
            response = self.session.get(self.URL, timeout=self.timeout)
            response.raise_for_status()
            parser = _TableParser()
            parser.feed(response.text)
            rows = parser.rows
            self.cache.set("hkex_board_calendar", rows)
        if not isinstance(rows, list):
            raise RuntimeError("unexpected HKEX board calendar payload")

        events: List[OfficialKeyDate] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            raw_date, _, name, raw_code, purpose, period = row[:6]
            code_digits = re.sub(r"\D", "", raw_code)
            if not code_digits:
                continue
            symbol = code_digits.zfill(5)
            if symbol not in target:
                continue
            try:
                event_date = datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat()
            except ValueError:
                continue
            events.append(
                OfficialKeyDate(
                    provider="hkex_board_calendar",
                    market="hk",
                    symbol=symbol,
                    event_date=event_date,
                    event_type="earnings_release",
                    title=f"{name} 業績董事會日期",
                    purpose=purpose,
                    period=period,
                    severity="high",
                    certainty="confirmed",
                    source_url=self.URL,
                )
            )
        return sorted(events, key=lambda item: (item.event_date, item.symbol))
