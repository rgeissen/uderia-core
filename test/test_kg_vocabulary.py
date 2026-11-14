"""
CI gate: the KG type vocabulary is defined ONCE.

The entity/relationship vocabulary used to be written out separately in
``graph_store.py``, ``sqlite_backend.py`` and ``conformance.py`` — three
hand-maintained copies of one contract, each carrying a comment claiming it was
"identical" to another.  That drifts the first time a type is added, and it fails
*silently*: an entity validates in one layer and is rejected — or, worse, never
expanded into the agent's context — in the next.

These assertions check **identity**, not equality, so re-introducing a literal set
anywhere fails here even if its contents happen to match today.

Run: python3 test/test_kg_vocabulary.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore import types as canon  # noqa: E402
from trusted_data_agent.kgstore import conformance, sqlite_backend  # noqa: E402
from components.builtin.knowledge_graph import graph_store  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


# ── 1. single source of truth (identity, not equality) ──────────────────────
def t_single_source():
    check("sqlite_backend re-exports the canonical ENTITY_TYPES",
          sqlite_backend.ENTITY_TYPES is canon.ENTITY_TYPES,
          "a literal set was re-introduced in sqlite_backend.py")
    check("sqlite_backend re-exports the canonical RELATIONSHIP_TYPES",
          sqlite_backend.RELATIONSHIP_TYPES is canon.RELATIONSHIP_TYPES)
    check("sqlite_backend re-exports the canonical SEMANTIC_TYPES",
          sqlite_backend._SEMANTIC_TYPES is canon.SEMANTIC_TYPES)
    check("sqlite_backend re-exports the canonical EXPANDABLE set",
          sqlite_backend._EXPANDABLE_STRUCTURAL_TYPES is canon.EXPANDABLE_STRUCTURAL_TYPES)

    check("graph_store re-exports the canonical ENTITY_TYPES",
          graph_store.ENTITY_TYPES is canon.ENTITY_TYPES,
          "a literal set was re-introduced in graph_store.py")
    check("graph_store re-exports the canonical RELATIONSHIP_TYPES",
          graph_store.RELATIONSHIP_TYPES is canon.RELATIONSHIP_TYPES)
    check("graph_store re-exports the canonical SEMANTIC_TYPES",
          graph_store._SEMANTIC_TYPES is canon.SEMANTIC_TYPES)

    check("conformance re-exports the canonical ENTITY_TYPES",
          conformance.ENTITY_TYPES is canon.ENTITY_TYPES,
          "a literal set was re-introduced in conformance.py")
    check("conformance re-exports the canonical LOGICAL_TYPES",
          conformance.LOGICAL_TYPES is canon.LOGICAL_TYPES)
    check("conformance re-exports the canonical PHYSICAL_TYPES",
          conformance.PHYSICAL_TYPES is canon.PHYSICAL_TYPES)


# ── 2. the vocabulary partitions cleanly ────────────────────────────────────
def t_partition():
    check("logical and physical are disjoint",
          not (canon.LOGICAL_TYPES & canon.PHYSICAL_TYPES),
          str(canon.LOGICAL_TYPES & canon.PHYSICAL_TYPES))
    check("logical ∪ physical == every entity type",
          canon.LOGICAL_TYPES | canon.PHYSICAL_TYPES == canon.ENTITY_TYPES)
    check("product-description types are logical",
          canon.PRODUCT_DESCRIPTION_TYPES <= canon.LOGICAL_TYPES)
    check("product-description types are not structural",
          not (canon.PRODUCT_DESCRIPTION_TYPES & canon.LOGICAL_STRUCTURAL_TYPES))


# ── 3. the agent can actually see the product-description layer ─────────────
def t_agent_visibility():
    """A type absent from SEMANTIC_TYPES renders in the inspector but never reaches
    the agent's context subgraph — the exact failure this layer exists to avoid."""
    missing = canon.PRODUCT_DESCRIPTION_TYPES - canon.SEMANTIC_TYPES
    check("every product-description type is context-expandable",
          not missing, f"invisible to the agent: {sorted(missing)}")
    check("semantic types are all valid entity types",
          canon.SEMANTIC_TYPES <= canon.ENTITY_TYPES,
          str(canon.SEMANTIC_TYPES - canon.ENTITY_TYPES))
    check("expandable structural types are all valid entity types",
          canon.EXPANDABLE_STRUCTURAL_TYPES <= canon.ENTITY_TYPES)


# ── 4. the frontend carries the same vocabulary ─────────────────────────────
def t_frontend_parity():
    """Backend/frontend parity is a design imperative: a type the backend can emit
    but the frontend cannot colour renders as an unstyled 'unknown' node and sorts
    outside both layer groups in the filter pills."""
    root = os.path.join(os.path.dirname(__file__), "..")
    js = open(os.path.join(root, "components/builtin/knowledge_graph/renderer.js"),
              encoding="utf-8").read()
    py = open(os.path.join(root, "components/builtin/knowledge_graph/handler.py"),
              encoding="utf-8").read()

    for etype in sorted(canon.ENTITY_TYPES):
        check(f"renderer.js colours '{etype}'", f"{etype}:" in js)
        check(f"handler.py colours '{etype}'", f'"{etype}":' in py)

    # the filter-pill order arrays must cover every type, or it sorts last as unknown
    import re
    logical = re.search(r"KG_LOGICAL_TYPE_ORDER\s*=\s*\[(.*?)\]", js, re.S)
    physical = re.search(r"KG_PHYSICAL_TYPE_ORDER\s*=\s*\[(.*?)\]", js, re.S)
    check("renderer declares a logical type order", logical is not None)
    check("renderer declares a physical type order", physical is not None)
    if logical and physical:
        ordered = set(re.findall(r"'([a-z_]+)'", logical.group(1) + physical.group(1)))
        missing = canon.ENTITY_TYPES - ordered
        check("every entity type has a filter-pill position",
              not missing, f"unordered (sort last as unknown): {sorted(missing)}")


# ── 5. the standards projection covers every type ───────────────────────────
def t_rdf_projection():
    """A type with no RDF class silently vanishes from the standards export.

    ``rdflib`` is a hard requirement (requirements.txt) and ``semantic_interchange``
    is not part of the removable Ontology Suite, so this check runs unconditionally.
    Skipping on ImportError would hide a genuinely broken install.
    """
    from trusted_data_agent.semantic_interchange.vocab import ENTITY_RDF_TYPE
    missing = canon.ENTITY_TYPES - set(ENTITY_RDF_TYPE)
    check("every entity type has an RDF class for the standards export",
          not missing, f"unmapped: {sorted(missing)}")


# ── 6. the inspector actually surfaces the new types ────────────────────────
def t_inspector_surfaces_types():
    """Colouring a type is not the same as showing it.

    Two things decide what a user actually sees: the default lens (types absent from
    it are hidden behind a pill click) and the tooltip (a node whose substance is not
    rendered is a bare name). Both are checked here because both were gaps.
    """
    import re
    root = os.path.join(os.path.dirname(__file__), "..")
    js = open(os.path.join(root, "components/builtin/knowledge_graph/renderer.js"),
              encoding="utf-8").read()
    api = open(os.path.join(root, "static/js/api.js"), encoding="utf-8").read()

    def _default_lens(src):
        m = re.search(r"DEFAULT_INSPECT_TYPES\s*=\s*\n?\s*'([^']+)'", src)
        return set(m.group(1).split(",")) if m else set()

    lens_js, lens_api = _default_lens(js), _default_lens(api)
    check("renderer declares a default lens", bool(lens_js))
    check("api declares a default lens", bool(lens_api))
    check("the two default-lens copies are identical", lens_js == lens_api,
          f"renderer-only={sorted(lens_js - lens_api)} api-only={sorted(lens_api - lens_js)}")

    missing = canon.PRODUCT_DESCRIPTION_TYPES - lens_js
    check("product-description types are visible in the default lens",
          not missing, f"hidden behind a pill click: {sorted(missing)}")
    # attributes/columns are deliberately NOT in the lens — thousands of them would
    # swamp the graph; they stay one pill away.
    check("attributes stay out of the default lens", "attribute" not in lens_js)
    check("columns stay out of the default lens", "column" not in lens_js)

    tip = re.search(r"KG_TIP_FIELDS\s*=\s*\{(.*?)\n\};", js, re.S)
    check("renderer declares per-type tooltip fields", tip is not None)
    if tip:
        covered = set(re.findall(r"^\s{4}(\w+):\s*\[", tip.group(1), re.M))
        gap = canon.PRODUCT_DESCRIPTION_TYPES - covered
        check("every product-description type surfaces its substance on hover",
              not gap, f"renders as a bare name: {sorted(gap)}")
    # the governance payload must reach the reader
    check("tooltip surfaces the PII declaration", "is_pii" in js)
    check("tooltip surfaces the decoded value codes", "allowed_values" in js)


for fn in [t_single_source, t_partition, t_agent_visibility,
           t_frontend_parity, t_rdf_projection, t_inspector_surfaces_types]:
    fn()

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
