# Platform Operations

> **Generated** from the `@operation` descriptors by
> `python maintenance/platform_operations.py docs --write` — do not edit by hand; the gate
> (`test/test_platform_operations.py`) fails when this file is stale. Contract:
> [`api/operations.py`](../../src/trusted_data_agent/api/operations.py). This catalog
> supersedes [MCP_TIER_MATRIX.md](MCP_TIER_MATRIX.md) for every described route.

## Coverage

**240 of 740** served routes described (32.4%); 500 in the backlog.

| effect | count | | scope | count | | tier | count |
|---|---|---|---|---|---|---|---|
| `read` | 129 | | `own` | 201 | | `user` | 227 |
| `write` | 47 | | `shared` | 16 | | `developer` | 0 |
| `human_only` | 61 | | `cross_user` | 3 | | `admin` | 13 |
| `never` | 3 | | `instance` | 20 | |  |  |

22 write(s) govern what an agent may do.

## Operations

### `admin`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `admin.db_health` | `GET /api/v1/admin/db-health` | read | instance | admin | The database's health: unreadable reads since the start, the last backup and how many are kept |
| `admin.platform_jobs` | `GET /api/v1/admin/scheduler/platform-jobs` | read | instance | admin | The platform's own maintenance jobs (usage resets, price sync, backups): their schedules and how their last runs went |
| `admin.stats` | `GET /api/v1/admin/stats` | read | instance | admin | The deployment's users in numbers: how many, active, locked, by tier, recent sign-ins and registrations — counts only |

### `agent_packs`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `agent_packs.create_and_install` | `POST /api/v1/agent-packs/create-and-install` | write | own | user | Bundle some of the caller's agents into a new draft pack — the agents stay where they are; removing the pack later leaves them |
| `agent_packs.get` | `GET /api/v1/agent-packs/<int:installation_id>` | read | own | user | One of the caller's packs in full: its agents, repositories, graphs and skills |
| `agent_packs.governance_policy` | `GET /api/v1/agent-packs/governance/policy` | read | instance | user | This deployment's rules for publishing packs: whether a pack needs approval, who approves, whether an untrusted pack may be published |
| `agent_packs.governance_policy_set` | `PUT /api/v1/agent-packs/governance/policy` | human_only | instance | admin | Set the deployment's rules for publishing packs — an administrator's act |
| `agent_packs.lifecycle` | `GET /api/v1/agent-packs/<int:installation_id>/lifecycle` | read | own | user | Where a pack stands on its way to publication — draft, proposed, approved, published — with its audit trail and the policy that applies |
| `agent_packs.lifecycle_transition` | `POST /api/v1/agent-packs/<int:installation_id>/lifecycle/<action>` | human_only | own | user | Move a pack along its lifecycle — propose, approve, reject, publish, deprecate — each a person's decision |
| `agent_packs.list` | `GET /api/v1/agent-packs` | read | own | user | The caller's agent packs — bundles of agents with their knowledge and skills: what each holds, its version, whether and how it is published |
| `agent_packs.notify_author` | `POST /api/v1/agent-packs/notify-author` | human_only | own | user | Email the author of a pack's source — a message from you to someone outside Uderia |
| `agent_packs.notify_author_update` | `POST /api/v1/agent-packs/notify-author-update` | human_only | own | user | Email the author of a pack's source about an update — a message from you to someone outside Uderia |
| `agent_packs.publish` | `POST /api/v1/agent-packs/<int:installation_id>/publish` | human_only | own | user | Publish a pack to the marketplace — the owner puts their name to what others will run |
| `agent_packs.sla` | `POST /api/v1/agent-packs/<int:installation_id>/sla` | human_only | own | user | Set a pack's service level — a promise to its subscribers, the owner's to make |
| `agent_packs.sync_subscribers` | `POST /api/v1/agent-packs/<int:installation_id>/sync-subscribers` | human_only | own | user | Push a pack's new version to everyone subscribed to it — it changes other people's agents, so the owner decides |
| `agent_packs.update` | `PUT /api/v1/agent-packs/<int:installation_id>` | write | own | user | Rename a pack, change its description or its version |

### `assurance`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `assurance.agent_story` | `GET /api/v1/assurance/agent-story/<tag>` | read | own | user | The life of one agent from its records: when it was built, signed, changed, what its gates caught and how its quality moved |
| `assurance.inbox` | `GET /api/v1/assurance/inbox` | read | own | user | The caller's Assurance Inbox: trust incidents to look at — answers withheld or flagged, gates that fired, agents whose trust changed |
| `assurance.inbox_ack` | `POST /api/v1/assurance/inbox/<event_id>/ack` | human_only | own | user | Acknowledge an incident in the Assurance Inbox — the person who looked at it says so; an agent never does |
| `assurance.inbox_ack_derived` | `POST /api/v1/assurance/inbox/derived/ack` | human_only | own | user | Acknowledge a live finding in the Assurance Inbox — the person who looked at it says so; an agent never does |
| `assurance.inbox_count` | `GET /api/v1/assurance/inbox/count` | read | own | user | How many incidents in the caller's Assurance Inbox are not acknowledged yet |
| `assurance.review_decide` | `POST /api/v1/assurance/reviews/<item_id>/<decision>` | human_only | own | user | Approve or reject a review item — the approval is the control; an agent is never its own reviewer |
| `assurance.review_withdraw` | `DELETE /api/v1/assurance/reviews/<item_id>` | human_only | own | user | Withdraw one of the caller's pending review items — the owner's call, never an agent's |
| `assurance.reviews` | `GET /api/v1/assurance/reviews` | read | own | user | The caller's review queue: answers withheld for a person and changes agents submitted, with why each waits and who may decide |
| `assurance.trust_history` | `GET /api/v1/assurance/trust-history/<tag>` | read | own | user | One agent's trust changelog: every signature, attestation, change and renewal, oldest first |
| `assurance.trust_trip` | `GET /api/v1/assurance/trust-trip/<tag>` | read | own | user | One agent's trip logbook: every point from which its readings were counted afresh, newest first |
| `assurance.trust_trip_reset` | `POST /api/v1/assurance/trust-trip/<tag>/reset` | human_only | own | user | Start counting one agent's trust readings afresh — the owner decides when a new leg begins, never an agent |
| `assurance.vocabulary` | `GET /api/v1/assurance/vocabulary` | read | instance | user | Every assurance mechanism in words — what each gate, measure and station checks, its modes and its limits |

### `canvases`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `canvases.delete` | `DELETE /api/v1/canvases/<canvas_id>` | write | own | user | Delete a canvas and its versions |
| `canvases.get` | `GET /api/v1/canvases/<canvas_id>` | read | own | user | One canvas with its content and sources |
| `canvases.list` | `GET /api/v1/canvases` | read | own | user | The caller's canvases — documents and code kept from conversations: title, language, when changed |
| `canvases.update` | `PUT /api/v1/canvases/<canvas_id>` | write | own | user | Rename a canvas |
| `canvases.version_restore` | `POST /api/v1/canvases/<canvas_id>/versions/<version_id>/restore` | write | own | user | Put a saved version back as the canvas's content |
| `canvases.versions` | `GET /api/v1/canvases/<canvas_id>/versions` | read | own | user | A canvas's saved versions |

### `components`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `components.get` | `GET /api/v1/components/<component_id>` | read | instance | user | One component in full: its manifest, its tool and the arguments the tool takes |
| `components.list` | `GET /api/v1/components` | read | instance | user | The components available on this deployment — capabilities such as charts, canvas, dashboards, memory: what each component does, which classes it supports, whether it starts on |

### `consumption`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `consumption.history` | `GET /api/v1/consumption/history` | read | own | user | The caller's consumption in earlier periods, one record per period |
| `consumption.summary` | `GET /api/v1/consumption/summary` | read | own | user | The caller's consumption this period: tokens, cost, turns, sessions, limits and how much of each is used |
| `consumption.system_summary` | `GET /api/v1/consumption/system-summary` | read | instance | admin | The deployment's consumption this period, all users together |
| `consumption.turns` | `GET /api/v1/consumption/turns` | read | own | user | The caller's most recent turns, each with its tokens, cost, model and agent |
| `consumption.users` | `GET /api/v1/consumption/users` | read | cross_user | admin | Every user's consumption this period |
| `consumption.warnings` | `GET /api/v1/consumption_warnings` | read | own | user | The caller's usage warnings (limits close or reached) |

### `context_window_types`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `context_window_types.get` | `GET /api/v1/context-window-types/<context_window_type_id>` | read | own | user | One context window type: its modules, their budgets and the order they are condensed in |
| `context_window_types.list` | `GET /api/v1/context-window-types` | read | own | user | The caller's context window types — how an agent's context budget is shared between history, knowledge, tools and the rest |

### `costs`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `costs.analytics` | `GET /api/v1/costs/analytics` | read | own | admin | Cost analytics over the caller's conversations: by model and provider, the trend, the costliest questions and conversations |
| `costs.coverage` | `GET /api/v1/costs/coverage` | read | instance | admin | Which models in use on this deployment have a price, and which run unpriced |
| `costs.models` | `GET /api/v1/costs/models` | read | instance | admin | The deployment's price list: input and output cost per million tokens for each model, and where each price came from |

### `dashboards`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `dashboards.create` | `POST /api/v1/dashboards` | write | own | user | Create an empty dashboard — its tiles are pinned from conversations (the dashboard component) |
| `dashboards.delete` | `DELETE /api/v1/dashboards/<dashboard_id>` | write | own | user | Delete a dashboard and its tiles |
| `dashboards.get` | `GET /api/v1/dashboards/<dashboard_id>` | read | own | user | One dashboard with its settings and tiles |
| `dashboards.layout` | `PUT /api/v1/dashboards/<dashboard_id>/layout` | write | own | user | Arrange a dashboard: positions and sizes of several tiles at once |
| `dashboards.list` | `GET /api/v1/dashboards` | read | own | user | The caller's dashboards: name, description, how many tiles, which is the default |
| `dashboards.tile_delete` | `DELETE /api/v1/dashboards/<dashboard_id>/tiles/<tile_id>` | write | own | user | Remove a tile from a dashboard |
| `dashboards.tile_refresh` | `POST /api/v1/dashboards/<dashboard_id>/tiles/<tile_id>/refresh` | write | own | user | Refresh a tile: its own agent answers its question again, under its own authority, in the tile's conversation |
| `dashboards.tile_update` | `PUT /api/v1/dashboards/<dashboard_id>/tiles/<tile_id>` | write | own | user | Rename a tile, move it or resize it |
| `dashboards.tiles` | `GET /api/v1/dashboards/<dashboard_id>/tiles` | read | own | user | A dashboard's tiles: what each shows, the agent behind it, when it was last refreshed and how that went |
| `dashboards.update` | `PUT /api/v1/dashboards/<dashboard_id>` | write | own | user | Rename a dashboard, change its description or its theme |

### `data_products`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `data_products.catalog` | `GET /api/v1/data-products` | read | own | user | The data products defined on a knowledge graph — each one's measures and dimensions, its trust state and whether a validation backs it |
| `data_products.compile` | `POST /api/v1/data-products/<product_name>/compile` | read | own | user | Turn a request against a data product into the SQL it would run — nothing is run; a refused request names the closest valid names |
| `data_products.get` | `GET /api/v1/data-products/<product_name>` | read | own | user | One data product's full definition: its manifest, its validation report and which combinations are safe to ask |

### `evals`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `evals.agent_runs` | `GET /api/v1/evals/agents/<tag>/runs` | read | own | user | One agent's scored turns, newest first, each with its verdict on every measure (a coordinator's with its experts' beneath) |
| `evals.agent_timeline` | `GET /api/v1/evals/agents/<tag>/timeline` | read | own | user | One agent over time: pass rate, cost, latency and volume per day (or hour, or week) — where a regression starts |
| `evals.backtest` | `POST /api/v1/evals/backtest` | read | own | user | What a proposed trust policy would have done to the caller's past turns — answers withheld, flagged, sent to a judge — at no token cost |
| `evals.kpi_dashboard` | `GET /api/v1/evals/agents/kpi-dashboard` | read | own | user | Every agent's scoreboard: pass rate, turns, cost and latency, the checks failing most, and the quality trend |
| `evals.library_outcomes` | `GET /api/v1/evals/library-outcomes` | read | own | user | For skill-library sections: did agents read a section before running what it teaches, and did that run succeed |
| `evals.online` | `GET /api/v1/evals/online` | read | own | user | The latest quality readings of the caller's turns as they were scored live, newest first |
| `evals.online_dashboard` | `GET /api/v1/evals/online/dashboard` | read | own | user | The caller's live quality in one view: pass rate per measure, per agent and per class, and the recurring failure clusters |
| `evals.score_turn` | `POST /api/v1/evals/score-turn` | write | own | user | Score one turn of the caller's conversation again — against assertions or a reference answer, optionally with model judges |
| `evals.scoring_thresholds` | `GET /api/v1/evals/scoring-thresholds` | read | instance | user | Where green, amber and red begin for every quality measure on this deployment |
| `evals.trust_behavior` | `GET /api/v1/evals/agents/trust-behavior` | read | own | user | Each agent's behavioural trust score: how its signature, its gates and its quality readings combine, and what pulls it down |
| `evals.turn_score` | `GET /api/v1/evals/turn-score` | read | own | user | The quality verdicts recorded for one turn of one of the caller's conversations — read, nothing is scored again |

### `files`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `files.knowledge_targets` | `GET /api/v1/files/knowledge-targets` | read | own | user | The knowledge repositories a file can be added to — the caller's own |
| `files.list` | `GET /api/v1/files` | read | own | user | The caller's files — uploads, kept answers, tables, charts and exports — newest first, searchable by name |
| `files.to_knowledge` | `POST /api/v1/files/<file_id>/to-knowledge` | write | own | user | Add a document from the caller's Files to one of their knowledge repositories — screened, chunked and embedded by the repository's own upload |

### `flows`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `flows.create` | `POST /api/v1/flows` | write | own | user | Create a flow from a topology template, its slots filled, or an empty one |
| `flows.delete` | `DELETE /api/v1/flows/<flow_id>` | write | own | user | Delete a flow, its versions and its run history |
| `flows.fork` | `POST /api/v1/flows/<flow_id>/fork` | write | shared | user | Copy a platform flow (or one of the caller's own) into the caller's flows |
| `flows.get` | `GET /api/v1/flows/<flow_id>` | read | own | user | One flow in full: its nodes, edges, agents and open slots |
| `flows.list` | `GET /api/v1/flows` | read | own | user | The caller's flows — multi-step automations — with each one's open slots |
| `flows.platform` | `GET /api/v1/flows/platform` | read | shared | user | The platform's shared flows — ready-made automations any user can fork |
| `flows.run` | `POST /api/v1/flows/<flow_id>/execute` | write | own | user | Run a flow now — its agents answer and its steps act; returns the run to follow |
| `flows.run_cancel` | `POST /api/v1/flows/runs/<run_id>/cancel` | write | own | user | Stop a running flow before its next step |
| `flows.run_status` | `GET /api/v1/flows/runs/<run_id>` | read | own | user | One flow run: its status, its result and what each node did |
| `flows.run_stream` | `GET /api/v1/flows/runs/<run_id>/stream` | never | own | user | A run's live events as a browser stream — the page's plumbing; agents follow flows.run_status |
| `flows.runs` | `GET /api/v1/flows/<flow_id>/runs` | read | own | user | A flow's recent runs, newest first: status, tokens, cost, when |
| `flows.template` | `GET /api/v1/flows/templates/<key>` | read | own | user | Preview one template: the flow it would create for these parameters, pre-filled from an agent, with the slots still open |
| `flows.templates` | `GET /api/v1/flows/templates` | read | instance | user | The flow topology templates a flow can be created from: their parameters, ranges and the agent slots to fill |
| `flows.toggle_platform` | `POST /api/v1/flows/<flow_id>/toggle-platform` | human_only | instance | admin | Make a flow one of the platform's shared flows, or stop sharing it — an administrator publishing to every user |
| `flows.update` | `PUT /api/v1/flows/<flow_id>` | write | own | user | Rename a flow or change its description |
| `flows.variables` | `GET /api/v1/flow-variables` | read | own | user | The caller's flow variables — plain constants every flow can use |
| `flows.version_restore` | `POST /api/v1/flows/<flow_id>/versions/<version_id>/restore` | write | own | user | Put a saved version back as the flow's current graph |
| `flows.version_save` | `POST /api/v1/flows/<flow_id>/versions` | write | own | user | Save the flow as it is now as a named version (nothing saved when unchanged) |
| `flows.versions` | `GET /api/v1/flows/<flow_id>/versions` | read | own | user | A flow's saved versions, oldest first |

### `input_requests`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `input_requests.answer` | `POST /api/v1/input-requests/<request_id>/answer` | human_only | own | user | Answer a question an agent asked — the person's act; the work resumes bound to the answer |
| `input_requests.get` | `GET /api/v1/input-requests/<request_id>` | read | own | user | One question an agent put to the caller: its items, who asked and on what grounds, its state and answer |
| `input_requests.list` | `GET /api/v1/input-requests` | read | own | user | The caller's questions from agents — pending by default: a turn that stopped to ask, waiting for the person |
| `input_requests.stats` | `GET /api/v1/input-requests/stats` | read | own | user | The caller's needs-input readings: questions asked, answered, declined, expired, who raised them, median time to answer — per agent or all |
| `input_requests.withdraw` | `POST /api/v1/input-requests/<request_id>/withdraw` | human_only | own | user | Decline a question an agent asked — the person's act; the turn ends unanswered and says so |

### `knowledge`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `knowledge.document_acknowledge_finding` | `POST /api/v1/knowledge/documents/<document_id>/acknowledge-finding` | human_only | own | user | Acknowledge a security finding on a document with a reason — a recorded human judgement that enters the repository's signature |
| `knowledge.document_delete` | `DELETE /api/v1/knowledge/repositories/<int:collection_id>/documents/<document_id>` | write | own | user | Remove a document from a knowledge repository — its chunks leave retrieval |
| `knowledge.document_quarantine` | `POST /api/v1/knowledge/documents/<document_id>/quarantine` | write | own | user | Quarantine a document: it stays listed in its repository but no agent retrieves it |
| `knowledge.document_release` | `POST /api/v1/knowledge/documents/<document_id>/unquarantine` | human_only | own | user | Release a quarantined document back into retrieval — the owner's judgement, never an agent's |
| `knowledge.document_source` | `PATCH /api/v1/knowledge/repositories/<int:collection_id>/documents/<document_id>` | human_only | own | user | Set where a document syncs from (a URL or path the server reads) — the owner's to choose: an agent could point the server at anything it can reach |
| `knowledge.documents` | `GET /api/v1/knowledge/repositories/<int:collection_id>/documents` | read | own | user | The documents in one knowledge repository: name, type, size, chunks and when each was added |
| `knowledge.feedback` | `GET /api/v1/knowledge/repositories/<int:collection_id>/retrieval-feedback` | read | own | user | How the caller rated the answers each document of a repository served — the lowest-rated documents first |
| `knowledge.health` | `GET /api/v1/knowledge/repositories/<int:collection_id>/scan-manifest` | read | own | user | A knowledge repository's health: how many documents are screened, the injection and exfiltration findings, the personal data found (labels and counts), the quarantined documents |
| `knowledge.repository_settings` | `PATCH /api/v1/knowledge/repositories/<int:collection_id>` | write | own | user | Change a knowledge repository's settings: its description, how often it syncs, its sensitivity label, whether its embedding model is locked |
| `knowledge.scan` | `POST /api/v1/knowledge/repositories/<int:collection_id>/scan` | write | own | user | Screen a knowledge repository's documents for injection, exfiltration and personal data — the unscreened ones, or all again |
| `knowledge.search` | `POST /api/v1/knowledge/repositories/<int:collection_id>/search` | read | own | user | Search one knowledge repository the way an agent retrieves from it: the passages a question finds and how close each is |
| `knowledge.sync` | `POST /api/v1/knowledge/repositories/<int:collection_id>/sync` | write | own | user | Sync a knowledge repository now: every document kept in step with its source is re-read, changed ones re-ingested |

### `knowledge_graph`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `knowledge_graph.activate` | `PATCH /api/v1/knowledge-graph/assignments/activate` | write · governs | own | user | Make a graph the one an agent reads — its other graphs stay assigned but inactive |
| `knowledge_graph.apply_update` | `POST /api/v1/knowledge-graph/apply-update` | human_only | own | user | Rebuild a graph from its source and replace it — saved as a version before and after |
| `knowledge_graph.assignments` | `GET /api/v1/knowledge-graph/assignments` | read | own | user | Which agents a graph is assigned to, and for which of them it is the active graph |
| `knowledge_graph.assignments_set` | `POST /api/v1/knowledge-graph/assignments` | write · governs | own | user | Set which agents a graph is assigned to — the list REPLACES the current one, so name every agent that should keep it |
| `knowledge_graph.authored` | `GET /api/v1/knowledge-graph/authored` | read | own | user | What people added to a graph by hand — drafted entities and attributes, and the design decisions behind them |
| `knowledge_graph.authored_deploy` | `POST /api/v1/knowledge-graph/authored/deploy` | human_only · governs | own | user | Create what people drafted on a graph in its database — tables and views |
| `knowledge_graph.authored_publish` | `POST /api/v1/knowledge-graph/authored/publish` | human_only · governs | own | user | Publish what people drafted on a graph as an extension of it |
| `knowledge_graph.authored_withdraw` | `DELETE /api/v1/knowledge-graph/authored/<extension_id>` | human_only · governs | own | user | Withdraw a published extension from a graph |
| `knowledge_graph.clear` | `DELETE /api/v1/knowledge-graph/clear` | human_only | own | user | Delete a graph — its entities, relationships and assignments |
| `knowledge_graph.conformance` | `GET /api/v1/knowledge-graph/<kg_id>/conformance` | read | own | user | Check a graph against the ontology standard — the issues found and how serious each is (nothing is changed) |
| `knowledge_graph.context` | `GET /api/v1/knowledge-graph/context` | read | own | user | What an agent would be told from its knowledge graph about a question — the context it adds before the agent answers |
| `knowledge_graph.entities` | `GET /api/v1/knowledge-graph/entities` | read | own | user | The entities of a graph — tables, columns, business concepts, metrics, products — found by words or by type |
| `knowledge_graph.lifecycle` | `GET /api/v1/knowledge-graph/<kg_id>/lifecycle` | read | own | user | Where an ontology stands in its lifecycle — draft, proposed, approved, published, deprecated — with its version and its audit trail |
| `knowledge_graph.lifecycle_transition` | `POST /api/v1/knowledge-graph/<kg_id>/lifecycle/<action>` | human_only · governs | own | user | Move an ontology along its lifecycle — propose, approve, reject, publish, deprecate — each a person's decision |
| `knowledge_graph.list` | `GET /api/v1/knowledge-graph/list` | read | own | user | The caller's knowledge graphs: the agent each belongs to, the agents it is assigned to, its size and backend |
| `knowledge_graph.marketplace_publish` | `POST /api/v1/knowledge-graph/<profile_id>/publish` | human_only · governs | own | user | Publish a graph to the marketplace — other people can then install, fork or subscribe to it |
| `knowledge_graph.node_neighborhood` | `GET /api/v1/knowledge-graph/node-neighborhood` | read | own | user | Everything directly connected to one entity, and how |
| `knowledge_graph.redeploy_metric_views` | `POST /api/v1/knowledge-graph/redeploy-metric-views` | human_only · governs | own | user | Re-create an industry-model graph's metric views in its database |
| `knowledge_graph.redeploy_physical` | `POST /api/v1/knowledge-graph/redeploy-physical` | human_only · governs | own | user | Re-create an industry-model graph's database — its tables, and optionally its views and demo data |
| `knowledge_graph.relationships` | `GET /api/v1/knowledge-graph/relationships` | read | own | user | The relationships of a graph, or of one entity — what each entity is connected to and how |
| `knowledge_graph.reload_demo_data` | `POST /api/v1/knowledge-graph/reload-demo-data` | human_only · governs | own | user | Replace the demo rows in an industry-model graph's database |
| `knowledge_graph.rename` | `PATCH /api/v1/knowledge-graph/rename` | write | own | user | Rename a graph or change its description |
| `knowledge_graph.schema_overview` | `GET /api/v1/knowledge-graph/schema-overview` | read | own | user | A graph at a glance — its entity types, how many of each, and which types connect to which |
| `knowledge_graph.stats` | `GET /api/v1/knowledge-graph/stats` | read | own | user | How big a graph is and how it is shaped — entities and relationships by type, its connected parts, whether it has cycles |
| `knowledge_graph.subgraph` | `GET /api/v1/knowledge-graph/subgraph` | read | own | user | The part of a graph around one entity — the entities near it and the relationships between them |
| `knowledge_graph.subscriptions` | `GET /api/v1/knowledge-graph/subscriptions` | read | own | user | The marketplace graphs the caller subscribes to, and whether each is up to date with its publisher |
| `knowledge_graph.tuning_decide` | `POST /api/v1/knowledge-graph/tuning/proposals/<rid>/<action>` | human_only · governs | own | user | Approve or reject a tuning proposal, or land an approved one on the database |
| `knowledge_graph.tuning_proposal_delete` | `DELETE /api/v1/knowledge-graph/tuning/proposals/<rid>` | human_only | own | user | Remove a tuning proposal that landed nothing |
| `knowledge_graph.tuning_proposals` | `GET /api/v1/knowledge-graph/tuning/proposals` | read | own | user | Proposed database tuning for a graph's tables — from what its agents actually ran — and where each proposal stands |
| `knowledge_graph.version_delete` | `DELETE /api/v1/knowledge-graph/versions/<version_id>` | human_only | own | user | Delete a saved version of a graph |
| `knowledge_graph.version_restore` | `POST /api/v1/knowledge-graph/versions/<version_id>/restore` | human_only | own | user | Put a graph back to a saved version — replaces what it holds now |
| `knowledge_graph.version_save` | `POST /api/v1/knowledge-graph/versions` | write | own | user | Save a graph as it is now as a version — nothing is saved when it has not changed since the last one |
| `knowledge_graph.versions` | `GET /api/v1/knowledge-graph/versions` | read | own | user | A graph's saved versions, oldest first — when each was saved, its label and its size |

### `llm`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `llm.configurations` | `GET /api/v1/llm/configurations` | read | own | user | The caller's LLM configurations: the name, provider and model of each — the ids an agent is built on |
| `llm.configurations_activate` | `POST /api/v1/llm/configurations/<config_id>/activate` | human_only | own | user | Make an LLM configuration the caller's active one |
| `llm.configurations_context_limit` | `GET /api/v1/llm/configurations/<config_id>/context-limit` | read | own | user | The model's maximum context window for an LLM configuration |
| `llm.configurations_create` | `POST /api/v1/llm/configurations` | human_only | own | user | Add an LLM configuration — a person's act: its credentials are the user's to enter |
| `llm.configurations_delete` | `DELETE /api/v1/llm/configurations/<config_id>` | write | own | user | Delete an LLM configuration — agents built on it stop working |
| `llm.configurations_get` | `GET /api/v1/llm/configurations/<config_id>` | human_only | own | user | One LLM configuration with its decrypted credentials — the user's editor only, never an agent |
| `llm.configurations_test` | `POST /api/v1/llm_configurations/<config_id>/test` | read | own | user | Test an LLM configuration with one small call — it costs a model call |
| `llm.configurations_update` | `PUT /api/v1/llm/configurations/<config_id>` | human_only | own | user | Change an LLM configuration — a person's act: it carries credentials |
| `llm.provider_health` | `GET /api/v1/llm/provider-health` | read | instance | user | Each LLM provider's circuit breaker on this deployment: its state and recent failures |

### `marketplace`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `marketplace.agent_pack` | `GET /api/v1/marketplace/agent-packs/<pack_id>` | read | shared | user | One marketplace pack in full: its agents, what they need, its ratings and versions |
| `marketplace.agent_pack_audience` | `GET /api/v1/marketplace/agent-packs/<pack_id>/targeted-users` | read | own | user | Who a pack the caller published is offered to (its publisher only) |
| `marketplace.agent_pack_audience_set` | `PUT /api/v1/marketplace/agent-packs/<pack_id>/targeted-users` | human_only | own | user | Choose who a published pack is offered to — whom you share with is yours to decide |
| `marketplace.agent_pack_fork` | `POST /api/v1/marketplace/agent-packs/<pack_id>/fork` | human_only | shared | user | Take your own copy of someone else's pack — bringing their agents into your estate is your trust decision |
| `marketplace.agent_pack_install` | `POST /api/v1/marketplace/agent-packs/<pack_id>/install` | human_only | shared | user | Install or subscribe to someone else's pack — bringing another publisher's agents into your estate is your trust decision |
| `marketplace.agent_pack_rate` | `POST /api/v1/marketplace/agent-packs/<pack_id>/rate` | human_only | shared | user | Rate a marketplace pack — a rating is a person's opinion, never an agent's |
| `marketplace.agent_pack_requirements` | `GET /api/v1/marketplace/agent-packs/<pack_id>/mcp-requirements` | read | shared | user | What each agent in a marketplace pack needs from the caller's data sources to run here |
| `marketplace.agent_pack_sync` | `POST /api/v1/marketplace/agent-packs/<pack_id>/sync` | human_only | shared | user | Take a subscribed pack's new version — accepting changed agents into your estate is yours |
| `marketplace.agent_pack_unpublish` | `DELETE /api/v1/marketplace/agent-packs/<pack_id>` | human_only | own | user | Withdraw a pack from the marketplace — its subscribers are affected, so the owner decides |
| `marketplace.agent_pack_unsubscribe` | `DELETE /api/v1/marketplace/agent-packs/<pack_id>/subscribe` | human_only | shared | user | End a subscription to someone else's pack — its agents leave your estate |
| `marketplace.agent_packs` | `GET /api/v1/marketplace/agent-packs` | read | shared | user | The agent packs published in the marketplace that the caller may see — what each offers, by whom, how rated |
| `marketplace.knowledge_graph` | `GET /api/v1/marketplace/knowledge-graphs/<marketplace_id>` | read | shared | user | One marketplace graph — what it contains, its versions and its ratings |
| `marketplace.knowledge_graph_audience` | `GET /api/v1/marketplace/knowledge-graphs/<marketplace_id>/targeted-users` | read | own | user | Who a graph the caller published is offered to |
| `marketplace.knowledge_graph_audience_set` | `PUT /api/v1/marketplace/knowledge-graphs/<marketplace_id>/targeted-users` | human_only · governs | own | user | Choose who a published graph is offered to |
| `marketplace.knowledge_graph_fork` | `POST /api/v1/marketplace/knowledge-graphs/<marketplace_id>/fork` | human_only | shared | user | Fork a marketplace graph — an editable copy the caller owns |
| `marketplace.knowledge_graph_install` | `POST /api/v1/marketplace/knowledge-graphs/<marketplace_id>/install` | human_only | shared | user | Install a marketplace graph as the caller's own copy |
| `marketplace.knowledge_graph_rate` | `POST /api/v1/marketplace/knowledge-graphs/<marketplace_id>/rate` | human_only | shared | user | Rate a marketplace graph |
| `marketplace.knowledge_graph_subscribe` | `POST /api/v1/marketplace/knowledge-graphs/<marketplace_id>/subscribe` | human_only | shared | user | Subscribe to a marketplace graph — a read-only copy that follows its publisher |
| `marketplace.knowledge_graph_sync` | `POST /api/v1/marketplace/knowledge-graphs/<marketplace_id>/sync` | human_only | own | user | Bring a subscribed graph up to date with its publisher |
| `marketplace.knowledge_graph_unpublish` | `DELETE /api/v1/marketplace/knowledge-graphs/<marketplace_id>` | human_only · governs | own | user | Withdraw a graph the caller published from the marketplace |
| `marketplace.knowledge_graph_unsubscribe` | `DELETE /api/v1/marketplace/knowledge-graphs/<marketplace_id>/subscribe` | human_only | own | user | Stop subscribing to a marketplace graph |
| `marketplace.knowledge_graphs` | `GET /api/v1/marketplace/knowledge-graphs` | read | shared | user | The knowledge graphs published in the marketplace that the caller may see — what each covers, by whom, how rated |

### `mcp`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `mcp.resources` | `POST /api/v1/mcp/resources` | never | own | user | The editor's preview of a server's tools from a transport given in the request — UI plumbing |
| `mcp.servers` | `GET /api/v1/mcp/servers` | read | own | user | The caller's MCP servers (data sources): name, host, transport and the tools each offers |
| `mcp.servers_activate` | `POST /api/v1/mcp/servers/<server_id>/activate` | human_only | own | user | Make an MCP server the caller's active one |
| `mcp.servers_create` | `POST /api/v1/mcp/servers` | human_only | own | user | Add an MCP server — a person's act: its endpoint and any credentials are the user's to enter |
| `mcp.servers_delete` | `DELETE /api/v1/mcp/servers/<server_id>` | write | own | user | Delete an MCP server configuration — agents that read from it lose their data source |
| `mcp.servers_import` | `POST /api/v1/mcp/servers/import` | human_only | own | user | Import MCP servers from a configuration file — a person's act: it brings endpoints and credentials |
| `mcp.servers_update` | `PUT /api/v1/mcp/servers/<server_id>` | human_only | own | user | Change an MCP server — a person's act: it carries the endpoint and credentials |

### `ontologies`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `ontologies.list` | `GET /api/v1/ontologies` | read | own | user | The caller's published ontologies — each with its version and conformance |

### `platform`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `platform.asking` | `GET /api/v1/platform/asking` | read | instance | user | Whether agents may ask other agents on this deployment, and its limits — chain depth, asks per turn, time per ask |
| `platform.asking_set` | `PUT /api/v1/platform/asking` | human_only · governs | instance | admin | Turn agents asking other agents on or off for the deployment, and set its limits — an administrator's act |
| `platform.roles` | `GET /api/v1/platform/roles` | read | own | user | The platform roles an agent can hold — what each is for, what it reaches and teaches, and which of the caller's agents hold it |

### `profiles`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `profiles.activate` | `POST /api/v1/profiles/<profile_id>/activate` | write | own | user | Run an agent's configuration checks and, when they pass, switch the caller's working context to it |
| `profiles.active_for_consumption` | `GET /api/v1/profiles/active_for_consumption` | read | own | user | The agents open to REST use (the list the REST firewall reads) |
| `profiles.check_sessions` | `GET /api/v1/profiles/<profile_id>/check-sessions` | never | own | user | Deprecated — an agent's active sessions; use GET /v1/artifacts/profile/<id>/relationships |
| `profiles.classification` | `GET /api/v1/profiles/<profile_id>/classification` | read | own | user | How an agent's tools and prompts are classified |
| `profiles.components` | `GET /api/v1/profiles/<profile_id>/components` | read | own | user | Every component with its effective enabled state for an agent |
| `profiles.connector_settings` | `GET /api/v1/profiles/<profile_id>/connector-settings` | read | own | user | An agent's opt-in state and tool choice for each platform connector |
| `profiles.connector_settings_update` | `PUT /api/v1/profiles/<profile_id>/connector-settings/<server_id>` | write · governs | own | user | Opt an agent in or out of a platform connector and choose which of its tools it gets |
| `profiles.create` | `POST /api/v1/profiles` | write · governs | own | user | Create an agent |
| `profiles.default` | `GET /api/v1/profiles/default` | read | own | user | The caller's default agent |
| `profiles.delete` | `DELETE /api/v1/profiles/<profile_id>` | write | own | user | Delete an agent; the sessions that used it are archived |
| `profiles.learning_repository` | `GET /api/v1/profiles/<profile_id>/learning-repository` | read | own | user | Where an Optimize agent learns: its own planner repository or the shared one |
| `profiles.learning_repository_own` | `POST /api/v1/profiles/<profile_id>/learning-repository` | write | own | user | Give an Optimize agent its own planner repository to learn into (idempotent) |
| `profiles.list` | `GET /api/v1/profiles` | read | own | user | List the caller's agents with their configuration, the default agent and the agents open to REST use |
| `profiles.metric` | `GET /api/v1/profiles/<profile_id>/metrics/<path:metric_name>` | read | own | user | A metric's governed definition (dimensions, measures, source) |
| `profiles.metric_compile` | `POST /api/v1/profiles/<profile_id>/metrics/<path:metric_name>/compile` | read | own | user | Compile a slice of a metric into governed, ready-to-run SQL (nothing runs) |
| `profiles.metric_query` | `POST /api/v1/profiles/<profile_id>/metrics/<path:metric_name>/query` | read | own | user | Compile a slice of a metric and run it — one read-only SELECT — on a database connection |
| `profiles.metrics` | `GET /api/v1/profiles/<profile_id>/metrics` | read | own | user | The metric catalog of an agent's ontology, one summary row per metric |
| `profiles.overview` | `GET /api/v1/profiles/<profile_id>/overview` | read | own | user | One agent in one answer: its class, model and data source, the knowledge repositories it answers from, the experts it consults, the components and skills in effect, its trust state with what decides it, and whether it is the default or open to REST use |
| `profiles.reclassify` | `POST /api/v1/profiles/<profile_id>/reclassify` | write | own | user | Classify an agent's tools and prompts again (a full classification calls the LLM) |
| `profiles.resources` | `GET /api/v1/profiles/<profile_id>/resources` | read | own | user | The tools, prompts and resources an agent has, each with its enabled state |
| `profiles.set_active_for_consumption` | `POST /api/v1/profiles/set_active_for_consumption` | human_only · governs | own | user | Replace the list of agents open to REST use (the whole list, not an addition) |
| `profiles.set_default` | `POST /api/v1/profiles/<profile_id>/set_default` | human_only · governs | own | user | Make an agent the caller's default — it answers every question not addressed to another agent |
| `profiles.skills` | `GET /api/v1/profiles/<profile_id>/skills` | read | own | user | The skills assigned to an agent, with their manifest details |
| `profiles.test` | `POST /api/v1/profiles/<profile_id>/test` | read | own | user | Run an agent's configuration checks (LLM credentials, MCP server, repositories) and return each result |
| `profiles.update` | `PUT /api/v1/profiles/<profile_id>` | write · governs | own | user | Change an agent's configuration — fields not sent are kept |

### `rag`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `rag.collection` | `GET /api/v1/rag/collections/<int:collection_id>` | read | own | user | One repository the caller owns or subscribes to: its type, description, backend and settings |
| `rag.collection_create` | `POST /api/v1/rag/collections` | write | own | user | Create a repository: a knowledge repository (documents an agent answers from) or a planner repository (plans an Optimize agent learns, tied to a data source) |
| `rag.collection_delete` | `DELETE /api/v1/rag/collections/<int:collection_id>` | write | own | user | Delete a repository and everything in it |
| `rag.collection_update` | `PUT /api/v1/rag/collections/<int:collection_id>` | write | own | user | Rename a repository, change its description or how it is searched; a planner repository's data source |
| `rag.collections` | `GET /api/v1/rag/collections` | read | own | user | The caller's repositories — knowledge repositories (documents a Focus agent answers from) and planner repositories (the plans an Optimize agent learns) — owned or subscribed |
| `rag.planner_acknowledge_finding` | `POST /api/v1/rag/collections/<int:collection_id>/rows/<case_id>/acknowledge-finding` | human_only | own | user | Acknowledge the signing gate's finding on ONE champion case with a reason — a recorded human judgement that unblocks sign-off and freezes into the repository's signature |
| `rag.planner_health` | `GET /api/v1/rag/collections/<int:collection_id>/scan-manifest` | read | own | user | A planner repository's health: every champion case whose SQL or question the signing gate flags, with its labels, the text that tripped the scanner and whether the owner acknowledged it — and the verdict the gate would give |

### `scheduled_tasks`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `scheduled_tasks.create` | `POST /api/v1/scheduled-tasks` | write · governs | own | user | Schedule a question for one of the caller's agents to answer on a timer, on its own |
| `scheduled_tasks.delete` | `DELETE /api/v1/scheduled-tasks/<task_id>` | write | own | user | Delete a scheduled task — it stops running |
| `scheduled_tasks.get` | `GET /api/v1/scheduled-tasks/<task_id>` | read | own | user | One scheduled task in full |
| `scheduled_tasks.list` | `GET /api/v1/scheduled-tasks` | read | own | user | The caller's scheduled tasks — questions an agent answers on a timer — with each one's schedule in words and its next runs |
| `scheduled_tasks.run_now` | `POST /api/v1/scheduled-tasks/<task_id>/run-now` | write | own | user | Run a scheduled task once, now, outside its schedule |
| `scheduled_tasks.runs` | `GET /api/v1/scheduled-tasks/<task_id>/runs` | read | own | user | A scheduled task's run history: status, a summary of each answer, tokens and cost |
| `scheduled_tasks.update` | `PUT /api/v1/scheduled-tasks/<task_id>` | write · governs | own | user | Change a scheduled task — its question, schedule, limits — or pause and resume it |

### `scheduler`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `scheduler.status` | `GET /api/v1/scheduler/status` | read | instance | user | Whether the deployment's scheduler is running and switched on, and how many jobs it holds |

### `sessions`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `sessions.analytics` | `GET /api/v1/sessions/analytics` | read | own | user | Across the caller's conversations: success rate, tokens, estimated cost, the models used and the most expensive questions |
| `sessions.context_analytics` | `GET /api/v1/sessions/<session_id>/context-analytics` | read | own | user | How one conversation used its context window, turn by turn: which parts took the budget and what was condensed |
| `sessions.details` | `GET /api/v1/sessions/<session_id>/details` | read | own | user | One conversation in full: every turn with its question, answer, execution trace, tokens and trust stamps |
| `sessions.list` | `GET /api/v1/sessions` | read | own | user | The caller's conversations, newest first: name, agent, turns, tokens, and whether a turn failed |

### `skills`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `skills.list` | `GET /api/v1/skills` | read | instance | user | The skills available on this deployment — expertise added to every turn: what each skill teaches and whether it is built in or the user's own |

### `tasks`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `tasks.input` | `POST /api/v1/tasks/<task_id>/input` | human_only | own | user | Answer the question a task stopped for (the A2A-shaped alias of input_requests.answer) |

### `trust`

| operation | route | effect | scope | tier | summary |
|---|---|---|---|---|---|
| `trust.agent` | `GET /api/v1/trust/agent/<profile_id>` | read | own | user | An agent's live trust state with the state of every ingredient it is built from (why it reads signed, changed or unsigned) |
| `trust.agents` | `GET /api/v1/trust/agents` | read | own | user | Every one of the caller's agents with its live trust state, in one call |
| `trust.artifact` | `GET /api/v1/trust/artifact/<kind>/<path:artifact_id>` | read | own | user | One artefact's signed state (signed, changed or unsigned) |
| `trust.artifacts` | `POST /api/v1/trust/artifacts` | read | own | user | The signed state of several artefacts in one call |
| `trust.history` | `GET /api/v1/trust/history/<kind>/<path:artifact_id>` | read | own | user | Every recorded trust event of one artefact, newest first (signed, reinstated, revoked, retired) |
| `trust.keys` | `GET /api/v1/trust/keys` | read | instance | user | Every public key that has signed on this deployment: active, retired or carried from another signer |
| `trust.unassigned` | `GET /api/v1/trust/unassigned` | read | cross_user | admin | Agent signatures the owner migration could not attribute to an account, each with the reason |
| `trust.unassigned_assign` | `POST /api/v1/trust/unassigned/assign` | human_only · governs | cross_user | admin | Give an unattributed agent signature to the account that owns the agent — an administrator's act |
| `trust.verify` | `POST /api/v1/trust/verify` | read | own | user | Verify a stored signature cryptographically (whether it is intact and which key made it) |
