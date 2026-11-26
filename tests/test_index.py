"""索引站批组：暂存/开放不可互换、无回话歧义、重发幂等、版本不覆盖、勘误撤回。"""
import unittest

from conference.errors import Conflict, Unavailable
from conference.service import (STATE_DISPATCHED, STATE_PREPARED, STATE_STAGED,
                                STATE_STOPPED, STATE_STOP_REQUESTED, STATE_VISIBLE,
                                STATE_WITHDRAWN)
from tests.support import build_service

T0 = "2027-01-18T10:55:00+08:00"


def receipt(rid, batch, phase, versions, grants, happened, arrived):
    return {"id": rid, "batch": batch, "phase": phase, "versions": versions,
            "grant_ids": grants, "happened": happened, "arrived": arrived}


class IndexBatchTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.store, _ = build_service()

    def test_create_batch_rejects_blocked_old_version(self):
        # M-10A 摘要引用受阻的 F-1，不能作为无图版本发出
        with self.assertRaises(Conflict):
            self.svc.create_batch("K-1", ["M-10A"], T0)

    def test_dispatch_no_reply_is_distinguished_and_resend_keeps_one_id(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex(fail_mode="drop")
        self.svc.create_batch("K-1", ["M-10B"], T0)
        # 第一次：请求可能已到对手方但没有回话
        with self.assertRaises(Unavailable):
            self.svc.dispatch_batch("K-1", T0)
        self.assertEqual(self.store.get_batch("K-1")["state"], STATE_DISPATCHED)
        # 重发沿用同一批号，不产生第二个批组/索引
        self.svc.index.fail_mode = None
        batch = self.svc.dispatch_batch("K-1", T0)
        self.assertEqual(batch["id"], "K-1")
        self.assertEqual(len([d for d in self.store.dispatches_of_batch("K-1")]), 2)
        self.assertEqual({p["batch"] for p in self.svc.index.received}, {"K-1"})

    def test_overlapping_version_in_second_batch_refused(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex()
        self.svc.create_batch("K-1", ["M-10B", "M-20B"], T0)
        self.svc.dispatch_batch("K-1", T0)
        with self.assertRaises(Conflict) as ctx:
            self.svc.create_batch("K-2", ["M-10B"], T0)
        self.assertIn("M-10B", ctx.exception.details["overlap"])

    def test_reconcile_staged_then_visible_with_grant_ids(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex(receipts=[
            receipt("R-1", "K-1", "staged", ["M-10B"], [],
                    "2027-01-18T10:56:00+08:00", "2027-01-18T10:56:00+08:00"),
            receipt("R-2", "K-1", "visible", ["M-10B"], ["INDEX-9"],
                    "2027-01-18T11:05:00+08:00", "2027-01-18T11:12:00+08:00"),
        ])
        self.svc.create_batch("K-1", ["M-10B"], T0)
        self.svc.reconcile(T0)
        self.assertEqual(self.store.get_batch("K-1")["state"], STATE_VISIBLE)
        status = self.svc.batch_status("K-1")
        self.assertEqual(status["receipts"][-1]["grant_ids"], ["INDEX-9"])
        # 已公开时间仍可查证（happened 是业务时间）
        self.assertEqual(status["receipts"][-1]["happened"],
                         "2027-01-18T11:05:00+08:00")

    def test_open_confirmation_lagging_rights_change_does_not_unpublish(self):
        from tests.support import FakeIndex
        # 批组在权利变更前已暂存；回执晚到，且本地时钟已后延
        self.svc.index = FakeIndex(receipts=[
            receipt("R-1", "K-1", "staged", ["M-20A"], [],
                    "2027-01-18T10:54:00+08:00", "2027-01-18T10:54:00+08:00"),
            receipt("R-2", "K-1", "visible", ["M-20A"], ["INDEX-8"],
                    "2027-01-18T11:05:00+08:00", "2027-01-18T11:20:00+08:00"),
        ])
        self.svc.create_batch("K-1", ["M-20A"], "2027-01-18T10:53:00+08:00")
        self.svc.reconcile("2027-01-18T11:25:00+08:00")
        # 已公开就是已公开：状态不回退，编辑只能发勘误/撤回
        self.assertEqual(self.store.get_batch("K-1")["state"], STATE_VISIBLE)
        with self.assertRaises(Conflict):
            self.svc.stop_batch("K-1", "想收回", T0)

    def test_stop_pending_batch_before_it_takes_effect(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex()
        self.svc.create_batch("K-2", ["M-10B"], T0)
        self.svc.dispatch_batch("K-2", T0)
        self.svc.stop_batch("K-2", "权利变更，暂停", T0)
        self.assertEqual(self.store.get_batch("K-2")["state"], STATE_STOP_REQUESTED)
        # 索引站确认未生效（staged 后撤回）：批组转为停止，版本可另组新批
        self.svc.index.receipts.append(
            receipt("R-3", "K-2", "withdrawn", ["M-10B"], [],
                    "2027-01-18T11:08:00+08:00", "2027-01-18T11:18:00+08:00"))
        self.svc.reconcile(T0)
        # withdrawn 是终态；停止语义下另建新批不冲突
        self.assertIn(self.store.get_batch("K-2")["state"],
                      (STATE_WITHDRAWN, STATE_STOPPED))
        self.svc.create_batch("K-3", ["M-10B"], T0)

    def test_stop_request_then_visible_receipt_wins(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex()
        self.svc.create_batch("K-2", ["M-10B"], T0)
        self.svc.dispatch_batch("K-2", T0)
        self.svc.stop_batch("K-2", "尝试停止", T0)
        self.svc.index.receipts.append(
            receipt("R-4", "K-2", "visible", ["M-10B"], ["INDEX-11"],
                    "2027-01-18T11:09:00+08:00", "2027-01-18T11:30:00+08:00"))
        result = self.svc.reconcile(T0)
        self.assertEqual(self.store.get_batch("K-2")["state"], STATE_VISIBLE)
        self.assertTrue(any(a["phase"] == "visible" for a in result["applied"]))

    def test_receipt_versions_are_not_overwritten_by_new_editorial_draft(self):
        from tests.support import FakeIndex
        # 索引站持有的是 M-20A；编辑后来准备了 M-20B。回执版本必须保留并报冲突
        self.svc.index = FakeIndex(receipts=[
            receipt("R-5", "K-4", "staged", ["M-20A"], [],
                    "2027-01-18T10:54:00+08:00", "2027-01-18T10:54:00+08:00"),
        ])
        self.svc.create_batch("K-4", ["M-20A"], "2027-01-18T10:53:00+08:00")
        applied = self.svc.reconcile(T0)["applied"]
        self.assertEqual(applied[0]["version_mismatch"], [])
        # 模拟对手方回执版本与本地批组不一致
        self.svc.index.receipts[0]["versions"] = ["M-20A", "M-20B"]
        self.svc.index.receipts[0]["id"] = "R-6"
        applied2 = self.svc.reconcile(T0)["applied"]
        self.assertEqual(applied2[0]["effect"], "version_conflict")
        self.assertEqual(sorted(applied2[0]["version_mismatch"]), ["M-20B"])
        # 本地批组版本原样保留
        self.assertEqual(self.store.get_batch("K-4")["versions"], ["M-20A"])

    def test_visible_batch_only_allows_erratum_or_withdrawal_notice(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex(receipts=[
            receipt("R-7", "K-5", "visible", ["M-10B"], ["INDEX-12"],
                    "2027-01-18T11:05:00+08:00", "2027-01-18T11:12:00+08:00"),
        ])
        self.svc.create_batch("K-5", ["M-10B"], T0)
        self.svc.reconcile(T0)
        notice = self.svc.issue_notice("K-5", "erratum", "图 2 坐标轴更正", T0)
        self.assertEqual(notice["kind"], "erratum")
        with self.assertRaises(Conflict):
            self.svc.dispatch_batch("K-5", T0)
        status = self.svc.batch_status("K-5")
        self.assertEqual(len(status["notices"]), 1)

    def test_phase_interchange_rejected(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex(receipts=[
            receipt("R-8", "K-6", "visible", ["M-10B"], ["INDEX-13"],
                    "2027-01-18T11:05:00+08:00", "2027-01-18T11:12:00+08:00"),
            receipt("R-9", "K-6", "staged", ["M-10B"], [],
                    "2027-01-18T11:40:00+08:00", "2027-01-18T11:42:00+08:00"),
        ])
        self.svc.create_batch("K-6", ["M-10B"], T0)
        self.svc.reconcile(T0)
        # 迟到的 staged 不能把 visible 改回去
        self.assertEqual(self.store.get_batch("K-6")["state"], STATE_VISIBLE)

    def test_reconcile_503_surfaces_unavailable(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex(fail_mode="503")
        with self.assertRaises(Unavailable):
            self.svc.reconcile(T0)

    def test_foreign_receipt_recorded_without_local_batch(self):
        from tests.support import FakeIndex
        self.svc.index = FakeIndex(receipts=[
            receipt("R-10", "K-UNKNOWN", "staged", ["M-10B"], [],
                    "2027-01-18T10:56:00+08:00", "2027-01-18T10:56:00+08:00"),
        ])
        applied = self.svc.reconcile(T0)["applied"]
        self.assertEqual(applied[0]["effect"], "foreign_only")
        self.assertIsNone(self.store.get_batch("K-UNKNOWN"))
        self.assertEqual(len(self.store.receipts_for_batch("K-UNKNOWN")), 1)

    def test_dispatch_without_index_client_is_unavailable(self):
        self.svc.index = None
        self.svc.create_batch("K-8", ["M-10B"], T0)
        with self.assertRaises(Unavailable):
            self.svc.dispatch_batch("K-8", T0)
        # 没有派发流水，批组保持 prepared，不会出现「可能已公开」歧义
        self.assertEqual(self.store.get_batch("K-8")["state"], STATE_PREPARED)

    def test_unknown_batch_returns_not_found(self):
        from conference.errors import NotFound
        with self.assertRaises(NotFound):
            self.svc.batch_status("K-NOPE")


if __name__ == "__main__":
    unittest.main()
