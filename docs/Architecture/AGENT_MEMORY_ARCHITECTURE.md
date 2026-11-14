# Agent Memory Architecture

**Component:** `memory` (`TDA_Memory`) · **Package:** `components/builtin/memory/` · **Context module:** `user_memory` · **Companion skill:** `memory-expert` · **Since:** Jun 2026 · **Last updated:** 2026-10-30

Agent Memory is a durable, inspectable memory **about the user** that survives across sessions: preferences ("I prefer metric tons"), facts about the user or their work ("I'm a finance analyst"), and style leanings ("answer concisely"). Recall is deterministic and reaches every engine class through the context window; writes arrive through three doors — the `TDA_Memory` tool (the user asked), the Memory manager panel / REST (the user typed it), and an opt-in background extractor (the agent inferred it). Every item is a row the user can see, pause, move, edit and erase.

Design record: `docs/roadmap/TIER_2.2_AGENT_MEMORY.md`. This document describes the code as it stands.

Agent Memory is not the session's working memory. The Context Window (`chat_object`, the conversation history module) is this session's memory and is purged with `POST /api/session/<id>/purge_memory`; Agent Memory is the cross-session store described here. The profile editor's **Memory** tab shows both as two sub-tabs (*Context Window* · *Agent Memory*); the Agent Memory sub-tab only explains the model and links to the component's settings — there is nothing to configure there.

---

## 1. Module map

| File | Role |
|---|---|
| `types.py` | Pure data types and vocabularies: `MemoryItem`, `MemoryConfig`, `RecallResult`; `KINDS` (`preference · fact · style`), `SOURCES` (`explicit · learned · manual`), `MODES` (`off · recall · recall_write`), `SCOPES` (`profile · global`); deterministic `infer_kind` / `resolve_kind` and `detect_global_scope` |
| `store.py` | `MemoryStore` — ORM CRUD over the `agent_memory` table, dedup-upsert, recall queries, GDPR erase, vote-driven pause/resume, and the **memory firewall** (`screen_memory_content`) |
| `recall.py` | Deterministic ranking (`score_item`, `rank`) and the context block (`format_block`) — no I/O, no LLM |
| `adapter.py` | Resolves the effective `MemoryConfig` for a profile from the component chain (`memory_component_enabled`, `resolve_config`) |
| `runtime.py` | The one engine-agnostic seam: `recall_for_turn`, `forget`, `maybe_dispatch_extraction` / `_extract_and_store` (background learning, cost-accounted) |
| `extractor.py` | The one place memory uses an LLM: `build_extraction_prompt`, `parse_candidates` |
| `handler.py` | `MemoryComponentHandler` — the `TDA_Memory` tool |
| `renderer.js` | The inline card (`renderMemory`) and the split-panel manager (`openMemorySplitPanel`) |
| `components/builtin/context_window/modules/user_memory/` | `UserMemoryModule` — delivers recall into the context window |

## 2. Tool contract (`TDA_Memory`)

From `manifest.json` → `tool_definition` (the correctness baseline; the skill only adds judgement):

| Arg | Required | Meaning |
|---|---|---|
| `action` | yes | `remember` · `forget` · `list_memories` |
| `content` | for `remember` | a short first-person durable fact |
| `kind` | no | `preference` · `fact` · `style` — inferred by the component when omitted or invalid |
| `scope` | no | `agent` (default — this agent only) or `global` (shared across all the user's agents); `shared` and `all` are accepted as `global` |
| `query` | for `forget` | text matching the memory to delete |

`validate_arguments` rejects `remember` without `content` and `forget` without `query`. The handler's actions:

- **remember** — builds a `MemoryItem` (`source=explicit`, the session and turn recorded) and calls `MemoryStore.add`. Scope is global when `scope` is `global/shared/all` **or** `detect_global_scope(context["user_query"])` finds unambiguous cross-agent phrasing in the user's request ("global agent memory", "for all my agents", "across agents", "shared memory", "globally") — a deterministic safety net for weak models that ignore the `scope` argument. Otherwise the item is scoped to the calling profile.
- **forget** — `runtime.forget(user, query, profile_id)`: deletes the **single best-matching** item within this agent's recall set (user-global ∪ this profile), ranked by the recall scorer; genuine token overlap is required, so an incidental substring never deletes, and another agent's local memory is never touched. Returns 0 or 1.
- **list_memories** — lists the agent's **recall set**: the user's active global items plus this agent's own (`list_for_recall(user, profile_id, scope)`, with the agent's configured `componentConfig.memory.scope`; `global` lists all of the user's active items). Another agent's local memories never reach this agent's context through it. The card's stat chips still count the user's whole pool.

Every payload carries `stats` (`total · global · local · agent · kinds`) and a message folding the counts in ("Saved to this agent memory · 7 memories · 2 global · 5 local"). Failures return `spec.error` and "Memory operation failed." — never an exception into the turn. The handler is deterministic (the `BaseComponentHandler.is_deterministic` default), so on the Optimize lane it takes the component fast path without a tactical LLM call; the manifest also declares `backend.fast_path: true`.

**Instructions** (`instructions.json`, intensity-keyed `none · medium · heavy`): both levels tell the model that recall is automatic (the `[Remembered about this user]` block) and that the tool is only for CHANGING memory; `heavy` adds "proactively capture lasting preferences/facts". Both state the `scope="global"` rule and "no emoji". Intensity comes from `componentConfig.memory.intensity`, default `medium`.

## 3. Deterministic layer vs skill layer

Per [COMPONENT_ARCHITECTURE.md §0](COMPONENT_ARCHITECTURE.md), the component absorbs every decision code can make:

| Decision | Who makes it |
|---|---|
| Which memories to recall this turn, in what order, how many | `recall.rank` (code — the LLM is never asked) |
| `kind` of a memory | `types.infer_kind` (style cues win over preference cues, else fact) unless the model passes a valid one |
| Global vs agent scope on clear phrasing | `types.detect_global_scope` overrides an ignored `scope` |
| Dedup / update-in-place | `MemoryStore.add` on a colliding `mem_key` |
| Quarantine of an injected instruction | the memory firewall (§6) |
| Which item a `forget` removes | token-overlap ranking in `runtime.forget` |
| **Whether** a statement is durable, how to phrase it, when global is warranted | the model — taught by `skills/builtin/memory-expert/memory-expert.md` |

The skill (`memory-expert`, `injection_target: user_context`, linked by `component_id: "memory"` in `skills/skill_registry.json`) carries the durability test (about the user · durable · stated as lasting), the anti-pattern table (never store the turn's question or the answer's data), first-person phrasing, the scope rule (agent by default, global only for genuine user-level facts), confirm-in-words, and "don't re-state what is already recalled". It auto-assigns whenever the component is enabled (`skills/manager.py:resolve_auto_skill_specs`). Delete the skill and the tool still works: the manifest and instructions carry every required argument.

## 4. Recall — the read path

```
every LLM call (any engine)
  └─ context-window orchestrator, pass 3
       └─ UserMemoryModule.contribute(budget, ctx)
            └─ runtime.recall_for_turn(user, profile, profile_type, query, budget)
                 ├─ adapter.resolve_config   → MemoryConfig(mode, scope, max_items)
                 ├─ MemoryStore.list_for_recall(user, profile_id, scope)   (active rows only)
                 ├─ recall.rank(candidates, query, cfg, budget)
                 ├─ MemoryStore.bump_recall(ids)       (recall_count, last_recalled_at)
                 └─ recall.format_block  → "[Remembered about this user]\n- …\n- …"
```

**Candidates.** `scope="profile"` (default) recalls user-global items ∪ this profile's items; `scope="global"` recalls all of the user's active items, including other agents' local ones. Inactive (paused or quarantined) rows are never candidates.

**Ranking** (`score_item`, in [0,1]): `0.50·overlap + 0.25·confidence + 0.15·source_weight + 0.10·recall_factor`, where overlap is the share of the query's tokens (≥3 chars, stopwords removed) found in the item, source weights are `explicit 1.0 · manual 0.85 · learned 0.7`, and `recall_factor = 1 − 1/(1+recall_count)`. Memory is **ranked, never filtered** — a preference like "answer concisely" rarely shares a word with the question, so lexical overlap is a booster, not a gate. Ties break by `updated_at` then `id`, so the output is reproducible. Items are taken until `max_items` (default 8, clamped 1–50) or the module's token budget is reached (the first item always fits).

**The block.** `format_block` emits `[Remembered about this user]` followed by one `- <content>` line per item; an empty result is an empty string.

**Delivery (Nov 2026).** The module's contribution is READ: the executor caches it after assembly (`_cw_memory_text`, `_get_memory_context()`), and `executor._session_context_blocks()` — memory first, then the `findings` block — is the ONE text every engine appends after its KG enrichment (Ideate, Focus, ReAct via `conversation_engine`, the strategic planner, the Optimize plan gate). The Coordinate engine sets `coordinator.memory_context` after its own assembly and `GenieCoordinator.execute` puts it on the coordinator's prompt once, before the KG enrichment; its experts recall through their own engines. Until this seam existed the block was assembled, measured and shown in the Live Status snapshot, but no engine read `contributions["user_memory"]` — recall never reached a model. Gate: `test_cw_delivery_defects.py` §5.

**The context module** (`user_memory/manifest.json`): applies to all four profile types; defaults `priority 65 · target 4% · min 0% · max 12%`; `condensable: true`, `purgeable: false`. `condense` drops the lowest-ranked lines first (they arrive ranked). When nothing is recalled the contribution is empty with `reason` `no_memory` (no candidates) or `disabled_or_empty`; every error contributes nothing (`reason: error`). Its snapshot metadata carries `recalled · considered · scope · items` (the item contents), so the Live Status context-window snapshot shows exactly what was recalled. The module is active without editing any stored Context Window Type, by the orchestrator's registry-default merge (see [CONTEXT_WINDOW_ARCHITECTURE.md](CONTEXT_WINDOW_ARCHITECTURE.md)).

## 5. Writes

| Door | Source | Path |
|---|---|---|
| The model calls `TDA_Memory remember` | `explicit` | `MemoryComponentHandler.process` → `MemoryStore.add` |
| The user adds one in the manager / REST | `manual` | `POST /v1/memory` → `MemoryStore.add` |
| Background learning (opt-in) | `learned` | `runtime.maybe_dispatch_extraction` → `_extract_and_store` |

**Dedup-upsert.** `MemoryStore.add` updates in place when an item with the same `(user, profile_id, mem_key)` exists (`mem_key` is normalised lower-case, ≤120 chars); otherwise it inserts. The tool path sets no `mem_key`, so explicit writes always insert; extracted candidates carry the model's `mem_key`.

**Background learning.** Off unless the profile sets `componentConfig.memory.learn: true` (mode `recall_write`). After a turn that produced a `final_answer`, `execution_service` calls `maybe_dispatch_extraction` for every engine — skipped for Genie expert sub-turns and for guardrail-blocked turns. It is fire-and-forget (strong-referenced task, never blocks or raises into the turn). `_call_extraction_llm` resolves the profile's own LLM configuration and credentials and runs `extractor.build_extraction_prompt` (the user's input plus the first 600 characters of the answer; "return at most 3 items … `[]` if nothing durable"); `parse_candidates` keeps ≤3 items of ≤400 chars with `source=learned` and the model's confidence (default 0.7). Items are stored per profile, or user-global when `componentConfig.memory.scope` is `global`. The call is priced through `CostManager` and booked through `core/post_turn_usage.book_post_turn_call` as a `memory_write` step "Memory Updated" with the tokens, cost and stored items — so learning is visible and paid for, never silent.

**Vote feedback.** A downvote on a turn pauses (`active=False`, never deletes) the `learned` items that turn produced; clearing or flipping the downvote resumes them; an upvote does nothing. Explicit and manual items are never touched by a vote. Consumer: `agent/feedback/consumers/memory.py` → `MemoryStore.set_active_by_source`; switch `FEEDBACK_SIGNALS.memory_deactivate` (default on). See [FEEDBACK_SIGNAL_ARCHITECTURE.md](FEEDBACK_SIGNAL_ARCHITECTURE.md).

## 6. The memory firewall

A memory that carries an injected instruction ("ignore all previous instructions…") is a **persistent** compromise vector: it would be recalled into every future turn. `store.screen_memory_content` runs the inbound guardrail's own detector (`agent/guardrails/primitives.detect_prompt_injection`, deterministic, 0 tokens) at the one chokepoint every write passes:

- **`MemoryStore.add`** — a matching item is stored **quarantined** (`active=False`): preserved and visible in the manager, never recalled. A warning is logged, a `memory_quarantine` trust event is recorded (`core/trust_events.record_event`, with the labels and the source) and a live notice is broadcast to the user.
- **`MemoryStore.update`** — edited content is screened too, so an edit cannot be a side door; a match forces `active=False` and, after the commit, records the same `memory_quarantine` trust event and live notice as a new write (`source: "edit"`). Both paths report through one helper, `store._report_quarantine`.

The screen is gated on the guardrails master switch (`APP_CONFIG.GUARDRAILS_ENABLED`); with it off the write path is byte-identical. It fails open — a firewall bug never blocks a legitimate write.

In the assurance registry (`agent/assurance_points.py`) `memory_firewall` is a **GUARDED** control registered with `excluded=True`: it protects future turns, not this one, so it never appears on an answer's path (the dial, a turn reading) — "it belongs on the agent's posture, not on a turn". The Assurance Guide still lists it under GUARDED, marked as not on a turn. Its fate is `refused`; its only tunable is the guardrails master switch.

## 7. Storage

ORM model `AgentMemory` (`auth/models.py`, table `agent_memory`, created by `Base.metadata.create_all` with the auth schema — no `schema/*.sql` file). One row per memory, so each fact can be scoped, inspected and erased independently:

`id` (uuid) · `user_id` (FK `users.id`, `ON DELETE CASCADE`) · `profile_id` (NULL = user-global) · `kind` · `mem_key` · `content` · `source` · `confidence` · `active` · `session_id` · `turn_id` · `recall_count` · `created_at` · `updated_at` · `last_recalled_at`. Indexes `ix_agent_memory_user_profile (user_id, profile_id, active)` and `ix_agent_memory_dedup (user_id, profile_id, mem_key)`.

Every store method opens its own short ORM session; the SQLite serialization layer ([SQLITE_CONCURRENCY.md](SQLITE_CONCURRENCY.md)) holds the process lock around the calls.

## 8. Governance and enablement

- **One enablement rule.** `adapter.memory_component_enabled` is a thin wrapper over `components.enablement.is_enabled(..., require_action=True)` — global admin gate and per-user override (Administration → Components) → explicit `componentConfig.memory.enabled` → per-user default → manifest `profile_defaults.enabled_for`. Disabled ⇒ mode `off`: no recall, no tool, no learning.
- **Default-on (Aug 2026).** `enabled_for` lists all four classes (`llm_only`, `rag_focused`, `tool_enabled`, `genie`), so a profile with no `componentConfig.memory` has memory on (recall-only); an explicit `enabled: false` wins. Agent packs set it explicitly — `agent_packs/common/component_config.focused_component_config(memory=True)` keeps memory on while switching the action components off.
- **Per-profile settings** (`componentConfig.memory`): `enabled`, `intensity`, `learn` (background learning, default false), `scope` (`profile` | `global`, which items are recalled and where learned items land), `maxItems` / `max_items` (default 8). No profile-editor control writes `learn`, `scope` or `maxItems` today; they are read when present.
- **`offers_tool`** — not overridden: wherever the component is enabled, `TDA_Memory` is offered.
- **Manifest declarations** — `instances: "agent"` (the things it keeps belong to an agent, so the Workspace scopes the section to the agent); `signals`: `announces` `created · updated · deleted` (every `MemoryStore` write method is decorated with `@_signals.announces("memory", …)`), `listens` `uderia:component-changed`, `emits` none, `consumes_refs: false`; `answer_surface.open: true`; render targets `inline` (default) and `sub_window`.
- **Data-subject rights outrank the feature flag.** `POST /v1/memory` (creating) is refused with `403 component_not_available` when the component is unavailable to the user; every read, edit and erase stays available so a user can always inspect and erase their memory after an admin disables the component.

## 9. UI

- **Inline card** (`renderMemory`) — teal accent (`#14b8a6`), the message, the stored fact (remember) or up to three items (list), stat chips (total · global · local, kinds), and the shared open-in-panel control. It auto-opens the manager when canvas split mode is on (never on a replayed answer).
- **Manager panel** (`#memory-split-panel`, `openMemorySplitPanel`) — two panes: an owner rail (Global + every agent that has memory, has the component enabled, or is the active agent, coloured by IFOC type) and the selected owner's items. Per item: pause/activate, edit inline, move/promote to another owner or Global, delete; bulk pause/delete; an add composer; and, for an agent owner, **Preview** — what that agent would recall for a typed question (`/v1/memory/recall-preview`), shown ranked and read-only. It follows `UderiaBus.onComponent('memory', …)`, so a write made anywhere (another tab, a REST script, an agent turn) refreshes it.
- **Workspace section** — the `memory` entry in `static/js/handlers/workspacePanelHandler.js` lists two buckets from `/v1/memory/buckets` (*Local memory (@TAG)* and *Global memory*, each with its count); "+" opens the agent's memory with the add field focused.
- **Agent Anatomy** (`static/js/handlers/agentAnatomy.js`) shows a Memory station (recall on/off, learning, item count). On/off is the **resolved** enablement (`componentsEnabled` from `GET /v1/profiles`), so a default-on agent with no stored `componentConfig.memory` reads on.
- **Platform Ontology** shows agent memory as an instantiation; a learned item can deep-link to the session it was extracted from.

## 10. REST endpoints

All in `api/rest_routes.py`; a user only ever reaches their own memory.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/memory` | List items (incl. paused); `?profile_id=` (exact) `?kind=` |
| `POST` | `/v1/memory` | Add a `manual` item; `scope` `agent` (with `profile_id`) or global (also when no `profile_id`); gated on availability |
| `PUT` | `/v1/memory/<item_id>` | Edit `content · kind · active · confidence · profile_id` (move/promote; empty = global) `· mem_key` |
| `DELETE` | `/v1/memory/<item_id>` | Delete one item |
| `DELETE` | `/v1/memory/all` | GDPR erase all of the user's memory, or one profile's via `?profile_id=` |
| `GET` | `/v1/memory/buckets` | The two Workspace buckets for `?profile_id=` |
| `GET` | `/v1/memory/owners` | Owners for the manager rail; `?active_profile_id=` marks the active agent |
| `POST` | `/v1/memory/recall-preview` | `{profile_id, query}` → what would be recalled (0 tokens, budget 2000) |

## 11. Gates

| Gate | What it pins | In the battery |
|---|---|---|
| `test/test_audit_followups.py` | `list_memories` returns the recall set only · an edit the firewall flags records the trust event · Anatomy reads the resolved enablement | yes |
| `test/test_memory.py` | types and kind/scope inference · deterministic recall ranking · adapter governance · extractor parsing · store CRUD/dedup (real temp SQLite) · `forget` scoping · the firewall (quarantine on add and on edit, never recalled) | no — run directly |
| `test/test_feedback_memory.py` | a downvote pauses only `learned` items of that turn; reversible | no — run directly |
| `test/test_component_enablement.py` (`t_memory_default_on`) | memory default-on for every class; an explicit `enabled: false` wins; the adapter agrees with the one rule | yes |
| `test/test_assurance_points.py`, `test/test_guide_parents.py` | `memory_firewall` is a deliberate exclusion with a reason; it sits under GUARDED | yes |
| `test/test_p2_components.py` | `TDA_Memory` tool contract (required/all args) and argument validation | no |
| `test/test_component_signals.py` | every builtin manifest declares `signals`; the `MemoryStore` write seams (`add · update · delete · erase_all · set_active_by_source`) announce | yes |
| `test/test_component_bus.mjs`, `test/test_component_instances.mjs` | the bus contract; every manifest's `instances` declaration and the Workspace scoping that reads it | yes |
