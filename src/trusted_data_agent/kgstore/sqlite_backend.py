"""
SQLite + NetworkX KG Store backend.

Extracted from GraphStore (graph_store.py) and wrapped in the KGStoreBackend
ABC.  Logic is identical to the original — only the interface changes:
  - Methods take an explicit `kg_id` parameter (instead of being methods of
    a kg_id-scoped instance).
  - The NetworkX graph cache is keyed by kg_id (not per-instance as before).

All methods are synchronous — SQLite is inherently sync and NetworkX is
CPU-bound.  Quart route handlers can call these directly without blocking
event-loop issues in typical workloads.

Registered as 'sqlite' on module import.
"""

import json
import re
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
from collections import deque, OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

from .base import KGStoreBackend
from .capabilities import KGStoreCapability
from .factory import register_backend

logger = logging.getLogger("quart.app")

# ── Entity / relationship type constants (re-exported for callers) ─────────────

# Re-exported from the canonical vocabulary (kgstore/types.py) — never redefined
# here.  Three hand-maintained copies used to exist; they drift silently the first
# time a type is added.  See the module docstring in types.py.
from .types import (  # noqa: F401  (re-exported for callers)
    ENTITY_TYPES,
    RELATIONSHIP_TYPES,
    EXPANDABLE_STRUCTURAL_TYPES as _EXPANDABLE_STRUCTURAL_TYPES,
    SEMANTIC_TYPES as _SEMANTIC_TYPES,
)
_MAX_JOIN_DISCOVERY_ROUNDS = 3


_CACHE_MISS = object()

#: Max in-memory NetworkX graphs kept per backend (LRU).
_GRAPH_CACHE_MAX = 6


def _has_cycle(G) -> bool:
    try:
        if G.number_of_nodes() == 0:
            return False
        nx.find_cycle(G)
        return True
    except Exception:  # NetworkXNoCycle (or NetworkX absent) → no cycle
        return False

from .types import ENTITY_TYPES as _KG_ENTITY_TYPES, RELATIONSHIP_TYPES as _KG_RELATIONSHIP_TYPES, PHYSICAL_TYPES as _KG_PHYSICAL_TYPES  # noqa: E402

#: Key-ish column markers for relevance-scoped expansion.
_KEY_LEAF_RE = re.compile(r"(?:^|_)(?:id|key|code|no|num)$", re.IGNORECASE)
#: IDENTITY columns — what a human calls the row by (`member_first_name`,
#: `customer_name`, `product_name`, `title`). A "who / which" question needs them
#: whatever words it used; on the prod turn that motivated this (0aa3a650), a top-5
#: customers question rendered ffp_member's `address_line_1` and `postal_code` and
#: budgeted out `member_first_name`, so the model went to a finance table for a name.
#: PLACE columns are the same class — a column whose values are the proper nouns a
#: question names ("in Chicago", "in Illinois", "the EMEA region"). On the policing core
#: model (Sep 2026) `v_Agency.city` was cut by the cap while `core_city_code` (a Yes/No
#: flag) was shown, and "how many robberies in Chicago?" answered every core city; the
#: pack's primer said "a city is Agency.city" and the model ignored the prose for the
#: data it was shown. Codes stay out (`state_code` is a key-shaped leaf, not a name).
_IDENTITY_LEAF_RE = re.compile(
    r"(?:^|_)(?:name|first_name|last_name|full_name|title|label"
    r"|city|town|state|state_abbr|province|country|region|county|district)$", re.IGNORECASE)


def _ident_tokens(identifier: str) -> Set[str]:
    out: Set[str] = set()
    for part in re.split(r"[\s_\-\.:]+", identifier or ""):
        out.update(m.lower() for m in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part))
    return out


def _column_tier(G, nid: int, query_id_set: Set[int], terms: List[str]) -> int:
    """0 = a seed (the question named it) · 1 = mentions a query term · 2 = an identity
    column (a name / title — what the row is called) · 3 = key / required column (join
    backbone) · 4 = everything else."""
    if nid in query_id_set:
        return 0
    d = G.nodes[nid]
    name = str(d.get("name") or "")
    leaf = name.rpartition(".")[2]
    props = d.get("properties") or {}
    if terms:
        hay_tokens = _ident_tokens(leaf) | _ident_tokens(str(props.get("label") or ""))
        desc = str(props.get("description") or "").lower()
        values = " ".join(str(v.get("label", "")) for v in (props.get("allowed_values") or []) if isinstance(v, dict)).lower()
        for t in terms:
            if t in hay_tokens or (len(t) >= 5 and t in leaf.lower()) or (len(t) >= 4 and (t in desc or t in values)):
                return 1
    if _IDENTITY_LEAF_RE.search(leaf) and not _KEY_LEAF_RE.search(leaf):
        return 2
    if (props.get("is_primary_key") or props.get("is_foreign_key") or props.get("is_required")
            or _KEY_LEAF_RE.search(leaf) or leaf.lower().endswith("id")):
        return 3
    return 4


#: Upper bound on the join-backbone columns kept per table over the cap — a hub
#: table referenced by dozens of discovered tables must not reopen the coverage
#: expansion the cap exists to close.
_BACKBONE_CAP = 12


def _join_backbone_columns(G, discovered_tables: Set[int]) -> Set[int]:
    """The columns that JOIN two tables of the subgraph — the endpoints of every
    ``foreign_key`` edge whose two owning tables are BOTH discovered — plus each
    discovered table's primary key. Pure; empty when the graph declares no keys.

    Why they are exempt from the relevance cap (2026-09-19, prod session 0aa3a650): on
    "top 5 customers based on miles flown" the cap was filled by the twelve columns that
    MENTION miles, `mileage_accrual.ffp_member_id` — the one declared key to `ffp_member`,
    a table sitting right beside it in the subgraph — was budgeted out, so the FK edge
    never entered the rendered subgraph, the DECLARED FOREIGN KEYS block was empty, the
    preflight guard had no key to enforce, and the model invented
    `booking_segment_id = ffp_member_id`. A join column between two tables in play is not
    one more fact competing on relevance: it is what makes the two facts combinable.
    """
    out: Set[int] = set()
    if not discovered_tables:
        return out

    def _owner(col_id: int) -> Optional[int]:
        for p in G.predecessors(col_id):
            if G.nodes[p].get("entity_type") in ("entity", "table"):
                return p
        return None

    per_table: Dict[int, int] = {}
    for tid in discovered_tables:
        for col in G.successors(tid):
            nd = G.nodes[col]
            if nd.get("entity_type") not in ("attribute", "column"):
                continue
            props = nd.get("properties") or {}
            keep = bool(props.get("is_primary_key"))
            if not keep:
                for nbr in set(G.successors(col)) | set(G.predecessors(col)):
                    if G.nodes[nbr].get("entity_type") not in ("attribute", "column"):
                        continue
                    edge = G.get_edge_data(col, nbr) or G.get_edge_data(nbr, col) or {}
                    if edge.get("relationship_type") != "foreign_key":
                        continue
                    other = _owner(nbr)
                    if other is not None and other != tid and other in discovered_tables:
                        keep = True
                        break
            if keep and per_table.get(tid, 0) < _BACKBONE_CAP:
                out.add(col)
                per_table[tid] = per_table.get(tid, 0) + 1
    return out


def _scope_columns(G, table_cols: List[int], query_id_set: Set[int], terms: List[str],
                   per_table_cap: Optional[int], column_budget: int,
                   backbone: Optional[Set[int]] = None) -> List[int]:
    """Relevance-scoped selection for ONE table. Seeds are never capped; neither is a
    join-backbone column (``backbone`` — see ``_join_backbone_columns``)."""
    keep_always = backbone or set()
    tiered = sorted(table_cols, key=lambda n: (_column_tier(G, n, query_id_set, terms), n))
    cap = per_table_cap if per_table_cap is not None else len(table_cols)
    chosen: List[int] = []
    for n in tiered:
        tier = _column_tier(G, n, query_id_set, terms)
        if tier == 0 or n in keep_always:
            chosen.append(n)
            continue
        if len(chosen) >= cap:
            continue
        if tier <= 3:
            chosen.append(n)
    # a small table with room left: show it whole (nothing to hide)
    if len(chosen) < cap:
        for n in tiered:
            if n not in chosen and len(chosen) < cap:
                chosen.append(n)
    return chosen[:max(0, column_budget)] if column_budget < len(chosen) else chosen


class SQLiteKGBackend(KGStoreBackend):
    """
    SQLite-backed knowledge graph storage with optional NetworkX graph algorithms.

    A single backend instance can serve many kg_ids.  The NetworkX DiGraph
    cache is a dict keyed by kg_id; any write operation to a kg_id invalidates
    that kg_id's cache entry.
    """

    # DB paths whose entity-type rename migration has already run this process.
    _MIGRATED_PATHS: Set[str] = set()

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._db_path: Optional[str] = self._config.get("db_path")
        # NetworkX cache: {kg_id: nx.DiGraph}
        self._graph_cache: "OrderedDict[str, Any]" = OrderedDict()
        self._name_index: Dict[str, Dict[tuple, int]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def initialize(self) -> None:
        if not self._db_path:
            try:
                from trusted_data_agent.core.config import APP_CONFIG
                self._db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
            except Exception:
                self._db_path = "tda_auth.db"
        # Apply the entity-type rename to this backend's DB (covers non-default
        # kg_store configs pointing at their own sqlite file). Idempotent + once
        # per path per process.
        if self._db_path not in SQLiteKGBackend._MIGRATED_PATHS:
            SQLiteKGBackend._MIGRATED_PATHS.add(self._db_path)
            try:
                from .migrations import migrate_entity_types_sqlite_path
                migrate_entity_types_sqlite_path(self._db_path)
            except Exception:  # noqa: BLE001
                pass

    # ── Capabilities ──────────────────────────────────────────────────────

    def capabilities(self) -> Set[KGStoreCapability]:
        caps = {
            KGStoreCapability.ADD_ENTITY,
            KGStoreCapability.GET_ENTITY,
            KGStoreCapability.DELETE_ENTITY,
            KGStoreCapability.LIST_ENTITIES,
            KGStoreCapability.ADD_RELATIONSHIP,
            KGStoreCapability.DELETE_RELATIONSHIP,
            KGStoreCapability.LIST_RELATIONSHIPS,
            KGStoreCapability.IMPORT_BULK,
            KGStoreCapability.CLEAR_GRAPH,
            KGStoreCapability.GET_STATS,
            KGStoreCapability.FULL_TEXT_SEARCH,
        }
        if HAS_NETWORKX:
            caps.add(KGStoreCapability.GRAPH_ALGORITHMS)
            caps.add(KGStoreCapability.ADAPTIVE_SUBGRAPH)
        return caps

    def test_connection(self) -> Dict[str, Any]:
        try:
            conn = self._get_conn()
            conn.execute("SELECT 1").fetchone()
            conn.close()
            return {"success": True, "message": "SQLite connection OK"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    # ── Cache invalidation ────────────────────────────────────────────────

    def invalidate_cache(self, kg_id: Optional[str] = None) -> None:
        if kg_id is None:
            self._graph_cache.clear()
        else:
            self._graph_cache.pop(kg_id, None); self._name_index.pop(kg_id, None)

    # ── SQLite helpers ────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite_util.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ── Entity CRUD ───────────────────────────────────────────────────────

    def add_entity(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        name: str,
        entity_type: str,
        properties: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        source_detail: Optional[str] = None,
    ) -> int:
        props_json = json.dumps(properties or {})
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO kg_entities
                    (kg_id, profile_id, user_uuid, name, entity_type,
                     properties_json, source, source_detail, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kg_id, name, entity_type)
                DO UPDATE SET
                    properties_json = excluded.properties_json,
                    source          = excluded.source,
                    source_detail   = excluded.source_detail,
                    updated_at      = excluded.updated_at
                """,
                (kg_id, profile_id, user_uuid, name, entity_type,
                 props_json, source, source_detail, now, now),
            )
            conn.commit()
            entity_id = cursor.lastrowid
            if not entity_id:
                row = conn.execute(
                    "SELECT id FROM kg_entities WHERE kg_id=? AND name=? AND entity_type=?",
                    (kg_id, name, entity_type),
                ).fetchone()
                entity_id = row["id"] if row else 0
            self.invalidate_cache(kg_id)
            return entity_id
        finally:
            conn.close()

    def get_entity(self, kg_id: str, entity_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM kg_entities WHERE id=? AND kg_id=?",
                (entity_id, kg_id),
            ).fetchone()
            return _row_to_entity(row) if row else None
        finally:
            conn.close()

    def get_entity_by_name(
        self,
        kg_id: str,
        name: str,
        entity_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        # Served from the in-memory graph when it is already loaded: the planner's
        # capability resolution asked SQLite ~5 times per table for facts the cached
        # DiGraph holds (72 connections per turn on a 15K-node graph), every one
        # serialised behind the process-wide SQLite lock. Same results, same
        # semantics (NOCASE, lowest id wins for the type-less lookup).
        cached = self._cached_entity_by_name(kg_id, name, entity_type)
        if cached is not None:
            return cached if cached is not _CACHE_MISS else None
        conn = self._get_conn()
        try:
            if entity_type:
                row = conn.execute(
                    "SELECT * FROM kg_entities "
                    "WHERE kg_id=? AND name=? COLLATE NOCASE AND entity_type=?",
                    (kg_id, name, entity_type),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM kg_entities "
                    "WHERE kg_id=? AND name=? COLLATE NOCASE",
                    (kg_id, name),
                ).fetchone()
            return _row_to_entity(row) if row else None
        finally:
            conn.close()

    def search_entities(
        self,
        kg_id: str,
        query_text: str,
        limit: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            pattern = f"%{query_text}%"
            if entity_type:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_entities
                    WHERE kg_id=? AND entity_type=?
                      AND (name LIKE ? COLLATE NOCASE
                           OR properties_json LIKE ? COLLATE NOCASE)
                    ORDER BY name LIMIT ?
                    """,
                    (kg_id, entity_type, pattern, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_entities
                    WHERE kg_id=?
                      AND (name LIKE ? COLLATE NOCASE
                           OR properties_json LIKE ? COLLATE NOCASE)
                    ORDER BY name LIMIT ?
                    """,
                    (kg_id, pattern, pattern, limit),
                ).fetchall()
            return [_row_to_entity(r) for r in rows]
        finally:
            conn.close()

    def list_entities(
        self,
        kg_id: str,
        entity_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM kg_entities "
                    "WHERE kg_id=? AND entity_type=? ORDER BY name LIMIT ?",
                    (kg_id, entity_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM kg_entities "
                    "WHERE kg_id=? ORDER BY entity_type, name LIMIT ?",
                    (kg_id, limit),
                ).fetchall()
            return [_row_to_entity(r) for r in rows]
        finally:
            conn.close()

    def update_entity(
        self,
        kg_id: str,
        entity_id: int,
        properties: Dict[str, Any] = None,
        name: str = None,
        entity_type: str = None,
    ) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT properties_json FROM kg_entities WHERE id=? AND kg_id=?",
                (entity_id, kg_id),
            ).fetchone()
            if not row:
                return False
            existing = json.loads(row["properties_json"] or "{}")
            if properties:
                existing.update(properties)
            now = datetime.now(timezone.utc).isoformat()
            sets = ["properties_json=?", "updated_at=?"]
            vals: list = [json.dumps(existing), now]
            if name is not None:
                sets.append("name=?")
                vals.append(name)
            if entity_type is not None:
                sets.append("entity_type=?")
                vals.append(entity_type)
            vals.extend([entity_id, kg_id])
            conn.execute(
                f"UPDATE kg_entities SET {', '.join(sets)} WHERE id=? AND kg_id=?",
                vals,
            )
            conn.commit()
            self.invalidate_cache(kg_id)
            return True
        finally:
            conn.close()

    def delete_entity(self, kg_id: str, entity_id: int) -> bool:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM kg_entities WHERE id=? AND kg_id=?",
                (entity_id, kg_id),
            )
            conn.commit()
            self.invalidate_cache(kg_id)
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ── Relationship CRUD ─────────────────────────────────────────────────

    def add_relationship(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        source_entity_id: int,
        target_entity_id: int,
        relationship_type: str,
        cardinality: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
    ) -> int:
        metadata_json = json.dumps(metadata or {})
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO kg_relationships
                    (kg_id, profile_id, user_uuid, source_entity_id, target_entity_id,
                     relationship_type, cardinality, metadata_json, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kg_id, source_entity_id, target_entity_id, relationship_type)
                DO UPDATE SET
                    cardinality   = excluded.cardinality,
                    metadata_json = excluded.metadata_json,
                    source        = excluded.source
                """,
                (kg_id, profile_id, user_uuid, source_entity_id, target_entity_id,
                 relationship_type, cardinality, metadata_json, source, now),
            )
            conn.commit()
            rel_id = cursor.lastrowid
            if not rel_id:
                row = conn.execute(
                    """SELECT id FROM kg_relationships
                       WHERE kg_id=? AND source_entity_id=?
                         AND target_entity_id=? AND relationship_type=?""",
                    (kg_id, source_entity_id, target_entity_id, relationship_type),
                ).fetchone()
                rel_id = row["id"] if row else 0
            self.invalidate_cache(kg_id)
            return rel_id
        finally:
            conn.close()

    # ── Cache-served read fast paths ───────────────────────────────────
    # (a miss on a fully-loaded graph is a definite "absent" — never a reason to
    #  fall through to SQL; _CACHE_MISS is the sentinel that says so)
    def _cached_entity_by_name(self, kg_id: str, name: str, entity_type: Optional[str]):
        G = self._graph_cache.get(kg_id)
        if G is None or not name:
            return None
        idx = self._name_index.get(kg_id)
        if idx is None:
            idx = {}
            for nid in sorted(G.nodes):  # lowest id first == the SQL "first row" semantics
                d = G.nodes[nid]
                key = str(d.get("name") or "").lower()
                idx.setdefault((key, d.get("entity_type")), nid)
                idx.setdefault((key, None), nid)
            self._name_index[kg_id] = idx
        nid = idx.get((name.lower(), entity_type))
        if nid is None:
            return _CACHE_MISS   # the cache holds the WHOLE graph: a miss is authoritative
        return self._node_to_entity(G, nid)

    @staticmethod
    def _node_to_entity(G, nid) -> Dict[str, Any]:
        d = G.nodes[nid]
        return {
            "id": nid, "name": d.get("name"), "entity_type": d.get("entity_type"),
            "properties": dict(d.get("properties") or {}), "source": d.get("source"),
            "source_detail": d.get("source_detail"), "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        }

    def _cached_relationships(self, kg_id: str, entity_id: int, direction: str):
        G = self._graph_cache.get(kg_id)
        if G is None or entity_id not in G:
            return None
        out = []
        edges = []
        if direction in ("outgoing", "both"):
            edges += [(entity_id, t, G.edges[entity_id, t]) for t in G.successors(entity_id)]
        if direction in ("incoming", "both"):
            edges += [(s, entity_id, G.edges[s, entity_id]) for s in G.predecessors(entity_id)]
        seen = set()
        for sid, tid, d in edges:
            rid = d.get("rel_id")
            if rid in seen:
                continue
            seen.add(rid)
            out.append({
                "id": rid, "source_entity_id": sid, "target_entity_id": tid,
                "source_name": G.nodes[sid].get("name"), "source_type": G.nodes[sid].get("entity_type"),
                "target_name": G.nodes[tid].get("name"), "target_type": G.nodes[tid].get("entity_type"),
                "relationship_type": d.get("relationship_type"), "cardinality": d.get("cardinality"),
                "metadata": dict(d.get("metadata") or {}), "source": d.get("source"),
                "created_at": d.get("created_at"),
            })
        out.sort(key=lambda r: (r["id"] is None, r["id"] or 0))
        return out

    def get_relationships(
        self,
        kg_id: str,
        entity_id: int,
        direction: str = "both",
    ) -> List[Dict[str, Any]]:
        cached = self._cached_relationships(kg_id, entity_id, direction)
        if cached is not None:
            return cached
        conn = self._get_conn()
        try:
            results = []
            if direction in ("outgoing", "both"):
                rows = conn.execute(
                    """
                    SELECT r.*, e1.name AS source_name, e1.entity_type AS source_type,
                           e2.name AS target_name, e2.entity_type AS target_type
                    FROM kg_relationships r
                    JOIN kg_entities e1 ON r.source_entity_id = e1.id
                    JOIN kg_entities e2 ON r.target_entity_id = e2.id
                    WHERE r.source_entity_id=? AND r.kg_id=?
                    """,
                    (entity_id, kg_id),
                ).fetchall()
                results.extend(_row_to_relationship(r) for r in rows)

            if direction in ("incoming", "both"):
                rows = conn.execute(
                    """
                    SELECT r.*, e1.name AS source_name, e1.entity_type AS source_type,
                           e2.name AS target_name, e2.entity_type AS target_type
                    FROM kg_relationships r
                    JOIN kg_entities e1 ON r.source_entity_id = e1.id
                    JOIN kg_entities e2 ON r.target_entity_id = e2.id
                    WHERE r.target_entity_id=? AND r.kg_id=?
                    """,
                    (entity_id, kg_id),
                ).fetchall()
                results.extend(_row_to_relationship(r) for r in rows)

            seen: set = set()
            unique = []
            for rel in results:
                if rel["id"] not in seen:
                    seen.add(rel["id"])
                    unique.append(rel)
            return unique
        finally:
            conn.close()

    def list_relationships(
        self,
        kg_id: str,
        entity_id: Optional[int] = None,
        relationship_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            query = """
                SELECT r.*, e1.name AS source_name, e1.entity_type AS source_type,
                       e2.name AS target_name, e2.entity_type AS target_type
                FROM kg_relationships r
                JOIN kg_entities e1 ON r.source_entity_id = e1.id
                JOIN kg_entities e2 ON r.target_entity_id = e2.id
                WHERE r.kg_id=?
            """
            params: list = [kg_id]
            if entity_id is not None:
                query += " AND (r.source_entity_id=? OR r.target_entity_id=?)"
                params.extend([entity_id, entity_id])
            if relationship_type:
                query += " AND r.relationship_type=?"
                params.append(relationship_type)
            query += " ORDER BY r.created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [_row_to_relationship(r) for r in rows]
        finally:
            conn.close()

    def delete_relationship(self, kg_id: str, relationship_id: int) -> bool:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM kg_relationships WHERE id=? AND kg_id=?",
                (relationship_id, kg_id),
            )
            conn.commit()
            self.invalidate_cache(kg_id)
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ── Bulk operations ───────────────────────────────────────────────────

    def import_bulk(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        entities: List[Dict],
        relationships: List[Dict],
    ) -> Dict[str, int]:
        # Batched, single-transaction import.  The per-row path (add_entity /
        # add_relationship) opened a fresh connection and committed (one fsync)
        # for EVERY row plus two name lookups per relationship — for a large
        # industry model (~15k entities / ~9k edges) that is ~25k fsyncs and
        # ~44k connections, all serialized through the process-wide SQLite lock,
        # so a subscribe/import took minutes.  Here: two executemany batches and
        # a SINGLE commit, with name→id resolution from one in-transaction map.
        now = datetime.now(timezone.utc).isoformat()

        # THE write chokepoint validates (Phase C of the Aug-2026 KG program):
        #  * an unknown entity/relationship TYPE is rejected loudly and counted —
        #    add_entity() always validated, import_bulk() never did, so an LLM- or
        #    marketplace-supplied payload could carry any type name into the graph;
        #  * `layer` is stamped when absent (physical types → physical, else logical)
        #    so every node is visible to the layer-aware consumers;
        #  * `source_detail` is carried (it was hard-coded to NULL — provenance dropped).
        rejected_entities = 0
        ent_rows = []
        seen_keys: set = set()
        for ent in entities:
            name = ent.get("name")
            etype = ent.get("entity_type")
            if not name or not etype:
                logger.warning(f"Bulk import: skipping entity without name/entity_type: {ent.get('name')}")
                rejected_entities += 1
                continue
            if etype not in _KG_ENTITY_TYPES:
                logger.warning(f"Bulk import: REJECTED entity '{name}' — unknown entity_type '{etype}'")
                rejected_entities += 1
                continue
            props = dict(ent.get("properties") or {})
            if not props.get("layer"):
                props["layer"] = "physical" if etype in _KG_PHYSICAL_TYPES else "logical"
            seen_keys.add((str(name).lower(), etype))
            ent_rows.append((
                kg_id, profile_id, user_uuid, name, etype,
                json.dumps(props),
                ent.get("source", "manual"), ent.get("source_detail"), now, now,
            ))

        conn = self._get_conn()
        try:
            existing_keys = {(r[0].lower(), r[1]) for r in conn.execute(
                "SELECT name, entity_type FROM kg_entities WHERE kg_id=?", (kg_id,))}
            entities_updated = len(seen_keys & existing_keys)
            entities_inserted = len(seen_keys - existing_keys)
            if ent_rows:
                conn.executemany(
                    """
                    INSERT INTO kg_entities
                        (kg_id, profile_id, user_uuid, name, entity_type,
                         properties_json, source, source_detail, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(kg_id, name, entity_type)
                    DO UPDATE SET
                        properties_json = excluded.properties_json,
                        source          = excluded.source,
                        source_detail   = excluded.source_detail,
                        updated_at      = excluded.updated_at
                    """,
                    ent_rows,
                )

            # Resolve names → ids ONCE from an in-transaction snapshot (replaces the
            # two get_entity_by_name round-trips per relationship).  COLLATE NOCASE
            # matching is reproduced by lower-casing keys; ORDER BY id makes the
            # type-less fallback deterministic (first/lowest id), matching the old
            # get_entity_by_name(name) "first row" semantics.
            by_name_type: Dict[tuple, int] = {}
            by_name: Dict[str, int] = {}
            for row in conn.execute(
                "SELECT id, name, entity_type FROM kg_entities WHERE kg_id=? ORDER BY id",
                (kg_id,),
            ):
                by_name_type[(row["name"].lower(), row["entity_type"])] = row["id"]
                by_name.setdefault(row["name"].lower(), row["id"])

            existing_rels = {(r[0], r[1], r[2]) for r in conn.execute(
                "SELECT source_entity_id, target_entity_id, relationship_type FROM kg_relationships WHERE kg_id=?",
                (kg_id,))}
            rel_rows = []
            rejected_relationships = 0
            unresolved_relationships = 0
            rel_keys: set = set()
            for rel in relationships:
                sname = rel.get("source_name")
                tname = rel.get("target_name")
                rtype = rel.get("relationship_type")
                if not sname or not tname or not rtype:
                    rejected_relationships += 1
                    continue
                if rtype not in _KG_RELATIONSHIP_TYPES:
                    logger.warning(f"Bulk import: REJECTED relationship {sname} --[{rtype}]--> {tname} — unknown relationship_type")
                    rejected_relationships += 1
                    continue
                stype = rel.get("source_type")
                ttype = rel.get("target_type")
                sid = by_name_type.get((sname.lower(), stype)) if stype else by_name.get(sname.lower())
                tid = by_name_type.get((tname.lower(), ttype)) if ttype else by_name.get(tname.lower())
                if sid and tid:
                    rel_keys.add((sid, tid, rtype))
                    rel_rows.append((
                        kg_id, profile_id, user_uuid, sid, tid, rtype,
                        rel.get("cardinality"), json.dumps(rel.get("metadata") or {}),
                        rel.get("source", "manual"), now,
                    ))
                else:
                    unresolved_relationships += 1
                    missing = []
                    if not sid:
                        missing.append(f"source '{sname}' (type: {stype or 'any'})")
                    if not tid:
                        missing.append(f"target '{tname}' (type: {ttype or 'any'})")
                    logger.warning(
                        f"Bulk import: could not resolve {', '.join(missing)} for "
                        f"{sname} --[{rtype}]--> {tname}"
                    )

            if rel_rows:
                conn.executemany(
                    """
                    INSERT INTO kg_relationships
                        (kg_id, profile_id, user_uuid, source_entity_id, target_entity_id,
                         relationship_type, cardinality, metadata_json, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(kg_id, source_entity_id, target_entity_id, relationship_type)
                    DO UPDATE SET
                        cardinality   = excluded.cardinality,
                        metadata_json = excluded.metadata_json,
                        source        = excluded.source
                    """,
                    rel_rows,
                )

            conn.commit()  # single fsync for the whole import
            self.invalidate_cache(kg_id)
            rels_updated = len(rel_keys & existing_rels)
            return {
                # `entities_added` / `relationships_added` = rows actually INSERTED (they
                # used to report rows submitted, so an idempotent re-import "added" everything)
                "entities_added": entities_inserted,
                "entities_updated": entities_updated,
                "entities_rejected": rejected_entities,
                "relationships_added": len(rel_keys) - rels_updated,
                "relationships_updated": rels_updated,
                "relationships_rejected": rejected_relationships,
                "relationships_unresolved": unresolved_relationships,
                # legacy readers that summed submitted rows keep a usable figure
                "entities_submitted": len(ent_rows),
                "relationships_submitted": len(rel_rows),
            }
        finally:
            conn.close()

    def get_relationship(self, kg_id: str, relationship_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                SELECT r.*, e1.name AS source_name, e1.entity_type AS source_type,
                       e2.name AS target_name, e2.entity_type AS target_type
                FROM kg_relationships r
                JOIN kg_entities e1 ON r.source_entity_id = e1.id
                JOIN kg_entities e2 ON r.target_entity_id = e2.id
                WHERE r.kg_id=? AND r.id=?
                """, (kg_id, relationship_id)).fetchone()
            return _row_to_relationship(row) if row else None
        finally:
            conn.close()

    def replace_model_layer(self, kg_id, profile_id, user_uuid, entities, relationships, keep_layers):
        """Native form of the generic operation (see KGStoreBackend.replace_model_layer):
        the kept set and the kept edges are read and the walked layer deleted in ONE
        transaction (foreign keys cascade the walked edges), then the fresh walk is
        imported and the kept edges re-linked by name."""
        from trusted_data_agent.kgstore.types import is_kept_on_replace
        keep = set(keep_layers or ())
        conn = self._get_conn()
        try:
            # THE keep rule (kgstore.types.is_kept_on_replace), applied to each row in Python
            # rather than restated in SQL: the kept layers, an extension's realisation, the
            # stacked contracts — one rule for every backend
            kept_ids = set()
            for r in conn.execute("SELECT id, entity_type, properties_json FROM kg_entities WHERE kg_id=?",
                                  (kg_id,)).fetchall():
                try:
                    props = json.loads(r["properties_json"]) if r["properties_json"] else {}
                except (TypeError, ValueError):
                    props = {}
                if is_kept_on_replace({"entity_type": r["entity_type"], "properties": props}, keep):
                    kept_ids.add(int(r["id"]))
            kept_edges: List[Dict[str, Any]] = []
            if kept_ids:
                idph = ",".join("?" * len(kept_ids))
                ids = tuple(kept_ids)
                rows = conn.execute(
                    f"""
                    SELECT r.*, e1.name AS source_name, e1.entity_type AS source_type,
                           e2.name AS target_name, e2.entity_type AS target_type
                    FROM kg_relationships r
                    JOIN kg_entities e1 ON r.source_entity_id = e1.id
                    JOIN kg_entities e2 ON r.target_entity_id = e2.id
                    WHERE r.kg_id=? AND (r.source_entity_id IN ({idph}) OR r.target_entity_id IN ({idph}))
                    """, (kg_id, *ids, *ids)).fetchall()
                kept_edges = [_row_to_relationship(r) for r in rows]
                # edges that would survive untouched (both ends kept) are not re-linked
                del_r = conn.execute(
                    f"SELECT COUNT(*) FROM kg_relationships WHERE kg_id=? AND NOT "
                    f"(source_entity_id IN ({idph}) AND target_entity_id IN ({idph}))",
                    (kg_id, *ids, *ids)).fetchone()[0]
                del_e = conn.execute(
                    f"DELETE FROM kg_entities WHERE kg_id=? AND id NOT IN ({idph})", (kg_id, *ids)).rowcount
                conn.execute(
                    f"DELETE FROM kg_relationships WHERE kg_id=? AND NOT "
                    f"(source_entity_id IN ({idph}) AND target_entity_id IN ({idph}))",
                    (kg_id, *ids, *ids))
            else:
                del_r = conn.execute("DELETE FROM kg_relationships WHERE kg_id=?", (kg_id,)).rowcount
                del_e = conn.execute("DELETE FROM kg_entities WHERE kg_id=?", (kg_id,)).rowcount
            conn.commit()
        finally:
            conn.close()
        self.invalidate_cache(kg_id)
        res = dict(self.import_bulk(kg_id, profile_id, user_uuid, entities, relationships) or {})
        relinked, dropped, dropped_detail = self._relink_kept_edges(
            kg_id, profile_id, user_uuid,
            [e for e in kept_edges if not (e["source_entity_id"] in kept_ids and e["target_entity_id"] in kept_ids)],
            kept_ids)
        self.invalidate_cache(kg_id)
        res.update({"entities_kept": len(kept_ids), "entities_deleted": int(del_e),
                    "relationships_deleted": int(del_r), "kept_edges_relinked": relinked,
                    "kept_edges_dropped": dropped, "kept_edges_dropped_detail": dropped_detail})
        return res

    def clear_graph(self, kg_id: str) -> Dict[str, int]:
        conn = self._get_conn()
        try:
            rels_deleted = conn.execute(
                "DELETE FROM kg_relationships WHERE kg_id=?", (kg_id,)
            ).rowcount
            entities_deleted = conn.execute(
                "DELETE FROM kg_entities WHERE kg_id=?", (kg_id,)
            ).rowcount
            conn.commit()
            self.invalidate_cache(kg_id)
            return {"entities_deleted": entities_deleted, "relationships_deleted": rels_deleted}
        finally:
            conn.close()

    # ── Statistics ────────────────────────────────────────────────────────

    def get_stats(self, kg_id: str) -> Dict[str, Any]:
        G = self._get_graph(kg_id)
        if G is None:
            conn = self._get_conn()
            try:
                ent_count = conn.execute(
                    "SELECT COUNT(*) FROM kg_entities WHERE kg_id=?", (kg_id,)
                ).fetchone()[0]
                rel_count = conn.execute(
                    "SELECT COUNT(*) FROM kg_relationships WHERE kg_id=?", (kg_id,)
                ).fetchone()[0]
                return {
                    "total_entities": ent_count,
                    "total_relationships": rel_count,
                    "networkx_available": False,
                }
            finally:
                conn.close()

        entity_types: Dict[str, int] = {}
        for _, data in G.nodes(data=True):
            etype = data.get("entity_type", "unknown")
            entity_types[etype] = entity_types.get(etype, 0) + 1

        rel_types: Dict[str, int] = {}
        for _, _, data in G.edges(data=True):
            rtype = data.get("relationship_type", "unknown")
            rel_types[rtype] = rel_types.get(rtype, 0) + 1

        undirected = G.to_undirected()
        components = list(nx.connected_components(undirected))

        return {
            "total_entities": G.number_of_nodes(),
            "total_relationships": G.number_of_edges(),
            "entity_types": entity_types,
            "relationship_types": rel_types,
            "connected_components": len(components),
            "density": round(nx.density(G), 4) if G.number_of_nodes() > 1 else 0,
            # `list(nx.simple_cycles(G))` enumerated EVERY simple cycle before the slice —
            # combinatorial on any FK graph with mutual references, and on the export
            # path (marketplace publish, agent-pack export, RDF). find_cycle stops at one.
            "has_cycles": _has_cycle(G),
            "networkx_available": True,
        }

    # ── NetworkX graph algorithms ─────────────────────────────────────────

    def _get_graph(self, kg_id: str) -> Any:
        """Lazy-load NetworkX DiGraph for kg_id; cached until invalidated."""
        if kg_id in self._graph_cache:
            self._graph_cache.move_to_end(kg_id)      # LRU touch
            return self._graph_cache[kg_id]

        if not HAS_NETWORKX:
            logger.warning("NetworkX not installed — graph algorithms unavailable")
            return None

        G = nx.DiGraph()
        conn = self._get_conn()
        try:
            entities = conn.execute(
                "SELECT * FROM kg_entities WHERE kg_id=?", (kg_id,)
            ).fetchall()
            for e in entities:
                G.add_node(
                    e["id"],
                    name=e["name"],
                    entity_type=e["entity_type"],
                    properties=json.loads(e["properties_json"] or "{}"),
                    source=e["source"],
                    source_detail=e["source_detail"],
                    created_at=e["created_at"],
                    updated_at=e["updated_at"],
                )

            rels = conn.execute(
                "SELECT * FROM kg_relationships WHERE kg_id=?", (kg_id,)
            ).fetchall()
            for r in rels:
                G.add_edge(
                    r["source_entity_id"],
                    r["target_entity_id"],
                    rel_id=r["id"],
                    relationship_type=r["relationship_type"],
                    cardinality=r["cardinality"],
                    metadata=json.loads(r["metadata_json"] or "{}"),
                    source=r["source"],
                    created_at=r["created_at"],
                )

            self._graph_cache[kg_id] = G
            self._graph_cache.move_to_end(kg_id)
            self._name_index.pop(kg_id, None)
            # bounded: the backend is a process singleton and every kg_id ever touched
            # used to keep its full DiGraph alive forever
            while len(self._graph_cache) > _GRAPH_CACHE_MAX:
                old_id, _ = self._graph_cache.popitem(last=False)
                self._name_index.pop(old_id, None)
                logger.info(f"KG graph cache: evicted {old_id} (LRU, cap {_GRAPH_CACHE_MAX})")
            return G
        finally:
            conn.close()

    def extract_subgraph(
        self,
        kg_id: str,
        entity_ids: List[int],
        depth: int = 2,
        max_nodes: int = 50,
    ) -> Dict[str, List]:
        G = self._get_graph(kg_id)
        if G is None:
            return {"entities": [], "relationships": []}

        visited: Set[int] = set()
        queue: List = []

        for eid in entity_ids:
            if eid in G:
                queue.append((eid, 0))
                visited.add(eid)

        while queue and len(visited) <= max_nodes:
            node_id, d = queue.pop(0)
            if d >= depth:
                continue
            neighbors = set(G.successors(node_id)) | set(G.predecessors(node_id))
            for neighbor in neighbors:
                if neighbor not in visited and len(visited) < max_nodes:
                    visited.add(neighbor)
                    queue.append((neighbor, d + 1))

        return _subgraph_to_dict(G, visited)

    def extract_subgraph_adaptive(
        self,
        kg_id: str,
        seed_entity_ids: List[int],
        query_entity_ids: Optional[List[int]] = None,
        max_nodes: int = 500,
        query_terms: Optional[List[str]] = None,
        per_table_cap: Optional[int] = None,
    ) -> Dict[str, List]:
        """
        Entity-type-aware adaptive subgraph extraction.
        Identical logic to GraphStore.extract_subgraph_adaptive.

        ``query_terms`` + ``per_table_cap`` (both optional — absent ⇒ the legacy
        coverage behaviour, byte-identical) switch Phase 2 to RELEVANCE-SCOPED
        column expansion: per table, seed columns always, then columns matching
        the question, then keys/required columns, up to ``per_table_cap``; the
        withheld remainder is reported per table in ``omitted_columns`` so the
        renderer can say "+N more columns". Measured before this (policing graph):
        two tables' 494 columns filled the whole 500-node budget on every
        question, ~78K tokens per turn.
        """
        G = self._get_graph(kg_id)
        return _adaptive_subgraph_from_graph(
            G, seed_entity_ids, query_entity_ids, max_nodes, query_terms, per_table_cap)

    def get_full_graph(self, kg_id: str, max_nodes: int = 100) -> Dict[str, List]:
        G = self._get_graph(kg_id)
        if G is None:
            return {"entities": [], "relationships": []}
        nodes = set(list(G.nodes())[:max_nodes])
        return _subgraph_to_dict(G, nodes)

    def get_typed_subgraph(
        self,
        kg_id: str,
        types: Optional[List[str]] = None,
        max_nodes: int = 500,
        focus: Optional[List[int]] = None,
        project_dependencies: bool = False,
        divisions: Optional[List[str]] = None,
        domains: Optional[List[str]] = None,
    ) -> Dict[str, List]:
        """Seed → connectivity-expand → fill: a connected, dependency-coherent sample.

        Three steps (the same engine for the detail lens and the focus lens):

        1. SEED — if ``focus`` entity ids are given, they seed the budget (focus+context:
           the allocation centres on what's in focus). Otherwise a type-stratified set
           of the highest-degree nodes per selected type seeds it, guaranteeing every
           type is represented.
        2. EXPAND — fill the remaining budget by walking the graph outward from the seed,
           adding the candidates most strongly connected to what's already shown (BFS by
           waves, ranked by edges-into-the-visible-set). This produces a CONNECTED
           subgraph that follows real dependencies, not scattered high-degree hubs.
        3. FILL — if a disconnected remainder leaves budget unused, top up with the
           highest-degree leftovers so the budget is honoured.

        Deterministic. ``types`` restricts expansion to the selected entity types
        (focus seeds are always included).
        """
        G = self._get_graph(kg_id)
        if G is None or len(G) == 0:
            return {"entities": [], "relationships": []}
        type_set = set(types) if types else None
        div_set = {str(d).strip().lower() for d in divisions if str(d).strip()} if divisions else None
        dom_set = {str(d).strip().lower() for d in domains if str(d).strip()} if domains else None
        importance = nx.degree_centrality(G)

        def etype(n):
            return G.nodes[n].get("entity_type", "unknown")

        def node_facets(n):
            return _node_facet_values(G.nodes[n])

        def allowed(n):
            if type_set is not None and etype(n) not in type_set:
                return False
            if div_set is not None or dom_set is not None:
                ndiv, ndom = node_facets(n)
                if div_set is not None and (ndiv is None or ndiv not in div_set):
                    return False
                if dom_set is not None and (ndom is None or ndom not in dom_set):
                    return False
            return True

        # Facet catalog over the FULL graph (distinct division/domain values present),
        # so the inspector renders a facet only when it exists — independent of the
        # current selection.  Normalised lower-case keys (UI title-cases for display).
        facet_div: Dict[str, int] = {}
        facet_dom: Dict[str, int] = {}
        for n in G.nodes():
            ndiv, ndom = node_facets(n)
            if ndiv:
                facet_div[ndiv] = facet_div.get(ndiv, 0) + 1
            if ndom:
                facet_dom[ndom] = facet_dom.get(ndom, 0) + 1
        facets: Dict[str, Dict[str, int]] = {}
        if facet_div:
            facets["division"] = facet_div
        if facet_dom:
            facets["domain"] = facet_dom

        def neighbours(n):
            for nbr in G.successors(n):
                yield nbr
            for nbr in G.predecessors(n):
                yield nbr

        chosen: Set[int] = set()

        # 1) SEED
        if focus:
            chosen = {fid for fid in focus if fid in G}
        if not chosen:
            by_type: Dict[str, List[int]] = {}
            for nid in G.nodes():
                if not allowed(nid):
                    continue
                by_type.setdefault(etype(nid), []).append(nid)
            if not by_type:
                return {"entities": [], "relationships": []}
            # Reserve ~half the budget for stratified seeds (presence guarantee);
            # the rest is filled by dependency expansion below.
            seed_per_type = max(1, max(1, max_nodes // 2) // len(by_type))
            for _t, ids in by_type.items():
                ids_sorted = sorted(ids, key=lambda i: (importance.get(i, 0), -i), reverse=True)
                chosen.update(ids_sorted[:seed_per_type])

        # 2) EXPAND by connectivity (dependency-aware), respecting the type filter.
        frontier = set(chosen)
        while len(chosen) < max_nodes and frontier:
            cand: Dict[int, int] = {}
            for n in frontier:
                for nbr in neighbours(n):
                    if nbr in chosen or not allowed(nbr):
                        continue
                    cand[nbr] = cand.get(nbr, 0) + 1
            if not cand:
                break
            ranked = sorted(cand.items(),
                            key=lambda kv: (kv[1], importance.get(kv[0], 0), -kv[0]),
                            reverse=True)
            newly: List[int] = []
            for nid, _score in ranked:
                if len(chosen) >= max_nodes:
                    break
                chosen.add(nid)
                newly.append(nid)
            frontier = set(newly)

        # 3) FILL any unused budget (disconnected remainder) with top-degree leftovers.
        if len(chosen) < max_nodes:
            leftovers = sorted((n for n in G.nodes() if n not in chosen and allowed(n)),
                               key=lambda i: (importance.get(i, 0), -i), reverse=True)
            for nid in leftovers:
                if len(chosen) >= max_nodes:
                    break
                chosen.add(nid)

        result = _subgraph_to_dict(G, chosen)
        # Relationship projection (link aggregation): when a type filter hides the
        # nodes a relationship runs through (e.g. columns), lift the relationship up
        # to the nearest VISIBLE ancestor via the containment hierarchy so the
        # derived dependency between the visible nodes is shown (table↔table FK from
        # column↔column FK; domain↔domain from column→table→domain; etc.).
        # Gated: only when the caller asks (project_dependencies) — it's a compute-heavy
        # details-on-demand overlay the frontend requests on zoom-in / expand, not on
        # every fetch. Needs a type filter (nothing to lift when all levels are visible).
        if project_dependencies and type_set is not None:
            result["relationships"].extend(
                self._derive_lifted_edges(G, chosen, type_set, result["relationships"]))
        result["facets"] = facets
        return result

    def _derive_lifted_edges(self, G, chosen, type_set, existing_rels) -> List[Dict[str, Any]]:
        lift_cache: Dict[int, Optional[int]] = {}

        def lift(n, depth=0):
            if n in chosen:
                return n
            if n in lift_cache:
                return lift_cache[n]
            t = G.nodes[n].get("entity_type", "unknown")
            # A selected-type node that's merely budget-hidden is a legit omission —
            # don't lift through it. Only lift through TYPE-excluded nodes.
            if t in type_set or depth > 8:
                lift_cache[n] = None
                return None
            lift_cache[n] = None  # cycle guard
            for pred in G.predecessors(n):
                ed = G.get_edge_data(pred, n) or {}
                if ed.get("relationship_type") == "contains":
                    up = lift(pred, depth + 1)
                    if up is not None:
                        lift_cache[n] = up
                        return up
            return None

        existing = {(r["source_id"], r["target_id"], r["relationship_type"]) for r in existing_rels}
        agg: Dict[tuple, dict] = {}
        for a, b, ed in G.edges(data=True):
            rt = ed.get("relationship_type")
            if rt == "contains":
                continue
            if a in chosen and b in chosen:
                continue  # already a direct edge
            la, lb = lift(a), lift(b)
            if la is None or lb is None or la == lb:
                continue
            key = (la, lb, rt)
            if key in existing:
                continue
            slot = agg.setdefault(key, {"count": 0, "via": []})
            slot["count"] += 1
            # Capture the REAL underlying endpoints (e.g. the join columns) so the derived
            # table↔table edge can answer "which column connects these?" even when those
            # columns are outside the render budget. Capped to keep metadata bounded.
            if len(slot["via"]) < 25 and (a not in chosen or b not in chosen):
                slot["via"].append({"from": G.nodes[a].get("name", ""),
                                    "to": G.nodes[b].get("name", "")})
        return [{
            "id": 0, "source_id": la, "target_id": lb,
            "source_name": G.nodes[la].get("name", ""),
            "target_name": G.nodes[lb].get("name", ""),
            "relationship_type": rt, "cardinality": None,
            "metadata": {"derived": True, "count": slot["count"], "via": slot["via"]},
        } for (la, lb, rt), slot in agg.items()]

    def get_schema_overview(self, kg_id: str) -> Dict[str, List]:
        """Type-level meta-graph (the overview lens): one node per entity type
        (with count), edges aggregated as (source_type, relationship_type,
        target_type) with counts.  Always tiny + complete — shows every type and
        how they connect, regardless of graph size."""
        G = self._get_graph(kg_id)
        if G is None or len(G) == 0:
            return {"nodes": [], "edges": []}
        type_counts: Dict[str, int] = {}
        for _nid, data in G.nodes(data=True):
            t = data.get("entity_type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        edge_agg: Dict[tuple, int] = {}
        for s, t, data in G.edges(data=True):
            st = G.nodes[s].get("entity_type", "unknown")
            tt = G.nodes[t].get("entity_type", "unknown")
            rt = data.get("relationship_type", "unknown")
            edge_agg[(st, rt, tt)] = edge_agg.get((st, rt, tt), 0) + 1
        return {
            "nodes": [{"type": k, "count": v} for k, v in sorted(type_counts.items())],
            "edges": [{"source_type": st, "relationship_type": rt, "target_type": tt, "count": c}
                      for (st, rt, tt), c in sorted(edge_agg.items())],
        }

    def find_shortest_path(
        self,
        kg_id: str,
        source_id: int,
        target_id: int,
    ) -> Optional[List[int]]:
        G = self._get_graph(kg_id)
        if G is None:
            return None
        try:
            return nx.shortest_path(G.to_undirected(), source_id, target_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_connected_entities(self, kg_id: str, entity_id: int) -> Dict[str, List[int]]:
        G = self._get_graph(kg_id)
        if G is None:
            return {"descendants": [], "ancestors": []}
        try:
            descendants = list(nx.descendants(G, entity_id))
        except nx.NetworkXError:
            descendants = []
        try:
            ancestors = list(nx.ancestors(G, entity_id))
        except nx.NetworkXError:
            ancestors = []
        return {"descendants": descendants, "ancestors": ancestors}

    def all_table_columns(self, kg_id: str) -> Dict[str, List[str]]:
        """{logical table leaf: [column leaves]} over the whole cached graph (one pass; the
        graph is already loaded for every enrichment). Physical `table` nodes are keyed too
        (their `column` children) so a deployed leaf that differs from the logical one —
        `v_Incident` beside `Incident` — resolves. Empty when the graph is not loaded."""
        from trusted_data_agent.kgstore.keys import with_declared_keys as _with_declared_keys
        G = self._get_graph(kg_id)
        if G is None:
            return {}
        out: Dict[str, List[str]] = {}
        for nid, nd in G.nodes(data=True):
            et = nd.get("entity_type")
            if et not in ("entity", "table"):
                continue
            cols: List[str] = []
            for succ in G.successors(nid):
                sd = G.nodes[succ]
                if sd.get("entity_type") in ("attribute", "column") and (G.get_edge_data(nid, succ) or {}).get("relationship_type") == "contains":
                    leaf = str(sd.get("name") or "").rpartition(".")[2]
                    if leaf and leaf not in cols:
                        cols.append(leaf)
            if len(cols) < 2:
                continue    # a one-column "table" is not a complete list — the floor is
                            # applied to the CONTAINS evidence, before the declared key is
                            # unioned in, so adding keys can never make a broken list pass.
            # A column the ontology declares as part of the table's KEY exists by
            # construction. Reading `contains` alone reported the declared join key of
            # Policing_Core.v_Offence as a non-existent column on a statement that ran
            # and returned rows (prod session be4979bf) — while the JOIN check, on the
            # same graph, correctly recognised that very key.
            cols = _with_declared_keys(cols, nd.get("properties") or {})
            tleaf = str(nd.get("name") or "").rpartition(".")[2]
            if tleaf and (tleaf not in out or et == "entity"):
                out[tleaf] = cols
        return out

    def get_node_neighborhood(self, kg_id: str, entity_id: int,
                              max_neighbors: int = 500) -> Dict[str, Any]:
        """The COMPLETE 1-hop neighbourhood of a node — every directly-connected entity
        plus the connecting relationships — regardless of any render budget. This is the
        consistency guarantee: focusing a node always shows its full relationship set, never
        a budget-truncated subset. Capped at max_neighbors (highest-degree kept) with an
        honest `truncated` flag for pathological hubs."""
        G = self._get_graph(kg_id)
        if G is None or entity_id not in G:
            return {"entities": [], "relationships": [], "truncated": False, "total_degree": 0}
        neigh = set(G.successors(entity_id)) | set(G.predecessors(entity_id))
        total = len(neigh)
        truncated = total > max_neighbors
        if truncated:
            neigh = set(sorted(neigh, key=lambda n: G.degree(n), reverse=True)[:max_neighbors])
        result = _subgraph_to_dict(G, {entity_id} | neigh)
        result["truncated"] = truncated
        result["total_degree"] = total
        return result

    def get_entity_importance(self, kg_id: str) -> Dict[int, float]:
        G = self._get_graph(kg_id)
        if G is None or len(G) == 0:
            return {}
        return nx.degree_centrality(G)

    def detect_cycles(self, kg_id: str) -> List[List[int]]:
        G = self._get_graph(kg_id)
        if G is None:
            return []
        try:
            import itertools
            return list(itertools.islice(nx.simple_cycles(G), 20))   # bounded, never a full enumeration
        except Exception:
            return []


# ── Module-level row conversion helpers ──────────────────────────────────────

def _row_to_entity(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "entity_type": row["entity_type"],
        "properties": json.loads(row["properties_json"] or "{}"),
        "source": row["source"],
        "source_detail": row["source_detail"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_relationship(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "source_entity_id": row["source_entity_id"],
        "target_entity_id": row["target_entity_id"],
        "source_name": row["source_name"],
        "source_type": row["source_type"],
        "target_name": row["target_name"],
        "target_type": row["target_type"],
        "relationship_type": row["relationship_type"],
        "cardinality": row["cardinality"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "source": row["source"],
        "created_at": row["created_at"],
    }


def _node_facet_values(data: Dict[str, Any]):
    """Extract (division, domain) facet keys for a node, normalised lower-case.

    Reads the ``division``/``domain`` properties present on content nodes
    (tables/columns/physical), and falls back to the node name for the domain
    grouping nodes themselves (``entity_type == "domain"`` with level division/domain).
    Returns (division|None, domain|None).
    """
    props = data.get("properties") or {}
    etype = data.get("entity_type")
    div = props.get("division")
    dom = props.get("domain")
    if etype == "domain":
        level = props.get("level")
        nm = data.get("name")
        if level == "division" and not div:
            div = nm
        elif level == "domain" and not dom:
            dom = nm
    div = str(div).strip().lower() if div not in (None, "") else None
    dom = str(dom).strip().lower() if dom not in (None, "") else None
    return div, dom



def _adaptive_subgraph_from_graph(
    G,
    seed_entity_ids,
    query_entity_ids=None,
    max_nodes: int = 500,
    query_terms=None,
    per_table_cap=None,
):
    """Entity-type-aware adaptive subgraph extraction over a NetworkX DiGraph.

    THE one implementation, shared by every backend (SQLite serves its cached
    graph; Neo4j builds a DiGraph and calls this), so adaptive extraction is not
    a per-backend reimplementation. Pure: no I/O, no self.
    """
    if G is None or not seed_entity_ids:
        return {"entities": [], "relationships": []}

        return {"entities": [], "relationships": []}

    if query_entity_ids is None:
        query_entity_ids = list(seed_entity_ids)
    query_id_set = set(query_entity_ids)

    # ── Phase 1a: FK-Chain Traversal ──────────────────────────────────
    discovered_tables: Set[int] = set()
    discovered_fk_nodes: Set[int] = set()
    distance_from_seed: Dict[int, int] = {}
    bfs_queue: deque = deque()
    # HOW each node entered the subgraph — the KG theatre draws the expansion in this
    # order (seeds first, then the key chain outward, then joinable hints, then the
    # columns and the semantic neighbours). Recorded as the phases run, so the
    # narration can never disagree with what the extraction actually did.
    provenance: Dict[int, str] = {}

    # A PHYSICAL seed stands for its LOGICAL twin. On a layered graph the same fact is
    # two nodes — `airlines_core.mileage_accrual.base_miles` (column) implements
    # `loyalty.mileage_accrual.base_miles` (attribute) — and search returns both. Only the
    # logical layer is structurally expandable (its table is an `entity`), so a physical
    # column seed used to be a dead end: its table never entered the subgraph, and on the
    # prod turn that motivated this the seed cut held nine physical columns whose tables
    # (mileage_accrual, ffp_member) were absent from TABLE SCHEMAS entirely. The twin is
    # one `implements` hop away (physical → logical); it is followed here so a seed
    # expands whichever layer search happened to return.
    _seed_ids: List[int] = []
    for sid in seed_entity_ids:
        if sid not in G:
            continue
        _seed_ids.append(sid)
        if G.nodes[sid].get("entity_type", "") in _KG_PHYSICAL_TYPES:
            for succ in G.successors(sid):
                e = G.get_edge_data(sid, succ) or {}
                if e.get("relationship_type") == "implements" and succ not in _seed_ids:
                    _seed_ids.append(succ)
                    provenance.setdefault(succ, "seed")
    _query_twins = {t for s in query_id_set if s in G and G.nodes[s].get("entity_type", "") in _KG_PHYSICAL_TYPES
                    for t in G.successors(s)
                    if (G.get_edge_data(s, t) or {}).get("relationship_type") == "implements"}
    if _query_twins:
        query_id_set = set(query_id_set) | _query_twins
    seed_entity_ids = _seed_ids

    for sid in seed_entity_ids:
        if sid not in G:
            continue
        provenance.setdefault(sid, "seed")
        etype = G.nodes[sid].get("entity_type", "")
        if etype in _EXPANDABLE_STRUCTURAL_TYPES:
            target = discovered_tables if etype == "entity" else discovered_fk_nodes
            target.add(sid)
            distance_from_seed[sid] = 0
            bfs_queue.append((sid, 0))
        else:
            for nbr in set(G.predecessors(sid)) | set(G.successors(sid)):
                ntype = G.nodes[nbr].get("entity_type", "")
                if ntype in _EXPANDABLE_STRUCTURAL_TYPES:
                    target = discovered_tables if ntype == "entity" else discovered_fk_nodes
                    if nbr not in target:
                        target.add(nbr)
                        distance_from_seed[nbr] = 0
                        bfs_queue.append((nbr, 0))
                        provenance.setdefault(nbr, "seed")

    visited_expandable = discovered_tables | discovered_fk_nodes

    # The key chain is walked for JOINS, so it follows the direction a join needs. From a
    # seed, its own children and every ancestor are in reach (a fact's parent is its
    # lookup: Offence → Incident → Agency). From a NON-seed ancestor the walk does not turn
    # back DOWN to that ancestor's other children: those are the seed's siblings, which the
    # question never reached, and joining through the shared parent is the fan-out shape.
    # Unbounded, one weak seed on a star model pulled the whole star in (local session
    # f622f5ef turn 2: "hacking" reached Offence at score 25 and ten crime entities with
    # ~80 columns were injected for a question about evidence). An edge that declares no
    # direction (no cardinality, not a specialisation) is walked both ways, as before.
    def _child_of(parent_id: int, other_id: int) -> Optional[bool]:
        """True: other is parent_id's CHILD · False: its PARENT · None: undeclared."""
        verdict = None
        for a_, b_ in ((parent_id, other_id), (other_id, parent_id)):
            ed = G.get_edge_data(a_, b_)
            if not ed:
                continue
            rt = ed.get("relationship_type")
            card = str(ed.get("cardinality") or "").upper().replace(" ", "")
            src_is_parent = None
            if rt == "is_a":
                src_is_parent = False                      # specialisation → its supertype
            elif card in ("1:N", "1:M"):
                src_is_parent = True
            elif card in ("N:1", "M:1"):
                src_is_parent = False
            if src_is_parent is None:
                continue
            # a_ → b_ ; is b_ (resp. a_) the child of parent_id?
            is_child = src_is_parent if a_ == parent_id else (not src_is_parent)
            verdict = is_child if verdict is None else (verdict and is_child)
        return verdict

    while bfs_queue:
        node_id, d = bfs_queue.popleft()
        for nbr in set(G.successors(node_id)) | set(G.predecessors(node_id)):
            if nbr in visited_expandable:
                continue
            ntype = G.nodes[nbr].get("entity_type", "")
            if ntype not in _EXPANDABLE_STRUCTURAL_TYPES:
                continue
            if d > 0 and _child_of(node_id, nbr) is True:
                continue                                   # a sibling of the seed — not reached
            target = discovered_tables if ntype == "entity" else discovered_fk_nodes
            target.add(nbr)
            visited_expandable.add(nbr)
            distance_from_seed[nbr] = d + 1
            bfs_queue.append((nbr, d + 1))
            provenance.setdefault(nbr, "fk_chain")

    fk_depth = max(distance_from_seed.values()) if distance_from_seed else 0
    logger.info(
        f"KG adaptive Phase 1a: {len(discovered_tables)} tables, "
        f"{len(discovered_fk_nodes)} FK nodes, max FK-chain depth={fk_depth}"
    )

    # ── Phase 1b: Joinable Table Discovery ───────────────────────────
    all_table_ids: Dict[int, str] = {
        nid: ndata.get("name", "")
        for nid, ndata in G.nodes(data=True)
        if ndata.get("entity_type") == "entity"
    }

    scoped = query_terms is not None or per_table_cap is not None

    def _column_names_for_tables(table_ids: Set[int]) -> Set[str]:
        names: Set[str] = set()
        for tid in table_ids:
            for succ in G.successors(tid):
                if G.nodes[succ].get("entity_type") == "attribute":
                    cname = G.nodes[succ].get("name", "").lower()
                    if cname:
                        names.add(cname)
        return names

    # In SCOPED mode joinable-table discovery is the handler's job (bounded
    # bridge-table discovery by shared column LEAF, ≤6 tables); the greedy
    # full-name round here is skipped. Measured: enabling a leaf comparison
    # in this loop grew the airlines enrichment 4× (91 → 371 nodes) —
    # coverage, not relevance. Legacy callers keep the exact old behaviour.
    _join_rounds = 0 if scoped else _MAX_JOIN_DISCOVERY_ROUNDS

    for round_num in range(_join_rounds):
        seed_col_names = _column_names_for_tables(discovered_tables)
        if not seed_col_names:
            break
        new_tables: Set[int] = set()
        for tid in all_table_ids:
            if tid in discovered_tables:
                continue
            for succ in G.successors(tid):
                if G.nodes[succ].get("entity_type") == "attribute":
                    cname = G.nodes[succ].get("name", "").lower()
                    if cname and cname in seed_col_names:
                        new_tables.add(tid)
                        break
        if not new_tables:
            break
        for tid in new_tables:
            discovered_tables.add(tid)
            distance_from_seed.setdefault(tid, fk_depth + round_num + 1)
            provenance.setdefault(tid, "joinable")
        logger.info(
            f"KG adaptive Phase 1b round {round_num + 1}: +{len(new_tables)} joinable tables"
        )

    # ── Phase 1c: Database Context ────────────────────────────────────
    discovered_databases: Set[int] = set()
    for tid in discovered_tables:
        for pred in G.predecessors(tid):
            if G.nodes[pred].get("entity_type") == "database":
                discovered_databases.add(pred)
                provenance.setdefault(pred, "database")

    # ── Assemble structural visited set ────────────────────────────────
    visited: Set[int] = set()
    visited |= discovered_tables
    visited |= discovered_fk_nodes
    visited |= discovered_databases
    for sid in seed_entity_ids:
        if sid in G:
            visited.add(sid)

    # ── Phase 2: Column Expansion (budget-aware) ──────────────────────
    column_budget = max(0, max_nodes - len(visited))
    # A table's rank = the best rank of the seed that brought it in (itself, or one of
    # its columns). The seeder ranks seeds by relevance; a set-ordered tie between
    # "query tables" threw that order away, so under a tight node budget the columns
    # went to whichever table had the lower node id — the carried-over table from the
    # previous turn, seeded FIRST for a reason, could receive none.
    _seed_rank: Dict[int, int] = {sid: i for i, sid in enumerate(seed_entity_ids)}
    _table_rank: Dict[int, int] = {}
    for sid, rk in _seed_rank.items():
        if sid not in G:
            continue
        if sid in discovered_tables:
            _table_rank[sid] = min(_table_rank.get(sid, rk), rk)
        else:
            for p in G.predecessors(sid):
                if p in discovered_tables:
                    _table_rank[p] = min(_table_rank.get(p, rk), rk)
    sorted_tables = sorted(
        discovered_tables,
        key=lambda tid: (
            0 if tid in query_id_set else 1,
            _table_rank.get(tid, len(_seed_rank)),
            distance_from_seed.get(tid, 999),
            tid,
        ),
    )

    columns_added = 0
    tables_with_columns = 0
    omitted_columns: Dict[str, int] = {}
    terms = [t.lower() for t in (query_terms or []) if t]
    # join-backbone columns (declared keys between two discovered tables) are kept
    # over the cap in scoped mode — computed once for the whole subgraph
    backbone = _join_backbone_columns(G, discovered_tables) if scoped else set()
    for tid in sorted_tables:
        if column_budget <= 0:
            break
        table_cols = [
            succ for succ in G.successors(tid)
            if succ not in visited
            and G.nodes[succ].get("entity_type") == "attribute"
        ]
        if not table_cols:
            continue
        if scoped:
            to_add = _scope_columns(G, table_cols, query_id_set, terms,
                                    per_table_cap, column_budget, backbone)
            if len(to_add) < len(table_cols):
                omitted_columns[G.nodes[tid].get("name", "")] = len(table_cols) - len(to_add)
        else:
            to_add = table_cols[:column_budget]
        if to_add:
            visited.update(to_add)
            column_budget -= len(to_add)
            columns_added += len(to_add)
            tables_with_columns += 1
            for cid in to_add:
                provenance.setdefault(cid, "column")

    logger.info(
        f"KG adaptive Phase 2: {columns_added} columns for "
        f"{tables_with_columns}/{len(discovered_tables)} tables, "
        f"{column_budget} budget remaining"
    )

    # ── Phase 3: Semantic Enrichment ──────────────────────────────────
    semantic_budget = min(max(0, max_nodes - len(visited)), 50)
    semantic_added = 0
    if semantic_budget > 0:
        structural_visited = discovered_tables | discovered_fk_nodes | discovered_databases
        for nid in structural_visited:
            if semantic_budget <= 0:
                break
            for nbr in set(G.successors(nid)) | set(G.predecessors(nid)):
                if nbr in visited:
                    continue
                if G.nodes[nbr].get("entity_type") in _SEMANTIC_TYPES:
                    visited.add(nbr)
                    provenance.setdefault(nbr, "semantic")
                    semantic_budget -= 1
                    semantic_added += 1
                    if semantic_budget <= 0:
                        break

    logger.info(
        f"KG adaptive extraction complete: {len(visited)} total nodes "
        f"({len(discovered_tables)} tables, {columns_added} columns, "
        f"{semantic_added} semantic)"
    )
    out = _subgraph_to_dict(G, visited)
    if omitted_columns:
        out["omitted_columns"] = omitted_columns
    # Additive: {node_id: seed|fk_chain|joinable|database|column|semantic}. A node
    # in the subgraph with no tag (a seed's non-expandable neighbour kept only through
    # `visited`) reads as unknown to the consumer, never as a guess.
    out["provenance"] = {nid: tag for nid, tag in provenance.items() if nid in visited}
    return out

def _subgraph_to_dict(G: Any, node_ids: Set[int]) -> Dict[str, List]:
    entities = []
    for nid in node_ids:
        if nid in G:
            data = G.nodes[nid]
            entities.append({
                "id": nid,
                "name": data.get("name", ""),
                "entity_type": data.get("entity_type", ""),
                "properties": data.get("properties", {}),
                "source": data.get("source", ""),
            })

    relationships = []
    for src, tgt, data in G.edges(data=True):
        if src in node_ids and tgt in node_ids:
            src_data = G.nodes[src]
            tgt_data = G.nodes[tgt]
            relationships.append({
                "id": data.get("rel_id", 0),
                "source_id": src,
                "target_id": tgt,
                "source_name": src_data.get("name", ""),
                "target_name": tgt_data.get("name", ""),
                # The endpoint TYPES travel with the edge. Without them a consumer
                # cannot tell a column-level foreign key (`mileage_accrual.ffp_member_id
                # → ffp_member.ffp_member_id`) from an entity-level one, and the declared
                # join-pair derivation silently produced NO pairs on every graph whose
                # keys are declared as column edges — the industry models, agent
                # discovery (2026-09-19, prod session 0aa3a650).
                "source_type": src_data.get("entity_type", ""),
                "target_type": tgt_data.get("entity_type", ""),
                "relationship_type": data.get("relationship_type", ""),
                "cardinality": data.get("cardinality"),
                "metadata": data.get("metadata", {}),
            })

    return {"entities": entities, "relationships": relationships}


# ── Self-registration ─────────────────────────────────────────────────────────
register_backend("sqlite", SQLiteKGBackend)
