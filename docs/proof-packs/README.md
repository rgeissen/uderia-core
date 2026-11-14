# Uderia Proof Packs

**What this is:** publishable, evidence-grade documents for claims the Uderia framework makes. Each pack pairs one claim with an artefact an auditor can reproduce: a verifier that runs offline, a trace, or real numbers read from a running instance with the file they came from. A pack states its limits as plainly as its result.

The [Value Proposition](../value/VALUE_PROPOSITION.md) links each of its outcomes to the pack that substantiates it.

## The four packs

| Pack | Claim | Proof artifact |
|---|---|---|
| [A6 — Eval & Observability](A6_EVAL_OBSERVABILITY.md) | Every turn is scored, and the verdicts are signed and bound to the turn | **Reproducible Ed25519-signed attestation** — verdicts cryptographically bound to the trajectory; standalone verifier + real sample |
| [A4 — Multi-Agent Orchestration](A4_MULTI_AGENT_ORCHESTRATION.md) | A coordinator delegates to real agents, each scored and costed on its own | **Annotated coordination trace** + nested-genie + per-expert self-cost (no double-count) |
| [P4 — FinOps / Cost Controls](P4_FINOPS.md) | Every token is metered, priced and attributed to an agent | **Per-agent cost-attribution demo** with real numbers + `session = Σ turn` invariant + 4 enforced tiers |
| [P6 — Sovereignty / Hybrid](P6_SOVEREIGNTY.md) | Every layer has a fully local option | **Deploy-anywhere matrix** — every layer has a fully-local option → 100% air-gapped is a config choice |

## The headline: a verifiable signed attestation

The strongest single artifact lives in [`samples/`](samples/). It is a **real captured eval span** (one of 514 in this instance) whose verdict is Ed25519-signed and bound to its turn's provenance chain. Anyone can verify it offline with no Uderia install:

```bash
cd samples
pip install cryptography
python verify_attestation.py
```
```
1. recomputed verdict_digest == stored : True
2. public-key fingerprint == attestation: True
3. Ed25519 signature over payload      : VERIFIED
4. tamper test                         : signature REJECTS altered payload (correct)
RESULT: ATTESTATION VALID — verdict is cryptographically bound to the trajectory
```

| File | What it is |
|---|---|
| `samples/attestation_sample.json` | A real captured `agent.turn.eval` span with 17 verdict events + signed attestation |
| `samples/provenance_key.pub` | The Ed25519 public key (safe to share) auditors verify against |
| `samples/verify_attestation.py` | Standalone offline verifier (digest match · fingerprint match · signature verify · tamper test) |

## Capability proof packs

This directory also carries proofs for capabilities shipped later:

| Pack | Claim | Proof artifact |
|---|---|---|
| [T4 — Open Semantic Standards](T4_OPEN_STANDARDS.md) | A knowledge graph leaves and returns through open standards without loss | **Standalone offline verifier** ([`samples/verify_open_standards.py`](samples/verify_open_standards.py)) — lossless round-trip · QB structural conformance · third-party SPARQL consumption · tamper-evident; + a live 86-entity/128-rel export→reimport with 0 loss |
| [T2 — Ontology as a Governed Product](T2_ONTOLOGY_PRODUCT.md) | An ontology is released, versioned, signed and served as a governed product | **Reproducible end-to-end verifier** (`scripts/verify_ontology_governance.sh`, 17/17 live) — conformance gate → release + version pin + signed Ed25519 attestation → catalog → content-negotiated serve → deprecate → audit; + 70 CI assertions. *Honest scope:* release ≠ marketplace publish, and (Model A) it does not gate the IFOC engines — the conformance check is the standalone-valuable part. |
| [RAG Trust Hardening — the Knowledge Lane](RAG_TRUST_HARDENING.md) | The knowledge lane carries every control the data lane has | **Deterministic, 0-token corpus-grounding benchmark** (`python -m trusted_data_agent.eval.benchmarks.corpus_grounding.harness` — reproducible offline, no server, no LLM): recall **85.7%** · false positives **0%** · second-chance recovery **100%**, numbers pinned in CI incl. the honest false negative; + the live red-team corpus lane (real planted documents, cleanup reported) + 189 CI assertions across the 5-wave control-parity matrix |

```bash
cd samples && pip install rdflib && python verify_open_standards.py   # 23/23 checks, no Uderia install
```

The framework-wide open-standards posture (semantic interchange + MCP + OTLP + OAuth/OIDC/SAML + Ed25519/Fernet) is consolidated in [OPEN_STANDARDS_ARCHITECTURE.md](../Architecture/OPEN_STANDARDS_ARCHITECTURE.md).

## How these were built (so the numbers are trustworthy)

Every claim is **file-anchored** (file:line) and every number is **real** — read directly from this instance's online-eval sink (`tda_eval/online_eval.jsonl`, 514 spans), the live `tda_auth.db`, and real session files. The packs deliberately **state limits honestly** (e.g. the coordination trace is labelled a code-faithful reconstruction where no live genie session was captured; the eval sink is dominated by `tool_enabled`/`conversation_with_tools` turns) — transparency includes uncertainty.

## Related public architecture docs

- [OPEN_STANDARDS_ARCHITECTURE.md](../Architecture/OPEN_STANDARDS_ARCHITECTURE.md) (T4 + the framework-wide open-standards posture)
- [AGENT_OPS_EVAL_ARCHITECTURE.md](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) (A6)
- [COORDINATOR_ARCHITECTURE.md](../Architecture/COORDINATOR_ARCHITECTURE.md) (A4)
- [COST_TRACKING_ARCHITECTURE.md](../Architecture/COST_TRACKING_ARCHITECTURE.md) (P4)
- [VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md](../Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md) · [KG_STORE_ABSTRACTION_ARCHITECTURE.md](../Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md) (P6)
