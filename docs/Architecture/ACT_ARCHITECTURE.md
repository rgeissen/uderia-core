# OBDA-MODEL and OBDA-ACT Architecture — acting on the model, and through it

**Status:** built (Oct 2026) on branch `feature/act-business-operating-model`, increments A1–A5,
B1–B4d, C1–C3; rehearsed end to end as Act II of the policing demo. This is the live contract
(checked against code 2026-10-30; since 2026-10-26 its page events are the one component change
event of COMPONENT_ARCHITECTURE § Component Signals — `action` announces `created` · `updated` ·
`deleted`, `obda_model` announces `deployed` from `authored_deploy`, both keep `instances: agent`).
The design record — the options considered and rejected, the rollout plan, the decisions taken
on the open questions — stays in
PROP-008 (`docs/proposals/PROP-008-act-business-operating-model.md`); where the two differ, this
document describes what was built.

**Related:** [ONTOLOGY_ARCHITECTURE.md](ONTOLOGY_ARCHITECTURE.md) (the layers OBDA-MODEL writes into),
[OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md) (the read-side twin: signed data products),
[TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md) (the two new trust kinds),
[AGENT_AUTHORITY_ARCHITECTURE.md](AGENT_AUTHORITY_ARCHITECTURE.md) (grants and the action-only
denial), `docs/Architecture/INDUSTRY_MODEL_PDM_DEPLOYMENT_PLAN.md` (the
deployment backends OBDA-MODEL reuses), [restAPI.md](../RestAPI/restAPI.md) (the endpoints).

---

## 0. What OBDA-MODEL and OBDA-ACT are

Until OBDA-MODEL and OBDA-ACT Uderia could only read (OBDA-READ): an agent answered from the ontology and the warehouse,
and anything that had to change — a concept missing from the model, a record that had to be
written — went to a person outside Uderia. OBDA-MODEL and OBDA-ACT close that loop on two planes that share
one road (propose → validate → land → publish → sign → use):

| Plane | What changes | Who decides | What guarantees it |
|---|---|---|---|
| **OBDA-MODEL** | the ontology and the warehouse schema — a new entity, a new attribute, a performance structure | a person publishes; an agent may only draft | the extension's own signature, the design standard read back from the warehouse's dictionary |
| **OBDA-ACT** | rows — record a lead, close a ticket, set a tier | the action type's posture; a person confirms or approves | the action's earned signature, one transaction with checks, a sealed receipt |

*The family:* **OBDA-MODEL** defines and extends the model, **OBDA-READ** reads through it
([OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md): signed data products), **OBDA-ACT** writes
through it — one ontology, one signature discipline, three directions. (Before Oct 2026 the two
planes here were called Model ACT and Instance ACT, and OBDA-READ plain OBDA; earlier changelog
entries, proposals and assessments keep the names they were written with.)

Three properties hold everywhere, and every section below is how one of them is kept:

1. **Nothing writes outside a signed contract.** Model changes land only through the delta
   deployer from a published extension; row changes only through a signed, granted action type.
   A raw `INSERT`/`UPDATE` from an agent is refused at the SQL gate.
2. **The base is never touched.** An extension, an action type, a landing on another warehouse
   and a tuning artefact are *stacked* on the graph: none moves the base ontology's seal, none
   counts as a base mutation (§2.5).
3. **Every act is accountable.** Each performed write has a receipt naming who, which contract,
   which statement, how many rows and through which door; every act through any door lands in
   one append-only log the audit surfaces read (§3.7).


## 0a. OBDA-ACT and OBDA-READ — the write half and the read half

[OBDA-READ](OBDA_ARCHITECTURE.md) governs what an agent may **read**: a signed data product is the
only shape of question it may ask of the warehouse. OBDA-ACT governs what it may **write**: a signed
action type is the only shape of change it may make. The two were built on the same pattern,
and where they share a mechanism it is one implementation, not two copies:

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

An agent can be confined on both sides at once — `surface-only` for reads and `action-only` for
writes — and the Trust Circle's BOUNDED station reads both.

*Naming:* the eval model also has a turn stage called **ACT** (Plan · Retrieve · Act · Compose ·
Deliver — e.g. "`surface_violation` (BOUNDED, ACT moment)"). That is the moment in a turn where
the agent uses tools; it predates PROP-008 and is unrelated to OBDA-ACT.

---

## 1. Switches and the baseline

| Switch | Where | Off means |
|---|---|---|
| **Availability — the components** (no master switch since Oct 2026) | OBDA-MODEL is the builtin component **`obda_model`** (tool `TDA_ModelProposal`), OBDA-ACT the component **`action`** (tool `TDA_Action`): each plane exists for a deployment, a user and an agent the way every component does — Administration → Components (global gate, per-user override), the agent's Capabilities toggle, and relevance (a graph bound). ONE reader: `core/obda_planes.plane_available` / `plane_enabled`. The former `ACT_ENABLED` (`TDA_ACT`) switch was migrated once at startup (`auth/database._migrate_obda_masters`: a persisted *off* disabled both components; a model-authoring grant switched `obda_model` on for the agent, an action grant `action`) | the plane is absent: no authoring or action text in any prompt (the `[[gate:act]]` blocks open per agent on the plane), the grants are dropped at parse (`planes=`), `TDA_Action` / `TDA_ModelProposal` are not offered, the REST faces answer `403 obda_act_off` / `obda_model_off` naming the admin pane, the scope reads pre-OBDA |
| **The OBDA policy** — "contracts only" per plane (`APP_CONFIG.OBDA_POLICY`, Administration → App Config → Trust → *OBDA policy*; `off · observe · enforce`) | applied once at `agent_authority.derive_principal` into every agent's scope, on every turn; `observe` records `policy_observed` events instead of refusing (see OBDA_ARCHITECTURE §0) | **model enforce**: an agent's DIRECT graph edits (`add_entity` · `add_relationship` · `declare_concept` · `restore`) are refused — the model changes through a proposal a person publishes; **act enforce**: every raw write and every command tool is refused — rows change only through the agent's granted signed actions (none granted ⇒ none at all) |
| **`modelAuthoring`** grant | `authorityConfig.modelAuthoring` on a profile | the agent cannot call `propose_entity` / `propose_attribute` |
| **Action grant** | `authorityConfig.actions.types` (list of action names) | the agent cannot perform that action — `not_granted`, whatever the face |
| **`actions_exclusive`** | `authorityConfig.actions` | when set, every raw write from the agent is refused as `action-only` (`ACTION_DENIAL_MARKER`), naming the granted actions |
| **`action` component** | `componentConfig.action` (and the deployment's component governance) — **one face only**: switching the component off removes `TDA_Action` from conversations and leaves authoring, the grants, REST/MCP, flow, scheduler and dashboard acts as they are; availability is the component chain's — Administration → Components (global gate, per-user override) and the agent's Capabilities toggle, read by `core/obda_planes.plane_enabled`; there is no master switch (Oct 2026). `is_profile_relevant` offers the tool only where it can act: a knowledge graph bound to the agent | `enabled_for: []` — no profile has `TDA_Action` until it is switched on; packs keep it off (`agent_packs/common/component_config.py:ACTION_COMPONENTS`) |

A grant is **access, not trust**: changing it moves no signature. The posture of an action
(§3.5) is part of the action's signed contract, not of the agent.

### 1a. Access in the warehouse — stated and checked before every act

OBDA-MODEL and OBDA-ACT act on a warehouse through two logins (§3.2): the **model** login lands tables and creates
ledgers; the **write** login performs an action's rows under the action's **role**. What each
needs is stated in one place (`dbdeploy/access.py`: `landing_requirements`,
`ledger_requirements`, `action_requirements`), judged against the dictionary
(`TeradataDeploymentBackend.read_access`: `DBC.AllRightsV`, `DBC.AllRoleRightsV`,
`DBC.RoleMembersV`; a database-level grant covers its tables; an unreadable dictionary holds
nothing — never a pass) and shown **before anything runs** — each requirement *held* or
*missing*, why, and the exact `GRANT` for what is missing (`judge`). Govern's preview carries it
(`deploy.access`) and **Land** is refused while it is missing; the Actions Designer shows it in
the Define form (re-read as the connection, columns or role change) and in each action's
details (`POST /v1/actions/access`). Gate: `test/test_act_access.py`.

| Act | Who | Must hold | Why | When it is checked |
|---|---|---|---|---|
| **OBDA-MODEL — land** a new sibling database `UDERIA_EXT_<root>` | the model connection's login | `CREATE DATABASE` on the parent (the login's own space by default — a login is not granted this on itself automatically) | the landing creates the database; the creator then holds every right inside it | Govern **Preview**; **Land** is disabled and refused (`409 access_missing`, nothing changed) while missing |
| **OBDA-MODEL — land** a new table in an existing database | the model connection's login | `CREATE TABLE` on that database | creates the table | as above |
| **OBDA-MODEL — land** a new column on an existing table | the model connection's login | `DROP TABLE` on that table | Teradata's `ALTER TABLE` needs it | as above |
| **OBDA-MODEL — land** a reference to a base table | the model connection's login | `REFERENCES` on the base table | the soft-RI `FOREIGN KEY … REFERENCES WITH NO CHECK OPTION` | as above |
| **OBDA-MODEL — the action ledger** | the model connection's login | `CREATE TABLE` on the target's database | the ledger `<db>.UDERIA_ACT_<table>` is created once, at the first validation | the Designer's **Access in the warehouse** (Define form and details); a refusal is named at validation |
| **OBDA-ACT — perform** | the write connection's login | the action's role (`GRANT <role> TO <login>`) | every write session acts under the role (`SET ROLE`) | Define form + details before anything runs; validation's probes run under the role; perform refuses `role_not_held` |
| **OBDA-ACT — perform** | the action's role | exactly the operation's rights on the target — insert `INSERT, SELECT` · update `UPDATE, SELECT` · upsert `INSERT, UPDATE, SELECT` — and `INSERT` on the ledger; **nothing wider** (A6 refuses a wider role) | the write, its pre/post checks and the before-image; the ledger entry | Define form + details; **A6** at validation |

The statements, with Uderia's names filled in, are what the surfaces print; the templates:

```sql
-- OBDA-MODEL (the model connection's login, e.g. uderia_ext_admin)
GRANT CREATE DATABASE ON <login> TO <login>;          -- a new sibling database under the login's own space
GRANT CREATE TABLE ON <landing_db> TO <login>;          -- a new table in an existing database
GRANT DROP TABLE ON <landing_db>.<table> TO <login>;    -- a new column on an existing table
GRANT REFERENCES ON <base_db>.<base_table> TO <login>;  -- a reference to a base table
-- OBDA-ACT (the write connection's login, e.g. policing_investigator, and the action's role)
GRANT <role> TO <write_login>;
GRANT INSERT, SELECT ON <db>.<table> TO <role>;          -- insert   (update: UPDATE, SELECT · upsert: INSERT, UPDATE, SELECT)
GRANT INSERT ON <db>.UDERIA_ACT_<table> TO <role>;       -- the ledger, append-only
```

---

## 2. OBDA-MODEL — the graph authors, the warehouse materialises

### 2.1 The authored layer and extensions

A proposed concept is written to the graph on the **authored** layer (`properties.layer =
"authored"`), beside the logical and physical layers, and belongs to an **extension** keyed
`extension_id = ext-<user uuid>`. Its lifecycle is `draft → validated → deployed → published`,
and `kgstore.types.advance_status` only ever moves it forward (a published node that lands on a
second warehouse stays published).
A landable node (an entity or an attribute) is published only after it has landed —
`kgstore.types.awaits_landing` is the one rule: `publish_extension` refuses `not_landed` (naming
the nodes, stamping nothing) and Govern counts it per extension (`unlanded`), saying "land … first,
then publish" instead of offering Publish; published early, the released model would carry a
column the warehouse does not have. A draft attribute on a published entity is its own row (an
attribute folds into its owner only at the same stage) and is outside the extension's signed
content, its edge included.

- A **draft** is visible to its author only. It never seeds retrieval and never survives
  subgraph assembly (`_unpublished_authored`), so agents grounded on the model do not see it.
  The model's own views follow the same rule: its card's figures (`list_all_graphs` counts
  drafts apart as `draft_entities`), the graph view and a node's expansion leave unpublished
  drafts out — the author sees them in the Workspace's **Your extension** section (every
  stage, and once published the trust act still owed) and in Govern's **Added to the model**. An extension is more than its drafts: landing it writes physical nodes (the table,
  its columns, a sibling schema — marked `authored_deploy`, each `realises` an authored node),
  and those are unpublished for exactly as long as what they realise
  (`kgstore.types.unpublished_extension_names` — the ONE rule, fed by
  `GraphStore.unpublished_names`, read by the agent's seeding and context assembly, the graph
  view, a node's expansion and every count). `GraphStore.DRAFT_SQL` is the authored half in SQL,
  gated to classify exactly as the Python predicate (`test_kg_draft_views`). The author sees a draft **in context** by opening
  the graph from its Drafts row (`graph-spec?include_drafts=1`, centred on the draft): it is
  drawn dashed and labelled `· draft`, and the panel keeps the drafts through lens changes and
  re-queries. Every change to the authored layer — a draft from the answer (the proposal row
  carries `data-kg-drafted`) or from **Draft it**, a landing, a publish — is announced as the
  one component change event (`uderia:component-changed`, `component_id: knowledge_graph`,
  `aspect: authored`; the publish and withdraw routes announce it at the server seam via
  `@_signals.announces_route`); the Workspace and an open graph panel on that graph redraw in
  place, without a page refresh (see COMPONENT_ARCHITECTURE § Component Signals).
- **Publishing is a person's act** (`ontology_governance.publish_extension`,
  `POST /v1/knowledge-graph/authored/publish`): an `agent:` actor is refused under every policy,
  and the person must hold an approver tier even when the ontology itself needs no approval.
- A **base update** (apply-update, a subscription sync, an agent-pack refresh, a declarative
  redeploy, a version restore — all `GraphStore.replace_model_layer`) replaces only what a walk
  produces. ONE rule, `kgstore.types.is_kept_on_replace`, keeps everything else: the authored
  and observed layers, an extension's **realisation** (the schema, tables and columns its landing
  wrote — `authored_deploy`), and the **stacked contracts** (action types and their ledgers, data
  products). A walked node that shares a realisation's name (a walk that reads the warehouse
  sees the landed table) takes the realisation's marks and stays the extension's. What an update
  cannot keep is named: a dropped container is stamped on the concept it concerns
  (`dangling_edges`), a realisation whose concept is gone is listed in `realisations_orphaned`.
  A pack payload carrying extension X replaces X and keeps every other layer. Before Oct 2026
  the rule read `layer` only: the Lead survived an update, its table and columns did not, and
  signed action types and data products were deleted.
- **What travels.** An extension's realisation is a fact of THIS deployment's warehouse: it is
  the extension's half in `split_layers` (`is_extension_layer`), so a base listing carries the
  published concept (with its signed `realisations` stamps, as the publisher's provenance) but
  never the landed table — the consumer lands it on their own warehouse.
- A Model B agent (`groundOn: released`) sees the released snapshot plus the **published**
  extension layer **with its realisation** (`kg_release.published_extension_layer`), never a
  draft; the pinned base drops every realisation, so an unpublished extension's table never
  rides it in, and a table landed after the pin still reaches the view.

### 2.2 The normalisation advisor

`agent/kg_constructors/normalisation.py` decides, deterministically, whether a proposed concept
is an entity or an attribute, from probes over the rows (when there are any): functional
dependency (must), multiplicity, hidden identity, shared reference, independent lifecycle, and
closed enumeration (never an entity). A declared choice stands unless a rule fires; with no rows
the verdict is *provisional* and says so; with every probe failed it is *unknown*. An attribute's
type is inferred from its name when not declared, and the preview says it was inferred.

The verdict is the body of the agent's answer row with one act — **Draft it** — offered only
when a draft is owed.

### 2.2a The model gap — decided and delivered in code

Before a concept can be proposed, someone has to notice the model lacks it. A question whose
**subject** nothing in the graph matches ("which leads are open?" asked of a model with no
Lead) is a *model gap*, and both halves of it belong to code:

| Seam | Owner | What it does |
|---|---|---|
| **Decide** | the knowledge-graph component (`handler.py:_model_gap`) | the question's subject from its own sentence shape (`_question_subject`), unmatched in the whole graph by name, label, description, declared value or meaning, in its own and its singular form. Stamps `model_gap = {term, singular, may_author}` on the turn's `kg_enrichment_event`. `may_author` is the **same** gate `propose_entity` applies (`_act_refusal`), so the offer and the act cannot disagree. Only while OBDA-MODEL · OBDA-ACT is on. |
| **Deliver** | `agent/model_gap.py`, called by **every engine** before the turn persists (`executor._apply_model_gap` for Optimize · Ideate · Ideate+tools · Focus, incl. Focus's nothing-retrieved answer; Coordinate calls the module on its coordinator's own enrichment, before its history write) | one note from Uderia under the answer (`answer_surface.note("model_gap")`, kind word *Not in the model*), placed above the evidence rail. When the agent may author (and the graph is known) it carries the act **Propose a ‹Concept›**, which opens a small form in place (`ui.js` `_onGapProposeAct`): the name, what it records **in plain words**, one per line, what it belongs to (the model's own entities, read by the deciding seam from the graph, bounded, carried on the act — metadata, never prompt text) and an optional definition. Opening it asks for one suggestion (`POST /v1/knowledge-graph/propose/suggest`, `agent/proposal_suggest.py`): one small call on the answering agent's own model for what such a concept records and which entity it belongs to, booked to the note's turn; it fills only what the person has not written, and says whose suggestion it is. **Check the proposal** posts it to `POST /v1/knowledge-graph/propose` — the same handler path a turn runs, with no model in between — and the verdict row with its **Draft it** takes the form's place. When the agent may not author, the note says extending the model needs an agent allowed to author it, and offers no button. The text form of the answer carries the same line. |
| **Model** | asked only for the prose (and, in a conversation, for `propose_entity` when a person asks it there) | the enrichment tells it the subject is not in the model, never to present another entity in its place, that the **offer is Uderia's** (not to repeat it), and to call `propose_entity` only when asked. |

**Business terms in, the model's terms out** (`agent/kg_constructors/business_terms.py`, run by
`propose_entity` for every face — a conversation, the form, REST). The person who extends a
model knows the business, not the warehouse, so code supplies everything physical: each attribute
becomes an identifier in the model's own naming style (`assigned_investigator` beside
`incident_number`; `AssignedInvestigator` beside `IncidentNumber`), the person's words kept as its
`label`; the concept gets an identifier of its own as its key unless one was named; and belonging
to an entity adds the reference the landing turns into a foreign key — named exactly like that
entity's declared key and typed like its deployed column. Uderia's additions carry
`added_by_platform` (`key` · `reference`) and the proposal row names them in words. The planner
declares a foreign key only to a parent that is a **table**: a parent realised as a view (every
`Policing_Core` entity) keeps the reference in the model and lands the column, with the reason
stated — the warehouse is never asked to reference a view.

Why both halves are code: on production (12 Oct 2026) the gap was decided correctly and the agent,
with a session primer ahead of the question, stated the gap but left out the offer, so there
was nothing for the person to say yes to. The first fix put a written request into the composer;
asked explicitly to propose, the agent then drew its own table of invented attribute names and
asked for permission instead of calling the advisor. Neither whether an owed act *appears* nor
whether an explicit request is *carried out* is left to the model: the person states the concept
in the form, and code asks the advisor. No gap ⇒ the answer
is byte-identical. Gates: `test_model_gap.py` (delivery, generality across terms, every engine
before it persists), `test_model_gap_act.mjs` (the form, executed: in place, the advisor called directly, the verdict row in its place, a refusal kept for correction).

### 2.3 The delta deployer

`dbdeploy/delta.py:plan_delta(authored, facts, dictionary, landing)` is pure: a function of
what the graph says and what the warehouse dictionary already holds. A new entity becomes a
`CREATE` under the design standard; a new attribute on a base entity a nullable `ADD`; a
reference on the attribute that names the parent's key; statistics on key and foreign keys. An
object the dictionary already holds is `exists`, so a re-run is a no-op and a half-landed delta
resumes. A realisation whose authored node is gone is refused unless forced, and only ever over
the graph's own objects.

**Where a table lands is policy, not a guess** (`landing_for`): the model's own database when
the deploy login owns it, otherwise a Uderia sibling `UDERIA_EXT_<root>` (provisioned first,
named from the **deployed** root). Unknown ownership never lands in the root.

`agent/kg_constructors/authored_deploy.py` orchestrates a landing: it stamps physical nodes that
`implements` the authored ones, marks the authored node deployed, and seals a `model_change`
receipt (Ed25519). The preview is the validation act.

**One graph, several warehouses.** The graph's own physical layer is the primary
infrastructure; a landing elsewhere is recorded on the authored node as
`realisations[<infrastructure>]` (table, standard, readback, receipt), never as a second
physical layer (ONTOLOGY_ARCHITECTURE §2.4b).


**Where a sibling database is created, and how large.** When the landing needs
`UDERIA_EXT_<root>` and it does not exist yet, `TeradataDeploymentBackend.provisioning()`
decides both and says why: the parent is the one given for this landing, else the connection's
configured `database_parent`, else its default database, else **the landing login's own
space** (a login can hold CREATE DATABASE on itself; `dbc` only works for a superuser); the size
is the one given, else configured, else **half of what that parent has free** (DBC.DiskSpaceV,
capped at 10 GB). The preview carries `provisioning` (`{sql, parent, perm_bytes, basis}`); the
Govern dialog states it and lets the person change both, and Land sends exactly what the last
preview showed. The landing *database* itself is never a request field — ownership decides it.

### 2.4 The design standard, probes and readback

- **Rule records with proof.** Each backend's standard is a set of rule records (id, rationale,
  source, the statement that proves it): `dbdeploy/design_standard.py` (Teradata, 13 rules) and
  `dbdeploy/pg_standard.py` (PostgreSQL, 11). A rule reads *verified* only when its statements
  were executed on the target's version family and recorded in
  `design_standard_verification*.json` (`scripts/design_rules_verify.py`); otherwise it reads
  *unverified*, never a pass.
- **Probes before choices** (`dbdeploy/probes.py`): on the rows a load will write, the declared
  key is kept unless a measurement contradicts it (constant, skewed or non-unique), and the
  demotion is named. On synthetic rows nothing is applied.
- **Readback** (`dbdeploy/readback.py`): after a landing the warehouse dictionary is read
  against each rule — `held · violated · unverified` per table — and a dictionary nothing was
  read from reads *unverified*, never a vacuous *conformant*.
- **Dialects** (`dbdeploy/dialects/`): the planner decides *what* lands; a `DeltaDialect` says
  it. The base class carries neither vendor's vocabulary.

### 2.5 Signing an extension, and what never moves the base

The extension is the trust kind **`ontology_extension`** (`core/trust/extensions.py`), signed
over `{extension_hash, base_attested_hash}` — the base's signature is inside it, so a base
re-sign reads the extension *changed · base moved* while the base stays green. Refusals:
`not_found · nothing_published · base_mutation · nonconformant · unsigned_ingredient`. An agent
grounded on the graph carries each published extension as an ingredient.

**Stacked contracts** (`kgstore.types.is_stacked_contract`, one rule read by trust composition,
governance and extension signing): an authored node, a realisation, an action type and its
`acts_on` edge, a data product, and a ledger or tuning `ddl_artifact` are excluded from the
ontology's content hash and are never a base mutation. A graph with none of them hashes
byte-identically.

### 2.5a Withdrawing an extension — the inverse of publish (Oct 2026)

A person takes an extension off a graph in one act: `DELETE /v1/knowledge-graph/authored/<extension_id>?kg_id=`
(`core/authored_withdraw.py`). What goes: every authored node of the extension and its design
decisions; the physical nodes a landing wrote **for** those nodes (`realises` names the authored
node) and the landing's schema node once nothing else realises into it, with whatever
Uderia stamped under it (the action ledger artefact); their retrieval telemetry; the
extension's signature (the trust history keeps both the signing and the revocation). What
stays: the **warehouse** — Uderia lands, a person owns what was landed (§1a), so the
table is dropped there by hand and on purpose — and the **act log**, which has no delete face.
Agents grounded on the graph read *changed* afterwards (their composition lost an ingredient)
until their owner re-signs them, exactly as after a publish. The rule that picks the nodes is a
pure function over the graph's entities (`select_withdrawal`), the same reading the authored
listing uses; another extender's layer is never touched. Gated like publish. Gate:
`test/test_withdraw_rest.py`. The Act II rehearsal reset (`act2_reset.py`) is this endpoint
plus four small deletes (pending Inbox items, undecided tuning proposals, snapshots, a
signature) — all through the API, so no rehearsal ever opens the server's database.

### 2.6 Workload tuning

The tuning review proposes performance structures from what the agents actually ran — never
applies them.

- `core/tuning_evidence.py` gathers the graph's deployed scope, the recorded workload of the
  agents grounded on it (successful data statements, never an `EXPLAIN` or a failed call), an
  `EXPLAIN` of each distinct statement, and the dictionary facts.
- `dbdeploy/tuning.py` decides (pure): statistics where a join or a plan rested on a guess, a
  covering join index on a large table scanned in full, a partition as advice only. **A view is
  never tuned**: the warehouse keeps statistics and indexes on tables, so a view the workload
  reads yields a finding naming its base tables, not a proposal.
- `core/tuning_proposals.py` holds the lifecycle: pending → approved **by a person** → landed |
  failed; rejected; expired with evidence kept. Landing measures the motivating statements
  before and after, records whether plans changed shape, and signs the receipt. A landed
  structure becomes a `ddl_artifact` that never moves the seal.

---

## 3. OBDA-ACT — the signed action type

### 3.1 The manifest

`dbdeploy/action_model.py` parses an action: name, version, backend; a target (entity, the
frozen deployed identifier, the key); an operation (`insert · update · upsert`; delete is
future); an attribute allowlist with types, value domains and defaults; author pre/post
conditions; a required row bound (`max_rows ≤ 1000`); an authority (`required_role`, posture);
and a ledger — on by default (`<target db>.UDERIA_ACT_<table>`, append-only, the before-image as
JSON). Rule violations are a closed set of REJECT codes with near-miss hints. The default
conditions follow from the operation and cannot be redeclared (update: `row_exists ·
one_row_affected`; insert: `row_absent · one_row_present`; upsert: `one_row_affected`).

### 3.2 The gate (A1–A6) and the earned signature

`dbdeploy/action_validation.py` (pure, evidence injected):

| Check | What it proves |
|---|---|
| **A1** | the frozen target is what the resolver answers today |
| **A2** | columns exist with compatible types; value domains agree with the graph's closed domains |
| **A3** | the grain is unique — measured live; a probe that cannot run fails |
| **A4** | conditions are boolean, hold no subquery, read only the target's columns |
| **A5** | one parameterised statement on the target only; live `EXPLAIN` shows no product join and no full scan of the target |
| **A6** | the role holds exactly the rights the operation needs, and the ledger is provisioned |

The trust kind **`action_type`** (`core/trust/actions.py`) signs `{manifest_hash,
validation_report_hash}`: the proof is inside the signature. Refusals: `invalid_manifest ·
unvalidated · stale_report · gate_blocked · unsigned_ingredient` (a write contract stands on a
signed ontology). Live evidence signs clean (`verified_write`), structural evidence partial
(`verified_declared`). An edited manifest drops its stored report and reads *changed*.

**The model under the action is in the signature — exactly the part the proof read.** The
report records `model_facts` — the facts A1/A2 read from the graph (the target's resolution,
its columns, the writable attributes' closed value domains), computed by ONE pure function
`action_types.model_facts` for both validation (through the store) and the trust reading
(through the graph rows). The signature covers the report, so it covers the facts;
`trust/actions.live_hash` substitutes TODAY's facts into the stored report, so the live hash
equals the signed one exactly while those facts hold. A change to one reads *changed* with
`drift` in words (`the allowed values of LoyaltyTier changed` · `new attribute Email` ·
`Lead now resolves to …`); signing the stale proof is refused `stale_report`; perform's
refusal names it; the Designer's Next is **Validate again**. A change elsewhere in the graph,
or a base re-sign, moves nothing — the action binds the facts it depends on, not the model's
signature (no churn on unrelated edits, no cycle between two signatures). A report from before
the facts were recorded proves nothing about today's model: it reads *changed* and re-validates
once. The other direction is deliberate: action nodes stay OUT of the ontology hash — the graph
does not depend on its actions, and an action's author may not own the model. Facts are read
from the PUBLISHED model only (`kgstore.types.unpublished_extension_names`): a draft on the
target moves nothing until it is published.

**Signing stands on the whole model the target lives in.** The bottom-up ingredient
(`trust/actions._model_ingredient`) is the base ontology AND — when the target is an authored
entity — the extension that holds it; the worse of the two blocks with its name
(`unsigned_ingredient`: *the action's extension by ann is changed … sign the extension by ann
first*). The trust reading of an unsigned action carries `stands_on {state, label}`, so the
Designer's Next is **Open Govern** (the graph's Govern dialog, via the `uderia:open-govern`
event) rather than a Sign the gate refuses, and it re-reads itself when Govern signs.

`core/action_types.py` is the authoring seam (`author_action`, `gather_evidence`, `validate`,
`provision_ledger`); the Action Designer (`static/js/handlers/actionDesigner.js`) shows the
journey validated · signed · granted · in use and offers only the act that is owed.

**Two logins, never confused** (Act II rehearsal, Oct 2026). A model has a MODEL login (it lands
tables, creates the ledger — `CREATE TABLE` in the extension's database) and a WRITE login (an
action's rows, under the action's role). `action_types.default_connection(…, purpose=)` keeps
them apart: `model` → the connection the model was landed through (`landed_via` on its physical
tables and columns); `write` → the user's only warehouse, else the person chooses — **never**
the landed one (the model login holds no action's role) and never anything on an agent. **The
action owns the connection it writes through:** the one it was validated through
(`validated_with`; a structural validation owns none). Perform writes through it
(`action_perform.action_connection` → `resolve_connection`, on every path: agent turn, Confirm,
REST, unattended, undo); to change it, validate through another connection — the proof follows
the login. There is no second place: nothing on the agent chooses a write login — not a
profile's legacy `componentConfig.action.connection_id` (a profile save once dropped it and the
agent could no longer write) and not its Database Navigator source (a READ connection, often an
administrator's). Validation provisions a missing ledger through the MODEL connection — a refusal is a
named result (`ledger.missing` + the warehouse's own sentence, `dbcatalog.sql_runner.friendly_db_error`),
never a 500, and the gate still runs. The Designer validates and tries an action through the
WRITE connection (the action's own `validated_with`, then `defined_with`, then the catalog's
`writes_through`), says on each row which connection it writes through, and never starts on
"the first connection listed". Every Teradata deployment backend holds
its **own `teradatasql` session for its own login**: teradataml's `create_context` is
process-global, and with two logins in one process a statement ran as whichever had connected
last (a PAT-only configuration is the one case still served through it).

**A6 names the fix.** When the role lacks a right, the check says exactly what to run
(`action_validation.grant_statements`: `GRANT INSERT, SELECT ON db.t TO role;` — never wider),
also when the role holds nothing yet; and a blocked action's owed act is **Validate again**
(the fix is in the warehouse, not the contract), with the blocker named in its Next box.

**Defining an action is a person's act in the Designer** (Oct 2026; before, only `POST
/v1/actions` could). The person decides what only a person can — the concept, insert or
update, the writable columns, the bound, the role, the posture; Uderia derives the rest
(`action_types.describe_target`, `GET /v1/action-targets` + `POST /v1/action-targets/describe`):
the deployed TABLE (the resolver; a view is never offered, and refused by name), the key (the
warehouse's unique index, else the model's), every column's contract type (the live dictionary
through `action_validation.contract_type_of`, the inverse of A2's rule — so a derived contract
cannot fail A2, the 7 Oct `lead_id` integer-over-text refusal), the closed value domains, and
the warehouse's roles for the table ranked per operation (exact rights from
`action_model.TARGET_RIGHTS` first, then a name that says the operation). The form opens
prepared — the concept defined last (else an extension's first), its table read, the operation
it has no action for yet, the role the warehouse suggests — and with a warehouse chosen one
click defines AND validates (validation never signs). The table is read through the connection
the person chooses (`action_types.default_connection(…, purpose="write")`: the user's only
warehouse, else a choice — said in words beside it; nothing is guessed between several, and the
connection the model was landed through is never offered for a write). What is not offered is named with its reason (deployed as a view —
read-only to an action; a draft; not landed). It opens from the graph card, the Workspace's
Actions section (`+`, and inline in its empty state) and the Actions panel; every list re-reads
on the action component's change event (`UderiaBus.onComponent('action')` — the define,
grant and delete routes announce `created` · `updated` · `deleted` at the server seam).

### 3.3 The compiler

`dbdeploy/action_compiler.py:compile_action(manifest, {key, values})` (pure, deterministic):

- one parameterised statement; every value a bind parameter, never a literal;
- an update carries `WHERE <grain> = ?` and nothing else; an insert carries every required
  attribute or is refused naming them; the key cannot be set by an update;
- every value is coerced through its declared type and checked against its domain. A string
  that differs from **exactly one** declared value only in case or surrounding space is that
  value (`'Closed'` → `'closed'`), said in the compile notes; two such candidates are refused as
  ambiguous;
- the executable checks: the default conditions, the bound, the author's predicates;
- the before-image read and the ledger insert;
- the **invocation hash** is over the coerced values, so a confirmation always matches what
  will be written. The statement the gate's A5 judged is this compiler's statement, byte for
  byte.

Refusals are recovery-shaped (`unknown_attribute` with the closest name, `key_not_settable`,
`missing_required`, `outside_domain`, …).

### 3.4 The runner

`dbdeploy/action_runner.py:run_compiled(conn, compiled, principal, posture, commit)` runs one
compiled write in **one transaction**: pre-checks → before-image → statement → post-checks →
ledger → commit only if every check held, otherwise rollback. A driver error fails closed. It
decides nothing about authority or posture.

### 3.5 Performing: authority, posture, connection

`core/action_perform.py` is the one perform seam.

**Refused before anything is touched:** OBDA-MODEL · OBDA-ACT off · unknown action · an action that is not signed
and intact (`not_trusted`) · an agent whose grant does not name it (`not_granted`; a person with
no agent acts with their own authority). On Teradata the connection's login must hold the
manifest's role, and the session acts under it (`SET ROLE`) — Uderia never widens what A6
proved.

**The posture** (signed, part of the contract):

| Posture | Effect |
|---|---|
| `observe` | records what would be written; writes nothing |
| `review` | runs the write in an always-rolled-back transaction (so the diff is the database's answer) and **parks** it in the Assurance Inbox; nothing written until a person approves |
| `annotate` / `enforce` | writes in one transaction through the runner |

**Interactive vs unattended.** On an interactive turn a perform **prepares** (`prepare_action`):
the consequence row shows the change and a Confirm; `confirm_action` performs only the
invocation whose hash the person saw (a different value set is refused, nothing written). An
unattended face — a flow node, a scheduled task, a dashboard tile —
calls `perform_unattended`, which never prepares: the posture decides, and it always acts as an
agent (no agent ⇒ refused — nobody is present to lend their authority).

**Approval performs.** Approving a parked item calls `perform_parked`, which writes only if the
manifest hash, the re-compiled statement hash and the invocation hash equal the parked ones and
the agent's grant still names the action; otherwise `409`, the item stays open.

**Four eyes — a designated approver, never the owner** (PROP-008 §5.4a). An agent's
`authorityConfig.actions.approvers` (account usernames, set with `POST /v1/actions/approvers` or
the **Approvers of parked writes** field on the profile editor's Agent Authority card — which
calls that same endpoint before anything is saved, so the one server rule refuses an unknown
account and the owner on both doors) moves the decision off the agent's owner:

- the park resolves the names to active accounts (`resolve_approvers`; the owner is never their
  own approver) and stores their ids on the review item (`approvers_json`); a name that is not an
  active account **refuses the park** (`approver_unknown`) — a write is never parked where nobody
  may decide it, and never silently falls back to the owner;
- ONE rule decides who may act, `review_queue.may_decide`: approvers designated ⇒ only they;
  otherwise the owner. The approve route refuses anyone else with `403 not_your_decision` before
  anything runs; `decide` enforces the same rule underneath it and records `decided_by`;
- the approver sees the item in their own queue (`list_items(awaiting_me=True)`; owner-centric
  readers — Trust History, the agent story, the audit pack — read the owner's own items alone) and
  in their own agent's answer (`TDA_Action pending_approvals`, with Approve / Reject controls the
  person clicks — no agent ever approves); `pending_count` counts only what THIS person owes;
- the write still runs **as the owner's agent on the owner's connection**; the ledger and the act
  log name the approver (`approved_by`), and Trust History says who it awaited and who decided.

The approval's write and its disposition run as one unit shielded from cancellation, so a client
that disconnects mid-approval can never leave a performed write on an item still pending.

**Undo** (`undo_invocation`): the ledger's before-image becomes an invocation of the same signed
contract, returned prepared, confirmed like any act. An insert's undo would be a delete —
refused in v1.

**The connection is decided, never guessed** (`resolve_connection`): explicit → the connection
the ACTION was validated through (it owns it) → the user's only Teradata or SQLite connection →
otherwise refused naming the candidates and saying how to give the action one (validate it
through that connection). Nothing on the agent decides.

**The Trust Circle counts a parking action.** A granted action in `review` posture can park
work exactly as a review-mode gate can, so REVIEWED's configuration reads it: `/v1/profiles`
serves the granted review-posture actions as `actionsParking` (`action_perform.parking_actions`,
read from the agent's own graph), and the station reads "can park" and names them. Before
this, an agent whose gates all observed read "cannot park" on the day it had parked five writes.

### 3.6 Receipts

- Each **performed** act inside a turn is sealed into the turn's Ed25519 provenance chain as an
  `action.performed` step; `verify_content` re-derives it from the turn's `actions` stamp, so a
  tampered stamp is a named mismatch.
- An act that happens outside a sealed turn (a confirm, an approval, any unattended or REST/MCP
  act) carries a **receipt signed on its own** (`sign_receipt`): Ed25519 over `{receipt,
  occurrence: {face, ref, session_id, confirmed_by, at}}`. The occurrence makes two identical
  writes on different runs distinguishable, and names the door. It verifies offline with only
  the public key.

### 3.7 The act log

Acts were first recorded only where a face kept state (a turn, a flow node run, a scheduled run,
the review queue), so an act through REST, MCP or a tile with no session left no record in Uderia.
`core/action_act_log.py` is **one append-only table** (`action_act_log`) written at every seam
where a final record exists — `perform_unattended`, `perform_parked` (face `review`), the REST
perform (with its face), and the conversation's own acts. Writes are fail-soft (the act's own
record and the warehouse ledger still hold it). Read by Trust History (`kind: "act"`, the face in
words, the approver by account) and `/v1/actions/activity` (`by_face`, `unattended`).

### 3.8 The SQL gate's part

The SQL gate (`agent/tool_enforcement.py` → `agent_authority.is_tool_allowed`) refuses the
**raw** write: with `actions_exclusive` every `INSERT`/`UPDATE`/`MERGE`/`DELETE` an agent sends
through its data tool is `action-only`, and the refusal names the granted actions. A signed
action never goes through the agent's data tool — a data tool runs one statement per call under
autocommit and cannot hold pre-checks, write, post-checks and ledger together.

---

## 4. Faces — one core, every door

| Face | Surface | `face` on the receipt |
|---|---|---|
| **Conversation** | builtin component `TDA_Action` (`list_actions` · `perform` · `undo` · `pending_approvals` — the writes awaiting the asking person as designated approver, each with Approve / Reject), companion skill `obda-act-expert`; answer row with Confirm / outcome / Undo; Actions split panel; pin to dashboard | `conversation` |
| **REST** | `/v1/actions` (catalog, author), `/<name>` (definition, delete + revoke), `/validate`, `/preview`, `/perform`, `/undo`, `/grants`, `/grant`, `/confirmations`, `/activity`; `/v1/trust/action/<kg>/<name>/sign-off` | `rest` (or the body's `face`, closed set) |
| **MCP** | `list_actions` · `perform_action` at developer tier — two-step: without `invocation_hash` it previews, with it it performs | `mcp` |
| **Flow** | node type `task_action` (`$name` / `{name}` resolved from upstream outputs); refused at save if the node's agent is not granted | `flow` |
| **Scheduler** | a task with `action_spec` instead of a prompt — no session, no LLM, no tokens | `scheduler` |
| **Dashboard** | a pinned action tile offers **Prepare again** once its row has settled | `dashboard` |
| **Assurance Inbox** | a parked write shown as the database's diff; **Approve — perform it** / Reject | `review` |

**How the conversation face resolves the action** (`ActionComponentHandler._resolve_action`):
a granted action named is used; otherwise, when exactly one granted action acts on the named
entity it is used without asking. The entity may be named where the action belongs
(`action_type: "Lead"` reads as the entity). When several act on the entity, the invocation's
own attributes pick the one action that can write them all; ambiguity is refused, never
guessed.

OBDA-MODEL faces: the **`obda_model` component** — `TDA_ModelProposal` `propose_entity` / `propose_attribute` (the `TDA_KnowledgeGraph` actions of the same names remain unadvertised aliases; behind the
`modelAuthoring` grant), `POST /v1/knowledge-graph/propose`, `GET …/authored`,
`POST …/authored/deploy`, `POST …/authored/publish`,
`POST /v1/trust/ontology-extension/<kg>/<ext>/sign-off`, the tuning endpoints
`/v1/knowledge-graph/tuning/*`, and the KG panel's Govern dialog (the road, the landing preview
and readback, extensions, and the Workload tuning segment).

---

## 5. Observability and evaluation

- **Live Status:** every act is a step (`action_record`: Prepared · Performed · Parked ·
  Observed · Refused), emitted live and rebuilt on reload; a refused prepare is recorded too.
- **Answer capsule:** an *Actions* row; the KG theatre shows the draft beat for a proposal.
- **Trust Circle — BOUNDED:** the `actions` assurance point reads the turn's acts; the station's
  evidence names the actions acted on in recent sessions.
- **`action_safety`** (informational dimension, ACT stage): of the action writes a turn put to
  the database, the share whose every check held; abstains when nothing reached the database.
- **`write-escape`** live red-team probe: lures an action-only agent into a raw `UPDATE`;
  scored against the persisted turn.
- **Friction budget** as a gate (`test/test_act_friction.mjs`): question → performed act 1
  click, a missing concept → draft 1 click, draft → published 3 clicks.

---

## 6. Honest limits

- `delete` is not an operation in v1, so an insert cannot be undone.
- **Approvers are named per agent, not per action.** `actions.approvers` covers every parked
  write of that agent; a different approver per action type, or a quorum (two of three), is not
  built.
- **A no-change write is refused at preparation and at the park, not when enforced.** An update
  whose every keyed row already holds every value it would write (`action_perform.no_change`,
  read from the before-image the database returned — never from the request) is refused
  `no_change` ("… already holds … — nothing to write") before a person is asked: no Confirm is
  offered and nothing is parked for an approver. An `enforce` posture performing autonomously
  (a flow, a schedule) still writes it — the rows are unchanged and the receipt records the act.
- The act log is fail-soft: an act whose log write failed is still in its own record and the
  warehouse ledger, but not in Trust History.
- Tuning measures through `EXPLAIN`; a login that cannot `EXPLAIN` the views gets findings from
  the workload alone, and views themselves are never tuned.
- The industry-model PDM converter remains Teradata-only; the authored delta lands on Teradata
  and PostgreSQL.
- MCP actions are developer tier, never user tier.

---

## 7. File and gate map

| Area | Files | Gates |
|---|---|---|
| Authored layer, extensions | `kgstore/types.py`, `components/builtin/knowledge_graph/{handler,graph_store}.py`, `core/{ontology_governance,kg_release}.py`, `core/trust/extensions.py` | `test_kg_extension_trust`, `test_kg_act_ux.mjs` |
| Advisor | `agent/kg_constructors/normalisation.py` | `test_kg_normalisation` |
| Delta deployer, landings | `dbdeploy/{delta,backend,teradata_backend,postgresql_backend}.py`, `dbdeploy/dialects/`, `agent/kg_constructors/authored_deploy.py` | `test_pdm_delta` |
| Design standard, probes, readback | `dbdeploy/{design_standard,pg_standard,probes,readback}.py`, `scripts/design_rules_verify.py` | `test_pdm_design_rules` |
| Tuning | `dbdeploy/tuning.py`, `core/{tuning_evidence,tuning_proposals}.py`, `static/js/handlers/rag/kgUpdateVersions.js` | `test_pdm_tuning`, `test_tuning_ui.mjs` |
| Action model, gate, trust | `dbdeploy/{action_model,action_validation}.py`, `core/action_types.py`, `core/trust/actions.py` | `test_action_model`, `test_action_validation`, `test_action_trust` |
| Compiler, runner | `dbdeploy/{action_compiler,action_runner}.py` | `test_action_compiler` |
| Perform, receipts, act log | `core/{action_perform,action_act_log,agent_authority}.py` | `test_action_enforcement`, `test_act2_rehearsal` |
| Four eyes (designated approvers) | `core/{review_queue,action_perform,agent_authority}.py`, `api/{rest_routes,action_routes}.py`, `components/builtin/action/{handler.py,renderer.js}`, `static/js/handlers/agentPerformanceHandler.js` | `test_action_four_eyes` |
| Model gap (decide + deliver) | `components/builtin/knowledge_graph/handler.py` (`_model_gap`, `_render_gap_lead`), `agent/model_gap.py`, `agent/proposal_suggest.py`, `agent/kg_constructors/business_terms.py`, `agent/answer_surface.py` (`model_gap` note kind), `agent/executor.py` (`_apply_model_gap`), every engine, `static/js/ui.js` (`_onGapProposeAct`) | `test_model_gap`, `test_model_gap_act.mjs`, `test_business_terms` |
| Faces | `components/builtin/action/*`, `skills/builtin/obda-act-expert/`, `api/action_routes.py`, `mcp_server/{client,server}.py`, `core/{flow_executor,task_scheduler}.py`, `components/builtin/dashboard/renderer.js`, `static/js/handlers/actionDesigner.js` | `test_action_agent_face`, `test_action_rest`, `test_action_renderer.mjs`, `test_flow_actions`, `test_scheduler_actions`, `test_dashboard_act_again.mjs`, `test_action_surfaces` |
| Baseline | the planes off (component availability) | `test_act_baseline`, `test_act_admin_switch` |
| Eval | `eval/scorers/reliability.py` (`action_safety`), `eval/live_redteam.py` | `test_eval_reliability`, `test_live_redteam`, `test_act_friction.mjs` |

All of these run in `test/trust_battery.sh`.
