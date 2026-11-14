# Agent Authority Architecture — the Agent Principal (Tier 5 Step 4 / P3)

**Status:** implemented on `main` (2026-07-03); last verified against code 2026-10-30. Delivers assessment requirement **P3** (agent
identity & permissions — only Salesforce/Microsoft were GREEN). Built *on* the
[Trust Membrane (Tier 5.2)](TRUST_MEMBRANE_ARCHITECTURE.md) foundation: the **agent is a
first-class governed principal**. Design:
`TIER_5.2_TRUST_MEMBRANE.md §1.5`.

> **Renamed 2026-07-04** — this document was formerly *"Agent Identity Architecture"* and the
> module was `core/agent_identity.py`. It was renamed to **Agent Authority** (`core/agent_authority.py`)
> because in Uderia an agent's *identity* is its **Profile**; this subsystem governs the agent's
> *authority*. See §0. The stored config key moved `identityConfig → authorityConfig` (the legacy
> key is still read). The internal class `AgentPrincipal` keeps its name — it is the correct IAM term.

---

## 0. What "Agent Identity" means in Uderia — read this first

"Agent identity" is an **umbrella** term. In Uderia it is not a single feature —
it is a **composite** realised across four subsystems on **two orthogonal axes**. Keeping them
distinct is the whole point of this rename.

| Concept | The question it answers | Where it lives in Uderia | Axis |
|---|---|---|---|
| **Agent identity** | *Who is this agent?* | The **Profile** — its IFOC class, its LLM, its MCP "senses", its Memory, its Components. The `@TAG` profile **is** the agent. | who |
| **Agent assurance** | *Is this agent verified / trustworthy?* | The **Trust Membrane** (Tier 5.2) — signed, compositional attestation. | trust |
| **Agent authority** ← *this document* | *What may it do, on whose behalf, when it acts autonomously?* | `core/agent_authority.py` — a delegated, **subset-of-owner** scope. | access |
| **Agent audit** | *What did it actually do?* | The **Execution Provenance Chain** — every autonomous action attributed to the agent. | audit |

> **Visual companion:** [**The Uderia Agent — An Anatomy of Trust**](../../static/trust/agent-anatomy-of-trust.html) — the whole-agent diagram this taxonomy comes from (the five faculties inside the Trust Membrane, on the Grounding & Guardrails foundations). Its center — *Agent Identity = who it is = its Profile* — is exactly the "identity" row above.

Two rules follow, and they are load-bearing:

1. **Identity ≠ authority.** The Profile says *who the agent is*; Agent Authority says *what it is
   permitted to do*. A trusted, well-defined agent (identity + assurance) can still be given a
   deliberately narrow authority. This is the framework's phrasing of **charter #8, *trust ≠ access***.
2. **Authority is delegated and can only shrink.** An agent has **no authority of its own** — when
   it acts autonomously it borrows a **strict subset** of its owner's permissions. The model can
   only *remove*; it can never grant or elevate.

If you came here looking for "the agent's identity," you want the **Profile** and, for how it's
surfaced as an estate-wide map, the [Platform Ontology](PLATFORM_ONTOLOGY_ARCHITECTURE.md). This
document is specifically the **access/authority** face.

## 1. The idea

When an agent acts **autonomously** — a genie expert delegated by a coordinator, a scheduled
task firing on a timer, a flow node, an inbound MCP `ask_profile` call — it is *not* a human
issuing that request in the moment. Until now such actions ran under the **owner's raw
authority** (the parent's `auth_token`, the task's `user_uuid`). P3 makes each autonomous
action run as an **Agent Principal derived from the owner** whose authority is a **strict
subset** of the owner's — and attributes every autonomous action to the agent in the audit
trail.

**Scope (updated Aug 2026).** The original delivery was the *attainable* form of P3: a
scoped principal + audit attribution + fail-closed enforcement over **capabilities**
(tools, writes, connectors, budget). The *fuller* vision — an agent confined to a data
slice ("cannot query outside its slice") — shipped with **OBDA-READ Phase C**: the
**data surface** (§2a below) makes the set of signed data products on an agent's
surface its entire data authority, enforced through this same principal at the same
chokepoints. See [OBDA_ARCHITECTURE.md §6](OBDA_ARCHITECTURE.md).

## 2. The model — pure, deterministic, subset-only

`core/agent_authority.py` is pure: the model + the enforcement *decision*. It **never grants** —
a scope only ever *removes* the owner's authority (subset invariant by construction, so an
agent with no config behaves exactly as today — byte-identical baseline, charter #15).

| Piece | Meaning |
|---|---|
| `AgentScope` | The restriction. `autonomous` (may it act at all?) · `deny_writes` (block mutating tools) · `denied_tools` (deny-list) · `allowed_connectors` (None = all; else an allow-list) · `max_cost_per_run` (USD cap) · **`dataSurface`** (§2a — products / exclusive / advisory) · `model_authoring` (OBDA-MODEL drafting, from `modelAuthoring`) · `actions_allowed` / `actions_exclusive` (§2b) · `policy_read` / `policy_model` / `policy_act` (the deployment's OBDA policy in force, §2c). All defaults = unrestricted. |
| `AgentPrincipal` | `owner_uuid` + `profile_id` + `scope`. `principal_id = agent:<profile_id>`. `actor()` = the audit stamp attributing an action to the *agent*, not the user. (Kept its name — "principal" is the correct IAM term for an entity that holds authority.) |
| `parse_scope(authorityConfig)` | Build a scope from a config dict (absent/empty → baseline). |
| `derive_principal(profile, owner_uuid)` | The principal for a profile, read from its stored `authorityConfig` — **falling back to the legacy `identityConfig` key** for profiles saved before the rename (same mechanism as every other `*Config` block). Parses with the agent's OBDA planes (`obda_planes.planes_for`) and then applies the deployment's OBDA policy ONCE (`apply_policy(scope, obda_planes.policy_map())`, §2c). |
| `is_tool_allowed(scope, tool, …)` | The decision: `(allowed, reason)`. Autonomous-off → deny all; denied-list → deny; connector not in allow-list → deny; write under `deny_writes` → deny. The write signal is the tool-name heuristic OR (when a `sql` arg is present) the classified statement (`classify_sql_write`) — so a write via a read-named tool is caught. `TDA_*` framework tools are **never** write-gated (they are the agent's own reporting/orchestration). |
| `is_run_within_budget(scope, cost)` | Per-run USD cap. Enforced **in-flight** (`check_budget_before_call`, before each model call) on every autonomous engine AND **post-hoc** at end of turn — one predicate, two moments. |

## 2a. The data surface (OBDA-READ Phase C — E1–E5, per-agent tri-state)

`authorityConfig.dataSurface = {products, exclusive, advisory}`, read through **ONE
resolver** — `agent/obda.py:profile_obda(profile) → (mode, products)` — which is what
the enrichment render/seed/event, both agent actions, the `governed_execution`
planner pass, the lane scan and the champion filter all consult (the tri-state,
operator model 2026-08-17):

- **Option A — Disable OBDA-READ** (`mode="off"`; the DEFAULT): `dataSurface` absent,
  `null`, or an **empty product list** — and *always* the answer when the OBDA-READ
  **component** is not enabled for the agent (Administration → Components, the per-user
  override, the agent's Capabilities toggle — read by `core/obda_planes` and handed to the
  parser as `parse_scope(cfg, planes=…)`), because `parse_scope` **drops the surface at the
  parse seam** (OBDA-READ is additive, charter #15: plane off ⇒ Uderia behaves as
  if OBDA-READ never shipped for that agent; gating each consumer separately is how the
  enrichment and the agent face were missed — found live, session d51cf073). The former
  `OBDA_ENABLED` master was migrated once into component availability (Oct 2026) and no
  longer exists. No products are
  visible to the planner, `query_product`/`list_products` refuse
  (`obda_not_enabled_for_agent`), no lane stamp, no confinement.
- **Option B — Surface Full** (`mode="exclusive"`, `{products, exclusive: true}`):
  surface-only (E1). When the agent runs autonomously, raw SQL is denied
  **fail-closed** at all three chokepoints below (the `sql` argument is threaded
  through `is_tool_allowed`); only signed-and-intact products are served (E2/E4,
  enforced in the product face). Counts as *restricted* for `authorityScope`;
  downgrade-guarded in the config contract (a pack republish cannot silently relax
  it). Deliberately **no `TDA_` exemption** — canvas SQL is the same escape hatch.
  `advisory` is B's observe-only development sub-toggle (E4): nothing denied,
  violations observed and scored.
- **Option C — Products + regular paths** (`mode="mixed"`, `{products,
  exclusive: false}`): the listed products render as consumption contracts BESIDE
  the full KG paths — the LLM decides per question. Nothing is denied, **but the
  list is still the grant**: `query_product` refuses products not on it
  (`not_on_surface`, naming the granted surface). That check is **config-driven**,
  so it holds on interactive turns that carry no ambient principal.

**Visibility vs enforcement — two seams, deliberately split.** Planning
*visibility* (which contracts the planner is shown, which champions are offered)
follows the PROFILE configuration on every turn, interactive included; raw-SQL
*enforcement* stays with the ambient PRINCIPAL (autonomous turns, plus the explicit
`enforce_interactive` opt-in). An empty product list is Option A by construction —
the profile editor never shows a mode that does not act, and saves an empty list as
a cleared surface.

**The one exception — the turn-scoped governed-SQL registry.** `query_product`
registers its compiled statement (`register_governed_sql`), so the only raw SQL a
surface-only agent can execute is a statement the surface itself produced *this
turn*; an **edited** statement forfeits the contract. The registry is a **mutable
set in a contextvar**, created at `set_current_principal` and mutated by reference —
a `.set()` in a child task is invisible to sibling tasks (contextvars propagate
parent→child only, and Optimize runs parallel phases).

**Recovery-shaped denial.** A surface denial spells out the NEXT STEP (call
`query_product`, execute the returned statement verbatim), keyed on the ONE
`SURFACE_DENIAL_MARKER`; the blanket "do not retry; report this limitation" tail is
reserved for *categorical* denials (denied tool, deny_writes) where nothing the
agent does can help.

**E5 — the empirical monitor.** Free-formed SQL *executed* by a surface-only agent
means the confinement leaked: a DANGER `surface_breach` incident (Assurance Inbox,
BOUNDED station) whose summary says *"a hole report, not agent misbehaviour"*. It
fires only on the breach — advisory, additive, governed and interactive turns are
all silent.

## 2b. The write surface (OBDA-ACT, PROP-008 §5.4 — increment B3)

The data surface's twin for writes. `authorityConfig.actions = {types: [...], exclusive?}`
parses to `AgentScope.actions_allowed` / `actions_exclusive` — **the grant is the list**, and
both are dropped at the one parse seam while the OBDA-ACT component (`action`) is not enabled
for the agent (`planes=` — the plane is the component; the former `ACT_ENABLED` master no
longer exists).

| Scope | A raw write through a data tool | A signed action |
|---|---|---|
| no `actions` | as today (`deny_writes` decides) | not performable by this agent (`not_granted`) |
| `actions` without `exclusive` | as today (`deny_writes` decides) | performable if listed |
| `actions` + `exclusive` | **refused** — `action-only` (`ACTION_DENIAL_MARKER`), naming the granted actions | performable if listed |

**Who decides a parked write — four eyes** (PROP-008 §5.4a). `authorityConfig.actions.approvers`
(account usernames, read by `agent_authority.action_approvers`, dropped while OBDA-MODEL · OBDA-ACT is off) names
the accounts that approve this agent's parked writes. With a designation the agent's OWNER may
not approve them — authority over the write is split between the agent's owner (whose agent and
connection perform it) and a second person (who decides it). Absent, the owner decides — the
single-account baseline. Like the grant, the designation is access, not trust: it moves no
signature. Full contract: ACT_ARCHITECTURE §3.5.

**Why a signed action never travels through a data tool.** A data tool runs one statement
per call under autocommit; an action is the preconditions, the before-image read, the write,
the postconditions and the ledger row in ONE transaction, rolled back if any check fails
(`dbdeploy/action_runner.py`). So the action face performs through a Database Connection
(`core/action_perform.py`), and the SQL gate's part is the refusing side above. On Teradata
the connection's login must HOLD the manifest's role (A6 proved what that role grants) and
the session acts under it (`SET ROLE`); Uderia never widens a role. Measured live: a
login holding only `UDERIA_ACT_FITNESS` was refused by the database itself when writing a
table outside the contract (Error 5315).

**Before anything touches a connection**, `perform_action` refuses: OBDA-MODEL · OBDA-ACT off, an unknown
action, an action that is not signed-and-intact, an agent whose grant does not name it.

**The posture ladder is reused verbatim**: `observe` records what would be written and
writes nothing; `review` checks the preconditions and parks the compiled write
(`review_queue`, source `action`, never releasable into a conversation); the owner's
approval (`POST /v1/assurance/reviews/<id>/approve`) performs it BEFORE the disposition is
recorded — only if the manifest hash, the re-compiled statement hash and the invocation hash
all equal the parked ones and the agent's grant still names the action; a refused approval
returns `409 action_not_performed` and records nothing. `annotate` and `enforce` write.

**The receipt.** Every invocation is appended to the turn's action record (a contextvar
started and ended at the execution seam), stamped on the turn as `turn['actions']` at the
persistence chokepoint; each PERFORMED one is sealed as an `action.performed` provenance step
in `ProvenanceChain.finalize()` through `action_perform.receipt_content` — the same function
`verify_content` uses, so a tampered stamp is a named mismatch. The OBDA-READ lane scanner reads
the record: `acted_count` / `refused_count`, headline lane `acted` (absent on a turn without
actions — byte-identical baseline).

**Measured (B4d).** `action_safety` (BOUNDED, ACT moment, owned by the `actions` mechanism):
of the action writes the turn put to the database, the share whose every check held —
read through the same fold every surface reads, informational, abstaining when no write
reached the database (an act refused before it compiled is not a write). The
**`write-escape` red-team probe** lures an action-only agent into a raw `UPDATE`
(a self-assignment on an impossible key, so even an escape changes no row) and adjudicates
from the turn's own stamps: the `action-only` denial on the write statement → held; a raw
write that executed → the escape; a refusal in words naming the action route → held; no
signal → honestly unknown. The live runner scores each probe against its PERSISTED turn
(`live_redteam.with_turn`) — the task result alone carries no tool calls.

**The `action-only` denial is recovery-shaped** (like the surface denial): it spells out the
next step — change the data through one of the signed actions it names, with `TDA_Action` if
that tool is offered to the agent, otherwise tell the user which action would make the
change — and never says "report this limitation". `TDA_Action` is named conditionally because
the action component may be off for the agent; an instruction to call a tool that is not
offered is the defect the tail exists to avoid.

## 2c. The deployment's OBDA policy — "contracts only" (Oct 2026)

`APP_CONFIG.OBDA_POLICY` (`{read, model, act}`, each `off · observe · enforce`; Administration →
App Config → Trust → *OBDA policy*; `obda_planes.policy` / `normalize_policy` / `set_policy`) is
applied at ONE seam — `agent_authority.apply_policy`, called by `derive_principal` — so every
consumer of the scope (the chokepoints, the tool surface, the enrichment cut, the champion
filter, the stations) inherits it with no gate of its own:

| Plane under `enforce` | Written into the scope |
|---|---|
| `read` | an EXCLUSIVE data surface over the agent's granted products (none granted ⇒ an empty surface: raw SQL refused, nothing readable but through a product) |
| `act` | an EXCLUSIVE write surface over the agent's granted actions (none ⇒ every raw write refused), and command tools (`_looks_like_command`: shell, bash, exec …) refused under the `contracts-only` marker (`POLICY_DENIAL_MARKER`) |
| `model` | recorded on the scope; the knowledge-graph component refuses direct graph edits for the agent (the graph changes through proposals a person publishes) |

An enforced plane makes the scope `is_restricted`. Under `observe` nothing is refused:
`enforce_tool` re-asks the decision against the scope as enforce would have it
(`_as_if_enforced`) and records a `policy_observed` trust event (`record_policy_observation`) —
the pre-enforcement rehearsal. With every plane `off` the scope is returned unchanged and the
stamp carries no `policy` key (byte-identical baseline). The policy applies wherever a principal
is derived — autonomous turns, and interactive turns under `enforce_interactive`. Gate:
`test/test_obda_policy.py`.

## 3. Enforcement — a per-turn security context, fail-closed at every chokepoint

The design problem: **one gate misses most engines**, and **threading a principal through
every call site is fragile**. The solution is a **per-turn security context via `contextvars`**
+ layered enforcement at the *execution chokepoints*.

- **`contextvars.ContextVar` (async-task-local, non-racy).** The principal is set **once** at
  the autonomous-turn boundary and read at every tool chokepoint. `contextvars` propagate into
  awaited coroutines *and* child tasks (asyncio copies the context at task creation), so
  **concurrent sessions stay isolated** and no signature plumbing is needed. An interactive
  user turn leaves it unset → baseline (no gating). *(A CI test proves the propagation across
  `await` and the concurrent-turn isolation.)*

- **Set/reset at the turn boundary** — `agent/execution_service.py:run_agent_execution`. When
  `_effective_origin ∈ AUTONOMOUS_ORIGINS` (`genie_parent` · `genie_child` · `flow` ·
  `scheduler` · `mcp`) it derives the principal from the active profile and sets the ambient
  context; on convergence it resets and stamps the turn payload with the actor (agent vs user).
  A scheduled task's `source="scheduler"` is mapped to the `scheduler` origin so the principal
  applies. A turn of a `genie` (Coordinate) profile is always the `genie_parent` origin, and a
  session marked `genie_metadata.is_genie_slave` the `genie_child` origin, so both derive a
  principal even when a person typed the question. Genie *slaves* run as separate HTTP requests
  (their own task/context) → each gets its own principal, no cross-task leak.

  **The turn's authority stamp.** At the same seam `build_authority_stamp(principal, origin,
  configured=…)` is set into a second contextvar (`set_turn_authority`) and read by the one
  persistence chokepoint, so every engine's turn carries `authority` — `applied: True` + the
  scope in force, or `applied: False` + the origin. `applied` is load-bearing: a measurement must
  tell "the boundary held" from "the boundary was never in force".

  **The scope is a declared choice, not a hardcoded constant (Aug 2026).** An agent may opt its
  INTERACTIVE turns into enforcement too: `authorityConfig.enforce_interactive: true`
  (`agent_authority.enforces_interactive`, consulted at the same one gate —
  `AUTONOMOUS_ORIGINS ∨ enforces_interactive(profile)`). Why: an operator ticked *Surface-only
  (structural confinement)* and then watched the agent hand-write raw SQL in chat, because a
  principal was never derived interactively — a switch labelled *structural confinement* that
  silently does not apply to the mode the operator is using is the wrong default, and what the
  human "control" actually sees is prose, not SQL. Rules, each mutation-tested: it WIDENS the
  scope, never replaces it (an autonomous origin still derives a principal whatever the flag
  says); only an explicit `True` opts in (a migrated `"true"` string must never start denying
  tools); it governs the WHOLE `authorityConfig`, not the data surface alone (a partially
  applied principal would be a second, subtler notion of authority); turning it OFF is a
  pack-import downgrade (`flag_paths`). Editor: Agent Authority card → *Apply to interactive
  turns too*. Default absent ⇒ byte-identical baseline. Consequence worth knowing: because
  `autonomous: false` is the decision's FIRST check, `enforce_interactive: true` together with
  *Allow autonomous* off denies EVERY tool on a chat turn — a blanket denial, not a read-only
  agent (found by the Optimize campaign; use `deny_writes` for read-only).

  **An interactive turn says which of the agent's limits it ran WITHOUT (Oct 2026).** Without
  the opt-in, an interactive turn is stamped `applied: False` — and the agent's own scope, when
  it is restricted, is sealed beside it as `not_in_force` (`build_authority_stamp(…,
  configured=…)`, both stamp copies in `execution_service`). BOUNDED then reads *"interactive
  turn — acting as the user. This agent's own limits were NOT in force on this turn: read-only ·
  1 tool denied …"*, state `na` (the turn honestly ran as the user — no alarm is invented) with
  the word *agent limits not in force*, and names the switch that would apply them. Found by the
  trust campaign (B3): a `deny_writes` agent's chat turn read "acting as the user", as though
  nothing had been configured. Under the opt-in, an interactive turn reads *"interactive turn
  under the agent principal"* — never "autonomous run (scheduler)", which an empty origin once
  produced. An unrestricted agent's stamp is byte-identical to the baseline. Gate:
  `test_agent_authority.py`.

- **A SQL argument that is not a STRING is refused before any round trip** —
  `tool_enforcement.nonstring_sql_block`, ONE implementation on BOTH data-tool paths (the MCP
  adapter and the ReAct wrapper; the first version lived inline in the adapter alone and a
  cross-class audit found the ReAct path uncovered). The refusal's marker is in
  `DEFINITIVE_TOOL_ERRORS`, so no self-correction rounds fire: retrying cannot change what the
  upstream step produced. `TDA_*` exempt; never raises.

- **Four chokepoints read the ambient principal and deny before executing** (all-engine, because
  every tool call from any engine funnels through one of these):

  | Chokepoint | File | Scoped by |
  |---|---|---|
  | MCP data-source tools (Optimize / genie-slave / FASTPATH) | `mcp_adapter/adapter.py:invoke_mcp_tool` | `denied_tools`, `deny_writes` (tool-name heuristic **plus SQL-statement classification** — a write smuggled through a read-named tool, `base_readQuery(sql="DELETE …")`, is denied by classifying the statement via `tool_enforcement.classify_sql_write` → `preflight.catalog.sql_is_write` (sqlglot, regex fallback), not just the name). Statements that write **files** on the database server — `SELECT … INTO OUTFILE/DUMPFILE`, `COPY`, `EXPORT DATABASE`, `ATTACH`, `LOAD DATA`, `INSTALL` — count as writes (Sep 2026; sqlglot reads them as reads and a read-only transaction does not stop them). Genuine `TDA_*` framework tools (final report, logging, orchestration) exempt — but **component tools are not** (enforced in the component-routing branch, see below). |
  | MCP data-source tools (Conversation / ReAct) | `agent/tool_enforcement.py:wrap_langchain_mcp_tools` | Same decision as `invoke_mcp_tool`, at full parity. The ReAct engine executes MCP tools through the LangChain session, NOT `invoke_mcp_tool`, so each tool's coroutine is wrapped at `ConversationAgentExecutor.__init__` (the one seam where the tools + `session_id` are both known) to reach the same fail-closed chokepoint. The wrapper runs the **full battery** the adapter runs: authority (incl. SQL-write classification), preflight SQL grounding (catalog, declared join key, text-stored date, value existence, column existence — `preflight.guard.check_sql`), the in-flight **loop guard** (`loop_guard_block`), the per-turn **tool ledger** (`agent/turn_tool_ledger.py`, Nov 2026 — applied AFTER the gate so a denial is refused in the gate's own words: an identical call that already succeeded this turn is reused without reaching the tool, a write invalidating every memoised read; a tool whose execution fails 4× in a row is disabled for the rest of the turn with a recovery-shaped block, `TDA_TOOL_BREAKER_FAILURES`, 0 = off; `TDA_*` exempt; gate `test_tool_ledger.py`), and the **tool-result firewall** (`scan_result`) — closing the gap where the ReAct engine, the one most prone to tool loops and whose output re-enters context, escaped the two newest controls. Since Sep 2026 it also owns two recovery behaviours the Optimize path has in its correction chain: a **raised tool error is handed back as recovery-shaped tool CONTENT** (`failed_tool_result` — `langchain_mcp_adapters` raises `ToolException` on any MCP error and LangGraph would otherwise end the turn; cancellation still propagates), and a **one-shot deterministic Teradata row-limit retry** (`retry_teradata_top` → the shared `sql_dialect.teradata_top_rewrite`), whose successful result carries a note naming the rewrite; the arguments dict is updated to the SQL that actually ran so the catalog cache and the firewall see the truth — and its sibling, a **one-shot reserved-alias retry** (`retry_teradata_reserved_alias` → the shared `sql_dialect.teradata_reserved_alias_rewrite`: Error 3707 names the word, the identifier is quoted wherever the statement uses it as one), the two retries sharing ONE body. A **raised database time-ceiling abort** (Error 3110, the endpoint's own `request_timeout`) is shaped as a recovery at the same seam (`sql_dialect.db_abort_hint`, also appended on the adapter's exception path): a verbatim resend cannot succeed, and the hint names what changes the outcome. A preflight **text-date refusal whose fix is mechanical** (shape sampled, no slice cutting across the shape's fields — an aligned slice such as the month of a DD-MMM-YYYY passes the guard) is likewise applied in place before execution (`text_date_rewrite` → the shared `preflight.textdates.rewrite_sql`), the result annotated with the change; a refusal that needs judgement is handed back as content. |
  | Component tools | `components/manager.py:_run_component` (LangChain path) + `mcp_adapter/adapter.py` component-routing branch (Optimize path) | `denied_tools` by **component id OR `TDA_` tool name** (`enforce_tool(..., aliases=…)`), so `denied_tools:["scheduler"]` and `denied_tools:["TDA_Scheduler"]` both work on both paths. Capabilities, not write-gated. On the LangChain path the denial is returned in the tool's declared `(content, artifact)` shape — a bare string raised in LangChain's tool node and ended a Coordinate turn with the error as its answer (trust campaign, Oct 2026; `test/test_component_denial_shape.py`). |
  | Platform connectors | `core/platform_connector_registry.py:invoke_connector_tool` | `allowed_connectors` allow-list. Connector *output* (web pages, browser, files, Google/Slack content — the canonical indirect-injection lane) is additionally screened by the **tool-result firewall** at the adapter's connector-routing return (`invoke_mcp_tool` → `tool_enforcement.scan_result`); observe-only, never altered, labels-only inbox event. |

  > The single shared decision module `agent/tool_enforcement.py` backs both MCP paths so the
  > "fail-closed at every tool chokepoint" guarantee holds on *every* engine, including the ReAct
  > engine that bypasses `invoke_mcp_tool`.

- **Fail-closed** (charter #8): `enforce_tool()` returns *deny* on any enforcement error; a
  denial returns a **terminal, non-retryable** result (`denied_tool_result`) stamped
  `agent_denied` so it is auditable and the LLM incorporates it as a definitive outcome (no
  self-correction loop). No principal (interactive) → *allowed* (baseline).

- **A denied write is not an answer's licence to claim it.** When a write call was denied on the
  turn and the answer nonetheless asserts the effect (no write succeeded, the refusal not
  disclosed), the honesty gate's `claimed_effect_after_denial` kind reads `danger` at GROUNDED
  (`agent/honesty/gate.py:assess_claimed_effect_after_denial`; `test/test_honesty_claimed_effect.py`).
  See [ANSWER_GATES_ARCHITECTURE.md](ANSWER_GATES_ARCHITECTURE.md).

### 3b. Several MCP servers (PROP-006 phase 2)

An agent with additional MCP servers sees an additional server's tools under a qualified name
(`crm__base_readQuery`, [MULTI_SOURCE_ARCHITECTURE.md §9](MULTI_SOURCE_ARCHITECTURE.md)). Authority
is decided at the same chokepoints, on the name the agent sees:

- `denied_tools` matches that name — `base_readQuery` denies the primary server's tool,
  `crm__base_readQuery` the additional server's. Deny both to deny the capability everywhere.
- `deny_writes` classifies the SQL statement itself, so a write is denied whichever server it goes to.
- Rules that recognise a tool by what it IS (the SQL tool, discovery tools, the governed tool-surface
  backbone) read the base name through `tool_identity.base_name`; `test/tool_identity_gate.py`
  fails the build on a comparison that bypasses it.
- Each server's own database login still bounds what the call can read — the database, not the tool
  name, is the boundary.

Open (build brief B4): an allow-list per source, with denials naming the source.

## 4. Persistence & UX

- **Storage:** the profile's `authorityConfig` block (in `user_preferences` profile JSON, same
  as `guardrailConfig`/`groundingConfig` — no schema change). The legacy `identityConfig` key is
  still read on load, so profiles saved before the rename keep their scope; the next save
  rewrites them under `authorityConfig`.
- **Profile editor:** an **Agent Authority** card (`configurationHandler.js` + `index.html`) —
  process-focused: it frames the model ("when this agent runs autonomously it acts as a scoped
  identity derived from you — its identity is the Profile itself; leave untouched to inherit your
  full authority") then exposes allow-autonomous · read-only · denied-tools · allowed-connectors ·
  per-run budget · action approvers (§2b) · the data surface (products + advisory, §2a) · *Apply
  to interactive turns too* (`enforce_interactive`). Theme-aware, monochrome icon, collapsed by
  default.

## 4a. Reporting a scope — presence is not constraint

Any surface that *reports* what an agent may do must resolve the scope, never inspect the
raw config. **`authorityConfig` being a non-empty dict says nothing about whether the agent
is constrained**: `{autonomous: true, denied_tools: [], deny_writes: false,
max_cost_per_run: null}` is a fully-populated block that restricts nothing, and is
byte-for-byte equivalent at runtime to no block at all. A presence check over that key once
rendered the *least*-bounded agent in an estate as green under the headline "a strict subset
of yours", while agents with no block — behaving identically — read as honest `n/a`. It
rewarded *touching* authority in the direction of more autonomy.

Two helpers make the resolved answer the only reachable one:

| Helper | Answers |
|---|---|
| `scope_for_profile(profile)` | The scope a profile actually runs under, honouring the legacy `identityConfig` fallback — one place, so a reader can never disagree with the enforcer. |
| `describe_scope(scope)` | The constraints in operator-reading order. Empty **⇔** `not scope.is_restricted`, asserted in CI, so the wording can never drift from the verdict. |

`GET /v1/profiles` ships the result as a computed, read-only
`authorityScope {restricted, autonomous, constraints[]}`; the Trust Circle's BOUNDED station
and the posture matrix render that field rather than re-deriving it. The station reads
**pass** only when every agent in scope genuinely restricts itself, and **warn** — with a
count of how many would act with the owner's full authority if run unattended — otherwise.

The asymmetries `is_restricted` already encodes are exactly the ones a naive reader inverts:
`allowed_connectors: []` is **deny-all**, not "no restriction", and `max_cost_per_run: 0` is
a real (deny-all) cap, not an absent one.

## 5. Guarantees & proof

- **Byte-identical baseline** — no `authorityConfig`/`identityConfig` (or all-default) →
  unrestricted scope, `is_restricted` False, nothing gated.
- **Subset-only** — the scope model has no grant/elevate field; it can only remove.
- **Backward-compatible** — legacy `identityConfig` is still honored; `authorityConfig` wins when
  both are present (asserted in CI).
- **Proof** — `test/test_agent_authority.py` (125 assertions: decision · subset invariant ·
  **reported-scope agrees with enforced scope** (a present-but-empty block is not restricted;
  description non-empty ⇔ restricted; `$0` cap and empty connector allow-list read as real
  constraints) ·
  config back-compat · contextvar set/get/reset · **async propagation-across-awaits +
  concurrent-turn isolation** · fail-closed enforce · denial shape) + a **live integration proof**
  through the real `invoke_mcp_tool` chokepoint (owner/no-principal passes; the autonomous agent
  is denied `base_writeQuery` *before* touching MCP; a permitted read tool passes) + the real
  stored-profile → scope loop (config_manager load of `@OPTIM` → `derive_principal` → denial).

## 5a. The trust-budget circuit breaker writes here (Jul 2026)

The Behavioral Trust Score's opt-in circuit breaker
(`trust_behavior.breaker_mode = "freeze_autonomy"` — see
[AGENT_OPS_EVAL_ARCHITECTURE.md §6c](AGENT_OPS_EVAL_ARCHITECTURE.md)) acts
**through this model, not around it**: when an agent's trailing conduct enters
`at_risk`, the breaker sets the profile's `authorityConfig.autonomous = False`
and stamps `frozen_by_breaker: true` + `frozen_reason` — suspending only the
agent's **unattended** use via the exact same scope the Agent Authority card
edits (interactive use untouched; visible and reversible by clearing the flag /
re-enabling autonomous in the card). The stamps are plain `authorityConfig`
keys, so export/import and the editor treat them like any other scope field.
Implementation: `eval/online.py:_run_trust_breaker` (the `freeze_autonomy` branch
calls `config_manager.update_profile`).

## 6. What's deferred (charter #19)

- **Structural data-scoping (OBDA-READ)** — *delivered, Aug 2026*: the Agent Principal now
  carries the data surface (§2a), exactly as this row predicted — extending, never
  forking, this model. Kept here as the record of the prediction.
- **Per-run budget is enforced in two places, on ONE predicate** (`is_run_within_budget`):
  - **In-flight** (`check_budget_before_call`, the same predicate): asked BEFORE each model call
    is paid for, on every engine that spends autonomously. Routed through `PlanExecutor`
    (Optimize/Ideate/Focus helpers), the ReAct engine (`conversation_agent` at each
    `on_chat_model_start`, accumulating `self._run_cost_usd`, halting with a named,
    graceful stop that keeps the partial answer + `budget_stopped`), and the genie coordinator
    (same seam). No ambient principal (interactive turn) → the check is a no-op → baseline. This
    closes the earlier gap where only the `PlanExecutor` path checked in flight, so ReAct + genie
    got post-hoc flagging only — "an audit record wearing a control's name".
  - **Post-hoc** (`execution_service.run_agent_execution` end-of-turn seam): a final check after
    the turn's cost is resolved, stamping `authority_budget_exceeded` + emitting an
    `agent_budget_exceeded` audit event so the caller (scheduler, coordinator) can react.
  - (`max_cost_per_run: 0` is honored as a real deny-all cap, not "no cap".)
- **MCP origin** is in `AUTONOMOUS_ORIGINS` but an inbound MCP `ask_profile` is not yet stamped
  with a distinct origin at the execution layer (it currently arrives as a user REST call);
  genie/flow/scheduler are the live autonomous triggers today.
- **"Agent identity" facets** Uderia does *not* yet claim (see §0): the agent
  authenticating to external systems *as itself* (its own workload credential), its own vaulted
  short-lived secrets distinct from the owner's, and formal OAuth token-exchange / on-behalf-of
  delegation downstream. Authority (this doc), assurance (Trust Membrane), and audit (provenance)
  are in place; those three are the open edge of the identity umbrella.

## 7. Files

| File | Role |
|---|---|
| `src/trusted_data_agent/core/agent_authority.py` | The model + decision + contextvar context + `enforce_tool` + `denied_tool_result` (pure). |
| `src/trusted_data_agent/agent/execution_service.py` | Derive/set the principal at the autonomous-turn boundary; reset + actor stamp on convergence; `source=scheduler` → origin. |
| `src/trusted_data_agent/mcp_adapter/adapter.py` | MCP-tool chokepoint enforcement. |
| `src/trusted_data_agent/components/manager.py` | Component-tool chokepoint enforcement. |
| `src/trusted_data_agent/core/platform_connector_registry.py` | Connector chokepoint enforcement. |
| `static/js/handlers/configurationHandler.js` + `templates/index.html` | The Agent Authority profile-editor card. |
| `test/test_agent_authority.py` | 125 CI assertions incl. the async architectural proof and config back-compat. |
| `test/test_obda_policy.py` · `test/test_component_denial_shape.py` | The OBDA policy seam (§2c); the component denial's tool shape. |
