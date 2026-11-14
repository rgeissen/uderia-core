# Trust Status Communication — the casebook

**This is the casebook, not the contract.** The contract is the one-page core,
[TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md): ten rules, each naming the
gate section that enforces it. This file holds every rule as it was written, with the
incident that produced it, **verbatim and with its original numbering** — so a reference to
`Rule 3bb.3` or `Rule 8.2c` anywhere in the code or the changelog still resolves here.

Read the core first. Come here when you need the *why* behind a rule, the history of a
breach, or the fine-grained form a rule takes on one surface. Nothing below was deleted in
the split (TRUST_CIRCLE_LEGIBILITY.md W7); the one-page core is a restatement, and where the
two disagree the gate decides — `test/test_trust_circle_grammar.mjs` asserts every core rule
names a live gate section.

> Amended by TRUST_CIRCLE_LEGIBILITY.md (Oct 2026): a chosen posture reads **`watching`**
> (quiet green, hollow — never amber, never dashed) rather than amber — Rule 4.4 extended,
> §8's residue resolved; the eight
> stations live in ONE registry (`assurance_points.STATION_TABLE` ↔ `trustGrammar.STATIONS`)
> that carries each station's question and lane; the verdict line is a new zone; the
> explainer keeps only live rows and routes the method to the Guide. Where a passage below
> says observe/annotate read *worth a look*, the core and the gate now say *watching*.

**Enforced by** [`test/test_trust_circle_grammar.mjs`](../../test/test_trust_circle_grammar.mjs).
**Visual tokens** live in [`static/css/trust.css`](../../static/css/trust.css).

---

## 0 · Why this document exists

The grammar is a cross-cutting convention, and an unwritten convention has already
failed twice in this codebase:

- **Five hand-rolled copies** of the posture-mode ladder in the profile editor. One
  omitted `review`, so a stored `review` *displayed* as `observe` and was silently
  downgraded on the next save — a protection weakened by opening and saving a form.
- **A hand-rolled `.some()` aggregation** that reported the best member as the state
  of a whole estate: 1 acting agent of 10 rendered **green**.

Both are the same failure mode. The rules below exist so the third instance can't
happen, and the test exists because a document explains a contract while only a test
enforces one.

---

## 1 · The two attributes

Every station reports **two orthogonal things**. They are never merged into a single
reported value.

| Attribute | Question | Auditor's term |
|---|---|---|
| **Configuration** | Is this control set up so it *would* work? | design effectiveness |
| **Execution** | Did it *actually* work over the window? | operating effectiveness |

The failure modes are opposite and demand opposite responses: well-configured but not
operating is **drift**; operating without configuration is **luck**. A single blended
verdict tells you neither, which is why auditors report them in separate columns and
why we do too.

**Rule 1.1 — Both attributes are always present, in a fixed order: configuration
first, execution second.**

**Rule 1.2 — A missing side renders `—`. It never collapses.** A vanishing side is
how a configuration value gets misread as a runtime figure. One station legitimately
has a side absent:

- **SIGNED** has no configuration dial — signing is an *act*, not a setting.

(BOUNDED briefly had a measurement-derived execution reading, when the retired
guarantee-axis partition re-homed four tool-conduct dimensions to it. Rule 3b.6 moved
every measurement to JUDGED, so BOUNDED again reports what its mechanisms did and its
tool-conduct dimensions are *labelled* BOUNDED while being *counted* at JUDGED.
Autonomous denials are still not aggregated; where nothing was measured the side
renders an honest dash.)

Fabricating an attribute to create symmetry is worse than an honest dash.

**Rule 1.3 — Never render the size of the scope as a value.** Three stations once
showed `profiles.length` / `states.length`, which is always `1` when an agent is
focused: a constant that reads like a measurement. The test fails on any bare `1`.

---

## 2 · The four zones

The panel has four zones. Each does exactly one job, and they never swap.

| Zone | Job | Rule |
|---|---|---|
| **value** | the reading | `configuration · execution`, fixed order, `—` never collapsed |
| **headline** | the consequence | what this state means for the agent's *answers*, in plain language |
| **facts** | the detail | four slots, fixed order (§3) |
| **actions** | what can be done | revealed when the station is **pinned** (§4b); derived from state |
| **cta** | where to go | the navigation route; never duplicated in `facts` |

---

## 3 · The four hover slots

Fixed order, always all four. Rows are **structured, never pre-formatted strings** —
`{ k: label, v: value, d: qualifier, t: 'read' | 'reason' }` — because a renderer handed
a finished sentence has nothing left to lay out. That is exactly how the panel once
became four lines of identical grey prose with the actual data buried mid-sentence.

| Row | `v` (the anchor) | `d` (the qualifier) |
|---|---|---|
| **Configuration** | the setting — `review`, `unscoped`, `—` | only what the value cannot say |
| **Execution** | the measurement — `4/4`, `0 open`, `—` | which dimensions fell short |
| **Why** | `<attention word> (<driver>)` | `— ` + the rule that produced the colour |
| **Evidence** | *(none)* | the named instances, or an explicit absence |

`t` splits the two **readings** from the two pieces of **reasoning**; the renderer draws
a hairline between them and gives `v` the visual weight, because the two attributes are
the reason the panel exists.

**Rule 3.0 — No row may restate the headline.** The headline carries the *consequence*
once. A Configuration row that repeats it produced the same sentence twice, adjacent —
the panel's most visible defect. Each slot has a distinct job:

| Slot | Job | SIGNED, attested |
|---|---|---|
| value | **what it is** | `attested` |
| headline | **so what** — the consequence for the answer | *"Content-bound and offline-verifiable…"* |
| qualifier (`d`) | **what was recorded** — the mechanism | *"both halves vouched for — the publisher signature, and this instance's resolved model, tool catalog and connector opt-ins"* |
| `Why` / `Evidence` | the **rule**, and the **instances** | *(exempt — restating the rule is their job)* |

This rule is older than its enforcement, and it has been broken twice since — first by
echoing the **wire string** (`deployment_unattested`, under a value already reading *"not
attested here"*), then, while fixing that, by echoing the **headline in paraphrase**. A
rule stated but unmeasured is a rule that will be broken; the gate now rejects a
qualifier whose content words are >70 % contained in its own headline, rejects any two
slots on a panel carrying identical text, and rejects any `snake_case` token reaching a
slot at all — swept across **every** station, because the first version of that sweep
covered SIGNED only and GROUNDED went on printing `grounded_overlap` at users.

**Dimension names come from `QualityDimensions.label()`** (`qualityDimensions.js`), the
same source the Quality card and roster use. One dimension must not have two names
depending on which panel you are reading.

**Rule 3.0b — `Evidence` carries the INSTANCES, never a second copy of the aggregate.**
`Execution` says *which dimensions fell short*; `Evidence` says *which turns, and how
they failed*. GROUNDED shipped with both slots byte-identical because one variable fed
two rows. The per-turn series answers what the aggregate cannot — **was a turn caught by
several checks, or only one?** — and that distinction changes the response: a turn failing
two grounding checks is a corroborated failure, while turns failing only a coarse lexical
check on a well-paraphrased answer are usually the check disagreeing with the judge.

**Rule 3.0b — The attention word belongs to the chip; in `Why` it earns its place only
by naming the driver.** The settled form is `worth a look (execution)` — the driver in
brackets after the attention word, and the explanation prose set off with an em dash
(`— …`), so the anchor and the explanation cannot run together as one phrase. Prefer
that over a sentence that repeats what the chip and the colour already said.

**Rule 3.0c — When two measures of ONE property disagree, report the disagreement.
Never resolve it by weighting, and never silence one of them.**

Some dimensions measure the same thing twice at different resolutions: `grounded_overlap`
counts literal token overlap, `groundedness` reads meaning and — by its own description —
*refines* it. On a paraphrasing model over technical prose they disagree systematically:
campaign 1 measured the coarse check red on 12 of 14 turns while the judge cleared the
same answers, so GROUNDED read `danger` permanently. **A station that can never go green
gets ignored** (§8, the same reasoning as `unsigned`).

Two obvious fixes are both dishonest:

- **Weighting the judge** builds a composite with invisible arithmetic — Rule 4.3 permits
  a composite *only* because the drill-down names its driver — and makes the station's
  colour depend on whether a **sampled** judge happened to run that turn (a Rule 3.5
  denominator problem wearing a fix's clothes).
- **Demoting the pre-signal to informational** silences a check that runs on *every* turn
  in favour of one that runs on some. Judges are stochastic; removing the cross-check is
  how a lenient verdict becomes a green station.

Both answer *"which measure is right?"*, which is the wrong question. **The disagreement is
more informative than either reading alone**, and Rule 3.0b already said so — *"a turn
failing two grounding checks is a corroborated failure, while turns failing only a coarse
lexical check on a well-paraphrased answer are usually the check disagreeing with the
judge."* This rule completes that sentence at the station's reading.

| Coarse check | Its refining judge | Station reads | Because |
|---|---|---|---|
| fail | fail | **needs you now** | corroborated — two independent methods agree |
| pass | fail | **needs you now** | the judge caught what tokens cannot; it is the better measure of meaning |
| fail | **pass** | **worth a look** | *disputed* — heavy paraphrase, or a lenient judge. A human should glance |
| pass | pass | quiet | |
| fail | *(did not run)* | **needs you now** | nothing refined it; the coarse check stands alone at full weight |

No new state and no weighting: the existing four words already carry it. Three constraints
make it honest:

1. **The measurement is never rewritten.** The dimension reports the band it measured (`b`,
   what the chip shows); a separate *station contribution* (`sc`) is what the station folds
   in. Changing `b` to calm a station would be lying about a real measurement.
2. **Nothing is silenced.** A disputed turn still colours the station amber, still appears
   in the routing summary, and is **named** in the qualifier and Evidence — *"N turns
   flagged by the coarse lexical check and cleared by the judge that refines it"*.
3. **The pair is declared, never inferred** (`REFINEMENT_PAIRS`), and a pair must live on
   ONE station — a refinement spanning two would move attention between stations.

*Why this is the transparent choice, concretely:* had the surface reported the
disagreement, the C5 scorer defect would have announced itself instead of needing a
forensic investigation. A rule that makes a latent defect self-reporting is worth more than
one that makes a number look better.

**This does not weaken worst-of (Rule 5.1).** That rule governs aggregation *across
agents*, where one agent's health cannot excuse another's exposure. This governs two
measures of the same property on the same turn, where a disagreement is evidence *about the
measures*. The two are easy to conflate and must not be.

**Rule 3.3b — A station's subtitle names its character, and agrees with its
derivation.** The subtitle is the only reading visible before anyone opens a panel, so it
carries what makes *this* station's verdict what it is — *"a human decides"*,
*"authority, borrowed"*, *"receipts, not promises"* — never a property every station
shares. "Process, end to end" is true of all eight and therefore separates none of them.

It must also agree with the derivation two slots below: JUDGED's *"execution quality,
graded"* names exactly what the explainer teaches — every measurement the turn produced,
grouped by the moment it was taken — so the glance and the drill-down cannot diverge. A subtitle promising something the note does not teach is
doctrine (Rule 3.3) wearing a reading's clothes.

Plain prose only — no digits, no abbreviations, no textspeak. A subtitle carrying a figure
would be a measurement with no denominator (Rule 3.5) frozen into a label that never
updates; and one token of textspeak on a compliance surface undoes more than it saves.
Where a station has two true things to say, the second one belongs where it has room for
its numbers: SCORED's deterministic-vs-judge split (43 of 52) moved to the roster act
description rather than being crushed into four words.

**Rule 3.4 — A rate anchor's qualifier EXPANDS it in words, with its unit.**

    <n> of <N> <unit> <predicate>   (+ a percentage where one helps)

`<unit>` is the thing being counted — turns · items · verdicts · agents — and is
**mandatory**. A dimension name may qualify the *predicate*; it may never stand in for
the unit. The qualifier must never restate the anchor's digits as its whole content
(Rule 3.0), and never state the anchor's **complement**: `2/3` beside *"1 of 3 show
shortfalls"* puts two numerators for one population on one line.

A non-rate anchor (SIGNED, BOUNDED) keeps a state word plus a mechanism qualifier, and
says explicitly when nothing was measured. Consistency here is of *grammar*, not of
shape — never fabricate a ratio to make a station match its neighbours (Rule 1.2).

SCORED is the reference: `9/11` · *"9 of 11 turns clean (82%)"*. It is the only rate
station no one has misread. The two that dropped the unit are exactly the two that
misled this product's own author, twice in one sitting — *"9/11 · Grounding Overlap
9/11 passed"* was read as *"1 turn HAD an overlap"*, the precise inverse of the truth.
A ratio without its unit is not a shorter sentence, it is an ambiguous one, and
ambiguity on a trust surface is indistinguishable from a lie.

**No `(s)` placeholder ever reaches a rendered surface** — use the plural helper. This
was fixed once at one site and recurred at nine others, so the gate now scans every
slot of every station in three fixtures rather than asserting a single string.

**Rule 3.1 — Evidence is mandatory and is never omitted.** This is structural, not
stylistic. REVIEWED once reported *"the Assurance Inbox is clear — every layer quiet"*
while a danger-severity session-sentinel item sat in that very inbox: the counter was
correct (derived items carry no disposition), but there was no slot in which the
finding *could* appear. A mandatory Evidence slot makes that class of omission
impossible rather than merely discouraged.

**Rule 3.2 — When a slot has nothing to report it says so explicitly.** An absent slot
must never be mistakable for a clean one.

**Rule 3.3 — No doctrine in the hover.** Product claims ("graded in code first — 52
dimensions", "receipts export from every capsule") are true but they are not readings,
and they crowd out live signal. They belong in the story link and the manual.
*(Measured: the gate sweeps every slot of every fixture for the banned phrases.)*

**Rule 3.5 — A figure carries its own denominator.** A rate rendered over a different
population than the one its sentence names is a lie by juxtaposition (#62); a per-turn
union figure needs a stable denominator, not one that moves per turn (#62b). Never
present a numerator against a denominator computed elsewhere.

**Rule 3.6 — A structural zero is not a measurement.** *"0 out-of-scope references"*
from a class that structurally cannot make data calls (#60), or `on · 1` where the `1`
is the scope size, presents a constant in a measurement's clothes. Say the structural
fact ("this class makes no data calls") instead of dressing it as a clean score.

**Rule 3.7 — No vacuous clean.** A station must not read "clean" from an empty
evidence set (#66): *no failures among zero examined turns* is absence of evidence,
not evidence of health, and renders as absence (Rule 1.2), never as a pass.

*(These three were measured by the gate before they were stated here — a new station's
author reading only this contract would not have known them. Now both.)*

---

## 3b · The derivation explainer (the "?")

The four slots say **what** the reading is and **why**. They deliberately do not say **how**
the figure was computed — most readers don't need the method, and a slot that carried it
would be the longest, least-read line on every hover. But a figure whose derivation cannot
be inspected is an assertion, not evidence; and the ring's numbers count *turns* while the
Quality card's count *checks*, which reads as a contradiction until someone explains it.

**Rule 3b.1 — Every station can explain its own figures.** A `?` on the pinned panel opens
a distinct zone — **never a fifth slot** (Rule 3.0 forbids restating a slot) — carrying:
*measured over* (the population, which is what actually resolves the confusion),
*configuration*, *execution*, the contributing dimensions, and an optional note.

**Rule 3b.2 — A station with no quality-dimension input says so.** Two of the eight
(SIGNED, REVIEWED) derive from attestations and the review queue — not from scored
turns. Leaving that unsaid implies the ring is a view onto the Quality card, which it
is not. (BOUNDED and PROVEN joined the dimension-fed stations when the partition
re-homed the tool-conduct and record-soundness checks to them.)

**Rule 3b.3 — Where dimensions contribute, they are named and reachable.** Each renders as
a real button that opens the **existing Assurance Guide**, scoped to that station's set and
carrying each one's live band. The ring never defines a dimension itself: a second glossary
is the drift this document exists to prevent.

**Rule 3b.4 — A dimension the agent's class never produces is shown, not dropped.** It
renders dashed and inapplicable. Silently omitting it would overstate what the station
watches; showing it as a pass would be worse.

**Rule 3b.6 — Measurement is a LANE, not a guarantee attribute. Every scored
dimension is counted at JUDGED; the other seven stations read only their
mechanisms.** The cut is **time**, not subject. A guarantee station answers *what did
the machinery that upholds this promise DO during the turn* — a gate that escalated, a
guard that redacted, a boundary that denied a tool. A measurement answers *how did the
finished answer grade* — taken after it shipped, by a scorer that changed nothing. Those
are two different questions asked at two different moments, and a station that folded
both into one colour was reporting a blend of the two with nothing on screen saying which
half moved. So the measurement lane is counted in one place — `MEASUREMENT_STATION`
(`assurance_points.allocate_measurements`) — and that place is **JUDGED**, whose whole
subject is *"execution quality, graded"*.

**The station survives as a LABEL.** `station_dimensions()` still answers *which
guarantee is this measurement ABOUT* — `grounded_overlap` is about GROUNDED — and that
answer is worth showing: it is the home chip on a Guide entry and the reason a reader
knows where to go next. It simply no longer **counts**. Two jobs that were one function
are now two: *labelled by* a guarantee, *counted at* the measurement lane.

The one place everything comes together is still **TRUSTED**: a recency-weighted blend
over *all* the evidence — itself a partition (trust-critical dims carry their configured
weight, every remaining dim carries the rest; no dim in both terms) — capped by integrity.

Four consequences the explainers must state, because each is a reading that surprises:

- **a guarantee station's execution side is about its mechanisms alone.** A red
  `grounded_overlap` no longer colours GROUNDED. It colours JUDGED, and GROUNDED reports
  what its four gates did. Both are true; they are answers to different questions.
- **JUDGED is not a residue and no longer has one.** It counts every scored dimension the
  agent's class produced — the honest total, not a leftover. (The residue existed only
  because the other stations took a share first.)
- **the measurement lane has its own axis** — the five **moments** of a turn (Plan ·
  Retrieve · Act · Compose · Deliver) plus *The run* and *Flow run*, declared in
  `eval/stages.py`. A measurement is grouped by *when in the turn it was taken*, which is
  a property of the measurement, where the guarantee was a property of something else.
- **a measurement's key set is per-class**, computed per cell from what the agent's class
  actually produced, never a hand-list.

*(This replaces two earlier models, and the failure of each is why the rule reads as it
does. Under the **union** — the original — one failing dimension coloured two stations, so
one root cause read as two problems and was triaged twice. Under the **guarantee-axis
partition** that replaced it, each dimension was counted at exactly one station, which
fixed the double count but put a post-hoc measurement inside an in-flight station's
colour: a gate that did its job all turn could read red because a scorer graded the
answer poorly afterwards, with nothing distinguishing the two. The lane cut keeps the
one-count property and adds the one the partition lacked — one station, one moment in
time.)*

**Naming — the label round-trip.** The station was renamed judged→SCORED (Aug 2026: at
the time 43 of 52 dimensions were deterministic code — 44 of 53 since `surface_violation`
landed; the platform also has *literal* judges) and then
BACK to JUDGED once TRUSTED's value became a conduct *score* — "Scored" carried two
meanings on one ring. The **wire key stays `scored`** permanently: spans, payloads and
Guide keys are persisted, and a wire rename would orphan every recorded turn.

**Rule 3b.8 — Confidence is not attention.** Colour belongs to the rate's band (Rule 4.1).
A thin sample is marked by **shape** — the fraction form, a dimmed bar, a dashed chip edge
— never by borrowing amber, which puts a perfectly healthy dimension under a warning tint
and spends the loudest channel on something that is not a call to action. This mirrors the
platform's existing "no/weak grade" vocabulary (`qd-status` distinguishes not-set /
abstained / not-applicable by border style, not colour).

**Rule 3b.9 — Below ~3 samples, show the fraction, not the percentage.** `0%` and `100%`
are the only values one verdict can produce, and both read as confident rates: at that
sample size the *percentage form itself* is the over-claim, and no badge beside it fully
repairs that. `0/1` cannot mislead, carries its own denominator (Rule 3.5), and the visual
break from neighbouring percentages is itself the signal that the row is a different kind
of thing. Where the fraction is shown, the `n=` badge is dropped — it would restate it
(Rule 3.0).

**Rule 3b.13 — Configure the numbers, never the semantics.** Where a band boundary sits is
a judgement an operator may legitimately hold (green at 0.80 or at 0.95). What the system
*does* is not: `enforce` can withhold an answer and `observe` cannot, and an estate is
governed by its weakest member. Exposing the first is good governance; exposing the second
would let a settings page declare an observe-only gate "green" or turn worst-of into
best-of — manufacturing the false green this document exists to prevent. Band floors are
therefore admin-configurable and resolved from ONE source; the mode ladder and
`_postureAggregate` are not configurable at all.

**Rule 3b.7 — Every rendered channel is in the legend.** If a surface encodes meaning in
colour, shape, opacity, a badge or a marker, the legend names it. The roster bar dimmed
itself to 45% to say *"the evidence is thin"* and said so nowhere — a signal nobody can
read is worse than no signal, because the viewer knows something was said and cannot tell
what. Add a channel and its legend entry in the same change; the gate asserts them together.

**Rule 3b.5 — The explanation is built from the values it explains.** Each derivation is
assembled from the same variables that produced the figure (the same key list, the same
window, the same union), so prose cannot drift from the number above it.

**Why the `?` is pinned-only:** Rule 4b.5 — a hover is a peek. The unpinned hint names the
explainer so it stays discoverable without putting a control under a passing pointer.

---

## 3bb · Intervention and quality are two dimensions, not two views of one

**Rule 3bb.1 — What a control DID to an answer and how well an answer GRADED are
different readings. Never let one stand for the other.**

The station panel already states the halves — mechanisms *"run DURING the turn and can
withhold or alter the answer"*, measurements *"grade the same guarantee AFTER the turn…
nothing here can open or close a gate"*. The consequence, which was not written down and
cost an operator a long conversation, is that the two produce **independent** readings:

| | **intervention** | **quality** |
|---|---|---|
| driven by | mechanisms | measurements |
| when | in-path, during the turn | after delivery |
| what it changes | the answer the user got | nothing |
| shape | binary per turn — a control acted, or it did not | banded per dimension, aggregated to a rate |
| a bad reading means | a control had to step in | the delivered answers grade poorly |

They are **orthogonal in practice, not just in theory**. Measured across one real agent's
18 turns:

```
a mechanism acted  + measurements flagged :  0
a mechanism acted  + measurements clean   :  2
nothing acted      + measurements flagged :  5   ← nothing caught these
nothing acted      + measurements clean   : 11
```

Every intervention landed on a turn whose measurements were otherwise clean, and every
measurement failure landed on a turn no control touched. A single number cannot carry
both, and the four cells mean four different things — the third is the one an operator
should care most about, and it is precisely the cell an intervention count would miss.

**Rule 3bb.2 — The live pass rate is a QUALITY reading. Say so where it is shown.**
It is measurement-driven, which is correct: a mechanism-driven rate would have read
**89%** on the agent above, because 16 of its 18 turns had no mechanism act at all — the
more comfortable number and the less honest one. But "pass rate" and "failed" are
validity words, and unqualified they invite exactly the mechanism reading they are not.

**Rule 3bb.3 — The one place the axes cross is a deliberate, documented exception.**
A runtime grounding **block** counts as a failure in the quality rate, so that a turn
whose answer was withheld can never read clean. That is the intended behaviour and it is
a real crossing of the two axes, so it must be stated wherever the rate is explained —
not discovered. Two honest consequences of it:

- On a blocked turn the delivered text is Uderia's own withholding notice, so the
  delivered-text scorers **abstain** (`platform_authored_answer()`). The turn is then
  counted as a quality failure on evidence the platform deliberately declined to gather.
  Both blocked turns in the sample had **zero** failing gate-eligible measurements; the
  entire failure was the fold.
- An **inbound refusal** is treated the opposite way: `eval.gate_only` spans skip the
  quality denominator entirely — *"it must reach the gate record WITHOUT diluting a
  quality denominator it was never measured against."* So one intervention is excluded
  from the quality axis and another is folded into it.

The asymmetry is accepted (a refused request never produced an answer at all; a blocked
one did produce a turn the user saw a refusal for). It is recorded here because an
undocumented exception to a stated rule is indistinguishable from a defect — the reader
cannot tell whether the crossing was chosen or overlooked.

**Rule 3bb.4 — A user's VOTE is a third axis. It is published beside the measurements
and never inside one.**
Intervention answers *what a control did*, quality answers *how the answer graded*, and
a vote answers *what the person wanted* — three axes, and a vote is the only one the
platform did not produce. It is subjective, self-issued and unverifiable, so it may
never be folded into `pass_rate`, `turn_failed` / `gating_failures`, a quality band, a
station colour, the TRUSTED composite, or any scored dimension. It may sit **next to**
all of them.

- Rendered as a **word** chip (`upvoted` / `downvoted`) in the muted register — colour
  on these surfaces is attention priority, and a preference is not an attention level
  (Rule 9.4a still applies: never colour alone).
- The reading is **absent at zero votes**, not a decorative `0` (Rule 3.7).
- Scoped to the voter. In system scope an admin sees **no** vote reading rather than a
  zero that would read as *"nobody rated this"* — a false absence is worse than none.
- **The disagreement count is why the reading exists.** A turn the scorers passed that
  the user downvoted (or the reverse) is the one place a human says the quality bands
  are miscalibrated for this deployment, so it is counted, named, and pointed at the
  place those bands are tuned.

Contract:
[FEEDBACK_SIGNAL_ARCHITECTURE.md](FEEDBACK_SIGNAL_ARCHITECTURE.md).

---

## 3c · A band is not a state, and a station owns its measurements

Two rules that look like implementation detail and are not: each was violated by shipped
code, and each violation made a real finding invisible on the instrument that exists to
show it.

**Rule 3c.1 — A quality BAND and a turn STATE are different vocabularies. Exactly one
declaration bridges them, and no renderer may convert.**

```
band  (what a measurement scored)   healthy · review · danger      + absent (abstained)
state (attention priority)          pass · warn · danger · na · pending · unknown
```

They overlap on exactly one word — `danger` — and that coincidence is the trap. A
conversion written by hand as `band === 'healthy' ? 'pass' : band` *appears* correct:
`healthy` is handled, `danger` passes through unchanged, and only `review` — the single
value where the two genuinely differ — falls through as an unrecognised word. Fed to a
rank table it then scores below `na`, so **an amber measurement can never raise anything.**

That is not hypothetical. Three such aliases lived in `assuranceDial.js`. GROUNDED read
*"quiet"* directly above its own `context_utilization 0.56`; the chip for that measurement
rendered grey because `ad-meas-review` matched no CSS rule; and it sorted last in a row
specified worst-first. One root cause, three wrong readings, through 387 assertions —
because every dimension fixture in the gate used `healthy` or `danger`.

So: `assurance_points.BAND_STATE` (Python) and `trustGrammar.BAND_STATE` (JS), pinned
byte-equal by `test_assurance_points`. The registry resolves each measurement's
contribution server-side and ships it already in the state vocabulary; the gate forbids
any band conversion in the dial. Only the three bands that *carry* a measurement are
listed — an abstention is the absence of a band, and both resolvers default to `na`.

**Two contributions, deliberately, because Rule 3.0c makes them differ.** Each allocated
measurement carries:

| field | meaning | consumer |
|---|---|---|
| `b` | the band, exactly as measured — never rewritten | the chip's label / Guide context |
| `st` | what it contributes to its STATION (`dimension_state`) | the station's worst-of fold |
| `bst` | the band as a state, for the measurement's OWN colour (`band_state`) | the chip's class |

A coarse check its refining judge cleared measures `danger` and contributes `warn`: the
station goes amber, the chip stays red, and neither is edited to agree with the other.
Collapsing them would either lie about the measurement or overstate the station.

**Rule 3c.2 — A measurement is allocated to the lane, not delivered by a mechanism.**

Allocation is one constant: every scored dimension the turn produced goes to
`MEASUREMENT_STATION`. Totality is not something to verify — there is one destination —
so what the gate asserts is the *other* half: that the seven guarantee stations receive
none, per profile class (G1).

Two earlier models each lost a dimension in their own way, which is why allocation is now
a constant rather than a computation. Under the first, a dimension reached its station as
cargo on a mechanism `Reading` — making coverage an *emergent agreement* between two
independently maintained lists (`Scorer.applies_to` vs `AssurancePoint.applies`), so a
dimension whose scorer applied more widely than its owning mechanism was filtered out of
JUDGED as foreign **and** carried by nobody: counted nowhere, silently. Under the second,
allocation became an ownership lookup — total by construction, but it routed a post-hoc
measurement into an in-flight station's colour. The lane cut ends both: one destination,
one moment in time.

The corollary is that no surface may re-derive which measurements a station holds. The
renderer used to union them out of the station's mechanisms behind a `seen` set — a
hand-rolled restatement of ownership, and the exact code that would have dropped an
orphan. The set travels **on the station** and every consumer reads it from there.

**Rule 3c.3 — The verb follows the state.** A station driven amber by a measurement must
say so in the register of amber. "…is failing" was written when only `danger` could reach
that sentence — because `review` could not fold at all — and it convicted a review band
the moment amber started arriving. A review band asks for a look; it does not convict.

---

## 4 · Colour means attention, not a dimension

**Rule 4.0 — One grammar, four channels, on EVERY surface (PROP-002).** The old
two-idiom split (verdict surfaces vs triage surfaces) is retired: an operator had to
know which idiom a surface spoke before an amber element meant anything. Every trust
surface — ring, dial, mini glyph, capsule, Quality chips, Assurance Inbox — now
speaks one grammar in which each visual channel has exactly one job:

| Channel | One job | Values |
|---|---|---|
| **colour** | attention priority — "how urgently should a human look here" | quiet · worth a look · needs you now · (absence family: grey) |
| **shape/fill** | the state family, legible without hue (Rule 9.4) | pass solid · warn half-fill · danger 2px outline · na sparse dash · pending dense moving dash · unknown ultra-sparse dash |
| **ticks** | engraving — what the record holds | I = detail engraved · II = a gate **acted** on this subject |
| **accent orange** | actionability — "you can act here / an act is owed" | hover · focus · the plus · the **owed dot** |

A *verdict* ("blocked", "masked", "parked", "clean") is carried by the **word on the
chip**, never by the colour alone. The state set is SIX everywhere (`pass · warn ·
danger · na · pending · unknown`); a renderer fed a state it has no class for is
committing fact loss (the mini glyph once drew `pending` as an invisible segment).

**Rule 4.0b — The altitude rule.** What the NUMBERS mean is decided by altitude, not
by surface: estate = worst-of `x/y agents` · agent = mode · windowed rate · window =
`n/N turns`, boundary named · **turn = events** (states are outcomes, ticks are acts,
nothing is a rate). Drilling down an altitude is always one gesture, same eight
stations, same order.

**Rule 4.1 — On the Trust Circle, glyph colour encodes attention priority.** Its
semantic is literally *"hover here, and here's how urgently."*

On a glance surface with eight stations the only decision the consumer makes is *where
to look next*. Colour is the highest-salience channel, and it is spent on the user's
actual decision — not on encoding one of the two attributes.

| Level | Word | Means |
|---|---|---|
| pass | **quiet** | nothing needs you — hover to confirm |
| warn | **worth a look** | one attribute is short of its intent |
| danger | **needs you now** | a detected discrepancy or a failing rate |
| na | **not turned on** | hover to see what it would do |

**Rule 4.2 — Attention is `worst-of(configuration, execution)`.** `na` on one side
neither masks a real reading on the other nor invents attention of its own: an armed
gate with no turns yet is *quiet*; a station with nothing configured and nothing
measured is simply *not on*.

**Rule 4.3 — A composite is legitimate here ONLY because the drill-down names its
driver.** This is the load-bearing condition. A colour ramp is honest only over an
ordinal scale; `(configuration, execution)` pairs have no legitimate ordering, but
attention priority does. Without the named driver the composite is opaque and you are
back to a false green — so `_assemble()` writes the driver unconditionally and no call
site can omit it.

The distinction that keeps this honest:

- ❌ *"green = this control is fine"* — a composite **verdict**; hides which attribute failed
- ✅ *"green = nothing here needs you right now"* — a **triage** signal whose whole meaning is "the hover has the details"

**Rule 4.4 — `danger` is reserved for a detected discrepancy or a measured bad
outcome, never for a chosen setting.** `observe` is the documented default; grading it
red would ship every fresh install in alarm and teach operators to ignore red. SIGNED's
`danger` for *changed after sign-off* is correct under this rule — tamper-evidence is a
detected discrepancy, not a preference.

**Rule 4.5 — Green must not mean "don't hover."** If it reads as *nothing to learn
here*, consumers stop exploring and the ring degrades into decoration. Interactive
affordance (pointer, hover, focus ring) applies to **every** station including quiet
ones, and a quiet hover still delivers the full pair.

---

## 4b · Read is a peek; act needs a pin

Hover **previews** a station. Clicking **pins** it: the panel stays put and its
**actions** appear beside the reading that justified them.

**Rule 4b.1 — Acting must not cost you the reading.** A click used to navigate away, so
the two attributes you were acting on left the screen at the moment you acted.

**Rule 4b.2 — Actions are derived from state, never hardcoded per station.** One fixed
action per station cannot serve a control whose needs change with its reading:
`unsigned` needs *sign*, `changed` needs *re-sign* **and** *verify*, `trusted` needs
*verify*. SIGNED's single hardcoded CTA pointed at the posture matrix — a **read-only**
surface — while the guided bottom-up flow (`TrustBadge.signAgentGuided`) existed with no
caller outside its own popover. The ring stated that work was owed and offered no way to
do it.

**Rule 4b.3 — An action is offered only where it can be performed.** No agent focused, a
scope spanning agents, another user's agent (tags are unique only *per user*), or the
trust package absent ⇒ no button. Never a button that fails on click.

**Rule 4b.4 — Navigation survives.** The `cta` renders alongside the actions, so pinning
never removes a route that already existed.

**Rule 4b.5 — Actions render only when pinned.** A hover is a peek; a live *"sign this
agent"* must not appear under a pointer merely passing through.

**Rule 4b.6 — Five dismissal routes, and it is never a trap.** Escape · clicking the
station again · clicking outside · the ✕ control · pinning another station. Focus returns
to the station that owned the pin. Deliberately **not** on `mouseleave` (the pointer must
travel to the buttons — a pin that evaporates en route is the classic hover-menu defect),
not on scroll, and not when an action completes (a long sign-off must not yank the panel
away from its own status line). The panel is **non-modal**: no focus trap, no `inert`.

**Rule 4b.7 — One pin at a time.** Eight open panels is not a UI. A re-render (agent
switch, scope change) drops the pin, so a button is never left over from a different
subject.

**Rule 4b.8 — The leading route follows the driver (#17).** When a station offers two
routes, the reading's driver decides which leads: an execution shortfall points at the
turns, a configuration shortfall at the posture matrix — and the other route stays
beneath it. Reordering routes is honest; deleting one is not (Rule 4b.4).

**Rule 4b.9 — A deeper read is not discarded by a passing pointer.** The pin/peek
ladder has three rungs, not two: narrative → peek → pinned → **explainer**. Opening the
`?` is a deliberate request to read one station's derivation, so a pointer merely crossing
the ring on its way somewhere else must not replace it. Scoped to POINTER peeks only —
keyboard focus *is* the navigation, and swallowing it would leave a Tab-driven reader with
no idea which station they had reached. The rule lives in one pure predicate
(`peekSuppressed`), not inline, so every branch of it is graded rather than assumed.

**Rule 4b.10 — Panel content must never feed back into pointer targeting.** The ring and
its panel share a grid row whose height is the taller of the two. Centre the row and the
ring *translates* whenever the panel's height changes — so a peek shrank the panel, the
ring slid out from under the pointer, `mouseleave` restored the taller panel, the ring slid
back, and the two states oscillated with nothing to settle on. The ring is anchored
(`align-items: start`), which makes panel height and ring geometry independent. Any layout
where what a panel *says* can move what a pointer is *aiming at* has this bug latent in it,
whether or not the current content happens to trigger it.

---

## 4c · Name the artefact, don't just identify it

**Rule 4c.1 — Where a surface asks you to act on an artefact, it names it.** A blocker
read `knowledge repository #2` at the exact moment it asked for a signature — and a
knowledge signature is *earned* (bound to a scan report), so a database id is not
something an operator can judge. `IngredientRef.label` carries the name; the id remains
the fallback so an unnamed ingredient still identifies itself.

**Rule 4c.2 — A name must never move a signature.** `label` is deliberately outside
`composition_hash` (which covers `(kind, live_hash)` only). Naming an ingredient would
otherwise flip every signed agent to 🟠.

---

## 5 · Aggregation across agents

**Rule 5.1 — Estate aggregation is worst-of, never best-of.** An assurance rollup is
governed by its weakest member: the strongest agent does not remove another agent's
exposure, and *"at least one agent is protected"* is not a question anyone asks.

Classify **per agent first**, then aggregate through the one declared policy
(`_postureAggregate`). Never `.some()` over the estate.

```
any danger        → danger      (a detected discrepancy dominates)
all pass          → pass
all na            → na
otherwise         → warn        ("acted / partial" — the honest state for mixed coverage)
```

**Rule 5.2 — The value carries the magnitude, the glyph carries the state.** `2/10`
next to amber and `9/10` next to amber are different situations; the ratio does the job
a single glyph cannot. This is also what makes worst-of survivable — without it, one
laggard in two hundred produces a permanently amber badge, which gets ignored.

**Rule 5.3 — `na` is excluded from attention tallies.** A chosen default must not put
the estate in permanent alarm. The same reasoning keeps derived observations out of the
REVIEWED badge count (they carry no disposition) — while Rule 3.1 guarantees they are
still *shown*.

**Rule 5.4 — Make amber actionable: name the laggards.** `not acting: @IDEAT, @OPTIM
+6 more` beats an accurate number with no route to the fix.

**Rule 5.5 — Unknown is not "off".** An unresolvable reading (e.g. a backend that
doesn't compute `authorityScope`) classifies as `warn`, never `na`. Unknown authority
must never read as *nothing enabled here*, which would understate real exposure; the
Configuration slot then says it could not be resolved.

---

## 6 · One source per rule

Hand-rolling any of these is the defect class this document exists to prevent.

| Concept | Single source |
|---|---|
| Station assembly (value, slots, colour, driver) | `_assemble()` — `agentPerformanceHandler.js` |
| Attention priority | `_attention()` + `_RANK` |
| Pin / dismiss behaviour | `togglePin()` + `unpin()` + programmatic `pin()` (act-then-refresh restore) — `trustCircle.js` |
| SIGNED's state-derived actions | `_signActions()` — `agentPerformanceHandler.js` |
| Which granted actions park for a person (REVIEWED's configuration beside the review-mode gates) | `action_perform.parking_actions` → served as `actionsParking` on `/v1/profiles`; the station reads it and never re-derives posture |
| Carrying out a trust act | `_runTrustAct()` → `TrustBadge.signAgentGuided` / `verifyAgent` / `attestDeployment` |
| Attesting this deployment | `TrustBadge.attestDeployment()` — one implementation, two callers (badge popover + ring) |
| Repainting after an inbox disposition (ack · review decision · quarantine) | `refreshInbox()` → `_repaintInboxConsumers()` — `agentPerformanceHandler.js`. A disposition moves ONE fact (the open-item model); the seam recomputes it once and repaints EVERY reader in place — the list, the badges, the ring (`update()`, mount-guarded), every mini ring. No disposition path renders the inbox itself; a second inline copy is exactly the path that left the ring on the pre-ack state until a page refresh. Gate: `test_trust_circle_routes` |
| **Who** may attest a deployment | `_authorize_deployment_attester()` — `trust_routes.py`. **Ownership, never signer tier**: the deployment half is a statement of intent about a surface only its owner controls, and the lookup is user-scoped so an approver-tier gate could only lock the owner out, never enable a reviewer. Signing off the *artefact* stays `_authorize_signer` |
| Integrity → seal shape | `TrustBadge.META[state].icon`, read via `_integrityGlyph()`; applied by `trustCircle._applyGlyph()` |
| An ingredient's human name | `IngredientRef.label` → joined into `blocking` by `AgentTrust.to_dict()` |
| Estate aggregation | `_postureAggregate()` |
| Reading a persisted gate stamp that carries **no verdict** (`{ran:false, action:"na"}` = the gate abstained · `action:"error"` = it failed) | `assurance_points._read_grounding` / `_guard_reading` (the words), mirrored verbatim by the capsule's `groundingChip()` / `guardChip()` (`answerCapsule.js`) and the strip's `deriveStages` / `guardState` (`trustJourney.js`). An absence reads `na` / `unknown` on a dashed `tc-na` / `tc-unknown` chip — **never** a verdict: a two-branch reader with a fall-through once rendered an abstained gate as *withheld — confirmed drift* under a dial reading all quiet (prod 0c09571e), and its neighbour fell back to *pass* (Rule 3.7). Gate: `test_capsule_stamp_readings` |
| Integrity → station state | `_INTEGRITY_STATE` |
| Posture-mode ladder (backend) | `core/posture_modes.py` |
| A tooltip's SUBJECT | `showTip()` — it builds from `data-dim`, which on a routing element is the DESTINATION, so a module described the property it opens. An element describes what it IS; where it goes is a separate slot |
| The **M** badge — a module | `moduleName()`. A module is not an entry: it has no page to open, so it is MARKED rather than linked, and the NAME routes to the property it assesses (1:1 across all nine). On the property's own lanes row it does not route — a self-route is a dead end wearing a link's clothes. **No arrow glyph**: with modules and siblings in separate rows it sat between two chips and read as *points at the next one*; direction is carried by the hover link, which names the real target |
| Which modules an entry reads | `modulesRow()` over `modulesOf()`. Rendered on every class EXCEPT property, whose `assessed by` row already names them per lane |
| The **S** badge — same assessment | `sharedModule()` over the served `shared_modules` map (module → entries). **Symmetric by rule**: it appears wherever a relation row names a sharer, in BOTH directions. Restricting it to one row made the link one-way — a measurement said *same assessment as this gate* and the gate's own entry said nothing back, the dead-end-one-hop-later this file bans for `relatedChips` |
| Which measurement grades a property | `Scorer.property` declared on the scorer, DERIVED into `property → [dimension]` at the vocabulary endpoint. Never a second hand-kept map — and never inside the substrate, which must not import `eval` |
| A property's displayed NAME | `ENTRY_NAME[k]` ← the served `property` field. The entry key carries a `property_` prefix for this registry; rendering it puts an implementation detail where every other class shows its real identifier |
| The modules behind a mechanism row | `assurance_points.active_modules()` — re-assessed through the CURRENTLY BOUND assessor, so after a remap the row shows the new module, never a fossil of the old one's output |
| Metric kind (count · ratio · score · flag) | `METRIC_RATIO` / `METRIC_SCORE` / `METRIC_FLAG` at **module scope** in `assuranceDial.js`. Declared above every use: as function-locals below their first call they sat in the temporal dead zone and every `inject()` threw |
| Refinement pairs + the disputed reading (Rule 3.0c) | `assurance_points.REFINEMENT_PAIRS` / `refinement_reading()` / `REFINEMENT_STATE` / `REFINEMENT_NOTE` — declared once and computed SERVER-side twice: `read_turn` (per-turn `sc`/`disp` for the dial) and `eval/analysis` (`turn_disputed_dims` for the ring's window). The renderers hold no opinion; `analysis` IMPORTS the map rather than restating it |
| The mode a turn STAMP records | `posture_modes.stamped_mode()` — a `review` gate resolves to `enforce` internally, so every runtime must stamp the CONFIGURED mode or REVIEWED reads "no gate is set to park answers for a human" while the ring, reading profile config, says it can park |
| Posture-mode ladder (profile editor) | `POSTURE_MODES` + `pickMode()` + `GATE_PROPS`/`applyGateMode`/`composeGateMode` — `configurationHandler.js`. The four strings are TWO postures × two properties: Observe (☑ disclose = `annotate`) / Enforce (☑ keep for review = `review`); the composites are stored unchanged. Round-trip contract pinned by `test/test_posture_editor_roundtrip.mjs` (verbatim extraction) |
| A gate's EFFECTIVE mode (agent-level inheritance) | `core/posture_modes.effective_posture()` — gate block → `postureConfig.mode` → observe, corpus following the grounding gate's effective mode, every value naming its source (`gate`/`agent`/`grounding`/`default`). Stamped as the computed `posture` field on `GET /v1/profiles`; the ring (`_gateMode`), `_canPark` and the runtimes all read the SAME resolution — never re-derive inheritance from a raw config block |
| How the Scorecard groups | `eval/stages.py` (SERVED) → `ui.js:_stageGroupsForCard` — by the MOMENT a measurement was taken. Both the KPI-tier taxonomy and the guarantee-axis partition are RETIRED on that surface; JS declares no group list of its own. Gates: `test_eval_stages`, `test_quality_card_partition.mjs` |
| The turn-level scoring controls (Guide · one stateful scoring button: *Score this answer* → *Ask the AI judges* → *again*) | ONE cluster (`#quality-ctl-cluster`, ui.js), ADOPTED onto the assurance band header (`.ad-ctl-slot`, assuranceDial.js) — a turn-level control, never a station one; the dial rescues the cluster across its innerHTML re-renders. No dial (legacy strip) ⇒ the cluster stays on the card |
| Band → state (Rule 3c.1) | `assurance_points.BAND_STATE` (Python) ≡ `trustGrammar.BAND_STATE` (JS), pinned byte-equal by `test_assurance_points`. The registry resolves each measurement's contribution (`dimension_state` → `st`) and its own colour (`band_state` → `bst`) server-side; **no renderer converts a band**, and `test_assurance_dial` asserts the dial declares no alias. The reverse relation (a mechanism's state → a band, for Guide context rows) is derived by INVERTING the same map, never written out again |
| Where measurements are counted (Rule 3c.2) | `assurance_points.allocate_measurements()` → `MEASUREMENT_STATION`, served as `assurance.measures`. One destination, never gathered from whichever mechanisms rendered. Gate: G1 in `test_assurance_points` (every scored dimension of every class lands in the lane; the seven guarantee stations receive none) |
| Rule 3.0c resolution | `assurance_points.apply_refinements()` — ONE application, shared by `read_turn` (the mechanism rows) and `read_turn_measures` (the station allocation), so a pair cannot be resolved on one path and not the other |
| Reading a profile config block | `profile_config_contract.config_block(profile, *path)` — ALWAYS a dict, never a raise. The gate resolvers walked into a profile assuming dict shape at every level, so one malformed block raised `AttributeError` outside their own guard and inside the caller's fail-open `except`: two real turns recorded `inbound·outbound·grounding·honesty = error` TOGETHER, every protection silently off while the answer shipped. Never re-introduce a direct `.get()` chain into profile config |
| The preference reading (the vote axis) | `voteNote()` (roster) + `_voteTag()` (run row) — `agentPerformanceHandler.js`. Absent at zero votes; muted word chips, never colour alone; must not move the band or the rate beside them (Rule 3bb.4) |
| What a vote will DO, per engine | `VOTE_TIP` + `composeVoteTip()` — `ui.js`. The LEAD is the consequence, never the button's own label (Rule 3.0); the boundary is the quieter `data-tip-note` tier. Every line names a SHIPPED, default-on mechanism — a tooltip promising an opt-in one would be a promise the platform does not keep |
| The intervention reading (the mechanism axis) | `interventionNote()` — `agentPerformanceHandler.js`, on the roster card AND the detail panel. Reports only what happened; `errored` is included because a guard that could not run is not a guard that passed, and it is deliberately NOT in the pass rate (Rule 3bb) |
| A guarantee's two relations | `station_entry()` serves `controls` (the mechanisms that UPHOLD it) and `dims` (the measurements ABOUT it, from `station_dimensions()` — the LABEL, never a count); the Guide renders them as **`upheld by`** and **`graded by`**, with the reverse reading **`upholds`** on the mechanism. Listing the mechanisms under "graded by" said a control grades a guarantee — the inversion of the mechanism/measurement distinction — and left the station's own measurements off its entry |
| An inbox row's owed act (Rule 8.5) | Every row offers the place it is **worked**, routed by what the row IS — a turn-scoped finding opens that turn's trace (`data-ap-open-session`, the lens's own route), an agent-scoped one opens the agent's roster detail. Offered whether or not the item is disposed: an **ack disposes of the NOTICE, never of the condition** (a station shortfall persists and re-derives after every ack), so a settled `needs you now` row that offered nothing read as "urgent, and there is nothing to do" |
| An `@TAG` chip | `ui.js:renderProfileTag()` — published as `window.renderProfileTag` for IIFE handlers, the same dual-surface pattern `profileColors.js` uses. Its markup, its three CSS variables and its hover colour are ONE design; an absent builder degrades to the plain tag, never to a styled lookalike (a fallback that reproduces the design is the duplication wearing a safety net) |
| The IFOC palette | `static/js/profileColors.js` (`window.ProfileColors`) — its own header records that the map "historically drifted across ~5 handlers". Never restate the four hexes; derive label-keyed or order-keyed variants from it |
| A station's route to its own Guide entry | `trustCircle.stationPanel` — declared ONCE in the renderer, because it is a property of BEING a station rather than per-station data, and routed through the SAME `explain-dim:` channel the dimension chips use. Guarded on the Guide being loaded (Rule 8.5: never offer a button that cannot open anything). Scope-independent — the route names the station, never the agent |
| What JUDGED counts | Every scored dimension the turn produced — not a residue, and never a hand-list. Its entry serves the MOMENTS under **`counts`**, never `graded by`: JUDGED is where measurements are counted, not something they assess, and calling its own contents "graded by" would invert that |
| A station's mechanism verbs | Derived from `POINT_CLASS`, one row per class: control → **upheld by** · boundary → **bounded by** · attestation → **attested by** · measure → **reported by**. A station's mechanisms are NOT all controls (five of eight have none), and "Evaluation upholds JUDGED" inverts the direction exactly as "graded by" would — a measure reports on a guarantee, it does not hold it up |
| A count stated in prose | `assurance_points._scorer_census(scope)` — counted from the registry at read time, **and scoped to what the entry describes**. A count is a fact ABOUT something: the platform-wide split printed on a station's own entry describes the platform, not the station. Gate: no count may be typed into `MECHANISM_DERIVATION`, and the rendered entry must state its own scope's total, never the platform's |
| Judged vs deterministic (the KIND channel) | Two carriers, one predicate. Where a check is **named**, the **`·judge` text tag** (Quality card, runs ledger chips, Guide chip rows, heatmap column headers, the shared dimension tooltip). Where it is only a **mark**, the **glyph's shape** — `.ap-sq-j`, a diamond (Rule 9.4b). Both resolve through ONE predicate `isJudgeVerdict()` (the verdict's server-resolved `kind`, falling back to `QualityDimensions.JUDGE_DIMS` for a span persisted before that field), so two marks describing one measurement cannot disagree about its kind || WHEN a judge runs | `assurance_points.POINT_JUDGES` → `guide_entry.judges` → `qualityDimensions.judgeRuns()`, derived from TWO independent facts — wired to a gate, and registered as a scorer — never assumed. Three readings from one declaration: the **`·in-flight`** tag in every list, the **Judge runs** facet, and the entry pair `escalates to` / `decides for`. Only in-flight is marked: *after the turn* is the constant (all nine judges are scored dimensions), so qualifying all nine would be weight without information. Station **ownership is not escalation** — five judges are owned by GROUNDED and GUARDED, two are wired to a gate; owning a guarantee says *this judge measures whether it held*, not *this judge runs inside its gate* |

| WHICH judge layer spent a turn's judge tokens | `_record_score_usage(layer=…)` — `eval/online.py`. One event type (`judge_evaluation_complete`) serves the scoring panel AND a gate's in-path escalation, because both bill through the same sink; the layer is **declared by the caller**, never inferred (the deferral defaults to `in_flight` because it exists FOR that case), degrades to the norm on an unknown value, and drives the step's own name (`Gate Consulted a Judge` / `Agent Quality Judged`) on the live stream AND on reload. Absent on older events ⇒ no reading rather than a guess |
| WHEN a judge runs | `assurance_points.POINT_JUDGES` → `guide_entry.judges` → `qualityDimensions.judgeRuns()`, derived from TWO independent facts — wired to a gate, and registered as a scorer — never assumed. Three readings from one declaration: the **`·in-flight`** tag in every list, the **Judge runs** facet, and the entry pair `escalates to` / `decides for`. Only in-flight is marked: *after the turn* is the constant (all nine judges are scored dimensions), so qualifying all nine would be weight without information. Station **ownership is not escalation** — five judges are owned by GROUNDED and GUARDED, two are wired to a gate; owning a guarantee says *this judge measures whether it held*, not *this judge runs inside its gate* |

| What makes a turn FAIL | `analysis.convicting_dimensions()` + `gate_withheld()` → `turn_failed()`. A check a **RULE measured** landed in danger — **unless** the judge that refines it corrected that (Rule 3.0c `disputed`, named by `judge_cleared()` and rendered) — **or** a gate withheld the answer. A judge alone does not convict: a model opined. `Scorer.gating` no longer decides a turn's fate (under it, a turn where the coarse check AND its refining judge both found the answer unsupported scored **clean** — the strongest grounding evidence available, counted as nothing); it still governs the Scorecard's gating-vs-flagged split. Resolved against the CURRENT registry, never the `eval.gating_failures` scalar frozen onto the span at write time — a sink outlives a policy change, so a stored derived value keeps convicting under a retired rule (a live agent read 44% with 3 of 10 failures convicted by advisory dimensions). A span with no per-dimension verdicts falls back to the scalar; the signed record is untouched, only the derived aggregate is recomputed |
| That a judge RAN, and its ruling | `_judgeTag()` — `agentPerformanceHandler.js`. **Two facts, two chips.** A gate CONSULTING a judge is its own chip (`gate asked a judge · cleared · 1/1 models`), shown only where nothing else already carries it — when the gate acted, the act chip states the consultation in its tooltip (Rule 3.0). The panel's tally names the **finding, not the ratio** (`1 judge flagged this`), because its denominator — *judges that ruled*, excluding abstentions — cannot be seen on a chip; the full tally lives in the tooltip. **Silent on the norm**: a unanimous panel earns no chip, since the diamonds already say a judge ran and agreed and a chip on 13 of 18 rows is weight without information. Muted, never a band colour: the row states its verdict once, and a second coloured chip reads as a second verdict |

| That a mechanism ACTED | `_actedTag()` — `agentPerformanceHandler.js`. The intervention axis on the row: Rule 9.3's dot (a mechanism acted) with the accent when an act is OWED, and the act NAMED because a dot cannot say which of five happened. An **errored** guard is never an act. Independent of the verdict beside it (Rule 3bb), leads the row's chips, and carries its own `act:` filter. Short form on the row, sentence in the tooltip — a chip may never move a column (Rule 9.4c) |
| The strip's key | `_stripKey` — every sample drawn by the shipped `bandMark()`, so the key cannot drift from the marks it explains. **Open until dismissed** (dismissal persists); each row appears only where its channel is in use; toggled in place, because re-rendering the ledger to explain a legend would collapse the rows being explained |
| A run row's band | `runBand()` reads `run.passed` — the SAME verdict, never re-derived. Two earlier versions each computed their own and each disagreed with the headline above them (7 vs 11, then 7 vs 10). A row that computes its own verdict will always eventually differ from the one printed above it |
| Colour / shape tokens | `static/css/trust.css` |
| Explaining a dimension | `QualityDimensions.openGuide(key, ctx)` — the ring routes to it via `explain-dim:<key>`; it never defines a dimension itself (Rule 3b.3) |
| A station's derivation | `_deriv()` + `_dimRows()` — built from the figure's own variables (Rule 3b.5) |
| Band floors (green/amber) | `rest_routes._quality_bands()` → `dashboard.quality_bands`, adopted by `_applyQualityBands()` and pushed to `agentKpiCharts.setBands()`. Admin-configurable; **numbers only** — the mode ladder and worst-of aggregation are deliberately NOT configurable (Rule 3b.13) |
| Practice signals (is anything owed?) | `signingInUse` (SIGNED, deployment-wide) · `profile.runsUnattended` (BOUNDED, per-agent — stamped in `rest_routes.get_profiles`). Scope per Rule 8.2c, always fail toward in-use (Rule 8.2b) |
| The trust vocabulary (words · six states · both rank tables · band/severity aliases · tick aria) | **`static/js/handlers/trustGrammar.js`** (PROP-002) — loaded before every consumer; `_ATTN_WORD` (handler), `stateWord` (trustCircle) and the dial's `RANK` READ it, each with an absence-tolerant fallback literal the gate pins EQUAL to the canonical (TC-50/P2) |
| Derived-event severity ↔ dial state | `assurance_points.DERIVED_EVENT_SEVERITY` — `trust_events.derived_severity()` imports it (equal fallback, test-pinned). One fact, one colour, every surface (Rule 11.3) |
| The OBDA-READ lane vocabulary (`governed` · `free-form` · `mixed` · `confined`) | `agent/obda.py:build_event()` — the ONE classifier; the Live-Status card, the answer capsule's lane chip and `_read_obda` all read the stamped `lane`, never re-derive it. `confined` = every raw attempt was DENIED, neither lane (a denial counted free-formed would record the confinement working as the escape it prevented) |
| The consumption receipt's sealed content | `agent/obda.py:receipt_content()` over the CLOSED `RECEIPT_FIELDS` tuple — the SAME function seals (`executor._run_obda_scan`) and verifies (`provenance.verify_content`), so the sealed receipt and the displayed record are checked against each other; a stray presentation field can never enter the seal |
| The surface-denial marker | `agent_authority.SURFACE_DENIAL_MARKER` — the E1 reason carries it, `denied_tool_result` keys the recovery-shaped tail on it, and the eval scorer + red-team probe match persisted traces by the same token (trace readers keep literals — they read RECORDS, which carry the token they were written with) |
| Inbox event → station attribution | `_stationOfEvent()` (handler) — the bucket tally for the ring's II ticks AND the live pulse route through it; never an inline regex ladder |
| Band colours (healthy/review/danger) | `TrustGrammar.bandColor()` → the `--trust-*` tokens — the Quality chips, ledger chips and QD Guide all alias; no private hex (Rule 9.5 reaching the files TC-91 never scanned) |
| Which module assesses a property (PROP-003) | `registry.bindings()` — read, never re-derived. The studio, the Guide's lanes row and the deployment envelope all render the SAME table, so a remap reaches every surface without any of them holding an opinion about it |
| Whether a binding is PERMITTED | `conformance.check_binding()` — the ONE validator, including the third-party trust gate (signed-and-intact, or refused). It lives there rather than at the REST layer so every caller inherits it from `registry.bind` |
| A binding's three states | `assurance/store.py`: **no row** = the shipped default · **row with a key** = rebound · **row with NULL** = explicitly NOT ASSESSED. `delete_override` restores the default and is a DIFFERENT act from unbinding; a UI offering only one of them cannot express the other |
| Whether a gate really consults a property in flight | `assurance.INFLIGHT_CONSUMERS` — data, pinned against the real `resolve` call sites. A bound-but-unconsulted lane renders *no gate consults this in flight yet*, because implying it can move an answer is a decoration wearing a control's clothes |
| A third-party module's lane eligibility | DERIVED from the tier the platform PROVED (`manager._build_assessor`), never from what the pack declares — a verified module is assigned `PURE|REPLAYABLE|DETERMINISTIC`, a scanned one is confined to POST_HOC by construction (stripping `PURE` alone leaves the triggered lane open, which asks nothing) |
| Dimension names | `QualityDimensions.label()` — the gate loads the real `qualityDimensions.js` under Node, so the title-case fallback is never what CI exercises (TC-51) |
| Estate subject (one subject for both attributes) | `_selfDash()` — restricts system-scope execution cells to the caller before any station reads them; gate asserts foreign cells change nothing (TC-8) |
| Behavioral-cell lookup (key shapes) | `_tbAgent()` — my-scope plain tags ↔ system-scope `tag::uuid`, one resolver (TC-35) |
| Metric label / unit / **direction** | `QualityDimensions.THRESHOLD_SPEC` — `thresholdSliders.js` merges it into its own range table; a second copy could print a band backwards on one surface and correctly on the other. Gate asserts the key sets agree BOTH ways (3b.14) |
| Threshold formatting | `QualityDimensions.fmtThreshold()` / `fmtBand()` — the admin slider readout delegates to it, so the Guide and the settings page never print one number two ways |
| Live scoring bands (what IS set) | `GET /v1/evals/scoring-thresholds` → `ONLINE_EVAL_DEFAULT_THRESHOLDS`. The Guide reports the LIVE band, never the shipped default (Rule 3.8); admin save calls `invalidateThresholds()` |
| A scorer's code-level fallback | `eval/scorers/deterministic.py:CODE_FALLBACK_CUTS` — served to the client, never restated in JS, so the Guide cannot promise a band the scorer does not apply |
| Peek-vs-explainer precedence | `trustCircle.peekSuppressed()` — one pure predicate, gate-driven as a truth table (Rule 4b.9). Never re-inline the condition in `renderPanel` |
| State shape + fill grammar (Rule 9.4) | `.tj-stage.tj-*` in `trust.css` — the gate extracts all four blocks and asserts they are pairwise distinct on a NON-colour property (TC-90) |
| The small state mark (Rule 9.4a) | `bandMark()` + `.ap-sq-*` — `agentPerformanceHandler.js`. ONE builder for every small state square in the ledger (the per-run band strip AND the station header mark), keyed on the BAND name directly so nothing converts; the CSS holds the shapes, the caller holds only the state. Never render a state square inline |
| The three colour channels (Rule 9.5) | `CHANNEL_TOKENS` in the gate mirrors the `--trust-*` set in `trust.css`; an allow-list, never a widened pattern. Hardcoded colour utilities are banned on every trust surface (TC-91) |
| Height-stable hover region (Rule 9.7) | `.tc-root { align-items: start }` — centring it is the shape that produced the screen-wide flicker (TC-93, TC-99) |
| Trust-surface inventory (Rule 9.8) | `test/extract_trust_inventory.mjs` + `test/trust_inventory.baseline.json` — a construct count that DROPS is potential fact or reach loss |
| Every intervention that can touch an answer | `agent/assurance_points.py` — the registry; stations are views over it (V1). Gates whose seam has no turn context record via `agent/assurance_tally.py` |
| Where a measurement is COUNTED (Rule 3b.6) | `assurance_points.MEASUREMENT_STATION` + `allocate_measurements()` — one constant, one allocator. `test_assurance_points.py` asserts every scored dimension lands there and the seven guarantee stations receive none |
| Which guarantee a measurement is ABOUT (the label) | `assurance_points.station_dimensions()` — derived from mechanism ownership, `partition_conflicts()` must be empty. It labels; it must never be read as a count (asserted) |
| When in the turn a measurement was taken | `eval/stages.py` — `STAGE_ORDER`/`STAGE_DIMENSIONS`, SERVED on `/v1/assurance/vocabulary`. The frontend declares no stage list of its own; `partition_gaps()` must be empty |
| TRUSTED's no-double-count blend | `eval/trust_behavior.py` — `_trust_dim_signal` + `_residual_signal` partition the per-dim evidence; the whole-turn rate is only the no-per-dim-data fallback |
| A measurement's MOMENT, on any surface | JS: `QualityDimensions.stageOf` (Guide chips) · the handler's `_dimStageOf` (roster bars, run chips, band-strip, heatmap) · `QualityDimensions.stageOfThreshold` (admin Trust panel's slider grouping via thresholdSliders stage-host mode) — all reading the SERVED model. Never a hand-placed group list on a surface; the gate asserts every consumer derives |
| The admin Trust panel's cut | `templates/index.html` `#config-panel-trust` — four sections (Scoring · Quality bands · Enforcement · What reaches you); every threshold sits under Quality bands in `[data-thresh-stage]` moment hosts. Route + destination + grouping seam asserted together (gate #65) |
| Deployment-wide readings (the admin masters, the OBDA planes' **component** availability `obda_enabled` / `obda_model_enabled` / `obda_act_enabled`, and the OBDA policy) | `rest_routes._deployment_masters()` → carried on the evals-dashboard payload, read as `dash.deployment_masters` |

Rule 4c.2 (label ∉ `composition_hash`) is enforced in `test/test_trust_membrane.py`
(`test_ingredient_label`) — the Python suite, because the hash lives in Python; the
grammar gate covers the JS surfaces.

**When a cache miss is a FINDING:** `assurance_points.CACHE_CAP_BREAKPOINTS` /
`CACHE_CAPS_BEST_EFFORT` (pinned equal to `llm.cache_policy`) — only an explicit
**breakpoint** attests a prefix: there the platform asked for exactly those bytes to be
cached and hashed them, so a same-prefix miss means something *outside* the hash moved.
Under **best-effort** caching (Gemini `implicit`, OpenAI-shaped `automatic`) the provider
caches on its own and the platform asked for nothing — so the same miss is the provider's
choice, reported and never called drift. Stating it unconditionally made the reading claim
a moved prefix that the platform's own unchanged hash refuted (prod session `d9a60903`).
The DEFINITION (`MECHANISM_NOTE`, `MECHANISM_DERIVATION`) carries the same condition as
the reading: one fact, every surface, one answer.

**Backend ladder mirror:** the profile editor's `POSTURE_MODES` mirrors
`core/posture_modes.py`. A validator that omits a valid stored mode *displays* the
fallback and then persists it on the next save — silently weakening a protection. Use
`pickMode()`; never write a fresh `['observe', …].includes(m)`.

---

## 7 · Adding a station — checklist

1. Build it with **`_assemble(key, design, execution, opts)`**. Do not construct the
   object literal by hand.
1a. If the station guards an **opt-in** protection, give it a **practice signal** and read
   `na` while the practice is not in use (Rules 8.1/8.2/8.2b). Fail toward `warn`. Scope it
   to the smallest subject that answers honestly — per-agent unless that would be circular
   (Rule 8.2c); a deployment-wide signal on a per-agent reading mis-attributes exposure.
1b. Give it a **`derivation`** (`_deriv({window, config, exec, dims, note})`). If no quality
   dimension feeds it, say so in `note` (Rule 3b.2); if some do, pass them through
   `_dimRows()` so they carry their live band and stay clickable (Rules 3b.3/3b.4).
1c. If any mechanism on the station **owns scored dimensions**, declare them in
   `POINT_DIMENSIONS` and nowhere else. Allocation, counting and the colour fold all
   follow from that one declaration (Rule 3c.2) — do not gather a station's measurements
   anywhere, in any language. Add the station to G1's per-class totality assertion, and
   fixture at least one **`review`** band: `healthy`/`danger` alone cannot detect a broken
   band bridge (Rule 3c.1), which is how the last one survived a full release.
2. Give both attributes a `{ state, text, detail }`. If one genuinely doesn't exist,
   pass `state: 'na'` with an empty `text` and a `detail` that says *why* — the value
   renders `—` automatically.
3. Make `state` come from a declared policy (`_postureAggregate` for anything that
   spans agents). Never `.some()`.
4. Write `headline` as a consequence for the *answer*, not a restatement of the value.
5. Write `why` as the **rule**, not the reading — `_assemble` prepends the attention
   word and the driver.
6. Write `evidence` as named instances, or `''` (the assembler substitutes an explicit
   honest-absence line).
7. If the station can be *acted on*, derive its `actions` from the state (§4b.2) and gate
   them on being performable (§4b.3). Never add a second fixed CTA.
8. Check both scopes: focused on one agent **and** estate-wide. **Both attributes must
   describe the same subject** — a per-agent configuration next to an estate-wide
   execution reads as one agent owning the estate's problem. If one side can only be
   estate-wide, say so in its `detail` *and* name the per-agent figure.
9. Check that the **prose agrees with the state**. Three defects shipped as a `headline`
   contradicting the very state it described, none caught by structural assertions —
   the gate now asserts this class explicitly.
10. If the station reports a **runtime**, consult the switch that decides whether that
    runtime runs at all (`dash.deployment_masters`), not only the per-profile mode.
    With a master off, a mode is a setting, not a protection — reporting it as running
    is a falsehood (finding #24: `observe · 7/7` on a deployment where both runtimes
    short-circuited at their first line). `null`/absent masters mean UNKNOWN (older
    backend) and must degrade to the per-profile reading, never a claimed "off".
    Configured-but-inert reads `warn` (declared intent contradicted); an unconfigured
    default under an off master is genuinely `na`.
11. Run `node test/test_trust_circle_grammar.mjs` and add the station key to
    `STATION_KEYS`.

---

## 8 · Deliberate absence vs outstanding work

The four levels conflate two kinds of absence, and the distinction is **not** *on vs
off* — it is **is anything owed?**

| Kind | Example | Honest reading |
|---|---|---|
| **Deliberate** | behavioral trust `off`; no gate in `review` | `na` — nothing is owed |
| **Outstanding** | an unsigned agent; an unscreened corpus | something *is* owed |

**Rule 8.1 — `na` may only mean "nothing is owed."** Reading outstanding work as *not
turned on* tells an operator there is nothing to do in a case where there is.

**Rule 3.8 — Report what IS configured, never what ships.** A settings-derived reading
must be read from the live setting. Stating the shipped default where a reader looks for
the current value is a constant wearing a live reading's clothes (the Rule 3.5 family),
and it fails in the direction that hurts: the Quality Dimensions Guide told every operator
"unset by default — measured but not banded" while their admin page showed all seventeen
metrics banded. It is not enough for the claim to have been true at install.

Three obligations follow. **Fetch it, cache it, and invalidate on write** — a per-page
cache that survives a settings save is the same stale reading one step later. **Never
guess while it is in flight**: an unresolved value renders as pending, and an unreadable
one says so; both are honest, a default is not. And **name a code-level fallback where one
exists** — reporting "not banded" for a metric the scorer still convicts on is the same
over-claim mirrored, so the fallback is served from its single source in the scorer rather
than restated on the client.

**Rule 8.2 — "Outstanding" is a property of the deployment's practice, not of the
artefact.** Signing is opt-in by design (badge-not-gate; sharing stays open), so a
deployment may legitimately never sign anything. Therefore:

> `unsigned` reads **`na`** while nothing in the deployment has ever been attested —
> the practice is not in use.
> `unsigned` reads **`warn`** once anything has been attested — the practice is in use
> and this artefact is outstanding.

The practice signal (`signingInUse`) is deliberately **deployment-wide, never the
focused scope** — otherwise a focused unsigned agent could never read as outstanding.
That is a consequence of *circularity*, not a general preference for coarse signals: a
per-agent signing signal would answer "should this be signed" with "is it signed". Where
a per-agent signal is **not** circular, it is the correct scope — see Rule 8.2c.
Any attestation counts, including `changed` and `deployment_unattested`: each proves
somebody signed. An **unknown** wire string stays `na` — it may not be outstanding at
all, and inventing attention for a state we cannot interpret would be a guess.

This is why no fifth state was added. Under *verdict* semantics `warn` meant "the layer
acted", so an unsigned agent could not be amber and a new level looked necessary. Under
*triage* semantics (§4) `warn` means "worth a look", which is exactly what outstanding
work is. Adding a level would dilute a grammar whose strength is that four are
learnable, and would churn every surface sharing the tokens — to express something the
existing amber already expresses correctly.

**Rule 8.2b — The practice rule is GENERAL, not a SIGNED special case.** Every opt-in
protection has a practice signal, and any station guarding one must consult it or it
becomes the station that can never go green. Adding a station that protects something
optional means answering: *what would have to be true for this to be owed?*

| Station | Protection | Practice signal | Reads `na` when |
|---|---|---|---|
| SIGNED | attestation | `signingInUse` — anything ever attested (deployment-wide) | nothing has ever been signed |
| BOUNDED | authority scope | `profile.runsUnattended` — a coordinator lists it as an expert, a scheduled task targets it, or a flow node drives it (**per-agent**) | nothing can drive *this agent* unattended |

BOUNDED shipped for months reading `warn` unconditionally — two protections of identical
shape treated oppositely, and the one that could never go green. The asymmetry was not a
decision; it was Rule 8.2 applied at one site and not the other.

**Rule 8.2c — Scope the signal to the smallest subject that answers the question
honestly.** A practice signal is deployment-wide only where a narrower one would be
circular (Rule 8.2, SIGNED). Otherwise it must be **per-agent**, because the coarse form
answers a question nobody asked.

BOUNDED's first fix used a deployment-wide `autonomy_in_use` ("does a coordinator, a
schedule or a flow exist *anywhere*"). It replaced a permanent amber with a *wrong* amber:
a Focus agent that **no** coordinator lists, **no** schedule targets and **no** flow drives
still read outstanding because two coordinators existed elsewhere in the estate. One
coordinator does not expose an agent it never calls. Reachability is independent of
scoping, so the per-agent signal is well-defined and non-circular — and it is the only one
that answers the station's actual question: *would authority scoping have protected
anything here?*

The generalisation: before shipping a practice signal, ask **whose** practice the reading
claims to describe. If the answer is "this agent's", a deployment-wide signal will
mis-attribute somebody else's exposure to it — the same defect class as Rule 5.1 (both
attributes must describe the same subject), one level up.

**The signal fails toward `warn`, never toward `na`.** Rule 8.1 permits `na` only where
nothing is owed, so an *absent* or unresolvable signal (an older backend, a failed lookup)
must read in-use. The honest error is an amber that turns out to be unnecessary; a dash
over real exposure is the failure this whole document exists to prevent.

**Rule 8.3 — Name the nature, not just the level.** "Worth a look" is the right triage
word but does not say work is owed, so the `Why` slot states it: *"sign-off is
outstanding — this deployment signs agents and this one is not signed yet."*

**Rule 8.4 — Where a second channel already exists, use it rather than a fifth state.**
Colour is committed to attention (§4), so two states at the same attention level are the
same colour *by design* — correct, but it left `unsigned` and `deployment_unattested`
visually identical while being **different acts** (sign the artefact vs attest this
deployment). The answer is not a new colour; it is a second channel.

SIGNED is the only station whose subject already has a published visual vocabulary: the
**Trust Membrane seal**, rendered on profile cards, marketplace listings and the badge
popover (`TrustBadge.META[state].icon`). The station borrows that mark — **shape carries
integrity, colour carries attention**, neither encodes the other:

| Integrity state | Shape (borrowed) | Attention colour |
|---|---|---|
| `trusted` | shield with a check | quiet |
| `deployment_unattested` | shield, lower half dashed | worth a look |
| `changed` | warning triangle | needs you now |
| `unsigned` | dashed circle | worth a look *(or* not turned on *— see 8.2)* |

This adds **no state and no vocabulary**: it fixes a *consistency* defect, since the ring
was the one surface disagreeing with the rest of the product about what a signed agent
looks like.

**It is deliberately SIGNED-only.** The other seven stations have no external vocabulary
to inherit, so giving them bespoke per-state icons would be inventing exactly the thing
this grammar exists to avoid. A station may supply an optional `glyph`; absent one, the
renderer keeps the station's own icon.

**Rule 8.5 — The offered act must be the act that is owed.** Two states that share a
colour do not share a remedy. `deployment_unattested` is already publisher-signed, so
"Sign this agent" there put the panel's most prominent action on an act that cannot move
the state — worse than offering nothing, because it reads as the fix. Actions derive from
state (§4b), and the acts differ: **sign** (portable half) vs **attest** (deployment
half) vs **re-sign** (drift) vs **verify** (quiet).

### Residue (honest)

The *misleading* reading is gone and the two outstanding-work states are now visually
distinct at SIGNED. What remains: outstanding work still shares amber with a measured
shortfall, distinguished by shape only at SIGNED and by text everywhere else. If signing
should feel like a checklist rather than a warning across all stations, that is a fifth
state or a dedicated surface — and it should still wait for evidence that operators
confuse *"a control under-performed"* with *"an act is owed"*.

---

## 9 · The form rules — how a reading is drawn

§§1–8 govern what a reading *says*. This section governs how it is *drawn*, and it
exists because those decisions were previously made only in a prototype no CI reads —
which is the same condition that let the earlier rules be breached five times before
they were written down.

Every rule below carries a gate. Where the surface it governs does not exist yet, the
gate is **presence-keyed**: it is inert until the construct appears and fails the moment
it appears without conforming. A forward rule with no gate is a rule that will be broken;
a gate that cannot yet run is not an excuse to omit it.

**Rule 9.1 — Pending is not absence.** A layer that has not reported *yet* must render
distinctly from one that is switched off. Folding them is the defect this whole document
exists to prevent, one level down: `na` says *nothing is owed here*, and a reading that
has not arrived owes you a second look. There is no `pending` state in shipped code
today — which means today the fold would happen by default, silently.
*Gate (presence-keyed): a `pending` token or state class must not reuse any `--trust-na`
token, and must be pairwise distinct from `na` on a non-colour property.*

**Rule 9.2 — The star marks the subject; the plus means you can change it.** One glyph,
one meaning, on every surface. A mark may not promise what its surface cannot do: the
Live Status dial shows one answer from an agent you cannot change there, so it carries a
star and **no plus**; the Trust Circle and the Anatomy let you choose another agent, so
they carry both.
*Gate (presence-keyed): a surface rendering the plus must expose a picker route; one
without a picker must not render it.*

**Rule 9.3 — Ticks mark what is engraved, and mean the SAME on every instrument
(PROP-002).** A radial tick is a promise that a click will pay: one tick = detail
engraved behind the mark, **two = a MECHANISM ACTED on this subject** — on this turn (the
dial) or on ≥1 turn in the window (the ring, attributed from the inbox's derived
observations at their one computation site).

> *Noun corrected: this said "a gate ACTED" on every surface, and the mark is not
> gate-scoped. Nine mechanisms can set `acted` and only four are gates — the other five
> are a tool DENIED by Agent Authority, a quarantined corpus document, documents withheld
> by the sensitivity ceiling, a retrieval-firewall chunk labelled untrusted, an item
> parked for review, and an integrity mismatch. The computation was always right; the
> word narrowed it and would have had a reader looking for a gate that never fired.* The earlier per-surface clause let the
ring's double-tick mean *"an act is owed"* — the same mark meaning the opposite of the
dial's ("already happened" vs "hasn't happened yet"), on two instruments that share
geometry by design. Outstanding work is neither an event nor a state: it rides the
**accent owed dot** (the actionability channel, Rule 9.5), and pinning the station
reveals the act.
*Gate: the P2-2/P2-3 sections — TICK_ARIA shared (and reaching aria-labels), the owed
double-tick absent from renderer and CSS, acted/owed toggled only from model facts.*

**Rule 9.4 — Weight before colour.** Attention must be legible without hue. The four
states already differ on a non-colour property each — `pass` solid border with full fill,
`warn` solid with **half** fill, `danger` **2px** border, `na` **dashed** — and that is
the property being protected, not the specific values.
*Gate: the four state classes are pairwise distinguishable on at least one non-colour
property. Enforced today (TC-90).*

**Rule 9.4a — On EVERY surface, including the small ones. Colour is never the sole
carrier.** A mark too small for a label is *more* dependent on shape, not less — and it
is exactly where the shape channel keeps getting dropped as a simplification.

This is not a preference. Measured on the shipped tokens:

| pair | ΔE | hue |
|---|---|---|
| **light** `--trust-warn #b45309` vs `--trust-danger #dc2626` | **31.9** | 26° |
| dark `--trust-warn #fbbf24` vs `--trust-danger #f87171` | 69.0 | 43° |

The light theme has **less than half** the separation of dark on the one pair that
matters most — *worth a look* vs *needs you now*. Simulated for colour-vision deficiency
that light pair reads **ΔE 11.7 (protanopia)** and **ΔE 5.5 (deuteranopia)** — the same
colour for ~6% of men. **Retuning the token cannot fix it**: the best candidate lifts
normal vision 31.9 → 47.8 but CVD only 11.7 → 13.1, because both hues collapse into one
yellow band. Any state distinction resting on hue alone is therefore unavailable to a
real fraction of users, in every theme.

Found because an operator could not tell a `review` square from a `danger` square in the
Agent Roster's per-run band strip. The colour was correct; the channel was missing — two
marks in that row (the strip square and the station header mark) were solid squares in
all four states with only the fill colour differing, each its own private implementation.
Both now render through one `bandMark()` builder against the declared shapes.

*Gate: neutralise every colour and the four states must still be distinguishable — a
property, not a grep, so a future mark that carries state by hue alone fails it.*

**Rule 9.4b — Shape carries WHO measured it; fill carries WHAT it found.** A strip of
identical marks says nothing about which of them is a *model's opinion* and which is a
*rule's measurement* — and on a real agent, two of ~24 were judges with no way to tell,
nor to see what they ruled, without expanding the row.

Fill treatment is already committed to the state family (9.4a) and colour to attention
(9.5), which leaves the glyph's own shape. A judge's mark is a **diamond**; a rule's is a
square. It composes with all four state treatments — a judge in danger is an outlined
diamond — so the kind never overwrites the finding.

**Deliberately not a circle.** A dot already means *a mechanism acted* (Rule 9.3). One
mark with two meanings, decided by which surface you are looking at, is the drift this
grammar exists to prevent.

A new glyph is offered with a **key**, and only where the thing it names is present —
a key for a mark nobody can see is furniture, and a key drawn in a state colour would
assert a state the sample does not have.

*Gate: per-mark exactness — exactly the judge marks carry the shape and no others, so
both deleting it and applying it to everything fail; and the judge glyph declares no fill
of its own, so the state still shows through.*

**Rule 9.4c — A mark carries at most what a reader can resolve, and a new channel
arrives with a key.** The per-run band strip carries three facts, each on its own channel, none of them hue
alone:

| channel | carries | vocabulary |
|---|---|---|
| **fill** | what it found | solid `passed` · half `worth a look` · outlined `failed` · dashed `did not run` |
| **shape** | who measured it | square = a rule · diamond = a model (9.4b) |
| **size** | what the ruling could DO | a **larger** diamond = a gate consulted it **during** the turn, so its verdict could withhold the answer · the plain diamond = **sampled** after the turn, it reported and changed nothing |
| **centre** | the refining judge DISAGREED | the dot is that judge's verdict (Rule 3.0c): **green** on a failed rule — it cleared the same answer, so the conviction is withdrawn · **red** on a passing rule — it disputed the pass, which does not fail the turn. The outline always keeps what the rule measured |

A further fact rides the ROW, not the strip, because it is not a measurement: **a
mechanism acted**. It takes the channel the grammar already owns for it (Rule 9.3) — a
state-coloured dot = a mechanism acted, **accent** = an act is owed — with the act named
beside it, because a dot cannot say which of the five acts happened (refused ·
withheld · annotated · redacted · rewritten). An **errored** guard is never an act: it is a guard that
did not run, and calling it one would report the absence of a control as the control
working.

This is the **intervention axis** (Rule 3bb) and it must not move the verdict beside it,
nor the verdict move it: on real data every intervention landed on a turn whose
measurements were otherwise clean, so a `healthy` row can legitimately be one whose
answer was withheld. It leads the row's chips because, when it is present, it is the
most decision-relevant fact about the turn — and it carries its own filter, since *which
turns were altered* is a different question from *which turns scored badly*.

**A modifier may ride the common case when it means "less weight".** The size channel was
briefly removed under *mark the exception, never the rule* — 48 of a real agent's 52 judge
marks are sampled, so nearly every diamond wore the modifier. That reasoning was wrong
here — but so was the first calibration. Shrinking the 48 made the strip read as
*degraded*; the modifier belongs on the **exception**, which is what the maxim meant. So
the sampled judge wears the plain diamond at the visual weight of a square, and the
in-path judge — the one whose verdict could withhold the answer — is drawn **larger**. The
ordering is the same (sampled < in-path); what changed is which end carries the emphasis,
and the rule generalises: **a modifier must not make the common case look broken.**

It still holds where it did apply: a `judged · N of M upheld` chip was retired from every
judged row, because a chip on almost every row is weight without information. It speaks
only when the ruling is notable — and then its own text must say **why it is there**
(Rule 3.2), since an escalated turn can also be unanimous.

**The strip is a FIXED GRID, not a variable-length list.** It is read *down* the ledger —
*did this check pass on every turn?* — which only works if a dimension sits in the same
column on every row. A check the turn did not produce renders as an **absence** (`na`),
exactly like one that abstained; omitting it made strips different lengths (20 vs 24 on a
real agent) and moved every dimension after it. The grid is the union of everything that
agent has produced, computed once from the **unfiltered** runs, so applying a filter cannot
move the columns. An absent check **keeps its shape** — shape says *who measures this*,
which is a property of the dimension, not of one turn — so the judge's column stays
recognisably the judge's.

**A judge's colour is worst-of its PANEL, and a split is named.** A judge dimension is
decided by a panel of models and the recorded verdict is their consensus, so a dissenting
model disappears into a majority. Worst-of is the house rule for aggregation (Rule 5.1),
and Rule 3.0c already says a disagreement between measures of one property is REPORTED,
never resolved by weighting — a panel is N measures of one property. So the mark is red if
**any** panel model voted fail. Where that differs from the consensus, the split is named
on both the mark's tooltip and the chip (`·split 2/3`): a red mark beside a chip reading
*upheld*, with nothing explaining the difference, is the contradiction this grammar exists
to prevent. An **abstention** stays in the absence family even if a vote inside it said
fail — a judge that declined to rule is an absence, not a finding.

The centre dot exists because the row's `judge-cleared` chip said a measurement had been
corrected without saying **which** — on a 24-mark strip a corrected failure looked exactly
like an uncorrected one. It is drawn in **both directions** because Rule 3.0c has four
readings and only one had a name on the surface: `judge_only` (the rule passed, the judge
disputed it) rendered as a green square beside a red diamond with nothing saying the two
are about the *same property*. Both are named on the row (`judge-cleared` / `judge-flagged`),
both leave the measurement untouched, and neither convicts. It is an *inclusion inside* a
mark, never a mark of its own: a standalone dot means *a mechanism acted* (Rule 9.3).

Each channel is **named in a key** that is **open until dismissed** — a key you have to
discover is not a key — drawn by the shipped `bandMark()` builder — so the key
cannot drift from the marks it explains — and each key row appears only where its channel
is actually in use (a key for a mark nobody can see is furniture).

*Gate: per-mark exactness on every channel, so both deleting a mark and applying it to
everything fail; and no channel may declare a fill or border of its own, or it would
overwrite the state.*

**One mark, one meaning — the verdict dot is retired.** A run row carried a verdict dot at
its head; once the acted mark landed there were **two dots side by side meaning different
things**, which is the misreading Rule 9.3 exists to prevent. A dot on these surfaces means
*a mechanism acted*, and nothing else. The verdict is carried by its **word**, in its
colour — and where there is no room for a word (the coordination tree), by the state
**mark**, which is a square.

**The same state on two shapes does not have the same consequence, and the key must say
so.** A red square is a rule that measured a failure and it **fails the turn**; a red
diamond is a model that flagged something and it **does not**, on its own (§6.4b — a judge
alone never convicts); a red square with a green centre was corrected and does not fail it
either. The key therefore draws its *shape* row in the **danger** state with both samples
in the same state — isolating the channel by holding every other one constant, and showing
the case where the distinction actually bites. Two green samples demonstrated the shapes
and hid the only thing a reader needed from them.

**One fact per chip, and a chip's presence must explain itself.** `gate-judged 2/3` fused
two facts: a gate consults exactly **one** judge, while the tally is the **scoring panel's**
— so the label read as *"the gate's judge said 2 of 3"*, which never happened. Split: the
panel's chip names the **finding, not the ratio** (`1 judge flagged this`), because its
denominator — *judges that ruled*, excluding abstentions — cannot be seen on a chip and was
asked about three times; the tally moves to the tooltip, where a denominator can be
explained. A gate consulting a judge is its own chip, shown only where nothing else already
carries it (when the gate acted, the act chip states it — Rule 3.0). The tally chip then
speaks **only when the panel flagged something**, so its own number always explains its
presence (Rule 3.2).

**One word, one population — a JUDGE is a check; its MODELS are the panel.** A judge is one
*check* asked once of the turn (`groundedness`, `content_safety`, …); the models are its
panel voting inside it. They appeared on one row a few pixels apart — `1 judge flagged this`
counting checks beside `(judges 0/1)` counting models. Every label states which population
it counts (`2/3 models`, `judges (checks)`, `·split 2/3 models`). **An unparsed vote string
is shown, never dropped**: a shape the parser does not recognise is still the gate's own
record, and a regex must not make a ruling vanish.

**No two chips may share a verb, and no ruling may be stated twice.** A row carried
`judge-flagged` beside `1 judge flagged this` — the **same** judge ruling, in the same
words, from two mechanisms — while `cleared` meant *a withdrawn conviction* on one chip and
*a cleared answer* on another. Three facts, two verbs. The Rule 3.0c marks are now named
for their **effect** (`not counted as failed` · `passed, but disputed`), which collides with
nothing; and the panel tally is **silent where the 3.0c mark already accounts for every
flagged judge** — each `judge_flagged` entry IS a refining judge in danger, so matching
counts mean one ruling twice, and the 3.0c mark is the richer statement because it names
the disagreement. A **cleared** pair never silences a flag: its judge is healthy, so any
flagged judge is a different one, and suppressing there would hide a real finding.

**A summary must count what the reader can count.** The judge tooltip read *"2 of the 3
judges that ruled on this turn upheld the answer"* above a list of **four** named judges —
a fraction whose denominator (*ruled*, excluding abstentions) contradicts the lines beneath
it. It was asked about three times before the label was the suspect rather than the reader.
It now states the composition in the list's own terms — *"4 judges ran on this turn: 2
upheld the answer, 1 flagged it, 1 abstained"* — with no fraction to decode, each
abstention carrying its reason on its own line, and a one-line **vocabulary anchor** where
the confusion actually lives (*a judge is one check; the models on its panel vote inside
it*), because a parenthetical gloss did not settle it.

**A chip may never move a column.** The row is read DOWN its columns — band, marks, cost,
latency — so the chip slot is the only flexible member and it **shrinks**: a long chip
truncates (its full text is in the tooltip either way) rather than pushing the strip
rightward and wrapping the timestamp onto a second line. The row carries the short form;
the sentence lives where there is room for it.

**Rule 9.5 — Three colour channels, and nothing else takes colour.**

| channel | means | where |
|---|---|---|
| trust green / amber / red / dashed-slate | **attention** — quiet, worth a look, needs you now, not turned on | anything with a posture |
| blueprint blue (`--map`) | the **map** — never a reading | anatomy star, spokes, labels, membrane, constellation |
| accent orange | what you can **act on** | hover, focus, the plus |

A fourth colour is a fourth meaning nobody defined. The concrete failure mode is a
hardcoded literal: `text-emerald-300` renders as a reading, is invisible to the token
system, and breaks in light mode — it has shipped here before.
*Gate: no hardcoded colour literal on any trust surface — a Tailwind colour utility
(TC-91) or a bare trust-STATE hex (TC-91b). TC-91b targets the `--trust-*` VALUES
(all three shade-families, both themes), which is precisely what renders the wrong
shade in light mode; it excludes identity/series palettes (marked `hex-palette`),
token fallbacks (`var(--…, #hex)`), SVG/anchor ID refs and `&#NNNN;` entities, none of
which use those values. Both halves are now measured and zero — a regression gate, not
an aspiration. (Identity palettes single-sourcing — e.g. the IFOC hexes — is the
separate Rule 6 concern; a fixed categorical palette is legitimate, only drift is not.)*

**Rule 9.6 — Locate, never filter.** A spine or index highlights and scrolls to its
target; it never removes the surrounding content. Filtering reads as *"this is
everything"*, and on an assurance surface that is the one thing it must never say — the
dropped "Trust only" filter is the worked example (ledger I11).
*Gate: a spine's click route resolves to a scroll/highlight, never to hiding or removing
a sibling (TC-92).*

**Rule 9.7 — Hover must not resize.** A reading written on hover goes into a region whose
height does not depend on what is written there. Otherwise a pointer crossing the dial
reflows the panel, which moved the element out from under the pointer and produced a
feedback loop that flickered the whole screen. The shipped fix is `align-items: start` on
`.tc-root` — the readings region no longer stretches its row.
*Gate: the hover-written region is height-stable — its container must not stretch to its
tallest child (TC-93).*

**Rule 9.8 — The reachability budget, and three kinds of loss.** A fact visible at rest
may move to one gesture; nothing may move from rest to two (invariant V2). And "lost" has
three forms, only the first of which an inventory catches on its own:

| kind | definition | how it hides |
|---|---|---|
| **fact loss** | a datum is no longer rendered anywhere | obvious once looked for |
| **reach loss** | still rendered, but deeper | every fact is "there", so an inventory passes while the surface gets worse |
| **meaning loss** | rendered and reachable, but now ambiguous | the hardest — `pending` collapsed into `not turned on` is Rule 9.1 |

*Gate: `test/extract_trust_inventory.mjs` — a construct count that DROPS is potential
fact or reach loss and must be justified in the migration ledger, never re-baselined
silently.*

---

**Rule 9.x — A mechanism's reading is the finding it acted on; a module's relation to the
mechanism is a mark.** Incident (prod 510f7011, 2026-10-30): a Focus answer annotated for one
unmatched statement read "out-of-scope references — answer annotated" — a fixed phrase per
decision, the wrong family — and the three modules beneath it read "none ran": they had run,
abstained because the gate had acted, and nothing showed the gate had consulted them during
the turn. The reading now comes from the stamp (`1 of 13 statements unmatched — annotated`);
a module the gate consults in flight carries a mark in its name cell, with the words on hover;
on an annotated answer the modules grade the model's words (the appended note dropped), so the
panel shows the reading that can expose a wrong annotation. Gate: `test_assurance_points.py`
[P2], `test_assurance_dial.mjs`.

---

## 10 · Lenses — a re-windowed reading names its boundary

The **trust-trip lens** (docs/roadmap/TRUST_TRIP_RESET.md) is the first lens: the
speedometer model, where a *trip reset* marks a per-agent boundary and the ring can
read *"since the marker"* instead of the whole retained window. Any future lens
inherits these rules.

**Rule 10.1 — A lens re-windows a reading; it never re-writes the record.** A reset
appends a marker and deletes nothing: spans, verdicts, incidents and reviews all
survive, and the whole-record lens stays byte-identical to a world with no marker.
The reset itself is an **audited act** (who / when / why / the agent's integrity
state at that moment) that appears in the agent's Trust History and its story — a
view-shaping act performed silently is a gaming vector, not a feature.

**Rule 10.2 — Every lens-scoped reading discloses its own boundary.** The derivation
window reads *"N scored turns since the trip reset on 30 Jul"*, the execution
qualifier carries *"this leg (since 30 Jul)"*, and an ambient leg chip rides the
instrument while the lens is active. An **empty leg says so** ("the leg is empty,
not clean") — it must never render as a clean figure or as "no execution data",
either of which reads as the lens being absent.

**Rule 10.3 — What a lens may scope is closed.** Execution measurements re-window
(GROUNDED / GUARDED / JUDGED execution, PROVEN's sealed count). Configuration is a
fact about *now* and never re-windows. The canonical trust artefacts — the
behavioral composite (TRUSTED), the circuit breaker, attestations, the audit pack —
**never** re-window: the lens is how the operator looks at the agent, not how the
platform judges it. The leg may appear beside a canonical reading as one clearly
labelled informational line, never in place of it.

**Rule 10.4 — A surface without the disclosure must not be lens-scoped.** The mini
ring-glyph on the roster and the coordination tree carries no lens chip and no
"since" line, so it always derives from the whole record — the one derivation site
is lens-guarded in code and gated (`test_trust_circle_routes.mjs` R-3.3).

*Gate: `test_trust_circle_grammar.mjs` — "the trust-trip lens" section pins 10.1
(byte-identical total lens), 10.2 (disclosures + the empty leg), 10.3 (TRUSTED
lens-invariance) and the estate's mixed-window disclosure.*

---

## 11 · The item taxonomy — when something is created (PROP-002)

Three item classes, one creation rule each, one visual grammar each. This is the one
answer to "when do we create a notification vs an approval":

| Class | Created when | Demands | Chip grammar | In the badge |
|---|---|---|---|---|
| **Approval** | a gate whose configured mode is `review` reaches an **acting** verdict — on **every** path, including the deterministic grounding block (`runtime._park_review`, the ONE parking seam) | a decision: Approve · Approve + release (releasable sources) · Uphold | `needs you now` + owed dot | yes — server's `pending` |
| **Incident** | a protection **acted**: out-of-band (quarantine, defuse, injection catch, breaker, red-team failure, anomaly, sentinel), an in-path gate at `annotate`+ (derived), or a post-hoc **judge condemnation** of a delivered answer (PROP-001) | acknowledgment — every incident is ack-able, derived observations and the sentinel included (`derived_acks`) | severity in the **attention vocabulary**, never wire strings | yes — `unacked` |
| **Receipt** | an owner/system **act completes** (release, finding-ack, quarantine/unquarantine, sign-off) | nothing | `recorded`, grey | **never** |

**Rule 11.1 — The completeness invariant.** Anything that renders `needs you now` on
any ring resolves to exactly one open item (approval or incident) or displays its
recorded disposition. No permanently-unresolvable red: a danger reading with no route
to resolution teaches operators to ignore red. The sealed trace is never rewritten —
a derived observation's ack is a separate stored disposition ABOUT it.

**Rule 11.2 — The receipt invariant.** Acting on the inbox can only ever LOWER its
count. An act's record (a receipt) never demands an acknowledgment and never counts.

**Rule 11.3 — One severity map.** The severity of a derived event and the state the
assurance-points registry reads for the SAME turn stamp come from ONE table
(`assurance_points.DERIVED_EVENT_SEVERITY`, imported by `trust_events` with a
gate-pinned equal fallback). A fact must not be `warning` in the inbox and `danger`
on the dial. The live banner TONE honours the severity carried on the notice
(`broadcast_notice(severity=…)`), and every live trust frame — the breaker included —
travels that one emission path.

**Rule 11.5 — The severity CONSTRUCT, and per-station reporting levels
(PROP-002b).** What a severity level MEANS is defined once, as behaviour across
every surface (word · ring fold · badge · banner tone · demand):
`assurance_points.SEVERITY_CONSTRUCT`, mirrored in `trustGrammar.js`, pinned equal
by test. Three layers, only one configurable: the construct is code (the grammar
itself); what severity a finding CARRIES is derived from what happened
(`DERIVED_EVENT_SEVERITY`, code); each station's REPORTING level (`all` ·
`warning` · `danger`, default `warning`) is operator-assigned from that one
construct in the admin Trust panel. **Danger is the floor** — it always surfaces,
on every station, non-assignable. Levels filter **presentation, never creation**:
below-level items stay recorded and listable (the inbox names them "recorded
below the reporting level"), they just don't badge, fold, or show by default.
**The correlation invariant:** open items at-or-above the level FOLD into their
station's colour (danger → red, warning → amber; the measurement underneath is
never rewritten — deciding/acking releases the fold, an ack means *seen*, never
*fixed*); and a danger-band execution shortfall derives one ack-able
station-shortfall observation per (agent, station)
(`analysis.station_shortfalls`), so a red segment always resolves to a worked
item. Event→station attribution has ONE mapping per language
(`assurance_points.station_of_event` ↔ the handler's `_stationOfEvent`), pinned
in agreement by test.

**Rule 11.4 — Dispositions are per-gate.** The derived-observation ↔ review-decision
join keys on `(session, turn, source)`: deciding one gate's parked item must not
resolve another gate's observation on the same turn.

---

## Related documents

- [AGENT_TRUST_AND_ASSURANCE.md](../manuals/AGENT_TRUST_AND_ASSURANCE.md) — operator guide (Part 3 covers reading the ring)
- [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md) — where execution readings come from
- [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md) — integrity states behind SIGNED
- `TIER_5.2_POSTURE.md` — the posture-mode ladder
