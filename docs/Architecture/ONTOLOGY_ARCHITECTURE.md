# Ontology Architecture

> **Scope:** this document is the canonical **Knowledge-Graph topology** every individual KG conforms to (logical/physical layers, `implements`, containment). For the platform-wide **navigator** that maps your *whole estate* (agents, knowledge, memory, sessions, components, configuration) into one agent-centric semantic map — the **"Ontology"** nav view — see [PLATFORM_ONTOLOGY_ARCHITECTURE.md](PLATFORM_ONTOLOGY_ARCHITECTURE.md).

**Status:** 🟡 Foundational / normative · **Date:** 2026-06-27 · **Last updated:** 2026-10-30 · **Owner:** platform architecture

> **Ontology note (2026-06-27):** the logical layer is named `entity`/`attribute` and the
> physical layer `table`/`column` (the deployed realizations). The earlier names — logical
> `table`/`column`, physical `physical_table`/`physical_column` — were a category error
> (a *table* is a physical construct by definition). The rename is data-migrated and
> idempotent; see `KG_MODEL_RENAME.md`.

> **This is the foundation document for Uderia's ontology — a core part of the
> framework.** It states the strategic thesis (Part I), the ontology's reference
> model (Part I), the *normative* knowledge-graph topology every participant must emit
> (Part II), the ontology's planes — production, grounding, assembly, trust, learning (Part
> III), its relation to neighbouring approaches (Part IV), and the extension contracts + roadmap (Parts
> V–VI).
>
> Every current and future KG **constructor** and context-producing **skill**
> (e.g. semantic source→target mapping) conforms to Part II and declares which
> reference-model stage(s) it serves (Part I §1.3). When this doc and the code
> disagree, the code wins — but divergence is a bug to reconcile, not a new
> convention.

**Unifies (does not replace):** [KG_STORE_ABSTRACTION_ARCHITECTURE.md](KG_STORE_ABSTRACTION_ARCHITECTURE.md) · `docs/Architecture/INDUSTRY_MODEL_KG_CONSTRUCTOR_PLAN.md` · `docs/Architecture/INDUSTRY_MODEL_PDM_DEPLOYMENT_PLAN.md` · [KG_INSPECTOR_ARCHITECTURE.md](KG_INSPECTOR_ARCHITECTURE.md) · [CONTEXT_WINDOW_ARCHITECTURE.md](CONTEXT_WINDOW_ARCHITECTURE.md) · [VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md](VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md) · [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md)

---

## Part I — Thesis, Reference Model, Principles

### 1.1 The thesis — *the model is a commodity; context decides*

Frontier models converge and commoditize. The durable differentiator in enterprise AI
is **context**: whether the agent is given the *correct, grounded, governed, and current*
knowledge to reason over. Every serious enterprise failure mode — hallucination, stale
answers, wrong-table joins, ungovernable access, "where did that number come from?" — is
a **context failure**, not a model failure.

Answering "context" with **RAG alone** means: embed documents, retrieve by vector
similarity, stuff chunks into the prompt. That is *flat, lossy, structureless,
unprovenanced, and non-deterministic*. It cannot tell a table from a paragraph, cannot
traverse a dependency, cannot say what a column *means*, cannot map a customer's schema to
an industry standard, cannot reach live data, and cannot prove its lineage.

**Uderia's ontology takes the other road: context as an engineered, structured,
governed artifact** — a typed, layered, provenanced **knowledge graph** as the semantic
backbone, RAG/vector/documents as the unstructured complement, a budget-aware **assembler**,
deterministic **constructors**, semantic **source→target mapping**, and a bridge to **live
deployed data**. The bet is that context engineering, more than the choice of model,
decides whether an enterprise agent can be trusted.

### 1.2 What it adds (eight properties RAG-only systems lack)

| Property | Uderia ontology | RAG-only |
|---|---|---|
| **Structured** | typed graph — traverse dependencies, don't guess | flat chunks |
| **Layered** | logical↔physical (`implements`), one meaning → many realizations | none |
| **Semantic** | a business-concept/metric layer bridges natural language ↔ schema | term-matching |
| **Provenanced + lineaged** | every node/edge carries source (who built it) AND data lineage (source field → business rule → PDM column → concept → answer) — end-to-end traceable | opaque |
| **Deterministic** | constructors are reproducible, zero-LLM where possible | stochastic |
| **Live** | deployed PDM maps the semantic model 1:1 to real queryable tables | documents only |
| **Governed *and enforced*** | per-tenant, versioned, diff-able, marketplace-shareable context — and the deployed scope is an **enforced trust boundary** (the grounding gate withholds answers that drift outside it, §3.10) | flat index |
| **Composable & learning** | one substrate serves planning, retrieval, coordination, recommendation, eval; improves with use | per-app glue |

### 1.3 The ontology reference model (the stages that make it active, not a static graph)

Every capability — current and future — maps to one of eight stages. A new participant
**declares the stage(s) it serves**; this is how the ontology stays coherent as it grows.

```
 ACQUIRE → MODEL → MAP → MATERIALIZE → GROUND → ASSEMBLE → SERVE → LEARN
   │         │      │        │            │          │         │       │
 ingest    canon.  align   deploy a     resolve   budget &   deliver  capture
 a source  KG via  source→ conformant   intent →  fuse KG+   traceable success +
 (DB,      construc target  physical     relevant  vector+   context  eval signals →
 model.json -tors   (skills) model+data  grounded  docs+live  object   improve
 docs,            )        (PDM)         structures           per turn  grounding
 a schema)
```

| Stage | Responsibility | Today |
|---|---|---|
| **Acquire** | Pull raw knowledge from a source | live DB (MCP), `model.json` (GitHub), documents, a customer schema |
| **Model** | Normalize into the **canonical KG topology** (Part II) | agent-discovery + industry-model constructors |
| **Map** | Semantically align a source model to a target model/standard | semantic-mapping skill *(planned, §3.6)* |
| **Materialize** | Realize the model as live, queryable infrastructure | PDM deployment (Teradata) — bridges semantics to data |
| **Ground** | Resolve a query/intent to the relevant grounded structures | KG traversal + semantic layer + faceting |
| **Assemble** | Budget-aware fusion of KG + vector + docs + live into one context | context-window orchestrator |
| **Serve** | Deliver a structured, **traceable** context object to the consumer | engines, recommender, eval |
| **Learn** | Capture successful context + eval grades → improve next grounding | RAG champion cases + Agent-Ops eval |

### 1.4 Principles (normative)

1. **One topology.** Every producer emits the same entity/relationship vocabulary, layer
   model, naming, and property contract (Part II). No producer invents types or naming.
2. **Context is an artifact, not a string.** The ontology produces a structured, layered,
   **budgeted, and traceable** context object — every element resolvable to its source.
3. **Determinism where possible; LLM only for judgement.** Constructors are deterministic;
   mapping skills are LLM-assisted but conformance-tested and *diffable* like a constructor.
4. **Provenance is mandatory.** No anonymous knowledge — `source`/`source_detail` on every
   node/edge; re-syncable producers stamp a `source_descriptor`.
5. **Layered, not flattened.** Logical (meaning) and physical (realization) are distinct,
   linked by `implements` — one LDM, many PDMs.
6. **Capability negotiation, not assumption.** KG/vector/deployment backends advertise
   capabilities; the topology and its machinery are backend-agnostic.
7. **Faceted + navigable.** Grouping dimensions (division, domain, layer, infrastructure)
   are queryable properties AND first-class filter facets.
8. **Grade context, don't dump it.** Curated, budgeted, relevance-ranked context beats
   max-context; the eval plane measures context quality.

---

## Part II — Canonical Knowledge Graph Topology (the contract)

Normative source: `src/trusted_data_agent/kgstore/types.py` (`ENTITY_TYPES`,
`RELATIONSHIP_TYPES`, `SEMANTIC_TYPES`, `LAYERS` — re-exported by
`components/builtin/knowledge_graph/graph_store.py`, see §2.4d) + the `kgstore/` abstraction. Producers write
through `GraphStore.import_bulk(entities, relationships)`.

### 2.1 Shapes

```
entity       = {name, entity_type, properties: {...}, source, source_detail}
relationship = {source_name, source_type, target_name, target_type,
                relationship_type, cardinality?, metadata?, source}
```

`import_bulk` resolves endpoints by **(name, entity_type)** and upserts on
**UNIQUE(kg_id, name, entity_type)** — so **naming is the identity contract** (§2.4).

### 2.2 Layer model

Every entity carries `properties.layer`. The **walked** model — what a constructor
re-derives from its source on a resync — is `logical` + `physical`; two further layers are
**not** walked and survive every resync (`kgstore/types.py`: `LAYERS`,
`KEPT_LAYERS_ON_REPLACE`; the one resync operation is `replace_model_layer`, see
[KG_STORE_ABSTRACTION_ARCHITECTURE.md](KG_STORE_ABSTRACTION_ARCHITECTURE.md)):

- `authored` — nodes a person or an agent added on top of the model (OBDA-MODEL, PROP-008),
  one layer per extender (`properties.extension_id`; a pre-vocabulary node reads as the
  `local` extension), additive only, walking `drafted → validated → deployed → published`;
  only a `published` node is signable content, reaches a released-mode agent or travels in
  a pack ([ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md));
- `observed` — assertions ABOUT the model (reserved so the replace operation keeps it).

`clear_graph` stays a delete and removes every layer.

| Layer | Meaning | Extra properties |
|---|---|---|
| `logical` (LDM) | Infrastructure-neutral model — the *meaning* | — |
| `physical` (PDM) | Infrastructure-specific realization (deployed/declared system) | `infrastructure` (e.g. `teradata`, `databricks_unity_catalog`), `deployment_catalog?`, `cataloging_style?` |

**One LDM → many PDMs.** Physical nodes `implements` their logical counterparts; multiple
physical layers (Teradata, Databricks, …) implement the *same* logical nodes,
distinguished by `infrastructure`. Logical scaffolding always exists (implements targets must).

### 2.3 Entity vocabulary (closed enum)

**Logical**

| `entity_type` | Meaning | Key properties |
|---|---|---|
| `database` | Logical model root (subject area / product) | `label`, `industry?`, `scope?`, `description` |
| `domain` | Business subject area; also the division grouping (via `level`) | `level ∈ {division, domain}`, `division`, `description`, `database_name?` |
| `entity` | A logical entity / data product | `domain`, `division`, `subdomain?`, `primary_key?`, `description` |
| `attribute` | A logical attribute (field) | `domain`, `table` (owning-table name), `semantic_type?`, `tags?`, `description` |
| `metric` | A measure / KPI concept | `owner_domain`, `owner_product`, counts now; full `dimensions[]`/`measures[]` (roadmap §6.1) |
| `business_concept` | A glossary term / semantic concept (the NL↔schema bridge) | `definition?`, `references?` |
| `taxonomy` | A classification scheme | — |
| `foreign_key` | (expressed as a relationship in practice; see §2.5) | — |

**Logical — the product-description layer.** What a governed data product publishes
*about itself*. Introduced by the AI-Native Data Product constructor (the richest
conformant source of it) but **source-neutral**: any KG may carry these. Each is
attached to what it describes — an unattached node is a footnote the agent never
meets.

| `entity_type` | Meaning | Key properties | Attaches via |
|---|---|---|---|
| `query_recipe` | A proven, executable query answering a business question | `sql`, `business_question?`, `complexity?` | `depends_on` → the entities it queries |
| `design_decision` | A recorded decision + rationale that shaped the model | `rationale`, `context?`, `consequences?`, `status?` | `relates_to` → the entity/domain it shaped |
| `quality_metric` | A measured data-quality signal | `metric_value`, `quality_threshold?`, `is_threshold_met?` | `measures` → the entity/table it reads |
| `naming_standard` | A convention a consumer must follow to query correctly | `standard_type`, `standard_value`, `meaning`, `usage_guidance?` | `relates_to` → the product root |
| `product_bridge` | A DECLARED link between two data products — a *claimed* mapping, deliberately not a `business_concept` (a definition) | `linkage_mechanism`, whether its targets resolved | to the two products' entities it claims to link |

> These types are in `SEMANTIC_TYPES`, so they are pulled into the **agent's** context
> subgraph. A type omitted there renders in the inspector but is invisible to the
> agent — which makes the enrichment decorative. See §2.4d.

**Logical — the governed-consumption layer (OBDA-READ, Aug 2026).** A signed data product
is a node ON the ontology — a contract stacked on the knowledge model, protected by
its own attestation:

| `entity_type` | Meaning | Key properties | Attaches via |
|---|---|---|---|
| `data_product` | A producer-declared, machine-validated, Ed25519-signed consumption contract (gold `#eab308` — the seal colour) | `manifest` (the contract), `validation_report` (the V1–V7 proof), `version`, `backend`, `description` | `member` → each entity it exposes |

**Logical — the governed-write layer (OBDA-ACT, Oct 2026).** An `action_type` is a signed,
machine-validated WRITE contract over ONE entity (target table frozen, grain, writable
allowlist with value domains, pre/post conditions, a row bound, the DB role that holds
exactly the grant), linked by `acts_on` (action_type → entity). Like `data_product` it is
stacked on the ontology, excluded from its content hash and signed on its own — but it is
deliberately **not** in `SEMANTIC_TYPES`: it reaches an agent's context only when that
agent's grant names it. Full architecture: [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md).

> `data_product` is in `SEMANTIC_TYPES` (invisible governance is no governance) and
> renders in the agent's enrichment as a **consumption contract** — measures,
> entities, validation state, "plan `query_product`, never hand-written SQL".
> **Deliberately excluded from `kg_content_hash`** (with its `member` edges): the
> ontology's signable content is the knowledge model; authoring a product must not
> flip the ontology's own seal. Full architecture:
> [OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md).

**Physical** (`layer="physical"`, `infrastructure` set) — node `name` == the deployed identifier

| `entity_type` | Meaning |
|---|---|
| `catalog` | Top container (deployment root / UC catalog) |
| `schema` | The single deployment database (one per deployment — see §2.4a) |
| `table` | A deployed table (`<db>.<table>` Teradata / `<catalog>.<schema>.<table>` UC) |
| `column` | A deployed column |
| `ddl_artifact` | Literal DDL (view/table/metric definition) |

### 2.4 Naming & identity contract

- **Logical names are fully qualified**: `database`=root, `domain`=name, `entity`=`<domain>.<table>`,
  `attribute`=`<domain>.<table>.<column>`, `metric`=`<owner_domain>.<view>`, `business_concept`=the term.
- **Physical names == the deployed identifier**, byte-for-byte — the *same naming function*
  produces the DDL identifier and the KG node name (so the KG is a true map of what exists).
- `label` (human) lives in `properties`; `name` is the canonical key.
- **Division and domain are LOGICAL-only** (§2.4a): they are first-class *grouping nodes and
  facets* in the KG, but **never physical containers** — neither carries a `database_name`.

### 2.4a Canonical physical namespace — the flat single-database model (NORMATIVE)

The logical model is 4 conceptual levels (`deployment → division → domain → table`); every
real warehouse namespace is ≤3 (`catalog.schema.table`, or Teradata `database.table`). Rather
than collapse the division *inconsistently* (present in the logical containment chain, absent
in the physical one), the canonical model **flattens the physical layer to a single database**:

> **One deployment → one physical database. Every table is deployed flat into it under its bare
> table name. Division and domain never appear in a physical containment chain — they are
> logical/KG-only concepts used for navigation, grounding, and faceting.**

Physical containment is therefore `catalog → schema(the one database) → table → column`;
the single `schema` node **implements nothing at the domain level**, while each `table`
still `implements` its logical `<domain>.<table>` node (the logical link is preserved via the
table's retained logical name). One LDM → many PDMs still holds: each backend deploys its own
single flat database, distinguished by `infrastructure`.

**Why flat is safe — and the collision tie-breaker (empirically validated):**

| Corpus | Tables | Globally-unique table names (case-insensitive) |
|---|---|---|
| **MVM** (all 30 industry models) | 5,556 | **30 / 30** — flat names are pristine |
| **ECM** (all 30) | 12,385 | 10 / 30 — 20 models reuse 1–6 generic names (`field`, `party`, `legal_entity`, …) |

- **Default:** the deployed table name is the **bare table name** (always unique for MVM).
- **ECM tie-breaker (prefix-on-collision):** when a bare name collides case-insensitively with
  another, *only the colliding tables* are disambiguated with a `<domain>_` prefix (e.g.
  `field` → `well_field` / `seismic_field`); the rest stay clean. The `table` KG node
  name equals the deployed identifier (invariant preserved), and it still `implements` the
  un-prefixed logical `<domain>.<table>`.

This removes the logical↔physical asymmetry entirely, eliminates per-domain database sprawl and
PERM-subdivision, and keeps the three-division scaffolding fully navigable — in the graph, where
grounding and traversal happen. Implementation: `dbdeploy/physical_model.py:flatten_to_single_database`
(default `flat_namespace=True` in `dbdeploy/converter.convert`).

### 2.4b One PDM per deployment; the reference PDM is optional (NORMATIVE)

A deployment produces **exactly one physical layer** — the deployed backend's (e.g. the
Teradata flat database). The KG↔warehouse linkage (`implements`) therefore targets the
**deployed** schema, never a reference one. A backend's *reference* PDM (e.g. the Databricks
source structure the model declares) is **not added by default**: the constructor walks
`layers=["logical"]` and the deploy supplies the real physical layer. "One LDM → many PDMs"
(distinguished by `infrastructure`) remains supported for source→target mapping, but a second
*reference* PDM is opt-in, not automatic — otherwise the graph doubles its physical nodes
(catalog/schema/table/column) with a layer that isn't what's actually
queryable. Validated: dropping the reference PDM loses **no context** — every property on a
reference physical node also lives on the logical node and/or the deployed physical node
(see §2.4c).

**Landing an authored node on a second warehouse (PROP-008 C3).** The same rule holds when an
authored entity is deployed to more than one backend: the graph's own physical layer is the
**primary** infrastructure, and only a landing there writes physical nodes, `implements` edges,
`deployed_table` and `deploy_receipt`. A landing on any other infrastructure (e.g. PostgreSQL
beside a Teradata graph) is recorded on the authored node itself as
`realisations[<infrastructure>] = {table|column, deployed_at, standard, readback, receipt}` —
never as a second physical layer — so the graph keeps exactly one PDM while saying everywhere
the entity has landed. A re-run on that infrastructure resumes from its realisation; drops
apply on the primary only. `GET /v1/knowledge-graph/authored` reports `landed_on` per node and
the graph's `graph_infrastructure`.

A landing never walks an authored node's lifecycle back: a node already `published` stays
published when it lands on another warehouse (`kgstore.types.advance_status` only moves forward —
draft → validated → deployed → published). Nor does what a landing or an action stacks on the
graph count as a change to the base: a realisation, an action type and its `acts_on` edge, a
data product and a tuning or ledger `ddl_artifact` are **stacked contracts**
(`kgstore.types.is_stacked_contract`) — outside the ontology's content hash and never a base
mutation when an extension is published. One rule, read by trust composition, governance and
extension signing (Act II rehearsal, Oct 2026: landing on PostgreSQL had un-published the Lead,
and the Lead's own actions had blocked its publish as base mutations).

### 2.4c Column type representation — logical *meaning* vs physical *storage* + the data-domain concept

A column's "type" splits across the two layers by responsibility — and the raw storage type is
**physical**, never logical:

| Aspect | Layer | Property | Example |
|---|---|---|---|
| **Storage type** (realized) | physical (`column`, per `infrastructure`) | `datatype` | `DECIMAL(15,2)`, `VARCHAR(4000)` |
| **Semantic meaning** (infrastructure-neutral) | logical (`attribute`) | `semantic_type` | "Planned Budget Amount (USD)" |
| **Constraints** | logical | `value_regex` | `^[0-9]{12}$` |
| **Provenance** | logical | `references` | "Source: Salesforce CRM" |

The storage type differs per backend (a logical `STRING` realizes as Teradata `VARCHAR(4000)`),
so it belongs on each PDM's `column` — **not** on the logical node. The logical column
carries the meaning (`semantic_type` — 100% coverage in the industry models), constraints, and
provenance. This is why §2.4b holds: meaning on logical, storage on physical → nothing lost when
a redundant reference PDM is dropped.

**Exception (NORMATIVE): `deployed_type` is mirrored onto the logical attribute — and so are the SOURCE-VALIDATION stamps.** When a constructor validates the graph against the source data (`validate_with_source`), the facts it reads land on the logical nodes for the same reason and under the same boundary as `deployed_type`: attribute `sampled_shape` (a Teradata FORMAT), `sample_value` (never on a PII column), `date_range` + `date_range_sampled`, `date_unparseable_ratio` + `date_unparseable_sampled` (the share of non-blank values outside the shape; sampled means the guarded cast stays the recommendation), `blank_ratio`; entity `row_count`, `key_unique`, `key_duplicate_groups`. No timestamp is stamped, so a build that changes nothing hashes identically. The predicate deciding which attribute is a text-stored date is ONE shared function (`components/dateforms.is_text_date_attr`) for the constructors, the enrichment and the preflight guard.


The rule above is about *ownership* — the physical `column` remains the authority, and
`declared_type` on the logical node stays a semantic fact that must never drive SQL. But a
consumer reading the logical layer needs one physical fact to write a **correct comparison**,
and making it join to the physical layer to get it means it usually will not. So the walker
mirrors the deployed storage type onto the logical `attribute` as `deployed_type`.

This is a denormalization, and it is only unambiguous because **§2.4b guarantees exactly one
deployed physical layer**. If that ever relaxes — a graph carrying two PDMs for the same
logical model — `deployed_type` becomes ill-defined and must either be omitted or keyed by
`infrastructure`. Do not quietly let it mean "whichever PDM was walked last".

*Why it is worth the exception, concretely.* `NIBRS_Source.Victim.V4019` declares `INTEGER`
and deploys as `VARCHAR(2)` — one of **1,425 such divergences in 1,508 columns** on that
product. An agent given the decoded values (`0=Female`) but not the deployed type wrote
`WHERE V4019 = 0`; the database cast the column, a blank cast to zero, and the answer came
back **900,719** where the truth is **477,466** — 423,253 blank rows silently included.
Nothing errored. The type rule (meaning from the ontology, types from the dictionary) already
had both facts; they were simply never joined at the point of use. Consumers of a character
column are now shown quoted codes (`'0'=Female`) and told that a numeric comparison also
matches blanks. Gated by `test_kg_enrichment_relevance`.

**Companion pattern (NORMATIVE): a coded field deployed as a raw-code + decoded-label PAIR
registers BOTH columns.** When a serving view delivers a coded attribute decoded — the raw
code column (`offence_code = '120'`) beside its readable label (`offence_type = 'Robbery'`) —
the catalog/ontology must register BOTH as separate attributes: the value-domain (decode set)
and `semantic_type='code'` go on the RAW CODE column, and a codeless decoded label
(`semantic_type='category'`) sits beside it. Registering only the label and attaching the code
enum to it tells the agent the label column holds codes — it writes `WHERE offence_type = '120'`
against a column holding `'Robbery'` and gets **zero rows**, silently. (The policing core-model
generator expands every value-domain attribute into this pair; a build that registered only the
label answered "0 robberies" where the truth is 179,193.)

**Data-domain / logical type class (concept — not yet built).** The formal *logical* analog of a
physical type is a **reusable, normalized type class** — grouping columns into named domains
("Monetary", "Identifier", "Timestamp", "Percentage") with shared constraints. It would be modeled
as a `taxonomy` node that columns `is_a` (both already in the closed enums — no schema change). It
is **deliberately deferred**: `semantic_type` already encodes most of the signal per column, the
source models don't *declare* domains (they'd be derived/heuristic, hence lower-confidence than the
authoritative semantic type), and no consumer requires it yet. Add it — as a `taxonomy`-backed
layer with `is_a` edges — when a concrete need appears (e.g. query-generation safety: "never `SUM`
an identifier", "format a monetary column with currency").

### 2.4d The vocabulary is defined ONCE (NORMATIVE)

`ENTITY_TYPES`, `RELATIONSHIP_TYPES`, `SEMANTIC_TYPES` and
`EXPANDABLE_STRUCTURAL_TYPES` live in **`src/trusted_data_agent/kgstore/types.py`** and
nowhere else. `graph_store.py`, `kgstore/sqlite_backend.py` and `kgstore/conformance.py`
**import and re-export** them.

They previously existed as three hand-maintained copies, each carrying a comment
claiming it was "identical" to another. Three copies of one contract drift the first
time a type is added, and the failure is *silent*: an entity validates in one layer and
is rejected — or never expanded into the agent's context — in the next.

**Do not re-introduce a literal type set anywhere.** `test/test_kg_vocabulary.py` asserts
**identity** (not equality) across the consumers, so a fresh copy fails CI even when its
contents happen to match today.

Adding a type is therefore four coordinated edits, all gated:

1. `kgstore/types.py` — the type itself, in the right layer set
2. `SEMANTIC_TYPES` — **if the agent should see it** (omission = invisible to the agent)
3. `handler.py` + `renderer.js` — colour and filter-pill order (backend/frontend parity)
4. `semantic_interchange/vocab.py` — the RDF class for the standards export

The gate covers all four: a type the backend can emit but the frontend cannot colour,
or that has no RDF class, or that the agent cannot see, fails the build.

### 2.5 Relationship vocabulary (closed enum)

| `relationship_type` | Semantics | Typical (source→target) |
|---|---|---|
| `contains` | Structural containment | database→domain, domain→entity, entity→attribute, catalog→schema, schema→table, table→column |
| `foreign_key` | Referential dependency | entity→entity or attribute→attribute (physical: table→table / column→column) |
| `implements` | Physical realizes logical | table→entity, column→attribute, catalog→database, ddl_artifact→(entity\|metric), **column→metric** (a metric realized as a direct column mapping), **table→metric** (a materialized metric table). *(In the flat model §2.4a the single `schema` implements nothing — domains are logical-only.)* |
| `measures` | A metric measures an entity | metric→entity |
| `relates_to` | Semantic association | business_concept→(entity\|attribute) |
| `is_a` | Taxonomic specialization | entity→taxonomy/concept |
| `has_property` | Attribute attachment | entity→concept |
| `derives_from` | Lineage / transformation | entity→source entity |
| `depends_on` | General dependency | entity→entity |
| `member` | Data-product membership (a governance claim, not containment) | data_product→entity |
| `acts_on` | The one entity a signed write contract may touch | action_type→entity |

`cardinality` (`N:1`) and `metadata` (FK column pairs, derived-edge `count`/`via`) are optional.

### 2.5a Metric realization kinds (a logical metric ↔ its physical implementation)

A logical `metric` is the **term** (meaning: dimensions/measures, infrastructure-neutral); its
**physical realization** is a distinct node `implements`-linked to it. The realization is *multi-kind* —
a metric is not always a computed view:

| Realization | Physical node | Edge | When |
|---|---|---|---|
| **Computed view** | `ddl_artifact` (`artifact_kind=metric_view`) | `ddl_artifact → implements → metric`, `derives_from → table` | the measure is an aggregate (`SUM(...)`, ratio) — compiled on read |
| **Direct column mapping** | `column` | `column → implements → metric` | the value already lives in a column (a single bare-column measure) — *read directly, no aggregation* |
| **Materialized table** | `table` | `table → implements → metric` | precomputed/stored aggregate |

The resolver (`components/builtin/knowledge_graph/resolver.py:resolve_metric_deployment`) reports the
realization `kind` (`view`/`column`/`table`/`base_table`/`none`) deterministically, in that precedence,
so the agent self-directs (query the view · **read the column** · query the table · compile from a base
table). The PDM deploy auto-detects the direct-column case: a metric whose single measure is a bare
column reference existing on the deployed source table is realized as `column → implements → metric`
(no view generated) — `dbdeploy/dialects/teradata.py:passthrough_column` + `_emit_metric_views`.

### 2.6 Faceting & grouping dimensions

`division`, `domain` (+ `layer`, `infrastructure`) are both **properties** (with name-fallback
for grouping nodes) and **first-class filter facets**. The sampler returns a facet *catalog* of
values present, so a facet appears **only when the data has it** (`get_typed_subgraph(divisions=, domains=)`).

### 2.7 Provenance & data lineage (two distinct concepts)

There are **two** "where did this come from?" questions, and the topology must answer both:

- **Provenance** (production metadata) — *who built this KG node/edge*. Every node/edge
  carries `source` (producer id — `industry_model_constructor`, `teradata_deployment`,
  `agent_discovery`, `semantic_mapping`, …) + `source_detail`. Re-syncable producers stamp a
  **`source_descriptor`** on `kg_metadata` (metadata, not a graph entity — `clear_graph()`
  must not wipe it) → check-update → diff → apply-update + versioning (`kg_version_db`,
  layer-aware `graph_diff`).
- **Data lineage** (data flow) — *how a PDM table/column is populated*: from which **source
  system** field(s), through which **business rule / transformation**. The PDM is not only a
  *target* (a structure we deploy) but a *sink* fed from sources via rules. Lineage is
  expressed with `derives_from` edges (target ⟶ source) carrying the transforming rule; see
  §3.9 for the full model. This is what lets the agent answer *"explain this number from raw
  source through every transformation"* — a core trust/zero-trust differentiator.

### 2.8 Conformance

Conformant iff: every `entity_type ∈ ENTITY_TYPES`; every `relationship_type ∈ RELATIONSHIP_TYPES`;
names per §2.4; `layer` set; physical nodes carry `infrastructure` + `implements` a logical node;
provenance set. New producers ship a CI conformance test (`test_kg_*`, real backend).

> **Conformance is now also the ontology *trust* validator.** In the `Trust Membrane (Tier 5.2)` a KG is an **ingredient** an agent grounds on, and `kgstore/conformance.py` is its per-type sign-off validator: **HIGH severity → nonconformant → the ontology cannot be signed 🟢**; MEDIUM/LOW → signable *with a badge note*. So the severity bands (§2.8 here) directly define what an ontology ingredient must satisfy to be *trusted*, and hence whether an agent composed on it can itself be signed. The mandatory-vs-optional artefact contract is `TIER_5.2 §3.3`.

---

## Part III — The Ontology in Action

### 3.1 Context as a first-class artifact

The ontology's output is **not a prompt string** — it is a *structured, layered, budgeted,
traceable context object*: the selected KG subgraph (logical + physical, facet-scoped) +
retrieved chunks + documents + live-data handles, each annotated with **provenance** and a
**budget cost**, assembled by the context-window orchestrator. Because every element resolves
to its source, the agent's answer is **explainable and auditable** ("this number came from
`finance.cost_center` deployed at `<catalog>` via mapping M from the source schema") — the
basis of the platform's zero-trust posture.

### 3.2 Producers (Acquire / Model / Map / Materialize)

| Producer | Stage(s) | Emits | Determinism |
|---|---|---|---|
| **Agent-discovery constructor** (`kg_constructors/agent_discovery`, inline in `kg_generate`) | Acquire+Model | logical graph from a live DB (MCP + LLM) | LLM-assisted |
| **Industry-model constructor** (`declarative_model` + `industry_model_walker`) | Acquire+Model | logical (+ Databricks physical) from `model.json` | deterministic, 0-LLM |
| **PDM deployment** (`dbdeploy/` + `_deploy_pdm`) | Materialize | Teradata physical layer for what it deploys + live tables/data | deterministic |
| **Semantic-mapping skill** (source→target) *(planned, §3.6)* | Map | `relates_to`/`is_a`/`derives_from` edges + `business_concept` mappings | LLM-assisted, conformance-tested, diffable |
| **Document / RAG ingestion** | Acquire | embeddings + knowledge-repo chunks (unstructured complement) | deterministic |

All graph producers go through `KGConstructorStrategy` (ABC + registry by `discovery_mode`)
or the `GraphStore` API, and MUST honor Part II.

### 3.3 Substrate

- **Knowledge Graph** — `kgstore/` (SQLite+NetworkX default, Neo4j enterprise),
  capability-negotiated, per-profile resolution. Holds the canonical topology.
- **Vector store** — `vectorstore/` (ChromaDB/Teradata/Qdrant) for embeddings.
- **Knowledge repositories** — document corpora + CDC sync.
- **Deployed physical model** — live relational schema + data, mapped 1:1 to the KG
  physical layer (the bridge from semantic model to queryable data).

### 3.4 Grounding & Assembly (the fusion model)

This is the heart of the ontology — turning a turn's intent into composed context:

1. **Resolve (Ground):** map the query/intent to relevant grounded structures — semantic-layer
   lookup (`business_concept`/`metric`) → logical entities → (via `implements`) physical/live;
   facet-scope (division/domain/layer) to bound the subgraph; reduce-then-render sampling
   (`get_typed_subgraph`: seed → connectivity-expand → fill) for legibility + budget.
2. **Retrieve:** RAG champion cases (proven plans) + knowledge chunks + documents, vector-ranked.
3. **Fuse & budget (Assemble):** the context-window orchestrator's five-pass assembly
   (resolve → dynamic adjust → allocate+contribute → reallocate surplus → condense) merges the
   KG subgraph, retrieved chunks, documents, and live-data handles into one budgeted object,
   condensing lowest-priority modules first; emits a `context_window_snapshot` for observability.
4. **Serve:** hand the structured context to the consumer with provenance intact.

Graph context is **structured signal** the planner trusts (traverse a real FK, not guess a
join); vector context is **recall** for the open-ended residue. The fusion is the point:
retrieval alone is only step 2.

### 3.5 Consumers (Serve)

| Consumer | Uses |
|---|---|
| Context-window orchestrator (`components/builtin/context_window/`) | budgets + assembles per-turn context across modules |
| IFOC engines (`agent/engines/`) | Optimize planner (RAG cases + KG grounding), Focus (retrieval), Coordinate, Ideate |
| Next-step recommender / Agent-Ops eval / KG inspector | suggestions, scoring, visualization |
| The agent at query time | traverses logical↔physical (`implements`) to answer "where is concept X?" and query live tables |
| **Grounding gate + `scope_adherence` scorer** | read the KG's **deployed scope** as the trust boundary — enforce (gate) / measure (scorer) that answers stay within governed data (§3.10) |

### 3.6 The semantic layer & source→target mapping

The `business_concept`/`metric` layer is the **bridge between human language and physical
schema** — the thing that lets an agent answer "show me churn by region" without knowing the
table. The flagship capability that this whole ontology exists to enable:

> **Ingest a customer's messy source schema → map it semantically to a canonical target
> model (an industry model / standard) → deploy a conformant physical model + data →
> ground the agent on the unified semantic layer *and* live data.**

The **semantic-mapping skill** is the *Map* stage producer: given a **source** KG and a
**target** KG/standard, it emits *graph edges, not prose* — `relates_to`/`is_a` from source
`attribute`/`entity` → target `business_concept`/`entity`, `derives_from` for lineage, new
`business_concept` nodes for target vocabulary — with `source="semantic_mapping"` and a
re-syncable `source_descriptor` (source ref + target ref + options) so the mapping is
re-runnable and diffable like any constructor. Because it honors Part II, its output is
**immediately** usable by every consumer (planner grounds on it, inspector facets it, eval
scores it) with zero bespoke integration. *That* is the payoff of one canonical topology:
ontologyering becomes a composable product, not per-customer glue.

### 3.7 The learning plane (Learn)

The ontology improves with use: successful turns become **RAG champion cases** (proven plans,
plan-based seeding gate) that accelerate + ground future turns; the **Agent-Ops eval** system
grades each turn (incl. retrieval relevance, groundedness) and feeds an agent-health view.
Context quality is *measured*, not assumed — closing the loop the differentiation rests on.

### 3.8 Governance & multi-tenancy

Per-profile KG assignment (`kg_profile_assignments`), backend governance
(`kg_backend_settings`), versioning + update/diff (`kg_version_db`, `graph_diff`), and
marketplace publish/fork/**subscribe** with auto-sync all operate on the canonical topology
and apply uniformly to every producer's output — so context is *owned, versioned, shareable,
and current* per tenant.

### 3.9 Data lineage: source systems → business rules → PDM (first-class)

> **Flagged as a core concern to design in, not an afterthought.** The PDM is fed: real
> data flows from **various source systems** into PDM tables/columns through **business
> rules** (mappings, derivations, aggregations, cleansing). Capturing this end-to-end —
> *source field → rule → target column → semantic concept → the agent's answer* — is the
> lineage backbone that makes the ontology **auditable and trustworthy**, and it is a
> top enterprise differentiator (answer "where did this value come from, through what rule,
> from which system?").

The lineage model on the canonical topology:

| Element | Representation |
|---|---|
| **Source system** | a physical layer of its own — `catalog`/`schema`/`table`/`column` with `infrastructure="<source system>"` (e.g. `salesforce`, `sap`, a CSV feed). The PDM is a *separate* physical layer; both `implements` the same logical model where applicable. |
| **Data flow** | `derives_from` edges, **column-level**: PDM `column` ⟶ source `column`(s). Table-level `derives_from` for coarse lineage. |
| **Business rule / transformation** | the transforming logic between source and target. Two candidate representations (open design decision): (a) **edge metadata** on `derives_from` (`metadata.rule`, `expression`, `rule_id`) for lightweight cases; (b) a **first-class node** — reuse `ddl_artifact` (the rule SQL/spec) `derives_from`-linked, or add a new additive `transformation`/`business_rule` entity type (conformance-tested) — when rules are reusable/governed objects in their own right. |
| **Lineage chain** | source `column` → (rule) → PDM `column` → (`implements`) → logical `attribute` → (`relates_to`) → `business_concept` → grounds the agent's answer. Fully traversable. |

Relationship to the **semantic-mapping skill** (§3.6): *mapping* defines the semantic
correspondence (source ⟷ target meaning); *lineage* records the data flow + the rule that
populates the target. Related but distinct — mapping can inform rule generation, and both
are `Map`/`Materialize`-stage concerns that emit `derives_from`/`relates_to` edges.

**Why now (capture, not build):** the deployment today loads synthetic demo data, so no
real lineage exists yet. But the topology must be *ready* — `derives_from` is already in the
enum; the source-system layer and business-rule representation are the additive pieces to
design before production feeds land, so lineage is captured at ingestion rather than
reverse-engineered later.

### 3.10 Enforcement — the deployed scope as a trust boundary (grounding gate)

The ontology doesn't only *serve* context; the scope it declares is now **enforced**. This
closes the loop the whole differentiation rests on — the documented failure mode where an
agent received a complete, scope-correct enrichment, **ignored it**, brute-forced calls
across unrelated databases, and answered confidently-wrong (the "230-day" case).

**The deployed scope is the trust boundary.** A KG knows the governed databases the agent is
allowed to answer from. Two derivation paths (both deterministic, 0-LLM), implemented in
`knowledge_graph/handler.py`:

| KG kind | Scope source |
|---|---|
| Industry-model (deployed PDM) | the **physical** `table`/`schema` node identifiers — `_derive_full_kg_scope_dbs` reads the qualified `<db>.<table>` names → the set of deployed databases |
| Discovery (logical-only) | the logical `database` root node — `_derive_scope_databases` falls back to it **only** when no physical layer resolves, so a discovery KG is protected without ever *widening* an industry KG's precise physical scope |

This is the same `database.scope` / physical-name substrate the topology already defines
(§2.2–§2.4) — surfaced as a queryable trust boundary, `get_trusted_scope(profile_id)`
(cached per KG).

**Two consumers, one definition.** That boundary feeds both an **observer** and an
**enforcer**, sharing one primitive (`agent/grounding/primitives.py`):
- **Observe** — the `scope_adherence` eval scorer counts tool calls that left the scope (Agent-Ops, §3.7).
- **Enforce** — a synchronous **grounding gate** runs before the answer reaches the user and can pass · annotate · **withhold** an answer that drifted outside the boundary. Enforcement is *tiered*: a deterministic trigger (an out-of-scope tool call, or an out-of-scope identifier in the answer) defers to the `scope_faithfulness` LLM judge, which makes the withhold/deliver call — so honest "that isn't in my scope" refusals are never punished. Default mode is `observe` (measure only); enforcement is opt-in per profile.

This is the **Serve**/**Govern** counterpart to **Learn**: the ontology grounds the agent on
governed context *and* guarantees the answer stays within it. Full design + the
judge-backed-enforce architecture: [AGENT_OPS_EVAL_ARCHITECTURE.md §6a/§6b](AGENT_OPS_EVAL_ARCHITECTURE.md)
(grounding gate + the published anti-hallucination benchmark).

---

## Part IV — Neighbouring approaches

| Approach | Provides | What the ontology adds |
|---|---|---|
| **RAG frameworks** (LangChain/LlamaIndex) | vector retrieval | structure, layering, provenance, determinism, semantic layer, live data, governance, learning |
| **GraphRAG** | a graph + retrieval | a *layered* logical↔physical model, deployable physical realization, cross-model mapping, per-tenant governance, an eval-driven learning loop |
| **Warehouse semantic layers** (dbt/Cube/LookML) | a semantic layer over one warehouse | agent grounding, cross-model source→target mapping, unstructured fusion, marketplace sharing, the assembly/budget plane |
| **Vanilla agent frameworks** | tool calls | a governed context substrate at all |

Uderia's ontology combines these — graph + semantics + live data +
retrieval + assembly + governance + **scope enforcement** + learning — packaged as one
governed, multi-tenant, marketplace-shareable ontology. The reference model (Acquire→…→Learn),
plus a deployed scope that is *enforced* not just declared (§3.10), is what holds the
combination together.

---

## Part V — Extension contracts (how to add a participant)

| To add… | Do | Stage |
|---|---|---|
| **A KG constructor** | implement `KGConstructorStrategy` (`construct`, optional `rebuild`+`supports_resync`); register by `discovery_mode`; emit Part II-conformant output; stamp provenance (+ `source_descriptor` if re-syncable); ship a `test_kg_*` conformance test | Acquire+Model |
| **A context-producing skill** (e.g. semantic mapping) | write via `GraphStore.import_bulk`, Part II types/naming only; provenance + descriptor; live under `skills/builtin/` per component+skill separation; conformance-test | Map |
| **A deployment backend** | implement `DeploymentBackend` (capabilities, `execute_ddl`, `ensure_database`, `bulk_load`); converter emits a Part II physical delta `implements`-linked to logical | Materialize |
| **A KG store backend** | implement `KGStoreBackend` + `register_backend`; advertise `KGStoreCapability`; `get_typed_subgraph` must support the facet contract (§2.6) | substrate |
| **A new entity/relationship type** | additive enum change in `kgstore/types.py` (the four coordinated edits of §2.4d) + a row in §2.3/§2.5; verify inspector legend, agent-pack export/import, diff, validation tolerate it | topology |
| **A context-window module** | implement the `ContextModule` contract; declare priority + RAG-condensation support | Assemble |

---

## Part VI — Harmonization roadmap

1. ✅ **Enrich `metric`** with full `dimensions[]`/`measures[]`/`source` → deterministic
   metric-compiler + metric→view generation (M7, done): the walker stamps the full
   definition on the `metric` node; `dbdeploy/metric_compiler.py` compiles ratio-safe
   Teradata SQL; metric views deploy as `ddl_artifact` physical nodes `implements`-linked
   to the logical metric; a `compile_metric` KG component tool exposes it at runtime.
2. **Extract the agent-discovery path** behind `KGConstructorStrategy` — *partly done*: the
   capability is registered (`kg_constructors/agent_discovery.py:AgentDiscoveryStrategy`,
   modes `tool`/`prompt`, `supports_resync=False`) beside the declarative strategies
   (`industry_model`, `declared_model`, `andp_semantic`, …, all resolved by
   `kg_constructors/registry.py`), but the orchestration still runs inline in
   `rest_routes.py::kg_generate`.
3. **Ship the semantic-mapping skill** (§3.6) — the first cross-model *Map*-stage producer.
4. **`test_kg_topology_conformance`** — one shared suite every producer runs (not yet: today
   `test/test_kg_conformance.py` gates the validator and its type-set parity, and each
   producer ships its own `test_kg_*`).
5. ✅ **A first-class `knowledge_graph_context` context-window module** (delivered Jun 2026) —
   contributes graph-grounded context beside `rag_context`/`knowledge_context` with a real token
   budget: the subgraph node count scales to the module's allocation (replacing the fixed 500-node
   raw append onto `knowledge_context`), it gets its own `context_window_snapshot` bar, and a
   condensation strategy. Single-source over the KG component's `get_context_enrichment` (now
   `max_nodes`-aware); preserves the grounding-gate `kg_enrichment_event`. See
   [CONTEXT_WINDOW_ARCHITECTURE.md](CONTEXT_WINDOW_ARCHITECTURE.md) → Built-In Modules.
6. **Context-lineage surfacing** — expose the per-answer provenance trail (which nodes/edges/
   chunks/live tables grounded the answer) in the UI + eval.
7. **Data lineage (source → business rules → PDM)** — design the source-system physical layer
   (`infrastructure="<source>"`), the business-rule/transformation representation (edge metadata
   vs a first-class `transformation`/`ddl_artifact` node — §3.9), and **column-level `derives_from`
   capture at ingestion** from the feeding pipelines; then end-to-end lineage traversal +
   surfacing (source field → rule → PDM column → concept → answer). Topology is ready
   (`derives_from` exists); the layer + rule node + capture path are the additive work.
8. ⭐ **The Platform Ontology (Uderia's centerpiece)** — this ontology, extended with platform
   component *types* (`profile`/`agent`, `memory`, `collection`, `flow`, `connector`, …) and
   per-user **segment** scoping, becomes (succeedingly) the **governed, access-scoped semantic
   gateway to data** — ontology-mediated data access, with `compile_metric`/`resolver` as the
   concept→query embryo and the per-user segment as the access boundary. The long-term
   differentiator. Full vision + step-by-step plan: `TIER_5_PLATFORM_ONTOLOGY.md`.

---

## Appendix — Canonical vocabularies (quick reference)

**Counting words (what a person reads).** A **node** is any element of a graph — whatever its
`entity_type` below. A **relationship** is an edge between two nodes; it is never a node. An
**entity** is a node of type `entity` only — a business concept (Incident, Offence, CaseObject).
The store calls every node an "entity" (`kg_entities`, `total_entities`, `entities_added`): those
names are storage and count NODES. Every count shown to a person goes through
`static/js/kgVocabulary.js` — a graph's size reads "275 nodes · 15 entities", an import "275 nodes
and 422 relationships", an extension "1 entity · 9 attributes" — and `test_kg_vocabulary` fails
any screen that labels a count "entities" by hand.

```
ENTITY_TYPES = {
  # logical  (entity = a logical relation, attribute = a logical field)
  database, domain, entity, attribute, foreign_key, metric, business_concept, taxonomy,
  # logical — product description
  query_recipe, design_decision, quality_metric, naming_standard, product_bridge,
  # logical — governed contracts stacked on the model
  data_product, action_type,
  # physical (layer="physical", infrastructure=...; table/column = deployed realizations)
  catalog, schema, table, column, ddl_artifact,
}
RELATIONSHIP_TYPES = {
  contains, foreign_key, implements, measures, relates_to,
  is_a, has_property, derives_from, depends_on, member, acts_on,
}
LAYERS = { logical, physical, authored, observed }   # authored + observed are kept on replace
FACETS = { division, domain, layer, infrastructure }   # queryable + selector tiles
REFERENCE_MODEL = [ Acquire, Model, Map, Materialize, Ground, Assemble, Serve, Learn ]
```

### 2.4e Structure the ANDP constructor derives from what a product declares (2026-09-04)

Every rule below is a rule of the constructor for ANY product; none names a column, a
prefix or a product. A product that declares nothing a rule can use is unchanged apart
from the two stamps every node now carries.

| structure | derived from | recorded as |
|---|---|---|
| **repeating group** — one fact stored once per slot | attributes of one entity whose description TITLES are equal once their trailing position numbers are stripped (`LOCATION TYPE - 1/2/3`) | `family` (the stem), `family_size`, `family_axes` (1 or 2 position indices), `family_members` (in position order), `positions` on each member |
| **position axes** | the numbers a title ends with (`… USING 1 - 2` = position 1, slot 2) | `positions: [1, 2]` |
| **column reference** — the column a description qualifies this one by | a description naming ANOTHER attribute of the same entity by its exact identifier | `refers_to: [...]` — a property, deliberately not an edge (an edge is walked by the extractor and rendered; measured, that grew every prompt by a third) |
| **range codes** | a declared value that is an integer range (`-5 to -9`, one label) | expanded to its members, each carrying `range`; the renderer folds them back into one entry |
| **module / product stamps** | the ANDP module the entity belongs to; the product name | `module`, `product` on every attribute |

Consumers: the enrichment renders a relevance-gated REPEATING GROUPS block (members listed
once, axes named) and a short `slot i/j of N` + `qualified by X` on each relevant column's
line; the seeder treats a declared family as one concept and expands it only when the
question asks for positions. Gate: `test/test_kg_repeating_groups.py`.

### 2.4f Declaring a concept (what a use case names and the product never defined)

`TDA_KnowledgeGraph action="declare_concept"` / `POST /v1/knowledge-graph/declare-concept`
(`components/builtin/knowledge_graph/concepts.py`, pure): from a concept name, a prose
definition, ONE boolean predicate and an entity, it declares a `business_concept` (the
term, definition, predicate, columns), a `metric` the platform's compiler consumes
(`SUM(CASE WHEN <predicate> THEN 1 ELSE 0 END)` over the entity's deployed table, so
`compile_metric` answers without an agent writing SQL) and a `query_recipe`, linked to the
entity and to every column the predicate names. Fails closed: the predicate must parse as
a condition and every column it names must be a declared attribute of the entity. The
product's Memory-module statements (glossary, cookbook) are RETURNED as text for the
owner; the platform never writes to a product's database. Declaring changes the
ontology's content hash — the seal reads *changed* until the owner re-signs, as it must.
Gate: `test/test_kg_concepts.py`.


### 2.4g The design standard on a physical node — `standard`, and the readback on an authored node (2026-10-06, PROP-008 A4)

A physical node a deploy writes (`table` · `column` · `ddl_artifact`) carries **`standard`** — the
version of the design standard that built it (`teradata/1.0`), so a graph records what built its
physical layer and the trust layer can bind it. The standard itself is a set of **rule records**
(`dbdeploy/design_standard.py:RULES` — id, rationale, source ∈ vendor_doc · measured · operator,
and the statement that verifies the rule on a target version; the executed record lives beside it
in `design_standard_verification.json`, the `teradata-patterns` shape). A rule with no executed
verification on the target's version reads `unverified` in every deploy report — never a pass.

An **authored** node (§4.1 of PROP-008) gains two facts the road reads: `validated_at` (+ `status:
validated`) when a deploy preview found its planned table conformant to the standard — **a preview
is the validation act** — and, after a landing, **`readback`**: `{status, held, violated,
unverified, violations[], standard, at}`, the counts of the dictionary read back rule by rule for
that node's table (`dbdeploy/readback.py`, the conformance gate's grammar: `conformant` ·
`partial` · `nonconformant` · `unverified` — a dictionary nothing was read from is **not** a clean
warehouse). The primary index is **chosen from a measurement, not copied from the declaration**
(`dbdeploy/probes.py`): on the rows a deploy will load, a skewed or non-unique declared key is
demoted — to the even foreign key or to NO PRIMARY INDEX — and the demotion is NAMED on the
decision, the receipt and the report; on synthetic rows every decision is `provisional` and
nothing is applied. These are measurement stamps in the §2.4c sense (facts about the data and
the warehouse, on the node that names what they measure) and are never part of the ontology's
content hash.
