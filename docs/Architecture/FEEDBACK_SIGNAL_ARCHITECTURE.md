# Feedback Signal Architecture — the universal vote

**What it is:** the per-turn upvote/downvote in the conversation panel, turned into a
learning signal that reaches every engine and the evaluation layer.

**Why it exists:** a vote is the scarcest signal the platform receives — free human
ground truth on a fully-instrumented turn. It was stored engine-agnostically from the
start (`update_turn_feedback` writes `turn["feedback"]` for every engine) but had
exactly one consumer: the RAG champion score. Everything else discarded it.

**The two semantics, which are not symmetric:**

| | Meaning | Shape of the effect |
|---|---|---|
| **Downvote** | *don't repeat this* | exclude · demote · deactivate · diagnose |
| **Upvote** | *do this again* | promote · protect · seed |

Implementation plan and the per-increment record of what the build changed about it:
`docs/roadmap/VOTE_SIGNAL_PLAN.md`.

---

## 1 · One dispatcher, many consumers

```
conversation panel  ──►  POST /api/session/<sid>/turn/<n>/feedback
                              │
                    session_manager.update_turn_feedback()      ← the ONE wire point
                              │  (persists turn["feedback"], then)
                              ▼
                    agent/feedback/dispatcher.schedule_vote_dispatch(VoteEvent)
                              │  fire-and-forget, strong-ref'd task
                              ▼
   champion → annotation → genie → retrieval → memory → diagnosis
   (sequential, each individually wrapped; the dispatcher NEVER raises)
```

`agent/feedback/` is a sibling of `agent/grounding/`, `agent/guardrails/` and
`agent/honesty/` — a pure core plus a runtime seam. **No engine, route or store may
implement vote logic of its own**; that one-seam discipline is what keeps a consumer's
gate and its effect from drifting apart.

| File | Role |
|---|---|
| `agent/feedback/types.py` | `VoteEvent` — the immutable vote transition (pure, no I/O) |
| `agent/feedback/dispatcher.py` | registry, `dispatch_vote`, `schedule_vote_dispatch`, `signal_enabled` |
| `agent/feedback/consumers/champion.py` | RAG champion case (planner repositories) |
| `agent/feedback/consumers/annotation.py` | the eval annotation + the read side (`latest_votes`) |
| `agent/feedback/consumers/genie.py` | coordinator → expert propagation |
| `agent/feedback/consumers/retrieval.py` | per-document retrieval feedback |
| `agent/feedback/consumers/memory.py` | pause facts learned from a rejected answer |
| `agent/feedback/consumers/diagnosis.py` | opt-in judge run (the ONLY token spender) |
| `core/retrieval_feedback.py` | the per-document store (self-applying table) |
| `eval/sink.py` | THE online-eval span reader (shared with `rest_routes`) |

### `VoteEvent`

```python
VoteEvent(user_uuid, session_id, turn_id, vote, prev_vote, turn,
          profile_tag=None, profile_type=None, depth=0, propagated=False)
```

`turn` is a **deep copy** taken inside the session transaction, *after* the vote is
applied — consumers run outside the lock and must neither alias nor mutate live state.

**There is deliberately no `profile_id`.** A real `workflow_history` turn carries
`profile_tag` and `profile_type` but not an id, and the session-level `profile_id` /
`last_executed_profile_id` describe the session's *current* profile — not necessarily
the one that produced an older or `@TAG`-overridden turn. A field that silently lies is
worse than an absent one, so a consumer that needs the id resolves it from the tag
(`consumers/diagnosis.resolve_profile`).

### The six transitions every consumer must handle

`None→up` · `None→down` · `up→None` · `down→None` · `up→down` · `down→up`, plus an
idempotent re-send of the same value. Consumers are written **recompute-style** (apply
the current state) rather than as one-shot side effects, which is what makes a vote
reversible.

### Depth guard

A propagated vote (genie) carries `depth ≥ 1`. At `MAX_DEPTH` (2) only the
**depth-safe** consumers run — currently `annotation`, which records and never acts.

---

## 2 · What each engine does with it

| Engine | Downvote | Upvote |
|---|---|---|
| **Optimize** (`tool_enabled`) | the turn's RAG case is scored `-1`, excluded from retrieval (`user_feedback_score >= 0`), and the champion is re-elected | scored `+1`, re-elected (the election ranks feedback before tokens), and the replay-failure counter is reset |
| **Focus** (`rag_focused`) | the documents that served the answer accrue a negative net and surface in **Corpus Health** | the same documents accrue a positive net |
| **Coordinate** (`genie`) | when **exactly one** expert produced the answer, the vote propagates to that expert's own turn — where its engine's consumer does the work | same |
| **Ideate / Conversation** (`llm_only`) | facts the extractor auto-learned from that turn are **paused** | nothing (see below) |
| **All** | annotated for calibration; opt-in judge diagnosis | annotated for calibration |

**Rules worth not re-litigating:**

- **A multi-expert coordinator answer propagates to nobody.** The answer is a synthesis
  and attribution is ambiguous; crediting or blaming every expert would manufacture
  signal the user never gave. The rule is one pure function, `genie.eligible_slave`.
- **The replay-failure counter is upvote-only.** It means *this champion was replayed
  and the run really failed*. A turn the user disliked may have executed perfectly, and
  letting taste increment it would retire a working plan through a mechanism built for
  broken ones.
- **A plain upvote never force-learns a memory.** An upvote is not a request to
  remember; treating it as one would put facts the user never asked for into every
  future session.
- **Only `learned` memories are paused.** An explicit "remember this", or a note the
  user typed, is not the agent's inference and survives a downvote on the turn it
  happened in.
- **Retrieval feedback records only.** It never reranks, never scores and never
  quarantines — a statistical signal surfaces for a human, exactly as the
  retrieval-anomaly detector does.

---

## 3 · The preference axis

A vote is a **preference**, not a measurement. It is published *beside* the quality
reading and never inside one — the third axis alongside intervention and quality
(see [TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md); the rule's history is
[casebook §3bb](TRUST_STATUS_COMMUNICATION_CASEBOOK.md)).

Nothing here may fold a vote into `pass_rate`, `turn_failed` / `gating_failures`, a
quality band, a station colour, the TRUSTED composite, or any scored dimension. The
surfaces that show it:

- **Runs ledger row** — a muted word chip (`upvoted` / `downvoted`), never colour
  alone, which must not move the row's band.
- **Agent roster detail** — `user feedback — N up · M down · K disagree with the
  scorecard`. Rendered only when votes exist (an honest absence, not a zero).
- **Corpus Health** — the lowest-net documents, as a curation list.
- **The vote buttons themselves** — a per-engine tooltip stating what the vote will do,
  and what it will not.

**Disagreement is the point.** A turn the scorers passed that the user downvoted (or
the reverse) is the only place a human says the quality bands are miscalibrated for this
deployment — which is why it is counted and named rather than smoothed away.

**Scoping:** votes are read only as the voter's own (`reviewer = "user:<uuid>"`,
filtered in SQL). In system scope an admin sees **no** vote reading rather than a zero
that would read as "nobody rated this".

---

## 4 · The eval layer

Every vote writes an `eval_annotations` row — `user_upvote` / `user_downvote` /
`user_vote_cleared`, suffixed `:propagated` when it arrived by genie propagation.

**Append-only:** a flip writes a NEW row; readers take the latest per `(session, turn)`.
`judge_passed` freezes the ruling the human SAW at vote time — never re-derived, because
a re-score could flip the verdict and silently rewrite what the human reacted to. A
turn that was never scored, or was refused at the door, records `NULL`: no ruling was on
screen, and that is the honest value rather than a pass.

`analysis.vote_alignment(spans, votes)` computes the agreement/disagreement reading;
`online_quality_by_agent(..., votes=…)` and `agent_runs(..., votes=…)` attach the
optional keys. **Omitted entirely when no votes are supplied**, so every existing caller
is byte-identical.

---

## 5 · Governance

One config block, `APP_CONFIG.FEEDBACK_SIGNALS` (`core/config.py`):

| Flag | Default | Effect |
|---|---|---|
| `enabled` | `True` | master — off restores the pre-feature path exactly |
| `annotate_eval` | `True` | write the calibration annotation |
| `champion_upvote` | `True` | upvote promotion (the downvote half pre-dates this) |
| `genie_propagation` | `True` | coordinator → single expert |
| `retrieval_feedback` | `True` | record per-document feedback |
| `memory_deactivate` | `True` | pause learned memories from a rejected turn |
| `judge_on_downvote` | **`False`** | run the judges on a downvote — **costs tokens** |

Every default-on consumer is **deterministic and 0 tokens**. `judge_on_downvote` is the
only spender and therefore ships off; when enabled it calls
`evaluate_turn_online(force_judges=True)` — the same timeouts, the same span, the same
Live-Status step and the **same canonical cost sink** as every other judge run. It never
overrides a profile's own judge opt-out, never runs on a propagated vote, and runs once
per turn per process.

---

## 6 · Invariants (each is test-pinned)

| Never | Where enforced |
|---|---|
| A vote changes a measurement, band or composite | `test_eval_analysis`, `test_trust_circle_grammar` |
| A vote bypasses a trust gate or releases withheld content | seeding is already gate-blocked; no consumer touches `grounding`/`guardrails`/`honesty` |
| A vote deletes anything | `test_feedback_memory` (deactivate only), `test_retrieval_feedback` |
| A vote is irreversible | the six-transition tables in every suite |
| Cross-user reads or writes | `test_feedback_annotation`, `test_retrieval_feedback`, `test_feedback_memory` |
| A default-on consumer spends tokens | `test_feedback_diagnosis` |
| Vote logic outside `agent/feedback/` | `test_feedback_dispatch` (structural) |
| The response waits on a consumer | `test_feedback_dispatch` (scheduled, strong-ref'd) |

**Gates:** `test_feedback_dispatch` (51) · `test_feedback_annotation` (42) ·
`test_feedback_champion` (19) · `test_feedback_genie` (27) · `test_retrieval_feedback`
(41) · `test_feedback_memory` (28) · `test_feedback_diagnosis` (26) ·
`test_vote_tooltip.mjs` (121). Every security- or contract-bearing rule is
mutation-tested to fail by name.
