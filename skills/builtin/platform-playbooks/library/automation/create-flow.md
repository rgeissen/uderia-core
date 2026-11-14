# Create a flow from a template

Build a multi-step automation from one of the platform's topology templates — a pipeline, a fork–join, a debate — with the user's agents in its slots, and submit it for their approval.

**Use for:** "build a flow where @SALES and @DOCS answer and @COORD combines them", "create a pipeline of three agents", "set up a debate between two agents", "make an automation from a template", "copy the platform's weekly report flow".

## Steps

1. call `flows.templates` with `fields: ["key", "label", "description", "available", "slots"]` and agree the shape with the user. Only `available: true` templates can be used; each slot has a `role` (`stage`, `worker`, `judge`, `synthesiser`, …).
2. call `profiles.list` with `fields: ["tag", "name", "profile_type"]` and agree which agent fills each role.
3. To show what it will look like first: call `flows.template` with `key` (and `source_profile_id` set to a coordinator's tag to pre-fill its experts). Its open slots are the ones still to fill.
4. call `flows.create` with `template`, `name`, `description` and `fills` — each role or slot to an agent's tag, e.g. `{"worker": "@SALES", "synthesiser": "@COORD"}`. It is **submitted for approval** — nothing exists yet.
5. A ready-made platform flow instead: call `flows.platform` with `fields: ["flow_id", "name", "description"]`, then call `flows.fork` with its `flow_id` and a `name`.

## Reading the result

- `status: awaiting_approval`: waiting in the user's Assurance Inbox.
- After approval, `fill_report` lists any slot left open — the flow cannot run until each is filled.

## What to tell the user

The shape, the agents in it and that it waits for them: *"I've submitted 'Quarterly answer' — a fork–join where @SALES and @DOCS work in parallel and @COORD combines them. Once you approve it, you can run it, or open it in Flows to change its steps."*

## Do not

- Write the flow's graph yourself — node by node editing is the flow component's (TDA_Flow); here a flow comes from a template or a copy.
- Fill a slot with an agent the user did not agree to.
