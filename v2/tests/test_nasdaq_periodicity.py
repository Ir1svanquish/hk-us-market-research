from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from v2.official_sources.nasdaq_calendar import NasdaqMacroCalendarClient


class _Response:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"asOf": "Thu, Sep 10, 2026", "rows": self.rows}}


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.rows)


class NasdaqPeriodicityTest(unittest.TestCase):
    def test_duplicate_ppi_rows_are_labeled_and_preserved(self):
        rows = [
            {
                "gmt": "08:30",
                "country": "United States",
                "eventName": "Core PPI",
                "actual": "0.2%",
                "consensus": "0.3%",
                "previous": "0.3%",
            },
            {
                "gmt": "08:30",
                "country": "United States",
                "eventName": "Core PPI",
                "actual": "4.6%",
                "consensus": "4.6%",
                "previous": "4.3%",
            },
            {
                "gmt": "08:30",
                "country": "United States",
                "eventName": "PPI",
                "actual": "0.4%",
                "consensus": "0.4%",
                "previous": "0.1%",
            },
            {
                "gmt": "08:30",
                "country": "United States",
                "eventName": "PPI",
                "actual": "5.4%",
                "consensus": "5.3%",
                "previous": "4.8%",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _Session(rows)
            releases = NasdaqMacroCalendarClient(
                Path(temp_dir), session=session, max_workers=1
            ).fetch_releases(date(2026, 9, 10), date(2026, 9, 10))

        by_title = {item.title: item for item in releases}
        self.assertEqual(len(releases), 4)
        self.assertEqual(by_title["PPI (MoM)"].actual, "0.4%")
        self.assertEqual(by_title["PPI (YoY)"].actual, "5.4%")
        self.assertEqual(by_title["Core PPI (MoM)"].actual, "0.2%")
        self.assertEqual(by_title["Core PPI (YoY)"].actual, "4.6%")
        self.assertEqual(by_title["PPI (MoM)"].unit, "MoM")
        self.assertEqual(by_title["PPI (YoY)"].unit, "YoY")
        self.assertEqual(session.calls[0][1]["params"]["date"], "2026-09-11")

    def test_ambiguous_duplicate_ppi_rows_are_not_guessed(self):
        rows = [
            {"country": "United States", "gmt": "08:30", "eventName": "PPI", "actual": "0.2%"},
            {"country": "United States", "gmt": "08:30", "eventName": "PPI", "actual": "0.4%"},
        ]
        self.assertEqual(NasdaqMacroCalendarClient._periodicity_labels(rows), {})


if __name__ == "__main__":
    unittest.main()
