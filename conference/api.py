"""会务服务 HTTP 接口（标准库实现，回环地址提供）。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .errors import ServiceError
from .service import ConferenceService, IndexClient
from .timeutil import parse_time, parse_time

MAX_BODY = 1 << 20


class ApiHandler(BaseHTTPRequestHandler):
    service = None  # 由 make_server 注入

    def _at(self, query):
        values = parse_qs(query).get("at", [])
        if not values:
            return None
        try:
            return parse_time(values[0])
        except ValueError as exc:
            raise ServiceError("at 须为带时区偏移的 ISO-8601 时间") from exc

    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, UnicodeError):
            raise ServiceError("请求须为 JSON 对象")

    def do_GET(self):
        target = urlsplit(self.path)
        at = self._at(target.query)
        try:
            if target.path == "/health":
                self._json(200, {"status": "ok"})
                return
            if target.path == "/v1/sessions":
                self._json(200, {"sessions": self.service.store.list_sessions()})
                return
            if target.path.startswith("/v1/sessions/") and target.path.endswith("/review"):
                session_id = target.path.split("/")[3]
                self._json(200, self.service.review_session(session_id, at))
                return
            if target.path.startswith("/v1/manuscripts/") and target.path.endswith("/review"):
                manuscript_id = target.path.split("/")[3]
                self._json(200, self.service.review_manuscript(manuscript_id, at))
                return
            if target.path.startswith("/v1/batches/"):
                batch_id = target.path.split("/")[3]
                self._json(200, self.service.batch_status(batch_id))
                return
            if target.path == "/v1/batches":
                self._json(200, {"batches": self.service.store.list_batches()})
                return
            if target.path.startswith("/v1/authors/"):
                actor = target.path.split("/")[3]
                self._json(200, self.service.author_view(actor, at))
                return
            self._json(404, {"message": "路径不存在"})
        except ServiceError as exc:
            self._json(exc.status, {"message": exc.message, "details": exc.details})

    def do_POST(self):
        target = urlsplit(self.path)
        try:
            body = self._read_body()
            at = body.get("at")
            path = target.path
            if path == "/v1/manuscripts":
                result = self.service.submit_manuscript(
                    body["id"], body["paper"], body["version"],
                    body.get("abstract_refs", []), body.get("extras", []))
            elif path == "/v1/consents":
                result = self.service.register_consent(
                    body["id"], body["version"], body["actor"],
                    body.get("scope", []), body["signed"])
            elif path == "/v1/embargoes":
                result = self.service.register_embargo(
                    body["id"], body["content"], body["earliest"],
                    body.get("received"))
            elif path == "/v1/sessions/choose":
                result = self.service.choose_session_combo(
                    body["session"], body["choices"], body.get("note", ""), at)
            elif path == "/v1/sessions/reschedule":
                result = self.service.reschedule_entry(
                    body["session"], body["paper"], body["new_publish"],
                    body.get("reason", ""), at)
            elif path == "/v1/sessions/remove-entry":
                result = self.service.remove_entry(
                    body["session"], body["paper"], body.get("reason", ""),
                    body.get("actor_consents", []), at)
            elif path == "/v1/batches":
                result = self.service.create_batch(body["id"], body["versions"], at)
            elif path.endswith("/dispatch") and path.startswith("/v1/batches/"):
                result = self.service.dispatch_batch(path.split("/")[3], at)
            elif path.endswith("/stop") and path.startswith("/v1/batches/"):
                result = self.service.stop_batch(path.split("/")[3],
                                                  body.get("reason", ""), at)
            elif path == "/v1/reconcile":
                result = self.service.reconcile(at)
            elif path.endswith("/notices") and path.startswith("/v1/batches/"):
                result = self.service.issue_notice(
                    path.split("/")[3], body["kind"], body.get("body", ""), at)
            else:
                self._json(404, {"message": "路径不存在"})
                return
            self._json(200, result)
        except KeyError as exc:
            self._json(400, {"message": f"缺少必填字段 {exc}"})
        except ServiceError as exc:
            self._json(exc.status, {"message": exc.message, "details": exc.details})

    def log_message(self, format, *args):
        pass


def make_server(store_path, index_url, host="127.0.0.1", port=8088,
                data_path=None, unavailable=False):
    from .domain import load_fixture
    store = Store(store_path)
    if store.is_empty() and data_path is not None:
        data = json.loads(data_path.read_text(encoding="utf-8"))
        load_fixture(store, data)
    client = None if unavailable else IndexClient(index_url)
    service = ConferenceService(store, client)

    class _Handler(ApiHandler):
        pass

    _Handler.service = service
    server = ThreadingHTTPServer((host, port), _Handler)
    server.service = service
    return server
