# Knowledge Graph Constructor — AI-Native Data Product

Builds a Knowledge Graph from a **deployed** AI-Native Data Product (ANDP) by reading
the catalog the product publishes about itself. Deterministic, zero-LLM, zero-token,
and re-syncable.

## Why

A conformant product already describes itself in a machine-readable way. This
constructor consumes that description directly, so the product becomes a grounded,
governed agent surface with **no bespoke modelling**:

- physical `schema`/`table`/`column` nodes carry the real, queryable identifiers →
  the grounding gate's **trusted scope** resolves against them
- `is_pii` / `data_classification` ride onto both the logical and physical nodes →
  **guardrails are driven by the product's own declaration**, not a hand-written rule
- decoded value enumerations ride onto attributes → the planner knows that
  `V4019 = 0` means *Female* instead of guessing

## What it reads

| Module | Asset | Becomes |
|---|---|---|
| Semantic | `data_product_map` | the module → domain structure |
| Semantic | `entity_metadata` | logical `entity` (+ physical `table`, linked by `implements`) |
| Semantic | `column_metadata` | logical `attribute` (+ physical `column`) with meaning, PII class, value codes |
| Semantic | `table_relationship` | `foreign_key` edges, logical and physical |
| Semantic | `naming_standard` | `naming_standard` nodes governing the product |
| Memory | `Business_Glossary` | `business_concept`, linked to the entity/attribute it defines |
| Memory | `Query_Cookbook` | `query_recipe`, linked to the entities it queries |
| Memory | `Design_Decision` | `design_decision`, linked to what it shaped |
| Memory | `Module_Registry` | enrichment on the domain nodes (version, owners, status) |
| Observability | `data_lineage` | `derives_from` edges; external file sources become provenance on the target |
| Observability | `data_quality_metric` | `quality_metric`, linked by `measures` |
| *dictionary* | `DBC.ColumnsV` | the **authoritative** physical column types |

## The type rule

**Meaning comes from the ontology; types come from the data dictionary.**

A product's declared `data_type` drifts from what was deployed. On the reference
NIBRS product, 1,425 of 1,508 columns declare a type the deployed schema contradicts
(the product deliberately lands its source losslessly as characters). Physical column
nodes therefore carry the dictionary type; the logical attribute keeps the declared
type as the semantic fact it is. Disagreements are **counted and reported**, never
silently reconciled — a divergence is a finding about the product.

## Discovery

Follows the standard's own order, and reports which step it used:

1. `governance.data_product_registry` (preferred)
2. `{Product}_Semantic` convention
3. dictionary scan for a `data_product_map`
