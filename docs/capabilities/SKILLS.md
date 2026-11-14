# 📘 Skills: Pre-Processing Context Injection

Skills are reusable markdown instruction sets that shape how the agent reasons, responds, and formats — injected into LLM context **before** query execution. They provide transparent, auditable control over agent behavior without modifying system prompts permanently.

**How it works:** Type `#` in the chat input to trigger autocomplete, select a skill, and optionally add a parameter with `:param` syntax on skills that declare one (none of the built-in skills does; see Parameterizable below). Skills appear as emerald-green badges in the input area and on chat messages.

**Built-in skills:**

| Skill | Purpose | Parameters |
|-------|---------|------------|
| `#teradata-sql-expert` | Teradata SQL dialect rules (TOP not LIMIT, QUALIFY, boolean literals …) | — |
| `#teradata-patterns` | Progressive library of Teradata SQL patterns, each run on a real Teradata | sections by grant |
| `#table-format` | Format all data responses as clean Markdown tables | — |
| `#concise` | Brief, focused responses without preamble or filler | — |
| `#detailed` | Thorough analysis with reasoning, context, and alternatives | — |
| `#step-by-step` | Chain-of-thought reasoning with numbered steps | — |

Plus a companion skill for each action component except Tool Finder and Skill Library (chart-expert, canvas-expert, compute-expert, dashboard-expert, flow-designer, …), auto-assigned when its component is enabled, and the progressive `platform-playbooks` skill the Platform component reads.

**Progressive disclosure skills — when a skill is too big to inject:**

A skill that carries a whole body of knowledge (a methodology, a standards corpus, a
product manual) cannot be injected in full on every turn — most of it is irrelevant to the
question in front of the agent, and all of it is paid for. Such a skill ships as a lean
**root** plus a **library** of sections: the root is injected as usual and carries a map of
what is available; the agent opens a section mid-turn, by name, only when the task calls
for it.

Two properties make this governable rather than merely cheaper:

- **You decide what each agent may open.** *Library access* on an agent's skill (Anatomy →
  Agent Profiles → Skills, or Components → Skill Library → Libraries) grants named sections
  or whole branches. Anything outside the grant is **refused at the tool layer** — so one
  corpus can serve a designer and a builder while the designer never sees the
  implementation half. A refusal names what the agent *may* read instead, so it corrects
  itself in the same turn.
- **The library is inside the signature.** Editing any section changes the skill's content
  hash exactly as editing its body would, so a signed agent bound to it reports as changed.

The agent chooses which section to open — Uderia enforces the boundary, not the
selection. Authoring guide: [SKILL_ARCHITECTURE §4b](../Architecture/SKILL_ARCHITECTURE.md).

**Key characteristics:**
- **Fully transient** — skill content is injected per-request into local LLM context variables, never stored in conversation history. Deactivating a skill removes it from all future context
- **Works across all profile types** — Optimizer, Conversation, Knowledge, and Genie profiles all support skill injection at the appropriate execution point
- **Parameterizable** — skills support runtime parameters via `<!-- param:name -->` blocks for fine-grained behavior variants within a single skill
- **Create your own** — via the visual Skill Editor (three levels: Citizen, Intermediate, Expert) or drop a skill into `skills/seed/` for zero-friction authoring (runtime installs live in `tda_skills/`; the legacy `~/.tda/skills/` is still read)
- **Portable** — export/import as `.zip` for sharing across environments
- **Admin-governed** — administrators can disable specific skills, control user skill creation, and manage availability globally
- **Transparent** — `skills_applied` events in the Live Status Window show which skills were injected and their estimated token cost

Architecture details: [**Skill Architecture (docs/Architecture/SKILL_ARCHITECTURE.md)**](../Architecture/SKILL_ARCHITECTURE.md)
