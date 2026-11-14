# Building assurance modules for Uderia

*A guide for third-party developers.*

Uderia's Trust Circle is extensible. You can ship a pack that **measures** something
Uderia does not, and that **acts** on what it finds — a real gate, altering or withholding
live answers — without writing a line of enforcement code and without Uderia having to
trust your Python.

This guide is the contract. It tells you what you may build, what you may not, and why each
boundary is where it is. Read §1 and §2 before designing anything: most questions about
"can my module do X" are answered by the split in §1.

**Related:** [ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](../Architecture/ASSURANCE_MODULE_PLANE_ARCHITECTURE.md)
(how it works inside) · [ASSURANCE_SUBSTRATE_ARCHITECTURE.md](../Architecture/ASSURANCE_SUBSTRATE_ARCHITECTURE.md)
(the assess/decide/score contract) · [AGENT_TRUST_AND_ASSURANCE.md](AGENT_TRUST_AND_ASSURANCE.md)
(the operator's view of what you ship)

---

## 1 · The four-way split — read this first

A trust capability is four things. Two are yours, two are the framework's, and knowing which
is which answers nearly every design question you will have.

| | | Whose |
|---|---|---|
| **the assessment** | what was found in this turn | **yours** |
| **the policy** | when to act, and which act | **yours**, declared as data |
| **the act** | withhold · alter · annotate | the framework performs it |
| **the ladder** | off → observe → annotate → enforce → review, the parking, the event, the reading | the framework |

You do not need to *implement* withholding an answer. You need to *choose* it, and say when.
So a pack ships a real control: answers are genuinely withheld or altered because of it —
while the act itself, the retention, the audit event and the trust reading are identical
however the finding arrived.

**Two things stay closed**, and both are about Uderia being able to *describe* what
happened rather than about distrusting you:

- **The acts** (`withhold` · `alter` · `annotate`). A new kind of effect needs new rendering,
  a new audit shape and a new operator reading. You choose one; you never define one.
- **The chokepoints** (`outbound_answer` · `inbound_request`). Your control attaches where
  Uderia already stands. You name one; you cannot invent a place in the turn.

### The one guarantee that makes all this safe: dormant on arrival

A third-party control installs at `observe`. It records what it *would* have done and changes
nothing until an operator raises its posture on the Trust panel.

**Installing your pack can never, by itself, withhold an answer.** Design for this: your
module should be genuinely useful in `observe`, because that is where it will spend its first
days on every deployment that installs it.

---

## 2 · What a pack contributes

A pack is a directory with a `manifest.json`, optionally alongside Python files.

```
~/.tda/assurance_modules/acmeref/
├── manifest.json
└── rules.py          # only if you use the verified or scanned tier
```

| You declare | What it is |
|---|---|
| `properties` | a question about a turn that can be answered — *"does the answer expose an internal customer reference?"* |
| `assessors` | how that question is answered — a pattern set, a metric expression, a rubric, or Python |
| `controls` | when to act on a finding, and which act |
| `tunables` | numbers an operator can adjust, rendered as sliders |

You may **not** contribute: a new station (a guarantee), a new act, a new chokepoint, or a
binding. That last one matters — see §4.

---

## 3 · The tiers — privilege by proof

Which lane your assessor may run in depends on what can be *verified* about it, not on what
you declare.

| Tier | What you write | Where it may run | How it is checked |
|---|---|---|---|
| **`declarative`** | no Python at all — patterns, a metric expression, or a judge rubric | any lane, including in-flight | nothing to check; there is no third-party code |
| **`verified`** | a statically-verified pure subset of Python | any lane, including in-flight | AST check + import allow-list, run by the loader **and** by the signing validator |
| **`scanned`** | full Python | **post-hoc only** — never during a turn | read by a scanner; a judgement, not a sandbox |

**Start declarative.** It covers most real demand, needs no verification, reaches every lane,
and cannot be rejected for a purity failure. Reach for `verified` only when a pattern or an
expression genuinely cannot express your assessment.

The `verified` tier's checker is deliberately strict: no dunder attribute traversal, no
imports outside the allow-list, no dynamic execution. A module declaring `verified` that fails
the subject is **refused at load**, not merely refused a signature — and the signing validator
runs the *same* verifier, so a pack cannot reach the in-flight lane on a claim nobody checked.

---

## 4 · Installing is not assigning

This surprises people, so it is worth stating plainly.

Installing your pack makes your modules **available**. It binds nothing. An operator assigns a
module to a property in a lane, in the Trust panel's Assessment modules card (Administration →
App Config → Trust), and that act is audited.

**And your pack must be signed before it can be bound at all.** The gate lives in
`conformance.check_binding`, so every path inherits it. An unsigned pack loads, appears in the
studio, and cannot be assigned to anything.

This has a consequence worth designing around: **your control cannot act until your assessor
is bound.** A control only ever sees findings that came through the in-flight seam, which is
to say findings from bound modules. So the chain is:

```
signed  →  bindable  →  produces a finding  →  your control may act
```

An unsigned pack's control is inert at any posture. This is structural, not a policy check.

---

## 5 · Writing the manifest

### A declarative property + assessor

```json
{
  "pack_id": "acmeref",
  "display_name": "Acme · Customer references",
  "version": "1.0.0",
  "description": "Treats an internal customer reference as personal data.",

  "properties": [{
    "key": "custref",
    "label": "Customer reference",
    "question": "Does the answer expose an internal customer reference?",
    "lanes": ["IN_FLIGHT", "POST_HOC"]
  }],

  "assessors": [{
    "key": "ref_rule",
    "property": "custref",
    "label": "Reference rule",
    "tier": "declarative",
    "requires": ["answer"],
    "spec": {
      "patterns": { "flag": ["CUST-[0-9]{5}"] },
      "finding_category": "pii",
      "span_label": "CUSTREF"
    }
  }]
}
```

Note `lanes` — any of `IN_FLIGHT` · `IN_FLIGHT_TRIGGERED` (in flight, only behind a trigger —
the only in-flight lane a rubric may take) · `POST_HOC`. Declaring both an in-flight lane and
`POST_HOC` is what makes your module a **sibling** — see §7.

### `finding_category` — you classify, Uderia decides the act

A pattern rule may declare `finding_category` ∈ `pii` · `toxicity` · `exfil`. This is the
division of labour in miniature: **you say "this is personal data", Uderia says "personal
data is redacted in enforce, noted in annotate, untouched in observe."**

`span_label` names what appears in the redaction (`[REDACTED:CUSTREF]`).

The raw matched value rides an internal `payload` field so a mask can be applied. It is
stripped before anything is reported and no surface renders it. **Do not put a matched value
anywhere else** — not in a label, a summary or an evidence string.

### A control

```json
"controls": [{
  "key": "ref_guard",
  "label": "Customer-reference guard",
  "station": "guarded",
  "attaches_to": "outbound_answer",
  "fires_on": "custref",
  "act": "alter",
  "description": "Redacts an internal customer reference from the answer."
}]
```

- `fires_on` **must name a property your own pack declares.** A bare name is namespaced to
  your pack, so naming one of Uderia's properties (`output_sensitivity`) silently becomes
  `<yourpack>.output_sensitivity` — a property that does not exist. That is refused at load,
  naming your pack's real properties, rather than registering a control that can never fire.
- `act` is what it does at `enforce`. Every weaker posture is **derived by the framework**, so
  you cannot ship "always withhold, whatever the operator set". At `annotate` your control is
  never stronger than a note; at `observe` and `off` it does nothing.
- `station` is which guarantee it upholds — one of `signed` · `grounded` · `guarded` ·
  `bounded` · `scored` · `trusted` · `reviewed` · `proven`.

### `enforced_by` — the shorthand

A property may declare `"enforced_by": "outbound_guard"` instead of a full control. It
synthesises one, with the same dormancy and the same ladder. It is sugar, not a second path —
use it when the default act for that gate is what you want.

---

## 6 · What your assessor receives, and what it must return

An assessor is asked one question about one turn and returns a `Finding`.

**Declare what you need** in `requires`, from: `answer` · `question` · `chunks` (retrieved
knowledge) · `tool_calls` · `tool_outputs` (tool results) · `retrieval_scores` ·
`scope_databases` · `config` · `stamp` (what the in-flight mechanism already recorded). A lane that cannot supply an input you require will refuse the binding rather
than silently hand you nothing — the failure mode where a module binds, runs, sees nothing and
reports clean forever. If you require `chunks`, Uderia also makes you abstain when the
turn's evidence has since been lost (an attachment deleted from Files) — you never grade
against a support set the answer was not written from.

Three rules your assessor must respect:

1. **Abstain when you cannot judge.** An abstention is not a pass. If the input is absent or
   ambiguous, abstain — Uderia renders that as *not assessed*, never as clean.
2. **Never raise to signal a finding.** Every failure path is caught and turned into an
   abstention. Raising loses your finding.
3. **Tighten, never loosen.** Your finding is *added* to whatever Uderia found natively.
   There is no return value that removes a native finding — union-only, by construction.

---

## 7 · Siblings — the shape this is designed for

The strongest thing you can build is **one module bound to both lanes**.

In flight, a gate acts on your assessment. Afterwards, the *same* module grades the turn on
the Scorecard. `registry.symmetry()` then reads `shared`, and the Assurance Guide draws an
**S** badge: the thing that acted and the thing that graded are not two implementations that
agree today — they are one that *cannot* disagree.

Declare both lanes on the property, ship one assessor, and let the operator bind it to both.

This also dissolves a question you might otherwise ask: *may my module replace Uderia's
own detector?* It does not need to. You **add** a property, so nothing native is displaced,
and your finding rides alongside Uderia's own.

---

## 8 · Scoring — you write no eval code

Bind your property post-hoc and it is scored automatically. `PropertyScorer` generalises the
idiom Uderia's own scorers share; a rubric judge inherits Uderia's judge panel,
its timeout, its consensus rule and its cost accounting.

Add a `tunables` entry if you want an operator-adjustable threshold. Without one your
dimension reports its finding without a band, which is a judgement rather than a gate — the
correct behaviour when nobody has told you where the line is.

---

## 9 · Getting signed

1. Install to `~/.tda/assurance_modules/<pack_id>/`.
2. An operator enables third-party modules (master switch, off by default).
3. An operator signs your pack: **Administration → App Config → Trust → Assessment modules**.
4. Only then can it be bound.

The validator checks code safety **and capability honesty** — a pack claiming the `verified`
tier while failing the pure subset cannot be signed, so it can never reach the in-flight lane
on an unchecked claim.

**Editing a file in place is drift.** The content hash covers your manifest *and* every source
file, so a change under a signature is detected. It is **reported, never auto-unbound** —
removing a running protection without a decision is worse than running a changed one visibly.
Ship a version bump and let the operator re-sign.

---

## 10 · Checklist before you publish

- [ ] Useful in `observe` — it will start there on every deployment
- [ ] `declarative` tier unless you genuinely need Python
- [ ] `fires_on` names a property your own pack declares
- [ ] Abstains when it cannot judge, rather than passing
- [ ] No matched value in any label, summary or evidence string
- [ ] Both lanes declared if it can be a sibling
- [ ] A `tunables` entry for any threshold an operator should own
- [ ] `description` and `display_name` set — a pack missing them signs only as *partial*

---

## 11 · Honest limits

- **A `scanned` module is read, not sandboxed.** It is confined to the post-hoc lane and the
  sign-off surface says so. If you need in-flight, use `declarative` or `verified`.
- **You cannot add a guarantee.** The eight stations are the framework's promises; a new one
  needs new rendering, new aggregation and a new operator reading.
- **You cannot add an act.** See §1.
- **Your control's posture is deployment-wide**, not per-agent. Uderia's own gates have per-agent
  modes; a third-party control has one posture for the deployment, set on the Trust panel.
