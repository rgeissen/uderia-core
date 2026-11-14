"""Ontology overlay — what the bound knowledge graph already KNOWS about an object.

The navigator is useful with no ontology; with one it gets richer: the logical entity
behind a physical table (its business description), attribute-level meaning
(PII classification, decoded values, sampled shapes, blank ratios, date ranges from
source validation), declared keys, and the graph's own foreign-key edges. Read-only,
fail-open: any failure returns ``{"present": False}`` and the navigator shows the
dictionary alone."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from .types import ColumnInfo

logger = logging.getLogger("quart.app")

_ATTR_KEYS = ("description", "is_pii", "classification", "semantic_type", "allowed_values",
              "sampled_shape", "date_range", "blank_ratio", "deployed_type", "declared_type",
              "date_unparseable_ratio", "sample_value", "business_meaning", "definition")


def _props(e: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return (e or {}).get("properties") or {}


async def ontology_overlay(store: Any, database: str, name: str,
                           columns: Sequence[ColumnInfo] = ()) -> Dict[str, Any]:
    """Overlay for ``database.name`` from ``store`` (a GraphStore). Never raises."""
    out: Dict[str, Any] = {"present": False}
    if store is None:
        return out
    try:
        qualified = f"{database}.{name}"
        table = store.get_entity_by_name(qualified, "table")
        if table is None:
            low = qualified.lower()
            for e in await store.list_entities(entity_type="table", limit=4000):
                if str(e.get("name") or "").lower() == low:
                    table = e
                    break
        entity = None
        if table is not None:
            for r in await store.get_relationships(table["id"], "outgoing"):
                if r.get("relationship_type") == "implements":
                    entity = store.get_entity(r["target_id"])
                    break
        if entity is None:
            # discovery graphs name the logical entity by the bare table name
            entity = store.get_entity_by_name(name, "entity")
        if entity is None and table is None:
            return out
        out["present"] = True
        out["kg_id"] = getattr(store, "kg_id", None)
        ep = _props(entity)
        out["entity"] = {
            "name": (entity or {}).get("name"),
            "description": ep.get("description") or _props(table).get("description") or "",
            "row_count": ep.get("row_count"), "key_unique": ep.get("key_unique"),
            "key_duplicate_groups": ep.get("key_duplicate_groups"),
            "module": ep.get("module"), "product": ep.get("product"),
        }
        # attributes by leaf name
        col_over: Dict[str, Dict[str, Any]] = {}
        declared_keys: List[str] = []
        related: List[Dict[str, Any]] = []
        if entity is not None:
            for r in await store.get_relationships(entity["id"], "both"):
                rt = r.get("relationship_type")
                if rt == "contains" and r.get("source_id") == entity["id"]:
                    attr = store.get_entity(r["target_id"])
                    ap = _props(attr)
                    leaf = str((attr or {}).get("name") or "").rpartition(".")[2]
                    if not leaf:
                        continue
                    o = {k: ap[k] for k in _ATTR_KEYS if ap.get(k) not in (None, "", [], {})}
                    if ap.get("is_primary_key"):
                        declared_keys.append(leaf)
                        o["is_primary_key"] = True
                    if o:
                        col_over[leaf.lower()] = o
                elif rt == "foreign_key":
                    other = r.get("target_name") if r.get("source_id") == entity["id"] else r.get("source_name")
                    related.append({"entity": other,
                                    "direction": "out" if r.get("source_id") == entity["id"] else "in"})
                elif rt in ("relates_to", "measures", "derives_from") and r.get("target_id") == entity["id"]:
                    src = store.get_entity(r["source_id"])
                    if src and src.get("entity_type") in ("business_concept", "metric", "query_recipe", "design_decision"):
                        related.append({"entity": src.get("name"), "kind": src.get("entity_type"),
                                        "description": str(_props(src).get("description") or _props(src).get("definition") or "")[:240]})
        out["columns"] = col_over
        out["declared_keys"] = declared_keys
        out["related"] = related[:24]
        for c in columns:
            o = col_over.get(c.name.lower())
            if o:
                c.ontology = o
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dbcatalog] ontology overlay unavailable for %s.%s: %s", database, name, exc)
        return {"present": False}
