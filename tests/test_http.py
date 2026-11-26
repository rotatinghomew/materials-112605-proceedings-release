"""端到端 HTTP：会务服务接口 + 真实 partner.py 索引站（含断连回放）。"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

from conference.api import ApiHandler
from conference.domain import load_fixture
from conference.service import ConferenceService, IndexClient
from conference.store import Store
from tests.support import start_partner

FIXTURE = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures" / "exchange.json"
T0 = "2027-01-18T10:55:00+08:00"


def q(at):
    return quote(at, safe="")


def _request(base, method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class HttpEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.partner_url, cls.journal, cls.partner_server = start_partner()
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.store = Store(":memory:")
        load_fixture(cls.store, data)
        cls.service = ConferenceService(cls.store, IndexClient(cls.partner_url))

        class _Handler(ApiHandler):
            pass

        _Handler.service = cls.service
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.partner_server.shutdown()

    def test_01_health_and_session_review(self):
        status, body = _request(self.base, "GET", "/health")
        self.assertEqual(status, 200)
        status, body = _request(self.base, "GET", f"/v1/sessions/G-1/review?at={q(T0)}")
        self.assertEqual(status, 200)
        self.assertEqual([s["chosen"] for s in body["slots"]], ["M-10B", "M-20B"])

    def test_02_submit_new_version_and_consent(self):
        status, body = _request(self.base, "POST", "/v1/manuscripts", {
            "id": "M-30C", "paper": "W-30", "version": 3,
            "abstract_refs": [], "extras": []})
        self.assertEqual(status, 200, body)
        status, body = _request(self.base, "POST", "/v1/consents", {
            "id": "S-4C", "version": "M-30C", "actor": "S4",
            "scope": ["abstract"], "signed": T0})
        self.assertEqual(status, 200, body)
        status, body = _request(self.base, "GET", f"/v1/manuscripts/M-30C/review?at={q(T0)}")
        self.assertEqual(body["level"], "full")

    def test_03_batch_dispatch_and_reconcile_against_real_partner(self):
        status, body = _request(self.base, "POST", "/v1/batches", {
            "id": "K-1", "versions": ["M-10B"], "at": T0})
        self.assertEqual(status, 200, body)
        status, body = _request(self.base, "POST", "/v1/batches/K-1/dispatch",
                                {"at": T0})
        self.assertEqual(status, 200, body)
        # partner 已登记原文
        import sqlite3
        with sqlite3.connect(self.journal) as db:
            rows = db.execute("SELECT requested FROM ingress").fetchall()
        self.assertTrue(any('"K-1"' in r[0] for r in rows))
        # 用业务时刻对账，拿到 fixtures 中的既有回执（K-1 staged/visible）
        status, body = _request(self.base, "POST", "/v1/reconcile",
                                {"at": "2027-01-18T11:20:00+08:00"})
        self.assertEqual(status, 200, body)
        status, body = _request(self.base, "GET", "/v1/batches/K-1")
        self.assertEqual(body["state"], "visible")
        grants = [g for r in body["receipts"] for g in r["grant_ids"]]
        self.assertEqual(grants, ["INDEX-7", "INDEX-8"])

    def test_04_visible_batch_rejects_stop_and_takes_erratum(self):
        status, body = _request(self.base, "POST", "/v1/batches/K-1/stop",
                                {"reason": "尝试停止", "at": T0})
        self.assertEqual(status, 409)
        status, body = _request(self.base, "POST", "/v1/batches/K-1/notices", {
            "kind": "erratum", "body": "更正排版", "at": T0})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["kind"], "erratum")

    def test_05_author_view_redaction_over_http(self):
        status, body = _request(self.base, "GET", f"/v1/authors/S4?at={q(T0)}")
        self.assertEqual(status, 200)
        w10 = next(p for p in body["papers"] if p["paper"] == "W-10")
        self.assertIsNone(w10["manuscripts"][0]["extras"])

    def test_06_validation_errors_are_structured(self):
        status, body = _request(self.base, "POST", "/v1/manuscripts", {
            "id": "X", "paper": "W-30", "version": 9,
            "abstract_refs": ["NOPE"], "extras": []})
        self.assertEqual(status, 422)
        self.assertIn("message", body)

    def test_07_reconcile_is_idempotent(self):
        _, first = _request(self.base, "POST", "/v1/reconcile",
                            {"at": "2027-01-18T11:20:00+08:00"})
        _, second = _request(self.base, "POST", "/v1/reconcile",
                             {"at": "2027-01-18T11:20:00+08:00"})
        self.assertEqual(len(first["applied"]), len(second["applied"]))


class DropReplyTests(unittest.TestCase):
    """调用已发出但没有回话：必须与明确失败区分，重发不另分配索引。"""

    @classmethod
    def setUpClass(cls):
        cls.partner_url, cls.journal, cls.partner_server = start_partner("drop")
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.store = Store(":memory:")
        load_fixture(cls.store, data)
        cls.service = ConferenceService(cls.store, IndexClient(cls.partner_url, timeout=2))

        class _Handler(ApiHandler):
            pass

        _Handler.service = cls.service
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.partner_server.shutdown()

    def test_dropped_reply_marks_dispatched_and_keeps_batch_id(self):
        _request(self.base, "POST", "/v1/batches",
                 {"id": "K-DROP", "versions": ["M-10B", "M-20B"], "at": T0})
        status, body = _request(self.base, "POST", "/v1/batches/K-DROP/dispatch",
                                {"at": T0})
        self.assertEqual(status, 503)
        status, body = _request(self.base, "GET", "/v1/batches/K-DROP")
        self.assertEqual(body["state"], "dispatched")
        self.assertEqual(body["versions"], ["M-10B", "M-20B"])
        # 重发必须沿用同一批号：含相同版本的新批组被拒绝，避免重复分配索引
        status, body = _request(self.base, "POST", "/v1/batches", {
            "id": "K-DROP2", "versions": ["M-10B", "M-20B"], "at": T0})
        self.assertEqual(status, 409)
        self.assertEqual(body["details"]["batch"], "K-DROP")


if __name__ == "__main__":
    unittest.main()
