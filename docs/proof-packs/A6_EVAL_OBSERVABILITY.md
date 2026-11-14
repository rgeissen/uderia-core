# Proof Pack — A6: Agent Evaluation & Observability

**What this proves:** every executed turn is scored against a profile-class-aware scorer suite, and the verdicts are **Ed25519-signed and bound to the exact trajectory they grade**.
**What you can check:** a captured attestation, verified offline with the shipped script and public key — no Uderia install.
**Status:** ✅ Built (WS3) · proof reproducible below · Last updated 2026-06-28

> **Headline proof (reproducible in 10 seconds, no Uderia install):**
> ```
> cd docs/proof-packs/samples && pip install cryptography && python verify_attestation.py
> ```
> ```
> 1. recomputed verdict_digest == stored : True  (4ae2ecec96a98e21…)
> 2. public-key fingerprint == attestation: True  (72c42df3aecefa9c…)
> 3. Ed25519 signature over payload      : VERIFIED
> 4. tamper test                         : signature REJECTS altered payload (correct)
> RESULT: ATTESTATION VALID — verdict is cryptographically bound to the trajectory
> ```
> The committed sample (`samples/attestation_sample.json`) is a **real captured eval span** — one of **514** signed spans in this instance's online-eval sink.

---

## 1. What the claim requires

Agent evaluation is trustworthy when it is measurable, reproducible and bound to what it grades. Uderia combines three things:

1. **A profile-class-aware deterministic scorer suite** — 39 of 46 scorers are 0-token/code-only, so every turn is graded for free, deterministically, and reproducibly. LLM judges are reserved strictly for the open-ended residue.
2. **Cryptographically-bound verdicts** — each verdict set is signed with the same Ed25519 key that seals the turn's Execution Provenance Chain (EPC). A grade you can't trust is not observability; a grade bound to the trajectory is **tamper-evident audit**.
3. **Agent-as-entity UX** — an IFOC-classed agent health roster, not a run firehose.

This proof pack ships a signed-attestation sample an auditor can verify offline.

---

## 2. The headline artifact — signed attestations (tamper-evident audit)

### How a verdict is bound to its trajectory

- **Algorithm: Ed25519**, reusing the deployment's existing EPC signing key — verdicts carry the *same* cryptographic identity that signs the trajectory itself.
- **Signed payload** (`eval/online.py:143`): `payload = f"{turn_tip_hash}|{verdict_digest}|{profile_class}"` — binding three facts:
  - `turn_tip_hash` — the turn's provenance **chain-tip hash** (the trajectory).
  - `verdict_digest` — canonical SHA-256 over the verdict set (`_verdict_digest`, `online.py:128-132`): `sha256(json.dumps([{"d":dim,"p":passed,"s":score}…], sort_keys=True, separators=(",",":")))`.
  - `profile_class` — the IFOC class graded.
- **Signing** (`online.py:135-162`, `sign_attestation`): pulls `provenance._get_provenance_signing_key()` + `provenance.get_key_fingerprint()`, signs the payload, stores the signature base64.
- **The key** (`core/provenance.py:55-103`): an `Ed25519PrivateKey` at `tda_keys/provenance_key.pem` (PKCS8, chmod 0600), auto-generated on first use; public key + fingerprint at `provenance.py:106-132`.
- **Graceful degradation** (`online.py:144,153-154`): if the key is unavailable the attestation still emits with `signed=false` — observability never breaks.

### Attestation fields (real captured object, from `samples/attestation_sample.json`)

```json
{
  "turn_tip_hash":   "57ba1335179dd1dba93d2f2aadbf790f002fbbf9aeb672af3bdd9ff056a12ccc",
  "verdict_digest":  "4ae2ecec96a98e21075abae8fed1c8a8558b328a14f6488b5b3755a343265db6",
  "profile_class":   "conversation_with_tools",
  "key_fingerprint": "72c42df3aecefa9ccd5bb4f0a6cc7c75c4c4d4a237c7038e12fd3aa6b7ba9dc6",
  "signature":       "PrpAM2VvNExOqIDu3E0ci3znm2Ykb73JzLX+ApvyA8+VosVY7sOnHWaQSrOIOECyRcLbqjkPhuWyzyAzAiFECA==",
  "signed":          true
}
```

### Verification (offline, by an auditor)

- Library: `provenance.verify_chain(provenance_data, public_key_pem)` (`provenance.py:306-376`) — checks chain linking, content hashing, and the Ed25519 signature.
- REST: `POST /api/v1/sessions/<id>/provenance/verify` (`api/provenance_routes.py:100-110`).
- Public key for auditors: `provenance.get_provenance_public_key_pem()` → `tda_keys/provenance_key.pub` (committed here as `samples/provenance_key.pub`).
- **This pack's standalone verifier**: `samples/verify_attestation.py` — needs only `cryptography` and the committed public key. It independently (1) recomputes the verdict digest from the span events, (2) recomputes the key fingerprint, (3) verifies the signature, and (4) shows a tamper test failing — proving the binding is real, not a stored claim.

**Tamper-evidence in one sentence:** altering any verdict changes `verdict_digest` → the signature over `tip|digest|class` no longer verifies → the audit trail self-detects the edit.

---

## 3. The deterministic scorer suite — 39 of 46 scorers are 0-token

`scorer.py` (ABC `:16`, `register_scorer` `:82`, `scorers_for` `:95-120`) dispatches a registry of **46 scorers** by profile class, plus **6 flow-agent** scorers (`scorers/flow.py`):

| Kind | Count | Token cost |
|---|---|---|
| `DETERMINISTIC` | 23 | 0 |
| `METRIC` (banded) | 16 | 0 |
| `JUDGE` (LLM) | 7 | judged sample only |
| **0-token total** | **39** | **free, every turn** |

**Core suite** (`scorers/deterministic.py`, 19): cross-cutting `completed`, `answer_present`, `format`, `cost`, `tasks`, `parallel_efficiency`, `safety`, `provenance_integrity`; planner `steps`/`efficiency`; tool `tool_subset`/`must_not_call`/`self_correction`/`plan_adherence`; Focus `retrieval_performed`/`grounded_overlap`/`citations`; Coordinate `routing`/`coordination_efficiency`.

**Reliability suite** (`scorers/reliability.py`, 14): `latency`, `tokens`, `clean_termination`, `context_health`, `output_sanity`; tool `tool_success_rate`/`no_tool_loops`/`scope_adherence`/`tool_calls`/`error_recovery`; data `numeric_fidelity`; Optimize `plan_execution`; Focus `retrieval_relevance`; Coordinate `expert_success`.

**Per-profile-class coverage** (each class gets exactly the scorers its execution signals support; the rest abstain cleanly — charter #14):

| Profile class | scorers | 0-token | judges |
|---|---|---|---|
| `llm_only` (Ideate) | 15 | 13 | 2 |
| `rag_focused` (Focus) | 20 | 18 | 2 |
| `tool_enabled` (Optimize) | 30 | 26 | 4 |
| `genie` (Coordinate) | 19 | 17 | 2 |
| `conversation_with_tools` (ReAct) | 26 | 21 | 5 |
| `flow` (flow-agent) | 8 | 8 | 0 |

3-band Healthy/Review/Danger model: `banded_metric()` (`scorer.py:41-76`) — *warn, don't gate*: only the Danger band fails the gate.

---

## 4. The LLM-judge layer — diverse-panel consensus, κ-calibrated, cost-attributed

- **7 judge dimensions** (`scorers/judges.py`), each scoring **one** isolated dimension with quoted evidence and an `"unknown"` escape: `answer_correctness`, `groundedness`, `instruction_following`, `tool_arg_optimality`, `answer_groundedness`, `scope_faithfulness`, `synthesis_quality`.
- **Panel-of-LLMs** (`eval/judge.py`): `build_panel()` (`:160-224`) assembles up to 3 models across **distinct provider families** (Uderia's 10 providers make diversity free), floats the user's default model first, skips credential-less configs. Judges run **concurrently** (`asyncio.gather`), each bounded by `ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS=20` → a hung model degrades to `"unknown"`. `consensus()` (`:53-76`) is conservative (ties → fail).
- **Cohen's-κ calibration** (`eval/calibration.py`): judges stay **non-gating** (`JudgeScorer.gating=False`) until `kappa ≥ 0.6` ("substantial agreement", Landis & Koch).
- **Cost attribution through CostManager** (`online.py:480-494`): judge token usage is summed via `CostManager().calculate_cost(...)` and routed through `_record_score_usage()` into session/turn totals + the idempotent `token_update` ledger (`call_id="turn_score"`) + `ConsumptionManager` — the *same* accounting path as any LLM call (charter #16). No untracked side-calls.

---

## 5. Online eval — scores every turn, never blocks one

- **Hook** `evaluate_turn_online()` (`online.py:402-571`) is wrapped to *never raise* (`:569-571`).
- **Background dispatch** (`agent/execution_service.py:28-37`): fired via `asyncio.ensure_future`, strong-ref'd in `_ONLINE_EVAL_TASKS` to survive GC, discarded on done. The answer is already delivered; scoring is pure off-path observability (the Jun-2026 "never blocks a turn" hardening).
- **Deterministic-first**: deterministic scoring always runs first and instantly (`online.py:466`); the judge layer is bounded by `asyncio.wait_for(timeout=ONLINE_EVAL_TIMEOUT_SECONDS=25)`.
- **Tail sampling**: deterministic on for **every** turn (free); only the costing judge layer is stochastic (`should_judge`, `online.py:116-124` — always judges anomalous turns + a `judge_rate` fraction).
- **Config** (`core/config.py:123-147`): `ONLINE_EVAL_ENABLED=True`, `ONLINE_EVAL_INCLUDE_JUDGES=False` (admin opt-in), `ONLINE_EVAL_JUDGE_SAMPLE_RATE=0.05`, `ONLINE_EVAL_SINK="tda_eval/online_eval.jsonl"`, plus the admin-tunable banded thresholds. Admin surface: **Administration → App Config → Trust** (the station-partitioned panel).

### OTLP span shape (real, name = `agent.turn.eval`)

```json
{ "name": "agent.turn.eval", "trace_id": "…", "span_id": "…",
  "attributes": {
    "eval.profile_class": "conversation_with_tools", "agent.profile_tag": "IDEAT",
    "agent.invocation": "direct", "eval.dimensions": 25, "eval.gating_failures": 0,
    "attestation.signed": true, "attestation.verdict_digest": "d0e0e2bd…",
    "eval.judged": true, "eval.cost_usd": 0.0009802,
    "eval.dim.completed.passed": true, "eval.dim.completed.score": 1.0, "…": "…" },
  "events": [ { "name": "verdict", "attributes": {
      "dimension": "completed", "kind": "deterministic", "passed": true, "score": 1.0,
      "explanation": "turn completed without error", "evidence": [], "band": null } }, "…" ],
  "attestation": { "…": "see §2" } }
```

A pluggable exporter (`OnlineEvalExporter` ABC, `online.py:36`) lets the JSONL sink be swapped for a real OTLP collector without touching the hook.

---

## 6. Surface area & CI proof

- **17 `/v1/evals/*` REST endpoints** (`api/rest_routes.py:18237-18653`): datasets/cases, mining, runs, scores, gate, score-turn, online, clusters, diff, dashboards, agents kpi-dashboard/timeline/runs, annotations.
- **CLI** (`eval/cli.py`): `list · mine · run · gate · report` — `gate` exits non-zero on regression (CI gate).
- **CI**: **358 self-asserting assertions across 10 suites** (`test/test_eval_*.py`): analysis 47, cli_rest 9, deterministic 45, flow 41, genie_ux 42, judges 47, online 40, reliability 42, runner 15, tasks 30. Adjacent grounding-gate suites add 151 more on the same primitives.

---

## 7. Real numbers from this instance (`tda_eval/online_eval.jsonl`)

| Metric | Value |
|---|---|
| Total captured eval spans | **514** |
| Signed (`attestation.signed=true`) | **514 / 514 (100%)** |
| Profile-class split | `tool_enabled` 474 · `conversation_with_tools` 39 · `llm_only` 1 |
| Dimensions per span | 13–30 (mode 26) |
| Independent digest re-verification | **MATCH** (recomputed `4ae2ecec…` from events == stored) |

---

## 8. Reproduce it

```bash
# 1. Offline attestation verification (no Uderia install; just `cryptography`)
cd docs/proof-packs/samples && python verify_attestation.py

# 2. The deterministic + judge suite, regression-gated
PYTHONPATH=src python -m trusted_data_agent.eval.cli gate --baseline <run>   # exits non-zero on regression

# 3. The CI suite
for t in test/test_eval_*.py; do PYTHONPATH=src python "$t"; done
```

**Public artifact:** [`docs/Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md`](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) (the full architecture). Central claim: §-headline *"Eval verdicts are cryptographically bound to the exact trajectory they grade."*

---

### Key code anchors
| Concern | File:line |
|---|---|
| Attestation sign | `eval/online.py:128-162` |
| Provenance Ed25519 key | `core/provenance.py:55-132` |
| Verify chain | `core/provenance.py:306-376`; REST `api/provenance_routes.py:100` |
| Scorer registry / dispatch | `eval/scorer.py:16,82,95-120` |
| Deterministic / reliability scorers | `eval/scorers/deterministic.py`, `eval/scorers/reliability.py` |
| Judge panel / κ-calibration | `eval/judge.py:53-224`, `eval/calibration.py:16-76` |
| Online hook / background dispatch | `eval/online.py:402-571`, `agent/execution_service.py:28-37,698-740` |
| Quality span | `eval/online.py:170-231` |
