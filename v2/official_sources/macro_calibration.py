"""Historical calibration for macro surprise scenario probabilities."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

from .bls_calendar import MacroRelease
from .macro_scenarios import scenarios_for


OUTCOMES = ("above", "inline", "below")


def _group_outcome(values: Iterable[str]) -> Optional[str]:
    """Return a majority outcome; mixed/tied batches are intentionally excluded."""
    counts = Counter(value for value in values if value in OUTCOMES)
    if not counts:
        return None
    top = max(counts.values())
    winners = [outcome for outcome in OUTCOMES if counts[outcome] == top]
    return winners[0] if len(winners) == 1 else None


def _counts_for_groups(groups: Mapping[tuple, Sequence[MacroRelease]]) -> dict:
    totals = defaultdict(lambda: [0, 0, 0])
    excluded = defaultdict(int)
    for key, releases in groups.items():
        label = str(key[-1])
        outcome = _group_outcome(
            str(item.analysis.get("comparison") or "")
            for item in releases
            if item.analysis.get("comparison_basis") == "consensus"
        )
        if outcome is None:
            excluded[label] += 1
            continue
        totals[label][OUTCOMES.index(outcome)] += 1
    return {
        label: {
            "counts": counts,
            "sample_size": sum(counts),
            "excluded_mixed_or_missing": excluded.get(label, 0),
        }
        for label, counts in sorted(totals.items())
    }


def build_calibration(
    releases: Iterable[MacroRelease], *, as_of: date, lookback_days: int
) -> dict:
    """Build exact-series and broader-category surprise histories.

    Multiple sub-series released at the same date/time are counted as one batch so
    CPI MoM/YoY or a survey's many components do not inflate the sample size.
    """
    family_groups = defaultdict(list)
    category_groups = defaultdict(list)
    released_count = 0
    for item in releases:
        if item.analysis.get("status") != "released":
            continue
        released_count += 1
        batch = (item.release_date, item.release_time)
        family_groups[(*batch, item.event_type)].append(item)
        category_groups[(*batch, item.category)].append(item)
    return {
        "version": 1,
        "as_of": as_of.isoformat(),
        "lookback_days": max(1, int(lookback_days)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "nasdaq_macro_calendar",
        "outcome_definition": "actual_vs_consensus; 0.5pct tolerance; mixed/tied release batches excluded",
        "released_rows": released_count,
        "family": _counts_for_groups(family_groups),
        "category": _counts_for_groups(category_groups),
    }


def save_calibration(payload: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_calibration(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def select_history(
    calibration: Mapping[str, object],
    event_type: str,
    category: str,
    *,
    min_samples: int = 12,
) -> Tuple[Optional[List[int]], str, int]:
    """Prefer exact series, then category, then the neutral prior."""
    for level, key in (("family", event_type), ("category", category)):
        table = calibration.get(level)
        item = table.get(key) if isinstance(table, dict) else None
        counts = item.get("counts") if isinstance(item, dict) else None
        if (
            isinstance(counts, list)
            and len(counts) == 3
            and sum(max(0, int(value)) for value in counts) >= min_samples
        ):
            normalized = [max(0, int(value)) for value in counts]
            return normalized, f"historical_{level}", sum(normalized)
    return None, "neutral_prior", 0


def apply_calibration(
    releases: Iterable[MacroRelease],
    calibration: Mapping[str, object],
    *,
    min_samples: int = 12,
) -> List[MacroRelease]:
    result = []
    for item in releases:
        counts, level, sample_size = select_history(
            calibration, item.event_type, item.category, min_samples=min_samples
        )
        calibrated = scenarios_for(item.event_type, item.category, counts)
        for scenario in calibrated:
            scenario["probability_calibration_level"] = level
            scenario["probability_sample_size"] = sample_size
        result.append(replace(item, scenarios=calibrated))
    return result

