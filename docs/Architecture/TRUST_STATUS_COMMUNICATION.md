# Trust Status Communication — the contract

How every assurance surface reports status: the Trust Circle (the reference
implementation), the per-turn assurance dial, the mini rings, the journey strip, the
answer capsule, the Quality chips and the Assurance Inbox.

**Ten rules.** Each names the section of
[`test/test_trust_circle_grammar.mjs`](../../test/test_trust_circle_grammar.mjs) that
enforces it — a rule stated but unmeasured is a rule that will be broken, which this
contract learned five times before the gate existed. The incidents, the rationale and the
fine-grained per-surface forms live in the
[casebook](TRUST_STATUS_COMMUNICATION_CASEBOOK.md), verbatim and with their original rule
numbers. When the core, the casebook and the code disagree, the gate decides and the code
is the bug.

Visual tokens: [`static/css/trust.css`](../../static/css/trust.css). The one vocabulary:
[`static/js/handlers/trustGrammar.js`](../../static/js/handlers/trustGrammar.js), mirrored
from `core/posture_modes.py` and `agent/assurance_points.py` and pinned byte-equal by
`test/test_assurance_points.py`.

---

## The ten rules

**1 · Two attributes, fixed order, never collapsed.** Every station reports
**configuration** (would this control work? — design effectiveness) and **execution** (did
it work over the window? — operating effectiveness), in that order. A missing side renders
`—`; it never vanishes, and it is never fabricated to make a pair. The size of the scope is
never rendered as a value.
*Gate:* `grammar invariants across representative scenarios` · `no station may render a bare scope-size count` · `#62 — the execution figure and its own denominator`.
*Casebook:* Rules 1.1–1.3, 3.5–3.7.

**2 · Colour is attention, and the drill-down names the driver.** Glyph colour answers
one question — *how urgently should a person look here* — as `worst-of(configuration,
execution, open inbox items)`: quiet · worth a look · needs you now. It is never a verdict
(the verdict is the word on the chip), and a composite is honest only because the panel's
`Why` slot names which attribute drove it. `danger` is reserved for a detected discrepancy
or a measured bad outcome.
*Gate:* `attention priority — colour is triage, not a dimension` · `REVIEWED — a zero count must never hide a derived observation`.
*Casebook:* Rules 4.1–4.5, 11.1–11.5.

**3 · A posture is a reading; a finding is attention.** A gate mode an operator CHOSE is
never an alarm: *enforce* (with or without keeping originals for review) reads **pass**;
*watch* (with or without disclosure) reads **watching** — quiet green because nothing needs
you, but HOLLOW and thinner than *pass* because nothing is protected (the shape and the
word carry "not protecting", never the hue); it is never amber; *off* reads **not turned
on** (dashed — dashed belongs to the absence family alone). ONE map
(`POSTURE_STATE`) and ONE label map (`POSTURE_LABEL` in JS, `POSTURE_LABELS` in `core/posture_modes.py` — the profile editor's words — the
stored tokens `annotate` and `review` never reach a reading surface bare) serve the ring,
the posture matrix, the Anatomy and the editor. The attention fold reads attention first,
posture second, absence last; a watching station is never summarised as quiet.
*Gate:* `W1 — posture is a reading: one map, one fold, one aggregate, one chip` · `estate aggregation — worst-of`.
*Casebook:* Rule 4.4 (extended), §8 Residue (resolved), TRUST_CIRCLE_LEGIBILITY.md W1/W4.

**4 · Aggregate worst-of across agents, never best-of.** Classify per agent first, then
fold through the one declared policy: any danger → danger · any warn → warn · all pass →
pass · all na → na · otherwise watching. The value carries the magnitude (`3/10`), the
glyph carries the state, and the panel names the laggards. `na` is excluded from attention
tallies; an unresolvable reading is `warn`, never `na`.
*Gate:* `estate aggregation — worst-of` · `estate execution — a red agent is never hidden by a healthy average`.
*Casebook:* Rules 5.1–5.5.

**5 · Measurement is a lane: counted at JUDGED, labelled by its guarantee.** A guarantee
station reports what its MECHANISMS did during the turn (they can change the answer); every
scored dimension is counted once, at JUDGED, where measurements grade the finished answer.
The station a check is *about* is a label for grouping, never a counter. On a guarantee
station the measurements appear as ONE graded line that opens JUDGED; the chip grid lives
at JUDGED and in the Guide. Each station carries its **lane** in its eyebrow. Two measures
of one property that disagree are reported as a disagreement, never weighted or silenced.
*Gate:* `the lane cut — a guarantee station reports its mechanisms, not measurements` · `W3/W6 — the lane in the eyebrow, the graded line, live-only explainer rows`.
*Casebook:* Rules 3b.6, 3bb.1–3bb.4, 3c.1–3c.3, 3.0c.

**6 · Every figure carries its own denominator, and nothing is clean from nothing.** A rate
anchor's qualifier expands it in words with its unit (`9 of 11 turns clean`); below three
samples the fraction is shown, not the percentage; a figure is never rendered over a
population its sentence does not name; a structural zero is stated as the structural fact;
an empty evidence set renders as absence, never as a pass.
*Gate:* `#62 — the execution figure and its own denominator` · `#64–#66/#68 — solo class-sweep findings` · `derivation subject — window and exec describe what the figure counts`.
*Casebook:* Rules 3.4–3.7, 3b.8–3b.9, 1.3.

**7 · No slot restates another, and no wire token reaches a reader.** The panel's four
slots — Configuration · Execution · Why · Evidence — are structured rows with distinct
jobs; the headline carries the consequence once; Evidence is mandatory and carries the
instances; the derivation explainer carries only rows with a live value and routes the
method to the Assurance Guide; a `snake_case` token, a bare storage mode or a hand-written
`scored → JUDGED` never reaches a slot. Dimension names come from `QualityDimensions.label()`.
*Gate:* `slots must not restate each other` · `qualifiers are written for a person, not copied from the wire` · `gate hardening — one vocabulary, real labels, renderer surfaces, one subject`.
*Casebook:* Rules 3.0–3.3, 3b.1–3b.5, 3b.13.

**8 · `na` means nothing is owed; outstanding work is amber, named, and offered.** An
absence is deliberate (a chosen posture — `na`) or outstanding (an act required and undone
— `warn`), decided by a practice signal scoped to the smallest subject that answers
honestly and failing toward in-use. Actions derive from state, render only when the station
is pinned, and only where they can be performed; the offered act is the act that is owed.
*Gate:* `unsigned — "not turned on" only while signing is not in use` · `station actions must follow the state, not the station` · `SIGNED — a blocker names the artefact when the backend supplies one` · `#24 — the master switch reaches the ring`.
*Casebook:* Rules 8.1–8.5, 4b.1–4b.10, 4c.1–4c.2.

**9 · Shape before colour; three colour channels and no fourth.** Every state is legible
without hue (solid · half fill · 2px outline · sparse dash · long dash · dense moving dash
· ultra-sparse dash); colour is spent on three meanings only — attention (green · amber ·
red · dashed slate), the map (blueprint blue), actionability (accent orange); every
rendered channel is in the legend; confidence is shown by shape, never by borrowing amber.
*Gate:* `§9 form rules — how a reading is drawn (R-1)` · `PROP-002 — one ring grammar (four channels · six states · one vocabulary)`.
*Casebook:* Rules 9.1–9.8, 3b.7–3b.9, 4.0–4.0b.

**10 · One source per rule; one registry per vocabulary; the glance layer is built from
the readings.** Station assembly (`_assemble`), the attention fold, the aggregate, the
posture maps, the station registry (key · label · character · question · lane — one table,
Python and JS pinned equal), the station icons (`STATION_ICONS`, read by the Trust Circle AND
the per-turn dial — a station looks the same on the answer and on the ring, and the two draw
each state in the same colour token), the severity construct and the band/state bridge each
exist exactly once, and every surface reads them. The verdict line — one sentence that leads the
ring and is the roster tooltip — is built from the stations' own values and traces every
figure to a slot. A hand-rolled copy of any of these is the defect this contract exists to
prevent.
*Gate:* `W5 — one station registry; a station leads with the question it answers` · `W2 — the verdict line is built from the stations and leads the panel` · `trust mark — a blended figure names its components` · `integrity states — all four wire strings + the unknown case`.
*Casebook:* §6 (the single-source table), Rules 3b.5, 10.1–10.4.

---

## Adding a station, in one paragraph

Add its row to `assurance_points.STATION_TABLE` and the `trustGrammar.STATIONS` mirror
(key · label · sub · q · lane). Build it with `_assemble(key, design, execution, opts)`;
give both attributes `{ state, text, detail }` (an absent side is `state: 'na'`, empty
`text`, a `detail` that says why); make `state` come from `_postureAggregate`; write the
`headline` as the consequence for the answer, `why` as the rule, `evidence` as named
instances; derive `actions` from state; give it a `derivation` with a window and, if it
labels measurements, a `graded` line; add a clause to `_verdictLine`; add the key to the
gate's `STATION_KEYS` and an assertion for any new rule. The casebook's §7 has the long form.

## Related

[TRUST_STATUS_COMMUNICATION_CASEBOOK.md](TRUST_STATUS_COMMUNICATION_CASEBOOK.md) ·
`TRUST_CIRCLE_LEGIBILITY.md` ·
[ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md) ·
[AGENT_TRUST_AND_ASSURANCE.md](../manuals/AGENT_TRUST_AND_ASSURANCE.md) ·
[TRUST_UX_FAQ.md](../manuals/TRUST_UX_FAQ.md)
