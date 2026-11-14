# Migration plan — applying the trust design language

> **Status (2026-10-30):** point-in-time plan (Aug 2026). The Live Status assurance dial (`assuranceDial.js`) and the Trust Circle restyle have shipped; the live contract is [TRUST_STATUS_COMMUNICATION.md](../../Architecture/TRUST_STATUS_COMMUNICATION.md).

**Compliance.** Yes. Two imperatives govern this work, and neither is accepted as a
promise:

1. **No information previously provisioned is lost.**
2. **Every click-through path is covered or explicitly migrated.**

A promise that nothing was lost is the exact claim that turns out false three surfaces
later. Both imperatives are therefore expressed as **machine-checked coverage**, and the
mechanism that checks them is treated as more important than any individual ledger row.

---

## 0 · What "lost" means — three kinds, not one

Most migrations only guard the first. The second and third are where this platform has
actually been bitten.

| kind | definition | how it hides |
|---|---|---|
| **Fact loss** | a datum is no longer rendered anywhere | obvious once looked for |
| **Reach loss** | still rendered, but deeper — more gestures than before | every fact is "there", so an inventory passes while the surface gets worse |
| **Meaning loss** | still rendered and reachable, but now **ambiguous** | the hardest. `pending` collapsed into `not turned on`; a guardrail's *labels-only* invariant broken into leaking values; a green foundation at 1-of-9 enforcing |

**All three are in scope.** A migration that preserves every fact, moves nothing deeper
than one gesture, and still lets a reading mean two things has failed the imperative.

---

## 1 · Method — derive the inventory, never recall it

This section exists because the first draft of this plan got it wrong, and the evidence is
worth keeping.

I hand-authored an inventory of 40 information rows and 18 route rows. Extracting from
source instead found, in `agentAnatomy.js` alone, **19 panel slots against my 6 rows**, and
an `input` handler — a search field — that appeared in no row at all. My extractor was then
*also* wrong: it reported zero tooltip facts for the capsule and the circle because they
use a different construction.

**So neither method is trustworthy alone.** The rule that follows:

> **The inventory is produced by extraction, reconciled against a hand-written ledger, and
> a mismatch in either direction is a build failure.**

- extracted ∉ ledger → an unledgered fact: **fail**, someone must classify it
- ledger ∉ extracted → a stale or imagined row: **fail**, the row is wrong
- extractor cannot parse a construct → **fail loudly**, never skip

That last clause is the one that matters. Every checker I wrote today passed while the
picture was visibly broken, because each silently ignored what it could not see — the band,
the constellation, the star outline, real character widths. An extractor that shrugs is
worse than none, because it launders a gap into a green tick.

**Deliverable (Phase 1):** `test/extract_trust_inventory.mjs` — enumerates every rendered
fact, handler, `data-*` route, `aria-label` and action key across the trust surfaces, and
exits non-zero on any construct it cannot classify.

**Built, and it earned the rule immediately.** This section said *seven* surfaces; extraction
found an **eighth** (`agentKpiCharts.js`, which renders the Agents lens's KPI charts and had
appeared in no list). It also found 22 unclassified `data-*` constructs, two more files needing
an explicit exclusion, and 5 native `title` attributes breaching a standing platform rule —
against a hand-written expectation of zero. Every one of the 22 was read at its own site before
being classified, rather than dropped into an allow-list, which would only have reproduced the
hand-written inventory the extractor replaces.

---

## 2 · Invariants — true at the end of *every* phase, not just the last

| # | Invariant | Why |
|---|---|---|
| **V1** | **The station is a view over the registry, never a replacement.** Every intervention point in `assurance_points.py` renders somewhere, always | Stations are guarantees, stages are mechanisms. Projecting 10 mechanisms onto 8 guarantees is lossy *unless* the mechanisms survive underneath. Gate: registry ≡ union of rendered gates |
| **V2** | **Reachability budget: +1 gesture, never +2.** A fact visible at rest may go to one gesture; nothing may go from rest to two | Guards against reach loss. A redesign that buries things passes a fact inventory and fails the user |
| **V3** | **Four states, and each means one thing.** `pass` · `warn` · `danger` · `na`, plus `pending` as a rendering of *not yet*, never folded into `na` | Guards against meaning loss — the defect this codebase keeps paying for |
| **V4** | **Absent evidence is named, never assumed.** A pre-tracing turn reads `na` with the reason *"not recorded on this turn"* — never *"not turned on"* | The migration must not commit the very defect it is fixing, on every historical turn |
| **V5** | **Labels-only invariants survive.** Guardrail findings render category labels, never matched values | A privacy invariant, CI-tested today; a redesign is exactly where it gets dropped |
| **V6** | **Keyboard and screen-reader parity.** Any fact reachable by pointer is reachable by keyboard and announced | The strip has `aria-label` today; parity is not automatic |
| **V7** | **One vocabulary, one glossary, one navigation channel.** `stateWord` exported and shared; dimension chips route only through `openGuide` | Five copies of a vocabulary is how a rename drifts |

---

## 3 · Prerequisites — the instruments cannot be honest without these

**P1 · A clean layer must leave a trace.** `scan_inbound` / `scan_outbound` return `None` on
a clean pass; grounding and honesty do the same. A guardrail that ran and passed is
therefore indistinguishable from one that does not exist — which is why the shipped strip
says *"not enabled"* about a working guard. Stamp a minimal `{mode, action:"pass"}`.
This is an **auditability** defect before a UI one: the sealed trace cannot answer *"did
this protection run?"*

**P2 · The intervention registry.** `agent/assurance_points.py` declares every point that
can **alter, withhold, constrain or attest** an answer, its station, and its applicable
classes. Six exist in code and appear nowhere today: preflight SQL, retrieval firewall,
corpus quarantine, sensitivity ceiling, review parking, and memory firewall — the last
recorded as a **deliberate exclusion** (it protects *future* turns, not this one).

**P3 · One vocabulary source.** Export `stateWord`; extend the four-site assertion to five.

**P4 · The extractor** (§1). Nothing else in this plan is verifiable without it.

---

## 4 · Ledgers — a **seed**, to be reconciled, not trusted

The ledger below is the starting hypothesis. Phase 1 reconciles it against extraction and
the reconciled artefact — not this table — becomes the contract.

Each row carries a status: `kept` (same place) · `moved` (same fact, new location) ·
`merged` (folded in, and the target must carry both) · `dropped` (**deliberate, with the
reason in the row** — never silence).

### 4.1 Trust Journey strip → Assurance dial

| # | Information | Destination | Gestures now → after | Status |
|---|---|---|---|---|
| I1 | 10 stage glyphs, icon + state | 8 station segments; stages move to the station's gate list | 0 → 1 | `moved` |
| I2 | Per-stage tooltip, multi-line | Station panel: Evidence + the gate's own line | 1 → 1 | `moved` |
| I3 | Scope databases · out-of-scope list | GROUNDED → Evidence | 1 → 1 | `moved` |
| I4 | Guardrail categories **(labels only — V5)** | GUARDED → gate line | 1 → 1 | `moved` |
| I5 | Chain tip hash · step count | PROVEN → Evidence | 1 → 1 | `moved` |
| I6 | Authority origin | BOUNDED → Configuration | 1 → 1 | `moved` |
| I7 | Tool-call count · denial flag | BOUNDED → gate line | 1 → 1 | `moved` |
| I8 | OBDA lane + counts | SCORED → gate line | 1 → 1 | `moved` |
| I9 | Eval verdicts (async-filled) | SCORED → Execution | 1 → 1 | `moved` |
| I10 | *"N layer(s) acted"* / *"all layers quiet"* | Summary, four-word vocabulary; `(s)` dies | 0 → 0 | `moved` |
| I11 | "Trust only" filter | **dropped** — the dial *is* the trust view; the filter existed only to extract trust events from a mixed trace | — | `dropped` |

### 4.2 Answer capsule → one dial + station sections

| # | Information | Destination | Gestures | Status |
|---|---|---|---|---|
| I12 | Mini ring (stages, radial) | The dial — the two collapse into one | 0 → 0 | `merged` |
| I13 | 9–10 linear stage glyphs | Station sections | 0 → 1 | `moved` |
| I14–I20 | GROUNDING · SOURCES · PROVENANCE · sealed chain · Verify · Export · Review · released original | The matching station sections, bodies unchanged | 0/1 → 1 | `kept` |
| I21 | Helpful / Unhelpful | Outside the dial; marked non-station so coverage can tell deliberate from dropped | 0 → 0 | `kept` |

**Capsule invariant:** sections ≡ stations, 1:1, rendered from `ORDER`. Derived, never a
parallel list — a second ordering is how the trace stopped being obvious the first time.

### 4.3 Trust Circle → markers on a band

| # | Information | Gestures | Status |
|---|---|---|---|
| I22–I26 | icon · label · sub-label · two-attribute reading · four hover slots · pinned actions · `?` derivation with clickable chips | unchanged | `kept` |
| I27 | Routing summary | 0 → 0 | `moved` |
| I28–I34 | legend · last-event + pulse · story · picker · loop arc · membrane · scope switch | unchanged | `kept` |

**Post-merge count changes, justified (all gains):** `assuranceDial.js` fact-attr +1
(`data-ad-intervention` — the intervention card's mechanism identity + idempotence key),
fact-html +1 and state-key +2 (the three intervention cards and the suppression state) —
the card decision's additions, no drops.

**Merge decisions (operator-approved, recorded):** (1) the dial ships DEFAULT-ON — the
plan's default-off clause guarded an unverified R-2; six operator-driven defect-fix
cycles on real turns are that verification, and `?dial=0` remains the latch. (2) The
card rule is applied: the trace records what HAPPENED, the dial reports what IS — the
clean-pass grounding card and the governed-data card leave the trace (their stamps and
dial segments remain); preflight blocks, retrieval-firewall defuses and review parking
gain trace cards rendered from the turn's own assurance readings at the inject seam
(live + reload are one implementation). (3) Judge sampling on scoring health is a
KNOWN, DELIBERATE limit: on this estate 18 of 35 turns carry a gating failure, so
anomaly-sampling on failures would judge ~half of all turns — real token spend for
evidence the deterministic layer already carries. Revisit when the knowledge-dimension
failures are fixed and a gating failure is rare enough to be worth a judge's opinion.

**R-4 changes, justified:** I37 executed — the category colour legend is REPLACED, not
merely dropped: the same `_legend()` slot now explains what colour MEANS (blueprint map ·
trust posture · accent action), so the inventory count holds while the category channel
retires. The sage metaphor colour, the orange "born here" mark and the steel-gradient
star (main + editor mini) all leave with it; the subject is schematic and carries the
plus (Change 3), and the starfield is 34 deterministic literals numerically filtered
against every plate — the filter re-runs in the R-4 gate.

**R-3 count changes, justified:** `trustCircle.js` state-key 4 → 5 (gain — arrow-key
navigation, the R17 gap the route gate found); fact-html 11 → 10 (NOT a loss — the estate
centre label moved from an `innerHTML` tspan sink to `textContent` when the identity
line moved below the subject star; the fact renders unchanged through a safer sink).

### 4.4 Agent Anatomy

| # | Information | Status |
|---|---|---|
| I35 | 5 faculties, **atlas and agent mode** — 19 extracted slots, not 6 | `kept`, **ledger under-counted; reconcile first** |
| I36 | 2 foundations | `kept`, now worst-of (was green at 1-of-9 enforcing — a **meaning loss** already shipped) |
| I37 | Category colour legend | **dropped** — its green sat beside `--trust-pass`, so a map read as a health reading; category moves to type |
| I38–I40 | membrane · constellation · centre · panel | `kept` |
| **I41** | **Search / filter (`input` handler)** | **`kept` — found by extraction, absent from the first ledger** |

### 4.5 Routes

| # | Gesture | Today → after | Status |
|---|---|---|---|
| R1 | Hover a stage | tooltip → reading in the **fixed-height** summary; no resize | `moved` |
| R2 | Click a stage | scrolls the trace to the matching step (**5 scroll targets extracted**) → station panel, *and the trace scroll survives on the gate row* | `moved` |
| R3 | Click again | — → collapses; both routes to a station toggle identically | new |
| R4–R8 | Ring: hover · pin · `?` · dimension chip → Guide · all **8 action keys** (`sign-agent` `verify-agent` `attest-deployment` `inbox` `roster` `posture-matrix` `posture-tools` `admin-eval`) | unchanged | `kept` |
| R9–R11 | roster glyph · chat-header glyph · capsule ring → open the circle | unchanged | `kept` |
| R12 | Capsule section | — → hover **locates** (highlight + scroll, never filter); click expands | new |
| R13 | Anatomy faculty → `openProfileForEdit(profileId, section)`, section-precise | unchanged | `kept` — **highest risk, §6** |
| R14–R16 | foundation → posture matrix · centre → picker · coordinator constellation → pivot + breadcrumb | unchanged | `kept` |
| R17 | Keyboard: arrows · Enter · Escape | unchanged, every instrument | `kept` |
| R18 | Story: play · pause · next · back · exit | unchanged | `kept` |
| **R19** | **Anatomy search input** | unchanged | **`kept` — extraction-found** |

---

## 5 · Historical turns — the migration must not commit its own defect

Phase 0 changes what every turn persists. Turns written **before** it lack the clean-pass
stamps, so an instrument that reads an absent stamp as *"not turned on"* would tell the same
lie we are fixing, on every historical turn, forever.

**V4 in practice:** absent stamp + turn predates tracing ⇒ `na` **with the reason
"not recorded on this turn"**, and the summary says how many layers reported. No fifth
state, no backfill, no date-gating the UI — the honesty lives in the words, which is
exactly what Rule 8.3 already requires.

---

## 6 · What the prototypes did **not** show

Highest risk, because nobody has seen these in the new language.

| Surface | Not prototyped | Rows |
|---|---|---|
| Trust Circle | story · agent picker · live pulse + last-event · estate↔agent scope | I29–I31, I34, R18 |
| Agent Anatomy | **agent mode** (prototype is atlas only) · coordinator constellation · the 112px mini star in the profile-editor header · section-precise editor jumps · **search** | I35, I41, R13, R16, R19 |
| Roster · coordination tree | mini glyph and per-node glyphs under station semantics | I22, R9 |
| Trust badge | unchanged — the *published* vocabulary the ring borrows (Rule 8.4) | — |

---

## 7 · Releases, not phases — grouped by what a user sees together

Migrating surface-by-surface leaves two co-visible surfaces speaking different languages —
the precise confusion that started this work. So the unit of shipping is a **release of
co-visible surfaces**.

| Release | Contains | Rationale |
|---|---|---|
| **R-0** ✅ | P1 trace · P2 registry · P3 vocabulary · P4 extractor | Nothing user-visible; everything else depends on it |
| **R-1** | Contract rules + gate coverage (§9) | Rules become enforceable before anything is built on them |
| **R-2** | **Live Status dial + answer capsule together** | Seen in the same screen on the same turn |
| **R-3** | **Trust Circle + roster glyph + coordination tree together** | All live in the Agents view |
| **R-4** | Agent Anatomy (incl. agent mode) | Standalone surface |
| **R-5** | Chat-header glyph sweep | Last consumer of the old stage semantics |

**R-0 landed.** What it cost, recorded because the estimates in this plan are only as
good as the evidence behind them:

| item | outcome |
|---|---|
| P1 trace | all three answer gates + four out-of-band ones; 8 stale assertions in 5 suites had pinned *silence* as the contract and were moved |
| P2 registry | 15 live points + 1 declared exclusion; the strip's 10 stages all survive as rows |
| P3 vocabulary | `stateWord` exported; TC-50 extended from four sites to five |
| P4 extractor | first run: **22 unclassified constructs, 3 unclassified files, 5 contract breaches** — against a hand-written expectation of zero |

Two findings worth keeping. First, grounding had **three** `to_event()` sites and only one
was tagged — the enforce/judge path, the one that actually acts, went untagged; all three
now route through one helper. Second, the first firewall wiring recorded every chunk as
*screened* regardless of the guardrails master switch, so with guardrails off the registry
would have reported a protection that never ran as one that ran clean — the exact defect
being fixed, reintroduced by the fix, and caught only by driving the real formatter instead
of a fixture.

**Parallel run and revert.** Each user-visible release ships behind a flag with both
renderers present; revert is flipping the flag, not reverting code. The flag is removed
only after the release's ledger rows are verified in production.

---

## 8 · What proves a release

A release does not close on "it looks right". It closes on:

1. **Reconciliation is clean** — extractor ≡ ledger, both directions, no unclassified rows.
2. **Every ledger row flipped to verified**, or amended to `dropped` with a reason, in
   review. A row that cannot be flipped blocks the release.
3. **Gate assertions added and mutation-tested** — reverting the change must fail exactly
   the intended assertion. An assertion that cannot fail is not an assertion.
4. **V1–V7 re-checked**, not assumed.
5. **Layout verified numerically** against the full obstacle set — text at real character
   widths, bands and rings as annuli, the star outline vertex-walked, decoration included,
   and **same-element-family pairs not excluded**. Every collision that reached review today
   was a label against its own sub-label or a reading across its own marker.
6. **Both themes and both width extremes** rendered (228px and 416px for Live Status).

---

## 9 · Rules promoted into the contract at R-1

These currently live only in a prototype no CI reads:

- **Pending ≠ absent.** A layer that has not reported yet renders distinctly from one that
  is off.
- **The star marks the subject; the plus means you can change it.** One glyph, one meaning,
  every surface. A mark may not promise what its surface cannot do.
- **Ticks mark what is engraved** — Live Status: detail behind it. Trust Circle: an act is
  owed.
- **Weight before colour.** Attention legible without hue.
- **Three colour channels** — trust = attention · blueprint = the map · accent = what you
  can act on. Nothing else takes colour.
- **Locate, never filter.** A spine highlights and scrolls; it never removes content.
- **Hover must not resize.** Readings written on hover go into a fixed-height region.
- **Reachability budget** (V2) and the **three kinds of loss** (§0), so the next migration
  starts from them rather than rediscovering them.

---

## 10 · The honest risks

| Risk | Mitigation |
|---|---|
| The extractor becomes the new blind spot | It must fail on unparsed constructs, and R-1 mutation-tests it by deleting a known fact and confirming the build breaks |
| 8 stations cannot carry 14+ mechanisms | V1 makes the station a *view*; if a mechanism has no home the registry fails the build before the UI does |
| Anatomy agent mode is the least-understood surface | R-4 is its own release, and agent mode is reviewed explicitly, not folded into atlas |
| A ledger row is quietly amended to `dropped` to unblock a release | `dropped` requires a written reason and review; the gate asserts the reason is non-empty |
| We ship a prettier version of the same lie | R-0 precedes every user-visible release — no exceptions |
