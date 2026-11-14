# Is the pack ready to publish

Whether one of the user's packs can be published now, and if not, what stands in the way and who acts next.

**Use for:** "is Sales Assist ready to publish", "what blocks my pack", "can I publish my pack", "why can't I publish", "where is my pack in approval".

## Steps

1. call `agent_packs.list` with `fields: ["installation_id", "name", "version", "marketplace_visibility"]` and agree which pack.
2. call `agent_packs.lifecycle` with `installation_id` — its state (draft, proposed, approved, published) and its audit trail.
3. call `agent_packs.governance_policy` — whether a pack needs approval, who approves, and whether an untrusted pack may be published.
4. call `agent_packs.get` with `installation_id` for its agents, then call `trust.agents` with `fields: ["tag", "state"]` and read the state of each agent in the pack.

## Reading the result

- `require_approval` with the pack still a draft: the user proposes it; an approver (the policy's `approver_tiers`) approves it.
- `block_untrusted_publish` with an agent that is not signed or reads changed: that agent is the blocker — name it.
- Already `published`: say so, and whether a newer version waits to be pushed to subscribers (that push is the user's).

## What to tell the user

Ready or not, and the next act with whose it is: *"Sales Assist is a draft. Publishing needs approval, and @DOCS reads changed since it was signed — sign @DOCS again, then propose the pack; an administrator approves it."*

## A person's act

Signing an agent or the pack, proposing, approving and publishing are never done by an agent.
