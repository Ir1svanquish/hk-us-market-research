"""Versioned, validated data contract for every stock in the V2 pool."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from v2.market_foundation import validate_effective_at, validate_trading_date
from v2.earnings_scenarios import build_earnings_scenario
from v2.opportunity_scoring import OpportunityResult, TECHNICAL_WEIGHTS, WEIGHTS
from v2.research_report import ResearchStock


SCHEMA_VERSION = "v2.stock-pool-snapshot.3"
SCORING_VERSION = "public-opportunity-v2-structured-technical"
EXECUTION_VERSION = "execution-maturity-v6-stateful-tiered-confirmation"


def _canonical_symbol(symbol: str, market: str) -> str:
    value = str(symbol or "").strip().upper()
    if market == "hk":
        digits = "".join(character for character in value if character.isdigit())
        return f"HK{digits.zfill(5)}"
    return value.replace(".", "-")


def _lookup(mapping: Mapping[str, Any], symbol: str, market: str) -> Any:
    canonical = _canonical_symbol(symbol, market)
    candidates = [canonical]
    if market == "hk":
        candidates.extend((canonical.removeprefix("HK"), str(int(canonical.removeprefix("HK")))))
    else:
        candidates.append(canonical.replace("-", "."))
    for candidate in candidates:
        if candidate in mapping:
            return mapping[candidate]
    return None


def _number(value: object) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _event_summary(events: Iterable[Mapping[str, Any]], symbol: str, market: str) -> dict:
    canonical = _canonical_symbol(symbol, market)
    selected = []
    invalid_time = []
    session_mappings = []
    sources = set()
    tags = set()
    for event in events:
        raw_symbol = str(event.get("symbol") or "")
        if raw_symbol == "__MARKET__" or _canonical_symbol(raw_symbol, market) != canonical:
            continue
        item = dict(event)
        sources.add(str(item.get("source") or ""))
        for tag in item.get("tags") if isinstance(item.get("tags"), list) else []:
            if isinstance(tag, dict) and tag.get("event_type"):
                tags.add(str(tag["event_type"]))
        effective_at = str(item.get("effective_at") or "")
        if effective_at:
            check = validate_effective_at(effective_at, market)
            if not check["valid"]:
                detail = {
                    "event_type": item.get("event_type"),
                    "headline": item.get("headline"),
                    "effective_at": effective_at,
                    "warnings": check["warnings"],
                }
                if item.get("event_type") == "earnings_calendar":
                    # A scheduled earnings release on a closed exchange date is
                    # suspicious and must be surfaced for review.
                    invalid_time.append(detail)
                else:
                    # Corporate announcements can legitimately be published on
                    # weekends.  They affect the next session rather than being
                    # treated as a bad date.
                    detail["effective_trading_session"] = (
                        (check.get("calendar") or {}).get("next_session")
                    )
                    session_mappings.append(detail)
        selected.append(item)
    return {
        "coverage_status": "covered" if selected else "checked_no_material_event",
        "event_count": len(selected),
        "sources": sorted(source for source in sources if source),
        "tags": sorted(tags),
        "calendar_warnings": invalid_time,
        "non_trading_publication_mappings": session_mappings,
    }


def _volume_payload(metric: Mapping[str, Any], stock: ResearchStock) -> dict:
    contract = metric.get("volume_contract") if isinstance(metric.get("volume_contract"), dict) else {}
    history5 = contract.get("historical_relative_volume_5d", metric.get("relative_volume_5d"))
    confirmation = contract.get("confirmation_value", metric.get("volume_confirmation_value"))
    source = contract.get("confirmation_source", metric.get("volume_confirmation_source"))
    scope = contract.get("confirmation_scope", metric.get("volume_confirmation_scope"))
    if confirmation is None and stock.relative_volume is not None:
        confirmation = stock.relative_volume
        source = stock.relative_volume_source or "analysis_database"
        scope = "completed_session_5d_fallback"
    return {
        "live": {
            "value": contract.get("live_volume_ratio", metric.get("volume_ratio")),
            "applicable_to_report": contract.get("live_applicable", False),
            "scope": "live_intraday",
            "source": metric.get("volume_ratio_source") or "longbridge_calc_index",
            "fetched_at": metric.get("fetched_at"),
        },
        "historical": {
            "relative_volume_5d": history5,
            "relative_volume_20d": contract.get(
                "historical_relative_volume_20d", metric.get("relative_volume_20d")
            ),
            "completed_session_date": contract.get(
                "completed_session_date", metric.get("completed_session_date")
            ),
            "scope": "completed_session",
            "source": "longbridge_daily_candles"
            if metric.get("history_status") == "ok"
            else stock.relative_volume_source or "analysis_database",
        },
        "confirmation": {
            "value": confirmation,
            "source": source or "missing",
            "scope": scope or "missing",
        },
        "turnover_rate": metric.get("turnover_rate") or _number(stock.turnover_rate),
        "warnings": list(contract.get("warnings") or []),
    }


def build_stock_snapshot(
    *,
    stock: ResearchStock,
    report_date: date,
    event_payload: Mapping[str, Any],
    relative_strength: Mapping[str, Any],
    opportunity: OpportunityResult,
    earnings_research: Mapping[str, Any] | None = None,
) -> dict:
    market = stock.market
    symbol = _canonical_symbol(stock.symbol, market)
    metrics = event_payload.get("market_metrics") if isinstance(event_payload.get("market_metrics"), dict) else {}
    quality = event_payload.get("decision_quality") if isinstance(event_payload.get("decision_quality"), dict) else {}
    cards = event_payload.get("trading_cards") if isinstance(event_payload.get("trading_cards"), dict) else {}
    gates = event_payload.get("symbol_gates") if isinstance(event_payload.get("symbol_gates"), dict) else {}
    metric = _lookup(metrics, symbol, market) or {}
    assessment = _lookup(quality, symbol, market) or {}
    card = _lookup(cards, symbol, market) or {}
    gate = _lookup(gates, symbol, market) or {}
    event_contract = _event_summary(event_payload.get("events") or [], symbol, market)
    event_contract["strongest_gate"] = gate
    volume = _volume_payload(metric, stock)
    calendar = asdict(validate_trading_date(report_date, market))
    warnings = list(volume["warnings"])
    warnings.extend(relative_strength.get("warnings") or [])
    if event_contract["calendar_warnings"]:
        warnings.append("部分事件日期/时区未通过交易日校验")
    if not assessment:
        warnings.append("缺少全量质量评估")
    if relative_strength.get("status") == "missing":
        warnings.append("缺少可靠市场相对强弱")

    earnings_scenario = build_earnings_scenario(
        symbol=symbol,
        market=market,
        report_date=report_date,
        earnings_outlook=stock.earnings_outlook,
        events=event_payload.get("events") or [],
        trade_card=card,
        research=earnings_research,
    )

    return {
        "snapshot_id": f"{report_date.isoformat()}:{market}:{symbol}:{SCORING_VERSION}",
        "schema_version": SCHEMA_VERSION,
        "scoring_version": SCORING_VERSION,
        "execution_version": EXECUTION_VERSION,
        "identity": {"symbol": symbol, "name": stock.name, "market": market},
        "report": {
            "date": report_date.isoformat(),
            "calendar": calendar,
            "information_cutoff": (
                event_payload.get("information_cutoff") or {}
            ).get(market),
            "source_action": stock.action,
            "source_score": stock.score,
        },
        "market_data": {
            "current_price": _number(stock.current_price),
            "change_pct": _number(stock.change_pct),
            "amplitude_pct": _number(stock.amplitude),
            "quote_source": stock.quote_source,
            "quote_fetched_at": metric.get("fetched_at"),
            "completed_close": metric.get("completed_close"),
            "atr14": metric.get("atr14"),
            "price_structure": metric.get("price_structure") or {},
            "volume": volume,
        },
        "relative_strength": dict(relative_strength),
        "official_events": event_contract,
        "earnings_scenario": earnings_scenario,
        "quality": {
            "data_completeness": assessment.get("data_completeness"),
            "signal_confidence": assessment.get("signal_confidence"),
            "major_gaps": assessment.get("major_gaps") or [],
            "final_gate": assessment.get("final_gate") or gate,
            "coverage_confirmed": bool(assessment),
        },
        "opportunity": {
            "score": opportunity.opportunity_score,
            "components": opportunity.components,
            "technical_score": opportunity.technical_score,
            "technical_components": opportunity.technical_components,
            "themes": opportunity.themes,
            "primary_catalyst": opportunity.primary_catalyst,
            "primary_risk": opportunity.primary_risk,
        },
        "execution": {
            "maturity_score": opportunity.execution_score,
            "status": opportunity.execution_status,
            "note": opportunity.execution_note,
            "risk_reward": opportunity.risk_reward,
            "standard_trade_card": card,
        },
        "provenance": {
            "market_metrics": metric.get("volume_ratio_source") or metric.get("status") or "missing",
            "official_sources": event_contract["sources"],
            "relative_strength": relative_strength.get("mapping_version") or "missing",
            "legacy_report": stock.data_sources,
        },
        "warnings": list(dict.fromkeys(warnings)),
    }


def build_pool_contract(
    *,
    report_date: date,
    stocks: Sequence[ResearchStock],
    event_payload: Mapping[str, Any],
    relative_strength: Mapping[str, Mapping[str, Any]],
    opportunities: Sequence[OpportunityResult],
    earnings_research: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict:
    earnings_research = earnings_research or {}
    opportunity_map = {item.symbol: item for item in opportunities}
    snapshots = []
    for stock in stocks:
        opportunity = opportunity_map.get(stock.symbol)
        if opportunity is None:
            continue
        snapshots.append(
            build_stock_snapshot(
                stock=stock,
                report_date=report_date,
                event_payload=event_payload,
                relative_strength=relative_strength.get(stock.symbol, {"status": "missing"}),
                opportunity=opportunity,
                earnings_research=_lookup(earnings_research, stock.symbol, stock.market) or {},
            )
        )
    snapshots.sort(
        key=lambda item: (
            item["identity"]["market"],
            -float(item["opportunity"]["score"]),
            item["identity"]["symbol"],
        )
    )
    market_ranks = {"hk": 0, "us": 0}
    for item in snapshots:
        market = item["identity"]["market"]
        market_ranks[market] += 1
        item["opportunity"]["rank"] = market_ranks[market]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "scoring_version": SCORING_VERSION,
        "execution_version": EXECUTION_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "report_date": report_date.isoformat(),
        "information_cutoff": event_payload.get("information_cutoff") or {},
        "opportunity_weights": WEIGHTS,
        "technical_weights": TECHNICAL_WEIGHTS,
        "totals": {
            "stocks": len(snapshots),
            "hk": sum(item["identity"]["market"] == "hk" for item in snapshots),
            "us": sum(item["identity"]["market"] == "us" for item in snapshots),
            "official_quality_coverage": sum(item["quality"]["coverage_confirmed"] for item in snapshots),
            "relative_strength_coverage": sum(
                item["relative_strength"].get("status") in {"ok", "partial"}
                for item in snapshots
            ),
            "volume_confirmation_coverage": sum(
                item["market_data"]["volume"]["confirmation"]["value"] is not None
                for item in snapshots
            ),
            "active_trade_cards": sum(
                bool(item["execution"]["standard_trade_card"].get("active"))
                for item in snapshots
            ),
            "standard_ready_trade_cards": sum(
                item["execution"]["standard_trade_card"].get("execution_tier") == "standard"
                for item in snapshots
            ),
            "cautious_ready_trade_cards": sum(
                item["execution"]["standard_trade_card"].get("execution_tier") == "cautious"
                for item in snapshots
            ),
        },
        "stocks": snapshots,
    }
    validation = validate_pool_contract(payload)
    payload["validation"] = validation
    return payload


def validate_pool_contract(payload: Mapping[str, Any]) -> dict:
    errors = []
    warnings = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version 不匹配")
    if sum((payload.get("opportunity_weights") or {}).values()) != 100:
        errors.append("机会分权重总和不是 100")
    if sum((payload.get("technical_weights") or {}).values()) != 100:
        errors.append("技术分权重总和不是 100")
    stocks = payload.get("stocks") if isinstance(payload.get("stocks"), list) else []
    ids = [str(item.get("snapshot_id") or "") for item in stocks if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("存在重复 snapshot_id")
    required_sections = (
        "identity",
        "report",
        "market_data",
        "relative_strength",
        "official_events",
        "earnings_scenario",
        "quality",
        "opportunity",
        "execution",
        "provenance",
    )
    for item in stocks:
        symbol = ((item.get("identity") or {}).get("symbol") or "unknown")
        missing = [section for section in required_sections if section not in item]
        if missing:
            errors.append(f"{symbol} 缺少字段: {','.join(missing)}")
            continue
        components = item["opportunity"].get("components") or {}
        score = item["opportunity"].get("score")
        if score is None or abs(sum(components.values()) - float(score)) > 0.11:
            errors.append(f"{symbol} 机会分与分项不一致")
        technical_components = item["opportunity"].get("technical_components") or {}
        technical_score = item["opportunity"].get("technical_score")
        if technical_components and (
            technical_score is None
            or abs(sum(technical_components.values()) - float(technical_score)) > 0.11
        ):
            errors.append(f"{symbol} 技术分与分项不一致")
        if item["relative_strength"].get("status") == "missing":
            warnings.append(f"{symbol} 缺少相对强弱")
        if not item["quality"].get("coverage_confirmed"):
            warnings.append(f"{symbol} 缺少质量评估")
    totals = payload.get("totals") or {}
    if totals.get("stocks") != len(stocks):
        errors.append("totals.stocks 与实际数量不一致")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def write_pool_contract(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
