"""
PROP-008 OBDA-ACT — performing a signed action (increment B3).

Binding design: docs/proposals/PROP-008-act-business-operating-model.md §5.4. ONE seam
every face calls (the agent's ``TDA_Action`` and the REST face land in B4; the review
approval calls it now), so authority, posture, transaction and receipt cannot differ by
caller:

1. **OBDA-MODEL · OBDA-ACT on**, the action exists on the graph, and its attestation is **signed and
   intact** — an unsigned or changed contract is never performed;
2. **the grant is the list** — an agent may perform an action only when its
   ``authorityConfig.actions.types`` names it (a person acting through REST acts with
   their own authority over their own graph);
3. **the connection** — a Database Connection the caller owns; on Teradata the login
   must HOLD the manifest's role (``DBC.RoleMembersV``), and the session acts under it
   (``SET ROLE``) — A6 proved what that role grants, the platform never widens it;
4. **compile** (B2) — a refusal is recovery-shaped and is the outcome;
5. **the posture ladder, verbatim** — ``observe`` records what would be written and
   touches nothing; ``review`` evaluates the preconditions and PARKS the compiled write
   in the review queue, which performs it only on the owner's approval
   (:func:`perform_parked`); ``annotate`` and ``enforce`` write, in ONE transaction
   with every check, and roll back on any check that does not hold (B2's runner);
6. **the record** — every invocation (performed, observed, parked or refused) is
   appended to the turn's action record; a PERFORMED one is sealed into the turn's
   provenance chain as an ``action.performed`` step (``ProvenanceChain.finalize``) and
   stamped on the turn (``turn['actions']``) at the one persistence chokepoint, so
   offline verification re-derives the receipt from the stamp through
   :func:`receipt_content`.

Deliberately NOT through the agent's data tool: a stateless tool runs one statement
per call under autocommit, so it cannot hold the preconditions, the write, the
postconditions and the ledger row in one transaction — and the rollback IS the safety
property. The SQL gate's part is the refusing side (``deny_writes`` unchanged, and
``actions_exclusive`` refuses every raw write — agent_authority).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

ACTION_TYPE = "action_type"

#: The outcomes an invocation can have — closed, so the lane, the stamp and a reader
#: agree on what each word means.
OUTCOMES = ("performed", "observed", "parked", "refused", "prepared")

#: The review-queue source for a parked write (never releasable into a conversation —
#: its "original" is a write, not an answer).
REVIEW_SOURCE = "action"


class ActionPerformError(ValueError):
    """A precondition of performing that no posture can relax (the action is unknown,
    untrusted or not granted; no connection). ``code`` is machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


PERFORM_CODES = frozenset({
    "act_off", "not_found", "invalid_manifest", "not_trusted", "not_granted",
    "connection_unavailable", "role_not_held", "parked_drifted", "not_parked",
    "not_undoable",
})


def _fail(code: str, message: str) -> None:
    raise ActionPerformError(code, message)


# ── the turn's action record (contextvars — the governed-SQL registry precedent) ─
# A MUTABLE list created at the turn boundary and MUTATED, never re-``.set()``:
# contextvars propagate parent→child only, so an append inside a child task (a
# component handler, a parallel phase) must reach the chokepoints by reference.

_TURN_ACTIONS: "contextvars.ContextVar[Optional[List[Dict[str, Any]]]]" = \
    contextvars.ContextVar("act_turn_actions", default=None)


def begin_turn():
    """Start the turn's action record. Returns a reset token."""
    return _TURN_ACTIONS.set([])


def end_turn(token) -> None:
    try:
        _TURN_ACTIONS.reset(token)
    except Exception:  # noqa: BLE001
        pass


def turn_active() -> bool:
    """Whether this turn's action record is open — "no act this turn" is evidence only then
    (outside a turn there is no record to read, not an empty one)."""
    return _TURN_ACTIONS.get() is not None


def turn_actions() -> List[Dict[str, Any]]:
    """The records of this turn's invocations (empty outside a turn)."""
    return list(_TURN_ACTIONS.get() or [])


def _record(rec: Dict[str, Any]) -> Dict[str, Any]:
    cur = _TURN_ACTIONS.get()
    if cur is not None:
        cur.append(rec)
    return rec


def record_refusal(action: Optional[str], code: str, message: str, *,
                   session_id: Optional[str] = None, turn_id: Any = None) -> Dict[str, Any]:
    """A refusal raised before anything compiled (not granted, not trusted, no connection, …)
    is still a refused act of this turn — BOUNDED names it beside the ones that ran."""
    return _record({"action": action, "outcome": "refused", "refused_check": code, "refusal": message,
                    "session_id": session_id, "turn_id": turn_id})


def summarize(records: Optional[List[Dict[str, Any]]],
              confirmations: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The ONE reading of a turn's acts, read by the BOUNDED station, the capsule row and the
    Live Status card: what the agent did on the turn (``records``) folded with what a person
    confirmed afterwards (``confirmations`` = ``turn['action_confirmations']``) — a prepared
    act that was confirmed, and a parked act the owner approved, read as how they ended,
    never twice."""
    acts: List[Dict[str, Any]] = []
    conf = confirmations if isinstance(confirmations, dict) else {}
    seen = set()
    taken = set()   # one invocation is ONE act, however often the turn recorded it
    for r in (records or []):
        if not isinstance(r, dict):
            continue
        h = r.get("invocation_hash")
        if h and h in taken:
            continue
        if h:
            taken.add(h)
        c = conf.get(h) if h else None
        if isinstance(c, dict) and c.get("outcome"):
            # the turn's own record of this invocation consumes its confirmation — merged when
            # the confirmation says how a prepared or parked act ENDED, never counted twice
            seen.add(h)
            if r.get("outcome") in ("prepared", "parked") \
                    and not (r.get("outcome") == "parked" and c.get("outcome") == "parked"):
                r = {**r, **{k: v for k, v in c.items() if v is not None}, "confirmed": True}
        acts.append(r)
    for h, c in conf.items():
        if h not in seen and isinstance(c, dict) and c.get("outcome"):
            acts.append({**c, "invocation_hash": h, "confirmed": True})
    count = {o: sum(1 for a in acts if a.get("outcome") == o) for o in OUTCOMES}
    refused = [a for a in acts if a.get("outcome") == "refused"]
    return {"acts": acts, "count": count,
            "performed": count["performed"], "refused": count["refused"],
            "parked": count["parked"], "prepared": count["prepared"], "observed": count["observed"],
            "refusals": [f"{a.get('action') or 'an action'}: {a.get('refusal') or a.get('refused_check')}"
                         for a in refused]}


def grant_config(authority_config: Optional[Dict[str, Any]], action_name: str,
                 granted: bool) -> Dict[str, Any]:
    """The agent's ``authorityConfig`` with ``action_name`` added to (or removed from) its
    grant — ``actions.types``, the list that IS the grant. Every other authority key, and
    every other action in the list, is kept as it was (a profile update replaces a block
    wholesale, so the merge happens here, once)."""
    cfg = dict(authority_config) if isinstance(authority_config, dict) else {}
    acts = dict(cfg.get("actions")) if isinstance(cfg.get("actions"), dict) else {}
    types = [str(t) for t in (acts.get("types") or []) if str(t) != action_name]
    if granted:
        types.append(action_name)
    acts["types"] = sorted(set(types))
    cfg["actions"] = acts
    return cfg


def approvers_config(authority_config: Optional[Dict[str, Any]],
                     approvers: List[str]) -> Dict[str, Any]:
    """The agent's ``authorityConfig`` with its designated approvers set (four eyes,
    PROP-008 §5.4a) — ``actions.approvers``; an empty list removes the designation. Every
    other authority key and the grant are kept (the merge happens here, once). Pure."""
    cfg = dict(authority_config) if isinstance(authority_config, dict) else {}
    acts = dict(cfg.get("actions")) if isinstance(cfg.get("actions"), dict) else {}
    names = []
    for n in approvers or []:
        n = str(n or "").strip().lstrip("@")
        if n and n.lower() not in (x.lower() for x in names):
            names.append(n)
    if names:
        acts["approvers"] = names
    else:
        acts.pop("approvers", None)
    cfg["actions"] = acts
    return cfg


def tally_turns(turns: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per agent (the turn's ``profile_tag``), the acts across a window of turns — each turn
    folded through :func:`summarize`, so a confirmed or approved act counts once, as how it
    ended. The Trust Circle's BOUNDED evidence reads this; the turns carry the truth, so a
    confirmation given after the turn was scored is never missed."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in turns or []:
        if not isinstance(t, dict) or not (t.get("actions") or t.get("action_confirmations")):
            continue
        sm = summarize(t.get("actions") or [], t.get("action_confirmations") or {})
        tag = t.get("profile_tag") or "?"
        cell = out.setdefault(tag, {o: 0 for o in OUTCOMES} | {"turns": 0, "refusals": []})
        cell["turns"] += 1
        for o in OUTCOMES:
            cell[o] += sm["count"][o]
        for r in sm["refusals"]:
            if len(cell["refusals"]) < 5 and r not in cell["refusals"]:
                cell["refusals"].append(r)
    return out


def turn_act_hashes(turns: List[Dict[str, Any]]) -> set:
    """The invocations the turns already hold (folded with their confirmations) — an act-log
    entry for one of them (an approval in the Assurance Inbox of a conversation's parked
    write) is the SAME act, never a second one."""
    out = set()
    for t in turns or []:
        if isinstance(t, dict) and (t.get("actions") or t.get("action_confirmations")):
            for a in summarize(t.get("actions") or [], t.get("action_confirmations") or {})["acts"]:
                if a.get("invocation_hash"):
                    out.add(a["invocation_hash"])
    return out


def tally_by_action(turns: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Per action type, how its invocations ended across a window of turns (the Designer's
    "in use" step) — the same fold as :func:`tally_turns`."""
    out: Dict[str, Dict[str, int]] = {}
    for t in turns or []:
        if not isinstance(t, dict) or not (t.get("actions") or t.get("action_confirmations")):
            continue
        for a in summarize(t.get("actions") or [], t.get("action_confirmations") or {})["acts"]:
            name = a.get("action") or "?"
            cell = out.setdefault(name, {o: 0 for o in OUTCOMES})
            if a.get("outcome") in cell:
                cell[a["outcome"]] += 1
    return out


def act_words(a: Dict[str, Any]) -> str:
    """One act in words — ``<name> v<n> · 1 row · enforce · receipt <hash>`` (the capsule
    row and the Live Status card speak the same line)."""
    o = a.get("outcome")
    head = f"{a.get('action') or 'action'}" + (f" v{a.get('version')}" if a.get("version") else "")
    rows = a.get("rows_affected")
    tail = f" · receipt {str(a.get('invocation_hash'))[:12]}" if a.get("invocation_hash") else ""
    if o == "performed":
        return f"{head} · {rows or 0} row(s) · {a.get('posture') or 'enforce'}{tail}"
    if o == "parked":
        appr = a.get("approvers")
        appr = ", ".join(appr) if isinstance(appr, (list, tuple)) else (appr or "")
        whose = f"{appr}'s approval" if appr else "the owner's review"
        return f"{head} · parked for {whose} — nothing written yet{tail}"
    if o == "observed":
        return f"{head} · observe — nothing changed{tail}"
    if o == "prepared":
        return f"{head} · prepared — awaiting confirmation{tail}"
    return f"{head} · refused — {a.get('refusal') or a.get('refused_check')}"


#: The Live Status step title per outcome — mirrored by `_assuranceStepTitle` in
#: eventHandlers.js (gate-asserted equal), so a step reads the same live and on reload.
STEP_TITLES = {"prepared": "Action Prepared", "performed": "Action Performed",
               "parked": "Action Parked for Review", "observed": "Action Observed",
               "refused": "Action Refused"}


def emit_step(user_uuid: Optional[str], rec: Dict[str, Any], *, session_id: Optional[str] = None,
              turn_id: Any = None, confirmed_by: Optional[str] = None) -> None:
    """Push one act as a Live Status step over the per-user notification channel (the step is
    emitted from inside a component tool or from a person's Confirm — neither runs in an open
    answer stream). Session-guarded on the client; best-effort, never raises."""
    if not user_uuid or not isinstance(rec, dict) or not rec.get("outcome"):
        return
    try:
        from trusted_data_agent.core.trust_events import broadcast_notice
        details = {k: v for k, v in rec.items()
                   if k in ("action", "version", "outcome", "posture", "rows_affected", "invocation_hash",
                            "refusal", "refused_check", "consequence", "review_id", "confirmed_by")}
        details.update(session_id=session_id or rec.get("session_id"),
                       turn_id=turn_id if turn_id is not None else rec.get("turn_id"))
        if confirmed_by:
            details["confirmed_by"] = confirmed_by
        details["words"] = act_words(rec)
        # the live dial surfaces an act as it happens (a refusal or a park), never a verdict
        details["acted"] = rec.get("outcome") in ("refused", "parked")
        broadcast_notice(user_uuid, "action_record", details["words"], session_id=details["session_id"],
                         notice_type="action_record",
                         extra={"step": STEP_TITLES.get(rec.get("outcome"), "Action"), "details": details,
                                "type": "action_record"})
    except Exception:  # noqa: BLE001
        pass


# ── the receipt (sealed content — ONE function for sealing and verification) ───

#: The fields an ``action.performed`` step seals. A closed tuple — the sealed content
#: must be byte-reproducible from the turn's ``actions`` stamp, so nothing volatile
#: (timestamps, check observations) enters it.
RECEIPT_FIELDS = ("action", "version", "kg_id", "manifest_hash", "statement_hash",
                  "invocation_hash", "posture", "principal", "rows_affected")


def receipt_content(rec: Dict[str, Any]) -> str:
    return json.dumps({k: rec.get(k) for k in RECEIPT_FIELDS},
                      sort_keys=True, separators=(",", ":"), default=str)


def performed_receipts(records: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The records worth a sealed receipt: the invocations that WROTE."""
    return [r for r in (records or []) if isinstance(r, dict) and r.get("outcome") == "performed"]


def describe(rec: Dict[str, Any]) -> str:
    if rec.get("outcome") == "refused" and rec.get("version") is None:
        # refused before anything compiled (not granted, no connection, OBDA-MODEL · OBDA-ACT off, …): there is
        # no version, row count or posture to report — say what refused it instead
        return f"{rec.get('action')} · refused ({rec.get('refused_check')}) — {rec.get('refusal')}"
    return (f"{rec.get('action')} v{rec.get('version')} · {rec.get('outcome')} · "
            f"{rec.get('rows_affected') if rec.get('rows_affected') is not None else 0} row(s) · "
            f"{rec.get('posture')}")


# ── which granted actions park for a person (the REVIEWED station's configuration) ──

def review_postured(nodes: List[Dict[str, Any]], granted) -> List[str]:
    """The granted action types whose manifest posture is ``review`` — the writes this agent
    PARKS for a person instead of performing. Pure: what the Trust Circle's REVIEWED station
    must count beside the answer gates (an agent with only such an action can park work, and
    the station must not read 'cannot park')."""
    allowed = set(granted or ())
    out = []
    for n in nodes or []:
        name = n.get("name")
        m = ((n.get("properties") or {}).get("manifest") or {})
        if name in allowed and (m.get("authority") or {}).get("posture") == "review":
            out.append(name)
    return sorted(out)


async def parking_actions(profile: Dict[str, Any], user_uuid: str) -> Optional[List[str]]:
    """``review_postured`` over this agent's own graph and grant. None when OBDA-MODEL · OBDA-ACT is off (the
    question does not exist); [] when nothing granted parks. Reads the graph the agent's own
    ``TDA_Action`` reads, so the station and the runtime cannot disagree about the actions."""
    if not _act_enabled(profile, user_uuid):
        return None
    from trusted_data_agent.core.agent_authority import scope_for_profile
    granted = scope_for_profile(profile, user_uuid).actions_allowed
    if not granted:
        return []
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler
    store = KnowledgeGraphHandler()._get_store({"profile_id": profile.get("id"), "user_uuid": user_uuid})
    if store is None or not getattr(store, "kg_id", None):
        return []
    nodes = store.list_entities(entity_type="action_type", limit=500)
    if hasattr(nodes, "__await__"):
        nodes = await nodes
    return review_postured(nodes, granted)


# ── loading + checks ────────────────────────────────────────────────────────

def _act_enabled(profile: Optional[Dict[str, Any]] = None, user_uuid: Optional[str] = None) -> bool:
    """OBDA-ACT is on for this agent (the component chain), or for this deployment/user when
    no agent is in play — never a master switch (core/obda_planes)."""
    try:
        from trusted_data_agent.core import obda_planes as _planes
        return _planes.plane_enabled("act", profile if isinstance(profile, dict) else None, user_uuid)
    except Exception:  # noqa: BLE001
        return False


def _load(store, action_name: str):
    from trusted_data_agent.dbdeploy.action_model import ActionManifestError, parse_manifest
    node = store.get_entity_by_name(action_name, ACTION_TYPE)
    if not node:
        _fail("not_found", f"no action type named {action_name!r} on this graph")
    raw = (node.get("properties") or {}).get("manifest") or {}
    try:
        return parse_manifest(raw)
    except ActionManifestError as e:
        _fail("invalid_manifest", f"the stored contract does not parse ({e.code}): {e.message}")


def _trust_state(kg_id: str, action_name: str, db_path: Optional[str]) -> str:
    """The action's trust state in words: ``trusted``, or the state with WHY when the model
    under it moved (``changed — the allowed values of status changed``), so every refusal
    names what to validate again for."""
    from trusted_data_agent.core.trust import actions as _ta
    st = _ta.action_state(kg_id, action_name, db_path=db_path)
    state = str(st.get("state"))
    drift = st.get("drift") or []
    if state != "trusted" and drift:
        if st.get("stale") == "unrecorded":
            return f"{state} — {drift[0]}; validate it again"
        return f"{state} — the model under it changed: {'; '.join(drift)}"
    return state


def _granted(profile: Optional[Dict[str, Any]], action_name: str) -> None:
    from trusted_data_agent.core.agent_authority import scope_for_profile
    scope = scope_for_profile(profile)
    allowed = scope.actions_allowed
    if allowed is None or action_name not in allowed:
        tag = (profile or {}).get("tag") or (profile or {}).get("id") or "this agent"
        have = ", ".join(sorted(allowed)) if allowed else "none"
        _fail("not_granted", f"@{str(tag).lstrip('@')} is not granted {action_name!r} — its granted "
                             f"actions: {have} (authorityConfig.actions.types is the grant)")


def _text_date_shapes(store, m) -> Dict[str, str]:
    """The sampled shape of each writable column, from the graph's own attributes."""
    out: Dict[str, str] = {}
    for a in m.attributes:
        try:
            node = store.get_entity_by_name(f"{m.entity}.{a.name}", "attribute")
            shape = ((node or {}).get("properties") or {}).get("sampled_shape")
            if shape:
                out[a.name] = shape
        except Exception:  # noqa: BLE001
            pass
    return out


# ── the connection ──────────────────────────────────────────────────────────

def open_connection(user_uuid: str, connection_id: str):
    """``(conn, dialect)`` for a Database Connection the caller owns."""
    from trusted_data_agent.core.database_connections import DatabaseConnectionStore
    creds = DatabaseConnectionStore().resolve(user_uuid, connection_id)
    if not creds:
        _fail("connection_unavailable", f"no Database Connection {connection_id!r} for this user")
    driver = (creds.get("driver") or "").lower()
    try:
        if driver == "teradata":
            import teradatasql
            kw = {"host": creds.get("host"), "user": creds.get("username"),
                  "password": creds.get("password")}
            if creds.get("port"):
                kw["dbs_port"] = str(creds["port"])
            return teradatasql.connect(**kw), "teradata"
        if driver == "sqlite":
            import sqlite3
            return sqlite3.connect(creds.get("database") or creds.get("path"), autocommit=True), "sqlite"
    except ActionPerformError:
        raise
    except Exception as e:  # noqa: BLE001
        _fail("connection_unavailable", f"the connection could not be opened: {str(e)[:200]}")
    _fail("connection_unavailable", f"an action writes through a Teradata or SQLite connection, "
                                    f"not {driver or 'an unknown driver'}")


def _teradata_session(conn, m) -> Dict[str, str]:
    """The login must HOLD the manifest's role; the session acts under it. Returns the
    target's dictionary column types (the grain's coercion)."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM DBC.RoleMembersV WHERE Grantee = USER AND RoleName = ?",
                    [m.required_role])
        held = int((cur.fetchone() or [0])[0] or 0)
        if not held:
            _fail("role_not_held", f"the connection's login does not hold {m.required_role} — the "
                                   f"role A6 proved; grant it to the login (the platform never widens it)")
        cur.execute(f'SET ROLE "{m.required_role}"')
        cur.execute("SELECT TRIM(ColumnName), TRIM(ColumnType) FROM DBC.ColumnsV "
                    "WHERE DatabaseName = ? AND TableName = ?", [m.target_db(), m.target_table()])
        return {str(r[0]): str(r[1]) for r in cur.fetchall() or []}
    finally:
        cur.close()


# ── perform ─────────────────────────────────────────────────────────────────

def perform_action(store, kg_id: str, action_name: str, invocation: Dict[str, Any], *,
                   user_uuid: str, profile: Optional[Dict[str, Any]] = None,
                   connection_id: Optional[str] = None,
                   connect_fn: Optional[Callable[[], Any]] = None,
                   session_id: Optional[str] = None, turn_id: Any = None,
                   db_path: Optional[str] = None,
                   expected_invocation_hash: Optional[str] = None) -> Dict[str, Any]:
    """Perform ONE invocation of a signed action. Returns the record (also appended to the
    turn's action record). Raises :class:`ActionPerformError` for what no posture can
    relax; an invocation the compiler or a check refuses is a ``refused`` record.

    ``profile`` is the AGENT performing it (its grant is checked); ``None`` means a person
    acting with their own authority. ``connect_fn() -> (conn, dialect)`` overrides
    ``connection_id`` (tests, and callers that already hold a connection)."""
    if not _act_enabled(profile, user_uuid):
        _fail("act_off", "OBDA-ACT is not enabled for this agent (the OBDA-ACT component is off for this deployment, this user or this agent)")
    m = _load(store, action_name)
    state = _trust_state(kg_id, action_name, db_path)
    if state != "trusted":
        _fail("not_trusted", f"{action_name!r} reads {state} — an action is performed only while its "
                             f"signature is intact; validate and sign it first")
    if profile is not None:
        _granted(profile, action_name)
    principal = (f"agent:{profile.get('id')}" if profile is not None else f"user:{user_uuid}")

    base: Dict[str, Any] = {"action": m.action, "version": m.version, "kg_id": kg_id,
                            "manifest_hash": None, "statement_hash": None, "invocation_hash": None,
                            "posture": m.posture, "principal": principal, "rows_affected": None,
                            "session_id": session_id, "turn_id": turn_id}
    from trusted_data_agent.dbdeploy.action_compiler import ActionInvocationError, compile_action
    from trusted_data_agent.dbdeploy.action_model import manifest_hash
    base["manifest_hash"] = manifest_hash(m)

    if connect_fn is not None:
        conn, dialect = connect_fn()
    elif connection_id:
        conn, dialect = open_connection(user_uuid, connection_id)
    else:
        _fail("connection_unavailable", "an action writes through a Database Connection — name one")
    try:
        ctypes = _teradata_session(conn, m) if dialect == "teradata" else {}
        try:
            c = compile_action(m, invocation, column_types=ctypes,
                               text_date_shapes=_text_date_shapes(store, m), dialect=dialect)
        except ActionInvocationError as e:
            return _record({**base, "outcome": "refused", "refused_check": e.code, "refusal": e.message})
        base.update(statement_hash=c.statement_hash, invocation_hash=c.invocation_hash,
                    receipt=c.receipt())
        if expected_invocation_hash and c.invocation_hash != expected_invocation_hash:
            return _record({**base, "outcome": "refused", "refused_check": "parked_drifted",
                            "refusal": "the invocation no longer compiles to what was confirmed — "
                                       "prepare it again"})

        if m.posture == "observe":
            return _record({**base, "outcome": "observed",
                            "note": "observe — recorded what would be written; nothing was written"})

        from trusted_data_agent.dbdeploy.action_runner import run_compiled
        if m.posture == "review":
            # the write runs inside a transaction that is ALWAYS rolled back — nothing is
            # written, every check is evaluated, and the owner reviews the database's own
            # answer (the before → after diff), never a prediction
            o = run_compiled(conn, c, principal=principal, posture=m.posture, commit=False)
            if not o.held:
                return _record({**base, "outcome": "refused", "refused_check": o.refused_check,
                                "refusal": o.refusal or o.error, "checks": o.checks})
            # a write that changes nothing is never parked for a person to approve
            _nc = no_change(c, o.before)
            if _nc:
                return _record({**base, "outcome": "refused", "refused_check": "no_change",
                                "refusal": _nc, "checks": o.checks})
            cons = consequence(c, o.before, o.rows_affected, o.checks, m.max_rows)
            payload = {"consequence": cons, "target": m.deployed, "operation": m.operation,"kg_id": kg_id, "action": m.action, "version": m.version,
                       "manifest_hash": base["manifest_hash"], "statement_hash": c.statement_hash,
                       "invocation_hash": c.invocation_hash, "invocation": invocation,
                       "connection_id": connection_id, "profile_id": (profile or {}).get("id"),
                       "principal": principal, "receipt": c.receipt()}
            from trusted_data_agent.core import review_queue
            # four eyes: the agent's designated approvers decide, never its owner; a
            # designation that names no active account is refused — a write is never parked
            # where nobody may decide it, and never silently falls back to the owner
            appr_ids, appr_names, appr_missing = resolve_approvers(profile, user_uuid, db_path=db_path)
            if appr_missing:
                return _record({**base, "outcome": "refused", "refused_check": "approver_unknown",
                                "refusal": f"the designated approver {', '.join(appr_missing)} is not an "
                                           f"active account here — nothing was parked or written"})
            rid = review_queue.park(
                user_uuid, source=REVIEW_SOURCE, original_text=json.dumps(payload, sort_keys=True, default=str),
                delivered_text=f"{m.action} v{m.version} on {m.deployed} awaits your approval",
                reason=f"the action's posture is review — {len(c.values)} value(s) for {c.key}",
                categories=[m.action], session_id=session_id, turn_id=turn_id,
                profile_tag=(profile or {}).get("tag"), approvers=appr_ids, db_path=db_path)
            if not rid:
                return _record({**base, "outcome": "refused", "refused_check": "review_queue",
                                "refusal": "the review queue is unavailable — nothing was parked or written"})
            for aid in appr_ids:
                try:
                    from trusted_data_agent.core import trust_events
                    trust_events.broadcast_notice(
                        aid, "action_review_awaits",
                        f"{m.action} awaits your approval — from @{(profile or {}).get('tag') or 'an agent'}",
                        severity="warning")
                except Exception:  # noqa: BLE001 — a notice never decides the park
                    pass
            return _record({**base, "outcome": "parked", "review_id": rid, "checks": o.checks,
                            "consequence": cons, **({"approvers": appr_names} if appr_names else {})})

        o = run_compiled(conn, c, principal=principal, posture=m.posture, commit=True)
        rec = {**base, "rows_affected": o.rows_affected, "checks": o.checks,
               "ledger_written": o.ledger_written}
        if o.committed:
            rec["outcome"] = "performed"
            if m.posture == "annotate":
                rec["disclose"] = True
        else:
            rec.update(outcome="refused", refused_check=o.refused_check or "error",
                       refusal=o.refusal or o.error, rows_affected=None)
        return _record(rec)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def resolve_approvers(profile: Optional[Dict[str, Any]], owner_uuid: str, *,
                      db_path: Optional[str] = None) -> Tuple[List[str], List[str], List[str]]:
    """The accounts that decide this agent's parked writes (four eyes, PROP-008 §5.4a):
    ``(approver ids, approver usernames, names that resolve to no active account)``. The
    owner is never their own approver — listing them adds nothing. An empty designation is
    the single-account baseline (the owner decides)."""
    from trusted_data_agent.core.agent_authority import action_approvers
    names = action_approvers((profile or {}).get("authorityConfig") or {})
    if not names:
        return [], [], []
    from trusted_data_agent.core import sqlite_util
    if db_path:
        path = db_path
    else:
        from trusted_data_agent.core.config import APP_CONFIG
        path = (getattr(APP_CONFIG, "AUTH_DB_PATH", None) or "tda_auth.db").replace("sqlite:///", "")
    conn = sqlite_util.connect(path)
    try:
        found = {r[1].lower(): (r[0], r[1]) for r in conn.execute(
            "SELECT id, username FROM users WHERE is_active = 1 AND lower(username) IN ("
            + ",".join("?" * len(names)) + ")", [n.lower() for n in names]).fetchall()}
    finally:
        conn.close()
    ids, shown, missing = [], [], []
    for n in names:
        hit = found.get(n.lower())
        if not hit:
            missing.append(n)
        elif hit[0] != owner_uuid:
            ids.append(hit[0])
            shown.append(hit[1])
    return ids, shown, missing


def perform_parked(store, user_uuid: str, item: Dict[str, Any], *,
                   profile_lookup: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                   connect_fn: Optional[Callable[[], Any]] = None,
                   approved_by: Optional[str] = None,
                   db_path: Optional[str] = None) -> Dict[str, Any]:
    """The owner approved a parked write: perform it NOW — only if the contract is the one
    that was parked. The action must still be signed and intact, its manifest hash and the
    re-compiled statement hash must equal the parked ones (a contract edited while the
    write waited is never performed under the old approval), and an agent's grant must
    still name it. Returns the record; a refusal changes nothing."""
    if (item or {}).get("source") != REVIEW_SOURCE:
        _fail("not_parked", "this review item is not a parked action")
    try:
        payload = json.loads(item.get("original_text") or "{}")
    except (TypeError, ValueError):
        _fail("not_parked", "the parked write could not be read")
    kg_id, name = payload.get("kg_id"), payload.get("action")
    m = _load(store, name)
    from trusted_data_agent.dbdeploy.action_model import manifest_hash
    if manifest_hash(m) != payload.get("manifest_hash"):
        _fail("parked_drifted", f"{name!r} changed after this write was parked — the approval was "
                                f"for the previous contract; reject it and perform again")
    profile = None
    if payload.get("profile_id"):
        profile = (profile_lookup or (lambda _pid: None))(payload["profile_id"])
        if profile is None:
            _fail("not_granted", "the agent that parked this write no longer exists")
    # the approval performs exactly what was parked, as an enforced write by the owner
    rec = _perform_enforced(store, kg_id, m, payload, user_uuid=user_uuid, profile=profile,
                            connect_fn=connect_fn, approved_by=approved_by, db_path=db_path)
    rec["review_id"] = item.get("id")
    from trusted_data_agent.core.action_act_log import log_act
    log_act(user_uuid, rec, "review", db_path=db_path)
    return rec


def _perform_enforced(store, kg_id, m, payload, *, user_uuid, profile, connect_fn, db_path,
                      approved_by: Optional[str] = None):
    if not _act_enabled(profile, user_uuid):
        _fail("act_off", "OBDA-ACT is not enabled for this agent (the OBDA-ACT component is off for this deployment, this user or this agent)")
    state = _trust_state(kg_id, m.action, db_path)
    if state != "trusted":
        _fail("not_trusted", f"{m.action!r} reads {state} — not performed")
    if profile is not None:
        _granted(profile, m.action)
    from trusted_data_agent.dbdeploy.action_compiler import compile_action
    from trusted_data_agent.dbdeploy.action_runner import run_compiled
    if connect_fn is not None:
        conn, dialect = connect_fn()
    elif payload.get("connection_id"):
        conn, dialect = open_connection(user_uuid, payload["connection_id"])
    else:
        _fail("connection_unavailable", "the parked write names no Database Connection")
    try:
        ctypes = _teradata_session(conn, m) if dialect == "teradata" else {}
        c = compile_action(m, payload.get("invocation") or {}, column_types=ctypes,
                           text_date_shapes=_text_date_shapes(store, m), dialect=dialect)
        if c.statement_hash != payload.get("statement_hash"):
            _fail("parked_drifted", "the write compiles to a different statement than the one parked")
        if c.invocation_hash != payload.get("invocation_hash"):
            _fail("parked_drifted", "the parked invocation no longer compiles to the same values")
        # the person who approved — the owner (single account) or a designated approver; the
        # write itself always runs as the owner's agent, on the owner's connection
        approver = approved_by or f"user:{user_uuid}"
        # the ledger names both: the principal that asked, and the person who approved
        o = run_compiled(conn, c, principal=f"{payload.get('principal')} approved by {approver}",
                         posture="review", commit=True)
        rec = {"action": m.action, "version": m.version, "kg_id": kg_id,
               "manifest_hash": payload.get("manifest_hash"), "statement_hash": c.statement_hash,
               "invocation_hash": c.invocation_hash, "posture": "review",
               "principal": payload.get("principal"), "approved_by": approver,
               "rows_affected": o.rows_affected if o.committed else None,
               "checks": o.checks, "ledger_written": o.ledger_written, "receipt": c.receipt()}
        if o.committed:
            rec["outcome"] = "performed"
        else:
            rec.update(outcome="refused", refused_check=o.refused_check or "error",
                       refusal=o.refusal or o.error)
        return rec
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# ── B4: the faces — connection resolution, prepare (the consequence), confirm, undo ──

def action_connection(store, action_name: str) -> Optional[str]:
    """The connection an action OWNS: the one it was validated through (``validated_with`` on
    its node) — the A1–A6 proof ran as that login, under the action's role, so it is the login
    the signed contract describes. ``None`` for an action validated without a connection."""
    try:
        node = store.get_entity_by_name(action_name, "action_type")
        return str(((node or {}).get("properties") or {}).get("validated_with") or "").strip() or None
    except Exception:  # noqa: BLE001 — an unread node owns nothing
        return None


def connection_choice(user_uuid: str, profile: Optional[Dict[str, Any]],
                      explicit: Optional[str] = None,
                      action: Optional[str] = None) -> Tuple[Optional[str], str, List[Dict[str, Any]]]:
    """THE rule of which Database Connection an action writes through, without refusing:
    ``(id, why, candidates)`` — ``why`` ∈ explicit · action · only_connection, or
    ``(None, 'ambiguous' | 'none', candidates)``.

    The ACTION owns its connection (``action`` = :func:`action_connection`, the one it was
    validated through — the login its A1–A6 proof ran as). Nothing on the AGENT chooses a write
    login: neither a profile's ``componentConfig.action.connection_id`` (the second place to set
    the same thing — a profile save once dropped it and the agent could no longer write) nor its
    Database Navigator source (a READ connection, often an administrator's — writing through it
    is the two-logins confusion). ``profile`` is kept for the callers' signature. The perform path
    refuses on None (``resolve_connection``)."""
    if explicit:
        return explicit, "explicit", []
    if action:
        return action, "action", []
    try:
        from trusted_data_agent.core.database_connections import DatabaseConnectionStore
        conns = [c for c in DatabaseConnectionStore().list(user_uuid, include_legacy=False)
                 if (c.get("driver") or "").lower() in ("teradata", "sqlite")]
    except Exception:  # noqa: BLE001
        conns = []
    if len(conns) == 1:
        return conns[0]["id"], "only_connection", conns
    return None, ("ambiguous" if conns else "none"), conns


def resolve_connection(user_uuid: str, profile: Optional[Dict[str, Any]],
                       explicit: Optional[str] = None, action: Optional[str] = None) -> str:
    """Which Database Connection an action writes through — decided, never guessed:
    an explicit id → the connection the ACTION was validated through (it owns it) → the user's
    ONLY Teradata or SQLite connection (``connection_choice``). Otherwise refused, naming the
    candidates and how to give the action its connection."""
    cid, _why, conns = connection_choice(user_uuid, profile, explicit, action)
    if cid:
        return cid
    names = ", ".join(f"{c.get('name')} ({c.get('id')})" for c in conns) or "none"
    _fail("connection_unavailable",
          f"the action has no connection to write through — validate it through the connection it "
          f"writes through (the ontology's Actions → Validate, choosing the connection whose login "
          f"holds the action's role); candidates: {names}")


def _words(value: Any) -> str:
    return "empty" if value is None or value == "" else str(value)


def _changes(c, b: Dict[str, Any]) -> List[str]:
    """The values this write changes on ONE keyed row — the one comparison the consequence
    words and the no-change rule both read."""
    return [f"{k} {_words(b.get(k))} → {_words(v if not hasattr(v, 'isoformat') else v.isoformat())}"
            for k, v in c.values.items() if str(b.get(k)) != str(v)]


def no_change(c, before: List[Dict[str, Any]]) -> Optional[str]:
    """An UPDATE whose every keyed row already holds every value it would write changes
    nothing — a write that asks a person (Confirm, or an approver's review) to approve
    nothing. Returns the refusal in words, else None. An insert never qualifies (the row is
    new); an update that reached no row is the checks' business (row_exists), not this. Pure."""
    if getattr(c, "operation", None) != "update" or not before:
        return None
    if any(_changes(c, b) for b in before):
        return None
    key = " · ".join(f"{k} {_words(v)}" for k, v in c.key.items())
    held = "; ".join(f"{k} {_words(v)}" for k, v in c.values.items())
    return f"{key} already holds {held} — nothing to write"


def consequence(c, before: List[Dict[str, Any]], rows: Optional[int], checks: List[Dict[str, Any]],
                bound: int) -> Dict[str, Any]:
    """The consequence in words, computed from what the database answered — never a
    prediction. One line per changed row (``column before → after``), the count against the
    bound, and the checks."""
    lines = []
    for b in (before or [{}]):
        changes = _changes(c, b)
        key = " · ".join(f"{k} {_words(v)}" for k, v in c.key.items())
        lines.append(f"{key}: " + ("; ".join(changes) if changes else "no value changes"))
    held = all(ch.get("holds") for ch in checks) if checks else True
    return {"lines": lines, "rows": rows, "bound": bound,
            "checks_hold": held, "checks": checks,
            "summary": (f"{c.operation} {rows if rows is not None else 0} row(s) of {c.target} · bound {bound} · "
                        + ("every check holds" if held else "a check does not hold"))}


def prepare_action(store, kg_id: str, action_name: str, invocation: Dict[str, Any], *,
                   user_uuid: str, profile: Optional[Dict[str, Any]] = None,
                   connection_id: Optional[str] = None,
                   connect_fn: Optional[Callable[[], Any]] = None,
                   db_path: Optional[str] = None) -> Dict[str, Any]:
    """The consequence BEFORE anything happens: every refusal ``perform_action`` would make,
    then the write executed inside a transaction that is ALWAYS rolled back — so the
    before-image, the affected-row count and every pre/post check are what the database
    answered, not a prediction. Returns the pending act (what a Confirm performs)."""
    if not _act_enabled(profile, user_uuid):
        _fail("act_off", "OBDA-ACT is not enabled for this agent (the OBDA-ACT component is off for this deployment, this user or this agent)")
    m = _load(store, action_name)
    state = _trust_state(kg_id, action_name, db_path)
    if state != "trusted":
        _fail("not_trusted", f"{action_name!r} reads {state} — validate and sign it first")
    if profile is not None:
        _granted(profile, action_name)
    from trusted_data_agent.dbdeploy.action_compiler import ActionInvocationError, compile_action
    from trusted_data_agent.dbdeploy.action_model import manifest_hash
    from trusted_data_agent.dbdeploy.action_runner import run_compiled
    if connect_fn is not None:
        conn, dialect = connect_fn()
    else:
        connection_id = resolve_connection(user_uuid, profile, connection_id, action_connection(store, action_name))
        conn, dialect = open_connection(user_uuid, connection_id)
    try:
        ctypes = _teradata_session(conn, m) if dialect == "teradata" else {}
        try:
            c = compile_action(m, invocation, column_types=ctypes,
                               text_date_shapes=_text_date_shapes(store, m), dialect=dialect)
        except ActionInvocationError as e:
            _record({"action": m.action, "version": m.version, "outcome": "refused",
                     "refused_check": e.code, "refusal": e.message, "posture": m.posture})
            return {"ready": False, "action": m.action, "refused_check": e.code, "refusal": e.message,
                    "posture": m.posture}
        o = run_compiled(conn, c, principal="preview", posture=m.posture, commit=False)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    cons = consequence(c, o.before, o.rows_affected, o.checks, m.max_rows)
    out = {"ready": o.held, "action": m.action, "version": m.version, "kg_id": kg_id,
           "operation": m.operation, "target": m.deployed, "posture": m.posture,
           "manifest_hash": manifest_hash(m), "statement_hash": c.statement_hash,
           "invocation_hash": c.invocation_hash, "invocation": invocation,
           "connection_id": connection_id, "profile_id": (profile or {}).get("id"),
           "consequence": cons, "before": o.before}
    if not o.held:
        out.update(refused_check=o.refused_check or "error", refusal=o.refusal or o.error)
    elif no_change(c, o.before):
        # nothing to confirm: the row already holds the values — said, never offered as an act
        out.update(ready=False, refused_check="no_change", refusal=no_change(c, o.before))
    _record({"action": m.action, "version": m.version, "kg_id": kg_id, "posture": m.posture,
             "outcome": "prepared" if out.get("ready") else "refused",
             "invocation_hash": c.invocation_hash, "statement_hash": c.statement_hash,
             "rows_affected": o.rows_affected, "consequence": cons,
             # the database evaluated every check inside the rolled-back run — the record carries
             # them like a performed or parked act's, so `action_safety` reads one key everywhere
             "checks": o.checks,
             **({} if out.get("ready") else {"refused_check": out.get("refused_check"),
                                             "refusal": out.get("refusal")})})
    return out


def confirm_action(store, kg_id: str, action_name: str, invocation: Dict[str, Any], *,
                   expected_invocation_hash: str, user_uuid: str,
                   profile: Optional[Dict[str, Any]] = None,
                   connection_id: Optional[str] = None,
                   connect_fn: Optional[Callable[[], Any]] = None,
                   session_id: Optional[str] = None, turn_id: Any = None,
                   db_path: Optional[str] = None, face: Optional[str] = None) -> Dict[str, Any]:
    """A person confirmed a prepared act: perform it — only if it still compiles to the
    invocation they saw (the consequence row's hash). The record names the agent that
    prepared it and the person who confirmed it, and carries a signed receipt (the act
    happens after the turn was sealed, so the receipt is signed on its own)."""
    if not expected_invocation_hash:
        _fail("parked_drifted", "a confirmation names the invocation it confirms")
    if connect_fn is None:
        connection_id = resolve_connection(user_uuid, profile, connection_id, action_connection(store, action_name))
    rec = perform_action(store, kg_id, action_name, invocation, user_uuid=user_uuid, profile=profile,
                         connection_id=connection_id, connect_fn=connect_fn,
                         session_id=session_id, turn_id=turn_id, db_path=db_path,
                         expected_invocation_hash=expected_invocation_hash)
    rec["confirmed_by"] = f"user:{user_uuid}"
    if rec.get("outcome") == "performed":
        rec["signed_receipt"] = sign_receipt(rec, face)
    return rec


def receipt_occurrence(rec: Dict[str, Any], face: Optional[str] = None,
                       at: Optional[str] = None) -> Dict[str, Any]:
    """WHEN and WHERE this act happened — the part the receipt content cannot carry. The
    content is a pure function of the act (action, invocation, rows, agent), so two runs of
    one schedule performing the same write have identical content; a standalone receipt
    must still say which run it is. (Inside a sealed turn the chain's own turn context
    plays this role; the turn chain keeps sealing the content alone.)"""
    from datetime import datetime, timezone
    return {"face": face or rec.get("face"),
            "ref": (str(rec.get("turn_id")) if rec.get("turn_id") is not None else None),
            "session_id": rec.get("session_id"),
            "confirmed_by": rec.get("confirmed_by"),
            "at": at or datetime.now(timezone.utc).isoformat()}


def sign_receipt(rec: Dict[str, Any], face: Optional[str] = None) -> Dict[str, Any]:
    """An Ed25519 envelope over the receipt content AND its occurrence (the same provenance
    key the turn chain and eval attestations use), verifiable offline with the public key
    alone. ``content`` stays the sealed receipt field set; the signed ``payload`` is
    ``{"receipt": content, "occurrence": {face, ref, session_id, confirmed_by, at}}`` — so
    two acts with the same content still carry two distinct signed receipts."""
    content = receipt_content(rec)
    occ = receipt_occurrence(rec, face)
    payload = json.dumps({"receipt": json.loads(content), "occurrence": occ},
                         sort_keys=True, separators=(",", ":"), default=str)
    try:
        from trusted_data_agent.eval.online import sign_payload
        env = sign_payload(payload)
    except Exception:  # noqa: BLE001
        env = {"signed": False, "payload": payload}
    return {**env, "content": content, "occurrence": occ,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()}


UNATTENDED_FACES = ("flow", "scheduler", "dashboard")


def unattended_grant_error(action_name: str, profile: Optional[Dict[str, Any]], *,
                           act_enabled: Optional[bool] = None) -> Optional[str]:
    """Pure — THE rule every unattended face checks before it stores an act (a flow node, a
    scheduled task, a dashboard tile): one sentence naming why it could not run, or ``None``.
    The same rule ``perform_unattended`` enforces at run time, so a save and a run never
    disagree. ``profile`` is the agent the act runs as (``None`` ⇒ no agent)."""
    name = str(action_name or "").strip()
    if act_enabled is None:
        from trusted_data_agent.core import obda_planes as _planes
        act_enabled = _planes.plane_enabled("act", profile if isinstance(profile, dict) else None)
    if not act_enabled:
        return (f"'{name}' is an action, but OBDA-ACT is not enabled for the agent that would run it — "
                f"enable the OBDA-ACT component for that agent, or remove the action.")
    if not isinstance(profile, dict) or not profile.get("id"):
        return ("names no agent — an unattended act runs under an agent's grant; set the agent "
                "that performs it.")
    from trusted_data_agent.core.agent_authority import scope_for_profile
    allowed = scope_for_profile(profile).actions_allowed or frozenset()
    if name not in allowed:
        granted = ", ".join(sorted(allowed)) or "none"
        return (f"@{profile.get('tag')} is not granted '{name}' (granted: {granted}). Grant it from "
                f"the Actions panel, or pick an agent that holds the grant.")
    return None


def perform_unattended(user_uuid: str, profile: Optional[Dict[str, Any]], action_name: str,
                       invocation: Dict[str, Any], *, face: str,
                       store=None, kg_id: Optional[str] = None,
                       connection_id: Optional[str] = None,
                       session_id: Optional[str] = None, turn_id: Any = None,
                       db_path: Optional[str] = None,
                       connect_fn: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """ONE seam for an act nobody confirms at the moment it runs — a flow node, a scheduled
    task, a dashboard tile (PROP-008 §6). It never prepares: the action's POSTURE decides
    (enforce writes · review parks for the owner · observe records), exactly as on an
    autonomous turn, so a schedule can never carry more authority than the action allows.

    Rules, each gated:
      * an unattended act ALWAYS acts as an agent — its grant decides; with no agent there
        is no person present to lend their own authority, so it is refused (``no_agent``);
      * the graph is the agent's active graph unless one is named (the faces agree);
      * the write connection is decided by :func:`resolve_connection`, never guessed;
      * a performed act carries a receipt signed on its own (the provenance key) and names
        its face. Never raises: a refusal is a ``refused`` record."""
    face = face if face in UNATTENDED_FACES else "flow"
    try:
        if not _act_enabled():
            _fail("act_off", "OBDA-ACT is not enabled for this agent (the OBDA-ACT component is off for "
                              "the deployment, the person or the agent)")
        if not isinstance(profile, dict) or not profile.get("id"):
            _fail("no_agent", f"an act run by a {face} acts as an agent — name the agent whose "
                              f"grant it runs under (nobody is present to lend their own authority)")
        if store is None:
            from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler
            store = KnowledgeGraphHandler()._get_store({"profile_id": profile.get("id"),
                                                        "user_uuid": user_uuid})
            kg_id = kg_id or getattr(store, "kg_id", None)
        if not kg_id:
            _fail("no_graph", f"@{profile.get('tag') or profile.get('id')} has no knowledge graph to act on")
        cid = None if connect_fn is not None else resolve_connection(user_uuid, profile, connection_id,
                                                                    action_connection(store, action_name))
        rec = perform_action(store, kg_id, action_name, invocation or {}, user_uuid=user_uuid,
                             profile=profile, connection_id=cid, connect_fn=connect_fn,
                             session_id=session_id, turn_id=turn_id, db_path=db_path)
    except ActionPerformError as e:
        rec = record_refusal(action_name, e.code, e.message, session_id=session_id, turn_id=turn_id)
    except Exception as e:  # noqa: BLE001 — an unattended run must report, never crash its host
        logger.warning(f"actions: unattended {face} act {action_name!r} failed: {e}")
        rec = record_refusal(action_name, "error", str(e), session_id=session_id, turn_id=turn_id)
    rec = dict(rec)
    rec["face"] = face
    if rec.get("outcome") == "performed":
        rec["signed_receipt"] = sign_receipt(rec, face)
    if isinstance(profile, dict) and not rec.get("principal"):
        rec["principal"] = f"agent:{profile.get('id')}"
    from trusted_data_agent.core.action_act_log import log_act
    log_act(user_uuid, rec, face, db_path=db_path)
    return rec


def undo_invocation(store, kg_id: str, action_name: str, invocation_hash: str, *,
                    user_uuid: str, profile: Optional[Dict[str, Any]] = None,
                    connection_id: Optional[str] = None,
                    connect_fn: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """The invocation that UNDOES a performed one: the ledger's before-image of the same
    keyed row, written back through the SAME signed contract — an act of its own, with its
    own consequence, confirmation and receipt. An insert's undo is a delete, which v1 does
    not perform (§9)."""
    m = _load(store, action_name)
    if m.operation != "update":
        _fail("not_undoable", f"undoing an {m.operation} would be a delete — not performed in v1")
    from trusted_data_agent.dbdeploy.action_model import ledger_identifier
    led = ledger_identifier(m)
    if not led:
        _fail("not_undoable", "this action records into the target's own audit columns — there is no "
                              "platform before-image to restore")
    if connect_fn is not None:
        conn, _dialect = connect_fn()
    else:
        conn, _dialect = open_connection(user_uuid, resolve_connection(user_uuid, profile, connection_id,
                                                                       action_connection(store, action_name)))
    try:
        db, table = led.split(".", 1)
        cur = conn.cursor()
        cur.execute(f'SELECT "key_json", "before_json" FROM "{db}"."{table}" '
                    f'WHERE "invocation_hash" = ? AND "action_name" = ?', [invocation_hash, action_name])
        row = cur.fetchone()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if not row:
        _fail("not_found", f"no performed invocation {invocation_hash[:12]}… of {action_name!r} in the ledger")
    key = json.loads(row[0] or "{}")
    before = (json.loads(row[1] or "[]") or [{}])[0]
    values = {a.name: before.get(a.name) for a in m.attributes if a.name in before}
    return {"key": key, "values": values}
