# The Assurance Substrate — one assessment, two lanes

**What it is.** Every trust property is assessed up to twice: once **in flight** by a
mechanism that can act on the answer, and once **after the turn** by a measurement that
grades it. This package makes that ONE assessment, consumed by two policies.

```
assess(ctx)           -> Finding    pure; what was found, no policy
decide(finding, mode) -> Decision   in-flight; may withhold, annotate, redact
score(finding, bands) -> Verdict    post-hoc; grades, never acts
```

**Why.** Where the lanes shared nothing, three things followed — all observed in this
codebase, all measured in [TRUST_SYMMETRY_MAP.md](TRUST_SYMMETRY_MAP.md):

1. **A station could not state a reason.** GROUNDED could say *"1 withheld"* but not
   *"withheld because 3 spans were unsupported"* — the gate's finding and the scorer's
   number were unrelated artifacts.
2. **The two disagreed for structural reasons** and the disagreement looked epistemic.
3. **A lesson learned on one side could not propagate** to the other.

`Finding.evidence` closes the first; `Finding.metrics` closes the second and third.

---

## Files

| file | holds |
|---|---|
| `contract.py` | `Finding` · `AssessmentContext` · `Assessor` ABC · `Severity` |
| `capabilities.py` | `AssessorCapability` · `Lane` · lane eligibility, **derived** |
| `properties.py` | the closed vocabulary of assessable properties |
| `registry.py` | register · **bind** · resolve · the binding table · `symmetry()` |
| `conformance.py` | the ONE place a binding is validated |
| `run_context.py` | persisted turn → `AssessmentContext` |
| `store.py` | the deployment's binding OVERRIDES (no row · rebound · explicitly NOT ASSESSED) |
| `inflight.py` | the in-flight seam `augment()` — where a bound module reaches a gate (union-only) |
| `controls.py` | third-party controls — the closed acts and chokepoints, the platform ladder |
| `__init__.py` | `bootstrap()` (shipped defaults → overrides) · `INFLIGHT_CONSUMERS` · `SEAM_CONSULTED` |
| `assessors/<property>.py` | one module per PROPERTY, never per lane |

**Twelve shipped properties** (`properties.py`): `answer_support` · `answer_scope` ·
`context_use` · `absence_claim` · `retrieval_sufficiency` · `citation_provenance` ·
`identifier_provenance` · `output_sensitivity` · `request_integrity` · `data_governance` ·
`tool_authority` · `tool_repetition`. A module pack may register more, always namespaced
`<pack>.<key>` ([ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](ASSURANCE_MODULE_PLANE_ARCHITECTURE.md)).
Live state (Oct 2026, `scripts/symmetry_state.py`): **10 of 12 share one assessor across
both lanes**; `tool_authority` is score-only by design (in flight the boundary is enforced at
the tool chokepoints, not measured); `request_integrity` has no shipped assessor — the inbound
guard assesses natively and the property exists so a pack module can reach the guard through
the seam.

**Which gate consults what in flight** is declared once, in `INFLIGHT_CONSUMERS`
(`context_use` → grounding gate, `retrieval_sufficiency` → honesty gate, resolved directly;
`output_sensitivity` → outbound guard, `request_integrity` → inbound guard, `answer_support` and
`answer_scope` → grounding gate, consulted through the seam). `test_assurance_substrate`
asserts it against the real call sites; `assurance_points.consulted_properties()` reads it so
the Guide's *modules during the turn* and the dial's `decides` mark come from one declaration.

---

## The five rules

### 1 · A property is named once; both lanes are named from it

`grounding_gate` and `grounded_overlap` gave no hint they were two views of one thing. So
the **property** carries the canonical name, and a module is named for what it assesses —
`assessors/answer_support.py`, never `assessors/grounding_gate.py`. "Remap this module"
then means *bind `answer_support` to a different assessor*, and **both lanes follow from
one act**. Gate-asserted: every assessor filename must be a property key.

A property is stable; an assessor is swappable.

### 2 · Bindings are DATA, so a UX can remap them

A call site asks `registry.resolve(property, lane)` and **never imports an assessor**. That
is the entire mechanism by which one remap reaches both lanes.

### 3 · A lane is a CAPABILITY, not a preference

Some remaps are not unwise but **unsafe**, and a warning in a dialog does not prevent them:

- an unconditional in-flight assessor runs on **every** turn → must be `PURE`;
- a post-hoc assessor replays a persisted turn → must be `REPLAYABLE`, or it would score
  the world as it is *now* rather than as the turn saw it;
- a model-calling judge may run in flight only **behind a trigger**.

`conformance.check_binding` also refuses a binding whose inputs the lane cannot supply —
the invisible failure, where a module binds, runs, sees nothing, and reports clean forever.

### 4 · Absence is loud

`resolve` returning `None` means **NOT ASSESSED**, never assessed-clean, and the package
ships no permissive stand-in. An assessor that raises, lacks an input, or returns the wrong
type **abstains**. A silent clean is how a protection gets switched off unnoticed —
observed live: one malformed config block disabled four gates behind a fail-open `except`,
and every surface still read healthy.

**Lost evidence abstains too (Oct 2026).** `AssessmentContext.evidence_lost` is set when
evidence the turn HAD can no longer be read — an attachment deleted from Files since
(PROP-010). `Assessor.run` makes every assessor that requires `chunks` abstain in one
wording: what is left of the support set is not what the answer was written from, so grading
against it would convict a figure for its source having been deleted. See
[FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md) §3.

### 5 · A gate that acts CHANGES THE SUBJECT

In flight a gate assesses what the **model** produced; afterwards the delivered text may be
what the **platform** wrote. The lanes are then correctly assessing different subjects —
**a divergence by design, not a symmetry defect.**

`AssessmentContext.platform_authored` carries the reason;
`Assessor.subject_is_model_output` declares whose text an assessor is about; `run()`
enforces it **once**.

It is deliberately **not uniform**:

| assessor | subject is the model? | because |
|---|---|---|
| `answer_support` · `absence_claim` | **yes** | "did the *model* ground its claims?" — a platform notice is not its answer, and it *quotes the spans it reports as unsupported* |
| `output_sensitivity` | **no** | "does what reached the *reader* carry sensitive content?" — the notice IS the subject; abstaining would go blind exactly when a gate acted |

Measured once: a blocked turn scored **3% context utilization** — a damning number about a
~30-word notice the model never wrote.

---

### 5a · A deployment's availability switch is declared on the PROPERTY, enforced in the envelope

Some properties sit behind a deployment-wide availability (`data_governance` →
`component:obda_read`, the OBDA-READ component's global gate — the planes are components
since Oct 2026; the former `OBDA_ENABLED` master is gone). That switch is declared **once**,
as `Property.master_switch` (the field keeps its name; its value names a component), and
enforced **once**, in `Assessor.run` through `core/obda_planes.plane_available`: while the
component is off the assessor abstains with one sentence — *"not assessed — the data
governance lane is off here (Administration → Components)"* —
and every reader inherits it: the post-hoc scorer (`f.abstained`), the station panel's
module line (`active_modules`), and the mechanism reader (`_read_obda` reads the same
function for its no-stamp case). Before this the three readers each kept — or forgot —
their own copy of the gate, and one GROUNDED panel read *"Governed data · no data
operations on this turn"* (false: the turn had queried) one line above *"1 of 1 found
something"* (the module line, ungated), about one turn (prod session 4f59f838, Sep 2026).
The assessor itself stays pure; the switch is read at the envelope, and a historical turn
read under today's switch says *"off here"*, never *"no data operations"*. Gate:
`test_assurance_substrate` (off ⇒ abstain naming the route, never a finding) and
`test_assurance_points` §O (the three readers agree in both switch states).

## The reverse link — which measurement grades a property

Rules 1 and 2 give a property its **assessors** (what runs, per lane). They do not give it its
**measurement** — the scored dimension that grades it afterwards and appears on the Scorecard.
That link existed only as a `resolve("<property>", POST_HOC)` call inside a scorer body: *code,
not data*, so no surface could read it. The Guide's property entries consequently said
*"No scored dimension measures this one"* while the lanes row directly beneath named the module
grading it — one entry, two answers.

A scorer now **declares** the property it grades, beside the call that proves it:

```python
class GroundednessOverlapScorer(Scorer):
    dimension = "grounded_overlap"
    property = "answer_support"   # the assurance property this grades
```

The reverse map is **derived** (`property → [dimension]`) at the one layer holding both
registries — the vocabulary endpoint. It is not built inside the substrate, because the
substrate must not import `eval`: `eval` imports *it*.

### The declaration is verifiable against the code

A free-floating attribute drifts the moment a scorer stops consuming its property, so the gate
checks both directions: **a class whose body resolves a property must declare that same
property, and a class that declares one must resolve it** — with the only exceptions recorded
**by name and reason**:

| dimension | property | why it does not resolve through the registry |
|---|---|---|
| `must_not_call` | `tool_authority` | resolves inside the shared `_authority_finding` helper, not the class body |
| `honest_absence` | `absence_claim` | reads the gate's **stamp first**, which the assessor cannot express — see below |

That list is the point of the gate. Without it, "declares a property it never reads" and
"legitimately routed another way" look identical.

### When routing through the registry would be a REGRESSION

Symmetry is not always the improvement. `honest_absence` looks like the same bypass the
governed-data scorer had, and is not: under `enforce` the honesty gate **rewrites the answer**
into an honest retrieval-failure, so a re-derivation from *delivered text* finds no absence
claim and scores the turn **clean** — the agent over-claimed, the gate caught it, and the record
would say nothing happened. The scorer therefore reads the gate's own stamp first.

This is Rule 5 (*a gate that acts changes the subject*) seen from the measurement side: the
assessor's `subject_is_model_output` makes it abstain on platform-authored text, which is right
for a gate deciding in flight and wrong for a measurement whose job is to record that the gate
acted. Folding the two would trade a real protection for a tidier diagram.

The governed-data scorer was the opposite case and **was** routed: it called `scan_obda`
directly, so a remap of `data_governance` would have moved the in-flight lane and left the
measurement on the old computation — the exact asymmetry this substrate exists to remove.
Proven byte-identical across seven turn shapes before landing.

---

## Adding an assessor

1. `assessors/<property>.py` — implement `Assessor`, expose `ASSESSOR`
2. add it to `_MODULES` in `assessors/__init__.py`
3. add a default binding in `bootstrap()`
4. if a scorer grades it, declare `property = "<name>"` on that scorer — otherwise the Guide entry is a dead end

Nothing else changes. The registry discovers it; `conformance` decides which lanes it may
serve. A shipped default that cannot bind is a **build error**, not a warning.

---

## Reading the live state

```
python3 scripts/symmetry_state.py
```

Derived from the bindings, never hand-kept — a written-down score is accurate only on the
day it was written, which is the argument [TRUST_SYMMETRY_MAP.md](TRUST_SYMMETRY_MAP.md)
Part E makes about second copies of a fact.

**Gate:** `test/test_assurance_substrate.py` — no server, no network. Its load-bearing
section drives each adapter **beside the code it wraps** and asserts they carry the same
facts: an adapter that quietly changes what a property computes while claiming to be a
wiring change is the worst outcome this migration could have.
