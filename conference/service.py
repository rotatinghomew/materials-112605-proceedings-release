"""会务服务外观：投稿交接、公开审查、场次安排、索引站批组与作者查询。"""
import json
import urllib.error
import urllib.request

from .domain import ABSTRACT, evaluate_manuscript
from .errors import Conflict, NotFound, Unavailable, ValidationFailed
from .timeutil import format_time, now, parse_time

# 批组状态机。索引站四态暂存/开放/拒绝/确认撤回不可互换，本地另记派发状态。
STATE_PREPARED = "prepared"
STATE_DISPATCHED = "dispatched"          # 已发出，尚无回话：可能已公开
STATE_STAGED = "staged"
STATE_VISIBLE = "visible"
STATE_REJECTED = "rejected"
STATE_WITHDRAWN = "withdrawn"
STATE_STOP_REQUESTED = "stop_requested"  # 停止请求已发出，回话未明
STATE_STOPPED = "stopped"                # 已确认未生效并停止

PHASE_TO_STATE = {
    "staged": STATE_STAGED,
    "visible": STATE_VISIBLE,
    "rejected": STATE_REJECTED,
    "withdrawn": STATE_WITHDRAWN,
}
# 回执只进不退：一旦已公开，后续对账不能把状态改回去


class IndexClient:
    """索引站 HTTP 客户端（论文目录交换）。"""

    def __init__(self, base_url, timeout=3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeError):
                body = {}
            return exc.code, body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Unavailable("索引站暂时无法联系", {"reason": str(exc)}) from exc

    def query_receipts(self, at):
        from urllib.parse import quote
        return self._request("GET", f"/v1/receipts?at={quote(format_time(at), safe='')}")

    def dispatch(self, payload):
        return self._request("POST", "/v1/dispatches", payload)


class ConferenceService:
    def __init__(self, store, index_client=None, clock=now):
        self.store = store
        self.index = index_client
        self.clock = clock

    def _at(self, at):
        return parse_time(at) if isinstance(at, str) else (at or self.clock())

    # ------------------------------------------------------------------
    # 投稿交接：逐版交稿、按版本签认、专利时钟
    # ------------------------------------------------------------------
    def submit_manuscript(self, manuscript_id, paper, version, abstract_refs, extras):
        work = self.store.get_work(paper)
        if work is None:
            raise NotFound("论文不存在", {"paper": paper})
        if self.store.get_manuscript(manuscript_id) is not None:
            raise Conflict("稿件版本 id 已存在", {"manuscript": manuscript_id})
        existing = {m["version"] for m in self.store.manuscripts_of_paper(paper)}
        if version in existing:
            raise Conflict("同一论文的版号已被占用", {"paper": paper, "version": version})
        refs = list(abstract_refs) + list(extras)
        for piece_id in refs:
            if self.store.get_piece(piece_id) is None:
                raise ValidationFailed("引用了不存在的图表或结果", {"piece": piece_id})
        if len(refs) != len(set(refs)):
            raise ValidationFailed("同一内容在一条稿件中重复引用")
        record = {"id": manuscript_id, "paper": paper, "version": int(version),
                  "abstract_refs": list(abstract_refs), "extras": list(extras)}
        self.store.add_manuscript(record)
        return record

    def register_consent(self, consent_id, version_id, actor, scope, signed):
        manuscript = self.store.get_manuscript(version_id)
        if manuscript is None:
            raise NotFound("稿件版本不存在", {"manuscript": version_id})
        if any(c["id"] == consent_id for c in self.store.consents_for_version(version_id)):
            raise Conflict("签认 id 已存在", {"consent": consent_id})
        work = self.store.get_work(manuscript["paper"])
        if actor not in work["owners"]:
            raise ValidationFailed("签认人不是论文权利人", {"actor": actor})
        clean_scope = []
        for item in scope:
            if item == ABSTRACT:
                clean_scope.append(item)
            elif self.store.get_piece(item) is None:
                raise ValidationFailed("授权范围引用了不存在的内容", {"piece": item})
            elif item not in manuscript["abstract_refs"] and item not in manuscript["extras"]:
                raise ValidationFailed("授权范围超出该版本引用的内容", {"piece": item})
            else:
                clean_scope.append(item)
        signed_at = self._at(signed)
        record = {"id": consent_id, "version": version_id, "actor": actor,
                  "scope": clean_scope, "signed": format_time(signed_at)}
        self.store.add_consent(record)
        return record

    def register_embargo(self, embargo_id, content_id, earliest, received=None):
        if self.store.get_piece(content_id) is None:
            raise NotFound("内容不存在", {"piece": content_id})
        received_at = self._at(received)
        earliest_at = self._at(earliest)
        record = {"id": embargo_id, "content": content_id,
                  "earliest": format_time(earliest_at), "received": format_time(received_at)}
        self.store.add_embargo(record)
        return record

    # ------------------------------------------------------------------
    # 公开审查
    # ------------------------------------------------------------------
    def review_manuscript(self, manuscript_id, at=None):
        return evaluate_manuscript(self.store, manuscript_id, self._at(at))

    def review_session(self, session_id, at=None):
        at = self._at(at)
        session = self.store.get_session(session_id)
        if session is None:
            raise NotFound("场次不存在", {"session": session_id})
        planned = {e["paper"]: e for e in self.store.entries_of_session(session_id)}

        slots = []
        chosen = []
        for group in session["candidates"]:
            options = []
            for manuscript_id in group:
                eval_ = evaluate_manuscript(self.store, manuscript_id, at)
                options.append(eval_)
            # 优先全版，其次可发的简版；同档取高版号
            full = [e for e in options if e["level"] == "full"]
            abstract = [e for e in options if e["level"] == "abstract"]
            pool = full or abstract
            pick = max(pool, key=lambda e: e["version"]) if pool else None
            slot = {
                "paper": pick["paper"] if pick else options[0]["paper"],
                "options": options,
                "chosen": pick["manuscript"] if pick else None,
                "chosen_level": pick["level"] if pick else "blocked",
                "recoverable_at": None if pick else _slot_recoverable_at(options, at),
                "plan": _plan_view(planned.get(pick["paper"] if pick else group[0])),
            }
            slots.append(slot)
            if pick:
                chosen.append(pick)

        all_full = bool(chosen) and all(s["chosen_level"] == "full" for s in slots)
        all_ready = bool(chosen) and len(chosen) == len(slots)
        if not all_ready:
            status = "blocked"
        elif all_full:
            status = "ready"
        else:
            status = "ready_simplified"

        # 专题整组开放：任一槽位的完全/简版就绪时刻晚于场次约定，即需顺延
        scheduled = parse_time(session["publish"])
        if all_ready and session["grouped"]:
            etas = [_slot_eta(s) for s in slots]
            if any(eta is not None and eta > scheduled for eta in etas):
                status = "delayed"
        elif not all_ready and session["grouped"]:
            # 全部候选当前受阻，但存在未来可恢复的就绪时刻 => 顺延而非死锁
            recoverable = [s["recoverable_at"] for s in slots if s["recoverable_at"]]
            if recoverable:
                status = "delayed"

        deciders = self._session_deciders(session, slots, at)
        return {
            "session": session_id,
            "grouped": session["grouped"],
            "publish": session["publish"],
            "at": format_time(at),
            "status": status,
            "slots": slots,
            "deciders": deciders,
        }

    def _session_deciders(self, session, slots, at):
        deciders = []
        for slot in slots:
            for opt in slot["options"]:
                # 缺签认与专利时钟分别归因，便于编辑追到具体决定来源
                for blocker in opt["blockers"]:
                    deciders.append({"paper": opt["paper"], "manuscript": opt["manuscript"],
                                     **blocker})
        # 索引站状态也可能改变决定
        for batch in self.store.list_batches():
            if batch["state"] in (STATE_VISIBLE, STATE_WITHDRAWN):
                deciders.append({"type": "index_state", "batch": batch["id"],
                                 "state": batch["state"], "versions": batch["versions"]})
            elif batch["state"] == STATE_DISPATCHED:
                deciders.append({"type": "index_unknown", "batch": batch["id"],
                                 "versions": batch["versions"],
                                 "detail": "批组已发出但无回话，可能已经公开"})
        # 去重保序
        seen, unique = set(), []
        for item in deciders:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    # ------------------------------------------------------------------
    # 专题/普通场次安排
    # ------------------------------------------------------------------
    def choose_session_combo(self, session_id, choices, note="", at=None):
        """专题主持人选定一个具备完整权利的组合；choices 为 paper -> manuscript。"""
        at = self._at(at)
        session = self.store.get_session(session_id)
        if session is None:
            raise NotFound("场次不存在", {"session": session_id})
        papers = [self.store.get_manuscript(group[0])["paper"]
                  for group in session["candidates"]]
        if set(choices) != set(papers):
            raise ValidationFailed("组合必须为每个论文槽位指定一个版本",
                                   {"expected": papers})
        for paper, manuscript_id in choices.items():
            valid = next((g for g in session["candidates"]
                          if self.store.get_manuscript(g[0])["paper"] == paper), [])
            if manuscript_id not in valid:
                raise ValidationFailed("版本不在该场次候选内",
                                       {"paper": paper, "manuscript": manuscript_id})
            eval_ = evaluate_manuscript(self.store, manuscript_id, at)
            if eval_["level"] == "blocked":
                raise Conflict("被选版本当前不具备公开条件",
                               {"manuscript": manuscript_id, "blockers": eval_["blockers"]})
            self.store.upsert_entry(session_id, paper, manuscript_id, None, note)
        self.store.save_plan(session_id, note or "主持人选定组合")
        return self.review_session(session_id, at)

    def reschedule_entry(self, session_id, paper_id, new_publish, reason, at=None):
        """按场次约定调整某条目的公开时间（专题整体或普通分批均可）。"""
        at = self._at(at)
        session = self._require_session_paper(session_id, paper_id)
        new_at = self._at(new_publish)
        if new_at < at:
            raise ValidationFailed("新公开时间不能早于当前时刻")
        entry = next((e for e in self.store.entries_of_session(session_id)
                      if e["paper"] == paper_id), None)
        version_id = entry["version"] if entry else self._fallback_version(session_id, paper_id)
        self.store.upsert_entry(session_id, paper_id, version_id, format_time(new_at), reason)
        self.store.save_plan(session_id, f"调整 {paper_id} 公开时间：{reason}")
        return self.review_session(session_id, at)

    def remove_entry(self, session_id, paper_id, reason, actor_consents, at=None):
        """征得相应作者同意后把条目移出未发出的专题。"""
        at = self._at(at)
        session = self._require_session_paper(session_id, paper_id)
        work = self.store.get_work(paper_id)
        if not set(actor_consents).issuperset(set(work["owners"])):
            raise ValidationFailed("移出条目须取得该论文全体权利人同意",
                                   {"missing": sorted(set(work["owners"]) - set(actor_consents))})
        # 已公开的条目只能勘误/撤回，不能移出
        for batch in self.store.batches_for_versions(
                [m["id"] for m in self.store.manuscripts_of_paper(paper_id)]):
            if batch["state"] == STATE_VISIBLE:
                raise Conflict("该论文已有版本公开，只能发勘误或撤回声明",
                               {"batch": batch["id"]})
        self.store.mark_entry_removed(session_id, paper_id, reason)
        self.store.save_plan(session_id, f"移出 {paper_id}：{reason}")
        return self.review_session(session_id, at)

    def _require_session_paper(self, session_id, paper_id):
        session = self.store.get_session(session_id)
        if session is None:
            raise NotFound("场次不存在", {"session": session_id})
        papers = {self.store.get_manuscript(g[0])["paper"] for g in session["candidates"]}
        if paper_id not in papers:
            raise NotFound("该论文不在此场次中", {"paper": paper_id})
        return session

    def _fallback_version(self, session_id, paper_id):
        session = self.store.get_session(session_id)
        group = next(g for g in session["candidates"]
                     if self.store.get_manuscript(g[0])["paper"] == paper_id)
        return group[-1]

    # ------------------------------------------------------------------
    # 索引站批组
    # ------------------------------------------------------------------
    def create_batch(self, batch_id, version_ids, at=None):
        at = self._at(at)
        if self.store.get_batch(batch_id) is not None:
            raise Conflict("批号已存在", {"batch": batch_id})
        if not version_ids or len(version_ids) != len(set(version_ids)):
            raise ValidationFailed("批组须含不重复的稿件版本")
        for manuscript_id in version_ids:
            eval_ = evaluate_manuscript(self.store, manuscript_id, at)
            if eval_["level"] == "blocked":
                raise Conflict("含摘要引用受阻结果的旧版，不能作为无图版本发出",
                               {"manuscript": manuscript_id, "blockers": eval_["blockers"]})
        for batch in self.store.list_batches():
            overlap = sorted(set(version_ids) & set(batch["versions"]))
            if overlap and batch["state"] not in (STATE_STOPPED, STATE_REJECTED, STATE_WITHDRAWN):
                raise Conflict("版本已在有效批组中，重发不得重复分配索引",
                               {"batch": batch["id"], "overlap": overlap})
        self.store.create_batch(batch_id, list(version_ids), STATE_PREPARED, format_time(at))
        return self.store.get_batch(batch_id)

    def dispatch_batch(self, batch_id, at=None):
        """把批组发往索引站。无回话时保留 dispatched，重发沿用同一批号。"""
        at = self._at(at)
        batch = self._require_batch(batch_id)
        if self.index is None:
            raise Unavailable("未配置索引站客户端，无法派发")
        if batch["state"] in (STATE_VISIBLE, STATE_WITHDRAWN):
            raise Conflict("批组已经公开，只能发勘误或撤回声明", {"state": batch["state"]})
        if batch["state"] == STATE_STOPPED:
            raise Conflict("批组已停止，不能再发出")
        payload = {"batch": batch_id, "versions": batch["versions"]}
        try:
            status, _ = self.index.dispatch(payload)
        except Unavailable as exc:
            # 调用已发出但没有回话：区别于明确失败，标记为「可能已公开」
            self.store.add_dispatch(batch_id, "dispatch", format_time(at), "no_reply",
                                    exc.message)
            self.store.update_batch_state(batch_id, STATE_DISPATCHED, format_time(at))
            raise
        if status == 202:
            self.store.add_dispatch(batch_id, "dispatch", format_time(at), "accepted")
            self.store.update_batch_state(batch_id, STATE_DISPATCHED, format_time(at))
        else:
            self.store.add_dispatch(batch_id, "dispatch", format_time(at), "refused",
                                    f"HTTP {status}")
            raise Conflict("索引站拒绝接收批组", {"http_status": status})
        return self.store.get_batch(batch_id)

    def stop_batch(self, batch_id, reason, at=None):
        """停止未生效的批组；已公开则拒绝并提示只能勘误/撤回。"""
        at = self._at(at)
        batch = self._require_batch(batch_id)
        if self.index is None:
            raise Unavailable("未配置索引站客户端，无法派发停止请求")
        if batch["state"] in (STATE_VISIBLE, STATE_WITHDRAWN):
            raise Conflict("批组已公开，停止不被受理，只能发勘误或撤回声明",
                           {"state": batch["state"]})
        if batch["state"] == STATE_STOPPED:
            return batch
        payload = {"batch": batch_id, "action": "stop", "reason": reason}
        try:
            status, _ = self.index.dispatch(payload)
        except Unavailable as exc:
            self.store.add_dispatch(batch_id, "stop", format_time(at), "no_reply", exc.message)
            self.store.update_batch_state(batch_id, STATE_STOP_REQUESTED, format_time(at))
            raise
        if status == 202:
            self.store.add_dispatch(batch_id, "stop", format_time(at), "accepted", reason)
            self.store.update_batch_state(batch_id, STATE_STOP_REQUESTED, format_time(at))
        else:
            self.store.add_dispatch(batch_id, "stop", format_time(at), "refused",
                                    f"HTTP {status}")
            raise Conflict("索引站拒绝停止请求", {"http_status": status})
        return self.store.get_batch(batch_id)

    def reconcile(self, at=None):
        """拉取索引站回执并对账。开放确认可能晚于权利变更。"""
        at = self._at(at)
        if self.index is None:
            raise Unavailable("未配置索引站客户端")
        status, payload = self.index.query_receipts(at)
        if status == 503:
            raise Unavailable("索引站暂时无法查阅回执")
        if status != 200:
            raise Conflict("索引站回执查询失败", {"http_status": status})
        applied = []
        for rec in payload.get("receipts", []):
            applied.append(self._apply_receipt(rec, at))
        return {"at": format_time(at), "applied": applied}

    def _apply_receipt(self, rec, at):
        receipt_id = rec["id"]
        batch_id = rec["batch"]
        phase = rec["phase"]
        if phase not in PHASE_TO_STATE:
            raise ValidationFailed("未知的索引站阶段", {"phase": phase, "receipt": receipt_id})
        batch = self.store.get_batch(batch_id)
        if batch is None:
            # 对接方留存的批组本地没有记录：登记但不自动建本地批组
            self.store.upsert_receipt(_normalize_receipt(rec))
            return {"receipt": receipt_id, "batch": batch_id, "effect": "foreign_only"}
        # 索引站持有的版本以回执为准，绝不被编辑新准备的稿覆盖
        version_mismatch = sorted(set(rec["versions"]) ^ set(batch["versions"]))
        stored = {r["id"]: r for r in self.store.receipts_for_batch(batch_id)}
        self.store.upsert_receipt(_normalize_receipt(rec))
        new_state = PHASE_TO_STATE[phase]
        # 已公开/终态不可回退；已确认停止的批组不再被迟到的 staged 回执复活
        irreversible = {STATE_VISIBLE, STATE_WITHDRAWN, STATE_REJECTED}
        if batch["state"] not in irreversible and not (
                batch["state"] == STATE_STOPPED and phase == "staged"):
            self.store.update_batch_state(batch_id, new_state, format_time(at))
        effect = "advanced"
        if version_mismatch:
            effect = "version_conflict"
        if receipt_id in stored:
            effect = "already_known"
        return {"receipt": receipt_id, "batch": batch_id, "phase": phase,
                "effect": effect, "version_mismatch": version_mismatch,
                "grant_ids": rec.get("grant_ids", [])}

    def issue_notice(self, batch_id, kind, body, at=None):
        """已公开批组只允许发勘误（erratum）或撤回声明（withdrawal）。"""
        at = self._at(at)
        if kind not in ("erratum", "withdrawal"):
            raise ValidationFailed("公开后只允许 erratum 或 withdrawal")
        batch = self._require_batch(batch_id)
        if batch["state"] != STATE_VISIBLE:
            raise Conflict("只有已公开批组可以发勘误或撤回声明", {"state": batch["state"]})
        notice_id = self.store.add_notice(batch_id, kind, batch["versions"],
                                          format_time(at), body)
        return {"id": notice_id, "batch": batch_id, "kind": kind,
                "versions": batch["versions"], "at": format_time(at), "body": body}

    def batch_status(self, batch_id):
        batch = self._require_batch(batch_id)
        return {
            "batch": batch_id,
            "state": batch["state"],
            "versions": batch["versions"],
            "created": batch["created"],
            "updated": batch["updated"],
            "receipts": self.store.receipts_for_batch(batch_id),
            "dispatches": self.store.dispatches_of_batch(batch_id),
            "notices": self.store.notices_of_batch(batch_id),
        }

    def _require_batch(self, batch_id):
        batch = self.store.get_batch(batch_id)
        if batch is None:
            raise NotFound("批组不存在", {"batch": batch_id})
        return batch

    # ------------------------------------------------------------------
    # 作者视图：按授权范围返回
    # ------------------------------------------------------------------
    def author_view(self, actor, at=None):
        at = self._at(at)
        visible_papers = {w["id"] for w in self.store.list_works() if actor in w["owners"]}
        papers = []
        for work in self.store.list_works():
            in_scope = work["id"] in visible_papers
            manuscripts = []
            for manuscript in self.store.manuscripts_of_paper(work["id"]):
                eval_ = evaluate_manuscript(self.store, manuscript["id"], at)
                public_batches = [b["id"] for b in self.store.batches_for_versions([manuscript["id"]])
                                  if b["state"] == STATE_VISIBLE]
                entry = {"manuscript": manuscript["id"], "version": manuscript["version"],
                         "public": bool(public_batches), "index_batches": public_batches,
                         "level_now": eval_["level"]}
                if in_scope:
                    entry["abstract_refs"] = manuscript["abstract_refs"]
                    entry["extras"] = manuscript["extras"]
                    entry["review"] = eval_
                    entry["confidential_reasons"] = eval_["blockers"]
                    entry["sessions"] = self._waiting_sessions(manuscript["id"], at)
                else:
                    # 无授权方只能看到是否公开，未公开内容与保密原因不返回
                    entry["abstract_refs"] = None
                    entry["extras"] = None
                    entry["confidential_reasons"] = None
                manuscripts.append(entry)
            papers.append({"paper": work["id"], "in_scope": in_scope,
                          "manuscripts": manuscripts})
        return {"actor": actor, "at": format_time(at), "papers": papers}

    def _waiting_sessions(self, manuscript_id, at):
        waiting = []
        for session in self.store.list_sessions():
            in_candidates = any(manuscript_id in group for group in session["candidates"])
            if not in_candidates:
                continue
            review = self.review_session(session["id"], at)
            chosen = [s["chosen"] for s in review["slots"]]
            waiting.append({
                "session": session["id"],
                "grouped": session["grouped"],
                "publish": session["publish"],
                "status": review["status"],
                "chosen_in_session": manuscript_id in chosen,
            })
        return waiting


def _normalize_receipt(rec):
    return {
        "id": rec["id"],
        "batch": rec["batch"],
        "phase": rec["phase"],
        "versions": list(rec.get("versions", [])),
        "grant_ids": list(rec.get("grant_ids", [])),
        "happened": rec["happened"],
        "arrived": rec["arrived"],
    }


def _slot_eta(slot):
    pick_level = slot["chosen_level"]
    for opt in slot["options"]:
        if opt["manuscript"] == slot["chosen"]:
            return parse_time(opt["full_ready_at"]) if pick_level == "full" and opt["full_ready_at"] \
                else parse_time(opt["abstract_ready_at"]) if opt["abstract_ready_at"] else None
    return None


def _slot_recoverable_at(options, at):
    """槽位当前无可选版本时，给出最早的未来恢复时刻（无则 None）。"""
    candidates = []
    for opt in options:
        for value in (opt["abstract_ready_at"], opt["full_ready_at"]):
            if value is not None and parse_time(value) > at:
                candidates.append(value)
    return min(candidates) if candidates else None


def _plan_view(entry):
    if entry is None:
        return None
    return {"version": entry["version"], "moved_publish": entry["moved_publish"],
            "removed": entry["removed"], "reason": entry["reason"]}
