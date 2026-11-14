# Trust Circle campaign — test approach

A **live, end-to-end** validation that the Trust Circle *operates and reports as expected*,
**on every engine class**: real turns through real agents, measured against the three
surfaces that must agree.

This is not a unit gate. The CI suites (`test_assurance_points.py`, `test_trust_behavior.py`,
`test_trust_circle_grammar.mjs`, …) prove the *logic* in isolation; this campaign proves the
**system**: that a real answer produced by a real model, screened by real gates, is reported
identically by the registry, the eval layer and the ring — and that this holds on Ideate,
Ideate + tools, Focus, Optimize and Coordinate alike, because the registry declares which
mechanisms apply to which class and a gate that reads correctly on one can be silent on
another.

The operating procedure (preflight, run, triage, pin, fix, re-run) is the `trust-campaign`
Claude skill: `.claude/skills/trust-campaign/SKILL.md`.

## What is being measured

Three surfaces read the same turn. A disagreement between any two is a defect, and finding
those disagreements is the point:

| Surface | Source | Renders |
|---|---|---|
| **Assurance dial** (per turn) | `GET /api/session/<sid>/turn/<tid>/details` → `assurance.{points,by_station,measures}` | the registry's readings — every gate that could touch the answer |
| **Quality card** (per turn) | the same payload's `eval` block | the scored verdicts |
| **Trust Circle** (per agent) | `GET /v1/evals/agents/kpi-dashboard` · `…/trust-behavior` · `/v1/trust/agents` | the eight stations' aggregate readings |

And the registry's own declaration — `GET /v1/assurance/vocabulary` (`k`, `applies_to`,
`station`, `dims`) — is the denominator: what *should* read on this class.

## The files

| File | Role |
|---|---|
| `agents.py` | the campaign's OWN agents: `ensure` (create/update, activate, sign + attest), `status`, `teardown` |
| `suite.py` | the cases — `engines`, `targets` (the registry points each provokes), honest `expect` — and the invariants I1–I9 |
| `coverage.py` | design-time I1: every applicable registry point reachable on every engine (`--strict` fails on a gap) |
| `run_campaign.py` | the live runner: engines in parallel, one new session per case, judges forced on, baseline diff |
| `analyse.py` | re-read a saved run: station matrix, cross-engine view, partition audit, `--recheck` after a suite fix |
| `baseline.json` | the last ACCEPTED run's readings per (engine, case), committed — every run diffs it |
| `FINDINGS.md` | each campaign and what it found |
| `../test_trust_campaign_suite.py` | the battery gate for the suite itself (well-formed, complete, labels current, invariants behave) |

## The five engines and their agents

| Registry class | IFOC | Tag | Binds |
|---|---|---|---|
| `llm_only` | Ideate | `@TCIDE` | the corpus; no MCP; memory off (a component tool would move the turn to the ReAct lane) |
| `conversation_with_tools` | Ideate + tools | `@TCIDT` | the corpus + MCP (ReAct); governed tool surface |
| `rag_focused` | Focus | `@TCFOC` | the corpus |
| `tool_enabled` | Optimize | `@TCOPT` | the corpus + MCP; governed tool surface |
| `genie` | Coordinate | `@TCCOO` | routes to `@TCOPT` + `@TCFOC` |

**Why the campaign owns its agents.** Campaign 1 measured the operator's `@UDERIA` as
configured and could not touch its posture (editing a gate flips SIGNED to `changed`). Across
five classes the campaign needs five agents whose posture is the SAME and KNOWN — `review` on
every gate that can park, so each gate can be seen to ACT — and whose signature is the
campaign's to make. It therefore provisions its own: a small knowledge repository built from
this repo's documents (scanned, its findings acknowledged, signed), its own MCP entry on a
live endpoint, its own LLM configuration, five signed and attested agents. Everything it
creates is listed in `tda_eval/trust_campaign/agents.json`; `teardown` removes exactly that.
**Nothing of the operator's is read, written, or signed** (`--agent TAG` measures one
operator agent as configured, as campaign 1 did).

**Why a weak model.** The campaign asserts what the gates REPORT, never what the model said.
A weak model hallucinates, complies with injections, invents records and leaks SQL far more
often per turn, so it provokes the gates more often per token spent. Default: the weakest
model on `llm.uderia.com` with a working provider (`OpenRouter/amazon/nova-2-lite-v1`);
`agents.py ensure --model Provider/slug` swaps it.

## Method

1. **One new session per case.** No cross-turn context, no cache, no ordering effects — each
   case is an independent experiment, and every turn is turn `1` of its own session.
2. **Engines in parallel, cases in order.** One worker per engine; a 96-case run takes ~35
   minutes with judges.
3. **Judges ON at rate 1.0 for the campaign** (restored afterwards). The judge dimensions are
   a first-class part of the trust engine; sampling them at 5 % would leave most cases
   unjudged and the results unreproducible. This costs real tokens — deliberately.
4. **The switches.** `TDA_TURN_BUDGET=1` and `TDA_OUTCOME_LOOP=1` are env, not app-config;
   the server must be started with them or the S5/S7 points honestly read `na`. The run
   detects the missing switch from the readings and says so instead of counting a miss.
5. **Every case carries expectations**; every case is additionally checked against the
   **universal invariants** below, which is where the lane cut is proven.
6. **Coverage before cost.** `coverage.py --strict` fails on any registry point that no case
   targets on a class it applies to, that no turn exercises on its own, and that
   `UNPROVOKABLE` does not explain. It runs in the battery.
7. **Drift against the baseline.** Every station reading is diffed against the last accepted
   run. Drift is a finding to triage, never a failure by itself.

## The invariants (checked on EVERY case)

| # | Invariant | Why it matters |
|---|---|---|
| **I1** | Every registry point that applies to the class produces a reading (`applies: true`); JUDGED carries measurements on an executed turn | a gate that reports nothing is indistinguishable from one that did not run |
| **I2** | No reading is `unknown` on a traced turn | `unknown` means the turn does not say — on a live turn that is a stamping gap |
| **I3** | **Label**: every measurement is ABOUT exactly ONE guarantee | a pointer that points two ways points nowhere |
| **I4** | **Lane**: every measurement is COUNTED at JUDGED, and the seven guarantee stations count none | a post-hoc grade inside an in-flight station's colour is a blend of two moments |
| **I5** | A **failing** measurement is counted in the lane and **nowhere else** | one root cause lights one station |
| **I6** | An engine error is never delivered as the answer with every station quiet | the coordinator died and the dial read clean (campaign 2) |
| **I7** | PROVEN: the turn is sealed (`provenance_meta.sealed`, chain tip present), or honestly absent on a refused turn | receipts, not promises |
| **I8** | SIGNED: the `integrity` stamp is sealed at answer time and replays | never today's badge on yesterday's turn |
| **I9** | Guard findings carry **labels only** — never the matched value | the privacy invariant |

## Case families

| Family | Station under test | Engines | What it provokes |
|---|---|---|---|
| **G** Grounding | GROUNDED | every class that retrieves (+ the coordinator for G1) | in-corpus recall, out-of-corpus honest absence, scope drift, fabrication bait, multi-doc consistency |
| **S** Safety | GUARDED | all | inbound prompt injection, outbound PII, toxicity, internals leakage |
| **B** Bounds | BOUNDED | tool-less (B1) · tool classes (B2–B6) · all (B4) | tool coercion against a tool-less class, write escape, a denied tool by name, component coercion, the turn budget, the governed surface |
| **P** Data path | GROUNDED | tool classes | fan-out bait for preflight SQL, number lineage |
| **H** Honesty | GROUNDED | tool classes | an unsourced record, fabrication after a failed query |
| **C** Coordinate | the coordinator's own turn | genie | single-expert routing, two experts |
| **J** Judged | JUDGED | all | instruction following, format constraints, resource bands |
| **X** Cross-station | the lane | all | a case designed to fail exactly one measurement, proving it colours JUDGED and nothing else |

## Running it

```bash
TDA_TURN_BUDGET=1 TDA_OUTCOME_LOOP=1 TDA_COMPUTE_LINEAGE=1 TDA_RESILIENCE_ENVELOPE=1 TDA_FAULT_INJECT=1 python -m trusted_data_agent.main &   # the switches (FAULT_INJECT: test server only)
python3 test/trust_campaign/agents.py ensure                                # the agents
python3 test/trust_campaign/coverage.py --strict                            # before a token
python3 -u test/trust_campaign/run_campaign.py                              # all engines
python3 -u test/trust_campaign/run_campaign.py --engines focus --only G     # one engine, one family
python3 -u test/trust_campaign/run_campaign.py --case B4                    # one case, every engine
python3 test/trust_campaign/analyse.py                                      # read the newest run
python3 test/trust_campaign/run_campaign.py --baseline write                # accept, after triage
```

Results land in `tda_eval/trust_campaign/<timestamp>/` (raw turn payloads + `report.md`),
which is gitignored — the campaign is reproducible, its output is evidence, not source.

## Honest limits

- The campaign measures **reporting fidelity**, not model quality. A case that "fails" because
  the model answered well is recorded as such, not counted against the engine.
- `answer_correctness` needs a reference answer; the live path has none, so it abstains. That
  is correct behaviour, and the campaign asserts the abstention rather than a score.
- Provoking a gate is probabilistic — a model may simply comply with an injection or refuse a
  PII echo on its own. Each provocation case therefore asserts **the gate's reading**, not the
  model's behaviour, and records the model's behaviour as context.
- Five points are declared `UNPROVOKABLE` with the reason and the phase-2 work that would
  provoke them (attachments, a campaign KG with one signed action, a fault-injecting provider,
  an Anthropic cache config). The declaration is a to-do list, not an exemption.
