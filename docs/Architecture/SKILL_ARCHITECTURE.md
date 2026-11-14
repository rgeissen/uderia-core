# Skill Architecture

> **Pre-processing prompt injections** — reusable markdown instruction documents injected into LLM context before query execution.

Skills provide transparent, auditable context enhancement. They are **fully transient**: skill content is injected into local variables per-request and never enters persistent chat history (`chat_object`).

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Backend Architecture](#2-backend-architecture)
3. [Skill Discovery Pipeline](#3-skill-discovery-pipeline)
4. [Content Resolution & Param Blocks](#4-content-resolution--param-blocks)
4a. [Progressive Disclosure Skills — Root + On-Demand Library](#4a-progressive-disclosure-skills--root--on-demand-library)
4b. [Authoring a Progressive Disclosure Skill](#4b-authoring-a-progressive-disclosure-skill)
5. [Injection Points by Profile Type](#5-injection-points-by-profile-type)
5a. [Companion Skills & Auto-Assign](#5a-companion-skills--auto-assign)
6. [Skill Lifecycle & Transience](#6-skill-lifecycle--transience)
7. [Database Schema](#7-database-schema)
8. [REST API Endpoints](#8-rest-api-endpoints)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Built-in Skills](#10-built-in-skills)
11. [Admin Governance](#11-admin-governance)
12. [CSS & Visual Design](#12-css--visual-design)
13. [File Reference](#13-file-reference)
14. [Agent Pack Integration (v1.3)](#14-agent-pack-integration-v13)

---

## 1. System Overview

### What Skills Do

Skills inject markdown instructions into LLM context to modify behavior — response style, domain expertise, formatting rules — without modifying system prompts permanently or polluting conversation history.

### Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Transient** | Injected into local variables, never stored in `chat_object` |
| **Transparent** | `skills_applied` SSE event shows what was injected and token cost |
| **Parameterizable** | `<!-- param:strict -->` blocks enable runtime behavior variants |
| **Governed** | Admin controls disable/enable skills globally or selectively |
| **Portable** | Export/import as `.skill` zip; flat `.md` and `SKILL.md` auto-discover |
| **Claude Code Compatible** | `SKILL.md` with YAML frontmatter natively supported for import, export, and discovery |

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ FRONTEND                                                             │
│                                                                      │
│  User types #sql-expert:strict                                      │
│       ↓                                                              │
│  Badge rendered in input area                                        │
│       ↓                                                              │
│  submitQuestion() → POST /ask_stream                                │
│     { message: "...", skills: [{name: "sql-expert", param: "strict"}] }
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ BACKEND: execution_service.py                                        │
│                                                                      │
│  run_agent_execution():                                             │
│  1. Save original user_input to chat_object                         │
│  2. Resolve skills (the SKILL RESOLUTION block):                    │
│     - Auto-skills: resolve_auto_skill_specs (profile + companions)  │
│     - Manual #skills checked against the profile's enabled list     │
│     - Filter by admin governance (is_skill_available)               │
│     - Load content via SkillManager.resolve_skills()                │
│     - Emit skills_applied SSE event                                 │
│  3. Pass SkillResult to PlanExecutor / GenieCoordinator             │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ EXECUTOR / PLANNER (profile-dependent injection)                     │
│                                                                      │
│  tool_enabled  → planner._generate_meta_plan → planning_prompt      │
│  llm_only      → engines/ideate_engine        → system_prompt + msg │
│  llm_only+tools→ engines/conversation_engine  → agent system prompt │
│  rag_focused   → engines/focus_engine         → system_prompt + msg │
│  genie         → genie_coordinator            → system_prompt + msg │
│                                                                      │
│  All injection targets are LOCAL VARIABLES — never persisted         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LLM CALL                                                             │
│                                                                      │
│  LLM receives context augmented with skill content                  │
│  Generates response influenced by skill instructions                │
│  Response saved to chat_object (skill content is NOT)               │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Backend Architecture

### Module Structure

```
src/trusted_data_agent/skills/
├── __init__.py
├── models.py          # Data classes: SkillSpec, SkillContent, SkillResult
├── loader.py          # Content loading, param block resolution
├── manager.py         # Discovery, registry, singleton manager
├── db.py              # Per-user activation persistence (SQLite)
├── settings.py        # Admin governance (skill_settings table)
├── package.py         # .skill zip export/import (Uderia + Claude Code SKILL.md formats)
├── versions.py        # Append-only version archive (tda_skills/.skill_versions/), version pins
├── library_use.py     # Progressive-library reads per turn (attribution, read-before-SQL)
├── library_availability.py  # Sections offered for what the agent's own database can run
└── execution_record.py      # A library's "executed on" claim, signed with the library

skills/
├── skill_registry.json    # Registry of built-in skills (with component_id links)
├── seed/                  # Repo-distributed, read-only seed skills (teradata-coordinator)
└── builtin/               # 20 built-in skill directories
    ├── <component>-expert/ … flow-designer/   # 14 component companions (§5a)
    ├── teradata-sql-expert/  teradata-patterns/  # database (teradata-patterns is progressive, §4a)
    └── table-format/  concise/  detailed/  step-by-step/   # style / formatting
```

### Data Models (`models.py`)

#### SkillSpec
Parsed from user input (e.g., `#sql-expert:strict`):

```python
@dataclass
class SkillSpec:
    name: str                    # Activation name
    param: Optional[str] = None  # Optional parameter
```

#### SkillContent
Loaded content from a single skill:

```python
@dataclass
class SkillContent:
    skill_name: str
    content: str                              # Markdown to inject
    injection_target: str = "system_prompt"   # "system_prompt" | "user_context"
    metadata: Dict[str, Any]                  # Full manifest
    param: Optional[str] = None

    @property
    def content_length(self) -> int: ...
    @property
    def estimated_tokens(self) -> int: ...    # len(content) // 4
```

#### SkillResult
Merged result from all selected skills for a query:

```python
@dataclass
class SkillResult:
    system_prompt_additions: List[str]             # Skills targeting system prompt
    user_context_additions: List[str]              # Skills targeting user context
    skill_contents: Dict[str, SkillContent]        # Per-skill details

    @property
    def has_content(self) -> bool: ...
    @property
    def total_estimated_tokens(self) -> int: ...

    def get_system_prompt_block(self) -> str: ...   # Concatenated with \n\n
    def get_user_context_block(self) -> str: ...     # Concatenated with \n\n
    def to_applied_list(self) -> List[Dict]: ...     # Metadata for SSE event
```

The `to_applied_list()` method builds the `skills_applied` event payload:

```python
[{
    "name": "sql-expert",
    "param": "strict",
    "injection_target": "system_prompt",
    "content_length": 2847,
    "estimated_tokens": 711
}]
```

---

## 3. Skill Discovery Pipeline

The `SkillManager` (singleton via `get_skill_manager()`) discovers skills from multiple sources with user overrides taking priority.

### Storage tiers (one meaning per word — settled Aug 2026)

| Tier | Directory | Owner | Writes at runtime? |
|------|-----------|-------|--------------------|
| **Builtin** | `skills/builtin/` | the platform product | never |
| **Seed** | `skills/seed/` (legacy: `skills/user/`, `~/.tda/skills/`) | the deployment's git repo | never (read-only; the delete endpoint refuses with 403) |
| **State** | `tda_skills/` (`SkillManager.state_dir`; env `UDERIA_USER_SKILLS_DIR`) | the running instance | **the ONE write path** — every runtime install (pack, editor, import) lands here via `save_skill()` |

`state_dir` was historically named `user_dir` (a deprecated read/write alias
survives). `tda_skills/` is gitignored; its `.skill_versions/` subtree is the
append-only version archive (see §Provenance & Versions below).

### Five Discovery Modes

| Mode | Source | Trigger | Manifest |
|------|--------|---------|----------|
| **Registry** | `skills/builtin/` | Listed in `skill_registry.json` | Required (`skill.json`) |
| **Seed/State Override** | seed dirs, then `tda_skills/` | Same name as registry skill | Required (`skill.json`) |
| **Manifest-Free** | any read dir, `<dir>/` | Subdirectory with `.md` file | Auto-generated |
| **Flat File** | any read dir, `*.md` | Direct `.md` file | Auto-generated |
| **Claude Code** | any read dir, `<dir>/` | Subdirectory with `SKILL.md`, no `skill.json` | Parsed from YAML frontmatter |

When both `SKILL.md` and `skill.json` exist in a directory, `skill.json` is loaded first, then `SKILL.md` frontmatter is merged on top — `name` and `description` from frontmatter take precedence, and `main_file` is set to `SKILL.md`.

### Discovery Order

```
1. Load skill_registry.json
   └─ For each entry:
      ├─ Check the read dirs (seeds → tda_skills/), later dirs win
      └─ Fall back to skills/builtin/<name>/

2. Auto-discover the read dirs (skills/seed/ + legacy seeds + tda_skills/)
   ├─ Mode C: Direct .md files (flat mode — no dir)
   ├─ Mode D: Subdirs with SKILL.md but no skill.json (Claude Code format)
   ├─ Mode A: Subdirs with skill.json (manifest mode)
   └─ Mode B: Subdirs with .md but no skill.json (manifest-free)
```

Later sources override earlier ones by skill name.

### Auto-Generated Manifests

For manifest-free and flat-file modes, the manager builds a manifest from the `.md` file:

```python
{
    "name": skill_id,                    # From filename/dirname
    "version": "1.0.0",
    "description": "<first non-heading line>",
    "author": "User",
    "main_file": "skill-name.md",
    "last_updated": "<YYYY-MM-DD>",
    "_is_user": True,
    "_auto_generated": True,
}
```

### Resolve Flow

```python
manager = get_skill_manager()
result = manager.resolve_skills([
    SkillSpec(name="sql-expert", param="strict"),
    SkillSpec(name="concise", param=None),
])
# result.get_system_prompt_block() → concatenated skill content
```

For each spec:
1. Look up manifest in `self.manifests[name]`
2. Call `load_skill_content(skill_dir, manifest, param)`
3. Wrap in delimited block: `--- Skill: SQL Expert (strict) ---\n\n{content}`
4. Route to `system_prompt_additions` or `user_context_additions`
5. Store in `result.skill_contents[name]`

---

## 4. Content Resolution & Param Blocks

### Param Block Syntax

Skills support optional parameter-specific content using HTML comments (invisible to non-Uderia markdown renderers):

```markdown
# SQL Expert

Base instructions that always apply.
Use explicit JOINs. Qualify column names.

<!-- param:strict -->
Enforce ANSI SQL compliance strictly.
Flag any deviation from standards.
<!-- /param:strict -->

<!-- param:lenient -->
Suggest improvements but accept valid SQL.
Focus on correctness over standards.
<!-- /param:lenient -->
```

### Gate blocks (per-agent content)

A skill may also carry `[[gate:<name>]] … [[/gate]]` blocks — the **same gates component
instructions use** (`ComponentManager.apply_instruction_gates`). `SkillManager.resolve_skills`
resolves them for the agent running the turn (`profile_config`), so a companion skill can teach
a capability only to the agents that have it (e.g. `database-navigator-expert`'s *Running your
own query* section behind `navigator_query`). Content without markers is byte-identical; if the
gates cannot be evaluated every gated block is removed (`gate_skill_text` fails **closed** — a
skill must never teach a capability an agent may not have). Place a block so that removing it
leaves the surrounding text exactly as it was.

### Resolution Algorithm (`loader.py`)

| Scenario | Result |
|----------|--------|
| No param | Strip ALL param blocks → base content only |
| `param="strict"` | Base content + `<!-- param:strict -->` block |
| Invalid param | Warning logged → base content only |

```python
def _resolve_content(raw: str, param: Optional[str]) -> str:
    if param is None:
        return _ALL_PARAM_BLOCKS_RE.sub("", raw).strip()

    match = _PARAM_BLOCK_RE.search(raw)  # Find matching block
    base = _ALL_PARAM_BLOCKS_RE.sub("", raw).strip()

    if match:
        return f"{base}\n\n{match.group(2).strip()}"
    return base  # Param not found, fallback
```

### Manifest Configuration

Parameters are declared in the `uderia` section of `skill.json`:

```json
{
    "uderia": {
        "allowed_params": ["strict", "lenient"],
        "param_descriptions": {
            "strict": "Enforce all SQL standards strictly",
            "lenient": "Suggest improvements but accept any valid SQL"
        },
        "injection_target": "system_prompt"
    }
}
```

### Editor Functions

The loader also exposes functions for the skill editor:

| Function | Purpose |
|----------|---------|
| `extract_param_blocks(raw)` | Returns `{param_name: content}` dict |
| `get_base_content(raw)` | Returns content outside all param blocks |
| `build_full_content(base, param_blocks)` | Reconstructs full markdown |

---

## 4a. Progressive Disclosure Skills — Root + On-Demand Library

A skill that uses **runtime progressive disclosure** is a **progressive disclosure skill**
(the operator-facing name; its sibling mechanism is config-time disclosure via `param`
blocks, §4): a lean, always-injected **root** (the ordinary
`main_file`, unchanged loader semantics) plus an on-demand **library** of documents the
agent fetches mid-turn through the `TDA_SkillLibrary` builtin component
(`components/builtin/skill_library/`). This is the platform's runtime lazy-loading form —
the config-time form remains `param` blocks (§4); the two compose.

**The identity shift, stated honestly:** skills are no longer *only* pre-processing
injections — a progressive skill is a pre-processing ROOT plus a governed runtime
library. Everything in §§2-5 still describes the root exactly; the library never passes
through the loader (it returns as tool output).

### Who decides what is loaded — the control model

**The model does.** The platform never selects a section: there is no similarity
scoring, no embedding, no planner-inserted load phase, and no pre-fetch anywhere in the
request path. The only thing that ever opens a section is the agent calling
`TDA_SkillLibrary(action='read', anchor=…)` because it judged it needed that section.

This is the sharp difference from RAG. In retrieval the *platform* chooses what the model
sees by ranking chunks against the question; here the model reads a table of contents and
chooses, the way a person consults a manual. That works because the library is a
reference work with named sections rather than a pile of chunks — a name plus a one-line
summary is enough to choose correctly, and the result is a fetch you can point at
afterwards instead of a similarity score you have to trust. (The same tiered pattern
Claude's own skills use: descriptions always resident, bodies and bundled files pulled on
the model's judgement.)

Three parties decide three different things, and conflating them is the usual source of
confusion:

| Who | When | Decides |
|---|---|---|
| Skill author | build time | Which sections exist, their anchors and summaries, and the reading procedure baked into the always-resident root |
| Operator | config time | Which sections this agent may touch **at all** — the anchor grant, enforced fail-closed at the tool layer |
| The model | runtime | Which of the permitted sections to open *now* |

So the grant is a **boundary, not a selection**: it decides what is refusable, and inside
it the model still chooses. What steers that choice is the root — the baked anchor index,
the author's prescriptive reading order, and the fetch discipline ("never answer from
memory what the library holds; where anything you have been told conflicts with a fetched
section, the section wins") — plus `list_sections` and the near-miss suggestions carried
by a refusal, so a wrong guess self-corrects in the same turn.

**The honest weak point: nothing CAUSES a read to happen.** (For a library of executed SQL, whether it happened before the SQL ran is now measured — see *Whether a read happened before the SQL ran*, below.) A model can answer from
pre-training, skip the fetch, and produce something plausible and subtly wrong. A
prescriptive root reduces the rate, it does not eliminate it — which is why **skip-read rate** is the headline metric for any evaluation
of a progressive skill (a shape that is cheap because it stopped reading is worse than the
one it replaced). The deterministic remedy exists and is deliberately **not** built: for
`tool_enabled` profiles the planner could insert a load phase so the platform performs the
read rather than trusting judgement (the Component → LLM → Skill priority order). Today
selection sits at the LLM layer and everything else — the boundary, refusal wording,
near-miss hints, size caps — sits in the component. Whether to promote selection is a
decision measurement should drive: if skip-read is rare, a forced read only burns tokens.

**First promotion, measured:** for platform playbooks the platform now performs the read —
`TDA_Platform` `find` delivers the playbook that covers it, through this component's own read
path and grant, recorded as a read (skip-read 1–3 of 4 per run → 0 of 4 in three runs on a weak
model). See [PLATFORM_OPERATIONS_ARCHITECTURE.md §16](PLATFORM_OPERATIONS_ARCHITECTURE.md).

**What IS caught, since F-001: a read the answer CLAIMS and never made.** Session
`a1d0b01d` opened with *"I have fetched `master-design` and the `domain` module
documentation"* and had called the tool zero times in ten turns. That is the failure mode
that matters — not a thin answer, but a confident fabrication wearing a citation, which
without a check is indistinguishable from success unless someone opens the trace.

The honesty gate's third branch (`kind="unread_citation"`) closes it. Deterministic and
0-token, because the anchor index is a **closed vocabulary**: it never guesses whether a
phrase names a section, only whether a KNOWN section name appears in a *completed* read
claim the turn's own record does not support. An intention (*"I will read `domain` next"*)
and a negation (*"I could not open it"*) are not claims. The act is an **append** — a
disclosure naming the unread sections — never a rewrite: the gate knows the citation is
unbacked, not that the answer is wrong, and replacing sound prose with a procedural
complaint would be the worse trade. `citation_fidelity` bands the same number on the
Scorecard, so skip-read stops being anecdotal and becomes a trailing metric per agent.

Two honest limits remain. It **detects, it does not cause** — the answer stays ungrounded,
it merely stops passing as grounded. And it catches the *citation*, not the fabricated
*content*: an answer that invents freely while citing nothing is invisible to it.

### What a fetch costs, and where the cost lands

A fetch is a **tool call**, so it is not free and — the part that misleads readers of a
trace — **its cost does not land on the fetch**. It lands on the *next* LLM call, whose
prompt now carries the section, and on every later call of the turn, because context is
cumulative. A section is paid for once per subsequent step, not once.

Measured on a real Focus/ReAct turn (`@ANDPA-DESIGN`, two sections opened):

| | input | output | cost |
|---|---|---|---|
| LLM step 1 — decides to fetch | 6,397 | 26 | $0.00065 |
| ↳ `read(master-design)` | — | — | *no cost here* |
| LLM step 2 — section resident, fetches again | 11,006 | 28 | $0.00111 |
| ↳ `read(reference/customer-orders)` | — | — | *no cost here* |
| LLM step 3 — both resident, writes the answer | 14,530 | 895 | $0.00181 |
| **turn** | **31,933** | **949** | **$0.00357** |

The steps sum to the turn total exactly: **every round-trip is already metered like any
other LLM call** (the ReAct step accounting persists each LangChain step to the session
counter exactly once; Optimize meters via `_call_llm_and_update_tokens`). Nothing about
progressive disclosure needs special accounting.

**It still wins, decisively, for a large corpus.** The same intake through the
param-injected pack cost **119,218** input tokens against this turn's 31,933 — 4×, because
a `param` block injects its whole module on *every* call (33,192 tokens) whether the turn
needs it or not. The extra round-trips cost far less than carrying content the model never
reads, and the gap widens with corpus size: injection scales with the library, fetching
scales with what one task opens (here 7.2% of 144K tokens).

**It loses for a small skill.** Below roughly the root's own size, injecting is both
cheaper and faster — there is no round-trip and no second prompt. Do not convert an
ordinary skill; the mechanism is for bodies of knowledge too large to carry.

**The latency trade is real.** Each fetch is a sequential hop, so a turn opening five
sections makes five extra round-trips. Cheaper in tokens is not automatically faster.

**And the extra call is where skip-read pressure comes from** — a model that would rather
answer than pay another round-trip has an incentive to skip the fetch. The cost saving and
the reliability risk are the same mechanism seen from two sides (see the honest weak point
above).

#### Attribution in the trace

Because the cost lands one step later, the trace would otherwise show input jumping
6,397 → 11,006 with nothing saying why. Each LLM step is therefore tagged with the sections
that entered context since the previous step ("includes 1 newly-loaded section (~5,232
tokens): master-design"), and the fetch's own card states the section, its size, and that
no cost lands there.

The tag is **derived by replaying the ordered event list**, not plumbed from the component
to the emitter. Two reasons, both load-bearing:

- **It stands a plan reload by construction.** Live and reload replay the *same* persisted
  list through the *same* renderers, so the two cannot drift — no second persistence path
  to keep in step.
- **A contextvar would be wrong here.** State written by the handler and drained by the
  emitter is invisible across sibling tasks when Optimize runs phases in parallel — the
  hazard the governed-SQL registry already hit.

All paths carry it: `_renderConversationAgentStep` (ReAct — Ideate-rerouted, Focus,
Conversation), `_renderStandardStep` (Optimize), `_renderGenieStep` (Coordinate). The
accumulator resets at every turn boundary (`renderHistoricalTrace` entry,
`conversation_agent_start`, `execution_start`, `genie_coordination_start`) because
mis-attributing a previous turn's sections is worse than omitting the tag. On the backend,
all three engines surface the handler's `skill_section_loaded` event — `genie_coordinator`
did not until this was built, so a coordinator's own fetches were invisible.

Gate: `test/test_progressive_attribution.mjs` — the shipped helpers executed, every
render/reset/emit path enumerated, and a real persisted turn replayed end to end.

### Mechanics

(Design record: PROP-005 (`docs/proposals/PROP-005-progressive-skills-design.md`).)

- **Package**: the `.skill` zip gains a `library/<relpath>` subtree; `skill.json` gains
  `uderia.progressive = {enabled, index[]}` where each index entry is
  `{anchor, title, summary, file, sha256}`. The index doubles as the **integrity
  manifest** — every serialization site verifies per-file sha256 at install and refuses
  a tampered/incomplete payload loudly. ONE serializer owns the format:
  `skills/package.py` (build/parse/zip-slip sanitization) — never hand-roll it.
- **Store**: `save_skill(..., library_files=)` (None = untouched → legacy call sites
  byte-identical; dict = replace wholesale; `{}` = clear). `get_skill_library_files()`
  is INDEX-driven — stray files never ship. The build bakes a human-readable anchor
  index into the root md; `skill.json`'s index is the machine contract.
- **Trust**: `trust/content.py:skill_content` is THE one hash resolver — legacy skills
  keep the exact single-string formula (zero attestation flips); progressive skills bind
  sorted `(relpath, sha256)` pairs into a composite, so an edited library file flips the
  badge (and the agent's ingredient hash) exactly like an edited body. The skill
  validator's prompt-injection scan covers every library file.
- **Exposure**: `TDA_SkillLibrary` is **structurally inert** — offered only to profiles
  that resolve an ACTIVE progressive skill (`is_profile_relevant` hook, consulted by
  `get_active_components` / `get_action_tool_status`). No progressive skill ⇒ the
  platform is byte-identical.
- **The anchor grant**: `skillsConfig.skills[].anchors` (exact names + prefix globs) is
  the runtime twin of `param` — config-time `param` selects what is *injected*, runtime
  `anchors` selects what is *fetchable*. Enforced in the component handler ("the list is
  the grant"), refusals name the admissible set. Absent = all anchors.
  **Operable from two surfaces, ONE dialog** (`static/js/handlers/skillGrantDialog.js` —
  `openSkillGrantDialog({skillId, skillName, totalSections, profileTag, anchors, onSave})`):
  the profile editor (Anatomy → Agent Profiles → edit → Skills → *Library access* on a
  progressive skill's row — best when configuring one agent) and the Skill Library
  component detail (Components → Skill Library → **Libraries** → an agent's grant chip —
  best when comparing agents' slices side by side). The dialog owns the editing; each
  caller owns only persistence via `onSave(grant)`, so the two cannot drift. The index is
  lazily fetched from `GET /v1/skills/<id>/library-index` (metadata only — anchor, title,
  summary), grouped by the library's own path structure with tri-state group toggles
  (the real decision is usually "this half, not that half" — one click, not sixty).
  Three semantics are load-bearing and gate-asserted: **"All" saves NO key** (an
  exhaustive list would freeze the grant and silently exclude sections a later skill
  update adds), **glob patterns are preserved verbatim, never expanded** (they grant
  forward — an anchor granted *by* a glob is marked and cannot be individually unticked,
  which would silently do nothing), and the stored grant is stamped on the editor row
  synchronously at render so a save before the async list renders carries the STORED
  config forward — the "an empty UI is not an empty choice" rule, applied to governance
  data.
- **Pack provenance**: a skill installed by an agent pack is stamped
  `uderia.pack_origin = {pack_name, installed_at}` and recorded in `agent_pack_resources`
  (`resource_type='skill'`), so the ledger knows what it installed. On uninstall the skill
  is **kept and marked orphaned** (`pack_origin.orphaned_at`), never deleted — a skill may
  have been assigned to profiles outside the pack, and silently removing it would break
  them; the response carries `skills_orphaned`. Both surfaces tag it (pack name; a muted
  *orphaned* tag once its pack is gone).
- **Editing**: the skill editor edits the **root only** — a notice on a progressive
  skill states the split, the section count, and that the library is packaged (not
  hand-edited) and survives the save. Critically, the editor **carries forward every
  manifest key it does not model** (`uderia.progressive`, `uderia.pack_origin`,
  `version`, `author`, `keywords`, …): rebuilding the manifest from the modelled
  fields alone silently deleted the whole library binding — the same hand-maintained-
  key-list defect class `core/profile_config_contract.py` exists to end.
- **Gate**: two suites. `test/test_progressive_attribution.mjs` covers the cost
  attribution above (every render/reset/emit path + a real persisted turn replayed).
  `test/test_progressive_skills.py` — baseline-freeze (§B) · serializer (§P) ·
  trust (§T) · component (§C) · marketplace (§M) · UX surfacing (§U) · pack provenance
  (§K) · editor grant (§G) · card layout (§V) · skill editor (§E) · detail dialog (§D) ·
  shared grant dialog (§W). Mutation-tested throughout; §B is the no-regression contract
  and was committed green against unmodified code BEFORE any feature work.

### Sections offered for what the agent's own database can run

A library whose sections hold executed SQL (`teradata-patterns`) records, per statement, the
database functions the statement calls — `index[].requires` (statement name → function names) and
`index[].statements`. The list is not hand-kept: when `scripts/teradata_patterns.py verify` runs a
section, each called identifier is looked up in the verification system's function databases, and
the ones that exist are what the statement requires.

At runtime `skills/library_availability.py` asks the agent's own database which of those functions
it has — through the same catalog provider the Database Navigator uses
(`dbcatalog/agent_source.agent_provider` → `CatalogProvider.existing_functions`, a `DBC.TablesV`
read over the function databases; time-series UAF functions are TableKind `3` there and absent from
`DBC.FunctionsV`). Per section:

| Statements that cannot run | Root page | `list_sections` | `read` |
|---|---|---|---|
| none | listed | as is | as is |
| some (half or fewer) | listed | `[<source> lacks X: n of m statements will not run]` | the section, with those statements named first |
| more than half | **left off**, one line says how many and where to see them | marked "most of this section does not apply" | the section, with the warning |

Two halves so the per-turn root rendering never waits on a database: the library tool probes
(async, cached per profile and source for six hours) and skill injection only reads that cache.
**Fail open:** until a probe succeeded the root page is unfiltered; a source that cannot tell (not
Teradata, no source, a failed read) changes nothing; content is never refused on this inference.
A successful probe logs `skill library: <source> has n of m functions …`. Gate:
`test/test_library_availability.py`.

### A library that claims its SQL was executed is signed with the claim

The build writes, per section, which statement texts ran and on which system —
`index[].executed = {system, on, statements: [sha256 …]}`, derived from the full record
(`verification.json`, which keeps statuses and row counts) and limited to statements that ran. The
claim sits in the manifest, so it travels wherever the skill travels (pack export, marketplace,
install) and a copy carries the same claim as the original.

`skills/execution_record.py` is the one definition used by the build tool, the CI gate and the
platform:

* **the signature binds the claim** — `core/trust/content.skill_content` adds the claim (anchor,
  system and statement hashes; not the date) to the progressive hash. A skill that claims nothing
  keeps its formula byte for byte, so no existing attestation moves.
* **sign-off refuses unexecuted SQL** — `skill_validator` blocks (`unverified`) while any statement
  in a claiming library has no executed record for its current text, naming the statements. A
  signature must not vouch for SQL nobody ran.
* **the parser is shared** — `scripts/teradata_patterns.py` imports `statements` / `statement_hash`
  from the module, so "a statement" means the same thing at build, in CI and at sign-off.

Gate: `test/test_skill_execution_record.py`.

### Whether a read happened before the SQL ran — measured per section

For a library of executed SQL, the question "did the agent read the manual?" has a deterministic
answer on every turn, because the index records which database functions each section's statements
call (`index[].requires`). `skills/library_use.py` records, for every statement the turn runs that
calls one of those functions **as a table operator**, two facts: was a section teaching it opened
earlier in the same turn (order from the turn's own tool calls), and did the statement run.

* **Table operators only** — a function in a table position: `FROM NPATH(…)`,
  `FROM TABLE(MonitorSession(…))`, `EXECUTE FUNCTION [INTO [VOLATILE] ART(…)] [COLUMNS(…)] TD_DFFT(…)`.
  Scalars a section happens to use (`TRUNC`, `LENGTH`, `TO_CHAR`) appear in almost any SQL and
  would attribute ordinary statements to whichever section used the same builtin. A `FROM` inside
  `EXTRACT`/`TRIM`/`SUBSTRING`/`POSITION`/`OVERLAY` is an argument keyword, not a table position.
  The cost: sections that teach only scalars (casting, validation) are not measured.
* **Four cells** — read·ran, read·failed, unread·ran, unread·failed. An operator several sections
  teach counts as read when any of them was opened.
* **Where it is recorded** — once per turn at the citation seam (the same seam, the same ordered
  calls, every executor engine), stamped `turn['library_use']`. A measurement, never an act.
* **Where it is read** — the informational `library_read_first` dimension (JUDGED, RETRIEVE stage:
  the share of library-operator statements run after a read, explanation naming the four cells) and
  the per-section aggregate `GET /v1/evals/library-outcomes`, which sums the cells from the quality
  spans and reports success after a read beside success without one.

That last pair is the evidence the control model above defers to: if statements run as well without
a read as after one, forcing a read only burns tokens; if unread statements fail where read ones run,
a planner-inserted load phase has a measured case.

Gate: `test/test_library_use.py`.

---

## 4b. Authoring a Progressive Disclosure Skill

The format is the contract; there is no builder UI (the skill editor edits the **root
only** — see Mechanics). A skill becomes progressive by shipping a `library/` subtree plus
an index in its manifest. `skills/package.py` is the ONE serializer — never hand-roll a
`.skill` zip.

**1. Decide the split.** The root is always resident and paid for on every turn; the
library is paid for only when fetched. Put in the root what the agent needs to *decide*
(the map, the rules that always hold, the reading procedure) and in the library what it
needs to *do* (the detail). A root that has to be read in full to be useful is not a root.

**2. Lay out the package.**

```
my-skill.skill  (a zip)
├── skill.json                    # manifest, incl. uderia.progressive.index[]
├── my-skill.md                   # main_file — the always-injected ROOT
└── library/
    ├── design/master-design.md
    └── teradata/PLATFORM_PROFILE.md
```

**3. Declare the index** — it is both the addressing table and the **integrity manifest**:

```json
"uderia": {
  "progressive": {
    "enabled": true,
    "index": [
      {"anchor": "master-design", "title": "Master Design",
       "summary": "The eight-module reference model",
       "file": "library/design/master-design.md", "sha256": "…"}
    ]
  }
}
```

`validate_progressive_payload()` refuses the install (loudly, never a partial
materialisation) on: a duplicate anchor, an index entry whose file is missing from the
payload, a sha256 mismatch, or a `file` outside `library/`. Extra non-indexed payload
files are tolerated but **not addressable** — the index, not the zip listing, defines what
exists.

**4. Choose anchors deliberately** — the anchor and its one-line summary are the *entire*
basis on which the model chooses, so they are prompt surface, not filenames. Prefer a
stable semantic name (`master-design`) over a path; derive from the path only where the
hierarchy is itself the meaning (`teradata/modules/domain/01-keymap`), because a prefix
glob (`teradata/*`) is what makes a grant expressible. Renaming an anchor is a **breaking
change**: it invalidates operators' stored grants.

**5. Bake the index into the root.** The model can only choose anchors it can see. Emit
the anchor list with summaries into the root at build time (`skill.json`'s index is the
machine contract; the root's copy is what the model reads), and state the fetch discipline
explicitly — an author who omits "never answer from memory what the library holds" is
choosing a higher skip-read rate.

**6. Ship it.** `POST /v1/skills/import` (multipart) or bundle it in an agent pack; the
marketplace path verifies every file's sha256 *before* materialising. Editing any library
file changes the skill's content hash, so a signed agent bound to it reads 🟠 CHANGED —
by design.

**Worked reference:** `agent_packs/andp/build.py --shape roles` builds a 77-section
library from a source repo (frontmatter anchors where declared, path-derived otherwise),
bakes the index into the root, and emits four role agents whose grants make the
design/implementation boundary structural. Its gate `verify_roles_build.py` asserts the
boundary **live** through the real handler — the pattern to copy for any pack whose agents
must not see each other's half.

---

## 5. Injection Points by Profile Type

Skill content is injected at exactly one point per profile type, always into **local variables** that are scoped to a single LLM call.

### Tool-Enabled Profile (Fusion Optimizer)

**File:** `agent/planner.py` → `_generate_meta_plan()` (the *Inject skill content into planning prompt* block)

```python
if self.executor.skill_result and self.executor.skill_result.has_content:
    sp_block = self.executor.skill_result.get_system_prompt_block()
    if sp_block:
        planning_prompt = f"{planning_prompt}\n\n{sp_block}"
    uc_block = self.executor.skill_result.get_user_context_block()
    if uc_block:
        planning_prompt = f"{uc_block}\n\n{planning_prompt}"
```

Injected into the **strategic planning prompt** (meta-plan generation). The `planning_prompt` is a local variable — never saved. The skill blocks sit inside the planner's declared stable prefix, so they are cached with it ([PROMPT_CACHE_ARCHITECTURE.md](PROMPT_CACHE_ARCHITECTURE.md)). The Optimize engine's **plan gate** (`engines/optimize_engine.py`, *Plan Gate: Evaluating Planning Necessity*) injects the same two blocks into its own prompt, so a turn the gate answers without a plan still follows the skill.

### LLM-Only Profile (Conversation Focused)

**File:** `agent/engines/ideate_engine.py` → `IdeateEngine.run()`

```python
# System prompt injection
if executor.skill_result and executor.skill_result.has_content:
    sp_block = executor.skill_result.get_system_prompt_block()
    if sp_block:
        system_prompt = f"{system_prompt}\n\n{sp_block}"

# User context injection (after building user_message)
if executor.skill_result and executor.skill_result.has_content:
    uc_block = executor.skill_result.get_user_context_block()
    if uc_block:
        user_message = f"{uc_block}\n\n{user_message}"
```

Both `system_prompt` and `user_message` are local variables.

**Ideate + tools** (`llm_only` with MCP or component tools): `agent/engines/conversation_engine.py` → `ConversationEngine.run()` combines both blocks into one `skill_context` handed to the ReAct agent's system prompt.

### RAG-Focused Profile (Knowledge Focused)

**File:** `agent/engines/focus_engine.py` → `FocusEngine.run()`

Same pattern as LLM-Only — skill content appended to local `system_prompt` and prepended to local `user_message` used for RAG synthesis.

### Genie Coordinator Profile

**File:** `agent/genie_coordinator.py` → `_build_agent()` (system prompt) and `execute()` (user context)

```python
# Inject skill content into coordinator system prompt
if self.skill_result and self.skill_result.has_content:
    skill_block = self.skill_result.get_system_prompt_block()
    if skill_block:
        system_prompt += f"\n\n{skill_block}"

# Inject user_context block prepended to the current query
uc_block = self.skill_result.get_user_context_block() if self.skill_result else ""
human_content = f"{uc_block}\n\n{query}" if uc_block else query
messages.append(HumanMessage(content=human_content))
```

Skills with `injection_target: "system_prompt"` augment the coordinator's routing instructions (e.g., domain-specific delegation rules). Skills with `injection_target: "user_context"` are prepended to the query sent to the coordinator's LangChain agent. Both injections use **local variables** — not persisted.

**Typical use case:** A `teradata-coordinator` skill (`skills/seed/teradata-coordinator/`) injected into a Genie coordinator system prompt provides Teradata-specific routing rules (`TDSQL` → SQL questions, `TDEXO` → live execution, etc.).

### Summary Table

| Profile | Injection Variable | Where | Scope |
|---------|-------------------|-------|-------|
| `tool_enabled` | `planning_prompt` (local) | `planner._generate_meta_plan` (+ the plan gate) | Single `plan_strategy()` call |
| `llm_only` | `system_prompt` + `user_message` (local) | `engines/ideate_engine.py` | Single conversation LLM call |
| `llm_only` + tools | `skill_context` (local) | `engines/conversation_engine.py` | Per ReAct agent run |
| `rag_focused` | `system_prompt` + `user_message` (local) | `engines/focus_engine.py` | Single RAG synthesis call |
| `genie` | `system_prompt` + `human_content` (local) | `genie_coordinator._build_agent` / `execute` | Per-coordinator LangChain agent invocation |

---

## 5a. Companion Skills & Auto-Assign

A **companion skill** is the layer-3 quality partner of a builtin component (see *Design
Philosophy §0* in [COMPONENT_ARCHITECTURE.md](COMPONENT_ARCHITECTURE.md)). Every action
component has one — it carries the "how to think" knowledge that does NOT belong in the
component's `instructions.json` (the correctness baseline).

### Registry link (`component_id`)

A companion skill declares its component in `skills/skill_registry.json` via a
`component_id` field. The name convention is `<component>-expert`, but the link is explicit
because at least one pairing breaks the convention:

| Component | Companion skill |
|---|---|
| `chart` | `chart-expert` |
| `canvas` | `canvas-expert` |
| `dashboard` | `dashboard-expert` |
| `flow` | `flow-designer` ← name exception |
| `knowledge_graph` | `knowledge-graph-expert` |
| `memory` | `memory-expert` |
| `scheduler` | `scheduler-expert` |
| `database_navigator` | `database-navigator-expert` |
| `explain_plan` | `explain-plan-expert` |
| `compute` | `compute-expert` |
| `action` (OBDA-ACT) | `obda-act-expert` ← name follows the plane, not the id |
| `obda_read` | `obda-read-expert` |
| `obda_model` | `obda-model-expert` |
| `files` | `files-expert` ← follows the TOOL (opt-in), see below |

Fourteen companions for sixteen action components. **Recorded exceptions** (no companion, by
design): `skill_library` (`TDA_SkillLibrary` — its quality layer is per-library, in each progressive
skill's own root, §4a) and `find_tools` (`TDA_FindTools` — offered only on a governed tool surface;
its description is the whole contract). The system component `context_window` has no tool and no
companion. The six non-companion builtin skills (`teradata-sql-expert`, `teradata-patterns`,
`table-format`, `concise`, `detailed`, `step-by-step`) carry no `component_id`.

`SkillManager.companion_skill_map()` returns `{component_id: skill_id}` for the loaded
skills; `list_skills()` exposes `component_id` per skill for the profile editor.

### Auto-assign rule (component enablement drives the skill)

**The same mark on components (Nov 2026).** "Auto" means one thing on both lanes: *the platform
holds the switch*. A companion skill is Auto while it follows its component; a component is Auto
while the agent stores no `componentConfig[id].enabled` and follows the manifest's class default.
A hand on the switch owns it on both, and both offer *Reset to default* to hand it back (on a
skill: the row follows its component again and the save writes `auto:true`). See *Resource
Vocabulary* in CLAUDE.md and `test/test_component_auto_mark.mjs`.

**A companion follows the TOOL it teaches (PROP-010, Oct 2026).** Where a component's LLM tool
is narrower than its enablement — `files` is enabled for every agent (panel, Keep, attachments)
but offers `TDA_Files` only to an agent whose owner opted in (`componentConfig.files.agentTool`)
— the component handler declares `offers_tool(profile)`. Every action-affordance resolution
(`enablement.is_enabled(..., require_action=True)`, the REST `skillsApplied` view, the editor's
companion rows) reads it, so `files-expert` applies only with the opt-in. Every other component
inherits `offers_tool → True`: their behaviour is unchanged.

When a component is enabled for a profile, its companion skill auto-applies each turn — no
manual assignment. The user keeps full control via an opt in/out override. The authority is
a **pure, unit-tested function** `skills/manager.py:resolve_auto_skill_specs(...)`
(`test/test_companion_skills.py`), called from the skill-resolution seam in
`agent/execution_service.py`, so it holds for **every** profile (editor, REST, agent-pack):

| Companion entry in `skillsConfig` | Component enabled? | Result |
|---|---|---|
| absent | yes | **auto-applied** |
| absent | no | not applied |
| `auto:true` (auto-managed) | yes | applied (stored `active` ignored — follows the component) |
| `auto:true` (auto-managed) | no | not applied |
| user-owned (`auto` falsey), `active:true` | either | applied (opt-in) |
| user-owned (`auto` falsey), `active:false` | either | not applied (opt-out) |

The invariant the editor writes on save is **`auto = (skill_active === component_enabled)`**:
matching the component-driven default marks the entry auto-managed; deviating marks it a
user override. **Backward-compatible:** legacy entries without an `auto` key are treated as
user-owned, so existing profiles are unaffected.

Component enablement is resolved with `recommender._component_enabled_for_profile`, a thin
wrapper over THE one rule (`components/enablement.is_enabled(..., require_action=True)`: global/user
governance → explicit `componentConfig` → manifest default for the profile type → the handler's
relevance gate → its tool gate) — the same rule the runtime tool surface and the profile editor use,
so the skill only attaches when the component's tool is genuinely offered. A relevance-gated
component (`compute`/`explain_plan` with `COMPUTE_LINEAGE` off, `skill_library` without a
progressive skill, the OBDA planes without a bound graph) therefore never pulls its companion in.

**Default-enablement consequence (watch out for this with imported/pack profiles):** the last
step of that resolution — `profile_type ∈ profile_defaults.enabled_for` — is **permissive**:
the six action components (chart/canvas/dashboard/flow/knowledge_graph/scheduler) and `memory` list
*all* IFOC classes (the Database Navigator and EXPLAIN plan list Optimize + Ideate; the three OBDA
planes list none), so a profile with **no `componentConfig`** has them enabled by default, which
auto-attaches their companion skills (~5.5K tokens/turn). `files` is default-on too, but its
companion follows the opt-in tool, so it costs nothing. An agent pack whose profiles omit
`componentConfig` therefore silently gains those skills on import. Control it by declaring
`componentConfig` explicitly — packs via the shared helper
`agent_packs/common/component_config.py:focused_component_config(memory=True, extra_enabled=[...])`
(memory on, action components off), profiles via the editor (which writes a full componentConfig
for every component toggle). The resource-panel skills view
(`GET /v1/profiles/<id>/skills`) runs this **same** resolver — there is no
"no skillsConfig → enable all skills" fallback — so the panel reflects exactly what runs.

### The three words: available · enabled · active (auto is a mark)

Every surface that reports a skill's state uses the platform's resource vocabulary
(`static/js/handlers/resourceBadge.js`, shared by every resource-panel badge):

| Word | The question it answers | For a skill |
|---|---|---|
| **available** | Is it on the list for this agent? (the denominator) | assigned to the profile (explicit `skillsConfig` entry or auto-companion) |
| **enabled** | Is it switched on for this agent — usable *when asked*? | `enabled` and not disabled by the admin; usable with `#name` on a turn |
| **active** | Is it in effect on *every turn* without being asked? | injected each turn (`active`); always a subset of enabled |

**Auto is not a fourth state** — it says *who holds the switch*: the platform, because the skill
follows its component (`auto:true`). It is shown as a small `auto` mark beside the state on the
Skills card and as the **"Auto"** badge in the editor, and it is never counted. The resource-panel
Skills badge is the one lane where active differs from enabled, so it reads three figures —
`Skills (active/enabled/available)`, e.g. `12/18/19` — while every other tab reads
`enabled/available` (an enabled tool's definition reaches the model on every turn). The one
tab that is not a list of switches, the Workspace, reads `items · sections` (`23 · 9`: the sum
of what its sections hold, then the section count) with a middle dot, so a slash pair always
means enabled/available. The Skills
card states are **ACTIVE** · **ENABLED** · **OFF**; it used to print `AUTO` for any always-on
skill, user-set ones included, which conflated the mark with the state. Gate:
`test/test_resource_badges.mjs`.

### Editor reflection (opt in/out UX)

In the profile modal's Skills tab (`static/js/handlers/configurationHandler.js`), a companion
skill of an enabled component shows as auto-active with an **"Auto"** badge (tooltip names
the component). Toggling it makes the row *user-owned* (the badge drops, **Reset to default**
appears, the save persists `auto:false`); changing a component toggle live-re-syncs the
auto-managed skill rows. **Reset to default** (Nov 2026, the components' control mirrored)
hands the switch back: the row follows its component again — Active = the component's
enablement — the badge returns, and the save writes `auto:true` (the entry stays, as a
companion entry always does; `auto:true` IS the Auto state on this lane). The opt-out is
persisted as an explicit `auto:false` suppression entry so the backend honours it (the legacy
"write only if enabled/active" filter would have dropped it, and the skill would
re-auto-apply).

> Auto-assignment is intentionally a **manual-process-friendly default**, not a hard lock:
> the platform sets the sensible default from component state, and the user opts in/out.

---

## 6. Skill Lifecycle & Transience

### Why Skill Content Never Enters `chat_object`

The user message is saved to `chat_object` in `run_agent_execution()` (`session_manager.add_message_to_histories`) — **before** the *SKILL RESOLUTION* block runs. The saved message contains only the original `user_input`, not the skill-augmented content.

### Timeline: Turn with Skill → Turn without Skill

```
Turn 1 (skill #concise activated):
  execution_service         → save original "What is X?" to chat_object
  execution_service         → resolve skills → SkillResult with markdown
  ideate_engine.run         → system_prompt += skill markdown (LOCAL var)
  ideate_engine.run         → user_message = skill_context + user_input (LOCAL var)
  LLM call                  → receives augmented prompt
  LLM response saved        → response text to chat_object
  SkillResult               → discarded (request-scoped)

Turn 2 (skill deactivated):
  Load chat_object from disk → contains:
    [0] user: "What is X?"           ← NO skill content
    [1] assistant: "X is..."          ← plain response text
  execution_service         → no skill_specs → skill_result = None
  ideate_engine.run         → no injection
  LLM call                  → receives history WITHOUT any skill content
```

### What IS Persisted

Only **metadata references** are stored — never the actual markdown content:

| Storage | What's Stored | Content? |
|---------|--------------|----------|
| `session_history` message | `skill_specs: [{name, param}]` | No |
| Turn data (`skills_applied` on the turn's `workflow_history` entry, written by `PlanExecutor`) | `skills_applied: [{name, param, injection_target, content_length, estimated_tokens}]` | No |

### Deactivation = Complete Elimination

- No cleanup needed — there's nothing to clean up
- Skill content never enters any persistent store
- The LLM's response text IS stored (it may reflect the skill's influence), but this is the LLM's own output

---

## 7. Database Schema

### `user_skills` Table

Per-user skill activations with custom naming.

```sql
CREATE TABLE IF NOT EXISTS user_skills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid       VARCHAR(36) NOT NULL,
    skill_id        VARCHAR(100) NOT NULL,          -- Base skill (e.g., "sql-expert")
    activation_name VARCHAR(100) NOT NULL,          -- User-facing name (e.g., "sql-expert2")
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    default_param   VARCHAR(255),                   -- Default parameter value
    config_json     TEXT,                           -- JSON configuration
    activated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_uuid) REFERENCES users(id),
    UNIQUE(user_uuid, activation_name)
);

CREATE INDEX idx_user_skills_user ON user_skills(user_uuid, is_active);
CREATE INDEX idx_user_skills_name ON user_skills(user_uuid, activation_name);
```

**Activation name auto-generation** (`db.py`): First activation uses `skill_id` directly. Subsequent activations of the same skill append incrementing numbers: `sql-expert`, `sql-expert2`, `sql-expert3`.

### `skill_settings` Table

Admin governance controls.

```sql
CREATE TABLE IF NOT EXISTS skill_settings (
    id            INTEGER PRIMARY KEY,
    setting_key   TEXT NOT NULL UNIQUE,
    setting_value TEXT NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Defaults
INSERT OR IGNORE INTO skill_settings (setting_key, setting_value) VALUES
    ('skills_mode', 'all'),             -- 'all' | 'selective'
    ('disabled_skills', '[]'),          -- JSON array of skill_ids
    ('user_skills_enabled', 'true'),              -- Can users create skills?
    ('auto_skills_enabled', 'false'),              -- Phase 2: automatic skill selection
    ('user_skills_marketplace_enabled', 'true');   -- Enable skill marketplace
```

### Marketplace Tables

```sql
-- schema/17_marketplace_skills.sql

CREATE TABLE IF NOT EXISTS marketplace_skills (
    id                VARCHAR(36)  PRIMARY KEY,
    skill_id          VARCHAR(100) NOT NULL,
    name              VARCHAR(255) NOT NULL,
    description       TEXT,
    version           VARCHAR(50),
    author            VARCHAR(255),
    injection_target  VARCHAR(20)  DEFAULT 'system_prompt',
    has_params        BOOLEAN      DEFAULT 0,
    tags_json         TEXT,                    -- JSON array of tags
    publisher_user_id VARCHAR(36)  NOT NULL,
    visibility        VARCHAR(20)  DEFAULT 'public',  -- 'public' | 'targeted'
    manifest_json     TEXT,                    -- Full skill.json for preview
    content_hash      VARCHAR(64),             -- SHA256 of .md content
    download_count    INTEGER      DEFAULT 0,
    install_count     INTEGER      DEFAULT 0,
    published_at      DATETIME     NOT NULL,
    updated_at        DATETIME     NOT NULL,
    FOREIGN KEY (publisher_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS skill_ratings (
    id                        VARCHAR(36) PRIMARY KEY,
    skill_marketplace_id      VARCHAR(36) NOT NULL,
    user_id                   VARCHAR(36) NOT NULL,
    rating                    INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment                   TEXT,
    created_at                DATETIME NOT NULL,
    updated_at                DATETIME NOT NULL,
    FOREIGN KEY (skill_marketplace_id) REFERENCES marketplace_skills(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_publisher    ON marketplace_skills(publisher_user_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_visibility   ON marketplace_skills(visibility);
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_published_at ON marketplace_skills(published_at);
CREATE INDEX IF NOT EXISTS idx_skill_ratings_skill            ON skill_ratings(skill_marketplace_id);
CREATE INDEX IF NOT EXISTS idx_skill_ratings_user             ON skill_ratings(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_ratings_unique    ON skill_ratings(skill_marketplace_id, user_id);
```

Targeted visibility reuses the existing `marketplace_sharing_grants` table (from `schema/11_marketplace_sharing.sql`) with `resource_type = 'skill'`.

### Schema Files

| File | Table |
|------|-------|
| `schema/15_skills.sql` | `user_skills` |
| `schema/16_skill_settings.sql` | `skill_settings` |
| `schema/17_marketplace_skills.sql` | `marketplace_skills`, `skill_ratings` |

---

## 8. REST API Endpoints

### Skill Discovery & Content

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/skills` | GET | List all available skills (filtered by governance) |
| `/v1/skills/reload` | POST | Hot-reload skills from disk |
| `/v1/skills/{skill_id}/content` | GET | Full content + manifest (for editor) |
| `/v1/skills/{skill_id}` | PUT | Create or update user skill |
| `/v1/skills/{skill_id}` | DELETE | Delete user-created skill |
| `/v1/skills/{skill_id}/versions` | GET | Archived versions of a skill (for the profile's version pin) |
| `/v1/skills/{skill_id}/library-index` | GET | A progressive skill's library index (§4a) |
| `/v1/profiles/{profile_id}/skills` | GET | The resource-panel view — runs the same `resolve_auto_skill_specs` as the runtime |
| `/v1/skills/{skill_id}/export` | POST | Export skill as `.skill` (ZIP with `skill.json` + `<name>.md` + `SKILL.md`) |
| `/v1/skills/import` | POST | Import skill from `.skill`/`.zip` — accepts both Uderia (`skill.json`) and Claude Code (`SKILL.md` frontmatter) formats |

### User Activations

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/skills/activated` | GET | User's active skills (for `#` autocomplete) |
| `/v1/skills/{skill_id}/activate` | POST | Activate skill with optional custom name |
| `/v1/skills/activations/{name}/deactivate` | POST | Soft-deactivate (set `is_active=0`) |
| `/v1/skills/activations/{name}/config` | PUT | Update default param |
| `/v1/skills/activations/{name}/rename` | PUT | Rename activation |
| `/v1/skills/activations/{name}` | DELETE | Hard-delete activation record |

### Admin Governance

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/admin/skill-settings` | GET | Get governance settings + builtin skill list |
| `/v1/admin/skill-settings` | POST | Save governance (disabled_skills, user_skills_enabled, user_skills_marketplace_enabled) |

### Marketplace

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/skills/{skill_id}/publish` | POST | Publish user skill to marketplace |
| `/v1/marketplace/skills` | GET | Browse marketplace (search, sort, paginate) |
| `/v1/marketplace/skills/{id}` | GET | Get marketplace skill detail + ratings |
| `/v1/marketplace/skills/{id}/install` | POST | Install skill from marketplace |
| `/v1/marketplace/skills/{id}/rate` | POST | Rate marketplace skill (1-5 stars + comment) |
| `/v1/marketplace/skills/{id}` | DELETE | Unpublish from marketplace (publisher only) |
| `/v1/marketplace/skills/{id}/targeted-users` | GET / PUT | Read / set the users a targeted listing is shared with |

Skill trust sign-off (`POST /v1/trust/skill/{skill_id}/sign-off`, `api/trust_routes.py`) is part of the trust membrane — see [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md).

All skill endpoints except the admin ones live in `api/skills_routes.py`.

**Marketplace publish status in `GET /v1/skills`:**

The skill list endpoint annotates each skill with the current user's marketplace publish status:

```json
{
  "skills": [
    {
      "skill_id": "teradata-coordinator",
      "is_marketplace_listed": true,
      "marketplace_id": "abc123..."
    }
  ]
}
```

`is_marketplace_listed: false` and `marketplace_id: null` when the skill has not been published. This allows the Skills UI to show publish/unpublish buttons without a separate round-trip.

**Browse marketplace (`GET /v1/marketplace/skills`) excludes the caller's own published skills** — publishers see only skills published by others. This prevents confusing self-visibility in the marketplace browser.

### Query Execution with Skills

**SSE Route** (`routes.py`):
```
POST /ask_stream
Body: { message, session_id, skills: [{name, param}], ... }
```

**REST Route** (`rest_routes.py`):
```
POST /v1/sessions/{session_id}/query
Body: { prompt, skills: [{name, param}], ... }
```

Both routes extract `skill_specs` and pass to `execution_service.run_agent_execution()`.

### Execution Service Flow (`execution_service.run_agent_execution` → *SKILL RESOLUTION*)

```python
# 0. Auto-skills: explicit profile actives + component companions (§5a)
for spec in resolve_auto_skill_specs(profile_skills, enabled_component_ids,
                                     companion_map, is_skill_available):
    resolved_specs.append(spec)

# 1. Manual #skills: must be enabled on the profile, then filtered by governance
for spec in skill_specs:
    if not is_skill_available(spec["name"]):
        continue  # Skip disabled skills
    resolved_specs.append(SkillSpec(name=spec["name"], param=spec.get("param")))

# 2. Resolve content
skill_result = skill_manager.resolve_skills(resolved_specs, profile_config=active_profile)  # resolves [[gate:…]] blocks

# 3. Emit transparency event
if skill_result.has_content:
    await event_handler({
        "type": "skills_applied",
        "payload": {
            "skills": skill_result.to_applied_list(),
            "total_estimated_tokens": skill_result.total_estimated_tokens,
        }
    }, "notification")

# 4. Pass to executor
executor = PlanExecutor(..., skill_result=skill_result)
```

---

## 9. Frontend Architecture

### State Management

```javascript
// main.js — Per-query skill selection
let activeSkills = [];        // [{name, param}] — cleared after each submission
window.activeSkills = activeSkills;

// main.js — Available skills from server
window.skillState = {
    activated: [              // From GET /v1/skills/activated
        {
            skill_id: "sql-expert",
            activation_name: "sql-expert",
            allowed_params: ["strict", "lenient"],
            param_descriptions: { strict: "...", lenient: "..." },
            ...
        }
    ]
};
```

### User Interaction Flow

```
1. PAGE LOAD
   └─ loadActivatedSkills() → GET /v1/skills/activated
      └─ window.skillState.activated = [skills...]

2. USER TYPES "#"
   └─ Input handler matches /#(\w*)$/
      └─ Filter activated skills by prefix
         └─ showSkillSelector(filtered) → emerald dropdown

3. USER SELECTS SKILL (Tab/Enter/Click)
   └─ selectSkill(index)
      └─ activeSkills.push({name, param: null})
         └─ _renderSkillBadges() → emerald badge in input area

4. USER TYPES ":param" (optional)
   └─ Input handler matches /^:(\S*)$/
      └─ activeSkills[last].param = typedParam
         └─ _renderSkillBadges() → badge updates to #name:param
         └─ showParamPicker() if allowed_params exist

5. USER SUBMITS QUERY
   └─ submitQuestion()
      └─ addMessage('user', text, ..., skillSpecs) → skill tags in chat
         └─ POST /ask_stream { skills: [...activeSkills] }
            └─ activeSkills = [] → cleared for next query

6. SSE EVENT: skills_applied
   └─ processStream() → UI.updateStatusWindow(..., 'skills')
      └─ _renderSkillStep() → emerald section in Live Status
```

### Event Handling

**SSE Streaming** (`eventHandlers.js` → `processStream()`):
```javascript
if (eventData.type === 'skills_applied') {
    UI.updateStatusWindow({
        step: 'Skills applied',
        details: eventData.payload,
        type: 'skills_applied'
    }, false, 'skills');
}
```

**REST Notifications** (`notifications.js`):
```javascript
if (eventType === 'skills_applied') {
    UI.updateStatusWindow({
        step: 'Skills applied',
        details: event.payload,
        type: 'skills_applied'
    }, false, 'skills');
    return;
}
```

**Historical Turn Reload** (`eventHandlers.js` → `_renderSkillEventsForReload()`):
```javascript
function _renderSkillEventsForReload(turnData, container) {
    const skills = turnData.skills_applied || [];
    if (skills.length === 0) return;
    // Render emerald divider + skill names
}
```

### Chat Message Rendering (`ui.js` → `addMessage()`)

```javascript
// In addMessage() — skill tags for user messages
if (role === 'user' && skillSpecs && skillSpecs.length > 0) {
    skillSpecs.forEach(spec => {
        const skillTag = document.createElement('span');
        skillTag.className = 'skill-tag';
        skillTag.textContent = `#${spec.name}${spec.param ? ':' + spec.param : ''}`;
        metaContainer.appendChild(skillTag);
    });
}
```

### Session Load Path (`sessionManagement.js` → `handleLoadSession()`)

When loading a session from the server, skill tags are restored from stored metadata:

```javascript
UI.addMessage('user', msg.content, ..., msg.skill_specs || null);
```

### Skill Configuration Tab (`skillHandler.js`)

The Skills tab in Anatomy provides:

- **Skill Grid**: Cards showing all available skills with activation toggle
- **Filter Bar**: By tag, source (built-in/user), status (active/inactive), sort order
- **Skill Editor**: Three-level progressive disclosure (Citizen/Intermediate/Expert)
- **Import/Export**: `.zip` format for portability
- **Hot Reload**: Refresh skills from disk without restart

#### Editor Levels

| Level | Fields |
|-------|--------|
| **Citizen** | Name, description, instructions (markdown), tags, token estimate |
| **Intermediate** | + Injection target, parameter editor, param descriptions |
| **Expert** | Raw markdown content, raw manifest.json |

---

## 10. Built-in Skills

Built-in skills ship with the platform in `skills/builtin/` and fall into two groups:

- **Component companion skills** (layer-3 quality partners, auto-assigned with their
  component — see [§5a](#5a-companion-skills--auto-assign)): `canvas-expert`, `chart-expert`,
  `compute-expert`, `dashboard-expert`, `database-navigator-expert`, `explain-plan-expert`,
  `files-expert`, `flow-designer`, `knowledge-graph-expert`, `memory-expert`, `obda-act-expert`,
  `obda-model-expert`, `obda-read-expert`, `scheduler-expert` (14).
- **Style / formatting / database skills** (manually assignable, 6): `teradata-sql-expert`,
  `teradata-patterns` (a progressive library, §4a), `table-format`, `concise`, `detailed`,
  `step-by-step`.

The general-purpose skills are detailed below; the companion skills are documented with the
component-skill layering in §5a and §0 of [COMPONENT_ARCHITECTURE.md](COMPONENT_ARCHITECTURE.md).

### teradata-sql-expert

| Field | Value |
|-------|-------|
| **Purpose** | Teradata-specific SQL conversion rules and best practices for correct, idiomatic Teradata SQL |
| **Injection** | `user_context` |
| **Params** | None |
| **Tags** | teradata, sql, database, best-practices |
| **Use Cases** | Writing Teradata queries (dialect conversion, reserved words, idioms) |

(The former generic `sql-expert` with `strict`/`lenient` params no longer ships; the param-block
examples elsewhere in this doc use it only as an illustration of the syntax.)

### teradata-patterns

| Field | Value |
|-------|-------|
| **Purpose** | A library of Teradata SQL patterns, read one section at a time; every statement was executed on a real system |
| **Injection** | `user_context` (root) + on-demand library via `TDA_SkillLibrary` |
| **Params** | None |
| **Tags** | teradata, sql, patterns, performance, data-quality |
| **Use Cases** | Analytics patterns (attribution, churn, …) — see §4a |

### table-format

| Field | Value |
|-------|-------|
| **Purpose** | Format all data as clean Markdown tables |
| **Injection** | `system_prompt` |
| **Params** | None |
| **Tags** | formatting, table, markdown |
| **Use Cases** | Query results as tables, side-by-side comparisons |

### concise

| Field | Value |
|-------|-------|
| **Purpose** | Brief, focused responses without filler |
| **Injection** | `system_prompt` |
| **Params** | None |
| **Tags** | style, brevity, concise |
| **Use Cases** | Quick answers, batch processing, dashboard summaries |

### detailed

| Field | Value |
|-------|-------|
| **Purpose** | Thorough analysis with reasoning and alternatives |
| **Injection** | `system_prompt` |
| **Params** | None |
| **Tags** | style, thorough, analysis |
| **Use Cases** | Deep analysis, decision support, complex relationships |

### step-by-step

| Field | Value |
|-------|-------|
| **Purpose** | Chain-of-thought reasoning with numbered steps |
| **Injection** | `system_prompt` |
| **Params** | None |
| **Tags** | reasoning, chain-of-thought, methodology |
| **Use Cases** | Understanding query logic, debugging data issues |

---

## 11. Admin Governance

### Settings (`settings.py`)

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `skills_mode` | `"all"` \| `"selective"` | `"all"` | Filter mode |
| `disabled_skills` | `List[str]` | `[]` | Skills to block (in selective mode) |
| `user_skills_enabled` | `bool` | `true` | Can users create custom skills? |
| `auto_skills_enabled` | `bool` | `false` | Phase 2: automatic skill selection |
| `user_skills_marketplace_enabled` | `bool` | `true` | Enable the skill marketplace |

### Governance Predicates

```python
is_skill_available(skill_id)    # True unless mode="selective" AND in disabled list
are_user_skills_enabled()       # Whether custom creation is allowed
are_auto_skills_enabled()       # Phase 2 feature flag
is_skill_marketplace_enabled()  # Whether the skill marketplace is accessible
```

Unlike components, skills have no per-user admin override: `disabled_skills` applies only in
`selective` mode, and the gate is global.

### Enforcement Points

1. **API Level**: `GET /v1/skills` and `GET /v1/skills/activated` filter by governance
2. **Execution Level**: `execution_service.run_agent_execution` checks `is_skill_available()` for both auto and manual skills before resolving
3. **Frontend Level**: Disabled skills hidden from `#` autocomplete (filtered server-side)

---

## 12. CSS & Visual Design

### Color Scheme: Emerald Green

Skills use emerald green throughout the UI, distinct from extensions (amber/yellow).

| Token | Value | Usage |
|-------|-------|-------|
| Primary | `#10b981` | Borders, accents |
| Display | `#34d399` | Text, badge labels |
| Light | `#6ee7b7` | Highlights |
| BG 20% | `rgba(16, 185, 129, 0.2)` | Badge backgrounds |
| BG 15% | `rgba(16, 185, 129, 0.15)` | Dropdown items |
| Border | `rgba(16, 185, 129, 0.3)` | Borders |
| Invalid | `rgba(239, 68, 68, 0.6)` | Invalid param state |

### Key CSS Classes

| Class | Location | Purpose |
|-------|----------|---------|
| `.skill-tag` | Chat messages | Emerald badge on user messages |
| `.active-skill-badge` | Input area | Selected skill badge |
| `.active-skill-badge--invalid` | Input area | Invalid parameter indicator |
| `.active-skill-badge__remove` | Input area | Remove button (x) |
| `#skill-selector` | Dropdown | `#` autocomplete container |
| `.skill-item` | Dropdown | Individual skill entry |
| `.skill-item.skill-highlighted` | Dropdown | Keyboard-selected item |
| `.skill-item-badge` | Dropdown | `#name` badge in item |
| `.skill-item-name` | Dropdown | Skill display name |
| `.skill-item-description` | Dropdown | Skill description text |
| `.skill-step` | Live Status | Skill event in status window |

### Visual Comparison

```
Skills (emerald):     Extensions (amber):
┌─────────────────┐   ┌─────────────────┐
│ #sql-expert     │   │ @json-export    │
│ #34d399 text    │   │ #fbbf24 text    │
│ emerald border  │   │ amber border    │
└─────────────────┘   └─────────────────┘
```

---

## 14. Agent Pack Integration (v1.3)

Agent packs (`.agentpack` files) bundle skills alongside profiles and collections. This enables one-click deployment of complete agent configurations, including the skills that power them.

### Export: Bundling Auto-Enabled Skills

During `POST /v1/agent-packs/export`, the manager scans every exported profile's `skillsConfig.skills[]` array. Any skill with `active: true` that is a **user skill** (`_is_user: true` in its manifest) is bundled into the pack:

```python
# agent_pack_manager.py — collect skills to bundle
for profile in selected_profiles:
    for skill_entry in profile.get("skillsConfig", {}).get("skills", []):
        if not skill_entry.get("active", False):
            continue
        s_manifest = skill_manager.get_skill_manifest(skill_id)
        if s_manifest and s_manifest.get("_is_user", False):
            skills_to_bundle[skill_id] = {"manifest": s_manifest, "content": ...}
```

Each skill is packaged as a `.skill` zip file (`skills/<skill_id>.skill`) containing:
- `skill.json` — manifest (cleaned of export metadata)
- `<skill_id>.md` — raw markdown content
- `SKILL.md` — Claude Code compatible format with YAML frontmatter

The manifest `skills` array lists all bundled skills:
```json
{
  "format_version": "1.3",
  "skills": [
    {
      "id": "teradata-coordinator",
      "file": "skills/teradata-coordinator.skill",
      "name": "Teradata Coordinator",
      "description": "Routing rules for the Teradata expert coordinator"
    }
  ]
}
```

**Manifest version logic:** `1.3` if any skills present, `1.2` if only VS configs, `1.1` otherwise.

### Import: Installing Bundled Skills

During `POST /v1/agent-packs/import`, skills are installed **before** profiles are created so that `skillsConfig` assignments are immediately resolvable:

1. Extract `manifest.skills[]` entries
2. For each entry, read the `.skill` zip from the `skills/` subdirectory
3. Call `_install_skill_from_zip_bytes()` → `skill_manager.save_skill()`
4. `skill_manager.reload()` so profiles can reference newly installed skills
5. Profiles created with `skillsConfig` intact (auto-enabled assignments preserved)

### `skills/seed/` Repository Directory

The project ships a `skills/seed/` directory (renamed from `skills/user/`,
Aug 2026 — "user" collided with the "User" badge and with hand-created skills,
which live in `tda_skills/`) containing repo-distributed skills:

```
skills/seed/
└── teradata-coordinator/    # Routing rules for TDEXP genie coordinator
    ├── skill.json
    ├── SKILL.md
    └── teradata-coordinator.md
```

These are git-versioned **seed skills**: they load on every checkout with zero
install steps and are read-only at runtime — the delete endpoint refuses them,
and no install ever writes here. Runtime-installed skills live in `tda_skills/`.

**A seed no longer shadows a builtin (Sep 2026).** `skills/seed/` also carried
`teradata-sql-expert`, documented as deliberately overriding the builtin of the
same id. That override was not free: **anything found in a read dir is stamped
`_is_user`, whether or not a builtin exists** (`_discover_and_load`), so the
platform's own skill rendered with the User badge — and the pack EXPORT gate is
exactly `_is_user`, so every pack whose profiles carried it auto-bundled it, and
each import wrote a state copy that re-armed the loop. Thirteen such installs
were recorded on one instance. The seed and state copies are gone; the builtin is
the only copy, and a pack install that would shadow a builtin id is now refused
(`BuiltinSkillShadowRefused`). A seed *may* still override an id the platform
does not ship — that is the mechanism's real use.

**And a shadow already on disk is IGNORED (Sep 2026) — the read side of the same
rule.** The guard above closes the *inflow* only. It does nothing about the copies
a deployment had **already written** before it existed, and `_discover_and_load`
took a read-dir copy over `skills/builtin/` **unconditionally**: `is_builtin_skill()`
was called at exactly ONE site — the install guard — and never by the loader, which
decides what is actually *served*. So a state copy kept demoting the platform skill
to "User" (carrying an uninstalled pack's orphan stamp with it) for the life of that
instance, and **no migration would ever have cleared it**.

ONE rule, TWO enforcement points: **refuse the write, refuse the read.** In Mode 1
the loader now asks `builtin_dir_for(skill_id)` first; a read-dir copy of an id the
platform **ships** is skipped and the builtin served. The copy is **named, not
deleted** — a one-per-process warning carries its path, because removing an
operator's files as a startup side effect is not the loader's call. An instance
carrying a shadow therefore **self-heals on restart**, with no migration and nothing
removed behind the operator's back.

The seed carve-out is untouched by construction: `builtin_dir_for` returns `None`
for an id the platform does **not** ship, so `teradata-coordinator` — and any other
seed override of a non-shipped id — still wins, which is the mechanism's real use.
The duplicate `builtin_dir / plugin_directory` derivation collapses into that one
resolver, so the guard and the loader can never disagree about what "is a builtin".

> **Invariant:** every directory under `skills/builtin/` is registry-listed, so Mode 1
> (registry-driven) is the only path on which a builtin can be shadowed —
> auto-discovery skips ids already loaded and cannot reach one.

### Provenance & Versions (Aug 2026)

Runtime-installed skills carry `uderia.pack_origin` (current pointer) plus the
append-only `uderia.pack_history` event log; a pack install by another user
with different content is refused (`SkillOwnershipConflict` → import warning).
Content-differing overwrites archive the replaced version to
`tda_skills/.skill_versions/`; a profile may pin a version via
`skillsConfig.skills[].content_hash` (picker in the profile editor's skill
row), and `TDA_SkillLibrary` serves the pinned version's library so root and
library never diverge. Full contract: `docs/RestAPI/restAPI.md` §3.21.3b;
gates: `test_skill_provenance_versions.py`, `test_skill_version_pin_ui.mjs`.

---

## 13. File Reference

### Backend

| File | Purpose |
|------|---------|
| `src/trusted_data_agent/skills/models.py` | `SkillSpec`, `SkillContent`, `SkillResult` data classes |
| `src/trusted_data_agent/skills/loader.py` | Content loading, param block resolution |
| `src/trusted_data_agent/skills/manager.py` | Discovery pipeline, singleton `SkillManager` |
| `src/trusted_data_agent/skills/db.py` | Per-user activation CRUD (`user_skills` table) |
| `src/trusted_data_agent/skills/settings.py` | Admin governance (`skill_settings` table) |
| `src/trusted_data_agent/skills/package.py` · `versions.py` | `.skill` zip format · version archive and pins |
| `src/trusted_data_agent/skills/library_use.py` · `library_availability.py` · `execution_record.py` | Progressive-library reads, offered sections, signed execution claims (§4a) |
| `components/builtin/skill_library/handler.py` | `TDA_SkillLibrary` — the progressive reader (relevance-gated, headless) |
| `src/trusted_data_agent/agent/execution_service.py` | `run_agent_execution` — *SKILL RESOLUTION* block (auto + manual skills, `skills_applied` event); genie skill passthrough to coordinator |
| `src/trusted_data_agent/agent/engines/ideate_engine.py` · `conversation_engine.py` · `focus_engine.py` | Injection for `llm_only`, `llm_only` + tools, `rag_focused` |
| `src/trusted_data_agent/agent/planner.py` | `_generate_meta_plan` — injection for `tool_enabled` (+ `engines/optimize_engine.py` plan gate) |
| `src/trusted_data_agent/agent/genie_coordinator.py` | `_build_agent` / `execute` — genie coordinator system prompt + user context |
| `src/trusted_data_agent/core/agent_pack_manager.py` | v1.3 skill bundling (export) and `_install_skill_from_zip_bytes()` (import) |
| `src/trusted_data_agent/api/rest_routes.py` | `skill_specs` in REST query execution |
| `src/trusted_data_agent/api/routes.py` | SSE skill passthrough in `/ask_stream` |
| `src/trusted_data_agent/api/admin_routes.py` | Skill governance endpoints |
| `src/trusted_data_agent/api/skills_routes.py` | All `/v1/skills*`, `/v1/marketplace/skills*` and `/v1/profiles/<id>/skills` endpoints; publish-status annotation; browse excludes own skills |

### Frontend

| File | Purpose |
|------|---------|
| `static/js/handlers/skillHandler.js` | Skills tab, card grid, filter bar, editor dialog |
| `static/js/handlers/skillsPanelHandler.js` | Resource-panel Skills card (ACTIVE · ENABLED · OFF, `auto` mark) |
| `static/js/handlers/skillGrantDialog.js` | Library access — the anchor-grant dialog for progressive skills (PROP-005 §6) |
| `static/js/handlers/resourceBadge.js` | The available · enabled · active badge rule (`active/enabled/available` on Skills) |
| `static/js/main.js` | `#` trigger, badge system, `loadActivatedSkills()`, autocomplete |
| `static/js/ui.js` | `addMessage()` skill tags, `_renderSkillStep()`, status window routing |
| `static/js/eventHandlers.js` | `processStream()` skill events, `_renderSkillEventsForReload()` |
| `static/js/notifications.js` | `skills_applied` handler in `_dispatchRestEvent()` |
| `static/js/handlers/sessionManagement.js` | Session load with `skill_specs` passthrough |
| `static/css/main.css` | All `.skill-*` CSS classes |

### Built-in Skills

| Directory | Skill |
|-----------|-------|
| `skills/builtin/<component>-expert/`, `flow-designer/` | The 14 component companions (§5a) |
| `skills/builtin/teradata-sql-expert/` | Teradata SQL conversion rules and idioms |
| `skills/builtin/teradata-patterns/` | Progressive library of executed Teradata SQL patterns |
| `skills/builtin/table-format/` | Markdown table formatting |
| `skills/builtin/concise/` | Brief, focused responses |
| `skills/builtin/detailed/` | Thorough analysis |
| `skills/builtin/step-by-step/` | Chain-of-thought reasoning |

### Database

| File | Table |
|------|-------|
| `schema/15_skills.sql` | `user_skills` |
| `schema/16_skill_settings.sql` | `skill_settings` |
| `schema/17_marketplace_skills.sql` | `marketplace_skills`, `skill_ratings` |
