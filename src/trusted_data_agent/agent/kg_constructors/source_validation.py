"""Source validation for knowledge-graph constructors — pure core, injected runner.

What the ontology declares and what the data holds drift: a column declared
MM/DD/YYYY holds `31-OCT-2023`; a declared key (`INCNUM`) is not unique across
agencies; a coded column is blank in a third of its rows; a reconciliation figure is a
literal. Every one of those cost a turn (or a three-hour join) before an agent found
out at runtime. This module reads the answers from the SOURCE at construction time and
stamps them on the LOGICAL nodes, so the enrichment can state them and the preflight
guard can act on them without a probe.

One pure core for every constructor (charter: one implementation, many seams):
  * `validation_targets(entities, relationships)` derives, from the graph the walker
    produced, what to check — the deployed identifier of each entity and attribute
    (from ``deployed_table`` / ``deployed_column`` when the walker stamped them, else
    through the physical layer's ``implements`` edge), the declared key columns, the
    text-stored date columns (the SAME predicate the enrichment and the guard use —
    ``components/dateforms.is_text_date_attr``) and the character-deployed coded columns.
  * the probe builders turn those into bounded SQL — a `TOP n` sample per text-date
    column (cheap), ONE exact count per table, blank counts AND date ranges over ONE
    bounded `TOP`-sampled read per table chunk (an estimate is all a ratio needs; a range
    from 200,000 hash-distributed rows says which years the data covers), and one exact
    GROUP BY scan per declared key. Measured on the 58-million-row NIBRS product: a
    MIN/MAX(CAST) per date column over full tables took a quarter of an hour — which is
    why the ranges are sampled, and why every round reports its seconds.
  * `apply_*` stamp what a probe answered onto the node it targeted — including
    ``date_unparseable_ratio``, the sampled share of a text date's non-blank values that
    are NOT dates in the sampled shape (TRYCAST nulls them; a plain CAST would fail).
  * `validate_with_source(entities, relationships, run_sql, ...)` orchestrates the
    rounds; every probe fails open (counted, logged, never raising into a build).

The runner is the constructor's: a DB-API cursor (ANDP), the deploy backend
(industry model, after its load), an MCP `read_query` tool (database discovery). It is
``run_sql(sql) -> list[dict]`` and may be sync or async.

Stamps (all on LOGICAL nodes, never on the physical layer — ONTOLOGY §2.4c's
documented exception, the same reasoning as ``deployed_type``):
  attribute: ``sampled_shape`` (a Teradata FORMAT), ``sample_value`` (omitted on a
             PII column), ``date_range`` [lo, hi] ISO (+ ``date_range_sampled``),
             ``date_unparseable_ratio`` (0..1, sampled), ``blank_ratio`` (0..1, sampled)
  entity:    ``row_count``, ``key_unique`` (bool), ``key_duplicate_groups``
No timestamp is stamped: a build that changes nothing must hash identically.
"""
from __future__ import annotations

import inspect
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, Union

from trusted_data_agent.components import dateforms as _df

logger = logging.getLogger("quart.app")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
_KEY_SPLIT = re.compile(r"[,\s]+")

RunSql = Callable[[str], Union[List[Dict[str, Any]], Awaitable[List[Dict[str, Any]]]]]


def _ident_ok(*parts: str) -> bool:
    return all(bool(p) and _IDENT.match(p) is not None for p in parts)


def _leaf_parts(qualified: str) -> List[str]:
    return [p.strip().strip('"') for p in str(qualified or "").split(".") if p.strip()]


def key_columns(props: Dict[str, Any]) -> List[str]:
    """The entity's declared key as a column list — `business_key` / `primary_key` /
    `natural_key_column`, comma- or space-separated, order kept, duplicates dropped.

    Deliberately a WIDER net than the runtime guards' `kgstore.keys.KEY_PROPERTIES`:
    this runs at construction time to decide what to probe, where reading one property
    too many costs a probe, while the guards read only what the join check has always
    read — widening theirs would change what they judge."""
    raw = ""
    for k in ("business_key", "primary_key", "natural_key_column", "key_columns"):
        v = (props or {}).get(k)
        if isinstance(v, (list, tuple)):
            raw = ",".join(str(x) for x in v)
        elif isinstance(v, str):
            raw = v
        if raw.strip():
            break
    out: List[str] = []
    for c in _KEY_SPLIT.split(raw.strip()):
        if c and c not in out:
            out.append(c)
    return out


# ── targets ────────────────────────────────────────────────────────────────────
def validation_targets(entities: Sequence[Dict[str, Any]],
                       relationships: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure: {"tables": [{entity, table(db.tbl), keys, node}], "text_dates": [{attribute,
    entity, table, column, is_pii, node}], "coded": [{attribute, table, column, node}]}.
    Nothing without a resolvable, identifier-safe deployed name is a target."""
    by_key = {(e.get("name"), e.get("entity_type")): e for e in entities if isinstance(e, dict)}
    # physical → logical through `implements` (column → attribute, table → entity)
    implements: Dict[Tuple[str, str], str] = {}
    contains_attr_to_entity: Dict[str, str] = {}
    phys_cols_of_entity: Dict[str, List[str]] = {}
    for r in relationships or []:
        if not isinstance(r, dict):
            continue
        rt = r.get("relationship_type")
        if rt == "implements":
            implements[(str(r.get("target_name")), str(r.get("target_type")))] = str(r.get("source_name"))
        elif rt == "contains" and r.get("source_type") == "entity" and r.get("target_type") == "attribute":
            contains_attr_to_entity[str(r.get("target_name"))] = str(r.get("source_name"))

    tables: List[Dict[str, Any]] = []
    table_of_entity: Dict[str, str] = {}
    for (name, etype), e in by_key.items():
        if etype != "entity":
            continue
        props = e.get("properties") or {}
        deployed = str(props.get("deployed_table") or implements.get((name, "entity")) or "")
        parts = _leaf_parts(deployed)
        if len(parts) < 2 or not _ident_ok(*parts):
            continue
        tbl = ".".join(parts[-2:])
        table_of_entity[name] = tbl
        keys = [k for k in key_columns(props) if _ident_ok(k)]
        tables.append({"entity": name, "table": tbl, "keys": keys, "node": e})

    text_dates: List[Dict[str, Any]] = []
    coded: List[Dict[str, Any]] = []
    for (name, etype), e in by_key.items():
        if etype != "attribute":
            continue
        props = e.get("properties") or {}
        ent = contains_attr_to_entity.get(name) or name.rpartition(".")[0]
        tbl = table_of_entity.get(ent)
        deployed_col = str(props.get("deployed_column") or implements.get((name, "attribute")) or "")
        parts = _leaf_parts(deployed_col)
        col = parts[-1] if parts else str(props.get("label") or name.rpartition(".")[2])
        if not tbl and len(parts) >= 3 and _ident_ok(*parts[-3:]):
            tbl = ".".join(parts[-3:-1])
        if not tbl or not _ident_ok(col):
            continue
        dep_type = props.get("deployed_type")
        if dep_type is None:
            phys = by_key.get((implements.get((name, "attribute")) or "", "column"))
            dep_type = ((phys or {}).get("properties") or {}).get("datatype")
        p2 = dict(props)
        p2.setdefault("deployed_type", dep_type)
        entry = {"attribute": name, "entity": ent, "table": tbl, "column": col,
                 "is_pii": bool(props.get("is_pii")), "node": e}
        if _df.is_text_date_attr(p2):
            text_dates.append(entry)
        elif props.get("allowed_values") and _df.is_char_type(dep_type):
            coded.append(entry)
    # entities with a table whose key columns are attributes we can name
    for t in tables:
        t["keys"] = [k for k in t["keys"]]
    return {"tables": tables, "text_dates": text_dates, "coded": coded}


# ── probes ─────────────────────────────────────────────────────────────────────
def _limit_clause(dialect: str, n: int) -> Tuple[str, str]:
    return (f"TOP {n} ", "") if dialect == "teradata" else ("", f" LIMIT {n}")


def shape_probe(t: Dict[str, Any], dialect: str = "teradata", n: int = 5) -> str:
    """Per-column fallback: a few non-blank values. On a mostly-blank column this is a full
    scan (measured: 525 s for 42 columns), which is why `shapes_probe` reads a whole table's
    date columns from ONE bounded sample first."""
    top, lim = _limit_clause(dialect, n)
    c, tbl = t["column"], t["table"]
    return f"SELECT {top}{c} AS v FROM {tbl} WHERE {c} IS NOT NULL AND TRIM({c}) <> ''{lim}"


def shapes_probe(table: str, dates: Sequence[Dict[str, Any]], dialect: str = "teradata",
                 sample: int = 200000) -> str:
    """ONE bounded sample read per table yielding, for every text-date column, its lexically
    smallest and largest non-blank value — two witnesses of the shape, from one cheap read."""
    parts = []
    for i, t in enumerate(dates):
        c = t["column"]
        nb = f"CASE WHEN {c} IS NOT NULL AND TRIM({c}) <> '' THEN {c} END"
        parts.append(f"MIN({nb}) AS s_{i}_a")
        parts.append(f"MAX({nb}) AS s_{i}_b")
    cols = ", ".join(dict.fromkeys(t["column"] for t in dates))
    top, lim = _limit_clause(dialect, sample)
    return f"SELECT {', '.join(parts)} FROM (SELECT {top}{cols} FROM {table}{lim}) x"


def _cast(t: Dict[str, Any], fmt: str) -> str:
    # A guarded CAST: a value outside the shape's pattern reads as NULL instead of failing
    # the whole read with Error 2666 — the failure the first live run met on six of
    # NIBRS's date columns. (TRYCAST does not accept a FORMAT phrase — Error 3706, measured.)
    return _df.guarded_cast(t["column"], fmt)


def table_probe(table: str) -> str:
    """ONE exact scan per table: the row count. Nothing per row is evaluated here — a
    MIN/MAX(CAST(…)) per date column on a 12-million-row table was the quarter-hour."""
    return f"SELECT COUNT(*) AS n FROM {table}"


def sample_probe(table: str, blank_columns: Sequence[str],
                 dated: Sequence[Tuple[Dict[str, Any], str]] = (),
                 dialect: str = "teradata", sample: int = 200000) -> str:
    """Blank counts AND date ranges over ONE bounded sample of rows (`TOP n`, cheap):
    an estimate is all a blank ratio needs, and a range read from 200,000 hash-distributed
    rows says which years the data covers — the question an agent actually asks.

    WHERE THE TIME GOES, measured on the deployed NIBRS ``Incident`` (12.35 M rows, 391
    columns, 361 coded, 10 text dates) on 2026-09-09 — recorded because two plausible
    optimisations were tried here and BOTH were refuted:

      * 120 blank counts, no dates ......................  6.6 s
      * + 10 date ranges (the guarded cast) ............. 33.8 s   <- the round's cost
      * the 10 ranges alone, TOP 200,000 ................ 21.5 s
      * the 10 ranges alone, TOP  25,000 ................  3.1 s

    (1) Splitting the unparseable-share term (`bad_i`) out into its own smaller-sample
        probe saves NOTHING: 33.9 s against 33.8 s. Its `REGEXP_SIMILAR` is the same
        pattern the guarded cast already evaluates on that column, so the optimizer was
        never paying for it twice. The cost is the GUARDED CAST, not the share.
    (2) A plain `CAST` without the guard is ~10x cheaper and simply FAILS here (the data
        carries values that are not dates) — which is the whole reason the guard exists.
    (3) Shrinking the sample to 25,000 is 7x faster and CHANGES THE ANSWER: only 3 of 10
        ranges matched, the rest understating the span by up to six weeks (V1014 read
        2024-03-20 against a true 2024-03-31). A narrower span is not a rounder number,
        it is a false statement about coverage, and an agent told the data ends earlier
        than it does will exclude rows it should have read.

    So the 200,000-row guarded read stays. The honest summary is that this round costs
    what it costs: about 20 s per table carrying text dates, and the price of the one
    fact — the real span — that a consumer cannot cheaply re-derive.
    """
    parts = ["COUNT(*) AS n"] + [f"SUM(CASE WHEN {c} IS NULL OR TRIM({c}) = '' THEN 1 ELSE 0 END) AS b_{i}"
                                 for i, c in enumerate(blank_columns)]
    for i, (t, fmt) in enumerate(dated):
        c = _cast(t, fmt)
        parts.append(f"MIN({c}) AS lo_{i}")
        parts.append(f"MAX({c}) AS hi_{i}")
        parts.append(f"SUM(CASE WHEN {t['column']} IS NOT NULL AND TRIM({t['column']}) <> '' AND "
                     f"REGEXP_SIMILAR(TRIM({t['column']}), '{_df.format_regex(fmt)}', 'i') = 0 THEN 1 ELSE 0 END) AS bad_{i}")
    cols = ", ".join(dict.fromkeys(list(blank_columns) + [t["column"] for t, _ in dated]))
    top, lim = _limit_clause(dialect, sample)
    return f"SELECT {', '.join(parts)} FROM (SELECT {top}{cols} FROM {table}{lim}) x"


def key_probe(table: str, keys: Sequence[str]) -> str:  # noqa: E302
    cols = ", ".join(keys)
    return f"SELECT COUNT(*) AS groups_n FROM (SELECT {cols} FROM {table} GROUP BY {cols}) x"


# the normalisation advisor's FD probe is the key probe one column over — ONE definition
from .normalisation import fd_probe  # noqa: E402,F401


# ── stamping ───────────────────────────────────────────────────────────────────
def _props(node: Dict[str, Any]) -> Dict[str, Any]:
    return node.setdefault("properties", {})


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _row_value(rows: List[Dict[str, Any]], key: str) -> Any:
    if not rows or not isinstance(rows[0], dict):
        return None
    for k, v in rows[0].items():
        if str(k).strip().lower() == key:
            return v
    return None


def apply_shape(t: Dict[str, Any], rows: List[Dict[str, Any]], values: Optional[List[Any]] = None) -> Optional[str]:
    vals = values if values is not None else [_row_value([r], "v") for r in rows if isinstance(r, dict)]
    fmt = _df.column_shape([v for v in vals if v is not None])
    if not fmt:
        return None
    p = _props(t["node"])
    p["sampled_shape"] = fmt
    if not t.get("is_pii"):
        sample = next((" ".join(str(v).split()) for v in vals if v is not None and str(v).strip()), "")
        if sample:
            p["sample_value"] = sample
    return fmt


def apply_range(t: Dict[str, Any], rows: List[Dict[str, Any]], index: int = 0) -> bool:
    lo, hi = _row_value(rows, f"lo_{index}"), _row_value(rows, f"hi_{index}")
    if lo is None or hi is None:
        return False
    lo_s, hi_s = str(lo)[:10], str(hi)[:10]
    if not (_df.date_key(lo_s) and _df.date_key(hi_s)):
        return False
    _props(t["node"])["date_range"] = [lo_s, hi_s]
    _props(t["node"])["date_range_sampled"] = True
    n, bad = _num(_row_value(rows, "n")), _num(_row_value(rows, f"bad_{index}"))
    if n and bad is not None:
        # the share of non-blank values that are NOT dates in this shape (sampled) — the
        # fact that decides whether a plain CAST is safe on this column at all
        _props(t["node"])["date_unparseable_ratio"] = round(bad / n, 4)
        # a SAMPLED zero is not a proof of cleanliness: six NIBRS columns read 0 here and
        # failed a full-table CAST — consumers keep the guarded form unless the share was exact
        _props(t["node"])["date_unparseable_sampled"] = True
    return True


def apply_row_count(table: Dict[str, Any], rows: List[Dict[str, Any]]) -> Optional[int]:
    n = _num(_row_value(rows, "n"))
    if n is None:
        return None
    _props(table["node"])["row_count"] = int(n)
    return int(n)


def apply_blanks(blank_targets: Sequence[Dict[str, Any]], rows: List[Dict[str, Any]]) -> int:
    """Blank ratios from a (sampled) aggregate: b_i / n. Returns how many were stamped."""
    n = _num(_row_value(rows, "n"))
    if not n:
        return 0
    done = 0
    for i, t in enumerate(blank_targets):
        b = _num(_row_value(rows, f"b_{i}"))
        if b is not None:
            _props(t["node"])["blank_ratio"] = round(b / n, 4)
            done += 1
    return done


def apply_key(table: Dict[str, Any], rows: List[Dict[str, Any]], row_count: Optional[int]) -> Optional[bool]:
    g = _num(_row_value(rows, "groups_n"))
    if g is None or row_count is None:
        return None
    unique = int(g) == int(row_count)
    p = _props(table["node"])
    p["key_unique"] = unique
    if not unique:
        p["key_duplicate_groups"] = int(row_count) - int(g)
    else:
        p.pop("key_duplicate_groups", None)
    return unique


# ── orchestration ──────────────────────────────────────────────────────────────
async def validate_with_source(entities: Sequence[Dict[str, Any]],
                               relationships: Sequence[Dict[str, Any]],
                               run_sql: RunSql, *,
                               dialect: str = "teradata",
                               max_tables: int = 300,
                               max_columns: int = 2500,
                               blank_chunk: int = 120,
                               date_ranges: bool = True) -> Dict[str, Any]:
    """Run the probes and stamp the results in place. Returns counts; never raises."""
    stats = {"tables_counted": 0, "keys_checked": 0, "keys_not_unique": 0, "shapes_sampled": 0,
             "shapes_unsettled": 0, "date_ranges": 0, "blank_ratios": 0, "probes": 0, "probe_errors": 0,
             "seconds": {}}
    try:
        targets = validation_targets(entities, relationships)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("source validation: could not derive targets (%s)", exc)
        return stats

    async def run(sql: str) -> Optional[List[Dict[str, Any]]]:
        stats["probes"] += 1
        try:
            res = run_sql(sql)
            if inspect.isawaitable(res):
                res = await res
            return res if isinstance(res, list) else []
        except Exception as exc:
            stats["probe_errors"] += 1
            logger.info("source validation probe failed (%s): %s", str(exc)[:160], sql[:160])
            return None

    tables = targets["tables"][:max_tables]
    text_dates = targets["text_dates"][:max_columns]
    coded = targets["coded"][:max_columns]
    coded_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for t in coded:
        coded_by_table.setdefault(t["table"], []).append(t)

    # round 1 — text-date shapes: ONE bounded sample read per table (two witnesses per
    # column); a column the sample left blank falls back to its own TOP-n read.
    dated_by_table: Dict[str, List[Tuple[Dict[str, Any], str]]] = {}
    t_round = time.monotonic()
    dates_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for t in text_dates:
        dates_by_table.setdefault(t["table"], []).append(t)
    for table, dates in dates_by_table.items():
        rows = await run(shapes_probe(table, dates, dialect))
        for i, t in enumerate(dates):
            vals = [_row_value(rows, f"s_{i}_a"), _row_value(rows, f"s_{i}_b")] if rows else []
            vals = [v for v in vals if v is not None and str(v).strip()]
            fmt = apply_shape(t, [], vals) if vals else None
            if not fmt:
                rows2 = await run(shape_probe(t, dialect))
                fmt = apply_shape(t, rows2) if rows2 is not None else None
            if not fmt:
                stats["shapes_unsettled"] += 1
                continue
            stats["shapes_sampled"] += 1
            if date_ranges and dialect == "teradata":
                dated_by_table.setdefault(t["table"], []).append((t, fmt))
    stats["seconds"]["shapes"] = round(time.monotonic() - t_round, 1)

    # round 2 — ONE exact scan per table: the row count
    row_counts: Dict[str, int] = {}
    t_round = time.monotonic()
    for tb in tables:
        rows = await run(table_probe(tb["table"]))
        if rows is None:
            continue
        n = apply_row_count(tb, rows)
        if n is None:
            continue
        row_counts[tb["table"]] = n
        stats["tables_counted"] += 1
    stats["seconds"]["counts"] = round(time.monotonic() - t_round, 1)

    # round 3 — ONE bounded sample read per table chunk: blank ratios + date ranges.
    # A dirty date row makes the CAST fail for the chunk → retry it without the dates.
    t_round = time.monotonic()
    for tb in tables:
        blanks = coded_by_table.get(tb["table"], [])
        dated = dated_by_table.get(tb["table"], [])
        chunks = [blanks[i:i + blank_chunk] for i in range(0, len(blanks), blank_chunk)] or [[]]
        for ci, chunk in enumerate(chunks):
            d = dated if ci == 0 else []
            if not chunk and not d:
                continue                       # nothing to sample on this table
            rows = await run(sample_probe(tb["table"], [c["column"] for c in chunk], d, dialect))
            if rows is None and d:
                rows = await run(sample_probe(tb["table"], [c["column"] for c in chunk], [], dialect))
                d = []
            if rows is None:
                continue
            stats["blank_ratios"] += apply_blanks(chunk, rows)
            for i, (t, _fmt) in enumerate(d):
                if apply_range(t, rows, i):
                    stats["date_ranges"] += 1
    stats["seconds"]["samples"] = round(time.monotonic() - t_round, 1)

    # round 4 — declared keys (one exact GROUP BY scan per keyed table)
    t_round = time.monotonic()
    for tb in tables:
        if not tb["keys"] or tb["table"] not in row_counts:
            continue
        rows = await run(key_probe(tb["table"], tb["keys"]))
        if rows is None:
            continue
        u = apply_key(tb, rows, row_counts[tb["table"]])
        if u is not None:
            stats["keys_checked"] += 1
            if not u:
                stats["keys_not_unique"] += 1
    stats["seconds"]["keys"] = round(time.monotonic() - t_round, 1)
    return stats


# ── what a validated graph carries (for the graph's metadata + its card) ────────
_STAMP_KEYS = ("row_count", "key_unique", "sampled_shape", "blank_ratio", "date_range")


def has_source_validation(entities: Sequence[Dict[str, Any]]) -> bool:
    """Pure: does this graph carry ANY source-validation stamp? Derived from the graph's
    own content — never a flag that could drift from it."""
    for e in entities or []:
        props = (e or {}).get("properties") if isinstance(e, dict) else None
        if isinstance(props, dict) and any(k in props for k in _STAMP_KEYS):
            return True
    return False


def summarize(entities: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Pure: the counts a card states — tables counted, keys checked / not unique,
    text-date shapes sampled, date ranges, blank ratios. Zero everywhere = not validated."""
    out = {"tables": 0, "keys_checked": 0, "keys_not_unique": 0, "shapes": 0, "date_ranges": 0, "blank_ratios": 0}
    for e in entities or []:
        props = (e or {}).get("properties") if isinstance(e, dict) else None
        if not isinstance(props, dict):
            continue
        if isinstance(props.get("row_count"), (int, float)):
            out["tables"] += 1
        if props.get("key_unique") is not None:
            out["keys_checked"] += 1
            if props.get("key_unique") is False:
                out["keys_not_unique"] += 1
        if props.get("sampled_shape"):
            out["shapes"] += 1
        if props.get("date_range"):
            out["date_ranges"] += 1
        if isinstance(props.get("blank_ratio"), (int, float)):
            out["blank_ratios"] += 1
    return out
