"""Exact per-graph content revision for the knowledge-graph tables.

WHY: the ontology ingredient's content hash (``composition.kg_graph_hash``) was cached
behind a fingerprint of the graph's RAW ROWS — exact, but every lookup still read the whole
graph to compute that fingerprint. An agent badge request hashes the same few industry
graphs dozens of times (74 full-graph reads for one /v1/evidence call), so the "cache hit"
path was itself the slowest part of every trust surface.

HOW: SQLite triggers on ``kg_entities`` and ``kg_relationships`` bump ``kg_content_rev.rev``
for the graph a row belongs to on EVERY insert, update and delete — whoever writes it (the
KG store, a type migration, a raw maintenance script, another process), because the
trigger lives in the database file, not in this code. A cached hash is keyed on
``(epoch, rev)``; validating it is one indexed single-row read.

The epoch closes the only gap: if the triggers were ever missing (a migration that
rebuilt a table drops its triggers with it), writes in that window were not counted. They
are re-created on the next read and the epoch moves, which invalidates every revision
recorded before — so a stale hash can never be served, only recomputed. Trigger presence
is re-verified whenever the schema cookie changes, which any DROP/CREATE of a table or
trigger does.

Everything here degrades to "no revision" (``None``) — a read-only or foreign database
falls back to the fingerprint path, which was always correct, merely slow.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional, Tuple

logger = logging.getLogger("quart.app")

_BUMP = ("INSERT INTO kg_content_rev(kg_id, rev) SELECT {ref}.kg_id, 1 "
         "WHERE {ref}.kg_id IS NOT NULL "
         "ON CONFLICT(kg_id) DO UPDATE SET rev = rev + 1;")

TRIGGERS = {
    f"trg_kgrev_{short}_{op[:3].lower()}":
        f"CREATE TRIGGER IF NOT EXISTS trg_kgrev_{short}_{op[:3].lower()} AFTER {op} ON {table} "
        f"BEGIN {' '.join(_BUMP.format(ref=ref) for ref in refs)} END"
    for table, short in (("kg_entities", "ent"), ("kg_relationships", "rel"))
    for op, refs in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",)))
}

#: ``kg_content_rev`` is never purged (see ``kg_derived_state``): a revision only ever
#: grows, so a graph deleted and re-created under the same kg_id cannot climb back to a
#: revision a hash was cached at. ``kg_content_hash_cache`` is derived state — purged with
#: its graph — and a stale row could never match anyway (its revision is gone for good).
_DDL = (
    "CREATE TABLE IF NOT EXISTS kg_content_rev ("
    " kg_id TEXT PRIMARY KEY, rev INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS kg_content_rev_epoch ("
    " id INTEGER PRIMARY KEY CHECK (id = 1), epoch INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS kg_content_hash_cache ("
    " kg_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, rev INTEGER NOT NULL,"
    " code_ver TEXT NOT NULL, hash TEXT NOT NULL, n INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS kg_content_memo ("
    " kg_id TEXT NOT NULL, name TEXT NOT NULL, epoch INTEGER NOT NULL, rev INTEGER NOT NULL,"
    " code_ver TEXT NOT NULL, value_json TEXT NOT NULL, PRIMARY KEY (kg_id, name))",
)
_TABLES = ("kg_content_rev", "kg_content_rev_epoch", "kg_content_hash_cache", "kg_content_memo")

#: file identity -> (schema_version at which the triggers were verified, epoch)
_VERIFIED: dict = {}
_LOCK = threading.Lock()


def _ident(path: str):
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def _ensure(conn, path: str) -> Optional[int]:
    """The current epoch, with the triggers guaranteed present — or None (unsupported)."""
    ident = _ident(path)
    if ident is None:
        return None
    sv = conn.execute("PRAGMA schema_version").fetchone()[0]
    hit = _VERIFIED.get(ident)
    if hit and hit[0] == sv:
        return hit[1]
    with _LOCK:
        names = (*TRIGGERS, *_TABLES, "kg_entities", "kg_relationships")
        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('trigger','table') AND name IN "
            f"({','.join('?' * len(names))})", names).fetchall()}
        if not {"kg_entities", "kg_relationships"} <= have:
            return None   # no KG tables in this database
        complete = set(TRIGGERS) | set(_TABLES) <= have
        if not complete:
            # Anything missing ⇒ writes may have gone uncounted: (re)create and move the epoch.
            for ddl in _DDL:
                conn.execute(ddl)
            for ddl in TRIGGERS.values():
                conn.execute(ddl)
            conn.execute("INSERT INTO kg_content_rev_epoch(id, epoch) VALUES (1, 1) "
                         "ON CONFLICT(id) DO UPDATE SET epoch = epoch + 1")
            conn.commit()
            logger.info(f"kg_revision: content-revision triggers installed on {path}")
        row = conn.execute("SELECT epoch FROM kg_content_rev_epoch WHERE id=1").fetchone()
        epoch = int(row[0]) if row else 0
        sv = conn.execute("PRAGMA schema_version").fetchone()[0]
        _VERIFIED[ident] = (sv, epoch)
        return epoch


def revision(conn, path: str, kg_id: str) -> Optional[Tuple[int, int]]:
    """``(epoch, rev)`` for ``kg_id`` — changes whenever any of its rows changes — or None
    when the database cannot carry revisions (the caller then reads the rows)."""
    try:
        epoch = _ensure(conn, path)
        if epoch is None:
            return None
        row = conn.execute("SELECT rev FROM kg_content_rev WHERE kg_id=?", (kg_id,)).fetchone()
        return (epoch, int(row[0]) if row else 0)
    except Exception as e:  # noqa: BLE001 — a revision is an optimisation, never a gate
        logger.debug(f"kg_revision: unavailable for {kg_id}: {e}")
        return None


def purge_memo(kg_id: str, db_path: Optional[str] = None) -> int:
    """Drop the persisted graph facts of a deleted graph (``kg_derived_state`` purger)."""
    from trusted_data_agent.core import sqlite_util
    from . import store as _store
    conn = sqlite_util.connect(_store._db_path(db_path))
    try:
        try:
            n = conn.execute("DELETE FROM kg_content_memo WHERE kg_id=?", (kg_id,)).rowcount
        except Exception as e:  # noqa: BLE001
            if "no such table" in str(e):
                return 0
            raise
        conn.commit()
        return int(n or 0)
    finally:
        conn.close()


def purge_hash_cache(kg_id: str, db_path: Optional[str] = None) -> int:
    """Drop the persisted ontology hash of a deleted graph (``kg_derived_state`` purger)."""
    from trusted_data_agent.core import sqlite_util
    from . import store as _store
    conn = sqlite_util.connect(_store._db_path(db_path))
    try:
        try:
            n = conn.execute("DELETE FROM kg_content_hash_cache WHERE kg_id=?", (kg_id,)).rowcount
        except Exception as e:  # noqa: BLE001 — no table yet ⇒ nothing cached
            if "no such table" in str(e):
                return 0
            raise
        conn.commit()
        return int(n or 0)
    finally:
        conn.close()


# ── an exact memo of anything derived from a graph's rows ─────────────────────
# Some trust readings need a FACT of a graph, not the graph — which extensions it carries
# and each one's content hash. Computing it meant loading and decoding every row (an
# industry model: ~245K entities, ~0.2 s locally, ~1 s on production) once per agent per
# badge request. The fact only changes when the graph's rows do, which is exactly what
# the revision counts. Same exactness rules as the ontology hash cache: kept only for a
# self-contained graph (every edge endpoint is its own — a foreign endpoint's rename bumps
# only the OTHER graph's revision), only when the revision did not move while computing,
# and never while a content capture is open (the capture must see the bytes hashed).
_MEMO: dict = {}
_MEMO_MAX = 512


def _self_contained(conn, kg_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM kg_relationships r WHERE r.kg_id=? AND ("
        " NOT EXISTS (SELECT 1 FROM kg_entities e WHERE e.id=r.source_entity_id AND e.kg_id=r.kg_id)"
        " OR NOT EXISTS (SELECT 1 FROM kg_entities e WHERE e.id=r.target_entity_id AND e.kg_id=r.kg_id))"
        " LIMIT 1", (kg_id,)).fetchone()
    return row is None


def code_version(*module_files: str) -> str:
    """A digest of the source files whose code decides a persisted value — any edit to
    them invalidates every row persisted under the old digest (recomputed, never served)."""
    import hashlib
    h = hashlib.sha256(b"kgmemo1\n")
    for f in module_files:
        try:
            with open(f, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"missing:" + f.encode())
    return h.hexdigest()[:32]


def memo_at_revision(kg_id: str, db_path: Optional[str], name: str, compute,
                     code_ver: Optional[str] = None):
    """``compute()`` — a pure function of ``kg_id``'s rows — cached at its revision.
    The value is shared between callers: treat it as read-only. With ``code_ver`` (see
    ``code_version``) the value — JSON-serialisable — is also persisted in
    ``kg_content_memo`` so a restart does not recompute it on the first request."""
    from trusted_data_agent.core import sqlite_util
    from .attestation import _CAPTURE
    from . import store as _store
    if _CAPTURE.get() is not None:
        return compute()
    path = _store._db_path(db_path)
    conn = sqlite_util.connect(path)
    try:
        rv = revision(conn, path, kg_id)
        if rv is None:
            return compute()
        hit = _MEMO.get((path, kg_id, name))
        if hit is not None and hit[0] == rv:
            return hit[1]
        if code_ver:
            row = conn.execute(
                "SELECT value_json FROM kg_content_memo WHERE kg_id=? AND name=? AND epoch=? "
                "AND rev=? AND code_ver=?", (kg_id, name, rv[0], rv[1], code_ver)).fetchone()
            if row is not None:
                import json
                value = json.loads(row[0])
                with _LOCK:
                    _MEMO[(path, kg_id, name)] = (rv, value)
                return value
        cacheable = _self_contained(conn, kg_id)
    finally:
        conn.close()
    value = compute()
    if cacheable:
        conn = sqlite_util.connect(path)
        try:
            if revision(conn, path, kg_id) == rv:
                with _LOCK:
                    if len(_MEMO) >= _MEMO_MAX:
                        _MEMO.clear()
                    _MEMO[(path, kg_id, name)] = (rv, value)
                if code_ver:
                    try:
                        import json
                        conn.execute(
                            "INSERT INTO kg_content_memo(kg_id, name, epoch, rev, code_ver, value_json) "
                            "VALUES (?,?,?,?,?,?) ON CONFLICT(kg_id, name) DO UPDATE SET "
                            "epoch=excluded.epoch, rev=excluded.rev, code_ver=excluded.code_ver, "
                            "value_json=excluded.value_json",
                            (kg_id, name, rv[0], rv[1], code_ver, json.dumps(value)))
                        conn.commit()
                    except Exception as e:  # noqa: BLE001 — a read-only db still serves from memory
                        logger.debug(f"kg_revision: memo {name} not persisted for {kg_id}: {e}")
        finally:
            conn.close()
    return value
