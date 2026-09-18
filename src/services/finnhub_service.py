# -*- coding: utf-8 -*-
"""Finnhub lightweight service for US earnings/recommendation enrichment."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class FinnhubService:
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_keys: Optional[List[str]] = None, timeout: float = 8.0):
        self.api_keys = [k.strip() for k in (api_keys or []) if k and k.strip()]
        self.timeout = max(1.0, float(timeout))
        self._key_index = 0

    @property
    def is_available(self) -> bool:
        return bool(self.api_keys)

    def get_us_earnings_context(self, symbol: str, *, as_of: Optional[date] = None) -> Dict[str, Any]:
        if not self.is_available:
            return {
                "status": "not_supported",
                "data": {},
                "errors": ["finnhub api key missing"],
                "source_chain": [{"provider": "finnhub", "result": "not_supported", "duration_ms": 0}],
            }

        normalized = (symbol or "").strip().upper()
        if not normalized:
            return {
                "status": "failed",
                "data": {},
                "errors": ["invalid symbol"],
                "source_chain": [{"provider": "finnhub", "result": "failed", "duration_ms": 0}],
            }

        start = datetime.utcnow()
        reference_day = as_of or date.today()
        errors: List[str] = []
        data: Dict[str, Any] = {}

        earnings = self._request("stock/earnings", {"symbol": normalized, "limit": 4}, errors)
        if isinstance(earnings, list) and earnings:
            data["earnings_history"] = earnings[:4]
            summary = self._build_earnings_summary(earnings, as_of=reference_day)
            if summary:
                data["earnings_summary"] = summary

        calendar = self._request(
            "calendar/earnings",
            {
                "symbol": normalized,
                "from": reference_day.isoformat(),
                "to": (reference_day + timedelta(days=35)).isoformat(),
            },
            errors,
        )
        if isinstance(calendar, dict):
            calendar_items = calendar.get("earningsCalendar") or []
            if calendar_items:
                data["earnings_calendar"] = calendar_items[:3]
                next_item = calendar_items[0]
                data["next_earnings"] = {
                    "date": next_item.get("date"),
                    "eps_estimate": next_item.get("epsEstimate"),
                    "hour": next_item.get("hour"),
                    "quarter": next_item.get("quarter"),
                    "year": next_item.get("year"),
                }

        recommendations = self._request("stock/recommendation", {"symbol": normalized}, errors)
        if isinstance(recommendations, list) and recommendations:
            latest = recommendations[0]
            prev = recommendations[1] if len(recommendations) > 1 else None
            data["recommendation_trend"] = {
                "period": latest.get("period"),
                "strong_buy": latest.get("strongBuy"),
                "buy": latest.get("buy"),
                "hold": latest.get("hold"),
                "sell": latest.get("sell"),
                "strong_sell": latest.get("strongSell"),
                "change_vs_prev": self._build_recommendation_delta(latest, prev),
            }

        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        status = "ok" if data else ("partial" if errors else "failed")
        source_result = "ok" if data else ("partial" if errors else "failed")
        return {
            "status": status,
            "data": data,
            "errors": errors,
            "source_chain": [{"provider": "finnhub", "result": source_result, "duration_ms": duration_ms}],
        }

    def _request(self, path: str, params: Dict[str, Any], errors: List[str]) -> Any:
        last_error: Optional[str] = None
        for _ in range(len(self.api_keys)):
            key = self._next_key()
            if not key:
                break
            try:
                response = requests.get(
                    f"{self.BASE_URL}/{path}",
                    params={**params, "token": key},
                    timeout=self.timeout,
                )
                if response.status_code == 200:
                    return response.json()
                text = response.text[:240]
                last_error = f"{path} status={response.status_code}: {text}"
                if response.status_code in {401, 403, 429}:
                    continue
                break
            except Exception as exc:
                last_error = f"{path} error: {exc}"
                continue
        if last_error:
            errors.append(last_error)
        return None

    def _next_key(self) -> Optional[str]:
        if not self.api_keys:
            return None
        key = self.api_keys[self._key_index % len(self.api_keys)]
        self._key_index += 1
        return key

    @staticmethod
    def _build_earnings_summary(
        items: List[Dict[str, Any]], *, as_of: Optional[date] = None
    ) -> Dict[str, Any]:
        """Summarize only earnings periods already completed by the cutoff.

        Finnhub occasionally returns future fiscal periods at the front of the
        earnings history response.  Treating that first row as a disclosed
        quarter creates a time-travel statement in the report, so require both
        a parseable period not later than the cutoff and an actual EPS value.
        """
        reference_day = as_of or date.today()
        eligible: List[tuple[date, Dict[str, Any]]] = []
        for item in items:
            try:
                period_day = date.fromisoformat(str(item.get("period") or "")[:10])
            except ValueError:
                continue
            actual = item.get("actual")
            if period_day <= reference_day and actual not in (None, "", "N/A"):
                eligible.append((period_day, item))
        eligible.sort(key=lambda value: value[0], reverse=True)
        selected = eligible[:4]
        if not selected:
            return {}

        surprises: List[float] = []
        positive = 0
        negative = 0
        latest_period = None
        latest_actual = None
        latest_estimate = None
        for idx, (period_day, item) in enumerate(selected):
            try:
                pct = item.get("surprisePercent")
                if pct is not None:
                    surprises.append(float(pct))
                    if float(pct) >= 0:
                        positive += 1
                    else:
                        negative += 1
            except (TypeError, ValueError):
                pass
            if idx == 0:
                latest_period = period_day.isoformat()
                latest_actual = item.get("actual")
                latest_estimate = item.get("estimate")
        avg_surprise = round(sum(surprises) / len(surprises), 2) if surprises else None
        return {
            "latest_period": latest_period,
            "latest_actual": latest_actual,
            "latest_estimate": latest_estimate,
            "positive_surprise_quarters": positive,
            "negative_surprise_quarters": negative,
            "avg_surprise_pct": avg_surprise,
        }

    @staticmethod
    def _build_recommendation_delta(latest: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
        keys = {
            "strong_buy": "strongBuy",
            "buy": "buy",
            "hold": "hold",
            "sell": "sell",
            "strong_sell": "strongSell",
        }
        delta: Dict[str, Optional[int]] = {}
        if not isinstance(prev, dict):
            for out_key in keys:
                delta[out_key] = None
            return delta
        for out_key, src_key in keys.items():
            try:
                delta[out_key] = int(latest.get(src_key, 0)) - int(prev.get(src_key, 0))
            except (TypeError, ValueError):
                delta[out_key] = None
        return delta
