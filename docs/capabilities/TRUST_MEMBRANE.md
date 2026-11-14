# 🛡️ The Trust Membrane — Verifiable Trust for Agents & Shared Artefacts

> *Sharing stays fully open. Trust becomes **legible** — a visible, cryptographically-verifiable badge, never a restriction.*

Enterprises need to know whether an agent — and everything it is built from — is **trusted**. The **Trust Membrane** makes trust a first-class, visible property of the framework, on one simple principle: **an agent is trusted exactly when its entire composition is** — its ontology, every skill and every repository it binds must each be signed. The consumer reads **one badge**; the whole configuration catalog hides behind the agent.

Four verifiable states appear as an industrial **signed seal** next to any artefact's name:

- ⚪ **Unsigned** — shared and usable, not yet attested.
- 🟢 **Trusted** — signed and intact for this exact version.
- 🟠 **Changed** — was signed, but its content drifted since.
- ◒ **Not attested here** — the state a **forked or installed pack** lands in: the publisher's
  signature travelled and verifies, but *this* deployment has not yet attested its own half —
  the resolved model, the reachable tool catalog, the connector opt-ins. Those deliberately
  never travel (you run your own), so each instance attests them. One click by the agent's
  **owner** — no admin required, because it is a statement about a surface only they control.

**Fourteen signable kinds, one discipline.** Thirteen kinds each have their own validator — agent · ontology · ontology extension · skill · extension · knowledge repository · planner repository · data product · action type · flow · dashboard · canvas · assurance module — and the agent pack is the fourteenth, signed as an aggregate of its members. Every kind is validated first, then attested; a new kind is one registered validator function.

**Sign-off validates, then attests.** Every category has a deterministic, zero-token validator before an Ed25519 attestation is bound to the exact content version. The acts themselves — sign-off, attestation of a deployment, revocation — are the enterprise package's (`trust-signing`); the public core shows every state, names the owed act and verifies the signatures that arrive with a pack or a subscription, but does not sign:

| Category | What sign-off checks |
|---|---|
| Ontology (KG) · Skill · Agent | conformance · prompt-injection safety · compositional (bottom-up) |
| Extension · Flow | code-safety (AST) + injection + SQL/markup |
| Knowledge · Planner repos | document/case integrity + injection + SQL safety |
| Data product · Action type · Ontology extension | the earned signature: the proof report that passed the gate (V1–V7 incl. live probes · A1–A6 · the five extension refusals) is inside what is signed |
| Assurance module | code-safety + capability honesty — a claimed tier must be provable |
| Agent Pack | aggregate — trusted only if every bundled member is |
| Dashboard · Canvas | tile-query safety · XSS/markup safety |

**Key properties:**

- **Compositional & guided** — signing an agent refuses unless every ingredient is already trusted, guiding you through a bottom-up flow.
- **Offline-verifiable** — signatures verify with only the signer's public key; a standalone verifier needs no Uderia install.
- **Tamper-evident** — any content edit flips 🟢 → 🟠 automatically.
- **Survives the marketplace** — trust travels with **content, not ids**, so a signed artefact stays signed across publish → subscribe/install/fork, gated by an **honesty check** (a copy is trusted only if byte-identical to what was signed). A read-only "publisher signed" seal shows trust *before* you install.
- **Badge, not gate** — nothing is ever blocked from being shared; trust is purely additive. With no attestations, Uderia is byte-identical to before.
- **Trust ≠ access** — a signed artefact is *validated*, not *authorized*; secrets never travel with a shared agent.
- **Renewed after a sync** — under the owner's standing policy (Platform Jobs → *After a sync that changes content*) a synced repository's and its agents' signatures are renewed, and the renewal reaches every subscriber of a pack that ships it.

**Agent Authority — the Agent Principal.** Trust's companion (an agent's *identity* is its
Profile; this governs its *authority* — **trust ≠ access**): when an agent acts **on its own**
(a Coordinate expert, a scheduled task, a flow node, an MCP call) it acts as a **scoped
principal derived from its owner** — a *strict subset* of the owner's authority, enforced
**fail-closed at every tool call**. Scope what an autonomous agent may do (block writes, deny
specific tools, restrict connectors, cap per-run cost) from a simple profile card; every
autonomous action is attributed to the agent — not you — in the audit trail. Untouched, an
agent inherits your full authority (byte-identical to before). An agent can also be
confined to a data slice: its data surface of signed data products becomes its entire data authority ([OBDA-READ →](../Architecture/OBDA_ARCHITECTURE.md)).

Architecture: [**Trust Membrane (docs/Architecture/TRUST_MEMBRANE_ARCHITECTURE.md)**](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md) · [**Agent Authority (docs/Architecture/AGENT_AUTHORITY_ARCHITECTURE.md)**](../Architecture/AGENT_AUTHORITY_ARCHITECTURE.md) · validator model: `docs/roadmap/TIER_5.2_VALIDATORS.md` · portability: `docs/roadmap/TIER_5.2_PORTABILITY.md`
