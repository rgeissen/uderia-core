# Evidence per result — architecture

> **Status:** implemented on `main` (Oct 2026); last verified against code 2026-10-30. Proposal and decisions:
> PROP-009 (`docs/proposals/PROP-009-evidence-per-result.md`). This document is the live contract;
> the proposal is the record of why.

## 1. The question this answers

*"For this answer, what produced it, and was each part signed and unchanged at that moment?"*

Uderia already signs turns (the Ed25519 provenance chain), artefacts (the Trust Membrane), acts
(OBDA-ACT receipts) and audit packs. Evidence is the **index over those signatures plus the one record
that was missing** — which versions of an agent's parts a turn actually ran with. It never copies
an artefact: artefacts stay where they live, so an attestation is always compared with the real,
live artefact.

## 2. The records

| Record | Where | Written by | Notes |
|---|---|---|---|
| Current attestation | `trust_attestations` | `trust.store.sign` / `record_attestation` | One row per artefact — per **owner** for agents (§3). The badge reads it. |
| Attestation history | `trust_attestation_history` | every store write | Append-only: `sign` · `renew` · `record` · `reinstate` · `revoke` · `retire` · `migrated` · `assigned`. A re-sign never erases what was signed before. `renew` (Oct 2026) is a signature re-established by the owner's standing post-sync policy (`core/trust/renewal.py`, see [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md)); it counts as an establishing event exactly like `sign` (`store._ESTABLISHING_EVENTS`). |
| Signing keys | `trust_signing_keys` | `sign` (local) · `reinstate` (carried) · migration | `active` · `retired` (an earlier local key) · `carried` (another signer's). |
| Bill of materials | `turn['bom']` + a `trust.bom` chain step | the turn (§4) | The signed parts the turn ran with. |

## 3. Owner-scoped agent attestations

Bootstrap copies the same profile ids into every account, so the portable agent signature
(`agent`) and the deployment attestation (`agent_deployment`) are keyed
`(kind, artifact_id, owner_uuid)`. Every other kind keeps `owner_uuid = ''`: graphs, collections,
flows, dashboards, canvases and packs have deployment-unique ids; skills and extensions are
deployment-wide by design.

- A scoped read that names no owner sees only the **unassigned** bucket — *unsigned*, never another
  account's signature. A structural gate requires every production caller of a scoped kind to pass
  the owner (`test_trust_attestation_history` §G).
- The one-time migration rebuilds the table (SQLite cannot alter a table-level `UNIQUE`), copies
  every row unchanged, and attributes scoped rows with one rule, `store.attribute_owner`: the sole
  account holding the agent; else the signer, if the signer holds it; else unassigned.
- Unassigned rows are listed by `GET /v1/trust/unassigned` with the reason in words and resolved by
  an administrator (`POST /v1/trust/unassigned/assign`) — never assigned by guess.
- Deleting a profile retires its agent **and** deployment attestations, for that owner only.

## 4. The bill of materials

### 4.1 What it records

```json
{
  "version": 1, "profile_id": "profile-…", "profile_tag": "IDGGT", "owner_uuid": "…",
  "resolved": "turn_start", "resolved_at": "2026-10-08T15:40:49Z",
  "badge": "unsigned",
  "agent":      {"kind": "agent", "artifact_id": "profile-…", "label": "IDGGT",
                 "live_hash": "…", "attested_hash": "…", "history_id": 812,
                 "state": "trusted", "badge": "changed"},
  "deployment": {"kind": "agent_deployment", "artifact_id": "profile-…", "label": "deployment",
                 "live_hash": "…", "attested_hash": "…", "history_id": 815, "state": "trusted",
                 "required": true,
                 "summary": {"provider": "Google", "model": "gemini-…", "tools": 31,
                             "prompts": 12, "connectors": 0}},
  "parts": [{"kind": "ontology", "artifact_id": "kg-…", "label": "Fitness",
             "live_hash": "…", "attested_hash": "…", "history_id": 790, "state": "changed"}]
}
```

- **`agent.state`** is the agent's own signature against its live composition; its ingredients
  report on their own rows (never twice). **`agent.badge`** is the portable badge (signature and
  every ingredient green); the top-level **`badge`** is the composite badge, equal to the turn's
  integrity stamp.
- An ingredient whose content could not be read is marked `unresolved` and is never `trusted`
  (`attestation.ingredient_state`, the same rule the badge and the sign gate use).
- **Data products and actions are not repeated here.** Each is sealed by its own receipt step in
  the same chain (`obda.consumption`, `action.performed`); the evidence tree joins them at read time.

### 4.2 When

Resolved once at turn **start**, at the seam that already resolved the integrity stamp:
`executor.run()` for the four standard engines and the Coordinate engine's own turn assembly
(`agent/engines/coordinate_engine.py` — genie is dispatched directly). `composition.turn_evidence()` is one resolution with two
products — the stamp and the BOM — so they cannot disagree; `turn_integrity_stamp` is its first
half. A part edited *during* the turn is recorded as it was at the start (`resolved: turn_start`).
Cost measured on real profiles: +6 to +11 ms per turn.

### 4.3 How it is sealed and verified

- Held in a contextvar for the turn (`trust.evidence.set_turn_bom`, reset to `None` at every turn
  start so an earlier turn's BOM cannot leak).
- `ProvenanceChain.finalize()` seals a `trust.bom` step whose content is
  `evidence.bom_step_content(bom)` — `bom:v1:<sha256 of the canonical BOM>:<part count>` — so a BOM
  of any size binds fully under the chain's 4 KB content cap.
- `session_manager.update_last_turn_data` stamps `turn['bom']` (internal keys dropped).
- `provenance.verify_content` re-derives the step from the stamp through the same function. An
  edited stamp, or a sealed BOM with no stamp, is a named mismatch.
- With the Ontology Suite removed, no step is sealed and no stamp is written.

### 4.4 The delivered answer verifies on every engine

`verify_content` checked the `turn_complete` step against `final_summary`, which only Optimize
writes; the other four engines store `final_summary_text`. Every non-Optimize answer therefore
read as altered (1,310 historical turns on the reference instance). The step now verifies against
the engine's own field. A legacy genie turn that kept only `final_response`, cut to 500
characters, is re-derivable when the answer was short and otherwise reported as **not checkable**
— never as tampering. After the fix, all 2,133 historical turns verify with no mismatch.

## 5. The evidence record and the usage index (PROP-009 §4.3)

`core/trust/evidence_index.py` — two tables beside the attestations, written once per turn at
`session_manager.update_last_turn_data` for any turn carrying a BOM (never by scanning session
files; a re-save of the same turn replaces its rows; every write fails soft):

| Table | Holds |
|---|---|
| `evidence_turns` | One row per (owner, session, turn): profile, chain tip, key fingerprint, the BOM as JSON, answered at. **Content-free** — hashes, labels and attestation references, never answer text. Outlives the session (§9.3). |
| `evidence_parts` | One row per signed part the turn used — agent, deployment, each ingredient, each **data product** it read (from its `obda.consumption` receipts, identified as `<kg_id>::<name>` through the agent's ontology), each **action** it performed (`<kg_id>::<action>`) — with the content hash it used and its state then. |

`usage(kind, id, owner, current_hash)` answers "which answers used this, and how many used the
version it has now" (`since_current`). A caller's view counts only their own turns; the system
view (`VIEW_ALL_SESSIONS`) counts every account's.

## 6. The read model (PROP-009 §4.3, §5.1)

`core/trust/evidence_view.turn_view(owner, session_id, turn, session_data)`:

- **When answered** comes only from the sealed BOM. It is never re-resolved: re-resolving would
  show today's state as yesterday's fact.
- **Now** is resolved live through the badge's own rules: the agent's current composition
  (`_resolve_profile_trust_detail`), its deployment, each ingredient by `ingredient_state` — or,
  for a part the agent no longer uses, directly by `content.live_hash`.
- **Status**, one word beside the pair: `unchanged` · `changed_since` (the content hash moved) ·
  `not_found` (cannot be found now — never read as *unsigned*) · `no_longer_used` (still exists,
  no longer one of the agent's parts) · `recorded` (data read and acts performed, which have no
  "now" comparison).
- **Absence is said:** a turn without a BOM reads *"parts not recorded — this answer predates
  evidence recording"*; a turn whose session file is gone is read from `evidence_turns` with
  *"session deleted — content not retained"*.
- **Chain:** the turn's own chain and content are re-verified (`verify_chain` + `verify_content`);
  an edited BOM stamp makes the turn read invalid, naming the `trust.bom` step.

REST: `GET /v1/evidence/turn/<session_id>/<turn>` (own turns; another user's is 404) and
`GET /v1/evidence/artifact/<kind>/<id>/usage` (`?scope=system` behind `VIEW_ALL_SESSIONS`).
Blueprint `api/evidence_routes.py`, registered only beside the Trust Membrane.

### The answer view

`static/js/handlers/evidenceView.js` (`window.EvidenceView`, loaded only with the suite). The
answer capsule (*Why trust this answer?*) renders an **Evidence** row only when the script is
present and mounts it after load (fresh on re-open, since "now" moves):

- **Collapsed:** the summary words — *"12 parts · 5 not signed-and-intact when answered · 1
  changed since"* — as an attention chip, with the parts that need a look named on the row.
- **Opened:** the chain verification, then the tree. Each part shows the fixed-order pair
  **when answered · now** in state words (a missing side reads `—`), and a status word only for
  the exception (`changed since`, `not found now`, `no longer used by the agent`).
- **Detail** (one click down): signer and time, the key fingerprint and whether it is this
  deployment's key, a retired one or another signer's, and both content hashes.
- **The owed act:** *Open trust record* only on an ingredient that changed since or was not
  signed; nothing on a signed, unchanged part.
- The receipt export carries the evidence beside the chain.

## 7. Artefact usage and the estate (PROP-009 §5.2, §5.3)

- **The trust popover** (`trustBadge.openArtifactTrust`) carries one more line, read from the
  usage index: *"Used by 3 answers · every one with this version"*, or *"… · 31 answers with
  this version, 183 with an earlier one"*, with the answers one click down. Nothing is used yet
  → *"Not used by any recorded answer yet"*; an unreadable count shows nothing, never a guess.
- **`GET /v1/evidence`** lists every current signature the caller may see, read against the
  artefact as it is now (the Trust Membrane's own `_resolve_artifact_state`, and the deployment
  resolver for an `agent_deployment`), worst first: *changed since signed* · *made with a retired
  key* · *recorded without a signature* · intact. Visibility reuses the membrane's ownership gate
  and fails closed. Usage and labels come from two single queries over the index
  (`usage_counts`, `labels`), so the list never queries per row for them. A filter narrows the
  list; the summary always describes the whole scope.
- **The Evidence tab** in Trust (rendered by `evidenceView.mountEstate`, present only with the
  suite): the summary chip, *Verify all shown*, filter pills, a kind selector and search; each
  row expands to the answers that used it, and each answer to its own evidence tree. The list is
  re-read when shown again after a minute ("now" moves).
- **Routes:** SIGNED leads with *Open Evidence — the changed signatures* (or *every signature*),
  PROVEN with *Open Evidence — what each answer was made with*; both keep their earlier route
  beneath it. Without the view the stations are unchanged.
- *Verify all shown* exports the shown signatures as a bundle and checks it in the reader's
  browser (§8).

## 8. Portable bundles and the offline verifiers (PROP-009 §4.4)

**One export, every caller.** `POST /v1/evidence/export` (`core/trust/evidence_bundle.build`)
returns a self-contained file: for each attestation the record **as it was signed** (from the
history row an answer's bill of materials names, so a later re-sign never changes what an
answer's evidence says; or the current record for an artefact asked for directly), its
signature, the public key it was signed with and its fingerprint, and — up to
`EVIDENCE_CONTENT_LIMIT_BYTES` (256 KB, env `TDA_EVIDENCE_CONTENT_LIMIT_BYTES`; `content:"all"`
lifts it, `"none"` omits it) — the **exact bytes** whose sha256 is the signed version hash. For
each answer: the sealed chain, the bill of materials and (on request) the question, the answer
and its trust stamps. Presets: an answer's **Export evidence** (the former receipt, now one
verifiable file), **Export shown** on the Evidence tab, and **Export for audit** — every visible
signature plus the deployment's signed audit pack, embedded unchanged.

**The bytes come from one source.** `attestation.content_capture` records what `content_hash`
hashed while it is open; the bundle runs the badge's own resolver under a capture and keeps the
recorded bytes whose digest equals the signed hash. Nothing re-implements a resolver, so what a
bundle carries is by construction what the signature binds. When the artefact as it is now no
longer produces those bytes (changed since signed, or gone), or they exceed the limit, the entry
carries the hash only and **names why**. The capture never changes what is hashed (gate A1). It only
sees bytes hashed through `content_hash`, so every signable kind must hash there: ontology extensions,
action types and data products once used a private `_canon_hash`, and every export of them read
*"changed since it was signed"* while the answer's own view read them intact. Gate H3 refuses any trust
module that hashes canonical JSON outside `content_hash`.

**Two verifiers, one rule set.** `scripts/verify_evidence.py` (needs only `cryptography`) and
`static/js/evidenceVerify.js` (WebCrypto Ed25519 + SHA-256) check: each signature over
`<kind>|<version_hash>` with the carried key, the key's fingerprint, and sha256 of the carried
content; each answer's chain (every step hash, link and signature; root, tip, count) and that
the sealed `trust.bom` step is the digest of the bill of materials; that an exported question
and answer hash to the chain's `query_intake` and `turn_complete` steps (the chain seals the first
4,096 characters, counted in code points by both verifiers; a longer text says only that prefix
is checked; the export carries the answer field the chain sealed, and an answer from an older
record that the chain does not cover is marked *not checked*, never verified); an embedded audit pack's
canonical hash and signature. The browser re-encodes the bill of materials exactly as Python's
`json.dumps(sort_keys=True, separators=(",", ":"))` does — ASCII escapes, code-point key order —
and takes every number from the bundle **text**, so `1.0` stays `1.0`; the bundle therefore
travels as text in the app, and anything the app adds (the answer's evaluation) is appended to
the text, never re-serialized. A gate runs both verifiers on the same bundles and requires the
same verdict on each, tampered or not.

**Who signed it.** A bundle carries its keys, so alone it proves the record is internally
consistent. The fingerprint, obtained separately from the deployment's operator, proves who
signed it; both verifiers ask for it and say "signer not confirmed" without it.

**The offline page** `static/trust/verify-evidence.html` (built by
`scripts/build_evidence_verifier_page.py`) inlines the verifier module verbatim — a gate asserts
the copy is identical — so it runs from disk with no network.

## 9. The evidence ledger (PROP-009 §4.5)

`core/trust/ledger.py` keeps an append-only, hash-chained record (`trust_ledger`, in the auth
db) of every attestation event and every answer's sealed record, so that editing, inserting or
removing history after the fact is detectable:

    entry_hash = sha256("ledger:v1:<seq>:<entry_type>:<ref>:<digest>:<prev_hash>")

- **attestation** — ref `h:<history id>`, digest = sha256 of the history row's canonical record
  (every field that says what was attested, by whom, when; numbers normalized so a value reads
  back exactly as it was written). Entered by `store._append_history` in the **same
  transaction**, after the row's own write, so the write lock is held and no two writers take
  one sequence number. Every event goes through that one seam — sign, record, reinstate,
  renew, revoke, retire, assign and the migration's seeding.
- **turn** — ref = sha256(owner|session|turn) (no identifier in a deployment-wide table),
  digest = sha256(`turn:v1:<chain tip>:<bill-of-materials digest>`). Entered by
  `evidence_index.record_turn` when the record is new or changed — never twice for the same
  content, however often a turn is saved.
- **Backfill.** A database that predates the ledger is entered once, in history order, as
  `backfilled`, before anything live; the reading says how many were entered when it began.

**`verify`** walks the chain (gaps, links, recomputed hashes) and cross-checks it against the
tables it records: an **edited** row or record, a **removed** one (pruning), and one **written
around** the ledger (unledgered). A **checkpoint** is the tip signed with the deployment key;
every export carries one, and `POST /v1/evidence/ledger {checkpoints}` proves the ledger still
contains each earlier tip — so a truncated ledger, or one rewritten and rebuilt consistently
(which internal checks alone cannot see), fails against a checkpoint taken before.

**Exports** carry the checkpoint, each attestation's ledger record, each answer's ledger ref,
and the contiguous segment from the earliest included entry to the tip (up to 20,000 entries;
beyond that each record's own entry, said so). Both verifiers check the checkpoint signature,
the segment's links up to the signed tip, and that every record re-derives the digest it was
entered with.

**Shown** as one chip on the Evidence tab (*"ledger intact · 178 entries · 178 entered when it
began"*, or *"ledger NOT intact — 1 record edited after entry"* in danger), with its limit in the
tooltip. Which records moved is listed only with `VIEW_ALL_SESSIONS` — the ledger is
deployment-wide.

**Honest limit.** The host administrator holds the signing key and could rebuild the whole
chain and re-sign a new tip; what they cannot do is make the rebuilt ledger contain a
checkpoint someone exported earlier. Key custody and external anchoring (§10) remove the
remaining trust in the host.

## 10. Key custody and external anchoring (PROP-009 §4.6) — opt-in

Both are off by default, and with them off every signature Uderia makes is byte-identical
to before (Ed25519 is deterministic; a gate compares against the direct key call). The settings
below are `APP_CONFIG` keys (`core/config.py`); each is set from the environment with a `TDA_`
prefix (`TDA_EVIDENCE_SIGNER`, `TDA_EVIDENCE_ANCHOR`, …). The command signer also honours
`TDA_EVIDENCE_SIGNER_TIMEOUT_SECONDS` (default 10).

**Who signs — one seam.** `core/signing.py` sits behind the only three signing sites: a
provenance-chain step (`ProvenanceChain._sign`), `eval.online.sign_payload` (attestations, audit
packs, ledger checkpoints) and `eval.online.sign_attestation`. `get_key_fingerprint` and
`get_provenance_public_key_pem` report the signer's key.

| `EVIDENCE_SIGNER` | Where the key is | What the host can do |
|---|---|---|
| `local` (default) | the `tda_keys` file on this host | sign anything — including re-signing a rewritten history |
| `command` | outside this host: an HSM, a cloud KMS (`gcloud kms asymmetric-sign`, `EC_SIGN_ED25519`), a Vault transit key, `pkcs11-tool -m EDDSA` | request signatures; it cannot read the key |

The command signer runs `EVIDENCE_SIGNER_COMMAND` with the message on stdin and reads a base64
signature from stdout; the public key is the Ed25519 PEM at `EVIDENCE_SIGNER_PUBLIC_KEY`. Every
returned signature is **verified against that key before it is used** — a signer that answers
wrongly, fails, or whose key cannot be read yields *no* signature (the existing degraded mode),
never a bad one. Under it the local key file is never loaded. One process call per signature:
for a busy deployment point it at a local signing agent rather than a cold CLI.

Switching custody (a restart with the new configuration) registers the new key and marks the
old one retired, and an answer's chain is
verified with **the key the chain names** (from `trust_signing_keys`), so older answers keep
verifying after a rotation or a custody switch.

**External anchoring.** `core/trust/anchoring.py` publishes a signed ledger checkpoint every
`EVIDENCE_ANCHOR_INTERVAL_SECONDS` (default 3600; skipped when the tip has not moved) — started
at boot only when `EVIDENCE_ANCHOR` is set and the suite is present — or on demand
(`POST /v1/evidence/ledger/anchor`, admin; `409 anchoring_off` when unset). A tip is skipped only
when the same sequence number **and** hash were already published: a ledger truncated and
re-extended reuses a sequence number with a different hash, and that rewritten tip is published
so it conflicts with what went out before. A checkpoint that cannot be signed is never
published; the failure is recorded:

| `EVIDENCE_ANCHOR` | `EVIDENCE_ANCHOR_TARGET` | Receipt |
|---|---|---|
| `file` | a path on storage this host cannot rewrite (WORM mount, append-only volume) | the appended line |
| `webhook` | an append-only log / transparency service (`EVIDENCE_ANCHOR_TOKEN` = bearer) | the HTTP status |
| `rfc3161` | a Time-Stamp Authority URL | the authority's signed token (check with `openssl ts -verify`) |

The RFC 3161 request timestamps sha256(the checkpoint payload) and is byte-identical to
`openssl ts -query -sha256 -cert -no_nonce` (gate-asserted). Each anchor is recorded in
`trust_ledger_anchors`, and the ledger reading re-proves that the ledger still contains every
anchored tip; a failed publish is recorded and shown (*"ledger intact · the last anchor failed:
…"*, as warn). The record of anchors lives on this host too — the external copy is the proof.

**Shown:** the ledger endpoint reports `custody {signer, key_on_host, fingerprint}` and
`anchoring {enabled, sink, interval_seconds, anchors, last_seq, last_at, last_ok, last_error}`; the Evidence tab's ledger
tooltip states custody and anchoring in words; every bundle carries `custody`, and its stated
limit follows it.

### Verified against real services (Oct 2026)

Each opt-in path was run once against a real external service, not a stand-in. These runs used a throwaway database and never touched the deployment key:

| Path | Service | Result |
|---|---|---|
| Command signer | SoftHSM 2 over PKCS#11 (`pkcs11-tool --sign -m EDDSA`, an Ed25519 key held in the token) | `sign_payload`, a sealed provenance chain, and an attestation all signed by the token key. `verify_chain` is valid. An exported bundle passes `scripts/verify_evidence.py --fingerprint <token key>`. No local key file was loaded. |
| Webhook anchor | A local HTTP receiver, reached through real `httpx` | The signed checkpoint arrived with the bearer token. An unmoved tip is not re-sent. |
| RFC 3161 anchor | freetsa.org | Status *Granted*. `openssl ts -verify -data <checkpoint payload> -CAfile cacert.pem -untrusted tsa.crt` returns OK on the stored receipt and FAILED (message imprint mismatch) on a payload altered by one byte. |

The command signer takes a raw 64-byte signature as well as base64, so an HSM CLI can be used as it is.

## 11. Gates

| Suite | Covers |
|---|---|
| `test/test_trust_attestation_history.py` | migration preserves every reading · attribution rule · owner isolation · history · signing keys · every scoped caller passes an owner |
| `test/test_evidence_bom.py` | canonical form · `turn_evidence` · seal and verification · re-sign survival · contextvar isolation · engine wiring · the answer field on every engine |
| `test/test_evidence_view.py` | the index (per part, re-save, owner isolation, usage) · when-answered vs now · status words · absence (not recorded, session gone) · chain re-verification · wiring · REST scoping |
| `test/test_evidence_estate.py` | rows, flags and the worst-first summary · usage counts equal the index · visibility fails closed · filters never change the summary · station routes and the suite guard |
| `test/test_evidence_bundle.py` | content capture never changes a hash · bundle content included / changed / over the limit / not requested · as-signed-when-answered across a re-sign · the Python verifier on clean and five tampers, fingerprints, the audit pack · the browser verifier reaches the same verdict on every case (Node WebCrypto) · the offline page inlines the module · route and UI wiring |
| `test/test_evidence_ledger.py` | the chain and turn de-duplication · every attestation event entered in its transaction · backfill once, before live entries · detection of edited, removed and written-around records, edited and removed entries, truncation and consistent rewrite against a checkpoint · answer records · the bundle's ledger proof in both verifiers (incl. a cut segment and a broken checkpoint signature) · words and admin-only detail |
| `test/test_evidence_custody.py` | the default is byte-identical at all three signing sites · the command signer signs, reports its key, never loads the local key, seals a chain that verifies · a wrong, failing or keyless signer yields no signature · anchoring off by default, file/webhook/RFC 3161 sinks, unmoved tip skipped, failures recorded, a rewritten tip at a reused sequence number re-published, an unsigned checkpoint never published, the request equals openssl's, anchored tips re-proved after truncation · an older answer verifies with the key it names after a custody switch · the ledger and bundle wording follow custody · surfaces |
| `test/test_evidence_view_ui.mjs` | the shipped renderer: state words, the fixed-order pair, absence, the owed act, mount/cache/fresh, the capsule wiring and the suite guard |
