"""
Evidence REST seam (PROP-009 §4.3). Part of the removable Ontology Suite — registered beside
the Trust Membrane routes only when the suite is present.

Endpoints (mounted under /api):
  GET /v1/evidence/turn/<session_id>/<turn>
      One answer's evidence: every signed part it used, each read "when answered" (the sealed
      bill of materials) and "now" (resolved live), plus the turn's chain verification.
      The caller's own sessions only — another user's turn is 404.
  GET /v1/evidence[?kind=&state=&q=&scope=system]
      Every current signature the caller may see, read now, worst first, with a summary in
      words and how many answers used each artefact (and how many its current version).
  GET|POST /v1/evidence/ledger
      The hash-chained evidence ledger, verified now, with a signed checkpoint of its tip, who
      holds the signing key (custody) and whether checkpoints are published outside this host.
  POST /v1/evidence/ledger/anchor
      Publish a signed checkpoint now (admin; anchoring must be configured).
  POST /v1/evidence/export
      A portable bundle anyone can verify offline (scripts/verify_evidence.py or the static
      verifier page): attestations with the bytes they sign (up to a size limit), answers with
      their sealed chain and bill of materials, and the public keys.
  GET /v1/evidence/artifact/<kind>/<artifact_id>/usage[?scope=system]
      Which answers used this artefact, and how many used the version it has now. The
      caller's own answers; ``scope=system`` (VIEW_ALL_SESSIONS) counts every account's.
"""

from __future__ import annotations

import contextlib
import asyncio
import logging

from quart import Blueprint, jsonify, request

from trusted_data_agent.core.trust import evidence_index as _index
from trusted_data_agent.core.trust import evidence_view as _view

logger = logging.getLogger("quart.app")

evidence_bp = Blueprint("evidence_bp", __name__)


def _current_user():
    try:
        from trusted_data_agent.auth.middleware import get_current_user
        user = get_current_user()
        return user if (user and getattr(user, "id", None)) else None
    except Exception:  # noqa: BLE001
        return None


def _can_view_all(user) -> bool:
    try:
        from trusted_data_agent.auth.features import get_user_features, Feature
        return Feature.VIEW_ALL_SESSIONS in get_user_features(user)
    except Exception:  # noqa: BLE001 — unknown features are not a grant
        return False


def _usernames(uuids):
    try:
        from trusted_data_agent.api.rest_routes import _usernames_for
        return _usernames_for(uuids)
    except Exception:  # noqa: BLE001
        return {}


def _signer_uuids(view: dict) -> set:
    out = set()
    for g in view.get("groups") or []:
        for e in g.get("items") or []:
            sg = (e.get("then") or {}).get("signer") or {}
            if sg.get("signer_uuid"):
                out.add(sg["signer_uuid"])
    return out


@evidence_bp.route("/v1/evidence/turn/<session_id>/<int:turn>", methods=["GET"])
async def get_turn_evidence(session_id: str, turn: int):
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    session_data = None
    try:
        from trusted_data_agent.core import session_manager
        session_data = await session_manager.get_session(user.id, session_id)
    except Exception:  # noqa: BLE001 — a missing session falls back to the index record
        session_data = None
    view = await asyncio.to_thread(_view.turn_view, user.id, session_id, turn, session_data)
    if view is None:
        return jsonify({"error": "not_found"}), 404
    names = _usernames(_signer_uuids(view))
    for g in view.get("groups") or []:
        for e in g.get("items") or []:
            sg = (e.get("then") or {}).get("signer")
            if sg and sg.get("signer_uuid"):
                sg["signer_name"] = names.get(sg["signer_uuid"])
    return jsonify(view)


def _current_hash(kind: str, artifact_id: str, owner_uuid: str):
    try:
        if kind in ("agent", "agent_deployment"):
            d = _view._current_agent(artifact_id, owner_uuid, None)
            if not d:
                return None
            return d.get("agent_live") if kind == "agent" else (d.get("deployment") or {}).get("hash")
        from trusted_data_agent.core.trust import content as _content
        return _content.live_hash(kind, artifact_id)
    except Exception:  # noqa: BLE001
        return None


@evidence_bp.route("/v1/evidence/artifact/<kind>/<path:artifact_id>/usage", methods=["GET"])
async def get_artifact_usage(kind: str, artifact_id: str):
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    system = request.args.get("scope") == "system"
    if system and not _can_view_all(user):
        return jsonify({"error": "VIEW_ALL_SESSIONS required for scope=system"}), 403
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
    except ValueError:
        limit = 50
    current = await asyncio.to_thread(_current_hash, kind, artifact_id, user.id)
    out = await asyncio.to_thread(_index.usage, kind, artifact_id,
                                  None if system else user.id, current, limit)
    out["scope"] = "system" if system else "mine"
    out["current_hash"] = current
    if system:
        names = _usernames({t["owner_uuid"] for t in out["turns"]})
        for t in out["turns"]:
            t["owner_name"] = names.get(t["owner_uuid"])
    else:
        for t in out["turns"]:
            t.pop("owner_uuid", None)
    return jsonify(out)


_NAME_SQL = {
    "ontology": "SELECT name FROM kg_metadata WHERE kg_id=?",
    "agent_pack": "SELECT name FROM agent_pack_installations WHERE id=?",
    "flow": "SELECT name FROM flows WHERE flow_id=?",
    "dashboard": "SELECT name FROM dashboards WHERE id=?",
    "canvas": "SELECT title FROM canvases WHERE canvas_id=?",
    "knowledge": "SELECT name FROM collections WHERE id=?",
    "planner": "SELECT name FROM collections WHERE id=?",
}


def _label_for(kind: str, aid: str, acting, names: dict, tag_maps: dict):
    """How the estate names an artefact: what a turn last recorded, else the artefact's own
    name from its store, else its id. Never raises."""
    if (kind, aid) in names and kind not in ("agent", "agent_deployment"):
        return names[(kind, aid)]
    try:
        if kind in ("agent", "agent_deployment"):
            from trusted_data_agent.core.trust import composition as _comp
            if acting not in tag_maps:
                tag_maps[acting] = _comp.profile_id_to_tag(acting)
            tag = tag_maps[acting](aid)
            return f"@{tag}" if tag else None
        if kind in ("data_product", "action_type", "ontology_extension"):
            return aid.split("::")[-1]
        sql = _NAME_SQL.get(kind)
        if sql:
            from trusted_data_agent.core import sqlite_util
            from trusted_data_agent.core.trust import store as trust_store
            with contextlib.closing(sqlite_util.connect(trust_store._db_path(None))) as conn:
                row = conn.execute(sql, (aid,)).fetchone()
            return row[0] if row and row[0] else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _estate(user_uuid: str, system: bool, kind_filter, state_filter, query) -> dict:
    """Every current signature the caller may see, read NOW, worst first — resolved in one
    ``sqlite_util.reuse_connections()`` scope (each row resolves its artefact's live state,
    which opens several connections; see ``trust_routes._resolve_agent_trust_rows``)."""
    from trusted_data_agent.core import sqlite_util
    with sqlite_util.reuse_connections():
        return _estate_in_scope(user_uuid, system, kind_filter, state_filter, query)


def _estate_in_scope(user_uuid: str, system: bool, kind_filter, state_filter, query) -> dict:
    """Every current signature the caller may see, read NOW, worst first.

    Visibility: the caller's own agents (owner-scoped), every artefact they own (the Trust
    Membrane's own ownership gate, which fails closed), and platform-wide kinds (skills,
    extensions). ``system`` (VIEW_ALL_SESSIONS) lists every account's.
    """
    from trusted_data_agent.core.trust import store as trust_store
    from trusted_data_agent.api import trust_routes as TR
    owner = None if system else user_uuid
    recs = trust_store.list_current(owner_uuid=owner)
    names = _index.labels(owner)
    counts = _index.usage_counts(owner)
    signer_names = _usernames({r.get("signer_uuid") for r in recs if r.get("signer_uuid")})
    tag_maps: dict = {}
    rows = []
    for rec in recs:
        kind, aid = rec.get("kind"), str(rec.get("artifact_id"))
        rec_owner = rec.get("owner_uuid") or None
        if kind_filter and kind != kind_filter:
            continue
        if not system and kind not in trust_store.OWNER_SCOPED_KINDS:
            try:
                ok, _r, _s = TR._assert_owner(kind, aid, user_uuid)
            except Exception:  # noqa: BLE001 — unknown ownership is not visibility
                ok = False
            if not ok:
                continue
        acting = rec_owner if kind in trust_store.OWNER_SCOPED_KINDS else user_uuid
        try:
            if kind == "agent_deployment":
                from trusted_data_agent.core.trust import composition as _comp
                _d = _comp.resolve_deployment(aid, acting)
                st, cur = _d.get("state"), _d.get("hash")
            else:
                st = TR._resolve_artifact_state(kind, aid, user_uuid=acting)
                cur = TR._artifact_live_hash(kind, aid, user_uuid=acting)
                cur = cur if isinstance(cur, str) else None
        except Exception:  # noqa: BLE001
            st, cur = "changed", None   # fail closed, as the badge does
        c = counts.get((kind, aid)) or {}
        uc = {"answers": c.get("answers", 0),
              "since_current": (c.get("hashes") or {}).get(cur, 0) if cur else None}
        label = _label_for(kind, aid, acting, names, tag_maps)
        row = _view.estate_row(rec, st, label, uc,
                               trust_store.key_status(rec.get("signer_fingerprint")),
                               signer_names.get(rec.get("signer_uuid")))
        if system:
            row["owner_uuid"] = rec_owner
        rows.append(row)
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r["label"] or "").lower() or q in r["artifact_id"].lower()]
    summary = _view.estate_summary(rows)
    if state_filter:
        rows = [r for r in rows if r["worst"] == state_filter or state_filter in r["flags"]]
    return {"summary": summary, "rows": _view.sort_estate(rows),
            "scope": "system" if system else "mine"}


@evidence_bp.route("/v1/evidence", methods=["GET"])
async def get_evidence_estate():
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    system = request.args.get("scope") == "system"
    if system and not _can_view_all(user):
        return jsonify({"error": "VIEW_ALL_SESSIONS required for scope=system"}), 403
    out = await asyncio.to_thread(_estate, user.id, system, request.args.get("kind") or None,
                                  request.args.get("state") or None, request.args.get("q") or None)
    return jsonify(out)


def _visible(kind: str, aid: str, user_uuid: str) -> bool:
    from trusted_data_agent.core.trust import store as trust_store
    if kind in trust_store.OWNER_SCOPED_KINDS:
        return True      # the store read is owner-scoped already
    try:
        from trusted_data_agent.api import trust_routes as TR
        return bool(TR._assert_owner(kind, aid, user_uuid)[0])
    except Exception:  # noqa: BLE001 — unknown ownership is not visibility
        return False


@evidence_bp.route("/v1/evidence/export", methods=["POST"])
async def export_evidence():
    """A portable evidence bundle (PROP-009 §4.4) for answers, artefacts, or a preset.

    Body ``{turns: [{session_id, turn}], artifacts: [{kind, artifact_id}],
    content: "limit"|"all"|"none", preset: "answer"|"audit"|null, title, include_receipt,
    include_audit_pack}``.
    ``preset: "audit"`` exports every signature the caller may see and embeds the signed
    audit pack (``POST /v1/assurance/audit-pack``, unchanged) as ``audit_pack`` +
    ``audit_pack_attestation``, so the verifier checks both from one file. Only the caller's own answers and visible artefacts are
    exported; anything else is listed in ``skipped`` with the reason.
    """
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    body = await request.get_json(silent=True) or {}
    content = body.get("content") or "limit"
    if content not in ("limit", "all", "none"):
        return jsonify({"error": "content must be limit, all or none"}), 400
    turns_req = body.get("turns") or []
    arts_req = body.get("artifacts") or []
    if not isinstance(turns_req, list) or not isinstance(arts_req, list) or len(turns_req) + len(arts_req) > 1000:
        return jsonify({"error": "turns and artifacts must be lists of at most 1000 entries together"}), 400
    skipped = []
    turns = []
    from trusted_data_agent.core import session_manager
    for t in turns_req:
        sid, tn = str((t or {}).get("session_id") or ""), (t or {}).get("turn")
        try:
            sess = await session_manager.get_session(user.id, sid) if sid else None
        except Exception:  # noqa: BLE001
            sess = None
        turn = _view.find_turn(sess, tn) if sess else None
        if turn is None:
            skipped.append({"session_id": sid, "turn": tn, "reason": "no such answer of yours"})
            continue
        turns.append((sid, turn))
    est_labels: dict = {}
    if body.get("preset") == "audit":
        est = await asyncio.to_thread(_estate, user.id, False, None, None, None)
        arts_req = [{"kind": r["kind"], "artifact_id": r["artifact_id"]} for r in est["rows"] if r.get("signed")]
        est_labels = {(r["kind"], r["artifact_id"]): r.get("label") for r in est["rows"]}
    arts = []
    for a in arts_req:
        kind, aid = str((a or {}).get("kind") or ""), str((a or {}).get("artifact_id") or "")
        if not kind or not aid or not await asyncio.to_thread(_visible, kind, aid, user.id):
            skipped.append({"kind": kind, "artifact_id": aid, "reason": "not an artefact of yours"})
            continue
        arts.append((kind, aid))
    from trusted_data_agent.core.trust import evidence_bundle as _bundle
    labels = await asyncio.to_thread(_index.labels, user.id)
    labels.update({k: v for k, v in est_labels.items() if v})
    out = await asyncio.to_thread(_bundle.build, user.id, turns=turns, artifacts=arts,
                                  content=content, labels=labels,
                                  title=(body.get("title") or None),
                                  include_receipt=bool(body.get("include_receipt")))
    out["skipped"] = skipped
    out["preset"] = body.get("preset") or None
    if body.get("preset") == "audit" and body.get("include_audit_pack", True):
        # The deployment's signed audit pack travels inside the bundle (built by its own
        # route, unchanged) so ONE file carries the posture and the signatures behind it.
        try:
            from trusted_data_agent.api import rest_routes as _rr
            res = await _rr.assurance_audit_pack()
            resp = res[0] if isinstance(res, tuple) else res
            ap = await resp.get_json()
            if isinstance(ap, dict) and "audit_pack" in ap:
                out["audit_pack"] = ap["audit_pack"]
                out["audit_pack_attestation"] = ap.get("attestation")
        except Exception as e:  # noqa: BLE001 — the bundle stands without it
            logger.debug(f"evidence export: audit pack unavailable: {e}")
    return jsonify(out)


def _ledger_status(detail: bool, checkpoints=None) -> dict:
    from trusted_data_agent.core.trust import store as trust_store, ledger as _ledger
    from trusted_data_agent.core.trust import anchoring as _anchoring
    conn = trust_store._connect()
    try:
        # Every tip published outside this host is re-proved here beside any the caller sent.
        anchored = [a for a in _anchoring.anchors(conn) if a["ok"]]
        cps = list(checkpoints or []) + [{"seq": a["seq"], "tip_hash": a["tip_hash"]} for a in anchored]
        v = _ledger.verify(conn, checkpoints=cps)
        cp = _ledger.checkpoint(conn)
        last = _anchoring.anchors(conn, limit=1)
    finally:
        conn.close()
    try:
        from trusted_data_agent.core.signing import get_signer
        v["custody"] = get_signer().describe()
    except Exception:  # noqa: BLE001
        v["custody"] = {"signer": "local", "key_on_host": True, "available": None}
    acfg = _anchoring.config()
    lst = last[0] if last else {}
    v["anchoring"] = {"enabled": _anchoring.enabled(), "sink": acfg["sink"] or None,
                      "interval_seconds": acfg["interval"], "anchors": len(anchored),
                      "last_seq": lst.get("seq"), "last_at": lst.get("anchored_at"),
                      "last_ok": lst.get("ok") if lst else None, "last_error": lst.get("error")}
    if not detail:
        for k in ("breaks", "edited", "removed", "unledgered"):
            v.pop(k, None)
    v["checkpoint"] = cp
    v["words"] = _view.ledger_words(v)  # reads v["anchoring"] (a failed last anchor is a warn)
    return v


@evidence_bp.route("/v1/evidence/ledger", methods=["GET", "POST"])
async def get_evidence_ledger():
    """The evidence ledger, verified now (PROP-009 §4.5): ``intact``, the entry counts, a signed
    checkpoint of the tip, and counts of breaks / edited / removed / unledgered records. POST
    ``{checkpoints:[{seq, tip_hash}]}`` (taken from earlier exports) also proves the ledger
    still contains each earlier tip. The lists naming WHICH records moved are returned only
    with ``VIEW_ALL_SESSIONS`` — the ledger is deployment-wide."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    cps = None
    if request.method == "POST":
        body = await request.get_json(silent=True) or {}
        cps = body.get("checkpoints") if isinstance(body.get("checkpoints"), list) else None
    out = await asyncio.to_thread(_ledger_status, _can_view_all(user), cps)
    return jsonify(out)


@evidence_bp.route("/v1/evidence/ledger/anchor", methods=["POST"])
async def anchor_evidence_ledger():
    """Publish a signed checkpoint of the ledger tip now (PROP-009 §4.6). Admin
    (``VIEW_ALL_SESSIONS``): the ledger is deployment-wide. 409 when anchoring is off."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if not _can_view_all(user):
        return jsonify({"error": "Only an administrator can anchor the evidence ledger"}), 403
    from trusted_data_agent.core.trust import anchoring as _anchoring
    if not _anchoring.enabled():
        return jsonify({"error": "anchoring is off — set TDA_EVIDENCE_ANCHOR and TDA_EVIDENCE_ANCHOR_TARGET",
                        "code": "anchoring_off"}), 409
    body = await request.get_json(silent=True) or {}
    out = await asyncio.to_thread(_anchoring.anchor_now, bool(body.get("force")))
    return jsonify(out)
