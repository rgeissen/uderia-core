# Assurance Instruments Architecture

**What this covers:** the per-turn assurance layer — the *intervention registry* that
declares every point which can alter, withhold, constrain or attest an answer, the
per-turn tally that lets context-less seams report into it, the gate-outcome lift that
carries what the gates DID onto the eval span, and the dial that renders all of it.

**Read first:** [TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md) — the
grammar every assurance reading obeys. This document describes the *machinery*; that
one governs *how it may speak*. The estate-level view (the eight-station Trust Circle)
is the same vocabulary at a different altitude.

---

## 1. Why a registry exists

The predecessor surface — the Trust Journey strip — **enumerated** its ten stages in a
hand-written frontend list. Six real interventions existed in the codebase and appeared
nowhere on it: preflight SQL, the retrieval firewall, corpus quarantine, the sensitivity
ceiling, review parking, and the memory firewall.

> A surface that claims to show the assurance path while silently omitting six of its
> gates is worse than no surface: it teaches the reader that what they see is what ran.

So the registry (`src/trusted_data_agent/agent/assurance_points.py`) is **the
declaration**, and every surface is a **view over it** — contract invariant **V1**. A
gate added to the codebase without a row here fails the build before it can fail a
reader.

**What qualifies as a point:** it must be able to *alter, withhold, constrain or attest*
an answer. A metric is not a point. A log line is not a point. A protection that guards
*future* turns rather than this one is declared with `excluded=True` **and a recorded
reason** — the memory firewall is the canonical case: it protects tomorrow's answer, not
this one, so counting it here would overstate what ran on the turn in front of you.

---

## 2. The points — 25 on a turn's path, 1 recorded exclusion

`REGISTRY` holds **26** `AssurancePoint` rows; `points()` returns the **25** that can appear
on a turn (`points(include_excluded=True)` adds the excluded one). Order is the order an
answer meets them. Several points apply only to some classes (`applies_to`, e.g.
`preflight_sql` and `compute_lineage` only to Optimize and Ideate + tools).

| Station | Points |
|---|---|
| `signed` | `agent_integrity` · `prompt_cache` (S2, the Attested Cache) · `llm_recovery` (S4, the Resilience Envelope) |
| `grounded` | `attachment_sources` (S9) · `outcome_loop` (S7) · `trusted_scope` · `preflight_sql` · `corpus_quarantine` · `grounding_gate` · `honesty_gate` · `compute_lineage` (S3, Number lineage) · `governed_data` |
| `guarded` | `inbound_guard` · `retrieval_firewall` · `outbound_guard` · *`memory_firewall` (excluded)* |
| `bounded` | `actions` (OBDA-ACT) · `turn_budget` (S5) · `tool_surface` (S6) · `authority` · `tool_enforcement` · `sensitivity_ceiling` |
| `scored` | *(none — nothing runs here)* |
| `reviewed` | `review_parking` · `needs_input` (PROP-011 — a turn that stopped to ask; owns `guessed_past_ambiguity`) |
| `trusted` | `trailing_trust` |
| `proven` | `provenance_seal` |

Each point carries a **power** (`alter · withhold · constrain · attest`) and, for the Guide, a
**class** (`POINT_CLASS`, all 25 rows: 12 `control` · 6 `boundary` · 5 `measure` · 2 `attestation`) —
you set a *mode* on a control, a *scope* on a boundary, a *threshold* on a measure, and you
*verify* an attestation. `prompt_cache`, `llm_recovery` and `attachment_sources` are classed
`measure` — evidence for their station, not acts (a fallback is inside the signed deployment,
so recovery is evidence for SIGNED, not a control; only a model outside the signature reads
`warn`). `unclassified_points()` must be empty.

**JUDGED (`scored`) holds no mechanism, and that is correct.** Its subject is the
measurement lane — checks taken *after* the answer shipped — so there is nothing that
acts during the turn for it to report. The station says exactly that in words rather than
rendering blank: *"nothing runs here — this station reports what the measurements found
after the turn."* An unexplained empty station and an explained one are different things,
and the gate asserts the explanation, not merely the emptiness.

`stations_covered()` is the programmatic check; the gates assert it.

**`governed_data` (GROUNDED) reads the OBDA-READ lane stamp** and knows all four lanes
(Aug 2026): `governed` → pass · `free-form`/`mixed` → the advisory warn ("shown
honestly") · **`confined` → pass** — every raw-SQL attempt was DENIED by the data
surface and nothing executed, so the confinement holding is the mechanism *working*,
never a warn (a warn there punishes the agent for being protected). With
the OBDA-READ component off for the agent (`obda_planes.plane_enabled("read", …)` — the
plane is a component since Oct 2026; there is no master switch) the scan writes no stamp and
the reading is `na` (structurally absent) — GROUNDED never sees a permanent advisory warn on an
agent that has not adopted governed data.

`evaluation` was **retired** as a point (Aug 2026). It was an intermediate layer with
nothing of its own to report: a mechanism records what it DID during the turn, and
evaluation does nothing during the turn — it grades afterwards. Its state was derived
from the very measurements it was supposed to introduce, so a station folded the same
evidence twice at two different strengths and the two could disagree (the mechanism read
`warn` where the measurements read `danger`). The measurement lane now speaks for itself;
`measurement_status()` reports whether scoring ran, which is the one fact evaluation
carried that nothing else did.

`trailing_trust` is a **recorded labelling defect**, not a mechanism: TRUSTED's reading is
a trailing-window composite, not something that acted during the turn. It is kept in the
registry because removing it would leave TRUSTED with no point at all; the fix is a
declared lane per point (step 0 of the trust-runtime roadmap), not a deletion.

For the same reason its Guide entry has **no `reads` row**: `reads` names a module (the
code that assesses a property of *this* turn) and the aggregate has none — an M chip
there would claim a shared computation that does not exist. What it does name is the
checks it weights: `POINT_WEIGHTS` reads `trust_behavior.DEFAULTS["trust_dims"]` and serves
them as the entry's `weights` row (each check answers `weighs on`), so "the trust-critical
checks carry their configured weight" is followed by which ones. The remainder of the
blend is JUDGED's members and is not repeated.

---

## 3. The state ladder — one reader, six states

`read_turn()` is the **single** stamp → state ladder. The frontend does not re-derive it
(an earlier duplicate derivation in JS was removed; a second ladder is precisely the
drift this codebase keeps paying for).

| State | Meaning |
|---|---|
| `pass` | the mechanism ran and was satisfied |
| `warn` | it ran and found something short of clean |
| `danger` | it ran and acted — withheld, redacted, blocked, parked |
| `na` | structurally absent — the mechanism's path was not reached on this turn |
| `unknown` | the mechanism RAN and could not report (`action: "error"`, or its reader failed); **never rendered as a pass** |
| `pending` | the mechanism runs after the answer and has not reported yet — **not** `na` |

**V4 — absent evidence is NAMED, never assumed — and absence is not an alarm.** A turn
carrying no stamp for a mechanism reads `na` with the reason stated ("did not run on this
turn"), never `pass` and never `unknown`. The earlier form of this rule split silence in
two — no stamps at all read `na` (pre-tracing), *some* stamps but not this one read
`unknown` ("should have recorded and did not") — and was retired 2026-08 against
production evidence: real turns carry every partial-stamp shape (`grounding` alone,
`guardrail_inbound` alone, `honesty` alone) from benign engine-path variance, and the
conditional stamps (`preflight`, `retrieval_hygiene`) exist only when their subject occurs
(a SQL call, a retrieval) — so `unknown`-on-absence buried whole stations in "not
recorded" on ordinary passing turns (`unknown` outranks `pass` in the worst-of fold). The
PRECISE gap signal survives where it belongs: a gate that ran and errored stamps
`action: "error"` itself and reads `unknown` from there. `turn_predates_tracing()` remains
the pre-tracing detector so a migration cannot retroactively grade history against a rule
that did not exist when it ran.

---

## 4. `assurance_tally.py` — the context-less seams

Three points fire where the turn is not in scope: the preflight guard runs inside the MCP
adapter (session id only), retrieval hygiene inside `retrieve_examples` (no session at
all), and the retrieval firewall inside a context-window module.

Threading a turn handle through every caller of all three was rejected. The platform had
already solved this exact shape once — the Agent Principal — so the tally reuses it: a
**`contextvar` set once at the turn boundary**, read at the chokepoints, propagating into
awaited coroutines and child tasks so concurrent turns stay isolated **with no signature
change**.

API: `begin_turn` · `end_turn` · `active` · `mark_ran` · `record` · `stamp` · `drain_new` · `snapshot`. `drain_new()` is the live half: what was recorded since the last drain, so the executor can stream `assurance_progress` from what a mechanism itself recorded rather than inferring it.

It is **not** a metrics sink and **not** a log. It holds only what a reader needs to
answer *"did this protection run on my answer?"*.

**The SIGNED seal rides here.** `composition.turn_integrity_stamp()` is recorded via
`record("integrity", …)` in `executor.run()` and persisted by all four standard engines
through `**_assurance_stamps()`; the genie coordinator bypasses `executor.run()` and
seals in its own turn assembly. The turn-details endpoint must **never** live-resolve
trust into a turn — doing so retroactively flipped historical turns' SIGNED whenever an
agent changed. `resolve_profile_trust` is CI-banned from `api/routes.py`
(`test_assurance_integrity.py` §5b; replay semantics in `test_assurance_points.py` §4c).

---

## 5. `eval/gate_outcome.py` — lifting what the gates DID onto the span

Every turn is described by two records: the **gate stamp** (what the runtime gates did —
persisted as `grounding` / `guardrail` / `guardrail_inbound` / `honesty`) and the **eval
span** (what the scorers measured). Every consumer surface — the Trust Circle's execution
attributes, the Agent roster, the runs ledger — reads only the span.

That one omission produced five findings of a single shape: *a gate that acted left no
trace where anyone looks*. The starkest: an inbound guard refused a turn and GUARDED
still read `11/11 clean` — the blocked turn is deliberately **not scored** (never feed an
injected prompt to a judge), so it was absent from the population entirely.

`gate_attributes()` lifts the outcome onto the span so the measurement surfaces cannot
report clean over a turn that was withheld.

---

## 6. The dial

`static/js/handlers/assuranceDial.js` is ONE instrument, replacing both the Trust Journey
strip in Live Status and the answer capsule's mini-ring/linear glyphs. **It derives
nothing** — it renders the server-produced `turn.assurance`. Every reading it shows is a
row of the registry read through `read_turn()`.

`GET /v1/assurance/vocabulary` serves the registry as Guide-shaped entries, so the
Assurance Guide is complete **from any entry point** — registering only what a rendered
dial happened to list left the Guide empty when opened from the Quality card.

**Live PROGRESS, distinct from the verdict.** While a turn runs, the dial fills
station-by-station via `note()` (fed by `assurance_progress` SSE events) instead of settling
the whole ring in one end-of-turn frame. The hard rule is **progress is not a verdict**: an
arrival adds a pending "N in, more to come" reading and NEVER takes the station's colour
until the authoritative payload lands (a station coloured on the first mechanism to report
would be the vacuous clean Rule 3.7 bans, one level down). The one exception — a mechanism
that ACTED (a redaction) — is surfaced immediately and labelled as an act, never as the
reading. `LIVE_STATION` maps each streaming mechanism to its station: the guardrail (GUARDED,
both directions), the grounding and honesty gates, a mid-turn **`retrieval`** cue (GROUNDED
begins to fill when knowledge is retrieved — never a verdict), `obda`, `compute_lineage`,
`outcome_loop` and `attachment_sources` (GROUNDED), `llm_recovery` (SIGNED), `turn_budget`,
`action_record` and `tool_surface` (BOUNDED), and `turn_score_complete` (JUDGED).

**The live model ends with the stream — never with a single later delivery (Sep 2026).**
The live model is a PROGRESS reading and must be superseded by the authoritative one at a
moment the page itself controls. That moment is the stream's own end: `processStream`
returns the turn it DELIVERED (from `final_answer`), and the stream's `finally` calls
`AssuranceDial.complete(sid, turn)` — render the persisted reading (with bounded fetch
retries, because the turn persists *after* `final_answer` is emitted), JUDGED honestly
`pending` until the score lands — or `settle()` when no turn was delivered (error, cancel →
"not recorded", never a pass). Before this, only the Stop handler settled the dial; a normal
completion waited on `turn_score_complete` alone, and once the stream has closed that event
rides the notification channel — one lost delivery left the pending words and the pulsing
phase dot on screen until a page refresh, and the stale live model made the NEXT turn's
`begin()` bail (no dial at all). Two consequences follow: the score paths call
`inject(…, {force: true, scored: true})`, because a settled turn must still be re-rendered
when its score arrives (`_isOnScreen` would otherwise turn the settle into a lock); and a
**bounded score fallback** re-fetches at 45 s and 200 s while no score has landed. The
registry's side of the same contract: an unscored turn reads `pending` only within
`EVAL_PENDING_WINDOW_SECONDS` (180 s) of its own timestamp; past it, the truthful
`absent` ("no score recorded"). A turn with no readable timestamp keeps `pending` — a
legacy shape is not pushed into absence on a field it never carried.

### 6a. A station's two populations, and the one bridge between vocabularies

A station holds **mechanisms** (what ran in-path, able to withhold an answer) and
**measurements** (what grades that guarantee afterwards, able only to report). Its colour
is **worst-of both** — a station that names its measures cannot then ignore them.

Both halves are produced by the registry, not assembled by the renderer:

| Payload key | Producer | Contents |
|---|---|---|
| `assurance.by_station` | `read_turn_by_station()` | the mechanism readings, per station |
| `assurance.measures` | `read_turn_measures()` → `allocate_measurements()` | every measurement the turn produced, all at `MEASUREMENT_STATION` |

**Allocation is one constant, not a computation.** Every scored dimension goes to
`MEASUREMENT_STATION` (= JUDGED); the seven guarantee stations receive none and read
their mechanisms alone. This is the **lane cut** (contract Rule 3b.6): a mechanism
reading answers *what ran during the turn*, a measurement answers *how the finished
answer graded*, and a station that folded both reported a blend of two moments with
nothing saying which half moved.

Two earlier models each lost a dimension in their own way. Under the first, a dimension
travelled as cargo on a mechanism `Reading`, so one whose scorer applied more widely than
its owning mechanism was filtered out of JUDGED as foreign **and** carried by nobody —
counted nowhere, silently. Under the second, allocation was an ownership lookup
(`station_dimensions()`): total by construction, but it routed a post-hoc measurement
into an in-flight station's colour. G1 in `test_assurance_points` now asserts the
surviving property per class — everything in the lane, nothing in the other seven.

`station_dimensions()` survives as a **label**: which guarantee a measurement is ABOUT.
It is the home chip on a Guide entry. It never counts.

**Each allocated record carries three fields, and they legitimately differ:**

- `b` — the band as measured. Never rewritten; this is what the chip reports.
- `st` — `dimension_state()`: what it contributes to JUDGED's fold. Respects Rule 3.0c
  (a coarse check its refining judge cleared contributes `warn` while measuring `danger`)
  and caps a lone judge in danger at `warn`, so the station's colour and
  `analysis.convicting_dimensions` — which does not let a model's opinion convict a turn
  on its own — cannot disagree about the same verdict.
- `bst` — `band_state()`: the band expressed as a state, for the measurement's own colour.

Both resolvers route through **`BAND_STATE`**, the single bridge between the band
vocabulary (`healthy · review · danger`) and the state vocabulary
(`pass · warn · danger · na · pending · unknown`). They overlap on `danger` only, which is
why hand-written conversions kept dropping `review` — see Rule 3c.1 in
[TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md). The dial converts nothing
and the gate asserts it declares no alias; the reverse relation it needs (a mechanism's
state → a band, for Guide context rows) is derived by inverting the same map.

`apply_refinements()` is the one application of Rule 3.0c, shared by `read_turn` and
`read_turn_measures`, so a pair cannot resolve on one path and not the other.

---

## 6b. The severity construct + per-station reporting (PROP-002b)

The registry also owns the **inbox-facing** vocabulary, because a fact must not be
`warning` in the inbox and `danger` on the dial:

- **`DERIVED_EVENT_SEVERITY`** — what severity a derived event CARRIES, derived from
  what happened (code, never settings). `trust_events.derive_turn_events` imports it
  (equal fallback literal, test-pinned — the pin caught the fifth copy drifting the
  day `surface_breach` was added). Includes **`surface_breach`** (OBDA-READ E5, always
  `danger`): a surface-only agent executed free-formed SQL — the structural
  confinement leaked, a hole report about the platform's own enforcement, attributed
  to **BOUNDED** (an authority event) in both station maps.
- **`SEVERITY_CONSTRUCT`** — one level = one behaviour across every surface (word ·
  ring fold · badge · banner tone · demand). `danger` carries the **floor** (never
  filterable); `info` never badges and never folds. Mirrored in `trustGrammar.js`,
  pinned equal by `test_assurance_points`.
- **`station_of_event()`** — event→station attribution, mirrored by the handler's
  `_stationOfEvent` (node-driven agreement test). One event lights one station.
- **`normalize_station_reporting()` / `severity_surfaces()`** — the per-station
  reporting levels (`all` · `warning` · `danger`, admin-assigned in the Trust panel,
  `agent_eval.station_reporting`): the one configurable layer. Levels filter
  **presentation, never creation** — below-level items stay recorded; danger always
  surfaces.
- **`analysis.station_shortfalls()`** — the red⇒item direction of the completeness
  invariant: one ack-able observation per (agent, station) in the danger band, so a
  red segment always resolves to a worked item. Ack keys are content-derived
  (`station_shortfall_<station>::<tag>`, `derived_acks` store).

---

## 6c. The Assurance Guide — one anatomy over the registry

The Guide (`static/js/handlers/qualityDimensions.js`, `window.QualityDimensions`) is the
glossary every assurance surface opens into. It renders what
`GET /v1/assurance/vocabulary` serves and derives nothing of its own: `{entries, stages,
shared_modules, terms}`, where `entries` holds every registry point (`guide_entry()`, the
excluded one marked `excluded`), the eight stations (`station_entry()`), the seven moments
(`eval/stages.stage_entries()`), and the assessable properties
(`agent/assurance/registry.guide_entries()`, each with the dimensions that grade it). The
measures themselves are the client's `DIM`/`LONG` table (see
[AGENT_QUALITY_DIMENSIONS.md](AGENT_QUALITY_DIMENSIONS.md)).

**Every entry kind has a fixed layout** (gate: `test/test_guide_anatomy.mjs`). The rows a
mechanism carries, each from one registry table:

| Row | Source | What it says |
|---|---|---|
| class · measured | `POINT_CLASS` · `measured_as()` | control / boundary / measure / attestation; atomic · composite · unmeasured |
| can | `MECHANISM_FATES` | what it can do to the answer: `stopped · changed · noted · refused`, or *reports only* (an empty tuple is a declaration). Must agree with the point's power (`withhold` names stopped/refused, `attest` names nothing, `constrain` only refused, `alter` never stopped) |
| fires · you can change · fixed · why | `MECHANISM_LIMITS` via `limits_of()` | structured: `fires {when, then, also, ignores, none}`, `tunable`/`fixed` as setting → effect pairs, the folded reasoning |
| properties | `POINT_PROPERTIES` | the properties it acts on, each opening its own entry |
| modules during the turn | `consulted_properties()` | the subset of its properties a gate consults **in flight** — `POINT_PROPERTIES` ∩ `agent/assurance.INFLIGHT_CONSUMERS`. The same function feeds the dial's `decides` mark (`active_modules`), so the Guide and the dial cannot disagree |
| escalates to | `POINT_JUDGES` | the judges it consults in-path (grounding gate → `groundedness`, `scope_faithfulness`; honesty gate → `grain_faithfulness`) |
| graded by | `POINT_DIMENSIONS` | the measures *about* it (a label; counting is JUDGED's) |
| weights | `POINT_WEIGHTS` | trailing trust only: the checks the window aggregate weights, read from `trust_behavior.DEFAULTS["trust_dims"]` |
| not graded because | `UNGRADED_REASON` | `by_design` vs `gap` — a reader must be able to tell a finished design from an unfinished one |

A station entry carries its registry row (`STATION_TABLE`: label · character · question ·
lane), its `controls` (every member, excluded ones too), its labelled `dims`, and three
`reads` rows from `STATION_READS` (on a turn · on the ring · where its checks count); JUDGED
additionally carries `counts` — the seven moments rather than a wall of chips.
`GUIDE_TERMS` defines every technical word a row may use (served as `terms`; gate
`test_assurance_points` [T]).

**Nothing stores a parent.** Each relation is served once, on the parent, as its child list
(a station's `controls` and `counts`, a mechanism's `dims` · `properties` · `weights`, a
moment's measures); `QualityDimensions.parentsOf(k)` reads it backwards. The eight
guarantees are the roots. A member names its guarantee in its kind's verb
(`MEMBER_VERBS`: control *upholds* · boundary *bounds* · attestation *attests* · measure
*evidence for*). Gate: `test/test_guide_parents.py`.

**Membership is one partition.** Every mechanism is a member of its guarantee; every scored
measure is a member of JUDGED (`allocate_measurements`, §6a). The Guide and the dial show a
guarantee's members as one group (*members · N — the worst of these colours X*); the measures
graded *about* that guarantee fold apart as *not members — colour JUDGED*, and JUDGED's own
members group by moment.

---

## 7. Files

| File | Role |
|---|---|
| `src/trusted_data_agent/agent/assurance_points.py` | the registry + `read_turn()` — the one ladder |
| `src/trusted_data_agent/agent/assurance_tally.py` | per-turn contextvar tally for context-less seams |
| `src/trusted_data_agent/eval/gate_outcome.py` | lifts gate outcomes onto the eval span |
| `static/js/handlers/assuranceDial.js` | the renderer (derives nothing) |
| `api/rest_routes.py` | `GET /v1/assurance/vocabulary` |
| `api/routes.py` | emits `assurance.by_station` + `assurance.measures` on the turn |
| `static/js/handlers/trustGrammar.js` | `BAND_STATE`, `STATIONS`, `SEVERITY_CONSTRUCT`, `POSTURE_STATE` (JS mirrors, pinned equal to Python) |
| `static/js/handlers/qualityDimensions.js` | the Assurance Guide (§6c) — renders the vocabulary, `parentsOf`, `MEMBER_VERBS` |

**Gates:** `test/test_assurance_points.py` (incl. G1 per-class totality + the
cross-language `BAND_STATE` pin) · `test_assurance_tally.py` ·
`test_assurance_integrity.py` · `test_gate_outcome.py` · `test/test_assurance_dial.mjs`
(incl. G2 the review band on every station, G3 no band conversion) ·
`test/test_quality_card_partition.mjs` · `test/test_guide_anatomy.mjs` · `test/test_guide_parents.py`.

---

## 8. Adding a point

1. Declare it in `REGISTRY` with its station, label, power, reader and (if conditional)
   `applies_to`; then give it a row in `POINT_CLASS`, `MECHANISM_FATES` and `MECHANISM_NOTE`
   (its Guide entry), and `POINT_DIMENSIONS` / `POINT_PROPERTIES` / `UNGRADED_REASON` as
   they apply. A gate with no row fails the build; so does an unclassified point or a fate
   that contradicts its power.
   If it streams progress, add it to the dial's `LIVE_STATION`.
2. If its seam has no turn context, report through `assurance_tally` — do not thread a
   turn handle through every caller.
3. If it can act on the answer, make sure its outcome reaches the span
   (`gate_outcome.gate_attributes`), or a measurement surface will read clean over a turn
   it withheld.
4. Add the assertion. A rule stated but unmeasured is a rule that will be broken — this
   contract has been breached repeatedly by code that predated its enforcement.
5. Give the live trust campaign a case that reaches it on every class it applies to:
   `test/trust_campaign/coverage.py --strict` (in the trust battery) fails while a
   registered point is reachable by no case (`.claude/skills/trust-campaign/SKILL.md`).
