"""The workload recommender's evidence (PROP-008 §4.5) — gathered, never invented.

`dbdeploy/tuning.py` decides; this module only READS what the platform already keeps and
hands it over in the recommender's shape:

    graph_scope(kg_id)                the graph's deployed tables and each one's declared key
    collect_workload(user, tables)    the statements the agents ran on those tables, from the
                                      persisted turns (one item per occurrence — frequency is
                                      evidence)
    explain_workload(creds, items)    EXPLAIN of each DISTINCT statement, read-only, the
                                      findings attributed to the base tables each step read
    gather_facts(creds, tables, keys) rows, columns + types, statistics with their age, the
                                      join indexes already on each table, partitioning — through
                                      the Database Navigator's one catalog contract
    review_graph(...)                 the four, then `tuning.review`, then the DDL rendered

Nothing here writes: every SQL statement it sends is a dictionary read or ``EXPLAIN`` of a
single SELECT that passed the shared guard. A statement that cannot be explained is kept as
workload without findings (its columns still count); a table whose facts cannot be read is
named in ``unavailable``, never guessed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("quart.app")

MAX_SESSIONS = 300          # newest sessions read for the workload
MAX_STATEMENTS = 600        # occurrences kept
MAX_EXPLAINS = 40           # distinct statements explained per review


def _norm(sql: str) -> str:
    return " ".join(str(sql or "").split()).rstrip(";").strip()


# ── the graph's scope ─────────────────────────────────────────────────────────

def graph_scope(entities: List[Dict[str, Any]], relationships: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """``{deployed_table (db.table, lower): declared key columns}``. The deployed name comes from
    a physical ``table`` node or an entity's ``deployed_table`` / ``deployed``; the key from the
    logical entity that table ``implements`` (or names it as deployed), read through the ONE
    `kgstore.keys.key_columns`. An empty key means none is declared — the dictionary's primary
    index is used later, and said so."""
    from trusted_data_agent.kgstore.keys import key_columns
    by_name = {e.get("name"): e for e in entities or [] if isinstance(e, dict)}
    out: Dict[str, List[str]] = {}

    def add(table: str, ent: Optional[Dict[str, Any]]) -> None:
        t = str(table or "").strip().lower()
        if not t or "." not in t:
            return
        key = key_columns((ent or {}).get("properties") or {}) if ent else []
        if t not in out or (key and not out[t]):
            out[t] = [k for k in key]

    for e in entities or []:
        if not isinstance(e, dict):
            continue
        props = e.get("properties") or {}
        if e.get("entity_type") == "table" and props.get("layer") == "physical":
            add(e.get("name"), None)
        dep = props.get("deployed_table") or props.get("deployed")
        if e.get("entity_type") == "entity" and isinstance(dep, str):
            add(dep, e)
    for r in relationships or []:
        if r.get("relationship_type") == "implements" and r.get("source_type") == "table":
            add(r.get("source_name"), by_name.get(r.get("target_name")))
    return out


def scope_for_kg(kg_id: str, db_path: Optional[str] = None) -> Dict[str, List[str]]:
    from trusted_data_agent.core.trust.composition import load_kg_graph
    ents, rels = load_kg_graph(kg_id, db_path)
    return graph_scope(ents, rels)


# ── the workload ──────────────────────────────────────────────────────────────

def _in_scope(sql: str, tables: Iterable[str]) -> bool:
    from trusted_data_agent.dbdeploy.tuning import analyze_statement
    want = {t.lower() for t in tables}
    leaves = {t.split(".", 1)[1] for t in want if "." in t}
    for t in analyze_statement(sql)["tables"]:
        if t in want or ("." not in t and t in leaves):
            return True
    return False


def workload_from_turns(turns: Iterable[Tuple[str, Dict[str, Any]]], tables: Iterable[str],
                        limit: int = MAX_STATEMENTS) -> List[Dict[str, Any]]:
    """Every SUCCESSFUL data statement of the given turns that reads one of ``tables`` — one
    item per occurrence, so how often a statement ran is part of the evidence. Pure."""
    from trusted_data_agent.eval.types import RunRecord
    tables = list(tables)
    out: List[Dict[str, Any]] = []
    for sid, turn in turns:
        if not isinstance(turn, dict):
            continue
        for i, c in enumerate(RunRecord(turn).tool_calls()):
            sql = (c.get("arguments") or {}).get("sql")
            if not isinstance(sql, str) or not sql.strip():
                continue
            if sql.lstrip().upper().startswith("EXPLAIN"):
                continue     # the agent explained a query; it did not run one
            if str(c.get("status") or "success").lower() in ("error", "failed", "failure"):
                continue
            if not _in_scope(sql, tables):
                continue
            out.append({"id": f"{sid}:{turn.get('turn')}:{i}", "sql": _norm(sql), "session_id": sid,
                        "turn": turn.get("turn"), "profile_tag": turn.get("profile_tag")})
            if len(out) >= limit:
                return out
    return out


def agents_for_kg(user_uuid: str, kg_id: str, db_path: Optional[str] = None) -> List[str]:
    """The tags of the caller's agents grounded on this graph — its owner and every profile it is
    assigned to. Empty when nothing can be read (the caller then reads every session)."""
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.core.sqlite_util import connect
        con = connect(db_path or "tda_auth.db")
        try:
            ids = {r[0] for r in con.execute("SELECT profile_id FROM kg_metadata WHERE kg_id = ?", (kg_id,))}
            try:
                ids |= {r[0] for r in con.execute(
                    "SELECT assigned_profile_id FROM kg_profile_assignments WHERE kg_id = ?", (kg_id,))}
            except Exception:  # noqa: BLE001
                pass
        finally:
            con.close()
        return sorted({(p.get("tag") or "").lstrip("@") for p in get_config_manager().get_profiles(user_uuid) or []
                       if p.get("id") in ids and p.get("tag")})
    except Exception as e:  # noqa: BLE001
        logger.info(f"tuning: agents of {kg_id} unread: {e}")
        return []


async def collect_workload(user_uuid: str, tables: Iterable[str], agent_tags: Iterable[str] = (),
                           max_sessions: int = MAX_SESSIONS) -> List[Dict[str, Any]]:
    """The statements of the sessions in which the graph's agents ran (``agent_tags``; every
    session when none are known), newest first, at most ``max_sessions`` read in full. The
    listing is summaries only, so filtering it is cheap. Archived sessions are still recorded
    workload (a pack reinstall archives its profiles' sessions; their statements did not stop
    being evidence)."""
    from trusted_data_agent.core import session_manager
    tags = {str(t).lstrip("@").lower() for t in agent_tags or () if t}
    listing = await session_manager.get_all_sessions(user_uuid, limit=None, include_archived=True)
    chosen = []
    for s in (listing or {}).get("sessions") or []:
        used = {str(t).lstrip("@").lower() for t in (s.get("profile_tags_used") or [])}
        used.add(str(s.get("profile_tag") or "").lstrip("@").lower())
        if not tags or used & tags:
            chosen.append(s)
        if len(chosen) >= max_sessions:
            break
    turns: List[Tuple[str, Dict[str, Any]]] = []
    for s in chosen:
        sid = s.get("id") or s.get("session_id")
        if not sid:
            continue
        try:
            sess = await session_manager.get_session(user_uuid, sid)
        except Exception:  # noqa: BLE001
            continue
        for t in ((sess or {}).get("last_turn_data") or {}).get("workflow_history") or []:
            turns.append((sid, t))
    return workload_from_turns(turns, tables)


# ── EXPLAIN ───────────────────────────────────────────────────────────────────

def findings_with_tables(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The plan parser's findings, each naming the BASE tables its steps read (spools left out),
    so a finding counts only against those tables. Pure."""
    steps = {s.get("id"): s for s in plan.get("steps") or []}
    out = []
    for f in plan.get("findings") or []:
        tabs: List[str] = []
        for sid in f.get("steps") or []:
            for i in (steps.get(sid) or {}).get("inputs") or []:
                if not str(i).startswith("Spool ") and str(i).lower() not in tabs:
                    tabs.append(str(i).lower())
        out.append({"kind": f.get("kind"), "severity": f.get("severity"), "tables": tabs})
    return out


def plan_shape(plan: Dict[str, Any]) -> str:
    """How a statement runs, without the numbers: each step's inputs, access path, join
    method and distribution. Two plans with the same shape run the same way — a landing
    that changes no shape changed nothing about how the workload executes. Pure."""
    import hashlib
    sig = [[[str(i).lower() for i in st.get("inputs") or []], [str(a).lower() for a in st.get("access") or []],
            st.get("join_method"), st.get("distribution"), bool(st.get("sort"))] for st in plan.get("steps") or []]
    return hashlib.sha256(json.dumps(sig, separators=(",", ":")).encode()).hexdigest()[:16]


async def explain_workload(creds: Dict[str, Any], items: List[Dict[str, Any]],
                           max_explains: int = MAX_EXPLAINS) -> Dict[str, Any]:
    """EXPLAIN each distinct statement (read-only, one SELECT each) and attach the findings and
    the estimated seconds to every occurrence. Returns ``{explained, failed}``."""
    try:
        from components.builtin.explain_plan import plan_parser
    except ImportError:  # the Explain Plan component is enterprise: no plan is read
        return {"explained": 0, "failed": len(items), "reason": "explain_plan_not_installed"}
    from trusted_data_agent.dbcatalog.sql_runner import rows_as_dicts, run_rows, single_select_guard
    plans: Dict[str, Optional[Dict[str, Any]]] = {}
    counts: Dict[str, int] = {}
    for it in items:
        counts[it["sql"]] = counts.get(it["sql"], 0) + 1
    explained = failed = 0
    for sql in sorted(counts, key=lambda s: -counts[s])[:max_explains]:
        try:
            body = single_select_guard(sql)
            cols, rows = await run_rows(creds, "EXPLAIN " + body)
            plan = plan_parser.parse(plan_parser.explain_text(rows_as_dicts(cols, rows)))
            # a plan rests on a GUESS when any step's estimate is not high-confidence (Teradata
            # says "no confidence" without statistics, "low" when it falls back to sampling or
            # heuristics) — what a before/after estimate must be read against
            guessed = any(str(st.get("confidence") or "").lower() in ("no", "low") for st in plan.get("steps") or [])
            plans[sql] = {"findings": findings_with_tables(plan), "est_seconds": plan.get("total_seconds") or 0.0,
                          "guessed": guessed, "shape": plan_shape(plan)}
            explained += 1
        except Exception as e:  # noqa: BLE001
            plans[sql] = None
            failed += 1
            logger.info(f"tuning: EXPLAIN skipped for a workload statement: {str(e)[:160]}")
    for it in items:
        p = plans.get(it["sql"])
        if p:
            it["findings"] = p["findings"]
            it["est_seconds"] = p["est_seconds"]
            it["guessed"] = p["guessed"]
            it["shape"] = p["shape"]
    return {"explained": explained, "failed": failed, "distinct": len(counts)}


# ── facts ─────────────────────────────────────────────────────────────────────

def _age_days(ts: Any, now: datetime) -> Optional[float]:
    if not ts:
        return None
    try:
        t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace(" ", "T")[:26])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max(0.0, (now - t).total_seconds() / 86400.0)
    except Exception:  # noqa: BLE001
        return None


async def gather_facts(creds: Dict[str, Any], tables: Dict[str, List[str]], *,
                       provider=None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Per deployed table: rows, column count and types, the declared key (the dictionary's
    primary index when the graph declares none, said so), statistics with their age, the join
    indexes already defined on it with their columns, and whether it is partitioned."""
    from trusted_data_agent.dbcatalog.sql_runner import rows_as_dicts, run_rows
    from trusted_data_agent.dbcatalog.teradata_provider import TeradataCatalogProvider, sql_columns
    now = now or datetime.now(timezone.utc)
    prov = provider or TeradataCatalogProvider("tuning", "tuning", creds)
    lit = lambda s: str(s).replace("'", "''")  # noqa: E731
    facts: Dict[str, Any] = {"unavailable": []}
    for table, key in tables.items():
        if "." not in table:
            continue
        db, name = table.split(".", 1)
        try:
            d = await prov.describe_object(db, name)
        except Exception as e:  # noqa: BLE001
            facts["unavailable"].append(f"{table}: {str(e)[:120]}")
            continue
        kind = str(getattr(d.info, "kind", "table") or "table").lower()
        if kind == "view":
            # Teradata keeps statistics, indexes and partitions on TABLES ("Statistics operations
            # are not supported on the object", Error 3706) — a view is recorded with the tables
            # it reads, never handed to the rules (Act II rehearsal: 11 statistics proposals on
            # the Policing Core views, every one of which the database would have refused)
            from trusted_data_agent.dbcatalog.teradata_provider import sql_view_text
            from trusted_data_agent.dbdeploy import tuning as _tuning
            base: List[str] = []
            try:
                cols, rows = await run_rows(creds, sql_view_text(db, name))
                text = " ".join(str(r[0] or "") for r in rows)
                me = table.lower()
                base = sorted(t for t in _tuning.analyze_statement(text).get("tables") or []
                              if "." in t and t.lower() != me)      # the view's own name, from its DDL
            except Exception as e:  # noqa: BLE001
                facts["unavailable"].append(f"{table}: view definition unread ({str(e)[:80]})")
            facts[table] = {"kind": "view", "base_tables": base, "rows": None,
                            "columns_count": len(d.columns), "key": list(key or []),
                            "key_source": "declared" if key else "none", "partitioned": False}
            continue
        f: Dict[str, Any] = {
            "rows": d.info.row_count, "columns_count": len(d.columns),
            "column_types": {c.name: c.data_type for c in d.columns},
            "stats": {},
            "join_indexes": [], "partitioned": False,
        }
        try:   # statistics as the dictionary names them: a multi-column statistic is "A,B"
            from trusted_data_agent.dbcatalog.teradata_provider import sql_column_stats
            cols, rows = await run_rows(creds, sql_column_stats(db, name))
            for r in rows_as_dicts(cols, rows):
                nm = str(r.get("col") or "").strip()
                if nm:
                    f["stats"][nm] = _age_days(r.get("ts"), now)
        except Exception as e:  # noqa: BLE001
            facts["unavailable"].append(f"{table}: statistics unread ({str(e)[:80]})")
        if key:
            f["key"], f["key_source"] = list(key), "declared"
        else:
            pi = next((i.columns for i in d.indexes if i.kind in ("primary", "primary_key")), [])
            f["key"], f["key_source"] = list(pi), ("primary index" if pi else "none")
        try:
            cols, rows = await run_rows(creds, (
                "SELECT TRIM(JoinIdxDataBaseName) AS jdb, TRIM(JoinIdxName) AS jname FROM DBC.JoinIndicesV "
                f"WHERE DataBaseName = '{lit(db)}' AND TableName = '{lit(name)}'"))
            for r in rows_as_dicts(cols, rows):
                c2, r2 = await run_rows(creds, sql_columns(r["jdb"], r["jname"]))
                f["join_indexes"].append([x["columnname"] for x in rows_as_dicts(c2, r2)])
        except Exception as e:  # noqa: BLE001
            facts["unavailable"].append(f"{table}: join indexes unread ({str(e)[:80]})")
        try:
            cols, rows = await run_rows(creds, (
                "SELECT COUNT(*) AS n FROM DBC.IndexConstraintsV "
                f"WHERE DatabaseName = '{lit(db)}' AND TableName = '{lit(name)}' AND ConstraintType = 'Q'"))
            f["partitioned"] = bool(rows and int(rows[0][0] or 0) > 0)
        except Exception as e:  # noqa: BLE001
            facts["unavailable"].append(f"{table}: partitioning unread ({str(e)[:80]})")
        if f["rows"] is None:
            f["rows_source"] = "unknown — no statistics on this table"
        facts[table] = f
    return facts


# ── one review ────────────────────────────────────────────────────────────────

async def review_graph(user_uuid: str, kg_id: str, connection_id: str, *,
                       db_path: Optional[str] = None) -> Dict[str, Any]:
    """The workload review of one graph against one connection: scope → workload → EXPLAIN →
    facts → `tuning.review` → DDL rendered per proposal. Proposals only."""
    from trusted_data_agent.core.database_connections import get_database_connection_store
    from trusted_data_agent.dbdeploy import tuning
    from trusted_data_agent.dbdeploy.dialects.teradata import render_tuning
    from trusted_data_agent.kgstore import usage
    creds = get_database_connection_store().resolve(user_uuid, connection_id)
    if not creds:
        raise ValueError(f"connection '{connection_id}' not found")
    if str(creds.get("driver") or "").lower() != "teradata":
        raise ValueError("the workload review reads a Teradata dictionary — pick a Teradata connection")
    scope = scope_for_kg(kg_id, db_path)
    if not scope:
        return {"proposals": [], "findings": [], "workload": {"statements": 0},
                "note": "this graph has no deployed tables — nothing to tune"}
    agents = agents_for_kg(user_uuid, kg_id, db_path)
    items = await collect_workload(user_uuid, scope.keys(), agents)
    ex = await explain_workload(creds, items) if items else {"explained": 0, "failed": 0, "distinct": 0}
    used = {t for it in items for t in tuning.analyze_statement(it["sql"])["tables"]}
    facts = await gather_facts(creds, {t: scope[t] for t in scope if t in used or t.split(".")[-1] in used})
    missed = [n for n, c in (usage.stats(kg_id) or {}).items() if (c or {}).get("missed")]
    out = tuning.review(items, facts, usage_missed=missed)
    by_sid: Dict[str, str] = {}
    for it in items:
        by_sid.setdefault(str(it.get("id") or tuning._sid(it["sql"])), it["sql"])
    for p in out["proposals"]:
        p["ddl"] = render_tuning(p)
        # the workload statements that motivated it — EXPLAINed again before/after a landing
        p["measure_sql"] = list(dict.fromkeys(by_sid[s] for s in p.get("statements") or [] if s in by_sid))[:10]
    out["explain"] = ex
    out["tables"] = {t: {k: v for k, v in f.items() if k in ("rows", "columns_count", "key", "key_source",
                                                              "partitioned", "rows_source", "kind", "base_tables")}
                     for t, f in facts.items() if t != "unavailable"}
    out["unavailable"] = facts.get("unavailable") or []
    out["kg_id"], out["connection_id"] = kg_id, connection_id
    out["workload"]["agents"] = agents
    return out
