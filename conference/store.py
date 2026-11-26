"""会务服务的 SQLite 存储。

所有写操作都在短事务中完成；业务状态以普通标量列保存，
列表类字段（scope、refs 等）用 JSON 文本保存。
"""
from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY,
    owners TEXT NOT NULL,
    editor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pieces (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    owners TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manuscripts (
    id TEXT PRIMARY KEY,
    paper TEXT NOT NULL,
    version INTEGER NOT NULL,
    abstract_refs TEXT NOT NULL,
    extras TEXT NOT NULL,
    UNIQUE(paper, version)
);
CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    actor TEXT NOT NULL,
    scope TEXT NOT NULL,
    signed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embargo (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    earliest TEXT NOT NULL,
    received TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    grouped INTEGER NOT NULL,
    publish TEXT NOT NULL,
    candidates TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_plans (
    session TEXT PRIMARY KEY,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_entries (
    session TEXT NOT NULL,
    paper TEXT NOT NULL,
    version TEXT,
    moved_publish TEXT,
    removed INTEGER NOT NULL,
    reason TEXT,
    PRIMARY KEY (session, paper)
);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    versions TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch TEXT NOT NULL,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    batch TEXT NOT NULL,
    phase TEXT NOT NULL,
    versions TEXT NOT NULL,
    grant_ids TEXT NOT NULL,
    happened TEXT NOT NULL,
    arrived TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch TEXT NOT NULL,
    kind TEXT NOT NULL,
    versions TEXT NOT NULL,
    at TEXT NOT NULL,
    body TEXT
);
"""


def _loads(value, default):
    if value is None:
        return default
    return json.loads(value)


class Store:
    """线程安全的 SQLite 封装：单连接配合可重入锁串行化所有工作单元。

    内存模式（path=None）下所有连接共享同一份库，因此必须复用连接。
    """

    def __init__(self, path):
        self.path = ":memory:" if path is None else str(path)
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = __import__("threading").RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.executescript(SCHEMA)
            self._db.commit()

    @contextmanager
    def connect(self):
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    # ---- 基础资料 ----
    def is_empty(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) AS n FROM works").fetchone()["n"] == 0

    def add_work(self, rec):
        with self.connect() as db:
            db.execute("INSERT INTO works(id, owners, editor) VALUES (?,?,?)",
                       (rec["id"], json.dumps(rec["owners"], ensure_ascii=False), rec["editor"]))

    def add_piece(self, rec):
        with self.connect() as db:
            db.execute("INSERT INTO pieces(id, kind, owners) VALUES (?,?,?)",
                       (rec["id"], rec["kind"], json.dumps(rec["owners"], ensure_ascii=False)))

    def add_manuscript(self, rec):
        with self.connect() as db:
            db.execute(
                "INSERT INTO manuscripts(id, paper, version, abstract_refs, extras) VALUES (?,?,?,?,?)",
                (rec["id"], rec["paper"], int(rec["version"]),
                 json.dumps(rec["abstract_refs"], ensure_ascii=False),
                 json.dumps(rec["extras"], ensure_ascii=False)))

    def add_consent(self, rec):
        with self.connect() as db:
            db.execute("INSERT INTO consents(id, version, actor, scope, signed) VALUES (?,?,?,?,?)",
                       (rec["id"], rec["version"], rec["actor"],
                        json.dumps(rec["scope"], ensure_ascii=False), rec["signed"]))

    def add_embargo(self, rec):
        with self.connect() as db:
            db.execute("INSERT INTO embargo(id, content, earliest, received) VALUES (?,?,?,?)",
                       (rec["id"], rec["content"], rec["earliest"], rec["received"]))

    def add_session(self, rec):
        with self.connect() as db:
            db.execute("INSERT INTO sessions(id, grouped, publish, candidates) VALUES (?,?,?,?)",
                       (rec["id"], 1 if rec["grouped"] else 0, rec["publish"],
                        json.dumps(rec["candidates"], ensure_ascii=False)))

    def list_works(self):
        with self.connect() as db:
            return [{"id": r["id"], "owners": _loads(r["owners"], []), "editor": r["editor"]}
                    for r in db.execute("SELECT * FROM works ORDER BY id")]

    def get_work(self, work_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
            return None if r is None else {"id": r["id"], "owners": _loads(r["owners"], []),
                                           "editor": r["editor"]}

    def list_pieces(self):
        with self.connect() as db:
            return [{"id": r["id"], "kind": r["kind"], "owners": _loads(r["owners"], [])}
                    for r in db.execute("SELECT * FROM pieces ORDER BY id")]

    def get_piece(self, piece_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM pieces WHERE id = ?", (piece_id,)).fetchone()
            return None if r is None else {"id": r["id"], "kind": r["kind"],
                                           "owners": _loads(r["owners"], [])}

    def list_manuscripts(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM manuscripts ORDER BY paper, version").fetchall()
            return [self._manuscript(r) for r in rows]

    def get_manuscript(self, manuscript_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM manuscripts WHERE id = ?", (manuscript_id,)).fetchone()
            return None if r is None else self._manuscript(r)

    def manuscripts_of_paper(self, paper_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM manuscripts WHERE paper = ? ORDER BY version",
                              (paper_id,)).fetchall()
            return [self._manuscript(r) for r in rows]

    @staticmethod
    def _manuscript(r):
        return {"id": r["id"], "paper": r["paper"], "version": r["version"],
                "abstract_refs": _loads(r["abstract_refs"], []),
                "extras": _loads(r["extras"], [])}

    def consents_for_version(self, version_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM consents WHERE version = ? ORDER BY id",
                              (version_id,)).fetchall()
            return [{"id": r["id"], "version": r["version"], "actor": r["actor"],
                     "scope": _loads(r["scope"], []), "signed": r["signed"]} for r in rows]

    def embargoes_for_content(self, content_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM embargo WHERE content = ? ORDER BY id",
                              (content_id,)).fetchall()
            return [{"id": r["id"], "content": r["content"], "earliest": r["earliest"],
                     "received": r["received"]} for r in rows]

    def list_sessions(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sessions ORDER BY id").fetchall()
            return [{"id": r["id"], "grouped": bool(r["grouped"]), "publish": r["publish"],
                     "candidates": _loads(r["candidates"], [])} for r in rows]

    def get_session(self, session_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return None if r is None else {"id": r["id"], "grouped": bool(r["grouped"]),
                                           "publish": r["publish"],
                                           "candidates": _loads(r["candidates"], [])}

    # ---- 场次安排 ----
    def save_plan(self, session_id, note):
        with self.connect() as db:
            db.execute(
                "INSERT INTO session_plans(session, note) VALUES (?,?) "
                "ON CONFLICT(session) DO UPDATE SET note = excluded.note",
                (session_id, note))

    def get_plan(self, session_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM session_plans WHERE session = ?",
                           (session_id,)).fetchone()
            return None if r is None else {"session": r["session"], "note": r["note"]}

    def upsert_entry(self, session_id, paper_id, version_id, moved_publish, reason):
        with self.connect() as db:
            db.execute(
                "INSERT INTO session_entries(session, paper, version, moved_publish, removed, reason) "
                "VALUES (?,?,?,?,0,?) ON CONFLICT(session, paper) DO UPDATE SET "
                "version=excluded.version, moved_publish=excluded.moved_publish, "
                "removed=0, reason=excluded.reason",
                (session_id, paper_id, version_id, moved_publish, reason))

    def mark_entry_removed(self, session_id, paper_id, reason):
        with self.connect() as db:
            db.execute(
                "INSERT INTO session_entries(session, paper, version, moved_publish, removed, reason) "
                "VALUES (?,?,NULL,NULL,1,?) ON CONFLICT(session, paper) DO UPDATE SET "
                "version=NULL, moved_publish=NULL, removed=1, reason=excluded.reason",
                (session_id, paper_id, reason))

    def entries_of_session(self, session_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM session_entries WHERE session = ? ORDER BY paper",
                              (session_id,)).fetchall()
            return [{"session": r["session"], "paper": r["paper"], "version": r["version"],
                     "moved_publish": r["moved_publish"], "removed": bool(r["removed"]),
                     "reason": r["reason"]} for r in rows]

    # ---- 批组与索引回执 ----
    def create_batch(self, batch_id, versions, state, at):
        with self.connect() as db:
            db.execute(
                "INSERT INTO batches(id, state, versions, created, updated) VALUES (?,?,?,?,?)",
                (batch_id, state, json.dumps(versions, ensure_ascii=False), at, at))

    def update_batch_state(self, batch_id, state, at):
        with self.connect() as db:
            db.execute("UPDATE batches SET state = ?, updated = ? WHERE id = ?",
                       (state, at, batch_id))

    def get_batch(self, batch_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
            return None if r is None else self._batch(r)

    def list_batches(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM batches ORDER BY id").fetchall()
            return [self._batch(r) for r in rows]

    def batches_for_versions(self, version_ids):
        wanted = set(version_ids)
        return [b for b in self.list_batches() if wanted.intersection(b["versions"])]

    @staticmethod
    def _batch(r):
        return {"id": r["id"], "state": r["state"], "versions": _loads(r["versions"], []),
                "created": r["created"], "updated": r["updated"]}

    def add_dispatch(self, batch_id, kind, at, outcome, detail=None):
        with self.connect() as db:
            db.execute(
                "INSERT INTO dispatches(batch, kind, at, outcome, detail) VALUES (?,?,?,?,?)",
                (batch_id, kind, at, outcome, detail))

    def dispatches_of_batch(self, batch_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM dispatches WHERE batch = ? ORDER BY id",
                              (batch_id,)).fetchall()
            return [{"id": r["id"], "batch": r["batch"], "kind": r["kind"], "at": r["at"],
                     "outcome": r["outcome"], "detail": r["detail"]} for r in rows]

    def upsert_receipt(self, rec):
        with self.connect() as db:
            db.execute(
                "INSERT INTO receipts(id, batch, phase, versions, grant_ids, happened, arrived) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET phase=excluded.phase, "
                "versions=excluded.versions, grant_ids=excluded.grant_ids, "
                "happened=excluded.happened, arrived=excluded.arrived",
                (rec["id"], rec["batch"], rec["phase"],
                 json.dumps(rec["versions"], ensure_ascii=False),
                 json.dumps(rec["grant_ids"], ensure_ascii=False),
                 rec["happened"], rec["arrived"]))

    def receipts_for_batch(self, batch_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM receipts WHERE batch = ? ORDER BY happened, id",
                              (batch_id,)).fetchall()
            return [self._receipt(r) for r in rows]

    def list_receipts(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM receipts ORDER BY arrived, id").fetchall()
            return [self._receipt(r) for r in rows]

    @staticmethod
    def _receipt(r):
        return {"id": r["id"], "batch": r["batch"], "phase": r["phase"],
                "versions": _loads(r["versions"], []), "grant_ids": _loads(r["grant_ids"], []),
                "happened": r["happened"], "arrived": r["arrived"]}

    def add_notice(self, batch_id, kind, versions, at, body=None):
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO notices(batch, kind, versions, at, body) VALUES (?,?,?,?,?)",
                (batch_id, kind, json.dumps(versions, ensure_ascii=False), at, body))
            return cur.lastrowid

    def notices_of_batch(self, batch_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM notices WHERE batch = ? ORDER BY id",
                              (batch_id,)).fetchall()
            return [{"id": r["id"], "batch": r["batch"], "kind": r["kind"],
                     "versions": _loads(r["versions"], []), "at": r["at"], "body": r["body"]}
                    for r in rows]
