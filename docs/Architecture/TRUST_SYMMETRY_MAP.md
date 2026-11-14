# Trust Symmetry Map — mechanisms ↔ measurements

**What this is.** Every trust property the platform assesses is assessed up to twice: once
**in flight** by a mechanism that can act on the answer, and once **after the turn** by a
measurement that grades it. This map pairs the two, scores how much substrate they share,
and says what it would take to share more.

**Why it matters.** Where the two lanes share nothing, three things follow, and all three
have been observed in this codebase:

1. **A station cannot state a reason.** GROUNDED can say *"1 withheld"* but not *"withheld
   because 3 spans were unsupported"* — because the gate's finding and the scorer's number
   are unrelated artifacts with no common vocabulary.
2. **The two disagree for structural reasons and the disagreement looks epistemic.**
   `grounded_overlap` reads 0.27 against `groundedness` 1.00 not because a coarse check
   disagrees with a judge, but because one of them skips a step the other ran.
3. **A lesson learned on one side does not propagate.** `honesty.gate.assess_rag` defaults
   its absolute-threshold branch OFF because *"absolute thresholds are backend-relative"*;
   `retrieval_relevance` ships a hardcoded 0.4 that bands red on a healthy agent.

**Related:** [ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md)
(the mechanism registry) · [AGENT_QUALITY_DIMENSIONS.md](AGENT_QUALITY_DIMENSIONS.md) (the
measurements) · [TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md) Rule 3b.6
(why the two lanes are counted apart) ·
`TRUST_RUNTIME_COMPREHENSIBILITY.md` §6
(the `assess` / `decide` / `score` model this map argues for).

---

## The score

Derived from four observable tests, not judgement:

| | test |
|---|---|
| **T1** | the measurement calls the mechanism's own assessor |
| **T2** | the measurement reads the finding the mechanism recorded, rather than re-deriving it |
| **T3** | both use the same inputs and the same statistic |
| **T4** | both lanes exist and can fire in production |

| score | meaning |
|---|---|
| **5** | **One assessor, one finding.** T1 ∧ T2 — the measurement derives nothing of its own. |
| **4** | **Same assessor, separate invocations** (T1, not T2) — or observes the record without sharing the assessor (T2, not T1). Two results, never reconciled. |
| **3** | **Shared primitives, independent assembly** (T3 partial) — same property, same data, different statistic or unit. |
| **2** | **Same property, independent implementations.** They can disagree for structural reasons. |
| **1** | **Both lanes nominally exist but do not meet** — different inputs, or one cannot fire in production. |
| **0** | **One lane only**, and the other is buildable. |
| **n/a** | The pairing is a **category error** — the label is loose, the design is not wrong. |

**Aggregate across the 21 scored rows: 2.0 / 5 — at the time of writing, and that
caveat is the point.**

This document is a point-in-time analysis. The live state is **derived from the assurance
registry**, because Part E's own argument applies to the map itself: a second copy of a
fact is a defect waiting to happen, and a hand-kept score is accurate only on the day it
was written.

```
python3 scripts/symmetry_state.py
```

### State at 2026-10-30 — read this before the rows below

Parts A–E are the original analysis and keep their original scores; the live picture has
moved on, and `scripts/symmetry_state.py` is the authority:

- **Properties:** 12 shipped; **10 of 12 share ONE assessor across both lanes**
  (`answer_support`, `answer_scope`, `context_use`, `absence_claim`, `retrieval_sufficiency`,
  `citation_provenance`, `identifier_provenance`, `output_sensitivity`, `data_governance`,
  `tool_repetition`); `tool_authority` is score-only by design; `request_integrity` has no
  shipped assessor (in flight only, a seam for pack modules — A14's "no ground truth" reason
  is now its declared `lane_note`). Binding conformance: clean.
- **Labels re-homed (A10, A18 done):** `corpus_quarantine` and `tool_enforcement` now label no
  dimension; `UNGRADED_REASON` records why (quarantine: a structural zero; tool enforcement: a
  *gap* — its denial count is not aggregated). `output_sanity` (A12) and `citations`,
  `answer_groundedness` (A9) are labelled to their gates.
- **The registry grew by eight points** the map predates — `prompt_cache`, `llm_recovery`
  (SIGNED), `attachment_sources`, `outcome_loop`, `compute_lineage` (GROUNDED), `actions`,
  `turn_budget`, `tool_surface` (BOUNDED). `actions` and `turn_budget` own a measurement
  (`action_safety`, `budget_adherence`); the other six are ungraded **by design**, each with
  its reason in `UNGRADED_REASON`.
- **Part B today** reads from `UNGRADED_REASON`: the genuine *gaps* are `tool_enforcement`,
  `preflight_sql`, `sensitivity_ceiling` and `memory_firewall`; everything else ungraded is
  `by_design`. **Part C today** is `residue_dimensions()` — 36 of the 62 dimensions carry no
  guarantee label.

### What has closed since (Aug 2026)

The modular substrate (`agent/assurance/`) replaced per-row patching: a property is named
once, an assessor is bound to it, and both lanes read one assessment. Remapping is a row of
data, validated in one place.

| row | was | now | how |
|---|---|---|---|
| **A2** `grounded_overlap` | 2 | **5** | bound to `answer_support`; one assessor branching on the answer's shape |
| **A3/A4** judge re-invocation | 4 | **5** | the gate records its full ruling; the scorer reads it instead of re-buying it |
| **A6** `scope_adherence` | 3 | **5** | reads the gate's own finding, recomputes only where the gate did not run |
| **A7** `retrieval_relevance` | 3 | **5** | one assessment returns BOTH statistics — and the raw scores now reach the gate |
| **A11** `safety` | 4 | **5** | reads the guard's categories and labels instead of counting steps |
| **A17** authority | 1 | **4** | graded against the scope that was in force, sealed on the turn |
| **A8** detection gap | open | **closed** | the gate binds the same property, so it gained span-free detection |

**Two fused assessors were split** — `evaluate_outbound` and `assess_rag` — each proven
byte-identical across 96 combinations. That was the real blocker: a post-hoc lane cannot
bind to a function that has already applied a policy.

**Three findings the rows themselves did not predict:**

- **A17** — the first assessor *could not fail*. `is_tool_allowed` returns `(allowed,
  reason)`, a tuple, tested as a bool; every violation passed. A measurement that cannot
  fail is worse than none, because it actively reports a boundary as holding.
- **A7** — the raw similarity scores were **dropped upstream** of the gate, so the
  mechanism could not have computed the measurement's statistic even if it wanted to. A
  pipeline information loss, not a duplication; no refactor at the gate would have found it.
- **A2** — the map's own recommendation was wrong, and the eval gate caught it. The
  benchmark corpus was span-based by construction, so it could not show the opposite
  failure mode.

---

## Part A · Paired — a mechanism and a measurement assess the same property

### GROUNDED

| # | Mechanism · functional module | Measurement | Score | The difference | How to close it |
|---|---|---|---|---|---|
| A1 | `honesty_gate` · `agent/honesty/gate.py:assess()` | `honest_absence` | **5** | None. The scorer calls the gate's own assessor and its primitives (`_hgate.assess`, `_hp.data_query_outcomes`, `_hp.asserts_nonexistence`). | **Nothing — this is the reference implementation.** Every other row in this table is a proposal to look like this one. |
| A2 | `grounding_gate` · `agent/grounding/corpus.py:assess()` + `apply_second_chance()` | `grounded_overlap` | **5** ✅ | **CLOSED** — both lanes now bind to the `answer_support` property (`assessors/answer_support.py`). *This row corrected the map's own recommendation.* The map said "replace the scorer with `corpus.assess`", measured on the corpus benchmark: 4 of 18 diverged and sentence-overlap was wrong in all four. **That was wrong, and the eval gate caught it** — the benchmark corpus is span-based by construction, so it could not show the opposite failure: a fluent fabrication carries no span, so the claim check abstains and lets through the one thing a groundedness check most needs to catch. (The scorer's history records the same change tried and reverted once before.) | The two are **complementary, not redundant**: one assessor, branching on the ANSWER'S SHAPE — claims → spans + second-chance; no claims → lexical. Measured: OLD wrong on 5 of 18, NEW wrong on 1 (a pre-existing small-talk false positive both share). Reproduce: `scripts/verify_answer_support.py`. **And because the gate binds the same property, the in-flight lane gained span-free detection — closing A8 through the binding rather than separate wiring.** |
| A3 | `grounding_gate` · `grounding/runtime.py` → `_enforce_with_judge` → `GroundednessJudge` | `groundedness` | **4** | **The same class**, invoked twice — the gate imports the eval layer's judge for its in-path escalation, and the scorer runs it again afterwards. Two verdicts on one turn, never reconciled; the panel shows the scorer's and the gate acted on the other. | Record the gate's judge verdict on the turn and have the scorer **read** it when present. Removes a duplicate judge call (real tokens) and makes the two provably the same ruling. → **5** |
| A4 | `grounding_gate` · `grounding/runtime.py` → `_enforce_with_judge` → `ScopeFaithfulnessJudge` | `scope_faithfulness` | **4** | As A3. | As A3. → **5** |
| A5 | `grounding_gate` · `corpus.assess()` → `CorpusFindings.numeric_fidelity` | `numeric_fidelity` | **3** | Both compute numeric fidelity from `norm_nums` (shared primitive), but **independently** — the gate over its span set, the scorer over answer-vs-`grounding_text()`. The gate's value is already stamped on the turn and ignored. | Read `grounding.numeric_fidelity` off the turn stamp; fall back to the local computation only when absent (older turns). → **5** |
| A6 | `trusted_scope` · `grounding/primitives.py:scope_diff()` | `scope_adherence` | **3** | Shared primitive, independent assembly: the scorer re-runs `scope_diff` over the trace while the gate's own `out_of_scope_dbs` sits on the turn. | Read `grounding.out_of_scope_dbs`; keep `scope_diff` as the fallback. → **4/5** |
| A7 | `honesty_gate` · `agent/honesty/gate.py:assess_rag()` | `retrieval_relevance` | **3** | **Two statistics of one dataset.** The mechanism uses `top_score` (the max); the measurement uses the **mean**. One excellent chunk among nine poor ones passes the gate and fails the scorer. Worse, the mechanism's S2 branch is **off by default** with the reason stated — *absolute thresholds are backend-relative* — while the scorer ships a hardcoded 0.4 that bands red on a healthy agent (campaign: median 0.46). | Agree one statistic, and propagate the calibration posture: either both use `top_score`, or `assess_rag` gains the mean as a second trigger. Retire the scorer's absolute default in favour of the mechanism's deterministic S1 (`qualifying_chunks == 0`). → **4** |
| A8 | `grounding_gate` · **nothing consumes it** | `context_utilization` | **1** | Both lanes exist; they never meet. The gate triggers on **spans**; `assess_rag` requires `claims_checked > 0`. So a **fluent, span-free answer that ignored its sources is invisible to both** — the `FOCUS_HALLUCINATION` shape the codebase already records. `context_utilization` is the one cheap deterministic signal that sees it, and no mechanism reads it. | Wire it as a **`needs_judge` trigger**, never a blocker (it false-positives on brevity: a terse correct summary and an honest refusal both score low). The tiered-enforce architecture already has the shape — deterministic trigger → the `groundedness` judge decides. **This is the only row that closes a detection gap rather than a presentation one.** → **4** |
| A9 | `grounding_gate` · **nothing consumes them** | `citations`, `answer_groundedness` | **1** | Labelled to the gate, read by nothing. `citations` is deterministic and free; `answer_groundedness` is a judge that never runs in path. | `citations` is a plausible second trigger (an answer asserting specifics with no attribution). `answer_groundedness` is the tool-lane twin of A3 and should follow it. |
| A10 | `corpus_quarantine` · `agent/guardrails/corpus_screen.py` | `knowledge_freshness`, `retrieval_performed`, `source_consistency` | **n/a** | **Category error in the label, not the design.** Quarantine excludes flagged documents from retrieval; these three measure document *age*, whether retrieval *ran*, and whether sources *disagree*. None of them grades quarantine. | Re-home the label (a measurement's guarantee is a navigation pointer — pointing at the wrong machinery strands the reader). Quarantine's honest measurement would be *"what fraction of retrieved chunks came from quarantined documents"* — which is currently **0** by construction, so the honest answer may be that it needs none. |

### GUARDED

| # | Mechanism · functional module | Measurement | Score | The difference | How to close it |
|---|---|---|---|---|---|
| A11 | `outbound_guard` · `guardrails/policy.py:evaluate_outbound()` | `safety` | **4** | Observes the record: counts `error:`/`guardrail` steps in the trace. No duplication, but it can only see what the mechanism chose to stamp, and it cannot say *what* was found. | Read the guardrail verdict's category labels off the turn (already stamped, labels-only by the privacy invariant) so the measurement can name the finding rather than count events. → **5** |
| A12 | `outbound_guard` · `guardrails/primitives.py` | `output_sanity` | **2** | Both scan the delivered text with **independent pattern sets** — the guard for PII/toxicity/exfil, the scorer for leaked internals. Related properties, no shared substrate, two regex families maintained apart. | Move the leak patterns into `guardrails/primitives` as a fourth detector family and have both call it. One pattern set, two policies. → **5** |
| A13 | `outbound_guard` · deliberate complement | `content_safety` | **n/a** | By design (TIER_1.3 §5): the judge covers the semantic residue only meaning can catch. Complementary, not duplicative. | Nothing. The asymmetry is the point. |
| A14 | `inbound_guard` · `guardrails/policy.py:evaluate_inbound()` | — | **0** | **Nothing grades the injection detector.** A refusal at the door is enforced and never measured: no measurement asks whether the block was right, or whether a real injection got through. | The honest counterpart is a **precision/recall reading over the detector**, which `eval/redteam.py` already computes against a curated corpus — but offline and unattached to any turn. Surface it as a mechanism-health reading rather than inventing a per-turn scorer (a per-turn measurement here would have no ground truth). |
| A15 | `retrieval_firewall` · `context_window/modules/knowledge_context/handler.py` | — | **0** | A defused chunk is recorded and never graded. | As A14 — the same detector family, so one reading covers both. |
| A16 | `memory_firewall` · `components/builtin/memory/store.py` | — | **0** | A quarantined memory write is recorded and never graded. | As A14. |

### BOUNDED

| # | Mechanism · functional module | Measurement | Score | The difference | How to close it |
|---|---|---|---|---|---|
| A17 | `authority` · `core/agent_authority.py:is_tool_allowed()` / `AgentScope` | `must_not_call`, `tool_subset`, `surface_violation` *(OBDA-READ C3, Aug 2026 — counts the surface's own denials, matched on `SURFACE_DENIAL_MARKER` in the recorded policy reason; non-gating: a steady nonzero is a producer-side product gap, not consumer misbehaviour)* | **1** | **The two lanes grade different things.** The mechanism enforces the agent's real `AgentScope` (from the profile) at three tool chokepoints, fail-closed, on every autonomous turn. The measurements grade against **`case.assertions`** — an eval-case expectation. Online scoring seeds no such assertion, so **both abstain on every live turn.** The boundary is enforced always and graded never. | Read `AgentScope` from the profile and grade the trace against the **real** scope, falling back to `case.assertions` for curated cases. Cheap, deterministic, and it turns BOUNDED's execution reading from permanently-absent into real. → **4** |
| A18 | `tool_enforcement` · `agent/tool_enforcement.py` | `tool_success_rate`, `no_tool_loops` | **n/a** | Category error in the label: the mechanism enforces *which* tools may run; the measurements grade *how well* the ones that ran performed. Different properties. | Re-home the label. Tool enforcement's honest measurement is the denial count, which is not aggregated (see the `_execAtJudged` note). |
| A19 | `sensitivity_ceiling` · `core/knowledge_provenance.py` | — | **0** | Documents withheld by the ceiling are recorded and never graded. | A deterministic counterpart is cheap: *"fraction of retrieved chunks withheld by the ceiling"*. Low value until ceilings are widely configured. |

### Elsewhere

| # | Mechanism · functional module | Measurement | Score | The difference | How to close it |
|---|---|---|---|---|---|
| A20 | `governed_data` · the `data_governance` property | `governed_data_ratio` | **5** | None *now*. Both lanes resolve the ONE bound assessor through the registry. | **Done (Aug 2026).** Scored 5 originally because both call sites ran the same `scan_obda` — but that symmetry was *coincidental*, not structural: the scorer called the function directly, so a remap of `data_governance` would have moved the in-flight lane and left the measurement on the old computation. Routed through the registry; byte-identical across 7 turn shapes. |
| A21 | `provenance_seal` · EPC chain | `provenance_integrity`, `clean_termination` | **4** | Observes the record (reads `provenance_meta`), does not share an assessor. The honest structural check; full cryptographic verification is a separate REST call. | Acceptable as-is. → 5 would mean the scorer calling the same verifier the seal uses, which costs I/O a pure scorer cannot take. **Documented ceiling, not a defect.** |
| A22 | `preflight_sql` · `agent/preflight/guard.py` | — | **0** | A SQL call validated against the session catalog, the declared join keys and the text-stored date columns is recorded and never graded. | The counterpart is nearly free: *"fraction of SQL calls whose databases were in the authoritative catalog"* — the guard already computes it per call. |
| A23 | `agent_integrity` · `core/trust/composition.py` | — | **0** | Signing and attestation are acts, not per-turn assessments. | **None warranted** — the integrity state is a fact about the agent, not a grade of a turn. Score 0 records the shape, not a defect. |
| A24 | `review_parking` · `core/review_queue.py` | — | **0** | Parking is an act. | **None warranted** — the queue's own counts are the reading. |
| A25 | `trailing_trust` · `eval/trust_behavior.py` | — | **n/a** | **A measurement wearing a mechanism's clothes.** It is a trailing-window composite; nothing acts during the turn. Recorded as a known labelling defect in the registry. | The fix is a **declared lane per point** (step 0 of the trust-runtime roadmap), not a new measurement. Deleting the point would leave TRUSTED with nothing. |

---

## Part B · Mechanism with no measurement — the six

`agent_integrity` · `preflight_sql` · `sensitivity_ceiling` · `review_parking` ·
`trailing_trust` · `memory_firewall`

Two of these (`agent_integrity`, `review_parking`) are **acts**, and no measurement is
warranted — a score of 0 records the shape, not a fault. `trailing_trust` is a
mislabelled measurement. The remaining three (`preflight_sql`, `sensitivity_ceiling`,
`memory_firewall`) are genuine controls that **run and are never graded**: nothing anywhere
answers *"is this control working?"*

---

## Part C · Measurement with no mechanism — the twenty-nine

These are correct as-is and should **not** be forced into symmetry. They fall into three
groups:

| group | examples | why no mechanism |
|---|---|---|
| **Properties of the run** | `cost`, `latency`, `tokens`, `steps`, `tasks`, `efficiency`, `parallel_efficiency`, `self_correction`, `tool_calls`, `error_recovery` | Resource and process facts. Nothing in-flight *guarantees* them; a control that blocked an answer for being slow would be a different product. |
| **Properties of the plan** | `plan_adherence`, `plan_execution`, `routing`, `coordination_efficiency` | The planner is not a trust mechanism. |
| **Open-ended quality** | `answer_correctness`, `instruction_following`, `synthesis_quality`, `format`, `answer_present`, `completed`, `context_health` | Judged after the fact by construction. |
| **Flow-run health** | the seven `flow_*` dimensions | A flow run has no assurance dial. |

**Two exceptions worth revisiting** — `context_utilization` (A8) and `citations` (A9) are
listed as labelled but unconsumed, and both are cheap, deterministic, and see something no
mechanism currently sees.

---

## What the map says, ranked

1. **A8 — `context_utilization` as a corpus trigger.** The only row that closes a
   **detection** gap. A fluent, span-free answer that ignored its sources is invisible to
   both the grounding gate and the honesty RAG check today.
2. **A2 — `grounded_overlap` onto `corpus.assess`.** The cheapest structural fix with the
   largest cleanup: it is the sole cause of `REFINEMENT_PAIRS`, and Rule 3.0c's four
   readings, two row chips, centre-dot channel, ~20 gate assertions, three FAQ entries and
   two contract rules exist to manage a divergence that is **mechanical, not epistemic**.
3. **A17 — grade authority against the real scope.** Turns BOUNDED's permanently-abstaining
   measurements into a live reading of a control that runs on every autonomous turn.
4. **A3/A4 — read the gate's judge verdict instead of re-invoking.** Saves real tokens and
   makes two rulings provably one.
5. **A7 — reconcile `retrieval_relevance` with `assess_rag`.** One statistic, one
   calibration story; propagates a lesson one side already learned.
6. **A11/A12/A5/A6 — read what the mechanism already stamped.** Four rows, all the same
   move, all cheap.
7. **A14–A16, A22 — the ungraded controls.** Lowest urgency, but *"nothing measures whether
   this control works"* is a real answer to give an auditor, and today the platform cannot
   give it.

**The two 5s are the proof the pattern works** — `honest_absence` and
`governed_data_ratio` are symmetric, and neither cost more to build that way. Symmetry is
cheap at design time and expensive to retrofit, which is the argument for fixing the
registry's shape rather than the individual rows.

---

## Part D · Measurements that could make a mechanism better

Parts A–C ask *"do the two lanes share substrate?"* This part asks the forward question:
**of the measurements no mechanism consumes, which would improve a mechanism if one did?**

The two are different. A measurement can be perfectly implemented, correctly counted at
JUDGED, and still represent a signal the platform computes every turn and then declines to
act on. That is not an asymmetry — it is unused evidence.

**The discipline:** a measurement earns an in-flight lane only if it is **cheap**,
**deterministic**, and **sees something the mechanism is blind to**. Anything else belongs
in the score lane where it already is.

| Measurement | Property it would feed | Lane | What the mechanism gains | Why it is safe |
|---|---|---|---|---|
| **`context_utilization`** | `context_use` → the grounding gate | `IN_FLIGHT_TRIGGERED` | The gate triggers on **spans**; `assess_rag` requires `claims_checked > 0`. A fluent, span-free answer that ignored its sources is invisible to **both**. This is the only signal that sees it. | Never a blocker — it false-positives on brevity (a terse correct summary and an honest refusal both score low). As a trigger, the `groundedness` judge makes the call. |
| **`no_tool_loops`** | `tool_authority` → a loop breaker | `IN_FLIGHT` | Today a loop is **graded after the agent has already run it**: the tokens are spent, the user has waited, and the reading arrives too late to help anyone. A deterministic repeat-detector at the tool chokepoint could stop it mid-run. | Pure, deterministic, 0 tokens. Identical-args repetition is a fact, not a judgement. |
| **`citations`** | `answer_support` → a second trigger | `IN_FLIGHT` | An answer asserting specifics with **zero attribution** is a grounding signal the span check cannot produce — it is about what the answer *omits*, not what it claims. | Already deterministic and free; already off by default, so it changes nothing until switched on. |
| **`source_consistency`** | `answer_support` → an **annotate** trigger | `IN_FLIGHT_TRIGGERED` | When retrieved documents materially disagree and the answer states one reading as settled, the honest act is **disclosure, not withholding** — precisely what `annotate` mode exists for and what no mechanism currently triggers. | A judge, so trigger-gated. And annotate cannot withhold, so the failure mode is a redundant note, not a lost answer. |
| **`cost` · `tokens`** | `tool_authority` → an in-flight ceiling | `IN_FLIGHT` | `max_cost_per_run` is enforced **post-hoc** (`authority_budget_exceeded` is flagged *after* the run). A budget that is checked once the money is spent is an audit record, not a control. | Pure arithmetic against a configured cap; already the declared intent of the setting. |

### What shipped (Aug 2026)

Three of the five, each **off unless configured** — so the shipped default is
byte-identical and the capability is there for an operator who wants it.

| row | shipped as | why it is safe |
|---|---|---|
| **`context_utilization`** ✅ | a `needs_judge` trigger on the corpus family | escalates, never decides — a deterministic blocker would withhold correct answers for being SHORT. The brevity risk resolved itself: an answer with no content words ≥5 chars yields no assessment, so `"Yes."` abstains rather than being convicted. |
| **`no_tool_loops`** ✅ | `agent/loop_guard.py` at BOTH tool chokepoints | blocks the (N+1)th and never the retry (a self-correction retry carries the same signature); the block is **recovery-shaped** so the agent can change course; `TDA_` tools exempt; turn-scoped via contextvars, proven under interleaving. Reaches the MCP-adapter path AND the **ReAct LangChain wrapper** (`tool_enforcement.loop_guard_block`) — the ReAct engine is the one most prone to self-loops, and it previously escaped the guard. |
| **`cost`** ✅ | `check_budget_before_call` at the LLM chokepoint of EVERY autonomous engine | the SAME predicate as the post-hoc record — one cap, two moments. Routed through `PlanExecutor` (Optimize/Ideate/Focus), the **ReAct engine** (`conversation_agent`, each `on_chat_model_start`, graceful `budget_stopped`), and the **genie coordinator** — closing the earlier gap where only the `PlanExecutor` path checked in flight so ReAct + genie got post-hoc flagging only. Autonomous turns only, because that is what an `AgentScope` governs. |

`citations` and `source_consistency` remain available and unwired — both are cheap and both
would follow the same trigger shape.

**The pattern all three share:** the gate resolves the property through the registry rather
than reimplementing its test, so a control can never act on a number the Scorecard
disagrees with.

### Deliberately rejected, with the reason

Recording these matters as much as the candidates — a list of everything that *could*
theoretically feed a gate would be a list of ways to make the product worse.

| Measurement | Why not |
|---|---|
| `latency` | Withholding an answer for being slow produces no better answer — only a slow failure. |
| `answer_correctness` · `synthesis_quality` · `instruction_following` | Judges with **no cheap trigger**. Without one they would run on every turn; with one, the trigger is the real mechanism and these are its refinement. |
| `self_correction` · `error_recovery` | They measure the recovery. The mechanism *is* the recovery — feeding them back would be a control acting on its own output. |
| `tool_success_rate` | A failing tool already drives self-correction in flight. A second consumer of the same signal is duplication, not coverage. |
| `plan_adherence` · `plan_execution` · `routing` · `efficiency` | Planner quality, not trust. Nothing here guarantees them, and nothing should withhold an answer over them. |
| the seven `flow_*` | A flow run has no assurance dial and no in-flight seam. |
| `answer_present` · `completed` · `format` · `context_health` | Post-conditions of the run, cheap and correct where they are. |

### How each lands in the substrate

Every row above is a **binding**, not a patch — which is the point of building the
substrate first:

```
bind("context_use",     "<assessor>", (Lane.IN_FLIGHT_TRIGGERED,))
bind("tool_authority",  "<assessor>", (Lane.IN_FLIGHT,))
```

`conformance.check_binding` already refuses the unsafe half of each: a judge cannot take
the unconditional in-flight lane, and an assessor whose inputs the in-flight lane cannot
supply is refused rather than left to report clean forever. So the risky part of Part D is
governed by the same validation the remapping UX will use — the work is declaring the
assessor and the binding, not writing a new guard for each case.

**Ranked**: `context_utilization` (closes a detection hole nothing else sees) →
`no_tool_loops` (the only row that saves the user time and money rather than improving a
reading) → `cost` (makes a setting that already claims to be a cap actually be one) →
`citations` → `source_consistency`.

---

## Part E · Does the asymmetric reading earn its place?

Parts A–D assume more symmetry is better. Part E tests that, because it is not
automatically true: two readings of one property can be genuine redundancy, or one of them
can be **duplication wearing a metric's name** — a number the consumer must reconcile that
tells them nothing the other does not.

**The test.** A second reading earns its place only if it can tell the consumer something
the symmetric form cannot. Three qualifying grounds, and nothing else counts:

| | ground |
|---|---|
| **1** | **Different question.** It measures a genuinely different property; the pairing was never duplication. |
| **2** | **Different evidence.** Same property, but it sees inputs the other cannot — usually because the mechanism runs before something exists. |
| **3** | **Deliberate redundancy.** Same property, same inputs, kept because a single point of failure is unacceptable. |

`grounded_overlap` (A2) is the worked example and the reason this section exists: two
readings, a disagreement that cost Rule 3.0c plus four surfaces and ~20 assertions, and the
honest resolution collapsed them into **one property with two branches**. The consumer lost
nothing and gained a reading that is right on 17 of 18 cases instead of 13.

| Row | Ground | Verdict | Reasoning |
|---|---|---|---|
| **A2** `grounded_overlap` | 1 → resolved | **Folded ✅** | The two saw different failure shapes, so neither was redundant — but neither needed to be a separate *dimension*. One assessor, branching on the answer's shape. |
| **A3/A4** judge re-invocation | **none** | **Fold** | On a passed turn both invocations judge the same text with the same class. On a blocked turn the scorer already abstains, so only the gate's ruling exists. The second call is duplication **that costs real tokens**. Read the gate's verdict when present; run when absent. |
| **A5** `numeric_fidelity` | **2** | **Keep** | Genuinely different evidence, and I expected otherwise. The gate grounds numbers against the retrieved CHUNKS; the scorer grounds them against `grounding_text()` — **tool results *and* retrieval** — and applies to the data classes, where there are no chunks at all. Folding naively would silently drop numeric grounding for every Optimize turn. Unify the *support set* in the context, not the readings. |
| **A6** `scope_adherence` | **none** | **Fold** | Same function (`scope_diff`), same inputs, and the gate's `out_of_scope_dbs` is already on the turn. Read the stamp when present; compute when the gate did not run. |
| **A7** `retrieval_relevance` | **1** | **Keep both** | `top_score` answers *"was the best chunk good enough to justify answering?"*; the mean answers *"was the retrieval set good?"*. One excellent chunk among nine poor ones makes those diverge legitimately. The fix is not to choose — it is to return **both in one Finding's `metrics`**, so the gate reads `top` and the scorer bands `mean` from a single assessment. |
| **A11** `safety` | **none** | **Fold** | Counting guardrail steps in the trace is *strictly less* informative than the guardrail's own category labels, which are already stamped. The consumer gains. |
| **A12** `output_sanity` | **1** | **Keep the reading, fold the substrate** | Leaked internals is not PII. The reading stays; the pattern sets should live once in `guardrails/primitives` so both lanes share detectors rather than maintaining two regex families. |
| **A17** `must_not_call` · `tool_subset` | **none** | **Fold** | They grade `case.assertions`, which online scoring never seeds — so today they give the consumer **nothing at all** on live traffic. The symmetric form (grade against the real `AgentScope`) goes from nothing to something. |
| **A21** provenance | **3** | **Keep** | A cheap structural check that runs always, beside an expensive cryptographic verification available on demand. Legitimate redundancy with different costs — a documented ceiling, not a defect. |
| **A10/A18** the mislabelled five | n/a | **Keep, relabel** | Not duplication in any direction: different properties wearing the wrong pointer. |

### Applying the same lens INSIDE the measurement lane

If two readings of one property are suspect between lanes, they are equally suspect within
one. Checked, and the suspicion did **not** hold — `steps` · `tasks` · `tool_calls` look
like three volume measures and are three different questions:

| | measures | scope |
|---|---|---|
| `steps` | plan SIZE — phases the strategic planner emitted | `tool_enabled` only |
| `tasks` | work VOLUME executed, classified, planning collapsed to one | all classes |
| `tool_calls` | RAW call count — catches the thrash `tasks` hides by collapsing | tool classes |

`tasks` deliberately excludes framework scaffolding and collapses planning, which is
exactly what makes `tool_calls` able to see a retry storm that `tasks` smooths away. No
fold. Recorded because an unexamined suspicion left in a document reads like a finding.

### The answer

**Four of the nine fold, and in two of them the symmetric form is strictly more valuable to
the consumer** — A17 currently reports nothing on live turns, and A11 reports a count where
labels are available. Two more (A3/A4) fold at a saving in real tokens.

**Four legitimately stay**, and the reasons are worth as much as the folds: one measures
wider evidence (A5), one asks a different question at a different statistic (A7), one is a
different property (A12), one is deliberate cost-tiered redundancy (A21).

So the answer to *"is the symmetric representation of equal value?"* is **usually yes and
sometimes better — but not always**, and the exceptions are principled rather than
historical. A programme that folded everything would have silently dropped numeric
grounding for every Optimize turn (A5) and collapsed two genuinely different retrieval
questions into one (A7). **The value of this section is the four it stopped.**
