# T4 — Open Semantic-Interchange Standards

**What this proves:** *open, interoperable semantic standards* — a knowledge graph exported, consumed by third-party tools and reimported without loss.
**Design + architecture:** `TIER_4.1_OPEN_STANDARDS.md`.
**Principles:** a capability is claimed only with its proof · *limits stated honestly* · *deterministic where possible* (this capability is 100% deterministic, zero LLM).

---

## The capability

Uderia's Knowledge Graph is a navigable ontology and its metrics are a real semantic layer — but both lived in a **proprietary JSON shape**. Tier 4.1 adds a **deterministic, bidirectional adapter** that re-serializes them into recognised **W3C open standards** *and parses them losslessly back*:

| Uderia concept | Open standard |
|---|---|
| Concept / taxonomy layer (`business_concept`, `taxonomy`, `domain`) | **SKOS** (`skos:Concept`, `skos:narrower`/`broader`) |
| Logical ontology (`entity`, `attribute`, typed relationships) | **RDFS / OWL** (`owl:Class`, `owl:DatatypeProperty` + `rdfs:domain`) |
| Metrics (dimensions + measures) | **RDF Data Cube — QB** (`qb:DataStructureDefinition`, `qb:DimensionProperty`/`qb:MeasureProperty`) |
| Lineage (`implements`, `derives_from`, deployed views) | **PROV-O** (`prov:wasDerivedFrom`) |
| Catalog (KG + each metric as a discoverable resource) | **DCAT** (`dcat:Dataset` / `dcat:DataService`) |

Serialized as **Turtle / JSON-LD / RDF-XML / N-Triples** via `rdflib`. A pure-standards consumer (Protégé, any SPARQL engine, a DCAT crawler) reads the standard predicates; a parallel **`uderia:` annotation layer** carries the exact Uderia facts (`entityType`, the full properties dict as JSON, relationship type/cardinality/metadata) so a re-import is lossless. That dual layer is what makes export genuinely **re-importable**, not just emittable.

REST: `GET /v1/knowledge-graph/export?format=skos|owl|rdf&serialization=…` · `GET /v1/knowledge-graph/metrics/<name>/export?format=qb|prov|dcat|all` · `POST /v1/knowledge-graph/import-rdf`. The default `format=json` path is unchanged.

---

## The proof

Three independent, reproducible proofs. The headline is a **standalone offline verifier** anyone runs with no Uderia install.

### 1. Lossless full round-trip — proven live on a real KG

Against this instance (a real 86-entity / 128-relationship KG spanning logical + physical + metric layers):

```
export (Turtle)  →  POST /import-rdf (create)  →  fresh KG  →  export (JSON)  →  compare
```
```
import result: entities_added 86 · relationships_added 128 · warnings []
PASS  entity set identical (86 vs 86)
PASS  relationship set identical (128 vs 128)
PASS  all entity properties identical (mismatches: 0)
```

Export is genuinely *re-importable* — a KG can leave Uderia as RDF, be edited by a third-party tool, and return with **zero semantic loss**.

### 2. Standalone offline verifier — `pip install rdflib`, no Uderia

The committed samples in [`samples/`](samples/) are verifiable by anyone:

```bash
cd samples
pip install rdflib
python verify_open_standards.py
```
```
[1] lossless round-trip — ontology / metric : turtle · json-ld · xml · nt all isomorphic
[2] re-importable (uderia: layer)            : 7 entities + 6 relationships reconstructable
[3] W3C RDF Data Cube structural conformance : typed DSD, ≥1 dim + ≥1 measure, every
                                               component links exactly one dim XOR measure
[4] third-party consumable (SPARQL, standard predicates only):
       → dimensions: ['order_date']
       → measures:   ['avg_order_value', 'total_revenue']
[5] tamper-evident                           : dropping one triple breaks isomorphism
  23 passed, 0 failed
```

Proof **4** is the interoperability claim made concrete: a SPARQL query using **only** `owl:` / `skos:` / `qb:` predicates — touching nothing `uderia:` — recovers the ontology's classes, the concept hierarchy, and the metric's dimensions and measures. A tool that has never heard of Uderia uses its semantics.

| File | What it is |
|---|---|
| `samples/ontology_sample.ttl` | A sample KG (logical + physical + a metric) exported to SKOS/OWL/QB/PROV/DCAT |
| `samples/metric_qb_sample.ttl` | One metric as a W3C RDF Data Cube DataStructureDefinition + PROV lineage |
| `samples/verify_open_standards.py` | Standalone verifier (round-trip · re-importability · QB conformance · SPARQL consumption · tamper) |

### 3. Module test — 79 CI-pure assertions

[`test/test_semantic_interchange.py`](../../test/test_semantic_interchange.py) asserts the full type mapping, the `uderia:` round-trip layer, lossless reconstruction across all four serializations, graph isomorphism, metric ↔ QB, the best-effort foreign-graph fallback (with honest warnings), vocab-layer filtering, and determinism — **79/79 pass**, zero external services.

---

## Honest limits (charter #19)

- **Round-trip import is in scope; arbitrary *foreign* ontology import is not.** A Uderia export (or a third-party edit that preserves the `uderia:` annotations) re-imports losslessly. A foreign SKOS/OWL graph with *no* `uderia:` layer falls back to a best-effort standard-predicate mapping (`owl:Class`→entity, `skos:narrower`→contains, …) and records what it could not place in `warnings` — never silent. Mapping arbitrary external vocabularies precisely would require judgement (an LLM), which would break the zero-LLM property.
- **QB conformance is structural, not observation-level.** The export is a *structure* (a DataStructureDefinition), not instance data, so the W3C QB integrity constraints IC-1..21 (which concern `qb:Observation`/`qb:DataSet`) are N/A and are deliberately **not** claimed. What is proven is structural well-formedness (typed DSD, typed components, dimension-XOR-measure).
- **SQL expressions are carried as annotations, not portable logic.** A measure's `expr` / the compiled SQL is dialect-specific; it rides as a `uderia:` annotation for fidelity, while the portable *structure* (dimensions/measures) is standard QB.

---

## Code

- Adapter (pure, deterministic): `src/trusted_data_agent/semantic_interchange/{vocab,kg_to_rdf,rdf_to_kg,metric_to_qb,catalog,serializers,__init__}.py`
- REST seam: `src/trusted_data_agent/api/rest_routes.py` (`kg_export` extended · `kg_metric_export` · `kg_import_rdf`)
- Tests: `test/test_semantic_interchange.py`
- Design: `docs/roadmap/TIER_4.1_OPEN_STANDARDS.md`
