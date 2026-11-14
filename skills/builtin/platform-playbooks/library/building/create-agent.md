# Create an agent

Compose a new agent of any class from what the user already has — its model, its data source, its knowledge, its experts, its skills and capabilities — and submit it for the user's approval.

**Use for:** "create an agent for …", "build me an agent that …", "make a new Optimize agent on the sales database", "build an agent that answers from our documentation", "a coordinator over @SALES and @DOCS".

## Steps

1. Agree the essentials with the user before you submit anything: a tag (`@SALES` — short, unique), a name, one sentence on what it is for, and its class:
   - **Ideate** (`llm_only`) — conversation; optionally its MCP server's tools
   - **Focus** (`rag_focused`) — answers from knowledge repositories
   - **Optimize** (`tool_enabled`) — answers from a database through its MCP server
   - **Coordinate** (`genie`) — routes each question to expert agents and combines their answers
2. call `llm.configurations` with `fields: ["id", "name", "provider", "model"]` and take the model the user names. When they name none, call `profiles.default` with `fields: ["tag", "llmConfigurationId"]` and use its `llmConfigurationId`.
3. What the class needs — take each from the list, never from memory:
   - **Optimize**, or **Ideate with tools**: call `mcp.servers` with `fields: ["id", "name", "host"]` and take the data source the user names. For Ideate also send `useMcpTools: true`.
   - **Focus**: call `rag.collections` with `light: true` and `fields: ["id", "name", "repository_type", "document_count"]`; offer only `repository_type: knowledge`. Send them as `knowledgeConfig: {"collections": ["<name or id>", …]}`. An empty repository answers nothing — say so if the user picks one.
   - **Coordinate**: call `profiles.list` with `fields: ["tag", "name", "profile_type"]` and agree which agents it consults. Send them as `genieConfig: {"slaveProfiles": ["@SALES", "@DOCS"]}`.
4. Capabilities and skills, only when the user asks for them (what is not named takes the platform's default for the class):
   - call `components.list` with `profile_type` set to the class and `fields: ["component_id", "display_name", "description"]`; send `componentConfig: {"chart": true, "dashboard": false}`.
   - call `skills.list` with `fields: ["skill_id", "description"]`; send `skillsConfig: ["<skill id>", …]`.
   - A context window other than the default: call `context_window_types.list` and send `contextWindowTypeId` with its name.
5. call `profiles.create` with `tag`, `name`, `description`, `profile_type`, `llmConfigurationId` and what step 3 and 4 gave you. It is **submitted for the user's approval** — it has not run.

## Reading the result

- `status: awaiting_approval` with a `request_id`: the request waits in the user's Assurance Inbox. Nothing exists yet.
- An error naming an argument, or "no … of yours — yours are: …": correct it once with a value from that list. Never invent an id or a name.

## What to tell the user

What will be created, in one sentence, and that it waits for them: *"I've submitted @DOCS — a Focus agent on Gemini 3.1 Flash Lite answering from Uderia Documentation — for your approval in the Assurance Inbox. Once you approve it, test it, then sign it if you want it trusted."*

## A person's act

Approving the request, signing the agent (`trust.agent_sign_off`) and opening it to the REST API (`profiles.set_active_for_consumption`) are the user's. Never attempt them.

## Do not

- Submit before the user has agreed the tag and the class.
- Submit the same agent twice — one request, then wait for the user.
- Create a repository, upload documents or build a knowledge graph — that is the Knowledge Curator's work; tell the user which repository is missing.
