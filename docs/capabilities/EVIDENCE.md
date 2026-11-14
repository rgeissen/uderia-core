# 🧾 Evidence — What Produced an Answer, and Was It Signed

> *For any answer: what produced it, and was each part signed and unchanged at that moment? Checkable by anyone, without Uderia.*

Every answer seals a **bill of materials** into its signed provenance chain: the agent, its
ontology, skills and repositories, the model and tool catalog it ran on, the data products it
read and the writes it performed — each with its content hash and the signature it matched. The
answer's trust record shows them as **when answered · now**, so a part that changed after the
answer is named, and the answer still points at the exact version it was made with.

- **History, not overwrite** — re-signing adds a record; an answer's evidence always names the
  signature that was valid when it answered.
- **Used by N answers** — every trust badge says which answers used the artefact, and how many
  used its current version: when a flaw is found, you know what it touched.
- **The Evidence tab** — every signature in the estate, worst first, with *Verify all shown* and an
  **Export for audit** that puts the signatures and the signed audit pack in one file.
- **Verifiable without Uderia** — an exported file carries the exact bytes each signature binds
  (up to a size limit). The [evidence verifier page](https://trust.uderia.com/verify-evidence.html)
  checks it in the browser, nothing uploaded; `scripts/verify_evidence.py` does the same with
  only `cryptography` installed. Give the deployment's key fingerprint separately and the
  verifier confirms who signed.
- **A hash-chained ledger** of every signing event and every answer's record: editing, inserting
  or removing one afterwards is visible, and every export carries a signed checkpoint that later
  proves the ledger still contains it.
- **Evidence outlives the session** — deleting a session removes the answer's text, not the
  record of what produced it.
- **Off-host custody and anchoring (opt-in)** — sign through an HSM, cloud KMS or Vault the
  server can ask but not read, and publish ledger checkpoints to a WORM file, a webhook or an
  RFC 3161 time-stamp authority, so even the server's administrator cannot quietly rewrite
  history.

A signature proves what was recorded and that it is unchanged — not that the answer is right;
grounding, guardrails and the judges are what check that.

Architecture: [**Evidence (docs/Architecture/EVIDENCE_ARCHITECTURE.md)**](../Architecture/EVIDENCE_ARCHITECTURE.md) — the live contract; it links the design record (PROP-009) for the history · operator workflow: [Agent Trust & Assurance §3.5b](../manuals/AGENT_TRUST_AND_ASSURANCE.md)
