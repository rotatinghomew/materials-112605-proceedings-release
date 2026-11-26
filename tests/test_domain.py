"""公开审查：签认范围、专利时钟、摘要引用阻断与逐版独立。"""
import unittest

from conference.errors import Conflict, ValidationFailed
from conference.service import (STATE_DISPATCHED, STATE_VISIBLE)
from tests.support import build_service

T0 = "2027-01-18T10:55:00+08:00"   # E-3（10:55 收到的时钟变更）之后
T1 = "2027-01-18T16:30:00+08:00"   # 全部专利时钟解除之后


class ClearanceTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.store, _ = build_service()

    def test_extended_embargo_blocks_full_version_through_abstract_ref(self):
        # F-1 的时钟在 10:55 被 E-3 改到 16:00；M-10A 摘要引用 F-1，整版阻断
        review = self.svc.review_manuscript("M-10A", T0)
        self.assertEqual(review["level"], "blocked")
        self.assertTrue(any(b["type"] == "embargo" and b["embargo"] == "E-3"
                            for b in review["blockers"]))
        # 摘要引用不能公开的结果时，不能把旧版当无图版本发
        with self.assertRaises(Conflict):
            self.svc.create_batch("K-X", ["M-10A"], T0)

    def test_signed_simplified_version_releases_independently(self):
        # M-10B 无引用、双方签 abstract：简版即全量可发，不受 M-10A 影响
        review = self.svc.review_manuscript("M-10B", T0)
        self.assertEqual(review["level"], "full")
        self.assertEqual(review["blockers"], [])

    def test_one_papers_abstract_change_does_not_revoke_other_approval(self):
        # W-10 与 W-20 共享补充件 F-1；W-10 换简版不影响 W-20 已批准的 M-20B
        review_20b = self.svc.review_manuscript("M-20B", T0)
        self.assertEqual(review_20b["level"], "full")
        # 新登记 W-10 的第三版也不改变 M-20B 的判定
        self.svc.submit_manuscript("M-10C", "W-10", 3, [], [])
        self.svc.register_consent("S-1C", "M-10C", "S1", ["abstract"], T0)
        self.svc.register_consent("S-2C2", "M-10C", "S2", ["abstract"], T0)
        self.assertEqual(self.svc.review_manuscript("M-20B", T0)["level"], "full")

    def test_embargo_governed_by_latest_received_change(self):
        # 10:54 时 E-3 尚未收到，F-1 仍按 E-1 的 11:00；M-20A 摘要不引用 F-1，
        # 只有补充件受阻 => 可发简版
        early = "2027-01-18T10:54:00+08:00"
        review = self.svc.review_manuscript("M-20A", early)
        self.assertEqual(review["level"], "abstract")
        # 10:55 之后 E-3 生效（16:00），简版仍可发，全版推迟
        review2 = self.svc.review_manuscript("M-20A", T0)
        self.assertEqual(review2["level"], "abstract")
        self.assertEqual(review2["full_ready_at"], "2027-01-18T16:00:00+08:00")

    def test_missing_piece_owner_signature_blocks(self):
        # F-2 权利人为 S1+S2；构造一个只有 S1 签 F-2 的版本
        self.svc.submit_manuscript("M-30B", "W-30", 2, [], ["F-2"])
        self.svc.register_consent("S-4B", "M-30B", "S4", ["abstract"], T0)
        # S1/S2 并非 W-30 权利人，不能替它签 —— 校验先挡住
        with self.assertRaises(ValidationFailed):
            self.svc.register_consent("S-x", "M-30B", "S1", ["abstract", "F-2"], T0)

    def test_consent_scope_must_stay_within_manuscript_refs(self):
        with self.assertRaises(ValidationFailed):
            self.svc.register_consent("S-bad", "M-10B", "S1", ["F-1"], T0)

    def test_version_number_unique_per_paper(self):
        with self.assertRaises(Conflict):
            self.svc.submit_manuscript("M-dup", "W-10", 1, [], [])

    def test_after_all_clocks_expire_full_release(self):
        for mid in ["M-10A", "M-20A", "M-30A"]:
            self.assertIn(self.svc.review_manuscript(mid, T1)["level"],
                          ("full", "abstract"))
        self.assertEqual(self.svc.review_manuscript("M-30A", T1)["level"], "full")

    def test_shared_extras_governed_per_version_consent(self):
        # M-20A 的 F-1 由 S2 在该版本签认；M-10A 的 F-1 由 S2 在另一版本签认，互不替代
        a = self.svc.review_manuscript("M-10A", T0)
        f1 = next(p for p in a["abstract_refs"] if p["piece"] == "F-1")
        self.assertEqual(f1["authorizing_consents"], ["S-2A"])
        self.assertFalse(f1["releasable_now"])  # 时钟未解除，授权本身齐备

    def test_embargo_update_registration_takes_effect(self):
        # 专利办再发一条把 F-4 提前到 11:30
        self.svc.register_embargo("E-5", "F-4", "2027-01-18T11:30:00+08:00",
                                  "2027-01-18T11:00:00+08:00")
        review = self.svc.review_manuscript("M-30A", "2027-01-18T11:45:00+08:00")
        self.assertEqual(review["level"], "full")
        # 时钟 11:30 已过，就绪时刻不早于评估时刻
        self.assertEqual(review["full_ready_at"], "2027-01-18T11:45:00+08:00")
        # 时钟未到的评估点上，就绪时刻就是时钟本身
        before = self.svc.review_manuscript("M-30A", "2027-01-18T11:10:00+08:00")
        self.assertEqual(before["full_ready_at"], "2027-01-18T11:30:00+08:00")


if __name__ == "__main__":
    unittest.main()
