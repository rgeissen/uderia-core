# Agent Trust &amp; Assurance — Administrator Operations Guide

> **This is the operations manual for the whole Agent Trust &amp; Assurance stack.** It is the
> single place an administrator comes to understand *why* the assurance capabilities exist,
> *what* each one is, and *how* to operate them together as one process — Grounding, Guardrails,
> Evaluation, Observability/Ops, the Ontology and its Governance, the Trust Membrane, Agent
> Authority, and Data-Product Governance.
>
> **This document is self-contained in its Why, What, and How.** You can read it end to end and
> fully understand the mission, every capability, and how to operate the whole process — without
> opening anything else. The architecture documents linked throughout are *optional deeper
> reading* on internals (data models, code seams); they are never required to understand what a
> capability is, why it exists, or how to run it. If you administer a production Uderia
> deployment, this is your runbook.

> **Version:** last updated 30 October 2026. This manual tracks a fast-moving framework; verify
> capability status against the linked architecture and roadmap documents for anything
> time-sensitive.

---

## Part 1 — WHY: the mission

### 1.1 The trust problem

An LLM agent is powerful and, by default, **untrustworthy for enterprise use**. Left ungoverned it will:

- **Hallucinate** — answer confidently from parametric memory instead of your data.
- **Drift out of scope** — reach for data it was never meant to touch. (Uderia's own documented
  failure mode: an agent received a complete, scope-correct 21 KB knowledge enrichment, *ignored
  it*, brute-forced 19 calls across unrelated databases, and produced a confident, wrong
  230-day answer.)
- **Act with borrowed authority** — a scheduled task or a sub-agent runs as *you*, with your
  full permissions, unattributed.
- **Consume unvalidated ingredients** — ground on a knowledge graph, knowledge repository,
  champion-case store, skill, or pack nobody checked, shared from who-knows-where. An agent
  cannot be signed until each of them is: for a knowledge-focused agent that means its
  corpus must be screened and signed first, because the corpus *is* the agent.
- **Leak or absorb harm** — leak personal data, or be steered by a hidden instruction buried in a document.
- **Degrade silently** — quality erodes turn over turn with nothing measuring it.

Enterprises cannot deploy what they cannot trust. Every one of these is a *trust* failure, not a
security failure — a deployment can be perfectly secure and still produce a confidently wrong,
out-of-scope, unattributable answer.

### 1.2 The mission

> **Make agents trusted.** Turn an AI agent from an opaque generator into a **grounded,
> guarded, measured, attributable, and verifiable** participant in the enterprise — *without*
> sacrificing the openness that makes the framework useful.

Trust is not one feature. It is a **stack of independent, composable layers**, each closing one
failure mode, each safe to run on its own, and each strongest when run together.

### 1.3 The two pillars — and where this manual sits

Uderia separates two concerns that are often conflated:

| Pillar | Question it answers | Covered by |
|---|---|---|
| **Deployment Security** | *Is the deployment itself secure?* (authN/Z, secrets, encryption, audit) | [SECURITY_ARCHITECTURE.md](../Architecture/SECURITY_ARCHITECTURE.md) |
| **Agent Trust &amp; Assurance** | *Is the AI's behaviour truthful, in-scope, safe, attributable, and verifiably good?* | **This manual** |

Security keeps the doors locked. Assurance makes the thing *inside* the doors trustworthy. Both
map onto external frameworks (EU AI Act, NIST AI RMF, GDPR) — see [§5](#part-5--compliance-mapping).

### 1.4 The design principles (why the stack behaves the way it does)

Every capability in this manual was built to the same principles. Knowing them tells you how the
whole system will behave under your hand:

1. **Safe by default.** Every capability ships **off, or observe-only**. A fresh deployment
   behaves exactly like an ungoverned one until *you* turn something on. You are never
   surprised; you opt into rigor deliberately.
2. **Show, don't block (for sharing).** Trust is *visible and verifiable*, and by default it
   *informs* rather than *blocks*. Sharing stays open; enforcement is a choice you make.
3. **When in doubt, refuse (for enforcement).** Where a capability *does* enforce (grounding,
   guardrails, agent-identity limits), any internal error **denies** — it never quietly permits.
4. **Applies to every kind of agent.** Assurance works the same for all five kinds of agent
   (Ideate, Focus, Optimize, Coordinate, Conversation) — not just the tool-using ones.
5. **Trust ≠ access.** A *checked* thing is not an *authorised* one. Signing proves an agent and
   its parts are intact and unchanged; it grants no permissions. Authority is a separate concern
   (Agent Authority).
6. **Defence in layers.** No single control is the whole answer. A turn passes through several
   independent checks; each catches what the others miss.
7. **Honest about limits.** Where a capability is the *attainable* form of a larger goal, this
   manual says so plainly. (The long-standing example — Agent Authority limiting *what an
   agent can do* while limiting *what data it can see* stayed "the OBDA-READ future" — has since
   closed: **signed data products** shipped Aug 2026, and a surface-only agent's data
   authority is now literally the set of signed products on its surface. See
   [§2.12](#212-signed-data-products-obda-read--determinism-you-can-verify). The write side followed in Oct 2026: **signed actions** make a signed contract the only shape of change an agent may make — [§2.13](#213-obda-model-and-obda-act--acting-under-signature).)

---

## Part 2 — WHAT: the assurance stack

### 2.0 The trust lifecycle of a single turn

The capabilities are not a menu — they are a **pipeline**. One agent turn passes through them in
order, and that ordering *is* the assurance process:

```
                    ┌─────────────────────── one agent turn ───────────────────────┐
  user / trigger →  │ ① INBOUND GUARDRAIL  → ② IDENTITY (who is acting, scope set)   │
                    │        → ③ GROUNDING trusted-scope stamped                     │
                    │        → engine runs: every tool call ④ IDENTITY-ENFORCED       │
                    │        → answer drafted                                         │
                    │        → ⑤ OUTBOUND GUARD (personal data/toxicity) → ⑥ GROUNDING │
                    │        → ⑦ EVAL scores the turn → ⑧ PROVENANCE sealed (signed)  │  → answer
                    └───────────────────────────────────────────────────────────────┘
        everything grounds on ⓪ THE ONTOLOGY;  the agent + its ingredients carry a ⑨ TRUST BADGE;
        the whole flow is watched in ⑩ OBSERVABILITY (Trust / Awareness lenses)
```

Each numbered stage is a capability below. Read Part 2 to know *what each is*; read Part 3 to
know *how to operate them*.

### 2.1 ⓪ The Ontology — the knowledge foundation

Everything trustworthy an agent says is anchored to a **knowledge model of your business** — the
ontology. It captures both the *business view* (the entities that matter and their attributes)
and *how that maps to the real data* (the tables and columns behind them), plus business
concepts, metrics, and domain terms.

- **Why it matters for trust:** the ontology *is* the boundary grounding holds the agent to, and
  its concepts are what the agent should reason over instead of guessing. No ontology → nothing
  for grounding to check against.
- **The Platform Ontology** ("Ontology" view) is the whole-deployment map, centred on your
  agents — showing agents, their memory, sessions, knowledge, components, and configuration — so
  you can *see* what grounds what.
- *Deeper reading (internals, optional):* [ONTOLOGY_ARCHITECTURE.md](../Architecture/ONTOLOGY_ARCHITECTURE.md) ·
  [PLATFORM_ONTOLOGY_ARCHITECTURE.md](../Architecture/PLATFORM_ONTOLOGY_ARCHITECTURE.md) ·
  [KG_INSPECTOR_ARCHITECTURE.md](../Architecture/KG_INSPECTOR_ARCHITECTURE.md)

### 2.2 ③⑥ Grounding — anti-hallucination, enforced

**Grounding** checks every answer against the knowledge the agent was actually given, and can
**pass · annotate · block** confident-but-wrong drift. It closes the failure mode in
[§1.1](#11-the-trust-problem).

- **What "declared scope" is:** the specific data the agent's knowledge model says it is
  allowed to draw on — for example, the sales databases and their tables. An answer that reaches
  for anything outside that (a different database, an invented table) is *drift*.
- **Process (how it runs):** a quick check flags any answer that references data outside the
  declared scope; a second opinion then confirms whether it is genuine drift — an honest *"that
  isn't in my scope"* refusal is allowed through, a confident answer built on out-of-scope data
  is caught. If the second opinion can't run, the turn is only flagged, never wrongly withheld.
- **Modes** (set per agent, in the profile editor's words): **Observe** (measure only, changes
  nothing) · **Observe + ☑ disclose** (stored `annotate` — deliver the answer with a note) ·
  **Enforce** (withhold a confirmed out-of-scope answer) · **Enforce + ☑ keep for review**
  (stored `review` — withhold exactly as enforce, but park the original for the owner's
  disposition: human oversight, nothing lost).
- **Proof it works:** a published test showed it went from catching none of the drift to
  catching all of it, with no false alarms — out-of-scope answers reaching users dropped to zero.
- *Deeper reading (internals, optional):* [AGENT_OPS_EVAL_ARCHITECTURE.md §6a/§6b](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) ·
  `TIER_1.1_GROUNDING_GATE.md` ·
  `TIER_1.4_BENCHMARK.md`

### 2.3 ①⑤ Guardrails — protection on both edges

Fast, free protection at both ends of the conversation (it adds no cost — no extra model call):

- **Incoming** — catches a hidden instruction planted in a request or document trying to hijack
  the agent. When enforcing, that request is **refused before the agent ever reads it**.
- **Outgoing** — catches **personal data** (email addresses, national ID numbers, card numbers,
  phone numbers, access keys, bank account numbers, IP addresses) and **toxic content**. When
  enforcing, personal data is **masked** and toxic answers are withheld.
- **Process (how it runs):** the incoming request is screened before the agent sees it, and the
  drafted answer is screened before it's delivered; either way a note records what was caught.
- **Privacy promise:** the note names the *category* found — never the actual value.
- **Modes:** two postures, each with one property — **Observe** (☑ *disclose findings to the
  reader* stores the composite `annotate`) and **Enforce** (☑ *keep withheld originals in the
  Assurance Inbox for my review* stores the composite `review` — enforce *with retention*, a
  human owes each parked item a disposition). Under plain Enforce a FULL withholding still
  retains its original as a quiet recoverable entry (no action owed); routine personal-data
  redactions never auto-park. The **outgoing** and **incoming** (prompt-injection) postures are
  chosen per agent on its Guardrails card, under the overall guardrails switch. Under Observe
  a guard that *caught* something reads **worth a look** on the answer's dial and journey strip
  (*caught pii — recorded, not altered*), never *screened — clean*: it changed nothing, but it
  found something.
- **On the stream, too.** When the deployment streams answers as they are written (the
  *Governed Stream*, an admin switch), the outgoing guard runs **on the stream**: under
  Enforce, personal data is masked *before* the affected words reach the screen, and a toxic
  turn is withdrawn mid-sentence — the reader never sees, even for a moment, what the guard
  would have caught afterwards. The final answer stays the authority: whatever streamed is
  replaced by it. A turn you **stop** keeps what arrived (marked *stopped*), and it is gated
  and recorded like any other answer — nothing you saw goes unrecorded.
- **What you see while it works — the Living Answer.** With streaming on, the answer bubble
  appears the moment you press Send, not at the first word. ONE line above the answer says what
  the agent is doing in plain words (*Assembling the context… · Queried fitness_db.Sales…*) with
  the seconds and tokens so far, and a hairline under it fills as the turn moves through its
  phases. Each step lands as a row of the **receipt** — a timeline in consumer words (*Queried
  Sales · 32 rows · 0.2s*, *Asked @NIBRS · answered · 3.2s*, *Considered your steer*). **The
  marks speak one vocabulary.** A step is an *act* or a *check*. An act (assembled · read ·
  queried · computed · corrected · retried · served by the fallback) carries no verdict, so no
  colour: a breathing accent mark while it runs, a solid mark at full text strength once done
  (*it happened* — never grey, which on every trust surface is the absence family and would
  read as inactive), an outlined red mark only when the act itself failed; its fact (*no LLM
  round spent*, *attempt 2*) is carried by its words. A check (*sources · screen · claims ·
  figures · revision*) carries a verdict and wears the trust family verbatim — solid green held,
  half amber worth a look, outlined red found something; a check that did not run has no row.
  **Green on the receipt always means trust**, and the receipt says where trust begins: the
  check rows sit under one labelled hairline, *Checks on the draft*, so the colour change is
  never an unexplained one. The work line uses the same words: **Working** (steps running,
  breathing mark) → **Writing** (the rows fold into that one line at the first word —
  *Writing · 1 query · 4 entities* — the work is done but the answer is still being written)
  → **Done** (settled; carried by shape — a monochrome check mark, never a third colour), or
  **Stopped** (a small amber square); the line reopens the receipt on a click. After the answer settles, the checks Uderia ran on the
  draft land as the receipt's last rows, in words (*Checked against its sources · in scope*,
  *Screened for personal data and harm · nothing found*, *Checked the claims*, *Traced the figures*),
  the marks land **on the text** (a masked value swept on, an unsupported claim's dotted
  underline, a receipted figure's underline), and **the ONE verdict** appears on the action row as the
  answer's single trust control (it IS *Why trust this answer?* — click it to open the trust
  record; the chevron turns while the record is open): **Verified** (at least one check ran and found nothing),
  **N findings** (amber, or red when an answer was withheld), or **Not checked** (dashed: no check
  ran — an agent with no gate armed; a turn nobody checked is never called clean). Hovering it
  names every check the verdict rests on. Clicking a figure with a dotted underline opens the
  row it was quoted from (the persisted turn carries a bounded sample of that statement's rows). **Every row opens
  its own Live Status card** — live, and on a reloaded turn (the row loads that turn's trace and
  lands on the card; a collapsed panel opens first). The row finds its card by identity, not by
  words: the key the receipt gave the step is written onto the event that produced it, and the
  card rendered from that same event wears it — so a query row lands on that query's result
  card (never on the engine's start card that lists every available tool), a second query on the
  second call, and a row of an earlier turn reloads *that* turn first. A query row names every
  table the statement read (*Queried fitness_db.Sales, fitness_db.Customers*), one row per call.
  A check that found nothing has no card by design — the panel is quiet on it — so that row
  opens the trust record instead. **A reloaded turn looks exactly like the
  live one**: the same line, receipt and verdict are rebuilt from the persisted turn through the
  same rules, so the answer never changes its story after a refresh. A stopped turn keeps its
  text with a *stopped here* hairline at the cut and reads *Stopped*. Reduced motion keeps every
  fact and removes the travel.
- *Deeper reading (internals, optional):* `TIER_1.3_GUARDRAILS.md`

### 2.4 ②④ Agent Authority — the agent as a governed principal

When an agent acts **on its own** — a coordinator's expert, a scheduled task, a step in a flow,
a call from another system — it takes on a **limited identity derived from its owner**: a strict
*subset* of what the owner could do. Every attempted action is checked against that limit
*before* it happens (and refused if it fails, safely), and every such action is recorded as done
*by the agent*, not the person who owns it.

- **Scope** (set per profile): may it act unattended at all · read-only (no writes) · specific
  tools it may never use · which connectors it may reach · a spending cap per run. A scope can
  only ever *remove* authority, never add — an agent is always a subset of its owner.
- **Process (how it runs):** the moment an agent starts an unattended run it takes on this
  limited identity; every time it tries to use a tool, that attempt is checked against the limit
  *before* it happens, and refused if it's outside scope. The refusal is recorded, and the whole
  run is attributed to the agent rather than the owner.
- **What data it can see:** the scope limits *what the agent can do*. Limiting *what data it
  can see* is the agent's **Data surface** on the same card — with the OBDA-READ component on,
  a *Surface Full* agent may read only through the signed data products listed on it
  ([§2.12](#212-signed-data-products-obda-read--determinism-you-can-verify)).
- **On an interactive turn** the agent acts as the person using it, so its own limits are not in
  force unless *Apply to interactive turns too* is ticked; BOUNDED says so, naming the limits that did not
  apply on that turn, rather than reading the turn as bounded.
- *Deeper reading (internals, optional):* [AGENT_AUTHORITY_ARCHITECTURE.md](../Architecture/AGENT_AUTHORITY_ARCHITECTURE.md)

### 2.5 ⑦ Evaluation — measuring every turn

Every turn is measured, for **all five kinds of agent** (not just the tool-using ones):

- **Automatic scoring (free, always on):** a suite of quick, rule-based checks grades each turn
  on cost, speed, tool success, whether it stayed in scope, how much work it took, whether it
  ended cleanly, how much of its data came from a **governed data product** (vs the agent
  free-forming its own query), and more. Each is banded **Healthy / Review / Danger**.
- **Independent judging (optional, sampled):** for the open-ended questions rules can't answer
  ("is this answer actually supported by the data?"), a panel of independent models judges the
  turn. Kept to a sample so it stays cheap.
- **Seeing how the panel voted:** when judges run, the *Agent Quality Judged* step shows the
  ruling first — a tally such as `3 pass · 1 fail`, with **any failing dimension named on that
  line** rather than hidden behind a click. Opening it reveals one row per dimension: each
  judge's vote (✓ pass · ✗ fail · ? abstained, the model named in the tooltip) beside the final
  consensus, and each judge's reasoning under a further disclosure. A model that could not be
  parsed shows as an **abstention, never as a pass** — an abstaining judge is part of the
  panel's composition, and hiding it would overstate the consensus.
- **Choosing who sits on the panel:** any LLM configuration can be excluded from judging
  (Anatomy → LLMs → edit → *Judging role*) while continuing to answer normally. **One panel
  serves both judge layers**, so excluding a model removes it from the in-flight judge a gate
  escalates to *and* from the panel that scores quality afterwards — it is not a
  measurement-only setting. Worth doing
  for a model that regularly abstains because it did not respond in time: the panel waits for
  every member on every judged turn, so one silent model sets the pace of the whole evaluation
  without contributing a vote. Every model participates unless you say otherwise.
- **If the panel did not return:** the step reads **`LLM judge panel — no ruling`** and says so
  in the verdicts row, rather than showing a panel and a bill with nothing behind it. Judges
  that finished are kept; judges the cut-off silenced are recorded as abstentions naming the
  cut-off, so a missing dimension is never mistaken for one that did not apply.
- **Process (how it runs):** the instant a turn finishes it is scored automatically, off to the
  side so it never slows the answer; a fraction of turns is also judged. The result is recorded
  against that exact turn so it can be reviewed later.
- **The Quality card** appears on every turn (live and on reload), grouped into plain sections
  (Core · Resource · Tasks · Tools · Knowledge · Planning · Coordination · Quality). A turn might
  read *speed 3.2 s (green), tool-success 100 % (green), stayed-in-scope: 3 out-of-scope calls
  (red), work 5 steps (amber)* — each banded, so a degraded turn is legible at a glance. Nothing
  here alters the answer; evaluation *measures*, it does not gate.
- *Deeper reading (internals, optional):* [AGENT_OPS_EVAL_ARCHITECTURE.md](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md)

### 2.6 ⑩ Observability &amp; Ops — the two lenses

Assurance you can watch. Two views onto the same tamper-evident record of what happened:

- **Awareness** (formerly "Session Performance") — the *per-conversation* view: cost, speed, the full
  trajectory of a past turn, and its Quality card.
- **Trust** (formerly "Agent Performance") — the *per-agent* view: a colour-coded **health roster**
  (each agent's pass-rate, cost, speed, failing checks), a history of its runs, coordination
  trees for multi-agent work, and side-by-side comparison. Admins can switch between *my* agents
  and the *whole system*.
- **The assurance dial** — on every turn, one instrument showing which protections actually ran
  on *that* answer. It is a view over Uderia's **intervention registry**: every point that
  can alter, withhold, constrain or attest an answer is declared once, and the dial renders what
  the record says — it derives nothing of its own. That matters because the dial's predecessor
  *enumerated* its stages by hand and silently omitted six real gates; a surface that claims to
  show the assurance path while leaving gates off it teaches you that what you see is what ran.
  **While the turn is still running the dial fills station-by-station** — you watch the inbound
  guard clear, watch grounding retrieve, then watch each gate report — rather than a ring that
  spins and settles all at once at the end. What it shows mid-turn is *progress*, never a verdict:
  a station takes its colour only when its machinery has finished (the one exception is a change
  already made to the answer, such as a redaction, which is surfaced immediately and labelled as
  an act).
  A protection with no evidence on a turn reads **"not recorded"**, never as a pass.
- **Process (how it runs):** every turn is sealed into a tamper-evident record as it completes;
  both views — and the quality scores and trust checks — read from that same unforgeable record,
  so what you see is what actually happened.

### 2.7 ⑨ The Trust Membrane — verifiable trust for agents &amp; artefacts

A tamper-evident **signed badge** on every agent and every shareable thing, on one principle: **an
agent is trusted exactly when everything it is built from is** — its knowledge model and every
skill, extension, and repository it uses must each be signed.

- **Three states:** ⚪ unsigned · 🟢 trusted (signed and unchanged) · 🟠 changed (signed, but the
  content has since moved — so trust is honestly withdrawn until re-signed).
- **Compositional:** the consumer reads *one* badge — on the agent — and the whole catalogue of
  parts hides behind it. Signing an agent is a single act that vouches for its whole make-up; if
  any part is unsigned, the agent can't be signed and you're shown exactly which part is blocking.
- **Verifiable by anyone**, even offline, with nothing but the signer's public identity;
  **trust ≠ access** (a signed thing is *checked*, not *authorised*); **badge, not gate** —
  nothing is ever blocked from being shared.

**Every kind of thing that can be exposed — what gets signed, and what "trusted" means for it.**
Anything that can be *shared* (published to the marketplace) or *built into an agent* can be
signed. There are **ten kinds**, and sign-off runs the **right check for that kind** — so
"trusted" is transparent and means something specific each time (a signed skill was screened for
hidden instructions; a signed extension had its code inspected for unsafe actions; a signed
knowledge model was checked for validity). This table is the whole surface — nothing exposable
is left uncovered:

| # | Kind of artefact | What gets signed | What "trusted" checks |
|---|---|---|---|
| 1 | **Ontology** (knowledge model) | the business model itself | **Validity** — checked against the correct shape of an ontology; an invalid one *cannot* be signed, a partial one can be signed *with a noted caveat* |
| 2 | **Skill** | the skill's written instructions | **Content safety** — screened for hidden / manipulative instructions |
| 3 | **Extension** | the extension's package and code | **Code + content safety** — the code is inspected for unsafe actions; the text screened for hidden instructions |
| 4 | **Knowledge repository** | its documents | **Integrity + content safety** — the documents are intact and unchanged, and screened for unsafe content |
| 5 | **Planner repository** | its saved query patterns | **Integrity + query + content safety** — the patterns are intact, and their queries and text are checked for unsafe statements |
| 6 | **Flow** | the flow's steps and logic | **Logic + query + content safety** — the step logic, any embedded queries, and step text are checked |
| 7 | **Dashboard** | its tiles and their queries | **Query + content safety** — the tile queries, and the request that generated them, are checked |
| 8 | **Canvas** | its content and blocks | **Display + content safety** — the rendered content is checked for unsafe markup, and its text screened |
| 9 | **Agent** | its *whole make-up* — its knowledge model and every skill it uses | **Everything together** — trusted only when *every* part is trusted; this is the one badge a consumer reads |
| 10 | **Agent pack** | its *whole bundle* — the agents and knowledge it packages | **Everything together** — trusted only when every packaged part matches what was signed |

Kinds 1–8 are **individual** things (signed on their own content); kinds 9–10 are
**bundles** (signed only when all their parts are). Every check returns a plain verdict —
**clean** (ready to sign), **caveat** (sign, with a note), or **blocked** (fix it first) — so
sign-off is never a rubber stamp: it tells you exactly what, if anything, is wrong. A shared copy
shows 🟢 only if it is an **exact, unmodified copy** of what was signed — which is why trust
**survives the marketplace**: publish it, and whoever installs, subscribes to, or forks it sees
the same honest badge.

- *Deeper reading (internals, optional):* [TRUST_MEMBRANE_ARCHITECTURE.md](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md) · the full validator-per-class reference: `TIER_5.2_VALIDATORS.md`

### 2.8 Governance of products — ontology, packs, metrics

Trust's governance face — turning knowledge assets into **governed data products** with a
release lifecycle:

- **Ontology governance:** a **draft → propose → approve → release → retire** lifecycle for the
  knowledge model, gated by a **validity check** — a thing can't be a governed ontology unless
  it's a valid one. Releasing fixes a version and signs a verifiable record of it.
- **Agent-pack governance:** the same lifecycle for a bundled agent (a "pack"), plus a **service
  tier**; you can require that a pack be *trusted* before it's published, so a distributed data
  product is always a governed one. Retiring a pack archives its conversations and stops new copies.
- **Governed metrics:** a business metric is defined once in the ontology and consumed by both
  the AI *and* business-intelligence tools from that one definition; each answer shows whether
  the metric comes from a governed, trusted ontology.
- *Deeper reading (internals, optional):* [PACK_GOVERNANCE_ARCHITECTURE.md](../Architecture/PACK_GOVERNANCE_ARCHITECTURE.md)

### 2.9 Operational trust — consumption &amp; cost

An agent you cannot bound is not operationally trustworthy. Per-user **consumption limits**
(requests per hour, usage per month, configuration changes) and **consumption tiers** (Free /
Pro / Enterprise / Unlimited) stop runaway cost; when a limit is reached, the request is refused
rather than quietly allowed. Configured under **Administration → App Config → Security** (rate limits) and **Administration → Quotas** (consumption tiers). (Full
detail lives with the deployment-security pillar — noted here so the assurance stack is complete.)

### 2.10 Every trust process at a glance

Every trust-related process in one view — what value it delivers, how the process runs in plain
terms, where you operate it, and how you see it working. Nothing in the stack is a black box.

| Trust process | The value (what it protects) | How the process runs | Where you operate it | How you see it working |
|---|---|---|---|---|
| **Grounding** | answers stay inside the knowledge you gave the agent — no confident-but-wrong | flags any answer reaching outside declared scope; a second opinion confirms real drift; passes honest "not in my scope" refusals; can annotate or withhold | master switch + per-profile mode | a *grounding* note on the turn: pass / annotate / block |
| **Incoming guard** | a hidden instruction in a document can't hijack the agent | the incoming request is screened before the agent reads it; a hostile one is refused | overall switch (default posture) | a refusal turn; a *guardrail* note listing what was caught |
| **Outgoing guard** | no personal data or toxic content leaves the agent | the drafted answer is screened; personal data is masked, toxic answers withheld | overall switch + per-agent mode | a *guardrail* note with the categories found (labels only) |
| **Agent authority** | an agent acting on its own can't exceed the authority you gave it, and every such action is attributable | when an agent runs unattended it takes on a limited version of its owner; each attempted action is checked against that limit before it happens | per-profile Agent Authority card | actions marked as done *by the agent*; blocked attempts recorded |
| **Evaluation** | quality is measured, so decline is visible before users complain | every turn is scored the moment it finishes; a sample is also judged by an independent model. Deterministic scoring is free; the independent-model judging is a real model call whose tokens are metered against your consumption exactly like a normal turn (visible in the live counters and the turn's cost) | on/off + judge sampling | the *Quality* card on every turn; the Agent Roster (Trust) |
| **Governed-data lane** | you can tell whether an answer's figures came from a *governed* data product or the agent free-forming its own query | every data operation is tagged **governed** (a product/metric compiled to correct SQL — including the agent executing a compiled statement verbatim), **free-formed** (raw SQL the agent wrote), or **confined** (every raw attempt was *denied* by the data surface — nothing executed, which reads as the protection working, never as a failure); every governed consumption also seals a receipt into the turn's signed record | the OBDA-READ component (Administration → Components; the agent's Capabilities toggle — the plane is a component, there is no master switch); rewarded by evaluation | the *Governed Data Operations* note on the turn · the **Data products** row in the answer capsule (product · version · trust · rows) · a governed-data-ratio on the Quality card |
| **Evidence** | you can show what produced an answer and whether each part was signed and unchanged then — to anyone, without Uderia | each answer seals its parts and their hashes into its signed chain; signatures are kept as history; a hash-chained ledger makes any later edit or removal visible | nothing to switch on (with the Ontology Suite); custody and anchoring are opt-in settings | the **Evidence** row on every answer · *Used by N answers* on each trust badge · the Trust → **Evidence** tab and its ledger chip · the offline verifier |
| **Signed data products** | a business question has a *signed answer path*: the same request always compiles to the same governed SQL and the same rows — machine-proven before signing, probed against the live database | declare a product on the ontology (entities, the only joins allowed, measures) → the V1–V7 gate proves it deterministic (live probes catch a wrong cardinality or phantom column) → sign → optionally confine an agent to its products (surface-only) → every consumption is receipted | the ontology's **Products** dialog (Designer) + the profile editor's **Data surface** card + the OBDA-READ component (Administration → Components) | product trust chips (🟢/🟠/⚪) in the Designer · the capsule's Data products receipt · a `surface_breach` inbox item if the confinement ever leaks |
| **Provenance** | an unforgeable record of what actually happened, verifiable later | every turn is sealed into a tamper-evident record | always on | the trajectory trace on any past turn |
| **Trust sign-off** | you know an agent and everything it's built from was checked and is unchanged | each artefact is validated for its class, then signed; an agent is signed only when all its parts are. An agent's seal covers its parts **and its safety posture** — the grounding, guardrail and honesty modes that decide what it will and will not say — so weakening a gate flips the badge to 🟠 and calls for a deliberate re-sign. Agent *authority* is deliberately **not** sealed: it is deployment-bound access, legitimately different per owner (*trust ≠ access*), and is protected by fail-closed enforcement plus audit instead. Signing also states whether the posture actually acts: **clean** when at least one gate enforces, **partial** when every gate is observe-only — signing is never refused for an unhardened agent, it is only labelled honestly | each artefact's trust badge | 🟢 / 🟠 / ⚪ badges; the agent shield; the posture band on the sign-off dialog |
| **Posture portability** | a hardened agent stays hardened when you ship it, and a republish cannot quietly un-harden it | an agent pack carries the full trust posture (grounding / guardrail / honesty / authority) with the agent instead of leaving it behind at the deployment defaults. On a re-import, a manifest that would REDUCE an existing profile's protection is refused (`posture_downgrade`) unless explicitly allowed; a block the manifest omits always preserves what you already have | `POST /v1/agent-packs/import` → `allow_posture_downgrade`; dry-run reports it first | the refusal naming each weakened setting; the dry-run change plan |
| **Trust verify** | anyone can independently confirm a shared thing is genuine | re-checking the signature against the published content | the badge, or on hand-off | a verified / not-verified result, checkable even offline |
| **Ontology governance** | the shared knowledge model is valid and versioned, not ad-hoc — and an agent can be pinned to a **release** | check validity, then walk it through draft → release → retire, with optional approval; per agent, set the knowledge-graph component's **Ground on** to *released version only* so the agent reads the last release (never the draft), its knowledge-graph writes are refused, and its badge follows the release rather than every draft edit; with no release yet it reads the draft and the turn's card says so | the ontology's Govern dialog + admin policy; the profile editor's Ground on selector | validity report + lifecycle badges; the turn's *Grounded on* row |
| **Pack governance** | a distributed agent bundle is approved, trusted, and service-rated | the same lifecycle for a pack, with a service tier and a "must be trusted to publish" gate | the pack's Govern dialog + admin policy | lifecycle stepper, trust hero, SLA pills |
| **Consumption** | no agent can run up unbounded cost | usage is metered against per-user limits and refused past them | admin security settings | quota state; refusals when exceeded |
| **Honesty check** | a confident *"that doesn't exist"* built on failed queries never reaches the user unflagged | when an answer asserts non-existence and every data query that turn actually errored, the claim is flagged (or, in enforce, rewritten to an honest retrieval-failure); a legitimate empty result is never touched | per-profile honesty mode | an *honesty* note on the turn |
| **Signed actions (OBDA-ACT)** | an agent can change the business only through a contract a person signed — and every change has a receipt | an action type (target, allowed attributes, bound, conditions, posture) is proven against the live warehouse by the A1–A6 gate and signed; granted to an agent; in a conversation the agent prepares and a person confirms; `review` posture parks the write for approval; unattended faces act by posture; raw writes are refused as `action-only` | the **OBDA-ACT** component (Administration → Components; the agent's Capabilities — there is no master switch) + the ontology card's **Actions** (Designer: validate · sign · grant) | the answer row (Confirm → *Done · 1 row · receipt* · Undo); *Action* steps in Live Status; the capsule's Actions row; BOUNDED's acted/refused reading; the act in Trust History |
| **Four eyes on writes** | the person who asked for a write is not the person who approves it | an agent's *Approvers of parked writes* decide its parked writes; the owner is refused; a designation naming no active account refuses the park | profile editor → Agent Authority → *Approvers of parked writes* | the parked item in the approver's Inbox and in their own agent's answer; Trust History names who it awaited and who decided |
| **Model authoring (extensions)** | a model can grow where a question found a gap, without touching the signed base | an agent drafts a concept (the normalisation advisor's verdict in the answer); a person lands it (preview in words, dictionary readback), publishes and signs the extension; the base seal never moves | the `modelAuthoring` grant + the ontology's **Govern** dialog | the draft row; the Govern road (drafted · validated · deployed · published); the extension's trust reading (*changed · base moved* after a base re-sign) |
| **Grain-substitution disclosure** | an answer computed at a coarser grain than the question asked never presents itself as the asked answer | two deterministic triggers (0 tokens): a data product REFUSED a member and the agent answered at a coarser admissible grain without saying so (precise); or a ranking question names a product entity while the answer was grouped by none of that entity's asked dimensions (a narrow heuristic — it also catches the plan-time substitution that produces no REJECT at all). In annotate/enforce the gate **appends** a disclosure naming the substitution — never a replacement, never parked: the figure itself is correct governed data; only the framing was dishonest. Optionally, the heuristic is confirmed by the **Grain Faithfulness LLM judge** before acting (`honestyConfig.grainJudge`, off by default — a judge PASS withdraws the verdict, recorded; unavailable keeps the deterministic append). An answer that already names the limit is left untouched. The rendered product contract also states the rule at plan time | per-profile honesty mode + the Honesty card's "Confirm with the Grain Faithfulness judge" toggle | the appended *Note:* in the answer; the Honesty card reading "grain substitution disclosed" / "judge ruled faithful — verdict withdrawn" |
| **Master-switch honesty in the profile editor** | a profile control whose deployment master switch is OFF says so — and one whose machinery keeps running is never falsely marked | every gated card in the profile editor reads the deployment masters and marks itself: **deactivated — master off** (chip + banner + greyed, still editable — configure ahead of enablement) on the cards whose runtime genuinely short-circuits on the master (Grounding, both Guardrail cards, Judges, Smart Next Steps); the **OBDA-READ data surface** is deactivated the same way when its **component** is off (the OBDA planes are components, not masters, since Oct 2026) — **OBDA-READ is additive** (charter #15): with the component off Uderia behaves as if OBDA-READ were not installed for that agent (no contracts reach the planner, `query_product` refuses, the surface does not confine — gated once at the authority parse seam, so every consumer inherits). Cards with no master (Honesty incl. the grain judge, the non-surface authority controls) are never marked | the admin masters under Administration → App Config → Trust (and Features for Smart Next Steps); the OBDA components under Administration → Components | the "deactivated — master off" chip on a collapsed card; the banner naming where to activate; the surface block's note naming the OBDA-READ component |
| **Preflight SQL checks** | a hallucinated database name, an off-key join, or a text-stored date used as a string is caught *before* the query runs | three checks on every raw-SQL call, each with its own mode: the database references are validated against the authoritative catalog cached in the session; a JOIN between two tables whose key the ontology declares must be on that key; a DATE the ontology knows is deployed as characters may not be compared, ordered or bucketed as text (the stored shape is learned from result rows, and the refusal names the `CAST … FORMAT` rewrite or the one probe that settles the shape). A provable defect is logged (observe) or refused with a recovery-shaped error (enforce) | global preflight modes (`PREFLIGHT_SQL_MODE`, `PREFLIGHT_JOIN_MODE`, `PREFLIGHT_TEXT_DATE_MODE`) | preflight log lines; recovered turns |
| **Behavioral trust** | a signed-but-misbehaving agent cannot keep reading as fully trusted | every agent's recent conduct (recency-weighted pass rate + trust-critical checks, volume-gated, stale-decayed) fuses with its integrity badge into one composite state: Provisional / At-risk / Watch / Proven / Trusted; integrity can only *cap* the state, never promote it | Administration → App Config → **Trust** → *Enforcement* → *Behavioral Trust Score* (window, floors and staleness via the `trust_behavior` block) | the ring around the seal on the Agents roster; the Trust Posture matrix |
| **Circuit breaker** | sustained bad conduct triggers a response instead of waiting for the weekly review | when an agent's trailing record enters At-risk (its trust budget is exhausted), the owner is alerted — and in `freeze_autonomy` mode the agent's *unattended* use is suspended (Agent Authority) until a human reviews; interactive use is never touched | Administration → App Config → **Trust** → *Enforcement* → *Circuit breaker* (`trust_behavior.breaker_mode`: off / notify / freeze_autonomy) | the breaker alert; the frozen flag on the Agent Authority card |
| **Policy backtest** | the observe → enforce climb is decided on evidence, not anxiety | a proposed grounding/guardrail policy is replayed over your recent persisted turns — deterministically, at zero token cost — showing exactly what it *would have* done (masked, refused, escalated) with the affected turns listed | Trust → Trust Posture → Policy backtest | counts + per-turn samples; "nothing would change" is an explicit result |
| **Memory firewall** | a planted instruction can't become a *persistent* compromise via Agent Memory | every memory write (explicit, learned, or manual) is screened for injection patterns; a match is stored *quarantined* — preserved and inspectable, never recalled | rides the overall guardrails switch | the paused item in the Memory manager; a firewall log line |
| **Retrieval firewall** | a poisoned document can't steer the agent through retrieval | every retrieved knowledge chunk is screened; a match is *defused* — kept (never silently dropped) but explicitly labelled untrusted data the model must not follow | rides the overall guardrails switch | the firewall label in the context; a log line |
| **Tool-result firewall** | a poisoned tool output — a database row, or a **web page / browser / files / Google / Slack result** that says "ignore all previous instructions" — can't hijack the agent | every tool output is screened for injection through one shared entry, covering the database-tool path, the **connector/web path** (the highest-risk indirect-injection lane), and the ReAct engine (whose output re-enters context directly); observe-only (the result is never altered), surfaced to the Assurance Inbox | rides the overall guardrails switch | a `tool_result_injection` item in the Assurance Inbox; a log line |
| **Trust-aware routing** | a coordinator only consults experts you have actually signed | with `requireTrustedExperts` on a coordinator, routing excludes any expert whose badge is not 🟢 (fail-closed per expert); if none qualify the turn refuses with the exact blockers listed | coordinator profile `genieConfig.requireTrustedExperts` | excluded experts in the log; an honest refusal when none qualify |
| **Answer receipts** | the answer's *consumer* can see — and export — why to trust it | every assistant answer carries a "Why trust this answer?" capsule: the turn's trust journey, scope, data lane, guard findings (labels only), and provenance seal, exportable as a portable JSON receipt containing the Ed25519-signed chain | always available under each answer | the capsule — where the assurance dial renders, the verdicts live in the dial and the capsule keeps one folded *Made from* list (**Drew on** · **Did** · **To verify**); the downloaded receipt |
| **Assurance inbox** | trust incidents are worked items, not dashboard archaeology | three item classes, one rule each: **approvals** (a `review`-mode gate parked a withheld original — you decide), **incidents** (a protection acted: breaker trips, quarantines, injection catches, judge condemnations, every acting gate outcome derived from the trace, the session sentinel, and per-agent **station shortfalls** when a segment's rate goes red) — all ack-able, derived observations included; **receipts** (records of your own acts — never counted). Each row wears a **severity dot** (red/amber/muted) + the severity word + its **station chip**, and the list filters by **agent** (one pill per agent with items, IFOC-coloured) and by severity (everything / needs you now / worth a look / for the record); **Approve all shown / Uphold all shown** bulk-disposition exactly the filtered view — release into the conversation is deliberately never bulkable. Severity behaviour comes from ONE construct; each station carries an admin-assigned **reporting level** (everything / warning+ / danger only — danger always surfaces, and below-level items stay recorded behind "recorded below the reporting level") | Trust → Assurance Inbox; reporting levels: Administration → App Config → Trust → *What reaches you* (one selector per station) | the item list; the badge (approvals + unacknowledged, level-filtered); open items folding into the ring segments' colour |
| **Session sentinel** | a multi-turn probing campaign the per-turn guards each miss is caught | across one session, injection-flagged inbound requests are counted; crossing a threshold surfaces one session-level danger item | always on (derived) | a session-sentinel item in the inbox |
| **Resilience self-test** | the deterministic guards' recall is measured, not assumed | a curated attack corpus is run straight through the real injection/PII/toxicity detectors; per-category recall + benign false-positive rate + a headline score are reported (0 tokens, no live model) | Trust → Trust Posture → Run resilience self-test | the recall chips + score |
| **Audit pack** | an auditor gets a portable, verifiable evidence bundle | posture + eval aggregates + incidents + the resilience test + a compliance-control map (EU AI Act / NIST AI RMF / GDPR), Ed25519-signed with the deployment provenance key | Trust → Trust Posture → Export audit pack | the downloaded, offline-verifiable file |
| **Human review (HITL)** | a human dispositions withheld answers — nothing lost, nothing silently delivered | the **review** mode (all four answer gates: grounding incl. the corpus family, inbound guardrail, outbound guardrail, honesty) = enforce *with retention*: the answer is withheld/redacted exactly as enforce, but the original is parked in the review queue; the owner approves or upholds. Release is **append-only** — delivered history is never rewritten. Two release scopes: **Approve** (record only — the original becomes readable from the turn's "Why trust this answer?" capsule, labelled *released after human review*) and **Approve + release** (the owner explicitly releases the original **into the conversation**: subsequent turns receive it via the `review_release` context module, clearly labelled as a human decision, and its exact PII values join a session-scoped outbound allowlist so the agent can state what was released — forward-only, the sealed turn is never rewritten). Either way the owner is notified and the choice is a recorded trust event. Distinct from the **recovery ledger**: under plain Enforce a full withholding retains its original as a `retained` entry — recoverable from the inbox's collapsed "recoverable originals" section, but never owed, never counted, and no oversight claimed | per gate: Enforce + ☑ *keep withheld originals in the Assurance Inbox for my review* (stores the composite mode `review`; Grounding / Guardrails / Honesty cards); release scope chosen per item at decision time | pending items in the Assurance Inbox (Approve / Approve + release / Uphold); the decision + release scope on the turn's trace; the released original in the answer capsule; released content visible in the context-window snapshot of subsequent turns |
| **RAG grounding (corpus family)** | answers grounded in retrieved knowledge don't assert unsupported specifics | the grounding gate's second trigger family: unsupported numbers / verbatim quotes / identifiers vs the retrieved chunks, with a **second-chance targeted retrieval** before anything is flagged; escalation to the groundedness judge; same posture model (Observe/Enforce, each with its property checkbox — disclosure stores `annotate`, retention stores `review`) | `groundingConfig.mode` (+ optional `.corpus.mode`) | the Grounding Gate card's corpus rows (unsupported claims named verbatim, second-chance cleared); **dotted-underline marks on the unsupported spans in the answer itself** (annotate) |
| **Retrieval-sufficiency honesty** | weak retrieval never becomes a confident specific answer | the honesty gate's RAG branch: retrieval ran, found (near-)nothing, yet the answer asserts specifics → flag / honest "the knowledge base does not cover this" (enforce); opt-in per-profile `sufficiencyThreshold` | `honestyConfig.mode` | the Honesty card's "not covered — honest refusal" wording |
| **Ingest firewall + earned corpus signature** | documents are screened at ingest; the corpus signature attests *screened*, not just *unchanged* | every ingestion path screens for injection / PII categories / exfil URLs (observe-only — never blocks an upload); signing then REQUIRES 100% screening and resolved critical findings (quarantine or an acknowledged reason — a recorded human judgement frozen into the signature) | guardrails master switch | the **Corpus Health** panel in the repository inspection modal (coverage, PII manifest, findings, quarantine); the sign-off verdict wording |
| **Document quarantine** | a poisoned document can be excluded from retrieval in one click, reversibly | `retrieval_defuse`/ingest/anomaly inbox events carry the doc ref → **Quarantine doc**; excluded at the shared retrieval seam, still visible in the manager, reversible; flips the corpus signature until re-signed | owner action | the Assurance Inbox action; Corpus Health quarantine list |
| **Needs input (PROP-011)** | an agent that cannot go on without a person stops and asks — never a guess presented as the answer | Uderia raises the question in code where two of your things share a name or a step lacks a value; the model may ask through one tool or a marked block; no tool runs past a question, the gates abstain, the turn reads `input_required`; your answer re-enters bound in code | the agent editor's Authority card (*Needs input*: ask · never, park · fail, enforce · observe, standing answers); Administration → Components → Needs Input | the card under the answer; the Needs Input panel; the Inbox; REVIEWED's `needs_input` point and the `guessed_past_ambiguity` measure |
| **Suspicious-chunk browser** | a document-level finding can be traced to the exact chunks that trip the detectors | the repository Inspect view's chunk browser screens every displayed chunk live with the SAME detectors as the ingest firewall and stamps flagged chunks with problem tags (injection label · `pii:<category> ×n` · `exfil_url`); a **Suspicious only** toggle filters the whole corpus to its flagged chunks; **Export findings** downloads them (tags + full text) as a JSON report | guardrails master switch (controls the whole screening surface) | the Inspect view chunk table (tags + severity edge marker), the chunk detail window's Screening row |
| **Exfil-URL guard** | a poisoned chunk can't exfiltrate via a rendered link | outbound category `exfil_url`: novel AND exfil-shaped URLs (parameters / data-URI / IP host / userinfo) are defanged in enforce — text preserved, link dead, never silently deleted | `guardrailConfig.outbound` (+ `.exfil` boolean) | the Guardrail card's findings (defanged form only) |
| **Source provenance** | every answer shows *where* its knowledge came from and how trustworthy the source is | retrieved chunks carry the corpus integrity-signature state (🟢/🟠/⚪ — a SOURCE guarantee, never a consumption guarantee), CDC staleness ("verified N d ago" / "sync off"), and sensitivity | always-on (read-side) | the answer capsule's **Sources** row; retrieval events |
| **Sensitivity levels** | confidential repositories are governed per agent | per-collection `sensitivity` (public/internal/confidential); per-agent `knowledgeConfig.maxSensitivity` retrieval ceiling; answers drawing on a confidential repo carry a themed annotation | collection PATCH + profile config | the sensitivity note under the answer; capsule source chips |
| **Retrieval-anomaly monitoring** | an index-poisoning "hub" document is detectable statistically | per-doc retrieval frequency × query diversity over persisted turns (honestly labelled statistical detection; never auto-quarantine) | on-demand (Trust Posture) | "Scan retrieval patterns"; flagged hubs in the Assurance Inbox with one-click quarantine |
| **Corpus red-team lane** | the retrieval firewall + outbound guard are proven end-to-end with REAL planted documents | live probes plant payload docs in a caller-designated TEST collection, ask the retrieving question, score deterministically, and delete the payloads (cleanup status reported) | opt-in per run | the live-probe row's corpus input + cleanup status |
| **Compute with Lineage** | a number in the answer is either computed by code with a receipt, quoted from a result row, or honestly marked as the agent's own arithmetic — never silently invented | with the deployment switch on, the agent gains `TDA_Compute` (a closed set of operations — sum · avg · min · max · count · rank · pivot · diff · join · pct_change · filter — computed in-process over the turn's own result sets, never model-written code); every receipt is sealed into the turn's provenance chain and every figure in the delivered answer is traced ONCE to a receipt, a result row, or nothing; on Optimize the planner inserts the compute step itself when a report phase's goal is arithmetic | `TDA_COMPUTE_LINEAGE=1` (default off); per profile the component toggle under Capabilities (`componentConfig.compute`) + honesty mode `annotate`/`enforce` for the disclosure | the dotted-underlined figures in the answer (hover = the lineage, click = the rows), the *Number Lineage* step in Live Status, the GROUNDED station's *Number lineage* mechanism, and `numeric_fidelity` counting receipted figures as grounded |
| **Governed Tool Surface** | the model is shown only the tools that could answer this turn — the rest are held behind a search, and a tool authority denies is never offered nor found | ONE deterministic selector (backbone + framework always; identifier-token overlap with the question and the ontology's entities; a category the question names lifted whole; the tools nearest in MEANING to the question under one bounded rule, each carrying its score; denials first) armed per turn on both lanes, full descriptions kept for what is shown; `TDA_FindTools` widens the surface from the held set by the same two rankers and names a denied match | per agent, *Tools in context*: All enabled tools / Relevant per turn, in the profile editor's **Data sources → Tools** (it covers every server the agent reads; no deployment switch; a new agent starts on Relevant per turn); the meaning ranker's rule is fixed (floor 0.18 · 80% of the best · at most 5) | the *Tool Surface* step in Live Status (chips with reasons), BOUNDED's *Tool surface* mechanism |
| **Regenerate as an Experiment** | a second answer comes with the reason to prefer it — a regenerate is a scored sibling under the same question, compared by the Scorecard's verdicts, one adopted; an edit-and-resend is a branch the record keeps | siblings and branches are lineage keys on the turn record (append-only); a non-adopted sibling and a displaced branch turn are out of the model's context by the ONE existing mechanism (`isValid`); every sibling is scored like any turn | `TDA_TURN_LINEAGE=1` (default off); Regenerate ▾ / Edit on the conversation, no per-agent setting | the *Experiment* card under an adopted answer (verdict strips · Adopt · Compare), the branch switch on an edited question |
| **Grounded Multimodal** | an attached image, PDF, CSV or text file is a *source*, not a prompt — Uderia reads it itself and vouches for what it read; a figure quoted from a table in a screenshot traces to the file and its region | every upload is read once, at upload, deterministically (table OCR on an image, tables/text from a PDF — a scanned PDF rendered and OCR'd — a CSV's rows, a text file); a VERIFIED read joins the grounding evidence the gate holds the answer to and its tables become result sets the compute tool can operate on; a model transcription of an image nothing could read (per-agent opt-in) is recorded UNVERIFIED and is never evidence | `TDA_ATTACHMENT_SOURCES=1` (default off); optional extras `rapidocr-onnxruntime` / `pdfplumber`; per agent `attachmentConfig.transcribeImages` (Trust Configuration → GROUNDED → Attachment Sources) | the badge on every attachment chip (`read · OCR · 1 table · 3 rows` → `used as source`), the image as its thumbnail, the *Attachments* row in *Why trust this answer?* with a crop of the cited region, the *Attachments Read as Sources* step, the GROUNDED station's *Attachment sources* reading |
| **Steer & Reattach** | a running turn survives the connection and can be talked to — a dropped laptop lid, a reloaded tab or a proxy timeout no longer loses the turn, and a change of mind no longer means Stop-and-ask-again | every frame of a UI turn is logged with a cursor; a client reattaches from the last cursor it saw and gets exactly what it missed, then the live tail; a steer sent while the turn runs is delivered at the agent's next step (the next tool result, the next tactical prompt, the final report) under an explicit operator lead, or carried to the next turn when no step is left — and recorded either way | `TDA_STEER_REATTACH=1` (default off) | the composer stays live while an answer streams (*Steer the agent — considered at its next step*; Enter steers, the button stops), the steer pin under the question (*sent* → *considered with the result of …*), the *Steer Considered* / *Steer Carried to the Next Turn* step, the *reconnected — caught up* note |
| **Outcome Loop** | a draft the grounding check flags with a fixable finding is revised once before delivery, graded by the same check — a revision that clears is delivered clean, one that does not delivers the honest annotated first draft, never a second silent try | the gate's own deterministic findings (unsupported claim · out-of-scope reference · ungrounded number) go back to the model with their spans and the fix; one LLM call, history off; the revision re-graded; both drafts sealed into the provenance chain | `TDA_OUTCOME_LOOP=1` (default off); per agent the Outcome Loop card under Trust Configuration → GROUNDED (on/off · max revisions · revisable kinds) | the *Outcome Revision* step in Live Status, the answer capsule's *Drafts* tabs, GROUNDED's *Outcome loop* mechanism |
| **Visible Budget** | a turn paces itself against a declared budget — steps, tokens, seconds — the model sees what is left, and an exhausted unit is a recorded graceful finish that says what remains, never a killed turn | the profile's `turnBudget` is armed per turn; every tool boundary carries the `[Budget]` note and refuses a further data-tool call once a unit is used up (the step is reserved at the gate, so a batch of parallel tool calls cannot overrun it); the planning lane skips the phases the budget cannot afford and tells the report to say what is left; a hard stop that fires first is recorded as a kill | `TDA_TURN_BUDGET=1` (default off); per agent the Turn Budget card under Trust Configuration → BOUNDED | the Turn budget bar in the Live Status header, the finish line under the answer, the *Budget Exhausted* steps in Live Status, BOUNDED's *Turn budget* mechanism + *Budget Adherence*, the *Continue where it stopped* chip |
| **Resilience Envelope** | a transient provider failure is retried and audited, a truncated answer is completed, a dead provider is served by the SIGNED fallback, a refusal is an honest answer — every recovery a recorded, sealed act | ONE policy classifies every error (retry only what a retry can change, honour retry-after, fail fast on the terminal), continues a cut-off answer at most twice, serves an exhausted or breaker-open primary from the profile's `llmFallbackConfigurationId` (and only that), and turns a provider refusal into a delivered answer naming the category; a per-provider circuit breaker routes to the fallback while the provider is down. When no model answers at all, the turn gets an honest sentence naming what the provider said (never the raw error), a failed record, and a red JUDGED | `TDA_RESILIENCE_ENVELOPE=1` (default off); per agent the Fallback LLM in the profile editor (part of the signed deployment) | the `served by <model>` chip on the answer, the *Provider Retry / Answer Continued / Served by Fallback / Provider Refusal* steps in Live Status, SIGNED's *Recovery* mechanism, the header's provider-health strip while a provider is degraded, `GET /v1/llm/provider-health` |

### 2.11 The closed trust loop — how the new layers compose

The stack above no longer just *measures* and *reports* — it closes the loop:

1. Every turn renders its **Trust Journey** (the §2.0 pipeline as a live strip in
   the turn's trace) and offers the consumer an **answer receipt**.
2. Every agent's measured conduct feeds its **behavioral trust state**; the seal
   (integrity) and the ring (conduct) are one glyph on the roster.
3. Exhausting the trust budget trips the **circuit breaker** — alerting, and
   optionally freezing autonomy, instead of waiting for a human to notice.
4. Before tightening any policy, the **backtest** replays it over sealed history
   so enforcement decisions are made on evidence.
5. The three **firewalls** (memory, retrieval, tool-result — the last covering the
   connector/web lane and the ReAct engine) close the indirect-injection paths
   the edge guardrails cannot see; **trust-aware routing** makes compositional
   trust operative at coordination time, not just visible on a badge.

Nothing in this loop weakens the founding principles: everything ships off or
observe-only, every enforcement fails closed, every action is attributable and
visible, and computing any of it costs zero tokens.

---


### 2.12 Signed data products (OBDA-READ) — determinism you can verify

The newest layer (Aug 2026) goes one level deeper than everything above: instead of
observing, grading, or enforcing what an agent *says*, it constrains what an agent can
even *reach* — and hands the reader a sealed receipt for every number that crossed the
boundary. Full architecture: [OBDA_ARCHITECTURE.md](../Architecture/OBDA_ARCHITECTURE.md);
the visual story: *The Anatomy of a Signed Data Product* on the trust site.

**Reading the Products dialog** (the gold **Products** button on an ontology's card —
rendered only when the **OBDA-READ component** is enabled for you — Administration → Components):

- Each product row carries three chips: **validated / unvalidated** (has the gate run
  for *this* manifest?), **signable / blocked** (did it pass?), and the trust state
  (🟢 trusted · 🟠 changed · ⚪ unsigned). A product that was edited after signing
  reads 🟠 until re-validated and re-signed — the ratchet.
- The **Verification selector** chooses the evidence tier: *Structural* (declarations
  trusted, stated honestly in the report) or *Live* against a chosen database
  connection — the V7 probes execute the battery at the real target and empirically
  spot-check every declared cardinality and key. **A contradiction blocks signing and
  names the declaration the data disagreed with.** The report's mode (live vs
  structural) is part of what gets signed, so an empirically-verified signature is
  distinguishable from a declaration-trusting one.
- The **grain-safety grid** shows every measure × dimension pair: solid = safe, dashed
  = rejected (hover names the offending 1:N hop). A query outside this grid is
  refused, never silently multiplied.
- The **sign ceremony** states exactly what is attested: the manifest hash AND the
  validation-report hash — the proof travels inside the signature.

**Reading a consumption.** A governed answer's capsule ("Why trust this answer?")
carries a **Data products** row — `fitness_sales v1 · trusted · 32 rows` — with the
request hash, compiled-SQL hash and the seal note one hover away. The receipt is
sealed into the turn's Ed25519 chain (step type `obda.consumption`) and rides the
existing **Export receipt**, verifiable offline. The Governed-Data note's **lane**
reads `governed` (all operations through products), `free-form`/`mixed` (the agent
wrote raw SQL), or **`confined`** — every raw attempt was *denied* by the data
surface and nothing executed: that is the protection working, and it reads as a pass
at GROUNDED, never as a failure.

**Choosing what an agent may plan with.** OBDA-READ is a component: an administrator enables it
for the deployment (and per user) under **Administration → Components → OBDA-READ**, and each
agent switches it on in its **Capabilities**. Where it is off Uderia behaves as if OBDA-READ
were not installed — no product contracts reach the planner, `query_product` refuses, nothing is
confined. Where it is on, the agent's **Data surface** (profile editor → Trust Configuration →
*Agent Authority* card) is one of three choices:

- **Disable OBDA-READ** (the default — every unconfigured agent, and any agent whose product list is
  empty): no products are visible to the planner at all.
- **Surface Full**: ONLY the listed products; contract-only enrichment (the planner is shown the
  product contracts, not the table schemas it could hand-write SQL against); raw SQL denied
  fail-closed when the agent runs unattended (or interactively too, with *structural
  confinement* ticked); only 🟢 products served; the one exception is the statement the surface
  itself compiled this turn, executed verbatim. **Advisory** is this mode's observe-only
  development sub-toggle — nothing denied, violations observed and scored.
- **Products + regular paths**: the listed products are offered as contracts *beside* the
  ordinary knowledge-graph paths and the model decides per question. The list is still the
  grant — a product not on it is refused by name.

If a Surface Full agent ever *executes* free-formed SQL, a danger **`surface_breach`** item
lands in the Assurance Inbox — *"a hole report, not agent misbehaviour"* — because the
structural confinement should have made that impossible. And when a product refuses the grain
a question asked for and the agent answers at a coarser one, the honesty gate appends a
**grain-substitution** note: the figures may still be useful, but they are not the asked
answer, and the reader is told so.

**The knowledge twin.** The Knowledge card's **Signed knowledge only** toggle
(`requireSignedKnowledge`) is the same posture for the other modality: only
collections whose integrity signature is signed-and-intact back this agent's answers.
If the trust suite is physically absent it fails *open* with a loud warning rather
than silently emptying the knowledge lane.

**Composition.** A product built from signed products signs with its members'
attested hashes inside its own signature (lineage). An unsigned member blocks the
sign-off *naming the member*; a re-signed parent flips the composed child 🟠 until it
is re-signed (and a parent *shape* change forces re-validation first).

### 2.13 OBDA-MODEL and OBDA-ACT — acting under signature

Everything above governs what an agent *reads* and *says*. OBDA-MODEL and OBDA-ACT (Oct 2026) govern what it may
*change* — and it does so with the same pattern as signed data products: a contract on the
ontology, a gate that proves it against the live warehouse, a signature with the proof inside,
and a receipt for every act. Full architecture:
[ACT_ARCHITECTURE.md](../Architecture/ACT_ARCHITECTURE.md); the visual story: *The Anatomy of a
Signed Action* on the trust site.

They act on two planes that share one road (propose → validate → land → publish → sign → use):

| Plane | What changes | Who decides | What guarantees it |
|---|---|---|---|
| **OBDA-MODEL** | the ontology and the warehouse schema — a new entity, a new attribute, a performance structure | an agent may only **draft**; a **person publishes** | the extension's own signature (the base model's seal never moves); the design standard read back from the warehouse dictionary |
| **OBDA-ACT** | rows — record a lead, close a ticket, set a tier | the action type's signed **posture**; a person confirms or approves | the action's earned signature (A1–A6 gate), one transaction with checks, a sealed receipt |

Three properties hold everywhere: **nothing writes outside a signed contract** (a raw
`INSERT`/`UPDATE` from an agent is refused at the SQL gate as `action-only`); **the base is never
touched** (an extension, an action type, a second-warehouse landing and a tuning structure are
all *stacked* on the graph and never move the base ontology's seal); **every act is
accountable** (a receipt naming who, which contract, which statement, how many rows and through
which door, and one append-only act log every audit surface reads).

**The components.** OBDA-MODEL and OBDA-ACT are components, like OBDA-READ — there is no master
switch. Each is available where Administration → Components enables it (for the deployment, or per
user) and the agent's **Capabilities** switches it on. Off ⇒ Uderia behaves as if that plane
were not installed for the agent: no authoring or action text reaches any prompt, `TDA_Action` is
not offered, and the REST faces refuse with `403 obda_model_off` / `obda_act_off`. On ⇒ nothing acts
until an action type is signed **and granted** to an agent. A separate **OBDA policy** (Administration
→ App Config → **Trust** → *Enforcement* → *OBDA policy — contracts only*, one `off · observe ·
enforce` row per plane) decides whether contracts are the *only* way, on every turn including
interactive ones: under `enforce`, OBDA-READ reads only through signed products, OBDA-ACT writes
only through signed actions (a raw write is refused as `action-only`) and OBDA-MODEL refuses direct
graph edits; under `observe` the would-be refusals are recorded as trust events instead.

**When the model is missing a concept.** An agent with the *model authoring* grant that meets a
gap ("which leads are open?" on a model with no Lead) answers with the gap first and a proposal
in the answer row: a normalisation advisor decides entity vs attribute from the rows (or says
*provisional · no rows to measure*), and refuses what the data cannot support (a per-record link
between two areas that share no key). One click — **Draft it** — writes the draft on the
graph's *authored* layer, visible to its author only. The ontology card's **Govern** dialog
then walks the road: a landing **preview in words** (which database — the model's own when the
deploy login owns it, otherwise a sibling Uderia owns, `UDERIA_EXT_<root>` — which columns, which
key), **Land**, the dictionary **readback** per design rule (`held · violated · unverified` —
never a vacuous pass), **Publish** (a person's act; an agent is refused under every policy),
and **Sign** the extension. The extension's signature carries the base's signature inside it,
so a base re-sign reads the extension *changed · base moved* while the base stays green.

**Designing an action.** The ontology card's **Actions** button opens the Action Designer: per
action the journey **validated · signed · granted · in use** and only the act that is owed. The
manifest declares the target, the operation (`insert · update · upsert`), the attribute
allowlist with value domains, conditions, a required row bound, the required database role and
the **posture**. The **A1–A6 gate** proves it against the warehouse: the target is what the
resolver answers today, columns and domains agree, the key is unique (measured live), conditions
read only the target, the statement is one parameterised write with a clean `EXPLAIN`, and the
role holds *exactly* the rights the operation needs. Live evidence signs `verified_write`;
structural evidence signs partial. Grant it to an agent from the same dialog — a grant is
access, not trust, so it moves no signature.

**Where an action writes through.** An action owns its connection: the one you validate it
through. Perform writes through that connection on every path (a confirmed write, an approved
review, a scheduled or flow run, an undo), and each action's row in the Designer says *writes
through …*. Choose the connection whose login holds the action's role — never the model
operator. To move an action to another login, validate it through that connection and sign it
again. There is no separate setting in the agent's profile.

**When the model under an action changes.** The signature covers the action's contract, its
proof, and the **graph facts the proof read**: which table the target resolves to, the target's
attributes, and the allowed values of the attributes it writes. Change one of those — a new
allowed value, a new attribute on the target, a different deployed table — and the action reads
*changed* with what moved (*the allowed values of status changed*): it is not performed, signing
the old proof is refused, and the Designer's Next is **Validate again**, then **Sign**. A change
anywhere else in the graph — another entity, a re-signed base — leaves the action as it is. The
reverse never holds: a changed action never moves the graph's signature (the graph does not
depend on its actions); the action alone reads *changed* until it is validated and signed again.
A draft on the target changes nothing until it is published. An action on an entity of your
extension also needs that **extension** signed and intact: when it is not, the Designer's Next is
**Open Govern**, where the extension is published and signed — then the action is signed.

**What the warehouse must grant — checked before you act.** Every OBDA-MODEL or OBDA-ACT act goes through a login, and
every right it needs is stated and checked *before* anything runs: Govern's **Preview** lists the
landing login's rights (held / missing) and keeps **Land** disabled until nothing is missing; the
Actions Designer's **Access in the warehouse** — in the Define form, before you define, and in
each action's details — lists what the write login and the action's role need, and the model
login for the action's ledger. Each missing right comes with the exact `GRANT` statement (and
**Copy the grants**) for a warehouse administrator to run. The full list:

| Act | Who | Must hold | Why | When it is checked |
|---|---|---|---|---|
| **OBDA-MODEL — land** a new sibling database `UDERIA_EXT_<root>` | the model connection's login | `CREATE DATABASE` on the parent (the login's own space by default — a login is not granted this on itself automatically) | the landing creates the database; the creator then holds every right inside it | Govern **Preview**; **Land** is disabled and refused (`409 access_missing`, nothing changed) while missing |
| **OBDA-MODEL — land** a new table in an existing database | the model connection's login | `CREATE TABLE` on that database | creates the table | as above |
| **OBDA-MODEL — land** a new column on an existing table | the model connection's login | `DROP TABLE` on that table | Teradata's `ALTER TABLE` needs it | as above |
| **OBDA-MODEL — land** a reference to a base table | the model connection's login | `REFERENCES` on the base table | the soft-RI `FOREIGN KEY … REFERENCES WITH NO CHECK OPTION` | as above |
| **OBDA-MODEL — the action ledger** | the model connection's login | `CREATE TABLE` on the target's database | the ledger `<db>.UDERIA_ACT_<table>` is created once, at the first validation | the Designer's **Access in the warehouse** (Define form and details); a refusal is named at validation |
| **OBDA-ACT — perform** | the write connection's login | the action's role (`GRANT <role> TO <login>`) | every write session acts under the role (`SET ROLE`) | Define form + details before anything runs; validation's probes run under the role; perform refuses `role_not_held` |
| **OBDA-ACT — perform** | the action's role | exactly the operation's rights on the target — insert `INSERT, SELECT` · update `UPDATE, SELECT` · upsert `INSERT, UPDATE, SELECT` — and `INSERT` on the ledger; **nothing wider** (A6 refuses a wider role) | the write, its pre/post checks and the before-image; the ledger entry | Define form + details; **A6** at validation |

```sql
-- OBDA-MODEL (the model connection's login, e.g. uderia_ext_admin)
GRANT CREATE DATABASE ON <login> TO <login>;          -- a new sibling database under the login's own space
GRANT CREATE TABLE ON <landing_db> TO <login>;          -- a new table in an existing database
GRANT DROP TABLE ON <landing_db>.<table> TO <login>;    -- a new column on an existing table
GRANT REFERENCES ON <base_db>.<base_table> TO <login>;  -- a reference to a base table
-- OBDA-ACT (the write connection's login, e.g. policing_investigator, and the action's role)
GRANT <role> TO <write_login>;
GRANT INSERT, SELECT ON <db>.<table> TO <role>;          -- insert   (update: UPDATE, SELECT · upsert: INSERT, UPDATE, SELECT)
GRANT INSERT ON <db>.UDERIA_ACT_<table> TO <role>;       -- the ledger, append-only
```

**The posture** is part of the signed contract, not the agent:

| Posture | What happens when the action is performed |
|---|---|
| `observe` | records what would be written; writes nothing |
| `review` | runs the write in a rolled-back transaction (so the diff is the database's own answer) and **parks** it in the Assurance Inbox; nothing is written until a person approves |
| `annotate` / `enforce` | writes in one transaction: pre-checks → before-image → statement → post-checks → ledger → commit only if every check held |

**Performing.** In a conversation the agent never writes directly: it **prepares** — the answer
row shows the change (`status open → closed`) and a **Confirm**; the confirm performs only the
invocation whose hash the person saw. An update that would change nothing is refused before
anyone is asked. Unattended doors — a flow node, a scheduled task, a dashboard tile — never
prepare: the posture decides, and they always act as the agent. Every performed act gets a
receipt — sealed into the turn's Ed25519 chain, or, outside a turn, signed on its own over the
act and its occurrence (face, time, who confirmed) — verifiable offline with the public key
alone. **Undo** replays the ledger's before-image through the same signed contract, confirmed
like any act.

**Four eyes.** The *Approvers of parked writes* field on the Agent Authority card names the
accounts that decide the agent's parked writes. With approvers set, the agent's owner can
**not** approve their own agent's write (`403 not_your_decision`); the approver sees the item in
their own Assurance Inbox and in their own agent's answer (`TDA_Action pending_approvals`, with
Approve / Reject buttons the person clicks — no agent ever approves). A name that is not an
active account refuses the park — a write is never parked where nobody may decide it. The write
still runs as the owner's agent on the owner's connection; the ledger, the act log and Trust
History name who decided.

**Reading it on the trust surfaces.**

- **Live Status:** every act is a step — *Action Prepared · Performed · Parked for Review ·
  Observed · Refused* — live and on reload.
- **Answer capsule:** an *Actions* row.
- **Trust Circle — BOUNDED** reads the turn's acts (acted · refused, the refusal named) and names
  the actions acted on in recent sessions; **REVIEWED** counts a granted action in `review`
  posture as able to park.
- **Assurance Inbox:** a parked write appears as the database's diff with **Approve — perform it**
  / Reject; rejecting writes nothing and the answer row reads *refused* after reload.
- **Trust History / the signed audit pack:** every act through every door — conversation, REST,
  MCP, flow, schedule, dashboard tile, Inbox approval — from one append-only act log.
- **Scorecard:** `action_safety` (informational) — of the writes a turn put to the database, the
  share whose every check held. The `write-escape` live red-team probe lures an action-only
  agent into a raw `UPDATE` and scores whether it held.

**Workload tuning.** The ontology's Update dialog carries a *Workload tuning* segment: from what
the agents grounded on the graph actually ran, Uderia proposes statistics or a covering
join index — never applied until a person approves, measured before and after when it lands
(including whether any plan changed shape), and signed. A view is never tuned; its base tables
are named instead.

**Honest limits.** `delete` is not an operation (so an insert cannot be undone); approvers are
named per agent, not per action, and there is no quorum; MCP actions are developer tier only;
the act log is fail-soft (an act whose log write failed is still in its own record and the
warehouse ledger).

### 2.14 Evidence — what produced an answer, and was each part signed

Every answer records **what it was made with**: the agent, its ontology, skills and
knowledge or planner repositories, the model and tool catalog it ran on, the data products it
read and the writes it performed. For each part the record holds the content hash at the
moment of answering and the signature that matched it, if any. This record (the *bill of
materials*) is sealed into the answer's signed provenance chain, so it cannot be edited
afterwards without the chain failing to verify.

That turns a single question into something you can answer, check, and hand to someone else:
**for this answer, what produced it, and was each part signed and unchanged at that moment?**

| Where | What it shows | Why it matters |
|---|---|---|
| **The answer** — *Why trust this answer?* → **Evidence** row | `7 parts · all signed when answered · 1 changed since`, with the exception named on the row; opened, the parts as a short tree, each with two readings in a fixed order: **when answered · now** | what the answer was made with, and whether any part has moved since |
| **An artefact's trust badge** | *Used by 214 answers · 31 with this version* | when a flaw is found in an ontology or skill, you see at once which answers it touched |
| **Trust → Evidence tab** | every signature you may see, worst first: changed since signed, made with a retired key, recorded without a signature; plus the **ledger** chip | the estate-wide reading, and one export for an auditor |
| **Outside Uderia** — the evidence verifier page, or `scripts/verify_evidence.py` | a dropped bundle checked in the reader's own browser or Python, no Uderia server involved | an auditor does not have to trust Uderia's UI to check Uderia's records |

**How each record is protected.**

- **Signatures** (Ed25519, the deployment's provenance key) prove a record is byte-for-byte what
  was signed, and who holds the key that signed it.
- **History, not overwrite.** Re-signing an artefact adds a record; the earlier signature
  stays. An answer's evidence therefore always names the version it was actually made with.
- **The evidence ledger** chains every signing event and every answer's record by hash.
  Editing, inserting or removing one afterwards breaks the chain, and the Evidence tab says so
  (*"ledger NOT intact — 1 record edited after entry"*). Every export carries a signed
  checkpoint of the ledger, which later proves the ledger still contains it.
- **Evidence outlives the session.** Deleting a session removes the answer's text; a
  content-free record (which parts, which hashes, which signatures) remains and reads
  *"session deleted — content not retained"*.

**What it does not prove.** A signature proves what was recorded and that it has not changed,
not that the answer is right; that is the job of grounding, guardrails and the judges. An
answer given before evidence recording existed reads *"parts not recorded — this answer
predates evidence recording"* and can never show its parts. And with the default setting the
signing key lives on the server, so its administrator could rewrite and re-sign history;
§3.5b describes the two opt-in settings that close this.

## Part 3 — HOW: operating the assurance process

This is the runbook. It assumes an **admin-tier** account.

### 3.0 Reading the Trust Circle

**Where:** *Trust → Trust Circle*. Pick an agent from the star at the centre, or leave it on the
whole estate.

The ring is eight live readings of one agent (or the estate), arranged as the loop the assurance
stack actually runs: **configuration → behaviour → judgement → back to configuration**. It is not
an illustration; every station reads from the same records the REST API serves.

"The estate" always means **your** estate. Even in admin system scope — where the Awareness and
Trust lenses can span every user — the ring restricts its execution readings to your own agents
before any station reads them, because its configuration side (your profiles) and SIGNED (your
attestations) can only ever describe yours. Both attributes of a reading describe one subject;
`4/12` pairing your four profiles against twelve agents belonging to everyone is the exact
mixed-subject reading the two-attribute model forbids.

#### Each station shows two things

Under every station you see a pair:

```
review · 1/4
   │       └── execution: what actually happened in the scored window
   └────────── configuration: how the control is set
```

Those answer different questions, and both matter:

- **Configuration** — is this control set up so it *would* work?
- **Execution** — did it *actually* work?

The pair is what makes drift visible. `review · 1/4` means *"configured to withhold a bad answer,
but only 1 of 4 scored turns came through clean."* Either number alone would mislead you.

#### One grammar, four channels (every trust ring in Uderia)

The same visual language holds on the Trust Circle, the per-answer assurance dial, the mini
glyphs and the Assurance Inbox — each channel has exactly one job:

- **Colour = attention priority.** Green *quiet* · amber *worth a look* · red *needs you now* ·
  grey = the absence family. Colour is never a verdict — the verdict is the **word** on the chip
  ("blocked", "masked", "parked", "clean"). **A chosen posture is never an alarm:** a gate you set
  to *watch* (with or without disclosure) that found nothing reads **watching** — quiet green
  like any station that needs nothing from you, but **hollow** and thinner, because it stops
  nothing. Dashed always means *not turned on*.
- **Ticks = engraving.** One tick = detail recorded behind the mark; **two ticks = a gate
  ACTED** — on this answer (the dial) or on at least one turn in the window (the ring).
- **The accent (orange) dot = an act is owed** — sign, attest, or decide. Pin the station and
  the button to perform it is right there. Outstanding work is neither an event nor a verdict,
  so it rides the actionability channel, not the ticks and not the colour.
- **Seven states, everywhere:** pass · warn · danger · **watching** (hollow green — a posture that
  records and stops nothing) · not turned on (sparse dash) · **not in yet** (dense moving dash —
  a reading still coming) · **not recorded** (a gap in the trace, never mistakable for a pass).

What the *numbers* mean is decided by **altitude**, not by surface: estate = worst-of
`x/y agents` · agent = mode · windowed rate · per turn = **events** (nothing is a rate).

#### Colour and the inbox correlate — by construction

An **open** inbox item folds into its station's colour (a danger item ⇒ the segment reads red
until you decide or acknowledge it), and a station red from a failing *rate* files one ack-able
**station-shortfall** observation per agent — so a red segment always resolves to a worked item
in the Assurance Inbox, and acting on the item releases the fold. An acknowledgment means
*seen*, never *fixed*: the measured rate stays red until the turns improve.

#### Where the numbers come from — press the "?"

Pin a station (click it) and a **?** appears beside the close control. It opens *"How these
figures are derived"*: the **population** the figure was measured over, how each attribute
was computed, and — where quality scores contribute — the **contributing dimensions as
clickable chips** that open the Assurance Guide, scoped to that station and showing each
dimension's live status.

This is the answer to the most common question about the ring: **the ring counts TURNS; the
Quality card counts CHECKS.** `12/18` on GROUNDED means twelve of eighteen scored *turns*
were clean on its dimensions — it is not comparable to the Quality card's "24 checks", which
grades the *one turn* you are looking at.

**Every check is counted at JUDGED. The other seven stations report what their machinery
did.** These are two different questions asked at two different moments. A guarantee
station answers *what ran during the turn* — a gate that escalated, a guard that
redacted, a boundary that denied a tool. A check answers *how the finished answer graded*,
taken after it shipped by a scorer that changed nothing. So every check lands at JUDGED,
whose whole subject is exactly that, and the seven guarantee stations read their own
machinery alone.

The practical consequence: **a red Grounding Overlap colours JUDGED, not GROUNDED.**
GROUNDED tells you what its four gates did; JUDGED tells you how the answer graded. Both
are true, and reading them as one number was the confusion this change removed — a gate
that did its job all turn could read red because a scorer graded the answer poorly
afterwards, with nothing on screen saying which half had moved.

Each check still records **which guarantee it is about** — you see it as a chip on its
Assurance Guide entry, and it is how you get from a poor score to the machinery that
should have prevented it. That is a label for navigation; it is not where the check is
counted.

The one place everything comes together is **TRUSTED**, the sole aggregate: a
recency-weighted blend of the trust-critical checks with every remaining check, each
counted exactly once, capped by integrity.

**Inside JUDGED, the checks are grouped by the MOMENT they measure** — Plan · Retrieve ·
Act · Compose · Deliver, plus *The run* (cost, latency, volume, termination) and *Flow
run* for flow-agents. That is the process an agent actually runs, so a poor group points
at a stage of the work rather than a category of metric.

#### What you can change, and what is fixed

Two things in the Assurance Guide look alike and are **not** equally under your control:

| | Configurable? | Where |
|---|---|---|
| **Which moment a check belongs to**, and which guarantee it is about | **No** — both derived in code | a code change |
| **A check's pass line** — but only for **deterministic** ones | **Yes** | Administration → App Config → **Trust → Quality bands** — each slider sits under the moment it measures |
| **A judge dimension's verdict** | **No threshold exists** — a model returns the verdict | you can turn judges on/off and change how often they sample |

Deterministic dimensions come in **three** shapes, and the Guide says which one you are
looking at:

| Shape | What it means |
|---|---|
| **Scored, with a pass line** | a number cut by a threshold you can set (Grounding Overlap, Cost, Latency…) |
| **A yes/no check** | it either holds or it doesn't — no score, nothing to configure (Safety, Completed, Output Sanity…) |
| **Informational** | reports a number for context and **never convicts a turn** — no pass/fail at all (Plan Execution, Efficiency, Context Health…) |

The third is easy to mistake for the second. A dimension that never returns a verdict
cannot fail a turn, so it never contributes to a station's clean count — it is there to be
read, not to judge.

The Guide states this per dimension: open it (from a station's **?** explainer, or *"What do these
mean?"* on the roster) and each entry names its pass line, the exact admin key that moves it, or
says plainly that there is nothing to set. The `deterministic` / `LLM judge` chip beside each name
is the tell.

**The Guide shows your CURRENT bands, not the shipped defaults.** Each entry reads
*"Bands now: Healthy ≤ $0.30 · Danger > $0.60"* for what is actually configured on this
deployment, read live from the same settings the sliders write — so it stays right the
moment you retune them, and the comparison direction follows the metric (`≤`/`>` where
lower is better, `≥`/`<` where higher is better). A metric you have not tuned says
**not set — measured, but not banded**; the one exception is Grounding Overlap, where the
scorer still applies a code fallback of **Danger < 50%**, and the Guide names it rather
than implying nothing convicts. Out of the box only two metrics ship banded
(`max_out_of_scope_dbs`, `max_tool_calls`).

#### Two lenses — the whole record and the current leg

The ring reads through one of two lenses, toggled at the top-left of the stage
(*Whole record · This leg*) — the speedometer model: the odometer never resets, the
trip meter does.

- **Whole record** (the default) — everything in the retained window, exactly as
  before. A trip reset changes nothing here, ever.
- **This leg** — each agent since its **trip reset**. After you fix an agent
  (re-tune it, quarantine a document, re-sign it), *"Start a new leg"* on the
  ring's narrative panel marks the moment; the leg lens then answers "how is it
  doing *since* the fix" without the old failures dragging the rates down, and the
  station explainers compare the leg against the previous one — *did the fix work?*

What a reset is **not**: it deletes nothing. Every turn, verdict, incident and
review stays on the whole record; the reset appends an audited marker (who, when,
your reason, and the agent's signing state at that moment) that appears in the
agent's Trust History and its story. While the leg lens is active, every scoped
reading names its boundary ("since 30 Jul") and an amber leg chip rides the ring —
a clean leg can never masquerade as a clean history.

The same leg is available on the **Agent Roster**: expand an agent and a
**This leg** pill sits beside the runs filters (only when a leg exists) — it
scopes the dimension bars, the work mix and the runs ledger together, each
section carrying the "this leg · since …" chip.

What the lens never touches: configuration readings (SIGNED, BOUNDED, the modes),
the behavioral badge, the circuit breaker, and the audit pack all keep reading the
whole record — the lens is how *you* look at the agent, not how Uderia judges
it. Only your own agents can be reset, and an empty leg reads "empty, not clean".

#### Why a station can read dashed rather than amber

Some protections are **opt-in**, and a station that guards one asks *"is anything owed?"*
before it asks for your attention:

- **SIGNED** reads dashed until this deployment has signed *something*. Once you sign one
  artefact, signing is a practice here, and every unsigned agent becomes amber — outstanding.
- **BOUNDED** reads dashed when nothing can drive **that agent** unattended — no coordinator
  lists it as an expert, no scheduled task targets it, no flow node runs it. Authority
  scoping only protects unattended runs, so an agent nothing drives that way has nothing to
  bound. The question is asked **per agent, not per estate**: owning a coordinator does not
  put every agent at risk, only the experts that coordinator actually calls. Add an unscoped
  agent to a coordinator, a schedule or a flow and it turns amber.

Dashed means *"nothing is owed"* — never *"we could not tell"*. Where the answer is
uncertain the station reads **amber**, because an unnecessary amber is a smaller error than
a dash hiding real exposure.

#### Where green ends and amber begins — configurable

A rate's colour comes from two floors: **green at or above 0.80**, **amber at or above 0.50**,
red below. They are set once under **Administration → App Config → Trust → Quality bands**
(*where green ends and amber begins*), and every surface that colours a pass rate reads them — the
roster bars, the Trust Circle's execution attribute, and the cross-agent KPI charts.

Raising the green floor turns currently-green agents amber immediately. It changes how
quality is *reported*, never how a turn is scored or gated.

**Only the numbers are configurable.** Whether a gate *acts* (*enforce* — with or without
keeping originals for review — can withhold an answer; *watch* — with or without disclosure —
only records) and worst-of aggregation across agents are
properties of the system, not judgements about where a bar sits — a setting that let an
observe-only gate read green, or turned worst-of into best-of, would manufacture exactly the
false green this whole grammar exists to prevent.

#### Reading `n=` — why some dimensions have far fewer verdicts

A dimension's rate is only as strong as the number of turns it was **decided on**, and that
number differs per dimension. The `n=` badge (on the roster bars and the explainer chips) is
that denominator:

| Badge | Meaning |
|---|---|
| *(none)* | decided on **every** scored turn — the rate is the whole window |
| `n=11` **grey** | decided on fewer turns than were scored (e.g. the dimension didn't apply to some) |
| `0/1` (a **fraction**, not a %) | **≤2 verdicts** — a single verdict drives the whole number, so it is shown as a raw fraction and the bar is dimmed. At this sample size a *percentage* would be false precision: `0%` and `100%` are the only values one verdict can produce, and both read as confident rates. `0/1` cannot mislead |

Colour is reserved for the **rate's band** and nothing else — a thin sample is marked by
*shape* (the fraction form, the dimmed bar, a dashed chip edge), never by borrowing amber,
which would put a perfectly healthy dimension under a warning tint. Every visual channel on
a bar row is in that legend — colour (the band), the `n=` badge (its denominator), the
fraction + dimming (thin evidence), the dashed *judge* marker (a model opined rather than a
rule measuring). A channel that carries meaning but is not in the
legend is a signal nobody can read; the grammar gate now asserts this.

**Why judge dimensions have tiny denominators.** Deterministic scoring is free and runs on
every turn; the LLM-judge dimensions cost real tokens, so they are **sampled** (5% of turns by
default, plus any anomalous turn). So `Groundedness (RAG) 0% n=1` means *"the one turn a judge
looked at did not pass"* — **not** "this agent is ungrounded". Read an amber `n=` as *"worth a
look at that one turn"*, never as a rate. Raise the judge sample rate under **Administration →
App Config → Trust → Scoring** (*Judge sample rate*) if you want a denominator you can trust.

**Seven stations read no checks at all** and say so in their explainer — they report
what their mechanisms did, which is a different thing and the point of the split. Only
JUDGED reads checks. Where a station's machinery did nothing this turn, the side renders
a dash: honest absence, never a zero.

**When two checks of the same thing disagree.** A few dimensions measure one property
twice — `Grounding Overlap` counts literal word overlap, `Groundedness` reads meaning and
refines it. Where both decided a turn, what you see is the *pair's* standing, not the worse
of the two: if the coarse check flagged an answer and the judge cleared it, the station reads
**worth a look** and tells you why — *"N turns flagged by the coarse lexical check and cleared
by the judge that refines it"*. That usually means the answer paraphrases heavily (benign) or
the judge was lenient (worth a glance). The numbers themselves are never rewritten — the chip
still shows what was measured; only what it contributes to the station changes. A failure both
checks agree on is never softened.

**Reading REVIEWED on a quiet turn.** A gate in `review` mode that ran and found nothing
reads *"armed — nothing on this answer needed a human"*, not "not turned on": the protection
is in force, it simply had nothing to park. And when a request is **refused at the door** —
the inbound guard blocking an injection before any model sees it — every layer downstream
reads a dash with the cause named (*"the request was refused before the agent ran"*), because
there was no retrieval to screen, no answer to ground and nothing to seal. That is the best
outcome the guard can produce, and it must not render as a screen full of gaps.

**A `—` means that side genuinely does not exist**, not that it is broken or empty:

| Reading | Meaning |
|---|---|
| `— · unsigned` | SIGNED has no setting to configure — signing is an act you perform |
| `unscoped · —` | BOUNDED has no execution data — autonomous denials are not aggregated yet |

When one agent is in focus, SIGNED shows the **state word** — `unsigned`, `not attested here`,
`attested`, `changed` — not a ratio. Focused on a single agent, `0/1` is the scope size wearing a
ratio's clothes: it reads identically for an agent that was never signed and for one whose only
outstanding act is the local attestation. Estate scope keeps the ratio, where it is real.

Every station's two attributes describe the **same subject**. Focused on one agent, REVIEWED shows
*that agent's* queue; the estate figure is still named in the hover so a focused zero can never be
misread as *"the inbox is empty."*

#### The colour tells you where to look, not whether you passed

This is the most important thing to internalise. **Station colour is a call to attention, not a
verdict.** It answers *"does this need me, and how urgently?"* — because with eight stations, the
only decision you actually make is where to look next.

| Colour | Word | What to do |
|---|---|---|
| Green | **quiet** | Nothing needs you. Hover to confirm if you want. |
| Amber | **worth a look** | One of the two attributes is short of its intent. |
| Red | **needs you now** | A real discrepancy was detected, or a rate is failing. |
| Hollow green | **watching** | A posture you chose: the gate is on, records what it finds, and stops nothing. Quiet — nothing needs you — but hollow, so you can see it is not protecting; the value carries how many agents actually stop drift. |
| Dashed | **not turned on** | A protection that exists but is off, and **nothing is owed**. Hover to see what it would do. |

Dashed always means *nothing is waiting on you* — never *work you haven't done*. SIGNED is the
case where that matters: while nothing in your deployment has ever been signed, SIGNED reads
dashed, because signing is opt-in and you have not opted in. **The moment you sign your first
agent, every unsigned agent turns amber** — signing is now a practice here, so the rest are
genuinely outstanding, and the hover says so: *"sign-off is outstanding — this deployment signs
agents and this one is not signed yet."* The station becomes a to-do list exactly when it starts
to mean something.

The colour is the **worse of the two attributes**, and the hover always tells you *which one*
caused it — look for *"needs you now **because of execution**"*. That distinction is the whole
point: a design problem and an operating problem need opposite responses.

Two deliberate behaviours worth knowing:

- **Green does not mean "nothing to see."** Hover a quiet station and you still get its full
  reading. Green means *nothing is waiting on you*.
- **Watching is a posture, not a problem.** *Watch* is a legitimate posture you chose, so a fresh
  install is not in alarm — it reads *watching*: quiet green because nothing needs you, hollow
  because nothing is protected. The summary line names which stations are watching, and the
  value says how many agents actually stop drift. It is never tallied as a finding.

#### SIGNED also changes shape, because two ambers can mean different work

Colour answers *how urgently*, so two states that need you equally are the same amber — correct,
but it left two genuinely different jobs looking identical. SIGNED therefore carries a **second
signal in its shape**: the same seal you already see on profile cards and marketplace listings.

| Shape | State | What is owed |
|---|---|---|
| Shield with a check | attested | nothing |
| Shield, lower half dashed | not attested here | **attest this deployment** — it is publisher-signed and intact; what is missing is this instance recording *its* model, tools and connectors |
| Warning triangle | changed | **re-sign** — its make-up moved after sign-off |
| Dashed circle | unsigned | **sign it** (or nothing, if signing is not in use here) |

The button follows the shape. An agent that is publisher-signed but unattested is offered
**"Attest this deployment"** — not "Sign this agent", which would be an act that cannot help.

Only SIGNED does this. The other seven stations have no such vocabulary to borrow, and inventing
one per station is exactly the drift this grammar exists to prevent.

#### SIGNED also carries the prompt-cache reading (the *Attested Cache*)

With the deployment's prompt-caching switch on (`TDA_PROMPT_CACHING=1`), every call declares its
**stable prefix** — the system prompt and the conversation so far — and one cache plan decides
per provider how to keep it warm (a breakpoint at the prefix's end on Claude and Nova, an affinity
key on OpenAI, Azure and xAI, nothing where marking would route away from a caching upstream);
the turn records what the provider served from cache
([PROMPT_CACHE_ARCHITECTURE.md](../Architecture/PROMPT_CACHE_ARCHITECTURE.md)). That reading lives under SIGNED, not TRUSTED,
because of what a miss *means*: the cached prefix is the same bytes the signature covers, so a
call whose prefix did not change, inside the cache lifetime, above the provider's minimum, on a
provider that caches — and that still came back uncached — says **something in the prefix
moved** (a tool schema, a rebuilt graph, a system prompt that carries per-question content). Its
three readings:

| Reading | Word | Meaning |
|---|---|---|
| served from cache | quiet | *20,596 of 23,957 prompt tokens served from cache* — the share on the step card as *Cached* |
| an unchanged prefix that missed | worth a look | *N call(s) missed on a prefix that did not change* — the cause when known |
| nothing cached yet / caching off / a provider without a cache | — (dashed) | never a pass; a warm cache is invisible, an absent one is honest |

It is a cost reading with an integrity edge, never a gate: it withholds nothing and moves no
badge (the per-profile `cacheConfig` is deliberately unsigned). The measurement twin is the
informational dimension `cache_efficiency` at JUDGED's residue. Two facts worth knowing when the
share reads lower than expected: Anthropic's minimum cacheable prefix is **per model** (Haiku 4.5
needs 4,096 tokens — a shorter system prompt is never cached however it is marked), and Gemini
caches implicitly whether or not the switch is on, so its cached share appears either way.

#### SIGNED also carries the recovery reading (the *Resilience Envelope*)

With the deployment's resilience switch on, what Uderia did when the model could not
answer as configured is a recorded, sealed act — never a silent retry and never a canned
"technical error". Four acts, one record: a **retry** on a transient provider error (rate
limit, overload, timeout, connection — with the provider's own `retry-after` honoured), a
**continuation** of an answer the output limit cut off (asked to continue exactly where it
stopped, at most twice), a **fallback** to the agent's signed fallback model when the primary
is exhausted or its circuit breaker is open, and an honest answer when the provider **refused**
on content grounds. The reading lives under SIGNED for the one fact SIGNED owns: **which model
answered, and is it inside the signature**. The signed deployment names two models — the primary
and the fallback — so either one serving is inside it; a recovery is *evidence for* SIGNED (the
Guide's verb), never a control, and retries and continuations are named in words without
colouring anything. Its readings:

| Reading | Word | Meaning |
|---|---|---|
| nothing recovered | — (dashed) | the configured model served on the first attempt (or the envelope is off here) — the ordinary turn |
| retried / continued | quiet | *2 retries — the configured model served the answer* |
| served by the signed fallback | quiet | *answered by the signed fallback Google/gemini-3.1-flash-lite instead of MLX/dead-model — inside the signature; cost billed at the model that served* |
| served by a model outside the signature | worth a look | *answered by X, which is not the agent's signed fallback (Y) — outside the signature* — the only SIGNED finding a recovery can produce |
| the provider refused / no model answered | — (dashed) | *the provider refused the request (safety) — no model answered, so nothing bears on the signature; an honest answer naming the refusal was delivered*. A turn the provider failed outright reads *no answer* here and is read at JUDGED, where it fails Clean Termination |

Until Oct 2026 a fallback and a refusal both read *worth a look* here, turning SIGNED amber on
turns where nothing about the signature had changed.

Three surfaces say the same thing: the answer wears a small **served by <model>** chip only when
a fallback served (the exception, never the rule — hover it for the fallback and the billing),
each act is its own **Live Status step** (*Provider Retry · Answer Continued · Served by
Fallback · Provider Refusal*, live and on a plan reload), and the chat header shows a
**provider-health strip** only while a provider's breaker is open or recovering — naming the
provider, the reason, that turns route to the fallback, and the seconds until the next probe;
it disappears on recovery. Configure the fallback per agent in the profile editor (Advanced
Configuration → Fallback LLM); with none set, an exhausted provider is an honest error rather
than an improvised model.

#### GROUNDED also carries the number-lineage reading (*Compute with Lineage*)

With the deployment's compute-lineage switch on, a data-running agent can hand its arithmetic to
code: `TDA_Compute` sums, averages, ranks, pivots, diffs, joins or filters the rows a query
returned this turn, in-process, and every figure it produces carries a **receipt** — the
operation, the statement(s) it read, the rows it used — sealed into the turn's provenance chain
beside the plan and the tool calls. At the end of the turn every number in the delivered answer
is classified once: **receipted** (it equals a receipt's value), **quoted** (it equals a cell of a
result row, or the number of rows a statement returned), **a literal** (the agent wrote it INTO
an executed statement — a filter such as `offence_code = '120'`, an `IN`/`BETWEEN` member, a
`TOP`/`LIMIT` bound: the turn's own input, never its arithmetic), **declared** (a code the
knowledge graph declares for a coded column — `'120' = Robbery` — even when the statement
filtered on the label), **derived** (it equals the sum or the average of a numeric column of a
complete result set — *the sum of robbery_count over the 12 rows of statement 1*: arithmetic,
but arithmetic the data vouches for), or **unreceipted** (nothing in the turn vouches for it — the
agent's own arithmetic). Literal and declared were added on 2026-09-22 after a production turn read
*"179,193 robberies (offence code '120')"* and the note called `120` the agent's own arithmetic;
**derived** on 2026-09-24, after a twelve-row monthly trend was headlined *"180,193 robberies"* where
the rows sum to 179,193 — and the correct total read exactly like the wrong one. A result set that
was truncated is never summed. An unreceipted figure that sits **close to** (within 5% of), but is
not, a column's sum or average is also named as a near miss: it stays unreceipted, and the note says
which value the rows give (*180,193 is close to, but is not, the sum of robbery_count over the 12
rows of statement 1, which is 179,193*) — the number itself is never changed. Its readings:

| Reading | Word | Meaning |
|---|---|---|
| every figure traces to a receipt or a row | quiet | *3 figures · 2 receipted · 1 quoted* — the mechanism worked, nothing to do |
| unreceipted figures | worth a look | *2 figure(s) match no compute receipt and no result row* — the figures named; under honesty mode `annotate`/`enforce` the answer carries an append-only note naming them |
| the turn ran no data statement and computed nothing | — (dashed) | Ideate/Focus turns, or a turn that asked nothing numeric — an absence, never a pass |

In the answer itself the marks are the reading you can act on: a receipted figure wears a faint
dotted underline and a hover that says *SUM(miles) over 149 rows · statement 1*; a quoted one says
*row 3 of statement 2*; click either (or press Enter on it — the figures are keyboard-reachable)
and a dialog shows the receipt and the rows it was computed over; a literal opens the statement
it was a filter in (*used as a filter in statement 1 — a declared code of v_Offence.offence_code
(Robbery)*) — the label follows the COLUMN the literal is compared against, never the value
alone (`'120'` is Robbery on `offence_code` and Handgun on `armed_with_code`; a literal whose
column declares no such code carries no label); a declared code names its column and label on
hover and opens nothing — its lineage is the ontology, and a code the graph declares on several
columns says so instead of picking one. An unreceipted figure is marked
without colour and without a claim — it may still be right; the note says only that nothing
vouches for it. The disclosure is never a rewrite: a figure is never changed, only traced. The
measurement twin is `numeric_fidelity`, which since S3 counts a receipted or quoted-by-lineage
figure as grounded — the measure moved from *flagging* the agent's arithmetic to *verifying* it.

#### BOUNDED also carries the turn-budget reading (the *Visible Budget*)

With the deployment's turn-budget switch on, an agent may declare how much **one turn** may
spend — steps (data-tool calls), tokens, seconds — and the model **sees what is left** at every
tool boundary. When a unit runs out the turn ends **gracefully by rule**: the next tool call is
refused naming what is left, and the answer is written from what was collected and says what
remains — a recorded finish, never a silent kill. Uderia's hard stops (the per-run cost
cap, the input-token cap) stay the last line one unit above the budget; a turn they stop is
recorded as **killed**, never as graceful. The reading lives under BOUNDED because a budget is a
boundary on what the agent may do; it owns the informational dimension *Budget Adherence*
(1.0 within · 0.5 graceful · 0.0 killed). Its readings:

| Reading | Word | Meaning |
|---|---|---|
| no budget | — (dashed) | no budget declared on this agent, or the switch is off here |
| within budget | quiet | *within budget (2 of 6 steps · ~9k of ~40k tokens)* |
| exhausted, graceful | worth a look | *the steps budget ran out — finished gracefully, 3 further tool calls refused, the answer says what is left* |
| killed | needs you now | *the tokens budget ran out and the turn was KILLED by a hard stop (cost_cap) — the answer does not say what is left* |

Three surfaces say the same thing: the **Turn budget bar** in the Live Status header (one
segment per unit, filling as the turn spends — amber from 80%, red only once a unit is used up,
each segment reads its state in words to a screen reader; the ending line says how the turn
ended), the quiet **finish line** under the answer (*finished within budget — …* / *budget
exhausted (steps) — stopped gracefully; …*), and the **Continue where it stopped** chip in
the next-step dock on a budget-stopped turn — one click resumes exactly the remaining work as a
new turn, with its own budget. On the planning lane a skipped phase is its own Live Status step
(*Budget Exhausted — Phase Skipped*, naming the phases and their goals). Configure the budget per
agent in the profile editor (Trust Configuration → BOUNDED → Turn Budget); every unit blank = no
budget, and the agent behaves exactly as today.

#### BOUNDED also carries the tool-surface reading (the *Governed Tool Surface*)

With an agent's *Tools in context* set to **Relevant per turn** (there is no deployment switch;
new and bootstrap Optimize agents start there), the model is shown **only the tools that could
answer this turn** — the discovery and execution backbone, every `TDA_*` tool, the tools the
question addresses by its words (identifiers, the ontology's entities, a named category) and the
few nearest to it in meaning (at most five, each with its score) — with their full descriptions,
and the rest are **held**: never lost, brought in by describing the capability (`TDA_FindTools`;
an agent without the finder holds nothing). A tool
Agent Authority denies is never offered and never findable — and a denied tool the model asked
for is a **named finding**. The reading lives under BOUNDED because the surface is a boundary on
what the agent is shown; it owns no scored dimension (what the model saw is not a quality of the
answer it gave). Its readings:

| Reading | Word | Meaning |
|---|---|---|
| not governed | — (dashed) | every enabled tool was offered — the agent is on *All enabled tools* |
| governed | quiet | *offered 19 of 38 tools · 1 found by the model · 1 denied by authority* |
| asked for a denied tool | worth a look | *the model asked for 1 tool its authority denies (base_writeQuery) — never offered, never found; offered 19 of 38 tools* |

The **Tool Surface** step in Live Status (live and on a plan reload) lists each set — offered,
found by the model, held, denied — as chips, each carrying the reason it is where it is on
hover (*backbone — discovery and execution* · *addresses the question: disk, space* · *held —
findable with TDA_FindTools* · the authority's own denial reason). Configure it per agent in the
profile editor under **Data sources → Tools** (*Tools in context*: All enabled tools / Relevant
per turn — one setting across every server the agent reads); it changes what is shown, never what
is permitted, so it is not part of the agent's signature.

#### GROUNDED also carries the outcome-loop reading (the *Outcome Loop*)

With the deployment's outcome-loop switch on and an agent's *Revise before delivery* enabled,
a draft the grounding check flags with a **fixable finding** — an unsupported claim, a reference
outside the declared scope, a figure that matches no result — is handed back to the model
**once**, with the finding named and its exact spans quoted, and the same check grades the
revision. A revision that clears is delivered clean; one that does not delivers the **honest
annotated first draft** — never the failed revision, never a second silent try. Both drafts are
kept and sealed. The reading lives under GROUNDED because it acts on the grounding check's own
finding; it owns no scored dimension (the gate already measures what it grades). Its readings:

| Reading | Word | Meaning |
|---|---|---|
| no revision | — (dashed) | the check found nothing fixable, or the loop is off on this agent / switch off here |
| cleared | quiet · acted | *revised once — ungrounded number removed; the revision passed the gate* |
| not cleared | worth a look · acted | *revised once — ungrounded number not cleared; the honest annotated first draft was delivered — never a second silent try* |

The **Outcome Revision** step in Live Status (live and on a plan reload) shows what was found
in the draft, what remained after the revision, and the revision's own token cost. Under the
answer, *Why trust this answer?* gains a **Drafts** disclosure — the first draft and each
revision as tabs, the graded spans marked in place — so the reader can see exactly what changed
and why. Configure it per agent in the profile editor under Trust Configuration → GROUNDED
(*Revise before delivery*, the bound of one or two revisions, which finding kinds may be
revised); it is posture and part of the agent's signature — a pack that turns it off or lowers
the bound is a posture downgrade.

#### Comparing answers by their verdicts (the *Experiment* card)

With the deployment's turn-lineage switch on, every answer offers **Regenerate ▾** (the same
agent, or any other activated agent — a regenerate "with another model" is a regenerate under
another agent) and every question offers **Edit**. A regenerate is a **scored sibling**: the
question is asked again, the new answer is scored by the same evaluation layer as any turn, and
the two (or three, or four) answers sit under the question in the **Experiment** card — one row
per answer with its agent and model, its verdict strip (the Scorecard's bands, worst first,
never re-derived), the words that count what needs you, its cost and latency. **Adopt** makes an
answer the one the conversation continues from; the newest regenerate is adopted on arrival, and
adopting is reversible — the record keeps every sibling, only the model's context changes.
**Show** opens a two-column compare (the adopted answer beside the one you chose); `r` on a
focused answer regenerates it; the arrow keys move between answers.

**Edit** resends a question as a **branch**: the edited text becomes the head of a new branch,
the turns from that question on stay in the record but leave the view (and the model's context);
the edited question shows *edited question · this branch · Show the original*, and back on the
original the question shows *1 other branch · Show the edited version*. Switching never rewrites
history — it moves the view.

The card compares; it never decides. What it shows are the same verdicts the Scorecard and the
Trust lens show for those turns, so preferring one answer over another is a judgement about
quality, not vibes.

#### An attachment is a source (the *Attachments* row)

With the deployment's attachment-sources switch on, dropping a screenshot of a table, a PDF, a
CSV or a text file into the composer makes Uderia **read it itself** before the question
is asked: the chip says `read · OCR · 1 table · 3 rows` (or `read · PDF tables · …`, `read ·
CSV · …`) and an image shows as its own thumbnail, with alt text taken from what was read. After
the turn the chip on your question reads `used as source · …`: the grounding check held the
answer to those rows, a figure quoted from them is dotted-underlined like any quoted figure and
opens on the **file and the cited region**, and the compute tool could operate on the rows (on an
agent with the compute component, "the total" is computed with a receipt, not estimated). *Why
trust this answer?* gains an **Attachments** row — each source named with its method and
verification, an image as a crop of the region the answer drew on.

Three words, one rule. **verified** — Uderia read it (OCR, PDF, CSV, text); the gate holds
the answer to it. **transcribed · unverified** — nothing could read the image and the agent's
*Transcribe unreadable images with the model* option is on: the model's own reading is recorded
so the answer can name where it came from, but it is **never evidence** — a transcription the
model wrote cannot vouch for the model's answer (on a chart with no numbers the model will invent
some; the GROUNDED station's *Attachment sources* reading warns that the answer may rest on it).
**not readable** — no extractor could read the file here (no OCR engine installed, a scanned PDF
with no engine, an image with no text); the model saw it natively and nothing in it is vouched
for. The Live Status step *Attachments Read as Sources* lists the three groups.

#### Steer the agent while it works; a running turn never dies with the tab

With the deployment's steer-and-reattach switch on, two things change about a running answer.

**You can talk to it.** While the answer streams the composer stays live and its placeholder
reads *Steer the agent — considered at its next step*. Type a short instruction — *Answer in
German*, *only the top three*, *use the Orders table, not Sales* — and press Enter: a pin appears
under your question (*sent · considered at the next step*) and, the moment the agent reaches its
next step, changes to *considered with the result of base_readQuery* (or *… in the final
report*). The Stop button still stops. The agent is told, in so many words, that the user changed
the instructions while it was working and that the steer wins where it conflicts with the
original question. Whether it then complies is the model's doing — the Live Status step *Steer
Considered* says WHERE the steer reached it; the answer shows whether it was followed. A steer
sent to a turn that has no step left (a pure conversation with no tool and no report step) is
**carried to your next question** and delivered with it — the step then reads *Steer Carried to
the Next Turn*. Nothing you send is silently dropped: every steer is on the turn's record.

**It survives you.** Close the lid, reload the tab, lose the network for a minute: the turn keeps
running on the server, and when the page is back — the same tab or a fresh one — the session
shows the turn still running, the trace filled in from where you last saw it, and a quiet
*reconnected — caught up* note. You can steer it from the fresh tab too. What you get back is
exactly the frames you missed, in order; if the log has moved on further than it keeps (it keeps
the last four thousand frames of a turn, and a finished turn for fifteen minutes), the runtime
says so and re-reads the session rather than pretending to replay.

#### The verdict answers first

Before any station is hovered, the side panel reads like the Anatomy pane. An eyebrow names the
subject (*@PXPROD · … · right now*), the headline names the loop in the stations' own order —
*Born · signed · grounded · guarded · judged — and the loop closes.* — and then **one verdict
sentence** led by its colour word, built from the eight readings:

```
2 stations need you: Grounding gate: 1 open inbox item. 14 of 20 turns graded clean.
Signed and attested. Watches drift and unsafe content, stops neither. Conduct 99%.
290 receipts sealed.
```

The lead (*N stations need you* · *N worth a look* · *Nothing needs you right now.*) counts the
stations; the sentences after it are the three readings Uderia keeps — **needs you**
(findings only, worst first), **posture** (signing, the two gates as one clause, the scope) and
**record** (conduct, graded turns, sealed receipts). Every figure is a station's own value; the
sentences use Uderia's names (the grounding gate, the guardrails) and never the stored
posture labels. The same readings are the tooltip on every roster mini-ring, so the two can never
disagree. The full key — what each colour, shape and mark means — folds behind *How to read this
ring · the full key*.

#### Each station leads with its question

A station's panel opens with the question it answers — *Does it stay on its corpus?* · *Is a
person deciding?* · *How did its answers grade?* — then its character line and its reading. The
eyebrow also carries the station's **lane**: *in flight · can change the answer* for the gates,
*after the turn · grades only* for JUDGED, *an act*, *a decision*, *the record*, *the aggregate*
for the rest. The lane is why a red check never colours a gate station (next section).

#### The station groups save you the scan

Beneath the verdict, three rows name the stations — never a second copy of a figure:
**attention** (*1 needs you now — JUDGED · 3 worth a look — GROUNDED, BOUNDED, TRUSTED*),
**quiet** (the green stations, then *2 watching — GUARDED, REVIEWED (recording, not stopping)*)
and **absent** (the dashed ones). Read that first. You should never have to scan eight glyphs to
find the two that matter.

#### The hover is the same four slots, every station

```
CONFIGURATION   enforce + keep for review
EXECUTION       —   2 withheld · 16 checks measure this guarantee — counted at JUDGED
────────────────────────────────────────────────
WHY             quiet   enforce can redact or withhold an answer;
                        watch only records it, with or without disclosure
EVIDENCE        2 of the last 14 scored turns tripped a guardrail
```

The configuration slot speaks the profile editor's words — *watch*, *watch + disclose*,
*enforce*, *enforce + keep for review* — the stored tokens never reach a reading surface.

The Execution row carries a **unit-bearing** figure — `N of M turns`, never a bare `4/4`
"dimensions" (the I4 fix: "dimensions" reads as the 14 scored dimensions when it actually counts
turns; the code now emits *"N of M turns clean on safety checks"* and the gate asserts it).

The top pair is **what it says** — the two attributes, with the value first because that is
what you came to read. The pair below the line is **why it says that**: which rule produced the
colour, and which concrete instances are behind it. When the colour was pulled by one attribute
rather than the other, the `Why` row names it — `worth a look (execution)` means the
configuration is fine and the measurement isn't; `watching (configuration)` means the colour is
the posture you chose.

**Always read Evidence.** It is mandatory precisely so that a comfortable-looking count can never
hide a finding. The clearest example: REVIEWED can read `0 open` (nothing is waiting on *your*
decision) while Evidence names a danger-severity observation sitting in the Assurance Inbox —
because derived observations are recomputed from the sealed trace and carry no disposition to
dispose of. The count is honest; Evidence is where the finding lives.

#### Clicking a station

A single click **pins** the station: the reading stays on screen and the station's *actions*
appear beside it — because acting on a reading must not cost you the reading. Which actions
appear is derived from the state, so SIGNED offers *Sign this agent* when sign-off is owed,
*Attest this deployment* when only the local half is missing, and *Verify offline* when it is
already signed (§3.5). Blocking ingredients get their own route, so a refusal is never a dead
end.

Navigation stays available alongside them, and the **driver decides which route leads**: a
station amber because its *execution* fell short opens this agent's runs, while one amber
because of its *configuration* opens the posture matrix. The other route stays beneath it —
reordering routes is honest, removing one is not.

Dismiss a pinned station five ways: `Esc`, clicking it again, clicking outside it, the ✕, or
pinning another. It is never a trap.

#### Estate scope

With no agent selected, each station aggregates across every agent — **worst-of**, so one laggard
keeps the station amber. The value then reads as a ratio (`2/10 · 4/6`), and Evidence names the
laggards so you know who to fix. A green estate station means *every* agent is clean, not that at
least one is.

> Contract and conventions behind these readings:
> [TRUST_STATUS_COMMUNICATION.md](../Architecture/TRUST_STATUS_COMMUNICATION.md)

### 3.0a Reading a station's modules — what actually assessed this

A station panel names the **mechanisms** that ran ("1 withheld", "nothing acted"). That tells you
what happened, not *why* — which module produced the finding the mechanism acted on, or what it
measured. Each mechanism row expands to exactly that.

| what you see | what it means |
|---|---|
| `3 modules, all quiet` | three modules assessed this turn; none found anything |
| `1 of 3 found something` | one produced a finding — the row it belongs to carries a solid accent edge |
| `1 module, all abstained` · `3 modules: 2 quiet, 1 abstained` | a module is bound and ran, but declined to grade — it had no input this turn (dashed, dimmed; the row says what it needed) |
| `nothing bound` | **no module is assessing this property at all** — never hidden, because "nothing is
watching this" is the reading that must not be invisible |

Each row is `property · module · what it found`, with the module's own numbers beneath it
(`supported ratio 100%`, `1 tool call`). A small arrow-into-a-bar mark beside the module name means
**the gate consulted this module during the turn**, so its reading is part of the decision above;
no mark means it was graded after. On the grounding panel the decision itself names the finding in
its family's words (*2 of 9 statements unmatched*, *out of scope: sales_db*), so you can see what
decided the gate before opening a row. The **property** is the stable name on the left; the
**module** is whatever is bound to it today — so after a remap the row shows the new module rather
than a fossil of the old one's output. Clicking a row opens that property's entry in the Assurance
Guide.

An abstention is not a pass. A module that could not run says so in words ("needs `scope_databases`,
which this turn did not provide") and is drawn dashed — a turn with nothing to assess is a turn
nobody checked, which is a different fact from a clean one.

**How a module finding reaches the station colour.** Through the mechanism, never around it:

> assessor **finding** → mechanism **reading** → station **colour** (worst-of its mechanisms)

So a station goes amber because a *mechanism* read amber, and a mechanism reads amber because it
folded a finding. The indirection is deliberate, not plumbing: the mechanism reading is the gate's
own **decision**, which may legitimately differ from a raw finding. The clearest case is the grain
heuristic — a module fires, the `grain_faithfulness` judge withdraws the verdict, and the reading is
correctly a pass. If module findings coloured the ring directly, a withdrawn verdict would still
light it up.

The failure mode to know about, because it has happened: a gate that assesses several
**branches** (the honesty gate runs three — absence claims, retrieval sufficiency, and the
library-provenance branch, which itself reports two kinds: a citation the turn never opened, and an
identifier the library does not contain)
must fold *all* of them into its one row. Reading only one branch's stamp produced a row saying
*"no unsupported absence claim"* with a green dot, directly above its own module list saying
*"1 of 3 found something"* — one mechanism, two answers, and a quiet station above both. A
multi-branch mechanism reads **worst-of its branches and names which one drove it**, and still names
the quiet ones, so you can tell *checked and clean* from *not checked at all*.

### 3.0b Reading the Assurance Guide's PROPERTIES

The Guide's other entries answer *what does this measure* and *what does this control do*. The
**Properties** shelf answers the question underneath both: **what is actually being assessed.**

A property is the unit both lanes are about — a guarantee promises it, a control upholds it, a
measurement grades it. Each entry states:

- **assessed by** — the module bound to each lane: *during the turn* (which can act on the answer)
  and *after the turn* (which reports only). Where one module serves both, the entry says so — the
  two can then never disagree about what was found, which is the whole point of the arrangement.
- **graded by** — the scored dimension that measures this property afterwards, as a link. Every
  property that has one carries the route; eleven of the twelve do.
- **assessed for** — the mechanisms that act on what this property's module finds.
- Where a lane has **nothing bound**, the entry says why. `Request integrity` is assessed in flight
  and deliberately never scored — whether a blocked request was truly an attack is not decidable
  from the turn, so its detector is measured against a curated corpus instead (the resilience
  self-test). `Tool authority` is the mirror case: graded after the turn, never assessed in
  flight, because authority is enforced per *call* at the tool chokepoints before each call runs.
  Both are finished designs, not gaps, and the entry distinguishes the two.

**This is the surface a remapping UX will operate on.** A property is permanent; which module
assesses it is a binding, and bindings are data.

### 3.0c Reading the Agent Roster — three readings, not one

The roster's headline (`44% live pass rate · 18 turns · 8 failed`) answers **one** of the
questions you can ask about an agent, and the words invite the others.

| question | answered by | where it lives today |
|---|---|---|
| **How well do its answers grade?** | measurements, after delivery | the live pass rate |
| **How often did a control have to act?** | mechanisms, in-path | the intervention reading on the card · the acted mark and `Acted` filter on each run |
| **What did the people using it think?** | the users' own votes | `user feedback — N up · M down · K disagree with the scorecard` on the card · an `upvoted`/`downvoted` chip on each run |

**The third one is a preference, not a measurement**, and the surface keeps it apart on
purpose: a vote never moves the pass rate, a band, or a station colour (Rule 3bb.4). The
line is **absent entirely when nobody has voted** — that is an honest absence, not a
zero. In system scope you see no vote reading at all rather than someone else's zero.

**Read the disagreement count first.** It counts turns where the scorers and the person
point in opposite directions — passed but downvoted, or failed but upvoted. That is the
only signal in Uderia where a human tells you the **quality bands are wrong for
this deployment**; everything else is Uderia grading itself. A rising
disagreement count is your cue to revisit Administration → App Config → Trust → Quality bands, not to
discipline the agent. See §3.0d for what a vote actually changes.

**The pass rate is the first one.** A turn counts as failed when a check a **rule
measured** landed in the danger band — **unless** the judge that refines it corrected that
(the row then carries a `judge-cleared` mark saying why it is not a failure) — **or** a
gate withheld the answer, so a withheld turn can never read clean. Amber flags but never
convicts, and a judge alone never convicts: a model opined, no rule measured it.

**Why it is not mechanism-driven.** It would flatter the agent. On a real 18-turn sample,
16 turns had no mechanism act at all: an intervention-driven rate would have read **89%**
on an agent whose grounding measurements were the worst thing about it.

**The two readings are close to independent.** The same sample:

```
a mechanism acted  + measurements flagged :  0
a mechanism acted  + measurements clean   :  2      the system working
nothing acted      + measurements flagged :  5      <- look here first
nothing acted      + measurements clean   : 11
```

The third row is the one to act on: those answers were **delivered**, and the
measurements say they were poorly grounded. No control caught them. That is the case for
moving a gate from `observe` to `enforce` — and it is invisible in an intervention count,
which reads `0` there.

**The intervention reading.** Beside the rate sits the mechanism axis — *"2 of 18
answers altered or withheld · 2 parked for review"*. It answers what the rate cannot, and
an agent nothing acted on says so plainly. A trailing *"N turns where a guard did not
run"* means a control failed open on those turns: the honest reading is **unknown**, not
pass, which is why it is shown here and not folded into the quality figure.

**Reading a run row.** Each row in the Runs Ledger carries a strip of marks — one per
scored dimension, in station order, the **same checks in the same columns on every row**
(a check that did not run on a turn appears dashed in its place), so a column can be read
down the ledger. Three channels, none of them colour alone: **fill** is
what the check found (solid passed · half worth a look · outlined failed · dashed did not
run), **shape** is who measured it (square = a rule · diamond = a model), **size** is what the
ruling could do (a larger diamond = a gate consulted that judge during the turn, so its
verdict could withhold the answer; the plain diamond = sampled after it), and a **centre
dot** means the rule measured a failure and the judge that refines it cleared the same
answer. Hovering any mark says what that check *found*, not just what it is; the **Key**
above the ledger draws all three.

Two chips appear only when they have something to say. **`withheld · parked`**, with a dot
and — when something is owed — the accent colour, means a mechanism acted: what was delivered is not what the model wrote. The **`Acted`** filter isolates
those turns, because *which turns were altered* is a different question from *which
scored badly*. **`1 judge flagged this`** appears when the scoring
panel found a problem, and **`gate asked a judge · cleared`** when a gate consulted one
mid-turn and did not act on it. A turn where every judge upheld carries no chip: the
diamonds already say a judge ran and agreed.

Two words, two populations: a **judge** is one *check* asked once of the turn; its
**models** are the panel voting inside it. So `1 judge flagged this` counts checks, and
`1/1 models` counts panel members.

**Reading the sparkline.** One bar per turn, oldest → newest, driven by the same value as
the rate — it is the rate's evidence, not a second signal. Pass is a full-height bar, fail
is half-height, so you can read it without relying on colour. There is no amber bar: per
turn the reading is binary, and amber lives on the rate (≥80% green, ≥50% amber, below
red).

**If a historical pass rate looks better than you remember**, it is: turn failure is now
resolved against the current gating policy rather than the value frozen onto the span when
it was scored, so turns convicted by advisory-only checks under a retired rule no longer
count. See the FAQ, §4.9.

### 3.0d What a vote actually changes

Rating an answer in the conversation panel is not a comment box. The rating reaches the
mechanism behind that answer, and **what it does depends on which engine produced it** —
hover either button and the tooltip tells you, for that agent, before you click.

| The agent that answered | Downvote | Upvote |
|---|---|---|
| **Optimize** | retires the plan behind the answer, so it stops guiding similar questions | promotes it, so similar questions reuse it |
| **Focus** | flags the documents that served the answer for review in the repository's **Corpus Health** | records them as good sources for questions like this |
| **Coordinate** | passes your rejection to the expert that produced the answer — where it lands in *that* agent's mechanism | passes your approval the same way |
| **Ideate / conversational** | stops facts the agent auto-learned from that answer being recalled | nothing — an upvote is not a request to remember |
| **Every agent** | recorded beside the quality scores as calibration evidence | the same |

Things worth knowing before you rely on it:

- **A coordinator answer built from more than one expert propagates to nobody.** The
  answer is a synthesis; blaming or crediting every expert would invent a judgement you
  never made. Single-expert answers propagate.
- **It is reversible.** Clearing a vote, or flipping it, undoes what it did — a retired
  plan competes again, paused memories resume.
- **Nothing is deleted.** A downvote demotes, excludes or pauses. Paused memories stay
  visible in the Memory manager and can be re-activated by hand; flagged documents are
  surfaced for you to look at, never auto-quarantined.
- **Your votes are yours.** Nobody else reads them, and they never affect another user's
  agents.
- **It costs nothing by default.** Every effect above is deterministic and spends zero
  tokens. The one exception is opt-in: an admin can enable *judge on downvote*, which
  runs the LLM judges on a rejected turn to explain it (the `FEEDBACK_SIGNALS.judge_on_downvote` app setting). That spends
  tokens, ships **off**, and never overrides an agent whose judges you turned off.

Full mechanism:
[FEEDBACK_SIGNAL_ARCHITECTURE.md](../Architecture/FEEDBACK_SIGNAL_ARCHITECTURE.md).

### 3.1 The operating posture — a maturity ladder

Do **not** switch everything to *enforce* on day one. The stack is designed for a graduated
rollout: **observe → measure → tighten**. Every capability starts safe (baseline), so you climb
the ladder deliberately.

| Stage | Posture | What you do |
|---|---|---|
| **0 · Baseline** | everything off/observe | Fresh install. Nothing changes agent behaviour. |
| **1 · Illuminate** | turn on **eval** + set grounding/guardrails to **observe** | You now *measure* quality, drift, and personal-data exposure without altering a single answer. Watch **Trust** → Agent Roster. |
| **2 · Build the foundation** | build/curate the **ontology**; run its **validity check** | Give grounding something true to check against; fix any validity issues it reports. |
| **3 · Tighten where it matters** | move critical profiles to grounding **enforce** + guardrails **enforce** | Enforce on the profiles that touch sensitive data or answer externally; leave exploratory profiles at observe. |
| **4 · Govern &amp; attest** | **sign** production agents **and attest each deployment**; **release** ontologies/packs via lifecycle; require approval | Make trust visible and versioned. Signing vouches for what travels; attesting vouches for what this instance resolved to — both are needed before a badge goes green (§3.5). |
| **5 · Constrain autonomy** | set **Agent Authority** scopes on autonomous profiles | Scope scheduled tasks / genie experts / flows to a subset of their owner's authority. |

You can stop at any rung; each is stable. Most production deployments live at **stage 3–4**.

### 3.2 The control map — where every switch lives

| Capability | Where you set it | The choices | Recommended default |
|---|---|---|---|
| **Evaluation** (scoring) | Administration → App Config → **Trust** → *Scoring* (+ *Quality bands*) | on/off (*Automatic Agent Scoring*) · *Include LLM Judges* · *Judge sample rate* · the green/amber floors and each deterministic check's pass line | **on**, judging sampled |
| **Grounding — overall** | the same tab → *Enforcement* → *Grounding Enforcement* | on/off | on once the ontology is ready |
| **Grounding — per agent** | Profile editor → **Trust Configuration** → *Grounding* card (two segments: knowledge graph — what it may reference; knowledge repository — what it may claim) | observe / annotate / review / enforce, per segment | observe → enforce for data-facing agents |
| **Guardrails — overall** | the same tab → *Enforcement* → *Guardrails (P2)* | on/off | on |
| **Agent posture — per agent** | Profile editor → **Trust Configuration** → *Agent posture* card | ONE hardening intent (`postureConfig.mode`) every gate inherits unless it overrides: Observe (☑ disclose = `annotate`) / Enforce (☑ keep for review = `review`). The card's live summary names, per gate, whether it *inherits* (and what it resolves to) or *overrides* — and `GET /v1/profiles` carries the same resolution as the computed `posture` field, source named, so no surface ever re-derives it | set the agent default here; override per gate only where a surface genuinely differs |
| **Guardrails — per agent** | Profile editor → **Trust Configuration** → *Guardrails* card | outgoing posture: Inherit / Observe (☑ disclose = `annotate`) / Enforce (☑ keep for review = `review`), for personal data and toxicity; incoming posture likewise | enforce outgoing for anything external-facing |
| **Judges — per agent** | Profile editor → **Trust Configuration** → *Judges* card (the JUDGED station) | inherit / on / off · sample-rate override · file condemnations in the Assurance Inbox | inherit; judge high-stakes agents at 1.0 |
| **Judges — which models sit on the panel** | Anatomy → **LLMs** → edit a configuration → *Judging role* | whether this model may judge at all. **On by default** — switch it off for a model that is slow or unresponsive as a judge; it keeps answering exactly as before. One panel serves both layers, so one switch governs both: the judge a gate escalates to **in flight** (a ruling there can withhold an answer) and the panel that **scores quality after the turn** — plus *Score this turn* and the CLI | leave on; exclude a model that never returns — the panel waits for every member on every judged turn, so one silent model sets the pace for the whole evaluation |
| **Station reporting levels** | Administration → App Config → **Trust** → *What reaches you* (one selector per station) | everything / warning+ / danger only — what each station surfaces in the inbox, badge and ring colour; **danger always surfaces**, and everything is always recorded | warning+ (the default) |
| **Agent Authority** | Profile editor → **Trust Configuration** → *Agent Authority* card | unattended on/off · read-only · tools it can't use · allowed connectors · spending cap · the **Data surface** — a three-option choice: **Disable OBDA-READ** (default — no products reach the planner) · **Surface Full** (ONLY the listed products; raw SQL denied when unattended; *advisory* = its observe-only dev sub-toggle) · **Products + regular paths** (the listed products offered beside the ordinary KG paths — the model decides per question; the list is still the grant: an unlisted product is refused) | unrestricted by default; scope any agent used unattended. An empty product list saves as *Disable*; with the OBDA-READ component off (for the deployment, the person or the agent) the whole surface block is deactivated and no option acts |
| **OBDA planes (OBDA-READ · OBDA-MODEL · OBDA-ACT) — availability** | Administration → **Components** → the plane's pane (deployment, or a per-user override) + the agent's **Capabilities** toggle — each plane is a component; there is **no master switch** | on/off per plane — off = Uderia behaves as if the plane were not installed for that agent (no contracts or action text in any prompt, no `TDA_Action`, the REST faces refuse `403 obda_read_off` / `obda_model_off` / `obda_act_off`) | off until you adopt data products / need a write contract; then sign and grant one at a time |
| **OBDA policy — contracts only** | Administration → App Config → **Trust** → *Enforcement* → *OBDA policy* | `off · observe · enforce`, one row per plane — enforce makes signed contracts the only way that plane may read, write or model, on every turn; observe records the would-be refusals | off; observe before enforce |
| **Signed actions — per agent** | the ontology card's **Actions** (Designer → Grant) · profile editor → Agent Authority → *Approvers of parked writes* | which signed actions the agent may perform (the grant is the list) · who decides its parked writes (never its owner) | grant only what the agent's job needs; set approvers for any action in `review` posture |
| **Data products — per ontology** | the ontology card's gold **Products** button (Designer) | author / edit the manifest · run the V1–V7 gate (Structural or **Live** per DB connection) · sign · delete | validate LIVE before signing wherever a connection exists |
| **Signed knowledge only — per agent** | Profile editor → *Knowledge* card → **Signed knowledge only** | on/off (`requireSignedKnowledge`) — retrieval restricted to integrity-signed collections | off; on for agents whose answers must be corpus-attributable |
| **Assessment modules** (which module assesses what) | Administration → App Config → **Trust** → *Assessment modules* | rebind a property's module per lane · unbind (reads NOT ASSESSED) · restore the shipped default. **Changes are staged and applied by Save Trust Settings**, each as its own audited act | leave at the shipped defaults unless a pack gives you a genuine alternative |
| **Third-party control posture** | the same card, *What acts on it* | off / observe / annotate / enforce / review — the one act that lets a pack's machinery act at all | **observe** (where it arrives); raise deliberately |
| **Trust sign-off** | each thing's trust badge (skills, extensions, knowledge models, repositories, packs) + the shield on the agents list + **the SIGNED station on the Trust Circle** (pin it; the act offered is the act that is owed) | sign · re-sign · attest this deployment · verify | sign your production agents, then attest each deployment |
| **Evidence export and verification** | an answer's *Why trust this answer?* → **Export evidence** / **Verify in this browser** · Trust → **Evidence** → *Verify all shown · Export shown · Export for audit* | — | export for audit before any external review; give the fingerprint separately |
| **Evidence content limit** | `TDA_EVIDENCE_CONTENT_LIMIT_BYTES` (compose / `.env`) | how much signed content a bundle carries per artefact | 256 KB |
| **Key custody** | `TDA_EVIDENCE_SIGNER` (+ `_COMMAND`, `_PUBLIC_KEY`) | `local` (key file on the host) · `command` (an external signer the host cannot read) | `local`; `command` where history must withstand the host's own administrator |
| **Ledger anchoring** | `TDA_EVIDENCE_ANCHOR` (+ `_TARGET`, `_TOKEN`, `_INTERVAL_SECONDS`) | off · `file` · `webhook` · `rfc3161` | off; on for regulated deployments |
| **Ontology Governance** | Administration → App Config → **Features** → *System Feature Availability* → *Ontology Governance* | require approval · who may approve (these tiers are also who may **sign off**; admin by default) · block invalid ontologies from release | off by default |
| **Agent Pack Governance** | Administration → App Config → **Features** → *System Feature Availability* → *Agent Pack Governance* | require approval · require trust to publish | off by default |
| **Consumption / cost** | Administration → App Config → Security (rate limits) + Administration → **Quotas** (consumption tiers) | per-user limits · consumption tiers | disabled for single-user |
| **Behavioral trust** | Administration → App Config → **Trust** → *Enforcement* → *Behavioral Trust Score* (evidence window, state floors and staleness via the `trust_behavior` block) | enabled · evidence window · state floors · staleness | **off** (compute-on-read; ring hidden until enabled) |
| **Circuit breaker** | the same block → *Circuit breaker* (`trust_behavior.breaker_mode`) | off / notify / freeze_autonomy | **off** |
| **Policy backtest** | Trust → Trust Posture → Policy backtest | grounding / outbound / inbound modes to simulate | on demand (read-only, 0 tokens) |
| **Memory + retrieval firewalls** | ride the overall Guardrails switch | — | inherit guardrails master (off = baseline) |
| **Trust-aware routing** | coordinator profile `genieConfig.requireTrustedExperts` | true / false | **false** |

#### 3.2a Third-party controls — what you are agreeing to when you raise one

A pack can ship a **control**: machinery that withholds, alters or annotates a live answer.
Three things are true of every one of them, and they are what make raising one a decision
you can make safely.

**It arrives dormant.** A third-party control installs at `observe`. It records what it
*would* have done and changes nothing until you raise it here. Installing a pack can never,
by itself, withhold an answer — so you can run a new pack for a week and read what it would
have caught before giving it any power.

**The act is the framework's, not the pack's.** The pack chooses *when* to act and *which*
act from a closed set (withhold · alter · annotate). Uderia performs it, and the posture
ladder, the parking, the retention, the audit event and the reading on the Trust Circle are
identical however the finding arrived. A pack cannot ship "always withhold, whatever the
operator set": it declares what it wants at **enforce**, and every weaker posture is derived
by the framework.

**It cannot act unless its pack is signed and bound.** A control only ever sees findings from
a module you have assigned, and assigning requires a signature. An unsigned pack's control is
inert at any posture — you do not have to remember this, it is structural.

**Two things to know when you set one:**

- *Restore* is not the same as choosing `observe`. Restoring forgets the decision (the
  control is dormant and nothing is recorded); choosing `observe` records that you decided
  it should watch. Only the first leaves this deployment's attestation.
- **Raising a control moves the deployment attestation.** The agent will read *changed* until
  you re-attest, exactly as it does when a bound module is swapped — because starting to
  withhold answers is the larger change of the two.

Uderia's built-in gates behave differently on one point: their posture is **per agent** (the profile
editor's Trust Configuration), not deployment-wide, so their rows here name where their mode
is set rather than offering a switch.

> **Where the per-agent cards live:** the profile editor has a dedicated **Trust
> Configuration** sidebar section, grouped by Trust Circle station — GROUNDED holds
> Grounding *and* Honesty (honesty's findings land at GROUNDED), GUARDED holds
> Guardrails, BOUNDED holds Agent Authority, JUDGED holds Judges. REVIEWED is armed
> by setting any gate to *Review*; SIGNED, TRUSTED and PROVEN have nothing
> per-profile to set. Cards open collapsed (a single-card section stays open).

> **Persistence note:** the App Config **Trust** tab has its own **Save Trust Settings** button;
> its settings (scoring, judges, quality bands, the grounding and guardrails masters, behavioral
> trust, the OBDA policy, reporting levels, staged module bindings) persist across restart.
> Per-profile cards save with the profile.

### 3.3 Runbook — standing up assurance on a new deployment

1. **Turn on measurement.** Administration → App Config → **Trust** → *Scoring* → enable
   *Automatic Agent Scoring* → **Save Trust Settings**. Leave judges sampled (or off to start). Nothing changes for users;
   you now have data.
2. **Set grounding + guardrails to observe** (switches on; the grounding and outgoing-guardrail
   modes at *observe*).
   Run real traffic for a few days.
3. **Read the Agent Roster.** Left nav → **Trust** → *Agent Roster*. Look for red/amber health, out-of-scope
   drift, personal-data exposure, cost outliers. This tells you *which* agents need enforcement.
4. **Curate the ontology** for the profiles that need grounding. Build/refine the KG; run the
   **validity** check; fix issues.
5. **Tighten.** Move the data-touching / externally-answering profiles to grounding **enforce**
   and guardrails **enforce**. Leave exploratory profiles at observe.
6. **Sign and attest.** Sign your production agents, then attest each deployment — two
   distinct acts (§3.5). Optionally enable Ontology/Pack governance approval.
7. **Constrain autonomy.** For any profile used by a scheduler, genie coordinator, or flow, set
   an **Agent Authority** scope (§3.8).
8. **Establish a monitoring cadence** (§3.9).

### 3.4 How the layers compose on one turn (defense in depth)

Understanding the ordering tells you *what catches what*, so you know which control to reach for
when something slips through:

1. **Inbound guardrail** — a prompt-injection is refused before the model sees it.
2. **Agent Authority** — if autonomous, the principal + scope are set for this turn.
3. **Grounding trusted-scope** — the profile's bound-ontology scope is stamped onto the turn.
4. **Tool calls** — each MCP/component/connector call is identity-enforced (denied if out of
   scope).
5. **Outgoing guard** — personal data masked, toxic answers withheld.
6. **Grounding gate** — a confident out-of-scope answer is annotated or withheld.
7. **Eval** — the turn is scored; drift/cost/quality banded.
8. **Provenance** — the whole turn is sealed into the signed chain; the agent's trust badge
   reflects its (unchanged) composition.

A hallucination that slips past grounding may still be caught by evaluation; a personal-data leak the model
generates is caught outbound; an injection is stopped inbound. **No single layer is load-bearing.**

### 3.5 Workflow — signing an agent, then attesting the deployment

**Two acts, not one.** An agent is partly portable and partly local, so its trust envelope has
two halves and each is vouched for separately.

| Act | Covers | Who performs it | Travels? |
|---|---|---|---|
| **Sign off** | composition (ontology · skills · repositories), posture, capability | a **signer tier** (the Ontology Governance approver tiers — admin by default), on their own artefact | **yes** — survives publish and install |
| **Attest deployment** | this instance's resolved **model**, reachable **MCP tool/prompt catalog**, per-profile **connector opt-in** | the **owner** of the agent — any tier | **no** — every instance attests its own |

#### Signing (bottom-up)

1. Open the agent — its shield shows ⚪ unsigned and *what is blocking it* (unsigned parts).
2. Sign each blocking part from its own badge (the knowledge model, the skills, …). The screen
   walks you through it — *"to sign this agent, these parts need signing first."*
3. Sign the agent. That one act vouches for the whole make-up. Only ingredients you can read
   count: a binding you cannot read (a planner a bootstrap template pointed at someone else's
   repository) is not a trust ingredient, and such inherited planner bindings are re-pointed to
   your own default planner at login — so a bootstrapped agent is signable by its owner. If the
   server still refuses, the dialog shows its reason in words (a 409 names the still-unsigned
   parts).
4. If any part later changes, the badge honestly flips 🟠; re-sign to re-affirm — **but read
   *which half* moved first** (see "When 🟠 changed means re-attest, not re-sign" below).
5. Anyone can verify the badge is genuine — including offline, on hand-off.

#### When 🟠 changed means re-attest, not re-sign

🟠 `changed` proves a baseline exists — the agent *was* vouched for, and something has moved
since. It does **not** say which half moved, and the two have different remedies:

| What drifted | What the panel names | The act that helps |
|---|---|---|
| **Portable** — composition, posture, capability | the changed ingredient | **Re-sign this agent** |
| **Deployment** — resolved model, tool catalog, connector opt-in | e.g. *"model changed: Google/gemini-3.1-flash-lite → OpenRouter/inception/mercury-2"* | **Re-attest this deployment** |
| **Both** | both drifts, named separately | both acts |

Re-signing a deployment-face drift **cannot clear it** — the portable signature was never
broken. That is why the SIGNED panel offers the act that is *owed* rather than a generic
"sign" button: an offered act that cannot help reads as the fix, which is worse than no button
at all ([Rule 8.5](../Architecture/TRUST_STATUS_COMMUNICATION.md)). The panel names the drift
verbatim in its Evidence slot, so you can tell the two apart before you click.

#### Attesting the deployment

**Signing alone does not turn a badge green.** An agent you installed from a pack — or any agent
whose deployment has never been attested here — reads ◒ **deployment unattested**: *the author
vouched, you haven't.* SIGNED shows `— · not attested here` and offers **"Attest this deployment"**.

6. Click the **SIGNED** station to pin it, then **Attest this deployment**. The station goes
   green and reads `— · attested`. **You do not need to be an admin** — attesting a deployment
   is the *owner's* act on their *own* agent, because it is a statement about a surface only
   they control ("this model, these tools, these connectors are what I intend here"). Signing
   off the artefact stays with the signer tiers (admin by default, set under Ontology
   Governance → who may approve; under an approval policy nobody signs their own): that half
   vouches for something a third party can check.

**Why it is a separate act.** The three things most worth catching are a **swapped model**, a
**grown tool catalog** (a write tool appearing where there were only reads) and a **newly enabled
connector** — and those are exactly the three that cannot survive an install unchanged, because a
subscriber runs their own model, their own server and has opted into nothing. Folding them into
the portable signature would force a choice between two dishonest outcomes: every installed pack
permanently 🟠 (which teaches people to ignore 🟠), or real widening hiding behind 🟢. Two halves
avoid both.

**What it buys you.** Once attested, those three are drift-detected and the badge *names what
changed* — *"tools appeared: base_writeQuery(sql)"*, *"connectors enabled: uderia-shell"*. An
unexplained amber gets clicked through; a named one does not.

**One honest limit.** The tool catalog is read from the stored classification snapshot, not a live
connection — so the badge resolves offline, but an upstream MCP server change is caught when the
profile next re-classifies, not the instant it happens.

**The fork trap — attest a freshly forked profile in the wrong order and it drifts on its own
first use.** A newly forked or installed profile's `classification_results` is not pre-populated —
classification is lazy: it runs when the profile is first actually dispatched to *while active for
consumption*, not at fork time. Attesting a deployment before that first run locks in an early
(often empty or partial) snapshot as the baseline; the moment the agent is actually used — its
classification runs, the catalog fills in for real, and the badge flips 🟠 `changed` (*"tools
appeared: …"*), even though nothing insecure happened — the reachable surface was simply measured
for the first time after, not before, the attestation. The safe order:

1. Fork or install the profile.
2. **Activate it** — run its connection test (Anatomy → Agent Profiles), the same step every agent needs
   before it can be driven at all. This is what makes classification eligible to run.
3. Optionally force it explicitly: `POST /v1/profiles/<id>/reclassify` (it only actually
   reclassifies an **active** profile — on an inactive one it just clears the cache and asks you to
   activate first).
4. **Then** attest the deployment. The snapshot is settled, so the badge holds.

Attesting before step 2–3 is not wrong, exactly — the fix is the same either way (re-attest once
classification has run) — but it produces a needless 🟠 the moment someone first uses the agent,
which reads as a problem when it is really just late-arriving information.

### 3.5a Workflow — sharing a planner repository: view, seal, lock

A subscriber's planner repository is a **view of the publisher's collection**, not a copy:
the subscriber's card shows the publisher's collection id and vector store, there is no
second collection anywhere, and the subscriber holds one subscription row keyed to their own
MCP server so the repository *loads* for their sessions. (A **fork** is the copy.) Keying it
to your own server changes where it loads, never whether you can write to it — a subscriber
never writes into a view; their agents' learning lands in their own default planner for that
server.

The seal over a planner covers the **shipped** cases (the ones the pack stamped), not what
the repository learned since — otherwise a signed agent would break its own seal by
answering well ([TRUST_MEMBRANE_ARCHITECTURE.md §3a](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md)).
**Lock** (owner-only, on the planner card) widens the seal to every case and stops the
owner's own learning loop from writing into it. Two walkthroughs on the same objects — the
publisher's planner with its shipped cases, and a subscriber viewing it:

**Unlocked (the default)**

| Step | The publisher sees | The subscriber sees |
|---|---|---|
| Start | 14 cases, no Locked pill, seal over the 14 | the same card, the publisher's collection id, *Subscribed*, no Locked pill |
| The publisher's agent succeeds on a new question | the learning loop seeds case 15; the seal still covers only the 14, so planner and agent stay trusted | 15 cases, immediately — no sync exists for a view; their agent stays trusted for the same reason |
| The publisher votes a case down | champion re-election runs; still trusted | their next query reads the re-elected champion; nothing moves on the trust side |
| The publisher rebuilds the pack with a shipped case rewritten | the baseline changes: planner and agent read `changed`; re-sign both, republish in place | their agent reads `changed` at the same moment; the pack sync fan-out reinstates it after the republish; until then they can only wait |
| The subscriber's agent succeeds on a new question | nothing — their learning never reaches the view | their case lands in their own default planner for their server, and their agent **reads it back** on its next question alongside the view; the shared card is unchanged |

**Locked (the publisher sets Lock)**

| Step | The publisher sees | The subscriber sees |
|---|---|---|
| Lock | *Locked* pill; the seal now covers every case, so an already-learned case 15 reads `changed` until re-signed; Lock/Unlock stays owner-only | *Locked* pill on their card too; their agent follows the publisher's seal state |
| The publisher's agent succeeds on a new question | the loop refuses to write into a locked repository; the case lands in the publisher's own default planner; the shared set and its seal are untouched | unchanged — exactly the frozen set, and it stays that way |
| The publisher edits a case by hand or rebuilds | the seal moves; re-sign, republish, fan-out, as above | `changed`, then reinstated after the republish |
| Unlock | pill gone; the seal shrinks back to the shipped cases — nothing reads `changed` because the content did not change; learning writes into the repository again | pill gone; nothing else moves |

What is identical in both: the subscriber never writes into the publisher's collection, and
the subscriber's agent shows the publisher's trust state at the same time, because there is
one collection. What Lock changes: whether the publisher's everyday use can grow the shared
set. Unlocked, every subscriber inherits what the publisher's agent learns, without touching
any seal; locked, the repository holds exactly what was signed.

### 3.5b Workflow — exporting and verifying evidence

**For one answer.** Open *Why trust this answer?* under the answer. The **Evidence** row reads
the parts; open it for the tree, and a part for its signer, time, key fingerprint and hashes. A
part of what the agent is made of that has changed since, or was not signed, offers **Open trust
record** (where you can re-sign it); an intact part offers nothing. **Verify in this browser** checks the answer's chain, its bill of materials and every
signature it was made with, in your own browser. **Export evidence** downloads one file: the
question, the answer, its trust checks, its sealed chain and every signature it was made with.

**From an artefact.** Open its trust badge. *Used by N answers* lists them; *N with this version*
tells you how many used the version it has now.

**For the whole estate.** Trust → **Evidence**. The summary line counts what needs attention;
the pills narrow the list (*Changed since signed · Retired key · Recorded, not signed*) without
changing the summary. **Verify all shown** checks the listed signatures in your browser.
**Export shown** downloads the listed signatures; **Export for audit** downloads every signature
you may see together with the deployment's signed audit pack, in one file. The Trust Circle's
SIGNED station routes here (filtered to *changed since signed* when something changed), and so
does PROVEN.

**Verifying outside Uderia.** Give the auditor the exported file and, **through a separate
channel**, the deployment's public-key fingerprint (`GET /api/v1/provenance/public-key`, or ask
the operator). Then either:

- open the evidence verifier page (`/static/trust/verify-evidence.html`, also on
  trust.uderia.com) and drop the file in. Nothing is uploaded; the check runs in the browser; or
- run `python scripts/verify_evidence.py <file> --fingerprint <fingerprint>`, which needs only
  `pip install cryptography`.

Both report each signature, each answer's chain, the audit pack and the ledger proof, and name
anything that does not verify. The question and answer in the file are checked too: they must
hash to what the answer's chain sealed, so an answer edited in the file reads *"the answer text
does not match what was sealed"*. The chain seals the first 4,096 characters of each; for a
longer answer the verifier says only that part is checked. Without a fingerprint the file is checked only against the keys
it carries, which proves it is consistent with itself, not who signed it; both verifiers say so.

**Signed content travels up to a limit.** A bundle carries the exact bytes each signature
covers, so the content can be re-hashed offline, up to `TDA_EVIDENCE_CONTENT_LIMIT_BYTES`
(256 KB by default). A larger artefact, such as a big ontology, travels as its hash only, and
the bundle says so per entry. An artefact that has changed since it was signed travels as its
hash too: its signed bytes are no longer held.

**Taking the key off the host (opt-in).** By default the signing key is a file on the server.
Set `TDA_EVIDENCE_SIGNER=command` to sign through an external signer instead: an HSM, a cloud
KMS or a Vault transit key behind `TDA_EVIDENCE_SIGNER_COMMAND` (it reads the message on stdin
and prints a base64 signature), with its public key at `TDA_EVIDENCE_SIGNER_PUBLIC_KEY`. The
server can then ask for signatures but cannot read the key. Every returned signature is checked
before use; a signer that fails or answers wrongly produces no signature, never a bad one.
After switching, older answers keep verifying against the key that signed them.

**Publishing checkpoints off the host (opt-in).** Set `TDA_EVIDENCE_ANCHOR` to `file` (a path
this host cannot rewrite, such as a WORM mount), `webhook` (an append-only log;
`TDA_EVIDENCE_ANCHOR_TOKEN` adds a bearer) or `rfc3161` (a time-stamp authority's URL), with
`TDA_EVIDENCE_ANCHOR_TARGET`. A signed checkpoint of the ledger is then published every
`TDA_EVIDENCE_ANCHOR_INTERVAL_SECONDS` (default an hour, skipped when nothing changed), and an
administrator can publish one at once (`POST /api/v1/evidence/ledger/anchor`). Rewriting history
would then conflict with what was already published. The Evidence tab's ledger chip counts the
published checkpoints and reads amber if the last publish failed. Its tooltip states who holds
the key and whether checkpoints are published, never claiming more than holds.

### 3.6 Workflow — governing an ontology (validity → release)

1. Open the ontology's **Govern** dialog. Read its **validity report**: valid / partial /
   invalid, with the specific issues listed.
2. Fix what's invalid (the report is specific). A *partial* ontology can still be released, with
   a noted caveat.
3. Walk it through the lifecycle: **draft → propose → approve → release**. Releasing fixes a
   version and signs a verifiable record of it. If approval is required, the approver must be
   authorised and (by policy) not the owner.
4. **Retire** it when it's no longer current — a recorded, auditable act.

> **Note:** "release" here means *marking this version as the official one*, which is a
> different act from *sharing it on the marketplace*. By default agents ground on the live
> (draft) model. To make a release govern what an agent grounds on, set the agent's
> knowledge-graph component **Ground on** to *released version only*: it then reads the last
> release, its knowledge-graph writes are refused, and its badge follows the release rather
> than every draft edit (with no release yet it reads the draft, and the turn's card says so).

### 3.7 Workflow — governing an agent pack / data product

Open the pack's **Govern** dialog: a lifecycle stepper, a **trust hero** (affirms or nudges you
to sign off), one primary CTA (the recommended next action), instant SLA pills, an activity
timeline. Enable *require-approval* / *require-trust* in the admin Agent Pack Governance policy
to enforce that a published pack is approved and trusted. Deprecation archives the pack's
sessions and blocks new forks.

### 3.8 Workflow — scoping an agent that acts on its own

For any agent used unattended (scheduled tasks, a coordinator's experts, flow steps, calls from
other systems):

1. Profile editor → **Agent Authority** card.
2. Decide the limits: keep it **read-only**? bar it from specific **tools**? restrict which
   **connectors** it may reach? cap its **spend per run**? Or switch off **unattended use**
   entirely.
3. Save. From then on, unattended runs act under those limits; the owner's own interactive use
   is unaffected.
4. Confirm in the audit trail: unattended actions show as done *by the agent*, and any blocked
   attempt is recorded.

### 3.9 Monitoring &amp; triage

| You want to… | Go to | Look for |
|---|---|---|
| See overall agent health | **Trust** → Trust Circle / Agent Roster | red/amber stations and cards, failing checks, cost/speed outliers |
| Investigate one bad answer | **Awareness** → the session → the turn → **Quality** card (or the answer's assurance dial in Reasoning) | which checks turned red; the grounding and guardrail notes on the trace |
| Confirm drift is being caught | a turn's live status → **grounding** note | its decision: pass / annotate / block, and the scope it checked |
| Confirm personal-data / attack handling | a turn's live status → **guardrail** note | the categories caught (labels only, never the raw value) |
| Confirm an agent is trusted | the agent's **shield** badge | 🟢 / 🟠 / ⚪ and, if not green, what's blocking it |
| Confirm who did an unattended action | the audit trail | whether it was done *by the agent* or *by the user*, and any blocked attempts |

### 3.10 Recommended production posture (checklist)

- ☑ Online eval **on**; judges sampled (raise the rate for high-stakes deployments).
- ☑ Grounding **enforce** on every profile that answers from data or answers externally;
  observe on exploratory profiles.
- ☑ Guardrails on: **enforce** the outgoing mode on every externally-facing agent; incoming
  protection on via the overall switch.
- ☑ Production ontologies **conformant** and **released**; production agents **signed** (🟢).
- ☑ Every autonomously-used profile carries an **Agent Authority** scope (least privilege).
- ☑ Agent Pack Governance requires approval + trust for published packs (if you distribute).
- ☑ A weekly cadence on the **Trust** → Agent Roster; triage any profile trending amber/red.
- ☑ The Trust → **Evidence** tab reads *ledger intact*; for a regulated deployment, the signing
  key held off the host (`TDA_EVIDENCE_SIGNER=command`) and checkpoints anchored outside it.

---

## Part 4 — the capability &amp; control reference

| Layer | Capability | Default | Can it enforce? | Where you set it | Deeper reading (optional) |
|---|---|---|---|---|---|
| Foundation | Ontology (knowledge model) | — | — | KG inspector, Ontology view | [ONTOLOGY](../Architecture/ONTOLOGY_ARCHITECTURE.md) |
| Grounding | anti-hallucination | observe / overall off | yes | App Config → Trust + agent card | `TIER_1.1` |
| Guardrails | incoming + outgoing | observe / overall off | yes | App Config → Trust + agent card | `TIER_1.3` |
| Identity | scoped autonomous agent | unrestricted | yes (safely) | agent card | [AGENT_AUTHORITY](../Architecture/AGENT_AUTHORITY_ARCHITECTURE.md) |
| Evaluation | scoring + judging | on (scoring) | no (measures only) | App Config → Trust → Scoring | [AGENT_OPS_EVAL](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) |
| Observability | Awareness + Trust views | on | no | left navigation | [AGENT_OPS_EVAL](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) |
| Trust | signed badges | unsigned | badge (optional gate) | each thing's badge | [TRUST_MEMBRANE](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md) |
| Trust | evidence per answer, ledger, offline verification | on (with the suite); custody + anchoring off | no (records and proves) | answer capsule, Trust → Evidence, env settings | [EVIDENCE](../Architecture/EVIDENCE_ARCHITECTURE.md) |
| Governance | ontology lifecycle | off | opt-in | App Config → Features → System Feature Availability | `TIER_4.3` |
| Governance | pack lifecycle + service tier | off | opt-in | App Config → Features → System Feature Availability | [PACK_GOVERNANCE](../Architecture/PACK_GOVERNANCE_ARCHITECTURE.md) |
| Governance | governed metrics | — | — | metric node, admin | `TIER_4.2` |
| Operational | consumption / cost | disabled | yes | App Config → Security | [SECURITY](../Architecture/SECURITY_ARCHITECTURE.md) |

---

## Part 5 — compliance mapping

Agent Trust &amp; Assurance is how a Uderia deployment evidences AI-governance obligations:

- **EU AI Act / NIST AI RMF** — grounding (accuracy), guardrails (safety/robustness),
  evaluation (measurement/monitoring), provenance (traceability), agent identity
  (accountability), governance lifecycle (risk management).
- **GDPR** — personal-data detection and masking on the way out (categories only, never the raw
  value); consumption and data-product governance; the ability to erase what an agent remembers.
- The tamper-evident record of every turn, plus the signed quality and trust verdicts, give an
  auditor evidence they can verify independently — even offline. **Export for audit** puts it in
  one file; the evidence verifier checks it without Uderia (§3.5b).

---

## Related documents

- **Reading the surfaces:** [TRUST_UX_FAQ.md](TRUST_UX_FAQ.md) — the companion FAQ. This manual covers *what each control does and how to operate it*; the FAQ covers *how to read what the ring, the Scorecard and the Assurance Guide are telling you* — why a station is red, what a dot means, why a check is greyed out, and the mechanism-vs-measurement distinction every other question turns out to be about.
- **Architecture (internals):** [AGENT_OPS_EVAL_ARCHITECTURE.md](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) ·
  [TRUST_MEMBRANE_ARCHITECTURE.md](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md) ·
  [EVIDENCE_ARCHITECTURE.md](../Architecture/EVIDENCE_ARCHITECTURE.md) ·
  [AGENT_AUTHORITY_ARCHITECTURE.md](../Architecture/AGENT_AUTHORITY_ARCHITECTURE.md) ·
  [ONTOLOGY_ARCHITECTURE.md](../Architecture/ONTOLOGY_ARCHITECTURE.md) ·
  [PLATFORM_ONTOLOGY_ARCHITECTURE.md](../Architecture/PLATFORM_ONTOLOGY_ARCHITECTURE.md) ·
  [PACK_GOVERNANCE_ARCHITECTURE.md](../Architecture/PACK_GOVERNANCE_ARCHITECTURE.md)
- **Roadmap / tier docs:** `TIER_1.1` ·
  `TIER_1.3` · `TIER_1.4` ·
  `TIER_4.2` · `TIER_4.3` ·
  `TIER_5.2 Trust Membrane`
- **Delivered (Aug 2026):** [OBDA_ARCHITECTURE](../Architecture/OBDA_ARCHITECTURE.md) — grounding and identity made *structural*: signed data products, the data surface, sealed consumption receipts, the compositional ratchet. (The vision that predicted it: OBDA_VISION (`docs/roadmap/OBDA_VISION.md`).)
- **Delivered (Oct 2026):** [ACT_ARCHITECTURE](../Architecture/ACT_ARCHITECTURE.md) — the write half: signed action types, model extensions, four-eyes approval, receipts for every act through every door.
- **The other pillar:** [SECURITY_ARCHITECTURE.md](../Architecture/SECURITY_ARCHITECTURE.md)
