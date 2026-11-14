"""
PROP-008 OBDA-ACT — the action-type seam: author, gather evidence, validate (B1).

ONE place that turns a manifest into a node on the ontology and a validation report on
that node — the REST face (B4), the Designer and the CI gate all call it, so the
evidence the gate sees cannot differ by caller. The pure halves live in
``dbdeploy/action_model.py`` (the contract) and ``dbdeploy/action_validation.py``
(A1–A6); this module does the I/O the gate is injected with:

* ``author_action``   parses the manifest (a REJECT changes nothing), writes the
                      ``action_type`` node + its ``acts_on`` edge through ``GraphStore``,
                      flattens the inspector summary, and DROPS a stored report whenever
                      the contract changed (the OBDA-READ A4b lesson: ``update_entity`` MERGES
                      properties, so a stale report survives an edit unless it is removed
                      explicitly);
* ``gather_evidence`` the resolver's answer for the target (A1) and the CLOSED value
                      domains the graph declares for the writable columns (A2);
* ``validate``        runs the gate with the evidence (+ the live probe executor, the
                      dictionary schema and unique keys when a backend is given), and
                      stamps the report on the node;
* ``provision_ledger`` creates the platform ledger table through a deployment backend
                      and registers it as a ``ddl_artifact`` (``purpose: action_ledger``)
                      that ``derives_from`` the target table — idempotent (§5.5, §13).

Gated on the OBDA-ACT component's availability (``core/obda_planes``): with the plane off nothing here writes (§7).
"""

from __future__ import annotations

import asyncio
import re
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("quart.app")

ACTION_TYPE = "action_type"


class ActionTypeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _act_enabled() -> bool:
    """OBDA-ACT exists for this deployment — the OBDA-ACT component's global gate
    (core/obda_planes); authoring has no agent in play."""
    try:
        from trusted_data_agent.core import obda_planes as _planes
        return _planes.plane_available("act")
    except Exception:  # noqa: BLE001
        return False


async def _maybe_await(v):
    return await v if asyncio.iscoroutine(v) else v


def summary_properties(m) -> Dict[str, Any]:
    """The inspector's tooltip fields, flattened at authoring so the tip never parses."""
    return {"operation": m.operation, "target_deployed": m.deployed,
            "writable": ", ".join(m.attribute_names()), "max_rows": m.max_rows,
            "posture": m.posture, "version": m.version, "description": m.description,
            "target_entity": m.entity}


async def author_action(store, raw: Dict[str, Any], *, actor: Optional[str] = None) -> Dict[str, Any]:
    """Write (or rewrite) one action type on the graph. Refuses with a machine-readable
    code; a refusal writes nothing."""
    if not _act_enabled():
        raise ActionTypeError("act_off", "OBDA-ACT is not enabled on this deployment (the OBDA-ACT component is off)")
    from trusted_data_agent.dbdeploy.action_model import (
        ActionManifestError, canonical_json, manifest_hash, parse_manifest)
    try:
        m = parse_manifest(raw)
    except ActionManifestError as e:
        raise ActionTypeError(e.code, e.message)
    target = store.get_entity_by_name(m.entity, "entity")
    if not target:
        raise ActionTypeError("unknown_target", f"the graph has no entity named {m.entity!r} — an action "
                                                f"writes an entity of THIS ontology")
    from trusted_data_agent.kgstore.types import is_authored, is_published_authored
    tprops = target.get("properties") or {}
    if is_authored(tprops) and not is_published_authored(tprops):
        raise ActionTypeError("draft_target", f"{m.entity!r} is a draft on the authored layer — an action "
                                              f"writes a base entity or a PUBLISHED extension entity; "
                                              f"publish it first")
    canon = json.loads(canonical_json(m))
    mh = manifest_hash(m)
    existing = store.get_entity_by_name(m.action, ACTION_TYPE)
    props: Dict[str, Any] = {"layer": "logical", "manifest": canon, "manifest_hash": mh,
                             **summary_properties(m)}
    if actor:
        props["authored_by"] = actor
    if existing:
        old = dict(existing.get("properties") or {})
        changed = old.get("manifest_hash") != mh
        if changed:
            old.pop("validation_report", None)   # a report for another contract proves nothing
        old.update(props)
        if changed:
            old["validation_report"] = None
        await _maybe_await(store.update_entity(existing["id"], properties=old))
        node_id, created = existing["id"], False
    else:
        node_id = await _maybe_await(store.add_entity(m.action, ACTION_TYPE, props))
        created, changed = True, True
    # the one edge: action_type --acts_on--> its target entity (idempotent)
    try:
        await _maybe_await(store.add_relationship(node_id, target["id"], "acts_on"))
    except Exception as exc:  # noqa: BLE001 — an existing edge is fine
        logger.debug(f"action_types: acts_on edge for {m.action}: {exc}")
    return {"action": m.action, "created": created, "manifest_changed": changed,
            "manifest_hash": mh, "node_id": node_id}


# ── defining an action: everything derivable is derived (the Actions Designer's form) ──
# The person decides what only a person can: which concept, insert or update, which columns
# the action may write, the bound, the role, the posture. The platform supplies the rest —
# the deployed table (the resolver, A1), the key (the warehouse's unique index, else the
# model's declared key), every column's contract type (the live dictionary through the ONE
# inverse of A2's rule, else the model) and the closed value domains the model declares.

def _closed_domain_of(props: Dict[str, Any]) -> Optional[List[Any]]:
    raw = props.get("allowed_values")
    if not isinstance(raw, list) or not raw:
        return None
    vals = {str(c["value"]): str(c.get("label") or "") for c in raw
            if isinstance(c, dict) and c.get("value") is not None and str(c["value"]).strip()}
    try:
        from trusted_data_agent.agent.preflight.values import closed_domain
        if vals and not closed_domain(vals):
            return None
    except Exception:  # noqa: BLE001 — without the preflight a declared domain stands
        pass
    return list(vals.keys()) or None


def _is_view(store, deployed: str) -> bool:
    node = store.get_entity_by_name(deployed, "table")
    return str(((node or {}).get("properties") or {}).get("object_kind") or "").lower() == "view"


_CONNECTION_WHY = {
    "landed": "the connection this graph's tables were landed through",
    "only_connection": "your only Teradata connection",
}


def default_connection(store, user_uuid: Optional[str], profile: Optional[Dict[str, Any]] = None,
                       connections: Optional[List[Dict[str, Any]]] = None,
                       purpose: str = "write") -> Dict[str, Any]:
    """The Database Connection a definition starts on, and why — an action WRITES through a
    connection, so the connection is chosen first and the concepts follow from it. For an
    action (``purpose='write'``): the user's only warehouse, else the person chooses — never an
    agent's setting and never the connection the model was landed through (the model login,
    which holds no action's role); the action then OWNS the connection it is validated through
    (``action_perform.action_connection``). For a MODEL change (``purpose='model'``: a landing,
    the action ledger): the connection this graph's tables were LANDED through (``landed_via``
    on its realisations, the most frequent), else the user's only one. ``{id: None}`` when
    nothing decides — never a guess."""
    if connections is None:
        try:
            from trusted_data_agent.core.database_connections import DatabaseConnectionStore
            connections = DatabaseConnectionStore().list(user_uuid, include_legacy=False) if user_uuid else []
        except Exception:  # noqa: BLE001
            connections = []
    have = {c.get("id") for c in connections or []}
    if purpose != "model":
        # an action OWNS its connection (the one it is validated through); nothing on an agent
        # chooses a write login, and the connection the model was LANDED through is the model
        # login, which never holds an action's role. Only a person's single warehouse is offered;
        # otherwise the person chooses (never a guess)
        writable = [c for c in connections or [] if (c.get("driver") or "").lower() in ("teradata", "sqlite")]
        if len(writable) == 1:
            return {"id": writable[0].get("id"), "why": _CONNECTION_WHY["only_connection"]}
        return {"id": None, "why": ""}
    counts: Dict[str, int] = {}
    tables: List[Dict[str, Any]] = []
    # a landing writes tables AND columns (a column landed into an existing table is the only
    # physical node that landing writes) — both say which connection landed the model
    for et in ("table", "column"):
        try:
            tables += store._get_backend().list_entities(store.kg_id, entity_type=et, limit=100000) or []
        except Exception:  # noqa: BLE001 — no landing record read, none counted
            pass
    for t in tables:
        via = str(((t or {}).get("properties") or {}).get("landed_via") or "")
        if via and via in have:
            counts[via] = counts.get(via, 0) + 1
    if counts:
        return {"id": max(counts, key=counts.get), "why": _CONNECTION_WHY["landed"]}
    writable = [c for c in connections or [] if (c.get("driver") or "").lower() in ("teradata", "sqlite")]
    if len(writable) == 1:
        return {"id": writable[0].get("id"), "why": _CONNECTION_WHY["only_connection"]}
    return {"id": None, "why": ""}


async def list_targets(store, *, not_offered: Optional[List[Dict[str, Any]]] = None,
                       backend=None) -> List[Dict[str, Any]]:
    """The entities an action may write: deployed on a TABLE (the resolver names one; a view
    is read-only to an action) and not a draft. With a ``backend`` (the connection the action
    writes through) only the concepts whose table EXISTS, as a table, in the warehouse that
    connection reads — the rest are named in ``not_offered`` with their reason."""
    from components.builtin.knowledge_graph.resolver import resolve_deployed_table
    from trusted_data_agent.kgstore.types import is_authored, is_published_authored
    out: List[Dict[str, Any]] = []
    skipped = not_offered if not_offered is not None else []

    def _skip(e, pr, why, deployed=None):
        skipped.append({"entity": e["name"], "label": pr.get("label") or str(e["name"]).rpartition(".")[2],
                        "why": why, "deployed": deployed})
    for e in await _maybe_await(store.list_entities(entity_type="entity", limit=5000)) or []:
        pr = e.get("properties") or {}
        if is_authored(pr) and not is_published_authored(pr):
            _skip(e, pr, "draft")         # publish it first
            continue
        try:
            dep = await resolve_deployed_table(store, e["name"])
        except Exception:  # noqa: BLE001
            dep = None
        if dep is None:
            _skip(e, pr, "not_landed")
            continue
        if _is_view(store, dep.identifier):
            _skip(e, pr, "view", dep.identifier)   # an action writes a table — a view is read-only here
            continue
        out.append({"entity": e["name"], "label": pr.get("label") or str(e["name"]).rpartition(".")[2],
                    "deployed": dep.identifier, "extension": bool(is_authored(pr))})
    if backend is not None and out:
        # what the warehouse behind THIS connection holds, read once per database
        present: Dict[str, Optional[Dict[str, str]]] = {}
        for db in sorted({t["deployed"].split(".", 1)[0] for t in out}, key=str.lower):
            try:
                present[db.lower()] = {str(n).lower(): k for k, n in await backend.list_database_objects(db)}
            except Exception as exc:  # noqa: BLE001 — an unread database hides nothing
                logger.info(f"action_types: objects of {db} not read: {exc}")
                present[db.lower()] = None
        keep = []
        for t in out:
            db, tb = t["deployed"].split(".", 1)
            objs = present.get(db.lower())
            kind = None if objs is None else objs.get(tb.lower())
            if objs is None or kind == "table":
                keep.append(t)
            else:
                skipped.append({"entity": t["entity"], "label": t["label"], "deployed": t["deployed"],
                                "why": "view" if kind == "view" else "not_on_connection"})
        out = keep
    return sorted(out, key=lambda t: (not t["extension"], t["label"].lower()))


async def describe_target(store, entity: str, *, backend=None) -> Dict[str, Any]:
    """What an action on ``entity`` can write, derived: the deployed table, its key, and its
    columns with contract types and declared value domains. With a ``backend`` the types
    and the key come from the live dictionary (``source: warehouse``); without one from the
    model (``source: model``, or ``unknown`` — the person picks, A2 checks)."""
    from components.builtin.knowledge_graph.resolver import resolve_deployed_table
    from trusted_data_agent.dbdeploy.action_model import LEDGER_PREFIX
    from trusted_data_agent.dbdeploy.action_validation import contract_type_of
    from trusted_data_agent.kgstore.types import is_authored, is_published_authored
    ent = store.get_entity_by_name(entity, "entity")
    if not ent:
        raise ActionTypeError("unknown_target", f"the graph has no entity named {entity!r}")
    eprops = ent.get("properties") or {}
    if is_authored(eprops) and not is_published_authored(eprops):
        raise ActionTypeError("draft_target", f"{entity!r} is a draft — publish it before an action may write it")
    dep = await resolve_deployed_table(store, entity)
    if dep is None:
        raise ActionTypeError("not_landed", f"{entity!r} has no deployed table — land it on the warehouse first")
    deployed = dep.identifier
    db, table = deployed.split(".", 1)
    if _is_view(store, deployed):
        raise ActionTypeError("view_target", f"{entity!r} is deployed as the view {deployed} — an action "
                                             f"writes a table; a view is read-only to it")

    # the model's view: the entity's attributes, where each landed, its declared type and domain
    model_cols: Dict[str, Dict[str, Any]] = {}
    graph_key: List[str] = []
    try:
        rels = await _maybe_await(store.list_relationships(relationship_type="contains", limit=100000))
        for r in rels or []:
            if r.get("source_name") != entity or r.get("target_type") != "attribute":
                continue
            node = store.get_entity_by_name(r.get("target_name"), "attribute")
            if not node:
                continue
            p = node.get("properties") or {}
            col = str(p.get("deployed_column") or r.get("target_name")).rpartition(".")[2]
            dt = str(p.get("data_type") or "")
            model_cols[col.lower()] = {"name": col, "label": p.get("label"),
                                       "type": dt if dt in ("string", "integer", "decimal", "date",
                                                            "timestamp", "boolean") else contract_type_of(dt),
                                       "value_domain": _closed_domain_of(p)}
            if p.get("added_by_platform") == "key":
                graph_key.append(col)
    except Exception as exc:  # noqa: BLE001 — the warehouse (or the person) still decides
        logger.info(f"action_types: model columns of {entity} not read: {exc}")
    declared = [k.strip() for k in str(eprops.get("business_key") or "").split(",") if k.strip()]
    tnode = store.get_entity_by_name(deployed, "table")
    pk = [k.strip() for k in str(((tnode or {}).get("properties") or {}).get("primary_key") or "").split(",") if k.strip()]
    graph_key = declared or pk or graph_key

    live_cols: Optional[Dict[str, str]] = None
    live_keys: List[Tuple[str, ...]] = []
    if backend is not None:
        described = await backend.describe_tables(db, [table])
        entry = next((v for k, v in (described or {}).items() if k.lower() == deployed.lower()), None)
        if not entry:
            raise ActionTypeError("not_landed", f"{deployed} is not in the warehouse this connection reads")
        if str(entry.get("kind") or "").lower() == "view":
            raise ActionTypeError("view_target", f"{deployed} is a view in the warehouse — an action writes a table")
        live_cols = dict(entry.get("columns") or {})
        try:
            live_keys = [tuple(k) for k in await backend.unique_indexes(deployed)]
        except Exception as exc:  # noqa: BLE001
            logger.info(f"action_types: unique indexes of {deployed} not read: {exc}")

    # the key: the warehouse's unique index (the model's when it is one of them), else the model's
    key, key_source = list(graph_key), "model" if graph_key else "none"
    if live_keys:
        gk = [k.lower() for k in graph_key]
        match = next((k for k in live_keys if [c.lower() for c in k] == gk), None)
        key, key_source = list(match or min(live_keys, key=len)), "warehouse"

    cols: List[Dict[str, Any]] = []
    names = list(live_cols.keys()) if live_cols is not None else [v["name"] for v in model_cols.values()]
    keyset = {k.lower() for k in key}
    for nm in names:
        m = model_cols.get(nm.lower()) or {}
        if live_cols is not None:
            t, src = contract_type_of(live_cols.get(nm, "")), "warehouse"
            db_type = live_cols.get(nm, "")
        else:
            t, src, db_type = m.get("type"), "model", ""
        cols.append({"name": nm, "label": m.get("label"), "type": t or "string",
                     "type_source": src if t else "unknown", "db_type": db_type,
                     "value_domain": m.get("value_domain"), "in_model": bool(m),
                     "is_key": nm.lower() in keyset})
    ledger = f"{db}.{(LEDGER_PREFIX + table)[:128]}"
    from trusted_data_agent.dbdeploy.action_model import RIGHT_WORDS, TARGET_RIGHTS

    def _words(codes) -> str:
        ws = [RIGHT_WORDS[c] for c in ("I", "U", "R") if c in codes]
        return ws[0] if len(ws) == 1 else ", ".join(ws[:-1]) + " and " + ws[-1]
    roles, suggested = ([], {})
    if backend is not None:
        roles, suggested = await _role_candidates(backend, db, table)
    return {"entity": entity, "label": eprops.get("label") or entity.rpartition(".")[2],
            "deployed": deployed, "backend": dep.infrastructure or "teradata",
            "key": key, "key_source": key_source, "columns": cols,
            "mode": "live" if live_cols is not None else "model", "ledger": ledger,
            "rights": {op: f"{_words(codes)} on {deployed}; INSERT on {ledger}" for op, codes in TARGET_RIGHTS.items()},
            "roles": roles, "suggested_role": suggested}


#: Words a role's name uses for an operation — only to RANK suggestions; A6 decides.
_OP_WORDS = {"insert": ("RECORD", "INSERT", "CREATE", "ADD", "WRITE"),
             "update": ("UPDATE", "CLOSE", "CHANGE", "EDIT", "SET", "WRITE"),
             "upsert": ("UPSERT", "RECORD", "WRITE")}


async def _role_candidates(backend, db: str, table: str) -> Tuple[List[Dict[str, Any]], Dict[str, Optional[str]]]:
    """The roles this warehouse already has for the table — the ones holding rights on it, and
    the ones named after it — each with its rights, ranked per operation: exactly the rights
    the operation needs first, then a name that says the operation. Read-only; a dictionary
    that cannot be read suggests nothing (the person types the role; A6 still judges it)."""
    from trusted_data_agent.dbdeploy.action_model import TARGET_RIGHTS
    held: Dict[str, set] = {}
    try:
        if hasattr(backend, "initialize"):
            await backend.initialize()      # never depend on a caller having read first
    except Exception as exc:  # noqa: BLE001
        logger.info(f"action_types: backend not initialized for role suggestions: {exc}")
        return [], {}
    try:
        for r in await asyncio.to_thread(backend.run_sync_query,
                                         "SELECT RoleName, AccessRight FROM DBC.AllRoleRightsV "
                                         "WHERE DatabaseName = ? AND TableName = ?", [db, table]) or []:
            held.setdefault(str(r[0]).strip(), set()).add(str(r[1]).strip().upper())
    except Exception as exc:  # noqa: BLE001
        logger.info(f"action_types: role rights on {db}.{table} not read: {exc}")
    try:
        for r in await asyncio.to_thread(backend.run_sync_query,
                                         "SELECT RoleName FROM DBC.RoleInfoV WHERE RoleName LIKE ?",
                                         [f"%{table}%"]) or []:
            held.setdefault(str(r[0]).strip(), set())
    except Exception as exc:  # noqa: BLE001
        logger.info(f"action_types: roles named after {table} not read: {exc}")
    roles = [{"role": k, "rights": sorted(v)} for k, v in sorted(held.items(), key=lambda kv: kv[0].lower())]
    suggested: Dict[str, Optional[str]] = {}
    for op, need in TARGET_RIGHTS.items():
        def score(r):
            rights, up = set(r["rights"]), r["role"].upper()
            exact = rights == set(need)
            named = any(w in up for w in _OP_WORDS[op])
            return (exact, named and set(need) >= rights, named, table.upper() in up)
        ranked = sorted(roles, key=score, reverse=True)
        best = ranked[0] if ranked else None
        suggested[op] = best["role"] if best and any(score(best)[:3]) else None
    return roles, suggested


def model_facts(m, get_entity: Callable[[str, str], Optional[Dict[str, Any]]],
                relationships: Sequence[Dict[str, Any]], hidden: Optional[set] = None) -> Dict[str, Any]:
    """The facts an action's proof reads from the graph — what the target resolves to, the
    target's columns, and the CLOSED value domains of the writable attributes (an open
    codebook — a range entry, nothing but missing codes — proves nothing, the preflight's
    rule). Pure over a lookup + the relationships, so validation (through the store) and the
    trust reading (through the graph rows) compute them with ONE function: the signature
    covers these facts, and a change to any of them reads as a changed action. Normalised
    (sorted) so the same model always yields the same facts. ``hidden`` = the nodes of an
    UNPUBLISHED extension (``kgstore.types.unpublished_extension_names``): a draft is not in
    the model yet, so it is no fact an action stands on and moves no action."""
    from components.builtin.knowledge_graph.resolver import _prefer
    hidden = hidden or set()
    rels = [r for r in (relationships or [])
            if r.get("source_name") not in hidden and r.get("target_name") not in hidden]
    resolved: Optional[str] = None
    if get_entity(m.entity, "entity"):
        impls = []
        for r in rels:
            if (r.get("relationship_type") == "implements" and r.get("target_name") == m.entity
                    and r.get("target_type") in (None, "entity") and r.get("source_type") == "table"):
                infra = ((get_entity(r.get("source_name"), "table") or {}).get("properties") or {}).get("infrastructure")
                impls.append((r.get("source_name"), infra, None))
        chosen = _prefer(sorted(set((n, i, None) for n, i, _ in impls), key=lambda x: (str(x[0]), str(x[1]))))
        resolved = chosen[0] if chosen else None
    try:
        from trusted_data_agent.agent.preflight.values import closed_domain
    except Exception:  # noqa: BLE001
        closed_domain = None  # type: ignore[assignment]
    writable = {a.name.lower(): a.name for a in m.attributes}
    columns: List[str] = []
    domains: Dict[str, List[str]] = {}
    for an in sorted({r.get("target_name") for r in rels
                      if r.get("source_name") == m.entity and r.get("relationship_type") == "contains"} - {None}):
        node = get_entity(an, "attribute")
        if not node:
            continue
        p = node.get("properties") or {}
        col = str(p.get("deployed_column") or an).rpartition(".")[2]
        columns.append(col)
        if col.lower() not in writable:
            continue
        raw = p.get("allowed_values")
        if not isinstance(raw, list) or not raw:
            continue
        vals = {str(c["value"]): str(c.get("label") or "") for c in raw
                if isinstance(c, dict) and c.get("value") is not None and str(c["value"]).strip()}
        if vals and (closed_domain is None or closed_domain(vals)):
            domains[writable[col.lower()]] = sorted(vals.keys())
    return {"resolution": {m.entity: resolved}, "graph_domains": dict(sorted(domains.items())),
            "entity_columns": sorted(set(columns)) or None}


def facts_fingerprint(facts: Dict[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


#: The drift of a report from before the facts were recorded — nothing is known to have moved,
#: the proof simply cannot say which model it describes (validate once more).
UNRECORDED = "validated before the model facts it depends on were recorded"


def facts_drift(recorded: Optional[Dict[str, Any]], current: Dict[str, Any]) -> List[str]:
    """What moved in the model since the action was validated, in words — empty when the
    recorded facts are the current ones. A report that recorded no facts (validated before
    they were recorded) proves nothing about today's model, so it is drift too."""
    if not isinstance(recorded, dict) or not isinstance(recorded.get("facts"), dict):
        return [UNRECORDED]
    was, now = recorded["facts"], current
    out: List[str] = []
    for ent, tgt in (now.get("resolution") or {}).items():
        before = (was.get("resolution") or {}).get(ent)
        if before != tgt:
            out.append(f"{ent} now resolves to {tgt or 'no deployed table'} (was {before or 'none'})")
    wc, nc = set(was.get("entity_columns") or []), set(now.get("entity_columns") or [])
    if nc - wc:
        out.append(f"new attribute{'s' if len(nc - wc) > 1 else ''} {', '.join(sorted(nc - wc))}")
    if wc - nc:
        out.append(f"attribute{'s' if len(wc - nc) > 1 else ''} {', '.join(sorted(wc - nc))} removed")
    wd, nd = was.get("graph_domains") or {}, now.get("graph_domains") or {}
    for a in sorted(set(wd) | set(nd)):
        if wd.get(a) != nd.get(a):
            out.append(f"the allowed values of {a} changed")
    return out or ([] if facts_fingerprint(was) == facts_fingerprint(now) else ["the model it depends on changed"])


async def gather_evidence(store, m) -> Dict[str, Any]:
    """A1 + A2 evidence from the ontology itself (:func:`model_facts` through the store)."""
    try:
        rels = await _maybe_await(store.list_relationships(limit=100000))
        hidden = await store.unpublished_names() if hasattr(store, "unpublished_names") else set()
        return model_facts(m, store.get_entity_by_name, rels or [], hidden)
    except Exception as exc:  # noqa: BLE001 — facts not read are facts not compared
        logger.info(f"action_types: graph facts not read for {m.entity}: {exc}")
        return {"resolution": {m.entity: None}, "graph_domains": {}, "entity_columns": None}


async def validate(store, action_name: str, *, backend=None, provisioner=None,
                   provisioner_label: str = "", connection_id: Optional[str] = None) -> Dict[str, Any]:
    """Run A1–A6 for one authored action type and stamp the report on its node. With a
    ``backend`` (a ``TeradataDeploymentBackend``) the gate runs LIVE: the dictionary
    schema + unique keys are read, the ledger is provisioned when the platform ledger is
    on, and the probe executor answers A3/A5/A6. Validation NEVER signs."""
    from trusted_data_agent.dbdeploy.action_model import ActionManifestError, parse_manifest
    from trusted_data_agent.dbdeploy.action_validation import validate_action
    node = store.get_entity_by_name(action_name, ACTION_TYPE)
    if not node:
        raise ActionTypeError("not_found", f"no action type named {action_name!r}")
    props = dict(node.get("properties") or {})
    try:
        m = parse_manifest(props.get("manifest") or {})
    except ActionManifestError as e:
        raise ActionTypeError(e.code, e.message)
    ev = await gather_evidence(store, m)
    kwargs: Dict[str, Any] = dict(resolution=ev["resolution"], graph_domains=ev["graph_domains"] or None,
                                  entity_columns=ev["entity_columns"])
    ledger = None
    if backend is not None:
        ledger = await provision_ledger(store, m, backend, provisioner=provisioner,
                                        provisioner_label=provisioner_label)
        schema, unique = await read_dictionary(backend, m)
        # the probes run AS the action will: on a session of the connection's login acting under
        # the action's role (a login that does not hold it is named, with the GRANT that fixes it)
        session, refusal = None, None
        if hasattr(backend, "role_session"):
            try:
                session = await asyncio.to_thread(backend.role_session, m.required_role)
            except Exception as exc:  # noqa: BLE001 — every probe then says why it could not run
                from trusted_data_agent.dbcatalog.sql_runner import friendly_db_error
                refusal = friendly_db_error(exc)
        kwargs.update(schema=schema, unique_keys=unique, probe_fn=_probe_fn(backend, session, refusal),
                      sample_params=sample_params)
    # the gate is pure and synchronous; with a backend its probes block on the warehouse,
    # so it runs on a worker thread and never holds the event loop
    try:
        report = (await asyncio.to_thread(validate_action, m, **kwargs)) if backend is not None \
            else validate_action(m, **kwargs)
    finally:
        if backend is not None and session is not None:
            session.close()
    # the graph facts this proof was built from — signed with the report, so a change to any
    # of them reads as a changed action (``trust/actions.live_hash``), never as intact
    props["validation_report"] = {**report.to_dict(),
                                  "model_facts": {"fingerprint": facts_fingerprint(ev), "facts": ev}}
    # the connection the live gate ran through — the connection the action OWNS: perform writes
    # through it (``action_perform.action_connection``), the Designer starts there. A structural
    # validation (no connection) proves no login, so it owns none — set to None explicitly
    # (``update_entity`` merges). A fact of this deployment, like `landed_via`; it never travels.
    props["validated_with"] = connection_id or None
    await _maybe_await(store.update_entity(node["id"], properties=props))
    return {"action": action_name, "report": report.to_dict(), "ledger": ledger}


# ── the live half: dictionary reads, the probe executor, the ledger ──────────

def _probe_fn(backend, session=None, refusal: Optional[str] = None) -> Callable[..., Any]:
    """A READ-ONLY executor — on the role session when there is one (the probes run as the
    action will), else the backend's own connection. Runs only statements the gate builds
    (a SELECT over the dictionary or the target, or an EXPLAIN — which explains, never
    executes); anything else is refused before it reaches the wire. With a ``refusal`` (the
    role session could not be opened) every probe says why."""
    def run(sql: str, params: Optional[Sequence[Any]] = None):
        body = (sql or "").strip()
        head = body.split(None, 1)[0].upper() if body else ""
        if head not in ("SELECT", "EXPLAIN") or ";" in body.rstrip(";"):
            raise RuntimeError(f"the action gate's executor runs SELECT/EXPLAIN only, refused: {head}")
        if refusal:
            raise RuntimeError(refusal)
        binds = list(params) if params is not None else None
        if session is not None:
            return session.run(body, binds)
        return backend.run_sync_query(body, binds)
    return run


def sample_params(m, names: Tuple[str, ...]) -> list:
    """Representative bind values for an EXPLAIN — never executed, never a literal in the
    statement. A declared domain's first value where there is one, else a typed zero."""
    by = {a.name: a for a in m.attributes}
    out = []
    for n in names:
        a = by.get(n)
        if a is not None and a.value_domain:
            out.append(a.value_domain[0])
            continue
        t = a.type if a is not None else "string"
        out.append({"integer": 0, "decimal": 0, "boolean": 0, "date": "2000-01-01",
                    "timestamp": "2000-01-01 00:00:00"}.get(t, "x"))
    return out


async def read_dictionary(backend, m) -> Tuple[Dict[str, Dict[str, str]], Dict[str, List[Tuple[str, ...]]]]:
    """The target's (and the ledger's) columns, and the target's unique indexes."""
    from trusted_data_agent.dbdeploy.action_model import ledger_identifier
    db = m.target_db()
    tables = [m.target_table()]
    led = ledger_identifier(m)
    if led:
        tables.append(led.split(".", 1)[1])
    described = await backend.describe_tables(db, tables)
    schema = {ident: entry.get("columns") or {} for ident, entry in (described or {}).items()}
    unique: Dict[str, List[Tuple[str, ...]]] = {}
    try:
        rows = await backend.unique_indexes(m.deployed)
        unique[m.deployed] = [tuple(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — the live key probe decides A3 regardless
        logger.info(f"action_types: unique indexes of {m.deployed} not read: {exc}")
    return schema, unique


async def provision_ledger(store, m, backend, *, provisioner=None,
                           provisioner_label: str = "") -> Optional[Dict[str, Any]]:
    """Create the platform ledger once and register it on the graph (§5.5). Idempotent:
    an existing ledger table is left as it is; ``None`` when the action records into the
    target's own audit columns.

    Creating the ledger is a MODEL change (a CREATE TABLE in the extension's database), not
    one of the action's writes: it runs through ``provisioner`` — the connection the model
    was landed through — when one is known, else through the action's own connection. The
    action's login typically may only write rows (Act II: the investigator login was refused
    CREATE TABLE and validation died with a 500). A refusal is a named result, never an
    exception: the gate still runs, and the ledger says what is missing and what would fix it."""
    from trusted_data_agent.dbdeploy.action_model import ledger_ddl, ledger_identifier
    from trusted_data_agent.dbcatalog.sql_runner import friendly_db_error
    led = ledger_identifier(m)
    if not led:
        return None
    db, table = led.split(".", 1)
    existing = await backend.describe_tables(db, [table])
    created = False
    if not existing:
        via = provisioner or backend
        try:
            await via.execute_statement(ledger_ddl(m))
            created = True
        except Exception as exc:  # noqa: BLE001 — a refusal is said, the gate still runs
            who = provisioner_label or "the action's connection"
            return {"identifier": led, "created": False, "missing": True,
                    "reason": f"the ledger {led} does not exist and could not be created through {who}: "
                              f"{friendly_db_error(exc)} — creating it is a model change: land it through a "
                              f"connection whose login may CREATE TABLE in {db}, or grant that right"}
    node = store.get_entity_by_name(led, "ddl_artifact")
    if not node:
        nid = await _maybe_await(store.add_entity(led, "ddl_artifact", {
            "layer": "physical", "purpose": "action_ledger", "action": m.action,
            "infrastructure": "teradata",
            "description": f"append-only audit of every performed '{m.action}' invocation"}))
        tgt = store.get_entity_by_name(m.deployed, "table")
        if tgt:
            try:
                await _maybe_await(store.add_relationship(nid, tgt["id"], "derives_from"))
            except Exception:  # noqa: BLE001
                pass
    return {"identifier": led, "created": created}
