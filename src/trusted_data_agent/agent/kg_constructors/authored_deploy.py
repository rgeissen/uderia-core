"""
authored_deploy — land the graph's AUTHORED layer on its warehouse (PROP-008 A3, §4.3).

The orchestration around the pure planner (``dbdeploy/delta.py``): read what was
drafted, ask the backend what the target holds and whether the login owns the model's
database, plan the delta, run it, then make the graph say what NOW exists — a physical
``table``/``column`` node per landed object, ``implements`` back to the authored node,
the authored node marked ``deployed`` with its realisation and its receipt.

Every executed delta is a ``model_change`` receipt: a closed field tuple (the landing,
each statement's kind + identifier + hash, what landed, what was skipped or refused),
Ed25519-signed with the deployment's provenance key — the same substrate as the
publish attestation — and shipped as a governance span.  Receipts are observability,
never context (§2b): nothing here reaches a prompt path.

Strategy-independent: an authored node may sit on any graph with a deployed physical
layer (a discovery graph, an industry model, a declared model); the planner reads the
graph, not the constructor that built it.
"""

from __future__ import annotations

import inspect
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from trusted_data_agent.dbdeploy import delta as D

logger = logging.getLogger("quart.app")

from trusted_data_agent.kgstore.types import REALISATION_SOURCE as DEPLOY_SOURCE  # "authored_deploy"
from trusted_data_agent.components import signals as _signals
_AUTHORED = "authored"


async def _maybe_await(x):
    return await x if inspect.isawaitable(x) else x


def _props(e: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return (e or {}).get("properties") or {}


def _is_authored(e: Optional[Dict[str, Any]]) -> bool:
    return _props(e).get("layer") == _AUTHORED


# ── reading the graph ────────────────────────────────────────────────────────

def realisation_on(props: Dict[str, Any], infrastructure: Optional[str], primary: bool, key: str = "deployed_table") -> Optional[str]:
    """Where an authored node is realised on ``infrastructure``: its ``realisations`` entry, or
    — on the graph's primary infrastructure only — its ``deployed_*`` identifier (C3)."""
    rz = (props or {}).get("realisations") or {}
    if infrastructure and isinstance(rz.get(infrastructure), dict):
        hit = rz[infrastructure].get("table" if key == "deployed_table" else "column")
        if hit:
            return hit
    if not primary:
        return None
    return (props or {}).get(key) or None


async def graph_infrastructure(store) -> Optional[str]:
    """The infrastructure of the graph's own physical layer (``None`` — the graph has none
    yet).  A deploy to ANOTHER infrastructure records its realisation on the authored node
    and never adds a second physical layer (ONTOLOGY §2.4b: one deployed physical layer)."""
    try:
        for e in await _maybe_await(store.list_entities(limit=999999)):
            pr = _props(e)
            if pr.get("layer") == "physical" and e.get("entity_type") in ("schema", "table"):
                return str(pr.get("infrastructure") or "teradata")
    except Exception:  # noqa: BLE001
        return None
    return None


async def read_authored(store, *, infrastructure: Optional[str] = None, primary: bool = True) -> Dict[str, Any]:
    """The planner's ``authored`` input, read from the graph: authored entities with their
    attributes and parent, authored attributes on BASE entities, and the realisations an
    earlier deploy stamped whose authored node is gone (``removed``).  ``infrastructure`` /
    ``primary`` choose which realisation a landed node resumes from (C3); drops are read
    from the graph's physical layer, so only the primary infrastructure has any."""
    ents = await _maybe_await(store.list_entities(limit=999999))
    rels = await _maybe_await(store.list_relationships(limit=999999))
    by_id = {e.get("id"): e for e in ents}
    by_name = {e.get("name"): e for e in ents}
    out: Dict[str, Any] = {"entities": [], "attributes": [], "removed": [], "base_entities": set()}
    contains_of: Dict[int, List[Dict]] = {}
    parent_of: Dict[int, Optional[Dict]] = {}
    implemented_by: Dict[int, Dict] = {}     # authored id → physical node implementing it
    for r in rels:
        rt = r.get("relationship_type")
        s, t = by_id.get(r.get("source_entity_id")), by_id.get(r.get("target_entity_id"))
        if rt == "contains" and s is not None and t is not None:
            contains_of.setdefault(s["id"], []).append(t)
        elif rt == "relates_to" and s is not None and t is not None and _is_authored(s) \
                and s.get("entity_type") == "entity" and t.get("entity_type") in ("entity", "table"):
            parent_of[s["id"]] = t
        elif rt == "implements" and s is not None and t is not None and _props(s).get(DEPLOY_SOURCE):
            implemented_by[t["id"]] = s
    for e in ents:
        if not _is_authored(e):
            continue
        et = e.get("entity_type")
        if et == "entity":
            attrs = [{"name": a["name"], "props": _props(a)} for a in contains_of.get(e["id"], [])
                     if a.get("entity_type") == "attribute"]
            parent = parent_of.get(e["id"])
            out["entities"].append({"name": e["name"], "id": e["id"], "props": _props(e), "attributes": attrs,
                                    "parent": parent["name"] if parent else None,
                                    "deployed": realisation_on(_props(e), infrastructure, primary)})
            if parent:
                out["base_entities"].add(parent["name"])
        elif et == "attribute":
            # authored attribute: its container decides — a BASE entity (a new column on an
            # existing table) or an authored entity (planned with that entity above)
            owner = next((s for s, kids in contains_of.items() if any(k.get("id") == e["id"] for k in kids)), None)
            oe = by_id.get(owner) if owner is not None else None
            if oe is not None and not _is_authored(oe) and oe.get("entity_type") in ("entity", "table"):
                out["attributes"].append({"name": e["name"], "id": e["id"], "props": _props(e), "entity": oe["name"]})
                out["base_entities"].add(oe["name"])
    # realisations stamped by an earlier deploy whose authored node no longer exists
    gone_tables: Set[str] = set()
    if not primary:
        return out
    for e in ents:
        pr = _props(e)
        if not pr.get(DEPLOY_SOURCE) or e.get("entity_type") != "table":
            continue
        realised = pr.get("realises")
        if realised and realised not in by_name:
            gone_tables.add(str(e["name"]))
            out["removed"].append({"kind": "table", "identifier": e["name"], "node": realised, "physical_id": e["id"]})
    for e in ents:
        pr = _props(e)
        if not pr.get(DEPLOY_SOURCE) or e.get("entity_type") != "column":
            continue
        realised = pr.get("realises")
        # a column of a removed table goes with the table — listed once, as the table
        if realised and realised not in by_name and str(e["name"]).rpartition(".")[0] not in gone_tables:
            out["removed"].append({"kind": "column", "identifier": e["name"], "node": realised, "physical_id": e["id"]})
    return out


async def gather_facts(store, names: Set[str]) -> Dict[str, Dict[str, Any]]:
    """``{entity: {"deployed", "key"}}`` for the base entities the plan references — the
    declared key through the ONE reader, the realisation through the ONE resolver."""
    from trusted_data_agent.agent.kg_constructors.source_validation import key_columns
    from components.builtin.knowledge_graph.resolver import resolve_deployed_table
    facts: Dict[str, Dict[str, Any]] = {}
    for n in names:
        e = store.get_entity_by_name(n)
        if not e:
            continue
        pr = _props(e)
        dep = pr.get("deployed_table") or ""
        if not dep:
            try:
                r = await resolve_deployed_table(store, n)
                dep = r.identifier if r else ""
            except Exception:  # noqa: BLE001
                dep = ""
        if not dep and e.get("entity_type") == "table":
            dep = e["name"]
        facts[n] = {"deployed": dep, "key": key_columns(pr)}
    return facts


def _deployed_root(facts: Dict[str, Dict[str, Any]]) -> str:
    """Pure: the database the model's own tables are DEPLOYED in — the most common
    database part of the base entities' deployed identifiers (a platform sibling never
    counts). '' when nothing is deployed."""
    from trusted_data_agent.dbdeploy.delta import EXT_PREFIX
    counts: Dict[str, int] = {}
    for f in facts.values():
        dep = str(f.get("deployed") or "")
        db = dep.partition(".")[0] if "." in dep else ""
        if db and not db.upper().startswith(EXT_PREFIX.upper()):
            counts[db] = counts.get(db, 0) + 1
    return max(sorted(counts), key=lambda d: counts[d]) if counts else ""


async def _root_database(store, facts: Dict[str, Dict[str, Any]]) -> str:
    """The model's root database AS DEPLOYED. A declared model names its logical root for
    people ("Policing Core") while its tables live in ``Policing_Core`` — the sibling and
    the ownership check must use the deployed name (Act II rehearsal: the preview named
    ``UDERIA_EXT_Policing Core``). The `database` node's name is the fallback for a graph
    with nothing deployed yet."""
    root = _deployed_root(facts)
    if root:
        return root
    try:
        ents = await _maybe_await(store.list_entities(limit=999999))
        for e in ents:
            if e.get("entity_type") == "database":
                return str(e["name"])
    except Exception:  # noqa: BLE001
        pass
    return ""


# ── the deploy ───────────────────────────────────────────────────────────────

@_signals.announces("obda_model", "deployed", user="store.user_uuid", ids=("store.kg_id",), profile="store.profile_id", when=lambda r: isinstance(r, dict) and r.get("status") not in ("noop", "error"))
async def deploy_authored(store, backend, *, actor: str, dry_run: bool = False, force_drop: bool = False,
                          comments: str = "table", standard=None, choose_pi=None,
                          sibling_parent: Optional[str] = None, sibling_perm_bytes: Optional[int] = None,
                          landed_via: Optional[str] = None) -> Dict[str, Any]:
    """Plan (and, unless ``dry_run``, land) the authored layer on ``backend``.

    ``sibling_parent`` / ``sibling_perm_bytes`` are the provisioning knobs for a
    platform-owned sibling database (``UDERIA_EXT_<root>``) when one has to be created —
    deployment knobs, never the landing choice itself."""
    from trusted_data_agent.dbdeploy.delta import resolve_delta_dialect
    infra = str(getattr(backend, "dialect", None) or getattr(backend, "backend_type", None) or "teradata")
    dialect = resolve_delta_dialect(infra, standard)
    g_infra = await graph_infrastructure(store)
    primary = g_infra is None or g_infra == infra
    authored = await read_authored(store, infrastructure=infra, primary=primary)
    if not authored["entities"] and not authored["attributes"] and not authored["removed"]:
        return {"status": "noop", "summary": "nothing is authored on this graph", "plan": None}
    facts = await gather_facts(store, set(authored["base_entities"]))
    root = await _root_database(store, facts)
    if not root:
        return {"status": "error", "message": "the graph has no database root and no deployed base entity — "
                                              "nowhere to land", "plan": None}
    # a realisation recorded without its database (an unqualified ``deployed_table``) lives
    # in the model's root database — the only place a walked table can be
    for f in facts.values():
        dep = str(f.get("deployed") or "")
        if dep and "." not in dep:
            f["deployed"] = f"{root}.{dep}"
    # the landing: deployment policy, decided once (§13)
    try:
        owned = await _maybe_await(backend.database_owned(root))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OBDA-MODEL deploy] ownership of %s unknown: %s", root, exc)
        owned = None
    landing = D.landing_for(root, owned)
    # the dictionary: the landing database (new tables) + every base table a column is added to
    dictionary: Dict[str, Any] = {"databases": set(), "tables": {}}
    dbs_to_read: Dict[str, Set[str]] = {landing.database: set()}
    for ent in authored["entities"]:
        dep = ent.get("deployed")
        if dep and "." in dep:
            dbs_to_read.setdefault(dep.partition(".")[0], set()).add(dep.rpartition(".")[2])
    for f in facts.values():
        dep = str(f.get("deployed") or "")
        if "." in dep:
            dbs_to_read.setdefault(dep.partition(".")[0], set()).add(dep.rpartition(".")[2])
    for db, names in dbs_to_read.items():
        try:
            exists = await _maybe_await(backend.database_exists(db))
        except Exception:  # noqa: BLE001
            exists = False
        if exists:
            dictionary["databases"].add(db)
            try:
                dictionary["tables"].update(await _maybe_await(backend.describe_tables(db, None)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[OBDA-MODEL deploy] dictionary of %s could not be read: %s", db, exc)
    # the PI choice: the standard's declared-key rule measured (A4) — an authored table is
    # NEW and empty, so there are no rows to probe and the declared key stands; the hook
    # still records the decision so the receipt names the rule
    pi_decisions: Dict[str, Any] = {}
    if choose_pi is None and dialect.key_places_rows:
        from trusted_data_agent.dbdeploy import probes as _probes
        amps = None
        try:
            amps = await _maybe_await(backend.amp_count())
        except Exception:  # noqa: BLE001
            amps = None
        choose_pi = _probes.as_delta_hook({}, amps=amps, decisions=pi_decisions)
    plan = D.plan_delta(authored, facts, dictionary, landing, force_drop=force_drop, comments=comments,
                        removed=authored["removed"], choose_pi=choose_pi, dialect=dialect)
    for t, d in pi_decisions.items():
        plan.pi_decisions[t] = d.to_dict() if hasattr(d, "to_dict") else dict(d)
    system = None
    try:
        system = await _maybe_await(backend.system_version()) if hasattr(backend, "system_version") else None
    except Exception:  # noqa: BLE001
        system = None
    design = plan_design(plan, system=system, standard=standard, dialect=dialect)
    info: Dict[str, Any] = {"landing": landing.to_dict(), "summary": plan.summary(), "plan": plan.to_dict(),
                            "warnings": list(plan.warnings), "design": design, "backend": infra,
                            "infrastructure": {"graph": g_infra, "this_deploy": infra, "primary": primary}}
    # VALIDATED: the plan conforms to the design standard for the node — the road's second
    # step, earned by a preview as much as by a landing (a dry run is the validation act)
    info["validated"] = await _mark_validated(store, plan, design)
    # a database to create: WHERE it goes and HOW LARGE, decided once and said in the preview
    # (the landing below creates exactly this; the person may override both, then preview again)
    if plan.requires_database and hasattr(backend, "provisioning"):
        try:
            info["provisioning"] = await _maybe_await(backend.provisioning(
                landing.database, parent=sibling_parent, perm_bytes=sibling_perm_bytes))
        except Exception as exc:  # noqa: BLE001 — the preview still says a database is created
            logger.info("[OBDA-MODEL deploy] provisioning of %s unread: %s", landing.database, exc)
    # ACCESS, before anything runs: what this landing needs of its login, held or missing,
    # with the exact GRANT — the preview says it, and a landing does not start without it
    login = str(getattr(backend, "_username", "") or "")
    if login and hasattr(backend, "read_access"):
        try:
            from trusted_data_agent.dbdeploy import access as _A
            reqs = _A.landing_requirements(plan, landing, login, info.get("provisioning"))
            if reqs:
                info["access"] = {"login": login, **_A.judge(reqs, await _maybe_await(backend.read_access([login], [])))}
        except Exception as exc:  # noqa: BLE001 — an unchecked access is said as unchecked
            logger.info("[OBDA-MODEL deploy] access of %s unread: %s", login, exc)
    acc = info.get("access")
    if not dry_run and acc and acc.get("readable") and not acc.get("ok"):
        info["status"] = "error"
        info["code"] = "access_missing"
        info["message"] = (f"the landing login {login} lacks what this landing needs — nothing was changed; "
                           f"grant exactly: {' '.join(acc.get('grants') or [])}")
        return info
    if dry_run:
        info["status"] = "dry_run"
        info["generated_sql"] = plan.sql_script()
        return info
    if plan.is_noop:
        info["status"] = "noop"
        return info
    if plan.requires_database:
        try:
            created = await _maybe_await(backend.ensure_database(landing.database, parent=sibling_parent,
                                                                 perm_bytes=sibling_perm_bytes))
            info["database_created"] = bool(created)
        except Exception as exc:  # noqa: BLE001
            info["status"] = "error"
            from trusted_data_agent.dbcatalog.sql_runner import friendly_db_error
            parent = sibling_parent or ((info.get("provisioning") or {}).get("parent")) or "its parent"
            info["message"] = (f"the landing database {landing.database} could not be created under {parent}: "
                               f"{friendly_db_error(exc)} — grant the connection's login CREATE DATABASE on "
                               f"{parent}, or choose another parent in the preview")
            return info
    res = await _maybe_await(backend.execute_delta(plan))
    info["result"] = res.to_dict()
    landed_ids = {ident for _k, ident in res.landed}
    # ── the readback: the dictionary says what the warehouse HOLDS, rule by rule (A4) ──
    readback = await read_back(backend, plan, landing, system=system, standard=standard, dialect=dialect)
    info["readback"] = readback.to_dict() if readback is not None else None
    # ── the graph says what now exists ──
    stamped = await _stamp_realisations(store, plan, landed_ids, actor, landing, readback=readback,
                                        infrastructure=infra, primary=primary, landed_via=landed_via)
    info["stamped"] = stamped
    receipt = _receipt(store, plan, res, actor, landing, readback=readback)
    info["receipt"] = receipt
    await _stamp_receipt(store, plan, landed_ids, receipt, infrastructure=infra, primary=primary)
    _emit(receipt)
    info["status"] = "success" if res.success else "partial"
    return info


# ── the design standard at the two moments it speaks (A4) ─────────────────────

def plan_design(plan: D.DeltaPlan, *, system: Optional[str] = None, standard=None, dialect=None) -> Dict[str, Any]:
    """What the standard says about THIS plan before anything runs: the rules with their
    verification status on the target version, and per authored table whether the plan
    conforms (a table without a key is a finding — conformance calls it partial — but
    never a refusal: NO PRIMARY INDEX is the standard's own answer to a keyless table)."""
    from trusted_data_agent.dbdeploy.delta import resolve_delta_dialect
    from trusted_data_agent.dbdeploy.design_standard import rules_report
    from trusted_data_agent.dbdeploy.readback import planned_from_ddl
    dl = dialect or resolve_delta_dialect(getattr(plan, "backend", None), standard)
    rules = rules_report(system, standard=standard if dl.name == "teradata" else None, rule_set=dl.rule_set())
    keyless_rule, keyless_msg = dl.keyless_finding()
    tables: Dict[str, Dict[str, Any]] = {}
    planned = planned_from_ddl(plan, dialect=dl)
    for ch in plan.changes:
        if ch.kind != "table" or ch.state == "refused":
            continue
        findings: List[Dict[str, str]] = []
        pt = planned.get(ch.identifier)
        if ch.state == "planned" and pt is not None and pt.keyless:
            findings.append({"severity": "medium", "rule": keyless_rule, "message": keyless_msg})
        for w in plan.warnings:
            if w.startswith(ch.node + ".") and "no data type declared" in w:
                findings.append({"severity": "low", "rule": "type_declared", "message": w})
        dec = plan.pi_decisions.get(ch.identifier) or {}
        if dec.get("rule", "").startswith("demoted"):
            findings.append({"severity": "medium", "rule": "pi_even_distribution", "message": dec.get("reason") or "the declared key was demoted"})
        tables[ch.identifier] = {"node": ch.node, "state": ch.state, "findings": findings,
                                 "conformance": "nonconformant" if any(f["severity"] == "high" for f in findings)
                                 else "partial" if findings else "conformant",
                                 "pi": dec}
    return {"backend": dl.name, "standard": rules.get("standard") or dl.standard_version, "system": system,
            "rules": rules, "tables": tables}


async def _mark_validated(store, plan: D.DeltaPlan, design: Dict[str, Any]) -> List[str]:
    """Stamp ``validated_at`` (+ ``status: validated`` on a draft) on every authored node THIS
    landing realises in a table that conforms to the standard with no HIGH finding: the entity
    and its attributes (one stage — an entity read validated beside draft attributes split them
    into rows of their own), or only the new attributes when the table already exists. A node
    already landed or published is not part of this landing (``awaits_landing``) — never
    stamped, never named. Returns what the preview validated, in the model's names: the entity
    when it lands, else each attribute that does. Idempotent."""
    from trusted_data_agent.kgstore.types import awaits_landing
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    done: List[str] = []
    for ident, t in (design.get("tables") or {}).items():
        if t.get("conformance") == "nonconformant":
            continue
        prefix = str(ident).lower() + "."
        members = [(t["node"], "entity")] + sorted(
            (str(n), "attribute") for n, rid in plan.realises.items() if str(rid).lower().startswith(prefix))
        named_entity = False
        for name, etype in members:
            node = store.get_entity_by_name(name, etype)
            if not node:
                continue
            props = _props(node)
            if not awaits_landing(etype, props.get("status")):
                continue
            listed = etype == "entity" or not named_entity
            if props.get("validated_at"):
                if listed:
                    done.append(name)
                named_entity = named_entity or etype == "entity"
                continue
            upd: Dict[str, Any] = {"validated_at": stamp, "standard": design.get("standard")}
            if (props.get("status") or "draft") == "draft":
                upd["status"] = "validated"
            try:
                await _maybe_await(store.update_entity(node["id"], properties=upd))
                if listed:
                    done.append(name)
                named_entity = named_entity or etype == "entity"
            except Exception as exc:  # noqa: BLE001
                logger.info("[OBDA-MODEL deploy] %s not marked validated: %s", name, exc)
    return done


async def read_back(backend, plan: D.DeltaPlan, landing: D.Landing, *, system: Optional[str] = None, standard=None,
                    dialect=None):
    """After a landing: the dictionary for every table the plan realised (landed or already
    there), the measured distribution of each primary index, and — in a database the
    platform owns outright (the sibling) — any object the model did not build.  Returns
    a ``ReadbackReport`` or ``None`` when the backend reads no dictionary at all."""
    from trusted_data_agent.dbdeploy.readback import planned_from_ddl, readback_report
    # only tables this plan CREATED are read back: a resumed table (``exists``) has no CREATE
    # to compare against — its conformance was read when it landed
    planned = planned_from_ddl(plan, dialect=dialect)
    idents = sorted(planned)
    if not idents:
        return None
    try:
        observed = await _maybe_await(backend.read_design(idents))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OBDA-MODEL deploy] readback failed: %s", exc)
        observed = {}
    skew: Dict[str, Any] = {}
    for ident, pt in planned.items():
        obs = (observed or {}).get(ident) or {}
        cols = obs.get("pi_columns") or pt.pi_columns
        if cols and ident in (observed or {}) and (dialect is None or dialect.key_places_rows):
            try:
                m = await _maybe_await(backend.measure_pi_skew(ident, list(cols)))
                if m:
                    skew[ident] = m
            except Exception as exc:  # noqa: BLE001
                logger.info("[OBDA-MODEL deploy] skew of %s not measured: %s", ident, exc)
    foreign: List[str] = []
    if landing.rule == "platform_sibling":
        try:
            objs = await _maybe_await(backend.list_database_objects(landing.database))
            own = {i.rpartition(".")[2].lower() for i in plan.realises.values() if i.count(".") == 1} | \
                  {i.rpartition(".")[2].lower() for i in planned}
            foreign = [f"{landing.database}.{n}" for k, n in objs if k in ("table", "view") and n.lower() not in own]
        except Exception as exc:  # noqa: BLE001
            logger.info("[OBDA-MODEL deploy] objects of %s not listed: %s", landing.database, exc)
    return readback_report(planned, observed or {}, skew=skew, foreign_objects=foreign, system=system,
                           standard=standard, dialect=dialect)


async def _stamp_realisations(store, plan: D.DeltaPlan, landed: Set[str], actor: str, landing: D.Landing,
                              readback=None, infrastructure: Optional[str] = None, primary: bool = True,
                              landed_via: Optional[str] = None) -> Dict[str, int]:
    """Physical nodes for what landed, ``implements`` back to the authored node, the
    authored node marked deployed with its realisation.  A graph whose base layer is
    read-only (a subscriber's copy) keeps the realisation on the authored node only — and
    so does a landing on an infrastructure that is not the graph's own (C3): the node's
    ``realisations[<infrastructure>]`` names it, the graph keeps ONE physical layer."""
    counts = {"physical_nodes": 0, "authored_updated": 0, "physical_skipped": 0}
    infra = infrastructure or getattr(store, "infrastructure", None) or "teradata"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    schema_ids: Dict[str, Optional[int]] = {}
    # the Database Connection the landing ran through — a fact of THIS deployment on the
    # realisation (which never travels), so a later act on the table reads it the same way
    via = {"landed_via": landed_via} if landed_via else {}

    async def schema_node(db: str) -> Optional[int]:
        if not primary:
            return None
        if db in schema_ids:
            return schema_ids[db]
        node = store.get_entity_by_name(db, "schema")
        if node:
            schema_ids[db] = node["id"]
            return node["id"]
        try:
            sid = await _maybe_await(store.add_entity(name=db, entity_type="schema",
                                                      properties={"layer": "physical", "infrastructure": infra, "label": db,
                                                                  DEPLOY_SOURCE: True, **via},
                                                      source=DEPLOY_SOURCE, source_detail=actor))
            counts["physical_nodes"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.info("[OBDA-MODEL deploy] schema node %s not written: %s", db, exc)
            sid = None
        schema_ids[db] = sid
        return sid

    async def physical(name: str, etype: str, props: Dict[str, Any], container: Optional[int], realises: str,
                       realises_type: str) -> Optional[int]:
        if not primary:
            return None
        existing = store.get_entity_by_name(name, etype)
        if existing:
            return existing["id"]
        try:
            pid = await _maybe_await(store.add_entity(name=name, entity_type=etype,
                                                      properties={"layer": "physical", "infrastructure": infra,
                                                                  "label": name.rpartition(".")[2],
                                                                  DEPLOY_SOURCE: True, "realises": realises, **via,
                                                                  **({"standard": plan.standard} if plan.standard else {}),
                                                                  **props},
                                                      source=DEPLOY_SOURCE, source_detail=actor))
            counts["physical_nodes"] += 1
        except Exception as exc:  # noqa: BLE001 — a read-only base layer; the authored node keeps the realisation
            logger.info("[OBDA-MODEL deploy] physical node %s not written: %s", name, exc)
            counts["physical_skipped"] += 1
            return None
        if container is not None:
            try:
                await _maybe_await(store.add_relationship(container, pid, "contains", source=DEPLOY_SOURCE))
            except Exception:  # noqa: BLE001
                pass
        target = store.get_entity_by_name(realises, realises_type)
        if target:
            try:
                await _maybe_await(store.add_relationship(pid, target["id"], "implements", source=DEPLOY_SOURCE))
            except Exception:  # noqa: BLE001
                pass
        return pid

    async def mark(node_name: str, etype: str, realised: str, fresh: bool = True) -> None:
        node = store.get_entity_by_name(node_name, etype)
        if not node:
            return
        if not fresh and not awaits_landing(etype, _props(node).get("status")):
            # its object was already there and the node had landed: nothing about it changed —
            # re-stamped, a published node's content moved and its extension read "edited"
            # (Act II: a column landed into the Lead table re-stamped the published Lead)
            return
        key = "deployed_table" if etype == "entity" else "deployed_column"
        rz = dict(_props(node).get("realisations") or {})
        rz[infra] = {**(rz.get(infra) or {}), ("table" if etype == "entity" else "column"): realised,
                     "deployed_at": stamp, "standard": plan.standard or None}
        # the lifecycle only moves forward: a published node landing on a second warehouse
        # stays published (its realisations change, which the extension's hash reports)
        from trusted_data_agent.kgstore.types import advance_status
        props: Dict[str, Any] = {"status": advance_status(_props(node).get("status"), "deployed"),
                                 "realisations": rz}
        if primary:
            props.update({key: realised, "deployed_at": stamp})
        if plan.standard:
            props["standard"] = plan.standard
        if etype == "entity" and readback is not None:
            # the readback section this node earned: its own rows of the report, in counts
            rows = [r for r in readback.rows if r.table.lower() == realised.lower()]
            rb = {"status": readback.status if rows else "unverified",
                                 "held": sum(1 for r in rows if r.status == "held"),
                                 "violated": sum(1 for r in rows if r.status == "violated"),
                                 "unverified": sum(1 for r in rows if r.status == "unverified"),
                                 "violations": [f"{r.rule}: {r.detail}" for r in rows if r.status == "violated"][:6],
                                 "standard": readback.standard, "at": stamp}
            rz[infra]["readback"] = rb
            if primary:
                props["readback"] = rb
        try:
            await _maybe_await(store.update_entity(node["id"], properties=props))
            counts["authored_updated"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.info("[OBDA-MODEL deploy] authored node %s not marked: %s", node_name, exc)

    from trusted_data_agent.kgstore.types import awaits_landing
    # tables that landed (or already existed — a resumed table is deployed too)
    table_changes = [c for c in plan.changes if c.kind == "table" and (c.identifier in landed or c.state == "exists")]
    for tc in table_changes:
        db, leaf = tc.identifier.partition(".")[0], tc.identifier.rpartition(".")[2]
        auth = store.get_entity_by_name(tc.node, "entity")
        key = ", ".join(k.strip() for k in str(_props(auth).get("business_key") or "").split(",") if k.strip())
        sid = await schema_node(db)
        tid = await physical(tc.identifier, "table", {"schema": db, "primary_key": key or None,
                                                     "description": _props(auth).get("description") or None},
                             sid, tc.node, "entity")
        await mark(tc.node, "entity", tc.identifier, fresh=tc.identifier in landed)
        # its columns: every attribute the plan realised under this table
        for an, cid in plan.realises.items():
            if cid.rpartition(".")[0] != tc.identifier or an == tc.node:
                continue
            # a freshly created table carries every column; a resumed table carries the
            # columns that exist plus the ALTERs that landed — never a refused or failed one
            if tc.identifier not in landed:
                col_state = next((c.state for c in plan.changes if c.kind == "column" and c.identifier == cid), "planned")
                if col_state == "refused" or (col_state == "planned" and cid not in landed):
                    continue
            a = store.get_entity_by_name(an, "attribute")
            await physical(cid, "column", {"datatype": _props(a).get("data_type") or None,
                                           "description": _props(a).get("description") or None},
                           tid, an, "attribute")
            await mark(an, "attribute", cid, fresh=tc.identifier in landed or cid in landed)
    # columns added to BASE tables
    for cc in [c for c in plan.changes if c.kind == "column" and (c.identifier in landed or c.state == "exists")]:
        tbl = cc.identifier.rpartition(".")[0]
        if any(tc.identifier == tbl for tc in table_changes):
            continue   # handled with its (authored) table above
        ptab = store.get_entity_by_name(tbl, "table")
        a = store.get_entity_by_name(cc.node, "attribute")
        await physical(cc.identifier, "column", {"datatype": _props(a).get("data_type") or None,
                                                 "description": _props(a).get("description") or None},
                       ptab["id"] if ptab else None, cc.node, "attribute")
        await mark(cc.node, "attribute", cc.identifier, fresh=cc.identifier in landed)
    # a reference that landed: a foreign_key edge between the physical columns
    for fk in [c for c in plan.changes if c.kind == "foreign_key" and c.identifier in landed and primary]:
        src = store.get_entity_by_name(fk.identifier, "column")
        m = _fk_target(fk.sql)
        tgt = store.get_entity_by_name(m, "column") if m else None
        if src and tgt:
            try:
                await _maybe_await(store.add_relationship(src["id"], tgt["id"], "foreign_key", cardinality="N:1",
                                                          source=DEPLOY_SOURCE))
            except Exception:  # noqa: BLE001
                pass
    # forced drops: the physical node goes with the object
    for dc in [c for c in plan.changes if c.kind.startswith("drop_") and c.identifier in landed]:
        node = store.get_entity_by_name(dc.identifier, "table" if dc.kind == "drop_table" else "column")
        if not node:
            continue
        doomed = [node["id"]]
        if dc.kind == "drop_table":
            # the table's physical columns go with it
            try:
                for r in await _maybe_await(store.list_relationships(entity_id=node["id"], relationship_type="contains", limit=9999)):
                    if r.get("source_entity_id") == node["id"] and r.get("target_type") == "column":
                        doomed.append(r["target_entity_id"])
            except Exception:  # noqa: BLE001
                pass
        for nid in doomed:
            try:
                await _maybe_await(store.delete_entity(nid))
            except Exception:  # noqa: BLE001
                pass
    return counts


_FK_RE = re.compile(r'REFERENCES\s+(?:WITH NO CHECK OPTION\s+)?"([^"]+)"\."([^"]+)"\s*\(\s*"([^"]+)"\s*\)', re.I)


def _fk_target(sql: str) -> Optional[str]:
    m = _FK_RE.search(sql or "")
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else None


def _receipt(store, plan: D.DeltaPlan, res: D.DeltaResult, actor: str, landing: D.Landing, readback=None) -> Dict[str, Any]:
    receipt: Dict[str, Any] = {
        "receipt_id": "mc_" + uuid.uuid4().hex[:12], "kind": "model_change", "backend": getattr(plan, "backend", None),
        "kg_id": getattr(store, "kg_id", None),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": actor,
        "landing": landing.to_dict(), "pi_rule": plan.pi_rule,
        "standard": plan.standard, "pi_decisions": dict(plan.pi_decisions),
        "readback": readback.to_dict() if readback is not None else None,
        "statements": [{"kind": c.kind, "identifier": c.identifier, "node": c.node, "sql_hash": D.sql_hash(c.sql)}
                       for c in plan.pending()],
        "landed": [list(x) for x in res.landed], "skipped": [list(x) for x in res.skipped],
        "refused": [{"identifier": c.identifier, "reason": c.reason} for c in plan.refused()],
        "errors": [list(x) for x in res.errors],
    }
    try:
        from trusted_data_agent.eval import online
        receipt["attestation"] = online.sign_payload(D.receipt_content(receipt))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[OBDA-MODEL deploy] receipt attestation unavailable: %s", exc)
        receipt["attestation"] = {"signed": False}
    return receipt


async def _stamp_receipt(store, plan: D.DeltaPlan, landed: Set[str], receipt: Dict[str, Any], *,
                         infrastructure: Optional[str] = None, primary: bool = True) -> None:
    small = {"receipt_id": receipt["receipt_id"], "at": receipt["at"], "by": receipt["by"],
             "backend": receipt.get("backend"),
             "landing": receipt["landing"]["database"],
             "signed": bool((receipt.get("attestation") or {}).get("signed")),
             "key_fingerprint": (receipt.get("attestation") or {}).get("key_fingerprint"),
             "standard": receipt.get("standard"),
             "readback": ((receipt.get("readback") or {}).get("status") if receipt.get("readback") else None)}
    from trusted_data_agent.kgstore.types import awaits_landing
    exists = {c.identifier for c in plan.changes if c.state == "exists"}
    for node_name, ident in plan.realises.items():
        # a column of a table this plan created landed WITH the table
        table_of = ident.rpartition(".")[0]
        if ident not in landed and ident not in exists and table_of not in landed:
            continue
        fresh = ident in landed or table_of in landed
        for et in ("entity", "attribute"):
            node = store.get_entity_by_name(node_name, et)
            if node:
                if not fresh and not awaits_landing(et, _props(node).get("status")):
                    break   # landed before, nothing landed for it now: its receipt is the one that landed it
                upd: Dict[str, Any] = {}
                if primary:
                    upd["deploy_receipt"] = {**small, "identifier": ident}
                if infrastructure:
                    rz = dict(_props(node).get("realisations") or {})
                    rz[infrastructure] = {**(rz.get(infrastructure) or {}), "receipt": {**small, "identifier": ident}}
                    upd["realisations"] = rz
                try:
                    await _maybe_await(store.update_entity(node["id"], properties=upd))
                except Exception:  # noqa: BLE001
                    pass
                break


def _emit(receipt: Dict[str, Any]) -> None:
    try:
        from trusted_data_agent.eval import online
        online.emit_governance_span({
            "name": "ontology.model_change", "ontology.kg_id": receipt.get("kg_id"),
            "model_change.receipt_id": receipt.get("receipt_id"), "model_change.by": receipt.get("by"),
            "model_change.landing": (receipt.get("landing") or {}).get("database"),
            "model_change.landed": len(receipt.get("landed") or []),
            "model_change.errors": len(receipt.get("errors") or []),
            "attestation": receipt.get("attestation"),
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[OBDA-MODEL deploy] span emit failed: %s", exc)
