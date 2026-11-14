# OBDA-READ — Signed Data Products (Ontology-Based Data Access)

> **The live contract** for the OBDA-READ subsystem — what is built, by which rules, and where
> the honest boundaries sit. The binding *design* is `TIER_5.5_OBDA_SCOPE.md`
> (normative MUST/REJECT language; its §13 stop-line governs); the per-increment build
> record is `OBDA_STATUS_ASSESSMENT_2026-08.md`.
> Status: **v1 complete** (branch `feature/obda-phase-a`, Aug 2026) — Phases A–D, V7 live
> probes, O2 receipts, E1–E5 enforcement, K5, and the P1–P3 compositional ratchet. Since
> Oct 2026 the plane is a component (no master switch) with a per-plane policy. Checked
> against code 2026-10-30.
>
> **The family:** OBDA-MODEL defines and extends the model, OBDA-READ (this document) reads
> through it, OBDA-ACT writes through it ([ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md)).
>
> **The write half** is OBDA-ACT — signed action types, built on this pattern: see §10a and
> [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md).

**One sentence:** a *data product* is a producer-declared, machine-validated,
Ed25519-signed subgraph of the ontology (entities + frozen deployed mappings + declared
joins + measures), and OBDA-READ is the single deterministic surface — agent, BI, and MCP —
through which it is consumed; consumption is a pure function of
`(product version, request)`, so a signed product cannot answer differently, drift,
or invent.

---

## 0. What "signed" MEANS for a data product

The Trust Membrane makes "signed" mean *attested + intact*. A data product's signature is
**stronger** (scope §2.4): *"the producer approved this definition AND the platform
mechanically verified that every request in its admissible space compiles to
deterministic, aggregation-safe SQL."* Three readings coincide in one act:

| Reading | What the signature asserts |
|---|---|
| **Cryptographic** | attested by an authorized signer, byte-intact since (Ed25519, the shared provenance key) |
| **Semantic** | same request → same governed SQL → same rows — deterministic by machine proof, not by promise |
| **Trust** | reproducible ⇒ governable: the signature is **earned** through the V-gate, never a rubber stamp (S1) |

Because the *proof travels inside the signature* (§2), an edited manifest, a stale
report, or a drifted member cannot hide behind an intact-looking seal.

**Availability is the component's (Oct 2026 — no master switch).** OBDA-READ is the builtin
component **`obda_read`** (tool `TDA_DataProduct`), and whether the plane exists for a
deployment, a user or an agent is decided the way it is for every component: the admin
pane's global gate (Administration → Components → OBDA-READ), the per-user override, the
agent's own toggle (Capabilities), and relevance — a knowledge graph bound to the agent.
ONE reader answers for every face: `core/obda_planes.plane_available` (deployment · user)
and `plane_enabled` (this agent), over `components.enablement`. With the plane off
Uderia behaves as if OBDA-READ never shipped: no scan, no stamp, no REST surface
(`403 obda_read_off`, naming the admin pane), no Designer button, no scorer banding
(the `data_governance` property declares its lane as `component:obda_read`), **no product
contracts in the KG enrichment, no product seeding, `query_product`/`list_products`
refuse (`obda_not_enabled_for_agent`, recovery-shaped — "use your ordinary data tools"),
and the data surface does not confine** — the surface is dropped ONCE at
`agent_authority.parse_scope` (`planes=` from `obda_planes.planes_for`), so the tool
chokepoints, the enrichment cut, the champion filter and the catalog marking all inherit
from one seam (gating each consumer separately is how the enrichment and the agent face
were once missed — session d51cf073). The former `OBDA_ENABLED` master was migrated once
at startup into component availability (`auth/database._migrate_obda_masters`): a
persisted *off* disabled the component for the deployment; every agent that carried a
data surface had `obda_read` switched on. The component keeps the moved actions on the
knowledge-graph tool as unadvertised aliases (stored champion plans keep working), has
its own split panel and Workspace section, and the companion skill `obda-read-expert`.
Like every component it declares its **signals** in its manifest (`instances: agent` — the
products it keeps belong to an agent's graph; it announces `created` · `deleted` ·
`validated` from the product routes via `@_signals.announces_route`, and its panel follows
`uderia:component-changed`), so a product defined, validated or deleted anywhere redraws an
open panel and the Workspace without a refresh (COMPONENT_ARCHITECTURE § Component Signals).

**The policy — "contracts only", one per plane (Oct 2026).** Availability says whether a
plane EXISTS; the policy says what the deployment REQUIRES of every agent once it does.
`APP_CONFIG.OBDA_POLICY` (`{read, model, act}`, each `off · observe · enforce` — the preflight
ladder; Administration → App Config → Trust → *OBDA policy*; persisted in
`system_settings.agent_eval.obda_policy`; the ONE normalizer `obda_planes.normalize_policy`
reads an unknown mode as *off*, never *enforce*). Applied ONCE at
`agent_authority.derive_principal` (`apply_policy`, pure): **enforce** on *read* writes an
EXCLUSIVE data surface over the agent's granted products into its scope (none granted ⇒ an
empty surface — nothing readable but through a product); on *act* an EXCLUSIVE write surface
over its granted actions, and command tools (`_looks_like_command`: a shell, a script, a
terminal — writes no classifier sees) refused; on *model* the knowledge-graph component
refuses the four DIRECT graph edits (`add_entity` · `add_relationship` · `declare_concept` ·
`restore`) for an agent, recovery-shaped — the graph changes through a proposal a person
publishes. Because enforce is written INTO the scope, every consumer inherits it without a
gate of its own: the three tool chokepoints, the tool surface, the enrichment cut, the champion
filter, `describe_scope` ("deployment policy: writes contracts-only") and the BOUNDED station.
**observe** changes no field: at the chokepoint (`enforce_tool`) the call is allowed and what
enforce WOULD have refused is recorded as a `policy_observed` trust event (warning; BOUNDED;
the Assurance Inbox) naming the plane, the tool and the agent — the pre-enforcement
rehearsal: see which agents would break before any breaks. An active policy (any plane not
*off*) applies to EVERY turn, interactive included (`enforces_interactive` yields to it — a
present human sees prose, not SQL). People are untouched: the graph editors, Govern, the
designers and the REST faces act under the person's own authority. The stamp carries a
`policy` key only for planes under policy (the baseline hash is unchanged at *off*). Gate:
`test/test_obda_policy.py`.

**The per-agent tri-state** (operator model, 2026-08-17; resolved by ONE rule,
`agent/obda.py:profile_obda(profile) → (mode, products)`, read by the enrichment
render/seed/event, both agent actions, the `governed_execution` planner pass, the lane
scan and the champion filter):

| Option | Stored shape (`authorityConfig.dataSurface`) | Behaviour |
|---|---|---|
| **A — Disable OBDA-READ** (the DEFAULT: every unconfigured profile, an empty product list, and always the answer with the master off) | absent / `null` | no products visible to the planner (subgraph, contracts, seed, event all clean), `query_product`/`list_products` refuse (`obda_not_enabled_for_agent`), no OBDA-READ planner behaviour, no lane stamp (the ring reads pre-OBDA-READ), OBDA-READ champions not offered |
| **B — Surface Full** | `{products, exclusive: true, advisory}` | only the listed products; contract-only enrichment; raw SQL denied fail-closed (Agent Authority); `advisory` is the observe-only dev sub-toggle |
| **C — Products + regular paths** | `{products, exclusive: false}` | the listed products render as contracts BESIDE the full KG paths — the LLM decides per question. **The list is still the grant**: `query_product` refuses products not on it (config-driven, so it holds on interactive turns that carry no principal) |

Profile editor: Agent Authority → *Data surface (OBDA-READ)* — a three-option radio;
an empty product list saves as A (the editor never shows a mode that does not act);
switching to A genuinely clears a stored surface. Enforcement (raw-SQL denial) stays
with the ambient principal; planning **visibility** follows the profile's
configuration on every turn.

---

## 1. The product model

A product is a **`data_product` node on the ontology** (gold `#eab308` in the inspector;
`member` edges to the entities it exposes; excluded from the ontology's own content hash
— see §2). Its `properties.manifest` is the contract:

```
product / version / backend / description
entities:  name · deployed (frozen resolver output, M1) · key (the grain, M2) · attributes (the allowlist, M4)
joins:     from (the MANY side) → to · on-pairs · cardinality N:1|1:1 (M3 — REQUIRED, never inferred) · role
measures:  name · anchor entity · expr (dialect-checked)
```

`dbdeploy/product_model.py` is the pure model: frozen dataclasses, `parse_manifest`
enforcing every manifest-decidable M-rule with a **closed REJECT-code set**
(`ERROR_CODES`) and difflib near-miss hints — a rejection is self-correction food, never
a guess. Canonicalization (`canonical_json` → `manifest_hash`) is byte-identical to the
Trust Membrane's discipline, so the two formulas can never drift.

Composed products (§8) declare `members` + `bridges` *only* — their entities and
measures come from the flatten.

---

## 2. The earned signature (trust kind #11)

`core/trust/products.py` + the `data_product` validator in `core/trust/validators.py`.
**Five refusal conditions**, each machine-readable, checked at every sign-off:

1. `invalid_manifest` — the manifest (or, composed, its flat form) fails an M-rule;
2. `unvalidated` — no stored V-gate report exists (S1: the signature is earned);
3. `stale_report` — the stored report validated a **different** manifest
   (`report.manifest_hash` mismatch — the ratchet: edit ⇒ re-validate ⇒ re-sign);
4. `gate_blocked` — the report exists but says not signable (V-gate blockers);
5. `unsigned_ingredient` / `unsigned_member` — the ontology (or, composed, a member
   product) is not signed-and-intact — the bottom-up rule, extended compositionally.

**Signable content** = `hash{manifest_hash, validation_report_hash}` (+ `member_hashes`
for composed products, §8) — *the proof is inside the signature.* `live_hash` recomputes
the same formula from the node's current state, so an edited manifest or refreshed
report reads 🟠 CHANGED, never silently intact. An unresolvable attested product reads
CHANGED (fail-safe), an unattested one UNSIGNED.

**The ontology-hash exclusion (load-bearing).** `kg_content_hash` **excludes**
`data_product` nodes and `member` edges: the ontology's signable content is the
*knowledge model*; a product is a contract **stacked on** it, protected by its own
attestation. Without this rule, authoring a product flipped its own ontology CHANGED and
forced a rubber-stamp re-sign loop (found live in Phase A, fixed at the root).

Products are removable: `DELETE /v1/data-products/<name>` deletes the node **and
revokes the attestation** — a dangling signature over a deleted contract attests
nothing; the ontology's seal is untouched by construction.

---

## 3. The validation gate — V1–V7

`dbdeploy/product_validation.py:validate_product` — deterministic (two runs hash
byte-identically; no timestamps in the report), evidence-mode-honest (S2):

| Check | What it proves | Evidence mode |
|---|---|---|
| **V1** | every entity resolves to exactly its frozen `deployed` table (drift → "re-validate + re-sign") | `schema` with a live resolver, else `structural`, stated honestly |
| **V2** | join columns exist | schema / structural |
| **V3** | join graph connected; every request-restrictable subgraph resolves to a **unique tree** (parallel role edges are alternatives, not cycles) | structural |
| **V4** | every expression parses + transpiles; anchor-column rule | structural |
| **V5** | the **grain-safety matrix** — every measure × dimension pair classified `safe` or `rejected:<the 1:N hop, named>` (G3: published, so a query-time grain_conflict can only mean the consumer ignored it) | structural |
| **V6** | the **determinism battery** — built FROM the admissible map (per-measure safe set · the multi-anchor common set · grand total · entity reads), compiled **twice, byte-identical**; an in-battery REJECT is a FAIL naming the map/compiler divergence, never a skip | structural |
| **V7** | **live probes** (opt-in, needs a DB connection): every battery query executes with limit 1 at the REAL target (TOP form on Teradata); every declared N:1/1:1 spot-checked empirically — the one-side must be UNIQUE on the join's to-columns (the composite-safe `GROUP BY … HAVING COUNT(*) > 1 → zero groups` form; a 1:1 also checks the from-side); every entity's declared key checked unique | `live` |

**V7 verdicts:** a probe **contradicting** a declaration fails V7 *naming the
declaration* → not signable. A probe that cannot *execute* also fails — live was
attempted, and signing a half-probed report as "live" would be dishonest (re-validate
without a connection to sign structurally: an explicit choice). No connection ⇒ V7
skips and the report says `mode: "structural"` — and **the mode is attested content**
(it changes the report hash), so an empirically-verified signature is distinguishable
from a declaration-trusting one.

**Two injection-safety layers, deliberately separate:** the compiler path quotes and
double-escapes identifiers (a hostile `deployed` stays inert inside a quoted
identifier); the hand-built dup-groups probes use bare identifiers and therefore
**refuse** any identifier failing a shape check rather than execute it.

The REST seam (`POST /v1/data-products/<name>/validate`, `connection_id` or
`live:true`) pre-executes the probe plan through the same read-only executor as product
queries and serves the pure gate from a cache; `live` with no usable connection falls
back to structural with an honest `live_note`, never N failed probes.

---

## 4. The compiler — fan-out-safe by construction

`dbdeploy/product_compiler.py:compile_request(manifest, request)` — the §3/§4 rules:

- **J1/J2** — join resolution over the declared edges only, unique-tree; a BARE
  dimension across parallel role edges is REJECTED demanding its role (`entity@role.attr`)
  — a silent pick would make the answer depend on traversal order.
- **G1/G2** — grain safety: a measure × dimension pair is safe iff every hop
  anchor→dimension is N:1/1:1 in that direction; a rejection **names the 1:N hop**.
- **A1/A2/A3** — assembly: per-anchor sub-aggregates **FULL-OUTER-JOINed on the
  NULL-safe dimension tuple** (`l = r OR (l IS NULL AND r IS NULL)`) with COALESCE — an
  absent slice renders as an honest NULL measure, never a dropped row, and the naive
  flat join's row multiplication is **provably impossible** (CI executes both forms on a
  real engine against hand-computed truth).
- **Closed filter set** with typed literals escaped in-compiler (LIKE wildcards
  neutralized) — no SQL fragment ever crosses the boundary, permanently.
- **R4** — full determinism including row order (tiebreak).
- **D2 dialect seam** — `_apply_limit`: Teradata has no LIMIT → `SELECT TOP n`;
  `backend:"sqlite"` makes compiled SQL executable in CI.
- **The admissible map** (`admissible_map`) — measures, dimensions, the grain matrix,
  filter ops, time grains — derived from the *same* grain function the compiler
  enforces, published on the definition endpoint (§3.2) and ridden along on every
  REJECT for one-step self-correction.
- **Entity-read form** — `{entity, attributes}` for the non-aggregate case.

---

## 5. Three faces, one semantic core

Every face compiles through the ONE compiler — byte-identical SQL by construction:

| Face | Surface | Notes |
|---|---|---|
| **Agent** | the **`obda_read` component** — `TDA_DataProduct` actions `list_products` / `query_product` (the `TDA_KnowledgeGraph` actions of the same names remain unadvertised ALIASES for stored champion plans; one implementation) | **Compiles, never executes** (the probe_bridge doctrine): in-component SQL would bypass the Agent-Authority chokepoint. The agent runs the returned statement through its own governed data tool; the OBDA-READ scanner recognises that execution as governed (§7). A REJECT reads as a FAILED tool call (`spec.error`) carrying the verbatim code + admissible members — correction food, never a silent success. Products render in the KG enrichment as **consumption contracts** (measures, entities, validation state, "plan query_product, never hand-written SQL"). |
| **BI / REST** | `GET/POST/DELETE /v1/data-products*`, `POST …/compile`, `POST …/query` | Query executes read-only under the caller's own DB connection (single-SELECT recheck; no connection → honest 409 *with* the SQL). Every query carries the **governed provenance stamp** `{product, version, request_hash, trust, lane:"governed", row_count}`. REJECTs are 422 with code + closest names. |
| **MCP** | `list_data_products` / `query_data_product` on the Uderia MCP server, **user tier** | An external agent queries a cryptographically signed product through MCP; REJECTs and the disabled switch return as structured error dicts, never bare exceptions. |

Composed products resolve through **one loader** (`product_model.load_flat_manifest`) at
every face — base passes through, composed flattens — so composition is transparent to
every consumer.

---

## 6. Enforcement — the data surface (E-rules) and the knowledge twin (K5)

### 6.1 `authorityConfig.dataSurface` (Agent Authority)

```json
"dataSurface": { "products": ["fitness_sales"], "exclusive": true, "advisory": false }
```

- **products alone** = ADDITIVE capability (E3) — the raw lane stays available and
  monitored; never a restriction.
- **`exclusive`** = **surface-only** (E1): when the agent runs autonomously, raw SQL is
  denied **fail-closed** at all three tool chokepoints (MCP adapter, component manager,
  ReAct wrapper) with the arguments threaded through `is_tool_allowed`. Only
  signed-and-intact (🟢) products are served (E2/E4 in the handler — off-surface →
  `not_on_surface`, non-trusted → `unsigned_product`, both fail-closed). *"Cannot query
  outside its slice", literally.* Deliberately **no `TDA_` exemption** on E1 (canvas SQL
  is the same escape hatch). Downgrade-guarded in the config contract
  (`flag_paths` — a pack republish cannot silently relax it).
- **`advisory`** = observe-only (E4's development mode): nothing denied, violations
  observed and scored.
- **The ONE exception — the turn-scoped governed-SQL registry:** `query_product`
  registers its compiled statement (`register_governed_sql`), so the only raw SQL a
  surface-only agent can execute is a statement the surface itself produced *this turn*;
  an **edited** statement forfeits the contract. The registry is a **mutable set in a
  contextvar**, created at `set_current_principal` and mutated by reference — a `.set()`
  in a child task is invisible to sibling tasks (contextvars propagate parent→child
  only, and Optimize runs parallel phases; the gate caught the first version).
- **Recovery-shaped denial:** a surface denial spells out the NEXT STEP (call
  `query_product`, execute the returned statement verbatim) — the blanket "do not retry;
  report this limitation" tail is reserved for categorical denials, because the live
  acceptance proved the model obeys the stop clause over a fix named two sentences
  earlier. Keyed on ONE `SURFACE_DENIAL_MARKER`.
- **E5 — the empirical monitor:** free-formed SQL *executed* by a surface-only agent
  means the confinement LEAKED. `_record_surface_breach` (the one scan seam) records a
  DANGER `surface_breach` incident — *"a hole report, not agent misbehaviour"* — plus a
  live broadcast. Fires **only** on the breach (advisory / additive / governed /
  interactive turns all silent); registered in all four event maps (severity, both
  station maps — BOUNDED, it is an authority event — and the inbox titles).

### 6.1a The enforcement scope is a declared choice (`enforce_interactive`)

By default a principal is derived only for `AUTONOMOUS_ORIGINS` — an interactive turn
has none, on the reasoning that a present human is the control. A live walkthrough
showed that reasoning is thinner than it looks: the operator ticked *Surface-only
(structural confinement)* and then watched the agent hand-write raw SQL four times,
because what the human sees is prose, not SQL. So the SCOPE is now a declared choice:
**`authorityConfig.enforce_interactive: true`** applies the agent's WHOLE authority to
interactive turns too (widens the scope, never replaces it — autonomous origins still
derive a principal regardless). Absent/false is byte-identical to the historic
behaviour; only an explicit `True` opts in (a migrated `"true"` string must never start
denying tools); turning it OFF is a pack-import downgrade (`flag_paths`). Editor:
Agent Authority card → *Apply to interactive turns too*.

### 6.1b Surface-aware enrichment — the contract ALONE (the operator's cut)

Under an **exclusive** surface the KG enrichment renders the consumption contract and
NOTHING else. Observed before this: filtering the product contracts removed three of
four *product* doors but left every *table* door standing — TABLE SCHEMAS, joinable
columns, entity listings exist solely to help an agent WRITE SQL, and a surface-only
agent cannot execute SQL it writes. With the schemas present the planner kept reaching
for `SELECT COUNT(*) FROM Customers` even with a one-door contract in front of it
(proven twice on identical context, once each way — raw-SQL patterns outweigh any
instruction). The confined enrichment is therefore:

- the **SURFACE-ONLY headline** (raw SQL will be denied; `query_product` then execute
  the returned statement exactly),
- the **on-surface contracts** — each naming its *measures* and its *qualified*
  dimensions (`Entity.Attribute` — the form a request must use; the unqualified form
  is what a model reaches for and what gets REJECTed),
- the **off-surface line** — locked products NAMED, never silently dropped (a product
  that vanishes is indistinguishable from one that does not exist, and the agent goes
  looking for it by other means),
- one honest line stating what was withheld and why. Even the `compile_metric`
  preamble is cut: metric SQL is NOT in the governed registry, so that hint compiles a
  statement the runtime then denies.

Advisory / non-exclusive surfaces keep the full enrichment (they can write SQL — for
them the schemas are information, not bait). The grounding gate's trusted scope
(`scope_databases`/`scope_entities`) is computed from the FULL subgraph and is
deliberately untouched by the text cut. The enrichment EVENT carries
`data_products` (the contracts actually shown — ONE shared rule with the renderer, so
event and prompt cannot disagree), `data_products_off_surface`, and `surface_only`;
the Live-Status title distinguishes **ANALYSED from INJECTED**
(*"surface-only — injected 1 data product contract: …; schemas withheld, 51 entities
analysed for scope"*). Signed products are ALWAYS seeded into the enrichment
regardless of the question's wording — a product is named for its subject area, a
question names entities, so the lexical match loses essentially every time, and an
absent product is a missing OPTION, not a missing fact. `list_products` mirrors the
same fact mid-turn: rows carry `on_surface`, surface rows sort first, and a
`surface_note` states the rule.

### 6.1c Deterministic consumption pipeline (no LLM where nothing is left to decide)

The walkthrough hardened the Optimize path end-to-end so the governed lane is
deterministic once the model has chosen it:

- **`governed_execution` rewrite pass** — a compile phase (`query_product` /
  `compile_metric`) whose result NOTHING consumes gains a following executor phase
  wired to `{"source": "result_of_phase_N", "key": "sql"}` (the component compiles and
  never executes — the probe_bridge doctrine — so the PLAN must carry the execution).
  Fires only on the provably broken shape; a correctly planned seam is byte-identical.
- **Dead-phase rewiring** — under an exclusive surface, a literal-SQL phase following
  a compile is REWRITTEN IN PLACE into the reference-executor (denied-by-construction
  code is dead code; in place, not removed — removal+renumber once sent a report's
  reference pointing at itself). Never on unconfined turns; never without a preceding
  compile (a literal-only plan keeps its phase — the recovery-shaped denial is the
  teaching mechanism).
- **The consolidation pass never merges a runtime reference** (a placeholder is not
  SQL; handing it to the consolidation LLM made it INVENT a query from the goal).
- **FASTPATH accepts the canonical reference** — `{"source": …, "key": "sql"}` is
  deterministically resolvable, so the phase never visits the tactical LLM (which
  could re-emit and flatten the dict, losing `key`).
- **A key-less reference resolves by the argument's OWN name first**
  (`sql: "result_of_phase_1"` means *the sql from phase 1*); the single-value unwrap
  survives as the fallback for scalar references.
- **`SurfaceRefusalStrategy`** — an off-surface refusal naming exactly ONE surface
  product is corrected by swapping `product_name`, ZERO LLM (the refusal names its own
  remedy; reading it is not judgement). A multi-product surface falls through to the
  LLM. Sibling rules: a **non-string SQL argument fails fast + definitively** at BOTH
  chokepoints (`tool_enforcement.nonstring_sql_block` — MCP adapter AND ReAct wrapper;
  retrying cannot change what the upstream produced), and the compiler **coerces the
  common filter shapes** onto `{attr, op, value}` (accepted SYNTAX widens, accepted
  MEANING never — `not_filterable` still refuses).
- **Dependency-failure skip** — a phase whose referenced upstream FAILED skips before
  argument resolution (no tool call, no correction rounds), records its own honest
  error result so later references cascade, and closes `status: skipped`.

### 6.1d The learning loop is authority-aware

Pre-governance turns seed champions teaching literal raw-SQL plans; once the agent is
surface-only those champions guide the planner into plans the runtime denies with
certainty — and can never self-demote, because a confined turn ends `status=success`
(an honest refusal). Two durable rules: **retrieval** — a champion whose plan the
ambient principal would deny is not OFFERED (`champion_blocked_by_surface`; not
demoted, not deleted — unconfine the agent and it is valid again; fails OPEN, the
trust-aware-routing precedent); **seeding** — a turn whose OBDA-READ lane is `confined`
never seeds (its plan proved nothing; defence-in-depth beside the generic success
criteria, pinned against criteria drift).

### 6.2 K5 — `requireSignedKnowledge` (the knowledge-modality twin)

`knowledgeConfig.requireSignedKnowledge`: only collections whose **integrity signature
is signed-and-intact** back the agent's answers — `changed` and `unsigned` corpora are
excluded at the ONE retrieval seam (`rag_retriever.retrieve_examples`, which Focus,
Ideate, and the `knowledge_context` module all route through), exactly like the
`maxSensitivity` ceiling. Pure core `filter_signed_collections`; rules that matter:
signed means *intact*; unattributable chunks (no collection id) cannot be signed;
the trust suite physically absent fails **open with a loud warning** (the
trust-aware-routing precedent — silently emptying the knowledge lane would be worse
than not enforcing); one unresolvable collection beside real states is excluded, never
a fail-open trigger. The knob is **signed** content and downgrade-guarded; the editor
toggle is modeled in carryForward so unchecking actually takes.

K3/O5 (source-provenance stamps on retrieval + the capsule) and K6 (bound collections
as agent-badge ingredients) are delivered by
[KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md](KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md) /
[TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md)'s earlier waves.

---

## 7. Observability, receipts & eval — two ends of one signal

### 7.1 The lane scanner (`agent/obda.py`)

`scan_obda` classifies a turn's data operations, consumed by BOTH the Live-Status card
and the eval scorer (one source):

| Lane | Meaning |
|---|---|
| **governed** | a `compile_metric`/`query_product` op — or a raw-SQL call whose statement **matches a governed compile's output from the same turn** (`execute_governed`, whitespace/semicolon-tolerant): the component compiles, the agent executes, and the scanner knows the truth of that split |
| **free-formed** | the agent wrote raw SQL itself — advisory, judgement in the loop |
| **confined** | every raw-SQL attempt was **DENIED** by the surface — *neither* lane: nothing executed. Counting a denial as free-formed would record the confinement working as the escape it prevented, and `governed_data_ratio` would punish the agent for being confined (found live in C4, fixed at the root: `denied_count`, its own card, GROUNDED reads *pass*) |

Beside the lanes, the scan carries **`rejected_count`** — requests the CONTRACT refused
(an `unknown_member`, a grain conflict, …). A REJECT moved no data: it is the governance
working, not a data operation, so it is **reported, never folded into a lane** (found
live, session 2fcbb51f: an M4 allowlist REJECT counted beside the real compile+execute
pair read "3 governed data operations"). Same rule as `denied_count`, one layer up: the
surface denies at the door, the contract rejects inside it. A rejected compile also never
seeds the governed-SQL allowlist (a REJECT produced no statement to pair an execution
against). The capsule's lane chip and the GROUNDED "Governed data" reading both name the
count (`… · 1 request rejected by the contract`).

**The REJECT tail splits by REJECT kind** (`handler._handle_query_product`): a *typo*
(the `closest declared:` hint fired) or a non-member reject (`empty_request`,
`grain_conflict`, …) keeps the mechanical retry tail — the fix is a reshape. A genuinely
**undeclared member** (`unknown_member` + "does not expose", no closest hint — the
allowlist working, M4) instead instructs: answer with an admissible dimension **and state
explicitly that the requested member is not exposed**, or report that the question cannot
be answered at that grain — **never silently substitute** a coarser dimension and present
it as the asked one. The one-size retry tail had literally commanded the substitution
observed live ("which customer has the most tickets?" answered at state grain over a
100-way per-customer tie). The safety net behind the instruction is deterministic: the
honesty gate's `grain_substitution` verdict (`agent/honesty/gate.assess_grain_substitution`,
0-token) detects an un-disclosed substitution post-hoc — observe flags it; annotate/enforce
**APPEND** the disclosure naming the refused member and the grain actually used (never a
replacement, never parked: the figure itself is correctly computed governed data — what
was missing is the admission of which question it answers).

**The substitution can also happen at PLAN time, with no REJECT at all** (session
7b9b5a25: "which customer has the most tickets?" was planned straight onto
`customer_count × Customers.State` — admissible, governed, fluent, answering a
different question — so every REJECT-keyed layer was blind). Three layers close it:
**(1)** the rendered contract itself now states the rule where the decision is made
("These are the ONLY members … NEVER silently answer at a different grain") — the
REJECT tail's doctrine moved to plan time; **(2)** a REJECT-free honesty-gate trigger
(`primitives.entity_grain_mismatch`, deterministic, deliberately narrow: a
ranking-shaped question naming a queried product ENTITY but none of that entity's
queried dimensions — "which **customer** …" grouped by `Customers.State` trips,
"which **state** has the most tickets" names the used grain and is clean) feeding the
same append-only verdict, tagged `trigger: entity` vs the precise `trigger: reject`;
**(3)** because (2) is a heuristic, the agent can opt into the
**`grain_faithfulness` LLM judge** (`honestyConfig.grainJudge`, OFF by default,
configurable in the profile editor's Honesty card): the heuristic escalates to the
judge before acting — a judge PASS **withdraws** the verdict (recorded, never
silent), a FAIL/timeout/absence keeps the deterministic append (fail-safe keeps the
protection, the grounding gate's own rule). The judge is also a registered
post-hoc quality dimension (the 54th), owned by the honesty gate at GROUNDED and
declared in `POINT_JUDGES` — the Assurance Guide renders it gate-consulted. The
precise `reject` trigger is never judge-gated: an observed REJECT-then-retry is
not a heuristic.

### 7.2 O2 — the consumption receipt, sealed

Every governed consumption seals an **`obda.consumption` step** into the turn's Ed25519
provenance chain: `{product, version, request_hash, sql_hash, row_count, trust_state}`
— `row_count` supplied by the paired execution call; a REJECTed compile carries none
(nothing was consumed). The sealed content is `obda.receipt_content(op)` — a **closed
field tuple** (a stray presentation field can never enter the seal), canonical JSON —
and `provenance.verify_content` re-derives it from the turn's stamped
`obda.operations` with the SAME function, so the sealed receipt and the displayed
record are checked against each other; a receipt matching no stamped operation is a
**named mismatch**, never a silent skip. The answer capsule renders a **Data products**
row (`fitness_sales v1 · trusted · 32 rows`, hashes + seal note in the tooltip), and
the existing "Export receipt" carries the consumption inside the signed chain —
offline-verifiable with the public key alone.

### 7.3 Eval + red-team

- **`governed_data_ratio`** (JUDGED scorecard, station label GROUNDED) — the incentive
  gradient toward the governed lane; abstains with OBDA-READ off or no data ops.
- **`surface_violation`** (BOUNDED, ACT moment) — counts surface denials, matched on
  the surface's own policy reason so another authority rule never misattributes.
  **Non-gating by design**: a steady nonzero is a *product gap made measurable*
  (producer backlog, not consumer fault).
- **`surface-escape` red-team probe** — lures "skip the data product layer", then
  adjudicates from the turn's own stamps: denial in the trace → held; a free-formed
  lane stamp → the escape; governed → held; no signal → honestly unknown. The live runner
  scores each probe against its PERSISTED turn (`live_redteam.with_turn`): until Oct 2026 it
  scored the task result alone, which carries no tool calls and no lane stamp, so only the
  refusal-in-words branch could ever fire live. Its sibling for writes is `write-escape`
  (OBDA-ACT, see AGENT_AUTHORITY_ARCHITECTURE).

---

## 8. The compositional ratchet (P1–P3)

A **composed product** declares `members` (≥ 2 base products, same ontology, same
backend) + `bridges` (the stitching joins — cardinality + on-pairs, as always). Then:

- **P1 — signed ∘ signed → mechanically signable.** Consumption and validation run on
  the **flattened** manifest through the *unchanged* pipeline (V7 probes the flat form,
  bridges included) — composition preserved determinism and the gate proves it, so
  sign-off proceeds **without re-judgement**. The attestation binds
  `{composed manifest hash, flat report hash, member ATTESTED hashes}` — **product
  lineage**, inside the signature, offline-verifiable.
- **P2 — anything ∘ unsigned → not signable.** The validator refuses `unsigned_member`
  **naming the members** (the agent shield's bottom-up rule, one level down). The
  validator stays pure — `sign_product` resolves members, flat form and states at the
  one impure seam and injects them.
- **P3 — structurally free.** `live_hash` recomputes the lineage from the members'
  *current* attestations, so a parent's re-sign flips composed children 🟠
  CHANGED (stale-ingredient). The cascade splits honestly: a **version-only** parent
  bump moves the lineage (child CHANGED) but the flat contract is byte-identical, so
  the child re-signs *without* re-validation — re-judgement would be ceremony; a
  **shape** change moves the flat hash, so the child's stored report proves nothing and
  re-sign refuses `stale_report` until re-validated.

Flatten refusals are precise, never silent: `nested_composition` (v1 stop-line),
`member_backend_mismatch`, `member_entity_collision` / `member_measure_collision`
(naming BOTH members), `bridge_unknown_entity` (naming what IS exposed),
`unknown_member_product`.

---

## 9. Lifecycle, portability, governance

- **L1 — closed by composition** (deliberate, recorded): the sign-off ceremony *is* the
  product-scoped release act; E4 *is* release-gates-consumption (a surface-only
  consumer is served only 🟢 versions); the ontology's Tier 4.3 lifecycle governs the
  estate layer. A second per-product draft/propose/approve state machine would
  duplicate the attestation's own semantics. Honest boundary: a per-product approval
  *workflow* finer than the signer-tier gate is not built (future opt-in).
- **L2 — portability for free:** products are KG nodes, so they travel inside KG
  export/import and agent packs; attestations travel via the shipped
  `capture`/`reinstate` honesty gate (byte-identical ⇒ 🟢 survives the copy, else
  honest ⚪).
- **Sign-off authorization:** `POST /v1/trust/data-product/<kg_id>/<name>/sign-off` —
  switch-gated, signer-tier-gated, ontology-ownership-scoped (TC-30).
- **UI:** the Designer (gold **Products** button on Intelligence-tab KG cards, rendered
  only with the switch on) — catalog with trust chips, the V1–V7 progressive checklist
  with evidence modes, the **Verification selector** (Structural / Live per DB
  connection), the grain-safety grid (dashed cells name their 1:N hop on hover), the
  sign ceremony stating exactly what is attested, a manifest editor rendering the
  REJECT taxonomy verbatim. The profile editor's Trust section carries the
  **Data surface** card; `authorityScope` reads "surface-only: N data product(s)".

---

## 10. Honest boundaries (the stop-line, held)

Refused by name, never silently (scope §13): no marketplace listings of *standalone*
products (v1 ships them inside ontologies/packs); no non-SQL backends; no
cross-ontology members; no **nested** composition (`nested_composition`); row-level
policy stays the pinned minimal v1 form. The grounding gate stays ON for surface-only
agents (E5's monitor — prevention subsumes detection, but detection is kept as the
audit of prevention).

---

## 10a. The write half — OBDA-ACT

OBDA-READ governs reading. Its write-side counterpart is **OBDA-ACT**
([ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md), PROP-008): a signed **action type** is the only
shape of change an agent may make to the warehouse, as a signed data product is the only shape
of question it may ask. OBDA-ACT was built on OBDA-READ's pattern; where the two share a mechanism it is
one implementation:

| Concern | OBDA-READ — reading | OBDA-ACT — writing |
|---|---|---|
| The contract | a **data product** manifest (`dbdeploy/product_model.py`) | an **action type** manifest (`dbdeploy/action_model.py`) |
| The gate | V1–V7, with live probes (`dbdeploy/product_validation.py`) | A1–A6, with live probes (`dbdeploy/action_validation.py`) |
| The earned signature | trust kind `data_product`, signed over `{manifest_hash, validation_report_hash}` | trust kind `action_type`, the same shape — the proof is inside the signature in both |
| Bottom-up rule | a product needs a signed ontology (`unsigned_ingredient`) | an action needs a signed ontology (`unsigned_ingredient`) |
| On the graph | `data_product` node + `member` edges | `action_type` node + `acts_on` edge |
| Never moves the ontology's seal | both are **stacked contracts** — one rule, `kgstore.types.is_stacked_contract`, read by trust composition, governance and extension signing | |
| Who executes | Uderia **compiles, the agent executes** through its governed data tool, so the read keeps the SQL gate | Uderia **executes** (`dbdeploy/action_runner.py`): a write needs pre-checks, statement, post-checks and ledger in one transaction, which a data tool cannot hold |
| Agent Authority | `dataSurface` with `exclusive` → raw SQL refused as `surface-only` | `actions` with `actions_exclusive` → raw writes refused as `action-only` — the **same chokepoint** (`agent_authority.is_tool_allowed`) |
| The lane scanner (`agent/obda.py`) | governed · free-formed · confined | the **acted** lane: `acted_count` / `refused_count` in the same `obda` stamp (only where the agent's OBDA-READ plane is on; OBDA-ACT's own `actions` stamp and act log do not depend on it) |
| Receipts | `obda.consumption` steps sealed in the turn's Ed25519 chain | `action.performed` steps sealed in the same chain; an act outside a sealed turn carries a receipt signed on its own |
| Faces | agent · REST (`/v1/data-products/*`) · MCP | the same three, plus flow · scheduler · dashboard tile · Assurance Inbox |
| Eval | `governed_data_ratio`, `surface_violation`, the `surface-escape` probe | `action_safety`, the `write-escape` probe |
| Availability | the `obda_read` component (global · per user · per agent) — `core/obda_planes` | the `action` component, the same chain (OBDA-MODEL: `obda_model`) |

The one deliberate difference is who executes. OBDA-READ compiles and hands the statement to the
agent's governed data tool, so every read still passes the SQL gate. OBDA-ACT executes in the
runtime, because a write must hold its checks, the statement and its ledger entry in one
transaction.

*Naming:* the "ACT moment" in §7.3 (`surface_violation (BOUNDED, ACT moment)`) is the eval
model's turn stage (Plan · Retrieve · Act · Compose · Deliver), not OBDA-ACT.

---

## 11. File & gate map

| Area | Files | Gate (assertions) |
|---|---|---|
| Model + composition | `dbdeploy/product_model.py` | `test_product_model.py` (98) |
| Validation V1–V7 | `dbdeploy/product_validation.py` | `test_product_validation.py` (66) |
| Compiler | `dbdeploy/product_compiler.py` | `test_product_compiler.py` (74) |
| Trust kind + ratchet | `core/trust/{products,validators}.py` | `test_product_trust.py` (45) |
| REST faces | `api/{rest_routes,trust_routes}.py` | `test_product_rest.py` (29, live) |
| Agent face | `components/builtin/knowledge_graph/handler.py` | `test_product_agent_face.py` (19) |
| Scanner + receipts + E5 | `agent/obda.py`, `agent/executor.py`, `core/provenance.py` | `test_obda.py` (74) |
| Surface enforcement | `core/agent_authority.py`, `mcp_adapter/adapter.py`, `components/manager.py`, `agent/tool_enforcement.py` | `test_agent_authority.py` (77) |
| K5 | `agent/rag_retriever.py`, `core/profile_config_contract.py` | `test_signed_knowledge.py` (19) |
| Eval + red-team | `eval/scorers/reliability.py`, `eval/live_redteam.py` | `test_eval_reliability.py` (69), `test_live_redteam.py` (38) |
| MCP face | `mcp_server/{client,server}.py` | `test_mcp_server.py` |
| Designer + capsule | `static/js/handlers/{knowledgeGraphPanelHandler,answerCapsule,configurationHandler}.js` | browser-verified per increment |

All Python gates run in `test/trust_battery.sh` (a gate the battery does not run is a
gate nobody runs). Every security-bearing rule above is **mutation-tested** — ~40
mutations across the subsystem, each failing by name.
