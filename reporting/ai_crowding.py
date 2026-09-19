"""Explainable AI-theme crowding model for the public report.

The model deliberately separates positioning heat from break risk. Broad
participation can support a trend, while weak breadth, macro pressure and a
price/social divergence increase fragility. This avoids treating breadth as
both bullish confirmation and crash risk at the same time.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _scale(value: float | None, low: float, high: float, neutral: float = 50.0) -> float:
    if value is None:
        return neutral
    if high == low:
        return neutral
    return max(0.0, min(100.0, (value - low) / (high - low) * 100.0))


def _average(values: list[float | None], neutral: float = 50.0) -> float:
    valid = [value for value in values if value is not None]
    return mean(valid) if valid else neutral


@dataclass(frozen=True)
class AICrowdingSnapshot:
    crowding_index: int
    break_risk: int
    momentum_heat: int
    social_heat: int
    speculation_heat: int
    concentration_risk: int
    macro_pressure: int
    breadth_health: int
    status: str
    icon: str
    interpretation: str
    signals: list[str]
    risk_lines: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_ai_crowding_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("snapshots") if isinstance(payload, Mapping) else payload
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def save_ai_crowding_history(path: Path, rows: list[Mapping[str, Any]]) -> None:
    normalized: dict[str, dict[str, Any]] = {}
    for row in rows:
        as_of = str(row.get("as_of") or "")[:10]
        if not as_of or not isinstance(row.get("crowding_index"), (int, float)):
            continue
        normalized[as_of] = {
            "as_of": as_of,
            "crowding_index": round(float(row["crowding_index"])),
            "break_risk": round(float(row.get("break_risk") or 0)),
            "status": str(row.get("status") or ""),
        }
    cutoff = date.today() - timedelta(days=370)
    kept = [
        row
        for day, row in sorted(normalized.items())
        if date.fromisoformat(day) >= cutoff
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": "ai-crowding-history-v1", "snapshots": kept}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def enrich_ai_crowding_trend(
    snapshot: Mapping[str, Any],
    history: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Attach previous-session, five-day and trailing-year comparisons."""
    result = dict(snapshot)
    as_of_text = str(result.get("as_of") or "")[:10]
    try:
        as_of = date.fromisoformat(as_of_text)
        score = float(result["crowding_index"])
    except (ValueError, TypeError, KeyError):
        return result

    rows: dict[date, float] = {}
    for row in history:
        try:
            day = date.fromisoformat(str(row.get("as_of") or "")[:10])
            value = float(row["crowding_index"])
        except (ValueError, TypeError, KeyError):
            continue
        if as_of - timedelta(days=365) <= day <= as_of:
            rows[day] = value
    rows[as_of] = score
    ordered = sorted(rows.items())
    prior = [item for item in ordered if item[0] < as_of]
    previous = prior[-1] if prior else None
    five_day_candidates = [item for item in prior if item[0] <= as_of - timedelta(days=5)]
    five_day = five_day_candidates[-1] if five_day_candidates else None

    result.update(
        {
            "change_1d": round(score - previous[1]) if previous else None,
            "comparison_1d_date": previous[0].isoformat() if previous else "",
            "change_5d": round(score - five_day[1]) if five_day else None,
            "comparison_5d_date": five_day[0].isoformat() if five_day else "",
            "percentile_1y": round(sum(value <= score for _, value in ordered) / len(ordered) * 100),
            "history_samples": len(ordered),
        }
    )
    return result


def score_ai_crowding(raw: Mapping[str, Any]) -> AICrowdingSnapshot:
    layers = raw.get("layers") if isinstance(raw.get("layers"), Mapping) else {}
    leader = layers.get("leaders") if isinstance(layers.get("leaders"), Mapping) else {}
    platform = layers.get("platforms") if isinstance(layers.get("platforms"), Mapping) else {}
    high_beta = layers.get("high_beta") if isinstance(layers.get("high_beta"), Mapping) else {}
    layer_rows = [leader, platform, high_beta]

    excess = [_number(row.get("avg_excess_vs_qqq_20d")) for row in layer_rows]
    returns_5d = [_number(row.get("avg_return_5d")) for row in layer_rows]
    breadth20 = [_number(row.get("above_ma20_ratio")) for row in layer_rows]
    breadth50 = [_number(row.get("above_ma50_ratio")) for row in layer_rows]
    social = [_number(row.get("social_heat_avg")) for row in layer_rows]

    avg_excess = _average(excess, 0.0)
    avg_return_5d = _average(returns_5d, 0.0)
    breadth20_pct = _average(breadth20, 0.5) * 100
    breadth50_pct = _average(breadth50, 0.5) * 100
    social_heat = _average(social, 50.0)

    momentum_heat = 0.55 * _scale(avg_excess, -5.0, 15.0) + 0.45 * breadth20_pct
    high_beta_excess = _number(high_beta.get("avg_excess_vs_qqq_20d"))
    high_beta_return = _number(high_beta.get("avg_return_5d"))
    high_beta_social = _number(high_beta.get("social_heat_avg"))
    speculation_heat = (
        0.55 * _scale(high_beta_excess, -5.0, 15.0)
        + 0.20 * _scale(high_beta_return, -5.0, 8.0)
        + 0.25 * (high_beta_social if high_beta_social is not None else social_heat)
    )

    valid_excess = [value for value in excess if value is not None]
    dispersion = pstdev(valid_excess) if len(valid_excess) >= 2 else 0.0
    concentration_risk = 0.50 * _scale(dispersion, 0.0, 15.0) + 0.50 * (100 - breadth50_pct)

    macro = raw.get("macro") if isinstance(raw.get("macro"), Mapping) else {}
    tnx = _number(macro.get("TNX"))
    vix = _number((macro.get("VIX") or {}).get("price")) if isinstance(macro.get("VIX"), Mapping) else None
    hyg = _number((macro.get("HYG") or {}).get("change_pct")) if isinstance(macro.get("HYG"), Mapping) else None
    lqd = _number((macro.get("LQD") or {}).get("change_pct")) if isinstance(macro.get("LQD"), Mapping) else None
    credit_gap = (lqd - hyg) if lqd is not None and hyg is not None else 0.0
    macro_pressure = (
        0.55 * _scale(tnx, 4.0, 4.8)
        + 0.25 * _scale(vix, 12.0, 25.0)
        + 0.20 * _scale(credit_gap, -0.25, 0.50)
    )

    crowding = (
        0.30 * momentum_heat
        + 0.25 * social_heat
        + 0.25 * speculation_heat
        + 0.20 * concentration_risk
    )
    reversal = _scale(-avg_return_5d, 0.0, 7.0)
    social_price_divergence = _scale(social_heat - momentum_heat, 0.0, 40.0)
    stress_signal = max(reversal, social_price_divergence)
    break_risk = (
        0.35 * macro_pressure
        + 0.25 * concentration_risk
        + 0.20 * (100 - breadth50_pct)
        + 0.20 * stress_signal
    )

    crowding_i = round(crowding)
    break_i = round(break_risk)
    if crowding_i >= 65 and break_i >= 65:
        status, icon = "高位拥挤·回撤脆弱", "⚠"
        interpretation = "中期热度仍高，但短线价格转弱且宏观压力偏大；不宜把回调自动视为低风险买点。"
    elif crowding_i >= 65:
        status, icon = "拥挤升温", "🔥"
        interpretation = "AI 主线热度与投机扩散都偏高，趋势未坏但追涨性价比下降。"
    elif break_i >= 65:
        status, icon = "结构转弱", "↘"
        interpretation = "热度并非极端，但广度、价格或宏观环境正在提高回撤风险。"
    elif crowding_i >= 45:
        status, icon = "正常偏热", "◉"
        interpretation = "主线仍有关注度，尚未达到全面泡沫化；重点观察扩散质量与利率压力。"
    else:
        status, icon = "降温修复", "❄"
        interpretation = "交易热度处于低位或修复期，需等待趋势与资金重新形成共振。"

    signals = [
        f"20日相对强度热度 {round(momentum_heat)}/100",
        f"社媒热度 {round(social_heat)}/100",
        f"高弹性投机热度 {round(speculation_heat)}/100",
        f"长期广度健康度 {round(breadth50_pct)}/100",
    ]
    risk_lines = list(raw.get("risk_triggers") or [])[:4]
    return AICrowdingSnapshot(
        crowding_index=crowding_i,
        break_risk=break_i,
        momentum_heat=round(momentum_heat),
        social_heat=round(social_heat),
        speculation_heat=round(speculation_heat),
        concentration_risk=round(concentration_risk),
        macro_pressure=round(macro_pressure),
        breadth_health=round(breadth50_pct),
        status=status,
        icon=icon,
        interpretation=interpretation,
        signals=signals,
        risk_lines=risk_lines,
    )
