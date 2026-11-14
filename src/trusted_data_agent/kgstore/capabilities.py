"""
KG Store capability enum.

Mirrors vectorstore/capabilities.py — each capability maps to an optional
feature of the KG backend.  REQUIRED_CAPABILITIES is the minimal set every
backend must support to be usable by the platform.
"""

from enum import Enum, auto
from typing import Set


class KGStoreCapability(Enum):
    # ── Required ─────────────────────────────────────────────────────
    ADD_ENTITY = auto()
    GET_ENTITY = auto()
    DELETE_ENTITY = auto()
    LIST_ENTITIES = auto()
    ADD_RELATIONSHIP = auto()
    DELETE_RELATIONSHIP = auto()
    LIST_RELATIONSHIPS = auto()
    IMPORT_BULK = auto()
    CLEAR_GRAPH = auto()
    GET_STATS = auto()

    # ── Optional — graph algorithms ───────────────────────────────────
    GRAPH_ALGORITHMS = auto()      # BFS subgraph, shortest path, centrality, cycles
    NATIVE_GRAPH_QUERIES = auto()  # Cypher / SPARQL native query language

    # ── Optional — search ─────────────────────────────────────────────
    FULL_TEXT_SEARCH = auto()      # LIKE / FTS-based entity search
    SEMANTIC_SEARCH = auto()       # Embedding-based entity search
    ADAPTIVE_SUBGRAPH = auto()     # relevance/FK-aware budgeted subgraph extraction (get_context_enrichment)


REQUIRED_CAPABILITIES: Set[KGStoreCapability] = {
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
}
