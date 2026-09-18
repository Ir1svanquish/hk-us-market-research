"""HKEXnews metadata adapter with Chinese-title and bounded body enrichment."""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cache import JsonFileCache
from .classifier import SEVERITY_RANK, classify_hkex_event, classify_hkex_events
from .document_text import OfficialDocumentTextExtractor
from .models import OfficialFiling


class HKEXnewsError(RuntimeError):
    pass


GENERIC_ENGLISH_TITLE_MARKERS = (
    "an announcement has just been published by the issuer in the chinese section",
    "a corresponding version of which may or may not be published",
)


def normalize_hk_symbol(symbol: str) -> str:
    value = (symbol or "").strip().upper()
    if value.startswith("HK"):
        value = value[2:]
    if value.endswith(".HK"):
        value = value[:-3]
    if not value.isdigit() or len(value) > 5:
        raise ValueError(f"invalid HK symbol: {symbol!r}")
    return value.zfill(5)


def _strip_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _stock_code_tokens(value: Any) -> set[str]:
    return set(re.findall(r"\b\d{5}\b", _strip_html(value)))


def _is_generic_english_title(title: str) -> bool:
    normalized = title.casefold()
    return any(marker in normalized for marker in GENERIC_ENGLISH_TITLE_MARKERS)


class HKEXnewsClient:
    BASE_URL = "https://www1.hkexnews.hk"
    TITLE_SEARCH_URL = f"{BASE_URL}/search/titleSearchServlet.do"
    ACTIVE_STOCK_URL = f"{BASE_URL}/ncms/script/eds/activestock_sehk_e.json"
    INACTIVE_STOCK_URL = f"{BASE_URL}/ncms/script/eds/inactivestock_sehk_e.json"

    def __init__(
        self,
        cache_dir: Path,
        timeout: float = 8.0,
        session: Optional[requests.Session] = None,
        map_ttl_seconds: int = 86400,
        document_extractor: Optional[OfficialDocumentTextExtractor] = None,
    ):
        self.cache = JsonFileCache(Path(cache_dir))
        self.timeout = max(1.0, float(timeout))
        self.map_ttl_seconds = max(0, int(map_ttl_seconds))
        self.session = session or self._build_session()
        self.document_extractor = document_extractor

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers.update(
            {
                "User-Agent": "daily-stock-analysis-v2/0.2 (official filing experiment)",
                "Accept": "application/json,text/plain,*/*",
                "Referer": f"{HKEXnewsClient.BASE_URL}/search/titlesearch.xhtml?lang=en",
            }
        )
        return session

    def resolve_stock_ids(self, symbols: Iterable[str]) -> Dict[str, Dict[str, str]]:
        normalized = [normalize_hk_symbol(symbol) for symbol in symbols]
        active = self._load_stock_map("hkex_active_stocks", self.ACTIVE_STOCK_URL)
        resolved = {symbol: active[symbol] for symbol in normalized if symbol in active}
        missing = [symbol for symbol in normalized if symbol not in resolved]
        if missing:
            inactive = self._load_stock_map("hkex_inactive_stocks", self.INACTIVE_STOCK_URL)
            resolved.update({symbol: inactive[symbol] for symbol in missing if symbol in inactive})
        return resolved

    def _load_stock_map(self, cache_key: str, url: str) -> Dict[str, Dict[str, str]]:
        payload = self.cache.get(cache_key, self.map_ttl_seconds)
        if payload is None:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            self.cache.set(cache_key, payload)
        if not isinstance(payload, list):
            raise HKEXnewsError(f"unexpected stock map payload from {url}")

        mapping: Dict[str, Dict[str, str]] = {}
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("c") or "").strip()
            stock_id = item.get("i")
            if re.fullmatch(r"\d{5}", code) and stock_id is not None:
                mapping[code] = {
                    "stock_id": str(stock_id),
                    "name": _strip_html(item.get("n")),
                }
        if not mapping:
            raise HKEXnewsError(f"empty or invalid stock map from {url}")
        return mapping

    def fetch_filings(
        self,
        symbol: str,
        stock_id: str,
        start_date: date,
        end_date: date,
        max_records: int = 500,
        include_chinese: bool = True,
        extract_bodies: bool = False,
        max_body_documents: int = 5,
    ) -> List[OfficialFiling]:
        normalized = normalize_hk_symbol(symbol)
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        english_rows = self._fetch_rows(stock_id, start_date, end_date, max_records, lang="E")
        chinese_rows = (
            self._fetch_rows(stock_id, start_date, end_date, max_records, lang="zh")
            if include_chinese
            else []
        )

        # FILE_INFO is localized and often changes from the 1KB English
        # placeholder to the real Chinese PDF size, so it cannot be a join key.
        localized_index: Dict[str, List[Dict[str, Any]]] = {}
        for row in chinese_rows:
            key = str(row.get("DATE_TIME") or "")
            localized_index.setdefault(key, []).append(row)

        filings: List[OfficialFiling] = []
        seen_ids: set[str] = set()
        for row in english_rows:
            key = str(row.get("DATE_TIME") or "")
            candidates = localized_index.get(key) or []
            localized_row = candidates.pop(0) if candidates else None
            filing = self._row_to_filing(normalized, str(stock_id), row, localized_row)
            if filing is None or filing.filing_id in seen_ids:
                continue
            seen_ids.add(filing.filing_id)
            filings.append(filing)

        if extract_bodies and filings:
            filings = self._enrich_bodies(filings, max_documents=max_body_documents)
        return filings

    def _fetch_rows(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        max_records: int,
        lang: str,
    ) -> List[Dict[str, Any]]:
        row_range = min(100, max(1, int(max_records)))
        rows: List[Dict[str, Any]] = []
        while True:
            payload = self._request_title_search(
                stock_id=str(stock_id),
                start_date=start_date,
                end_date=end_date,
                row_range=row_range,
                lang=lang,
            )
            rows = self._decode_results(payload)
            if not bool(payload.get("hasNextRow")) or row_range >= max_records:
                break
            next_range = min(row_range + 100, max_records)
            if next_range == row_range:
                break
            row_range = next_range
        return rows

    def _request_title_search(
        self,
        stock_id: str,
        start_date: date,
        end_date: date,
        row_range: int,
        lang: str,
    ) -> Dict[str, Any]:
        params = {
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": stock_id,
            "documentType": "-1",
            "fromDate": start_date.strftime("%Y%m%d"),
            "toDate": end_date.strftime("%Y%m%d"),
            "title": "",
            "searchType": "1",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": str(row_range),
            "lang": lang,
        }
        response = self.session.get(self.TITLE_SEARCH_URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "result" not in payload:
            raise HKEXnewsError("HKEXnews title-search response contract changed")
        return payload

    @staticmethod
    def _decode_results(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        raw_result = payload.get("result")
        if isinstance(raw_result, str):
            try:
                raw_result = json.loads(raw_result)
            except json.JSONDecodeError as exc:
                raise HKEXnewsError("HKEXnews result field is not valid JSON") from exc
        if not isinstance(raw_result, list):
            raise HKEXnewsError("HKEXnews result field is not a list")
        return [dict(item) for item in raw_result if isinstance(item, Mapping)]

    def _row_to_filing(
        self,
        symbol: str,
        stock_id: str,
        row: Mapping[str, Any],
        localized_row: Optional[Mapping[str, Any]] = None,
    ) -> Optional[OfficialFiling]:
        if symbol not in _stock_code_tokens(row.get("STOCK_CODE")):
            return None
        filing_id = str(row.get("NEWS_ID") or "").strip()
        english_link = str(row.get("FILE_LINK") or "").strip()
        english_title = _strip_html(row.get("TITLE"))
        english_category = _strip_html(row.get("LONG_TEXT") or row.get("SHORT_TEXT"))
        if not filing_id or not english_title or not english_link:
            raise HKEXnewsError(f"incomplete HKEXnews row for {symbol}")

        chinese_title = _strip_html(localized_row.get("TITLE")) if localized_row else ""
        chinese_category = (
            _strip_html(localized_row.get("LONG_TEXT") or localized_row.get("SHORT_TEXT"))
            if localized_row
            else ""
        )
        chinese_link = str(localized_row.get("FILE_LINK") or "").strip() if localized_row else ""
        generic_english = _is_generic_english_title(english_title)
        title = chinese_title if generic_english and chinese_title else english_title
        category = chinese_category if generic_english and chinese_category else english_category
        file_link = chinese_link if generic_english and chinese_link else english_link

        raw_date = str(row.get("DATE_TIME") or "").strip()
        try:
            published = datetime.strptime(raw_date, "%d/%m/%Y %H:%M").replace(
                tzinfo=ZoneInfo("Asia/Hong_Kong")
            )
        except ValueError as exc:
            raise HKEXnewsError(f"unexpected HKEXnews DATE_TIME: {raw_date!r}") from exc

        combined_title = " ".join(part for part in (title, english_title, chinese_title) if part)
        combined_category = " ".join(part for part in (category, english_category, chinese_category) if part)
        tags = classify_hkex_events(combined_title, combined_category)
        event_type, severity = classify_hkex_event(combined_title, combined_category)
        metadata = {
            "stock_name": _strip_html(row.get("STOCK_NAME")),
            "stock_codes": sorted(_stock_code_tokens(row.get("STOCK_CODE"))),
            "file_info": _strip_html(row.get("FILE_INFO")),
            "dod_web_path": str(row.get("DOD_WEB_PATH") or "").strip(),
            "event_tags": tags,
            "english_title": english_title,
            "english_category": english_category,
            "english_source_url": urljoin(self.BASE_URL, english_link),
            "english_title_generic": generic_english,
        }
        if localized_row:
            metadata.update(
                {
                    "chinese_news_id": str(localized_row.get("NEWS_ID") or "").strip(),
                    "chinese_title": chinese_title,
                    "chinese_category": chinese_category,
                    "chinese_source_url": urljoin(self.BASE_URL, chinese_link) if chinese_link else "",
                }
            )
        return OfficialFiling(
            provider="hkexnews",
            market="hk",
            symbol=symbol,
            issuer_id=stock_id,
            filing_id=filing_id,
            published_at=published.isoformat(),
            form_type=_strip_html(row.get("FILE_TYPE")) or "UNKNOWN",
            category=category,
            title=title,
            source_url=urljoin(self.BASE_URL, file_link),
            document_type=_strip_html(row.get("FILE_TYPE")) or "UNKNOWN",
            event_type=event_type,
            severity=severity,
            metadata=metadata,
        )

    def _enrich_bodies(self, filings: List[OfficialFiling], max_documents: int) -> List[OfficialFiling]:
        extractor = self.document_extractor or OfficialDocumentTextExtractor(timeout=max(15.0, self.timeout))
        ranked = sorted(
            enumerate(filings),
            key=lambda item: (
                bool(item[1].metadata.get("english_title_generic")),
                SEVERITY_RANK.get(item[1].severity, 0),
                item[1].published_at,
            ),
            reverse=True,
        )
        selected_indexes = {index for index, _ in ranked[: max(0, int(max_documents))]}
        enriched: List[OfficialFiling] = []
        for index, filing in enumerate(filings):
            if index not in selected_indexes or not filing.source_url.lower().endswith((".pdf", ".htm", ".html")):
                enriched.append(filing)
                continue
            metadata = dict(filing.metadata)
            try:
                extracted = extractor.extract(filing.source_url)
                body_text = str(extracted.get("text") or "")
                title_tags = list(metadata.get("event_tags") or [])
                if filing.event_type == "earnings_calendar":
                    # Board-meeting notices legitimately add results/dividend
                    # facts in the body.  Keep only those narrow extensions.
                    allowed = {"earnings_calendar", "financial_results", "dividend"}
                    tags = [
                        tag
                        for tag in classify_hkex_events(filing.title, filing.category, body_text)
                        if tag.get("event_type") in allowed
                    ] or title_tags
                else:
                    # Chinese titles are paired before this stage. Monthly
                    # returns, next-day disclosure forms, results, overseas
                    # filings and scheme documents contain large boilerplate
                    # vocabularies, so their body must not upgrade unrelated
                    # event risk. The text is still retained for fact display.
                    tags = title_tags
                primary = max(
                    enumerate(tags),
                    key=lambda item: (SEVERITY_RANK.get(item[1].get("severity", "medium"), 1), -item[0]),
                )[1]
                event_type = str(primary.get("event_type") or filing.event_type)
                severity = str(primary.get("severity") or filing.severity)
                metadata.update(
                    {
                        "body_status": "ok",
                        "body_document_type": extracted.get("document_type"),
                        "body_bytes": extracted.get("bytes"),
                        "body_excerpt": body_text[:6000],
                        "key_facts": extracted.get("key_facts", []),
                        "event_tags": tags,
                    }
                )
                enriched.append(
                    replace(
                        filing,
                        event_type=event_type,
                        severity=severity,
                        metadata=metadata,
                    )
                )
            except Exception as exc:
                metadata.update({"body_status": "failed", "body_error": str(exc)[:500]})
                enriched.append(replace(filing, metadata=metadata))
        return enriched
