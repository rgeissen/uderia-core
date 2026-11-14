# The Assurance Module Plane — third-party trust capability

> **What this is.** The fifth plugin plane, and the only one whose code runs *inside the
> assurance path*. A third party can package a **property** (something that can be
> assessed), **assessors** (ways of assessing it) and **rubrics** (questions for the judge
> panel), ship them through the marketplace, and an operator can assign them to the Trust
> Circle's properties and lanes through a visual **binding studio** — with every
> assignment conformance-checked, trust-gated and audited.
>
> Design + rejected alternatives: PROP-003 (`docs/proposals/PROP-003-assurance-module-plane.md`).
> The substrate it builds on: [ASSURANCE_SUBSTRATE_ARCHITECTURE.md](ASSURANCE_SUBSTRATE_ARCHITECTURE.md).
> Trust kinds and signing: [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md).

---

> **Building a pack?** The developer-facing contract is
> [docs/manuals/BUILDING_ASSURANCE_MODULES.md](../manuals/BUILDING_ASSURANCE_MODULES.md).
> This document is how the plane works *inside*; that one is what you may declare and why.

## 1 · The two tenets

Everything below follows from two decisions. If you are changing this subsystem, change
these last.

### Tenet A — third parties **choose**; the platform **performs**

> **Revised (Aug 2026).** This tenet originally read *"third parties assess; the platform
> acts"*, and closed controls to third parties on the grounds that the posture ladder took
> five recorded contract breaches *by platform code* to get right. That reasoning conflated
> four things, and only two of them must stay the platform's. The record of the original
> reasoning is kept below the split, because the two closed items are closed for exactly it.

A trust capability is four things:

| | | Whose |
|---|---|---|
| the **assessment** | what was found | third party |
| the **policy** | when to act, which act | third party, declared as data |
| the **act** | withhold · alter · annotate | **platform performs it** |
| the **ladder** | observe→enforce, parking, retention, the event, the reading | **platform** |

A third party does not need to *implement* withholding — it needs to *choose* it, and say
when. So a pack ships a real control: answers are genuinely withheld or altered because of
it, while the act, the parking, the retention, the audit event and the trust reading are
identical however the finding arrived.

**Two things stay closed**, and both are about the platform being able to DESCRIBE what
happened rather than about distrust — this is where the original reasoning still holds:

- **`ACTS`** — a new kind of effect needs new rendering, a new audit shape and a new
  operator reading.
- **`CHOKEPOINTS`** — a control attaches where the platform already stands. A pack names
  one; it cannot invent a place in the turn.

**Dormant on arrival** is the property that replaces the old lock, and it is a *better*
guarantee because it is an operator's decision rather than a vendor's: a third-party control
installs at `observe`, records what it would have done, and changes nothing until an admin
raises its posture. Installing a pack can never, by itself, withhold an answer.

**Signed-to-act is transitive, and structural.** On paper a control has no signing gate —
`check_binding` refuses an unsigned module's *assessor*, while `controls.register()` accepts
any control, which would put the weaker gate on the machinery with more power. It holds
anyway: a control is only ever offered `inflight.augment()`'s output, i.e. findings from
BOUND modules, and a native gate's own findings never enter it. So `signed → bindable →
produces a Finding → a control may act`, and an unsigned pack's control is inert at any
posture. Asserted from both ends in `test_assurance_controls` §9 — the property held through
a call-site detail one refactor could have undone silently.

The consequence to hold on to is unchanged: **a third-party module can tighten what an agent
will say; it can never loosen it silently.** An unbound property reads NOT ASSESSED, never
assessed-clean, and a gate whose module abstains falls back to its native composition exactly
as it does when the registry returns `None`.

### Tenet B — privilege by **proof**, not by declaration

The lane-capability model is the sandbox policy: `IN_FLIGHT` requires `PURE`, `POST_HOC`
requires `REPLAYABLE`. For platform code a declared capability is a reasonable contract;
for third-party code it is worth nothing. So the plane **verifies** capability and derives
lane eligibility from what was proven:

| Tier | What it is | Verification | Lanes |
|---|---|---|---|
| **T0 · declarative** | No third-party Python at all. Pattern rules, metric expressions (through the flow `safe_expression` sandbox), or a judge **rubric**. | Schema + spec validation. There is nothing to sandbox. | all (rubrics: triggered/post-hoc — a judge is never unconditional in flight) |
| **T1 · verified** | An `Assessor` subclass in a statically verifiable pure subset. | `verifier.verify_source` at **load** and again at **sign** | all |
| **T2 · scanned** | Full Python, for logic the subset cannot express. | `code_safety_scan` + a human sign-off | **POST_HOC only, by construction** |

T0 is expected to cover most real demand (domain compliance phrasing, required
disclaimers, industry vocabulary rules). Python is the escape hatch, not the default.

---

## 2 · What a pack contributes — and what it cannot

```
assurance_modules/<pack>/          (builtin)      ~/.tda/assurance_modules/<pack>/  (user)
  manifest.json     properties · assessors · controls · tunables · suggested_bindings · guide
  assessors.py      T1/T2 only
```

| Contributes | Never contributes |
|---|---|
| **properties** — new assessable things | a **station** (the eight are the platform's status grammar) |
| **assessors** — ways of assessing them | a **lane**, a **posture**, or an enforcement **act** |
| **tunables** — the bands its numbers are read against | a **binding** (an operator's decision, §4) |
| **rubrics** — questions for the judge panel | its own **model**, sampling rate or billing |
| **guide** text — so its entries document themselves | an **eval scorer** (the platform supplies one, §5) |

**Namespacing is the discriminator everything keys on.** Builtin keys contain no dot;
pack keys are always `<pack_id>.<local_key>`, applied *by the loader*, so an author writes
local keys and cannot collide with a platform property however they name things. The dot is
the single test for "is third-party" used by the bind gate, the `builtin` flag and the
studio's seal.

Schema: [`assurance_modules/schemas/assurance_module_manifest_v1.schema.json`](../../assurance_modules/schemas/assurance_module_manifest_v1.schema.json). (The v1 schema does not yet describe `controls`; the loader reads them — `manager.py` — and `controls.register()` validates each one, §4b.)
Reference pack (shipped **disabled**, to be read and copied):
[`assurance_modules/builtin/sample_compliance/`](../../assurance_modules/builtin/sample_compliance/).

---

## 3 · Trust — signed to bind

New Trust-Membrane kind **`assurance_module`**, wired the standard way (validator +
`_LEAF` content resolver + sign-off route + badge label). Its validator does one thing no
other validator does: it checks **capability honesty**. A pack claiming the verified tier
while failing the pure subset is `blocked`, because signing it would let it reach the
in-flight lane on the strength of a claim nobody checked.

The gate itself lives in `conformance.check_binding` — not at the REST layer — so every
caller (the studio, the API, a future importer) inherits it from `registry.bind`:

```
"." in assessor_key  →  resolve the pack's trust state
                        TRUSTED   → bind permitted
                        anything else → refused, naming the pack to sign
                        trust suite absent → refused, naming that
```

Three consequences worth stating:

- **Drift never auto-unbinds.** A bound module whose hash has moved keeps running, is
  reported by `conformance.audit()`, renders 🟠 in the studio and raises a danger-severity
  inbox item. Removing a running protection without a decision is worse than running a
  changed one visibly (Rule 8.5: the offered act is the act that is owed).
- **The binding table is deployment-trust surface.** It folds into the **deployment** half
  of the two-hash envelope (never the portable half — a subscriber's bindings are their own
  decision), carrying each module's content hash so an in-place edit is drift a key-only
  record would miss. `describe_drift` names the property and the module, because an
  unexplained amber trains operators to click through it.
- **A marketplace install grants availability, never authority.** A publisher's signature
  says *this is what I published*; the installing deployment still signs for itself.

---

## 4 · Binding is an operator act

**Installing a pack binds nothing.** This keeps one invariant trivially true: *the set of
assessments running on a deployment is exactly the set an operator assigned, each
assignment audited.* `suggested_bindings` in a manifest is a recommendation the studio may
surface; it is never applied.

The **binding studio** (Administration → App Config → Trust → *Assessment modules*) renders
`registry.bindings()` as a properties × lanes matrix. The two in-flight lanes render as
ONE column — exactly as the Assurance Guide collapses them — because they are alternatives,
never both; which is in force is said in words (`can act on the answer` vs `can only
escalate to a judge`).

Every cell is a reading, and absence is rendered rather than blank:

| Cell | Reading |
|---|---|
| bound | the module's name + the **M** badge, its power in words, `override` where it differs from the shipped default |
| unbound, lane meaningful | `nothing bound` · *not assessed — never assessed-clean* |
| lane absent by design | the property's own `lane_note`, in italics |
| bound in flight, unconsulted | *no gate consults this in flight yet* (see §6) |
| bound, module drifted | the act owed: *re-sign it or unbind* |

The picker lists `conformance.candidates()` and shows the **ineligible** options with their
`blocked_by` reasons verbatim — a picker that lists only valid options teaches nothing.
Nothing is applied on selection: a confirm step states the from→to and, for an unbind, that
*this property will read NOT ASSESSED in this lane — never assessed-clean*. The module
already bound in a cell is shown but **not offered**, since selecting it could not move the
state.

Three distinct states, and the storage keeps them distinct:

```
no row                  the shipped default applies
row, assessor_key SET   the operator rebound this lane
row, assessor_key NULL  the operator chose NOT ASSESSED (an audited act)
```

`delete_override` therefore **restores the default**; it is not the same act as unbinding,
and a UI offering only one of them cannot express the other.

---

## 4a · The in-flight seam — how a plugin affects an answer

A pack that can only measure a turn after it shipped is half a plane. The other half is
one function:

```python
augment(mechanism, own_properties, ctx) -> [Finding, ...]     # agent/assurance/inflight.py
```

ONE call per gate. It returns what a bound module **contributes**; the gate then applies
its own policy to the union of its native findings and these. **There is no return value
that removes a native finding** — "a plugin may tighten, never loosen" is structural here
rather than remembered at four call sites.

A pack reaches a gate by declaring `enforced_by` on its property, naming a mechanism from
a closed set (`outbound_guard` · `inbound_guard` · `grounding_gate` · `honesty_gate`). It
asks an existing gate to look at one more thing; it never introduces a gate, an act or a
posture.

**The intended shape is SIBLINGS**, and it is what makes a third-party capability
trustworthy:

```
a pack declares its OWN property        acmeref.custref — "does the answer expose an
                                        internal customer reference?"
routed to a gate                        enforced_by: outbound_guard
ONE module bound to BOTH lanes          in flight the guard REDACTS what it found, through
                                        the platform's own ladder; afterwards the SAME
                                        module grades it on the Scorecard
→ symmetry() reads `shared`             the Guide's S badge — the thing that acted and the
                                        thing that graded are not two implementations that
                                        agree today, they are one that cannot disagree
```

It also dissolves the question that would otherwise have to be answered — *may a plugin
replace the platform's own detector, and so weaken it?* A pack **adds** a property, so
nothing native is displaced: in the live proof, the pack's references are redacted **and
the platform's own email detection still fires**. A module rebound onto a *platform*
property is still permitted, but it is an addition to that mechanism's assessment, never a
substitution for it (`inflight._is_shipped` skips the shipped module, because the gate has
already made that assessment natively and running it twice would double-count).

**Classification is assessment; the act is policy.** A declarative pattern rule declares
`finding_category ∈ {pii, toxicity, exfil}` and supplies `(label, start, end, value)`
spans. The pack says *this is personal data*; the platform's ladder says *personal data is
redacted in enforce, noted in annotate, and left alone in observe*. The raw matched value
rides `payload` — which no surface renders and which the seam strips before anything is
reported — so it can mask without ever reaching an event.

Every path fails open to nothing: a module that raises, a broken envelope, an unreadable
substrate all leave the gate behaving exactly as it did before the seam existed.

## 4b · Controls — a pack's own machinery that acts

`agent/assurance/controls.py`. A `Control` is a **frozen dataclass with no callable field**:
the thing with POWER never carries logic, and the thing with logic (an assessor) never has
power directly. The policy is a declaration, never a function a pack supplies.

```
key           <pack>.<local>          namespaced; bare keys are the platform's
station       which guarantee it upholds
attaches_to   a CHOKEPOINT            outbound_answer | inbound_request
fires_on      a property              MUST be one the pack itself declares
act           what it does at enforce withhold | alter | annotate
```

**`fires_on` is the routing.** A control's declaration is what makes its property consulted
at that chokepoint (`triggers_at`), so a pack declares the fact once. Before this, a pack
shipping a control but not the `enforced_by` shorthand had its property left unconsulted and
its control could never fire — two declarations for one fact, and the one that mattered was
the one nobody wrote.

**A bare `fires_on` is namespaced to the pack**, so naming a PLATFORM property becomes
`<pack>.output_sensitivity` — a property that does not exist. Both that and a plain typo
produced a control that registers, signs, is raised to `enforce` and never fires, forever,
with nothing explaining why. Refused at load now, naming the pack's own properties.

**`enforced_by` is sugar for a control, not a second runtime path.** It predates third-party
controls and, left as its own mechanism, it inherited the GATE's posture — so installing a
pack routed to a gate already at `enforce` made it act immediately, with no dormancy and no
separate operator decision. Two mechanisms, two safety properties; now one of each.

**The ladder is the platform's.** A pack declares the act it wants at `enforce`; every weaker
posture is derived (`_LADDER`). An unrecognised posture resolves to `observe` — the direction
that RECORDS rather than acts, because a typo must never silently grant a control more power
than the operator gave it.

### Posture — stored, served, and in the envelope

`store.control_posture` / `set_control_posture` / `clear_control_posture`. Clearing is a
distinct act from setting `observe`: *"nobody has decided"* and *"somebody decided the
default"* are different facts about a deployment, and only the first should leave the
attestation.

`POST/DELETE /v1/assurance/controls/<key>/posture` fails **closed** on an unrecognised
posture rather than persisting a value `effective_act` would later resolve as `observe`, so
the stored state always means exactly what it says.

**Raised postures are part of the DEPLOYMENT half of the trust envelope**
(`deployment.control_postures_state`). Raising a control from `observe` to `enforce` starts
altering or withholding answers — a larger change than a rebind, which was already carried
while this was not. Only deviations from the default are recorded and the field is **omitted
when empty**, so a deployment that has never raised a control hashes exactly as it did before
this existed: adding it flips no existing attestation, and the record appears on the day it
first means something.

## 4c · The studio — two axes, one model, staged

`static/js/handlers/assuranceBindings.js`, in Administration → App Config → Trust.

**By guarantee** (a ring of the eight stations) answers *"what upholds this promise?"*; **By
capability** (the property × lane matrix) answers *"where is this capability used?"*. Neither
contains the other and both reach every action.

The ring is **deliberately a different instrument** from the Agents lens's ring: there, colour
means attention priority on live data. A config page has no live agent, so a coloured ring
here would be the same shape under different rules and an operator would read "GROUNDED is
red" as "GROUNDED is misconfigured" — the exact drift the trust grammar exists to prevent.
No state fill, structural strokes only, selection is the one fill, and each segment is
labelled with what it HOLDS rather than how it is DOING.

*What acts on it* merges the PLATFORM's mechanisms with any pack's controls. Reading only the
third-party registry made every station report "nothing acts here" on a fresh install —
including GUARDED, which three gates uphold. Platform mechanisms carry no deployment-wide
posture switch (a gate's mode is per-agent) and say where theirs is set instead.

**Changes are STAGED and applied by the panel's Save.** The card applied on confirm while it
sat below the Save row, visibly outside it; once it moved into the stack, two save models
shared one column. Staging normally moves refusals to Save — that does not happen here,
because eligibility is decided at PICK time (`conformance.candidates()` returns
eligible/blocked with reasons before any write, and the picker never offers a blocked module).
The server re-checks on apply and still fails closed. Applied sequentially, never as a batch:
one act, one audit event, and a refused change stays STAGED with its reason rendered on its
own row.

## 5 · A pack writes no eval code

Nine native scorers already share one idiom — resolve the property through the registry,
run the bound assessor over the persisted turn, band the metric. `PropertyScorer`
generalises it, so a bound pack property gets its Scorecard chip, roster bar, runs-ledger
square and Guide `graded by` row with no pack-supplied Python.

The consequence that matters most is **symmetry by construction**: the in-flight lane and
the scorer resolve the SAME binding, so a pack cannot ship a gate input and a score that
disagree.

A **rubric** property gets a `PropertyRubricScorer` instead — a subclass of the platform's
own `JudgeScorer`, so the panel, the timeout, the consensus rule, the vote attribution and
the **canonical cost sink** all come with it. A rubric that billed through its own path
would break the platform's "one canonical sink for every judge LLM call" rule on day one.

`gating = False` is a deliberate v1 policy: a third-party dimension **bands and is
flagged, but never fails a turn**. Failing a turn is the platform's own judgement to make;
changing that is a governance switch, not a redesign.

---

## 6 · Honest limits (stated, not hidden)

- **T2 modules are scanned, not isolated.** A signed post-hoc module runs in-process with
  platform privileges; the sign-off is a judgement the validator informs, not a sandbox.
  Said on the sign-off surface, and the validator never returns `clean` for such a pack.
- **In-flight influence reaches four gates, not all of them.** The outbound guardrail, the
  inbound guardrail and the grounding gate consult bound modules through the seam (§4a);
  `context_use` and `retrieval_sufficiency` are resolved directly by the grounding and
  honesty gates as their own assessment. The honesty gate does not yet take seam
  contributions, and `data_governance` / `tool_repetition` / `tool_authority` are
  post-hoc only. All of it is declared in `assurance.INFLIGHT_CONSUMERS`, pinned against
  the gates' own source by a gate, and rendered per cell — a bound-but-unconsulted lane
  still says *no gate consults this in flight yet*, because that is a decoration wearing a
  control's clothes and the studio must not imply otherwise.
- **Bindings are per-process.** A multi-worker deployment needs reload-on-bootstrap plus an
  invalidation signal; single-process today, but the limit is real.
- **Bindings are deployment-global in v1.** A pack binds for the whole deployment, not one
  agent. Per-profile overlays are deferred; the `profile_id` column exists (nullable, always
  NULL today) so adding them is additive rather than a migration of live trust config.
- **A rubric spends real tokens.** Safe as code, not free as calls — it rides the existing
  judge governance (master switch, sample rate) and the shared accounting.

---

## 7 · Files

| Path | Role |
|---|---|
| `agent/assurance/store.py` | the binding OVERRIDE table (three states, §4) |
| `agent/assurance/__init__.py` | `bootstrap` = defaults → overrides · `reload_overrides` · `INFLIGHT_CONSUMERS` |
| `agent/assurance/conformance.py` | `check_binding` — incl. the third-party trust gate |
| `agent/assurance/inflight.py` | the IN-FLIGHT seam — `augment()`, union-only by construction (§4a) |
| `agent/assurance/controls.py` | third-party CONTROLS — the closed acts + chokepoints, the platform ladder, dormant-on-arrival (§4b) |
| `assurance_modules/manager.py` | discovery, tier enforcement, registration (binds nothing) |
| `assurance_modules/verifier.py` | the T1 pure subset |
| `assurance_modules/declarative.py` | T0 pattern + metric assessors |
| `assurance_modules/rubric.py` | T0 rubrics (prompt only; the judge layer adjudicates) |
| `assurance_modules/settings.py` | governance KV (master OFF by default) |
| `eval/scorers/plugin_properties.py` | `PropertyScorer` + `PropertyRubricScorer` + `sync_plugin_scorers` |
| `core/trust/{validators,content,deployment}.py` | the `assurance_module` kind + the bindings fold |
| `api/rest_routes.py` | `/v1/assurance/bindings[/candidates]`, `/v1/assurance/modules[/settings]`, `/v1/assurance/controls/<key>/posture` |
| `api/trust_routes.py` | `/v1/trust/assurance-module/<pack>/sign-off` |
| `api/assurance_module_marketplace_routes.py` | publish · browse · install · rate · unpublish |
| `static/js/handlers/assuranceBindings.js` | the studio — ring + matrix, controls, staging (§4c) |
| `static/js/adminManager.js` | Save Trust Settings applies the staged module changes |
| `schema/42_assurance_modules.sql`, `schema/43_marketplace_assurance_modules.sql` | self-applying |

## 8 · Gates

| Suite | Covers |
|---|---|
| `test_assurance_substrate.py` | override precedence, fail-soft, the three latent gaps, pack properties |
| `test_assurance_bindings_rest.py` | the REST surface through the real auth stack |
| `test_assurance_bindings_studio.mjs` | every cell state, the picker, the controls half, **staging** (stage-not-write · staged rendering · undo · refusal on its row · beforeunload) |
| `test_assurance_modules.py` | tiers, the verifier, the bind gate, regex safety, `PropertyScorer`, the deployment fold |
| `test_assurance_modules_live.py` | install → refused → sign → bind → score → drift, end to end |
| `test_assurance_inflight_live.py` | the SIBLING story: one module, both lanes, a real redaction in flight and the same module grading it after — plus the union rule and the privacy invariant |
| `test_assurance_marketplace.py` | publish/install/rate, install-grants-availability-not-authority, rubric judges |
| `test_assurance_controls.py` | a pack ships a control · **dormant on arrival** · the platform ladder · what a pack may not declare · `enforced_by` is one path · **signed-to-act is transitive** · an unfireable `fires_on` is refused · a raised posture is in the envelope |

Each security-bearing rule is **mutation-tested**: removing the trust gate, the scanned-tier
lane confinement, the load-time verified check, property namespacing, the install integrity
check, or any of the seam's four invariants (a shipped module contributing twice, a clean
module contributing anyway, a broken envelope breaking the gate, routed properties being
ignored) must each fail by name.

**Adding a tier, a lane, a station or an act is a platform change, never a plugin.** If an
implementation seems to need one, that is the signal to raise a proposal rather than widen
this contract.
