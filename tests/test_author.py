"""作者视图：已对外状态、合集等待项、按授权范围的保密遮蔽。"""
import unittest

from conference.service import STATE_VISIBLE
from tests.support import build_service

T0 = "2027-01-18T10:55:00+08:00"


class AuthorViewTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.store, _ = build_service()

    def test_owner_sees_versions_review_and_waiting_sessions(self):
        view = self.svc.author_view("S1", T0)
        w10 = next(p for p in view["papers"] if p["paper"] == "W-10")
        self.assertTrue(w10["in_scope"])
        versions = {m["manuscript"]: m for m in w10["manuscripts"]}
        self.assertIsNotNone(versions["M-10A"]["review"])
        self.assertEqual(versions["M-10A"]["confidential_reasons"],
                         versions["M-10A"]["review"]["blockers"])
        sessions = {s["session"]: s for s in versions["M-10A"]["sessions"]}
        self.assertIn("G-1", sessions)
        self.assertFalse(sessions["G-1"]["chosen_in_session"])  # 选中的是 M-10B

    def test_non_owner_gets_redacted_unpublished_content(self):
        # S4 不是 W-10 权利人
        view = self.svc.author_view("S4", T0)
        w10 = next(p for p in view["papers"] if p["paper"] == "W-10")
        self.assertFalse(w10["in_scope"])
        for manuscript in w10["manuscripts"]:
            self.assertIsNone(manuscript["abstract_refs"])
            self.assertIsNone(manuscript["extras"])
            self.assertIsNone(manuscript["confidential_reasons"])

    def test_public_flag_tracks_visible_batch(self):
        self.svc.create_batch("K-1", ["M-10B"], T0)
        self.store.update_batch_state("K-1", STATE_VISIBLE, T0)
        view = self.svc.author_view("S1", T0)
        w10 = next(p for p in view["papers"] if p["paper"] == "W-10")
        m10b = next(m for m in w10["manuscripts"] if m["manuscript"] == "M-10B")
        self.assertTrue(m10b["public"])
        self.assertEqual(m10b["index_batches"], ["K-1"])
        m10a = next(m for m in w10["manuscripts"] if m["manuscript"] == "M-10A")
        self.assertFalse(m10a["public"])

    def test_owner_of_piece_but_not_paper_still_redacted(self):
        # S2 拥有 F-1 且是 W-10 共同权利人，能看 W-10；但不是 W-30 权利人
        view = self.svc.author_view("S2", T0)
        w30 = next(p for p in view["papers"] if p["paper"] == "W-30")
        self.assertFalse(w30["in_scope"])
        self.assertIsNone(w30["manuscripts"][0]["extras"])


if __name__ == "__main__":
    unittest.main()
