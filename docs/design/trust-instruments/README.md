# Trust instruments — design prototypes

Three interactive HTML prototypes for the platform's trust surfaces, all speaking one
design language. **Open them in a browser** — they are self-contained (no build, no
network, no dependencies), theme-aware, and keyboard-navigable.

| File | Surface | Status |
|---|---|---|
| [`assurance-ring.html`](assurance-ring.html) | **Live Status** dial + the answer capsule | shipped as `static/js/handlers/assuranceDial.js` (as of 2026-10-30; the journey strip `trustJourney.js` remains as a summary surface) |
| [`trust-circle.html`](trust-circle.html) | **Trust** view → Trust Circle (formerly *Agents*) | design reference for the shipped ring (`trustGrammar.js`, `agentPerformanceHandler.js`) |
| [`agent-anatomy.html`](agent-anatomy.html) | **Anatomy** panel | design reference for the shipped panel (`agentAnatomy.js`) |

These are **design references, not shipped code.** The implementation contract is
[TRUST_STATUS_COMMUNICATION.md](../../Architecture/TRUST_STATUS_COMMUNICATION.md); where
a prototype and the contract disagree, the contract wins. Data in the prototypes is real
(read from this instance's persisted turns) so the quiet and the alarm states could both
be judged — a design that only works in alarm is a bad design.

---

## The language

**Markers on a band.** Stations are positions on one continuous arc, not boxes floating on
a field. The Live Status dial is the band alone; the Trust Circle is the band with markers
on it. Same instrument, two scales, so learning one teaches the other.

**Three colour channels, three meanings.** Nothing else gets colour.

| channel | means | where |
|---|---|---|
| trust green / amber / red / dashed-slate | **attention** — quiet, worth a look, needs you now, not turned on | anything with a posture |
| blueprint blue (`--map`) | the **map** — never a reading | anatomy star, spokes, labels, membrane, constellation |
| accent orange | what you can **act on** | hover, focus, the plus |

**Weight before colour.** Arc thickness rises with attention (hairline dashed → heaviest at
*needs you now*), so state survives a colourblind reader and reads before the hue does.

**The star marks the subject; the plus means you can change it.**

| surface | centre | because |
|---|---|---|
| Live Status dial | star, **no plus** | one answer, from an agent you cannot change here |
| Trust Circle | star **+ plus** | choosing another agent is a real act |
| Anatomy | star **+ plus** | same |

One glyph, one meaning, three surfaces. A mark can never promise something the surface
cannot do.

**Radial ticks mark what is engraved.** On the Live Status dial: one tick = detail behind
it, two = a gate acted, none = the click would not pay. On the Trust Circle every station
has detail, so the tick earns its keep differently — it marks where **an act is owed**.

**Progressive disclosure, three gestures.** Rest shows the dial and a summary. Hover writes
the station's reading *into the summary region*, which has a fixed height — so a pointer
crossing the dial never resizes the panel and the trace below never jumps. Only a click
expands, and clicking again collapses.

---

## Verifying a change

Every layout in here was checked numerically before publishing, and the checker was wrong
four times — each time because it verified the list I had written rather than what was on
the canvas. Anything you add must be added to the obstacle set:

- all text boxes, at **real** character widths (mono at `.14em` tracking is ≈ 0.74 × size
  per character, not 0.6 — a 14-character label is 145px, not 126)
- the **band** and the tick ring, as annuli
- the **star outline**, walked vertex by vertex — a bounding box says "inside" while the
  text crosses an arm
- the **constellation** and its connecting segments
- the **starfield** (deterministic, from a seeded LCG — a field that reshuffles per render
  cannot be reviewed, and a collision cleared today returns tomorrow)

Same-element-family pairs must **not** be excluded. The collisions that shipped were a
label against its own sub-label and a reading across its own marker.

---

## Applying this to the platform

[MIGRATION_PLAN.md](MIGRATION_PLAN.md) — how the language reaches every surface without
losing information or interactions. It defines the **three kinds of loss** (fact, reach,
meaning), seven invariants that hold at the end of *every* release, and the rule that the
inventory is **extracted from source and reconciled against a written ledger**, because a
hand-written inventory of these surfaces was demonstrably incomplete: 6 rows against 19
extracted slots in the anatomy alone, and a search handler that appeared in no row.

## The foundation — built (release R-0)

The prototypes assumed two things the platform did not do. Both now exist, because building
the instruments first would have shipped a better-organised version of the same lie.

**1 · A clean layer leaves a trace.** All three answer gates returned `None` on a clean
pass, so a working guardrail was indistinguishable from an absent one — which is why the
shipped strip reports *"not enabled"* for a guard that ran and passed. An auditability
defect before a UI one: the sealed trace could not answer *"did this protection run?"*

Each gate is stamped in the shape its render sites already read — the guardrails emit only
when they fire, so those sites gate on `acted`; grounding already emitted on a clean
verdict, so its stamps deliberately carry no `decision` key and stay audit-only. Four
states are now distinguishable, and none of them is silence:

| stamp | meaning |
|---|---|
| `ran:false action:"off"` | switched off — the reason names master vs profile |
| `ran:false action:"na"` | inapplicable — nothing in this answer to ground |
| `ran:true action:"pass"` | ran and found nothing |
| `ran:true action:"error"` | failed — **never** recorded as a pass |

**2 · The intervention registry.** `src/trusted_data_agent/agent/assurance_points.py`
declares every point that can alter, withhold, constrain or attest an answer. The six that
appeared nowhere are in it; the memory firewall is registered as a **deliberate exclusion**
with its reason (it screens what a turn *writes*, protecting future turns rather than this
one). Three of them fire at seams with no turn context, so `assurance_tally.py` carries a
contextvar opened at the turn boundary — the pattern the Agent Principal already uses.

**Verification.** `test/extract_trust_inventory.mjs` enumerates every fact, handler, route,
tooltip and aria label across the eight surfaces and **fails on anything it cannot
classify**. That clause is the point: every checker written for this design passed while the
picture was visibly broken, because each silently skipped what it could not see. On its
first run it found 22 unclassified constructs, 3 unclassified files, and 5 native `title`
attributes breaching a standing rule.

```
node test/extract_trust_inventory.mjs           # verify (fails on drift)
node test/extract_trust_inventory.mjs --write   # re-baseline, after classifying the change
```

A count that **drops** is potential fact or reach loss and must be justified in the ledger,
never re-baselined silently.
