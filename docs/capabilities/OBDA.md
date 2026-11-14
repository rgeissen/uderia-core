# ✍️ OBDA-MODEL & OBDA-ACT — Agents That Act on the Business, Under Signature

> *Reading data (OBDA-READ) is the first half. OBDA-MODEL and OBDA-ACT are the second: agents that change the model and the records — and every change is a signed, checked, receipted act.*

OBDA-READ governs how agents **read** (signed data products). OBDA-MODEL and OBDA-ACT govern how they **write**, with the same pattern: a contract on the ontology, a machine gate that proves it, a signature, and a receipt for every use.

- **Grow the model from a question.** When the model lacks a concept a question needs, the agent proposes it; a normalisation advisor checks where it belongs, you review the draft, and a person lands it on the warehouse — in its own extension, never in the base model. The landing follows a design standard whose rules were each executed on the target, and the dictionary is read back to prove what landed. Teradata and PostgreSQL.
- **Signed actions.** An *action type* is a write contract (target, allowed attributes, conditions, a row bound, the database role) proven by a six-step gate against the live warehouse, then signed. Agents perform only the actions they are granted; every write runs in one transaction with its checks, is recorded in a ledger with the row's previous values, can be undone, and carries a sealed receipt.
- **The posture decides.** *Enforce* writes, *review* parks the exact write for a person, *observe* records what would happen. In conversation the agent prepares and you confirm with one click.
- **Four eyes.** Name a supervisor for an agent and its parked writes are decided by that person only — never by the agent's owner, never by an agent. The supervisor can ask their own agent what awaits them and approve from the answer.
- **Every face, one core.** Conversation, REST, MCP, flow steps, schedules and dashboard tiles all go through the same perform seam, under the same grant and posture.
- **Faster queries, approved by a person.** A workload recommender reads the queries agents actually ran and proposes statistics or indexes; a person approves, the gain is measured before and after, and the result is signed.

Both are components: turn them on under **Administration → Components** (globally, or per user) and in an agent's **Capabilities**; an agent is offered them only when a graph is bound. Off, Uderia behaves exactly as if OBDA-MODEL and OBDA-ACT never shipped. Whether a plane accepts only its contracts is a separate policy (`off · observe · enforce`) under **Administration → Config → Trust → OBDA policy**.

Architecture: [**OBDA-MODEL · OBDA-ACT (docs/Architecture/ACT_ARCHITECTURE.md)**](../Architecture/ACT_ARCHITECTURE.md) — the live contract; it links the design record (options considered and rejected) for the history · reading side: [OBDA-READ →](../Architecture/OBDA_ARCHITECTURE.md)
