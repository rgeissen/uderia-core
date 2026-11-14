# Proof Pack — RAG Trust Hardening (the Knowledge Lane)

> **Claim proven here:** the knowledge lane (RAG) has every control the structured lane
> earned in Tier 1 — a runtime grounding gate, honesty semantics, earned signatures,
> closed-loop disposition, adversarial E2E probing — *plus* three further controls, and
> the headline detection numbers are **published, pinned in CI, and reproducible offline at
> zero tokens.** Scope: `RAG_TRUST_HARDENING.md`
> (all 5 waves shipped 2026-07-19).

## 1. The corpus-grounding benchmark — deterministic, therefore stronger

The anti-hallucination benchmark (`Tier 1.4`) needs a
live LLM judge to label ground truth. The knowledge lane's benchmark does **not**: the Wave
1 corpus-support trigger is a pure function of *(answer, retrieved chunks, question)*, so
the entire benchmark runs offline, in-process, at **0 tokens** — anyone with the repo can
reproduce the published numbers exactly:

```bash
python -m trusted_data_agent.eval.benchmarks.corpus_grounding.harness
```

**Published numbers — dataset `corpus_v1` (18 curated cases: 8 supported · 7 unsupported ·
3 second-chance), scored by the SAME production primitive the live gate runs
(`agent/grounding/corpus.assess`):**

| Metric | Value | Meaning |
|---|---|---|
| **Recall** | **85.7%** (6/7) | unsupported claims the trigger flagged. The one miss (`uns-id-01`) is the deliberate ≥3-identifier floor — a single novel identifier never triggers alone (the false-positive discipline), recorded honestly rather than tuned away. |
| **False-positive rate** | **0.0%** (0/11) | supported answers wrongly flagged — the number that decides whether `enforce` is deployable. |
| **Second-chance recovery** | **100%** (3/3) | claims unsupported by the *first* retrieval but present in the corpus, cleared by the targeted re-search before flagging — the C1 false-positive killer, isolated and proven. |
| **Net precision** | **100%** | every trigger that fired was a real unsupported claim. |

Two honesty notes (charter #19): (1) the false negative is *published in the report body by
case id*, not summarized away; (2) the benchmark already **improved Uderia** — its
first run exposed a trailing-punctuation brittleness in T2 quote matching (`sup-quote-01`,
initially a false positive), fixed in the primitive, with the benchmark case retained as
the regression.

**CI pin:** `test/test_corpus_grounding_benchmark.py` (21 assertions) asserts these exact
numbers — including the false negative — so any primitive change that moves them fails CI
and forces a deliberate re-render of this pack. The dataset is structurally validated
(malformed data fails loudly), and the aggregation math is unit-tested against hand-built
TP/FP/FN sets.

## 2. The live end-to-end proof — real planted documents

The benchmark proves the *trigger*; the **red-team corpus lane** proves the *whole path*
with real turns: `POST /v1/assurance/live-redteam` with a caller-owned test collection
plants payload documents (a doc-embedded injection; a doc-embedded secret), asks the
questions that retrieve them, and scores the agent's actual behaviour deterministically —
did it obey the planted instruction, did it echo the secret unredacted. Payloads are
deleted in a `finally`, with the cleanup status **reported in the result** (never silent).
CI: `test/test_live_redteam.py` (33, incl. the corpus-probe scorers both ways + the cap).

## 3. The control-parity evidence (per wave)

| Control | Evidence |
|---|---|
| Corpus grounding gate (2 trigger families, 1 gate) | `test_corpus_grounding.py` — 68 assertions; the shipped scope suite (`test_grounding.py`, 102) green *unchanged*, proving byte-identity |
| Retrieval-sufficiency honesty | same suite (S1/S2/S3 incl. the backend-relative-threshold honesty) |
| Ingest firewall + PII manifest (labels only) + earned signature | `test_ingest_firewall.py` — 31 (incl. G1 unscanned-blocks-signing, G2 quarantine-or-acknowledge, the v2 hash shape with no retroactive flip) |
| Quarantine → retrieval exclusion (reversible, one seam) | `test_corpus_quarantine.py` — 13 |
| Exfil-URL guard (novel AND exfil-shaped; defang never delete) | `test_exfil_guard.py` — 23 |
| Source provenance (trust state · staleness · sensitivity) + the 2 blind-spot scorers | `test_knowledge_provenance.py` — 20 |
| Retrieval-anomaly monitoring · sensitivity ceiling · source-consistency judge | `test_retrieval_anomaly.py` — 24 |

Full regression context: the 24-suite trust/eval battery — **1,092 assertions — green** at
ship (2026-07-19).

## 4. What this pack deliberately does NOT claim

- The corpus trigger checks support **in the corpus**, never truth in the world —
  "unsupported ≠ false" is printed on the card, the inline mark, and the annotation.
- RAG remains the **monitored** lane; it is never promoted to the guaranteed lane
  (`TIER_5.5_OBDA_SCOPE.md §1.1`: retrieval over
  meaning is judgement and cannot be signed as a deterministic consumption contract).
- The retrieval-anomaly monitor detects **statistical** hub signatures, not proven
  semantic poisoning — its payload says so, and disposition is always human.

---
*Reproduce everything:* the benchmark command above (0 tokens, no server) + the CI suites
named per row (`python test/test_<name>.py`). Companion packs:
[proof-packs/README.md](README.md).
