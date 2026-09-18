"""Deterministic event gates shared by HK, US, and macro events."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Mapping

from .models import GateDecision


ACTION_PRIORITY = {
    "info_only": 0,
    "monitor": 1,
    "reassess": 2,
    "conditional_only": 3,
    "block_new_positions": 4,
    "risk_off": 5,
}

SENSITIVE_MACRO_SECTORS = [
    "科技/半导体",
    "可选消费",
    "房地产/REITs",
    "公用事业",
    "金融",
    "工业",
]


def _as_date(value: object) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _decision(action: str, reason: str, gate_until: str = "", *, scope: str = "symbol", sectors=None) -> GateDecision:
    return GateDecision(
        action=action,
        priority=ACTION_PRIORITY[action],
        reason=reason,
        gate_until=gate_until,
        scope=scope,
        affected_sectors=list(sectors or []),
    )


def evaluate_event_gate(event: Mapping[str, object], as_of: date) -> GateDecision:
    event_type = str(event.get("event_type") or "")
    severity = str(event.get("severity") or "medium")
    certainty = str(event.get("certainty") or "")
    effective = _as_date(event.get("effective_at"))
    published = _as_date(event.get("published_at"))
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}

    if event_type in {"trading_status", "critical_corporate_event"} or severity == "critical":
        until = (as_of + timedelta(days=30)).isoformat()
        return _decision("risk_off", "停牌/退市/破产或其他关键公司事件，暂停交易并人工核验。", until)

    if event_type in {"earnings_release", "earnings_calendar"} and effective:
        days = (effective - as_of).days
        confirmed = certainty in {"confirmed", "official_confirmed"}
        if days < 0:
            if days == -1:
                return _decision("reassess", "业绩事件刚落地，先按结果、指引和首个完整交易日重估。", (effective + timedelta(days=1)).isoformat())
            return _decision("info_only", "业绩日期已过且已超过重估窗口。")
        label = "已确认" if confirmed else "日历预估"
        block_window = 2 if confirmed else 1
        if days <= block_window:
            return _decision("block_new_positions", f"{label}财报进入 {block_window} 天跳空风险窗口，暂停新开仓和加仓。", effective.isoformat())
        if days <= 3:
            return _decision("conditional_only", f"{label}财报距今 {days} 天，仅保留财报前短周期条件计划。", effective.isoformat())
        if days <= 7:
            return _decision("monitor", f"{label}财报位于 4–7 天窗口，保留技术计划并同步执行财报三情景。", effective.isoformat())
        if days <= 14:
            return _decision("info_only", f"{label}财报位于两周内，纳入场景跟踪但不自动降低执行状态。", effective.isoformat())
        return _decision("info_only", f"财报日期距今 {days} 天，暂不触发近期闸门。", effective.isoformat())

    if event_type == "capital_dilution":
        if metadata.get("terms_verified"):
            return _decision("monitor", "融资条款已核验，按实际规模与摊薄比例评估。")
        base = published or as_of
        until = (base + timedelta(days=30)).isoformat()
        if as_of <= base + timedelta(days=30):
            return _decision("conditional_only", "发现配售、供股、S-3/424B/ATM 或可转债文件；条款核验前暂停主动买入。", until)

    if event_type in {"earnings_warning", "audit_change"}:
        base = published or as_of
        until = (base + timedelta(days=14)).isoformat()
        if as_of <= base + timedelta(days=14):
            return _decision("conditional_only", "盈利预警或审计变更处于有效风险窗口，只允许条件观察。", until)

    if event_type == "financial_results" and published:
        age = (as_of - published).days
        if age == 0:
            return _decision("reassess", "正式业绩当日发布，按结果、指引与价格反应重新分析。", published.isoformat())
        if age > 0:
            return _decision("info_only", "正式业绩已进入公布后交易，保留结果作为分析依据但不持续降档。")

    if event_type == "earnings_calendar_notice":
        age = (as_of - published).days if published else None
        if age is None or 0 <= age <= 14:
            return _decision("monitor", "董事会业绩通知已发布；实际日期以独立官方日历事件为准。")
        return _decision("info_only", "董事会业绩通知已超过 14 天观察窗口。")

    if event_type in {"insider_transaction", "planned_insider_sale", "share_buyback", "routine_disclosure"}:
        return _decision("info_only", "内部人交易、Form 144、回购或例行披露默认只作辅助，不自动升级风险。")

    if metadata.get("is_macro") and effective:
        days = (effective - as_of).days
        importance = str(metadata.get("importance") or "medium")
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        if days < 0:
            if importance == "high" and days >= -1 and analysis.get("status") == "released":
                return _decision(
                    "reassess",
                    "高影响宏观数据刚公布，需按实际值相对预期重新评估敏感板块。",
                    (effective + timedelta(days=1)).isoformat(),
                    scope="market",
                    sectors=SENSITIVE_MACRO_SECTORS,
                )
            return _decision("info_only", "宏观数据已公布并保留公布后分析。", scope="market")
        if importance == "high" and days <= 1:
            return _decision(
                "conditional_only",
                "高影响美国宏观数据将在 1 个自然日内公布；敏感板块避免扩大隔夜仓位。",
                effective.isoformat(),
                scope="market",
                sectors=SENSITIVE_MACRO_SECTORS,
            )
        if importance == "high" and days <= 7:
            return _decision(
                "monitor",
                "高影响美国宏观数据位于一周内，交易计划需注明数据意外情景。",
                effective.isoformat(),
                scope="market",
                sectors=SENSITIVE_MACRO_SECTORS,
            )
        if importance == "medium" and days <= 1:
            return _decision(
                "monitor",
                "中等影响宏观数据将在 1 个自然日内公布，相关板块需检查事件暴露。",
                effective.isoformat(),
                scope="market",
            )
        return _decision(
            "info_only",
            f"宏观数据距今 {days} 天，重要度为 {importance}。",
            effective.isoformat(),
            scope="market",
        )

    if severity == "high":
        age = (as_of - published).days if published else None
        if age is None or 0 <= age <= 14:
            return _decision("monitor", "高严重度官方事件处于 14 天观察窗口，需保留来源并人工复核。")
        return _decision("info_only", "高严重度事件已超过 14 天观察窗口，不再持续降档。")
    return _decision("info_only", "事件不触发硬性交易限制。")


def strongest_gate(decisions: Iterable[GateDecision]) -> GateDecision:
    items = list(decisions)
    if not items:
        return _decision("info_only", "没有触发事件闸门。")
    return max(items, key=lambda item: item.priority)
