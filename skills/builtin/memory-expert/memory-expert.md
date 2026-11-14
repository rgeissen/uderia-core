# Memory Expert

The platform handles all mechanics automatically: durable facts are **recalled into your context every turn** (you see them as a `[Remembered about this user]` block), storage is deterministic, and dedup/scope are handled by the store. Your only job is **judgement**: decide what is worth remembering, phrase it well, and never pollute memory.

---

## What memory is for

Agent memory is **about the user**, and it persists into *future sessions*. It is the answer to "what would I wish I still knew when this user comes back next week?"

Three kinds:
- **preference** — how the user wants to work ("answer concisely", "always show SQL", "metric units")
- **fact** — stable truths about the user or their work ("I'm a finance analyst", "my primary database is sales_prod", "I report to the EU region")
- **style** — tone/format leanings ("prefer bullet points", "no marketing language")

---

## The durability test (apply before every `remember`)

Only store something if **all three** hold:
1. **About the user**, not about the data or this question.
2. **Durable** — still true and useful weeks from now, across unrelated questions.
3. **Stated or clearly implied as lasting** — "I always…", "from now on…", "remember that…", or a direct profile fact.

If you're unsure, **don't store it.** A clean, small memory is far more valuable than a large noisy one. Under-remembering is cheap; over-remembering pollutes every future turn's context.

---

## Anti-patterns — never store these

| Don't store | Why |
|---|---|
| "Show me Q3 revenue" | the turn's specific question — transient |
| "Revenue was $4.2M" | answer data — belongs to that turn, not the user |
| "User asked about the customers table" | one-off navigation, not a durable fact |
| "User seems happy" | speculation, not a stated fact |
| Anything you inferred weakly | low confidence pollutes recall |

---

## Phrasing

Write each memory as a **short, first-person, self-contained** statement — it must make sense with zero surrounding context when recalled months later.

- Good: *"I prefer metric tons for all weight figures."*
- Good: *"I'm a supply-chain analyst focused on the EU region."*
- Weak: *"metric"* (no context), *"the user wants tons"* (third person, vague)

Keep each memory atomic — one clear, first-person fact per entry. The component infers `kind` for you, so you don't manage storage keys or versions; when a preference changes, just `remember` the new value (and `forget` the stale one if needed).

---

## Scope

- **agent** (default) — only THIS agent remembers it. Most memories belong here: they're learned in the context of this agent's role and shouldn't leak to unrelated agents.
- **global** — shared across *all* of the user's agents. Reserve for genuine user-level facts that every agent should know ("I'm a finance analyst", "answer concisely", "I prefer metric tons").

When in doubt, **agent** (the default) — keep memory local to the agent that learned it, and only promote to `global` when the user clearly states a preference they'd want everywhere.

---

## Confirming and forgetting

- After storing an explicit request, briefly confirm in words: *"Got it — I'll remember that."* (No emoji; the card shows the visual state.)
- Use **forget** when the user says to drop something; pass a `query` that matches the stored content.
- Use **list_memories** when the user asks what you remember — never guess from context, list the real store.

---

## Don't re-state what's already recalled

The `[Remembered about this user]` block is already in your context — use it silently to personalize your answer. Don't call `list_memories` just to see it, and don't narrate "I remember that you…" unless the user asks.
