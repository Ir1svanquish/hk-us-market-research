import json
import tempfile
import unittest
from pathlib import Path

from reporting.full_pool_pipeline import load_earnings_research


class EarningsResearchSnapshotTest(unittest.TestCase):
    def test_child_snapshot_extends_parent_and_overrides_symbol(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            (root / "base.json").write_text(
                json.dumps(
                    {
                        "stocks": {
                            "HK00001": {"period": "base"},
                            "HK00002": {"period": "kept"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "child.json").write_text(
                json.dumps(
                    {
                        "extends": "base.json",
                        "stocks": {"HK00001": {"period": "child"}},
                    }
                ),
                encoding="utf-8",
            )

            merged = load_earnings_research(root / "child.json")

        self.assertEqual(merged["HK00001"]["period"], "child")
        self.assertEqual(merged["HK00002"]["period"], "kept")


if __name__ == "__main__":
    unittest.main()
