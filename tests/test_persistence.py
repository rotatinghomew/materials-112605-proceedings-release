"""持久化：重启服务后状态、批组流水与安排记录不丢。"""
import json
import tempfile
import unittest
from pathlib import Path

from conference.domain import load_fixture
from conference.service import ConferenceService
from conference.store import Store
from tests.support import FIXTURE, FakeIndex

T0 = "2027-01-18T10:55:00+08:00"


class PersistenceTests(unittest.TestCase):
    def test_state_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "conf.db"
            data = json.loads(FIXTURE.read_text(encoding="utf-8"))

            store = Store(db_path)
            load_fixture(store, data)
            svc = ConferenceService(store, FakeIndex())
            svc.create_batch("K-1", ["M-10B"], T0)
            svc.dispatch_batch("K-1", T0)
            svc.choose_session_combo("G-1", {"W-10": "M-10B", "W-20": "M-20B"},
                                     note="定档", at=T0)

            # 用同一数据库文件重新开服务
            store2 = Store(db_path)
            svc2 = ConferenceService(store2, None)
            batch = svc2.batch_status("K-1")
            self.assertEqual(batch["state"], "dispatched")
            self.assertEqual(batch["dispatches"][0]["outcome"], "accepted")
            review = svc2.review_session("G-1", T0)
            self.assertEqual([s["chosen"] for s in review["slots"]],
                             ["M-10B", "M-20B"])
            plan = store2.get_plan("G-1")
            self.assertEqual(plan["note"], "定档")

    def test_load_refuses_to_overwrite_populated_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "conf.db"
            data = json.loads(FIXTURE.read_text(encoding="utf-8"))
            store = Store(db_path)
            load_fixture(store, data)
            with self.assertRaises(Exception):
                load_fixture(store, data)

    def test_fixture_session_candidates_must_resolve(self):
        bad = json.loads(FIXTURE.read_text(encoding="utf-8"))
        bad["sessions"][0]["candidates"][0].append("M-GHOST")
        store = Store(":memory:")
        with self.assertRaises(Exception):
            load_fixture(store, bad)


if __name__ == "__main__":
    unittest.main()
