# Trust UX — Frequently Asked Questions

*How to read Uderia's assurance surfaces: the Trust Circle, the assurance dial, the
Scorecard and the Assurance Guide.*

This is the **reading** guide. What each control does and how to configure it is the
[Agent Trust & Assurance](AGENT_TRUST_AND_ASSURANCE.md) operations manual; why each
surface is shaped the way it is, is
[TRUST_STATUS_COMMUNICATION.md](../Architecture/TRUST_STATUS_COMMUNICATION.md).

Most of these questions came from operators reading real turns. Where a question rests
on a misconception, the misconception is stated plainly — several were reasonable
readings of a surface that was saying the wrong thing, and are marked as such.

---

## Find your question

**Start with Part A if you read nothing else.** Every other confusion in this document is
a version of the distinction it makes.

| You are… | Go to |
|---|---|
| new to these surfaces, or unsure why a check exists at all | **A** — [§1 Mechanisms vs measurements](#part-a--the-idea-everything-rests-on) |
| looking at the **ring** — a coloured arc, a dot, a station panel | **B** — [§2 Reading the ring](#2--reading-the-ring) |
| looking at the **Scorecard** — chips, greyed-out checks, the headline | **B** — [§3 The Scorecard](#3--the-scorecard) |
| looking at the **Agent Roster** — a pass rate, a sparkline, a run's marks | **B** — [§4 The Agent Roster](#4--the-agent-roster) |
| looking at the **Assurance Guide** — an entry's rows and counts | **B** — [§5 The Assurance Guide](#5--the-assurance-guide) |
| deciding **observe vs enforce**, or what a mode actually does | **C** — [§6 Gates and modes](#6--gates-and-modes) |
| wondering **why a judge ran**, what it can change, or whether to believe it | **C** — [§7 Judges](#7--judges) |
| looking at a **data product** — the capsule receipt, the confined lane, the Products dialog — or a **signed action** — a Confirm, a parked write, an approver, an *action-only* refusal | **C** — [§8 Signed data products and signed actions](#8--signed-data-products-and-signed-actions) |
| looking at a **shared repository** — a Subscribed or Locked pill, a subscriber's card that shows the publisher's id | **C** — [§9 Shared repositories](#9--shared-repositories) |
| looking at **Evidence** — an answer's parts, *Used by N answers*, the Evidence tab, the ledger chip, an exported file | **D** — [§10 Evidence and offline verification](#10--evidence-and-offline-verification) |
| convinced something is **broken** | **D** — [§11 Looks like a bug, isn't](#11--looks-like-a-bug-and-is-not) · [§12 Known open items](#12--known-open-items) |
| looking at the **header's status word**, the answer's **Made from** fold, a **failed turn**, or a refusal to sign | **D** — [§13 The answer, the header and a failed turn](#13--the-answer-the-header-and-a-failed-turn) |

**The four parts**

| Part | | Read it |
|---|---|---|
| **A** | The idea everything rests on | once, properly |
| **B** | Reading the surfaces | when something on screen is unclear |
| **C** | Behaviour and configuration | before changing a setting |
| **D** | Troubleshooting | when a reading looks wrong |

---

# Part A — The idea everything rests on

*One section. If a trust surface ever confuses you, come back here first.*

## 1 · Mechanisms vs measurements

### 1.1 What is the difference between a mechanism and a measurement?

This is the question. Almost every other confusion is a version of it.

| | **Mechanism** | **Measurement** |
|---|---|---|
| What it is | something that **runs during the turn** | a **check on the finished turn** |
| Examples | Grounding gate, Inbound guard, Corpus quarantine, Agent Authority | Numeric Fidelity, Grounding Overlap, Retrieval Relevance, Cost, Latency |
| Can it change the answer? | **Yes** — withhold, redact, rewrite, annotate, deny | **No, ever** |
| When | in-path, before delivery | after delivery |
| Where you see it | "what ran" in a station panel | the Scorecard, "what it measures" |

A station panel shows both, each labelled: **what ran · acted during the turn** and
**what it measures · graded after**.

### 1.2 Do the measurements drive the gates?

**No.** This is the single most common misreading, and the surfaces used to invite it.

By the time any measurement runs, the gate has already decided. Nothing on the Scorecard
can open or close a gate.

```
DURING the turn                      AFTER the turn
─────────────────                    ──────────────
model writes answer
   ↓
gate's own check → flags
   ↓
gate's judge → rules
   ↓
GATE WITHHOLDS  ───────────────────→ measurements run on the completed turn
                                     → the Scorecard
                                     → they change nothing
```

The gate is a guard at the door. The measurements are an inspector who arrives
afterwards and writes a report. The report does not move the door.

### 1.3 Then why have measurements at all?

Because a gate answers **one question, once**. It cannot tell you:

- **whether it was right** — it is a judgement, and judgements need a second opinion
- **why it happened** — was the model bad, or was the retrieval bad?
- **whether this is getting worse** — a gate is one turn, binary; trends need numbers
- **anything it must never block on** — cost, latency, tokens, corpus freshness

Worked example. A gate says *"unsupported"* — true, but not actionable. The
measurements said why:

| | |
|---|---|
| `retrieval_relevance` 0.63 | the search found plausible-looking documents |
| `context_utilization` 0.03 | but only 3% of the answer's words came from them |
| `knowledge_freshness` 92 | and the corpus has not been verified in three months |

**The fix was the corpus, not the model.** No gate could have told you that.

### 1.4 What does "graded by" mean, concretely?

Take two real turns of the same question.

**The gate passed an answer.** Afterwards `numeric_fidelity` looked at it:

> *6 of 10 numbers in the answer appear in the source documents — 60%.*

That is a grade **on the gate's work**. The gate's job is to stop unsupported content; it
let this through, and a check afterwards found 4 in 10 numbers were not in the documents.
The answer had already shipped. The grade changed nothing.

One turn at 60% is noise. **60% across fifty turns tells you the gate is too lenient for
that agent** — which the gate, saying only pass or block one turn at a time, can never
reveal about itself.

**The gate blocked an answer.** There it asked `groundedness` *before* deciding, and the
answer came back `0✓/3✗`. That verdict **was** the decision.

| | Groundedness (RAG) | Numeric Fidelity |
|---|---|---|
| When | **before** the gate decides | **after** |
| Effect | **it is the decision** | none |
| Guide row | `escalates to` | `graded by` |

### 1.5 So you grade a control through measurements?

Yes — with one refinement. **You grade the guarantee the control is responsible for, not
the control's internals.**

`numeric_fidelity` does not inspect the gate's code. It looks at the answer and asks *are
these numbers in the sources?* — the property the gate exists to protect. A 60% reading
does **not** mean "the gate malfunctioned"; it means "the property this control guards
was only 60% satisfied". The fix might be the gate's threshold, the corpus, or the model.

```
STATION      = the guarantee        GROUNDED: "the answer is not invented"
  ↑ upheld by
MECHANISMS   = act during the turn
  ↑ graded by
MEASUREMENTS = score it afterwards
```

### 1.6 The grades are deterministic and free. Why not feed them into the mechanisms?

Good instinct — and **Uderia already does, where it is safe.**

`numeric_fidelity` is *already* both: an in-path input (`minNumericFidelity` →
`numeric_concern` → the gate annotates on it) **and** a post-hoc score, computed with the
same primitive. The gate's whole deterministic trigger — numbers, quotes, identifiers —
*is* a set of free grounding measurements running in-path.

Three reasons the rest have not been promoted:

**Timing.** `cost`, `latency`, `tokens`, `clean_termination`, `provenance_integrity`,
`tasks`, `efficiency` all need the turn to be over. You cannot feed the finish line into
the race.

**Precision.** `grounded_overlap` and `numeric_fidelity` are declared non-gating because
they *over-flag by design*. A heavily paraphrased but perfectly correct answer fails an
overlap check. The gate already encodes the lesson: **the trigger does recall; the judge
does precision.**

**Independence — the real one.** An input cannot audit the thing it feeds. On one real
turn the gate's judge said *"faithful"* while `grounded_overlap` said `0.46 danger`. Read
by hand, the model had invented an encryption standard, an access-control model and a
bug-bounty programme: **the crude check was right and the judge was lenient.** Had
`grounded_overlap` been an *input*, you would have gotten a block and learned nothing —
the disagreement would have been consumed inside the decision. Because they stayed
independent, the disagreement was visible and told you something the gate cannot say
about itself.

That is why Uderia **reports** a disagreement between two measures of one property
rather than resolving it. The auditor cannot also sign the accounts.

> **The rule:** promote a measurement into a control when it is precise enough and
> available in time. Keep it independent when its value is telling you the control is
> wrong. Each promotion costs you an independent check.


### 1.7 Who decides whether a turn "passed" — the mechanisms or the measurements?

The **measurements**. This surprises most people, including the person who asked.

A turn counts as failed when at least one **gate-eligible** measurement lands in the
**danger** band. Amber never convicts — the scorer says so in its own words: *"Amber
(review) PASSES the gate but is flagged; only Danger (red) fails."*

**One exception crosses the two axes deliberately.** A runtime **grounding block** counts
as a failure on its own, so a turn whose answer was withheld can never read clean.

**Why not the mechanisms?** Because the number would flatter the agent. On one real
agent, 16 of its 18 turns had no mechanism act at all — a mechanism-driven rate would
read **89%** on an agent whose grounding measurements were the worst thing about it. The
comfortable number and the dishonest one.

> **Worth knowing about that exception.** On a blocked turn the delivered text is
> Uderia's own withholding notice, so the delivered-text scorers deliberately *abstain*.
> The turn is then counted as a quality failure on evidence Uderia chose not to
> gather. Both blocked turns in the sample below had **zero** failing gate-eligible
> measurements — the whole failure was the exception. This is intended (a withheld answer
> must not read clean), but it is a real crossing of the axes, so it is stated here
> rather than left to be discovered.

### 1.8 So an intervention and a low grade are two different readings?

Yes — and on real traffic they barely overlap. The same 18 turns:

```
a mechanism acted  + measurements flagged :  0
a mechanism acted  + measurements clean   :  2
nothing acted      + measurements flagged :  5   <- nothing caught these
nothing acted      + measurements clean   : 11
```

Every intervention landed on a turn whose measurements were otherwise clean; every
measurement failure landed on a turn no control touched.

Four cells, four meanings:

| | what it means |
|---|---|
| acted + flagged | a control stepped in *and* the result still grades poorly |
| acted + clean | **the system working** — a control caught it, what shipped is fine |
| **nothing acted + flagged** | **the one to care about** — the answer was delivered and it grades poorly. Nothing caught it. |
| nothing acted + clean | quiet |

A single number cannot carry both axes, and an intervention count would read `0` on the
third row — the row that matters most.

---

# Part B — Reading the surfaces

*Grouped by what is in front of you: the ring, the Scorecard, the Guide.*

## 2 · Reading the ring

### 2.1 What drives a segment's colour?

One rule per kind of station. A **guarantee** station (SIGNED, GROUNDED, GUARDED, BOUNDED,
TRUSTED, REVIEWED, PROVEN) is the **worst of its members** — the mechanisms that uphold, bound,
attest or evidence it, plus any open Assurance Inbox item filed against it. **JUDGED** is the
worst grade of *its* members, which are every scored measurement. A measurement is counted at
JUDGED and nowhere else (§3.2): it never colours a guarantee station.

| State | Colour | Width (dial · ring) | Word |
|---|---|---|---|
| `pass` | green | 1.0× · 3.4 | quiet |
| `warn` | amber | 1.35× · 4.6 | worth a look |
| `danger` | red | 1.6× · 6.2 | needs you now |
| `watching` | green, hollow, thinner | · 2.0 | watching — a chosen posture that records and stops nothing; quiet, because nothing needs you (ring altitude only; a turn has events, not postures) |
| `na` | grey, dashed | 0.36× · 1.6 | not turned on |
| `unknown` | grey, finer dash | 0.6× | not recorded |
| `pending` | animated | 0.7× · 2.2 | not in yet |

Two things worth internalising:

**Colour is attention priority, not a verdict.** It answers *"where do I look next?"*, not
*"is this good?"* A blocked answer turns GROUNDED red — and that is the gate **working**.
Red means *there is something here for you*, which there is: a parked answer awaiting your
decision.

**Weight carries the state before hue does.** Danger is ~1.8× the width of pass; `na` is
thin and dashed. The ranking survives without colour.

### 2.2 What does the dot mean?

**Binary. One dot, one meaning: a mechanism ACTED on this turn.** Withheld, redacted,
rewrote, denied, quarantined, parked.

- **No dot** — nothing acted; the station only reported.
- **An orange accent dot** — *an act is owed by you* (sign-off, attestation). Colour alone
  separates the two jobs: state-coloured = the system did something; orange = you must.

It is **not a count**. Whether one mechanism acted or three, it is one dot.

### 2.3 There is a dot on some run rows too. Same meaning?

Yes — that is the point. A dot means **a mechanism acted**, on every surface. It is why a
run row no longer carries a verdict dot at its head: for a while it had two dots side by
side meaning different things (§4.4). The verdict is carried by its word, in its colour.

### 2.4 "A gate acted" — is the dot gate-scoped?

No, and the surfaces used to say so incorrectly. **Nine mechanisms can set `acted`, and
only four are gates.** The others: a tool DENIED by Agent Authority, a quarantined corpus
document, documents withheld by the sensitivity ceiling, a retrieval-firewall chunk
labelled untrusted, an item parked for review, and an integrity mismatch.

The wording now says **"a mechanism acted"** everywhere.

### 2.5 Why is a station red when no dot says anything acted?

Because colour and the dot answer different questions. The **dot** says a mechanism
*acted*; the **colour** says where to look. They separate in two places:

```
GROUNDED red + dot      something WAS DONE to this answer
                        → grounding acted: True, "confirmed drift — answer withheld"

GROUNDED red, no dot    an OPEN inbox item is filed against the station (a station
                        shortfall, an unacknowledged incident) — it folds into the
                        colour until you decide or acknowledge it

JUDGED red, no dot      something was MEASURED and nobody intervened
                        → nothing acted; numeric_fidelity 0.60 and grounded_overlap 0.46
                          in danger
```

The last reading is the useful one: *here is what enforce would have caught*, sitting
there costing nothing — and it is on JUDGED, not on GROUNDED, because the gate did not
fail; the answer graded poorly afterwards (§3.5).

### 2.6 A measurement is amber. Does that colour the station?

It colours **JUDGED** — every measurement is a member of JUDGED, and a review-band
measurement raises JUDGED to *worth a look*. It never colours the guarantee the
measurement is *about*: on that guarantee's Guide entry it sits in the folded
*not members · graded by* group, which says so (*they colour JUDGED, not GROUNDED*).

The chip and JUDGED can legitimately differ. The chip always shows what was
**measured**; the station shows what it **folded**. Where a coarse check was cleared by
the judge that refines it (§3.6), the chip stays red and JUDGED goes amber — neither
is edited to agree with the other.

> Until the lane cut, a measurement also coloured the guarantee it was about; a screenshot
> showing GROUNDED red from a grounding *check* predates it.
>
> Until Aug 2026 an amber measurement could not colour anything: a band→state conversion
> written by hand handled `healthy` and `danger` and silently dropped `review`, the one
> value where the two vocabularies differ. A station could read *quiet* directly above
> its own amber check. A screenshot older than that showing exactly this is the bug, not
> the design.

### 2.7 What is the difference between the assurance dial and the Trust Circle?

Same grammar, different altitude.

- **Assurance dial** (Live Status) — **one turn**, the agent that produced it.
- **Trust Circle** (Trust → Trust Circle) — an **agent or the whole estate**, over a window.

What the numbers mean is decided by altitude: a turn reports events; a window reports
`n/N turns`; the estate reports worst-of across agents. Colour, dots, states and words are
identical on both.

### 2.8 Why does the dial fill up gradually while a turn runs, instead of all at once?

Because you should be able to *watch* the protections work, not just see the result. As the
turn runs, each station lights the moment its machinery reports: the inbound guard clears
first, then grounding shows it has retrieved knowledge, then the gates report in turn. That
mid-turn movement is **progress, not a verdict** — a station takes its colour only when its
check has actually finished. The one thing shown immediately is a change already made to the
answer (a redaction), which is surfaced and labelled as an act, because you are entitled to
see it happen. A station still "not in yet" is honest waiting, never a pass.

### 2.9 The Trust Circle's side panel opens with one sentence. Where did the summary rows go?

They are the sentence. The panel reads like the Anatomy pane: an eyebrow naming the agent (or
the estate), the loop in the stations' own order — *Born · signed · grounded · guarded · judged —
and the loop closes.* — then **one verdict sentence led by its colour word** (*2 stations need
you:* · *1 station worth a look:* · *Nothing needs you right now.*), followed by the station
readings it rests on: what needs you, the posture, the record. Below it, three rows —
**attention · quiet · absent** — name which stations are in each, and never repeat a figure. The
full key folds behind *How to read this ring · the full key*. Nothing was removed; it was said
twice before.

### 2.10 The grounding panel marks one module with a small arrow. What does that mean?

That the gate **consulted that module during the turn**, so its reading is part of the decision
shown above it; a module without the mark was graded after. The decision line itself now names
the finding in its family's words — *2 of 9 statements unmatched*, *1 figure unmatched*, *out of
scope: sales_db* — so you can see what decided the gate before opening a row. On an annotated
answer the modules show their real readings: the appended note is not graded as if it were part
of the answer.

### 2.11 A module row says "all abstained". Did the modules not run?

They ran, and each declined to grade — it had nothing to grade on this turn, and its row says
what it needed (*needs `scope_databases`, which this turn did not provide*). The row used to read
*none ran*, as if the modules had never been consulted. A mixed panel reads *3 modules: 2 quiet,
1 abstained*. An abstention is drawn dashed: it is not a pass.

### 2.12 SIGNED says "signed fallback". Is a fallback a problem?

No. The agent's signed deployment names two models — the primary and its fallback — so either one
answering is **inside the signature**, and SIGNED stays quiet while saying which one answered
(*answered by the signed fallback … instead of …*). The only recovery finding SIGNED can show is
a model **outside** the signature answering. Retries and continuations are named in words and
colour nothing; a provider refusal reads *refused* as an absence, because no model answered and so
nothing bears on the signature. Until Oct 2026 a fallback and a refusal both turned SIGNED amber.

---

## 3 · The Scorecard

### 3.1 Is the Scorecard the JUDGED station's card?

**No** — and its old name ("Quality") invited exactly that reading, which is why it was
renamed. It is the turn's **full scorecard, grouped by station**:

```
GROUNDED  7 · GUARDED 2 · JUDGED 9 · PROVEN 2  =  20 checks
```

**JUDGED is one group inside it.**

### 3.2 Where is a measurement counted?

**At JUDGED — all of them.** Measurement is a lane, not a property of a guarantee. A
check is taken after the answer shipped and changes nothing, so it is counted in one
place, and that place is the station whose subject is exactly that.

The other seven stations report what their **mechanisms** did during the turn. Those are
two different questions asked at two different moments, and folding them into one colour
meant a station could go red because a scorer graded the answer poorly *afterwards*, with
nothing saying whether its gates had actually failed.

A check still records **which guarantee it is about** — shown as a chip on its Guide
entry, and the way you get from a poor score to the machinery behind it. That is a label,
not a count.

### 3.3 Why are some chips grey?

Grey is three different states, and the difference matters:

| | Meaning | Example |
|---|---|---|
| **Abstained** | it applies and is configured, but there was nothing to measure | `grounded_overlap` when the gate withheld the answer — the delivered text is Uderia's notice, not a model claim |
| **Not applicable** | structurally does not apply here | `citations` when citations are not required |
| **Not set** | it **measured**, but no threshold is configured, so it cannot band | `context_utilization 0.03`, `knowledge_freshness 92` |

The third is worth attention. **"Not set" means it produced a real number that colours
nothing.** `context_utilization` at 3% is arguably the most diagnostic figure on the card
— it says the answer barely used the corpus — and it is the one check that structurally
cannot raise a flag until an admin gives it a threshold
(*Administration → App Config → Trust → Quality bands* — each slider sits under the moment
it measures).

### 3.4 The headline says "passing" but a station says "needs you now". Which is right?

Both. They answer different questions.

- The **headline** asks *did this turn fail?* Only a **gate-eligible** check failing
  convicts it.
- The **station** asks *where should I look?* Any signal, advisory included, raises it.

An **advisory** failure (`grounded_overlap`, `numeric_fidelity` — deliberately non-gating
because they over-flag) is real information but not a conviction. It shows in the headline
as *"passing — N flagged for review"*, and it still colours its station.

Nothing is dropped: convictions + flagged always equals every non-clean check.

### 3.5 GROUNDED is green but a grounding check failed. Is that wrong?

No — they answer different questions. GROUNDED reports what its **four gates did during
the turn**: whether one escalated to a judge, withheld an answer, or passed everything
cleanly. The failing check is a **measurement of the finished answer**, taken afterwards,
and it is counted at JUDGED.

So a clean GROUNDED beside a red grounding check reads: *the gates ran and did not have
to act, and the answer still graded poorly.* That is worth knowing — and it is exactly
the reading a single blended colour destroyed. Click the check's Guide entry: its home
chip takes you to the guarantee whose machinery should have caught it.

### 3.6 A check is marked "disputed" — what does that mean?

Two measures of one property disagreed. `grounded_overlap` counts literal token overlap;
the `groundedness` judge reads meaning and is documented as *refining* it.

When the coarse check flags and the judge clears, Uderia **reports the
disagreement** instead of resolving it — the station reads *worth a look*, and the reason
is named. Either the answer is heavily paraphrased (benign) or the judge was lenient, and
a human should glance either way.

The measurement is never rewritten to make a station calmer.

---


## 4 · The Agent Roster

### 4.1 What does the "live pass rate" actually measure?

**Quality, not intervention** — see §1.7. It is measurement-driven: the share of scored
turns with no gate-eligible measurement in the danger band.

The name is historical. "Pass rate" and "failed" are validity words, and unqualified they
invite the mechanism reading they are not. Read it as *answer quality*.

### 4.2 What makes a sparkline bar green or red?

One bar per turn, oldest → newest, driven by the **same** value as the rate above it — so
the sparkline is the rate's own evidence, not a second opinion. 10 red bars ⇔ "10 failed".

Two things it deliberately does not do:

- **The height is not magnitude.** There are exactly two heights — a pass is full, a fail
  is half and bottom-aligned — so the state is legible with no colour at all. The
  variation you see is those two alternating.
- **A red bar does not say which kind of red.** A turn whose answer a gate *withheld* and
  a turn that merely graded poorly render identically.

### 4.3 Why is there no amber bar?

Because per turn the reading is binary — a turn passed or it did not. Amber lives one
level up, on the **rate**: ≥80% green, ≥50% amber, below that red. A single review-band
measurement can never tint a bar, but enough of them can move the rate into amber.

### 4.4 What do the marks in a run row mean?

The per-run band strip — one mark per scored dimension, in **station order** with hairline
separators, so it reads as the ring's segments laid flat. Three channels, none of them
colour alone. The **Key** above the ledger shows them drawn (hide it once and it stays
hidden); the samples come from the same builder that draws the marks, so the key cannot
drift from what it explains.

| channel | reads |
|---|---|
| **fill** | solid = passed · half = worth a look · outlined = failed · dashed = did not run |
| **shape** | square = a rule measured it · **diamond** = a model judged it |
| **size** | a **larger** diamond = a gate consulted that judge **during** the turn, so its verdict could withhold the answer; the plain diamond = **sampled** after the turn, it reported and changed nothing |
| **centre dot** | the refining judge **disagreed** (§3.6). **Green** on a failed check — the judge cleared the same answer, so the conviction is withdrawn. **Red** on a passing check — the judge disputed the pass; the measurement stands and the turn does not fail, because a judge alone never convicts (§7.4). The outline always keeps what the rule measured |

The same state on two shapes does **not** have the same consequence — see §4.5.

Every row shows the **same checks in the same columns**, so you can read a column *down*
the ledger and ask "did this one pass on every turn?". A check that did not run on a given
turn appears dashed in its place rather than being left out — it keeps its shape, because
shape says who measures it, not whether it ran.

Hovering any mark says what that check **found** on this turn, not just what it is.

A further mark sits on the row rather than in the strip, because it is not a measurement:
a **dot plus the act in words** (`withheld · parked`) means a mechanism
acted — what was delivered is not what the model wrote. It turns **accent orange** when
something is owed, and the **Acted** filter isolates those turns. It is deliberately
independent of the verdict beside it: a turn every check calls clean can still have had
its answer withheld (§1.8).

### 4.5 What is the difference between a red square and a red diamond?

The state is the same — both say the check found a failure. The **consequence** is not:

| mark | means | fails the turn? |
|---|---|---|
| **red square** | a rule measured a failure | **yes** |
| **red square, green centre** | a rule failed it and the judge that refines it cleared the same answer (§3.6) | no — the conviction is withdrawn |
| **red diamond** | a model judged it and flagged something — **or its panel was not unanimous** | **no** — a judge alone never convicts (§7.4) |
| **green square, red centre dot** | the rule passed and the judge that refines it disputed that | **no** — same reason |

A judge's colour is **worst-of its panel**: if any model on the panel voted fail, the mark
is red even where the consensus upheld the answer. That disagreement is real information
and Uderia reports disagreements rather than weighting them away — so where it
happens, the chip says `·split 2/3` and the mark's tooltip spells it out. An abstention
stays dashed even if a vote inside it said fail: a judge that declined to rule is an
absence, not a finding.

This is why the Key draws its *shape* row in red rather than green: green squares and
green diamonds demonstrate the shapes and hide the only thing you need from them.

A red diamond is still worth reading — it is a model saying the answer has a problem no
rule caught. It just is not, by itself, a verdict.

### 4.6 What do the chips on a run row mean — and what is `2/3`?

Each chip carries **one** fact, and appears only when it has something to say.

| chip | means |
|---|---|
| **`withheld · parked`** (with a dot) | a mechanism acted — what was delivered is not what the model wrote. Accent orange when something is owed. Its tooltip names the gate's own judge consultation, where there was one |
| **`gate asked a judge · cleared · 1/1 models`** | a gate consulted **one** judge during the turn, so that ruling could have stopped the answer. Shown only when the gate did not act — otherwise the chip above already says it. The fraction is that judge's **panel**, not a count of judges |
| **`1 judge flagged this`** | the **scoring panel**: one of its judges found a problem. Shown only when something was flagged — a unanimous panel earns no chip, because the diamonds already say a judge ran and agreed. Hover for the composition: *4 judges ran on this turn: 2 upheld the answer, 1 flagged it, 1 abstained*, then each judge and its ruling |
| **`not counted as failed`** / **`passed, but disputed`** | a coarse check and the judge that refines it disagreed (§3.6). The first is why a failing check did not fail the turn; the second is a passing check its judge contested. Named for their effect, so neither borrows another chip's verb |

When a Rule 3.0c mark already accounts for every judge the panel flagged, the tally chip
stays silent — otherwise the same ruling would appear twice, once as a disagreement and
once as a count.

**Two words, two populations.** A **judge** is one *check*, asked once of the turn
(`groundedness`, `content_safety`, …). Its **models** are the panel voting inside it. So
`1 judge flagged this` counts checks, while `1/1 models` counts panel members — and a gate
consults exactly **one** judge, whereas the scoring panel runs every judge that applies.
They were briefly one chip (`gate-judged 2/3`), which read as "the gate's judge said 2 of
3" — something that never happened.

### 4.7 What does "the grounding gate (0 of 1 models upheld it)" mean?

That fraction is the **panel of the one judge the gate consulted** — not a count of judges.
The gate asks exactly one judge; that judge's panel is one or more models, and the number
says how many of them upheld the answer. `0 of 1` means a single-model panel ruled against
it, which is how a withholding gets decided today (see §7.6 on why panel size is the lever
worth pulling).

It is parsed from the judge's own record (`panel 0✓/1✗`). If that record is in a shape the
parser does not recognise, it is shown verbatim rather than dropped — a recorded ruling
must not vanish into a regex.

### 4.8 I acknowledged an item. Where did it go?

Into the **settled** fold at the bottom of the queue — collapsed, not deleted.

The queue lists what is **owed**. An acknowledged notice and a decided item are finished
work, and mixing them with open items makes you re-read the same rows every visit to find
the two that matter. Open the fold and every one of them is there, with its disposition
and its route intact.

Worth knowing: an ack settles the **notice**, and a condition can outlive it (§4.6). That
state lives on the ring, not in the queue — which is why folding the notice away is safe.

### 4.9 What makes a turn count as failed?

A check that a **rule measured** landed in the danger zone — **or** a gate withheld the
answer. Both are failures, for different reasons: one measured, one intervened.

Two things deliberately do **not** convict:

- **A judge alone.** If only an LLM judge found a problem, the turn is flagged, not
  failed — a model opined, no rule measured it.
- **A coarse check the judge corrected.** Where a deterministic check flags something and
  the judge that refines it disagrees, the turn is not a failure and the row carries a
  **`judge-cleared`** mark saying so. A correction hidden is a correction nobody can
  check.

Where both agree — the coarse check *and* its judge — the turn fails. That is the
strongest grounding evidence available, and it used to be scored clean because each
check is individually advisory.

The ledger's `danger` rows and the card's failed count are the same number by
construction: the row reads the verdict rather than computing its own.

### 4.10 An inbox row says "needs you now" but it is already `seen`. What do I do?

`Ack` disposes of the **notice**, never of the **condition**. A station shortfall is
recomputed from the window, so it persists and re-derives after every acknowledgment —
ticking it again achieves nothing.

What is owed there is to go and look at the agent, which is why every row also offers the
place it is **worked**:

- a **turn-scoped** finding (a withheld answer, a redaction) → `Open turn`, its trace
- an **agent-scoped** one (a station shortfall) → `Open @TAG`, the agent's dimension bars

That route is offered whether or not the item is disposed. A row with no affordance at
all read as *"urgent, and there is nothing to do"* — see the contract's Rule 8.5, the
offered act must be the act that is owed.

### 4.11 What does "2 of 18 answers altered or withheld" mean?

That is the **intervention** reading — the mechanism axis of §1.8, beside the quality
rate. It answers the question the pass rate cannot: *how often did a control have to act
on this agent's answers?*

It reports only what happened, so an agent nothing acted on says so plainly rather than
showing a decorative zero. Parked answers and judge escalations appear when they occur.

**"N turns where a guard did not run"** is not an intervention — it is a gap in the
record. A guard can fail open (`action: "error"`), and the honest reading of that is
*unknown*, never *pass*. It is shown on this line rather than in the pass rate, because
a guard that could not run is not a quality failure (§1.7).

### 4.12 My pass rate went UP after an update. Was it wrong before?

Yes, and the correction is the interesting part. `eval.gating_failures` was a **derived
value stored on the span when it was written**. When the gating policy changed, every
already-written span kept convicting under the retired rule — so a sink could hold spans
where a failing advisory check correctly did *not* fail a turn, beside spans where it
did.

One real agent read **44%** (10 of 18 failed) where **3** of those 10 were convicted by
advisory dimensions alone. The failure is now re-derived from the span's own
per-dimension verdicts against the current registry: **61%**, which crosses the amber
floor. Nothing is rewritten — the signed record keeps every attribute it was written
with; only the derived aggregate is recomputed.

### 4.13 What happens when I upvote or downvote an answer?

It reaches the mechanism behind that answer, and **what it does depends on the engine**.
Hover either button — the tooltip states the consequence for that agent before you click.
Optimize promotes or retires the plan it reused; Focus flags or endorses the documents
that served the answer; a coordinator passes the rating to the expert that produced it
(only when a single expert did — a synthesis cannot be attributed); a conversational
agent stops recalling facts it auto-learned from a rejected answer. Every vote is also
recorded beside the quality scores as calibration evidence.

It is reversible, nothing is deleted, your votes are private to you, and it costs no
tokens unless an admin has enabled *judge on downvote*. Full table: operator manual
§3.0d.

### 4.14 Why doesn't my downvote lower the agent's pass rate?

Because it is not a measurement. Intervention, quality and preference are three separate
axes (Rule 3bb.4): the pass rate reports what the **scorers** found, the acted mark
reports what a **control** did, and a vote reports what **you** wanted. A vote is
subjective, self-issued and unverifiable, so folding it into a quality figure would make
that figure mean two things at once and neither reliably.

Look at the **disagreement count** instead: `K disagree with the scorecard` counts turns
where you and the scorers point opposite ways. That is the one signal where a human tells
you the **bands** are wrong for this deployment — the cue to revisit Administration →
App Config → Trust → Quality bands.

### 4.15 The roster shows no user-feedback line at all. Is it broken?

No — nobody has voted on that agent yet. The line is **omitted** rather than rendered as
`0 up · 0 down`, because a zero reads as "people rated this and were neutral", which is a
different fact from "nobody rated it".

You will also see no line when viewing **another user's** agent in system scope. Votes
are scoped to the person who cast them, and showing you a zero for someone else's agent
would be a false absence rather than an honest one.

---

## 5 · The Assurance Guide

### 5.1 What do `escalates to` and `graded by` mean?

- **`escalates to`** — the judges this mechanism **consults in-path**. Decisive.
- **`graded by`** — the checks that **score it afterwards**. Report-only.

And the reverse, on a measurement's own entry:

- **`decides for`** — this measurement *is* a mechanism's decision
- **`grades`** — this measurement scores a mechanism after the fact

These used to sit under a single **"measured by"** heading, which read as an *input* list
— the direct cause of the "measurements drive the gates" misreading.

### 5.2 A station entry shows `members` and a folded `not members · graded by`. What is the difference?

The two populations of §1.1, on the guarantee itself, grouped by **membership** — the cut
the colour rule needs:

- **`members · N — the worst of these colours GROUNDED`** — the mechanisms that hold the
  guarantee up (`upheld by`), limit it (`bounded by`), sign it (`attested by`) or put a fact
  about it on the record (`evidenced by`). The station can never read better than the
  weakest of them. (A station with a single member states its reading in its own words and
  claims no worst-of rule.)
- **`not members · graded by N — JUDGED's members: they colour JUDGED, not GROUNDED`** —
  the **measurements** that grade the same guarantee afterwards, folded apart. They report
  only; nothing there can open or close a gate, and they colour JUDGED.

JUDGED's own entry turns this round: its members *are* the measurements, shelved by the
moment each is taken at (*members · N — the worst grade of these colours JUDGED*). The
dial's station panel uses the same grouping. Every row links, so you can step from a
guarantee into either population and back — a member's own entry names its guarantee in
its kind's verb (`upholds`, `bounds`, `attests`, `evidence for`), a measurement's reads
`grades`.

Until Aug 2026 the station listed its **mechanisms** under `graded by`, which said a
control grades a guarantee (it upholds it), and its real measurements appeared nowhere on
its own entry.

### 5.3 A station lists `upheld by`, `bounded by`, `attested by`, `evidenced by`. Why four words?

Because a station's mechanisms are not all the same kind of thing, and one verb for all of
them would misstate most. The word follows what the mechanism **is**:

| kind | verb | example |
|---|---|---|
| control | **upheld by** | Grounding gate holds GROUNDED up |
| boundary | **bounded by** | Trusted scope limits what counts as in-scope |
| attestation | **attested by** | Agent integrity signs SIGNED |
| measure | **evidenced by** | Recovery is evidence for SIGNED: it records which model answered; it does not hold the signature up |

Four of the eight stations have **no control at all** — SIGNED holds an attestation and two
measures (prompt cache, recovery), TRUSTED a measure (the trailing trust state), PROVEN an
attestation, and JUDGED no mechanism at all (its members are the measurements). Saying
"Recovery upholds SIGNED" would invert the direction the same way "graded by" would (§5.4).

### 5.4 How do I tell an LLM judge from a deterministic check in a list?

By the **`·judge`** tag after the name. Ten of the scored checks are LLM judges; the rest are
deterministic code. The tag appears wherever checks are listed — the Quality card, the
runs ledger, and the Guide's chip rows — so a model's opinion is never presented as a
rule's measurement.

It is deliberately **not** a dashed chip. Dashes belong to the absence family on every
trust surface (`na`, `pending`, `unknown`), so a dashed chip would mean two different
things depending on where you saw it.

### 5.5 How do I tell which judges a gate can consult?

Three places, all reading one declaration (`POINT_JUDGES`):

- **In any list** — a judge carries `·judge`, and one a gate can consult also carries
  **`·in-flight`**. Its entry header reads `LLM judge · also in-flight`.
- **By filter** — the **Judge runs** facet: *Also in-flight* / *After the turn only*. It
  appears only where judges are in scope, and narrows to judges by construction.
- **On the entries** — a mechanism's entry splits `escalates to` (decisive) from
  `graded by` (report-only); a judge's own entry mirrors it with `decides for` / `grades`.

Today that is **3 of 10**: `groundedness` and `scope_faithfulness` on the grounding gate,
which consults **one of them** per turn (by which trigger family fired), and
`grain_faithfulness` on the honesty gate — only when the agent opts in
(`honestyConfig.grainJudge`, the Honesty card's *Confirm with the Grain Faithfulness judge*).

**Only in-flight is marked**, because *after the turn* is the constant: all ten judges are
registered scored dimensions, so every one of them produces a verdict once the answer has
shipped. Marking that on all ten would be weight without information. (The reading is
still derived from two independent facts — wired to a gate, and registered as a scorer — so
a judge that were wired but never scored would read `in-flight only` rather than being
mis-filed.)

Worth knowing, because it is what this answers: a judge being **owned by** GROUNDED or
GUARDED does *not* mean a gate consults it. Ownership says *this judge measures whether
that guarantee held*; escalation says *this judge can act for it*. Several judges are owned
by those two stations and only three are wired to a gate — the guardrail gates are
deterministic and import no judge at all, and the honesty gate consults only its opt-in
grain judge (§7.5).

### 5.6 Evaluation shows `counts`, not `graded by`. Why the different word?

Because the direction is opposite. `graded by` names the dimensions that **assess** a
mechanism. Evaluation is not assessed by its checks — it is the layer that **produces**
them; its own entry says *"Never — it grades, it never acts."* Labelling its output
"graded by" would say the checks grade the thing that computes them.

`counts` lists the **moments** — Plan, Retrieve, Act, Compose, Deliver, The run, Flow
run. Those are what JUDGED holds: every check the turn produced, grouped by when in the
turn it was taken. Adding a scorer places it in a moment automatically (a scorer with no
moment fails a CI gate rather than landing in a default).

### 5.7 Why does an entry have no `grades` row at all?

Because **not every measurement measures a mechanism.** Some measure the *execution
itself*, and nothing in the framework guarantees them — `self_correction`, `cost`,
`latency`, `tokens`, `completed`. Nothing runs a gate promising "few self-corrections".

Those have nothing to point at, so they carry no `grades` row. They are still counted at
JUDGED and still sit in a moment — most of them in **The run**. It is correct, not a gap,
and the entry says so.

### 5.8 The Guide says 9 checks; the Scorecard's station says 7. Why?

Different questions, both honest:

- **9** = what that **one mechanism** owns, whenever those checks apply
- **7** = what actually **ran on this turn**, across **all** of that station's mechanisms

So the second includes checks belonging to sibling mechanisms and excludes judge
dimensions that were not sampled.

---

---

# Part C — Behaviour and configuration

*What the settings actually do, and what changing one costs.*

### 5.9 What is a **Property**, and how is it different from a Measure or a Control?

A property is **what is actually being assessed** — the thing a guarantee promises, a control
upholds and a measurement grades. It is the layer underneath the other three.

`Answer support` is a property: *is every specific claim in the answer supported by the sources
it was given?* The grounding gate is a **control** that acts on it during the turn;
`Grounding Overlap` is a **measure** that grades it afterwards. One question, three entries, and
the property is the one that stays put — the modules bound to it are configuration.

### 5.10 A property says `assessed by · During the turn · After the turn` with the same module twice. Is that a duplicate?

No — it is the strongest thing the entry can tell you. The same module serves both lanes, so the
control acting in flight and the measurement grading afterwards **can never disagree about what
was found**. Where two different modules appear, they are two readings of one property and the
entry says so. Most of the twelve properties share one module today.

### 5.11 A property shows no `graded by` route. Is something missing?

Depends which one, and the entry tells you.

`Request integrity` is assessed in flight and **deliberately never scored**: whether a blocked
request was truly an attack is not decidable from the turn, so the detector is measured against a
curated corpus instead (the resilience self-test) — a reading about the *detector*, not about
that turn. That is a finished design, and the entry states the reason rather than showing an
empty row.

Anything else with no route reads **"Nothing grades this yet"** — a gap, said in those words. A
reader who cannot tell a finished design from an unfinished one has been told nothing.

### 5.12 I expanded a mechanism row and it says `nothing bound`. Is that broken?

It is working. `nothing bound` means **no module is assessing that property at all** — which is
exactly the reading that must never be invisible. A row that vanished would be
indistinguishable from one that passed.

Likewise `1 module, none ran` (dashed and dimmed) means a module is bound but had no input this
turn — an abstention, not a pass. The row says what it needed: *"needs `scope_databases`, which
this turn did not provide."*

### 5.13 What does the small **M** badge mean, and where does its arrow go?

**M marks a module** — the code that actually performs an assessment. It is deliberately not a
link to a page of its own, because a module has none: what it *has* is a property it assesses,
and clicking the module name opens that.

Modules used to render as bare text, indistinguishable from an entry you could open. Worse, they
appeared on the property's entry only — so on a measurement or a control the **S** badge asserted
a shared assessment the entry never named, and hovering was the only way to learn which. A
measure now carries a `reads` row naming its module. A control names what it is about instead —
its `properties`, each opening its own entry. Each property's module sits where it is used: under
*modules during the turn* when the control consults it in flight, under *after the turn* (above
*graded by*) when only a measure uses it. An **S** on a chip is the other end of an **M**: the two
use one module. Pointing at either lights the other; clicking the letter opens the fold that holds
its partner. Every S has its M in the same entry. A gate's *graded by* lists only the measures that use its
module; a measure that only asks its guarantee's question (Numeric Fidelity under GROUNDED, say)
is listed on the guarantee and counted at JUDGED — never on a gate whose code it never touches.

Hovering either end lights the other — that is what carries the direction. An arrow was tried and
removed: once modules and siblings sat in separate rows, a glyph between two chips read as
*points at the next one*, a chain that does not exist.

### 5.14 What does the small **S** badge on a chip mean?

**Same assessment.** The two entries read the *same module*, so they can never disagree about what
was found.

`graded by` on its own says a measurement grades this property — it does not say the two are the
same computation, and that difference is the whole point of the assurance substrate. Where a
control acts in flight on one assessment and a measurement grades that *same* assessment
afterwards, the pair is corroboration; where they are two separate computations, agreement is
only coincidence.

It marks **peers only**, at **both ends** of a mechanism ↔ measure link: on the measurement and
on the control. A one-way badge would be a dead end one hop later — if Grounding Overlap says
*same assessment as the Grounding gate*, the gate has to say it back. It is **never** on a link to
a property: a property's mechanism and its measures read its module by definition, so the badge
would be true of every chip there and say nothing.

That is also why a control's `graded by` row is short. A gate's `graded by` lists only the
measures that use its module — three for the grounding gate. `Retrieval Relevance` is not one of
them — its module belongs to the honesty gate, so it is listed there; its agreement with the
grounding gate's guarantee is independent evidence, and it is counted at JUDGED under GROUNDED's
*not members* fold. Hovering any badge names the module (the S's own tooltip speaks alone; the
entry card returns when the pointer is on the name).

## 6 · Gates and modes

### 6.1 In `observe`, do the mechanisms still run?

**Yes — fully. They detect; they just do not act.**

| | observe | enforce |
|---|---|---|
| Deterministic detection | ✅ in full | ✅ |
| Second-chance retrieval | ✅ | ✅ |
| **LLM judge** | ❌ **never — 0 tokens** | ✅ on flagged turns |
| Effect on the answer | ❌ none | ✅ |
| Verdict recorded and shown | ✅ | ✅ |

Everything up to the decision is computed identically. Only the decision is suppressed,
and the judge is structurally unreachable — which is what makes `observe` free and safe to
leave on.

### 6.2 Can dots appear in `observe`?

The gate's **own** dot: no — it cannot act.

But other mechanisms at the same station have **their own switches** and can still act:
corpus quarantine, the sensitivity ceiling, the retrieval firewall, the honesty gate. So
*"grounding is on observe"* does not mean *"nothing at GROUNDED can act"*.

### 6.3 Where does the colour come from in `observe`?

**The rule never changes** (§2.1): a guarantee station is the worst of its members, JUDGED the
worst of the measurements. In practice, in `observe` a gate's own row reads pass — its state is
derived from its *decision*, and `observe` never decides anything but pass — so what you see
lighting up is JUDGED (the measurements), the station's other members, and any open inbox item.
Two exceptions say what observe *found* rather than hiding it: a guard that caught something
reads **worth a look** on the dial (*caught pii — recorded, not altered (observe mode)*), and a
knowledge-grounding check names its *unsupported claims recorded, not acted on (observe mode)*.

On the **Trust Circle** (window altitude) the posture itself is a reading: a gate set to
*watch* makes its station read **watching** — quiet green, hollow, never amber — and the
summary line names the watching stations. The dial (turn altitude) never shows `watching`: a
turn has events, not postures.

### 6.4 Can I read `observe` findings as "what enforce would block"?

**No — read them as "what would have been sent to a judge."**

The deterministic trigger is tuned for **recall**, not precision. It deliberately
over-flags, and the judge is what decides. A turn flagged in observe is not necessarily a
turn enforce would withhold.

To size the real block rate: run `observe` for a while, then compare against a short
`enforce` window.

### 6.5 What is "Inherit"?

Not a fourth mode. It means the setting is **omitted**, so the gate resolves through
*gate block → agent posture → observe*. The profile editor's live summary names what it
resolves to (*"inherits → enforce"* vs *"overrides: watch"*), so you never have to guess.

### 6.6 What actually withholds an answer?

Two independent layers must agree:

1. **A deterministic check** (0 tokens) extracts the answer's checkable claims —
   **numbers**, **verbatim quotes ≥15 chars**, and **identifiers** (dotted / backticked /
   Capitalized Multi-Word names present in neither the documents nor the question, with a
   floor of 3 so one unusual name never trips it) — and reports which the corpus does not
   support.
2. Before flagging, a **second-chance retrieval** re-queries the corpus for the missed
   spans. A claim the first retrieval merely *missed* is **cleared, not flagged**.
3. What survives escalates to an **LLM judge panel**, which makes the real call.

In `review` mode the original is also **parked in the Assurance Inbox** — withheld, but
nothing lost.

---

## 7 · Judges

### 7.1 There are two things called "judge". What is the difference?

| | **In-path judge** | **Evaluation judge** |
|---|---|---|
| Who asks | the grounding gate, when its trigger fires | the scoring layer, after the turn |
| Effect | **decides** whether the answer ships | measures only |
| Guide row | `escalates to` / `decides for` | `graded by` / `grades` |

They can be the *same class* (`groundedness`) in two roles. The Assurance Guide separates
them so the causal direction is never lost.

A third thing is **not** a judge, and the labels keep it apart: a judge is one **check**
asked once of the turn; the **models** on its panel vote inside it. So `1 judge flagged
this` counts checks and `2/3 models` counts panel members — see §4.6.

### 7.2 When do the evaluation judges run?

Deterministic scoring is **unconditional and free** — every turn. Judges cost tokens, so
they run only when:

- a deterministic check **flagged** the turn (a failed check or a danger band), or
- the run is **anomalous** (errors, self-corrections), or
- it is **sampled in** (`judge_sample_rate`).

With the rate at `0.0` the policy is *"judges only when something is wrong"* — which is
the setting most deployments want.

### 7.3 Do judges clear measurements?

**No — with one declared exception**, and it is worth knowing exactly where the line is.

A judge never rewrites a measurement. A check that measured 0.46 still reads 0.46 and
still shows red on its chip. And a judge's own verdict **never fails a turn on its own**
(§4.6): a model opined, no rule measured it.

The single exception is one declared pair:

```python
REFINEMENT_PAIRS = { "grounded_overlap": "groundedness" }   # one entry, that is all
```

Those two are not a measurement and a judge *of* it — they are **two measures of the same
property** (is the answer supported by its sources): one lexical, one by reading meaning.
When the coarse one flags and the one that exists to refine it disagrees, the
**conviction** is withdrawn — the measurement stands, JUDGED still goes amber, and
the row carries a **`judge-cleared`** mark saying so (§3.6).

Nothing else can produce that mark. No other judge, no gate outcome, no verdict.

### 7.4 So a judge can never make a turn fail?

Correct, and the mirror case is worth knowing because it looks like an inconsistency and
is not:

| | JUDGED | turn |
|---|---|---|
| coarse check flags, judge clears | amber | **passes** — conviction withdrawn |
| coarse check clean, judge flags | **red** | **passes** — a judge alone does not convict |

**JUDGED reports everything; the turn verdict convicts only on rules.** So the same
declared pair can *withdraw* a conviction and cannot *create* one.

That asymmetry is deliberate. Clearing is the safe direction — a judge overturning a
coarse lexical proxy is the judge doing its documented job. Convicting is the risky one:
it would let a *sampled, stochastic* check fail a turn, so a turn scored without a judge
would be held to a different standard than one that was.

### 7.5 Why are almost all judges "sampled after the turn"?

Because that is the architecture, and it surprises most people the first time they see it.

| | runs | can it change the answer? |
|---|---|---|
| the **grounding gate's** escalation judge | **during** the turn, when its trigger fires | **yes** — it can withhold the answer |
| the **honesty gate's** grain judge (opt-in per agent) | **during** the turn, on the heuristic grain-substitution trigger | **yes, one way** — a PASS withdraws the appended note; it never withholds |
| every other judge | **after** the answer is delivered, as a background task | no — measurement only |

On one real agent that is **4 judge marks during the turn, 48 after**.

It is deliberate. A judge call takes seconds, so running the panel in-path would add that
to every turn; it would multiply cost on turns nothing is wrong with; and an inline judge
once froze the server when a provider hung, which is why the scoring hook is
fire-and-forget with a timeout today. And a judge that runs in-path but is not wired to a
gate is just a slow measurement — it changes nothing.

**If you want a judge to have teeth, the path exists**: wire it to a gate, the way the
grounding gate escalates to `groundedness` / `scope_faithfulness`. That is a deliberate
decision per judge, with a latency and cost budget attached — not a default.

This is the mechanism-vs-measurement split (§1.1) at its sharpest: nearly every judge
verdict on the roster is a **grade**, not a **control**.

### 7.6 The judge flagged something that looks wrong. Now what?

It happens, and the framework is built so that it costs you nothing when it does — this is
exactly why a judge cannot fail a turn alone (§7.4).

A real example from this deployment: a judge ruled an answer had cited a document that
"is not present in the provided retrieved documents". That document exists, and the title
the answer used is its literal heading. The panel was **one model**, scoring exactly
**0.5** — the boundary. The turn correctly read `review`, not `danger`.

Two things to check when you see one:

- **Panel size.** A one-model panel with a tiebreak score is thin evidence. Consensus
  across several model families is what makes a judge worth listening to.
- **What it was shown.** A judge grades against the *retrieved chunks*, not your whole
  corpus. "Not in the provided material" can be a retrieval finding wearing a
  groundedness finding's clothes.

### 7.7 The Live Status trace shows a judge step. Which layer was it?

The step says so, in its name and in the card:

| step | card's **When** row |
|---|---|
| **Gate Consulted a Judge** | *during the turn — a gate consulted it; its ruling could withhold the answer* |
| **Agent Quality Judged** | *after the turn — measurement only; it changed nothing that shipped* |

Both ride one event type, because both spend tokens through the same accounting sink —
which is what makes the per-event costs sum to the turn total. The layer is declared by
whichever path spent it, never inferred, and it survives a plan reload.

A judge step from before this existed carries no layer and shows **no** When row rather
than guessing one.

### 7.8 I expected no judges but my turn was judged twice. Why?

Two different layers, both legitimate:

1. the **grounding gate's escalation judge** — trigger-gated; it ran because the gate had
   something to adjudicate (it may well have blocked the answer)
2. the **evaluation panel** — measurement only

Both are visible as their own step with their own cost. If you see the panel running on
clean turns, the admin `judge_sample_rate` is above 0.

### 7.9 The judge step shows a cost but no ruling. What happened?

The panel spent tokens and did not return a verdict in time, so the step reads
**`LLM judge panel — no ruling`** and says so where the verdicts would be. The spend is
real and is shown deliberately — the alternative is a bill you cannot see.

Judges that *did* finish are kept; each judge the cut-off silenced is recorded as an
abstention naming the cut-off, so you can tell "this dimension does not apply here" from
"we tried and lost it". If you see this repeatedly, one panel member is probably not
responding — check the Live Status timings and consider excluding that model
(§7.10).

### 7.10 Can I stop a particular model from judging?

Yes — **Anatomy → LLMs → edit the configuration → *Judging role***. Switch it off and that
model keeps answering exactly as before but no longer judges. Its card in the LLM list
then shows a muted **Not a judge** badge.

Note the scope: **one panel serves both judge layers**, so this removes the model from the
judge a gate escalates to **in flight** (where a ruling can withhold an answer) as well as
from the panel that **scores quality after the turn**. It is not a measurement-only switch.

Why you might: the panel waits for **every** member on **every** judged turn, so a model
that habitually times out contributes no vote while setting the pace for the whole
evaluation. Excluding it makes judging faster and cheaper and loses nothing.

Every model participates unless you say otherwise, and the switch is per configuration —
another configuration of the same provider can still be selected. If you exclude them
all, the panel is empty and judges simply abstain.

---

# Part D — Troubleshooting

*Readings that look wrong and are not — and the one that is.*

## 8 · Signed data products and signed actions

Data products govern what an agent may **read** (8.1–8.8); signed actions govern what it may
**write** (8.9–8.16). They are built on one pattern — a contract on the ontology, a gate that
proves it live, a signature with the proof inside, a receipt for every use.

### 8.1 What is the "Data products" row in the answer capsule?

The consumption receipt. When an answer's figures came through a signed data product,
the capsule shows one chip per product — `fitness_sales v1 · trusted · 32 rows` — with
the request hash, the compiled-SQL hash, and the trust state *at the moment of
consumption* in the tooltip. The same receipt is sealed into the turn's Ed25519
provenance chain (step type `obda.consumption`), so "Export receipt" hands you a
record you can verify offline — and verification re-derives the receipt from the
turn's own displayed record, so the seal and the display can never quietly disagree.

### 8.2 The Governed-Data note says the lane is "confined". Is that bad?

No — it is the protection *working*. `confined` means every raw-SQL attempt this turn
was **denied** by the agent's data surface and **nothing executed**: the agent is
surface-only and something (usually a lure, sometimes a product gap) tried to go
around the products. A denied call is deliberately counted in *neither* lane — calling
it "free-formed" would record the confinement working as the escape it prevented, and
the quality scorer would punish the agent for being protected. GROUNDED reads it as a
pass: *"nothing executed outside it."*

### 8.3 What is a "Surface breach" in the Assurance Inbox?

The one thing that should never happen: a surface-only agent **executed** free-formed
SQL. Structural confinement (the E1 denial at every tool chokepoint) should have made
that impossible, so a breach is recorded as a DANGER incident whose summary says it
plainly — *"a hole report, not agent misbehaviour."* Treat it as a defect report about
Uderia's own enforcement, not as a reason to distrust the agent. It fires only
on a real breach; advisory mode, governed turns, and interactive turns are all silent.

### 8.4 A product shows "validated" but "blocked". How can both be true?

"Validated" says the V1–V7 gate *ran* for this exact manifest; "blocked" says it found
blockers — open the checklist and each failing check names its reason (a grain-unsafe
pair names its 1:N hop; a live probe that contradicted a declaration names the
declaration). Fix the manifest, re-validate, and the chips move together.

### 8.5 What does the Verification selector change about the signature?

The evidence tier. *Structural* proves everything provable without a connection and
the report says so honestly; *Live* additionally executes the battery at the real
target and empirically spot-checks every declared cardinality and key. The mode is
**part of what gets signed** (it changes the report hash), so a signature backed by
live probes is distinguishable from one that trusted the declarations — and a lie in
the manifest cannot be signed over when a connection is available.

### 8.6 What does "Signed knowledge only" do, and what if nothing is signed?

It restricts the agent's retrieval to collections whose integrity signature is
signed-and-intact — a `changed` or `unsigned` corpus never backs its answers. If the
trust suite is physically absent (nothing *can* be signed), the toggle fails **open**
with a loud warning instead of silently emptying the knowledge lane; one unresolvable
collection beside signed ones is simply excluded.

### 8.7 How do I control which data products an agent can plan with?

The **Data surface** on the profile's Agent Authority card — a three-option choice:

- **Disable OBDA-READ** (the default): no products are visible to the planner at all — no
  contracts in the enrichment, `query_product` refuses, no governed-lane behaviour.
  Every unconfigured profile reads as this, and an **empty product list saves as
  this** (the editor never shows a mode that does not act).
- **Surface Full**: ONLY the listed products; when the agent runs unattended its raw
  SQL is denied fail-closed — the surface is its entire data authority. *Advisory*
  is this mode's observe-only development sub-toggle.
- **Products + regular paths**: the listed products are offered as contracts
  *beside* the ordinary KG paths and the model decides per question. The list is
  still the grant — a product not on it is refused by name.

Two things override all three: the **OBDA-READ component** (Administration → Components
for the deployment and the person; the agent's own Capabilities toggle — there is no master
switch, the plane *is* the component) — off means the whole family is deactivated for that
agent and Uderia behaves as if OBDA-READ were not installed (the surface block says
so and names the component); and product **trust** — only signed-and-intact products are
ever served. A third control sits beside availability without replacing it: the deployment's
**OBDA policy** (Administration → App Config → Trust → *Enforcement* → *OBDA policy*, `off · observe · enforce`) — under
`enforce` every agent reads data only through its signed products ("contracts only"); under
`observe` the same would-be refusals are recorded in the Assurance Inbox instead.

### 8.8 The answer carries a note about a "substituted grain". What is that?

The honesty gate caught the agent answering a *different question at a coarser
grain* — e.g. "which customer has the most tickets?" answered per **state** because
the product only exposes state-level counts. The note is **appended, never
replacing the answer**: the state-level figures may still be useful, but the reader
must know they are not the per-customer answer that was asked for. It fires two
ways: deterministically, when a product REJECT was followed by success at a
different grain; and heuristically, when a ranking question names an entity whose
queried dimensions never appear in the answer. An optional LLM judge
(`honestyConfig.grainJudge`, off by default, a checkbox on the profile's Honesty
card) can adjudicate the heuristic form — a judge PASS withdraws the note and the
withdrawal is recorded.

### 8.9 The agent answered with a Confirm button instead of doing what I asked. Why?

Because a write in a conversation is always **prepared**, never performed directly. The answer
row shows exactly what would change — `status open → closed` on the keyed row, the row count
against the action's bound, every check — and nothing has been written yet. **Confirm**
performs that invocation and only that one: the confirm carries the hash of what you saw, so a
different value set is refused with nothing written. The row then flips in place to
*Done · 1 row · receipt*, with **Undo** and **Export receipt**. A double click, a second tab or
a replayed click reads *already confirmed* — one confirmation per invocation, decided on the
server.

Unattended doors — a flow node, a scheduled task, a dashboard tile — have nobody to confirm, so
they never prepare: the action's signed **posture** decides (write, park for approval, or only
record what it would have written).

### 8.10 The row says "Sent to the owner · parked for review". Nothing was written?

Correct — nothing was written. The action type's signed posture is **review**: Uderia ran
the write in a transaction that is always rolled back, so the diff you see is the database's own
answer, and parked the write in the Assurance Inbox. It is written only when the person
entitled to decide clicks **Approve — perform it** there (or in their own agent's answer). Even
then it writes only if the contract, the re-compiled statement and the invocation are all
unchanged since it was parked, and the agent is still granted the action; otherwise the approval
is refused and the item stays open. A rejection writes nothing, and the original answer row
reads *refused* after a reload — it never reads "parked" forever.

### 8.11 I own the agent. Why can I not approve its parked write?

Because the agent names **approvers** (profile editor → Agent Authority → *Approvers of parked
writes*). With approvers set, only they decide — the owner is refused (`403 not_your_decision`)
before anything runs. That is the point of four eyes: the person whose agent asked for the
write is not the person who approves it. The approver sees the item in their own Inbox and can
ask their own agent ("what closures await my approval?") — the answer lists each write with
Approve / Reject buttons *they* click; no agent ever approves. With no approvers set, the owner
decides.

### 8.12 The agent refused: "action-only". What does that mean?

The agent tried to write through its data tool with a raw `INSERT`/`UPDATE`, and its authority
is set so that **signed actions are the only way it may write**. The refusal names the actions
it *is* granted. It is the write-side twin of a data product's `surface-only` refusal, decided
at the same chokepoint. If no granted action covers the change, the right answer is a new
action type — designed, proven and signed — not a wider grant.

### 8.13 "Nothing to write — the row already holds these values". Is that an error?

No. The update would change nothing: every keyed row already holds every value it would write
(read from the database's before-image, never from the request). Asking a person to confirm or
approve nothing would waste their attention, so it is refused at preparation and at the park. An
`enforce` action running unattended still performs it — the receipt records the act.

### 8.14 What does BOUNDED's "acted · refused" reading count?

The acts the turn's agent put through signed actions (performed, parked or observed) and the
ones it was refused — each refusal named. It sits at BOUNDED because a grant is a boundary: the
reading says the agent stayed inside it. The station's evidence also names the actions acted on
in recent sessions. **REVIEWED** reads an agent with a granted action in `review` posture as able
to park, even if all its gates only observe.

### 8.15 A model extension reads "changed · base moved". Did someone edit my extension?

Not necessarily. An extension's signature carries the **base ontology's** signature inside it,
so when the base is re-signed (a new release of the model it extends), the extension honestly
reads *changed* — the mover is named: *base moved* or *extension edited*. The base's own seal
stays green; extensions never move it. Re-sign the extension once you have checked it still
fits the new base.

### 8.16 Where do I see every act an agent performed, whatever door it came through?

Trust History (the agent's detail in the Trust view) and the signed audit pack. Acts arrive
through many doors — the conversation, REST, MCP, a flow, a schedule, a dashboard tile, an
Inbox approval — and each writes one row into one append-only act log, with the door named and,
for an approval, who decided. Every performed act also has its own receipt, verifiable offline
with the deployment's public key alone.

## 9 · Shared repositories

### 9.1 My subscribed planner repository shows the publisher's collection id. Is that a bug?

No. A subscription to a repository is a **view** of the publisher's collection, not a copy
(a fork is the copy). There is no second collection: your card shows the publisher's id and
store, and your subscription row keys it to your own MCP server so it *loads* for your
sessions. You never write into it; your agents' learning lands in your own default planner
for that server, and the agent reads that repository back on its next question alongside
the view — so a subscribed agent still learns from your use, privately. The pack chip on it names your own pack subscription (once — a reference is
one collection id named by the publisher's ledger and by every subscriber's, and the card
lists only yours).

### 9.2 Why is my subscribed repository not Locked? I cannot write to it either way.

Because *Subscribed* and *Locked* answer different questions. *Subscribed* says **you**
cannot write here. *Locked* is the **publisher's** opt-in: it stops the publisher's own
learning loop from adding cases and widens the seal to every case. The Locked pill on your
card reports the publisher's decision; it appears only when they set it. Keying the
repository to your own MCP server changes nothing about this — it decides where the
repository loads, not who may write.

### 9.3 The publisher's repository gained cases. Why is nothing red?

Because the seal covers the **shipped** cases, not what the repository learned since. A case
the publisher's agent seeded after a successful turn, a hand-added case, a vote, a re-elected
champion — none of these are in the hash, so the publisher's planner, their agent, and your
agent (which hashes the same collection) all stay trusted. You inherit the new case on your
next query. The seal moves only when a **shipped** case changes — a pack rebuild, an edit or
a removal — and then it moves for everyone at once: the publisher re-signs and republishes,
and the pack sync fan-out reinstates your agent. Until that republish your agent reads
`changed` and there is nothing on your side to do; the content is the publisher's.

### 9.4 Why can I not unsubscribe the planner on its own card?

Because your pack subscription made that reference and will remove it. The card says
*Removed with the pack*, and the endpoint refuses a lone unsubscribe with
`409 managed_by_agent_pack`. Unsubscribe from the pack on its marketplace card instead. A
repository you subscribed to directly on the collection marketplace still offers Unsubscribe.

## 10 · Evidence and offline verification

Every answer records what it was made with, sealed into its signed chain; signatures are kept
as history; a hash-chained ledger makes later edits visible. The manual's §2.14 and §3.5b
describe the machinery and the workflow; these are the readings people ask about.

### 10.1 What is the Evidence row under an answer?

The list of everything the answer was made with — the agent, its ontology, skills and
repositories, the model and tool catalog, the data products it read, the writes it performed —
each with two readings in a fixed order: **when answered · now**. *When answered* comes only
from the answer's own sealed record, so it cannot be rewritten later; *now* is read live. The
collapsed row names only the exceptions (*"1 changed since: Fitness ontology"*); the norm is not
marked.

### 10.2 A part reads "changed since". Is the answer wrong?

No — it says the part has moved **after** the answer was given, nothing about the answer itself.
The answer was made with the version that was signed at the time, and that version's
signature is still kept (re-signing adds a record, it never replaces one). What it does tell you:
asking the same question now may not reproduce this answer. To see every answer that used the
older version, open the part's trust badge: *Used by N answers · M with this version*.

### 10.3 "parts not recorded — this answer predates evidence recording". Can that be recovered?

No. The parts are recorded at the moment of answering; an answer given before evidence
recording existed never captured them, and working them out afterwards would be a guess
dressed as evidence. Its own signed chain still verifies; only the list of parts is missing.

### 10.4 "session deleted — content not retained". Where did the answer go?

The session was deleted, so the question and answer text went with it. A content-free record
of the answer — which parts, which hashes, which signatures — is kept on purpose, so the audit
history has no unexplained gaps. It proves the answer happened and what it was made with,
without keeping what it said.

### 10.5 Why does Verify run in my browser instead of asking the server?

So that a checkmark means *your browser checked this*, not *the server says it is fine*. The
button exports the same file an auditor would receive and checks it with your browser's own
Ed25519 support — the same rules as the offline verifier page and `scripts/verify_evidence.py`,
which reach the same verdict on every case.

### 10.6 The verifier says the signer is "not confirmed". What is missing?

The deployment's public-key fingerprint. A file carries the public keys it was signed with,
so it can always prove it agrees with itself; it cannot prove **who** signed it, because a
forger would carry their own key. Get the fingerprint through a separate channel (the operator,
or `GET /api/v1/provenance/public-key` on the deployment itself) and enter it: then the
verifier confirms the signer, or says the file was signed by a different key.

### 10.7 A signature reads "content not included". Can it still be verified?

The signature can — the record of what was signed is in the file. What cannot be re-checked
offline is the content itself, and the entry says why: it is larger than the bundle limit
(`TDA_EVIDENCE_CONTENT_LIMIT_BYTES`, 256 KB by default — a big ontology), it has changed since it
was signed (its signed bytes are no longer held), or the export was made without content.
For the first case, export again through `POST /api/v1/evidence/export` with `"content": "all"`; the buttons in the app use the limit.

### 10.8 What does "made with a retired key" mean?

The signature was made with a provenance key this deployment has since replaced. It still
verifies — every key that has signed here is kept — but if you rotated the key because the old
one was exposed, that is the list of signatures worth re-making.

### 10.9 The ledger chip reads "ledger NOT intact". What now?

Something in the trust history was edited, inserted or removed after it was written, outside
Uderia's own paths: a database edit, a restore from an old backup, a pruning script.
The chip names how many records and of which kind; an administrator sees which ones
(`GET /api/v1/evidence/ledger`). Treat it as an incident: the signatures themselves may still
verify, but the history around them is no longer complete.

### 10.10 The ledger chip is amber: "the last anchor failed". Is the ledger broken?

No. The ledger itself is intact; what failed is publishing its latest checkpoint outside this
host (the anchoring target was unreachable, refused, or the checkpoint could not be signed).
The chip quotes the error. Nothing is lost — the next successful publish covers everything
since — but until it succeeds, the window since the last published checkpoint is protected
only by the host.

### 10.11 Can the server's administrator still rewrite history?

With the default setting, yes: the signing key is a file on the server, so whoever controls the
server could rewrite records and re-sign them. What they cannot do is make the rewritten history
contain a ledger checkpoint someone exported earlier — that shows. Two opt-in settings close
the rest: holding the key outside the host (`TDA_EVIDENCE_SIGNER=command`, an HSM, KMS or Vault
the server can ask but not read) and publishing checkpoints outside it (`TDA_EVIDENCE_ANCHOR`).
The ledger chip's tooltip and every exported file say which of these is in force.

### 10.12 The verifier says "the answer text does not match what was sealed". Was it forged?

The answer's chain is intact, but the question or answer text in the file is not the text the
chain sealed: someone or something changed it after export (an edited file, a re-encoding that
altered characters). Re-export the answer from the app and compare. A different reading,
*"the answer text is NOT checked"*, is not a failure: the answer comes from an older record
whose chain does not cover its text, so the verifier cannot vouch for it either way, and says
so rather than calling it verified. The chain seals the first 4,096 characters of each text;
for a longer answer the verifier says only that part is checked.

---

## 11 · Looks like a bug, and is not

| What you see | Why |
|---|---|
| A **red** station after the gate correctly blocked a fabrication | Colour is attention priority. A parked answer needs your decision. |
| **Green GROUNDED** beside a failed grounding check | Two questions. GROUNDED reports what its gates did during the turn; the check grades the finished answer and is counted at JUDGED. |
| **"Passing"** headline with a red chip | The red chip is advisory; it flags without convicting. |
| A grey chip showing a **real number** | "Not set" — it measured, but no threshold is configured. |
| Grounding Overlap **abstaining** on a blocked turn | The delivered text is Uderia's notice, not a model claim. Grading it would count one shortfall twice. |
| **JUDGED red with no dot** | A measurement graded the answer poorly; no mechanism acted. A *guarantee* station red with no dot carries an open inbox item (§2.5). |
| The grounding card names an **unsupported claim** but the gate did not act | A single novel identifier never triggers alone — three distinct ones are required, so extraction noise cannot withhold an answer. The headline reports the **finding**; the decision reports what was **done** about it. |
| An **amber** square that looks red | In the light theme amber and red sit close (and are the same colour under deuteranopia). Read the **shape**: half-filled = review, ring = danger. |
| A **blocked** turn also counted as a quality failure | The one deliberate crossing of the two axes — §1.7. A withheld answer must not read clean. |
| **Two heights** in the sparkline | Not magnitude — pass is full, fail is half, so the state is legible without colour (§4.2). |
| A **freshly forked** pack reading `— · not attested here` | The publisher's signature travelled and is intact; what has not happened is **this** instance attesting its own resolved model, tool catalog and connector opt-ins — which deliberately never travel, because you run your own. Click **Attest this deployment** (the owner's act, any tier). |

---

## 12 · Known open items

**A scope-family gate in `observe` that detected drift still reports "answer stays inside
the declared scope."** The mechanism's row derives its state from its *decision*, and
`observe` never decides anything but pass — so the detection is recorded in the trace, and
where out-of-scope databases were found the row lists them beneath a headline saying the
scope held. The knowledge-grounding (corpus) family no longer does this: it reads *N
unsupported claims recorded, not acted on (observe mode)*, and an observe-mode guard catch
reads *worth a look*.

This matters most for the decision the row exists to support: *is it safe to switch this
agent to enforce?* Until the scope family is fixed too, read the **measurements** beside it
and the out-of-scope list under the row, not the row's headline, when running in observe.

**A guard that ERRORED is reported apart, but only in aggregate.** Three of one agent's
turns recorded `error` on the honesty and guardrail gates — the guard did not run at all.
The roster's intervention reading counts them (*"N turns where a guard did not run"*), and
the per-turn instrument shows `unknown` rather than a pass, but there is no mark for it on
the run row: an errored guard is deliberately **not** an intervention (calling it one would
report the absence of a control as the control working), and it is not a measurement
either, so it currently has no home in the strip.

**The judge exception has no size ceiling.** `REFINEMENT_PAIRS` (§7.3) is the only way a
model's opinion can change a turn's verdict, and it is declared data — a second pair is one
line, and the gate checks that each pair is well-formed but not how many there are. Adding
one should be a reviewed decision, not an edit.

---

## 13 · The answer, the header and a failed turn

### 13.1 What do the words in the header's status strip mean?

The strip leads with one verdict word, built from the six connection lights (MCP, LLM, context,
knowledge, CCR, the live stream). Click the dot or the word to see the lights themselves.

| Word | Means |
|---|---|
| **Signed out** | no session — nothing to report, so the dot is hollow and opens nothing |
| **Connecting** | signed in, the live stream has not reported yet |
| **Ready** | connected and idle |
| **Working** | some subsystem is busy (amber, with a ripple) — takes priority over everything below |
| **Reconnecting** | the live stream dropped (a proxy timeout during a restart, a network blip) and is coming back on its own, with a 2 s → 30 s backoff — amber, but not work |
| **Offline** | the stream itself failed |

A page that has just loaded never says *Offline*: a stream that has not started yet is not an
outage.

### 13.2 Where did the Grounding, Guardrail and Provenance rows go from *Why trust this answer?*

Into the **assurance dial** above it, whose stations say them in the same words. Where the dial
renders, the capsule says each fact once: the dial carries the verdicts, and the capsule keeps a
single folded **Made from** list — its closed line names what it holds (*2 repositories · 5
documents · evidence · sealed chain*) — in three groups:

- **Drew on** — the scope, the sources (grouped by repository, one signature pill per
  repository, each document once), attachments, the data lane and data products
- **Did** — the signed actions the answer performed, and how each ended (only when there were any)
- **To verify** — the evidence, the kept drafts, the sealed chain and the one-file export

Without the dial (an older turn, the legacy strip) the reading rows stay, since nothing else
carries them.

### 13.3 The answer says "The language model provider did not answer". What happened?

The provider failed the call — no credits, rejected credentials, a rate limit, an outage, a
timeout — and no model answered. Uderia delivers an **honest sentence** naming what the
provider said and that nothing was computed (*… the provider account has no credits left — an
administrator must top it up … Nothing was computed for this question.*), never the raw error.
The turn is recorded as **failed**: JUDGED reads red (Clean Termination fails), SIGNED's recovery
reads *no answer* (nothing bears on the signature), and a REST task reports `error`, not
`complete`. An empty account also counts against the provider's circuit breaker, so a signed
fallback takes over while the provider stays down.

### 13.4 The Honesty card says "Values from nowhere". What is that?

The answer showed a **record** — table rows, `Field: value` lines — on a turn that ran **no data
query**, and its values appear in nothing the model was given: not the knowledge, the
conversation, the question, or any tool result. That is a fabricated record, and the honesty gate
names it (kind *unsourced record*). A record copied from the knowledge graph or an earlier turn's
rows is supported and is left alone; a declared example abstains. If **every** data query that
turn failed and the answer still shows a record, the reading is *danger* and the note says *all N
data queries failed*. What happens next follows the honesty mode: observe records it, disclose
appends the note naming the values, enforce replaces the record with an honest statement (and
keep-for-review parks the original).

### 13.5 BOUNDED says this agent's limits "were NOT in force on this turn". Is the agent unbounded?

On an **interactive** turn the agent acts as you, so its own Agent Authority limits apply only to
its autonomous runs — unless *Apply to interactive turns too* is ticked on its Agent Authority
card. BOUNDED names the limits that did not apply rather than reading the turn as bounded. And
when the agent's authority **refuses** a query, the answer says so as a permission boundary — it
is no longer rewritten as "a retrieval error, please retry".

### 13.6 Signing my own agent says I am not allowed. Why?

Sign-off is for the **signer tiers** — the tiers Ontology Governance lets approve (admin by
default) — and, under an approval policy, never on your own artefact. The dialog now quotes the
server's reason instead of a generic "admin only", and a 409 names the parts that are still
unsigned. One former cause is gone: a bootstrapped agent whose template pointed at another user's
planner repository could never be signed by its owner; that binding is now re-pointed to your own
default planner at login, and a binding you cannot read is no longer a trust ingredient.
Attesting the **deployment** is different: it needs only ownership, at any tier (§11).

---

## See also

- [Agent Trust & Assurance](AGENT_TRUST_AND_ASSURANCE.md) — the operations manual: what
  each control does, how to configure it, sign-off and attestation workflows
- [TRUST_STATUS_COMMUNICATION.md](../Architecture/TRUST_STATUS_COMMUNICATION.md) — the
  contract every assurance surface conforms to, and the rules behind these answers
- [AGENT_QUALITY_DIMENSIONS.md](../Architecture/AGENT_QUALITY_DIMENSIONS.md) — every
  scored dimension: what it measures, its scope, its thresholds
- [ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](../Architecture/ASSURANCE_INSTRUMENTS_ARCHITECTURE.md)
  — the intervention registry behind the dial
