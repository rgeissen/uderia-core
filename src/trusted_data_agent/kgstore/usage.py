"""KG entity usage telemetry — a SEPARATE store, deliberately not `kg_entities`.

Why a separate table: writing per-turn usage counters onto `kg_entities.properties`
would invalidate the process-wide NetworkX graph cache (Phase D) on every turn and
mutate the graph the trust/attestation hashes are taken over. Keeping usage in its
own `kg_entity_usage` table means recording never touches the graph — no cache
invalidation, no hash churn, no read-path cost.

What it records, from the retrieval path (deterministic, off the hot path):
  * ``seeded``   — the entity was in the ranked seed set for a turn,
  * ``rendered`` — the entity was in the subgraph actually shown to the model,
  * ``used``     — an optional stronger signal a caller may record when it observes
                   the entity's deployed name in a successful data query.

What it feeds: a bounded ranking PRIOR (`priors()`), 0.0 when there is no data, so an
un-instrumented graph ranks byte-identically. The prior only breaks near-ties and
nudges consistently-used entities up; it can never displace a real lexical match
(see `_USAGE_PRIOR_MAX` at the call site).

All writes are best-effort and never raise into a turn.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("quart.app")

#: ``missed`` — the entity's table was READ by a turn's SQL while the enrichment had
#: not shown it: a retrieval miss, recorded so the prior can pull the entity forward
#: the next time (the strongest possible evidence the graph should have offered it).
_KINDS = ("seeded", "rendered", "used", "missed")


def _db_path() -> str:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return (getattr(APP_CONFIG, "AUTH_DB_PATH", "") or "tda_auth.db").replace("sqlite:///", "") or "tda_auth.db"
    except Exception:  # noqa: BLE001
        return "tda_auth.db"


def _connect():
    from trusted_data_agent.core import sqlite_util
    conn = sqlite_util.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kg_entity_usage (
            kg_id          TEXT NOT NULL,
            entity_name    TEXT NOT NULL,
            seeded_count   INTEGER NOT NULL DEFAULT 0,
            rendered_count INTEGER NOT NULL DEFAULT 0,
            used_count     INTEGER NOT NULL DEFAULT 0,
            last_used_at   TEXT,
            PRIMARY KEY (kg_id, entity_name)
        )
        """
    )
    # self-applying: the `missed` counter (Sep 2026) on a table created before it
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(kg_entity_usage)").fetchall()}
        if "missed_count" not in cols:
            conn.execute("ALTER TABLE kg_entity_usage ADD COLUMN missed_count INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    except Exception:  # noqa: BLE001
        pass
    return conn


def record(kg_id: str, entity_names: Iterable[str], kind: str) -> int:
    """Increment ``kind`` for each of ``entity_names`` on ``kg_id``. Best-effort; 0 on failure.

    Returns the number of rows touched. Deduplicates names within one call so a name
    that appears twice in a subgraph counts once per turn.
    """
    if kind not in _KINDS:
        return 0
    names = sorted({str(n) for n in (entity_names or []) if n})
    if not kg_id or not names:
        return 0
    col = f"{kind}_count"
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = _connect()
        try:
            conn.executemany(
                f"""
                INSERT INTO kg_entity_usage (kg_id, entity_name, {col}, last_used_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(kg_id, entity_name)
                DO UPDATE SET {col} = {col} + 1, last_used_at = excluded.last_used_at
                """,
                [(kg_id, n, now) for n in names],
            )
            conn.commit()
            return len(names)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — telemetry must never break a turn
        logger.debug(f"kg usage record failed ({kind}, kg={kg_id}): {exc}")
        return 0


#: A leaf shorter than this, or in this set, appears in almost any SQL — "id", "name",
#: "date" — and would read as "touched" on every turn. The rule is deliberately
#: conservative: it prefers under-counting utilisation to inventing it.
_TOUCH_MIN_LEN = 3
_TOUCH_STOP = frozenset({"id", "key", "name", "type", "date", "time", "value", "code",
                         "count", "total", "status", "data", "row", "rows", "col", "table",
                         "database", "schema", "int", "str", "num", "all", "and", "or", "not"})


def _leaf(name: str) -> str:
    return str(name or "").rpartition(".")[2].strip()


def _call_text(call: Dict) -> str:
    """The text a data-tool call carried — its arguments, serialised — never the result."""
    import json as _json
    args = (call or {}).get("arguments")
    try:
        return _json.dumps(args, default=str) if not isinstance(args, str) else args
    except Exception:  # noqa: BLE001
        return str(args)


def touched_entities(entity_names: Iterable[str], tool_calls: Iterable[Dict]) -> List[str]:
    """Which of ``entity_names`` a turn's data tool calls actually REFERENCED.

    THE rule, stated once, read by two consumers: the ``kg_seed_utilization`` scorer
    (post-hoc, pure) and the ``used`` telemetry recorder (end of turn, fire-and-forget).
    An entity counts as touched when its qualified name's LEAF (``Victim`` from
    ``NIBRS_Source.Victim``, ``V40191`` from ``…Incident.V40191``) appears as a whole
    identifier, case-insensitively, in the ARGUMENTS of any tool call — the SQL, a
    ``table_name``/``object_name``, a column list. Results are never scanned: a
    row that happens to contain the word is not the agent using the entity.

    Honest limits (documented, not hidden): a metric or business concept whose name
    never appears in SQL cannot read as touched; a generic leaf (``id``, ``date``) is
    excluded rather than counted everywhere. Deterministic; input never mutated.
    """
    import re as _re
    names = [str(n) for n in (entity_names or []) if n]
    if not names:
        return []
    texts = [_call_text(c) for c in (tool_calls or []) if isinstance(c, dict)]
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return []
    out: List[str] = []
    seen = set()
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        leaf = _leaf(n)
        if len(leaf) < _TOUCH_MIN_LEN or leaf.lower() in _TOUCH_STOP:
            continue
        if _re.search(r"(?<![A-Za-z0-9_])" + _re.escape(leaf) + r"(?![A-Za-z0-9_])", blob,
                      flags=_re.IGNORECASE):
            out.append(n)
    return out


def record_used_async(kg_id: str, entity_names: Iterable[str], tool_calls: Iterable[Dict]) -> None:
    """End-of-turn ``used`` telemetry — the third, strongest signal the priors weight.

    Declared in this module's header since Phase E and never recorded by anyone until
    Sep 2026: the priors were ranking on ``seeded``/``rendered`` alone. Computes the
    touched set with the ONE rule above (so telemetry and the scorer cannot disagree
    about what "used" means) and writes off the request path. Never raises."""
    try:
        touched = touched_entities(entity_names, tool_calls)
    except Exception:  # noqa: BLE001
        return
    if not kg_id or not touched:
        return
    import threading

    def _run():
        try:
            record(kg_id, touched, "used")
        except Exception:  # noqa: BLE001
            pass
    try:
        threading.Thread(target=_run, name="kg-usage-used", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def _sql_tables(sql: str) -> List[Tuple[str, str]]:
    """Pure: the base tables a SQL statement READS, as (database_lower, leaf_lower) — sqlglot
    when available, a FROM/JOIN regex otherwise. CTE names and derived tables are not base
    tables and are excluded. Never raises."""
    import re as _re
    out: List[Tuple[str, str]] = []
    if not isinstance(sql, str) or not sql.strip():
        return out
    try:
        import sqlglot
        from sqlglot import exp
        ctes: set = set()
        for stmt in sqlglot.parse(sql, read="teradata"):
            if stmt is None:
                continue
            for c in stmt.find_all(exp.CTE):
                if c.alias:
                    ctes.add(str(c.alias).lower())
            for t in stmt.find_all(exp.Table):
                leaf = str(t.name or "").lower()
                if not leaf or leaf in ctes:
                    continue
                db = str(t.db or "").lower()
                pair = (db, leaf)
                if pair not in out:
                    out.append(pair)
        return out
    except Exception:  # noqa: BLE001
        pass
    for m in _re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)(?:\.([A-Za-z_][\w]*))?", sql, flags=_re.IGNORECASE):
        db, leaf = (m.group(1).lower(), m.group(2).lower()) if m.group(2) else ("", m.group(1).lower())
        if (db, leaf) not in out:
            out.append((db, leaf))
    return out


def tables_read(tool_calls: Iterable[Dict]) -> List[Tuple[str, str]]:
    """Pure: every base table a turn's data tool calls read — the SQL of a raw-SQL call,
    plus the `database_name`/`object_name`|`table_name` pair of a catalog tool call —
    as (database_lower, leaf_lower), deduped, in call order. Framework `TDA_*` tools are
    never data reads. Never raises."""
    out: List[Tuple[str, str]] = []
    for c in tool_calls or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("tool_name") or "")
        if name.startswith("TDA_"):
            continue
        args = c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
        for pair in _sql_tables(args.get("sql") or ""):
            if pair not in out:
                out.append(pair)
        obj = args.get("object_name") or args.get("table_name")
        if isinstance(obj, str) and obj.strip():
            pair = (str(args.get("database_name") or args.get("db_name") or "").lower(), obj.strip().lower())
            if pair not in out:
                out.append(pair)
    return out


def deployed_names(enrichments: Iterable[Dict]) -> List[str]:
    """The DEPLOYED identifiers of the entities an enrichment showed, from its own
    ``entity_facts`` (`Policing Core.Incident` → `Policing_Core.v_Incident`). A shown
    logical entity whose deployed name differs from its own (a `v_` view, a renamed table)
    is shown under that name too — the SQL can only ever name the deployed one. Pure,
    tolerant of any event shape; empty when no facts were recorded."""
    out: List[str] = []
    for e in enrichments or []:
        if not isinstance(e, dict):
            continue
        facts = e.get("entity_facts")
        if not isinstance(facts, dict):
            continue
        for f in facts.values():
            if isinstance(f, dict) and f.get("deployed"):
                d = str(f["deployed"])
                if d not in out:
                    out.append(d)
    return out


def _shown_table_leaves(entity_names: Iterable[str], deployed: Optional[Iterable[str]] = None) -> set:
    """The table LEAVES an enrichment showed: a two-segment name is a table (`domain.table`
    / `db.table`), a three-segment name is a column and contributes its table segment; a
    shown entity's DEPLOYED identifier (``deployed``) contributes its own leaf."""
    out: set = set()
    for d in deployed or []:
        if d:
            out.add(str(d).rpartition(".")[2].lower())
    for n in entity_names or []:
        parts = str(n or "").split(".")
        if len(parts) == 2:
            out.add(parts[1].lower())
        elif len(parts) >= 3:
            out.add(parts[-2].lower())
    return out


def unshown_tables(entity_names: Iterable[str], tool_calls: Iterable[Dict],
                   deployed: Optional[Iterable[str]] = None) -> List[str]:
    """Which tables a turn's data tool calls READ that the enrichment had NOT shown — the
    inverse of ``touched_entities`` and the ONE definition of an enrichment MISS, read by
    the ``enrichment_recall`` scorer and the ``missed`` telemetry recorder.

    A table counts as shown when its leaf appears among the shown entity names (as a table
    or as a column's owner) — leaf-level on purpose: the enrichment renders logical
    names (`loyalty.ffp_member`) and the SQL names deployed ones
    (`airlines_core.ffp_member`), and the leaf is what the two share. Returns the missed
    tables as `db.leaf` (or bare leaf), deduped, in read order. Empty when nothing was
    shown (no enrichment ⇒ no miss to attribute) or nothing was read.

    ``deployed`` (`deployed_names(enrichments)`): the shown entities' deployed identifiers.
    The leaf is NOT always shared — the policing core model deploys Incident as
    `v_Incident`, so every turn that read it scored a miss on a table the enrichment had
    shown, and the `missed` prior pulled the physical twin forward (session 34f5d1b6)."""
    shown = _shown_table_leaves(entity_names, deployed)
    if not shown:
        return []
    out: List[str] = []
    for db, leaf in tables_read(tool_calls):
        if leaf in shown:
            continue
        label = f"{db}.{leaf}" if db else leaf
        if label not in out:
            out.append(label)
    return out


def record_missed_async(kg_id: str, entity_names: Iterable[str], tool_calls: Iterable[Dict],
                        table_entity_names: Iterable[str],
                        deployed: Optional[Iterable[str]] = None,
                        entity_deployed: Optional[Dict[str, str]] = None) -> None:
    """End-of-turn ``missed`` telemetry: for every table the turn read that the enrichment
    did not show, and that the GRAPH holds (``table_entity_names`` — the graph's table-level
    entity names, any layer), bump `missed` on that entity so the prior pulls it forward next
    time. A table the graph does not hold is not a retrieval miss. Never raises."""
    try:
        missed = unshown_tables(entity_names, tool_calls, deployed)
    except Exception:  # noqa: BLE001
        return
    if not kg_id or not missed:
        return
    leaves = {m.rpartition(".")[2].lower() for m in missed}
    # a graph entity matches a missed table by its own leaf OR by the leaf of the table it is
    # DEPLOYED as (``entity_deployed``: name → deployed identifier) — `v_Agency` is Agency's
    # table, and matching on the entity's leaf alone never recorded that miss at all
    dep = {str(k): str(v) for k, v in (entity_deployed or {}).items() if k and v}
    names = [str(n) for n in (table_entity_names or []) if n and (
        str(n).rpartition(".")[2].lower() in leaves
        or dep.get(str(n), "").rpartition(".")[2].lower() in leaves)]
    if not names:
        return
    import threading

    def _run():
        try:
            record(kg_id, names, "missed")
        except Exception:  # noqa: BLE001
            pass
    try:
        threading.Thread(target=_run, name="kg-usage-missed", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def delete_for_entities(kg_id: str, entity_names) -> int:
    """Drop the telemetry rows of the named nodes on one graph (a withdrawn extension's
    nodes no longer exist to be retrieved). Returns the rows removed."""
    names = [str(n) for n in (entity_names or []) if n]
    if not names:
        return 0
    try:
        con = _connect()
    except Exception:  # noqa: BLE001
        return 0
    try:
        q = "DELETE FROM kg_entity_usage WHERE kg_id = ? AND entity_name IN (" + ",".join("?" * len(names)) + ")"
        cur = con.execute(q, [kg_id, *names])
        con.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    except Exception:  # noqa: BLE001
        return 0
    finally:
        con.close()


def delete_for_kg(kg_id: str) -> int:
    """Remove every usage row for ``kg_id`` — called when the KG itself is deleted.

    Unlike the recorders above this one RAISES on failure: it is a purge primitive
    consumed by `core.kg_derived_state.purge_for_kg`, which owns the fail-soft
    policy and names the table that could not be purged. Returns rows removed.
    Deletion sites must not call this directly — the one purge is `purge_for_kg`,
    so a derived table added later is one registry row, not one edit per site.
    """
    if not kg_id:
        return 0
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM kg_entity_usage WHERE kg_id=?", (kg_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def priors(kg_id: str) -> Dict[str, float]:
    """{entity_name: prior in [0, 1]} — a normalised, saturating usage signal.

    Empty dict when there is no telemetry, so ranking is unchanged on an
    un-instrumented graph. The signal weights ``used`` most, then ``rendered``,
    then ``seeded``, and saturates via log so a single hot entity cannot dominate.
    """
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT entity_name, seeded_count, rendered_count, used_count, missed_count "
                "FROM kg_entity_usage WHERE kg_id=?",
                (kg_id,),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"kg usage priors failed (kg={kg_id}): {exc}")
        return {}
    if not rows:
        return {}
    raw: Dict[str, float] = {}
    for r in rows:
        name, seeded, rendered, used = r[0], r[1] or 0, r[2] or 0, r[3] or 0
        missed = (r[4] or 0) if len(r) > 4 else 0
        # missed ≫ used ≫ rendered ≫ seeded — a table the agent had to find on its own
        # is the one the graph most needs to offer next time; log-saturated so hot
        # entities can't dominate
        raw[name] = math.log1p(4.0 * missed + 3.0 * used + 1.5 * rendered + 0.5 * seeded)
    hi = max(raw.values()) or 1.0
    return {n: v / hi for n, v in raw.items()}


def stats(kg_id: str, entity_names: Optional[List[str]] = None) -> Dict[str, Dict[str, int]]:
    """Raw counters for the inspector: {entity_name: {seeded, rendered, used, last_used_at}}."""
    try:
        conn = _connect()
        try:
            if entity_names:
                names = list({str(n) for n in entity_names if n})
                q = ("SELECT entity_name, seeded_count, rendered_count, used_count, last_used_at, missed_count "
                     f"FROM kg_entity_usage WHERE kg_id=? AND entity_name IN ({','.join('?' for _ in names)})")
                rows = conn.execute(q, (kg_id, *names)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT entity_name, seeded_count, rendered_count, used_count, last_used_at, missed_count "
                    "FROM kg_entity_usage WHERE kg_id=?",
                    (kg_id,),
                ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"kg usage stats failed (kg={kg_id}): {exc}")
        return {}
    return {
        r[0]: {"seeded": r[1] or 0, "rendered": r[2] or 0, "used": r[3] or 0, "last_used_at": r[4],
               "missed": (r[5] or 0) if len(r) > 5 else 0}
        for r in rows
    }
