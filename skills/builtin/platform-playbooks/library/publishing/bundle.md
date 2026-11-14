# Bundle agents into a pack

Put some of the user's agents into a new draft pack — ready to be proposed and published by the user — or keep a pack's name, description and version right.

**Use for:** "bundle @SALES and @DOCS into a pack", "make a pack from my policing agents", "rename the Sales Assist pack", "set the pack's version to 1.2.0", "package these agents for the marketplace".

## Steps

1. call `profiles.list` with `fields: ["tag", "name", "profile_type"]` and agree which agents go in. A coordinator brings its experts' purpose with it — say so if an expert is left out.
2. call `agent_packs.create_and_install` with `profile_ids` (the agents' tags), `name` and `description`. It is **submitted for approval** unless the agent may run changes itself; the pack starts as a draft, and its agents stay where they are.
3. An existing pack: call `agent_packs.list` with `fields: ["installation_id", "name", "version"]`, then call `agent_packs.update` with `installation_id` and only what changes (`name`, `description`, `version`).

## Reading the result

- `status: awaiting_approval`: waiting in the user's Assurance Inbox.
- "no agent of yours …": a tag that is not the user's — take one from step 1.

## What to tell the user

What the pack holds and what comes next: *"I've submitted a draft pack 'Sales Assist' with @SALES and @DOCS. Once it exists, you propose and publish it from Agent Packs — publishing is yours."*

## A person's act

Proposing, publishing, choosing who receives the pack, and signing it (`agent_packs.lifecycle_transition`, `agent_packs.publish`, `marketplace.agent_pack_audience_set`, `trust.agent_pack_sign_off`) are the user's.
