"""Metadata-only adapter for SEC EDGAR public data APIs."""

from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cache import JsonFileCache
from .classifier import classify_sec_event, classify_sec_events
from .models import OfficialFiling


class SECEdgarError(RuntimeError):
    pass


TRACKED_FORMS = {
    "8-K",
    "8-K/A",
    "10-Q",
    "10-Q/A",
    "10-K",
    "10-K/A",
    "6-K",
    "6-K/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
    "4",
    "4/A",
    "144",
    "144/A",
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
    "F-1",
    "F-1/A",
    "F-3",
    "F-3/A",
    "424B2",
    "424B3",
    "424B4",
    "424B5",
    "SC 13D",
    "SC 13D/A",
    "SC 13G",
    "SC 13G/A",
    "DEF 14A",
    "PRE 14A",
}


def normalize_us_symbol(symbol: str) -> str:
    value = (symbol or "").strip().upper().replace(".", "-")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,14}", value):
        raise ValueError(f"invalid US symbol: {symbol!r}")
    return value


class SECEdgarClient:
    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path,
        timeout: float = 8.0,
        session: Optional[requests.Session] = None,
        map_ttl_seconds: int = 86400,
        min_request_interval: float = 0.5,
    ):
        if "@" not in (user_agent or ""):
            raise ValueError("SEC user_agent must identify the application and contain a contact email")
        self.user_agent = user_agent.strip()
        self.cache = JsonFileCache(Path(cache_dir))
        self.timeout = max(1.0, float(timeout))
        self.map_ttl_seconds = max(0, int(map_ttl_seconds))
        self.min_request_interval = max(0.0, float(min_request_interval))
        self._last_request_at = 0.0
        self.session = session or self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json,*/*",
            }
        )
        return session

    def resolve_ciks(self, symbols: Iterable[str]) -> Dict[str, Dict[str, str]]:
        normalized = [normalize_us_symbol(symbol) for symbol in symbols]
        mapping = self._load_ticker_map()
        return {symbol: mapping[symbol] for symbol in normalized if symbol in mapping}

    def _load_ticker_map(self) -> Dict[str, Dict[str, str]]:
        payload = self.cache.get("sec_company_tickers", self.map_ttl_seconds)
        if payload is None:
            payload = self._get_json(self.TICKER_MAP_URL)
            self.cache.set("sec_company_tickers", payload)
        if not isinstance(payload, Mapping):
            raise SECEdgarError("unexpected SEC company_tickers payload")

        mapping: Dict[str, Dict[str, str]] = {}
        for item in payload.values():
            if not isinstance(item, Mapping):
                continue
            ticker = str(item.get("ticker") or "").upper().strip().replace(".", "-")
            cik = item.get("cik_str")
            if ticker and cik is not None:
                try:
                    normalized_cik = f"{int(cik):010d}"
                except (TypeError, ValueError):
                    continue
                mapping[ticker] = {
                    "cik": normalized_cik,
                    "name": str(item.get("title") or "").strip(),
                }
        if not mapping:
            raise SECEdgarError("empty or invalid SEC company_tickers payload")
        return mapping

    def fetch_filings(
        self,
        symbol: str,
        cik: str,
        start_date: date,
        end_date: date,
        forms: Optional[Iterable[str]] = None,
    ) -> List[OfficialFiling]:
        normalized = normalize_us_symbol(symbol)
        normalized_cik = f"{int(cik):010d}"
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        tracked_forms = {form.upper() for form in (forms or TRACKED_FORMS)}
        payload = self._get_json(self.SUBMISSIONS_URL.format(cik=normalized_cik))
        if not isinstance(payload, Mapping):
            raise SECEdgarError(f"unexpected submissions payload for {normalized}")
        recent = payload.get("filings", {}).get("recent", {})
        if not isinstance(recent, Mapping):
            raise SECEdgarError(f"missing filings.recent for {normalized}")

        fields = {
            key: value
            for key, value in recent.items()
            if isinstance(value, list)
        }
        accession_numbers = fields.get("accessionNumber", [])
        filings: List[OfficialFiling] = []
        for index, accession in enumerate(accession_numbers):
            row = {key: values[index] if index < len(values) else "" for key, values in fields.items()}
            form = str(row.get("form") or "").upper().strip()
            filing_date_raw = str(row.get("filingDate") or "").strip()
            is_default_extension = form.startswith(
                ("424B", "SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G")
            )
            if form not in tracked_forms and not is_default_extension:
                continue
            try:
                filing_date = date.fromisoformat(filing_date_raw)
            except ValueError:
                continue
            if not start_date <= filing_date <= end_date:
                continue
            filing = self._row_to_filing(
                symbol=normalized,
                cik=normalized_cik,
                issuer_name=str(payload.get("name") or "").strip(),
                row=row,
            )
            filings.append(filing)
        return filings

    def _get_json(self, url: str) -> Any:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        response = self.session.get(url, timeout=self.timeout)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response.json()

    def _row_to_filing(
        self,
        symbol: str,
        cik: str,
        issuer_name: str,
        row: Mapping[str, Any],
    ) -> OfficialFiling:
        accession = str(row.get("accessionNumber") or "").strip()
        primary_document = str(row.get("primaryDocument") or "").strip()
        if not accession:
            raise SECEdgarError(f"SEC filing without accession number for {symbol}")
        accession_compact = accession.replace("-", "")
        cik_compact = str(int(cik))
        if primary_document:
            source_url = f"{self.ARCHIVES_BASE_URL}/{cik_compact}/{accession_compact}/{primary_document}"
        else:
            source_url = f"{self.ARCHIVES_BASE_URL}/{cik_compact}/{accession_compact}/"

        form = str(row.get("form") or "").upper().strip()
        items = str(row.get("items") or "").strip()
        title = f"{form} filing"
        event_type, severity = classify_sec_event(form, items=items, title=title)
        event_tags = classify_sec_events(form, items=items, title=title)
        published_at = str(row.get("acceptanceDateTime") or "").strip()
        if not published_at:
            published_at = f"{row.get('filingDate')}T00:00:00"

        return OfficialFiling(
            provider="sec_edgar",
            market="us",
            symbol=symbol,
            issuer_id=cik,
            filing_id=accession,
            published_at=published_at,
            form_type=form,
            category=items,
            title=title,
            source_url=source_url,
            document_type="HTML/XML",
            event_type=event_type,
            severity=severity,
            metadata={
                "issuer_name": issuer_name,
                "filing_date": str(row.get("filingDate") or ""),
                "report_date": str(row.get("reportDate") or ""),
                "primary_document": primary_document,
                "primary_doc_description": str(row.get("primaryDocDescription") or ""),
                "file_number": str(row.get("fileNumber") or ""),
                "film_number": str(row.get("filmNumber") or ""),
                "items": items,
                "event_tags": event_tags,
            },
        )
