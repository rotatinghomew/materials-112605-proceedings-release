"""测试共用夹具：内存存储 + 合成交换资料 + 假索引站。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from conference.domain import load_fixture
from conference.service import ConferenceService
from conference.store import Store

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "exchange.json"


def build_service(extra_data=None, unavailable=False):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if extra_data:
        for key, rows in extra_data.items():
            data.setdefault(key, []).extend(rows)
    store = Store(":memory:")
    load_fixture(store, data)
    return ConferenceService(store, None), store, data


class FakeIndex:
    """可编排回执与网络行为的假索引站，记录所有收到的派发。"""

    def __init__(self, receipts=None, fail_mode=None):
        self.receipts = list(receipts or [])
        self.fail_mode = fail_mode  # None / "503" / "drop" / "refuse"
        self.received = []
        self.query_at = []

    def query_receipts(self, at):
        self.query_at.append(str(at))
        if self.fail_mode == "503":
            return 503, {"message": "对接方暂时无法查阅回执"}
        return 200, {"receipts": list(self.receipts)}

    def dispatch(self, payload):
        self.received.append(payload)
        if self.fail_mode == "drop":
            from conference.errors import Unavailable
            raise Unavailable("连接已断开")
        if self.fail_mode == "refuse":
            return 409, {"message": "拒绝"}
        return 202, {"received": True}


def start_partner(extra_args=None):
    """启动真实的 partner.py 回执服务，返回 (base_url, journal_path, server_thread)。"""
    import sys
    import tempfile
    sys.path.insert(0, str(ROOT))
    import partner  # noqa: E402

    journal = Path(tempfile.mkdtemp()) / "ingress.db"
    port_holder = {}

    class _Server(ThreadingHTTPServer):
        daemon_threads = True

    server = _Server(
        ("127.0.0.1", 0),
        partner.handler_for(
            json.loads(FIXTURE.read_text(encoding="utf-8"))["receipts"],
            False, journal, drop_reply=extra_args == "drop"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", journal, server
