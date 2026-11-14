"""
Knowledge Graph Component Handler.

Processes TDA_KnowledgeGraph tool calls for graph queries, visualization,
and entity management. Also provides context enrichment for the planner
by extracting relevant subgraph context based on the user's query.

This is the guardrail mechanism: by injecting known relationships into the
planner prompt, the LLM makes better decisions about which tools to use
and how to construct queries.
"""

from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
import json
import logging
from html import escape as html_escape
import asyncio
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.components.base import (
    BaseComponentHandler,
    ComponentRenderPayload,
    RenderTarget,
)

logger = logging.getLogger("quart.app")

# Entity type → color mapping for D3 visualization
ENTITY_TYPE_COLORS = {
    # logical (LDM)
    "database": "#3b82f6",         # blue
    "entity": "#22c55e",           # green  (logical relation)
    "attribute": "#a3e635",        # lime   (logical field)
    "foreign_key": "#f59e0b",      # amber
    "business_concept": "#8b5cf6", # violet
    "taxonomy": "#ec4899",         # pink
    "metric": "#06b6d4",           # cyan
    "domain": "#f97316",           # orange
    # product-description layer — rose/purple family: what the product says about itself
    "query_recipe": "#e11d48",     # rose
    "design_decision": "#c026d3",  # fuchsia
    "quality_metric": "#14b8a6",   # teal-cyan
    "naming_standard": "#a855f7",  # purple
    "product_bridge": "#f43f5e",   # rose-red — a claimed cross-product link
    # governed-consumption layer — gold: a signed, deterministic consumption contract
    "data_product": "#eab308",     # gold — an OBDA-READ data product (the signable contract)
    "action_type": "#dc2626",      # signal red — a signed write contract (PROP-008 OBDA-ACT)
    # physical (PDM) — slate/teal family to read as a distinct layer
    "catalog": "#64748b",          # slate
    "schema": "#94a3b8",           # light slate
    "table": "#0d9488",            # teal       (deployed relation)
    "column": "#5eead4",           # light teal (deployed field)
    "ddl_artifact": "#78716c",     # stone
}


def _derive_scope_databases(capabilities: Dict[str, Any], entities: List[Dict]) -> List[str]:
    """The deployed, queryable database scope for a KG enrichment (lower-bounded,
    deterministic). Drives the eval scope-adherence scorer and the Tier 1.1 grounding
    gate. Pure — no I/O.

    Primary path — the DEPLOYED physical identifiers resolved from the graph's physical
    layer (industry-model KG with a deployed PDM: ``db.table`` / ``db.view``).

    Fallback path — agent-discovery KGs are logical-only (no physical layer), so the
    primary path resolves nothing. But a discovery KG was walked from a LIVE warehouse,
    so its logical ``database`` root node IS the real queryable scope (logical ==
    physical for discovery). Derive scope from the ``database`` node(s) when, and only
    when, the physical path is empty — industry-model KGs never reach the fallback (so
    their precise physical scope is never widened), and a logical-only *industry*
    reference model with no deployed layer still declares its subject-area database.
    Without this, the grounding gate would silently abstain for the most common KG type
    despite its scope being fully known. See docs/roadmap/TIER_1.1_GROUNDING_GATE.md §3e.
    """
    scope: set = set()
    for cap in (capabilities or {}).values():
        for ident in (getattr(cap, "deployed_as", None),
                      getattr(cap, "metric_view", None),
                      getattr(cap, "metric_column", None),
                      getattr(cap, "metric_base_table", None)):
            if isinstance(ident, str) and "." in ident:
                scope.add(ident.split(".")[0])
    if not scope:
        for e in entities or []:
            if (e or {}).get("entity_type") == "database":
                props = e.get("properties") or {}
                name = props.get("database_name") or e.get("name")
                if isinstance(name, str) and name.strip():
                    scope.add(name.strip())
    return sorted(scope)


def _derive_full_kg_scope_dbs(table_entities: List[Dict]) -> set:
    """Deployed databases of a KG's PHYSICAL `table` nodes (qualified `db.table` names).
    The full bound-KG deployed scope — query-independent — used as the gate's always-on
    trusted scope so out-of-scope drift is caught even on queries that match no KG entity."""
    dbs = set()
    for e in table_entities or []:
        nm = (e or {}).get("name") or ""
        if "." in nm:
            dbs.add(nm.split(".", 1)[0].strip())
    return {d for d in dbs if d}


def _columns_complete(subgraph: Dict[str, Any], roots: Optional[List[Dict]] = None) -> bool:
    """Pure: does this graph's constructor list EVERY column of its tables? Read from the
    graph's CONTENT — a subscribed copy rewrites `source` to `marketplace` and carries no
    descriptor, so the signal must travel with the nodes. The database root of an industry
    model carries `industry` + `scope`; a declared (ISO 11179) model carries `standard:
    iso_11179`; an agent-discovery graph's nodes carry `source: agent_discovery`. An ANDP
    graph (`standard: ai_native_data_product`) is NOT complete — its `column_metadata`
    covers a fraction of the deployed columns — and an unrecognised graph is not assumed
    to be: a guard that refuses on a partial list would refuse real columns."""
    ents = subgraph.get("entities") or []
    # the graph's database ROOT(s): from the store when the caller fetched them (an
    # entity hangs under its DOMAIN, so the root is rarely in the subgraph), else whatever
    # the subgraph carries
    for e in list(roots or []) + list(ents):
        if e.get("entity_type") != "database":
            continue
        pr = e.get("properties") or {}
        std = str(pr.get("standard") or "").lower()
        if std == "ai_native_data_product":
            return False
        if std == "iso_11179":
            return True
        if pr.get("industry") and pr.get("scope"):
            return True
    srcs = {str(e.get("source") or "") for e in ents}
    if srcs & {"agent_discovery", "industry_model_constructor", "declared_model_constructor", "teradata_deployment"}:
        return not (srcs & {"andp_semantic", "andp_semantic_constructor"})
    return False


def _key_columns(props: Dict) -> List[str]:
    """The entity's declared natural key as a column list.

    Delegates to `kgstore.keys` — the ONE reading, shared with the column-existence
    preflight's list builders. Two parsers over one declaration is how the JOIN check
    and the COLUMN check came to disagree about the same two columns."""
    from trusted_data_agent.kgstore.keys import key_columns as _kc
    return _kc(props)


def _declared_join_pairs(subgraph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pure: every table pair with a DECLARED foreign key, with the join columns the two
    tables' declared keys share. ONE computation for the renderer (the DECLARED FOREIGN KEYS
    block) and for the ``join_keys`` metadata the preflight guard enforces — so what the
    agent is told and what the guard checks can never diverge.

    Returns [{"tables": [a, b], "leaves": [leaf_a, leaf_b] (lower), "key": [shared cols],
              "keys": {a: [...], b: [...]}}], sorted; ``key`` may be empty when the tables
    declare no shared key column (the pair is still declared)."""
    ents = subgraph.get("entities") or []
    table_props = {e["name"]: (e.get("properties") or {}) for e in ents
                   if e.get("entity_type") in ("entity", "table")}
    tbl_leaf = {t.rpartition(".")[2].lower(): t for t in table_props}
    # endpoint types: from the edge when it carries them, else from the subgraph's own
    # entities by id (an older backend's edge shape omitted them)
    _type_by_id = {e.get("id"): e.get("entity_type") for e in ents if e.get("id") is not None}
    pairs: set = set()
    # The COLUMNS a column-level FK edge joins, per pair — on a model whose keys are
    # declared as edges (the industry models, agent discovery) rather than as a table's
    # `business_key` (ANDP), the edge IS the declaration of the join column.
    edge_cols: Dict[frozenset, set] = {}
    for rel in subgraph.get("relationships") or []:
        if rel.get("relationship_type") != "foreign_key":
            continue
        sn, tn = str(rel.get("source_name") or ""), str(rel.get("target_name") or "")
        _st = rel.get("source_type") or _type_by_id.get(rel.get("source_id"))
        _tt = rel.get("target_type") or _type_by_id.get(rel.get("target_id"))
        if _st in ("attribute", "column") and _tt in ("attribute", "column"):
            s_t = tbl_leaf.get(sn.rpartition(".")[0].rpartition(".")[2].lower())
            t_t = tbl_leaf.get(tn.rpartition(".")[0].rpartition(".")[2].lower())
            if s_t and t_t and s_t != t_t:
                edge_cols.setdefault(frozenset((s_t, t_t)), set()).add(
                    ((s_t, sn.rpartition(".")[2]), (t_t, tn.rpartition(".")[2])))
        else:
            s_t = tbl_leaf.get(sn.rpartition(".")[2].lower())
            t_t = tbl_leaf.get(tn.rpartition(".")[2].lower())
        if s_t and t_t and s_t != t_t:
            pairs.add(frozenset((s_t, t_t)))
    def _dep_leaf(name: str) -> str:
        """The leaf a SQL statement names — the DEPLOYED object's when the entity binds one
        (a logical `Policing Core.Incident` deploys as `Policing_Core.v_Incident`, and the
        guard keys its checks on what the SQL says), else the entity's own leaf. On graphs
        whose entity names ARE the deployed leaves (ANDP) the two coincide."""
        dep = (table_props.get(name) or {}).get("deployed_table")
        src = dep if isinstance(dep, str) and dep.strip() else name
        return src.rpartition(".")[2].strip().lower()

    out = []
    for pr in pairs:
        a, b = sorted(pr)
        ka, kb = _key_columns(table_props.get(a) or {}), _key_columns(table_props.get(b) or {})
        kb_l = {k.lower() for k in kb}
        key = [k for k in ka if k.lower() in kb_l]
        # oriented to (a, b): [[col_on_a, col_on_b], ...]
        edges = sorted({(ca if ta == a else cb, cb if ta == a else ca)
                        for (ta, ca), (tb, cb) in edge_cols.get(pr, set())})
        # No declared table key, ONE edge, the SAME column name on both sides — the join
        # column is unambiguous and becomes the pair's checkable key (2026-09-19: on
        # `mileage_accrual ⇄ ffp_member` the guard had nothing to enforce and the model
        # joined `booking_segment_id = ffp_member_id`). Two edges between one pair (a
        # sender and a receiver FK to the same table) or two different names stay
        # UN-keyed by construction: a guard that enforced either would refuse legitimate
        # joins, and fail-open is the guard's contract. The renderer still names them.
        if not key and not ka and not kb and len(edges) == 1 and edges[0][0].lower() == edges[0][1].lower():
            key = [edges[0][0]]
        out.append({"tables": [a, b],
                    "leaves": [_dep_leaf(a), _dep_leaf(b)],
                    "key": key,
                    "keys": {a: ka, b: kb},
                    "edges": [list(e) for e in edges]})
    return sorted(out, key=lambda d: d["tables"])


def _fanout_keys(subgraph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pure: the columns that would FAN OUT if two tables were joined on them — a column
    that is a FOREIGN key in two or more tables of the subgraph (each pointing at the same
    owner table), as [{"column", "owner" (the table whose key it is, deployed leaf or None),
    "tables": [deployed leaves of the FK holders]}]. Joining two of the holders to each other
    on it is fact-to-fact through a dimension key: every redemption row of a member against
    every benefit row of the same member (prod 2026-09-19: 2,638,492 "miles redeemed" where
    the true figure was 17,708). ONE computation for the DECLARED FOREIGN KEYS / NOT JOIN
    KEYS render and for the ``fanout_keys`` metadata the preflight guard enforces."""
    ents = subgraph.get("entities") or []
    table_props = {e["name"]: (e.get("properties") or {}) for e in ents
                   if e.get("entity_type") in ("entity", "table")}
    tbl_leaf = {t.rpartition(".")[2].lower(): t for t in table_props}
    _type_by_id = {e.get("id"): e.get("entity_type") for e in ents if e.get("id") is not None}

    def _dep_leaf(name: str) -> str:
        dep = (table_props.get(name) or {}).get("deployed_table")
        src = dep if isinstance(dep, str) and dep.strip() else name
        return src.rpartition(".")[2].strip().lower()

    holders: Dict[str, Dict[str, Any]] = {}          # column leaf → {"owner": table, "tables": set}
    for rel in subgraph.get("relationships") or []:
        if rel.get("relationship_type") != "foreign_key":
            continue
        st = rel.get("source_type") or _type_by_id.get(rel.get("source_id"))
        tt = rel.get("target_type") or _type_by_id.get(rel.get("target_id"))
        if st not in ("attribute", "column") or tt not in ("attribute", "column"):
            continue
        sn, tn = str(rel.get("source_name") or ""), str(rel.get("target_name") or "")
        s_t = tbl_leaf.get(sn.rpartition(".")[0].rpartition(".")[2].lower())
        t_t = tbl_leaf.get(tn.rpartition(".")[0].rpartition(".")[2].lower())
        col = sn.rpartition(".")[2]
        if not s_t or not col or s_t == t_t:
            continue
        # keyed by the SOURCE column's leaf — the name the SQL uses on the holder's side
        h = holders.setdefault(col.lower(), {"column": col, "owner": None, "tables": set()})
        # A table whose OWN declared primary key is this very column (a 1:1 extension
        # table — its PK is the FK) cannot fan out: at most one row per key value, so a
        # join to it on the key never multiplies. It is not a holder. Only a single-column
        # key qualifies — a composite key containing the column still admits many rows.
        if not _pk_is_column(table_props.get(s_t) or {}, col):
            h["tables"].add(s_t)
        if t_t and tn.rpartition(".")[2].lower() == col.lower():
            h["owner"] = t_t
    out = []
    for col, h in holders.items():
        if len(h["tables"]) < 2:
            continue
        out.append({"column": h["column"],
                    "owner": _dep_leaf(h["owner"]) if h["owner"] else None,
                    "owner_name": h["owner"],
                    "tables": sorted(_dep_leaf(t) for t in h["tables"]),
                    "table_names": sorted(h["tables"])})
    return sorted(out, key=lambda d: d["column"])


def _pk_is_column(props: Dict[str, Any], col: str) -> bool:
    """Pure: does the table declare ``col`` as its ENTIRE primary key (single column)?"""
    pk = _split_cols(props.get("primary_key") or props.get("business_key") or [])
    return len(pk) == 1 and pk[0].lower() == str(col).lower()


def _split_cols(value: Any) -> List[str]:
    """'ori, incident_number' | ['ori', 'incident_number'] → ['ori', 'incident_number']."""
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


_THEATRE_ENTITY_TYPES = ("entity", "table", "data_product", "product_bridge", "metric")
_THEATRE_ATTR_TYPES = ("attribute", "column")


def _theatre_facts(subgraph: Dict[str, Any], seeds: List[Dict[str, Any]],
                   seeding_report: Optional[Dict[str, Any]], query: str) -> Dict[str, Any]:
    """Pure: the ER facts the KG theatre narrates from — WHY each seed rose (the scoring
    walk's own reasons), HOW each node entered the subgraph (the extractor's provenance),
    the entity-level relationships with their keys and cardinality, each entity's key
    attributes, and the per-attribute facts a statement is checked against. Everything
    here is already computed for the enrichment; this only keeps it on the event so the
    narration is a reading of the pipeline, never a reconstruction of it. Bounded by the
    subgraph's own node budget; fails soft to a partial dict."""
    ents = subgraph.get("entities") or []
    rels = subgraph.get("relationships") or []
    names_by_id = {e.get("id"): str(e.get("name") or "") for e in ents}
    rendered = set(names_by_id.values())
    terms = _scoring_terms(query)
    out: Dict[str, Any] = {"question_terms": terms}

    # seeds — reasons + the terms that reached them + the recall channel
    bridged = {str(b.get("name")) for b in (seeding_report or {}).get("bridged", []) or []
               if isinstance(b, dict)}
    carried = set(((seeding_report or {}).get("carry_over") or {}).get("entities") or [])
    rows = []
    for e in seeds or []:
        nm = str(e.get("name") or "")
        if nm not in rendered:
            continue
        score, reasons, matched = _match_reasons(e, terms)
        et = e.get("entity_type")
        channel = ("product" if et == "data_product" else
                   "carry_over" if nm in carried else
                   "semantic" if nm in bridged else "lexical")
        rows.append({"name": nm, "type": et, "score": int(score), "reasons": reasons,
                     "terms": matched, "channel": channel})
    out["seeds"] = rows

    # provenance — id → tag, mapped to names; bridge tables override their tag
    prov = subgraph.get("provenance") or {}
    bridge_ids = set(subgraph.get("_bridge_ids") or [])
    out["provenance"] = {names_by_id[i]: ("bridge" if i in bridge_ids else tag)
                         for i, tag in prov.items() if i in names_by_id and names_by_id[i]}
    for i in bridge_ids:
        if i in names_by_id and names_by_id[i]:
            out["provenance"][names_by_id[i]] = "bridge"

    # entity-level relationships with keys + cardinality
    types_by_id = {e.get("id"): e.get("entity_type") for e in ents}
    erels = []
    keys: Dict[str, List[str]] = {}
    fks: Dict[str, List[str]] = {}
    for r in rels:
        st, tt = types_by_id.get(r.get("source_id")), types_by_id.get(r.get("target_id"))
        if st not in _THEATRE_ENTITY_TYPES or tt not in _THEATRE_ENTITY_TYPES:
            continue
        rtype = str(r.get("relationship_type") or "")
        if rtype in ("contains", "implements"):
            continue
        meta = r.get("metadata") or {}
        fc, tc = _split_cols(meta.get("from_columns")), _split_cols(meta.get("to_columns"))
        sn, tn = names_by_id.get(r.get("source_id"), ""), names_by_id.get(r.get("target_id"), "")
        erels.append({"source": sn, "target": tn, "type": rtype,
                      "cardinality": r.get("cardinality"),
                      "name": meta.get("relationship_name"),
                      "from_columns": fc, "to_columns": tc,
                      "mechanism": meta.get("linkage_mechanism") or meta.get("mechanism")})
        # a 1:N foreign key: the ONE side's columns are its key, the N side's its FK
        if rtype == "foreign_key" and fc and tc:
            one, many = (sn, tn) if str(r.get("cardinality") or "1:N") in ("1:N", "1:1") else (tn, sn)
            kcols, fcols = (fc, tc) if one == sn else (tc, fc)
            k = keys.setdefault(one, [])
            for c in kcols:
                if c not in k:
                    k.append(c)
            f = fks.setdefault(many, [])
            for c in fcols:
                if c not in f:
                    f.append(c)
    out["entity_relationships"] = erels

    # attribute facts, and identifiers as key attributes
    attrs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for e in ents:
        if e.get("entity_type") not in _THEATRE_ATTR_TYPES:
            continue
        nm = str(e.get("name") or "")
        owner, _, leaf = nm.rpartition(".")
        if not owner or not leaf:
            continue
        p = e.get("properties") or {}
        fact: Dict[str, Any] = {}
        for src, dst in (("deployed_type", "type"), ("data_type", "declared_type"),
                         ("semantic_type", "semantic"), ("sampled_shape", "shape"),
                         ("decoded_from", "decoded_from"), ("decoded_as", "decoded_as"),
                         ("date_range", "span"), ("label", "label")):
            v = p.get(src)
            if v not in (None, "", [], {}):
                fact[dst] = v
        if p.get("is_pii"):
            fact["pii"] = True
        if p.get("allowed_values"):
            fact["coded"] = True
        if p.get("semantic_type") == "identifier":
            k = keys.setdefault(owner, [])
            if leaf not in k:
                k.append(leaf)
        attrs.setdefault(owner, {})[leaf] = fact
    # The DECLARED key is the key: an entity that declares one is drawn with exactly those
    # columns, complete and in declared order. Identifier-typed attributes are only the
    # fallback for an entity that declares none — read as the key they drew `tool_id` and
    # `source_bundle_id` as key columns and dropped `ori` from Arrestee's (the column was
    # budgeted out of the subgraph), so the stage contradicted the model it narrates.
    for e in ents:
        if e.get("entity_type") in _THEATRE_ENTITY_TYPES:
            dk = _key_columns(e.get("properties") or {})
            if dk:
                keys[str(e.get("name") or "")] = list(dk)
    out["attribute_facts"] = attrs
    out["entity_keys"] = keys
    out["entity_foreign_keys"] = fks
    # per-entity facts the stage reads directly: deployed identifier, subject area,
    # description, validated row count / key uniqueness (the source-validation stamps)
    efacts: Dict[str, Dict[str, Any]] = {}
    for e in ents:
        if e.get("entity_type") not in _THEATRE_ENTITY_TYPES:
            continue
        p = e.get("properties") or {}
        f: Dict[str, Any] = {"type": e.get("entity_type")}
        for src, dst in (("deployed_table", "deployed"), ("subject_area", "area"),
                         ("description", "description"), ("row_count", "rows"),
                         ("key_unique", "key_unique"), ("basis", "basis"),
                         ("linkage_mechanism", "mechanism")):
            v = p.get(src)
            if v not in (None, "", [], {}):
                f[dst] = v
        efacts[str(e.get("name") or "")] = f
    out["entity_facts"] = efacts
    if subgraph.get("omitted_columns"):
        out["omitted_columns"] = dict(subgraph["omitted_columns"])
    return out


def _subject_area_map(subgraph: Dict[str, Any]) -> Dict[str, str]:
    """Pure: ``{deployed leaf (lower): subject_area (lower)}`` for every logical entity that
    declares a subject area AND a deployed table — the domain partition of a declared
    model (crime / evidence on the policing core model). Graphs that declare no subject
    areas (ANDP, industry, discovery) yield ``{}``, so the cross-domain join check they
    feed stays silent there by construction."""
    out: Dict[str, str] = {}
    for e in subgraph.get("entities") or []:
        if (e or {}).get("entity_type") != "entity":
            continue
        props = e.get("properties") or {}
        area = props.get("subject_area")
        dep = props.get("deployed_table")
        if isinstance(area, str) and area.strip() and isinstance(dep, str) and dep.strip():
            out[dep.rpartition(".")[2].strip().lower()] = area.strip().lower()
    return out


def _class_bridges(subgraph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pure: every ``type_correspondence`` product_bridge with the deployed leaves and
    subject areas of the entities it relates — so a guard refusing a cross-domain
    instance join can NAME the class-level correspondence the model does declare."""
    ents = subgraph.get("entities") or []
    by_name = {e.get("name"): e for e in ents if e.get("name")}
    areas = _subject_area_map(subgraph)
    rel_targets: Dict[str, List[str]] = {}
    for rel in subgraph.get("relationships") or []:
        if rel.get("relationship_type") == "relates_to" and rel.get("source_type") == "product_bridge":
            rel_targets.setdefault(str(rel.get("source_name")), []).append(str(rel.get("target_name")))
    out: List[Dict[str, Any]] = []
    # The relevance-scoped subgraph may carry the bridge node WITHOUT its relates_to
    # edges (seen live: the bridge matched the question, its edges did not make the
    # budget), so the ends are resolved from the bridge's own declaration
    # (from/to_object_class) as well — by the entity whose name ends in that class.
    by_leaf_class: Dict[str, str] = {}
    for e in ents:
        if e.get("entity_type") == "entity" and e.get("name"):
            by_leaf_class[str(e["name"]).rpartition(".")[2].lower()] = e["name"]
    for e in ents:
        props = (e or {}).get("properties") or {}
        if e.get("entity_type") != "product_bridge" or props.get("linkage_mechanism") != "type_correspondence":
            continue
        ends = list(rel_targets.get(e.get("name"), []))
        for cls in (props.get("from_object_class"), props.get("to_object_class")):
            n = by_leaf_class.get(str(cls or "").lower())
            if n and n not in ends:
                ends.append(n)
        leaves, bareas = [], []
        for tn in ends:
            dep = ((by_name.get(tn) or {}).get("properties") or {}).get("deployed_table") or ""
            leaf = dep.rpartition(".")[2].lower() if isinstance(dep, str) else ""
            if leaf:
                leaves.append(leaf)
                if areas.get(leaf):
                    bareas.append(areas[leaf])
        entry: Dict[str, Any] = {"name": props.get("label") or e.get("name"), "tables": sorted(set(leaves)),
                                 "areas": sorted(set(bareas)), "basis": props.get("basis") or ""}
        # What the correspondence BINDS, as data — the registry's from/to values, the
        # ends' deployed leaves, and the from-column's decoded domain (code → label) —
        # so the honesty gate can tell a kind the correspondence covers from one it
        # does not (an agent once read "robbery/burglary ↔ evidence" as covering hacking).
        # Every field is optional; a registry that predates the columns yields none.
        def _leaf_of(cls):
            n = by_leaf_class.get(str(cls or "").lower())
            dep = ((by_name.get(n) or {}).get("properties") or {}).get("deployed_table") if n else None
            return dep.rpartition(".")[2].lower() if isinstance(dep, str) and dep else None
        fv, tv = props.get("from_values"), props.get("to_values")
        if isinstance(fv, list) and fv:
            entry["from_values"] = [str(v) for v in fv]
        if isinstance(tv, list) and tv:
            entry["to_values"] = [str(v) for v in tv]
        for side in ("from", "to"):
            cls, col = props.get(f"{side}_object_class"), props.get(f"{side}_columns")
            leaf = _leaf_of(cls)
            if cls:
                entry[f"{side}_class"] = str(cls)
            if leaf:
                entry[f"{side}_table"] = leaf
            if col:
                entry[f"{side}_column"] = str(col)
            ent_name = by_leaf_class.get(str(cls or "").lower())
            attr = by_name.get(f"{ent_name}.{col}") if ent_name and col else None
            codes = ((attr or {}).get("properties") or {}).get("allowed_values")
            if isinstance(codes, list) and codes:
                dom = {}
                for c in codes:
                    if isinstance(c, dict) and c.get("value") is not None:
                        dom[str(c["value"])] = str(c.get("label") or "")
                if dom:
                    entry[f"{side}_domain"] = dom
        out.append(entry)
    return sorted(out, key=lambda d: str(d["name"]))


def _db_node_name(e: Dict) -> str:
    props = (e or {}).get("properties") or {}
    nm = props.get("database_name") or (e or {}).get("name") or ""
    return nm.strip() if isinstance(nm, str) else ""


def _entity_area(name: str, subgraph: Dict[str, Any]) -> Optional[str]:
    """Pure: the subject area an entity OR one of its attributes belongs to (an attribute
    `Domain.Entity.column` resolves through its owning entity). None when the graph
    declares no area for it."""
    ents = subgraph.get("entities") or []
    by_name = {e.get("name"): e for e in ents if e.get("name")}
    n = str(name or "")
    for cand in (n, n.rpartition(".")[0]):
        e = by_name.get(cand)
        if e and e.get("entity_type") == "entity":
            area = ((e.get("properties") or {}).get("subject_area") or "")
            return area.strip().lower() or None
    return None


def _spanning_correspondences(seeds: List[Dict], subgraph: Dict[str, Any],
                              bridges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure: the class-level correspondences whose BOTH subject areas the question's
    seeds reach. This is the whole class of cross-area questions on an integrated model:
    two areas, no per-record key between them, one declared correspondence — and a
    question that names something in each. Keyed on nothing but generic graph facts
    (`subject_area` on entities, a `type_correspondence` bridge with its areas), so a
    graph that declares no areas yields [] and is untouched. Measured 2026-09-21 on the
    Ideate lane: with the bridge rendered only as one entity line near the bottom, a weak
    model wrote the cross-area join 3 of 9 times and, refused, gave up twice; the pack
    prose that prevented it was restating the graph. The graph now leads with it instead."""
    seed_areas = {a for a in (_entity_area(e.get("name"), subgraph) for e in seeds or []) if a}
    out = []
    for b in bridges or []:
        areas = set(b.get("areas") or [])
        if len(areas) >= 2 and areas <= seed_areas:
            out.append(b)
    return out


def _render_correspondence_lead(bridges: List[Dict[str, Any]]) -> List[str]:
    """Pure: the lines that LEAD the enrichment when a question spans the areas of a
    declared correspondence — the correspondence is the answer's shape, so it goes first,
    filled from the same bridge facts the entity line renders (one source)."""
    lines: List[str] = []
    for b in bridges:
        areas = ", ".join(sorted(b.get("areas") or []))
        lines.append(
            f"THIS QUESTION SPANS TWO SUBJECT AREAS ({areas}). The model declares NO per-record "
            f"key between them — a JOIN across them is refused before it runs. It declares ONE "
            f"class-level correspondence, {b.get('name')}: {b.get('basis') or 'see the bridge entity below'}")
        fv, tv = b.get("from_values"), b.get("to_values")
        cov = []
        if fv:
            cov.append(f"covers {b.get('from_class') or b.get('from_table') or 'from'}.{b.get('from_column') or 'code'} "
                       f"IN ({', '.join(repr(v) for v in fv)})")
        if tv:
            cov.append(f"<-> {b.get('to_class') or b.get('to_table') or 'to'}.{b.get('to_column') or 'kind'} "
                       f"IN ({', '.join(repr(v) for v in tv)})")
        if cov:
            lines.append("  " + " ".join(cov))
        lines.append(
            "  Answer a cross-area question by STATING this correspondence (the kinds it binds) and "
            "each area's own facts from its own tables; never one figure spanning both areas; a kind "
            "the correspondence does not list is NOT bound by it. Never record or claim that a SPECIFIC "
            "record of one area is linked to a specific record of the other — nothing supports it; a "
            "suspicion is recorded as an enquiry that says so, attached only to evidence the user named.")
    return lines


#: The words that may sit between a question opener and the thing it asks about —
#: determiners and the state adjectives people put on a subject ("which OPEN leads").
_SUBJECT_SKIP = {"the", "all", "any", "our", "my", "your", "their", "of", "these", "those",
                 "open", "closed", "active", "current", "new", "existing", "pending", "recent",
                 "outstanding", "unresolved", "known", "other", "remaining",
                 # qualifiers that precede a subject and are never one themselves — "show me the
                 # ACTUAL email" / "show me ONE email" read back as "Not in the model: actual"
                 # (prod session 86ac6b28 turn 12) because the first unskipped word was taken
                 "actual", "real", "full", "entire", "whole", "original", "raw", "complete",
                 "specific", "exact", "latest", "earliest", "first", "last", "next", "previous",
                 "same", "one", "single", "each", "every", "few", "some", "more", "another",
                 "such", "that", "this", "top", "only", "just", "please"}
_SUBJECT_VERBS = {"is", "are", "was", "were", "do", "does", "did", "there", "have", "has", "me", "us"}
_SUBJECT_OPENER = re.compile(
    r"\b(?:which|what|how\s+many|list|show(?:\s+me)?|count|are\s+there\s+any|do\s+we\s+have(?:\s+any)?)\s+"
    r"((?:[a-z]+\s+){0,5}[a-z]+)", re.I)


def _question_subject(query: str) -> Optional[str]:
    """Pure: the head noun a question ASKS ABOUT — the word after its opener ("which
    leads …", "how many robberies …", "list open leads …", "do we have any warrants …"),
    skipping determiners and state adjectives. None when the question has no such
    opener: the subject is decided only where the sentence's own shape says it, never
    guessed from the rest of the words (PROP-008 Act II, scene 7)."""
    m = _SUBJECT_OPENER.search(str(query or ""))
    if not m:
        return None
    for w in (m.group(1) or "").split():
        lw = w.lower()
        if (lw in _SUBJECT_SKIP or lw in _STOP_WORDS or lw in _SUBJECT_VERBS
                or lw in _LOOKUP_NOISE or len(lw) < 3):
            continue
        return lw
    return None


def _model_gap(query: str, unreached: List[str], semantic_bridged: int = 0) -> Optional[str]:
    """Pure: the question's subject when NOTHING in the whole graph matched it — not by
    name, label, description or declared value, in either its own or its singular form,
    and not by meaning (any semantic bridge withdraws the claim: it may have found what
    the word meant). A subject is a thing the model does not describe; saying so is the
    honest answer, and presenting another entity in its place is the failure this exists
    to prevent (scene 7: evidence types read back as "the open leads")."""
    subj = _question_subject(query)
    if not subj or semantic_bridged:
        return None
    miss = {str(t).lower() for t in unreached or []}
    sing = _singularize(subj) or subj
    if subj in miss and sing in miss:
        return subj
    return None


#: The entities a model-gap proposal may belong to, offered by the note's form (bounded).
_GAP_MAX_PARENTS = 120


def _render_gap_lead(term: str, may_author: bool) -> List[str]:
    """Pure: the line that LEADS the enrichment when the question's subject is not in
    the model. Bounded (one or two lines); the propose clause only for an agent the
    authoring gate would let propose."""
    sing = _singularize(term) or term
    lines = [f"NOT IN THIS MODEL: the question asks about '{term}', and nothing in this model "
             f"(no entity, attribute, declared value or term) is a {sing}. Do not present another "
             f"entity as {term}. Say plainly that the model does not record {term}."]
    if may_author:
        # The OFFER is the platform's (agent/model_gap.py renders it under every engine's
        # answer); the model is asked only for the act it alone can do once asked.
        lines.append(f"  The platform offers to extend the model with a {sing} under your answer — do "
                     f"not offer it yourself. Only when asked to, propose the {sing} with action "
                     f"'propose_entity' and present the advisor's verdict.")
    return lines


def _derive_scope_entities(entities: List[Dict], limit: int = 50) -> List[str]:
    """Human-meaningful in-scope concept names (logical entities, metrics, business
    concepts) — so the scope is **self-describing**: downstream consumers (the
    ``scope_faithfulness`` judge, transparent reload/audit) know WHAT the trusted scope
    covers, not just the database name. Bounded; pure. Mirrors ``_derive_scope_databases``
    as the conceptual companion of the database-level scope signal."""
    names: List[str] = []
    seen = set()
    for e in entities or []:
        # A product_bridge is a declared in-scope concept too: an agent that names it
        # ("Policing Core.bridge.Offence__CaseObject") is reading the model, not reaching
        # outside it — without it here the grounding trigger read that name as a database.
        if (e or {}).get("entity_type") in ("entity", "metric", "business_concept", "product_bridge"):
            n = (e or {}).get("name")
            if isinstance(n, str) and n.strip() and n not in seen:
                seen.add(n)
                names.append(n)
    return sorted(names)[:limit]



#: Decoded values rendered per column before truncating (bounded prompt payload).
_MAX_CODES_PER_COLUMN = 8
#: Repeating groups rendered per enrichment (bounded, sorted — deterministic).
_MAX_FAMILIES = 12
#: Columns that get a decoded-value line in the TABLE SCHEMAS block.
_MAX_CODED_COLUMNS = 40


# The text-date vocabulary lives in ONE place — `components/dateforms.py` — shared with
# the preflight guard and every constructor's source validation; these names stay as
# the handler's aliases so the enrichment renderer and the gates read one definition.
from trusted_data_agent.components.dateforms import (  # noqa: E402
    looks_like_date_attr as _looks_like_date_attr,
    is_text_date_attr as _is_text_date_attr,
    is_char_type as _is_char_type,
    MIN_DATE_WIDTH as _MIN_DATE_WIDTH,
    _NOT_DATE_WORDS, _DATE_NAME_RE, _CHAR_TYPES,
)


def _text_date_columns(subgraph: Dict[str, Any], capabilities: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """[{"table": deployed_or_logical, "columns": [{"name", "type"}]}] for every text-stored
    date attribute, keyed the way `_table_columns_map` keys a table (its deployed identifier
    when resolved) — what the preflight text-date guard records for the session."""
    capabilities = capabilities or {}
    ents = subgraph.get("entities") or []
    tables = {e["name"] for e in ents if e.get("entity_type") == "entity"}
    attrs = {e["name"]: (e.get("properties") or {}) for e in ents if e.get("entity_type") == "attribute"}
    out: Dict[str, List[Dict[str, str]]] = {}
    for rel in subgraph.get("relationships") or []:
        if rel.get("relationship_type") != "contains" or rel.get("source_name") not in tables:
            continue
        props = attrs.get(rel.get("target_name"))
        if props is None or not _is_text_date_attr(props):
            continue
        tbl = rel["source_name"]
        cap = capabilities.get(tbl)
        key = (cap.deployed_as if (cap and getattr(cap, "deployed_as", None)) else tbl)
        entry = {"name": str(rel["target_name"]).rpartition(".")[2],
                 "type": str(props.get("deployed_type") or "")}
        if props.get("sampled_shape"):
            # validated against the source at construction — the guard needs no probe
            entry["shape"] = str(props["sampled_shape"])
            if props.get("sample_value"):
                entry["sample"] = str(props["sample_value"])
            if isinstance(props.get("date_unparseable_ratio"), (int, float)):
                entry["unparseable"] = float(props["date_unparseable_ratio"])
                if props.get("date_unparseable_sampled"):
                    entry["unparseable_sampled"] = True
        rng = props.get("date_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2 and all(rng):
            # the measured extent of the data — what lets the guard refuse a filter on a
            # period the extract does not cover (never learnable from result rows)
            entry["span"] = [str(rng[0]), str(rng[1])]
            if props.get("date_range_sampled"):
                entry["span_sampled"] = True
        out.setdefault(key, []).append(entry)
    return [{"table": t, "columns": sorted(c, key=lambda d: d["name"])} for t, c in sorted(out.items())]


def _value_domains(subgraph: Dict[str, Any], capabilities: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """[{"table": deployed_or_logical, "columns": [{"name", "values": {code: label},
    "kind": "code"|"label", "pair": other_column}]}] for every attribute with a declared
    value domain — a coded column's codes, and a decoded-label column's labels (the
    walker links the pair by `source_column`). Keyed the way `_table_columns_map` keys
    a table — what the preflight VALUE guard records for the session: a literal a
    declared domain does not contain is provable from the ontology alone."""
    capabilities = capabilities or {}
    ents = subgraph.get("entities") or []
    tables = {e["name"] for e in ents if e.get("entity_type") == "entity"}
    attrs = {e["name"]: (e.get("properties") or {}) for e in ents if e.get("entity_type") == "attribute"}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for rel in subgraph.get("relationships") or []:
        if rel.get("relationship_type") != "contains" or rel.get("source_name") not in tables:
            continue
        props = attrs.get(rel.get("target_name"))
        if props is None:
            continue
        name = str(rel["target_name"]).rpartition(".")[2]
        entry = None
        raw = props.get("allowed_values")
        if isinstance(raw, list) and raw:
            vals = {}
            for c in raw:
                if isinstance(c, dict) and c.get("value") is not None and str(c["value"]).strip():
                    vals[str(c["value"])] = str(c.get("label") or "")
            if vals:
                entry = {"name": name, "values": vals, "kind": "code"}
                if props.get("decoded_as"):
                    entry["pair"] = str(props["decoded_as"])
        elif props.get("decoded_labels") and props.get("decoded_from"):
            labels = [str(x) for x in props["decoded_labels"] if str(x).strip()]
            if labels:
                entry = {"name": name, "values": {x: x for x in labels}, "kind": "label",
                         "pair": str(props["decoded_from"])}
        if entry is None:
            continue
        tbl = rel["source_name"]
        cap = capabilities.get(tbl)
        key = (cap.deployed_as if (cap and getattr(cap, "deployed_as", None)) else tbl)
        out.setdefault(key, []).append(entry)
    return [{"table": t, "columns": sorted(c, key=lambda d: d["name"])} for t, c in sorted(out.items())]


def _rank_codes(raw: List[Any], terms: Optional[List[str]]) -> List[Any]:
    """The declared values, most relevant to the question first — stable otherwise.

    An OPTIMISATION of the bounded push, not the fix: it makes the visible slice more
    likely to hold what the question is about, and can never guarantee it. The fix is
    that the truncation is resolvable at all (see `_format_codes`). Without terms the
    order is the product's own, so a question that matches nothing renders exactly as
    it did before.
    """
    if not terms:
        return raw
    def score(item):
        if not isinstance(item, dict):
            return 0
        lab = str(item.get("label", "")).lower()
        return sum(2 if lab.startswith(t) else 1 for t in terms if t in lab)
    # `sorted` is stable, so an unscored value keeps the product's declared order.
    return sorted(raw, key=lambda item: -score(item))


def _format_codes(raw: Any, limit: Optional[int] = _MAX_CODES_PER_COLUMN,
                  quote: bool = False, terms: Optional[List[str]] = None) -> str:
    """Render a column's decoded values as ``0=Female, 1=Male``.

    ``quote`` renders them as ``'0'=Female`` for a character column, because the
    agent copies the form it is shown — and the quoted form is the one that does
    not silently match blanks.

    ``limit=None`` renders the COMPLETE list. The bounded form belongs to the
    enrichment, which must fit every column into one prompt; the complete form
    belongs to a lookup, which is asked for one column at a time. A truncated list
    that cannot be completed is a dead end: a 50-value offence code rendered as its
    first 8 told a live agent nothing about `'267'=Hacking/Computer Invasion`, so it
    took the FBI code from a document in its knowledge corpus, queried `26G`, got
    zero rows, and reported that hacking offences "may not be captured in this
    dataset" — of 400,187 that are. The marker therefore names the way out rather
    than only admitting the gap.
    """
    if not isinstance(raw, list) or not raw:
        return ""
    # Members of a DECLARED range ("-5 to -9", expanded by the walker so a predicate
    # can name each code) render ONCE as the range: five copies of one label taught the
    # reader nothing and cost a line's worth of tokens per column.
    folded: List[Dict[str, Any]] = []
    seen_ranges: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        rng = item.get("range")
        if rng:
            if rng in seen_ranges:
                continue
            seen_ranges.add(rng)
            folded.append({"value": str(rng), "label": item.get("label", "")})
        else:
            folded.append(item)
    raw = folded or raw
    ordered = _rank_codes(raw, terms) if limit is not None else raw
    shown = []
    for item in (ordered if limit is None else ordered[:limit]):
        if not isinstance(item, dict):
            continue
        v, lbl = str(item.get("value", "")).strip(), str(item.get("label", "")).strip()
        if quote and v:
            v = f"'{v}'" if " " not in v else v  # a range ('-5 to -9') is not a literal
        if v or lbl:
            shown.append(f"{v}={lbl}" if lbl else v)
    if not shown:
        return ""
    if limit is None or len(raw) <= limit:
        return ", ".join(shown)
    return (", ".join(shown)
            + f" (+{len(raw) - limit} more — ask TDA_KnowledgeGraph "
              f'action="query" for this column to see them all)')


#: Chars of a column's description kept as its DISCRIMINATOR in the coded block.
#: Enough to separate "SEX OF VICTIM" from "SEX OF ARRESTEE - 2"; short enough that
#: 40 such lines stay a fraction of the enrichment budget.
_CODED_DESC_CHARS = 72

#: The heading of the relevant-columns block. Named ONCE: a test that greps a title it
#: hardcodes breaks on a rename while the property it claims to protect still holds —
#: which is exactly what happened when this block stopped being called "CODED /
#: SENSITIVE COLUMNS". Consumers import this rather than spelling it.
RELEVANT_COLUMNS_HEADING = ("COLUMNS MOST RELEVANT TO THIS QUESTION "
                            "(where a column is coded: filter on the CODE, present the label)")

#: Chars of a published recipe's SQL carried into the prompt. Recipes are short by
#: nature; the bound exists so one pathological statement cannot eat the budget.
_RECIPE_SQL_CHARS = 420
# How many declared measure/dimension names a compilable metric's line carries
# (the rest is counted, never dropped silently) — bounded so a wide metric cannot
# take the budget of a whole entity group. MEASURES are the required argument of
# compile_metric, so they are shown generously; DIMENSIONS are optional (omitting
# them takes all of them), so they are shown sparingly.
_METRIC_NAMES_SHOWN = 12
_METRIC_DIM_NAMES_SHOWN = 6
#: How many metrics of one subgraph carry their names — the seeder's OWN reserved
#: slots for the type, so "the question reached this metric" is one notion, not two.
_METRIC_NAMES_MAX_METRICS = 2
#: Score a question term contributes by matching a metric's own declared measure or
#: dimension NAME. Calibrated against _match_reasons' own tiers, not chosen freely: a
#: declared name is a member of the node's OWN vocabulary carrying the term, which is
#: exactly what "decoded value" (25) is, and a step above "formula" (15, the definition
#: merely mentions it). Deliberately well below "identifier word" (70) and "exact
#: identifier" (100), so a metric the question NAMES still outranks one whose measures
#: merely contain the word.
_METRIC_NAME_TERM_SCORE = 25


def _metrics_carrying_names(entities, terms: List[str]) -> set:
    """WHICH metrics carry their declared measure/dimension NAMES on their line.

    The names exist so the FIRST compile_metric call is likely right. They are not
    needed on every metric in the subgraph, because the refusal is recovery-shaped —
    it names every declared measure and the closest match to what was asked, so a wrong
    guess costs exactly one round and then cannot fail. Rendering them unconditionally
    cost 12-18% of the WHOLE enrichment on every turn of a metric-bearing graph, paid
    whether or not the tool was ever called (measured on the airlines conversation
    bench, where it also pushed two turns past the size bound).

    So: the metrics the question actually REACHED — the highest-scoring few, by the
    same walk the seeder ranks with, bounded by the seeder's OWN reserved slots for the
    type. A nonzero score alone is far too weak a bar: on that bench it admitted 29 of
    51 metrics, including a baggage-irregularity metric scoring 5 on a question about
    customer names. Reusing the seeder's number means "the question reached this
    metric" is one notion in both places rather than two that drift.

    No terms at all (an enrichment built without a question) ⇒ every metric carries its
    names: there is no relevance signal to gate on, and silence would be worse than the
    bytes. Any failure falls back the same way — fail open, never silently mute."""
    metrics = [e for e in (entities or [])
               if isinstance(e, dict) and e.get("entity_type") == "metric" and e.get("name")]
    if not terms:
        return {str(e["name"]) for e in metrics}
    try:
        scored = [(_metric_relevance(e, terms), str(e["name"])) for e in metrics]
        hits = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))
        return {n for _, n in hits[:_METRIC_NAMES_MAX_METRICS]}
    except Exception:  # noqa: BLE001
        return {str(e["name"]) for e in metrics}


def _metric_relevance(entity: Dict, terms: List[str]) -> int:
    """How strongly the question reached THIS metric, for the purpose of showing its
    declared names.

    The shared `_match_score` walks a node's name, description and value domain — it
    never looks inside `properties.measures`, so a metric whose measures are display
    names scores ZERO on the very word that makes it relevant: the prod question "how
    much revenue did we generate with our cars" scored 0 against
    `dealer.dealer_retail_sale`, whose measures include 'Total Fi Product Revenue'
    (session d9a60903). Gating on that signal alone would have withheld the names from
    exactly the turn they were built for.

    So the metric's OWN declared names count as evidence of its relevance — a local
    addition, deliberately NOT folded into the shared scorer, which ranks seeds for
    every type and whose blast radius is the whole enrichment."""
    base = _match_score(entity, terms)
    props = entity.get("properties") or {}
    hay = " ".join(
        str(it.get("name") if isinstance(it, dict) else it or "")
        for key in ("measures", "dimensions")
        for it in (props.get(key) or [])
    ).lower()
    if not hay:
        return base
    # A whole-word hit on a declared name, scored below an exact identifier match so a
    # metric the question NAMES still outranks one it merely describes.
    hits = sum(1 for t in terms if t and len(t) >= 3 and re.search(rf"\b{re.escape(t.lower())}", hay))
    return base + (_METRIC_NAME_TERM_SCORE * hits if hits else 0)


def _bounded_names(names, limit, terms=None):
    """`'A', 'B', …` quoted so a display name with spaces reads as ONE token, `+N more`
    past the bound.

    Names the QUESTION matched come first; the rest follow in declared order, which is
    the stable tiebreak. Declared order alone truncated the wrong end: on the prod metric
    (32 measures, session d9a60903) 'Total Fi Product Revenue' is the THIRTEENTH declared
    name, so a bound of 12 showed twelve measures none of which was the one the question
    asked for — worse than showing none, because it invites picking a neighbour."""
    names = [str(n) for n in (names or []) if str(n).strip()]
    if not names:
        return ""
    if terms:
        _t = [t.lower() for t in terms if t and len(t) >= 3]
        hit = [n for n in names if any(re.search(rf"\b{re.escape(t)}", n.lower()) for t in _t)]
        if hit:
            _seen = set(hit)
            names = hit + [n for n in names if n not in _seen]
    shown = ", ".join(f"'{n}'" for n in names[:limit])
    if len(names) > limit:
        shown += f" (+{len(names) - limit} more)"
    return shown


def _short_desc(props: Dict[str, Any]) -> str:
    """The leading, most-identifying clause of a description, bounded.

    Product-neutral by construction: it takes the FRONT of whatever the product
    declared and cuts at the first natural boundary inside the budget, because a
    description's opening names the thing while its tail elaborates. No product's
    conventions are encoded here — a standard that shipped prose in another shape
    still gets its first clause, which is the best available discriminator.
    """
    desc = " ".join(str(props.get("description") or "").split())
    if not desc:
        return ""
    head = desc[:_CODED_DESC_CHARS]
    # The LAST boundary inside the budget, not the first: "SEX OF ARRESTEE - 2 - ..."
    # cut at the first dash loses the ordinal that separates it from arrestee 1 and 3.
    for sep in (" - ", " \u2014 ", ". "):
        cut = head.rfind(sep)
        if cut > 12:                      # a real clause, not a stray dash
            return head[:cut].strip()
    if len(desc) <= _CODED_DESC_CHARS:
        return desc
    return head.rsplit(" ", 1)[0].strip() + "\u2026"


def _preferred_query_object(props: Dict[str, Any], deployed_table: str) -> str:
    """The object the PRODUCT says to consume, when it differs from the base table.

    ANDP v2 products publish access objects as DATA: ``consumable_object`` names what a
    consumer should query, ``deployed_view`` names a typed view over the base table.
    Both are standard declarations read here for ANY conformant product — nothing about
    a particular product is encoded, and a product declaring neither renders exactly as
    before.

    This must be a RENDER concern, not a prompt one. The policing CASE product declares
    ``deployed_view = v_chain_of_custody`` on its Provenance Record; the skill already
    says "query the object the product points you at" and the session primer already
    says "prefer the typed views" — and both were useless, because the enrichment named
    only the base table. A skill cannot name an object the enrichment never showed.

    A bare view name is qualified with its own table's database: products deploy their
    views beside the tables they wrap, and an unqualified name is not executable.
    """
    consumable = str(props.get("consumable_object") or "").strip()
    if consumable and consumable != deployed_table:
        return consumable
    view = str(props.get("deployed_view") or "").strip()
    if not view:
        return ""
    if "." in view or "." not in deployed_table:
        return view
    return deployed_table.rpartition(".")[0] + "." + view


def _coded_line(props: Dict[str, Any], parts: List[str]) -> str:
    """One relevant-column line: the discriminator FIRST, then the governance facts.

    The discriminator leads because that is the field the reader is choosing ON. With
    the facts alone, five columns whose decoded values all read `'0'=Female` are
    indistinguishable, and the agent picks the first one it meets.

    A column may legitimately carry NO governance facts — a plain-text column is often
    the safest one to query precisely because it needs no decode — so the description
    stands alone rather than trailing an empty dash.
    """
    desc = _short_desc(props)
    parts = list(parts)
    # Structure the walker declared on the node: which sibling qualifies this column,
    # and the repeating group it belongs to — stated where the reader chooses.
    refs = props.get("refers_to")
    if isinstance(refs, list) and refs:
        parts.append("qualified by " + ", ".join(str(r) for r in refs[:3]))
    fam = props.get("family_members")
    if isinstance(fam, list) and len(fam) > 1:
        pos = props.get("positions")
        slot = "/".join(str(p) for p in pos) if isinstance(pos, list) and pos else "?"
        parts.append(f"slot {slot} of {len(fam)}")  # the members are listed once, in REPEATING GROUPS
    body = "; ".join(parts)
    if desc and body:
        return f"{desc} \u2014 {body}"
    return desc or body


def _governance_parts(props: Dict[str, Any], complete: bool = False,
                      terms: Optional[List[str]] = None) -> List[str]:
    """The facts a column carries that change what SQL is CORRECT or PERMITTED.

    Decoded values make a coded column answerable at all; the sensitivity class tells
    the agent how the answer may be shaped. Both live on the node and were previously
    dropped on the way to the prompt.

    ``complete`` selects the UNBOUNDED rendering. The same facts, the same formatter,
    two audiences: the enrichment must fit every column into one prompt and so takes the
    bounded form; a lookup asks about one column and gets everything. Keeping it one
    function is the point — when the push and the pull each had their own idea of what a
    node carries, the pull returned strictly LESS than the push, which made the push's
    own truncation marker a dead end.
    """
    out: List[str] = []
    char_col = _is_char_type(props.get("deployed_type"))
    codes = _format_codes(props.get("allowed_values"),
                          limit=None if complete else _MAX_CODES_PER_COLUMN,
                          quote=char_col, terms=terms)
    if codes:
        out.append(f"values: {codes}")
        if props.get("decoded_as"):
            out.append(f"decoded label column: {props['decoded_as']}")
    elif props.get("decoded_labels") and props.get("decoded_from"):
        # The decoded-label half of a two-column decode: its OWN value set is the
        # domain's labels. Without it the label column read as free text, and a live
        # turn filtered `core_city = 'Chicago'` on a Yes/No label (0 rows, read as none).
        labels = [str(x) for x in props["decoded_labels"] if str(x).strip()]
        lim = None if complete else _MAX_CODES_PER_COLUMN
        shown = labels if lim is None else labels[:lim]
        more = f" … ({len(labels) - len(shown)} more)" if len(labels) > len(shown) else ""
        out.append(f"decoded label of {props['decoded_from']}; values: "
                   + ", ".join(f"'{x}'" for x in shown) + more)
    if codes and char_col:
        # The single most expensive silent failure this graph can prevent: the
        # declared type says INTEGER, the deployed column is characters, and a
        # numeric comparison quietly matches every blank row too.
        out.append(f"deployed as {props['deployed_type']} — compare as strings; "
                   f"a numeric comparison also matches blank rows")
    if _is_text_date_attr(props):
        # The second silent failure of a text-deployed column: a DATE stored as
        # characters ORDERS lexically. `ORDER BY INCDATE DESC` over '31-OCT-2023' text
        # returns every 31st first, and a `TOP 30` of it is thirty arbitrary days —
        # observed on production as a "trend" that was no such thing.
        shape = props.get("sampled_shape")
        if shape:
            # The constructor validated the column against the source: state the
            # exact CAST, no sampling round needed.
            col = str(props.get("label") or props.get("column") or "col")
            sample = f" (values look like '{props['sample_value']}')" if props.get("sample_value") else ""
            rng = props.get("date_range")
            span = (f"; data spans {'about ' if props.get('date_range_sampled') else ''}{rng[0]} to {rng[1]}"
                    if isinstance(rng, (list, tuple)) and len(rng) == 2 else "")
            bad = props.get("date_unparseable_ratio")
            dirty = isinstance(bad, (int, float)) and bad > 0
            # a sampled share of 0 is not a proof: the guarded form stays unless the share was exact
            guarded = dirty or bool(props.get("date_unparseable_sampled"))
            from trusted_data_agent.components.dateforms import date_read_form as _date_read_form
            form = _date_read_form(col, shape, bad if dirty else 0.0, bool(props.get("date_unparseable_sampled")))
            bad_s = (f"; {round(float(bad) * 100, 1)}% of its non-blank values are not dates in that shape — "
                     f"a plain CAST fails on them (Error 2666), which is why the form above reads only "
                     f"values that are dates (TRYCAST of the rebuilt ISO text: NULL for anything else)"
                     if dirty else ("; a value outside the shape (none seen in the sample, but the check "
                                    "was sampled) would fail a plain CAST, which is why the form above "
                                    "skips values of the wrong length" if guarded else ""))
            # The cheap forms first: a period bucket or a year filter is a fixed slice of the
            # text and converts no row; the CAST converts every scanned row (measured
            # 2026-09-05: 15 s vs past the 110 s ceiling over 12.35M rows) and is for real
            # date comparisons and chronological order only.
            from trusted_data_agent.components.dateforms import text_period_forms as _tpf
            forms = _tpf(col, shape)
            cheap = ""
            if forms.get("year") or forms.get("month"):
                bits = [f"year {forms['year']} (filter: {forms['year']} = '2023')"] if forms.get("year") else []
                if forms.get("month"):
                    bits.append(f"month {forms['month']}" + (f" (as a number: {forms['month_number']})" if forms.get("month_number") else ""))
                cheap = "; period buckets and year/month filters use the TEXT, no conversion: " + ", ".join(bits)
            out.append(f"a DATE stored as {props['deployed_type']} text{sample}{cheap}; a date comparison, "
                       f"a date range, MIN/MAX or a chronological ORDER BY needs {form} — bare, "
                       f"it follows STRING order{span}{bad_s}; that CAST converts every scanned row, so keep "
                       f"it out of the WHERE clause of a large table; compare that DATE against DATE '2023-01-01' "
                       f"literals, never a quoted string (the cast's FORMAT would be applied to the string: Error 2665)")
        else:
            out.append(f"a DATE stored as {props['deployed_type']} text: ORDER BY / TOP / "
                       f"GROUP BY on it follow STRING order, not date order — SELECT one "
                       f"value first, then CAST(col AS DATE FORMAT '…') with the FORMAT that "
                       f"matches its shape (31-OCT-2023 → 'DD-MMM-YYYY', 2023-10-31 → "
                       f"'YYYY-MM-DD', 10/31/2023 → 'MM/DD/YYYY'); a guessed FORMAT fails with "
                       f"Error 2666")
    br = props.get("blank_ratio")
    if isinstance(br, (int, float)) and br >= 0.05:
        out.append(f"blank in {round(float(br) * 100)}% of rows")
    if props.get("is_pii") is True:
        out.append("PII")
    cls = props.get("data_classification")
    if cls and str(cls).upper() != "PUBLIC":
        out.append(str(cls))
    return out



#: Seed priority — a tiebreak only.  Match STRENGTH decides first: a field whose
#: description merely mentions a query word is weak evidence, while one whose name or
#: description leads with it is strong, regardless of type.
#: Tie-break order among seeds of EQUAL lexical relevance. It never promotes
#: something the query did not match — only decides who wins a tie.
#:
#: ``product_bridge`` leads deliberately. A bridge is the one node type that
#: carries a WARNING rather than a fact ("declared link, not a verified join;
#: target does not resolve"), it is the answer to any cross-product question, and
#: a graph holds a handful of them against thousands of columns. Left mid-table it
#: matched the query, entered the pool, scored zero on a plural, and lost the tie
#: to arrival order — so the caveat was silently withheld on precisely the
#: questions it exists to answer. Being wrongly included costs one line; being
#: wrongly omitted lets an agent present a synthetic crosswalk as a join.
from trusted_data_agent.kgstore.types import LOGICAL_STRUCTURAL_TYPES, PHYSICAL_TYPES  # the one type vocabulary
from trusted_data_agent.kgstore.keys import with_declared_keys as _with_declared_keys  # the one declared-key reading

_SEED_PRIORITY = {
    "product_bridge": 0,
    "entity": 1, "table": 1, "metric": 2, "business_concept": 2, "domain": 3,
    "database": 3, "query_recipe": 3,
    "attribute": 4, "column": 4,
}

#: How many candidates to fetch per term before ranking.  Search returns matches in
#: arbitrary backend order, so a small fetch truncates the right answer before any
#: ranking can see it: at 25 the correct column was absent from the pool entirely.
_SEARCH_FETCH = 120
#: Upper bound on the number of TERMS searched (each term fetches up to _SEARCH_FETCH).
#: Root cause this replaced (2026-09-03): the pool used to stop growing at 600 ROWS, and
#: a compound question is searched in question order — after five productive words the
#: cap was hit and every later concept was never searched at all. "computer",
#: "location", "cyberspace", "identity" sit at the END of a digital-channel definition,
#: so the two columns the definition rests on never entered the pool, and no ranking or
#: allocation can seed what the pool does not contain. Ranking a few thousand rows costs
#: milliseconds; the row cap cost the answer. The bound is now on terms, at a level no
#: real question reaches, so every concept a question names is searched.
_SEARCH_TERM_CAP = 96

_STOP_WORDS = {
    "the", "show", "get", "list", "find", "all", "from", "with", "for", "and", "that",
    "this", "what", "how", "many", "much", "are", "was", "were", "has", "have", "been",
    "can", "will", "please", "help", "give", "tell", "about", "into", "each", "count",
    "which", "whose", "any", "whole", "needed", "using", "based", "most", "than", "does",
    # Function words of three or more letters that name no graph node. Each was a
    # substring of decoded values and descriptions ("per" in "Personal", "person",
    # "perpetrator"): on "robberies per month" `per` was a matched term of 9 of 14
    # seeds and the only reason for two of them (prod session ce559107, turn 2).
    "per", "via", "versus", "also", "just", "very", "its", "our", "your", "their",
    "these", "those", "there", "then",
}


def _query_terms(query: str) -> List[str]:
    """Meaningful lower-cased words from the query (3+ chars, no stop words)."""
    return [w for w in re.findall(r"\b[a-zA-Z_0-9]{3,}\b", (query or "").lower())
            if w not in _STOP_WORDS]


#: Words that describe what the asker wants DONE with the data rather than what the
#: data is; they never name a graph node and would only add noise to a term lookup.
_LOOKUP_NOISE = {
    "trend", "trends", "summary", "summarize", "analysis", "analyse", "analyze", "report",
    "overview", "details", "information", "data", "multiple", "several", "various", "total",
    "totals", "number", "numbers", "over", "last", "recent", "top", "per", "between",
}
_LOOKUP_MAX_TERMS = 8


def _lookup_terms(query_text: str) -> List[str]:
    """The distinct, meaningful terms of a lookup phrase, in order, bounded — the
    fallback vocabulary when the phrase itself names no node."""
    out: List[str] = []
    for w in _query_terms(query_text):
        if w in _LOOKUP_NOISE or w in out:
            continue
        out.append(w)
        if len(out) >= _LOOKUP_MAX_TERMS:
            break
    return out


def _identifier_hit(entity: Dict, phrase: str) -> bool:
    """Does the lookup phrase name the node — in its identifier or label — rather than
    merely occur in its prose? `miles_flown` / `miles flown` both address a column whose
    leaf or label contains that word sequence; a description that mentions it does not."""
    p = re.sub(r"[\s_\-]+", " ", str(phrase or "").strip().lower())
    if not p:
        return False
    props = (entity or {}).get("properties") or {}
    leaf = re.sub(r"[\s_\-]+", " ", str((entity or {}).get("name") or "").rpartition(".")[2].lower())
    label = re.sub(r"[\s_\-]+", " ", str(props.get("label") or "").lower())
    return p in leaf or (bool(label) and p in label)


def _singularize(word: str) -> Optional[str]:
    """The singular of a plural query term, or None if it already looks singular.

    Entity names are singular nouns (``Victim``, ``bridge:Investigation To
    Incident``) while people ask plural questions ("which investigations relate
    to NIBRS incidents?"). Search is a SUBSTRING match, so the gap is strictly
    one-directional: a singular term already matches a plural name (``victim``
    is inside ``Victims``), but ``investigations`` matches nothing — and a node
    that never enters the candidate pool cannot be ranked into it, which is the
    same failure that once hid ``Victim.V4019`` and, until this fix, kept every
    declared product bridge invisible to cross-product questions.

    Deliberately a handful of English suffix rules and not a stemmer: this runs
    against identifiers and labels, where an aggressive stem ("bridges" ->
    "bridg") would match less, not more.
    """
    if len(word) <= 3:
        return None
    if word.endswith("ies"):
        return word[:-3] + "y"          # agencies -> agency
    if word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]                # matches -> match
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]                # investigations -> investigation
    return None


_IDENT_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _ident_tokens(identifier: str) -> List[str]:
    """Lower-cased words of an identifier: camelCase / snake_case / spaces / digits."""
    out = []
    for part in re.split(r"[\s_\-\.:]+", identifier or ""):
        out.extend(m.lower() for m in _IDENT_SPLIT_RE.findall(part))
    return [t for t in out if t]


#: How many of the previous turn's entities are carried into the next turn's seeds, and
#: how far back a turn with a usable trail is looked for.
_CARRY_OVER_LIMIT = 8
_CARRY_OVER_LOOKBACK = 3


def _turn_number(turn: Dict, index: int) -> int:
    for k in ("turn_number", "turn"):
        v = (turn or {}).get(k)
        if isinstance(v, int) and v > 0:
            return v
        if isinstance(v, dict) and isinstance(v.get("turn_number"), int):
            return v["turn_number"]
    return index + 1


def _carry_logical(used: List[str], missed: List[str], enrs: List[Dict],
                   calls: List[Dict]) -> tuple:
    """What a turn READ, in the logical model's terms (local session 34f5d1b6, turn 3).

    The SQL names the DEPLOYED table (`Policing_Core.v_Incident`) while the enrichment
    shows the logical entity (`Policing Core.Incident`). So (1) the logical entity never
    read as used — its name is not in the SQL; (2) the deployed table read as an
    enrichment MISS (its leaf `v_incident` is not a shown leaf), and the carry resolved
    that miss to the physical twin — a second, relationship-less copy of Incident the
    theatre drew as an extra box and a statement's join could not land on; (3) a shared
    column leaf (`ori`, `incident_number`) matched EVERY table's column of that name, so
    Property, Victim, Weapon … were carried though the turn never read them.

    Rule: the tables the SQL read (`kgstore.usage.tables_read`) are mapped to the logical
    entity whose `deployed` they are (the event's own `entity_facts`); those entities are
    carried, are no longer misses, and a column is kept only when its owning table was
    read. With no facts on the event (an older shape) nothing changes. Pure.
    Returns (used, missed)."""
    facts: Dict[str, Dict] = {}
    for e in enrs:
        f = e.get("entity_facts")
        if isinstance(f, dict):
            facts.update({str(k): v for k, v in f.items() if isinstance(v, dict)})
    if not facts:
        return used, missed
    to_logical = {}
    for k, v in facts.items():
        dep = v.get("deployed")
        if dep and v.get("type") in ("entity", "table"):
            to_logical[str(dep).lower()] = k
            to_logical.setdefault(str(dep).rpartition(".")[2].lower(), k)
    try:
        from trusted_data_agent.kgstore.usage import tables_read as _tables_read
        read = list(_tables_read(calls))
    except Exception:  # noqa: BLE001
        read = []
    tables: List[str] = []
    resolved_labels = set()
    for db, leaf in read:
        label = f"{db}.{leaf}" if db else leaf
        ent = to_logical.get(label.lower()) or to_logical.get(str(leaf).lower())
        if ent:
            tables.append(ent)
            resolved_labels.add(label.lower())
    if not tables:
        return used, missed
    tset = set(tables)
    cols = [n for n in used if n not in facts and n.rpartition(".")[0] in tset]
    out = list(dict.fromkeys(tables + cols))
    still_missed = [m for m in missed if str(m).lower() not in resolved_labels]
    return out, still_missed


def previous_turn_carry_over(workflow_history: Optional[List[Dict]],
                             limit: int = _CARRY_OVER_LIMIT) -> Optional[Dict[str, Any]]:
    """Pure: the graph entities the CONVERSATION is about, read from the previous turn.

    Seeding is a function of the question's words, and a follow-up question names nothing
    a graph holds: "Give me the names of those customers" (prod session 0aa3a650, turn 2)
    seeded every `*_name` column in the model — accounts_receivable.customer_name,
    service_bulletin.oem_name — and not `ffp_member`, the table the whole of turn 1 was
    about. The agent then guessed `boarding_event.miles_flown` and joined a finance table
    for the names. The conversation already knew the answer.

    Rule: walk the previous turns newest-first (bounded). For the first turn whose
    enrichment showed the model entities, take the ones its data tool calls actually
    REFERENCED — ``kgstore.usage.touched_entities``, the ONE definition of "used" the
    seed-utilization scorer and the usage telemetry already share — tables before
    columns, deduped, capped. A turn that showed entities but referenced none (a
    refusal, an answer from memory) falls back to its top seeds, since those are what the
    conversation was steered by; a turn with no enrichment is skipped. Returns
    {"from_turn", "entities", "source"} or None. Deterministic, 0 tokens, never raises.
    """
    hist = [t for t in (workflow_history or []) if isinstance(t, dict)]
    if not hist:
        return None
    try:
        from trusted_data_agent.kgstore.usage import touched_entities as _touched
        from trusted_data_agent.agent import obda as _obda
    except Exception:  # noqa: BLE001
        return None
    n = len(hist)
    for offset in range(1, min(_CARRY_OVER_LOOKBACK, n) + 1):
        idx = n - offset
        turn = hist[idx]
        if turn.get("is_session_primer"):
            continue                                  # a briefing is not a topic
        ev = turn.get("kg_enrichment_event")
        if not isinstance(ev, dict):
            ev = {}
        _raw = ev.get("enrichments")
        enrs = [e for e in (_raw if isinstance(_raw, list) else []) if isinstance(e, dict)]
        names: List[str] = []
        seeds: List[str] = []
        for e in enrs:
            _en, _sd = e.get("entity_names"), e.get("seeds")
            names.extend(str(x) for x in (_en if isinstance(_en, list) else []) if x)
            seeds.extend(str(sd.get("name")) for sd in (_sd if isinstance(_sd, list) else [])
                         if isinstance(sd, dict) and sd.get("name"))
        if not names and not seeds:
            continue
        calls: List[Dict] = []
        try:
            calls += _obda.calls_from_conversation_events(turn.get("conversation_agent_events") or [])
            calls += _obda.calls_from_trace(turn.get("execution_trace") or turn.get("turn_action_history") or [])
        except Exception:  # noqa: BLE001
            calls = []
        used: List[str] = []
        missed: List[str] = []
        if names and calls:
            try:
                used = list(_touched(names, calls))
            except Exception:  # noqa: BLE001
                used = []
            # The tables the turn READ that the enrichment had NOT shown — the agent found
            # them on its own (a retrieval MISS, `kgstore.usage.unshown_tables`). They are
            # the strongest possible evidence of what the conversation is about, and the
            # one thing "carry what was shown" can never hand on: with semantic seeding off,
            # "customers" never reaches `ffp_member` lexically, the SQL read it anyway, and
            # the follow-up would have started the hunt over. Returned as db.leaf; the
            # caller resolves them to graph entities (a table the graph does not hold is
            # not a miss to carry).
            try:
                from trusted_data_agent.kgstore.usage import unshown_tables as _unshown, deployed_names as _deployed
                missed = list(_unshown(names, calls, _deployed(enrs)))
            except Exception:  # noqa: BLE001
                missed = []
        if used or missed:
            used, missed = _carry_logical(used, missed, enrs, calls)
        if used or missed:
            # tables first (a name with fewer segments), then the DESCRIPTIVE columns the
            # answer projected (the names the next question will filter on), then the key
            # columns — the join backbone keeps those in scope anyway, so under the cap
            # they are the ones to give up
            _uniq = list(dict.fromkeys(used))
            ordered = sorted(_uniq, key=lambda x: (x.count("."), 1 if _is_key_leaf(x) else 0, _uniq.index(x)))
            return {"from_turn": _turn_number(turn, idx), "entities": ordered[:limit],
                    "missed": missed[:limit], "source": "used" if used else "missed"}
        # a turn that read nothing: what IT was carrying (continuity is transitive), else
        # its top seeds
        prev = []
        for e in enrs:
            _co = e.get("carry_over")
            if isinstance(_co, dict) and isinstance(_co.get("entities"), list):
                prev.extend(str(x) for x in _co["entities"] if x)
        if prev:
            return {"from_turn": _turn_number(turn, idx), "entities": list(dict.fromkeys(prev))[:limit],
                    "source": "carried"}
        if seeds:
            return {"from_turn": _turn_number(turn, idx), "entities": list(dict.fromkeys(seeds))[:max(1, limit // 2)],
                    "source": "seeded"}
    return None


_KEY_LEAF_RE = re.compile(r"(?:^|_)(?:id|key|code|no|num)$", re.IGNORECASE)


def _is_key_leaf(name: str) -> bool:
    leaf = str(name or "").rpartition(".")[2]
    return bool(_KEY_LEAF_RE.search(leaf)) or leaf.lower().endswith("id")


def _layer_twin_key(entity: Dict) -> Optional[tuple]:
    """The identity a logical node and its physical twin SHARE — (owner leaf, leaf) for a
    field, (leaf,) for a table — so the two can be recognised as one fact. None for every
    other type (a metric, a concept, a product is never a twin of anything)."""
    et = (entity or {}).get("entity_type")
    name = str((entity or {}).get("name") or "")
    if et in ("attribute", "column"):
        owner, _, leaf = name.rpartition(".")
        return ("field", owner.rpartition(".")[2].lower(), leaf.lower())
    if et in ("entity", "table"):
        return ("table", name.rpartition(".")[2].lower())
    return None


def _collapse_layer_twins(results: List[Dict],
                          semantic_scores: Optional[Dict[str, float]] = None) -> Tuple[List[Dict], int]:
    """Collapse each physical/logical twin pair in a candidate pool onto its LOGICAL node,
    keeping arrival order. Returns (pool, number of physical twins dropped).

    On a layered graph the same fact is two nodes with the same text —
    `loyalty.mileage_accrual.base_miles` (attribute) and `airlines_core.mileage_accrual
    .base_miles` (column, which `implements` it) — and substring search returns BOTH. In
    the seed cut they were two candidates competing for fifteen slots: on the prod turn
    that motivated this, nine of the first twelve seeds were physical columns whose
    logical twins were also in the pool, so the cut held ~7 concepts instead of 15 and
    the physical copies expanded to nothing (only the logical layer is structurally
    expandable). The logical node is kept because it is the one the extractor walks and
    the one the deployed identifier is resolved FROM; a semantic score the physical twin
    earned moves onto it so the ranking sees the same evidence. A node with no twin in
    the pool is untouched — a physical-only or logical-only graph is unchanged.
    """
    sem = semantic_scores if isinstance(semantic_scores, dict) else None
    by_key: Dict[tuple, Dict] = {}
    logical_types = ("entity", "attribute")
    # A twin is recognised by what the logical node DEPLOYS AS, not only by a shared name:
    # a model that deploys `CaseObject` as the view `v_CaseObject` has no name in common
    # between the two layers, so the name key collapsed nothing and the physical copies took
    # seed slots (session f622f5ef turn 2: 2 of 15). A physical node whose full name is some
    # pooled logical node's `deployed_table` / `deployed_column` shares that node's key; the
    # name key remains for a graph that records no deployed identifier.
    deployed_key: Dict[str, tuple] = {}
    for e in results or []:
        if (e or {}).get("entity_type") in logical_types:
            p = (e or {}).get("properties") or {}
            dep = str(p.get("deployed_table") or p.get("deployed_column") or "").strip().lower()
            nk = _layer_twin_key(e)
            if dep and nk is not None:
                deployed_key.setdefault(dep, nk)

    def _key(e):
        if (e or {}).get("entity_type") in ("table", "column"):
            k = deployed_key.get(str((e or {}).get("name") or "").strip().lower())
            if k is not None:
                return k
        return _layer_twin_key(e)

    for e in results or []:
        k = _key(e)
        if k is None:
            continue
        cur = by_key.get(k)
        if cur is None or ((e or {}).get("entity_type") in logical_types
                           and (cur or {}).get("entity_type") not in logical_types):
            by_key[k] = e
    dropped = 0
    out: List[Dict] = []
    for e in results or []:
        k = _key(e)
        keep = by_key.get(k) if k is not None else None
        if k is not None and keep is not e and keep is not None:
            dropped += 1
            if sem is not None:
                kn, en = str(keep.get("name") or ""), str((e or {}).get("name") or "")
                if en in sem:
                    sem[kn] = max(float(sem.get(kn, 0.0)), float(sem[en]))
            continue
        out.append(e)
    return out, dropped


def _match_score(entity: Dict, terms: List[str]) -> int:
    """How strongly an entity answers the query. Higher is better. ONE computation with
    ``_match_reasons`` — the score the ranker sorts by and the reasons the theatre
    narrates are the same walk over the same signals, so they cannot disagree."""
    return _match_reasons(entity, terms)[0]


def _match_reasons(entity: Dict, terms: List[str]) -> tuple:
    """(score, reasons, matched_terms) — the scoring walk with its evidence kept.

    ``reasons`` are short human labels in signal order ("exact identifier", "decoded
    value", …); ``matched_terms`` the question terms that contributed. Used by the
    seeding stage of the KG theatre to say WHY a seed rose, and by ``_match_score``.

    Search returns matches in arbitrary backend order, so without this the right
    column is a lottery: querying "how many victims are female" returned six
    SEX-OF-VICTIM columns belonging to Arrestee and Incident and never surfaced
    Victim.V4019, because `V40191` sorted ahead of `V4019`. Exact and leading matches
    must beat an incidental mention buried in prose.
    """
    props = (entity or {}).get("properties") or {}
    name = str((entity or {}).get("name") or "")
    leaf = name.rpartition(".")[2].lower()
    label = str(props.get("label") or "").lower()
    desc = str(props.get("description") or "").lower()
    # decoded value labels are real evidence: "female" identifies a sex column
    values = " ".join(
        str(v.get("label", "")) for v in (props.get("allowed_values") or [])
        if isinstance(v, dict)).lower()

    # The owning entity is the decisive signal for a field: "how many victims are
    # female" must reach Victim.V4019, not Arrestee.V40191 — both are SEX-OF-VICTIM
    # columns with identical text, and only the owner distinguishes them.
    owner = name.rpartition(".")[0].rpartition(".")[2].lower()

    # Identifier TOKENS: "TotalRevenue" → {"total", "revenue"}; "unit_price" → {"unit",
    # "price"}. A question names a concept by ONE of its words ("revenue"), and prefix
    # matching alone scored `TotalRevenue` ZERO for it — the metric that answered the
    # question outright was dropped below the seed cut while `Products.*` columns
    # (prefix "product") filled it, and the planner never saw the join path it needed.
    tokens = (set(_ident_tokens(name.rpartition(".")[2]))
              | set(_ident_tokens(str(props.get("label") or ""))))
    formula = str(props.get("formula") or "").lower()

    score = 0
    reasons: List[str] = []
    matched: List[str] = []

    def _hit(points: int, why: str, term: str) -> None:
        nonlocal score
        score += points
        if why not in reasons:
            reasons.append(why)
        if term not in matched:
            matched.append(term)

    for t in terms:
        if owner and len(t) >= 4 and (owner.startswith(t[:-1]) or t.startswith(owner)):
            _hit(60, "owning entity", t)      # the field belongs to the asked-about thing
        if leaf == t or label == t:
            _hit(100, "exact identifier", t)  # exact identifier match
        elif leaf.startswith(t) or label.startswith(t):
            _hit(40, "identifier prefix", t)
        elif t in tokens:
            _hit(70, "identifier word", t)    # a whole word of the identifier
        elif len(t) >= 5 and (t in leaf or t in label):
            _hit(20, "contained", t)          # contained, not a whole word
        if formula and len(t) >= 4 and t in formula:
            _hit(15, "formula", t)            # the metric's definition names it
        if desc.startswith(t):
            _hit(30, "description leads", t)  # the description LEADS with it
        elif t in desc:
            _hit(5, "description mentions", t)  # merely mentioned in passing
        if t in values:
            _hit(25, "decoded value", t)      # a decoded value carries the term
    return score, reasons, matched


def _scoring_terms(query: str) -> List[str]:
    """Query terms plus their singular forms.

    The pool and the ranking must speak the SAME language. Teaching only the
    search to singularize put the declared bridges into the candidate set and
    then scored them zero — `bridges` does not prefix `bridge:Investigation To
    Incident` — so they lost the stable-sort tie to earlier zero-scoring rows and
    were invisible exactly as before. A fix applied to one half of a two-stage
    pipeline is not a fix.
    """
    terms = _query_terms(query)
    out = list(terms)
    for t in terms:
        s = _singularize(t)
        if s and s not in out:
            out.append(s)
    return out


#: Types that may never be crowded out of the seed set once the query has
#: matched them, and how many slots they may claim.
#:
#: A declared bridge scores like any other node and loses: on "can I join CASE
#: investigations to NIBRS incidents?" it scored 100 and was displaced by a dozen
#: interchangeable `Incident.BH04x` columns at 130. Lexical similarity is the
#: wrong judge here — a bridge is not one more fact competing on relevance, it is
#: the standing caveat about whether these two products may be joined at all, and
#: there are a handful of them against thousands of columns. Dropping one costs an
#: agent the warning; keeping one costs a single line.
_RESERVED_SEED_TYPES = ("product_bridge", "metric")
#: The node types a question's CONCEPT may be represented by in the seed cut — the
#: structural data model, logical and physical (kgstore.types is the one vocabulary);
#: reserved types keep their own slots, and a foreign key is an edge's twin, not a node
#: a query names.
_CONCEPT_DATA_TYPES = frozenset(
    (LOGICAL_STRUCTURAL_TYPES | PHYSICAL_TYPES) - set(_RESERVED_SEED_TYPES)
    - {"foreign_key", "ddl_artifact", "business_concept", "taxonomy"}
)
#: Seeds for a compound question: the budget grows with the number of concepts the
#: data can answer, never beyond this ceiling — a thirty-concept definition cannot be
#: represented in fifteen slots, and the context-window module trims the render to fit.
_CONCEPT_SEED_CEILING = 48
_RESERVED_SEED_SLOTS = 3
#: The seed cut. Carried-over entities CONSUME slots of it rather than extend it — a
#: follow-up whose own words are generic ("the names of those customers") would
#: otherwise add the previous turn's tables on top of a full cut of `*_name` columns
#: (measured: 289 entities / 46K chars on turn 2 of the airlines conversation); the
#: floor keeps room for a concept the follow-up genuinely introduces.
_SEED_LIMIT = 15
_SEED_LIMIT_FLOOR = 7
#: A matched metric is the ontology's own answer to "how is X computed" — it must not
#: be crowded out by a dozen interchangeable columns that merely share a prefix with
#: the question. Bounded per type so the rescue cannot itself crowd the seeds.
_RESERVED_SLOTS_BY_TYPE = {"product_bridge": 3, "metric": 2}

#: Signed data products always seeded into the enrichment, bounded. See
#: _search_entities_for_query — an absent product is a missing OPTION, not a
#: missing fact, and the governed lane cannot be chosen if it is never shown.
_MAX_SEEDED_PRODUCTS = 10


def _table_columns_map(subgraph: Dict[str, List], capabilities: Dict[str, Any] = None) -> Dict[str, List[str]]:
    """{deployed_or_logical_table: [column leaf, ...]} from entity→attribute `contains`."""
    capabilities = capabilities or {}
    tables = {e["name"] for e in subgraph.get("entities", []) if e.get("entity_type") == "entity"}
    cols = {e["name"] for e in subgraph.get("entities", []) if e.get("entity_type") == "attribute"}
    out: Dict[str, List[str]] = {}
    for rel in subgraph.get("relationships", []):
        if (rel.get("relationship_type") == "contains" and rel.get("source_name") in tables
                and rel.get("target_name") in cols):
            tbl = rel["source_name"]
            cap = capabilities.get(tbl)
            key = (cap.deployed_as if (cap and getattr(cap, "deployed_as", None)) else tbl)
            out.setdefault(key, []).append(str(rel["target_name"]).rpartition(".")[2])
    return out


def _metric_facts(subgraph: Dict[str, List]) -> List[Dict[str, Any]]:
    """[{name, formula, description, source_tables}] for every metric in the subgraph."""
    by_id = {e.get("id"): e for e in subgraph.get("entities", [])}
    facts = []
    for e in subgraph.get("entities", []):
        if e.get("entity_type") != "metric":
            continue
        props = e.get("properties") or {}
        sources = set()
        for rel in subgraph.get("relationships", []):
            if rel.get("source_name") != e.get("name"):
                continue
            tgt = by_id.get(rel.get("target_id")) or {}
            if tgt.get("entity_type") == "entity":
                sources.add(str(tgt.get("name")))
            elif tgt.get("entity_type") == "attribute":
                owner = str(rel.get("target_name") or "").rpartition(".")[0]
                if owner:
                    sources.add(owner)
        facts.append({"name": str(e.get("name")), "formula": str(props.get("formula") or ""),
                      "description": str(props.get("description") or ""),
                      "source_tables": sorted(sources)})
    return facts


#: Max ranking bonus a fully-hot usage prior can add. Kept below a leading-prefix
#: match (+40) so telemetry only breaks near-ties and nudges — it can never displace
#: a real lexical match, and an empty usage table adds exactly 0 (byte-identical).
_USAGE_PRIOR_MAX = 8
#: Max ranking bonus a perfect semantic (cosine=1) match adds — comparable to a
#: leading-prefix lexical match (+40), so a strong synonym competes with a lexical
#: hit (the point) but a mediocre one still sits below an exact identifier match
#: (+100). Empty semantic_scores adds exactly 0 (byte-identical when disabled).
_SEMANTIC_SCORE_MAX = 45
# The reserved meaning slot (see _rank_seeds): the cosine a meaning-only row needs to claim
# it, and the lexical score below which the weakest row in a full cut yields to it (an
# "identifier word" match is 70 — that tier and above never yields).
_SEMANTIC_RESERVE_MIN = 0.40
_SEMANTIC_RESERVE_YIELD_BELOW = 70


def _concept_evidence(entity: Dict, term: str) -> int:
    """Does this node ANSWER the concept named by ``term`` — as opposed to merely
    containing the letters? Every strong channel of ``_match_score`` counts (owner,
    exact identifier, identifier prefix or whole token, formula, description lead,
    decoded value label); the two weak ones never do (a substring inside an
    identifier, a passing mention in prose) — those are how "contain"/"table"/"total"
    pulled in noise. Kept as ONE function beside the scorer so the two cannot drift."""
    props = (entity or {}).get("properties") or {}
    name = str((entity or {}).get("name") or "")
    leaf = name.rpartition(".")[2].lower()
    label = str(props.get("label") or "").lower()
    desc = str(props.get("description") or "").lower()
    values = " ".join(str(v.get("label", "")) for v in (props.get("allowed_values") or [])
                      if isinstance(v, dict)).lower()
    owner = name.rpartition(".")[0].rpartition(".")[2].lower()
    tokens = (set(_ident_tokens(name.rpartition(".")[2]))
              | set(_ident_tokens(str(props.get("label") or ""))))
    formula = str(props.get("formula") or "").lower()
    t = term.lower()
    score = 0
    # No owner channel here, deliberately: a column BELONGS to the entity a concept
    # names, it does not ANSWER the concept — the entity row does, and it is the
    # entity that should represent "baggage" or "flights" in the cut, not one of its
    # columns. (The ranking scorer keeps the owner channel for ordering.)
    if leaf == t or label == t:
        score += 100
    elif len(t) >= 5 and (leaf.startswith(t) or label.startswith(t)):
        score += 40  # a three-letter prefix is noise ("age" is a prefix of "agent")
    elif t in tokens:
        score += 70
    if formula and len(t) >= 4 and t in formula:
        score += 15
    # The TITLE of a description is its leading clause — up to the first " - ",
    # sentence end or colon, at most twelve words ("OFFENDER(S) SUSPECTED OF USING 1 - 1
    # - V20081 represents…" titles itself before the first dash). A title names its
    # concept anywhere within it; the prose after it merely mentions.
    title = " ".join(re.split(r"\s+-\s+|\.\s|:\s|\n", desc, maxsplit=1)[0].split()[:12])
    if title and re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", title):
        score += 30
    if t in values:
        score += 25
    return score


def _declared_products(store: Any) -> List[str]:
    """The products a graph DECLARES (an ANDP descriptor: a flat ``product`` or a
    ``products`` list). Names are the only place a node's product lives, and a name's
    first segment means "product" on an ANDP graph but "subject area" on an
    industry-model graph — so confinement keys on the declaration, and a graph that
    declares none confines nothing. Fails open to []."""
    kid = getattr(store, "kg_id", None)
    now = time.monotonic()
    hit = _DECLARED_PRODUCTS_CACHE.get(kid) if kid else None
    if hit and now - hit[0] < _DECLARED_PRODUCTS_TTL:
        return list(hit[1])
    try:
        d = store.get_source_descriptor() if hasattr(store, "get_source_descriptor") else None
    except Exception:
        d = None
    out: List[str] = []
    if isinstance(d, dict):
        entries = d.get("products") if isinstance(d.get("products"), list) else [d]
        out = sorted({str(e.get("product") or "").strip().lower() for e in entries
                      if isinstance(e, dict) and str(e.get("product") or "").strip()})
    if kid:
        _DECLARED_PRODUCTS_CACHE[kid] = (now, tuple(out))
    return out


#: Declared products per graph, cached — the descriptor changes only on a resync, and
#: reading it on every question would add a SQLite connection per turn.
_DECLARED_PRODUCTS_CACHE: Dict[str, tuple] = {}
_DECLARED_PRODUCTS_TTL = 60.0


_FAMILY_CAP = 9  # positions × offences in a NIBRS-style family; a bound, not a target


def _title_stem(entity: Dict) -> str:
    """The family a column belongs to: the walker's DECLARED `family` stem where the
    graph was built with it, else the description's leading clause with its trailing
    position numbers removed — "LOCATION TYPE - 2" and "- 3" share "location type"."""
    props = (entity or {}).get("properties") or {}
    declared = props.get("family")
    if declared:
        return str(declared).lower()
    desc = str(props.get("description") or "").lower()
    title = re.split(r"\s+-\s+|\.\s|:\s|\n", desc, maxsplit=1)[0]
    title = re.sub(r"(\s*-?\s*\d+)+\s*$", "", title).strip()
    title = " ".join(title.split()[:12])
    return title if len(title) >= 6 else ""


#: A positional family is expanded only when the question asks for positions — these
#: are stop words for scoring, and the signal here. A question naming "the location
#: type" needs one representative; "any of the three location positions" needs them all.
_FAMILY_TRIGGERS = frozenset({"any", "all", "each", "every", "three", "positions", "position", "multiple"})


def _allocate_by_concept(scored: List[tuple], terms: List[str], limit: int,
                         products: Optional[List[str]] = None,
                         expand_families: bool = False) -> List[tuple]:
    """The seed cut, allocated per CONCEPT rather than taken off one global ranking.

    Root cause this replaces (2026-09-03, both engine lanes on production): a compound
    question — "digital channel = suspected of using computer equipment OR a cyberspace
    location OR a cyber-intrinsic offence" — names several concepts, and a single
    global cut of 15 filled with the concepts that happened to score highest, so the
    location column never reached the prompt and every agent silently dropped that
    clause (541,571 became 487,705, with no error to catch it). A rescue of the losers
    was tried first and measured; it was a patch on the same defect.

    Rule: walk the question's concepts in the order they were asked, round-robin, and
    give each the STRONGEST-RANKED row not yet chosen that answers it on strong
    evidence (`_concept_evidence`) — global rank decides WHICH row represents a
    concept, evidence only decides WHETHER a row may; a row answering several
    concepts therefore keeps its place — then fill what remains from the global
    order. A word and its singular are ONE concept. A product-qualified row is only
    reached for inside the product the question is about (the products of the
    globally best rows); a row with no product qualifier is never confined. Every
    concept the question names is represented before any concept is represented
    twice; the budget is unchanged; a question naming one concept is allocated
    exactly as before (its global order). Deterministic, 0-token.
    """
    if not scored or limit <= 0:
        return [], set(), {}
    if os.environ.get("KG_SEED_ALLOCATION", "on").lower() in ("off", "0", "false"):
        return scored[:limit], set(), {}  # operator fail-back: the plain global cut
    words = [t for t in terms if len(t) >= 3]  # "age", "atm" are concepts; evidence decides admissibility, not length
    concepts: List[List[str]] = []
    seen = set()
    for t in words:
        if t in seen:
            continue
        forms = [t]
        sg = _singularize(t)
        if sg and sg in words:
            forms.append(sg)
        seen.update(forms)
        concepts.append(forms)
    if len(concepts) < 2:
        return scored[:limit], set(), {}

    # A concept slot is for a DATA node — an entity, attribute, column, table, metric:
    # what a query can name. Documentation nodes (decisions, recipes, glossary terms,
    # quality readings, naming standards) never REPRESENT a question's concept; they
    # reach the cut through the global fill and their own reserved slots, as before.
    # Without this, "extract", "file", "three" each took a slot for a design decision
    # whose title happened to contain the word, ahead of the columns the question
    # actually turns on.
    def _is_data(e):
        return (e or {}).get("entity_type") in _CONCEPT_DATA_TYPES
    def _segments(e):
        return str((e or {}).get("name") or "").split(".")
    declared = [p for p in (products or []) if p]
    def _product(e):
        # a module of a product ("NIBRS_Source_Semantic") folds into its product
        head = _segments(e)[0].lower()
        for p in declared:
            if head == p or head.startswith(p + "_"):
                return p
        return None
    def _ev(e, forms):
        return max(_concept_evidence(e, f) for f in forms)

    # The ENTITIES the question names (an exact identifier match on an entity/table
    # row): a concept is represented WITHIN them when a row there answers it, and
    # elsewhere only otherwise — "age" is the victim's age on the Incident row the
    # question is about, not a catalogue attribute whose title contains the word.
    named = {str(r[3].get("name") or "").rpartition(".")[2].lower()
             for r in scored if _is_data(r[3]) and r[3].get("entity_type") in ("entity", "table")
             and any(_ev(r[3], f) >= 100 for f in concepts)}
    # The product the question is ABOUT: the dominant product among the best data rows
    # (a union of the top three let one bridge or decision row admit a second product's
    # whole catalogue). Products tied for the lead are all admitted.
    _lead = [_product(r[3]) for r in [r for r in scored if _is_data(r[3])][:10]]
    _cnt: Dict[str, int] = {}
    for _pr in _lead:
        if _pr is not None:
            _cnt[_pr] = _cnt.get(_pr, 0) + 1
    anchor = {k for k, v in _cnt.items() if v == max(_cnt.values())} if _cnt else set()
    # When the question NAMES entities, the product it names MOST is the one it is
    # about — whatever catalogue rows of another product happen to score, and even
    # when one question word coincides with an entity name elsewhere ("relationship"
    # is a CASE entity; a question naming Incident, Victim and Offender is about NIBRS).
    _np: Dict[str, set] = {}
    for r in scored:
        e = r[3]
        if _is_data(e) and e.get("entity_type") in ("entity", "table"):
            leaf = str(e.get("name") or "").rpartition(".")[2].lower()
            pr = _product(e)
            if leaf in named and pr is not None:
                _np.setdefault(pr, set()).add(leaf)
    if _np:
        top_n = max(len(v) for v in _np.values())
        anchor = {k for k, v in _np.items() if len(v) == top_n}

    def _tier(e, forms):
        # 0: a column of an entity the question NAMES (the victim's age lives on the
        #    Incident row the question is about); 1: an entity on its IDENTIFIER (never
        #    on a title mention); 2: everything else.
        segs = _segments(e)
        if e.get("entity_type") not in ("entity", "table") and len(segs) >= 2 and segs[-2].lower() in named:
            return 0
        if e.get("entity_type") in ("entity", "table") and _ev(e, forms) >= 70:
            return 1
        return 2

    def _candidates(forms):
        rows = []
        for row in scored:
            e = row[3]
            if not _is_data(e):
                continue
            prod = _product(e)
            if anchor and prod is not None and prod not in anchor:
                continue
            ev = _ev(e, forms)
            if ev > 0:
                rows.append((ev, row))
        if not rows:
            return None, 0
        lowest = min(_tier(x[1][3], forms) for x in rows)
        tier = [x for x in rows if _tier(x[1][3], forms) == lowest]
        best = max(x[0] for x in tier)
        pick = next(x[1] for x in tier if x[0] == best)  # scored is best-first: first at the max IS the strongest-ranked
        return pick, best

    resolved = [(ci, *_candidates(concepts[ci])) for ci in range(len(concepts))]
    answered = [ci for ci, pick, _ in resolved if pick is not None]
    limit = max(limit, min(_CONCEPT_SEED_CEILING, len(answered)))
    chosen: List[tuple] = []
    chosen_ids = set()
    families: List[tuple] = []   # positional siblings, placed BEHIND every representative
    family_ids = set()
    for ci, pick, best_ev in resolved:  # question order
        if pick is None or len(chosen) >= limit:
            continue
        forms = concepts[ci]
        # Satisfied by a row already chosen ONLY if it answers the concept as strongly
        # as the best available — a value label ("Weapon Law Violations" on the offence
        # code) must not stand in for the column titled WEAPON.
        if any(_ev(row[3], forms) >= best_ev for row in chosen):
            continue
        if id(pick[3]) in chosen_ids:
            continue
        chosen.append(pick); chosen_ids.add(id(pick[3]))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("KG seeding: concept %r -> %s (evidence %d)", forms[0], pick[3].get("name"), best_ev)
        # A POSITIONAL FAMILY travels together. A denormalised extract repeats a column
        # per position ("LOCATION TYPE - 1/2/3", "OFFENDER(S) SUSPECTED OF USING 1 - 1..3");
        # one representative satisfies the concept, but a question asking "any of the
        # three" needs every sibling, and an agent shown only "- 1" never learns the
        # others exist (measured: 140,609 for 145,193). Siblings = same table, same title
        # once its trailing position numbers are stripped. Bounded.
        stem = _title_stem(pick[3]) if expand_families else ""
        if stem:
            owner = _segments(pick[3])[:-1]
            added = 0
            for row in scored:
                if added >= _FAMILY_CAP:
                    break
                e = row[3]
                if id(e) in chosen_ids or id(e) in family_ids or not _is_data(e) or _segments(e)[:-1] != owner:
                    continue
                if _title_stem(e) == stem:
                    families.append(row); family_ids.add(id(e)); added += 1
    # Order: every representative (score-sorted) → its families → the global fill. A
    # family sits behind the representatives so a render budget that caps nodes keeps
    # the concepts before the siblings; the budget grows for the families, bounded.
    chosen.sort(key=lambda x: (x[0], x[1], x[2]))
    limit = max(limit, min(_CONCEPT_SEED_CEILING, len(chosen) + len(families)))
    for row in families:
        if len(chosen) >= limit:
            break
        chosen.append(row); chosen_ids.add(id(row[3]))
    protected = set(chosen_ids)  # the concept picks and their families — never evicted by a reserved rescue
    group = {id(r[3]): (1 if id(r[3]) in family_ids else 0) for r in chosen}
    for row in scored:
        if len(chosen) >= limit:
            break
        if id(row[3]) not in chosen_ids:
            chosen.append(row); chosen_ids.add(id(row[3])); group[id(row[3])] = 0  # fill sorts with the representatives by score; only families sit behind
    return chosen[:limit], protected, group


def _drop_weak_mentions(scored: List[tuple], terms: List[str]) -> List[tuple]:
    """Drop rows whose ONLY evidence is a passing description mention of a word some
    other row answers more strongly.

    "crime" is the exact name of the crime domain (100); every `ori` column whose
    description says "the agency reporting the crime" also mentions it (5), and those
    filled four seed slots on "robberies per month" (prod session ce559107, turn 2).
    A mention is kept when it is the best evidence the graph has for a word — "month"
    reaches `incident_date_text` only through its description, and that IS the column
    the question turns on. Rows carrying any stronger signal are never touched.
    """
    if not scored or not terms:
        return scored
    weak, best = {}, {}
    for row in scored:
        e = row[3]
        score, reasons, matched = _match_reasons(e, terms)
        if reasons == ["description mentions"]:
            weak[id(e)] = matched
        for t in matched:
            pts = _match_score(e, [t])
            if pts > best.get(t, 0):
                best[t] = pts
    if not weak:
        return scored
    _MENTION = 5
    kept = [row for row in scored
            if id(row[3]) not in weak or any(best.get(t, 0) <= _MENTION for t in weak[id(row[3])])]
    return kept or scored


def _rank_seeds(results: List[Dict], query: str = "", limit: int = 15,
                usage_priors: Optional[Dict[str, float]] = None,
                semantic_scores: Optional[Dict[str, float]] = None,
                products: Optional[List[str]] = None) -> List[Dict]:
    """Order seeds by match strength, then by type, then stably by arrival.

    ``usage_priors`` (name → [0,1]) adds a small learned bonus so entities agents
    consistently use rank slightly higher; None/empty leaves ranking unchanged.
    ``semantic_scores`` (name → cosine [0,1]) adds a moderate bonus so a synonym the
    lexical seeder could not match still ranks — this is what makes semantic seeding
    (opt-in) competitive with a lexical match; empty leaves ranking unchanged.
    """
    terms = _scoring_terms(query)
    up = usage_priors or {}
    sem = semantic_scores or {}

    def _score(e):
        nm = str((e or {}).get("name") or "")
        base = _match_score(e, terms)
        bonus = int(round(up.get(nm, 0.0) * _USAGE_PRIOR_MAX))
        bonus += int(round(sem.get(nm, 0.0) * _SEMANTIC_SCORE_MAX))
        return base + bonus

    scored = [
        (-_score(e), _SEED_PRIORITY.get((e or {}).get("entity_type"), 5), i, e)
        for i, e in enumerate(results or [])
    ]
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    # A row the question did not reach is not a seed. The search pool also holds rows
    # the full-question substring search returned on a word the scorer does not count;
    # with fewer strong rows than slots the global fill took them at score 0 — on
    # "robberies per month" a correspondence entered the cut with no matched term and
    # pulled the whole evidence side into a crime-count turn. Kept only when nothing
    # scored at all, so a question the scorer cannot read still gets its pool.
    # Reached = the WORDS or the MEANING of the question reach the row. A usage prior is
    # not evidence: it orders rows the question reached, it never admits one — with
    # priors on, every entity agents used before scored a few points and filled the cut
    # on no evidence at all (local session ff0d5a6e: ten crime entities and the
    # correspondence, score 0 on the question, admitted by their prior).
    def _reached(e):
        return _match_score(e, terms) > 0 or sem.get(str((e or {}).get("name") or ""), 0.0) > 0
    if any(_reached(row[3]) for row in scored):
        scored = [row for row in scored if _reached(row[3])]
    scored = _drop_weak_mentions(scored, terms)
    _raw_words = set(re.findall(r"[a-z]+", (query or "").lower()))
    top, protected, group = _allocate_by_concept(scored, terms, limit, products,
                                                 expand_families=bool(_raw_words & _FAMILY_TRIGGERS))

    # Reserved types the query DID match (score > 0) but which the cut dropped.
    # Never force-include an unmatched one: on a single-product question no bridge
    # scores, so nothing is added and the output is unchanged.
    chosen = {id(e) for _, _, _, e in top}
    # Slots already held by reserved types INSIDE the cut count against the cap —
    # and a rescue may only displace the weakest NON-reserved row. (The first
    # version trimmed the tail blindly: it evicted a legitimately-ranked
    # TotalRevenue (70) to rescue a weaker StockLevel (30) — the exact node the
    # rescue exists to protect, thrown out by the rescue.)
    used: Dict[str, int] = {}
    for _, _, _, e in top:
        et = (e or {}).get("entity_type")
        if et in _RESERVED_SEED_TYPES:
            used[et] = used.get(et, 0) + 1
    # Every ranked row outside the cut — the cut is allocated per concept, so a
    # reserved row can sit inside the global top-N and still be outside it.
    for row in scored:
        et = (row[3] or {}).get("entity_type")
        if row[0] < 0 and et in _RESERVED_SEED_TYPES and id(row[3]) not in chosen:
            cap = _RESERVED_SLOTS_BY_TYPE.get(et, _RESERVED_SEED_SLOTS)
            if used.get(et, 0) >= cap:
                continue
            # weakest non-reserved row in the cut (scored is sorted best-first)
            victim = next((i for i in range(len(top) - 1, -1, -1)
                           if (top[i][3] or {}).get("entity_type") not in _RESERVED_SEED_TYPES
                           and id(top[i][3]) not in protected), None)
            if victim is None:
                break
            top[victim] = row
            used[et] = used.get(et, 0) + 1
    # ONE slot for the best row reached by MEANING alone. The semantic bonus is capped below
    # any lexical tier on purpose, so in a cut the question's words fill, a meaning-only row
    # can never win a place and the channel contributes nothing exactly when it found
    # something the words could not ("hacking evidence" → the entity whose description is
    # "computer equipment is the digital-involvement signal", cosine 0.44, dropped behind
    # fifteen lexical rows — local session f622f5ef turn 2). The best such row takes a free
    # slot, or the place of the weakest unprotected row when that row's own evidence is weak
    # (below an identifier-word match); a strong lexical row never yields. Channel off ⇒
    # `sem` is empty ⇒ nothing here runs.
    if sem:
        in_cut = {id(r[3]) for r in top}
        cands = [r for r in scored if id(r[3]) not in in_cut
                 and (r[3] or {}).get("entity_type") in _CONCEPT_DATA_TYPES
                 and _match_score(r[3], terms) == 0
                 and sem.get(str((r[3] or {}).get("name") or ""), 0.0) >= _SEMANTIC_RESERVE_MIN]
        has_meaning_row = any(_match_score(r[3], terms) == 0
                              and sem.get(str((r[3] or {}).get("name") or ""), 0.0) > 0 for r in top)
        if cands and not has_meaning_row:
            best = max(cands, key=lambda r: (sem.get(str(r[3].get("name") or ""), 0.0), -r[2]))
            if len(top) < limit:
                top.append(best)
            else:
                victim = next((i for i in range(len(top) - 1, -1, -1)
                               if (top[i][3] or {}).get("entity_type") not in _RESERVED_SEED_TYPES
                               and id(top[i][3]) not in protected
                               and _match_score(top[i][3], terms) < _SEMANTIC_RESERVE_YIELD_BELOW), None)
                if victim is not None:
                    top[victim] = best
    # Representatives, rescued reserved rows and the fill by score; a positional FAMILY
    # sits behind them all. The render caps nodes on a small budget from the tail, so
    # the order IS the priority, and a plain score sort would put a family ahead of
    # another concept's representative.
    top.sort(key=lambda x: (group.get(id(x[3]), 0), x[0], x[1], x[2]))
    return [e for _, _, _, e in top]


def _obda_enabled() -> bool:
    """OBDA-READ IS ADDITIVE (charter #15): the plane off ⇒ this platform behaves as if
    OBDA-READ never shipped. Availability is the OBDA-READ COMPONENT's (global · per user ·
    per agent — `core/obda_planes`); the per-agent answer is `_profile_surface`'s (which
    reads `obda.profile_obda`, which asks the component chain). This deployment-level
    reader remains for the faces that have no agent in play."""
    try:
        from trusted_data_agent.core import obda_planes as _planes
        return _planes.plane_available("read")
    except Exception:  # noqa: BLE001
        return False




_INTENSITY_COLUMN_CAP = {"low": 6, "medium": 12, "high": 24, "full": None}
_DEFAULT_INTENSITY = "medium"


def _intensity_column_cap(context) -> Optional[int]:
    """Per-table column cap from the profile's `componentConfig.knowledge_graph.intensity`
    (was read NOWHERE before Aug 2026 — the dial did nothing). Unresolvable → medium."""
    try:
        profile = (context or {}).get("profile")
        if not isinstance(profile, dict):
            pid = (context or {}).get("profile_id")
            uid = (context or {}).get("user_uuid")
            if pid and uid:
                from trusted_data_agent.core.config_manager import ConfigManager
                profile = ConfigManager().get_profile(pid, uid)
        cfg = ((profile or {}).get("componentConfig") or {}).get("knowledge_graph") or {}
        level = str(cfg.get("intensity") or _DEFAULT_INTENSITY).lower()
        return _INTENSITY_COLUMN_CAP.get(level, _INTENSITY_COLUMN_CAP[_DEFAULT_INTENSITY])
    except Exception:  # noqa: BLE001
        return _INTENSITY_COLUMN_CAP[_DEFAULT_INTENSITY]


_MODEL_AUTHORED_SOURCES = {"llm_inferred": "proposed by the agent mid-turn, not validated",
                           "constructor_semantic": "model-derived at build time, not validated",
                           "kg_constructor_semantic": "model-derived at build time, not validated"}
try:
    from trusted_data_agent.kgstore.types import PHYSICAL_TYPES as _PHYSICAL_TYPES_FOR_LAYER
except Exception:  # noqa: BLE001
    _PHYSICAL_TYPES_FOR_LAYER = frozenset({"catalog", "schema", "table", "column", "ddl_artifact"})


def _formula_provenance(entity: Dict) -> str:
    """Provenance note for a metric formula: '' for a deterministic/validated definition,
    a plain statement of its origin for a model-authored one."""
    props = (entity or {}).get("properties") or {}
    if props.get("validated") or props.get("validated_by"):
        return ""
    return _MODEL_AUTHORED_SOURCES.get(str((entity or {}).get("source") or ""), "")


def _usage_priors(kg_id: str) -> Dict[str, float]:
    try:
        from trusted_data_agent.kgstore import usage as _usage
        return _usage.priors(kg_id)
    except Exception:  # noqa: BLE001 — telemetry must never break retrieval
        return {}


def _record_usage_async(kg_id: str, seeded_names, rendered_names) -> None:
    """Increment seeded/rendered off the request path (a daemon thread), so the two
    small writes never sit under the turn's latency or the process-wide SQLite lock."""
    import threading

    def _run():
        try:
            from trusted_data_agent.kgstore import usage as _usage
            _usage.record(kg_id, [n for n in seeded_names if n], "seeded")
            _usage.record(kg_id, [n for n in rendered_names if n], "rendered")
        except Exception:  # noqa: BLE001
            pass
    try:
        threading.Thread(target=_run, name="kg-usage", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def _semantic_contribution(report: Dict[str, Any], rendered_names: set) -> Dict[str, Any]:
    """What the semantic channel actually CONTRIBUTED to this turn's prompt.

    A hit is a contribution only if it was BOTH reached by meaning and still present in
    the rendered subgraph: one that ranking or the node budget dropped never reached the
    planner, and crediting it would let the event overstate the channel. The dropped ones
    are counted rather than hidden — "found it and could not fit it" is a real finding.

    Pure, so the rule is executable on its own: a structural check of the call site
    passed a mutation that deleted the filter and left the surrounding tokens intact.
    """
    bridged = report.get("bridged") or []
    kept = [b for b in bridged if b.get("name") in rendered_names]
    return {
        "state": report.get("state", "off"),
        "hits": int(report.get("hits", 0)),
        "bridged": kept,
        "bridged_dropped": len(bridged) - len(kept),
    }


def _kg_signature(kg_id: str):
    """The graph's content version — delegates to the ONE definition beside the index
    it keys (`kgstore.semantic_index.graph_signature`). A second copy here would key
    reads differently from writes: every build a miss, every lookup a rebuild."""
    from trusted_data_agent.kgstore.semantic_index import graph_signature
    return graph_signature(kg_id)


def _profile_of(profile_id, user_uuid):
    """The agent's profile for an availability decision (None when it cannot be read)."""
    try:
        if profile_id and user_uuid:
            from trusted_data_agent.core.config_manager import ConfigManager
            return ConfigManager().get_profile(profile_id, user_uuid)
    except Exception:  # noqa: BLE001
        return None
    return None


def _profile_surface(context) -> tuple:
    """The PROFILE's OBDA-READ mode + product grant — the config-level truth
    (obda.profile_obda: Option A default · B exclusive · C mixed), resolved
    from the component context. Distinct from ``_surface_for_turn`` (the
    ambient PRINCIPAL, which exists only on autonomous / enforce-interactive
    turns): planning VISIBILITY follows the profile's configuration on every
    turn; enforcement stays with the principal. Tests may inject
    ``context["profile"]`` directly. Never raises; unresolvable → Option A
    (fail toward invisible — the additive baseline)."""
    try:
        profile = (context or {}).get("profile")
        if not isinstance(profile, dict):
            pid = (context or {}).get("profile_id")
            uid = (context or {}).get("user_uuid")
            if pid and uid:
                from trusted_data_agent.core.config_manager import ConfigManager
                profile = ConfigManager().get_profile(pid, uid)
        from trusted_data_agent.agent.obda import profile_obda
        return profile_obda(profile)
    except Exception:  # noqa: BLE001
        return "off", frozenset()


def _surface_for_turn():
    """The ambient agent principal's data surface, or (None, False) when unconfined.

    Read from the SAME contextvar the tool chokepoints enforce with, so what the
    contract SHOWS and what the runtime will ALLOW cannot disagree. Fails soft:
    an unreadable authority layer must never cost the turn its enrichment.
    """
    try:
        from trusted_data_agent.core import agent_authority as _aa
        pr = _aa.get_current_principal()
        sc = getattr(pr, "scope", None)
        products = getattr(sc, "data_surface_products", None)
        if products is None:
            return None, False
        exclusive = bool(getattr(sc, "data_surface_exclusive", False)
                         and not getattr(sc, "data_surface_advisory", False))
        return frozenset(str(x) for x in products), exclusive
    except Exception:  # noqa: BLE001
        return None, False


def _unpublished_authored(e, hidden=None) -> bool:
    """PROP-008 §2b: an extension competes for the planner's context ONLY once it is
    PUBLISHED — a draft, a validated or a deployed-but-unpublished extension never renders.
    ONE predicate, read by the seeder (a draft is never a seed) and at subgraph assembly
    (a draft pulled in by an edge leaves with its edges). ``hidden`` is the graph's set of
    unpublished-extension names (``GraphStore.unpublished_names`` — the authored nodes AND the
    physical nodes landing them wrote); without it, the authored rule alone applies."""
    try:
        pr = e.get("properties") or {}
    except AttributeError:
        return False
    if hidden and str(e.get("name")) in hidden:
        return True
    return pr.get("layer") == "authored" and (pr.get("status") or "draft") != "published"


async def _unpublished_names_of(store) -> set:
    """The store's unpublished-extension names, or an empty set when it cannot say (a test
    double, an older store) — the authored rule then still applies on its own."""
    fn = getattr(store, "unpublished_names", None)
    if not fn:
        return set()
    try:
        return await _maybe_await(fn()) or set()
    except Exception:  # noqa: BLE001
        return set()


def _cut_action_types(subgraph: Dict[str, Any]) -> Dict[str, Any]:
    """PROP-008 §2b: an action type never reaches an agent's context by default — its
    nodes AND every ``acts_on`` edge (and any edge naming an action node) leave the
    subgraph. B4 admits the types an agent's grant names; until then this cut is
    unconditional. Pure; returns a new dict, the input is not mutated."""
    ents = list(subgraph.get("entities") or [])
    rels = list(subgraph.get("relationships") or [])
    ids = {e.get("id") for e in ents if (e.get("entity_type") or "") == "action_type"}
    names = {str(e.get("name")) for e in ents if (e.get("entity_type") or "") == "action_type"}
    if not ids and not names and not any(r.get("relationship_type") == "acts_on" for r in rels):
        return subgraph
    out = dict(subgraph)
    out["entities"] = [e for e in ents if (e.get("entity_type") or "") != "action_type"]
    out["relationships"] = [
        r for r in rels
        if r.get("relationship_type") != "acts_on"
        and not ({r.get("source_id"), r.get("target_id")} & ids
                 or {str(r.get("source_name")), str(r.get("target_name"))} & names)]
    return out


def _cut_unpublished_authored(subgraph: Dict[str, Any], hidden=None) -> Dict[str, Any]:
    """Remove every node of an unpublished extension AND the relationships that name them from
    an assembled subgraph (the data-product cut's shape — one seam, after extraction)."""
    ents = subgraph.get("entities") or []
    cut_ids = {e.get("id") for e in ents if _unpublished_authored(e, hidden)}
    cut_names = {str(e.get("name")) for e in ents if _unpublished_authored(e, hidden)}
    if not cut_ids and not cut_names:
        return subgraph
    subgraph["entities"] = [e for e in ents if not _unpublished_authored(e, hidden)]
    subgraph["relationships"] = [
        r for r in subgraph.get("relationships", [])
        if not ({r.get("source_id"), r.get("target_id")} & cut_ids
                or {str(r.get("source_name")), str(r.get("target_name"))} & cut_names)]
    return subgraph


def _is_product(e) -> bool:
    """Is this node a data product? The ONE definition — every consumer asks here.

    Two callers ask for different reasons: `_all_products` decides what may be RENDERED,
    and the theatre's seed stage labels WHY a seed was picked. Those are different
    questions over the same fact, and a second inline comparison is a second definition
    that drifts the first time the type is renamed."""
    return isinstance(e, dict) and e.get("entity_type") == "data_product"


def _all_products(entities) -> list:
    """Every data-product node, bounded. The ONE place the type filter lives."""
    try:
        return [e for e in (entities or []) if _is_product(e)][:_MAX_SEEDED_PRODUCTS]
    except Exception:  # noqa: BLE001
        return []


def _off_surface_product_names(entities, surface=None) -> list:
    """Products present on the graph but NOT consumable by this agent's surface.

    Named rather than silently dropped: a product that simply vanishes from the
    contract is indistinguishable from one that does not exist, and the agent
    would go looking for it by other means. One honest line closes that door too.
    """
    if surface is None:
        surface = _surface_for_turn()           # legacy principal fallback
    products_set, exclusive = surface
    # The list is the grant in BOTH Option B (exclusive) and Option C (mixed —
    # operator decision #3), so non-listed products are named off-surface in
    # both; only a turn with NO configured list names nothing.
    if products_set is None:
        return []
    return [str(e.get("name")) for e in _all_products(entities)
            if str(e.get("name")) not in products_set]


def _rendered_products(entities, surface=None) -> list:
    """The data products that will be RENDERED as consumption contracts, in order.

    ONE rule, read by the prompt renderer AND by the enrichment event, so the two can
    never disagree about what the planner was shown. That disagreement is not
    hypothetical: the event carried only `entity_types`, so "a product was present"
    was visible while "WHICH product, and did its contract render" was not — and a
    live investigation twice concluded the product was absent from an enrichment that
    in fact contained it. An observability surface that cannot answer the question it
    exists for is worse than none.

    Under an EXCLUSIVE data surface only the on-surface products are rendered as
    contracts. Observed without this: a surface-only agent was shown FOUR contracts
    of which three were unusable, picked the wrong one on a lexical cue ("customer
    geography"), was denied, and concluded — falsely — that no product could count
    customers while the right contract sat in the pile. The runtime already knows
    which doors are open; showing locked ones is a maze, not transparency.
    """
    products = _all_products(entities)
    if surface is None:
        surface = _surface_for_turn()           # legacy principal fallback
    products_set, exclusive = surface
    if products_set is None:
        return products
    # Option B AND Option C: the listed products only (the list is the grant);
    # B additionally cuts the rest of the enrichment to the contract alone.
    return [e for e in products if str(e.get("name")) in products_set]


def _mark_on_surface(items: list, surface=None) -> tuple:
    """Stamp each catalog entry with whether THIS agent may consume it, surface-first.

    Returns (items, note). Unconfined turn -> items unchanged, note None. Pure and
    fail-soft so the catalog never breaks on an unreadable authority layer. This is
    the mid-turn half of the same fact the enrichment contract states at plan time:
    the agent that has just been denied re-orients from this listing, and a catalog
    that will not say which rows are usable sends it to the wrong row again.
    """
    if surface is None:
        surface = _surface_for_turn()           # legacy principal fallback
    products_set, exclusive = surface
    if products_set is None:
        return items, None
    try:
        for it in items:
            it["on_surface"] = str(it.get("product")) in products_set
        items = sorted(items, key=lambda it: not it.get("on_surface"))
        # THE LIST IS THE GRANT in Option C too — the note states the half that
        # differs: raw SQL stays available in C, denied in B.
        note = (("This agent is SURFACE-ONLY: only products with on_surface=true "
                 "are consumable; query_product refuses the others and raw SQL "
                 "is denied.") if exclusive else
                ("Only products with on_surface=true are consumable by this "
                 "agent (its configured list); query_product refuses the "
                 "others. Regular data paths remain available."))
        return items, note
    except Exception:  # noqa: BLE001
        return items, None


def _member_names(value) -> list:
    """Declared member names from a manifest block, whatever shape it is stored in.

    Canonically a LIST of objects ([{"name": "ticket_count", ...}]). Tolerates a dict
    (name -> spec) and a plain list of strings so a hand-edited or older manifest still
    names its members rather than silently naming none. Never raises: a contract that
    renders nothing is exactly the defect this replaced.
    """
    try:
        if isinstance(value, dict):
            return sorted(str(k) for k in value)
        if isinstance(value, list):
            out = []
            for item in value:
                if isinstance(item, dict) and item.get("name"):
                    out.append(str(item["name"]))
                elif isinstance(item, str):
                    out.append(item)
            return out
    except Exception:  # noqa: BLE001
        pass
    return []


def _dimension_names(entities) -> list:
    """Qualified dimension names (``Entity.Attribute``) — the form a request must use.

    Unqualified is what a model reaches for and what gets REJECTED, so the qualified
    form is the one worth showing.
    """
    out = []
    try:
        items = entities.items() if isinstance(entities, dict) else (
            [(e.get("name"), e) for e in entities if isinstance(e, dict)]
            if isinstance(entities, list) else [])
        for ent_name, spec in items:
            if not ent_name or not isinstance(spec, dict):
                continue
            for attr in _member_names(spec.get("attributes")):
                out.append(f"{ent_name}.{attr}")
    except Exception:  # noqa: BLE001
        pass
    return out



#: TDA_KnowledgeGraph actions that WRITE the graph — refused for a released-mode agent.
_KG_WRITE_ACTIONS = frozenset({"add_entity", "add_relationship", "restore", "declare_concept",
                               "propose_entity", "propose_attribute"})
# PROP-008 OBDA-MODEL: the two actions that may write the AUTHORED layer (gated on the OBDA-MODEL ·
# OBDA-ACT master + the modelAuthoring grant inside the handler).
_KG_ACT_ACTIONS = frozenset({"propose_entity", "propose_attribute"})
#: the DIRECT edits of the graph (not proposals): what a contracts-only model policy refuses
#: for an agent — the graph then changes only on the authored layer, through a proposal a
#: person lands and publishes (OBDA-MODEL)
_KG_DIRECT_EDITS = frozenset({"add_entity", "add_relationship", "declare_concept", "restore"})


def _act_next_step(drafted: bool, offered: bool) -> str:
    """What is owed after a proposal, in words the agent repeats — decided here, never left to
    the model (Oct 2026: an agent that had DRAFTED an attribute still asked "would you like me
    to draft it?", following an instruction to always offer the draft)."""
    if drafted:
        return ("ALREADY DRAFTED — say it is drafted on the authored layer and invisible to agents "
                "until published; the next steps are the person's own acts in the Govern dialog "
                "(land it, then publish it). Do NOT offer to draft it again.")
    if offered:
        return ("NOT drafted yet — present the verdict and offer to draft it; the person can also "
                "click 'Draft it' on the proposal.")
    return "Nothing to draft — say why, from the note."


async def _maybe_await(value):
    """GraphStore mixes sync reads (`get_entity_by_name`) with async writes; a caller
    must not assume either. Awaits an awaitable, returns anything else as is."""
    import inspect as _inspect
    return await value if _inspect.isawaitable(value) else value


class KnowledgeGraphHandler(BaseComponentHandler):
    """
    Knowledge Graph component handler.

    Dual purpose:
    1. Tool handler: processes TDA_KnowledgeGraph calls (query, visualize, add_entity, etc.)
    2. Context enrichment: provides get_context_enrichment() for planner guardrail injection
    """

    @property
    def component_id(self) -> str:
        return "knowledge_graph"

    @property
    def tool_name(self) -> str:
        return "TDA_KnowledgeGraph"

    @property
    def is_deterministic(self) -> bool:
        return True

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate that required arguments are present for the given action."""
        action = arguments.get("action")
        if not action:
            return False, "Missing required argument: 'action'"

        valid_actions = {"query", "visualize", "open", "add_entity", "add_relationship",
                         "get_context", "snapshot", "versions", "restore", "compile_metric",
                         "probe_bridge", "query_product", "list_products", "declare_concept",
                         "propose_entity", "propose_attribute"}
        if action not in valid_actions:
            return False, f"Invalid action '{action}'. Must be one of: {valid_actions}"

        if action == "compile_metric" and not arguments.get("metric_name"):
            return False, "compile_metric requires 'metric_name'"

        if action == "probe_bridge" and not arguments.get("bridge_name"):
            return False, "probe_bridge requires 'bridge_name'"

        if action == "add_entity":
            if not arguments.get("entity_name"):
                return False, "add_entity requires 'entity_name'"
            if not arguments.get("entity_type"):
                return False, "add_entity requires 'entity_type'"

        if action == "propose_attribute":
            if not (arguments.get("entity") or arguments.get("entity_name")):
                return False, "propose_attribute requires 'entity'"
            if not arguments.get("attribute_name"):
                return False, "propose_attribute requires 'attribute_name'"

        if action == "propose_entity" and not arguments.get("entity_name"):
            return False, "propose_entity requires 'entity_name'"

        if action == "add_relationship":
            if not arguments.get("entity_name"):
                return False, "add_relationship requires 'entity_name' (source)"
            if not arguments.get("target_entity"):
                return False, "add_relationship requires 'target_entity'"
            if not arguments.get("relationship_type"):
                return False, "add_relationship requires 'relationship_type'"

        return True, ""

    async def process(
        self,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ComponentRenderPayload:
        """Route to action-specific handlers."""
        action = arguments.get("action", "query")

        # Model B: a release is immutable by definition. An agent grounded on the released
        # view must not edit the draft it cannot see — it would never observe its own
        # edit. Recovery-shaped: names the mode and the act that changes it.
        if action in _KG_WRITE_ACTIONS:
            _refusal = self._released_write_refusal(action, context)
            if _refusal is not None:
                return _refusal
            # the deployment's OBDA-MODEL policy ("contracts only"): an agent changes the model
            # through a proposal a person publishes, never by editing the graph directly
            if action in _KG_DIRECT_EDITS:
                _refusal = self._policy_write_refusal(action, context)
                if _refusal is not None:
                    return _refusal

        if action == "query":
            return await self._handle_query(arguments, context)
        elif action == "visualize":
            return await self._handle_visualize(arguments, context)
        elif action == "open":
            return await self._handle_open(arguments, context)
        elif action == "snapshot":
            return await self._handle_snapshot(arguments, context)
        elif action == "versions":
            return await self._handle_versions(arguments, context)
        elif action == "restore":
            return await self._handle_restore(arguments, context)
        elif action == "add_entity":
            return await self._handle_add_entity(arguments, context)
        elif action == "add_relationship":
            return await self._handle_add_relationship(arguments, context)
        elif action == "get_context":
            return await self._handle_get_context(arguments, context)
        elif action == "compile_metric":
            return await self._handle_compile_metric(arguments, context)
        elif action == "declare_concept":
            return await self._handle_declare_concept(arguments, context)
        if action == "probe_bridge":
            return await self._handle_probe_bridge(arguments, context)
        if action == "query_product":
            return await self._handle_query_product(arguments, context)
        if action == "list_products":
            return await self._handle_list_products(arguments, context)
        if action == "propose_attribute":
            return await self._handle_propose_attribute(arguments, context)
        if action == "propose_entity":
            return await self._handle_propose_entity(arguments, context)
        else:
            return ComponentRenderPayload(
                component_id=self.component_id,
                render_target=RenderTarget.INLINE,
                html=f"<p class='text-red-400'>Unknown action: {action}</p>",
                spec={"error": f"unknown action '{action}'"},
                metadata={"tool_name": self.tool_name, "action": action, "error": True},
            )

    # -------------------------------------------------------------------
    # Context Enrichment (guardrail)
    # -------------------------------------------------------------------

    # Per-KG cache of the (query-independent) deployed trusted scope. The scope is stable
    # for a given KG; invalidated implicitly by process restart after a KG rebuild.
    _TRUSTED_SCOPE_CACHE: Dict[str, tuple] = {}

    async def get_trusted_scope(self, profile_id: str, user_uuid: str) -> tuple:
        """The profile's bound-KG **deployed trusted scope** — (databases, concept names) —
        resolved from the WHOLE KG, independent of any per-query enrichment. This is the
        always-on reference the grounding gate enforces: with it, the gate catches an answer
        that reaches outside the trusted scope even on a query that matched no KG entity
        (the hole that left the gate blind on out-of-domain queries). Cached per KG; never
        raises (returns ([], []) on any failure → gate abstains, safe)."""
        try:
            active_kg_owner = self._get_active_kg_owner(profile_id, user_uuid)
            kg_pid = active_kg_owner or profile_id
            store, _grounding = self._resolve_grounding_store(kg_pid, user_uuid, profile_id)
            kg_id = getattr(store, "kg_id", None)
            # keyed on a cheap content signature, so a rebuild/resync/import invalidates
            # the scope the grounding gate enforces (it was cached until process restart)
            _sig = _kg_signature(kg_id) if kg_id else None
            _hit = self._TRUSTED_SCOPE_CACHE.get(kg_id) if kg_id else None
            if _hit and _hit[0] == _sig:
                return _hit[1]
            tables = await store.list_entities(entity_type="table", limit=4000)
            dbs = _derive_full_kg_scope_dbs(tables)
            if not dbs:  # discovery KG with no physical layer → the database root node(s)
                dbnodes = await store.list_entities(entity_type="database", limit=50)
                dbs = {_db_node_name(e) for e in dbnodes if _db_node_name(e)}
            ents, seen = [], set()
            for et in ("entity", "metric", "business_concept"):
                for e in await store.list_entities(entity_type=et, limit=300):
                    n = (e or {}).get("name")
                    if n and n not in seen:
                        seen.add(n); ents.append(n)
            result = (sorted(dbs), sorted(ents)[:60])
            if kg_id:
                self._TRUSTED_SCOPE_CACHE[kg_id] = (_sig, result)
            return result
        except Exception as e:  # noqa: BLE001
            # The grounding gate's always-on boundary: a failure here makes the gate
            # ABSTAIN on every turn of this profile with nothing saying why. Once per
            # (profile, process) at WARNING — a fourth seam the DEBUG sweep found.
            from trusted_data_agent.core.log_once import warn_once as _warn_once
            _warn_once(logger, f"kg-trusted-scope:{profile_id}",
                       f"trusted-scope resolution failed for {profile_id} — the grounding gate "
                       f"abstains on this profile ({type(e).__name__}: {e})")
            return ([], [])

    async def get_context_enrichment(
        self,
        query: str,
        profile_id: str,
        user_uuid: str,
        max_nodes: int = 500,
        column_cap: Optional[int] = None,
        record_usage: bool = True,
        session_id: Optional[str] = None,
        workflow_history: Optional[List[Dict]] = None,
    ) -> tuple:
        """
        Context enrichment for the planner — the core guardrail mechanism.

        ``session_id`` / ``workflow_history``: the conversation this query continues.
        The previous turn's referenced entities are carried into the seeds (see
        ``previous_turn_carry_over``) so a follow-up that names nothing the graph holds
        still reaches the tables the conversation is about. Absent ⇒ query-only.

        Extracts a relevant subgraph based on keyword matching of the query
        against entity names, descriptions, and business meanings. Returns
        a tuple of (formatted_text, metadata_dict) suitable for prompt
        injection and Live Status event emission.

        Uses ONLY the active KG for this profile. At most one KG can be active
        per profile at a time (enforced by kg_profile_assignments.is_active).
        Falls back to the profile's own KG if no assignment row exists yet
        (backward compatibility for profiles created before the assignment system).

        Called by ComponentManager.get_context_enrichment() before strategic planning.

        Returns:
            (text, metadata) where metadata is a dict with entity/relationship
            counts for the Live Status event, or ("", None) if no enrichment.
        """
        # THE per-agent OBDA-READ mode (Option A default · B exclusive · C mixed) —
        # resolved ONCE here and threaded to the subgraph filter, the event and
        # the renderer, so they cannot disagree about what the planner is shown.
        _obda_mode, _obda_products = _profile_surface(
            {"profile_id": profile_id, "user_uuid": user_uuid})

        # Determine which KG to use: the active one from the assignment table
        active_kg_owner = self._get_active_kg_owner(profile_id, user_uuid)

        if active_kg_owner:
            kg_pid = active_kg_owner
        else:
            # Fallback: no assignment rows yet — use the profile's own KG
            kg_pid = profile_id

        logger.debug(f"KG enrichment: resolved kg_pid={kg_pid} for profile_id={profile_id}")

        store, _grounding = self._resolve_grounding_store(kg_pid, user_uuid, profile_id)
        # KG theatre: live stage notices (fail-open; a no-op without an armed sink)
        try:
            from trusted_data_agent.core import kg_stage as _kg_stage
        except Exception:  # noqa: BLE001
            _kg_stage = None
        if _kg_stage:
            _kg_stage.emit_stage("understand", {"terms": _scoring_terms(query),
                                                "kg_id": getattr(store, "kg_id", None)})
        seeding_report: Dict[str, Any] = {}

        # Conversational carry-over: the previous turn's entities join the seeds, ahead
        # of the lexical ranking (bounded), so "those customers" reaches the table the
        # last turn read. Resolved BEFORE seeding so the carried entities take slots of
        # the cut instead of extending it. Never widens on a session with no usable
        # previous turn.
        carry = None
        try:
            if workflow_history is None and session_id and user_uuid:
                from trusted_data_agent.core import session_manager as _sm
                workflow_history = await _sm.get_workflow_history(user_uuid, session_id)
            carry = previous_turn_carry_over(workflow_history) if workflow_history else None
        except Exception as exc:  # noqa: BLE001 — continuity must never cost the turn its enrichment
            from trusted_data_agent.core.log_once import warn_once as _warn_once
            _warn_once(logger, f"kg-carry-over:{getattr(store, 'kg_id', None)}",
                       f"KG enrichment: carry-over skipped "
                       f"(kg={getattr(store, 'kg_id', None)}): {type(exc).__name__}: {exc}")
            carry = None
        carried: List[Dict] = []
        if carry and (carry.get("entities") or carry.get("missed")):
            have: set = set()
            # missed tables FIRST — the agent had to find them without the graph's help,
            # so they lead; resolved by LEAF to the logical table entity (the SQL names
            # the deployed identifier, the graph the logical one — the leaf is shared)
            for label in carry.get("missed") or []:
                ent = await self._table_entity_by_leaf(store, str(label).rpartition(".")[2])
                if isinstance(ent, dict) and ent.get("id") is not None and ent["id"] not in have:
                    carried.append(ent)
                    have.add(ent["id"])
            for nm in carry.get("entities") or []:
                try:
                    ent = await _maybe_await(store.get_entity_by_name(nm))
                except Exception:  # noqa: BLE001
                    ent = None
                if isinstance(ent, dict) and ent.get("id") is not None and ent["id"] not in have:
                    carried.append(ent)
                    have.add(ent["id"])
        entities = await self._search_entities_for_query(
            store, query, obda_surface=(_obda_mode, _obda_products),
            seeding_report=seeding_report,
            seed_limit=_SEED_LIMIT - len(carried))
        if carried:
            _ids = {e.get("id") for e in carried}
            entities = [e for e in entities if e.get("id") not in _ids]
            # after the seeded data products (governance first — the seeder put them
            # at the head, see _search_entities_for_query), before the ranking
            _n_prod = int(seeding_report.get("products_seeded") or 0)
            entities = entities[:_n_prod] + carried + entities[_n_prod:]
            seeding_report["carry_over"] = {"from_turn": carry.get("from_turn"),
                                            "source": carry.get("source"),
                                            "entities": [str(e.get("name")) for e in carried]}

        logger.debug(f"KG enrichment: found {len(entities)} matching entities for query")

        if _kg_stage:
            _terms = _scoring_terms(query)
            _bridged = {str(b.get("name")) for b in seeding_report.get("bridged", []) or []
                        if isinstance(b, dict)}
            _carried = set((seeding_report.get("carry_over") or {}).get("entities") or [])
            _kg_stage.emit_stage("seed", {
                "count": len(entities),
                "seeds": [{"name": str(e.get("name") or ""), "type": e.get("entity_type"),
                           "score": int(_match_reasons(e, _terms)[0]),
                           "reasons": _match_reasons(e, _terms)[1],
                           "terms": _match_reasons(e, _terms)[2],
                           "channel": ("product" if _is_product(e)
                                       else "carry_over" if str(e.get("name")) in _carried
                                       else "semantic" if str(e.get("name")) in _bridged
                                       else "lexical")}
                          for e in entities[:25]],
            })

        if not entities:
            return "", None

        entity_ids = [e["id"] for e in entities]

        # Adaptive extraction: entity-type-aware, unbounded FK chains,
        # iterative joinable-table discovery, budget-aware column expansion.
        # Replaces the old all-tables-as-seeds + fixed depth=2 + max_nodes=50
        # approach which missed multi-hop JOIN chains and truncated at ~5 tables.
        # Relevance-scoped column expansion (Phase B of the Aug-2026 KG program):
        # seed columns always, then question-matching columns, then keys, up to a
        # per-table cap set by the profile's `intensity` (low 6 · medium 12 · high
        # 24; `column_cap` from the context-window module overrides it when the
        # budget forces a smaller enrichment). Legacy coverage expansion (all
        # columns of every discovered table) is still reachable with intensity
        # "full" — and is what filled 500 nodes with two tables' columns.
        _cap = column_cap if column_cap is not None else _intensity_column_cap(
            {"profile_id": profile_id, "user_uuid": user_uuid})
        _scope_kwargs = ({"query_terms": _scoring_terms(query), "per_table_cap": _cap}
                         if _cap is not None else {})
        # Off the event loop: the first call loads the whole graph (two full-table
        # reads + json.loads per row) synchronously; on a 16K-node graph that stalled
        # every other request. The SQLite layer serialises C-calls itself, so a worker
        # thread is safe by design (SQLITE_CONCURRENCY).
        subgraph = await asyncio.to_thread(
            store.extract_subgraph_adaptive,
            seed_entity_ids=entity_ids,
            query_entity_ids=entity_ids,
            # Budget-driven node cap. Default 500 preserves the legacy behaviour;
            # the knowledge_graph_context module passes a value derived from the
            # module's token allocation so the enrichment fits its budget.
            max_nodes=max(20, int(max_nodes)),
            **_scope_kwargs,
        )

        if not subgraph.get("entities"):
            logger.debug("KG enrichment: subgraph extraction returned no entities")
            return "", None

        # Bridge tables (deterministic): two tables in play that share NO column
        # cannot be joined directly, but a third table sharing a column with each
        # can bridge them (SaleDetails —SaleID— Sales —CustomerID— Customers).
        # On a graph without foreign_key nodes nothing else pulls that middle
        # table in, and the planner was left to invent a join (`Customers.SaleID`)
        # — a guaranteed error round. Bounded; fails soft.
        try:
            bridge_ids = await self._bridge_table_ids(store, subgraph)
            if bridge_ids:
                subgraph = await asyncio.to_thread(
                    store.extract_subgraph_adaptive,
                    seed_entity_ids=list(dict.fromkeys(entity_ids + bridge_ids)),
                    query_entity_ids=entity_ids,
                    max_nodes=max(20, int(max_nodes)),
                    **_scope_kwargs,
                )
                if isinstance(subgraph, dict):
                    subgraph["_bridge_ids"] = list(bridge_ids)
        except Exception as exc:  # noqa: BLE001
            # Once per (graph, process) at WARNING — a seam that fails silently at DEBUG is
            # a channel that can be dead for weeks (the semantic-seeding lesson, Sep 2026).
            from trusted_data_agent.core.log_once import warn_once as _warn_once
            _warn_once(logger, f"kg-bridge-discovery:{getattr(store, 'kg_id', None)}",
                       f"KG enrichment: bridge-table discovery skipped "
                       f"(kg={getattr(store, 'kg_id', None)}): {type(exc).__name__}: {exc}")

        if _obda_mode == "off":
            # OBDA-READ IS ADDITIVE, per agent: Option A (the default, and always
            # the case with the master off) — product nodes leave the planner's
            # subgraph ENTIRELY: nodes AND the relationships that name them (a
            # `support_load --[member]--> Products` line is a product made
            # visible by its edge). ONE seam, here at assembly, because a
            # filter on a single extraction path was bypassed by the adaptive
            # extractor (found live: the enrichment card kept listing
            # 'data_product' in its type chips, session a52f10d2).
            _prod_ids = {e.get("id") for e in subgraph["entities"]
                         if (e.get("entity_type") or "") == "data_product"}
            _prod_names = {str(e.get("name")) for e in subgraph["entities"]
                           if (e.get("entity_type") or "") == "data_product"}
            if _prod_ids or _prod_names:
                subgraph["entities"] = [
                    e for e in subgraph["entities"]
                    if (e.get("entity_type") or "") != "data_product"]
                subgraph["relationships"] = [
                    r for r in subgraph.get("relationships", [])
                    if not ({r.get("source_id"), r.get("target_id")} & _prod_ids
                            or {str(r.get("source_name")),
                                str(r.get("target_name"))} & _prod_names)]
            if not subgraph.get("entities"):
                return "", None
        # PROP-008 §2b: an action type reaches an agent's context ONLY when the agent's
        # grant names it (B4 renders granted types); until then it is cut here, nodes AND
        # the `acts_on` edges that name them — the same assembly seam as the product cut,
        # unconditional, so no write contract is advertised to an agent that may not use it.
        subgraph = _cut_action_types(subgraph)
        if not subgraph.get("entities"):
            return "", None
        # PROP-008 §2b: an unpublished authored node (a draft, a landed-but-unpublished
        # extension) never reaches the prompt — the same assembly seam as the product cut.
        subgraph = _cut_unpublished_authored(subgraph, await _unpublished_names_of(store))
        if not subgraph.get("entities"):
            return "", None

        # Collect entity type names for the event
        entity_types = list({e["entity_type"] for e in subgraph["entities"]})
        relationships = subgraph.get("relationships", [])

        if _kg_stage:
            _pc: Dict[str, int] = {}
            for _tag in (subgraph.get("provenance") or {}).values():
                _pc[_tag] = _pc.get(_tag, 0) + 1
            _kg_stage.emit_stage("expand", {
                "entities": len(subgraph["entities"]),
                "relationships": len(relationships),
                "provenance_counts": _pc,
                "bridges": len(subgraph.get("_bridge_ids") or []),
            })

        # WHICH graph enriched the turn. The event carried the owner profile but not the
        # graph's own identity, so a Live Status card could not say which ontology it read
        # — invisible for a consumer whose pack copy sits beside the publisher's, and blind
        # for any future multi-graph turn. Best-effort: an older store shape yields None.
        _kg_id = getattr(store, "kg_id", None)
        try:
            _kg_name = (store.get_kg_metadata() or {}).get("name") if _kg_id else None
        except Exception:  # noqa: BLE001
            _kg_name = None
        metadata = {
            "kg_owner_profile_id": kg_pid,
            "kg_id": _kg_id,
            "kg_name": _kg_name,
            "entity_count": len(subgraph["entities"]),
            "relationship_count": len(relationships),
            "entity_types": entity_types,
            # The rendered entities by qualified name (bounded by the subgraph's own
            # node budget). This is what lets a post-turn reader — the seed-utilization
            # scorer, the `used` telemetry — ask "did the answer TOUCH what the graph
            # showed?" without re-running retrieval.
            "entity_names": [str(e.get("name")) for e in subgraph["entities"] if e.get("name")],
            # Model B: WHICH version the agent grounded on — live draft, a release, or a
            # release-mode agent honestly falling back to the draft because none exists.
            "grounding": _grounding,
        }

        # How the question REACHED these entities. Seeding has two recall channels and
        # they answer different questions: lexical matches the words the question used,
        # semantic matches what it MEANT. Without this the event cannot distinguish a
        # graph whose vocabulary already matches the question from one where the answer
        # was only found by meaning — and, when there are no semantic hits, cannot say
        # whether the channel is off, still warming, or simply found nothing.
        if seeding_report:
            metadata["semantic_seeding"] = _semantic_contribution(
                seeding_report, {str(e.get("name")) for e in subgraph["entities"]})
            if seeding_report.get("carry_over"):
                metadata["carry_over"] = dict(seeding_report["carry_over"])

        # Capability-aware enrichment: deterministically resolve each logical
        # table/metric to its DEPLOYED, queryable physical identifier (+ metric
        # compile capability) so the engine receives *resolved knowledge*, not just
        # logical meaning, and can self-direct. Best-effort and additive — a
        # logical-only / undeployed graph simply yields no annotations.
        capabilities = await self._resolve_capabilities(store, subgraph["entities"])

        # Declare the model's DEPLOYED, queryable scope (the database(s) the resolved
        # identifiers live in) on the enrichment metadata. This is a self-describing
        # capability signal — it lets downstream observability (the eval layer's
        # scope-adherence scorer) and the grounding gate know which data sources are
        # in-scope WITHOUT constraining the agent.
        metadata["scope_databases"] = _derive_scope_databases(capabilities, subgraph["entities"])
        # Self-describing scope: the concept names the trusted scope covers (for the
        # scope_faithfulness judge + transparent reload/audit, charter #17/#19).
        metadata["scope_entities"] = _derive_scope_entities(subgraph["entities"])
        # Structured schema facts for DETERMINISTIC consumers downstream (the
        # column-not-found self-correction, the champion–ontology filter). The
        # rendered text is for the model; this is for code.
        metadata["table_columns"] = _table_columns_map(subgraph, capabilities)
        metadata["metrics"] = _metric_facts(subgraph)
        # Declared join keys per table pair — what the preflight guard checks a JOIN
        # against before it reaches the database (the runaway-join class).
        # Columns two tables both hold as a FOREIGN key — what the preflight guard checks a
        # JOIN between the two holders against (the fan-out class).
        metadata["fanout_keys"] = [{"column": f["column"], "owner": f["owner"], "tables": f["tables"]}
                                   for f in _fanout_keys(subgraph)]
        metadata["join_keys"] = [{"tables": jp["leaves"], "key": jp["key"]}
                                 for jp in _declared_join_pairs(subgraph) if jp["key"]]
        # The model's domain partition + its declared class-level correspondences — what
        # the preflight guard checks a CROSS-DOMAIN join against: two tables in different
        # subject areas with no declared per-record key may only be related through the
        # bridge the model declares, never joined. Empty on graphs that declare no areas.
        metadata["subject_areas"] = _subject_area_map(subgraph)
        metadata["class_bridges"] = _class_bridges(subgraph)
        # Text-stored date columns — what the preflight guard checks a comparison,
        # ORDER BY or SUBSTR bucket against (the lexical-date class). ONE predicate
        # (`_is_text_date_attr`) feeds the enrichment's warning line AND this list.
        metadata["text_date_columns"] = _text_date_columns(subgraph, capabilities)
        # Declared value domains per column — what the preflight VALUE guard checks an
        # equality literal against (the hallucinated-literal class: a FIPS code on a
        # UCR-coded column, a city name on a Yes/No label).
        metadata["value_domains"] = _value_domains(subgraph, capabilities)
        # The COMPLETE column list of every table in play — what the preflight COLUMN guard
        # checks a reference against (the hallucinated-column class: `boarding_event.miles_flown`).
        # The render above is CAPPED by relevance; this is the whole list, from the loaded
        # graph, and only for a graph whose constructor is known to list every column.
        try:
            metadata["table_columns_full"] = await self._full_table_columns(store, subgraph, capabilities)
            # …and the WHOLE graph's lists into the session's preflight store (never onto the
            # event — 200 tables × 40 columns per turn would bloat every persisted turn), so a
            # table a later statement names that this turn's enrichment did not render is
            # still checkable. Once per (session, graph); the event's small in-play map is
            # what reload/audit carries.
            if session_id and metadata["table_columns_full"]:
                await self._record_whole_graph_columns(store, session_id)
        except Exception as exc:  # noqa: BLE001
            from trusted_data_agent.core.log_once import warn_once as _warn_once
            _warn_once(logger, f"kg-full-columns:{getattr(store, 'kg_id', None)}",
                       f"KG enrichment: full column lists skipped "
                       f"(kg={getattr(store, 'kg_id', None)}): {type(exc).__name__}: {exc}")
            metadata["table_columns_full"] = {}
        # KG theatre facts — the ER reading of this enrichment (seeds with reasons,
        # per-node provenance, entity relationships with keys, attribute facts).
        # Persisted with the event, so the reload replay narrates the same pipeline.
        try:
            metadata.update(_theatre_facts(subgraph, entities, seeding_report, query))
        except Exception as exc:  # noqa: BLE001
            from trusted_data_agent.core.log_once import warn_once as _warn_once
            _warn_once(logger, f"kg-theatre-facts:{getattr(store, 'kg_id', None)}",
                       f"KG enrichment: theatre facts skipped "
                       f"(kg={getattr(store, 'kg_id', None)}): {type(exc).__name__}: {exc}")
        if _kg_stage:
            _kg_stage.emit_stage("inject", {
                "entities": len(subgraph["entities"]),
                "relationships": len(relationships),
                "omitted_columns": dict(subgraph.get("omitted_columns") or {}),
                "surface_only": _obda_mode == "exclusive",
            })
        # WHICH signed data products the planner was actually shown as consumption
        # contracts — read from the SAME rule the renderer uses, never recomputed.
        # `entity_types` already said a product was present; it could not say which,
        # and that is precisely the question asked when a turn writes raw SQL over a
        # table a product already covers.
        # Same mode as the prompt renderer — the event describes what the
        # planner was SHOWN: Option A shows no contracts; B/C show the LIST.
        _ev_surface = ((_obda_products, _obda_mode == "exclusive")
                       if _obda_mode != "off" else None)
        metadata["data_products"] = ([str(e.get("name")) for e in
                                      _rendered_products(subgraph["entities"],
                                                         surface=_ev_surface)]
                                     if _obda_mode != "off" else [])
        _ev_off = (_off_surface_product_names(subgraph["entities"],
                                              surface=_ev_surface)
                   if _obda_mode != "off" else [])
        if _ev_off:
            metadata["data_products_off_surface"] = _ev_off
        # The event must distinguish what was ANALYSED from what was INJECTED.
        # Under an exclusive surface the full subgraph is still extracted (the
        # grounding gate's trusted scope derives from it) but the prompt text is
        # the contract alone — and a title reading "51 entities" over a turn that
        # injected none of them claims an injection that did not happen.
        if _obda_mode == "exclusive":
            metadata["surface_only"] = True

        # Usage telemetry (fire-and-forget, off the hot path, separate table — never
        # touches kg_entities so the graph cache/trust hashes are untouched): the
        # entities we SEEDED and the ones actually RENDERED into the subgraph.
        if record_usage:
            _kid = getattr(store, "kg_id", None)
            if _kid:
                _record_usage_async(_kid,
                                    [e.get("name") for e in entities],
                                    [e.get("name") for e in subgraph.get("entities", [])])

        # Format as structured text for prompt injection
        text = self._format_subgraph_for_prompt(
            subgraph, capabilities,
            surface=((_obda_products, _obda_mode == "exclusive")
                     if _obda_mode != "off" else "off"),
            query=query)
        # A question that spans both areas of a declared correspondence LEADS with it:
        # the correspondence is the shape of the answer, and a line near the bottom of
        # the block is one a weak model walks past on its way to writing a JOIN.
        spanning = _spanning_correspondences(entities, subgraph, metadata.get("class_bridges") or [])
        if spanning and text:
            metadata["spanning_bridges"] = [b.get("name") for b in spanning]
            head, sep, rest = text.partition("\n")
            text = head + sep + "\n".join(_render_correspondence_lead(spanning)) + "\n" + rest
        # PROP-008 Act II, scene 7: a question whose SUBJECT nothing in the graph matched
        # leads with that fact — decided in code from the seeding search, never left to the
        # model to notice. Only while OBDA-MODEL · OBDA-ACT is on, so the baseline is byte-identical.
        _gap = None
        try:
            from trusted_data_agent.core import obda_planes as _planes_gap
            if text and seeding_report is not None and _planes_gap.plane_enabled(
                    "model", _profile_of(profile_id, user_uuid), user_uuid):
                _gap = _model_gap(query, seeding_report.get("unreached_terms") or [],
                                  len((metadata.get("semantic_seeding") or {}).get("bridged") or []))
        except Exception:  # noqa: BLE001 — a gap that cannot be decided is not claimed
            _gap = None
        if _gap:
            _may_author = self._act_refusal("propose_entity", {"profile_id": profile_id, "user_uuid": user_uuid}) is None
            # What the proposal may belong to: the model's own entities, read here from the
            # graph that decided the gap (bounded; only when a proposal can follow). The
            # note's form offers exactly these — metadata for the turn, never prompt text.
            _cands: List[str] = []
            if _may_author:
                try:
                    _ents = await _maybe_await(store.list_entities(entity_type="entity", limit=400)) or []
                    _cands = sorted({str(e.get("name")) for e in _ents if e.get("name")})[:_GAP_MAX_PARENTS]
                except Exception:  # noqa: BLE001 — no candidates is a form with a free choice
                    _cands = []
            # `singular` decided HERE, once — the note under the answer names the concept
            # with it and must never re-derive (or guess) it downstream.
            metadata["model_gap"] = {"term": _gap, "singular": _singularize(_gap) or _gap,
                                     "may_author": _may_author, "entities": _cands}
            head, sep, rest = text.partition("\n")
            text = head + sep + "\n".join(_render_gap_lead(_gap, _may_author)) + "\n" + rest
        return text, metadata

    #: Bounds for the full-column emission: tables per turn, and the neighbourhood read
    #: (a 393-column NIBRS table plus its FK edges must fit, or the table is skipped).
    _FULL_COLUMNS_MAX_TABLES = 40
    _FULL_COLUMNS_MAX_NEIGHBORS = 1500

    async def _full_table_columns(self, store: Any, subgraph: Dict[str, List],
                                  capabilities: Dict[str, Any]) -> Dict[str, List[str]]:
        """{table_leaf: [every column leaf]} for the logical tables of the subgraph, keyed by
        the logical leaf AND the deployed leaf (`Incident` and `v_Incident`), read from the
        loaded graph's complete 1-hop neighbourhood. {} unless the graph is COLUMN-COMPLETE
        (`_columns_complete`) — a guard that refuses on a partial list would refuse real
        columns. A table whose neighbourhood was truncated is skipped, never guessed."""
        try:
            roots = await _maybe_await(store.list_entities(entity_type="database", limit=5)) or []
        except Exception:  # noqa: BLE001
            roots = []
        if not _columns_complete(subgraph, roots):
            return {}
        out: Dict[str, List[str]] = {}
        tables = [e for e in subgraph.get("entities") or [] if e.get("entity_type") == "entity"]
        for e in tables[:self._FULL_COLUMNS_MAX_TABLES]:
            eid = e.get("id")
            if eid is None:
                continue
            nb = await _maybe_await(store.get_node_neighborhood(eid, max_neighbors=self._FULL_COLUMNS_MAX_NEIGHBORS))
            if not isinstance(nb, dict) or nb.get("truncated"):
                continue
            by_id = {n.get("id"): n for n in nb.get("entities") or [] if isinstance(n, dict)}
            cols: List[str] = []
            for r in nb.get("relationships") or []:
                if r.get("relationship_type") != "contains" or r.get("source_id") != eid:
                    continue
                tgt = by_id.get(r.get("target_id")) or {}
                if tgt.get("entity_type") in ("attribute", "column"):
                    leaf = str(tgt.get("name") or "").rpartition(".")[2]
                    if leaf and leaf not in cols:
                        cols.append(leaf)
            if len(cols) < 2:
                continue                                      # a one-column "table" is not a complete list
            # The declared KEY columns exist by construction — the table is identified by
            # them — and they are declared as a PROPERTY, not as `contains` edges. Reading
            # only the edges made this check contradict the JOIN check on the same graph
            # (prod session be4979bf: the declared key of v_Offence reported as
            # non-existent columns on a statement that ran and returned rows). The floor
            # above is applied to the CONTAINS evidence first, so unioning keys can never
            # make an incomplete list pass.
            cols = _with_declared_keys(cols, e.get("properties") or {})
            logical_leaf = str(e.get("name") or "").rpartition(".")[2]
            out[logical_leaf] = cols
            cap = capabilities.get(e.get("name")) if capabilities else None
            dep = getattr(cap, "deployed_as", None) if cap else None
            if isinstance(dep, str) and dep.strip():
                out.setdefault(dep.rpartition(".")[2], cols)
        return out

    _WHOLE_GRAPH_COLS_RECORDED: set = set()   # (session_id, kg_id) — bounded by the guard's own TTL sweep

    async def _record_whole_graph_columns(self, store: Any, session_id: str) -> None:
        """Record every table's complete column list into the preflight COLUMN store for this
        session (one graph pass, off the loop). Only called for a column-complete graph."""
        kid = getattr(store, "kg_id", None)
        key = (session_id, kid)
        if not kid or key in self._WHOLE_GRAPH_COLS_RECORDED:
            return
        fn = getattr(store, "all_table_columns", None)
        if not callable(fn):
            return
        full = await asyncio.to_thread(fn)
        if full:
            from trusted_data_agent.agent.preflight import columns as _columns
            _columns.record_columns(session_id, full)
            if len(self._WHOLE_GRAPH_COLS_RECORDED) > 5000:
                self._WHOLE_GRAPH_COLS_RECORDED.clear()
            self._WHOLE_GRAPH_COLS_RECORDED.add(key)

    async def _table_entity_by_leaf(self, store: Any, leaf: str) -> Optional[Dict]:
        """The graph's LOGICAL table entity whose leaf is `leaf` (exact, case-insensitive);
        the physical `table` node as a fallback. None when the graph holds no such table
        or the lookup fails — a table the graph does not hold is not a miss to carry."""
        leaf = str(leaf or "").strip().lower()
        if not leaf:
            return None
        try:
            for et in ("entity", "table"):
                rows = await _maybe_await(store.search_entities(leaf, limit=50, entity_type=et)) or []
                for r in rows:
                    if str(r.get("name") or "").rpartition(".")[2].lower() == leaf:
                        return r
        except Exception:  # noqa: BLE001
            return None
        return None

    async def _resolve_capabilities(self, store: Any, entities: List[Dict]) -> Dict[str, Any]:
        """Resolve {logical_name → EntityCapabilities} for tables/metrics via the
        deterministic resolver. Never raises — resolution failure degrades to no
        annotation (the knowledge graph stays additive)."""
        from components.builtin.knowledge_graph.resolver import resolve_capabilities
        caps: Dict[str, Any] = {}
        for e in entities:
            if e.get("entity_type") not in ("entity", "metric"):
                continue
            try:
                c = await resolve_capabilities(store, e)
                if c.has_any:
                    caps[e["name"]] = c
            except Exception:  # noqa: BLE001
                pass
        return caps

    _BRIDGE_MAX_ATTRIBUTES = 3000

    async def _bridge_table_ids(self, store: Any, subgraph: Dict[str, List]) -> List[int]:
        """Ids of logical tables that bridge two unconnected tables of the subgraph
        via shared column names. Empty when every pair already shares a column, when
        the graph is too large to scan, or on any failure."""
        tables_in = [e for e in subgraph.get("entities", []) if e.get("entity_type") == "entity"]
        if len(tables_in) < 2:
            return []
        # explicit limit: the store's DEFAULT limit is 200, which made the guard below
        # unreachable and handed this scan an arbitrary alphabetic slice of the graph
        attrs = await store.list_entities(entity_type="attribute", limit=self._BRIDGE_MAX_ATTRIBUTES + 1)
        if not attrs or len(attrs) > self._BRIDGE_MAX_ATTRIBUTES:
            return []
        cols_by_table: Dict[str, set] = {}
        for a in attrs:
            tbl, _, col = str(a.get("name") or "").rpartition(".")
            if tbl and col:
                cols_by_table.setdefault(tbl, set()).add(col.lower())
        names_in = [str(t.get("name")) for t in tables_in]
        needed: set = set()
        for i in range(len(names_in)):
            for j in range(i + 1, len(names_in)):
                a, b = names_in[i], names_in[j]
                ca, cb = cols_by_table.get(a, set()), cols_by_table.get(b, set())
                if not ca or not cb or (ca & cb):
                    continue                     # unknown, or already directly joinable
                for c, cc in cols_by_table.items():
                    if c in names_in:
                        continue
                    if (cc & ca) and (cc & cb):
                        needed.add(c)
        if not needed:
            return []
        all_tables = await store.list_entities(entity_type="entity", limit=5000)
        ids = [t["id"] for t in all_tables if str(t.get("name")) in needed and t.get("id") is not None]
        if ids:
            logger.info(f"KG enrichment: bridge tables added for JOIN paths: {sorted(needed)}")
        return ids[:6]

    async def _semantic_scores(self, store: Any, query: str, results: List[Dict],
                               seen_ids: set,
                               report: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        """{entity_name: cosine} from the semantic index; {} when it cannot contribute.

        Semantic-only hits (nearest by meaning, not matched lexically) are ADDED to the
        candidate pool in place so ranking can surface them; the returned scores let
        `_rank_seeds` weight both the new hits and any pool entity the query is
        semantically near. Never raises; fetching a hit's entity dict is best-effort.

        ``report`` is filled in place for the Live Status event. It must distinguish
        the three ways this returns nothing — the channel is OFF, its index is still
        WARMING (a cold graph never blocks a turn), or it ran and nothing cleared the
        threshold — because "no semantic hits" reads identically in all three and only
        one of them is a finding about the question.
        """
        rep = report if report is not None else {}
        rep.setdefault("state", "off")
        rep.setdefault("hits", 0)
        rep.setdefault("bridged", [])
        try:
            from trusted_data_agent.kgstore import semantic_index as _sem
            if not _sem.is_enabled():
                return {}
            kid = getattr(store, "kg_id", None)
            if not kid:
                return {}
            sig = _kg_signature(kid)
            rep["state"] = _sem.index_state(kid, sig)
            hits = await asyncio.to_thread(
                _sem.semantic_hits, kid, query,
                lambda: self._all_entities_sync(store), sig)
            # State is read again AFTER the call: a cold graph starts its build there,
            # so the pre-call read would report `cold` for a turn that is warming.
            rep["state"] = _sem.index_state(kid, sig)
            if not hits:
                return {}
            rep["hits"] = len(hits)
            # The channel's VALUE is the entity lexical search never found — a hit the
            # pool already held is corroboration, not a bridge, and counting it as one
            # would let the event overstate what semantics contributed.
            lexical_names = {str(e.get("name")) for e in results}
            scores = {name: score for name, score in hits}
            for name, score in hits:
                if name in lexical_names:
                    continue
                # GraphStore.get_entity_by_name is SYNC (its writes are async) — a bare
                # `await` here raised TypeError on every turn semantics had something to
                # add, and the blanket except below reported it as "did not run". The
                # gate's fake store had declared the method async; it is now pinned to
                # the real calling convention (test_kg_phase_f).
                ent = await _maybe_await(store.get_entity_by_name(name))
                if ent and ent.get("id") not in seen_ids:
                    results.append(ent)
                    seen_ids.add(ent["id"])
                    rep["bridged"].append({"name": name, "score": round(float(score), 3)})
            return scores
        except Exception as exc:  # noqa: BLE001 — never break retrieval
            # WARNING, not debug: the event already tells the operator the channel
            # "did not run", and a production log that cannot name why is what turned
            # this into a session-by-session investigation.
            from trusted_data_agent.core.log_once import warn_once as _warn_once
            _warn_once(logger, f"kg-semantic-scoring:{getattr(store, 'kg_id', None)}",
                       f"KG semantic scoring skipped (kg={getattr(store, 'kg_id', None)}): "
                       f"{type(exc).__name__}: {exc}")
            rep["state"] = "error"
            return {}

    def _all_entities_sync(self, store: Any) -> List[Dict]:
        """All entity dicts for the index build (sync — called inside a worker thread).

        Prefers the store's own accessor so the read side and the write side (which
        warms the index after a bulk import) see the same entity set; the fallbacks
        cover a store shape that predates it, and a test double."""
        if hasattr(store, "all_entity_dicts"):
            return store.all_entity_dicts()
        le = store.list_entities
        import inspect as _inspect
        if _inspect.iscoroutinefunction(le):
            # GraphStore.list_entities is async; reach the sync backend method underneath
            be = store._get_backend() if hasattr(store, "_get_backend") else None
            if be is not None and hasattr(be, "list_entities"):
                return be.list_entities(getattr(store, "kg_id", None), limit=999999)
            return []
        return le(limit=999999)

    async def _search_entities_for_query(self, store: Any, query: str,
                                         obda_surface: tuple = None,
                                         usage_priors: Optional[Dict[str, float]] = None,
                                         seeding_report: Optional[Dict[str, Any]] = None,
                                         seed_limit: int = _SEED_LIMIT) -> List[Dict]:
        """
        Search for entities relevant to the user's query.
        Uses both direct substring search and tokenized keyword matching.

        ``usage_priors``: None ⇒ fetch this KG's telemetry priors from the usage
        store (empty when un-instrumented → no-op); pass {} to disable (the offline
        benchmark does this to stay deterministic regardless of production telemetry).
        """
        if usage_priors is None:
            _kid = getattr(store, "kg_id", None)
            usage_priors = _usage_priors(_kid) if _kid else {}
        # Direct search on the full query
        results = await store.search_entities(query, limit=_SEARCH_FETCH)

        # Also search individual words (for multi-word queries like "show orders by product")
        # Stop words come from the ONE declared set: a second inline copy had already
        # drifted from it, which is how a vocabulary quietly stops being one vocabulary.
        words = re.findall(r'\b[a-zA-Z_]{3,}\b', query.lower())
        seen_ids = {e["id"] for e in results}
        searched: set = set()

        _term_hit: Dict[str, bool] = {}
        for word in words:
            if word in _STOP_WORDS:
                continue

            # A plural term finds nothing against singular entity names, so try the
            # singular too. Ranking happens afterwards and can only order what the
            # pool contains.
            for term in (word, _singularize(word)):
                if not term or term in searched:
                    continue
                searched.add(term)
                _rows = await store.search_entities(term, limit=_SEARCH_FETCH)
                _term_hit[term] = bool(_rows)
                for ent in _rows:
                    if ent["id"] not in seen_ids:
                        results.append(ent)
                        seen_ids.add(ent["id"])

            if len(searched) >= _SEARCH_TERM_CAP:
                logger.warning("KG seeding: question exceeds %d searchable terms; later terms not searched",
                               _SEARCH_TERM_CAP)
                break

        # The words that matched NOTHING in the whole graph (every searched form came back
        # empty) — what `_model_gap` reads to say a question's subject is not in the model.
        if seeding_report is not None:
            seeding_report["unreached_terms"] = [t for t, hit in _term_hit.items() if not hit]

        # Opt-in SEMANTIC seeding: a second recall channel that bridges the vocabulary
        # gap the lexical seeder cannot ("customers" → an entity called `Party`). Off by
        # default → this whole block is skipped and the pool/ranking are byte-identical.
        semantic_scores = await self._semantic_scores(store, query, results, seen_ids,
                                                      report=seeding_report)
        # ONE candidate per fact: a physical twin never competes with its logical node
        # for a seed slot (see _collapse_layer_twins).
        results, _twins = _collapse_layer_twins(results, semantic_scores)
        _hidden = await _unpublished_names_of(store)
        results = [e for e in results if not _unpublished_authored(e, _hidden)]   # §2b: a draft never seeds
        if seeding_report is not None and _twins:
            seeding_report["twins_collapsed"] = _twins
        ranked = _rank_seeds(results, query, limit=max(_SEED_LIMIT_FLOOR, int(seed_limit)),
                             usage_priors=usage_priors,
                             semantic_scores=semantic_scores, products=_declared_products(store))

        # ALWAYS seed the graph's signed data products, whatever the question said.
        #
        # A product is named for its SUBJECT AREA ("support_load"); a question names
        # entities and columns ("how many customers do we have?"). So a product loses
        # the lexical match essentially every time — and unlike an ordinary node, an
        # absent product is not a missing fact, it is a missing OPTION: the planner
        # cannot choose the governed lane it was never shown, so it writes raw SQL and
        # the whole product layer silently never engages. Observed exactly that: a
        # question `customer_count` answers precisely, planned as
        # `SELECT COUNT(CustomerID) FROM fitness_db.Customers` with the product absent
        # from the enrichment entirely.
        #
        # Stronger than the _RESERVED_SEED_TYPES rescue beside it, and deliberately so:
        # that one requires the query to have MATCHED the node. Products are few and
        # bounded, each renders as one contract line, and being preferred is their
        # entire purpose. Fails soft — a product layer that cannot be read must never
        # cost the turn its ordinary enrichment.
        try:
            # the seed follows the PROFILE's grant: Option A seeds nothing,
            # B/C seed the LISTED products only (the list is the grant).
            # (threaded as a PARAMETER — this function has no profile in scope,
            # and a resolver call here raised a NameError the fail-open guard
            # would have swallowed into a silently dead seed)
            _sm, _sp = obda_surface if obda_surface is not None else ("off", frozenset())
            products = (await store.list_entities(entity_type="data_product")
                        if _sm != "off" else [])
            if products and _sp:
                products = [p for p in products if str(p.get("name")) in _sp]
            if products:
                have = {e.get("id") for e in ranked}
                _head = [p for p in products[:_MAX_SEEDED_PRODUCTS] if p.get("id") not in have]
                ranked = _head + ranked
                if seeding_report is not None:
                    seeding_report["products_seeded"] = len(_head)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"KG enrichment: could not seed data products ({exc})")

        # (the master-off product exclusion lives at SUBGRAPH ASSEMBLY in
        #  get_context_enrichment — one seam covering every extraction path;
        #  a ranked-level filter here was bypassed by the adaptive extractor)

        return ranked

    def _format_subgraph_for_prompt(self, subgraph: Dict[str, List],
                                    capabilities: Optional[Dict[str, Any]] = None,
                                    surface=None, query: str = "") -> str:
        """Format subgraph as structured text for LLM context injection.

        ``capabilities`` (logical_name → EntityCapabilities) carries the
        deterministically-resolved DEPLOYED identifiers so the enrichment is
        capability-aware: it states *what is true and what can be done*, leaving
        each engine to self-direct. When empty (logical-only / undeployed graph)
        the output is exactly as before — additive, never required.
        """
        if not subgraph["entities"]:
            return ""
        capabilities = capabilities or {}

        # Resolved ONCE for the whole render: an EXCLUSIVE surface changes what may
        # honestly be advertised. Even the capabilities preamble is bait there —
        # `compile_metric` SQL is NOT in the governed registry (only query_product
        # registers), so a surface-only agent that followed that hint would compile
        # a statement it is then denied from executing.
        # `surface`: the PROFILE-mode surface threaded by get_context_enrichment —
        # (products, exclusive) for Options B/C, the string "off" for Option A,
        # None (legacy callers/tests) falls back to the ambient principal.
        if surface == "off":
            _surface_set, _surface_exclusive = None, False
        elif surface is not None:
            _surface_set, _surface_exclusive = surface
        else:
            _surface_set, _surface_exclusive = _surface_for_turn()
        lines = ["--- KNOWLEDGE GRAPH CONTEXT ---"]
        if not _surface_exclusive:
            lines.append(
                "The following known entities and relationships may inform your planning:")
            if capabilities:
                # The compile_metric invitation renders ONLY when the subgraph
                # actually carries a metric node. On a metric-less graph the
                # unconditional invitation lured the planner into
                # compile_metric('Revenue') — a metric that does not exist —
                # and a full self-correction detour (session dd40d071).
                _has_metric = any(isinstance(e, dict)
                                  and e.get("entity_type") == "metric"
                                  for e in subgraph.get("entities") or [])
                _metric_note = (
                    "; a metric marked 'compilable' can be turned into "
                    "ratio-safe SQL via TDA_KnowledgeGraph action 'compile_metric'"
                    if _has_metric else "")
                lines.append(
                    "(An entity marked 'deployed: <name>' has that real, queryable "
                    f"identifier{_metric_note}.)"
                )

        # OBDA-READ Phase C — signed data products render as CONSUMPTION CONTRACTS, not bare
        # entity names.  The live C4 acceptance showed why: with the product listed as a
        # plain node, the planner planned raw SQL "to query the data product" — the one
        # thing a surface-only agent cannot do.  The instruction the planner needs is
        # deterministic (Component → LLM priority 1), so it is rendered in code here,
        # bounded, and only when a product node is actually in the subgraph (additive).
        _render_surface = (None if surface is None
                           else ((_surface_set, _surface_exclusive)
                                 if surface != "off" else "off"))
        _products = ([] if (_render_surface == "off" or not _obda_enabled())
                     else _rendered_products(subgraph["entities"],
                                             surface=(_render_surface
                                                      if _render_surface is not None
                                                      else None)))
        _off_surface = ([] if (_render_surface == "off" or not _obda_enabled())
                        else _off_surface_product_names(subgraph["entities"],
                                                        surface=(_render_surface
                                                                 if _render_surface is not None
                                                                 else None)))
        _rendered_names = {e.get("name") for e in _products}
        if _products:
            if _surface_exclusive:
                lines.append(
                    "\nSIGNED DATA PRODUCTS — THIS AGENT IS SURFACE-ONLY: the products "
                    "below are its ENTIRE data authority; raw SQL will be denied. Answer "
                    "from a declared measure via TDA_DataProduct action 'query_product', "
                    "then execute the returned statement exactly as given:")
            else:
                lines.append(
                # The preference has to be stated against the ALTERNATIVE, because the
                # same enrichment also shows the underlying tables and columns — the
                # obvious route. Observed: with the contract present, "how many
                # customers do we have?" was still planned as
                # `SELECT COUNT(CustomerID) FROM fitness_db.Customers` because nothing
                # said the declared measure OUTRANKS the table it is built on.
                "\nSIGNED DATA PRODUCTS (governed consumption contracts). If a declared "
                "measure below answers the question, you MUST use TDA_DataProduct "
                "action 'query_product' and then execute the statement it returns, "
                "exactly as given. Do NOT hand-write SQL over the same tables — the "
                "schemas shown elsewhere in this context are NOT an alternative route "
                "to a measure a product already declares:")
            for e in _products:
                props = e.get("properties") or {}
                man = props.get("manifest") if isinstance(props.get("manifest"), dict) else {}
                parts = []
                if props.get("description") or man.get("description"):
                    parts.append(str(props.get("description") or man.get("description")))
                # The ADMISSIBLE NAMES, which the planner cannot otherwise know.
                # A manifest stores `entities`/`measures` as LISTS of objects; the
                # first version of this block tested `isinstance(..., dict)`, so both
                # were silently never rendered and the contract read
                # "- support_load: <description>; validated" — no vocabulary at all.
                # The model then guessed a column name on every single turn
                # (`count`, `CustomerID`, `TicketID`, `ServiceTickets.TicketID` were all
                # observed), earned a REJECT, and self-corrected — paying an LLM round
                # trip to be told something that was deterministically known before the
                # turn began. Naming them here is Component → LLM priority 1.
                ents = _member_names(man.get("entities"))
                meas = _member_names(man.get("measures"))
                dims = _dimension_names(man.get("entities"))
                if meas:
                    parts.append("measures: " + ", ".join(meas[:10]))
                if dims:
                    parts.append("dimensions: " + ", ".join(dims[:14]))
                elif ents:
                    parts.append("entities: " + ", ".join(ents[:8]))
                parts.append("validated" if props.get("validation_report")
                             else "NOT VALIDATED (may be refused)")
                pname = man.get("product") or str(e.get("name", "")).rpartition(".")[2]
                lines.append(f"  - {pname}: {'; '.join(parts)}")
            # THE SUBSTITUTION RULE, stated where the decision is made (plan time).
            # Session 7b9b5a25: "which customer has the most tickets?" was planned
            # straight onto customer_count × Customers.State — an admissible,
            # governed answer to a DIFFERENT question, with no REJECT for any
            # runtime gate to see. The REJECT tail already states this rule, but a
            # planner that never attempts the un-exposed member never reads a
            # REJECT; the contract itself must carry it.
            lines.append(
                "  These are the ONLY members. Answer at the grain the question "
                "actually asks: if the question asks for something not listed above "
                "(e.g. an individual customer when only State/City are declared), "
                "the product CANNOT answer it — say so explicitly, or answer with "
                "an admissible member while stating clearly that it answers a "
                "coarser question. NEVER silently answer at a different grain or "
                "with a different measure and present it as the asked answer.")
        if _off_surface:
            # Named, never silently dropped: a product that simply vanishes is
            # indistinguishable from one that does not exist, and the agent would go
            # looking for it by other means (observed: it picked an off-surface
            # product on a lexical cue and burned the turn on the denial).
            lines.append(
                "  NOT on this agent's data surface (query_product will refuse them): "
                + ", ".join(_off_surface))

        # THE OPERATOR'S CUT (walkthrough finding): under an EXCLUSIVE surface the
        # rest of the enrichment — TABLE SCHEMAS, joinable columns, entity listings,
        # coded columns — exists solely to help an agent WRITE SQL, and this agent
        # cannot execute SQL it writes. Rendering it is not transparency, it is bait:
        # with the schemas present the planner kept reaching for
        # `SELECT COUNT(*) FROM Customers` even with a one-door contract in front of
        # it (raw-SQL patterns outweigh any instruction; proven twice on identical
        # context, once each way). So the enrichment for a surface-only turn is the
        # contract ALONE, plus one honest line saying what was withheld and why.
        # The EVENT metadata (scope_databases, scope_entities — the grounding gate's
        # trusted scope) is computed from the FULL subgraph by the caller and is
        # deliberately unaffected by this text cut.
        if _surface_exclusive:
            lines.append(
                "  (Schema details are withheld: this agent cannot execute raw SQL, "
                "so table and column listings do not apply. Everything answerable is "
                "declared above.)")
            return "\n".join(lines)

        # Build entity→database lookup from 'contains' relationships
        # Pattern: database --[contains]--> entity --[contains]--> attribute
        entity_db_map: Dict[str, str] = {}
        db_entities = {e["name"] for e in subgraph["entities"] if e["entity_type"] == "database"}
        table_entities = {e["name"] for e in subgraph["entities"] if e["entity_type"] == "entity"}
        table_props = {e["name"]: (e.get("properties") or {})
                       for e in subgraph["entities"] if e["entity_type"] == "entity"}
        column_entity_map = {e["name"]: e for e in subgraph["entities"] if e["entity_type"] == "attribute"}

        for rel in subgraph.get("relationships", []):
            if rel["relationship_type"] == "contains" and rel["source_name"] in db_entities:
                entity_db_map[rel["target_name"]] = rel["source_name"]
        # Propagate: if a table is in a database, its columns are too
        table_db = dict(entity_db_map)
        for rel in subgraph.get("relationships", []):
            if rel["relationship_type"] == "contains" and rel["source_name"] in table_db:
                entity_db_map[rel["target_name"]] = table_db[rel["source_name"]]

        # Build table→columns mapping from contains relationships
        # table_columns: {table_name: [(col_name, col_type), ...]}
        table_columns: Dict[str, List[tuple]] = {}
        table_col_contains = set()  # Track (table, column) pairs to filter from relationships
        for rel in subgraph.get("relationships", []):
            if (rel["relationship_type"] == "contains"
                    and rel["source_name"] in table_entities
                    and rel["target_name"] in column_entity_map):
                tbl = rel["source_name"]
                col_name = rel["target_name"]
                col_entity = column_entity_map[col_name]
                col_type = (col_entity.get("properties", {}).get("CType", "")
                            or col_entity.get("properties", {}).get("data_type", ""))
                if tbl not in table_columns:
                    table_columns[tbl] = []
                table_columns[tbl].append((col_name, col_type))
                table_col_contains.add((rel["source_name"], rel["target_name"]))

        # Emit TABLE SCHEMAS section if we have table-column data
        if table_columns:
            lines.append("\nTABLE SCHEMAS (use these to validate SQL column references):")
            for tbl in sorted(table_columns.keys()):
                db_name = entity_db_map.get(tbl, "")
                cols = table_columns[tbl]
                cap = capabilities.get(tbl)
                _dep = cap.deployed_as if (cap and cap.deployed_as) else ""
                # A column entity is named `<table entity>.<column>` — for an industry
                # model `production.routing_operation.sequence_number`.  Rendered whole,
                # thirty columns repeat `production.routing_operation.` thirty times against
                # ONE `[deployed: …]` tag, and a model copies the repeated prefix into its
                # FROM clause (Error 3802 on seven of eleven validation turns).  The line
                # shows the columns as SQL needs them — bare — and names the table as the
                # deployed identifier, keeping the logical name as a label.
                _leaf = (lambda c: c[len(tbl) + 1:] if c.startswith(tbl + ".") else c)
                col_strs = [f"{_leaf(c)}({t})" if t else _leaf(c) for c, t in cols]
                db_prefix = f"{db_name}." if (db_name and not _dep) else ""
                # `_dep or tbl`: with nothing deployed the table's identity IS its
                # logical name, and a consumable naming the thing we are already
                # talking about adds nothing.
                _qobj = _preferred_query_object(table_props.get(tbl) or {}, _dep or tbl)
                _bits = ([f"logical: {tbl}"] if _dep else []) \
                    + ([f"query: {_qobj}"] if _qobj else [])
                _sep = " \u00b7 "  # a backslash inside an f-string expression is 3.12-only (PEP 701)
                deployed = f"  [{_sep.join(_bits)}]" if _bits else ""
                _shown_tbl = _dep or tbl   # the identifier to write in SQL leads the line
                _more = (subgraph.get("omitted_columns") or {}).get(tbl)
                _more_s = f"  (+{_more} more columns not shown — ask to expand)" if _more else ""
                # The entity's declared natural key. The walker has always carried it
                # (`business_key`, from the product's `natural_key_column`) and the
                # prompt never showed it — so a segment table keyed on (ORI, INCNUM)
                # read as if INCNUM identified a row, and every cross-segment join was
                # written one column short (rows multiplied; a 48 s join ran >300 s).
                _key = _key_columns(table_props.get(tbl) or {})
                _key_s = f"  [key: {', '.join(_key)}]" if _key else ""
                # Validated against the source at construction (optional): the real row
                # count, and whether the declared key actually identifies a row — a key
                # that does not (INCNUM alone: 12.35M rows, 7.48M distinct) is the
                # runaway-join class stated before the join is written.
                _tp = table_props.get(tbl) or {}
                _val_s = ""
                if isinstance(_tp.get("row_count"), (int, float)):
                    _val_s += f"  [rows: {int(_tp['row_count']):,}]"
                if _tp.get("key_unique") is True and _key:
                    _val_s += "  [key verified unique]"
                elif _tp.get("key_unique") is False and _key:
                    _dup = _tp.get("key_duplicate_groups")
                    _val_s += (f"  [key NOT unique — {int(_dup):,} duplicate groups; do not treat it as a row identifier]"
                               if isinstance(_dup, (int, float)) else "  [key NOT unique]")
                lines.append(f"  {db_prefix}{_shown_tbl}{deployed}{_key_s}{_val_s}: {', '.join(col_strs)}{_more_s}")

            # REPEATING GROUPS — one fact stored once per slot ("LOCATION TYPE - 1/2/3").
            # Declared by the walker on every member (`family`, `family_members`,
            # `family_axes`); rendered ONCE per family in play so the agent sees the
            # family as a family — which members exist, how many axes — instead of
            # meeting "- 1" and never learning the others exist (measured: 140,609 for
            # 145,193). A graph whose walker declared no families renders nothing here.
            # ENTRY IS RELEVANCE (the coded block's rule): a family earns its line by a
            # member that matched the question — on a table where nearly every column
            # belongs to some family, rendering every family in the subgraph is the
            # whole codebook again (measured: +1,900 chars a question for nothing).
            _fam_terms = _scoring_terms(query or "")
            _fams: Dict[tuple, Dict] = {}
            for e in subgraph["entities"]:
                if e.get("entity_type") not in ("attribute", "column"):
                    continue
                pr = e.get("properties") or {}
                if not pr.get("family") or not pr.get("family_members"):
                    continue
                # concept evidence, not the ranking score: the ranking's OWNER channel
                # makes every column of a named entity "match", and with it every family
                if not _fam_terms or not any(_concept_evidence(e, t) > 0 for t in _fam_terms):
                    continue
                owner = str(e.get("name", "")).rpartition(".")[0].rpartition(".")[2]
                _fams.setdefault((owner, str(pr["family"])), pr)
            if _fams:
                lines.append("\nREPEATING GROUPS (one fact stored once per slot — a question about \"any\" slot needs every member):")
                for (owner, fam), pr in sorted(_fams.items())[:_MAX_FAMILIES]:
                    mem = [str(m) for m in (pr.get("family_members") or [])]
                    axes = int(pr.get("family_axes") or 1)
                    span = f"{mem[0]}…{mem[-1]}" if len(mem) > 4 else ", ".join(mem)
                    ax = " (two indices: position, then slot)" if axes == 2 else ""
                    lines.append(f"  {owner}: {fam.upper()} = {span} [{len(mem)} columns{ax}]: {', '.join(mem)}")

            # Build joinable columns: columns appearing in 2+ tables
            # Joinability is by column LEAF: on a logical/physical-layered graph the
            # attribute name is `Table.Column`, so a full-name comparison never
            # matched and this block was silently dead (no JOIN keys were ever shown).
            col_tables: Dict[str, List[str]] = {}
            # per (table, leaf): is this column a FOREIGN key here (and not the pk)?
            _fk_here: Dict[tuple, bool] = {}
            for tbl, cols in table_columns.items():
                for col_name, _ in cols:
                    leaf = str(col_name).rpartition(".")[2]
                    if leaf not in col_tables:
                        col_tables[leaf] = []
                    col_tables[leaf].append(tbl)
                    _cp = (column_entity_map.get(col_name) or {}).get("properties") or {}
                    _fk_here[(tbl, leaf)] = bool(
                        not _cp.get("is_primary_key")
                        and (_cp.get("is_foreign_key")
                             or "foreign key" in str(_cp.get("semantic_type") or "").lower()))
            joinable = {c: tbls for c, tbls in col_tables.items() if len(tbls) > 1}
            # A column that is a FOREIGN key in EVERY table sharing it points at a third
            # table (the dimension both reference). Joining the two on it is fact-to-fact
            # through a dimension key — the classic fan-out. Prod session 0aa3a650: the
            # hint `company_code_id: accounts_receivable, ffp_member` produced five
            # customers with the identical total. Where one table holds the column as
            # its pk the hint is the legitimate join and stays.
            fanout = {c: tbls for c, tbls in joinable.items()
                      if len(tbls) >= 2 and all(_fk_here.get((t, c)) for t in tbls)}
            joinable = {c: tbls for c, tbls in joinable.items() if c not in fanout}

            # DECLARED foreign keys — a real key the constructor recorded, not a name
            # coincidence. Prefer these: two `ID` columns sharing a name is a guess, a
            # `foreign_key` edge is a constraint. Rendered as its own block so the
            # planner joins on verified keys first (leaf-keyed to line up with the
            # name-based joinables below).
            _tbl_leaf = {t.rpartition(".")[2].lower(): t for t in table_columns}
            declared_fk: Dict[str, set] = {}
            declared_pairs: set = set()
            for rel in subgraph.get("relationships", []):
                if rel.get("relationship_type") != "foreign_key":
                    continue
                sn, tn = str(rel.get("source_name") or ""), str(rel.get("target_name") or "")
                s_leaf, t_leaf = sn.rpartition(".")[2], tn.rpartition(".")[2]
                # column↔column FK (leaves are the join columns) or entity↔entity FK
                if (rel.get("source_type") in ("attribute", "column")
                        and rel.get("target_type") in ("attribute", "column")):
                    s_tbl, t_tbl = _tbl_leaf.get(sn.rpartition(".")[0].rpartition(".")[2].lower()), _tbl_leaf.get(tn.rpartition(".")[0].rpartition(".")[2].lower())
                    for leaf, tbl in ((s_leaf, s_tbl), (t_leaf, t_tbl)):
                        if tbl:
                            declared_fk.setdefault(leaf, set()).add(tbl)
                    if s_tbl and t_tbl:
                        declared_pairs.add(frozenset((s_tbl, t_tbl)))
                else:
                    s_tbl, t_tbl = _tbl_leaf.get(s_leaf.lower()), _tbl_leaf.get(t_leaf.lower())
                    if s_tbl and t_tbl:
                        declared_pairs.add(frozenset((s_tbl, t_tbl)))
            declared_fk = {c: t for c, t in declared_fk.items() if len(t) > 1}
            # What the product SAID about the join, per table pair — a relationship
            # row can name only one column per side, so a composite key lives in its
            # free text ("join also on ORI"). Carried, never dropped.
            fk_notes: Dict[frozenset, set] = {}
            for rel in subgraph.get("relationships", []):
                if rel.get("relationship_type") != "foreign_key":
                    continue
                _d = str((rel.get("properties") or {}).get("description") or rel.get("description") or "").strip()
                if not _d:
                    continue
                sn, tn = str(rel.get("source_name") or ""), str(rel.get("target_name") or "")
                s_t = _tbl_leaf.get((sn.rpartition(".")[0] if rel.get("source_type") in ("attribute", "column") else sn).rpartition(".")[2].lower())
                t_t = _tbl_leaf.get((tn.rpartition(".")[0] if rel.get("target_type") in ("attribute", "column") else tn).rpartition(".")[2].lower())
                if s_t and t_t:
                    fk_notes.setdefault(frozenset((s_t, t_t)), set()).add(_d[:160])
            if declared_fk or declared_pairs:
                lines.append("\nDECLARED FOREIGN KEYS (declared join columns — prefer these over name matches, "
                             "but join on the WHOLE key where a table's key is wider):")
                for col, tbls in sorted(declared_fk.items()):
                    lines.append(f"  {col}: {', '.join(sorted(tbls))}")
                    # A declared single column that is NOT the whole key of the tables it
                    # joins multiplies rows: say so, name the full key, on the same line
                    # the agent reads the join from.
                    _wider = {}
                    for _t in tbls:
                        _k = _key_columns(table_props.get(_t) or {})
                        if len(_k) > 1 and col.lower() in {k.lower() for k in _k}:
                            _wider[_t] = _k
                    if _wider:
                        _ex = sorted(_wider.items())[0]
                        lines.append(f"    NOT the whole key — {_ex[0]} is keyed on ({', '.join(_ex[1])}): "
                                     f"join on every key column, or rows multiply")
                # A pair whose column-level key edge is out of view (it lives on the
                # physical layer) still needs its JOIN COLUMNS named: the agent that
                # reads "FK declared between these tables" and no column guesses one
                # — on the policing graph it guessed BH010/BH012 and ran for hours.
                # The tables' declared keys carry the answer: the columns they share.
                # ONE computation (`_declared_join_pairs`) feeds this block AND the
                # `join_keys` metadata the preflight guard enforces.
                for _jp in _declared_join_pairs(subgraph):
                    _ta, _tb = _jp["tables"]
                    pair = f"{_ta} ⇄ {_tb}"
                    _notes = fk_notes.get(frozenset((_ta, _tb)), set())
                    _note_s = f" — {'; '.join(sorted(_notes))}" if _notes else ""
                    _ka, _kb = _jp["keys"][_ta], _jp["keys"][_tb]
                    _shared = _jp["key"]
                    if _shared:
                        _wide = [(t, k) for t, k in ((_ta, _ka), (_tb, _kb)) if len(k) > len(_shared)]
                        _w = ("; " + "; ".join(f"{t} is keyed on ({', '.join(k)}) — the extra column(s) do not join" for t, k in _wide)) if _wide else ""
                        _key_s = f" — join on ({', '.join(_shared)}){_w}"
                    elif _ka or _kb:
                        _key_s = f" — keys: {_ta} ({', '.join(_ka) or '?'}), {_tb} ({', '.join(_kb) or '?'})"
                    elif _jp.get("edges"):
                        # the declared edge(s) name the columns even when no single
                        # shared key resolved (differing names, or several FKs between
                        # the same two tables — join on ONE of them, never on both)
                        _alts = " OR ".join(f"{_ta}.{ca} = {_tb}.{cb}" for ca, cb in _jp["edges"])
                        _key_s = f" — join on {_alts}" if len(_jp["edges"]) == 1 else f" — join on ONE of: {_alts}"
                    else:
                        _key_s = ""
                    lines.append(f"  {pair} (FK declared between these tables){_key_s}{_note_s}")

            # A column shared by NAME between tables that already have a declared key
            # between them is not a hint, it is a trap: on the policing graph the agent
            # joined Incident to Arrestee on BH010/BH012 (batch-header fields both tables
            # carry) beside the declared (ORI, INCNUM), and the near-cartesian product ran
            # for three hours. Name those columns as NOT keys; keep the hint only where at
            # least one table pair has no declared key to fall back on.
            from itertools import combinations as _combos
            hint_cols, not_keys = {}, {}
            for col, tbls in joinable.items():
                if col in declared_fk:
                    hint_cols[col] = tbls
                    continue
                pairs = [frozenset(p) for p in _combos(sorted(tbls), 2)]
                if pairs and all(p in declared_pairs for p in pairs):
                    not_keys[col] = tbls
                else:
                    hint_cols[col] = tbls
            if hint_cols:
                lines.append("\nJOINABLE COLUMNS (shared column NAMES — a hint, NOT a verified key; confirm against DECLARED FOREIGN KEYS):")
                for col, tbls in sorted(hint_cols.items()):
                    _decl = " [declared FK]" if col in declared_fk else ""
                    lines.append(f"  {col}: {', '.join(sorted(tbls))}{_decl}")
            if not_keys:
                _nk = sorted(not_keys)
                _shown = ", ".join(_nk[:12]) + (f" (+{len(_nk) - 12} more)" if len(_nk) > 12 else "")
                lines.append(f"\nNOT JOIN KEYS: {_shown} — shared by name only between tables whose "
                             f"join key is DECLARED above; joining on them multiplies rows without bound. "
                             f"Join those tables on the declared key.")
            # …and the DECLARED form of the same trap: a key two tables both hold as a
            # foreign key to a THIRD table. `ffp_member_id: ffp_member, mileage_redemption,
            # member_benefit [declared FK]` invites mileage_redemption ⋈ member_benefit —
            # every redemption row against every benefit row of the same member. Each
            # holder joins to the OWNER; the holders never join each other on it.
            for _f in _fanout_keys(subgraph):
                _hold = [t for t in _f["table_names"] if t in table_columns]
                if len(_hold) >= 2 and _f["column"].lower() not in {c.lower() for c in fanout}:
                    fanout[_f["column"] + (f" (owner: {_f['owner_name']})" if _f.get("owner_name") else "")] = _hold
            if fanout:
                # the rule ONCE, then one line per column — a paragraph per column was
                # eight paragraphs on a follow-up turn, most of the join section's tokens
                _fo = sorted(fanout.items())
                lines.append("\nNOT JOIN KEYS (fan-out) — a FOREIGN key in EVERY table listed, all pointing at the "
                             "owner table; joining two of the listed tables to EACH OTHER on it multiplies rows (every "
                             "row of one against every row of the other sharing the value — a per-member total becomes "
                             "hundreds of times too large). Join each listed table to the OWNER, or aggregate each "
                             "separately per key and join the aggregates:")
                for c, tbls in _fo[:12]:
                    lines.append(f"  {c}: {', '.join(sorted(tbls))}")
                if len(_fo) > 12:
                    lines.append(f"  (+{len(_fo) - 12} more)")

            # Decoded values for the coded columns in play.  The TABLE SCHEMAS block
            # above is name(type) only, so without this a coded column reads as an
            # opaque identifier and the agent invents a literal for it.
            # Ordered by relevance to the QUESTION, not by subgraph arrival, and each
            # line carries the description that DISCRIMINATES it from its neighbours.
            #
            # Both halves were load-bearing and both were missing. On "how many victims
            # are female?" this block rendered five indistinguishable `'0'=Female`
            # columns — Victim.V4019 (SEX OF VICTIM), Victim.V60152 (SEX OF ARRESTEE-2),
            # Victim.V50083, Victim.V60153, Arrestee.V50082 — as bare `Table.Column:
            # values: ...` lines with no label. The seeder had ranked V4019 FIRST (150 vs
            # 145), so retrieval was correct; the render threw that ordering away and
            # dropped the one field that tells the columns apart, and the model answered
            # 16,110 (arrestee sex) instead of 477,466. A near-miss is worse than a blank.
            #
            # Ordering also makes the _MAX_CODED_COLUMNS cap SAFE: an arbitrary-order cut
            # can silently drop the column that answers the question (V4019 sat 20th).
            # ENTRY IS RELEVANCE, NOT GOVERNANCE. Gating on `_governance_parts` selected
            # for governance RISK when this block's job is DISCRIMINATION — telling the
            # agent which column answers the question. A column with no decoded values,
            # no PII flag and a public classification was skipped however well it matched,
            # so its description never reached the planner at all. Measured across the
            # graphs on the reference deployment: on a 15,226-column industry-model graph
            # EVERY column is documented and NONE was eligible, so the block rendered
            # empty and the agent planned against `name(type)` alone; on the ANDP policing
            # graph 364 documented columns were invisible beside the coded ones — among
            # them the plain-text state abbreviation, while the coded state column that
            # misled the agent (its values look like FIPS and are not) rendered in full.
            # The safe column was less visible than the trap.
            #
            # Governance facts are now CONTENT on a line the column earned by matching,
            # and they still win a tie so a coded column that matches equally leads — the
            # ordering the V4019 fix depends on. `_MAX_CODED_COLUMNS` still bounds the
            # block, which is what makes admitting 15,226 candidates safe.
            _coded_terms = _scoring_terms(query or "")
            _cands = []
            _fam_seen: set = set()
            for _i, e in enumerate(subgraph["entities"]):
                if e.get("entity_type") not in ("attribute", "column"):
                    continue
                props = e.get("properties") or {}
                # a family's members share their codes and description: one line, the
                # family's, stands for all of them (the REPEATING GROUPS block lists the members)
                _fam = props.get("family")
                if _fam:
                    _fkey = (str(e.get("name", "")).rpartition(".")[0], str(_fam))
                    if _fkey in _fam_seen:
                        continue
                    _fam_seen.add(_fkey)
                extra = _governance_parts(props, terms=_coded_terms)
                body = _coded_line(props, extra)
                if not body:
                    continue          # neither facts nor a description — nothing to say
                label = props.get("label") or str(e.get("name", "")).rpartition(".")[2]
                owner = str(e.get("name", "")).rpartition(".")[0].rpartition(".")[2]
                _cands.append((-_match_score(e, _coded_terms) if _coded_terms else 0,
                               0 if extra else 1, _i, f"  {owner}.{label}: {body}"))
            _cands.sort(key=lambda r: (r[0], r[1], r[2]))
            coded: List[str] = [c[3] for c in _cands[:_MAX_CODED_COLUMNS]]
            if coded:
                lines.append("\n" + RELEVANT_COLUMNS_HEADING + ":")
                lines.extend(coded)

        # Objects a product_bridge declares an UNVERIFIED link through, for the recipe
        # cross-reference below. Product-neutral: read from whatever the bridge nodes
        # declare (`declared_in`, `target_reference`); a graph with no bridges, or none
        # unresolved, yields an empty map and every recipe renders exactly as before.
        _unverified_link_objects: Dict[str, str] = {}
        for _b in subgraph["entities"]:
            if _b.get("entity_type") != "product_bridge":
                continue
            _bp = _b.get("properties") or {}
            if _bp.get("target_resolved") is not False:
                continue
            _why = "; ".join(str(x) for x in (_bp.get("linkage_mechanism"),
                                              _bp.get("resolution")) if x)
            for _key in ("declared_in", "target_reference"):
                _tok = str(_bp.get(_key) or "").strip().lower()
                if len(_tok) >= 4:            # too short to match on without false hits
                    _unverified_link_objects.setdefault(_tok, _why)

        # The question's terms, resolved ONCE for the whole render — the same walk the
        # seeder ranks by, so "the question reached this metric" means the same thing in
        # both places. Empty when the enrichment was built without a question.
        _metric_terms = _scoring_terms(query) if query else []
        _metrics_named = _metrics_carrying_names(subgraph.get("entities"), _metric_terms)

        # Group entities by type — skip columns (already in TABLE SCHEMAS)
        by_type: Dict[str, List] = {}
        for entity in subgraph["entities"]:
            etype = entity["entity_type"]
            if etype == "attribute" and table_columns:
                continue  # Already represented in TABLE SCHEMAS
            # Same reasoning, one layer up: a product already rendered as a CONSUMPTION
            # CONTRACT above must not appear again as a bare entity. The second, poorer
            # entry is precisely what made the planner treat a product as an ordinary
            # table and plan raw SQL "to query the data product" — the C4 defect the
            # contract block exists to prevent. Only the ones actually rendered are
            # skipped, so a product beyond the bound is still named somewhere.
            if etype == "data_product":
                # Every product is already handled above — as a consumption contract
                # or on the off-surface line. A bare-entity rendering is the poorer
                # second entry that made the planner treat a product as an ordinary
                # table (the C4 defect); an off-surface one rendered here would offer
                # a door the runtime will slam.
                continue
            if etype not in by_type:
                by_type[etype] = []
            by_type[etype].append(entity)

        for etype, entities in sorted(by_type.items()):
            if etype == "metric":
                _any_compilable = any(getattr(capabilities.get(e["name"]), "compilable", False) for e in entities)
                # The names clause promises "the NAMES listed on its line" — say it only
                # when a line actually carries them, or it points at something absent.
                _any_named = any(e.get("name") in _metrics_named
                                 and getattr(capabilities.get(e["name"]), "compilable", False)
                                 for e in entities)
                lines.append("\nMETRIC ENTITIES (authoritative definitions — when the question asks for "
                             "one of these measures, compute it EXACTLY as its formula states"
                             + (("; a metric marked 'compilable' can be compiled via action 'compile_metric'"
                                 + (" using the measure/dimension NAMES listed on its line, exactly as "
                                    "written — a column name is not a measure name" if _any_named else ""))
                                if _any_compilable else
                                " by writing SQL that implements the formula over its source table(s), "
                                "joining through the JOINABLE COLUMNS listed above — these metrics are NOT "
                                "compilable, do not call compile_metric on them")
                             + "; never substitute another arithmetic):")
            elif etype == "query_recipe":
                lines.append("\nPUBLISHED QUERIES (the product's OWN recipes for business questions — "
                             "prefer adapting one over composing SQL from scratch, and say when you did. "
                             "A recipe is the product's claim, not a verified result: any CAUTION on a "
                             "recipe below, and every caveat stated elsewhere in this context, still "
                             "applies to it):")
            else:
                _qualified = etype == "entity" and any(
                    "." in e.get("name", "") and capabilities.get(e["name"]) is not None
                    and getattr(capabilities.get(e["name"]), "deployed_as", None) for e in entities)
                lines.append(f"\n{etype.upper()} ENTITIES"
                             + (" (a name written <domain>.<table> is a logical grouping — the domain "
                                "is NOT a database; in SQL use the 'deployed:' identifier):"
                                if _qualified else ":"))
            for e in entities:
                props = e.get("properties", {})
                desc = props.get("description", "")
                biz = props.get("business_meaning", "")
                dtype = props.get("data_type", "") or props.get("CType", "")
                db_name = entity_db_map.get(e["name"], "")

                line = f"  - {e['name']}"
                parts = []
                if desc:
                    parts.append(desc)
                # A published recipe's VALUE is its statement. Without it the node
                # renders as its own question restated — the agent is told a proven
                # query exists and not what it is, which is worse than silence: it
                # invites composing from scratch while a working statement sits in
                # the graph. Measured on the policing CASE product: the recipe that
                # answers the chain-of-custody question ranked FIRST among seeds and
                # the agent still ground through 20 `SELECT *` probes and gave up.
                if props.get("sql"):
                    _sql = " ".join(str(props["sql"]).split())
                    # A recipe that joins through a link the product itself declares
                    # UNVERIFIED must carry that caveat, or rendering it silently
                    # promotes a self-declared synthetic key to a working join. Found
                    # live: surfacing recipes made an agent that had correctly REFUSED
                    # this join start performing it, because the product publishes a
                    # "Join CASE Evidence to NIBRS Incidents via Crosswalk" recipe and
                    # the caveat lived on a different node. The bridge and the recipe
                    # are two halves of one fact; they must arrive together.
                    _low = _sql.lower()
                    for _tok, _caveat in _unverified_link_objects.items():
                        if _tok in _low:
                            parts.append(f"CAUTION: joins through '{_tok}', which this "
                                         f"product declares an UNVERIFIED link ({_caveat}) "
                                         f"— do not present its results as a join")
                            break
                    if len(_sql) > _RECIPE_SQL_CHARS:
                        _sql = _sql[:_RECIPE_SQL_CHARS].rsplit(" ", 1)[0] + " \u2026"
                    parts.append(f"SQL: {_sql}")
                if props.get("target_module"):
                    parts.append(f"queries: {props['target_module']}")
                if etype == "metric" and props.get("formula"):
                    # The ontology's OWN definition of the measure. Provenance is stated:
                    # a formula a model wrote at build time or added mid-turn is never
                    # rendered as if a human validated it — the planner still gets the
                    # formula (it beats guessing), and the reader knows what it rests on.
                    _prov = _formula_provenance(e)
                    parts.append(f"formula: {props.get('formula')}" + (f" ({_prov})" if _prov else ""))
                if dtype:
                    parts.append(f"type: {dtype}")
                if db_name:
                    parts.append(f"database: {db_name}")
                if biz:
                    parts.append(f"business: {biz}")
                # Deterministically-resolved deployment + capability (metrics/tables).
                cap = capabilities.get(e["name"])
                if cap:
                    if cap.deployed_as:
                        parts.append(f"deployed: {cap.deployed_as}")
                    if cap.metric_view:
                        parts.append(f"deployed view: {cap.metric_view}")
                    if getattr(cap, "metric_column", None):
                        parts.append(f"deployed column: {cap.metric_column} (read directly)")
                    if cap.metric_base_table:
                        parts.append(f"base table: {cap.metric_base_table}")
                    if cap.compilable:
                        parts.append("compilable")
                        # The contract of compile_metric IS these names; without them
                        # the model guesses a column (prod d9a60903: 'fi_product_revenue'
                        # on a metric whose measures are display names → a dead-end
                        # refusal and two wasted tool rounds). Gated on the question
                        # having reached this metric — see _metric_names_relevant.
                        if e.get("name") in _metrics_named:
                            _mn = _bounded_names(getattr(cap, "metric_measures", None),
                                                 _METRIC_NAMES_SHOWN, _metric_terms)
                            if _mn:
                                parts.append(f"measures: {_mn}")
                            _dn = _bounded_names(getattr(cap, "metric_dimensions", None),
                                                 _METRIC_DIM_NAMES_SHOWN, _metric_terms)
                            if _dn:
                                parts.append(f"dimensions: {_dn}")
                # The product's OWN verdict on itself, on the product node.  A
                # DEGRADED product is usable and must SAY it is degraded; one that
                # publishes no verdict has made no claim, which is not the same as
                # a passing one and must never render as though it were.
                if e.get("entity_type") == "database" and props.get("trust_status"):
                    allowed = props.get("agent_use_allowed")
                    parts.append(f"product validation: {props['trust_status']}")
                    if allowed is False:
                        parts.append("the product declares itself NOT fit for "
                                     "analytical use — say so rather than answering")
                    elif str(props.get("trust_status")).upper() == "DEGRADED":
                        parts.append("surface the degradation alongside the answer")

                # Which object a consumer should actually QUERY — a fact the product
                # publishes and a name cannot carry.  This travels with the node for
                # the same reason the decoded values do: the graph held `is_pii` on
                # 511 attributes and the model still queried the base table, because
                # the projection that omits them was never mentioned in the prompt.
                # A fact in the graph that never reaches the prompt is not guidance.
                # ONE resolver, shared with the TABLE SCHEMAS block above, so the two
                # renderings of the same fact cannot drift apart. This block used to
                # read `consumable_object` alone and compare it to the deployed table:
                # an ANDP v2 product that sets its consumable TO the base table (as the
                # policing products do) rendered nothing, and the typed view it also
                # declared was never mentioned anywhere.
                _qobj = _preferred_query_object(props, (cap.deployed_as if cap else "") or e["name"])
                if _qobj and e["name"] not in table_columns:   # not already stated above
                    parts.append(f"QUERY THIS OBJECT: {_qobj}")
                    note = props.get("access_note")
                    if note:
                        parts.append(str(note))

                # A product_bridge is a CLAIM, not a join.  Its basis and whether its
                # target resolved are the two most decision-relevant facts about it, so
                # they travel WITH the node — an agent must not have to go looking to
                # discover that a "link" is a self-declared synthetic key pointing at a
                # table that was never deployed.  A ``type_correspondence`` is different
                # in kind: a CLASS-level correspondence, never a per-record join, so it is
                # rendered as something the agent READS, not a table it could ever JOIN.
                if e.get("entity_type") == "product_bridge":
                    mech = props.get("linkage_mechanism")
                    basis = props.get("basis") or props.get("definition")
                    if mech == "type_correspondence":
                        parts.append(
                            "CONCEPTUAL CORRESPONDENCE at the CLASS/KIND level — this node is "
                            "NOT a table and NOT a per-record join; never SELECT FROM or JOIN it. "
                            "Read the corresponding kinds from its basis and state them")
                        if basis:
                            parts.append(f"correspondence: {basis}")
                        # The membership as DATA when the registry carries it: which
                        # values of each side's column the correspondence covers — so a
                        # filter on "robbery" is written on the CODE column, never on a
                        # class name (an agent once wrote from_object_class = 'Robbery').
                        _fv, _tv = props.get("from_values"), props.get("to_values")
                        if isinstance(_fv, list) and _fv:
                            parts.append(f"covers {props.get('from_object_class')}.{props.get('from_columns') or 'code'} "
                                         f"IN ({', '.join(repr(str(v)) for v in _fv)})")
                        if isinstance(_tv, list) and _tv:
                            parts.append(f"<-> {props.get('to_object_class')}.{props.get('to_columns') or 'kind'} "
                                         f"IN ({', '.join(repr(str(v)) for v in _tv)})")
                        if props.get("target_resolved") is False:
                            parts.append(
                                "one corresponding class is not deployed in this graph — "
                                "state the correspondence only for the class that is present")
                    else:
                        parts.append("DECLARED LINK, not a verified join")
                        if mech:
                            parts.append(f"declared basis: {mech}")
                        if basis:
                            parts.append(f"basis: {basis}")
                        if props.get("target_resolved") is False:
                            parts.append(
                                f"target does not exist in this graph "
                                f"({props.get('target_reference') or 'unknown'}) — "
                                "unusable for a join")
                        elif props.get("target_resolved") is True:
                            parts.append("both sides exist; use action 'probe_bridge' "
                                         "for value-overlap evidence")
                # A coded column is unusable without its codes: an agent that sees
                # "SEX OF VICTIM" but not "0 = Female" has no way to be right except by
                # luck, and will guess a plausible-looking literal ('F'). These are the
                # highest-value payload the graph carries — render them.
                parts.extend(_governance_parts(props))
                # The identifier to QUERY leads the line.  A logical entity of an
                # industry model is named `<domain>.<table>` — which reads exactly like
                # `<database>.<table>` — and with the deployed identifier buried after
                # a paragraph of description, a weak model copied the name into SQL
                # (`production.routing_operation`, Error 3802) on nine of eleven
                # validation turns before correcting itself.  The most decision-
                # relevant fact goes first; the description follows.
                _lead = [x for x in parts if x.startswith(("deployed: ", "QUERY THIS OBJECT: "))]
                if _lead:
                    parts = _lead + [x for x in parts if x not in _lead]
                if parts:
                    line += f" ({'; '.join(parts)})"
                lines.append(line)

        if subgraph["relationships"]:
            # Filter out table→column contains (already in TABLE SCHEMAS)
            non_schema_rels = [
                rel for rel in subgraph["relationships"]
                if not (rel["relationship_type"] == "contains"
                        and (rel["source_name"], rel["target_name"]) in table_col_contains)
            ]
            if non_schema_rels:
                lines.append("\nKNOWN RELATIONSHIPS:")
                for rel in non_schema_rels:
                    meta_desc = rel.get("metadata", {}).get("description", "")
                    card = f" [{rel['cardinality']}]" if rel.get("cardinality") else ""
                    # a key the constructor INFERRED from column names is a hypothesis,
                    # not a declared constraint — say so where the planner reads it
                    _via = (rel.get("metadata") or {}).get("inferred_via")
                    if _via and not meta_desc:
                        meta_desc = f"inferred from {_via}, not a declared key"
                    desc_part = f" — {meta_desc}" if meta_desc else ""
                    lines.append(
                        f"  - {rel['source_name']} --[{rel['relationship_type']}{card}]--> {rel['target_name']}{desc_part}"
                    )

        lines.append("--- END KNOWLEDGE GRAPH CONTEXT ---")
        return "\n".join(lines)

    # -------------------------------------------------------------------
    # Action handlers
    # -------------------------------------------------------------------

    async def _handle_query(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Search entities and return text results."""
        store = self._get_store(context)
        query_text = arguments.get("query_text") or arguments.get("entity_name", "")
        entity_type = arguments.get("entity_type")

        if not query_text:
            # Return graph stats
            stats = await store.get_stats()
            html = self._format_stats_html(stats)
            return ComponentRenderPayload(
                component_id=self.component_id,
                render_target=RenderTarget.INLINE,
                html=html,
                metadata={"tool_name": self.tool_name, "action": "query", "stats": stats},
            )

        # A lookup is a NAME match (substring over name + properties). A model that hands
        # the whole question over as the lookup — "property crime offense codes multiple
        # offenders incident trends" — matches nothing as a phrase; observed on
        # production (2026-09-04), where the miss then rendered as a dead "No entities
        # found" card in the answer. The component absorbs it: the phrase first, then its
        # meaningful terms (each with its singular), ranked by how many terms a node
        # satisfies — the nodes the question is ABOUT surface even from a sentence.
        try:
            results = await store.search_entities(query_text, limit=20, entity_type=entity_type)
        except Exception as exc:                          # pragma: no cover - defensive
            logger.warning(f"KG query '{query_text}' failed: {exc}")
            results = []
        search_mode, terms, matched_terms = "phrase", [], []
        # `miles_flown` is the phrase `miles flown` written as an identifier: once the
        # phrase itself has been tried, its terms are the words, not the whole token
        _term_src = query_text.replace("_", " ")
        if not results:
            terms = _lookup_terms(_term_src)
            if terms and (len(terms) > 1 or terms[0] != query_text.strip().lower()):
                results, matched_terms = await self._search_by_terms(store, terms, entity_type, limit=20)
                search_mode = "terms"
        elif not any(_identifier_hit(e, query_text) for e in results):
            # The phrase matched only DESCRIPTIONS. "miles flown" (prod session 0aa3a650)
            # returned `cabin_class.ffp_accrual_multiplier` — whose prose says "multiplier
            # applied to base miles flown" — and never `mileage_accrual.base_miles`, the
            # column that IS miles flown. A prose mention is the weakest evidence the
            # ranking knows; when it is the only evidence the phrase found, the lookup
            # also searches the phrase's terms so identifier-level matches can compete.
            terms = _lookup_terms(_term_src)
            if terms:
                more, matched_terms = await self._search_by_terms(store, terms, entity_type, limit=20)
                have = {e.get("id") or (e.get("name"), e.get("entity_type")) for e in results}
                results = results + [e for e in more if (e.get("id") or (e.get("name"), e.get("entity_type"))) not in have]
                if more:
                    search_mode = "phrase+terms"
        if not results:
            return await self._query_nothing_found(store, query_text, terms, entity_type)

        # ONE row per fact: a physical column beside the logical attribute it implements
        # is the same fact twice (the twin collapse the seeder applies).
        results, _ = _collapse_layer_twins(results)
        if search_mode != "terms":
            # Phrase hits are served in the backend's arbitrary order; rank them by the
            # same evidence the seeder ranks on (exact/leading identifier > word of the
            # identifier > prose mention), an identifier-level phrase hit first.
            _sterms = _scoring_terms(_term_src)
            results = sorted(results, key=lambda e: (0 if _identifier_hit(e, query_text) else 1,
                                                     -_match_score(e, _sterms)))
        results = results[:20]

        html = self._format_query_results_html(results, query_text)
        if search_mode == "terms":
            html = (f"<p class='text-gray-400 text-xs'>No entity matches the phrase "
                    f"'{html_escape(query_text)}' — showing entities matching its terms: "
                    f"{html_escape(', '.join(matched_terms))}.</p>" + html)
        elif search_mode == "phrase+terms":
            html = (f"<p class='text-gray-400 text-xs'>'{html_escape(query_text)}' matched only "
                    f"descriptions — also showing entities matching its terms: "
                    f"{html_escape(', '.join(matched_terms))}.</p>" + html)

        # The model reads the tool result as JSON, so the facts go in the METADATA as
        # plain text as well as in the HTML. HTML is for the reader; a decoded value list
        # buried in markup is a fact the agent has to parse out of presentation, and this
        # lookup exists precisely to hand it over cleanly.
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            html=html,
            # the row summary (Answer Surface): what was looked up and how much came back
            title=f"{len(results)} entit{'y' if len(results) == 1 else 'ies'} matching '{query_text}'",
            metadata={"tool_name": self.tool_name, "action": "query",
                      "result_count": len(results),
                      "search_mode": search_mode,
                      "query_terms": terms, "matched_terms": matched_terms,
                      "entities": [
                          {"name": e.get("name"), "type": e.get("entity_type"),
                           "facts": _coded_line(e.get("properties") or {},
                                                _governance_parts(e.get("properties") or {},
                                                                  complete=True))}
                          for e in results]},
        )

    #: Entity names offered back when a lookup misses, so the agent can correct itself
    #: in the same turn rather than spending a round trip discovering the vocabulary.
    _MISS_SAMPLE = 8
    #: Per-term candidate pool for the term search; the union is ranked, then cut to
    #: the caller's limit, so a term that matches broadly cannot crowd out the rest.
    _TERM_POOL = 120

    async def _search_by_terms(self, store: Any, terms: List[str], entity_type: Optional[str],
                               limit: int) -> Tuple[List[Dict], List[str]]:
        """Entities matching ANY of `terms` (each also in its singular), ranked by the
        number of distinct terms they satisfy, then by name matches over property
        matches, then by name. Returns (entities, matched_terms). Never raises."""
        hits: Dict[Any, Dict[str, Any]] = {}
        matched: List[str] = []
        for term in terms:
            forms = [term]
            singular = _singularize(term)
            if singular and singular != term:
                forms.append(singular)
            for form in forms:
                try:
                    rows = await store.search_entities(form, limit=self._TERM_POOL, entity_type=entity_type) or []
                except Exception as exc:                  # pragma: no cover - defensive
                    logger.warning(f"KG term search '{form}' failed: {exc}")
                    rows = []
                for e in rows:
                    key = e.get("id") or (e.get("name"), e.get("entity_type"))
                    rec = hits.setdefault(key, {"entity": e, "terms": set(), "name_hits": 0})
                    if term not in rec["terms"]:
                        rec["terms"].add(term)
                        if form in str(e.get("name") or "").lower():
                            rec["name_hits"] += 1
                if rows and term not in matched:
                    matched.append(term)
        ranked = sorted(hits.values(),
                        key=lambda r: (-len(r["terms"]), -r["name_hits"], str(r["entity"].get("name") or "")))
        return [r["entity"] for r in ranked[:limit]], matched

    async def _query_nothing_found(self, store: Any, query_text: str, terms: List[str],
                                   entity_type: Optional[str]) -> ComponentRenderPayload:
        """The recovery-shaped miss for `query` — the same contract as
        `_visualize_nothing_to_show`: never a dead end, never a dead card.

        Says what was tried (the phrase AND its terms), what the graph actually holds
        (size, types, a sample of real names), what a lookup matches (entity NAMES and
        their descriptions — not values inside the data), and the next step. The card is
        suppressed inline (`suppress_inline`): a miss is guidance for the agent, and a
        "No entities found" panel in the delivered answer told the reader nothing they
        could act on. Never raises.
        """
        total, types, sample = 0, {}, []
        try:
            stats = await store.get_stats() or {}
            total = int(stats.get("total_entities") or 0)
            types = stats.get("entity_types") or {}
        except Exception as exc:                          # pragma: no cover - defensive
            logger.warning(f"KG query miss: could not read stats: {exc}")
            total = -1
        if total > 0:
            try:
                sample = [str(e.get("name")) for e in
                          (await store.list_entities(entity_type=entity_type, limit=self._MISS_SAMPLE) or [])]
            except Exception as exc:                      # pragma: no cover - defensive
                logger.warning(f"KG query miss: could not sample entities: {exc}")
        if total == 0:
            text = ("Knowledge graph is empty. Use 'add_entity' to populate it, "
                    "or import entities via the REST API.")
        elif total > 0:
            tried = f"'{query_text}'"
            if terms:
                tried += f" — neither as a phrase nor by its terms ({', '.join(terms)})"
            _types = ", ".join(f"{k} ({v})" for k, v in sorted(types.items())) or "unknown"
            _scope = f" of type '{entity_type}'" if entity_type else ""
            text = (f"No entity matches {tried}. The graph is NOT empty — it holds {total} "
                    f"entities of these types: {_types}. A lookup matches entity NAMES and "
                    f"their descriptions, not values found inside the data, so pass ONE "
                    f"concept word (an entity, column or term name from the KNOWLEDGE GRAPH "
                    f"CONTEXT you already have), or omit query_text for the graph summary. "
                    f"The graph is supportive, never required: if the enrichment already "
                    f"names the tables and columns, proceed with them. NEXT STEP: try one of "
                    f"these names{_scope}")
            text += (": " + ", ".join(sample[:self._MISS_SAMPLE]) + ".") if sample else "."
        else:
            text = f"The knowledge graph could not be searched for '{query_text}' right now."
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            html=f"<p class='text-gray-400'>{html_escape(text)}</p>",
            metadata={"tool_name": self.tool_name, "action": "query", "result_count": 0,
                      "entities": [], "matched": False, "search_mode": "miss",
                      "query_terms": terms, "graph_total_entities": total,
                      "sample_names": sample[:self._MISS_SAMPLE], "message": text,
                      "suppress_inline": True},
        )

    async def _visualize_nothing_to_show(self, store: Any,
                                         entity_name: Optional[str]) -> ComponentRenderPayload:
        """Nothing to draw — say WHICH of the two reasons, because they are opposite.

        A lookup that matched nothing was reported as "Knowledge graph is empty. Use
        'add_entity' to populate it". Observed live on a graph holding 3,867 entities:
        the agent asked for an identifier that is a VALUE in the data rather than an
        entity NAME, was told its knowledge graph did not exist, stopped trusting the
        enrichment it had already been given, and fell back to brute-force querying for
        the rest of the turn. The operator reading the transcript was told the same
        untrue thing.

        The two cases need opposite responses, so they are answered separately:

        * the graph genuinely holds nothing — populating it IS the remedy;
        * the graph holds plenty and this name is not in it — the remedy is a different
          name, so the reply carries the size, the entity types, and a bounded sample of
          real names. A refusal that names its own remedy costs no further round trip.

        Never raises: if the graph cannot be measured the reply degrades to the neutral
        "nothing to display", which claims neither emptiness nor content.
        """
        total, types, sample = 0, {}, []
        try:
            stats = await store.get_stats() or {}
            total = int(stats.get("total_entities") or 0)
            types = stats.get("entity_types") or {}
        except Exception as exc:                      # pragma: no cover - defensive
            logger.warning(f"KG visualize miss: could not read stats: {exc}")
            total = -1
        if total > 0 and entity_name:
            try:
                sample = [str(e.get("name")) for e in
                          (await store.list_entities(limit=self._MISS_SAMPLE) or [])]
            except Exception as exc:                  # pragma: no cover - defensive
                logger.warning(f"KG visualize miss: could not sample entities: {exc}")

        if total == 0:
            text = ("Knowledge graph is empty. Use 'add_entity' to populate it, "
                    "or import entities via the REST API.")
        elif total > 0 and entity_name:
            _types = ", ".join(f"{k} ({v})" for k, v in sorted(types.items())) or "unknown"
            text = (f"No entity named '{entity_name}' is in this knowledge graph. "
                    f"The graph is NOT empty — it holds {total} nodes of these types: "
                    f"{_types}. Names are entities, not values found inside the data. "
                    f"NEXT STEP: call visualize with no entity_name to see the graph, or "
                    f"use one of these names")
            if sample:
                text += ": " + ", ".join(sample[:self._MISS_SAMPLE])
            text += "."
        else:
            text = "Nothing to display for this request."

        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            html=f"<p class='text-gray-400'>{html_escape(text)}</p>",
            metadata={"tool_name": self.tool_name, "action": "visualize", "node_count": 0,
                      "graph_total_entities": total, "matched": False,
                      "requested_entity": entity_name},
        )

    async def _handle_visualize(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Build D3 force-graph spec for frontend rendering."""
        store = self._get_store(context)
        entity_name = arguments.get("entity_name")
        depth = arguments.get("depth", 2)
        # Title the card with the ACTUAL knowledge graph name (from kg_metadata),
        # not a generic "Knowledge Graph" label. Fall back to the generic label only
        # when the KG has no name recorded.
        title = arguments.get("title")
        if not title:
            try:
                _kg_name = (store.get_kg_metadata() or {}).get("name")
            except Exception:
                _kg_name = None
            _base = _kg_name or "Knowledge Graph"
            title = f"{_base}{': ' + entity_name if entity_name else ''}"

        if entity_name:
            entity = store.get_entity_by_name(entity_name)
            if entity:
                subgraph = store.extract_subgraph_adaptive(
                    seed_entity_ids=[entity["id"]], max_nodes=100,
                )
            else:
                # Try search
                results = await store.search_entities(entity_name, limit=3)
                if results:
                    subgraph = store.extract_subgraph_adaptive(
                        seed_entity_ids=[e["id"] for e in results], max_nodes=100,
                    )
                else:
                    subgraph = {"entities": [], "relationships": []}
        else:
            subgraph = store.get_full_graph(max_nodes=100)

        if not subgraph["entities"]:
            return await self._visualize_nothing_to_show(store, entity_name)

        # Get importance scores for node sizing
        importance = store.get_entity_importance()

        # Build D3 spec
        nodes = []
        links = []
        node_id_map = {}

        for i, entity in enumerate(subgraph["entities"]):
            node_id_map[entity["id"]] = i
            nodes.append({
                "id": i,
                "entity_id": entity["id"],
                "name": entity["name"],
                "type": entity["entity_type"],
                "properties": entity.get("properties", {}),
                "importance": round(importance.get(entity["id"], 0), 3),
                "is_center": entity["name"].lower() == (entity_name or "").lower(),
            })

        for rel in subgraph["relationships"]:
            source_idx = node_id_map.get(rel["source_id"])
            target_idx = node_id_map.get(rel["target_id"])
            if source_idx is not None and target_idx is not None:
                links.append({
                    "source": source_idx,
                    "target": target_idx,
                    "type": rel["relationship_type"],
                    "cardinality": rel.get("cardinality"),
                    "metadata": rel.get("metadata", {}),
                })

        # Full type catalog (ALL entity/relationship types with their TOTAL counts)
        # from stats — so the filter pills show every type in the graph, not just
        # those present in this sampled subgraph. Without this the renderer falls
        # back to sample-derived pills, so a type absent from the current sample
        # (e.g. 'metric' in a column-heavy 100-node window) has no tile to toggle
        # and only appears once the node budget is raised. Mirrors the /graph-spec
        # endpoint exactly (same source) so the two paths can never drift.
        total_nodes, total_edges = len(nodes), len(links)
        entity_type_counts, relationship_type_counts = {}, {}
        try:
            _stats = await store.get_stats()
            total_nodes = _stats.get("total_entities", total_nodes)
            total_edges = _stats.get("total_relationships", total_edges)
            entity_type_counts = _stats.get("entity_types", {}) or {}
            relationship_type_counts = _stats.get("relationship_types", {}) or {}
        except Exception:
            pass
        shown_type_counts = {}
        for n in nodes:
            shown_type_counts[n["type"]] = shown_type_counts.get(n["type"], 0) + 1

        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={
                "nodes": nodes,
                "links": links,
                "title": title,
                "center_entity": entity_name,
                "depth": depth,
                "entity_type_colors": ENTITY_TYPE_COLORS,
                "kg_id": getattr(store, "kg_id", None),
                "profile_id": (context or {}).get("profile_id"),
                "shown_nodes": len(nodes),
                "shown_edges": len(links),
                "total_nodes": total_nodes,
                "total_edges": total_edges,
                "truncated": total_nodes > len(nodes),
                "entity_type_counts": entity_type_counts,
                "relationship_type_counts": relationship_type_counts,
                "shown_type_counts": shown_type_counts,
                # The active filter = the types actually present in this focused
                # sample, so their pills render "on" and every other catalog type
                # renders "off" (dimmed) and togglable — clicking it re-queries the
                # server to pull that type in. Without this the renderer assumes ALL
                # types are active, so an absent type would show as "on" yet render
                # nothing and a click would toggle it OFF (backwards).
                "applied_types": sorted(shown_type_counts.keys()),
                # Preserve the focus seed + node budget across a pill-toggle re-query
                # so toggling a type doesn't drop the entity this view was centered on.
                "applied_focus_names": [entity_name] if entity_name else None,
                "requested_max": 100,
            },
            # the row summary leads with the CENTRE (the graph's own name is the body's
            # header — restating it would push the counts off the line)
            title=f"{entity_name or title} · {len(nodes)} entit{'y' if len(nodes) == 1 else 'ies'} · {len(links)} relationship{'' if len(links) == 1 else 's'}"
                  + (f" of {total_nodes}" if total_nodes > len(nodes) else ""),
            metadata={
                "tool_name": self.tool_name,
                "node_count": len(nodes),
                "link_count": len(links),
                "action": "visualize",
                # a graph the user asked to SEE opens (the component's default is collapsed)
                "answer_surface": {"open": True},
            },
        )

    async def _handle_open(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Surface an existing knowledge graph's full panel from any session.

        Builds the same D3 spec as 'visualize' but flags the payload with
        action='open' so the renderer auto-opens the split panel in the CURRENT
        session — letting a user open, in session B, a KG populated in session A.
        Unlike 'visualize' (which only renders an inline preview with a manual
        'Open in Graph' button), 'open' is the explicit "show me the graph" verb.
        """
        payload = await self._handle_visualize(arguments, context)
        # Flag for the renderer to auto-open the split panel. visualize returns
        # an html-only payload (no nodes in spec) for an empty graph — the
        # renderer no-ops on that, so flagging it is harmless.
        payload.spec["action"] = "open"
        payload.metadata["action"] = "open"
        return payload

    # ------------------------------------------------------------------
    # Version history (same design pattern as flow / canvas)
    # ------------------------------------------------------------------

    async def _gather_graph(self, store: Any) -> tuple:
        """Export the full graph (all entities + relationships) for a snapshot."""
        entities = await store.list_entities(limit=999999)
        relationships = await store.list_relationships(limit=999999)
        return entities, relationships

    async def _handle_snapshot(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Capture the current graph as a new version (deduped, compressed, capped)."""
        from trusted_data_agent.core.kg_version_db import snapshot_kg

        store = self._get_store(context)
        entities, relationships = await self._gather_graph(store)
        result = snapshot_kg(
            store.kg_id, store.user_uuid, entities, relationships,
            label=arguments.get("label"),
        )
        if result is None:
            message = "Knowledge graph unchanged since the last snapshot — no new version created."
        else:
            message = (
                f"Snapshot v{result['version_num']} created "
                f"({result['entity_count']} nodes, {result['rel_count']} relationships)."
            )
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={"view": "silent_snapshot", "result": result},
            title="",
            metadata={"tool_name": self.tool_name, "action": "snapshot", "message": message},
        )

    async def _handle_versions(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """List the version history for the active KG (silent — the LLM reads it)."""
        from trusted_data_agent.core.kg_version_db import list_versions

        store = self._get_store(context)
        versions = list_versions(store.kg_id, store.user_uuid)
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={"view": "silent_versions", "versions": versions},
            title="",
            metadata={"tool_name": self.tool_name, "action": "versions", "count": len(versions)},
        )

    async def _handle_restore(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Restore the graph to a prior version (clear + bulk re-import), then re-open it."""
        from trusted_data_agent.core.kg_version_db import (
            get_version_graph, snapshot_kg,
        )

        version_id = arguments.get("version_id")
        if not version_id:
            return ComponentRenderPayload(
                component_id=self.component_id,
                render_target=RenderTarget.INLINE,
                spec={"view": "silent_error", "error": "'restore' requires 'version_id'"},
                title="",
                metadata={"tool_name": self.tool_name, "action": "restore", "error": True},
            )

        store = self._get_store(context)
        graph = get_version_graph(version_id, store.kg_id, store.user_uuid)
        if not graph:
            return ComponentRenderPayload(
                component_id=self.component_id,
                render_target=RenderTarget.INLINE,
                spec={"view": "silent_error", "error": "Version not found"},
                title="",
                metadata={"tool_name": self.tool_name, "action": "restore", "error": True},
            )

        # Replace the live graph with the version's contents (clear + bulk import).
        store.clear_graph()
        store.import_bulk(graph["entities"], graph["relationships"])
        # Record the restore as its own version so the action is itself tracked
        # (parity with canvas restore; differs from the pre-restore state so it
        # is not deduped away).
        snapshot_kg(
            store.kg_id, store.user_uuid, graph["entities"], graph["relationships"],
            label=f"Restored v{graph['version_num']}",
        )
        # Re-render the restored graph in the panel.
        return await self._handle_open(arguments, context)

    async def _handle_add_entity(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Add an entity to the graph."""
        store = self._get_store(context)
        name = arguments["entity_name"]
        entity_type = arguments["entity_type"]
        properties = arguments.get("properties", {})

        # every node carries its layer (a layer-less node is invisible to the
        # layer-aware consumers and fails conformance); agent-added = logical
        properties = dict(properties or {})
        properties.setdefault("layer", "physical" if entity_type in _PHYSICAL_TYPES_FOR_LAYER else "logical")
        entity_id = await store.add_entity(
            name=name,
            entity_type=entity_type,
            properties=properties,
            source="llm_inferred",
        )

        color = ENTITY_TYPE_COLORS.get(entity_type, "#6b7280")
        props_html = f'<br><small class="text-gray-400">{properties}</small>' if properties else ""
        html = (
            f"<div class='glass-panel p-3 rounded-lg border' style='border-color: {color}40'>"
            f"<span style='color: {color}'>&#9679;</span> "
            f"Added <strong>{entity_type}</strong> entity: <strong>{name}</strong>"
            f"{props_html}"
            f"</div>"
        )

        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            html=html,
            metadata={
                "tool_name": self.tool_name, "action": "add_entity",
                "entity_id": entity_id, "name": name, "entity_type": entity_type,
            },
        )

    async def _handle_add_relationship(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Add a relationship between two entities."""
        store = self._get_store(context)

        source_name = arguments["entity_name"]
        target_name = arguments["target_entity"]
        rel_type = arguments["relationship_type"]
        source_type = arguments.get("entity_type")
        target_type = arguments.get("target_entity_type")

        # Resolve source and target entities
        source_ent = store.get_entity_by_name(source_name, source_type)
        target_ent = store.get_entity_by_name(target_name, target_type)

        if not source_ent:
            return ComponentRenderPayload(
                component_id=self.component_id,
                render_target=RenderTarget.INLINE,
                html=f"<p class='text-red-400'>Source entity '{source_name}' not found in the knowledge graph.</p>",
                spec={"error": f"source entity '{source_name}' not found"},
                metadata={"tool_name": self.tool_name, "action": "add_relationship", "error": True},
            )

        if not target_ent:
            return ComponentRenderPayload(
                component_id=self.component_id,
                render_target=RenderTarget.INLINE,
                html=f"<p class='text-red-400'>Target entity '{target_name}' not found in the knowledge graph.</p>",
                spec={"error": f"target entity '{target_name}' not found"},
                metadata={"tool_name": self.tool_name, "action": "add_relationship", "error": True},
            )

        rel_id = await store.add_relationship(
            source_entity_id=source_ent["id"],
            target_entity_id=target_ent["id"],
            relationship_type=rel_type,
            cardinality=arguments.get("cardinality"),
            metadata=arguments.get("properties", {}),
            source="llm_inferred",
        )

        src_color = ENTITY_TYPE_COLORS.get(source_ent["entity_type"], "#6b7280")
        tgt_color = ENTITY_TYPE_COLORS.get(target_ent["entity_type"], "#6b7280")
        html = (
            f"<div class='glass-panel p-3 rounded-lg border' style='border-color: var(--border-primary)'>"
            f"<span style='color: {src_color}'>&#9679;</span> {source_name} "
            f"<span class='text-gray-400'>--[{rel_type}]--&gt;</span> "
            f"<span style='color: {tgt_color}'>&#9679;</span> {target_name}"
            f"</div>"
        )

        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            html=html,
            metadata={
                "tool_name": self.tool_name, "action": "add_relationship",
                "relationship_id": rel_id, "source": source_name, "target": target_name,
                "type": rel_type,
            },
        )

    async def _handle_get_context(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Get enrichment context text (for debugging/testing)."""
        store = self._get_store(context)
        query_text = arguments.get("query_text", "")

        if query_text:
            entities = await self._search_entities_for_query(store, query_text)
            if entities:
                subgraph = store.extract_subgraph_adaptive(
                    seed_entity_ids=[e["id"] for e in entities],
                    max_nodes=200,
                )
                caps = await self._resolve_capabilities(store, subgraph["entities"])
                context_text = self._format_subgraph_for_prompt(subgraph, caps, query=query_text)
            else:
                context_text = "(No relevant entities found)"
        else:
            stats = await store.get_stats()
            context_text = f"Graph has {stats.get('total_entities', 0)} nodes and {stats.get('total_relationships', 0)} relationships."

        html = f"<pre class='text-sm text-gray-300 bg-gray-900 p-3 rounded-lg overflow-auto'>{context_text}</pre>"

        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            html=html,
            metadata={"tool_name": self.tool_name, "action": "get_context"},
        )

    async def _handle_probe_bridge(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Turn a DECLARED cross-product link into testable evidence.

        A ``product_bridge`` is a claim, not a join.  This resolves both sides to their
        deployed identifiers and compiles an overlap probe; the agent runs it through
        its normal (Agent-Authority-governed) path — the component deliberately holds
        no warehouse connection.  A bridge whose target does not exist yields the
        reason rather than SQL: "there is nothing to measure" IS the answer.
        """
        import html as _html
        from components.builtin.knowledge_graph.bridge_probe import (
            index_entities, resolve_bridge_probe,
        )

        def _err(msg):
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-sm' style='color:#f87171'>{_html.escape(msg)}</p>",
                spec={"error": str(msg)},
                metadata={"tool_name": self.tool_name, "action": "probe_bridge", "error": msg})

        store = self._get_store(context)
        wanted = (arguments.get("bridge_name") or "").strip()

        try:
            bridges = await store.list_entities(entity_type="product_bridge", limit=200)
            entities = await store.list_entities(entity_type="entity", limit=5000)
        except Exception as exc:  # noqa: BLE001
            return _err(f"Could not read the knowledge graph: {exc}")

        if not bridges:
            return _err("This knowledge graph declares no cross-product links.")

        low = wanted.lower()
        node = next((b for b in bridges
                     if low in str(b.get("name", "")).lower()
                     or low in str((b.get("properties") or {}).get("label", "")).lower()), None)
        if node is None:
            names = ", ".join(str((b.get("properties") or {}).get("label") or b.get("name"))
                              for b in bridges[:8])
            return _err(f"No declared link matching '{wanted}'. Available: {names}")

        by_name, by_label = index_entities(entities)
        esc = _html.escape
        _np = node.get("properties") or {}
        if _np.get("linkage_mechanism") == "type_correspondence":
            # a class-level correspondence is READ, not probed: state its basis and the
            # corresponding classes; there is no row-level overlap and no table to query.
            _basis = _np.get("basis") or _np.get("definition") or ""
            _frm, _to = _np.get("from_object_class") or "", _np.get("to_object_class") or ""
            body = ["<div><span style='opacity:.7'>declared basis:</span> "
                    "<strong>type_correspondence</strong> (class-level)</div>"]
            if _frm and _to:
                body.append(f"<div><span style='opacity:.7'>corresponds:</span> {esc(_frm)} \u21c4 {esc(_to)}</div>")
            if _basis:
                body.append(f"<div class='mt-2'>{esc(_basis)}</div>")
            body.append("<div class='text-xs mt-2' style='opacity:.75'>A class-level correspondence "
                        "has no per-record key — there is nothing to probe at the row level. State it "
                        "from its basis; do not SELECT FROM or JOIN this node.</div>")
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=("<div class='text-sm'><div style='font-weight:600'>Declared cross-product link — "
                      f"{esc(str(_np.get('label') or node.get('name')))}</div>" + "".join(body) + "</div>"),
                metadata={"tool_name": self.tool_name, "action": "probe_bridge",
                          "linkage_mechanism": "type_correspondence", "class_level": True})

        probe = resolve_bridge_probe(node, by_name, by_label)
        rows = [f"<div><span style='opacity:.7'>declared basis:</span> "
                f"<strong>{esc(probe.linkage_mechanism)}</strong></div>"]
        if probe.links:
            rows.append(f"<div><span style='opacity:.7'>claims:</span> {esc(probe.links)}</div>")
        if probe.blocked:
            rows.append("<div class='mt-2' style='color:#f59e0b'>Not probeable — "
                        f"{esc(probe.blocked)}</div>")
        else:
            rows.append(f"<div><span style='opacity:.7'>source:</span> "
                        f"<code>{esc(probe.source_identifier or '')}</code></div>")
            rows.append(f"<div><span style='opacity:.7'>target:</span> "
                        f"<code>{esc(probe.target_identifier or '')}</code></div>")
            rows.append("<pre class='text-xs mt-2 p-2 rounded' style='overflow-x:auto;"
                        "background:var(--bg-overlay,rgba(0,0,0,0.25))'>"
                        f"{esc(probe.overlap_sql or '')}</pre>")
        for n in probe.notes:
            rows.append(f"<div class='text-xs mt-1' style='color:#f59e0b'>{esc(n)}</div>")

        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE,
            html=("<div class='text-sm'><div style='font-weight:600'>Declared cross-product "
                  f"link — {esc(str((node.get('properties') or {}).get('label') or node.get('name')))}"
                  "</div>" + "".join(rows) + "</div>"),
            metadata={"tool_name": self.tool_name, "action": "probe_bridge",
                      **probe.as_dict()})

    async def _handle_declare_concept(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Declare a business concept on the graph — a term, a compilable metric and a
        recipe, grounded in an entity's declared columns (concepts.build_declaration).
        Deterministic; refuses, naming what is missing, rather than inventing."""
        from .concepts import build_declaration
        store = self._get_store(context)
        concept = str(arguments.get("concept_name") or arguments.get("label") or "").strip()
        definition = str(arguments.get("definition") or "").strip()
        predicate = str(arguments.get("predicate") or "").strip()
        entity_name = str(arguments.get("entity") or arguments.get("entity_name") or "").strip()
        if not (concept and definition and predicate and entity_name):
            return self._error_payload("declare_concept needs concept_name, definition, predicate and entity")
        ent = await self._find_entity_any_layer(store, entity_name)
        if not ent:
            return self._error_payload(f"no entity named '{entity_name}' in this graph")
        attrs = await self._entity_attribute_leaves(store, ent)
        d = build_declaration(concept=concept, definition=definition, predicate=predicate, entity=ent,
                              entity_attributes=attrs, dimensions=arguments.get("dimensions"),
                              declared_by=str((context or {}).get("user_uuid") or "agent"))
        if d["errors"]:
            return self._error_payload("concept not declared: " + "; ".join(d["errors"]))
        ids: Dict[str, int] = {}
        for e in d["entities"]:
            ids[e["name"]] = await store.add_entity(name=e["name"], entity_type=e["entity_type"],
                                                    properties=e["properties"], source="declared")
        ids[ent["name"]] = ent["id"]
        made = 0
        for r in d["relationships"]:
            tgt = ids.get(r["target_name"])
            if tgt is None:
                t = await _maybe_await(store.get_entity_by_name(r["target_name"]))
                tgt = t["id"] if t else None
            src_id = ids.get(r["source_name"])
            if src_id is not None and tgt is not None:
                await store.add_relationship(src_id, tgt, r["relationship_type"])
                made += 1
        html = (f"<div class='glass-panel p-3 rounded-lg border'>Declared <strong>{concept}</strong> on "
                f"<strong>{ent['name']}</strong>: a glossary term, the compilable metric "
                f"<code>{d['entities'][1]['properties']['label']}</code> and a recipe ({made} links).</div>")
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE, html=html,
            metadata={"tool_name": self.tool_name, "action": "declare_concept", "concept": concept,
                      "entity": ent["name"], "metric": d["entities"][1]["name"], "links": made,
                      "memory_module_sql": d["memory_module_sql"]},
            spec={"concept": concept, "entity": ent["name"], "metric": d["entities"][1]["name"],
                  "recipe": d["entities"][2]["name"], "memory_module_sql": d["memory_module_sql"]},
        )

    async def _find_entity_any_layer(self, store, name: str) -> Optional[Dict]:
        """The entity by exact name, or by its leaf among entities/tables (one match)."""
        e = await _maybe_await(store.get_entity_by_name(name))
        if e and e.get("entity_type") == "entity":
            return e
        if e and e.get("entity_type") == "table":
            return await self._logical_of_table(store, e) or e
        hits = [x for x in await store.search_entities(name, limit=50)
                if x.get("entity_type") in ("entity", "table")
                and str(x.get("name", "")).rpartition(".")[2].lower() == name.rpartition(".")[2].lower()]
        logical = [x for x in hits if x.get("entity_type") == "entity"]
        found = (logical or hits)[0] if len(logical or hits) == 1 else None
        if found is not None and found.get("entity_type") == "table":
            # A deployed name (``Policing_Core.v_CaseObject``) names the PHYSICAL table; a
            # proposal or an action relates to the LOGICAL entity it realises — follow the
            # table's `implements` edge (Act II rehearsal: a Lead drafted beside the view).
            found = await self._logical_of_table(store, found) or found
        return found

    async def _logical_of_table(self, store, table: Dict) -> Optional[Dict]:
        """The logical entity a physical table `implements` (exactly one), else None."""
        try:
            nb = await _maybe_await(store.get_node_neighborhood(table["id"], max_neighbors=200)) or {}
        except Exception:  # noqa: BLE001
            return None
        ents = {x.get("id"): x for x in (nb.get("entities") or nb.get("nodes") or [])}
        targets = [ents.get(r.get("target_id")) for r in (nb.get("relationships") or nb.get("edges") or [])
                   if r.get("relationship_type") == "implements" and r.get("source_id") == table.get("id")]
        targets = [t for t in targets if t and t.get("entity_type") == "entity"]
        return targets[0] if len(targets) == 1 else None

    async def _entity_attribute_leaves(self, store, ent: Dict) -> List[str]:
        """Leaf names of the entity's declared attributes (its `contains` children)."""
        try:
            sub = store.extract_subgraph_adaptive(seed_entity_ids=[ent["id"]], query_entity_ids=[ent["id"]],
                                                   max_nodes=2000, per_table_cap=5000)
        except Exception:  # noqa: BLE001
            return []
        prefix = str(ent.get("name", "")) + "."
        return [str(x.get("name", "")).rpartition(".")[2] for x in sub.get("entities", [])
                if x.get("entity_type") in ("attribute", "column") and str(x.get("name", "")).startswith(prefix)]

    # ── PROP-008 A2 — OBDA-MODEL: propose_attribute / propose_entity ───────────────────
    # The normalisation advisor (agent/kg_constructors/normalisation.py) decides from
    # evidence; the model only NAMES. A draft lands on the AUTHORED layer only — the walked
    # base layer is never written — and every verdict is a design_decision node, so the
    # next person reads why. Gated on the OBDA-MODEL · OBDA-ACT master switch; an autonomous agent also
    # needs the `modelAuthoring` grant (Agent Authority).

    def _act_refusal(self, action: str, context: Optional[Dict] = None) -> Optional[ComponentRenderPayload]:
        """None when the act may proceed; a recovery-shaped refusal otherwise. The plane's
        availability is the OBDA-MODEL component's for this agent (the component chain)."""
        try:
            from trusted_data_agent.core import obda_planes as _planes
            _ctx = context or {}
            _prof = _ctx.get("profile") if isinstance(_ctx.get("profile"), dict) else \
                _profile_of(_ctx.get("profile_id"), _ctx.get("user_uuid"))
            if not _planes.plane_enabled("model", _prof, _ctx.get("user_uuid")):
                return self._error_payload(
                    f"{action} is not available: OBDA-MODEL is not enabled for this agent. Answer "
                    f"from the model as it is; say that the model does not describe this and that "
                    f"extending it is not enabled here.")
            from trusted_data_agent.core.agent_authority import get_current_principal
            principal = get_current_principal()
            if principal is not None and not bool(getattr(principal.scope, "model_authoring", False)):
                return self._error_payload(
                    f"{action} refused: this agent has no model-authoring grant (authorityConfig.modelAuthoring). "
                    f"Say that the model does not describe this; a person can extend it.")
        except Exception as exc:  # noqa: BLE001 — a failed gate check is a refusal, never a write
            return self._error_payload(f"{action} refused: the OBDA-MODEL · OBDA-ACT gate could not be resolved ({exc})")
        return None

    @staticmethod
    def _act_extension(context: Optional[Dict]) -> str:
        """The extension a draft lands in: the OWNING user's (PROP-008 A5 / §3a — an agent
        drafts on its owner's layer; one layer per extender)."""
        from trusted_data_agent.kgstore import types as _T
        return _T.extension_id_for((context or {}).get("user_uuid"))

    def _act_actor(self, context: Optional[Dict]) -> str:
        try:
            from trusted_data_agent.core.agent_authority import get_current_principal
            principal = get_current_principal()
            if principal is not None:
                return principal.principal_id
        except Exception:  # noqa: BLE001
            pass
        return str((context or {}).get("user_uuid") or "agent")

    async def _act_probe_runner(self, context: Optional[Dict]):
        """``run_sql(sql) -> list[dict]`` on the agent's OWN default source, bounded and
        read-only through the navigator provider; None when the agent reaches no direct
        source (an MCP source has no run_select) — the advisor then reads ``provisional``."""
        try:
            from trusted_data_agent.dbcatalog.agent_source import agent_provider
            _svc, provider, _res = await agent_provider(context or {})
        except Exception:  # noqa: BLE001
            return None
        if provider is None or not hasattr(provider, "run_select"):
            return None

        async def run_sql(sql: str):
            cols, rows = await provider.run_select(sql, 80)
            names = [str(c) for c in (cols or [])]
            return [dict(zip(names, r)) for r in (rows or [])]
        return run_sql

    async def _act_run_plan(self, run_sql, plan: Dict[str, str]) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for key, sql in plan.items():
            try:
                results[key] = await run_sql(sql)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[OBDA-MODEL] advisor probe '{key}' failed: {str(exc)[:160]}")
                results[key] = None
        return results

    async def _act_entity_facts(self, store, ent: Dict) -> Dict[str, Any]:
        """``{"name", "key", "deployed"}`` for the advisor — the declared key through the
        ONE declared-key reading, the deployed identifier through the ONE resolver."""
        from trusted_data_agent.agent.kg_constructors.source_validation import key_columns
        from .resolver import resolve_deployed_table
        props = ent.get("properties") or {}
        deployed = props.get("deployed_table") or ""
        if not deployed:
            try:
                dep = await resolve_deployed_table(store, ent["name"])
                deployed = dep.identifier if dep else ""
            except Exception:  # noqa: BLE001
                deployed = ""
        return {"name": ent["name"], "key": key_columns(props), "deployed": deployed}

    async def _act_referenced_by(self, store, leaf: str, own: str) -> List[str]:
        """Entities OTHER than ``own`` that declare an attribute with this leaf name."""
        try:
            hits = await store.search_entities(leaf, limit=60)
        except Exception:  # noqa: BLE001
            return []
        owners: List[str] = []
        for x in hits:
            if x.get("entity_type") not in ("attribute", "column"):
                continue
            n = str(x.get("name", ""))
            owner, _, l = n.rpartition(".")
            if l.lower() == leaf.lower() and owner and owner != own and owner not in owners:
                owners.append(owner)
        return owners

    async def _act_verdict(self, store, ent: Optional[Dict], candidate: Dict[str, Any],
                           context: Optional[Dict], declared: Optional[str]) -> Tuple[Any, Dict[str, Any]]:
        """Run the advisor for a candidate against ``ent`` (None = no target entity)."""
        from trusted_data_agent.agent.kg_constructors import normalisation as N
        try:
            from trusted_data_agent.agent.kg_constructors.andp_semantic_walker import parse_title_positions
        except ImportError:  # enterprise package absent: no title/position parsing
            def parse_title_positions(_description, _name):
                return None, []
        name = str(candidate.get("name") or "")
        cand = dict(candidate)
        if declared:
            cand["declared"] = declared
        title, positions = parse_title_positions(cand.get("description"), name)
        if positions and not cand.get("positions"):
            cand["positions"], cand["family"] = positions, title
        facts: Dict[str, Any] = {"name": "", "key": [], "deployed": ""}
        probes: Dict[str, Any] = {}
        plan: Dict[str, str] = {}
        if ent is not None:
            facts = await self._act_entity_facts(store, ent)
            leaves = await self._entity_attribute_leaves(store, ent)
            cand.setdefault("siblings", leaves)
            cand.setdefault("referenced_by", await self._act_referenced_by(store, name, ent["name"]))
            if name in leaves:          # exists as data: measure it
                cand["column"] = name
                plan = N.probe_plan(cand, facts, other_columns=leaves)
                run_sql = await self._act_probe_runner(context)
                if run_sql is not None and plan:
                    probes = N.apply_probes(plan, await self._act_run_plan(run_sql, plan))
        verdict = N.advise(cand, facts, probes)
        verdict = N.overrule(verdict, candidate.get("chosen"))
        return verdict, {"facts": facts, "probed": bool(plan), "measured": bool(probes.get("rows"))}

    async def _act_record_decision(self, store, verdict, candidate: Dict[str, Any], facts: Dict[str, Any],
                                   actor: str, link_ids: List[int], context: Optional[Dict] = None) -> Tuple[str, int]:
        from trusted_data_agent.agent.kg_constructors import normalisation as N
        decision_id = "dec_" + uuid.uuid4().hex[:12]
        decided_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        props = N.design_decision_props(verdict, candidate, facts, actor, decision_id, decided_at)
        props.setdefault("extension_id", self._act_extension(context))
        node_name = f"decision.{decision_id}"
        did = await store.add_entity(name=node_name, entity_type="design_decision", properties=props,
                                     source="authored", source_detail="normalisation_advisor")
        for tgt in link_ids:
            try:
                await store.add_relationship(did, tgt, "relates_to", source="authored")
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[OBDA-MODEL] decision link skipped: {exc}")
        return decision_id, did

    @staticmethod
    def _act_proposal_html(kind: str, name: str, verdict, drafted: Optional[str], ent_name: str = "",
                           draft_act: Optional[Dict[str, Any]] = None,
                           records: Optional[List[Dict[str, Any]]] = None,
                           drafted_kg: Optional[str] = None, target_node: Optional[str] = None) -> str:
        """The proposal row's BODY: the verdict in words, every reason, every finding, and
        what happened (drafted / not drafted). No DDL, no JSON."""
        esc = html_escape
        head = {"attribute": f"an attribute of <strong>{esc(ent_name)}</strong>",
                "value_domain": f"a value domain on an attribute of <strong>{esc(ent_name)}</strong>",
                "entity": "an entity of its own",
                "unknown": "not decidable from the evidence"}.get(verdict.decision, esc(verdict.decision))
        # a row that DID draft names its graph, so the page can update live (ui.js announces it)
        drafted_attr = f" data-kg-drafted='{esc(str(drafted_kg))}'" if drafted_kg else ""
        # the node a draft creates — so the page can show the state NOW (a row answered before
        # the person clicked Draft it must not offer it again after a reload)
        if target_node:
            drafted_attr += f" data-kg-node='{esc(str(target_node))}'"
        parts = [f"<div class='kg-proposal' data-kg-proposal='{esc(kind)}'{drafted_attr}>",
                 f"<div class='kg-proposal-head'>Proposed {esc(kind.replace('_', ' '))} <strong>{esc(name)}</strong> "
                 f"&mdash; the model's rules read it as {head}"
                 + (" <span class='kg-proposal-tag'>provisional</span>" if verdict.provisional else "")
                 + (" <span class='kg-proposal-tag'>must</span>" if verdict.must else "") + ".</div>"]
        if records:
            # what it records, in the person's words; what the platform added, said as such
            own = [r for r in records if not r.get("added")]
            plat = [r for r in records if r.get("added")]
            words = lambda rs: " · ".join(esc(str(r.get("label") or r.get("name"))) for r in rs)  # noqa: E731
            line = (f"It records {words(own)}" if own else "")
            if plat:
                line += (". " if line else "") + "The platform adds " + " · ".join(
                    esc(str(r.get("label") or r.get("name"))) + (" (identifies a row)" if r.get("added") == "key" else "")
                    for r in plat)
            parts.append(f"<div class='kg-proposal-records'>{line}.</div>")
        if verdict.reasons:
            parts.append("<ul class='kg-proposal-why'>" + "".join(f"<li>{esc(r)}</li>" for r in verdict.reasons) + "</ul>")
        if verdict.findings:
            parts.append("<ul class='kg-proposal-findings'>" + "".join(f"<li>{esc(f)}</li>" for f in verdict.findings) + "</ul>")
        parts.append(f"<div class='kg-proposal-state'>{esc(drafted) if drafted else 'Not drafted.'}</div>")
        # The one-click Draft (§5a): offered ONLY when a draft is the act that is owed — the
        # verdict allows it and nothing was drafted. Rule 8.5: never a button that cannot act.
        if draft_act:
            parts.append("<div class='kg-proposal-foot'><button type='button' class='ans-act kg-proposal-draft' "
                         f"data-kg-id='{esc(str(draft_act.get('kg_id') or ''))}' "
                         f"data-kg-args='{esc(json.dumps(draft_act.get('args') or {}, ensure_ascii=False))}'>"
                         "Draft it on the authored layer</button></div>")
        parts.append("</div>")
        return "".join(parts)

    async def _handle_propose_attribute(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Attribute, value domain or entity? The advisor decides; a draft is written on the
        authored layer only when the verdict allows an attribute (or a value domain)."""
        refusal = self._act_refusal("propose_attribute", context)
        if refusal is not None:
            return refusal
        store = self._get_store(context)
        entity_name = str(arguments.get("entity") or arguments.get("entity_name") or "").strip()
        attr = str(arguments.get("attribute_name") or "").strip()
        if not (entity_name and attr):
            return self._error_payload("propose_attribute needs entity and attribute_name")
        ent = await self._find_entity_any_layer(store, entity_name)
        if not ent:
            return self._error_payload(f"no entity named '{entity_name}' in this graph")
        candidate: Dict[str, Any] = {"name": attr, "description": arguments.get("description"),
                                     "data_type": arguments.get("data_type"), "values": arguments.get("values"),
                                     "chosen": arguments.get("decision")}
        verdict, info = await self._act_verdict(store, ent, candidate, context,
                                                declared=arguments.get("declared"))
        actor = self._act_actor(context)
        drafted: Optional[str] = None
        node_id: Optional[int] = None
        draft = bool(arguments.get("draft", False))
        if draft and verdict.decision in ("attribute", "value_domain"):
            node_name = f"{ent['name']}.{attr}"
            existing = await _maybe_await(store.get_entity_by_name(node_name))
            if existing:
                drafted = f"'{node_name}' already exists ({(existing.get('properties') or {}).get('layer') or 'base'} layer) — nothing drafted."
            else:
                decision_id, did = await self._act_record_decision(store, verdict, candidate, info["facts"], actor, [ent["id"]], context)
                props = {"layer": "authored", "status": "draft", "authored_by": actor,
                         "extension_id": self._act_extension(context),
                         "authored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "decision_id": decision_id, "description": arguments.get("description") or "",
                         "data_type": arguments.get("data_type") or ""}
                if verdict.decision == "value_domain" and arguments.get("values"):
                    props["allowed_values"] = arguments.get("values")
                node_id = await store.add_entity(name=node_name, entity_type="attribute", properties=props,
                                                 source="authored", source_detail=actor)
                await store.add_relationship(ent["id"], node_id, "contains", source="authored")
                await store.add_relationship(did, node_id, "relates_to", source="authored")
                drafted = f"Drafted '{node_name}' on the authored layer (decision {decision_id})."
        elif draft and verdict.decision == "entity":
            drafted = (f"Not drafted as an attribute: {verdict.rule} says '{attr}' is an entity of its own — "
                       f"propose it with propose_entity (from_attribute='{attr}', relates_to='{ent['name']}').")
        elif draft:
            drafted = "Not drafted: the evidence does not decide — state what is known and ask."
        act = None
        if not node_id and not draft and verdict.decision in ("attribute", "value_domain"):
            act = {"kg_id": getattr(store, "kg_id", None),
                   "args": {"action": "propose_attribute", "entity": ent["name"], "attribute_name": attr,
                            "description": arguments.get("description") or "", "data_type": arguments.get("data_type") or "",
                            "values": arguments.get("values") or None, "declared": arguments.get("declared") or None,
                            "decision": arguments.get("decision") or None}}
        html = self._act_proposal_html("attribute", attr, verdict, drafted, ent["name"], act,
                                       drafted_kg=getattr(store, "kg_id", None) if node_id else None,
                                       target_node=f"{ent['name']}.{attr}")
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE, html=html,
            title=f"Proposed attribute {attr} on {ent['name']} · {verdict.decision}"
                  + (" · provisional" if verdict.provisional else "") + (" · drafted" if node_id else ""),
            metadata={"tool_name": self.tool_name, "action": "propose_attribute", "entity": ent["name"],
                      "attribute": attr, "verdict": verdict.to_dict(), "drafted": bool(node_id), "layer": "authored",
                      "measured": info["measured"], "answer_surface": {"open": True}},
            spec={"action": "propose_attribute", "entity": ent["name"], "attribute": attr,
                  "verdict": verdict.to_dict(), "drafted": bool(node_id), "node_id": node_id,
                  "note": drafted or "", "next": _act_next_step(bool(node_id), act is not None)},
        )

    async def _handle_propose_entity(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Propose a new entity on the authored layer. A declared entity wins over the
        inferred reading (a contradiction is a finding); the draft carries its attributes,
        its key and a reference to the entity it belongs to."""
        refusal = self._act_refusal("propose_entity", context)
        if refusal is not None:
            return refusal
        store = self._get_store(context)
        name = str(arguments.get("entity_name") or "").strip()
        if not name:
            return self._error_payload("propose_entity needs entity_name")
        parent_name = str(arguments.get("relates_to") or arguments.get("entity") or "").strip()
        parent = await self._find_entity_any_layer(store, parent_name) if parent_name else None
        if parent_name and not parent:
            return self._error_payload(f"no entity named '{parent_name}' in this graph")
        from_attr = str(arguments.get("from_attribute") or "").strip()
        raw_attrs = arguments.get("attributes") or []
        attrs: List[Dict[str, Any]] = []
        for a in raw_attrs if isinstance(raw_attrs, (list, tuple)) else []:
            if isinstance(a, dict) and a.get("name"):
                attrs.append({"name": str(a["name"]), "data_type": a.get("data_type") or "",
                              "description": a.get("description") or "",
                              **({"label": str(a["label"])} if a.get("label") else {}),
                              **({"added": str(a["added"])} if a.get("added") in ("key", "reference") else {})})
            elif isinstance(a, str) and a.strip():
                # a model also writes "Name:TYPE" — the name is the part before the colon
                nm, _, ty = a.strip().partition(":")
                attrs.append({"name": nm.strip(), "data_type": ty.strip(), "description": ""})
        # A person's words become the model's terms IN CODE (business_terms): identifiers in the
        # model's own naming style (the words kept as labels), a key of its own unless one was
        # named, and the reference to what it belongs to named like that entity's declared key —
        # so a proposal in plain words lands, and nobody has to know a column.
        from trusted_data_agent.agent.kg_constructors import business_terms as _bt
        from trusted_data_agent.kgstore.keys import key_columns as _key_columns
        try:
            _model_attrs = [e.get("name") for e in (await _maybe_await(
                store.list_entities(entity_type="attribute", limit=400)) or [])]
        except Exception:  # noqa: BLE001 — no names to read is the default style
            _model_attrs = []
        _pkey = _key_columns((parent or {}).get("properties") or {}) if parent else []
        _pkey_types: Dict[str, str] = {}
        for _pk in _pkey:
            try:
                _pa = await _maybe_await(store.get_entity_by_name(f"{parent['name']}.{_pk}"))
                _pp = (_pa or {}).get("properties") or {}
                if _pp.get("deployed_type") or _pp.get("declared_type"):
                    _pkey_types[_pk] = str(_pp.get("deployed_type") or _pp.get("declared_type"))
            except Exception:  # noqa: BLE001 — an unread type is inferred like any other
                pass
        _done = _bt.complete(name, attrs, key=arguments.get("key") if isinstance(arguments.get("key"), (list, tuple))
                             else ([arguments["key"]] if arguments.get("key") else None),
                             parent_name=parent["name"] if parent else None, parent_key=_pkey or None,
                             parent_key_types=_pkey_types, model_names=_model_attrs)
        attrs = _done["attributes"]
        for _a in attrs:
            _a.setdefault("description", "")
            _a.setdefault("data_type", "")
        from trusted_data_agent.agent.kg_constructors.normalisation import infer_attribute_type
        for a in attrs:
            if not a["data_type"]:
                guess = infer_attribute_type(a["name"])
                if guess:
                    a["data_type"], a["type_reason"] = guess
        key = list(_done["key"])            # named by the person, or the concept's own identifier
        candidate: Dict[str, Any] = {"name": from_attr or name, "description": arguments.get("description"),
                                     "chosen": arguments.get("decision")}
        verdict, info = await self._act_verdict(store, parent, candidate, context, declared="entity")
        actor = self._act_actor(context)
        drafted: Optional[str] = None
        node_id: Optional[int] = None
        if bool(arguments.get("draft", False)) and verdict.decision == "entity":
            root = (parent["name"].split(".")[0] if parent and "." in parent["name"] else "")
            node_name = f"{root}.{name}" if root and "." not in name else name
            if await _maybe_await(store.get_entity_by_name(node_name)):
                drafted = f"'{node_name}' already exists — nothing drafted."
            else:
                links = [parent["id"]] if parent else []
                decision_id, did = await self._act_record_decision(store, verdict, candidate, info["facts"], actor, links, context)
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                ext_id = self._act_extension(context)
                props = {"layer": "authored", "status": "draft", "authored_by": actor, "authored_at": stamp,
                         "extension_id": ext_id,
                         "decision_id": decision_id, "description": arguments.get("description") or "",
                         "business_key": ", ".join(key)}
                if from_attr and parent:
                    props["split_from"] = f"{parent['name']}.{from_attr}"
                node_id = await store.add_entity(name=node_name, entity_type="entity", properties=props,
                                                 source="authored", source_detail=actor)
                await store.add_relationship(did, node_id, "relates_to", source="authored")
                for a in attrs:
                    aid = await store.add_entity(name=f"{node_name}.{a['name']}", entity_type="attribute",
                                                 properties={"layer": "authored", "status": "draft", "authored_by": actor,
                                                             "extension_id": ext_id,
                                                             "authored_at": stamp, "decision_id": decision_id,
                                                             "data_type": a["data_type"], "description": a["description"],
                                                             **({"label": a["label"]} if a.get("label") else {}),
                                                             **({"added_by_platform": a["added"]} if a.get("added") else {}),
                                                             **({"type_inferred": a["type_reason"]} if a.get("type_reason") else {})},
                                                 source="authored", source_detail=actor)
                    await store.add_relationship(node_id, aid, "contains", source="authored")
                if parent:
                    await store.add_relationship(node_id, parent["id"], "relates_to",
                                                 metadata={"kind": "belongs_to"}, source="authored")
                drafted = (f"Drafted '{node_name}' on the authored layer with {len(attrs)} attribute(s), key "
                           f"({', '.join(key) or 'none named'}) (decision {decision_id}).")
        elif bool(arguments.get("draft", False)):
            drafted = f"Not drafted: the advisor reads '{name}' as {verdict.decision}" + (f" ({verdict.rule})" if verdict.rule else "") + "."
        act = None
        if not node_id and not bool(arguments.get("draft", False)) and verdict.decision == "entity":
            act = {"kg_id": getattr(store, "kg_id", None),
                   "args": {"action": "propose_entity", "entity_name": name, "relates_to": parent["name"] if parent else "",
                            "attributes": [{k: a[k] for k in ("name", "data_type", "label", "added") if a.get(k)} for a in attrs],
                            "key": key, "description": arguments.get("description") or "",
                            "from_attribute": from_attr or None, "decision": arguments.get("decision") or None}}
        _root = (parent["name"].split(".")[0] if parent and "." in parent["name"] else "")
        html = self._act_proposal_html("entity", name, verdict, drafted, parent["name"] if parent else "", act,
                                       records=attrs, drafted_kg=getattr(store, "kg_id", None) if node_id else None,
                                       target_node=f"{_root}.{name}" if _root and "." not in name else name)
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE, html=html,
            title=f"Proposed entity {name}" + (f" beside {parent['name']}" if parent else "") + f" · {verdict.decision}"
                  + (" · provisional" if verdict.provisional else "") + (" · drafted" if node_id else ""),
            metadata={"tool_name": self.tool_name, "action": "propose_entity", "entity": name,
                      "relates_to": parent["name"] if parent else "", "verdict": verdict.to_dict(),
                      "drafted": bool(node_id), "layer": "authored", "measured": info["measured"],
                      "answer_surface": {"open": True}},
            spec={"action": "propose_entity", "entity": name, "relates_to": parent["name"] if parent else "",
                  "attributes": [a["name"] for a in attrs], "key": key, "verdict": verdict.to_dict(),
                  "drafted": bool(node_id), "node_id": node_id, "note": drafted or "",
                  "next": _act_next_step(bool(node_id), act is not None)},
        )

    @staticmethod
    async def authored_nodes(store) -> Dict[str, Any]:
        """Every node on the AUTHORED layer of ``store``'s graph, with its decision — the one
        reading the Workspace listing and the Govern dialog share. Drafts first."""
        from trusted_data_agent.kgstore import types as _T
        ents = await _maybe_await(store.list_entities(limit=99999))
        auth = [e for e in ents if (e.get("properties") or {}).get("layer") == "authored"]
        # A5: the extensions on this graph — one row per extender, published vs not
        extensions: Dict[str, Dict[str, int]] = {}
        for e in auth:
            if e.get("entity_type") == "design_decision":
                continue
            pr = e.get("properties") or {}
            cell = extensions.setdefault(_T.extension_id_of(pr), {"published": 0, "unpublished": 0})
            cell["published" if _T.is_published_authored(pr) else "unpublished"] += 1
        # an authored ATTRIBUTE of an authored ENTITY travels with it (one draft, listed once);
        # an authored attribute of a BASE entity is a draft of its own
        auth_ids = {e.get("id") for e in auth}
        owner_of: Dict[Any, Any] = {}
        try:
            for r in await _maybe_await(store.list_relationships(relationship_type="contains", limit=999999)):
                if r.get("source_entity_id") in auth_ids and r.get("target_entity_id") in auth_ids:
                    owner_of[r.get("target_entity_id")] = r.get("source_entity_id")
        except Exception:  # noqa: BLE001 — without edges every node lists on its own
            owner_of = {}
        # C3: the graph's own physical infrastructure — where a node's ``deployed_*`` landed
        graph_infra = next((str((e.get("properties") or {}).get("infrastructure") or "teradata") for e in ents
                            if (e.get("properties") or {}).get("layer") == "physical"
                            and e.get("entity_type") in ("schema", "table")), None)
        decisions = {}
        for e in auth:
            if e.get("entity_type") == "design_decision":
                pr = e.get("properties") or {}
                decisions[pr.get("decision_id") or e["name"]] = {
                    "decision_id": pr.get("decision_id"), "rule": pr.get("rule"), "status": pr.get("status"),
                    "evidence": pr.get("evidence") or [], "findings": pr.get("findings") or [],
                    "decided_by": pr.get("decided_by"), "decided_date": pr.get("decided_date"), "label": pr.get("label")}
        def _row(e):
            pr = e.get("properties") or {}
            return {"id": e.get("id"), "name": e.get("name"), "entity_type": e.get("entity_type"),
                    # the person's words for it, its type, and whether the platform added it
                    "label": pr.get("label"), "data_type": pr.get("data_type"),
                    "added_by_platform": pr.get("added_by_platform"),
                    "status": pr.get("status") or "draft", "authored_by": pr.get("authored_by"),
                    "authored_at": pr.get("authored_at"), "decision_id": pr.get("decision_id"),
                    "validated_at": pr.get("validated_at"), "deployed_at": pr.get("deployed_at"),
                    "deployed_table": pr.get("deployed_table"), "deployed_column": pr.get("deployed_column"),
                    "deploy_receipt": pr.get("deploy_receipt"),
                    # C3: every infrastructure the node is realised on (a legacy deployed node:
                    # the graph's own), and each realisation with its receipt and readback
                    "realisations": dict(pr.get("realisations") or {}),
                    "landed_on": sorted(set(pr.get("realisations") or {})
                                        | ({graph_infra or "teradata"} if (pr.get("deployed_table") or pr.get("deployed_column")) else set())),
                    # A4: the standard that validated/built the node and what the warehouse read back
                    "standard": pr.get("standard"), "readback": pr.get("readback"),
                    # A5: which extension (one layer per extender) and the publish stamps
                    "extension_id": _T.extension_id_of(pr),
                    "published_at": pr.get("published_at"), "published_by": pr.get("published_by"),
                    "dangling_edges": list(pr.get("dangling_edges") or []),
                    "decision": decisions.get(pr.get("decision_id") or "")}
        by_id = {e.get("id"): e for e in auth}
        _stage = lambda x: str((x.get("properties") or {}).get("status") or "draft")  # noqa: E731

        def _folds(attr_e) -> bool:
            # an authored attribute travels WITH its authored entity only while they are at the
            # same stage — a draft added to a PUBLISHED entity is a draft of its own (its own
            # road, its own Land and Publish); folded, it read as published and could not be landed
            owner = by_id.get(owner_of.get(attr_e.get("id")))
            return owner is not None and _stage(owner) == _stage(attr_e)
        nodes = []
        for e in auth:
            if e.get("entity_type") == "design_decision":
                continue
            if e.get("entity_type") == "attribute" and _folds(e):
                continue   # folded into its authored entity below
            row = _row(e)
            if e.get("entity_type") == "entity":
                kids = [by_id[k] for k, o in owner_of.items() if o == e.get("id") and k in by_id and _folds(by_id[k])]
                row["attributes"] = sorted(({**_row(k), "leaf": str(k.get("name") or "").rpartition(".")[2]} for k in kids),
                                           key=lambda a: a["leaf"].lower())
            nodes.append(row)
        order = {"draft": 0, "validated": 1, "deployed": 2, "published": 3}
        nodes.sort(key=lambda n: (order.get(n["status"], 9), 0 if n["entity_type"] == "entity" else 1, n["name"]))
        return {"nodes": nodes, "decisions": list(decisions.values()), "count": len(nodes),
                "graph_infrastructure": graph_infra,
                "extensions": [{"extension_id": k, **v} for k, v in sorted(extensions.items())]}

    def _error_payload(self, message: str) -> ComponentRenderPayload:
        return ComponentRenderPayload(component_id=self.component_id, render_target=RenderTarget.INLINE,
                                      html=f"<div class='glass-panel p-3 rounded-lg border'>{message}</div>",
                                      metadata={"tool_name": self.tool_name, "error": message}, spec={"error": message})

    async def _handle_compile_metric(self, arguments: Dict, context: Optional[Dict]) -> ComponentRenderPayload:
        """Compile a KG metric into ready-to-run Teradata SQL for a requested slice.

        The LLM only picks the slice (metric_name + optional dimensions/measures); the
        compilation is deterministic and routes through the shared metric-semantic core
        (``metric_api.resolve_metric_semantics``) — the *same* code the governed BI REST API
        calls, so the AI and BI faces of a metric can never drift (Tier 4.2, "define once,
        consume everywhere"). This handler only *renders* that core result.
        """
        import html as _html
        from components.builtin.knowledge_graph.metric_api import resolve_metric_semantics

        def _err(msg):
            # spec.error is the adapter's failed-call shape (the C4 lesson,
            # fixed for query_product and MISSED here): without it a failed
            # compile read as SUCCESS, no correction fired, the dependency
            # skip never engaged, and the next phase executed a reference to
            # nothing (session dd40d071).
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-sm' style='color:#f87171'>{msg}</p>",
                spec={"error": str(msg)},
                metadata={"tool_name": self.tool_name, "action": "compile_metric", "error": msg})

        store = self._get_store(context)
        metric_name = (arguments.get("metric_name") or "").strip()
        if not metric_name:
            # A malformed/duplicate compile_metric call with no metric (e.g. one the
            # model leaked into its text and the engine re-invoked). Degrade silently
            # — render nothing rather than alarm the user with an error card.
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE, html="",
                metadata={"tool_name": self.tool_name, "action": "compile_metric", "suppressed": True})

        # Distinguish "not provided" (None → all) from "provided empty" ([] → none).
        dims = arguments.get("dimensions")
        if dims is None:
            dims = arguments.get("selected_dimensions")
        meas = arguments.get("measures")
        if meas is None:
            meas = arguments.get("selected_measures")

        try:
            sem = await resolve_metric_semantics(store, metric_name, dimensions=dims, measures=meas)
        except Exception as exc:  # noqa: BLE001 — store lookup / unexpected
            return _err(f"Could not compile metric '{metric_name}': {exc}")
        if not sem.found or sem.error or not sem.compiled_sql:
            return _err(sem.error or f"No metric named '{metric_name}' in this knowledge graph.")

        warn = ""
        if sem.warnings:
            warn = "<div class='text-xs mt-2' style='color:#f59e0b'>" + \
                   "<br>".join(_html.escape(w) for w in sem.warnings) + "</div>"
        if sem.realization == "column" and sem.deployed_column:
            note = (f"direct column <code>{_html.escape(sem.deployed_column)}</code> "
                    f"(read directly, no aggregation)")
        else:
            view_note = (f" · deployed view <code>{_html.escape(sem.deployed_view)}</code> (queryable directly)"
                         if sem.deployed_view else "")
            note = (f"{len(sem.selected_dimensions)} dim · {len(sem.selected_measures)} measure(s) · source "
                    f"<code>{_html.escape(sem.source_table or '')}</code>{view_note}")
        body = (
            f"<div class='text-xs mb-1' style='color:#9ca3af'>Metric <strong>{_html.escape(metric_name)}</strong> · {note}</div>"
            f"<pre class='text-sm p-3 rounded-lg overflow-auto' style='background:#0b1020;color:#d1d5db'>"
            f"{_html.escape(sem.compiled_sql)}</pre>{warn}"
        )
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE, html=body,
            metadata={"tool_name": self.tool_name, "action": "compile_metric",
                      "sql": sem.compiled_sql, "dimensions": sem.selected_dimensions,
                      "measures": sem.selected_measures, "source_table": sem.source_table,
                      "deployed_view": sem.deployed_view, "deployed_column": sem.deployed_column,
                      "realization": sem.realization})

    # -------------------------------------------------------------------
    # OBDA-READ data products — the agent face (Phase B4, TIER_5.5 §6 C3)
    # -------------------------------------------------------------------
    # The doctrine that shaped these: the KG component COMPILES and never
    # executes (the probe_bridge precedent) — executing SQL from inside a
    # component would bypass the MCP chokepoint where Agent Authority enforces
    # deny_writes. So query_product returns governed, deterministic SQL the
    # agent runs through its own governed data tool; the OBDA-READ scanner counts
    # the execution of a compiled product statement as the GOVERNED lane.

    def _product_trust_state(self, kg_id: str, name: str) -> str:
        """Defensive trust read (the Ontology Suite is removable): its absence
        reads 'unavailable', never a claimed state."""
        try:
            from trusted_data_agent.core.trust import products as _tp
            return _tp.product_state(kg_id, name).get("state", "unavailable")
        except Exception:  # noqa: BLE001
            return "unavailable"

    async def _handle_list_products(self, arguments: Dict,
                                    context: Optional[Dict]) -> ComponentRenderPayload:
        """The product catalog for the agent: what exists, what is askable, and the
        trust state of each — deterministic discovery (the obda.catalog reading)."""
        import html as _html
        _pmode, _pgrant = _profile_surface(context)
        if _pmode == "off":
            _msg = ("OBDA-READ is not enabled for this agent (the OBDA-READ component is off "
                    "for it — Administration → Components, or its Capabilities — or its profile "
                    "has no data products configured). Answer with your ordinary data tools instead.")
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-sm text-gray-400'>{_html.escape(_msg)}</p>",
                spec={"error": _msg},
                metadata={"tool_name": self.tool_name, "action": "list_products",
                          "error": _msg, "error_code": "obda_not_enabled_for_agent"})
        store = self._get_store(context)
        try:
            nodes = await store.list_entities(entity_type="data_product", limit=200)
        except Exception as exc:  # noqa: BLE001
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-sm' style='color:#f87171'>Could not list data "
                     f"products: {_html.escape(str(exc))}</p>",
                metadata={"tool_name": self.tool_name, "action": "list_products",
                          "error": str(exc)})
        items = []
        rows_html = []
        for n in nodes:
            props = n.get("properties") or {}
            report = props.get("validation_report")
            trust = self._product_trust_state(store.kg_id, n.get("name"))
            items.append({
                "product": n.get("name"), "version": props.get("version"),
                "backend": props.get("backend"),
                "description": props.get("description") or "",
                "measures": props.get("measure_names") or "",
                "members": props.get("member_entities") or "",
                "validated": isinstance(report, dict),
                "signable": bool(report.get("signable")) if isinstance(report, dict) else False,
                "trust_state": trust,
            })
            rows_html.append(
                f"<div class='text-sm' style='padding:2px 0'>"
                f"<strong>{_html.escape(str(n.get('name')))}</strong> "
                f"<span style='color:#9ca3af'>v{_html.escape(str(props.get('version')))}"
                f" · {_html.escape(str(props.get('backend') or ''))} · "
                f"{_html.escape(trust)}</span></div>")
        items, _lp_note = _mark_on_surface(
            items, surface=(_pgrant if _pgrant else None,
                            _pmode == "exclusive"))
        html_body = ("<div class='text-xs mb-1' style='color:#9ca3af'>Signed data "
                     "products on this ontology</div>" + "".join(rows_html)) \
            if rows_html else \
            "<p class='text-sm' style='color:#9ca3af'>No data products on this ontology.</p>"
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE,
            html=html_body,
            metadata={"tool_name": self.tool_name, "action": "list_products",
                      "products": items,
                          **({"surface_note": _lp_note} if _lp_note else {}), "count": len(items)})

    async def _handle_query_product(self, arguments: Dict,
                                    context: Optional[Dict]) -> ComponentRenderPayload:
        """Compile a §3 request against a signed data product → governed SQL.

        The LLM's ENTIRE job is choosing (product, slice) from the published
        admissible map; compilation is deterministic and shared byte-for-byte with
        the BI face (one compiler). A REJECT is returned VERBATIM with its
        machine-readable code — self-correction food, never a guess. The returned
        SQL is executed by the agent through its own governed data tool; the OBDA-READ
        lane scanner recognises that execution as governed.
        """
        import html as _html
        _pmode, _pgrant = _profile_surface(context)
        if _pmode == "off":
            _msg = ("OBDA-READ is not enabled for this agent (the OBDA-READ component is off "
                    "for it — Administration → Components, or its Capabilities — or its profile "
                    "has no data products configured). Answer with your ordinary data tools instead.")
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-sm text-gray-400'>{_html.escape(_msg)}</p>",
                spec={"error": _msg},
                metadata={"tool_name": self.tool_name, "action": "query_product",
                          "error": _msg, "error_code": "obda_not_enabled_for_agent"})
        from trusted_data_agent.dbdeploy.product_model import (
            ProductManifestError, parse_manifest)
        from trusted_data_agent.dbdeploy.product_compiler import (
            ProductRequestError, admissible_map, compile_request)

        def _err(msg, code=None):
            # `spec.error` is the shape the Optimize adapter recognises as a FAILED
            # tool call (adapter.py: `payload.spec.get("error")` → status error) —
            # without it a refusal reads as success, no correction runs, and the
            # next phase pipes this payload into `sql` (the live C4 failure).
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-sm' style='color:#f87171'>{_html.escape(msg)}</p>",
                spec={"error": msg},
                metadata={"tool_name": self.tool_name, "action": "query_product",
                          "error": msg, **({"error_code": code} if code else {})})

        store = self._get_store(context)
        name = (arguments.get("product_name") or arguments.get("product") or "").strip()
        if not name:
            return _err("query_product needs 'product_name' — use list_products to "
                        "see what exists.", "bad_request_shape")

        # ── THE LIST IS THE GRANT (operator decision #3): in Option B AND
        # Option C, query_product refuses products not on the profile's list —
        # config-driven, so it covers interactive turns that carry no principal.
        # Same wording as the principal check below (SurfaceRefusalStrategy
        # reads it for the deterministic one-product swap).
        if _pgrant and name not in _pgrant:
            return _err(
                f"'{name}' is not on this agent's data surface — its surface "
                f"is: {', '.join(sorted(_pgrant))}",
                "not_on_surface")

        # ── the data surface (Phase C, E2/E4) — FAIL-CLOSED against the ambient
        # principal. Interactive turns carry no principal (E3 — baseline untouched).
        try:
            from trusted_data_agent.core import agent_authority as _aa
            _p = _aa.get_current_principal()
            if _p is not None and _p.scope.data_surface_products is not None:
                _sc = _p.scope
                if name not in _sc.data_surface_products:
                    return _err(
                        f"'{name}' is not on this agent's data surface — its surface "
                        f"is: {', '.join(sorted(_sc.data_surface_products)) or '(empty)'}",
                        "not_on_surface")
                if _sc.data_surface_exclusive and not _sc.data_surface_advisory:
                    _state = self._product_trust_state(store.kg_id, name)
                    if _state != "trusted":
                        # E4: only signed-and-intact versions exist to a surface-only
                        # consumer; an unresolvable state fails CLOSED the same way.
                        return _err(
                            f"data product '{name}' is {_state} — a surface-only "
                            f"agent is served only signed-and-intact (🟢) products; "
                            f"ask the producer to re-validate + re-sign it",
                            "unsigned_product")
        except Exception as _exc:  # noqa: BLE001 — the surface check must fail CLOSED
            return _err(f"data-surface enforcement error — denied ({_exc})",
                        "not_on_surface")

        node = store.get_entity_by_name(name, "data_product")
        if not node:
            try:
                known = [n.get("name") for n in
                         await store.list_entities(entity_type="data_product", limit=50)]
            except Exception:  # noqa: BLE001
                known = []
            return _err(f"No data product named '{name}' on this ontology."
                        + (f" Declared: {', '.join(map(str, known))}" if known else ""),
                        "unknown_member")
        try:
            # §8 — a COMPOSED product compiles against its FLAT form, resolved
            # through the one loader (members live on the same ontology); a base
            # product passes through unchanged.
            from trusted_data_agent.dbdeploy.product_model import load_flat_manifest
            _flat, _cm, _fe = load_flat_manifest(store, name)
            if _fe is not None:
                raise _fe
            parsed = parse_manifest(_flat)
        except ProductManifestError as e:
            return _err(f"The product's manifest is invalid ({e.code}): {e.message}",
                        e.code)

        request = {k: arguments[k] for k in
                   ("measures", "dimensions", "time_grain", "filters", "order_by",
                    "limit", "entity", "attributes") if arguments.get(k) is not None}
        try:
            compiled = compile_request(parsed, request)
        except ProductRequestError as e:
            # the REJECT taxonomy, verbatim — the admissible map rides along so the
            # model can correct in ONE step instead of probing
            amap = None
            try:
                amap = admissible_map(parsed)
            except Exception:  # noqa: BLE001
                pass
            # The correction text IS the tool error (spec.error → the adapter's
            # `data` field → what the correction LLM reads): the verbatim REJECT
            # plus the admissible map, so the model can fix the request in ONE step.
            _corr = f"query_product rejected ({e.code}): {e.message}."
            if amap:
                _corr += (" Admissible measures: "
                          + ", ".join(map(str, amap.get("measures") or [])) or "(none)")
                _corr += ("; dimensions: "
                          + ", ".join(map(str, amap.get("dimensions") or [])))
                _member_reject = (str(getattr(e, "code", "")) == "unknown_member"
                                  and "does not expose" in str(e.message))
                if not _member_reject or "closest declared:" in str(e.message):
                    # a NAMING slip or a non-member reject (empty_request,
                    # grain_conflict, …) — the fix is mechanical, retry is right
                    _corr += (". Correct the request and call query_product again "
                              "(same product).")
                else:
                    # a genuinely UNDECLARED concept (the allowlist is deliberate —
                    # data minimisation). The old tail ("correct the request and
                    # retry") COMMANDED the silent substitution observed live: the
                    # model retried at a coarser dimension and presented the figure
                    # as if it answered the asked question. The honest paths, named:
                    _corr += (". This member is NOT exposed by this product — a "
                              "deliberate contract limit, not a typo. Do NOT "
                              "silently substitute another dimension and present it "
                              "as the asked one. Either answer with an admissible "
                              "dimension AND state explicitly that the requested "
                              "member is not exposed, or report that the question "
                              "cannot be answered at that grain.")
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-sm' style='color:#f59e0b'>Request rejected "
                     f"({_html.escape(e.code)}): {_html.escape(e.message)}</p>",
                spec={"error": _corr},
                metadata={"tool_name": self.tool_name, "action": "query_product",
                          "error": e.message, "error_code": e.code,
                          **({"admissible": amap} if amap else {})})

        # E1's one exception: register the compiled statement so a SURFACE-ONLY agent
        # may execute exactly this SQL through its governed data tool (the turn-scoped
        # registry in agent_authority; fail-open — registration failure only means the
        # execution will be denied, never that ungoverned SQL slips through).
        try:
            from trusted_data_agent.core import agent_authority as _aa
            _aa.register_governed_sql(compiled.sql)
        except Exception:  # noqa: BLE001
            pass

        trust = self._product_trust_state(store.kg_id, name)
        body = (
            f"<div class='text-xs mb-1' style='color:#9ca3af'>Data product "
            f"<strong>{_html.escape(name)}</strong> v{parsed.version} · "
            f"governed &amp; deterministic · signed: {_html.escape(trust)}</div>"
            f"<pre class='text-sm p-3 rounded-lg overflow-auto' "
            f"style='background:#0b1020;color:#d1d5db'>"
            f"{_html.escape(compiled.sql)}</pre>")
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE,
            html=body,
            metadata={"tool_name": self.tool_name, "action": "query_product",
                      "product": name, "version": parsed.version,
                      "sql": compiled.sql,
                      "measures": list(compiled.measures),
                      "dimensions": list(compiled.dimensions),
                      "request_hash": compiled.request_hash,
                      "trust_state": trust, "lane": "governed",
                      "execute_hint": "Run this SQL EXACTLY as returned through "
                                      "your data query tool; do not modify it — "
                                      "the statement is the governed contract."})

    # -------------------------------------------------------------------
    # HTML formatting helpers
    # -------------------------------------------------------------------

    def _format_stats_html(self, stats: Dict[str, Any]) -> str:
        """Format graph statistics as HTML."""
        total_ent = stats.get("total_entities", 0)
        total_rel = stats.get("total_relationships", 0)

        if total_ent == 0:
            return "<p class='text-gray-400'>Knowledge graph is empty. Use 'add_entity' to populate it.</p>"

        lines = [
            f"<div class='glass-panel p-4 rounded-lg'>",
            f"<h4 class='text-white font-semibold mb-2'>Knowledge Graph Statistics</h4>",
            f"<p class='text-gray-300'>Entities: <strong>{total_ent}</strong> | Relationships: <strong>{total_rel}</strong></p>",
        ]

        entity_types = stats.get("entity_types", {})
        if entity_types:
            lines.append("<div class='mt-2 flex flex-wrap gap-2'>")
            for etype, count in sorted(entity_types.items()):
                color = ENTITY_TYPE_COLORS.get(etype, "#6b7280")
                lines.append(
                    f"<span class='px-2 py-1 rounded text-xs' style='background:{color}20;color:{color}'>"
                    f"{etype}: {count}</span>"
                )
            lines.append("</div>")

        components = stats.get("connected_components", 0)
        density = stats.get("density", 0)
        if components:
            lines.append(
                f"<p class='text-gray-400 text-sm mt-2'>Components: {components} | "
                f"Density: {density}</p>"
            )

        lines.append("</div>")
        return "\n".join(lines)

    #: Value chips shown before the disclosure on a lookup card. The chips that match
    #: the question lead, so the one the reader asked about is visible without a click.
    _LOOKUP_VISIBLE_VALUES = 8

    #: The card's stylesheet, emitted ONCE per card (classes, not a style attribute per
    #: chip: with sixty chips the per-chip form was 27K tokens of markup in the session
    #: file). Theme tokens throughout; color-mix for every tint so light mode holds.
    _LOOKUP_CSS = (
        "<style>"
        ".kg-lookup{display:flex;flex-direction:column;gap:8px;min-width:0}"
        ".kg-lk-card{padding:8px 10px;border-radius:8px;min-width:0;"
        "background:color-mix(in srgb, var(--text-muted, #94a3b8) 6%, transparent);border-left:2px solid var(--kg-c)}"
        ".kg-lk-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;min-width:0}"
        ".kg-lk-dot{color:var(--kg-c);font-size:10px;flex:none}"
        ".kg-lk-name{color:var(--text-primary, #e5e7eb);font-weight:600;font-size:14px;overflow-wrap:anywhere}"
        ".kg-lk-owner{color:var(--text-muted, #94a3b8);font-size:12px;overflow-wrap:anywhere}"
        ".kg-lk-chip{display:inline-block;padding:1px 7px;border-radius:9999px;font-size:11px;line-height:18px;"
        "white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis;vertical-align:middle;"
        "background:color-mix(in srgb, var(--text-muted, #94a3b8) 14%, transparent);color:var(--text-muted, #94a3b8)}"
        ".kg-lk-type{background:color-mix(in srgb, var(--kg-c) 14%, transparent);"
        "color:color-mix(in srgb, var(--kg-c) 62%, var(--text-primary, #e5e7eb))}"
        ".kg-lk-hit{background:color-mix(in srgb, var(--teradata-orange, #F15F22) 18%, transparent);"
        "color:var(--text-primary, #e5e7eb);border:1px solid color-mix(in srgb, var(--teradata-orange, #F15F22) 45%, transparent)}"
        ".kg-lk-badges{display:inline-flex;gap:4px;flex-wrap:wrap}"
        ".kg-lk-desc,.kg-lk-note{color:var(--text-muted, #94a3b8);font-size:12px;margin-top:4px;overflow-wrap:anywhere}"
        ".kg-lk-note{font-size:11px;margin-top:6px}"
        ".kg-lk-desc details,.kg-lk-desc summary{display:inline}"
        ".kg-lk-desc summary{cursor:pointer;list-style:none;text-decoration:underline dotted}"
        ".kg-lk-desc summary::-webkit-details-marker{display:none}"
        ".kg-lk-desc details[open] span{display:block;margin-top:3px}"
        ".kg-lk-vlabel{margin-top:6px;font-size:11px;color:var(--text-muted, #94a3b8)}"
        ".kg-lk-vals{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px;min-width:0}"
        ".kg-lk-vals details{display:inline}"
        ".kg-lk-vals summary{cursor:pointer;list-style:none;border:1px dashed color-mix(in srgb, var(--text-muted, #94a3b8) 40%, transparent)}"
        ".kg-lk-vals summary::-webkit-details-marker{display:none}"
        ".kg-lk-more{display:inline-flex;flex-wrap:wrap;gap:4px;margin-top:4px}"
        "</style>"
    )

    def _format_query_results_html(self, results: List[Dict], query: str) -> str:
        """Format search results as cards a PERSON can read — the body of an evidence row.

        The reader's side of the same contract the enrichment pushes. The facts the
        model needs travel as TEXT in the payload metadata (see `_handle_query`), so
        this markup owes the agent nothing and owes the reader a result that reads as
        one: the attribute's leaf name at full width, its owner entity beside it, the
        governance facts as badges, the description as one line, and the decoded
        value domain as chips — the ones matching the question first, the rest behind
        a disclosure. COMPLETE, still: every declared value is in the markup (a lookup
        exists to resolve the enrichment's `+N more`), it is just not a wall of text.

        Collapsed by default, the platform's idiom for evidence under an answer (a
        chart's rows under "Show Details", a Focus answer's "Source Documents"): the
        model has already restated the entities in its own words above the card, so
        the card is the tool's evidence, one click away — and its summary line alone
        says what was looked up and how much came back, so it is never a blank slot.

        The previous form was a flex row whose facts span held the whole 50-value
        domain, so the name column shrank until it wrapped LETTER BY LETTER
        ("P / oli / ci / n / g …") and the two-line fact the reader wanted — which
        attribute, on which entity, PII — sat under fifty offence labels.
        """
        try:
            terms = _lookup_terms(query)
        except Exception:  # noqa: BLE001 — ranking is an optimisation, never the fix
            terms = []
        # The body is FLAT: the Answer Surface row that wraps every component output is the
        # disclosure (its summary line carries "N entities matching 'x'"), so a second
        # <details> here was two chevrons for one gesture.
        lines = [
            self._LOOKUP_CSS,
            "<div class='kg-lookup'>",
        ]
        for ent in results:
            props = ent.get("properties") or {}
            etype = str(ent.get("entity_type") or "")
            color = ENTITY_TYPE_COLORS.get(etype, "#6b7280")
            parts = [x for x in str(ent.get("name") or "").split(".") if x]
            leaf = html_escape(parts[-1] if parts else str(ent.get("name") or ""))
            owner = html_escape(" \u203a ".join(parts[:-1])) if len(parts) > 1 else ""

            badges: List[str] = []
            if props.get("is_pii"):
                badges.append("PII")
            cls = str(props.get("data_classification") or "").strip()
            if cls and cls.upper() != "PUBLIC":
                badges.append(cls.upper())
            # a logical attribute carries deployed_type; a physical column carries datatype
            dtype = str(props.get("deployed_type") or props.get("datatype") or "").strip()
            if dtype and dtype.upper() != "UNKNOWN":
                badges.append(dtype)
            badge_html = "".join(f"<span class='kg-lk-chip'>{html_escape(b)}</span>" for b in badges)

            # Badges stay IN FLOW after the owner, never pushed to the right edge: in a
            # narrow panel a right-aligned group wraps onto its own line and floats
            # away from the name it describes.
            head = (
                "<div class='kg-lk-head'>"
                "<span class='kg-lk-dot'>&#9679;</span>"
                f"<span class='kg-lk-name'>{leaf}</span>"
                f"<span class='kg-lk-chip kg-lk-type'>{html_escape(etype)}</span>"
                + (f"<span class='kg-lk-owner'>in {owner}</span>" if owner else "")
                + (f"<span class='kg-lk-badges'>{badge_html}</span>" if badge_html else "")
                + "</div>"
            )
            # The description's FIRST clause is the discriminator ("UCR ARREST OFFENSE
            # CODE", "TYPE OF VICTIM"); a product's full codebook prose is four lines of
            # detail the reader opens when they want it, never a wall they scroll past.
            desc = " ".join(str(props.get("description") or "").split())
            lead = desc if len(desc) <= _CODED_DESC_CHARS else _short_desc(props)
            desc_html = ""
            if desc:
                more_desc = ""
                if lead != desc:
                    more_desc = (" <details><summary>full description</summary>"
                                 f"<span>{html_escape(desc)}</span></details>")
                desc_html = f"<div class='kg-lk-desc'>{html_escape(lead)}{more_desc}</div>"

            # The value domain: coded columns carry code=label pairs; the decoded-label
            # half of a two-column decode carries labels only. Both fold a declared range
            # to one chip, exactly as the text renderer does.
            items: List[Tuple[str, str]] = []
            raw = props.get("allowed_values")
            if isinstance(raw, list) and raw:
                seen_ranges: set = set()
                folded: List[Dict[str, Any]] = []
                for it in raw:
                    if not isinstance(it, dict):
                        continue
                    rng = it.get("range")
                    if rng:
                        if rng in seen_ranges:
                            continue
                        seen_ranges.add(rng)
                        folded.append({"value": str(rng), "label": it.get("label", "")})
                    else:
                        folded.append(it)
                for it in _rank_codes(folded, terms):
                    v, lbl = str(it.get("value", "")).strip(), str(it.get("label", "")).strip()
                    if v or lbl:
                        items.append((v, lbl))
            elif props.get("decoded_labels"):
                items = [("", str(x)) for x in props["decoded_labels"] if str(x).strip()]
            domain_label = ("values" if raw else
                            f"decoded label of {html_escape(str(props.get('decoded_from') or ''))} \u2014 values")

            values_html = ""
            if items:
                lowered = [t.lower() for t in terms]
                def is_hit(v: str, lbl: str) -> bool:
                    hay = f"{v} {lbl}".lower()
                    return any(t and t in hay for t in lowered)
                def one(v: str, lbl: str) -> str:
                    text = html_escape(f"{v} = {lbl}" if v and lbl else (v or lbl))
                    return f"<span class='kg-lk-chip{' kg-lk-hit' if is_hit(v, lbl) else ''}'>{text}</span>"
                # hits first (already ranked by _rank_codes), then the product's order
                hits = [x for x in items if is_hit(*x)]
                rest = [x for x in items if not is_hit(*x)]
                ordered = hits + rest
                vis = ordered[: max(self._LOOKUP_VISIBLE_VALUES, len(hits))]
                hidden = ordered[len(vis):]
                row = "".join(one(*x) for x in vis)
                more = ""
                if hidden:
                    more = (f"<details><summary class='kg-lk-chip'>+{len(hidden)} more</summary>"
                            "<span class='kg-lk-more'>" + "".join(one(*x) for x in hidden) + "</span></details>")
                values_html = (f"<div class='kg-lk-vlabel'>{domain_label} ({len(items)})</div>"
                               f"<div class='kg-lk-vals'>{row}{more}</div>")

            # The remaining governance facts (deployed-as-text traps, date shapes, the
            # sibling that qualifies this column) — the same sentences the agent reads,
            # as a quiet note; the values themselves are the chips above.
            already = {"PII", cls, dtype}   # the badges above; a note must not restate a slot
            notes = [x for x in _governance_parts(props, complete=True)
                     if not x.startswith("values:") and not x.startswith("decoded label of")
                     and not x.startswith("decoded label column:") and x not in already]
            refs = props.get("refers_to")
            if isinstance(refs, list) and refs:
                notes.append("qualified by " + ", ".join(str(r) for r in refs[:3]))
            notes_html = f"<div class='kg-lk-note'>{html_escape('; '.join(notes))}</div>" if notes else ""

            lines.append(f"<div class='kg-lk-card' style='--kg-c:{color}'>{head}{desc_html}{values_html}{notes_html}</div>")
        lines.append("</div>")
        return "\n".join(lines)

    # -------------------------------------------------------------------
    # Store access helpers
    # -------------------------------------------------------------------

    def _policy_write_refusal(self, action: str, context: Optional[Dict]):
        """The deployment's OBDA-MODEL policy on a DIRECT graph edit by an agent: `enforce`
        refuses (recovery-shaped — propose, a person publishes); `observe` records a
        `policy_observed` event and lets it through; `off` or no principal → None."""
        try:
            from trusted_data_agent.core.agent_authority import get_current_principal
            principal = get_current_principal()
            if principal is None:
                return None
            mode = str(getattr(principal.scope, "policy_model", "off") or "off")
            if mode == "off":
                return None
            if mode == "observe":
                try:
                    from trusted_data_agent.core import trust_events as _te
                    _te.record_event(principal.owner_uuid, "policy_observed",
                                     f"POLICY OBSERVED: {principal.principal_id} edited the graph directly "
                                     f"('{action}') — the deployment's contracts-only model policy would refuse it under enforce",
                                     severity="warning",
                                     payload={"tool": self.tool_name, "action": action, "plane": "model",
                                              "profile_id": principal.profile_id})
                except Exception:  # noqa: BLE001
                    pass
                return None
            msg = (f"'{action}' is refused: this deployment's model policy is contracts-only, so an agent "
                   f"changes the model only through a proposal a person publishes. NEXT STEP: call "
                   f"TDA_ModelProposal (action='propose_entity' or 'propose_attribute') if it is offered "
                   f"to you, and present the advisor's verdict; otherwise tell the user what you would "
                   f"have added to the model.")
            return ComponentRenderPayload(
                component_id=self.component_id, render_target=RenderTarget.INLINE,
                html=f"<p class='text-amber-400'>{msg}</p>",
                spec={"error": msg, "refused": "policy_contracts_only", "action": action},
                metadata={"tool_name": self.tool_name, "action": action, "error": True,
                          "refused": "policy_contracts_only", "plane": "model", "suppress_inline": True})
        except Exception:  # noqa: BLE001 — an undecidable policy refuses nothing it cannot name
            return None

    def _released_write_refusal(self, action: str, context: Optional[Dict]):
        """A ComponentRenderPayload refusing a write for a released-mode agent, else None."""
        try:
            ctx = context or {}
            profile_id = ctx.get("profile_id", "__default__")
            user_uuid = ctx.get("user_uuid", "system")
            owner = self._get_active_kg_owner(profile_id, user_uuid) or profile_id
            _store, grounding = self._resolve_grounding_store(owner, user_uuid, profile_id)
            if grounding.get("mode") != "released" or not grounding.get("kg_id"):
                return None
            label = grounding.get("label") or grounding.get("version_id") or "the release"
            msg = (f"This agent grounds on the RELEASED ontology ({label}), which is read-only. "
                   f"'{action}' would edit the draft the agent cannot see. NEXT STEP: report the "
                   f"proposed change to the ontology owner, who edits the draft and publishes a "
                   f"new release; or set this agent's knowledge-graph 'Ground on' to the live draft.")
            return ComponentRenderPayload(
                component_id=self.component_id,
                render_target=RenderTarget.INLINE,
                html=f"<p class='text-amber-400'>{msg}</p>",
                spec={"error": msg, "refused": "released_read_only", "action": action},
                metadata={"tool_name": self.tool_name, "action": action, "error": True,
                          "refused": "released_read_only", "grounding": grounding,
                          "suppress_inline": True},
            )
        except Exception:  # noqa: BLE001 — a refusal helper must never break a write on Model A
            return None

    def _get_store(self, context: Optional[Dict]) -> Any:
        """Get GraphStore scoped to the active KG owner for this profile + user."""
        from components.builtin.knowledge_graph.graph_store import GraphStore

        ctx = context or {}
        profile_id = ctx.get("profile_id", "__default__")
        user_uuid = ctx.get("user_uuid", "system")

        # Resolve through assignment table (same logic as get_context_enrichment)
        active_kg_owner = self._get_active_kg_owner(profile_id, user_uuid)
        kg_pid = active_kg_owner if active_kg_owner else profile_id

        # Model B: a released-mode agent's tool READS see the released view too — an
        # agent must not be told one thing by its enrichment and another by its tool.
        store, _grounding = self._resolve_grounding_store(kg_pid, user_uuid, profile_id)
        return store

    def _get_store_direct(self, profile_id: str, user_uuid: str) -> Any:
        """Get GraphStore with explicit profile/user (for context enrichment)."""
        from components.builtin.knowledge_graph.graph_store import GraphStore

        return GraphStore(profile_id, user_uuid)

    def _resolve_grounding_store(self, kg_pid: str, user_uuid: str,
                                 agent_profile_id: Optional[str]) -> Tuple[Any, Dict[str, Any]]:
        """THE store an agent grounds on — Model A (live) or Model B (released view).

        `kg_pid` is the KG OWNER profile (resolved through the assignment table);
        `agent_profile_id` is the agent whose `componentConfig.knowledge_graph.groundOn`
        decides. Every KG read seam (enrichment, trusted scope, the tool's reads) calls
        this one method, so the three can never disagree about which graph is read.
        Returns (store, grounding) where `grounding` is the event-facing description
        from `kg_release.resolve_grounding`. Fails toward the live graph."""
        from components.builtin.knowledge_graph.graph_store import GraphStore
        live = GraphStore(kg_pid, user_uuid)
        try:
            from trusted_data_agent.core import kg_release as _rel
            profile = None
            if agent_profile_id and user_uuid:
                from trusted_data_agent.core.config_manager import ConfigManager
                profile = ConfigManager().get_profile(agent_profile_id, user_uuid)
            grounding = _rel.resolve_grounding(getattr(live, "kg_id", None), profile, user_uuid)
            if grounding.get("kg_id"):
                return GraphStore(kg_pid, user_uuid, kg_id=grounding["kg_id"]), grounding
            return live, grounding
        except Exception as exc:  # noqa: BLE001 — never break retrieval
            logger.debug(f"KG grounding resolution fell back to live: {exc}")
            return live, {"mode": "live"}

    def _get_active_kg_owner(self, profile_id: str, user_uuid: str) -> Optional[str]:
        """
        Look up which KG is currently ACTIVE for this profile via kg_profile_assignments.

        Returns the owner profile_id of the active KG, or None if no KG is active.
        The active KG could be the profile's own (self-assignment) or another profile's KG.
        Only one KG can be active per profile at a time (enforced by partial unique index).
        """
        import sqlite3

        try:
            from trusted_data_agent.core.config import APP_CONFIG
            db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
        except Exception:
            db_path = "tda_auth.db"

        try:
            conn = sqlite_util.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT kg_owner_profile_id FROM kg_profile_assignments "
                "WHERE assigned_profile_id = ? AND user_uuid = ? AND is_active = 1",
                (profile_id, user_uuid),
            )
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Failed to query active KG assignment: {e}")
            return None
