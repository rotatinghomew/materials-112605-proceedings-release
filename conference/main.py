"""会务服务启动入口。

示例：
    python3 -m conference.main --db run/conference.db \
        --data fixtures/exchange.json --index http://127.0.0.1:8087
"""
import argparse
import json
from pathlib import Path

from .api import make_server
from .domain import load_fixture
from .service import IndexClient
from .store import Store

DATA_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "exchange.json"


def build_service(args):
    store = Store(args.db)
    if store.is_empty() and args.data is not None:
        data = json.loads(args.data.read_text(encoding="utf-8"))
        load_fixture(store, data)
    client = None if args.unavailable else IndexClient(args.index)
    from .service import ConferenceService
    return ConferenceService(store, client), store


def main(argv=None):
    parser = argparse.ArgumentParser(description="会务分级公开与索引回执服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--db", default="run/conference.db", help="SQLite 路径，:memory: 仅测试用")
    parser.add_argument("--data", type=Path, default=DATA_PATH, help="首次启动载入的交换资料")
    parser.add_argument("--index", default="http://127.0.0.1:8087", help="索引站回执服务地址")
    parser.add_argument("--unavailable", action="store_true", help="不连接索引站（模拟不可用）")
    args = parser.parse_args(argv)

    service, _ = build_service(args)
    from http.server import ThreadingHTTPServer
    from .api import ApiHandler

    class _Handler(ApiHandler):
        pass

    _Handler.service = service
    with ThreadingHTTPServer((args.host, args.port), _Handler) as server:
        print(f"http://{args.host}:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
