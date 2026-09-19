"""Finnhub adapter for US earnings dates and SEC filing indexes."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from .cache import JsonFileCache
from .classifier import classify_sec_events
from .sec_edgar import normalize_us_symbol


class FinnhubClient:
    BASE_URL = "https://finnhub.io/api/v1"
    CACHE_SCHEMA_VERSION = 2

    def __init__(
        self,
        api_keys: Iterable[str],
        cache_dir: Path,
        timeout: float = 10.0,
        cache_ttl_seconds: int = 21600,
        session: Optional[requests.Session] = None,
    ):
        self.api_keys = [key.strip() for key in api_keys if key and key.strip()]
        if not self.api_keys:
            raise ValueError("at least one Finnhub API key is required")
        self.cache = JsonFileCache(Path(cache_dir))
        self.timeout = max(1.0, float(timeout))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.session = session or requests.Session()
        self._key_index = 0

    def fetch_snapshot(self, symbol: str, as_of: date, lookback_days: int = 30) -> Dict[str, Any]:
        normalized = normalize_us_symbol(symbol)
        cache_key = f"finnhub_us_v{self.CACHE_SCHEMA_VERSION}_{normalized}_{as_of.isoformat()}"
        cached = self.cache.get(cache_key, self.cache_ttl_seconds)
        if isinstance(cached, dict):
            return cached

        errors: List[str] = []
        calendar = self._request(
            "calendar/earnings",
            {
                "symbol": normalized,
                "from": as_of.isoformat(),
                "to": (as_of + timedelta(days=45)).isoformat(),
            },
            errors,
        )
        history = self._request("stock/earnings", {"symbol": normalized, "limit": 4}, errors)
        recommendations = self._request("stock/recommendation", {"symbol": normalized}, errors)
        filings = self._request(
            "stock/filings",
            {
                "symbol": normalized,
                "from": (as_of - timedelta(days=max(1, lookback_days))).isoformat(),
                "to": as_of.isoformat(),
            },
            errors,
        )

        calendar_items = calendar.get("earningsCalendar", []) if isinstance(calendar, dict) else []
        filing_items = []
        for item in filings if isinstance(filings, list) else []:
            if not isinstance(item, dict):
                continue
            form = str(item.get("form") or "").upper().strip()
            tags = classify_sec_events(form)
            filing_items.append(
                {
                    "accession": item.get("accessNumber"),
                    "form": form,
                    "filed_date": item.get("filedDate"),
                    "accepted_date": item.get("acceptedDate"),
                    "report_url": item.get("reportUrl"),
                    "filing_url": item.get("filingUrl"),
                    "event_tags": tags,
                    "severity": max(
                        tags,
                        key=lambda tag: {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(
                            str(tag.get("severity")), 0
                        ),
                    ).get("severity", "medium"),
                }
            )

        payload = {
            "symbol": normalized,
            "as_of": as_of.isoformat(),
            "status": "ok" if any((calendar_items, history, recommendations, filing_items)) else "failed",
            "next_earnings": calendar_items[0] if calendar_items else {},
            "earnings_history": history[:4] if isinstance(history, list) else [],
            "recommendation": recommendations[0] if isinstance(recommendations, list) and recommendations else {},
            "filings": filing_items,
            "errors": errors,
            "sec_direct_status": "pending_contact_user_agent",
        }
        self.cache.set(cache_key, payload)
        return payload

    def _request(self, path: str, params: Dict[str, Any], errors: List[str]) -> Any:
        last_error = ""
        for _ in range(len(self.api_keys)):
            key = self.api_keys[self._key_index % len(self.api_keys)]
            self._key_index += 1
            try:
                response = self.session.get(
                    f"{self.BASE_URL}/{path}",
                    params={**params, "token": key},
                    timeout=self.timeout,
                )
                if response.status_code == 200:
                    return response.json()
                last_error = f"{path} status={response.status_code}"
                if response.status_code not in {401, 403, 429}:
                    break
            except Exception as exc:
                last_error = f"{path}: {exc}"
        if last_error:
            errors.append(last_error)
        return None
