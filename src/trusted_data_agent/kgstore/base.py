"""
KG Store backend abstract base class.

All backend implementations must inherit from KGStoreBackend and implement
every method marked abstract.  Optional capabilities (graph algorithms, etc.)
can be left un-implemented — callers must check capabilities() before use.

Design notes:
- All public methods are *synchronous* so GraphStore's existing callers (both
  sync helper functions and async Quart handlers) need no special adaptation.
- Backend implementations that talk to remote stores (e.g. Neo4j) should use
  the respective sync driver (neo4j.GraphDatabase.driver) or run async calls
  in a thread pool at the backend level — callers must not change.
- The kg_id parameter scopes every operation to a single knowledge graph
  within a backend instance (a backend instance may be shared across many kg_ids).
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from .capabilities import KGStoreCapability, REQUIRED_CAPABILITIES

logger = logging.getLogger("quart.app")


class KGStoreBackend(ABC):
    """Abstract base class for all KG storage backends."""

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """
        One-time initialisation (create tables, open connections, etc.).
        Called once by the factory after construction.  Default: no-op.
        """

    def shutdown(self) -> None:
        """
        Release resources (close connections, flush buffers, etc.).
        Default: no-op.
        """

    # ── Capability declaration ────────────────────────────────────────────

    @abstractmethod
    def capabilities(self) -> Set[KGStoreCapability]:
        """Return the set of capabilities this backend supports."""

    def supports(self, capability: KGStoreCapability) -> bool:
        return capability in self.capabilities()

    def assert_required_capabilities(self) -> None:
        missing = REQUIRED_CAPABILITIES - self.capabilities()
        if missing:
            raise NotImplementedError(
                f"{self.__class__.__name__} is missing required capabilities: "
                + ", ".join(c.name for c in missing)
            )

    # ── Connectivity check ────────────────────────────────────────────────

    def test_connection(self) -> Dict[str, Any]:
        """
        Test the backend connection.
        Returns {'success': bool, 'message': str}.
        Default: always returns success (override for remote stores).
        """
        return {"success": True, "message": "OK"}

    # ── Entity CRUD ───────────────────────────────────────────────────────

    @abstractmethod
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
        """Insert or upsert an entity.  Returns entity id."""

    @abstractmethod
    def get_entity(self, kg_id: str, entity_id: int) -> Optional[Dict[str, Any]]:
        """Return entity dict or None."""

    @abstractmethod
    def get_entity_by_name(
        self,
        kg_id: str,
        name: str,
        entity_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Case-insensitive name lookup."""

    @abstractmethod
    def search_entities(
        self,
        kg_id: str,
        query_text: str,
        limit: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Substring / FTS search over entity names and properties."""

    @abstractmethod
    def list_entities(
        self,
        kg_id: str,
        entity_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """List entities, optionally filtered by type."""

    @abstractmethod
    def update_entity(
        self,
        kg_id: str,
        entity_id: int,
        properties: Dict[str, Any],
    ) -> bool:
        """Merge new properties into entity.  Returns True if found."""

    @abstractmethod
    def delete_entity(self, kg_id: str, entity_id: int) -> bool:
        """Delete entity and cascade its relationships.  Returns True if deleted."""

    # ── Relationship CRUD ─────────────────────────────────────────────────

    @abstractmethod
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
        """Insert or upsert a relationship.  Returns relationship id."""

    @abstractmethod
    def get_relationships(
        self,
        kg_id: str,
        entity_id: int,
        direction: str = "both",
    ) -> List[Dict[str, Any]]:
        """Get edges from/to entity.  direction: 'outgoing' | 'incoming' | 'both'."""

    @abstractmethod
    def list_relationships(
        self,
        kg_id: str,
        entity_id: Optional[int] = None,
        relationship_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """List relationships with optional entity/type filters."""

    @abstractmethod
    def delete_relationship(self, kg_id: str, relationship_id: int) -> bool:
        """Delete a relationship.  Returns True if deleted."""

    # ── Bulk operations ───────────────────────────────────────────────────

    @abstractmethod
    def import_bulk(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        entities: List[Dict],
        relationships: List[Dict],
    ) -> Dict[str, int]:
        """Bulk import.  Returns {entities_added, relationships_added}."""

    @abstractmethod
    def clear_graph(self, kg_id: str) -> Dict[str, int]:
        """Delete all entities and relationships.  Returns {entities_deleted, relationships_deleted}."""

    def get_relationship(self, kg_id: str, relationship_id: int) -> Optional[Dict[str, Any]]:
        """One relationship by id (generic scan; a backend may override natively)."""
        for r in self.list_relationships(kg_id, limit=999999) or []:
            if r.get("id") == relationship_id:
                return r
        return None

    def replace_model_layer(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        entities: List[Dict],
        relationships: List[Dict],
        keep_layers,
    ) -> Dict[str, Any]:
        """Replace the WALKED model with a fresh walk, keeping every node a walk never
        produces — ``kgstore.types.is_kept_on_replace``: the layers in ``keep_layers``
        (PROP-008 A1 / KG_OBSERVATION_LAYER §6.4), an extension's realisation, and the
        stacked contracts.

        The one operation every "resync" caller uses instead of clear_graph()+import_bulk(),
        so a routine update can never silently delete an authored or observed node. An edge
        touching a kept node is remembered BY NAME and re-linked after the walk is
        re-imported (the re-walked node gets a new id); an edge whose other end the new walk
        no longer has is dropped and NAMED in ``kept_edges_dropped`` — that is a finding for
        the conformance check, never a silent loss.

        Generic implementation over the backend's own primitives; SQLite overrides it with
        one transaction. Returns the import result plus ``entities_kept``,
        ``entities_deleted``, ``relationships_deleted``, ``kept_edges_relinked``,
        ``kept_edges_dropped``.
        """
        from trusted_data_agent.kgstore.types import is_kept_on_replace
        keep = set(keep_layers or ())
        all_ents = self.list_entities(kg_id, limit=999999) or []
        # THE keep rule (kgstore.types.is_kept_on_replace): the kept layers, an extension's
        # realisation, and the stacked contracts — everything a walk never produces
        kept_ids = {e["id"] for e in all_ents if is_kept_on_replace(e, keep)}
        all_rels = self.list_relationships(kg_id, limit=999999) or []
        kept_edges = [r for r in all_rels
                      if r.get("source_entity_id") in kept_ids or r.get("target_entity_id") in kept_ids]
        deleted_e = 0
        for e in all_ents:
            if e["id"] not in kept_ids and self.delete_entity(kg_id, e["id"]):
                deleted_e += 1
        deleted_r = 0
        for r in all_rels:
            if r.get("source_entity_id") not in kept_ids or r.get("target_entity_id") not in kept_ids:
                try:
                    if self.delete_relationship(kg_id, r["id"]):
                        deleted_r += 1
                except Exception:  # noqa: BLE001 — already cascaded away
                    pass
        res = dict(self.import_bulk(kg_id, profile_id, user_uuid, entities, relationships) or {})
        relinked, dropped, dropped_detail = self._relink_kept_edges(kg_id, profile_id, user_uuid, kept_edges, kept_ids)
        res["kept_edges_dropped_detail"] = dropped_detail
        res.update({"entities_kept": len(kept_ids), "entities_deleted": deleted_e,
                    "relationships_deleted": deleted_r, "kept_edges_relinked": relinked,
                    "kept_edges_dropped": dropped})
        return res

    def _relink_kept_edges(self, kg_id, profile_id, user_uuid, kept_edges, kept_ids):
        """Re-create the remembered edges against the CURRENT node ids. A kept endpoint keeps
        its id; a walked endpoint is resolved by (name, type) in the fresh walk.

        Returns ``(relinked, dropped_labels, dropped_detail)`` — the detail carries each
        dropped edge's endpoints by name/type so the caller can STAMP the finding on the
        kept source node (an extension edge whose base end vanished is a conformance
        finding the resync made; PROP-008 A5 names it, never loses it)."""
        relinked = 0
        dropped: List[str] = []
        dropped_detail: List[Dict[str, Any]] = []
        existing = {(r.get("source_entity_id"), r.get("target_entity_id"), r.get("relationship_type"))
                    for r in (self.list_relationships(kg_id, limit=999999) or [])}
        for r in kept_edges:
            def _resolve(eid, name, etype):
                if eid in kept_ids:
                    return eid
                ent = self.get_entity_by_name(kg_id, name, etype) if name else None
                return ent.get("id") if ent else None
            src = _resolve(r.get("source_entity_id"), r.get("source_name"), r.get("source_type"))
            tgt = _resolve(r.get("target_entity_id"), r.get("target_name"), r.get("target_type"))
            label = f"{r.get('source_name')} --[{r.get('relationship_type')}]--> {r.get('target_name')}"
            detail = {"source_name": r.get("source_name"), "source_type": r.get("source_type"),
                      "relationship_type": r.get("relationship_type"),
                      "target_name": r.get("target_name"), "target_type": r.get("target_type"),
                      "label": label}
            if src is None or tgt is None:
                dropped.append(label)
                dropped_detail.append(detail)
                continue
            key = (src, tgt, r.get("relationship_type"))
            if key in existing:
                relinked += 1
                continue
            try:
                self.add_relationship(kg_id=kg_id, profile_id=profile_id, user_uuid=user_uuid,
                                      source_entity_id=int(src), target_entity_id=int(tgt),
                                      relationship_type=r.get("relationship_type"),
                                      cardinality=r.get("cardinality"), metadata=r.get("metadata"),
                                      source=r.get("source", "manual"))
                existing.add(key)
                relinked += 1
            except Exception:  # noqa: BLE001
                dropped.append(label)
                dropped_detail.append(detail)
        return relinked, dropped, dropped_detail

    # ── Statistics ────────────────────────────────────────────────────────

    @abstractmethod
    def get_stats(self, kg_id: str) -> Dict[str, Any]:
        """Return graph statistics dict."""

    # ── Graph algorithms (optional, guarded by GRAPH_ALGORITHMS capability) ──

    def extract_subgraph(
        self,
        kg_id: str,
        entity_ids: List[int],
        depth: int = 2,
        max_nodes: int = 50,
    ) -> Dict[str, List]:
        raise NotImplementedError("extract_subgraph requires GRAPH_ALGORITHMS capability")

    def extract_subgraph_adaptive(
        self,
        kg_id: str,
        seed_entity_ids: List[int],
        query_entity_ids: Optional[List[int]] = None,
        max_nodes: int = 500,
        query_terms: Optional[List[str]] = None,
        per_table_cap: Optional[int] = None,
    ) -> Dict[str, List]:
        raise NotImplementedError("extract_subgraph_adaptive requires GRAPH_ALGORITHMS capability")

    def get_full_graph(self, kg_id: str, max_nodes: int = 100) -> Dict[str, List]:
        raise NotImplementedError("get_full_graph requires GRAPH_ALGORITHMS capability")

    def get_typed_subgraph(
        self, kg_id: str, types: Optional[List[str]] = None, max_nodes: int = 500,
        focus: Optional[List[int]] = None, project_dependencies: bool = False,
        divisions: Optional[List[str]] = None, domains: Optional[List[str]] = None,
    ) -> Dict[str, List]:
        """Seed → expand → fill subgraph for the detail/focus lens. Default fallback
        filters a full-graph slice by type; backends should override for connectivity
        expansion + importance ranking + focus seeding.

        project_dependencies: when True AND a type filter hides intermediate levels,
        lift hidden-level relationships to derived edges between visible nodes (the
        "show dependencies" overlay). Default OFF — it's a compute- + render-heavy
        details-on-demand layer the frontend requests only on zoom-in / expand."""
        full = self.get_full_graph(kg_id, max_nodes=max_nodes * 4)

        def _facets(e):
            props = e.get("properties") or {}
            div = props.get("division"); dom = props.get("domain")
            if e.get("entity_type") == "domain":
                lvl = props.get("level"); nm = e.get("name")
                if lvl == "division" and not div: div = nm
                elif lvl == "domain" and not dom: dom = nm
            return (str(div).strip().lower() if div else None,
                    str(dom).strip().lower() if dom else None)

        # Facet catalog over the full slice.
        facet_div: Dict[str, int] = {}
        facet_dom: Dict[str, int] = {}
        for e in full["entities"]:
            d, m = _facets(e)
            if d: facet_div[d] = facet_div.get(d, 0) + 1
            if m: facet_dom[m] = facet_dom.get(m, 0) + 1
        facets = {}
        if facet_div: facets["division"] = facet_div
        if facet_dom: facets["domain"] = facet_dom

        tset = set(types) if types else None
        dset = {str(d).strip().lower() for d in divisions if str(d).strip()} if divisions else None
        mset = {str(d).strip().lower() for d in domains if str(d).strip()} if domains else None

        def keep_ent(e):
            if tset is not None and e.get("entity_type") not in tset:
                return False
            if dset is not None or mset is not None:
                d, m = _facets(e)
                if dset is not None and (d is None or d not in dset): return False
                if mset is not None and (m is None or m not in mset): return False
            return True

        ents = [e for e in full["entities"] if keep_ent(e)][:max_nodes]
        keep = {e["id"] for e in ents}
        rels = [r for r in full["relationships"]
                if r.get("source_id") in keep and r.get("target_id") in keep]
        return {"entities": ents, "relationships": rels, "facets": facets}

    def get_schema_overview(self, kg_id: str) -> Dict[str, List]:
        """Type-level meta-graph (overview lens). Default fallback derives it from a
        full-graph read; backends should override for an aggregate query."""
        full = self.get_full_graph(kg_id, max_nodes=10 ** 9)
        id_type = {e["id"]: e.get("entity_type", "unknown") for e in full["entities"]}
        tc: Dict[str, int] = {}
        for e in full["entities"]:
            t = e.get("entity_type", "unknown")
            tc[t] = tc.get(t, 0) + 1
        ea: Dict[tuple, int] = {}
        for r in full["relationships"]:
            key = (id_type.get(r.get("source_id"), "unknown"),
                   r.get("relationship_type", "unknown"),
                   id_type.get(r.get("target_id"), "unknown"))
            ea[key] = ea.get(key, 0) + 1
        return {
            "nodes": [{"type": k, "count": v} for k, v in sorted(tc.items())],
            "edges": [{"source_type": s, "relationship_type": rt, "target_type": t, "count": c}
                      for (s, rt, t), c in sorted(ea.items())],
        }

    def find_shortest_path(
        self,
        kg_id: str,
        source_id: int,
        target_id: int,
    ) -> Optional[List[int]]:
        raise NotImplementedError("find_shortest_path requires GRAPH_ALGORITHMS capability")

    def get_connected_entities(self, kg_id: str, entity_id: int) -> Dict[str, List[int]]:
        raise NotImplementedError("get_connected_entities requires GRAPH_ALGORITHMS capability")

    def all_table_columns(self, kg_id: str) -> Dict[str, List[str]]:
        """{logical table LEAF: [every column leaf]} for the whole graph — the complete lists
        the preflight COLUMN guard checks a SQL reference against. Default {}: a backend that
        cannot enumerate cheaply yields no lists, and the guard then has no verdict (never a
        false refusal). SQLite serves it from the cached NetworkX graph."""
        return {}

    def get_node_neighborhood(self, kg_id: str, entity_id: int,
                              max_neighbors: int = 500) -> Dict[str, Any]:
        """COMPLETE 1-hop neighbourhood of a node (all neighbours + connecting edges),
        uncapped by any render budget. Default fallback derives it from a full-graph read;
        backends should override with a direct 1-hop query."""
        full = self.get_full_graph(kg_id, max_nodes=10 ** 9)
        nid = entity_id
        neigh = set()
        for r in full["relationships"]:
            if r.get("source_id") == nid:
                neigh.add(r.get("target_id"))
            elif r.get("target_id") == nid:
                neigh.add(r.get("source_id"))
        total = len(neigh)
        truncated = total > max_neighbors
        if truncated:
            neigh = set(list(neigh)[:max_neighbors])
        keep = {nid} | neigh
        ents = [e for e in full["entities"] if e.get("id") in keep]
        rels = [r for r in full["relationships"]
                if r.get("source_id") in keep and r.get("target_id") in keep]
        return {"entities": ents, "relationships": rels, "truncated": truncated, "total_degree": total}

    def get_entity_importance(self, kg_id: str) -> Dict[int, float]:
        raise NotImplementedError("get_entity_importance requires GRAPH_ALGORITHMS capability")

    def detect_cycles(self, kg_id: str) -> List[List[int]]:
        raise NotImplementedError("detect_cycles requires GRAPH_ALGORITHMS capability")

    # ── Cache invalidation ────────────────────────────────────────────────

    def invalidate_cache(self, kg_id: Optional[str] = None) -> None:
        """
        Invalidate any in-memory caches (e.g. NetworkX graph cache).
        kg_id=None invalidates all.  Default: no-op.
        """
