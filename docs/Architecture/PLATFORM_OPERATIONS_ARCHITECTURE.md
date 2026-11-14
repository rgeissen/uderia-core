# Platform Operations Architecture

**Component:** `platform` (`TDA_Platform`) · **Package:** `components/builtin/platform/` · **Runtime:** `src/trusted_data_agent/core/platform_operations.py` · **Descriptors:** `src/trusted_data_agent/api/operations.py` · **Companion skill:** `platform-expert` · **Since:** Oct 2026 · **Last updated:** 2026-10-31

The Platform component lets an agent use Uderia itself: it **finds** the platform operations that could answer a question about the user's own agents and resources, and **calls** the ones it may — as the user, through the same REST routes and checks as any other client.

What an agent may do is never a list of use cases. The REST API **describes itself**: every route carries a descriptor in code, and ONE policy derived from the descriptor decides what any operation is to an agent. A route nobody described is not an operation. The component adds no execution path of its own — a call runs the real route in-process, so the REST layer's authentication, ownership checks and governance are the policy for what the call does.

It **never** calls a person's act (signing, activating an agent for REST use, attestation, credentials, the default agent), and in phase 2 it **never** writes or reads other users' data. It is **off for every agent** until the agent's owner turns it on.

| Phase | Scope | Status |
|---|---|---|
| 1 | `@operation` descriptors, the catalog, the coverage gate, the drafting tool | implemented — `/v1/profiles` described (24 of 809 routes) |
| 2 | `TDA_Platform` — find and call, **reads only** | implemented |
| 2b | **roles** — what an agent is FOR, across namespaces (the Platform component's `roles/`, declared in its manifest); four active, four planned | implemented (§17) |
| 3 | **writes**: the write mode (`authorityConfig.platformSurface.writes`), approval in the review queue, run as the owner on approval; the Agent Builder active | implemented (§11) |
| 3b | cross-user containment (the Admin Monitor) · four-eyes approvers for platform writes | planned (§11) |
| 4 | the MCP server on the same catalog and policy | planned (§11) |

---

## 1. Files

| File | Role |
|---|---|
| `src/trusted_data_agent/api/operations.py` | the descriptor (`@operation`, `Arg`), `build_catalog(app)` — pure, reads the served routing table, fails closed |
| `src/trusted_data_agent/core/platform_operations.py` | the runtime: the catalog of the running app, the policy (`availability`), `find`, `bind`, `call`, `project`, `bound` — shared by every face |
| `components/builtin/platform/manifest.json` | tool definition, `profile_defaults.enabled_for: []`, `signals`, `instances: none` |
| `components/builtin/platform/handler.py` | `PlatformHandler` — validates, checks enablement, decodes arguments, delegates to the runtime; headless |
| `components/builtin/platform/instructions.json` | intensity-keyed `none · medium · heavy` — the correctness baseline |
| `skills/builtin/platform-expert/` | the companion skill — the judgement layer |
| `src/trusted_data_agent/auth/middleware.py` | `require_auth` / `require_admin` record `__auth_tier__` on the route |
| `src/trusted_data_agent/main.py` | registers the served app: `APP_STATE["quart_app"]` |
| `maintenance/platform_operations.py` | `report` · `backlog` · `docs` · `draft --prefix … --apply` |
| `docs/RestAPI/PLATFORM_OPERATIONS.md` | the catalog, **generated** from the descriptors |
| `test/platform_operations_backlog.txt` | the routes not yet described — may only shrink |

---

## 2. Tool contract (`TDA_Platform`)

| Argument | Type | Used by | Meaning |
|---|---|---|---|
| `action` | string, required | both | `'find'` or `'call'` |
| `query` | string | find (required) | what the agent wants to see or do, in a few words |
| `op` | string | call (required) | an op id exactly as `find` returned it, e.g. `profiles.list` |
| `args` | dict | call | the operation's arguments by name — path, query and body alike |
| `fields` | list[str] | call | keep only these keys of every record in the answer |

`args` is advertised as a JSON-encoded string (the component manager's rule for open objects, valid for every provider) and decoded before the handler runs; the handler also accepts a raw JSON string or a native object (the Optimize lane passes arguments as planned). `fields` may also arrive as a JSON array string or a comma-separated list.

**find** returns, per operation:

```json
{"op": "profiles.components", "summary": "Every component with its effective enabled state for an agent",
 "effect": "read", "callable": true,
 "args": {"profile_id": "required: string — the agent's (profile's) id"}}
```

with `notes` when the descriptor has them, and `why_not` when `callable` is false. The envelope carries `searched` (how many operations were visible to this user) and, when nothing matched, a `note` saying how to ask.

**call** returns `{"ok", "op", "status", "result"}` — `ok` is true only for a 2xx — plus, when they apply: `summarised` (the answer was too large and the operation's summary fields were shown, §7), `shortened` (a sentence saying what was cut), `fields_not_found` with `record_fields` (a requested key that matches nothing is named, with the keys the records do carry — never silently dropped). A refusal returns `{"ok": false, "error": "<a sentence>"}` and never raises; an unknown op id's refusal names the likely operations **inside the sentence** (`Did you mean: profiles.list (…; no args) · …`) and again as `did_you_mean`, because some lanes hand a correction step only an error's text. The payload is headless (`metadata.suppress_inline`); a refusal also sets `metadata.error`, so the turn records it as a failed tool call.

---

## 3. The descriptor (`api/operations.py`)

Every route declares what it is, directly under its `…route(…)` decorator:

```python
@rest_api_bp.route("/v1/profiles/<profile_id>/connector-settings/<server_id>", methods=["PUT"])
@operation("profiles.connector_settings_update", effect="write", scope="own", governs=True,
           summary="Opt an agent in or out of a platform connector and choose which of its tools it gets",
           path={"profile_id": "the agent's (profile's) id", "server_id": "the connector's id"},
           body={"opted_in": Arg("integer", "1 opts in, 0 opts out, omitted keeps the connector's default", enum=(0, 1)),
                 "user_tools": Arg("array", "the connector's tools the agent gets (omitted: all)")})
```

| Field | Values | Meaning |
|---|---|---|
| `op` | dotted lower_snake | unique; names exactly one (method, path) |
| `effect` | `read` | changes nothing the platform keeps — a POST may still be a read (a query, a compile, a test run) |
| | `write` | changes what the platform keeps |
| | `human_only` | a person's act — listed so an agent can name it, never callable by one |
| | `never` | not an operation (streams, callbacks, blob transfer, deprecated routes) — absent from the catalog |
| `scope` | `own` · `shared` · `cross_user` · `instance` | the caller's · published to the caller · other users' (admin only) · the deployment's |
| `tier` | `user` · `developer` · `admin` | the lowest user tier offered it |
| `governs` | bool | a write that changes what an agent may do (grants, postures, composition) |
| `path` / `query` / `body` | `Arg(type, doc, required, enum)` or a doc string | arguments; path parameters are read from the route, `path` only documents them |
| `dry_run` | an argument name | the argument that turns a write into a plan (phase 3) |
| `summary_fields` | e.g. `("id", "tag", "name", "profile_type")` | the keys that identify one record of a list answer — shown instead of cutting the list when the answer is too large and no `fields` were named |
| `methods` | e.g. `("GET",)` | required when one handler serves several methods — stack one `@operation` per method |
| `notes` | text | what else the model should know (e.g. "fields not sent are kept") |

The decorator is **metadata only**: it returns the handler unchanged and never raises, so a typo fails the gate, not the server's start. `build_catalog(app)` reads every served rule and **leaves out** (and reports) any descriptor that breaks a rule:

- an unknown effect, scope, tier or argument type; an empty summary; an op id that is not dotted
- an op id that already names another route; a method described twice; a method the route does not serve; an ambiguous descriptor on a multi-method handler
- a documented path parameter the route lacks; a path parameter declared as a query or body argument; one argument in two places
- `governs` on anything but a write or a person's act; `dry_run` on a read, or naming an undeclared argument
- `cross_user` below tier `admin`; a tier **below the route's own guard** — `require_auth` records `__auth_tier__ = "user"`, `require_admin` records `"admin"` (the stricter guard wins when stacked), so an admin-only route can never be offered to a user

---

## 4. The policy (`core/platform_operations.availability`)

One function, from the descriptor alone. The caller's tier is checked first.

| The operation | To an agent |
|---|---|
| `never` | not listed, unknown to a call |
| above the user's tier | not listed, unknown to a call (the user could not call it either) |
| `human_only` | listed with `callable: false` and *"a person's act — the user does this in Uderia; an agent never does"* |
| `write` | listed with `callable: false` — not offered yet (phase 3) |
| `read` on `cross_user` | listed with `callable: false` — not offered yet (phase 3: containment comes first, even for an admin) |
| `read` | callable |

The user's tier is read per call from the user record (`profile_tier`; the legacy `is_admin` flag reads `admin`). No user, or an inactive one, is no tier: nothing is listed and nothing is called.

---

## 5. find — ranking

`find(query, tier)` searches only what the user's tier lists, and ranks with two signals:

1. **Words, weighted by rarity.** Each operation's text is its op id, summary, notes and the literal segments of its route, split on camelCase, `_` and `-`, lower-cased, stop words removed, simply stemmed. Every word the query shares with an operation counts `log(1 + N / df)` — a word every operation in a namespace shares ("agent") says little, a rare one ("default", "metric") says a lot.
2. **Meaning**, through the tool surface's own ranker (`agent/tool_semantics.similarity`) — the platform's RAG embedder, catalog vectors cached by content, the same calibrated rule: a floor (`FLOOR`), a share of the question's best match (`RATIO`), a cap (`CAP`). Until the embedder is warm the first call starts warming it and words rank alone; it never blocks a turn.

Ties go to what can be called, then to the more general operation (the shorter op id). At most `FIND_LIMIT` (8) operations return. A query with no usable words finds nothing (no guessing); no match returns a note suggesting the platform's words.

The point of find is that **the agent's context stays the same size however large the API grows**: the tool schema is fixed, and only the few operations a question needs are ever described to the model.

---

## 6. call — the path of one call

```
TDA_Platform(action='call', op, args, fields)
  │ handler: validate · is Platform enabled for THIS agent? (enablement.is_enabled) · decode args/fields
  ▼
platform_operations.call(op, args, user_uuid)
  │ 1 catalog of the running app (built once per app)
  │ 2 the user's tier — none → refuse
  │ 3 op known, not `never`, within the tier — else "unknown operation", with the closest ids
  │ 4 availability — a person's act / a write / other users' data → refuse, naming why
  │ 5 bind — unknown args named with the accepted ones; missing required named with its type;
  │          values coerced to the declared type; enums held; empty optionals dropped;
  │          an argument named `profile_id` resolved to one of the CALLER's agents by id or
  │          @TAG (RESOLVERS) — a value that is neither is refused, listing their tags
  │ 6 the URL — path parameters percent-quoted (a `path:` converter keeps its slashes);
  │          query encoded (booleans lower-case, lists repeated); body as JSON for non-GET
  │ 7 dispatch — app.test_client().open(path, method, query, json,
  │          Authorization: Bearer <create_operation_token(user, method, path, op)>,
  │          X-Uderia-Operation: <op>)
  ▼
the REAL route (auth middleware → handler → its own ownership and governance checks)
  │ status + body, returned as they are — a 404 or 403 stays a 404 or 403 (ok=false)
  ▼
project(body, fields) on a 2xx · bound(…) → {"ok", "op", "status", "result", "shortened"?}
```

- **Refusals happen before any route runs.** Steps 2–5 never reach the app: a refused write, a person's act or an over-tier read leaves no trace in the route.
- **As the user, never more.** The route sees exactly the user — what the user could not do over REST, the agent cannot do here.
- **A token for this one request.** `auth/security.create_operation_token` mints a JWT carrying the user id, `internal: true` and an `op_scope` — the operation, the method and the request path as the app routes on it (percent-decoded) — valid for 60 seconds and never stored; it never leaves the process. `verify_auth_token`, the one function every bearer entry point verifies through (the middleware, `get_user_id_from_auth`, the `?token=` routes), refuses it on any other method or path, outside a request, expired, or with a malformed scope — before any database read. The middleware never caches it, because its user cache is read before verification. The policy in steps 2–5 chooses what may be called; the token is a second, independent check that the dispatch can reach nothing else, even if the policy were wrong. (`create_internal_token` — unbound, 30 minutes — stays for the Genie coordinator and the flow executor.)
- **In-process, not over the network.** The app's ASGI test client runs the request inside the serving process, with its request hooks, so the call depends on no port, no loopback and no proxy. (Older internal callers go over HTTP to the same process — the flow executor to `127.0.0.1:<server_port>`, the Genie coordinator to `localhost:5050`.)
- **Never raises.** A dispatch failure is caught and returned as `"<op> failed inside the platform: …"`. Every call is logged `[platform-ops] <op> <METHOD> <path> → <status> (user …)`.

---

## 7. Shaping the answer

**`fields` — projection.** `project(obj, fields)`: a dict that holds any requested key is reduced to those keys; any other dict or list is walked. `{"profiles": [{…42 keys…}], "default_profile_id": …}` with `["tag", "name", "profile_type"]` returns every agent with three keys each, and keeps the wrapper's own fields. Projection never reshapes a refusal (non-2xx).

**Summary fields.** When a 2xx answer exceeds the limit, no `fields` were named and the operation declares `summary_fields`, it is projected to them first (`summarised` says so). `profiles.list` declares `id`, `tag`, `name`, `profile_type`: all agents come back, each identifiable, instead of the head of a list of 42-key records.

**`bound` — the limit.** An answer whose JSON still exceeds `RESULT_CHARS` (12,000 characters) is shrunk step by step until it fits, in this order:

| Step | Lists keep | Strings keep | Nested parts |
|---|---|---|---|
| 1 | 100 items | 2,000 chars | whole |
| 2–4 | 100 | 600 → 300 → 160 | summarised below depth 4 → 3 → 2 |
| 5–8 | 50 → 25 → 10 → 5 | 120 → 80 → 60 → 60 | summarised below depth 2, then 1 |

A summarised part says its size (`"{8 fields}"`, `"[6 items]"`), a cut string says how many characters it held back, a cut list ends with `"… N more"`. **Nested detail goes first**, so a list of large records keeps each record's plain fields (id, name, tag) longest, and **a list of plain values (ids, names) keeps every item until the last two steps** — it is small, and it is often exactly what was asked (a 17-id list cut to 5 once read as "this agent is not in it"). The `shortened` note says what was cut and tells the agent to call again with `fields`; the companion skill forbids reporting a shortened list as complete. Measured on a local instance: `profiles.list` (36 agents) bounds to 11 KB; with three fields it returns all 36 in 4.2 KB.

---

## 8. Deterministic layer vs skill layer

| Layer | Carries |
|---|---|
| **Code** (`platform_operations`, `operations`) | everything decidable: what is listed and callable, ranking, argument checking, the URL, identity, projection, bounding, every refusal sentence |
| **`instructions.json`** | the correctness baseline: find first; never invent an op id or argument; `callable: false` is stated, never retried; correct a named error once; `fields` for long answers; no emoji |
| **`platform-expert`** | judgement: describing what to find (not the user's whole sentence), asking for few fields on the first call, resolving a tag to an id before an operation that takes ids, IFOC class names (`llm_only` → **Ideate**, `rag_focused` → **Focus**, `tool_enabled` → **Optimize**, `genie` → **Coordinate**), summary first, nothing beyond what the platform returned, and how to name a person's act |

Design test: with the skill deleted, an agent still calls find before call, uses exact op ids and arguments, and does not retry a person's act — the instructions say so.

---

## 9. Governance and enablement

- **Off until turned on.** `profile_defaults.enabled_for` is empty, so under the one enablement rule (`components/enablement.py`) no agent has Platform unless its `componentConfig.platform.enabled` is true. The profile editor lists Platform under Capabilities from `/api/v1/components`, default off. No existing agent gained the tool, its companion skill or its instructions when the component shipped.
- **The admin gate** (Administration → Components, and the per-user override) applies as for every component: a globally disabled Platform is offered to no agent.
- **Enabling it is a capability change.** `componentConfig` is part of the agent's signed composition (`profile_config_contract`), so turning Platform on changes what a signed agent is.
- **The companion follows.** `platform-expert` is linked by `component_id: "platform"` in `skills/skill_registry.json` and auto-assigns where the component is enabled (`resolve_auto_skill_specs`), overridable per agent.
- **Refused where not enabled.** The handler re-checks enablement for the calling agent, so a call that arrives anyway (a replayed plan) is refused with *"its owner has not turned it on … Do not retry; tell the user."*

---

## 10. Security properties

What an agent with Platform enabled can never do, and what holds it:

| Never | Held by |
|---|---|
| call a person's act | the policy (`human_only` is never callable), refused before the route runs |
| write without its role allowing it, or with its write mode off | the role (§17) and the write mode, checked before any route runs |
| change what an agent may do, or delete anything, without a person | `governs` and every `DELETE` are submitted for approval whatever the mode |
| run an approved request that no longer holds | `perform_parked` re-checks it against the current catalog; refused requests stay open |
| see a credential | `redact` withholds credential-like keys in every result; credential entry is `human_only` |
| read or change other users' data | the policy (`cross_user` is not offered, even to an admin, until containment) |
| reach a route above the user's tier | the descriptor tier (never below the route's guard — §3) and the per-call tier check |
| reach a route nobody described | the catalog lists only described routes; an undescribed route is not an operation |
| act as anyone but the turn's user | the token names that user, taken from the turn's server-side context, never from the model; the route authorises it like any client |
| reach any route but the one the policy chose | the call's token is bound to that method and path for 60 seconds; `verify_auth_token` refuses it anywhere else, and the middleware never caches it |
| inject into a route path | path parameters are percent-quoted; an argument the descriptor does not declare is refused |
| start an agent turn | the operations that run turns create sessions — writes, not offered |

Because every callable operation is a read of the user's own data (or of the deployment's, at the tier that may see it), what Platform exposes to the model is what the user could already see through the UI or REST.

---

## 11. Writes (phase 3) — and what comes next

**Who may write.** A write is offered only to an agent whose **role** names the operation and
allows the `write` effect (§17 — the Guide reads only; the Agent Builder writes) **and** whose
**write mode** permits it — `authorityConfig.platformSurface.writes`:

| Mode | An ordinary write | A write that `governs` an agent, or any `DELETE` | A person's act |
|---|---|---|---|
| `off` (default; anything unreadable reads as off) | refused, naming the mode | refused | never |
| `review` | **submitted for approval**, not run | submitted for approval | never |
| `allow` | **runs**, as the user | submitted for approval — always | never |

The mode is **authority**, not capability: portable, unsigned (it legitimately differs per
deployment) and **downgrade-guarded** — `off < review < allow`, a pack update that widens it is
flagged (`profile_config_contract._authority_downgrades`). The role is capability and signed
(§17); the two never stand in for each other.

**Submitted for approval** (`platform_operations.park_write`): the exact request — operation,
bound arguments, the agent, the conversation — is parked in the platform's one review queue
(`core/review_queue.py`, `source="platform"`), the owner gets a live notice, and the agent is
told *"it has NOT run"*. It appears in the **Assurance Inbox** as its own row — *"An agent asks
to: Create an agent"*, the operation, every argument — with **Approve — run it** and **Reject**.

**Approval** goes through the same route as every parked write (`POST
/v1/assurance/reviews/<id>/approve`, four-eyes-aware): `platform_operations.perform_parked`
checks the request against the **current** catalog — the operation still exists, is still a
write an agent may submit, its arguments still bind — and only then runs the real route **for
its owner, as the owner**, with a one-request token. Anything that no longer holds is refused,
nothing runs, and the item **stays open** with the reason (`409`); a route that refuses the write
reads the same. A rejection records the decision; nothing runs.

**References and tags are checked in code before anything is submitted.** `llmConfigurationId`
and `mcpServerId` resolve like `profile_id` — one of the caller's own, by id or name, anything
else refused with the valid ones listed — and a `tag` is written without `@`. The parked request
holds the arguments **as they will run** (bound, normalised, resolved), so what a person approves
is exactly what runs. (Found live: a model sent `tag: "@PLAYGROUND"` and its default *agent's*
id as the model; the agent was created with an `@` in its tag and pointing at no model.)

**Measured live** (`@BUILDER` — Guide + Agent Builder, writes `review`, Gemini 3.1 Flash Lite —
one targeted case, twice: before and after the fix above): asked to create an Ideate agent, it
read `building/create-agent`, submitted one `profiles.create` request and told the user it awaits
approval; nothing existed until the request was approved through the Inbox's decision route,
which then ran it as the owner (201). Cost: $0.0065 and $0.0091.

**Open pages are told when a write ran.** A write that ran (approved, or under `allow`) raises
one live notice, `platform_change` (`platform_operations.announce_change`: the operation, its
namespace, its method); a parked request, a refused write and a read raise none. The page relay
(`notifications.js`) maps the namespace to its views — `profiles`, `llm`, `mcp` reload the shared
agent list (`configState.loadProfiles`) and emit `uderia:profile-modified`, which every view that
lists agents follows (Agent Anatomy drops its cache and rebuilds). Found live: an approved delete
left the deleted agent selectable in Agent Anatomy — the write had happened on the server, and no
page was told.

**Credentials never reach a model.** Every result an agent receives passes `redact`:
credential-like keys at any depth (`credentials`, `password`, `secret`, `api_key`,
`authorization`, `*_token` — but not `*_tokens`, which are usage) read `[withheld — a credential]`.
Entering credentials stays a person's act: creating or changing an LLM configuration or an MCP
server is `human_only`, and the endpoint that returns decrypted credentials is never offered.

**Next.** *Four-eyes for platform writes* — `authorityConfig.platformSurface.approvers`, the
OBDA-ACT precedent (`actions.approvers`): the agent's owner then cannot approve. *Cross-user
containment* — before the Admin Monitor becomes active: an agent granted other users' data may
not learn into a shared planner repository, write memory, be published or shared, or serve a
non-admin's coordinator — derived from its role, never left to configuration. *Role allowances
per user tier* (§17). *Phase 4* — the MCP server on the same catalog, policy and roles.

**Phase 4 — the MCP server.** `mcp_server/` serves the same catalog through the same runtime (`find`, `call`) and the same policy, replacing its hand-curated tool list and its single-token-per-process limitation with per-request identity.

---

## 12. UI and REST

No renderer and no panel: Platform has no Workspace section (the Workspace gate names the exclusion — *headless: no panel*). The owner's only control is the Capabilities toggle in the agent editor. No new REST endpoint: the catalog's reader is the generated [PLATFORM_OPERATIONS.md](../RestAPI/PLATFORM_OPERATIONS.md).

---

## 13. Describing a namespace

```bash
python maintenance/platform_operations.py draft --prefix /api/v1/<ns> --apply   # first passes, marked # DRAFT
#   review each against its handler: does it change anything kept (effect)? whose data (scope)?
#   which tier? does it change what an agent may do (governs)? a summary in the user's words,
#   the arguments it reads; then remove the # DRAFT mark
python maintenance/platform_operations.py backlog --write
python maintenance/platform_operations.py docs --write
```

The drafter guesses effect, scope, tier and `governs` from the method, the path and the auth guard, and the arguments from the handler's source (`request.args.get`, the JSON body's `.get` and index reads — not the fields the handler fills in itself). Every guess is reviewed; the gate refuses a descriptor still marked. Once a namespace is finished, add it to `COMPLETE` in the gate so the backlog cannot take a route back.

---

## 14. Gates

| Gate | Holds |
|---|---|
| `test/test_platform_operations.py` | every descriptor rule (each reported **and** left out); the auth guards' tiers; every served route described or in the backlog, nothing stale in the backlog; finished namespaces stay finished; the generated catalog is current; no `# DRAFT` mark left; the drafter's ids, guesses and argument extraction |
| `test/test_platform_tool.py` | the call's token: accepted on its request only, refused on another path or method, outside a request, expired, forged or malformed, never cached, and refused end to end by the real app on another route; the policy per effect, scope and tier; find's ranking, limit and visibility (never `never`, never above tier), the semantic rule; argument binding, coercion, enums and URL quoting; projection and bounding; call runs the real route as the user with the operation named, returns a route's refusal as it is, and refuses before any route runs; the component's defaults, validation, enablement refusal, argument decoding and companion; the served app registered for dispatch. Mutation-tested (allowing writes, dropping path quoting, bypassing the refusal, removing the token's scope check, caching bound tokens each turn it red) |
| `test/test_primary_server_reads.py` | a descriptor that documents `mcpServerId` is not counted as a read |
| `test/test_ui_spacing_and_refresh.mjs` | Platform's Workspace exclusion is named |

All four run in `test/trust_battery.sh`. `test_platform_tool.py` also pins every live-probe finding (§15): agent resolution, `fields_not_found`, plain lists kept whole, summary fields, the planner's argument names, inline suggestions.

---

## 15. Two lanes, and what a live run measured

**`test/platform_live_probe.py`** runs real turns on two probe agents it owns (`@PLIDT` Ideate + tools, `@PLOPT` Optimize; the trust campaign's weak model, Amazon Nova 2 Lite) and checks what the platform **did** — the operations the server log records as dispatched (`[platform-ops]`), and the user's agents, default and REST list before and after each turn — not only what the model said. It appends its agents to the REST consumption list in order (a whole-list write: read, append, verify) and `teardown` deletes them.

**The ReAct lane (Ideate + tools)** calls `TDA_Platform` turn by turn with the tool's own schema. **The Optimize lane** plans every phase up front, often before any `find` has run, and writes arguments blind. Three things serve it:

- **Not on the component fast path.** `is_deterministic` is false: a `call` depends on what an earlier `find` returned, which only the tactical planner sees. (A phase whose plan already carries every argument still runs through the executor's FASTPATH.)
- **Argument names normalised in code** (`handler.normalize`): `operation` / `op_id` / `search_terms` / a nested `arguments` become `action` / `op` / `query` / `args`; a top-level `profile_id` moves into `args`, `fields` out of it. The action is inferred only from what is present (an op id → call, words → find) — an operation is never guessed.
- **Every refusal is self-contained text** (the unknown-op suggestions above), and the handler validates itself on every lane.

Measured 2026-10-31, five cases per lane (the agent list · an agent's components · "is @TCOPT open to REST" · "make @PLIDT my default" · "delete @TCFOC"):

| Run | Ideate + tools | Optimize | What changed before it |
|---|---|---|---|
| 1 | 3 / 5 | — | — (a wrong agent's components reported; a cut id list read as "not open") |
| 2 | 5 / 5 | 0 / 5 | agent resolution by @TAG, `fields_not_found`, plain lists kept |
| 3 | 5 / 5 | 3 / 5 | normalisation, tactical path, did-you-mean |
| 4 | 5 / 5 | 3 / 5 | suggestions inside the error text |
| 5 | 5 / 5 | 2 / 5 | summary fields |

In every turn of every run the platform held: no agent, default or REST list changed, only reads were dispatched, a person's act and a write were declined. **The Optimize lane on a weak model is not reliable for platform questions**: its planner is built around its MCP database, and on "agents" it writes SQL against tables that do not exist, or picks a neighbouring operation. That is the model's plan, not the tool's plumbing — on the questions where it reached the tool, the tool answered right. Recommend Platform on Ideate + tools agents; re-measure Optimize on a stronger model before recommending it there.

---

## 16. Use cases as progressive playbooks — the Platform Expert agent

The component stays generic; **use cases live in a progressive skill**, `platform-playbooks`
(`skills/builtin/platform-playbooks/`): a short root on every turn (the rules that always
hold, the fetch discipline, and one line per playbook naming the questions it answers) and
playbooks read on demand through `TDA_SkillLibrary`. Each playbook says which operations to
call, how to read the result (the trust states in words, never the wire string) and what to
tell the user. Twenty playbooks cost about what one costs; an agent can be granted a slice of
them (anchor grants).

| Playbook | Calls |
|---|---|
| `agents/inspect` | `profiles.list` (with fields) · `profiles.components` · `profiles.skills` · `profiles.resources` |
| `trust/explain-agent` | `trust.agent` — names a person's acts (`trust.agent_sign_off`, `trust.agent_attest_deployment`) |
| `trust/my-agents` | `trust.agents` |
| `usage/this-period` | `consumption.summary` · `consumption.turns` · `consumption.history` |

**Built, never hand-edited:** `scripts/platform_playbooks.py build` regenerates the index
(anchor, title, summary, file, sha256) and the root's list. **Tied to the catalog:**
`test/test_platform_playbooks.py` holds every op id a playbook names to the SERVED catalog,
every op it calls to what an agent may call at the user tier, and every op it names without
calling to a person's act — a renamed route or a changed policy fails the gate, not a user.

**The agent:** `scripts/platform_expert_agent.py ensure [--llm …] [--rest]` provisions
**@PLATFORM** on any instance through its REST API — Ideate, no MCP server, `platform` +
`skill_library` + `memory` on and every other action component explicitly off,
`platform-playbooks` active (`platform-expert` follows as companion), the default agent's
model unless `--llm`. It never signs the agent.

**Measured 2026-10-31** (`test/platform_live_probe.py playbooks`, @PLATFORM on Gemini 3.1
Flash Lite, five cases: inspect · explain trust · trust overview · usage · "sign it for me"):

| Run | Hard checks | Playbook read (before the first platform call) |
|---|---|---|
| 1 | 4 / 5 | 3 / 4 |
| 2 (root lines carry each playbook's *Use for* questions) | 3 / 5 | 1 / 4 |

In both runs nothing changed, nothing was signed, and wherever the playbook was read the
answer was right and in words. **A skipped read is the real weakness** (SKILL_ARCHITECTURE
§4a predicted it): in every skipped case the model went to `TDA_Platform` `find` first and
answered from the raw result — a table without counts, once a configuration test instead of
the components. One run each cannot say whether the root's question cues help.

**find delivers the playbook (built 2026-11-01).** Every skipped read began with `find`, so
`find` is where selection moved from the model to the platform. When the agent holds active
progressive skills, `find` ranks their sections that call platform operations against its query
(`platform_operations.rank_playbooks`, pure): each shared word by its rarity across the
playbooks, and only through a word that sets a playbook apart (one every playbook shares never
qualifies); a playbook calling one of `find`'s top operations gains a step, more for the first
than the third; a query naming one agent (`@TAG`) favours a playbook about one agent. A clear
leader is **read for the agent** through the skill library's own read path — the agent's
pinned version, its anchor grant (enforced there a second time), the size cap — and returned
as `playbook` beside the results, carrying the `skill_section_loaded` event, so the turn
records a read exactly as for a `TDA_SkillLibrary` call and citation honesty sees no
difference. Sent once per turn (pointed to after); two equal leaders are named as
`playbooks.candidates` and neither is sent. Gate: `test_platform_playbooks.py` §E–§F.

Measured after it (three runs, the same five cases):

| Run | Hard checks | Playbook in hand before the first operation |
|---|---|---|
| 1 | 4 / 5 | 4 / 4 |
| 2 | 5 / 5 | 4 / 4 |
| 3 | 3 / 5 | 4 / 4 |

Reads went from 1–3 of 4 per run to 4 of 4 in every run. What remained was the model not
following a playbook it holds — `agents/inspect` stopped after its first of three calls — so
the aggregation moved into code: **`profiles.overview`** (`GET /v1/profiles/<id>/overview`)
answers one agent in one call by CALLING the components, skills and trust routes (never
re-deriving them) and adding class, model, data source, default and REST use; a part it could
not read is named in `not_read`, never shown empty. Two more deterministic corrections: a
call's arguments written into `query` as JSON are the arguments, and a call that names one of
the agent's playbook anchors (`agents/inspect`) hands that playbook over instead of answering
"unknown operation". A `find` query that is an exact op id selects the one playbook that calls
it. After them, three runs: **15 of 15** hard checks, playbook in hand 12 of 12.

**Testing it costs model calls — test what changed.** The gates are deterministic (no LLM).
`test/platform_live_probe.py` makes real calls (this whole effort: 115 turns, $0.84, mostly
repeated full rounds), so it refuses to run without named cases (`--case inspect`, or `--all`
on purpose) and stops at a spend cap (`--max-usd`, default 0.05).

---

## 17. Roles — what an agent is for, across namespaces

### The idea

A **namespace** is how the API is organised (`profiles`, `trust`, `flows`) — technical, and
the unit routes are described in. A **role** is what an agent is *for* — "Guide", "Agent
Builder", "Automation Builder" — and a job spans namespaces: an Automation Builder needs
`flows` and `scheduled_tasks`, but also the agent list (agents are nodes), components, the
repositories and files a flow reads, the database connections it queries. A role names those
operations across namespaces, with the effects it may use, and the playbooks it is taught.

### Roles are not user tiers

| | User tier (`user` · `developer` · `admin`) | Role |
|---|---|---|
| Describes | who the **user** is | what the **agent** is for |
| Lives on | the user account | the agent (`componentConfig.platform.roles`) |
| Gates | each **operation**, through its own descriptor (`tier`) | which operations the agent is **offered** |
| Example | an admin may read every user's consumption | the Admin Monitor role names those operations |

They meet only in the intersection — what an agent may do is **its roles' surface ∩ each
operation's own policy** (its tier, person's acts, cross-user). A role carries no tier (the gate
refuses one): an Admin Monitor role on a non-admin's agent finds nothing callable, because its
operations are admin-only. *Which tiers may grant which roles* ("role allowances") is a later
phase, deliberately not built.

### A role is data

`components/builtin/platform/roles/<id>.json` — part of the Platform component, versioned,
reviewed, gated, shipped with the code. The component's manifest declares them (§21); until Nov
2026 they lived in `platform_roles/` at the repository root:

```json
{
  "id": "guide", "title": "Guide", "status": "active", "rank": 1,
  "description": "Explains the user's own estate — …",
  "effects": ["read"],
  "operations": ["profiles.*", "!profiles.metric*", "trust.*", "consumption.*"],
  "playbooks": ["platform-playbooks:*"],
  "needs": ["sessions"]
}
```

`operations` are op-id globs across namespaces, `!` excludes (an exclusion wins). `effects`
are what the role may *call* — `read`, later `write`; a person's act is never a role's effect.
`playbooks` are `<skill>:<anchor glob>` — the slice of playbooks the role is taught. `needs`
lists the namespaces it is still waiting for; a `planned` role grants nothing until it is
`active`. `rank` is its place in the work order.

### Granting, signing, resolving

- **The grant** is `componentConfig.platform.roles` — **capability**, so signed:
  `profile_config_contract._canon_component_config` hashes `platform:role=<id>@<digest>` for every
  held role, the default included — the digest covers what the role grants (§21), so a widened
  role moves the signature of every agent holding it. The write mode (phase 3) is **authority** and goes to
  `authorityConfig.platformSurface` — portable, unsigned, downgrade-guarded — because it
  legitimately differs per deployment.
- **The default** is the manifest's (`roles.default`: the Guide): an agent that names no role (or an empty list) holds it.
- **Fail closed:** a planned or unknown role grants nothing; an agent whose profile cannot be
  read holds no role and is offered nothing.
- **Several roles combine** as a union; each role only ever narrows.
- **In the agent editor** (Capabilities → Platform, shown when the component is on): one
  checkbox per role from `GET /v1/platform/roles` (op `platform.roles`, the registry as data:
  title, description, status, effects, plus the default and the write modes), a planned role
  shown and disabled with what it still needs, and a **write mode** select (off · review ·
  allow) that is enabled only while a chosen role allows `write`. Saving writes the roles to
  `componentConfig.platform.roles` (so a change moves the signature, like any capability) and
  the mode to `authorityConfig.platformSurface.writes`, keeping the other authority keys. The
  roles are saved only once the list has loaded — an editor that could not read the roles
  never overwrites the agent's grant with an empty one.

### What a role changes at runtime

| Where | Effect |
|---|---|
| `find` | searches only the agent's role surface — smaller pool, sharper ranking |
| `describe` | an operation a role names but whose effect it does not allow is listed `callable: false`, *"outside this agent's role (Guide — read only)"*; the operation's own policy is reported first when it refuses too (a write today: "not offered yet") |
| `call` | an operation outside every role is refused before any route runs, naming the role |
| playbook delivery | `find` delivers only from the playbooks the agent's roles are taught (and still within the skill's anchor grant) |

### The roles, in work order

The roles follow Uderia's lifecycle — model the knowledge, build the agent, assure it, put it to
work, distribute it, govern the estate — in the platform's own words (builder, operator,
publisher, analyst). **No role is a signer or an approver:** signing, attesting, approving a
publication, activating an agent for REST use, entering credentials and deciding user matters
are person's acts in every role.

| Rank | Role | For | Reads | Writes (from phase 3) | Status |
|---|---|---|---|---|---|
| 1 | **Guide** | explains the user's estate | profiles, trust, consumption, the ingredient lists (repositories, graphs, skills, components, context window types), a graph's shape, entities, versions, lifecycle and conformance (§26) (next: sessions) | — | active |
| 2 | **Agent Builder** | composes agents of every class from the user's ingredients and submits them for approval (§18) | profiles, llm, mcp, trust, the ingredient lists (next: connectors, database connections) | profiles (create, update, delete, test), which agents read a graph (§26) — `governs`/`DELETE`, always approved | **active** |
| 3 | **Knowledge Curator** | keeps the knowledge agents stand on right (§19) | repositories, documents, health, ratings, search, graphs and ontologies (§26), Files (next: data products) | repositories (create, update, delete), documents from Files, quarantine, delete, sync, scan; a graph's versions and name | **active** |
| 4 | **Automation Builder** | puts agents to work without being asked (§20) | flows, templates, runs, versions, scheduled tasks and their runs, the scheduler (next: database connections) | flows from templates, runs, versions, scheduled tasks — a schedule always approved | **active** |
| 5 | **Quality Analyst** | finds what goes wrong and why (§22) | live quality, scoreboards, timelines, scored runs, the Assurance Inbox, the review queue, an agent's trust history and story, conversations | scoring a turn again (judges spend tokens) — acknowledging, deciding, resetting stay a person's | **active** |
| 6 | **Insight Builder** | keeps the views people read in order (§23) | dashboards and their tiles, canvases, the data product catalog and compiled SQL | dashboards (create, arrange, refresh a tile through its own agent, remove), canvases (rename, restore, remove) — never a tile's content or question, never running data | **active** |
| 7 | **Publisher** | prepares agents for others (§24) | packs, their lifecycle and the publishing rules, the marketplace, what a pack needs to run here | bundling agents into a draft pack, renaming it — every act that reaches other people is a person's | **active** |
| 8 | **Admin Monitor** | the deployment in totals, for administrators (§25) | consumption across users, costs and price coverage, user counts, database health, maintenance jobs, scheduler, providers | — (read-only; every administrator-tier reading recorded) | **active** |

**Work order.** (1) The role mechanism with the Guide as the first active role (done).
(2) Widen the Guide: describe `sessions` and the read-only skills/components routes. (3) Phase 3
(approval cards, write mode) together with the Agent Builder's namespaces. (4) The remaining
roles by rank, each bringing its namespaces and playbooks. A role becomes `active` when every
namespace it needs is described and its playbooks exist — the gate holds an active role to
that.

**Later, deliberately not now:** role allowances per user tier (which tiers may grant which
roles); admin-defined roles beyond the shipped ones. (Choosing roles and the write mode in the
agent editor is done — see *Granting, signing, resolving*; `scripts/platform_expert_agent.py`
still provisions @PLATFORM.)

### Tools and gates

- `python maintenance/platform_operations.py roles` — what each role reaches today (listed /
  callable under its effects) and which namespaces a planned role still waits for.
- `test/test_platform_roles.py` — the registry (well-formed, the default active, no tier, no
  person's act as an effect, planned roles say what they need); every active role's patterns
  and playbook patterns match the served catalog and real playbooks; resolution and fail-closed
  defaults; the surface for `find`, `describe` and `call`; the tier still gating an operation a
  role names; the signature (default hashes as before, another role enters the hash, order and
  a disabled component do not); playbook delivery limited to a role's slice. No LLM calls, no
  writes; mutation-tested (removing the role filter turns it red).

`test/test_platform_writes.py` holds phase 3: the write mode (off/review/allow, unreadable →
off); off refuses before any route; review parks the exact request with the owner's notice and
runs nothing; allow runs an ordinary write but parks a governing or deleting one, and never a
person's act; describe/find mark "needs approval"; approval runs the request as the owner after
re-checking it, refuses what no longer holds and keeps it open; credentials withheld at any
depth; the downgrade guard; the real approval route wired end to end (on a temporary review
database); the Inbox row. No LLM calls; mutation-tested.

## 18. Building every class — the composition shaped in code

An agent is made of ingredients the user already has: a model and a data source (every class),
**knowledge repositories** (Focus), **expert agents** (Coordinate), **skills**, **components**
and a **context window type**. The Agent Builder reads each list — `rag.collections` (+
`rag.collection`, `knowledge.documents`), `knowledge_graph.list`, `skills.list`,
`components.list` (+ `components.get`), `context_window_types.list` (+ `.get`) — and sends the
composition with `profiles.create` / `profiles.update`, whose descriptors now name it
(`_COMPOSITION_ARGS`: `knowledgeConfig`, `ragCollections`, `genieConfig`, `skillsConfig`,
`componentConfig`, `contextWindowTypeId`, `useMcpTools`). Before this, `bind` refused those
fields — an argument a descriptor does not name is not accepted — so a Focus or Coordinate agent
could not be built at all, whatever the notes said.

**The shaper** (`core/platform_profile_shape.py`) runs inside `bind` whenever a call is resolved
— at submission and again at approval — so the request parked for a person is exactly the
request that runs:

| Field | The model sends | Stored |
|---|---|---|
| `knowledgeConfig.collections` | names, ids or `{"id": …}` | `[{"id": <int>, …}]`, one of the user's **knowledge** repositories; retrieval turned on when the list is non-empty (unless `enabled` is sent) |
| `ragCollections` | names or ids | planner repository ids |
| `genieConfig.slaveProfiles` | `@TAG`s or ids | agent ids — never the coordinator itself; an expert's `slaveProfileSettings` leave with it |
| `skillsConfig` | `["chart-expert"]` or entries | `{"skills": [{id, active, enabled, auto: false, param}]}` — a skill the builder names is the user's choice, not auto-managed |
| `componentConfig` | `{"chart": true}` | `{"chart": {"enabled": true, …}}` |
| `contextWindowTypeId` | a name or id | the id |

A name that is not the user's is refused with the list of what they have ("no knowledge
repository 'Sales handbook' of yours — yours are: Uderia Documentation (7), …"); an unreadable
store is a refusal sentence, never an exception.

**On an update each object is merged onto the agent as it is.** `config_manager.update_profile`
is a shallow replace: sending `componentConfig: {"dashboard": true}` would have replaced every
other component's setting with the class default. The shaper merges a component and a skill by
its id and keeps the other top-level keys; a list inside an object (`collections`,
`slaveProfiles`) is replaced as sent — the playbook tells the model to send the whole list.
Shaping is idempotent (an approval re-binds the parked, already-shaped request).

**Ranking a change.** The new playbook `building/configure-agent` shares its nouns with
`agents/inspect` ("@SALES components"). A playbook that calls a write operation is marked
`changes` (from the catalog's effects); a query with a change word (*add, turn, give, switch,
let, …*) favours a change playbook, one without favours a reading one — a step of 0.5, below a
single rare word's weight, so it decides only between playbooks the words cannot tell apart.

**Limits.** The builder reads repositories and graphs but never changes them (the Knowledge
Curator's work). Assigning a knowledge graph to an agent (`knowledge_graph` assignments),
`authorityConfig` and an agent's additional MCP servers are not yet part of the composition it
may send.

Found on the way: `GET /v1/rag/collections` without a token answered the **admin view** —
every user's repositories (`get_all_collections(None)`); it now answers 401 (gate:
`test_unauth_routes.py` §5). The other routes that answer anonymously serve the bootstrap
template (`tda_config.json`), not a user's data.

**Live (2026-11-01, one turn, $0.0103):** @BUILDER (Gemini Flash Lite) asked to create a Focus
agent on the Uderia Documentation repository: find delivered `building/create-agent`, it read
the default agent and `rag.collections`, and parked `profiles.create` with `profile_type:
rag_focused`, the default's model and `knowledgeConfig: {"collections": [{"id": 7}], "enabled":
true}` — the model sent `"@DOCSQA"` and `["7"]`; the code normalised both.

**Gate:** `test/test_platform_profile_shape.py` — each field's shape, the refusals, the merge,
idempotence, the bind on the served catalog, the descriptors and the roles (no LLM, no writes;
mutation-tested). `test_platform_playbooks.py` holds the change ranking.

## 19. The Knowledge Curator — keeping knowledge right

The third role is active. It keeps the knowledge agents stand on right, and only that: it
composes no agent (`profiles.create/update` are the Builder's), it reaches no Files write but
adding a document to knowledge, and the acts that need an owner's judgement stay the owner's.

| Operation | Effect | Note |
|---|---|---|
| `rag.collections` · `rag.collection` · `knowledge.documents` | read | what the user has and what each repository holds |
| `knowledge.health` | read | the corpus health rollup: screened / unscreened, injection and exfiltration findings, personal data by label and count (never values), quarantined |
| `knowledge.feedback` | read | the user's own ratings of the answers each document served, lowest first |
| `knowledge.search` | read | what a question retrieves, with each passage's distance (an agent's similarity is `1 - distance`) |
| `files.list` · `files.knowledge_targets` | read | the user's files (`can_knowledge` says which can be added) and the repositories they can go into |
| `rag.collection_create` · `rag.collection_update` | write | name, type, description, chunking, search mode, a planner's data source |
| `files.to_knowledge` | write | a document from Files into a repository, through the repository's own upload |
| `knowledge.document_quarantine` · `knowledge.sync` · `knowledge.scan` · `knowledge.repository_settings` | write | reversible or additive: they follow the agent's write mode |
| `knowledge.document_delete` · `rag.collection_delete` | write | `DELETE` — always reviewed |
| `knowledge.document_release` · `knowledge.document_acknowledge_finding` · `knowledge.document_source` | **human_only** | releasing a quarantine and acknowledging a finding are recorded human judgements; choosing where a document syncs from could point the server at anything it can reach |

**Not on offer, by omission.** `bind` accepts only the arguments a descriptor names, so leaving
an argument out is the control: a repository's `source_root` (a path on the server), its
standing trust policy (`sync_auto_ack_findings`, `sync_auto_resign`), raw vector store
configuration (`backend_type`, `backend_config` — credentials), the embedding model at creation,
and a planner repository's `trust_locked` are the owner's.

**Names resolve in code** (one convention, `RESOLVERS`): `collection_id` by a repository's name
(owned or subscribed — the route still decides what the caller may do), `file_id` by the exact
file name (two files of one name are refused, naming the ids), `mcp_server_id` like
`mcpServerId`.

**The inner hop.** `files.to_knowledge` runs the repository's own upload in process. It used to
forward the caller's `Authorization` header: a browser's session token reaches further than the
upload needs, and an agent's operation token is bound to the outer route and was refused. The
route now mints its own one-request token for `POST …/repositories/<id>/documents` — narrower
for every caller.

**An empty list is empty.** Asked for `fields` on a list with no records, the answer used to
say the fields were not found — which a model reads as a wrong field name. It now says
`empty: "the answer holds no records — nothing matched"`; records without a field still name it.

**The overview** (`profiles.overview`) now names what an agent answers from — its repositories
by name, with `minRelevanceScore`, `maxDocs`, `maxTokens` — a coordinator's experts by tag, and
its context window type: what the Builder composes, read back in the words it was composed in.

**Playbooks** (`knowledge/*`): `add-documents` and `clean-up` change (marked `changes`),
`health` and `search` read — the Guide is taught those two and reads the same operations.

**Live (2026-11-01, one turn, $0.0060):** @CURATOR (Gemini Flash Lite, writes `review`) asked to
"add the file curator-probe.md from my Files to the Curator probe repository": find delivered
`knowledge/add-documents`; `files.list`, `files.knowledge_targets`, then `files.to_knowledge`
parked with the repository's name resolved to `39`. Approved through the real route: the
upload ran under the minted token, one chunk stored. The browser path through the same route
re-added it (unchanged, skipped). The scratch repository and file were removed after.

**Gate:** `test/test_platform_curator.py` — the effects, what is not on offer (bind refuses
each), the role and the Guide's slice, the resolvers, the inner hop, the overview (no LLM, no
writes; mutation-tested). Routing in `test_platform_playbooks.py`; the empty-list answer in
`test_platform_tool.py`.

## 20. The Automation Builder — agents at work without being asked

The fourth role is active. It operates and schedules; it does not author graphs.

| Operation | Effect | Note |
|---|---|---|
| `flows.list` · `flows.get` · `flows.platform` · `flows.templates` · `flows.template` · `flows.versions` · `flows.runs` · `flows.run_status` · `flows.variables` | read | what is automated and how it ran |
| `flows.create` | write | from a topology template, its slots filled by role → `@TAG` — never a raw graph |
| `flows.update` | write | name and description only |
| `flows.fork` · `flows.version_save` · `flows.version_restore` · `flows.run` · `flows.run_cancel` | write | follow the agent's write mode; a run spends and may act through its nodes (each under its own agent's authority) |
| `flows.delete` · `scheduled_tasks.delete` | write | `DELETE` — always reviewed |
| `scheduled_tasks.list` · `.get` · `.runs` · `scheduler.status` | read | |
| `scheduled_tasks.create` · `scheduled_tasks.update` | write, **governs** | standing authority to act and spend on a timer — always a person's approval, whatever the write mode |
| `scheduled_tasks.run_now` | write | one run now |
| `flows.toggle_platform` | **human_only**, admin | sharing a flow with every user is an administrator publishing |
| `flows.run_stream` | never | the page's live stream |

**One write path for a graph.** A flow's nodes and edges are edited by the flow component
(`TDA_Flow`, with its flow-designer skill); `flows.create` and `flows.update` take no
`definition`. Two ways for an agent to rewrite a graph would be two contracts to keep true.

**Not on offer.** Where a scheduled task's results are **sent** (`output_channel`,
`output_config`: an email address, a webhook URL with its bearer token) is an exit for the
user's data — the owner sets it in the Scheduler panel. Also not offered: a scheduled database
action (`action_spec`, under OBDA-ACT's own gate), a pinned session, a run's debugging options
(`start_node_id`, `run_mode`), and replacing the whole set of flow variables (`PUT
/v1/flow-variables` stays undescribed).

**Found on the way — two fixes:**
- **A private flow could be copied by anyone.** `flow_db.fork_flow` copied any flow id — its
  definition included — into whoever asked. It now copies a platform flow (`is_template`) or the
  caller's own; another user's reads as not found, so an id cannot be probed.
- **Credentials in JSON text.** `redact` withheld credential keys at any depth of a structure,
  but a scheduled task's `output_config` is a JSON object stored as **text**, so its bearer
  token and webhook URL passed through whole. `redact` now redacts inside a JSON object carried
  as text, and `webhook_url` is a credential key (whoever holds a Slack or Teams webhook can
  post to it).

**Names resolve in code:** `flow_id` by a flow's name (the caller's or a platform flow — so a
fork by name works; the route still decides), `task_id` by a scheduled task's name.

**Live (2026-11-01, one turn, $0.0042):** @AUTOMATE asked to have @IDEAT answer a question
"every weekday at 9" called "Morning tip": find delivered `automation/schedule`, one call to
`scheduled_tasks.create` with `schedule: "0 9 * * 1-5"` and `profile_id: "@IDEAT"` — parked as
always reviewed, `@IDEAT` resolved to its id. Rejected afterwards, so nothing runs on a timer.

**Gate:** `test/test_platform_automation.py` — effects, governs and always-reviewed, what is not
on offer, the fork fix on a scratch database, redaction inside JSON text, the role and the
Guide's slice, the resolvers (no LLM; mutation-tested).

## 21. Roles, declared and visible

Roles are the Platform component's, and the component says so.

**The manifest declares them.** `components/builtin/platform/manifest.json`:

```json
"roles": {
  "grant": "componentConfig.platform.roles",
  "default": ["guide"],
  "definitions": "roles/",
  "playbooks": "platform-playbooks"
}
```

Where an agent's roles are stored, which apply when it names none, where the definitions are
(one JSON file per role, beside the component's `instructions.json`), and the progressive skill
whose playbooks the roles slice. `core/platform_roles.py` reads this declaration — the folder,
the default and the playbook skill are not written anywhere else; an unreadable declaration
means no roles and no default (fail closed). The schema (`component_manifest_v1.schema.json`)
describes the section (`additionalProperties: false`); the definitions stay out of the manifest
— their patterns are checked against the served catalog and the library, which a static
manifest gate cannot do. Only Platform declares roles; it is one documented section, not a
pattern every component is expected to adopt.

**What a role grants is signed.** Each held role enters the agent's signature as
`platform:role=<id>@<digest>`; the digest (`platform_roles.digest`) is a hash of its status,
effects, operations and playbooks, order-free. Its title, description, needs and rank are words
about it and stay out. Before, only the id was hashed: the Agent Builder and the Guide both
widened on the way here and every signature holding them stayed put. Now a widened role reads
*changed — sign again* on every agent that holds it. The one-time cost: signed agents with the
Platform component on read changed once when this shipped.

**One reading, computed by the enforcing rules.** `platform_operations.roles_overview` (served
as `GET /v1/platform/roles`, op `platform.roles`; `detail=1` adds the lists) gives, per role:
what it lists and can call for the caller's tier (`role_reach` — the same `availability` and
`role_state` that find, describe and call apply; a planned role shows nothing callable), the
changes a person always approves, what it lists but cannot call and why, the playbooks it is
taught, its digest and the caller's agents that hold it; and per playbook the roles taught it.
The gate holds the view to the enforcement: every operation the view shows callable is callable
through TDA_Platform, and no other.

**Four places read it**, through one module (`static/js/handlers/platformRolesView.js`):

| Where | What it shows |
|---|---|
| Components → Platform card | `Roles · 4 active · 4 planned — playbooks from platform-playbooks` |
| Components → Platform → **Roles** tab | the declaration in one sentence; per role its status, effects, the default mark, its digest, a counts line, and folds — playbooks it teaches · changes it can submit (always-approved ones marked) · what it reads · listed, not callable (with why) · held by |
| Agent editor → Capabilities → Platform | under each role checkbox the counts line and *What it reaches*, opening the same folds in place (the editor is never left with unsaved changes); a planned role says it grants nothing and what it waits for |
| Components → Skill Library → Libraries | on the playbook skill's card the roles that slice it, and per section *taught to …* |

The Roles tab is drawn for any component whose manifest declares `roles` — never by component id.

**What the view surfaced at once.** The Agent Builder's `llm.*` and `mcp.*` patterns reached
`llm.configurations_delete` and `mcp.servers_delete`: removing credential-bearing
configuration other agents depend on. Creating or changing that configuration is a person's act;
deleting it now sits with the same person — the role excludes both (`!llm.configurations_delete`,
`!mcp.servers_delete`). The operations stay described, for a later administrator role.

**For people:** [docs/manuals/PLATFORM_ROLES.md](../manuals/PLATFORM_ROLES.md) — the four
controls, the rules every role keeps, giving an agent roles, then **per role** its examples, what
it does not offer and why, what it reaches, and the playbook appendix. The examples and
boundaries are written in each role's file (`examples`, `not_offered` — words, outside the
digest) and rendered with the rest (`python maintenance/platform_operations.py manual --write`);
the Roles tab and the editor's *What it reaches* show the same two folds. `test_platform_roles.py`
§I holds them per role: every person's act and cross-user operation a role lists is explained in
its own boundaries, every operation a boundary names is one it lists, every argument it names is
one the operation really leaves out.

**Gates:** `test/test_platform_roles.py` §A (the declaration and the location), §E (the digest:
what moves it and what does not), §H (the view against enforcement, counts against lists, the
inverse playbook map, the tier still deciding); `test/test_platform_roles_view.mjs` (the
renderer, one cached fetch whose failure is not cached, the four places, the manifest section).

## 22. The Quality Analyst — and no cross-user reach through an agent

The fifth role is active. It finds what goes wrong and why, from what the platform already
records; its one change is scoring a turn again.

| Operation | Effect | Note |
|---|---|---|
| `evals.kpi_dashboard` · `evals.online_dashboard` · `evals.online` · `evals.trust_behavior` · `evals.library_outcomes` | read | the caller's live quality — scoreboards, failing checks, failure clusters |
| `evals.agent_timeline` · `evals.agent_runs` · `evals.turn_score` · `evals.scoring_thresholds` | read | one agent over time; its scored runs with every verdict; one turn's recorded verdicts; the bands |
| `evals.backtest` | read | what a proposed policy would have done to past turns — a replay, zero tokens, nothing changed |
| `evals.score_turn` | write | score a turn again against a reference or assertions, optionally with model judges: it changes nothing, but judges spend tokens, so the agent's write mode decides |
| `assurance.inbox` · `.inbox_count` · `.reviews` · `.trust_history` · `.agent_story` · `.trust_trip` · `.vocabulary` | read | what waits for the user, and one agent's trust life |
| `sessions.list` · `.details` · `.analytics` · `.context_analytics` | read | the caller's conversations |
| `assurance.inbox_ack` · `.inbox_ack_derived` · `.review_decide` · `.review_withdraw` · `.trust_trip_reset` | **human_only** | a person says they looked; the approval is the control; a new trust leg is the owner's call |

**Arguments that reach other users are never offered.** Several of these routes take `scope=system`
(every user's spans), `user=` or `all_users=true` for a caller holding the view-all-sessions
feature; their descriptors name none of them, so `bind` refuses them and every call reads the
caller's own.

**Containment — a request an agent makes never carries a cross-user feature.** Some routes widen
on the caller's *features* without any argument: session details falls back to any user's
conversation for a caller who may view all sessions. A developer's or administrator's agent
would have inherited that. Now `auth/middleware.get_current_user` marks a user authenticated by
an operation token (`user.agent_operation`), and `auth/features` grants a marked user none of
`CROSS_USER_FEATURES` (view or export all sessions, view all users, credentials, audit logs);
every other feature of the tier stays. A person is unaffected. Gate: an administrator in person
reaches another user's conversation through the real route; the same administrator's agent gets
not-found (a fixture session store, so the check never depends on the instance) — removing the
containment turns it red. **Known, not yet contained:** routes that widen on the *tier*
(`current_user.tier == "admin"`) rather than a feature; the resolvers keep the described ones on
the caller's own (a repository named by an agent must be the caller's), and the Admin Monitor
role will bring the rest under one rule.

**Found on the way:**
- **The instance-wide evaluation store had four open doors.** Its runs, scores and annotations
  required the view-all-sessions feature (TC-34), but `evals/datasets` (list, create), `…/cases`
  (list, add), `…/mine` and `evals/gate` required nothing: any signed-in user could read and
  write the datasets the operators' runs depend on. All four now require the same feature. No UI
  calls them; the store stays undescribed for agents.
- **Session details without a user** fell through to the owner lookup, which reads any
  conversation when per-user filtering is switched off (`TDA_SESSIONS_FILTER_BY_USER=false`).
  It now answers 401 without a user.

**Live (2026-11-01, one turn, $0.0048):** @QUALITY asked which checks fail most and which agent
is worst: find delivered `quality/whats-failing`, one call to `evals.kpi_dashboard`, and an
answer that matched the scoreboard exactly (`instruction_following` 82% of 67 turns; @IDEAT 0 of
12) and named how many turns each rate rests on.

**Gate:** `test/test_platform_quality.py` (effects, nothing that reaches other users on offer,
the containment unit and end to end, the store and session-details gates, the role and the
Guide; mutation-tested).

## 23. The Insight Builder — and nothing that renders is written through the platform

The sixth role is active. It keeps dashboards and canvases in order and explains what the data
products on a graph can answer; what a view *shows* stays with the component that produces it.

| Operation | Effect | Note |
|---|---|---|
| `dashboards.list` · `.get` · `.tiles` | read | dashboards, their tiles, each tile's agent and last refresh |
| `dashboards.create` · `.update` · `.tile_update` · `.layout` | write | an empty dashboard; name, description, theme; a tile's title, place and size |
| `dashboards.tile_refresh` | write | the tile's OWN agent answers its question again, under its own authority (spends) — the answering agent is never chosen here |
| `dashboards.delete` · `.tile_delete` · `canvases.delete` | write | `DELETE` — always reviewed |
| `canvases.list` · `.get` · `.versions` | read | |
| `canvases.update` · `.version_restore` | write | a title; a saved version back |
| `data_products.catalog` · `.get` · `.compile` | read | a graph's products, one product's definition, the SQL a request would run — nothing runs |

**Nothing that renders is written through the platform.** A tile's body is markup (`html`, a
chart `spec`) rendered in the user's dashboard; a canvas renders HTML and code. Offered to an
agent, either would let a prompt plant script in the user's page. So adding a tile, changing a
tile's content, and writing a canvas's content are not offered — they come from the dashboard
and canvas components, inside a conversation, under their own enablement. The gate holds this
**catalog-wide**: no write an agent can call takes `html`, `spec`, `content` or a raw
`definition`.

**Standing work stays the owner's.** A tile's question decides what another agent is asked on
every refresh, and a dashboard's auto-refresh re-runs every tile on a timer — like a schedule,
both are the owner's (`source_prompt`, `profile_id`, `auto_refresh_s` not offered).

**Data stays the agent's own access.** Running a data product query and running SQL from a
canvas are not described: through the platform they would run as the user and skip the agent's
data surface and the deployment's OBDA policy, which apply on `TDA_DataProduct` and the agent's
own tools. Canvas connections carry credentials. Compiling a request (SQL back, nothing run) and
the direct tool replay of a tile (`refresh-mcp`, outside an agent turn — not offered) mark the line.

**Live (2026-11-01, one turn, $0.0050):** @INSIGHT asked which data products the Fitness Database
KG offers and whether they are trusted: `knowledge_graph.list`, `data_products.catalog`, and an
answer naming the one product, unsigned and unvalidated, and what that means for agents limited
to signed products.

**Gate:** `test/test_platform_insight.py` (effects, what is not offered — bind refuses each, the
catalog-wide no-raw-markup rule, data reads not described, the role and the Guide).

## 24. The Publisher — and every act that reaches other people stays a person's

The seventh role is active. It prepares the user's agents for others and reads the marketplace;
everything that changes what *other people* run, or brings *their* agents into the user's estate,
is a person's act.

| Operation | Effect | Note |
|---|---|---|
| `agent_packs.list` · `.get` · `.lifecycle` · `.governance_policy` | read | the caller's packs, one pack, where it stands on its way to publication, the deployment's rules |
| `marketplace.agent_packs` · `.agent_pack` · `.agent_pack_requirements` · `.agent_pack_audience` | read | what is published, one listing, what it would need from the caller's data sources, who the caller's own listing is offered to |
| `agent_packs.create_and_install` | write | bundle the caller's agents (`profile_ids`, by tag — resolved element by element) into a draft pack; the agents stay, removing the pack leaves them |
| `agent_packs.update` | write | name, description, version |
| `agent_packs.lifecycle_transition` · `.publish` · `marketplace.agent_pack_unpublish` · `.agent_pack_audience_set` · `agent_packs.sync_subscribers` · `.sla` | **human_only** | proposing, approving, publishing, deprecating, withdrawing, choosing who receives it, pushing a version to subscribers, promising a service level |
| `marketplace.agent_pack_install` · `.agent_pack_fork` · `.agent_pack_sync` · `.agent_pack_unsubscribe` | **human_only** | bringing another publisher's agents into the estate, or letting them go, is a trust decision |
| `marketplace.agent_pack_rate` · `agent_packs.notify_author` · `.notify_author_update` | **human_only** | a rating is an opinion; an email leaves Uderia |
| `agent_packs.governance_policy_set` | **human_only**, admin | the deployment's publishing rules |

Not offered at all: removing an installed pack (it deletes the agents, repositories and graphs it
brought) and the pack file itself (`create`, `export` — a file that leaves the platform).

**Found on the way — a pack's lifecycle was anyone's to read.** `GET
/v1/agent-packs/<id>/lifecycle` returned any pack's lifecycle state and audit trail to any signed-in
user. It now answers for the pack's owner and for a tier the governance policy names as an approver
(they decide what is proposed); anyone else reads not-found. Gate: behavioural through the real
route on a fixture pack, mutation-tested.

**List arguments resolve like singular ones.** `LIST_RESOLVERS` resolves each element of
`profile_ids` exactly as `profile_id` is resolved; one tag that is not the caller's refuses the
whole request, naming it.

**Live (2026-11-01, one turn, $0.0082):** @PUBLISH asked whether the Policing Data Products pack is
ready to publish: it read the pack list, the readiness playbook, the lifecycle and every agent's
trust, and answered that the pack is a draft whose trust gate reads nonconformant because its four
agents are unsigned — checked against the platform: exactly so.

**Gate:** `test/test_platform_publisher.py` (effects, the lifecycle fix, list resolution, the role;
no LLM, no writes).

## 25. The Admin Monitor — aggregates only, every reading recorded, and one rule for session reads

The eighth role is active; every shipped role now is. It keeps an administrator informed about
the whole deployment, in totals, and is read-only.

| Operation | Effect · tier | Note |
|---|---|---|
| `consumption.system_summary` | read · admin | the deployment's consumption, all users together |
| `admin.stats` | read · admin | users in numbers — counts only |
| `admin.db_health` | read · admin | unreadable reads, the last backup |
| `admin.platform_jobs` | read · admin | the platform's own maintenance jobs and their last runs |
| `costs.coverage` · `costs.models` | read · admin | models running unpriced; the price list (filling prices is not offered — it writes) |
| `costs.analytics` | read · admin, own | cost over the administrator's own conversations |
| `scheduler.status` · `llm.provider_health` | read · user | the only part a non-administrator's agent holding the role reaches |
| `consumption.users` · `trust.unassigned` | cross_user | listed, explained, never callable |

**Aggregates only.** The user directory, a user's conversations, usage named per user, and every
setting are out of its reach. Its deployment readings are administrator-tier, so the role gives a
non-administrator's agent nothing but the scheduler's and providers' status.

**Every reading recorded.** `platform_operations.call` writes an audit entry
(`platform_agent_admin_operation`) whenever an agent runs an administrator-tier operation — the
agent, the operation, the conversation. Recording never breaks the call.

**One rule for session reads.** Eight places read the deployment's `SESSIONS_FILTER_BY_USER`
switch — routes and both session backends; with it off, they read every user's conversations.
That is a deployment's choice for *people*. An agent's request now never widens: the middleware
marks it (`g.agent_operation`, beside `user.agent_operation`), and every read goes through
`core/config.sessions_filtered_by_user()` — the switch, OR an agent's request. The gate fails if
any session read consults the switch directly again.

**Live (2026-11-01, two turns, $0.0109 together):** @MONITOR asked how the deployment is doing, whether the
database is healthy and whether any model runs unpriced. First run: it read the health playbook's
calls and said nothing was unpriced — without reading the price coverage (two models were). The
playbook now reads the coverage when prices are asked about and never reports as fine what it did
not read; the rerun named both unpriced models. Every administrator-tier reading appeared in the
audit log under @MONITOR.

**Gate:** `test/test_platform_admin_monitor.py` (the descriptors, recording, the session rule —
a person may read across users when the switch is off, an agent may not; mutation-tested — and the
role).

## 26. Knowledge graphs — and a graph is read by id only by its owner

Graphs were the next gap after the eight roles: the routes behind them were 80 of the 609
undescribed. 44 are described now (246 of 811 in all).

| Operations | Effect | Role |
|---|---|---|
| `knowledge_graph.stats` · `schema_overview` · `entities` · `relationships` · `subgraph` · `node_neighborhood` | read | Guide, Curator |
| `knowledge_graph.versions` · `assignments` · `lifecycle` · `conformance` · `subscriptions` · `ontologies.list` | read | Guide, Curator |
| `knowledge_graph.authored` · `tuning_proposals` · `context` · `marketplace.knowledge_graph*` (reads) | read | Curator (`context`: Builder) |
| `knowledge_graph.version_save` · `rename` | write | Curator |
| `knowledge_graph.assignments_set` · `activate` | write · `governs` (always approved) | Agent Builder |
| lifecycle transitions, clear, version restore/delete, rebuild from source, tuning decisions, authored publish/withdraw/deploy, the physical/metric-view/demo-data redeploys, marketplace publish/unpublish/audience/install/fork/subscribe/sync/rate | `human_only` | listed by the Curator, explained, never callable |

**Not offered.** A graph's entities and relationships, import, generation and concept
declaration stay undescribed: a graph's content changes through the knowledge-graph component of
the agent that holds it, under the OBDA policy; building one is the Graph constructor's.

**Naming a graph.** `kg_id` resolves in code (`platform_operations.resolve_graph`): the caller's
graph by id or by name, case-insensitive; a released shadow is not a graph of its own; another
user's id is refused before the route runs. `assigned_profile_id(s)` resolve like `profile_id(s)`.
The four oldest reads (`entities`, `relationships`, `stats`, `subgraph`) and version saving took
only an owner profile; they accept `kg_id` now, through the same ownership check.

**The leak this closed.** The graph store reads a graph by its id for whoever asks. Fifteen routes
fell back to building the store from a foreign `kg_id` when the ownership lookup found nothing —
the shared resolver `_resolve_kg_store_for_request` and its callers (metric export, the four
profile metric routes, lifecycle read, conformance, lifecycle transitions, the published
ontology), and inline in clear, export, schema overview, node neighbourhood, graph spec and the
marketplace publish. Any signed-in user could read another user's graph, its metrics and compiled
SQL; run a lifecycle transition on it (the route passes `is_owner=True`); clear it; and publish
it. Two more — assigning and activating — wrote an assignment row for a foreign graph, so the
caller's agent read it on every turn. Every one now answers 404 (`_KGNotFound` → `_kg_not_found`
in the routes; an ownership check in the assignment writes and the marketplace publish).

**Not changed: the store's legacy fallback.** A `GraphStore` built from a profile with no active
graph uses the profile id as its `kg_id`. Bootstrap profile ids (`profile-default-chat`) are the
same for every user, so a graph written without metadata under such an id is one namespace for all
of them. Locally that is one orphan entity; whether production holds any is not known yet, and
changing the fallback would orphan such graphs for their owners — so it is recorded here, not
changed.

**Live (2026-11-01, one turn, $0.0082):** @CURATOR (Gemini Flash Lite) asked what is in the
admin's *Fitness Database KG* and whether it is conformant. It listed the graphs, read the overview
and the conformance by the graph's id, and answered: 87 entities (33 attributes, 33 columns,
5 tables, 4 business concepts, 3 metrics, 1 data product, …), conformant with no issues — both
matching the store and the conformance route read directly.

**Gates:** `test/test_kg_ownership.py` (structural — every refusal answered as a 404, no route
building a store from a fallback again — and behavioural: a plain user asking for another user's
graph by id is refused by every read route with nothing of the graph shown, the owner still reads
it, the acts that change a graph refuse an id that is nobody's; 14 checks fail on the old code);
`test/test_platform_graphs.py` (the descriptors, naming a graph, the three roles).

## 27. Agents asking other agents — `TDA_Platform`'s `ask`

An agent puts a question to another of its user's agents and uses the answer
(`core/platform_asking.py`). It is a third action beside `find` and `call`, not a catalog
operation: it reaches none of the platform's operations, and its policy is its own.

| Gate | Where | Default |
|---|---|---|
| The deployment allows asking, with limits: chain depth (1–3), asks per turn (1–10), seconds per ask (30–900) | `component_settings` `platform_ask_*`; Administration → Components → Platform → **Agent asking** (`platformAdmin.js`, the `ComponentAdminTabs` extension point); `GET /v1/platform/asking` (read), `PUT` (administrators, `human_only`) | off · 1 · 3 · 180 |
| The asking agent lists whom it may ask | `authorityConfig.platformSurface.ask` (profile ids); editor → Capabilities → Platform, shown only while asking is on | nobody |
| The asked agent is activated for REST use | the session route's activation firewall | — |

**How an ask runs.** In-process, as the user, each request with a one-request operation token
(`op: platform.ask`): `POST /v1/sessions` for the asked agent → the conversation records who asked
(`platform_asked_by`: the asking conversation, agent, turn and depth) → `POST …/query` → `GET
/v1/tasks/<id>` until the answer, a failure or the time limit. The asked agent runs under its own
authority, posture, gates and consumption limits; nothing of the asker is lent to it. The record is
written BEFORE the question is sent, and a failure to write it asks nothing — an unrecorded ask
would leave the depth unbounded. The answer returns with the asked agent's tag and conversation id
and an instruction to say who answered; over 6,000 characters it is shortened, naming where the
rest is. An answer that takes longer than the limit is left running and said so.

**Every refusal is a sentence, before anything runs:** asking off (naming where an administrator
turns it on), no question, an agent that is not the user's, the agent itself, an agent not on the
list (naming whom it may ask), a chain at its depth (read from the asking conversation's own
record), a turn at its ask limit, an asked agent not activated.

**Authority, not capability — like the write mode.** The list is portable in principle but holds
this instance's profile ids, so it is an instance-local path of `authorityConfig`
(`local_ref_paths`): a pack never carries it, and an import keeps what the instance held (the
contract's merge now restores a NESTED local path the shallow merge used to drop). Adding an agent
to it is a widening the downgrade guard reports (`platformSurface.ask`). It is not signed: like the
write mode, it decides what the agent may reach through the platform at run time, under gates the
asked agent keeps.

**Live (2026-11-01, two asking turns, $0.0083 together):** @CURATOR asked @UDERIA what the Trust
Circle is; @UDERIA answered from the Uderia documentation in its own conversation (recorded at
depth 1, $0.0014) and @CURATOR passed the answer on ($0.0039) — without saying @UDERIA answered,
which the instruction asks for (a weak model's reading). An earlier ask of @IDEAT failed honestly
and was reported as @IDEAT's failure: @IDEAT's MCP server was unreachable from the test machine.
**Found on the way:** that turn's own failure read "the language model provider did not answer"
— an MCP connection failure surfaced as a provider failure (`agent/turn_failure`); fixed in §29.

**Gate:** `test/test_platform_asking.py` (settings, every refusal before anything runs, an ask
through the three routes with the record first, the tool's argument names, the contract — widening,
never carried by a pack, kept on import — and the two surfaces).

## 28. The Problem Solver — asking is a role's

The ninth role gets a question answered that none of the user's agents can answer alone. It
reads what each agent is for (`profiles.list`, `profiles.overview`), how trusted it is and how well
it does (`trust.agents`, `evals.kpi_dashboard`), and what the user holds that no agent uses
(`mcp.servers`, `rag.collections`, `knowledge_graph.list`); it asks the agents that fit and puts
their answers together, naming who answered what and what remains unanswered. It is read-only.

**Asking is its grant.** A role file may say `"asks": true`; only the Problem Solver does, and
`platform_asking.ask` refuses an agent holding no such role before anything runs. The grant
enters the role's digest only when set, so every other role kept its digest and no signature
moved for them. The roles API marks it (`asks`); the editor offers the ask list only while a role
that asks is ticked; the roles view reads "asks other agents".

**Building what is missing is the Agent Builder's.** The role names `profiles.create` (to say
what it would build) but cannot submit it; an agent holding both submits it, always for approval.
The gaps playbook tells a missing agent (a source, repository or graph no agent is built on)
from missing data (nothing the user holds covers it), and never builds for missing data. A new
agent is asked only once the user has approved, tested and activated it.

**Playbooks** (`solving/`): `route` (split, choose, ask, combine), `second-opinion` (ask the
original question, never the answer to confirm), `gaps`. Their heads avoid the word "agents" — it
is in over half the playbooks, so it no longer sets one apart, and three more uses of it had
stopped "which agents do I have" from finding anything.

**Meeting the agents at the moment of searching.** Every `find` by an agent that may ask carries
`can_ask`: the agents on its list with what each is for, and the instruction to ask the one whose
field a part is instead of answering from memory (`platform_asking.find_hint`). Live, the first run
without it answered "what is the Trust Circle" from memory, wrongly, and never asked; the rerun with
it asked @UDERIA and said so.

**Live (2026-11-01, two turns of @CURATOR given the role, $0.0129 + @UDERIA $0.0017):** "What is the
Trust Circle, and which of my agents are signed?" — the second run answered the platform part from
`trust.agents` and the concept from @UDERIA, attributed. **Found, then fixed (§29):** both runs
counted 11 trusted / 36 unsigned / 48 agents where the route returns 10 / 33 / 1 changed (44).

**Gate:** `test/test_platform_problem_solver.py` (the role, the grant and the unchanged digests,
asking refused without the role, the playbooks taught only to it, the surfaces); asking's own gate
covers the find hint.

## 29. The platform counts; a data source is not the model provider

**Counts.** The Problem Solver's live runs read the 44 records of `trust.agents` and answered
11 trusted / 36 unsigned / 48 agents — the route said 10 / 33 / 1 changed. The data reached the
model whole (44 records, summarised to their identifying fields); the model tallied by hand, and
the playbook's example ("Of your 36 agents … 12 are trusted, 21 are unsigned") pulled it further
off. A descriptor now names the keys to count — `count_by` (validated: key names, reads only) —
and `_shape` answers with `counts` (`total` and the records per value of each key, most first)
counted over EVERY record before anything is summarised, projected or shortened, with a note to
use them and never count by hand (`count_records`: the answer's one list of records; two lists
are not guessed at). `trust.agents` counts by `state` and `ifoc`, `profiles.list` by
`profile_type`. The trust overview playbook reads `counts` and no longer carries example totals.
Live after the fix: "Which of my agents are signed?" → *"Of your 44 agents, 10 are trusted, 1 has
changed … 33 are unsigned"* ($0.0047).

**A data source that cannot be reached.** The Ideate + tools engine loads the agent's MCP tools
before the first model call; when the MCP server was unreachable, the `httpx.ConnectError` inside
the client's task group was classified as `connection` and worded as the provider's ("The language
model provider did not answer: the provider could not be reached"). The tool loader now raises
`turn_failure.DataSourceUnavailable` (naming the server) for a failure of the MCP layer — read
from the MCP client libraries' own frames (`source_failure`), so a defect in Uderia's code in the
same block is re-raised as it is — and `turn_failure.describe` reads it (or any failure raised
inside the third-party MCP client) as `data_source`: never the provider's, never counted against
it. The sentence: *"The agent's data source (Teradata MCP - POfficer) could not be reached — its MCP
server did not answer. Check that the server is running and reachable from Uderia, then try again.
Nothing was computed for this question."* (live, $0 — it fails before any model call).

**Gates:** `test/test_platform_counts.py` (counting, the descriptor, counts surviving summaries,
the shipped operations through the real route, the playbook); `test/test_turn_failure.py` §8 (the
named failure, a failure inside the MCP client through the engines' unwrap, an ordinary
connection failure still the provider's, Uderia's own adapter never read as the source, and the
real loader against a closed port).

