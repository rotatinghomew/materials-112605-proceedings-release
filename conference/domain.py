"""领域判定：签认范围、专利时钟与稿件可公开性。

规则来源见 docs/exchange.md：

* 签认按版本生效，scope 覆盖该版本批准的内容集合，缺任一权利人签名则内容不可公开；
* 专利办可对同一结果多次发时钟，以「当时已收到」的最新一条为准（申请变更后的新日期）；
* 摘要直接引用的结果若不可公开，整份稿件不能以无图简版发出；
* 简版（abstract-only）只需要摘要本身及其引用结果的授权，不携带补充件。
"""
from .errors import Conflict, NotFound, ValidationFailed
from .timeutil import latest, parse_time

ABSTRACT = "abstract"


def load_fixture(store, data):
    """把交换资料中的基础集合写入空存储；receipts 由对账流程独立处理。"""
    if not store.is_empty():
        raise Conflict("存储已存在资料，拒绝覆盖")
    for work in data.get("works", []):
        store.add_work(work)
    for piece in data.get("pieces", []):
        store.add_piece(piece)
    for manuscript in data.get("manuscripts", []):
        _require_refs_exist(store, manuscript["abstract_refs"] + manuscript["extras"])
        store.add_manuscript(manuscript)
    for consent in data.get("consents", []):
        store.add_consent(consent)
    for embargo in data.get("embargo", []):
        store.add_embargo(embargo)
    for session in data.get("sessions", []):
        for group in session["candidates"]:
            for manuscript_id in group:
                if store.get_manuscript(manuscript_id) is None:
                    raise ValidationFailed("场次候选引用了不存在的稿件",
                                           {"manuscript": manuscript_id})
        store.add_session(session)


def _require_refs_exist(store, piece_ids):
    for piece_id in piece_ids:
        if store.get_piece(piece_id) is None:
            raise ValidationFailed("引用了不存在的图表或结果", {"piece": piece_id})


def governing_embargo(store, content_id, at):
    """返回 at 时刻对某内容生效的专利时钟（最新收到且已到达的一条）。"""
    candidates = []
    for rec in store.embargoes_for_content(content_id):
        received = parse_time(rec["received"])
        if received <= at:
            candidates.append((received, rec["id"], rec))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _piece_owners(store, piece_id):
    piece = store.get_piece(piece_id)
    if piece is None:
        raise ValidationFailed("引用了不存在的内容", {"piece": piece_id})
    return piece["owners"]


def evaluate_piece(store, manuscript, piece_id, at):
    """评估某版本中单个图表/结果的公开条件。"""
    owners = _piece_owners(store, piece_id)
    consents = store.consents_for_version(manuscript["id"])
    signed_by = {c["actor"]: c for c in consents if parse_time(c["signed"]) <= at}

    missing = []
    authorizing = []
    for owner in owners:
        consent = signed_by.get(owner)
        if consent is None or piece_id not in consent["scope"]:
            missing.append(owner)
        else:
            authorizing.append(consent["id"])

    embargo = governing_embargo(store, piece_id, at)
    embargoed_until = parse_time(embargo["earliest"]) if embargo else None
    consented = not missing
    releasable = consented and (embargoed_until is None or embargoed_until <= at)
    reasons = []
    if missing:
        reasons.append({"type": "missing_consent", "owners": missing})
    if embargo is not None and embargoed_until > at:
        reasons.append({"type": "embargo", "embargo": embargo["id"],
                        "earliest": embargo["earliest"]})
    return {
        "piece": piece_id,
        "owners": owners,
        "consented": consented,
        "missing_owners": missing,
        "authorizing_consents": authorizing,
        "embargo": embargo["id"] if embargo else None,
        "embargo_until": embargo["earliest"] if embargo else None,
        "releasable_now": releasable,
        "reasons": reasons,
    }


def _abstract_consent(store, manuscript, at):
    """摘要本身需要论文全体权利人在该版本签认 abstract。"""
    work = store.get_work(manuscript["paper"])
    if work is None:
        raise NotFound("论文不存在", {"paper": manuscript["paper"]})
    consents = store.consents_for_version(manuscript["id"])
    signed_by = {c["actor"]: c for c in consents if parse_time(c["signed"]) <= at}
    missing = [owner for owner in work["owners"]
               if ABSTRACT not in signed_by.get(owner, {"scope": []})["scope"]]
    authorizing = [c["id"] for c in consents
                   if ABSTRACT in c["scope"] and parse_time(c["signed"]) <= at]
    return {
        "consented": not missing,
        "missing_owners": missing,
        "authorizing_consents": sorted(authorizing),
    }


def evaluate_manuscript(store, manuscript_id, at):
    """给出稿件在 at 时刻的完整公开评估。

    层级：
    * full        —— 摘要及全部补充件现可公开；
    * abstract    —— 仅简版可公开，且摘要没有引用受阻结果；
    * blocked     —— 摘要引用了不能公开的结果，任何形式都不能发。
    """
    manuscript = store.get_manuscript(manuscript_id)
    if manuscript is None:
        raise NotFound("稿件版本不存在", {"manuscript": manuscript_id})

    abstract = _abstract_consent(store, manuscript, at)
    abstract_pieces = [evaluate_piece(store, manuscript, pid, at)
                       for pid in manuscript["abstract_refs"]]
    extra_pieces = [evaluate_piece(store, manuscript, pid, at)
                    for pid in manuscript["extras"]]

    abstract_refs_blocked = any(not p["releasable_now"] for p in abstract_pieces)
    extras_blocked = any(not p["releasable_now"] for p in extra_pieces)
    abstract_ok = abstract["consented"] and not abstract_refs_blocked

    if abstract_ok and not extras_blocked:
        level = "full"
    elif abstract_ok:
        level = "abstract"
    else:
        level = "blocked"

    # 简版可发时间：摘要授权齐备且摘要引用结果全部解除时钟
    abstract_ready_at = None
    if abstract["consented"]:
        abstract_ready_at = _earliest_all(store, manuscript["abstract_refs"], at)
    # 全版可发时间：再叠加补充件
    full_ready_at = None
    if abstract["consented"] and not abstract["missing_owners"]:
        full_ready_at = _earliest_all(store,
                                      manuscript["abstract_refs"] + manuscript["extras"], at)

    blockers = []
    if not abstract["consented"]:
        blockers.append({"type": "missing_abstract_consent",
                         "owners": abstract["missing_owners"]})
    blockers.extend(p["reasons"][0] for p in abstract_pieces if not p["releasable_now"])
    blockers.extend(p["reasons"][0] for p in extra_pieces if not p["releasable_now"])

    return {
        "manuscript": manuscript["id"],
        "paper": manuscript["paper"],
        "version": manuscript["version"],
        "at": at.isoformat(),
        "level": level,
        "abstract_consent": abstract,
        "abstract_refs": abstract_pieces,
        "extras": extra_pieces,
        "abstract_ready_at": abstract_ready_at.isoformat() if abstract_ready_at else None,
        "full_ready_at": full_ready_at.isoformat() if full_ready_at else None,
        "blockers": blockers,
    }


def _earliest_all(store, piece_ids, at):
    """假设授权齐备，等待所有内容专利时钟解除的最早时刻。

    取各内容生效时钟 earliest 的最大者；没有时钟的内容不增加等待。
    """
    moment = None
    for piece_id in piece_ids:
        embargo = governing_embargo(store, piece_id, at)
        if embargo is not None:
            moment = latest(moment, parse_time(embargo["earliest"]))
    return latest(moment, at)
