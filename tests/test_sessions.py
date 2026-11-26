"""场次审查：专题整组开放、普通场次分批、调整与移出、决定溯源。"""
import unittest

from conference.errors import Conflict, ValidationFailed
from tests.support import build_service

T0 = "2027-01-18T10:55:00+08:00"


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.store, _ = build_service()

    def test_grouped_session_picks_rights_complete_combo(self):
        review = self.svc.review_session("G-1", T0)
        # 专题需要完整权利组合：M-10A 受阻，自动选已签字的简版 M-10B/M-20B
        self.assertEqual(review["status"], "ready")
        self.assertEqual([s["chosen"] for s in review["slots"]], ["M-10B", "M-20B"])
        self.assertTrue(all(s["chosen_level"] == "full" for s in review["slots"]))

    def test_grouped_session_keeps_approval_records_when_rights_slip(self):
        # 先选定组合
        self.svc.choose_session_combo("G-1", {"W-10": "M-10B", "W-20": "M-20B"},
                                      note="主持人选定", at=T0)
        # 专利办再后延 F-3（M-20B 摘要引用 F-3）
        self.svc.register_embargo("E-9", "F-3", "2027-01-19T09:00:00+08:00",
                                  "2027-01-18T10:56:00+08:00")
        review = self.svc.review_session("G-1", "2027-01-18T10:57:00+08:00")
        # 批准记录仍在，只是按新时钟转为延迟
        self.assertEqual(review["status"], "delayed")
        entries = {e["paper"]: e for e in self.store.entries_of_session("G-1")}
        self.assertEqual(entries["W-10"]["version"], "M-10B")
        self.assertEqual(entries["W-20"]["version"], "M-20B")
        blocked_slot = next(s for s in review["slots"] if s["paper"] == "W-20")
        self.assertEqual(blocked_slot["recoverable_at"],
                         "2027-01-19T09:00:00+08:00")
        decider = next(d for d in review["deciders"] if d.get("embargo") == "E-9")
        self.assertEqual(decider["paper"], "W-20")

    def test_reschedule_entry_follows_session_clock(self):
        self.svc.choose_session_combo("G-1", {"W-10": "M-10B", "W-20": "M-20B"}, at=T0)
        self.svc.reschedule_entry("G-1", "W-20", "2027-01-19T09:00:00+08:00",
                                  "专利时钟后延", at=T0)
        entries = {e["paper"]: e for e in self.store.entries_of_session("G-1")}
        self.assertEqual(entries["W-20"]["moved_publish"],
                         "2027-01-19T09:00:00+08:00")
        self.assertFalse(entries["W-10"]["removed"])

    def test_remove_entry_requires_all_owner_consent(self):
        with self.assertRaises(ValidationFailed):
            self.svc.remove_entry("G-1", "W-10", "时钟后延移出", ["S1"], at=T0)
        review = self.svc.remove_entry("G-1", "W-10", "时钟后延移出",
                                       ["S1", "S2"], at=T0)
        entries = {e["paper"]: e for e in self.store.entries_of_session("G-1")}
        self.assertTrue(entries["W-10"]["removed"])
        # 另一稿批准记录不受影响
        self.assertEqual(review["slots"][1]["chosen"], "M-20B")

    def test_ungrouped_session_can_open_in_batches(self):
        review = self.svc.review_session("G-2", T0)
        self.assertFalse(review["grouped"])
        # M-30A 简版可发、全版等 12:00 时钟：普通场次可先分批开放简版
        self.assertEqual(review["status"], "ready_simplified")
        slot = review["slots"][0]
        self.assertEqual(slot["chosen"], "M-30A")
        self.assertEqual(slot["chosen_level"], "abstract")
        later = self.svc.review_session("G-2", "2027-01-18T12:05:00+08:00")
        self.assertEqual(later["slots"][0]["chosen_level"], "full")

    def test_deciders_trace_to_specific_consent_clock_or_index(self):
        review = self.svc.review_session("G-1", T0)
        embargo_ids = {d.get("embargo") for d in review["deciders"]
                       if d.get("type") == "embargo"}
        self.assertIn("E-3", embargo_ids)
        # 制造一个已公开批组，索引站状态也应进入决定来源
        self.svc.create_batch("K-9", ["M-10B"], T0)
        self.store.update_batch_state("K-9", "visible", T0)
        review2 = self.svc.review_session("G-1", T0)
        self.assertTrue(any(d.get("type") == "index_state" and d["batch"] == "K-9"
                            for d in review2["deciders"]))

    def test_choose_combo_rejects_blocked_version(self):
        with self.assertRaises(Conflict):
            self.svc.choose_session_combo("G-1", {"W-10": "M-10A", "W-20": "M-20B"}, at=T0)

    def test_remove_already_public_entry_refused(self):
        self.svc.create_batch("K-7", ["M-10B"], T0)
        self.store.update_batch_state("K-7", "visible", T0)
        with self.assertRaises(Conflict):
            self.svc.remove_entry("G-1", "W-10", "想移出", ["S1", "S2"], at=T0)


if __name__ == "__main__":
    unittest.main()
