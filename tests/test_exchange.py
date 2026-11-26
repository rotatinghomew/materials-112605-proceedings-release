"""核对交换资料中的实体引用与原始回执顺序。"""
from datetime import datetime
import json
from pathlib import Path
import unittest

from partner import parse_time as time

ROOT = Path(__file__).resolve().parents[1]


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "exchange.json").read_text(encoding="utf-8"))

    def test_record_identity(self):
        for name, collection in self.data.items():
            with self.subTest(collection=name):
                ids = [rec["id"] for rec in collection]
                self.assertTrue(ids)
                self.assertTrue(all(isinstance(i, str) and i for i in ids))
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(set(x) == set(collection[0]) for x in collection))

    def test_receipt_clocks(self):
        for rec in self.data["receipts"]:
            with self.subTest(receipt=rec["id"]):
                self.assertLessEqual(time(rec["happened"]), time(rec["arrived"]))
        with self.assertRaises(ValueError):
            time(datetime(2027, 1, 18).isoformat())

    def test_related_records(self):
        def row(collection, key):
            return next(rec for rec in self.data[collection] if rec["id"] == key)
        assert "F-1" in row("manuscripts","M-10A")["abstract_refs"]
        assert "F-1" in row("manuscripts","M-20A")["extras"]
        assert "F-1" not in row("manuscripts","M-10B")["abstract_refs"]
        assert row("consents","S-2B")["version"] == "M-10B"
        assert row("embargo","E-3")["content"] == row("embargo","E-1")["content"]
        before, changed = row("receipts","IDX-1"), row("embargo","E-3")
        after = row("receipts","IDX-2")
        assert after["batch"] == before["batch"] and after["versions"] == before["versions"]
        assert time(before["happened"]) < time(changed["received"]) < time(after["happened"]) < time(after["arrived"])
