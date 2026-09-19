"""Orchestration for the official-source metadata sync."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional

from .board_calendar import HKEXBoardCalendarClient
from .hkexnews import HKEXnewsClient, normalize_hk_symbol
from .sec_edgar import SECEdgarClient, normalize_us_symbol
from .storage import OfficialFilingStore


@dataclass
class SymbolSyncResult:
    provider: str
    market: str
    symbol: str
    status: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    high_value: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BatchSyncResult:
    items: List[SymbolSyncResult] = field(default_factory=list)
    key_dates: List[dict] = field(default_factory=list)
    calendar_error: str = ""

    @property
    def ok(self) -> bool:
        return all(item.status == "ok" for item in self.items) and not self.calendar_error

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "totals": {
                "symbols": len(self.items),
                "failed": sum(item.status != "ok" for item in self.items),
                "fetched": sum(item.fetched for item in self.items),
                "inserted": sum(item.inserted for item in self.items),
                "updated": sum(item.updated for item in self.items),
                "high_value": sum(item.high_value for item in self.items),
                "confirmed_key_dates": len(self.key_dates),
            },
            "items": [item.to_dict() for item in self.items],
            "key_dates": self.key_dates,
            "calendar_error": self.calendar_error,
        }


class OfficialSourceSync:
    def __init__(
        self,
        store: OfficialFilingStore,
        hkex_client: Optional[HKEXnewsClient] = None,
        board_calendar_client: Optional[HKEXBoardCalendarClient] = None,
        sec_client: Optional[SECEdgarClient] = None,
    ):
        self.store = store
        self.hkex_client = hkex_client
        self.board_calendar_client = board_calendar_client
        self.sec_client = sec_client

    def sync(
        self,
        *,
        hk_symbols: Iterable[str],
        us_symbols: Iterable[str],
        start_date: date,
        end_date: date,
        extract_bodies: bool = False,
    ) -> BatchSyncResult:
        result = BatchSyncResult()
        hk_list = [normalize_hk_symbol(symbol) for symbol in hk_symbols]
        us_list = [normalize_us_symbol(symbol) for symbol in us_symbols]
        if hk_list:
            result.items.extend(self._sync_hk(hk_list, start_date, end_date, extract_bodies))
            if self.board_calendar_client is None:
                result.calendar_error = "HKEX board calendar client unavailable"
            else:
                try:
                    key_dates = self.board_calendar_client.fetch_key_dates(hk_list)
                    self.store.upsert_key_dates(key_dates)
                    result.key_dates = [item.to_dict() for item in key_dates]
                except Exception as exc:
                    result.calendar_error = str(exc)
        if us_list:
            result.items.extend(self._sync_us(us_list, start_date, end_date))
        return result

    def _sync_hk(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        extract_bodies: bool,
    ) -> List[SymbolSyncResult]:
        if self.hkex_client is None:
            return [
                SymbolSyncResult("hkexnews", "hk", symbol, "failed", error="HKEXnews client unavailable")
                for symbol in symbols
            ]
        try:
            issuer_map = self.hkex_client.resolve_stock_ids(symbols)
        except Exception as exc:
            return [
                SymbolSyncResult("hkexnews", "hk", symbol, "failed", error=str(exc))
                for symbol in symbols
            ]

        results = []
        for symbol in symbols:
            started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            issuer = issuer_map.get(symbol)
            if issuer is None:
                item = SymbolSyncResult("hkexnews", "hk", symbol, "failed", error="stockId not found")
            else:
                try:
                    filings = self.hkex_client.fetch_filings(
                        symbol,
                        issuer["stock_id"],
                        start_date,
                        end_date,
                        include_chinese=True,
                        extract_bodies=extract_bodies,
                    )
                    stats = self.store.upsert_filings(filings)
                    item = SymbolSyncResult(
                        "hkexnews",
                        "hk",
                        symbol,
                        "ok",
                        fetched=len(filings),
                        inserted=stats.inserted,
                        updated=stats.updated,
                        high_value=sum(filing.is_high_value for filing in filings),
                    )
                except Exception as exc:
                    item = SymbolSyncResult("hkexnews", "hk", symbol, "failed", error=str(exc))
            self.store.record_sync_run(
                provider=item.provider,
                market=item.market,
                symbol=item.symbol,
                started_at=started_at,
                status=item.status,
                fetched_count=item.fetched,
                error_message=item.error,
            )
            results.append(item)
        return results

    def _sync_us(self, symbols: List[str], start_date: date, end_date: date) -> List[SymbolSyncResult]:
        if self.sec_client is None:
            return [
                SymbolSyncResult("sec_edgar", "us", symbol, "failed", error="SEC EDGAR client unavailable")
                for symbol in symbols
            ]
        try:
            issuer_map = self.sec_client.resolve_ciks(symbols)
        except Exception as exc:
            return [
                SymbolSyncResult("sec_edgar", "us", symbol, "failed", error=str(exc))
                for symbol in symbols
            ]

        results = []
        for symbol in symbols:
            started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            issuer = issuer_map.get(symbol)
            if issuer is None:
                item = SymbolSyncResult("sec_edgar", "us", symbol, "failed", error="CIK not found")
            else:
                try:
                    filings = self.sec_client.fetch_filings(
                        symbol,
                        issuer["cik"],
                        start_date,
                        end_date,
                    )
                    stats = self.store.upsert_filings(filings)
                    item = SymbolSyncResult(
                        "sec_edgar",
                        "us",
                        symbol,
                        "ok",
                        fetched=len(filings),
                        inserted=stats.inserted,
                        updated=stats.updated,
                        high_value=sum(filing.is_high_value for filing in filings),
                    )
                except Exception as exc:
                    item = SymbolSyncResult("sec_edgar", "us", symbol, "failed", error=str(exc))
            self.store.record_sync_run(
                provider=item.provider,
                market=item.market,
                symbol=item.symbol,
                started_at=started_at,
                status=item.status,
                fetched_count=item.fetched,
                error_message=item.error,
            )
            results.append(item)
        return results
