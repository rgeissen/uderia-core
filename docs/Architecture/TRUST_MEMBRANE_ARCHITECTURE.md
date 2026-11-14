# Trust Membrane Architecture (Tier 5.2)

**Status:** implemented and **merged to `main`** (2026-07-03); last verified against code 2026-10-30. This is the
*architecture* reference — how the shipped system works. The *design* rationale is
`docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md`; the
per-artefact validator model is `TIER_5.2_VALIDATORS.md`;
marketplace portability is `TIER_5.2_PORTABILITY.md`.

---

## 1. The idea in one paragraph

Trust is a **visible, cryptographically-verifiable badge on an artefact**, and the agent is
the primary unit: **a signed agent is trusted exactly when its whole composition is** — its
active ontology, its active skills, and the knowledge/planner **repositories it consumes**
must each be signed 🟢. Trust is *computed, never re-derived*;
sharing stays fully open (badge, not gate). Four states: ⚪ **unsigned**, 🟢 **trusted**
(signed & intact), 🟠 **changed** (signed, but content drifted since), and ◒
**deployment_unattested** (publisher-signed and intact, but *this* instance's model, tool
catalog and connector opt-ins have not been attested here — see §3b). Default (no
attestations) = a byte-identical baseline — every artefact is ⚪.

## 2. Module map (`src/trusted_data_agent/core/trust/`)

| Module | Responsibility |
|---|---|
| `attestation.py` | Pure core: `TrustState`, `content_hash`, **id- & order-independent** `composition_hash`, `resolve_state`, `resolve_agent_trust`, `agent_signable` (bottom-up rule), the validator registry, `ValidationVerdict` + `SignBand` (clean/partial/blocked). Zero I/O. |
| `scanners.py` | Deterministic 0-token safety scanners: `content_safety_scan` (injection), `code_safety_scan` (AST), `sql_safety_scan`, `markup_safety_scan` (XSS), `aggregate_verdict`. `ScanResult` splits findings into blocking vs caveats. |
| `validators.py` | The **13 per-kind** validators (ontology · skill · **agent** [posture, Jul 2026] · extension · knowledge · planner · flow · dashboard · canvas · **assurance_module** [PROP-003] · **data_product** [OBDA-READ, the earned signature] · **ontology_extension** [PROP-008 A5] · **action_type** [PROP-008 B1]), each returning a `ValidationVerdict`, registered by `kind`. `agent_pack` remains the **aggregate/composition** path with no per-kind `register_validator`. 13 per-kind + 1 aggregate = the 14 signable categories in §3. |
| `content.py` | **Canonical** per-kind content + `version_hash` resolver (skill/ontology/extension/knowledge/planner/flow/dashboard/canvas). One definition, shared by sign-off, badge state, and portability. |
| `composition.py` | A real profile → its ingredients (active KG + active skills + the knowledge/planner repositories it consumes) → live `AgentTrust`. Owns **`agent_version_hash`**, the single definition of an agent's **portable** signable content (ingredients + posture + capability), and folds in the deployment half via `compose_agent_state`. |
| `deployment.py` | The **deployment** half (§3b) — resolved LLM identity, reachable MCP tool/prompt catalog, per-profile connector opt-in. Never travels with a pack; `describe_drift` names what changed so re-attesting is informed. |
| `extensions.py` | Tier 1 — the **ontology extension** (PROP-008 A5): one extender's published authored layer, its content hash, the lineage-bound signable content, `base_mutations`, the composition conformance, the person-only `publish_decision`, `sign_extension`, and the claim capture/reinstate that travels with a graph. |
| `actions.py` | OBDA-ACT (PROP-008 B1) — the **action type**: a signed write contract on the ontology. Signable content binds the manifest AND the A1–A6 report; `sign_action` is the one gate + sign seam; `live_hash` recomputes from the node so an edited manifest or a replaced report reads 🟠 CHANGED. |
| `pack.py` | Agent-pack composition resolver — a pack is "an agent whose ingredients are its members". |
| `store.py` | The `trust_attestations` table (one CURRENT attestation per artefact — and per **owner** for agents), the append-only `trust_attestation_history`, the `trust_signing_keys` registry (all self-applying, §7c), Ed25519 sign/verify reusing the deployment's provenance key, **content-bound signatures** + the **signer public key** carried for cross-instance verification. |
| `evidence.py` | PROP-009 — a turn's **bill of materials** (every signed part it ran with), its contextvar, and the ONE content function the `trust.bom` chain step is sealed and verified through. See [EVIDENCE_ARCHITECTURE.md](EVIDENCE_ARCHITECTURE.md). |
| `portability.py` | Marketplace `capture` / `reinstate` / `claim_state` + the honesty gate. |
| `renewal.py` | Signature renewal after a sync that changed content — the owner's per-repository standing policy (carry-forward of acknowledgements, auto-acknowledge on changed documents, renewal of the repository's and its agents' signatures as the `renew` event; never a first signature). Shared with the sign-off route: `sign_knowledge_repository` is the ONE knowledge sign act. |
| `validators` registry | `register_validator(kind, fn)` — new kind = one function, no forks. |

REST seam: `api/trust_routes.py` (`GET /v1/trust/agents`, `GET /v1/trust/agent/<id>`, `GET/POST /v1/trust/artifact(s)`,
`POST /v1/trust/<kind>/<id>/sign-off` (one route per kind — data products, actions and ontology extensions are addressed
`<kg_id>/<name>`), `DELETE /v1/trust/<kind>/<id>/signature`, `POST /v1/trust/agent/<id>/attest-deployment`, `POST /v1/trust/verify`,
`GET /v1/trust/history/<kind>/<id>`, `GET /v1/trust/unassigned` + `POST …/assign`, `GET /v1/trust/keys`). UI: `static/js/handlers/trustBadge.js`.

## 3. The validator model (all 14 categories)

Every signable kind maps onto one **closed archetype set** — the two shipped poles
(conformance, content-safety) plus code-safety, query-safety, provenance-integrity, and
aggregate. Each validator is pure, deterministic, 0-token, and maps its result onto the
harmonised band **clean · partial · blocked**.

| Artefact | `kind` | Signable content | Archetype |
|---|---|---|---|
| Ontology (KG) | `ontology` | entities + relationships | conformance (Tier 4.3 gate) |
| Skill | `skill` | instruction text | content-safety |
| Agent (profile) | `agent` | composition over ingredients **+ safety posture** | aggregate + posture |
| Extension | `extension` | handler source + manifest | code + content-safety |
| Knowledge repo | `knowledge` | `sorted(per-doc hash)` + embedding | integrity + content-safety |
| Planner repo | `planner` | the **pack-owned** cases over an instance-invariant view (baseline); **every** case when the owner has **locked** it; the raw per-case hash only for a legacy repo no pack ever stamped — §3a.1 | integrity + query-safety |
| Agent pack | `agent_pack` | composition over members | aggregate |
| Flow | `flow` | node/edge logic (positions excluded) | code + query + content |
| Dashboard | `dashboard` | tile structure + data source | query-safety |
| Canvas | `canvas` | markup | markup/XSS + content |
| Assurance module | `assurance_module` | pack manifest + module content + **capability honesty** (a claimed tier must be provable) | code-safety + tier proof ([ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](ASSURANCE_MODULE_PLANE_ARCHITECTURE.md)) |
| Data product | `data_product` | `{manifest_hash, validation_report_hash}` (+ `member_hashes` lineage for composed) — **the proof is inside the signature** | the **earned signature**: five machine-readable refusals, the V1–V7 gate incl. live probes ([OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md)) |
| Ontology extension | `ontology_extension` | `{extension_hash, base_attested_hash}` — the extender's **published** authored nodes + their edges, and the base's attested hash (the lineage) | five refusals: `not_found` · `nothing_published` · `base_mutation` · `nonconformant` · `unsigned_ingredient` (PROP-008 A5) |
| Action type | `action_type` | `{manifest_hash, validation_report_hash}` — the parsed write contract and the A1–A6 proof it passed; the node, its `acts_on` edge and its ledger `ddl_artifact` are excluded from the ontology hash | five refusals: `invalid_manifest` · `unvalidated` · `stale_report` · `gate_blocked` · `unsigned_ingredient`; structural evidence signs partial (`verified_declared`), live evidence clean (`verified_write`) (PROP-008 B1) |

**The authored-layer exclusion (PROP-008 A5, load-bearing):** `kg_content_hash` also
excludes every `layer: authored` node and every edge touching one. An extension is tier 1
on a signed graph — one extender's layer, keyed `extension_id = ext-<user uuid>` — signed
on its own with the base's **attested** hash inside its content. So an extension write
never moves the base's seal, a base re-sign flips each extension `changed · base moved`
(the mover is named from the lineage the signature recorded), two extenders flip
independently, and an agent grounded on base + extensions carries each published
extension as an ingredient (bottom-up rule, worst-of, the drift line naming the mover;
gated on the OBDA-MODEL component — `obda_planes.plane_enabled("model", …)`, the plane is a
component, there is no master switch — so an agent without it gains no signature field). Only a
**published** node is content; a draft is never signed, never an ingredient, never in the
released view and never travels. A payload carrying extension X replaces X on resync /
install and keeps every other layer; an edge the resync drops is stamped on the authored
node (`dangling_edges`) and named by the extension's conformance. The base's own publish
gate judges the base only. Publishing is a **person's** act (§13).

**The ontology-hash product exclusion (load-bearing):** `kg_content_hash` excludes
`data_product` nodes and `member` edges — the ontology's signable content is the
*knowledge model*; a product is a contract **stacked on** it, protected by its own
attestation. Without this rule, authoring a product flipped its own ontology CHANGED
and forced a rubber-stamp re-sign loop (found live, fixed at the root — OBDA-READ Phase A).

Full detail: `TIER_5.2_VALIDATORS.md §6`. Config artefacts
(MCP servers, LLM configs, vector/graph backends) are **not-attestable by design** — their
content is credentials, and *trust ≠ access*.

### 3a. What counts as an ingredient

An agent's ingredients are its **active ontology**, its **active skills**, and the
**knowledge/planner repositories it consumes**.

Repositories earn their place because the compositional promise — *a signed agent contains
only signed ingredients* — is otherwise false for two whole agent classes. A Focus agent
**is** its corpus and an Optimize agent is steered by its champion cases; while repositories
sat outside the composition, an agent with a bound corpus and no KG resolved with
`ingredients: []`, so the bottom-up gate had nothing to gate on and signed instantly over an
unscreened corpus, a corpus edit could not flip the badge, and re-pointing the agent at a
different corpus disturbed nothing.

Applicability **mirrors the runtime rather than adding a rule** — attesting an artefact the
agent never consults would make the badge claim more than it verifies:

| Ingredient | Counts when |
|---|---|
| `ontology` | it is the profile's active KG (`kg_profile_assignments`); for an agent whose `componentConfig.knowledge_graph.groundOn` is `released` (Tier 4.3 Model B) the CONTENT hashed is the graph's **released view** (`<kg_id>::released`) — the seal describes what the agent reads, so a draft edit does not move a released-mode agent's badge and a new publish does; the ingredient keeps the live `kg_id` (the attestation key) and names the view in `released_view` |
| `skill` | the `skillsConfig` entry is active |
| `knowledge` | the collection is bound, the owner can read it, **and** the runtime would retrieve it — **per class**: `rag_focused` always (Focus retrieval is unconditional; `enabled` is ignored by the engine), every other class only when `knowledgeConfig.enabled` is truthy (runtime default **false**, mirroring `get("enabled", False)` in the engines) |
| `planner` | the collection is bound, the owner can read it, **and** the profile is a planning class (`tool_enabled`/`genie`), matching `planner.py`, `agent_pack_manager` and the ontology projection |

**A binding the owner cannot read is not an ingredient (Oct 2026).** `_repo_ingredients` applies
the retrieval gate's own rule (`CollectionDatabase.can_user_read`): a bound collection the owner
cannot read is runtime-inert, so it is logged once and skipped like a dangling binding. Counting
it produced a sign-gate block the owner could never clear — the bootstrap template had shipped
the admin's planner id (`ragCollections: [1]`) into every account; that binding is also healed to
the user's own default at login (TC-37; `test_planner_binding_heal.py`).

Kind is decided by each collection's own `repository_type`, never by which field named it —
`knowledgeConfig.collections` and `ragCollections` overlap.

**Empty and unreadable are deliberately different outcomes.** A repository with no content
has nothing to attest and is omitted, so an empty default planner repo cannot make every
agent unsignable. A repository that demonstrably holds content whose hash could not be
computed is emitted **`unresolved`** and blocks: silently omitting it would reopen exactly
the gap repositories close, this time under a transient read error. This holds for **both**
repo kinds: `planner_cases` returns a `None` hash when any case file is unparseable (a
corrupt case must not silently shrink the signed set) and `planner_has_files` is the
independent existence probe, so a populated-but-unreadable planner repo blocks rather than
vanishing. A **per-collection** failure emits that one repo as `unresolved` and continues —
it can neither silently drop the repo nor abort the loop and drop every repo after it.
`IngredientRef` carries the flag explicitly and one shared `ingredient_state()` resolves it
for **both** the badge and the sign gate, so what you see and what blocks can never
disagree. A binding to a deleted collection is warned and skipped — a configuration fault,
not an ingredient.

### 3a.1 What a repository signature covers — learning stays outside the seal

A planner repository is a **learning** artefact: the seeding loop writes a champion case
after every successful turn. A signature over *every* case would therefore break through
correct operation — a signed agent answering well would flip its own seal 🟠 — and a
tier-gated owner could never repair it. So `content.planner_cases` selects one of two
formulas by **evidence, never by a flag**:

| Formula | Selected when | Covers |
|---|---|---|
| **baseline** | any case carries the `uderia_pack_ref` ownership stamp (every case a pack writes is stamped at all five pack write sites) | ONLY the stamped cases, over an instance-invariant view (`case_content_id`: instance-local ids and the three operational fields normal use writes back — vote, champion election, replay counter — are stripped) |
| **locked** | the owner set `trust_locked` on the collection (`PUT /v1/rag/collections/<id> {trust_locked}`, owner-only, planner only) | **every** case over the same canonical view — and the seeding loop **never writes into a locked repository**, whoever owns it: the lesson lands in the agent's own learning repository when it has one, else the user's own default planner for that server, so "frozen" never means "fragile" |
| **legacy** | no case is stamped | the original raw per-case hash, so no attestation made before the stamp existed moves |

Consequences the reader needs, on both sides of a share:

* **A case added by use does not move the seal** (baseline). A case added by hand, a vote,
  a re-elected champion: none of them are in the hash. The signature says the one thing a
  consumer needs — *the shipped cases are intact* — and what the agent learned locally is
  deliberately out of scope.
* **Changing a shipped case moves it**, for the publisher and for everyone holding the
  collection: a pack rebuild, an edit or a removal of a stamped case changes the baseline;
  the publisher's planner and every agent composed on it read `changed` until the
  publisher re-signs; a republish in place carries the new signature to subscribers through
  the pack sync fan-out. The consumer of a REFERENCE cannot repair it — the content is the
  publisher's.
* **A subscribed repository is a VIEW of the publisher's collection, not a copy**
  ([MARKETPLACE_ARCHITECTURE.md §1](MARKETPLACE_ARCHITECTURE.md)) — the subscriber's card
  shows the publisher's collection id and store. Its trust state is therefore the
  publisher's, at the same moment, and the **Locked** pill on the subscriber's card reports
  the *publisher's* opt-in, never the subscriber's (who cannot write to it under any
  setting). A forked repository is a copy: it hashes on its own, and the fork's
  attestation is reinstated only while its content is byte-identical to what was signed.
* **What the subscriber's agent learns is read back.** The subscribed agent is bound to the
  view, and its lessons cannot land there, so they land in the subscriber's own default
  planner for the session's MCP server — and retrieval consults that repository too
  (`rag_retriever.read_back_own_learning`, the mirror of the seeding fallback: it widens the
  agent's planner allowlist exactly when a bound planner is a view or locked, never creates a
  collection, and leaves the allowlist unchanged otherwise). The subscriber's agent therefore
  learns privately on top of the publisher's shipped set; the publisher never sees it.
* **Locked answers a different question than Subscribed.** *Subscribed* = you cannot write
  here. *Locked* = the owner's own everyday use cannot grow it either, and the seal covers
  all of it. Unlocked (the default), the owner's learning quietly adds cases that every
  subscriber inherits on their next query, without touching anyone's seal.

**Where an Optimize agent learns, and what it reads (Oct 2026).** Three rules from the Optimize
campaign change the runtime around the seal without changing the seal:

* **Its own learning repository** (`core/agent_learning.py`). A planner collection carrying the
  agent's id in `collections.learning_profile_id` is that agent's notebook: new Optimize agents
  get one at creation, existing agents opt in (`POST /v1/profiles/<id>/learning-repository`).
  Lessons go only there (a locked one is frozen), it is read beside the bindings, and it is NOT
  bound in `ragCollections` — so it is never an ingredient and learning never unsigns the agent.
  Agents without one keep the shared default notebook (and `read_back_own_learning` above).
* **Only what it binds** (`planner.planner_binding_scope`). Nothing bound means no champions —
  it had read every repository its owner could, beyond its signed composition. The explicit
  wildcard `"*"` still means every accessible repository.
* **A signed seed replays like a locked one** (`planner.planner_replayable`). A seed asked word
  for word is replayed verbatim (0 planner tokens) when the repository is locked **or** its
  signature still matches its content (`resolve_state == TRUSTED`); a case learned after signing
  moves the hash, so a learned case is never replayed under a signature that does not cover it.

Gates: `test_agent_learning.py`, `test_planner_binding_scope.py`, `test_signed_seed_replay.py`.
Rule: [RAG.md](../RAG/RAG.md) → "Where an agent learns".

The worked walkthrough, publisher and subscriber side by side, is
[AGENT_TRUST_AND_ASSURANCE.md §3.5a](../manuals/AGENT_TRUST_AND_ASSURANCE.md).

### 3a.2 Planner Health — the gate names the case it refused (Nov 2026)

A champion case is replayed verbatim on a retrieval hit, so the planner validator is where a
poisoned or wrong case is stopped. It scans every case and reports per case, not per
repository: `validators.planner_case_findings` is the ONE per-case reading (case id, question,
labels, the argument and an excerpt of the text that tripped the scanner, acknowledged or not),
and `planner_validator` is built on it. Blocking labels are destructive, mutating or stacked SQL
and prompt injection; a SQL comment is a caveat.

* **The verdict names the cases.** A refused signature lists each flagged case by its question,
  and the Trust Circle's SIGNED evidence carries the finding on the blocker
  (`IngredientRef.finding`).
* **The owed acts are in the inspect view (Planner Health).** *Remove* deletes the case; one
  resolver (`RAGRetriever._stored_case_id`) handles both stored-id conventions (a learned case is
  `case_<id>`, a template-populated seed its bare `<id>`). *Acknowledge* records a reason as
  `trust_ack` on the case itself, so it travels into both stores and every hash formula and
  freezes into the earned signature as a document's acknowledgement does. An acknowledgement
  covers the labels present when it was recorded; a label that appears later reopens the finding.
* **Routes:** `GET /v1/rag/collections/<id>/scan-manifest` (the per-case reading) and
  `POST /v1/rag/collections/<id>/rows/<case_id>/acknowledge-finding`.

Gates: `test_planner_health.py`, `test_planner_health.mjs`.

### 3b. Two signable halves — portable and deployment

An agent is signed in **two** halves, because two different questions are being asked and
one hash cannot answer both:

| Half | Question | Contents | Travels? | Authority |
|---|---|---|---|---|
| **portable** | *"is this agent the artefact that was validated?"* | composition (ingredients) + **posture** (what it will say) + **capability** (what it can do) | ✅ reinstates on install | **approver tier** (`_authorize_signer`) |
| **deployment** | *"is its resolved capability surface still what was attested **here**?"* | resolved LLM identity · **resolved fallback LLM identity** (S4 — `llmFallbackConfigurationId`, absent from the hash when none is bound so older attestations do not move) · reachable MCP tool/prompt catalog · per-profile connector opt-in · the agent's **file store** (PROP-010 — `fileStoreConfigId`, absent when the agent follows the admin default, so older attestations do not move; a change reads as `file store changed: …` drift) | ❌ never travels | **ownership** (`_authorize_deployment_attester`) |

The split is forced by the marketplace honesty gate (§8): `reinstate` only re-applies a
signature when the copy's live hash equals what was signed, so anything in the portable hash
must reproduce byte-identically after an id remap. A subscriber uses *their* LLM, *their* MCP
server and has opted into *nothing* — yet a model swap, a server quietly gaining a write
tool, and a connector opt-in are exactly the changes most worth catching. One hash cannot
both survive installation and cover them; two hashes answer both honestly.

**Two halves, two authorities** (the provenance-vs-policy split every trust system makes —
a build system signs the artefact, the deploying org attests its own admission policy). The
portable half can be vouched for by a third party, so it keeps the approver-tier gate. The
deployment half is a statement of *intent* about a surface only its owner controls, so its
authority is **ownership** — the profile is resolved under the caller's own uuid, and a
non-owner gets 404 (never 403, which would confirm another user's profile id exists).

**A signed key registered after the attestation baseline is omitted at its defaults**
(`profile_config_contract.signature_for`, Sep 2026). The keys in the frozen
`_ATTESTED_BASELINE_KEYS` keep the projection every existing attestation was taken with
(a bare profile still projects each of them at its documented default); any key outside the
baseline is projected twice — over the profile and over `{}` — and enters the signature only
when the two differ. So **registering a new signed key flips no existing attestation**: only
agents that actually configured it move. Learned on production: S5 (`turnBudget`) and S7
(`outcomeConfig`) each added a signed key, and the next deploy read every hardened agent as
🟠 `changed` with nothing about them changed (19 of 19 on one account). The obvious repair —
omit *every* key at its defaults — was tried first and moved every production signature the
same way, because the attestations already contain the baseline keys' default projections;
hence the split, and hence the baseline is frozen (a key is never added to it). Gates:
`t_new_signed_key_flips_nothing` (era literal + a fresh registered key) and
`t_attested_baseline_is_frozen` (membership pinned by name).

Routing attestation through the approver-tier gate was wrong for three reasons, all pinned by
`test_deployment_attest_authority.py` so it is not reinstated: (1) it could never enable a
reviewer, because that same user-scoped lookup means an approver reaches only their OWN
profiles — the gate could only lock the owner out; (2) it disabled the drift detector it
appeared to protect, since with no one able to take the baseline a model swap or a grown tool
catalog never flips the badge — a control that cannot be exercised is not a control; (3) under
`require_approval` the separation-of-duties rule refuses ALL self-signing, and attesting is
always self, so deployment attestation became impossible deployment-wide for every tier
including admin. Ownership is the authority model for a per-user artifact — the same
conclusion TC-30 reached for leaf sign-offs. The record still carries `signer_uuid` +
`signer_fingerprint`, so a surface can distinguish an owner-attestation from an admin one
rather than flattening both to 🟢.

**Enablement is content.** A disabled→enabled flip changes what the agent can do while
changing no artefact, so opt-in state is hashed alongside the catalog itself. Conversely
*absent must equal inert*: a disabled session primer, an opted-out connector and a
component's intensity dial hash as though they were not there, because a badge that flips on
tuning is a badge people learn to ignore.

**Portability normalisation.** Coordinator routing stores raw profile ids that an install
remaps, so `genieConfig` is hashed in **tag space** via an injected resolver; hashing ids
would make every coordinator pack permanently 🟠. Instance-local pointers
(`llmConfigurationId`, `mcpServerId`) never enter the portable hash at all — what matters is
the *resolved identity*, which lives in the deployment half.

**The catalog is read from the persisted classification snapshot**, not a live MCP
connection, so badge resolution works offline. *Honest limit:* an upstream server change is
caught when the profile next re-classifies, not instantaneously. The timestamp is excluded,
so re-classifying an unchanged server does not flip the badge.

**The runtime-injected categories are not the surface.** On every activation the runtime
injects two categories into APP_STATE — `System Tools` (the `TDA_*` framework tools) and
`Component Tools` (the action components) — and they leaked into the persisted record three
times (the loader aliasing the cache, the adapter persisting APP_STATE as-is, and the
resource-panel endpoint enriching a live reference — the last one *replacing* the category
with only the tools it found missing, so a record went from the 8 pre-S3 components to
`[TDA_Compute]`). Hashing them made a Uderia release read as an agent's deployment drift
("TDA_Compute appeared, 8 tools removed", 10 of 57 production attestations, 2026-09-22).
The reader now ignores exactly the categories `config_manager.RUNTIME_INJECTED_CATEGORIES`
declares — the same constant the persistence chokepoint strips — never a name heuristic, so
an MCP tool that happens to be named `TDA_something` stays in the surface. Structurally,
`get_profile_classification` hands out a **copy**, so no reader can mutate the record again.
An attestation taken over a polluted record is rewritten once by
`maintenance/normalize_deployment_attestations.py` (only when runtime-injected names are the sole
difference; signer and time preserved; any other change stays genuine drift).

Drift is **described, not merely detected** (`deployment.describe_drift`) — "tools appeared:
base_writeQuery(sql)", "connectors enabled: uderia-shell". An unexplained amber trains
operators to click through it.

`skillsConfig` is deliberately **not** signed: active skills are already ingredients, so
signing the block would double-count and flip on inert edits.

### 3c. An agent with several MCP servers (PROP-006 phase 2, Sep 2026)

An agent may read additional MCP servers (`additionalMcpServers`). WHICH servers (their aliases)
and the tool/prompt selection of each are **portable capability** — signed, carried by an agent
pack by server name and reinstated on install; the server ids are not. Their catalogs
belong to the **deployment** half — they are this instance's servers, exactly like the primary's
catalog — through one new field, `additional_mcp`:

- one entry per additional server, `{server_id, alias, catalog}`, sorted by (alias, server id); the
  catalog lists the tools and prompts in the **server's own names** (`_catalog_names` is the one
  naming rule for the primary's catalog and every additional one), the alias beside it;
- a server never classified on this instance reads `catalog: UNRESOLVED` — a fact, not "no tools";
- **absent for a single-server agent**, so no attestation taken before phase 2 moves.

`unresolved_surfaces` names such a server `additional_mcp:<alias>`, and `attest_refusal` refuses to
attest over it with its remedy (*"the tool catalog of the additional MCP server 'crm' is not
classified on this instance — activate the agent once, then attest"*). `describe_drift` names the
server: added, removed, renamed alias, its catalog becoming unreadable, tools or prompts that
appeared or were removed. The portable half does not change — which servers an agent reads is a
deployment decision, and a pack does not carry them. Architecture:
[MULTI_SOURCE_ARCHITECTURE.md §9](MULTI_SOURCE_ARCHITECTURE.md). Gate: `test_trust_deployment`,
`test_multi_mcp_runtime`.

## 4. The bottom-up sign gate

`agent_signable(ingredients)` refuses to sign an agent unless every ingredient is already
🟢; the blocking list drives the guided "sign these first" flow. The same rule composes for
**agent packs** (`pack.member_refs` → `agent_signable`). Verdicts are Ed25519-signed and
bound to the exact content version (`store.sign`).

For a Focus agent the chain therefore runs two levels deep: the agent is blocked by its
corpus, and the corpus is blocked by its own earned-signature rule (unscreened documents, or
injection findings neither quarantined nor acknowledged). That is the intended shape — the
badge cannot outrun the screening that justifies it.

## 5. Badges + sign-off UX

`trustBadge.js` provides the reusable primitives:
- **`mountArtifactBadges(root)`** — fills `[data-trust-artifact-*]` slots with a signer-aware
  seal (green intact · amber changed · faint "sign off" for signers on unsigned; nothing for
  non-signers). Wired across Skills, Extensions, KGs, Repos, Agent Packs, and Workspace
  instances (flow/dashboard/canvas).
- **`mountAgentBadges(root)`** — the compositional agent shield on the profiles list.
- **`openArtifactTrust(kind, id)`** — the trust popover: state · offline Verify · signer-only
  Sign off / Re-sign (surfaces an agent-pack's blocking members).
- **`mountMarketplaceTrust(root)`** — read-only "publisher signed" seal on marketplace cards.

## 6. Marketplace portability

Trust travels with **content, not ids**: signatures are content-bound (`kind|version_hash`),
so an attestation survives an id remap. Every marketplace surface uses the same two calls —
`capture(kind, id)` at publish, `reinstate(kind, new_id, claim)` at materialize — and the
**honesty gate** re-establishes a signature only if the copy's live hash equals what was
signed. The claim carries the signer's public key, so a cross-instance consumer verifies with
the publisher's key. Wired into KG · agent-pack (+ per-member) · skill · extension ·
component-instance; collections inherit trust free via the reference model. Full detail:
`TIER_5.2_PORTABILITY.md`.

## 7. Packaging + isolation (the Ontology Suite)

The Trust Membrane ships bundled with the Platform Ontology UX as **one physically-isolatable
enterprise feature — the *Ontology Suite*** — governed by a single switch
(`core/enterprise.py`, ON for all tiers today; a one-line flip makes it Enterprise-only).
Removal manifest + absence-tolerance contract: `src/trusted_data_agent/core/trust/README.md`.
Canonical suite description: [PLATFORM_ONTOLOGY_ARCHITECTURE.md §11](PLATFORM_ONTOLOGY_ARCHITECTURE.md).

## 7a. What an agent's signature covers (Jul 2026)

An agent's signable content is its **composition + its safety posture** — the grounding,
guardrail and honesty modes that decide what it will and will not say. Before this, two
agents with identical ontology and skills but opposite enforcement hashed identically and
both resolved 🟢: the seal attested to something other than the thing protecting the user.

`authorityConfig` is deliberately **excluded**. It governs what an agent may *do* on whose
behalf, is a strict subset of the deploying owner's permissions, and legitimately differs per
deployment — hashing it would make 🟠 fire as often for a correct adaptation as for
tampering. *Trust ≠ access* (§8) is the boundary. Authority is still portable,
downgrade-guarded, and enforced fail-closed at every tool chokepoint.

An agent whose posture sits entirely at the runtime defaults hashes **exactly as before**, so
existing attestations stay 🟢 and nothing re-signs; the posture dimension engages only once
there is real posture to protect. The `agent` kind also gained its first validator: signing
bands `clean` (at least one gate acts) or `partial` (every gate observe-only, with a caveat
naming them) — it never blocks. Full detail: `TIER_5.2_POSTURE.md`.

## 7b. Assessment hardening (Aug 2026) — the seal must not lie

A four-lens adversarial audit (`TRUST_CIRCLE_DEEP_ASSESSMENT_2026-08.md`)
closed a family of *fail-open* defects. The standing invariants it established:

- **One hash space, everywhere.** The agent hash is computed in **tag space** at ALL four
  surfaces — `sign_agent`, live resolve, `pack.agent_live_hash`, the REST artifact badge.
  Signing previously hashed a coordinator's `slaveProfiles` in raw-id space while the badge
  resolved in tag space, so every signed coordinator read 🟠 instantly and no coordinator
  pack member could ever reinstate.
- **A row is not an attestation.** `store.attested_hash` surfaces a recorded hash only for
  a genuinely-signed row (`signed=1`). A row written while the provenance key was absent is
  a row in a writable table, not a cryptographic attestation — it turns nothing green
  (mirroring `portability.capture`, which already refused to travel one).
- **Badge resolution fails closed.** A live-hash resolver that RAN and could not confirm
  the content yields 🟠 `changed`, never 🟢 (`_NO_LIVE_HASH` distinguishes "this kind has
  no resolver" — the only case that legitimately falls back to signed/unsigned).
- **Sign-offs are ownership-gated.** Every owned kind resolves its owner per kind and
  **fails closed** on a lookup error or NULL owner (`trust_routes._assert_owner`);
  `skill`/`extension` are deployment-level and stay tier-gated. Every successful sign/attest
  broadcasts a live `trust_event_notice`.
- **An unreadable surface is UNRESOLVED, never absent.** LLM identity, MCP catalog AND
  connector opt-in all hash a failure as `UNRESOLVED` — a persistent read failure can no
  longer sign a signature that silently omits an enabled shell/browser connector.
- **The downgrade guard covers absence.** A mode/flag *dropped* from a present incoming
  block (the shallow-merge wholesale replace) is compared against the runtime default —
  holding `enforce` steady while quietly dropping `pii` is a caught downgrade.
- **`verify()` names its key.** `key_source ∈ carried|local` — verification proves internal
  consistency with that key, not publisher identity (trusted-publisher pinning remains a
  deliberately deferred policy).

Deliberate, documented limits kept as-is: `content_hash`'s type-domain collision and the
catalog entry encoding (any change invalidates every existing attestation — a fix belongs
to an explicit hash-format version bump); the sign-time TOCTOU (fail-safe direction); the
license gate's fail-open (search key `TRUST_LICENSE_FAIL_OPEN` — must flip with the
Enterprise gate).

## 7c. History, owner-scoped agents, signing keys (PROP-009 §4.1, Oct 2026)

Three facts the evidence work (PROP-009 (`docs/proposals/PROP-009-evidence-per-result.md`))
needs, and that the original single-row design could not hold:

- **A re-sign must not erase what was signed before.** `trust_attestations` keeps one
  CURRENT row per artefact (the badge reads it, unchanged), and every write — `sign`,
  `reinstate`, `revoke`, `retire`, the one-time `migrated` seed, an admin `assigned` — is
  appended to `trust_attestation_history`. The current row carries `history_id`, the history
  row that produced it, which is what a turn's bill of materials references. A superseded
  signature stays findable by its hash (`signed_history_for_hash`) and still verifies
  (`verify_record`).
- **Agents are keyed by owner.** Bootstrap copies the same profile ids into every account,
  so `agent` and `agent_deployment` attestations are keyed `(kind, artifact_id, owner_uuid)`.
  Keyed by id alone, one account's signature was another account's badge, and one account's
  re-sign, revocation or deletion erased another's. Every other kind keeps `owner_uuid = ''`:
  their ids are deployment-unique (graphs, collections, flows, packs) or the artefact is
  deployment-wide by design (skills, extensions). A scoped read that names no owner sees only
  the unassigned bucket — *unsigned*, never another account's signature — and a structural
  gate requires every production caller of a scoped kind to pass the owner.
  The migration rebuilds the table once (SQLite cannot alter a table-level UNIQUE), copies
  every row unchanged, and attributes scoped rows with one rule (`attribute_owner`): the sole
  account holding the agent; else the signer if the signer holds it (since July 2026 only the
  owner can sign or attest); else unassigned, listed by `GET /v1/trust/unassigned` with the
  reason in words — never assigned by guess. Profile deletion now retires the deployment
  attestation too, for that owner only.
- **A rotated key must not orphan its signatures.** `trust_signing_keys` records every key
  that signed here: `active`, `retired` (an earlier local key — a new local fingerprint
  retires it) or `carried` (another signer's, seen on a reinstated attestation). `verify`
  reports `key_status` beside `key_source`.

*Measured on the reference instance at migration:* 174 rows copied byte-identical, 115
agent rows, 25 unassigned — every one an attestation of a profile no account holds any more.
Re-resolving all 127 agents across every account: 95 identical, 32 changed, and every change
is an agent whose id several accounts hold (it had been reading another account's
attestation). Gate: `test/test_trust_attestation_history.py`.

**The bill of materials (PROP-009 §4.2).** `composition.turn_evidence()` is the one resolution
behind SIGNED's per-turn stamp *and* the turn's bill of materials — the hashes the badge
computes are now kept instead of discarded, each part paired with the attestation-history row it
matched. Sealed as a `trust.bom` chain step and stamped as `turn['bom']`; see
[EVIDENCE_ARCHITECTURE.md](EVIDENCE_ARCHITECTURE.md) §4. Gate: `test/test_evidence_bom.py`.

**Renewal (Oct 2026).** A synced knowledge repository may be re-signed on the owner's behalf
after a sync that changed its content, under the owner's per-repository standing policy
(`collections.sync_auto_resign`; `core/trust/renewal.py`). The history grammar gained ONE
event for it — `renew`, an establishing event like `sign` — and `store.sign` refuses any
other value. A renewal is never a first signature, carries the owner's authority exactly as a
hand sign-off would (approver tier; refused under separation of duties), keeps the validator's
and the bottom-up gate's refusals, and records why under `verdict.renewal` (the run, the
repository, the files, the policy), so an evidence view that names a signer can say *renewed by
the scheduled sync*. Detail:
[KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md](KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md) §*Signature renewal
after a sync*. Gate: `test/test_signature_renewal.py`.

The renewal reaches a pack's subscribers: after the owner's agents, the seal of every installed
pack that ships the repository (or a renewed agent) is renewed — renewal only, the aggregate
gate — and the publisher's own fan-out (`fan_out_pack_sync`) re-syncs each subscriber, whose
copy is reinstated by content hash (`renewal.packs_carrying`; event
`signature_renewal_carried`). Before this a subscriber's agent read unsigned after every daily
sync (§H of the gate).

## 8. Guarantees & scope

- **Byte-identical baseline** — with no attestations, every artefact is ⚪; nothing changes.
- **Tamper-evident** — any content edit flips 🟢 → 🟠 (hash mismatch); for an agent this now
  includes weakening its enforcement posture.
- **Offline-verifiable** — Ed25519 signatures verify with only the signer's public key.
- **Honest portability** — a shared copy is 🟢 only if byte-identical to the signed original.
- **Trust ≠ access** — a signed artefact is *validated*, not *authorized*; secrets never travel.

**Downstream consumers (Jul 2026):** the membrane's integrity state now feeds two live
surfaces beyond the badge — **(1) Behavioral Trust Score**
([AGENT_OPS_EVAL_ARCHITECTURE.md §6c](AGENT_OPS_EVAL_ARCHITECTURE.md)): the composite
per-agent trust state = live conduct **capped** by this integrity reading (unsigned and
`deployment_unattested` both cap at "proven", changed caps at "watch" — a signature never
promotes bad conduct, and a deployment never attested here can never out-score a
portable-signed one), rendered as a
seal+ring glyph on the Agents roster; **(2) trust-aware routing**
(`coordinate_engine.py`: `genieConfig.requireTrustedExperts`) — a coordinator can require its
experts to resolve 🟢 at routing time, making compositional trust operative, not just visible.
Both resolve via `composition.resolve_profile_trust` defensively (suite absent → unknown /
loud fail-open warning), preserving the removability contract.

**Remaining (minor):** a cross-instance *trusted-publisher policy* (verification works; the
allow-list is a config choice); the *Enforced* grounding level (a turn may only execute 🟢
ingredients — TIER_5.2 §6). Note the Tier 4.3 **Model B** (an agent grounds on the ontology's
RELEASED version only, opt-in per agent) IS built (Sep 2026, [KNOWLEDGE_GRAPH_ARCHITECTURE.md](KNOWLEDGE_GRAPH_ARCHITECTURE.md#released-only-grounding--model-b-tier-43-1a)).

**Tests:** `test/test_trust_membrane.py` (244 assertions — three-state truth, hashing,
compositional truth table, the per-kind validators, scanners, bands, portability incl. cross-instance
pubkey round-trip, the owner-readable ingredient rule), plus `test_trust_deployment.py`,
`test_deployment_attest_authority.py`, `test_trust_attestation_history.py`,
`test_signature_renewal.py`.
