# Change what an agent is made of

Change an existing agent's composition — its model, its knowledge repositories, its experts, its skills, its capabilities, its context window — and submit the change for the user's approval.

**Use for:** "add the chart component to @SALES", "give @DOCS the Trust campaign corpus too", "turn off dashboards for my agent", "add a skill to my agent", "let @COORD also consult @HR", "switch @SALES to another model".

## Steps

1. call `profiles.overview` with `profile_id` set to the agent's tag. It shows the agent's class and what it is made of now — say what will change against it.
2. Take each new ingredient from its list, never from memory:
   - knowledge: call `rag.collections` with `light: true` (only `repository_type: knowledge`)
   - experts: call `profiles.list` with `fields: ["tag", "name", "profile_type"]`
   - capabilities: call `components.list` with `fields: ["component_id", "display_name", "description"]`
   - skills: call `skills.list` with `fields: ["skill_id", "description"]`
   - model: call `llm.configurations` with `fields: ["id", "name", "provider", "model"]`
   - context window: call `context_window_types.list`
3. call `profiles.update` with `profile_id` and **only what changes**:
   - `componentConfig: {"chart": true}` — one component on or off; the others stay as they are
   - `skillsConfig: [{"id": "chart-expert", "active": true}]` — one skill on or off; the others stay
   - `knowledgeConfig: {"collections": [...]}` — the **whole** list of repositories it should answer from (a list is replaced as sent: keep the ones it already has)
   - `genieConfig: {"slaveProfiles": [...]}` — the **whole** list of experts, the same way
   - `llmConfigurationId`, `contextWindowTypeId` — by name
   It is **submitted for the user's approval** — it has not run.

## Reading the result

- `status: awaiting_approval` with a `request_id`: the change waits in the user's Assurance Inbox; the request shows the agent's composition exactly as it will be saved.
- "no … of yours — yours are: …": correct it once with a value from that list.

## What to tell the user

The change in one sentence and that it waits for them: *"I've submitted turning on the chart component for @SALES — the rest of its setup is unchanged. It waits for your approval in the Assurance Inbox."* A signed agent's signature no longer matches once a change to what it is made of is approved — say they will need to sign it again.

## A person's act

Approving the change and signing the agent again (`trust.agent_sign_off`) are the user's. Never attempt them.

## Do not

- Send a list (repositories, experts) without the entries the agent already has, unless the user asked to remove them.
- Change the agent's class — create a new agent instead.
- Submit the same change twice.
