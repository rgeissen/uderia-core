"""
database_context — ontology-conformant helpers for the *database-context*
(agent-discovery) KG constructor.

This module is the deterministic, **pure** core that turns a discovered database
schema into the canonical two-layer KG topology
(ONTOLOGY_ARCHITECTURE §2.2–2.5): a LOGICAL model (`database` → `entity` →
`attribute`) and the PHYSICAL realization (`schema` → `table` → `column`, every
node `infrastructure`-tagged), linked by `implements`.

Design imperatives honored here:

* **No hardcoded MCP tool or prompt names.** This module never names a tool. The
  caller resolves which available tool performs each discovery role — either from
  explicit modal config or via :func:`tool_role_derivation_messages` (the LLM picks
  from the profile's *available* tools, a judgement task). The deterministic
  iteration then calls whatever was resolved.
* **Determinism where possible; LLM only for judgement.** Building the graph from a
  schema is pure and unit-tested; the LLM is used only to (a) map tool→role and
  (b) enrich semantics — never to enumerate tables/columns.
* **Reuse over replacement.** :func:`build_physical_layer` derives the physical
  layer from *already-discovered* logical structural entities, so it composes with
  the existing agent discovery + semantic enrichment + FK-inference phases unchanged.

Field-name flexibility (TableName vs table_name …) is data-shape heuristics, NOT
tool-name hardcoding — different MCP servers label result columns differently.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

# Heuristic result-field name sets (shape detection, not tool identity).
TABLE_NAME_FIELDS = {"TableName", "table_name", "tablename", "Name", "ObjectName", "object_name"}
COLUMN_NAME_FIELDS = {"ColumnName", "column_name", "columnname", "Column", "col_name"}
TYPE_FIELDS = {"Type", "DataType", "data_type", "ColumnType", "column_type", "ColumnFormat", "datatype", "CType", "ctype"}

SOURCE_TAG = "agent_discovery"


# ── infrastructure (from the modal's "Target Database" field — never hardcoded) ──

def derive_infrastructure(target_database: Optional[str]) -> str:
    """Map the modal's free-text *Target Database* field to an ``infrastructure``
    tag for physical nodes (e.g. "Teradata" → "teradata"). Empty → "unknown"."""
    s = (target_database or "").strip().lower()
    return re.sub(r"[^a-z0-9_]+", "_", s).strip("_") or "unknown"


def _find_field(item: Dict[str, Any], candidates: set) -> Optional[str]:
    for k in item:
        if k in candidates and item.get(k) not in (None, ""):
            return str(item[k])
    # case-insensitive fallback
    low = {k.lower(): k for k in item}
    for c in candidates:
        if c.lower() in low and item.get(low[c.lower()]) not in (None, ""):
            return str(item[low[c.lower()]])
    return None


# ── pure two-layer graph builder ────────────────────────────────────────────────

def build_two_layer_graph(
    database_name: str,
    tables: List[Dict[str, Any]],
    infrastructure: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Pure: a discovered schema → canonical logical + physical KG delta.

    ``tables`` shape (produced by :func:`discover_schema_via_tools` or the parser)::

        [{"name": "Customers",
          "columns": [{"name": "CustomerID", "datatype": "INTEGER",
                       "is_primary_key": bool?}, ...],
          "foreign_keys": [{"column", "ref_table", "ref_column"}]?}]

    Emits both layers (ONTOLOGY_ARCHITECTURE §2.2–2.5):
      logical : database → entity → attribute            (storage type NOT here)
      physical: schema   → table  → column (datatype)    (infrastructure-tagged)
      implements: schema→database, table→entity, column→attribute
    """
    entities: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []

    def add_entity(name, etype, props):
        entities.append({"name": name, "entity_type": etype,
                         "properties": props, "source": SOURCE_TAG})

    def add_rel(sn, st, tn, tt, rt):
        relationships.append({"source_name": sn, "source_type": st,
                              "target_name": tn, "target_type": tt,
                              "relationship_type": rt, "source": SOURCE_TAG})

    db = database_name
    # roots: logical `database`, physical `schema` (the live DB IS the schema)
    add_entity(db, "database", {"layer": "logical", "description": f"Database {db}"})
    add_entity(db, "schema", {"layer": "physical", "infrastructure": infrastructure, "label": db})
    add_rel(db, "schema", db, "database", "implements")

    for t in tables:
        tname = (t.get("name") or "").strip()
        if not tname:
            continue
        ent = tname                      # logical entity (bare name, per the discovery convention)
        ptbl = f"{db}.{tname}"           # physical table == deployed identifier
        add_entity(ent, "entity", {"layer": "logical",
                                   "description": t.get("description") or None})
        add_entity(ptbl, "table", {"layer": "physical", "infrastructure": infrastructure,
                                   "label": tname})
        add_rel(db, "database", ent, "entity", "contains")
        add_rel(db, "schema", ptbl, "table", "contains")
        add_rel(ptbl, "table", ent, "entity", "implements")

        for c in t.get("columns") or []:
            cname = (c.get("name") or "").strip()
            if not cname:
                continue
            attr = f"{tname}.{cname}"            # logical attribute
            pcol = f"{db}.{tname}.{cname}"       # physical column
            is_pk = bool(c.get("is_primary_key"))
            # logical attribute carries MEANING; storage type stays physical (§2.4c)
            add_entity(attr, "attribute", {"layer": "logical",
                                           "is_primary_key": is_pk or None})
            pcol_props = {"layer": "physical", "infrastructure": infrastructure, "label": cname}
            if c.get("datatype"):
                pcol_props["datatype"] = c["datatype"]
            if is_pk:
                pcol_props["is_primary_key"] = True
            add_entity(pcol, "column", pcol_props)
            add_rel(ent, "entity", attr, "attribute", "contains")
            add_rel(ptbl, "table", pcol, "column", "contains")
            add_rel(pcol, "column", attr, "attribute", "implements")

    # explicit foreign keys (best-effort; the existing phase-3.5 inference still runs too)
    tnames = {(t.get("name") or "").strip() for t in tables}
    for t in tables:
        src = (t.get("name") or "").strip()
        for fk in t.get("foreign_keys") or []:
            ref = (fk.get("ref_table") or "").strip()
            col, rcol = (fk.get("column") or "").strip(), (fk.get("ref_column") or "").strip()
            if not (src and ref and ref in tnames):
                continue
            add_rel(src, "entity", ref, "entity", "foreign_key")               # logical
            add_rel(f"{db}.{src}", "table", f"{db}.{ref}", "table", "foreign_key")  # physical
            if col and rcol:
                add_rel(f"{src}.{col}", "attribute", f"{ref}.{rcol}", "attribute", "foreign_key")
                add_rel(f"{db}.{src}.{col}", "column", f"{db}.{ref}.{rcol}", "column", "foreign_key")

    return {"entities": entities, "relationships": relationships}


def build_physical_layer_from_logical(
    database_name: str,
    logical_entities: List[Dict[str, Any]],
    logical_relationships: List[Dict[str, Any]],
    infrastructure: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Reuse path: derive ONLY the physical layer (+ implements) from already-stored
    LOGICAL structural entities/relationships, so the physical layer composes with
    the existing agent discovery without re-querying the database.

    Mirrors every logical `entity`→`table`, `attribute`→`column`, and the `contains`
    / `foreign_key` edges into the physical namespace, with `implements` links.
    """
    db = database_name
    entities: List[Dict[str, Any]] = [
        {"name": db, "entity_type": "schema",
         "properties": {"layer": "physical", "infrastructure": infrastructure, "label": db},
         "source": SOURCE_TAG}
    ]
    relationships: List[Dict[str, Any]] = [
        {"source_name": db, "source_type": "schema", "target_name": db,
         "target_type": "database", "relationship_type": "implements", "source": SOURCE_TAG}
    ]

    ent_by_name = {e["name"]: e for e in logical_entities if e.get("entity_type") == "entity"}
    attr_by_name = {e["name"]: e for e in logical_entities if e.get("entity_type") == "attribute"}

    # which entity owns each attribute (from contains edges) → to qualify physical names
    owner_of_attr: Dict[str, str] = {}
    for r in logical_relationships:
        if (r.get("relationship_type") == "contains"
                and r.get("source_type") == "entity" and r.get("target_type") == "attribute"):
            owner_of_attr[r["target_name"]] = r["source_name"]

    for ename in ent_by_name:
        ptbl = f"{db}.{ename}"
        entities.append({"name": ptbl, "entity_type": "table",
                         "properties": {"layer": "physical", "infrastructure": infrastructure,
                                        "label": ename}, "source": SOURCE_TAG})
        relationships.append({"source_name": db, "source_type": "schema", "target_name": ptbl,
                              "target_type": "table", "relationship_type": "contains", "source": SOURCE_TAG})
        relationships.append({"source_name": ptbl, "source_type": "table", "target_name": ename,
                              "target_type": "entity", "relationship_type": "implements", "source": SOURCE_TAG})

    for aname, aent in attr_by_name.items():
        owner = owner_of_attr.get(aname)
        if not owner:
            continue
        # physical column name: <db>.<owner>.<bare col>  (attr name is "<owner>.<col>")
        bare = aname.split(".", 1)[1] if "." in aname else aname
        pcol = f"{db}.{owner}.{bare}"
        props = {"layer": "physical", "infrastructure": infrastructure, "label": bare}
        aprops = aent.get("properties") or {}
        dtype = aprops.get("data_type") or aprops.get("CType") or aprops.get("datatype")
        if dtype:
            props["datatype"] = dtype
        if aprops.get("is_primary_key"):
            props["is_primary_key"] = True
        entities.append({"name": pcol, "entity_type": "column", "properties": props, "source": SOURCE_TAG})
        relationships.append({"source_name": f"{db}.{owner}", "source_type": "table",
                              "target_name": pcol, "target_type": "column",
                              "relationship_type": "contains", "source": SOURCE_TAG})
        relationships.append({"source_name": pcol, "source_type": "column", "target_name": aname,
                              "target_type": "attribute", "relationship_type": "implements", "source": SOURCE_TAG})

    # mirror logical FK edges into the physical namespace
    for r in logical_relationships:
        if r.get("relationship_type") != "foreign_key":
            continue
        st, tt = r.get("source_type"), r.get("target_type")
        sn, tn = r.get("source_name"), r.get("target_name")
        if st == "entity" and tt == "entity":
            relationships.append({"source_name": f"{db}.{sn}", "source_type": "table",
                                  "target_name": f"{db}.{tn}", "target_type": "table",
                                  "relationship_type": "foreign_key", "source": SOURCE_TAG})
        elif st == "attribute" and tt == "attribute":
            so, to = owner_of_attr.get(sn), owner_of_attr.get(tn)
            if so and to:
                relationships.append({
                    "source_name": f"{db}.{so}.{sn.split('.',1)[1]}", "source_type": "column",
                    "target_name": f"{db}.{to}.{tn.split('.',1)[1]}", "target_type": "column",
                    "relationship_type": "foreign_key", "source": SOURCE_TAG})

    return {"entities": entities, "relationships": relationships}


# ── tool-role resolution (LLM-derived from AVAILABLE tools; never hardcoded) ─────

ROLE_KEYS = ("list_tables", "describe_columns", "table_ddl", "run_sql")


def tool_role_derivation_messages(available_tools: List[Dict[str, Any]], database_name: str) -> str:
    """Build the LLM prompt that maps discovery roles → available MCP tools.

    ``available_tools``: ``[{"name", "description", "args"?}, ...]`` for THIS profile's
    MCP server (passed in by the caller — this module never enumerates tools itself).
    When ``args`` (the tool's real argument names) is provided, the LLM is told to pick
    ``db_arg``/``table_arg`` ONLY from those names, so its first guess is correct (no
    arg-name probing needed). Returns the prompt string.
    """
    def _fmt(t):
        args = t.get("args") or []
        argpart = f"  (args: {', '.join(args)})" if args else ""
        return f"- {t.get('name')}{argpart}: {t.get('description','')[:140]}"
    catalog = "\n".join(_fmt(t) for t in available_tools)
    return (
        "You are mapping schema-discovery ROLES to the MCP tools actually available "
        "to this connection. Do NOT invent tool names — choose only from the list. "
        "Choose db_arg/table_arg ONLY from the chosen tool's listed args (verbatim).\n\n"
        f"Target database: {database_name}\n\nAVAILABLE TOOLS:\n{catalog}\n\n"
        "Return ONLY JSON of this exact shape (use null when no suitable tool exists):\n"
        "{\n"
        '  "list_tables":      {"tool": "<name>", "db_arg": "<arg for database name>"},\n'
        '  "describe_columns": {"tool": "<name>", "db_arg": "<db arg>", "table_arg": "<arg for table/object name>"},\n'
        '  "table_ddl":        {"tool": "<name>", "db_arg": "<db arg>", "table_arg": "<table arg>"} | null,\n'
        '  "run_sql":          {"tool": "<name>", "sql_arg": "<arg that takes a SQL SELECT statement>"} | null\n'
        "}\n"
        "Pick the tool that lists tables in a database for list_tables; the tool that "
        "returns a table's columns and their data types for describe_columns; and a "
        "SHOW-TABLE / DDL tool for table_ddl if one exists; and the tool that runs a "
        "read-only SQL SELECT and returns its rows for run_sql if one exists."
    )


def parse_tool_roles(text: str) -> Dict[str, Optional[Dict[str, str]]]:
    """Parse the LLM's role→tool JSON. Tolerant of markdown fences / surrounding prose."""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        s = s[4:] if s[:4].lower() == "json" else s
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    out: Dict[str, Optional[Dict[str, str]]] = {}
    for k in ROLE_KEYS:
        v = data.get(k)
        out[k] = v if isinstance(v, dict) and v.get("tool") else None
    return out


def _rows_from_result(result: Any) -> List[Dict[str, Any]]:
    """Normalize an MCP tool result to a list of row dicts (best-effort)."""
    if isinstance(result, dict):
        for key in ("results", "rows", "data"):
            r = result.get(key)
            if isinstance(r, list):
                return [x for x in r if isinstance(x, dict)]
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    return []


# Common argument-name synonyms (NOT tool names) — probed when the preferred arg
# name is wrong, so discovery adapts to any MCP server without the LLM having to
# guess the exact argument name (which is the unreliable step).
_DB_ARG_CANDIDATES = ["database_name", "database", "db_name", "db", "schema_name", "schema", "databaseName"]
_TABLE_ARG_CANDIDATES = ["obj_name", "table_name", "object_name", "table", "name", "tablename", "objectName", "objName"]


def _call_succeeded(res: Any) -> bool:
    """Did the server ACCEPT the call (a success result, rows or none)? An error result or a
    raised refusal is not acceptance."""
    return isinstance(res, dict) and str(res.get("status") or "").lower() == "success"


async def _call_first_nonempty(invoke, tool, value, preferred, candidates,
                               row_field, extra_args=None, trust_preferred=False):
    """Call ``tool`` trying ``preferred`` then each candidate arg name until the
    result yields at least one row carrying ``row_field``. Returns (rows, used_arg).

    A wrong arg name comes back as an error result (→ no rows), so 'first arg that
    produces rows' is a reliable, schema-free way to find the right argument.

    ``trust_preferred``: ``preferred`` is the name the tool's own schema DECLARES (or one
    that already worked). If the server accepts it, an empty result is the answer — the
    object is empty — not a sign of a wrong name; probing every candidate after it sent
    names the tool does not have, which a FastMCP 4 server refuses at ~0.9 s each
    (trust campaign C12: 515 refusals, a navigator call past a turn's 300 s cap)."""
    order = ([preferred] if preferred else []) + [a for a in candidates if a != preferred]
    for arg in order:
        args = {arg: value}
        if extra_args:
            args.update(extra_args)
        try:
            res = await invoke(tool, args)
        except Exception:
            continue
        rows = _rows_from_result(res)
        if any(_find_field(r, row_field) for r in rows):
            return rows, arg
        if trust_preferred and arg == preferred and _call_succeeded(res):
            return rows, arg
    # last resort: no-arg call (some list tools default to the connected DB)
    if not extra_args:
        try:
            rows = _rows_from_result(await invoke(tool, {}))
            if any(_find_field(r, row_field) for r in rows):
                return rows, None
        except Exception:
            pass
    return [], None


async def discover_schema_via_tools(
    invoke: Callable[[str, Dict[str, Any]], Any],
    roles: Dict[str, Optional[Dict[str, str]]],
    database_name: str,
) -> List[Dict[str, Any]]:
    """Deterministic schema discovery using the RESOLVED tool roles.

    ``invoke(tool_name, arguments) -> result`` is an async callable supplied by the
    caller (wraps ``invoke_mcp_tool``); this function never names a tool itself. The
    LLM-resolved arg names are used as the *preferred* first try, but the actual arg
    name is probed (arg-name synonyms) so a wrong LLM guess can't break discovery.
    Returns the ``tables`` structure consumed by :func:`build_two_layer_graph`.
    """
    lt = roles.get("list_tables")
    dc = roles.get("describe_columns")
    if not lt or not lt.get("tool"):
        return []

    rows, _used_db_arg = await _call_first_nonempty(
        invoke, lt["tool"], database_name, (lt or {}).get("db_arg"),
        _DB_ARG_CANDIDATES, TABLE_NAME_FIELDS)
    table_names: List[str] = []
    seen: set = set()
    for row in rows:
        tn = _find_field(row, TABLE_NAME_FIELDS)
        if tn and tn not in seen:
            seen.add(tn)
            table_names.append(tn)

    # describe-columns: PROBE BOTH the db-arg and the table-arg on the first table,
    # then reuse the resolved pair. Seed the db-arg order with the one that just
    # worked for list_tables (same MCP server → usually identical), so it resolves
    # on the first call instead of grinding through every combination.
    db_pref = _used_db_arg or (dc or {}).get("db_arg")
    tbl_pref = (dc or {}).get("table_arg")
    dc_db_resolved: Optional[str] = None
    dc_tbl_resolved: Optional[str] = None
    tables: List[Dict[str, Any]] = []
    for tn in table_names:
        cols: List[Dict[str, Any]] = []
        if dc and dc.get("tool"):
            if dc_db_resolved is None:
                crows, da, ta = await _probe_two_args(
                    invoke, dc["tool"], database_name, tn, db_pref, tbl_pref,
                    _DB_ARG_CANDIDATES, _TABLE_ARG_CANDIDATES, COLUMN_NAME_FIELDS)
                if crows:
                    dc_db_resolved, dc_tbl_resolved = da, ta
            else:
                try:
                    cres = await invoke(dc["tool"],
                                        {dc_db_resolved: database_name, dc_tbl_resolved: tn})
                except Exception:
                    cres = None
                crows = _rows_from_result(cres)
            for row in crows:
                cn = _find_field(row, COLUMN_NAME_FIELDS)
                if cn:
                    cols.append({"name": cn, "datatype": _find_field(row, TYPE_FIELDS)})
        tables.append({"name": tn, "columns": cols})
    return tables


async def _probe_two_args(invoke, tool, db_value, tbl_value, db_pref, tbl_pref,
                          db_candidates, tbl_candidates, row_field, trust_preferred=False):
    """Probe (db_arg × table_arg) combinations until the tool returns rows carrying
    ``row_field``. Preferred names are tried first. Returns (rows, db_arg, tbl_arg).

    ``trust_preferred``: both preferred names are DECLARED by the tool's schema; if the
    server accepts that pair, its (possibly empty) result is the answer and the grid is
    never walked (see ``_call_first_nonempty``; trust campaign C12)."""
    db_order = ([db_pref] if db_pref else []) + [a for a in db_candidates if a != db_pref]
    tbl_order = ([tbl_pref] if tbl_pref else []) + [a for a in tbl_candidates if a != tbl_pref]
    for da in db_order:
        for ta in tbl_order:
            try:
                res = await invoke(tool, {da: db_value, ta: tbl_value})
            except Exception:
                continue
            rows = _rows_from_result(res)
            if any(_find_field(r, row_field) for r in rows):
                return rows, da, ta
            if (trust_preferred and db_pref and tbl_pref and da == db_pref and ta == tbl_pref
                    and _call_succeeded(res)):
                return rows, da, ta
    return [], db_pref, tbl_pref


async def validate_graph_via_tool(
    invoke: Callable[[str, Dict[str, Any]], Any],
    run_sql_role: Optional[Dict[str, str]],
    graph: Dict[str, List[Dict[str, Any]]],
    infrastructure: str = "",
) -> Dict[str, Any]:
    """Validate a discovered two-layer graph against the SOURCE through the resolved
    ``run_sql`` tool role — the same pure core every constructor uses
    (`kg_constructors.source_validation`), with an MCP tool as the runner. Stamps the
    logical nodes of ``graph`` in place. Applicable only when a SQL-running tool was
    resolved; otherwise a no-op that says so. Never raises."""
    from trusted_data_agent.agent.kg_constructors import source_validation as _sv
    tool = (run_sql_role or {}).get("tool") if isinstance(run_sql_role, dict) else None
    if not tool:
        return {"skipped": "no run_sql tool resolved"}
    sql_arg = (run_sql_role or {}).get("sql_arg") or "sql"

    async def _run(sql: str):
        return _rows_from_result(await invoke(tool, {sql_arg: sql}))

    dialect = "teradata" if "teradata" in str(infrastructure or "").lower() else "generic"
    return await _sv.validate_with_source(
        graph.get("entities") or [], graph.get("relationships") or [], _run, dialect=dialect)
