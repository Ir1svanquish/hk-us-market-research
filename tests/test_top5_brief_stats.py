from __future__ import annotations

from pathlib import Path

from src.analyzer import AnalysisResult
from src.top5_brief_stats import build_top5_brief_lines
import src.top5_brief_stats as top5_stats
import pandas as pd


def _result(code: str, score: int = 60) -> AnalysisResult:
    return AnalysisResult(
        code=code,
        name=code,
        sentiment_score=score,
        trend_prediction="震荡",
        operation_advice="观望",
        current_price=100.0,
    )


def test_build_top5_brief_lines_tracks_frequency_streak_and_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(top5_stats, "STATE_PATH", Path(tmp_path) / "top5_brief_stats.json")

    day1 = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "MU", "RKLB")]
    day2 = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "ASTS", "RKLB")]
    day3 = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "TE", "RKLB")]

    build_top5_brief_lines("us", day1, report_date="2026-06-12")
    build_top5_brief_lines("us", day2, report_date="2026-06-13")
    lines = build_top5_brief_lines("us", day3, report_date="2026-06-14")

    assert lines[0] == "近3期Top5高频：暂无"
    assert lines[1] == "连续上榜：NVDA(3) / PLTR(3) / CRWV(3)"


def test_build_top5_brief_lines_replaces_same_day_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(top5_stats, "STATE_PATH", Path(tmp_path) / "top5_brief_stats.json")

    first = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "MU", "RKLB")]
    second = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "TE", "RKLB")]

    build_top5_brief_lines("us", first, report_date="2026-06-14")
    lines = build_top5_brief_lines("us", second, report_date="2026-06-14")

    assert lines[0] == "近1期Top5高频：暂无"
    assert lines[1] == "连续上榜：暂无"


def test_build_top5_brief_lines_highlights_repeaters_over_ten_periods(tmp_path, monkeypatch):
    monkeypatch.setattr(top5_stats, "STATE_PATH", Path(tmp_path) / "top5_brief_stats.json")

    schedules = [
        ("2026-06-01", ("NVDA", "PLTR", "CRWV", "MU", "RKLB")),
        ("2026-06-02", ("NVDA", "PLTR", "CRWV", "ASTS", "RKLB")),
        ("2026-06-03", ("NVDA", "PLTR", "CRWV", "TE", "RKLB")),
        ("2026-06-04", ("NVDA", "PLTR", "CRWV", "NOK", "RKLB")),
        ("2026-06-05", ("NVDA", "PLTR", "CRWV", "MU", "RKLB")),
        ("2026-06-06", ("NVDA", "PLTR", "CRWV", "ASTS", "RKLB")),
        ("2026-06-07", ("NVDA", "PLTR", "CRWV", "TE", "RKLB")),
        ("2026-06-08", ("NVDA", "PLTR", "CRWV", "NOK", "RKLB")),
        ("2026-06-09", ("NVDA", "PLTR", "CRWV", "MU", "RKLB")),
        ("2026-06-10", ("NVDA", "PLTR", "CRWV", "ASTS", "RKLB")),
    ]

    lines = []
    for report_date, codes in schedules:
        results = [_result(code) for code in codes]
        lines = build_top5_brief_lines("us", results, report_date=report_date)

    assert lines[0] == "近10期Top5高频：NVDA(10) / PLTR(10) / CRWV(10)"
    assert lines[1] == "连续上榜：NVDA(10) / PLTR(10) / CRWV(10)"


def test_build_top5_brief_lines_adds_cumulative_change_for_repeaters(tmp_path, monkeypatch):
    monkeypatch.setattr(top5_stats, "STATE_PATH", Path(tmp_path) / "top5_brief_stats.json")

    def _fake_load_history_df(code: str, days: int = 60, target_date=None):
        df = pd.DataFrame(
            [
                {"date": "2026-06-12", "close": 100.0},
                {"date": "2026-06-13", "close": 105.0},
                {"date": "2026-06-14", "close": 110.0},
            ]
        )
        return df, "db_cache"

    monkeypatch.setattr("src.services.history_loader.load_history_df", _fake_load_history_df)

    day1 = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "MU", "RKLB")]
    day2 = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "ASTS", "RKLB")]
    day3 = [_result(code) for code in ("NVDA", "PLTR", "CRWV", "TE", "RKLB")]
    for result in day3:
        if result.code == "NVDA":
            result.current_price = 120.0
        elif result.code == "PLTR":
            result.current_price = 90.0
        elif result.code == "CRWV":
            result.current_price = 105.0

    build_top5_brief_lines("us", day1, report_date="2026-06-12")
    build_top5_brief_lines("us", day2, report_date="2026-06-13")
    lines = build_top5_brief_lines("us", day3, report_date="2026-06-14")

    assert lines[2] == "高频股胜率：25.0%（1/4）｜平均累计涨跌（次日收盘口径）：-1.2%"
    assert lines[3] == "首次上榜至今（次日收盘口径）：NVDA(首次 2026-06-12, +14.3%) / PLTR(首次 2026-06-12, -14.3%) / CRWV(首次 2026-06-12, +0.0%)"
