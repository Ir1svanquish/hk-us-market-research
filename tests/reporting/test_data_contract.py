from __future__ import annotations

import unittest

from reporting.data_contract import SCHEMA_VERSION, validate_pool_contract
from reporting.opportunity_scoring import TECHNICAL_WEIGHTS, WEIGHTS


class DataContractTests(unittest.TestCase):
    def test_contract_rejects_duplicate_snapshots_and_score_drift(self):
        stock = {
            "snapshot_id": "same",
            "identity": {"symbol": "TEST", "market": "us"},
            "report": {},
            "market_data": {},
            "relative_strength": {"status": "ok"},
            "official_events": {},
            "earnings_scenario": {"status": "not_applicable"},
            "quality": {"coverage_confirmed": True},
            "opportunity": {"score": 10, "components": {"a": 9}},
            "execution": {},
            "provenance": {},
        }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "opportunity_weights": WEIGHTS,
            "technical_weights": TECHNICAL_WEIGHTS,
            "totals": {"stocks": 2},
            "stocks": [stock, dict(stock)],
        }
        result = validate_pool_contract(payload)
        self.assertFalse(result["valid"])
        self.assertTrue(any("重复" in item for item in result["errors"]))
        self.assertTrue(any("机会分" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
