# Platform Roles

**What an agent can do with Uderia itself — and what it cannot.**

An agent with the **Platform** component can work with Uderia on your behalf: explain your
agents, build and change them, keep your knowledge repositories right, run and schedule
automations, find what goes wrong. A **role** decides which of that work an agent does. This
guide shows each role — what you can ask it, what it can change, what it will not do and why —
and, in the appendices, the playbooks that teach it and the operations behind it.

*For agent owners and administrators.*

## Contents

<!-- generated:toc -->
- [1. The idea in one page](#idea)
- [2. Giving an agent roles](#giving-roles)
- [3. The roles](#roles)
    - [3.1 Guide](#role-guide)
    - [3.2 Agent Builder](#role-agent-builder)
    - [3.3 Knowledge Curator](#role-knowledge-curator)
    - [3.4 Automation Builder](#role-automation-builder)
    - [3.5 Quality Analyst](#role-quality-analyst)
    - [3.6 Insight Builder](#role-insight-builder)
    - [3.7 Publisher](#role-publisher)
    - [3.8 Admin Monitor](#role-admin-monitor)
    - [3.9 Problem Solver](#role-problem-solver)
- [4. Asking other agents](#asking)
- [Appendix A — The playbooks](#appendix-playbooks)
    - Administration: [What the deployment costs](#playbook-admin-costs) · [How is the deployment doing](#playbook-admin-health)
    - Agents: [Inspect an agent](#playbook-agents-inspect)
    - Automation: [Create a flow from a template](#playbook-automation-create-flow) · [Run a flow](#playbook-automation-run-flow) · [Schedule a question for an agent](#playbook-automation-schedule) · [What runs on its own](#playbook-automation-status)
    - Building agents: [Give an agent a knowledge graph](#playbook-building-bind-graph) · [Change what an agent is made of](#playbook-building-configure-agent) · [Create an agent](#playbook-building-create-agent)
    - Insight: [Keep canvases tidy](#playbook-insight-canvases) · [Keep dashboards in order](#playbook-insight-dashboards) · [What the data products can answer](#playbook-insight-data-products)
    - Knowledge: [Add documents to a knowledge repository](#playbook-knowledge-add-documents) · [Clean up a knowledge repository](#playbook-knowledge-clean-up) · [A graph's versions, lifecycle and conformance](#playbook-knowledge-graph-governance) · [Explore a knowledge graph](#playbook-knowledge-graphs) · [Check a knowledge repository's health](#playbook-knowledge-health) · [Try a question against a knowledge repository](#playbook-knowledge-search)
    - Publishing: [Bundle agents into a pack](#playbook-publishing-bundle) · [What is in the marketplace](#playbook-publishing-marketplace) · [Is the pack ready to publish](#playbook-publishing-readiness)
    - Quality: [What needs me](#playbook-quality-inbox) · [When did it get worse, and what changed](#playbook-quality-regression) · [Score a turn again, or try a policy](#playbook-quality-rescore) · [What is failing, and why](#playbook-quality-whats-failing)
    - Solving problems: [What is missing to answer a question](#playbook-solving-gaps) · [Answer a question that spans several fields](#playbook-solving-route) · [Get a second opinion](#playbook-solving-second-opinion)
    - Trust: [Explain an agent's trust state](#playbook-trust-explain-agent) · [Trust across my agents](#playbook-trust-my-agents)
    - Usage: [My usage this period](#playbook-usage-this-period)
- [Appendix B — Operations by role](#appendix-operations)
- [About this guide](#about)
<!-- /generated:toc -->

---

<a id="idea"></a>

## 1. The idea in one page

An agent with the Platform component finds what Uderia can do — *list my agents*, *create a
repository*, *schedule a question* — and does it as you, through the same checks as any
other client. Four separate controls decide what actually happens.

### The four controls

- **Role — what is this agent for?**
  Which parts of Uderia it may see and act on, and which playbooks it is taught.
  Set in the agent editor → Capabilities → Platform. Part of the agent's signature.
- **Write mode — may it submit changes, and do they wait for you?**
  Off · Review · Allow, in the same place. Not part of the signature: it is this deployment's
  setting, and only its owner can loosen it.
- **Your user tier — what may *you* do?**
  An agent never does more than its user. Set in Administration → Users.
- **Your approval — does this change go ahead?**
  Given in the Assurance Inbox.

### Rules every role keeps

- **A role only narrows.** It never lets an agent do what you could not, and several roles
  simply add up: an agent holding the Guide and the Agent Builder can do what each can do.
- **A role is not a user tier.** Your tier says who *you* are; a role says what an *agent* is
  for. Something that needs an administrator stays out of reach for a non-administrator's agent,
  whatever its role.
- **Some changes always wait for you**, whatever the write mode: anything that changes what an
  agent may do (creating or changing an agent, scheduling one to act on its own) and anything
  that deletes. In *Review* mode every change waits; in *Allow* mode only ordinary, reversible
  ones run without you — renaming a flow, quarantining a document, starting a sync.
- **Your acts stay yours.** Signing, attesting, approving, acknowledging, entering credentials
  and opening an agent to the REST API are never done by an agent. It may name one, so it can
  tell you what is owed.
- **Nothing across users — even when you could.** If you may see other users' conversations
  yourself — because of your tier, or because your deployment lets people see each other's — an
  agent acting for you still cannot. An administrator's agent sees the deployment in totals only,
  and each such reading is recorded.
- **What it is not offered stays out.** Uderia can do more than an agent is offered; anything
  not offered is refused before it runs.

### What you approve

A parked change shows exactly what will run: the names the agent used ("@SALES", "Uderia
Documentation") are already resolved to your own items, and a change to an agent's setup is
already merged onto the agent as it is. You approve the result, not a guess.

---

<a id="giving-roles"></a>

## 2. Giving an agent roles

1. Open the agent in the editor → **Capabilities**.
2. Turn on **Platform**. The roles are listed beneath it; *What it reaches* under each shows the
   same reading as this guide.
3. Tick the roles. An agent that ticks none holds the **Guide**.
4. If a ticked role may change things, choose the write mode — start with **Review**.
5. Save, and sign the agent again if you rely on it being signed.

**When a role changes with a release,** every agent holding it reads *changed — sign again*
until you re-sign: a signature never covers more than it did when you signed.

**Where else to see roles:** Components → Platform → **Roles** (every role and the agents
holding it) and Components → Skill Library → **Libraries** (each playbook and the roles taught
it).

---

<a id="roles"></a>

## 3. The roles

The roles follow Uderia's own lifecycle: understand your agents, build them, keep their
knowledge right, put them to work, find what goes wrong, keep their views in order, prepare
them for others, and, for administrators, watch the whole deployment. The last role puts them
to work together: a question none of them answers alone.

The examples are cases Uderia was tested with, live, on a deliberately weak model.

<!-- generated:roles -->
<a id="role-guide"></a>

### 3.1 Guide

*Active · reads only · the default role · 9 playbooks*

Explains your own estate — their agents, what each is built from, its trust state, and your usage — in words, from what the platform returns.

<a id="role-guide-examples"></a>

#### What you can ask

- **“Is @TCOPT trusted? Explain why.”**  
  It reads the agent's trust state and what decides it, then explains in words which ingredient changed and what you would sign. It never signs.
- **“What is @SALES and what does it use?”**  
  One call to the agent's overview: its class, model, data source, repositories, experts, components, skills and trust state, summarised.
- **“What needs my attention?”**  
  It counts what is unacknowledged in your Assurance Inbox and what waits in your review queue, and tells you where to act. It acknowledges nothing.
- **“How much have I used this period?”**  
  It reads your usage and warnings and says where you stand against your limits.

<a id="role-guide-changes"></a>

#### What it can change

Nothing — it only reads.

#### What it can read

Agents (11) · Assurance (5) · Canvases (1) · Components (2) · Context windows (2) · Conversations (2) · Dashboards (3) · Data products (2) · Flows (6) · Knowledge (4) · Knowledge graphs (12) · Ontologies (1) · Quality (2) · Repositories (2) · Scheduler (1) · Schedules (3) · Skills (1) · Trust (7) · Usage (5)

<a id="role-guide-boundaries"></a>

#### What it does not do — and why

- **Any change — to an agent, a repository, a flow or a schedule.** The Guide only explains. Changes belong to the Agent Builder, the Knowledge Curator and the Automation Builder — or to you.
- **Signing anything, attesting a deployment, or withdrawing a signature.** A signature is your statement that you checked it. An agent signing — or unsigning — its own work proves nothing.
- **Opening an agent to the REST API, or making one your default.** They decide what every other client and conversation runs on.
- **Other users' usage, conversations or unattributed signatures.** An agent works on your items only — even when your tier lets you see more. Giving a signature to another account is an administrator's act.

#### Playbooks it is taught

- [Inspect an agent](#playbook-agents-inspect)
- [What runs on its own](#playbook-automation-status)
- [Explore a knowledge graph](#playbook-knowledge-graphs)
- [Check a knowledge repository's health](#playbook-knowledge-health)
- [Try a question against a knowledge repository](#playbook-knowledge-search)
- [What needs me](#playbook-quality-inbox)
- [Explain an agent's trust state](#playbook-trust-explain-agent)
- [Trust across my agents](#playbook-trust-my-agents)
- [My usage this period](#playbook-usage-this-period)

*Every operation it reaches, by name: [Appendix B — Guide](#ops-guide).*

<a id="role-agent-builder"></a>

### 3.2 Agent Builder

*Active · reads and may submit changes · 4 playbooks*

Composes agents of every class from the ingredients you has — model, data source, knowledge repositories, expert agents, skills, components, context window — and submits them for your approval; changes an agent's composition the same way, and which knowledge graph each agent reads; tests them once they exist. It reads repositories and graphs; changing them is the Knowledge Curator's.

<a id="role-agent-builder-examples"></a>

#### What you can ask

- **“Create a Focus agent DOCSQA that answers from the Uderia Documentation repository, on my default agent's model.”**  
  It reads your default agent and your repositories and submits a new Focus agent — your default model, Uderia Documentation as its knowledge, the name already resolved to its id. Nothing exists until you approve.
- **“Turn on the chart component for @SALES.”**  
  It submits a change naming only the chart; the request you approve carries @SALES's whole component setup with the chart on and every other component exactly as it was.
- **“Make a coordinator over @SALES and @DOCS.”**  
  It submits a Coordinate agent with both as its experts, their tags resolved to your agents — and refuses a tag that is not one of yours, listing the ones that are.
- **“Let @SALES use the Retail graph.”**  
  It reads which agents the Retail graph is assigned to, then submits the full list with @SALES added — and, when you want it to be the graph @SALES reads, making it active. Both wait for your approval. Afterwards it can show what @SALES would be told from the graph about a question.

<a id="role-agent-builder-changes"></a>

#### What it can change

- Make a graph the one an agent reads — its other graphs stay assigned but inactive — *always waits for you*
- Set which agents a graph is assigned to — the list REPLACES the current one, so name every agent that should keep it — *always waits for you*
- Opt an agent in or out of a platform connector and choose which of its tools it gets — *always waits for you*
- Create an agent — *always waits for you*
- Delete an agent; the sessions that used it are archived — *always waits for you*
- Change an agent's configuration — fields not sent are kept — *always waits for you*
- Run an agent's configuration checks and, when they pass, switch your working context to it — *your write mode decides*
- Give an Optimize agent its own planner repository to learn into (idempotent) — *your write mode decides*
- Classify an agent's tools and prompts again (a full classification calls the LLM) — *your write mode decides*

#### What it can read

Agents (11) · Components (2) · Context windows (2) · Data sources (1) · Knowledge (1) · Knowledge graphs (4) · Models (4) · Repositories (2) · Skills (1) · Trust (2)

<a id="role-agent-builder-boundaries"></a>

#### What it does not do — and why

- **Entering, changing or reading back credentials — LLM configurations and MCP servers.** Credentials are yours to hold; no result an agent sees ever contains one.
- **Choosing which LLM or MCP server is your active one.** It changes every conversation you start, not only the agent being built.
- **Deleting an LLM configuration or an MCP server.** Other agents depend on them, and creating them is already yours — so is removing them. The role excludes both.
- **Opening an agent to the REST API, or making one your default.** They decide what every other client and conversation runs on.
- **An agent's authority limits and its additional data sources.** Not part of what the Builder may compose yet — set them in the agent editor.
- **Signing the agents it builds.** Building and vouching stay apart: you test the agent, then sign it.
- **Creating repositories, adding documents or building graphs.** That is the Knowledge Curator's work; the Builder chooses from what already exists.

#### Playbooks it is taught

- [Inspect an agent](#playbook-agents-inspect)
- [Give an agent a knowledge graph](#playbook-building-bind-graph)
- [Change what an agent is made of](#playbook-building-configure-agent)
- [Create an agent](#playbook-building-create-agent)

*Every operation it reaches, by name: [Appendix B — Agent Builder](#ops-agent-builder).*

<a id="role-knowledge-curator"></a>

### 3.3 Knowledge Curator

*Active · reads and may submit changes · 6 playbooks*

Keeps the knowledge agents stand on right: creates knowledge repositories, adds documents from your Files, removes or quarantines what is wrong, syncs and screens them, and reads their health and ratings; reads your knowledge graphs — their shape, entities, versions, lifecycle, conformance and tuning proposals — and saves and names their versions. Releasing a quarantine, acknowledging a finding, choosing a sync source, signing, and every act that changes what a graph means or where it is published stay the owner's. Data products next.

<a id="role-knowledge-curator-examples"></a>

#### What you can ask

- **“Add curator-probe.md from my Files to the Curator probe repository.”**  
  It finds the file and the repository by name and submits adding one to the other; once you approve, the repository's own upload screens, chunks and embeds it.
- **“Quarantine the vendor FAQ.”**  
  It lists the repository's documents, confirms which one with you, and submits the quarantine. The document leaves retrieval but stays listed — only you can release it.
- **“Is my Policies repository healthy?”**  
  It reads the repository's health and your ratings: what is screened, what the screening found, personal data by label and count (never values), the lowest-rated documents — and offers to screen or quarantine.
- **“What is in my Policing graph, and is it ready to publish?”**  
  It finds the graph by name and reads its shape — entity types and how many of each, the relationships between them — then its conformance and its lifecycle. It names any issue the check found and says publishing is yours to do.
- **“Save the Policing graph as a version before I change it.”**  
  It saves the graph as it is now under a label, or tells you nothing was saved because it has not changed since the last version.

<a id="role-knowledge-curator-changes"></a>

#### What it can change

- Remove a document from a knowledge repository — its chunks leave retrieval — *always waits for you*
- Delete a repository and everything in it — *always waits for you*
- Add a document from your Files to one of their knowledge repositories — screened, chunked and embedded by the repository's own upload — *your write mode decides*
- Quarantine a document: it stays listed in its repository but no agent retrieves it — *your write mode decides*
- Change a knowledge repository's settings: its description, how often it syncs, its sensitivity label, whether its embedding model is locked — *your write mode decides*
- Screen a knowledge repository's documents for injection, exfiltration and personal data — the unscreened ones, or all again — *your write mode decides*
- Sync a knowledge repository now: every document kept in step with its source is re-read, changed ones re-ingested — *your write mode decides*
- Rename a graph or change its description — *your write mode decides*
- Save a graph as it is now as a version — nothing is saved when it has not changed since the last one — *your write mode decides*
- Create a repository: a knowledge repository (documents an agent answers from) or a planner repository (plans an Optimize agent learns, tied to a data source) — *your write mode decides*
- Rename a repository, change its description or how it is searched; a planner repository's data source — *your write mode decides*

#### What it can read

Agents (2) · Files (2) · Knowledge (4) · Knowledge graphs (15) · Marketplace (3) · Ontologies (1) · Repositories (2) · Trust (3)

<a id="role-knowledge-curator-boundaries"></a>

#### What it does not do — and why

- **Releasing a quarantined document, or acknowledging a security finding.** Both are recorded human judgements that let flagged content back into what agents read.
- **Choosing where a document or a repository syncs from.** A source is an address the server reads; an agent could point it at anything the server can reach.
- **The repository's standing policy after a sync.** Acknowledging findings or renewing signatures automatically is your trust policy.
- **Another vector store, or its configuration.** Configuration can carry credentials — the owner chooses it.
- **Unlocking a planner repository's trust seal.** Unlocking weakens a trust seal; it stays yours.
- **Signing a repository.** A signature is your statement that you checked its content.
- **Uploading a file.** Documents come from your Files: you put them there, and the Curator adds what you already hold.
- **Moving an ontology along its lifecycle — proposing, approving, publishing, deprecating.** Each is a governance decision a person signs their name to; publishing makes the ontology what other agents and BI tools trust.
- **Deleting a graph, restoring or deleting a saved version, rebuilding a graph from its source.** Each replaces or removes what agents read, all at once.
- **Changing a database from a graph — deploying drafted tables, re-creating tables, views or demo data, landing a tuning proposal.** These run statements against your database; the owner decides when, and on which target.
- **Publishing or withdrawing what people drafted on a graph.** An extension changes what the graph means to every agent that reads it.
- **Publishing a graph to the marketplace, choosing its audience, or installing, forking, subscribing to and rating someone else's.** These reach other people, or bring their work into yours.
- **Editing a graph's entities and relationships, or building a graph.** A graph's content is changed by the agent that holds it, through its knowledge-graph component under your OBDA policy; building one runs discovery against your data sources from the Graph constructor.
- **Which agents read a graph.** Assigning a graph is part of an agent's composition — the Agent Builder submits it for your approval.

#### Playbooks it is taught

- [Add documents to a knowledge repository](#playbook-knowledge-add-documents)
- [Clean up a knowledge repository](#playbook-knowledge-clean-up)
- [A graph's versions, lifecycle and conformance](#playbook-knowledge-graph-governance)
- [Explore a knowledge graph](#playbook-knowledge-graphs)
- [Check a knowledge repository's health](#playbook-knowledge-health)
- [Try a question against a knowledge repository](#playbook-knowledge-search)

*Every operation it reaches, by name: [Appendix B — Knowledge Curator](#ops-knowledge-curator).*

<a id="role-automation-builder"></a>

### 3.4 Automation Builder

*Active · reads and may submit changes · 4 playbooks*

Puts your agents to work without being asked: creates flows from topology templates with your agents in their slots, runs them and follows the runs, keeps versions, and schedules questions for agents to answer on a timer. Every schedule is approved by a person; a flow's graph is edited with the flow component (TDA_Flow); where results are sent stays the owner's.

<a id="role-automation-builder-examples"></a>

#### What you can ask

- **“Every weekday at 9, have @IDEAT answer: what is one tip for writing clearer emails? Call it Morning tip.”**  
  One call: a schedule `0 9 * * 1-5` for @IDEAT, always submitted for your approval. Where its results are sent stays your choice in the Scheduler.
- **“Build a flow where @SALES and @DOCS answer and @COORD combines them.”**  
  It picks the fork–join template, fills its slots with the three agents and submits the flow; you edit its steps in Flows if you want more.
- **“Did the morning report run?”**  
  It reads the task's run history: when each run went, whether it succeeded, a summary of its answer and what it cost.

<a id="role-automation-builder-changes"></a>

#### What it can change

- Delete a flow, its versions and its run history — *always waits for you*
- Schedule a question for one of your agents to answer on a timer, on its own — *always waits for you*
- Delete a scheduled task — it stops running — *always waits for you*
- Change a scheduled task — its question, schedule, limits — or pause and resume it — *always waits for you*
- Create a flow from a topology template, its slots filled, or an empty one — *your write mode decides*
- Copy a platform flow (or one of your own) into your flows — *your write mode decides*
- Run a flow now — its agents answer and its steps act; returns the run to follow — *your write mode decides*
- Stop a running flow before its next step — *your write mode decides*
- Rename a flow or change its description — *your write mode decides*
- Put a saved version back as the flow's current graph — *your write mode decides*
- Save the flow as it is now as a named version (nothing saved when unchanged) — *your write mode decides*
- Run a scheduled task once, now, outside its schedule — *your write mode decides*

#### What it can read

Agents (2) · Flows (9) · Scheduler (1) · Schedules (3) · Trust (1)

<a id="role-automation-builder-boundaries"></a>

#### What it does not do — and why

- **Where a schedule sends its results.** Email or a webhook is a way for your data to leave the platform — you set it in the Scheduler.
- **Scheduling a database action.** Actions pass their own approval gate (OBDA-ACT), not this one.
- **Editing a flow's steps.** There is one way to change a graph: the flow component (TDA_Flow) and its designer skill.
- **Debugging options when running a flow.** Running one node, or from one node on, is for the person debugging in the flow editor.
- **Sharing a flow with every user.** It publishes to the whole deployment — an administrator's act.
- **Replacing all flow variables at once.** One call would wipe every variable.
- **Starting a schedule without you.** An agent acting on its own on a timer is your decision: every new or changed schedule waits for your approval, whatever the write mode.

#### Playbooks it is taught

- [Create a flow from a template](#playbook-automation-create-flow)
- [Run a flow](#playbook-automation-run-flow)
- [Schedule a question for an agent](#playbook-automation-schedule)
- [What runs on its own](#playbook-automation-status)

*Every operation it reaches, by name: [Appendix B — Automation Builder](#ops-automation-builder).*

<a id="role-quality-analyst"></a>

### 3.5 Quality Analyst

*Active · reads and may submit changes · 6 playbooks*

Finds what goes wrong and why: which checks fail and for which agents, when a regression started and what changed around it, what waits in the Assurance Inbox and the review queue, and what a proposed policy would have done. It may score a turn again; acknowledging, approving and resetting stay the owner's, and the instance-wide evaluation store is not offered to agents.

<a id="role-quality-analyst-examples"></a>

#### What you can ask

- **“Which quality checks fail most across my agents, and which agent is doing worst?”**  
  One call to the scoreboard; it names the check and the agent with the number of turns behind each figure, and offers to open that agent's failing turns.
- **“When did @SALES get worse?”**  
  It reads the agent's timeline, its trust history and its scored runs around the step, and names the likely cause with its time — or says nothing in its history changed then.
- **“What would enforcing grounding have done to @SALES?”**  
  A replay of past turns at no token cost: how many answers would have been withheld, flagged or sent to a judge. Nothing is changed.

<a id="role-quality-analyst-changes"></a>

#### What it can change

- Score one turn of your conversation again — against assertions or a reference answer, optionally with model judges — *your write mode decides*

#### What it can read

Agents (2) · Assurance (7) · Conversations (4) · Quality (10) · Trust (7) · Usage (5)

<a id="role-quality-analyst-boundaries"></a>

#### What it does not do — and why

- **Acknowledging an incident; approving, rejecting or withdrawing a request.** Saying you looked, and the approval itself, are the controls — an agent cannot be its own reviewer.
- **Starting an agent's trust readings afresh.** Where a new leg begins decides what its readings count; that is the owner's judgement.
- **Signing anything, attesting a deployment, or withdrawing a signature.** A signature is your statement that you checked it. An agent signing — or unsigning — its own work proves nothing.
- **Other users' quality, usage or conversations.** Even when your tier lets you see them, an agent acting for you never can — a request an agent makes carries none of the features that reach other users.
- **The instance-wide evaluation store — datasets, offline runs, annotations.** It holds every user's turns; it belongs to the operators and is not offered to agents.
- **Changing an agent's posture or the deployment's policy.** It shows what a policy would do; deciding is yours.

#### Playbooks it is taught

- [What needs me](#playbook-quality-inbox)
- [When did it get worse, and what changed](#playbook-quality-regression)
- [Score a turn again, or try a policy](#playbook-quality-rescore)
- [What is failing, and why](#playbook-quality-whats-failing)
- [Explain an agent's trust state](#playbook-trust-explain-agent)
- [Trust across my agents](#playbook-trust-my-agents)

*Every operation it reaches, by name: [Appendix B — Quality Analyst](#ops-quality-analyst).*

<a id="role-insight-builder"></a>

### 3.6 Insight Builder

*Active · reads and may submit changes · 3 playbooks*

Keeps the views people read in order: creates and arranges dashboards, refreshes their tiles through each tile's own agent, keeps canvases tidy, and explains what the data products on a knowledge graph can answer — down to the SQL a request would run. What a tile shows and asks, a canvas's content, and running data stay with their own components.

<a id="role-insight-builder-examples"></a>

#### What you can ask

- **“Make a dashboard called Weekly Sales.”**  
  It submits an empty dashboard for your approval; its tiles come from conversations, pinned with the dashboard component.
- **“Refresh the revenue tile on Weekly Sales.”**  
  It finds the tile and submits a refresh: the tile's own agent answers its question again, under its own authority, and the tile shows the new answer.
- **“Which data products can I ask about on the Sales graph?”**  
  It reads the graph's catalog: each product with what it measures, its trust state and whether a validation backs it.
- **“What SQL would revenue by region for last quarter run on the Sales product?”**  
  It compiles the request and shows the SQL — nothing runs. A request the product cannot answer comes back with the closest valid names.

<a id="role-insight-builder-changes"></a>

#### What it can change

- Delete a canvas and its versions — *always waits for you*
- Delete a dashboard and its tiles — *always waits for you*
- Remove a tile from a dashboard — *always waits for you*
- Rename a canvas — *your write mode decides*
- Put a saved version back as the canvas's content — *your write mode decides*
- Create an empty dashboard — its tiles are pinned from conversations (the dashboard component) — *your write mode decides*
- Arrange a dashboard: positions and sizes of several tiles at once — *your write mode decides*
- Refresh a tile: its own agent answers its question again, under its own authority, in the tile's conversation — *your write mode decides*
- Rename a tile, move it or resize it — *your write mode decides*
- Rename a dashboard, change its description or its theme — *your write mode decides*

#### What it can read

Agents (2) · Canvases (3) · Conversations (1) · Dashboards (3) · Data products (3) · Knowledge graphs (1)

<a id="role-insight-builder-boundaries"></a>

#### What it does not do — and why

- **Adding a tile, or changing what a tile shows or which question it asks.** A tile's content renders in your dashboard, so it is written only by pinning a conversation's own visual; a tile's question decides what another agent is asked on every refresh — both are yours.
- **Choosing which agent answers when a tile refreshes.** A tile is always refreshed by its own agent, under that agent's authority.
- **How often a dashboard refreshes on its own.** It re-runs every tile's agent on a timer — standing work and spend, like a schedule, so it is yours.
- **Writing a canvas's content.** Content is written with the canvas component (TDA_Canvas), under the agent's own enablement — one way in.
- **Running a data product query, or running SQL from a canvas.** Reading data is the agent's own access (TDA_DataProduct and its tools), held to its data surface and the deployment's policy; through the platform it would run as you and skip both.
- **Creating, validating, deleting or signing a data product.** Defining what a product may answer is modelling, and signing it is yours.
- **Canvas database connections.** They carry credentials — yours to enter.

#### Playbooks it is taught

- [Keep canvases tidy](#playbook-insight-canvases)
- [Keep dashboards in order](#playbook-insight-dashboards)
- [What the data products can answer](#playbook-insight-data-products)

*Every operation it reaches, by name: [Appendix B — Insight Builder](#ops-insight-builder).*

<a id="role-publisher"></a>

### 3.7 Publisher

*Active · reads and may submit changes · 3 playbooks*

Prepares your agents for others: bundles them into a draft pack, keeps its name and version right, says whether it is ready to publish and what stands in the way, and reads the marketplace — what is there, and what a pack would need to run here. Proposing, publishing, choosing who receives it, updating subscribers, and installing anyone else's pack stay you's.

<a id="role-publisher-examples"></a>

#### What you can ask

- **“Bundle @SALES and @DOCS into a pack called Sales Assist.”**  
  It resolves both tags to your agents and submits a new draft pack; the agents stay where they are, and removing the pack later leaves them.
- **“Is Sales Assist ready to publish?”**  
  It reads the pack's lifecycle, the deployment's publishing rules and each agent's trust state, and says what stands in the way — an unsigned agent, an approval still needed — and what you do next.
- **“Which packs in the marketplace are about policing?”**  
  It searches the marketplace and lists what it finds: what each pack offers, by whom, how it is rated.
- **“What would the Policing pack need from my data sources?”**  
  It reads the pack's requirements, resolved against your data sources, and says which agents would run here as they are and which need a source you do not have.

<a id="role-publisher-changes"></a>

#### What it can change

- Bundle some of your agents into a new draft pack — the agents stay where they are; removing the pack later leaves them — *your write mode decides*
- Rename a pack, change its description or its version — *your write mode decides*

#### What it can read

Agent packs (4) · Agents (2) · Marketplace (4) · Trust (4)

<a id="role-publisher-boundaries"></a>

#### What it does not do — and why

- **Proposing, approving, publishing, deprecating or withdrawing a pack.** Putting your name to what others will run is yours — and approving it is an approver's, never the publisher's agent.
- **Choosing who a pack is offered to.** Whom you share with is yours to decide.
- **Pushing a new version to subscribers.** It changes other people's agents.
- **Setting a pack's service level.** It is a promise to subscribers — yours to make.
- **Installing, forking, updating or leaving someone else's pack.** Bringing another publisher's agents into your estate, or letting them go, is your trust decision.
- **Rating a pack, or emailing a pack's author.** A rating is a person's opinion, and a message to someone outside Uderia is yours to send.
- **The deployment's publishing rules.** Whether packs need approval, and who approves, is an administrator's setting.
- **Signing a pack.** A signature is your statement that you checked what it bundles.
- **Removing an installed pack, or exporting its file.** Removing a pack deletes what it brought — agents, repositories, graphs — and a pack file leaves the platform; both are yours.

#### Playbooks it is taught

- [Bundle agents into a pack](#playbook-publishing-bundle)
- [What is in the marketplace](#playbook-publishing-marketplace)
- [Is the pack ready to publish](#playbook-publishing-readiness)

*Every operation it reaches, by name: [Appendix B — Publisher](#ops-publisher).*

<a id="role-admin-monitor"></a>

### 3.8 Admin Monitor

*Active · reads only · 2 playbooks*

Keeps an administrator informed about the whole deployment, in aggregates only: usage and cost across all users, models running without a price, the database's health and backups, the platform's maintenance jobs, the scheduler and the model providers. It never reads a user's conversations or content, changes nothing, and each of its readings of the deployment is recorded in the audit log. Its readings of the deployment are for administrators: anyone else's agent reaches only the scheduler's and the model providers' status.

<a id="role-admin-monitor-examples"></a>

#### What you can ask

- **“How is the deployment doing this month?”**  
  It reads the deployment's consumption across all users — active users, tokens, estimated cost, failed turns, the models in use — and gives you the totals, never anyone's conversations.
- **“Which models run without a price?”**  
  It reads the price coverage and names the models in use that have no price, so their cost reads as zero until one is set.
- **“Is the database healthy, and when was it last backed up?”**  
  It reads the database's health: unreadable reads since the start, the last backup and how many are kept.
- **“Did the nightly maintenance jobs run?”**  
  It reads the platform's own jobs — usage resets, price sync, backups — with their schedules and how their last runs went.

<a id="role-admin-monitor-changes"></a>

#### What it can change

Nothing — it only reads.

#### What it can read

Administration (3) · Costs (3) · Models (1) · Scheduler (1) · Usage (1)

<a id="role-admin-monitor-boundaries"></a>

#### What it does not do — and why

- **Any user's conversations, questions or answers — or usage named per user.** The instance-wide view is aggregates; what a person asked, and how much each person used, are theirs. An agent reads its own user's conversations only, whatever the deployment allows people.
- **Signatures not yet attributed to an account.** They name other users' agents; assigning one is an administrator's act in person.
- **The user directory, or any change to a user — tier, lock, deletion.** Who has an account, and what they may do, is decided by an administrator in person.
- **Changing the deployment — its settings, components, prices, publishing rules.** The Admin Monitor reads; every setting is an administrator's to change.
- **Filling in missing prices, running a backup or a maintenance job.** Each one changes the deployment; it reports what is missing, you act.
- **The deployment's usage, costs, users or database for an agent whose user is not an administrator.** Those readings are administrator-tier: anyone else's agent holding the role reaches only the scheduler's and the model providers' status.

#### Playbooks it is taught

- [What the deployment costs](#playbook-admin-costs)
- [How is the deployment doing](#playbook-admin-health)

*Every operation it reaches, by name: [Appendix B — Admin Monitor](#ops-admin-monitor).*

<a id="role-problem-solver"></a>

### 3.9 Problem Solver

*Active · reads only · 3 playbooks*

Gets a question answered that none of your agents can answer alone: reads what each agent is for, how trusted it is and how well it does, asks the ones that fit, and puts their answers together — each part naming the agent that answered it, and any part no agent could answer named as a gap. Asks a second agent for a second opinion. When an agent is missing, says which one would close the gap and from what — and, held together with the Agent Builder, submits it for your approval. Changes nothing itself.

<a id="role-problem-solver-examples"></a>

#### What you can ask

- **“What is the Trust Circle, and which of my agents are signed?”**  
  It answers the second part from the platform itself, asks the documentation agent the first, and gives both — saying which agent answered which part.
- **“Check this answer about Q3 revenue with @FINANCE.”**  
  It asks @FINANCE the same question in full and reports where the two answers agree and where they differ — never which one is right unless the data says so.
- **“Can any of my agents tell me which claims are overdue?”**  
  It reads what each agent is for and its data sources. When none covers claims, it says so and names the agent that would: its class and the source or repository it would be built on — or that the data itself is missing.

<a id="role-problem-solver-changes"></a>

#### What it can change

Nothing — it only reads.

#### What it can read

Agents (5) · Data products (1) · Data sources (1) · Knowledge graphs (2) · Quality (2) · Repositories (2) · Trust (2)

<a id="role-problem-solver-boundaries"></a>

#### What it does not do — and why

- **Building the agent it finds missing.** Proposing an agent is the Agent Builder's. Give an agent both roles and it submits what it found for your approval; with this role alone it describes the agent and stops.
- **Asking an agent you have not listed, or one that is not activated.** You choose whom it may ask (Capabilities → Platform), and an agent answers other agents only once you have activated it — both are your decisions, not the asking agent's.
- **Asking when the deployment does not allow it.** Asking spends other agents' tokens; an administrator turns it on and sets its limits for everyone (Administration → Components → Platform → Agent asking).
- **Changing anything.** It reads and asks. The agents it asks act under their own authority — whatever they may change, they change under their own rules, never this agent's.

#### Playbooks it is taught

- [What is missing to answer a question](#playbook-solving-gaps)
- [Answer a question that spans several fields](#playbook-solving-route)
- [Get a second opinion](#playbook-solving-second-opinion)

*Every operation it reaches, by name: [Appendix B — Problem Solver](#ops-problem-solver).*
<!-- /generated:roles -->

---

<a id="asking"></a>

## 4. Asking other agents

An agent with the Platform component can put a question to another of your agents and use its
answer — "ask @UDERIA what the Trust Circle is", or a question that needs your data agent and
your documents agent together. Asking is the **Problem Solver**'s ([3.9](#role-problem-solver)):
the role that is taught to split a question, choose who answers each part and put the answers
together. Asking reaches nothing of Uderia's own operations.

**Four switches, all of which must be on:**

1. **The deployment.** An administrator turns asking on — Administration → Components →
   Platform → **Agent asking** — and sets its limits: how long a chain of asks may be (by
   default an asked agent may not ask on), how many asks one turn may make (3), how long one ask
   may take (180 s).
2. **The asking agent's role.** It holds the Problem Solver.
3. **The asking agent's list.** In the agent editor → Capabilities → Platform → **Other agents it
   may ask**, tick each agent it may ask. An agent asks no one until you do. The list is shown
   while the deployment allows asking, and can be chosen while the Problem Solver is ticked.
4. **The asked agent.** It must be activated for REST use, as for any client.

**What happens.** The asked agent answers in a conversation of its own, which you can open from
the sidebar; that conversation records which agent asked. It works under its own authority,
gates and posture — nothing of the asking agent is lent to it — and spends its own tokens.
The question it receives is the only thing it sees of yours, so the asking agent writes it in
full. An answer that takes longer than the limit keeps going in the asked agent's conversation.

**What it is not.** The list of agents it may ask is not part of the agent's signature and is
not carried by an agent pack: whoever installs a pack decides whom its agents may ask. Adding
an agent to the list on an update is reported as a widening, like any other authority.

**Example** (live, Gemini Flash Lite, $0.0053 for both turns): *"Ask @UDERIA what the Trust Circle is, and
tell me its answer in two sentences."* @CURATOR asked; @UDERIA answered from the Uderia
documentation in its own conversation; @CURATOR passed the answer on.

---

<a id="appendix-playbooks"></a>

## Appendix A — The playbooks

Roles decide what an agent *may* do; playbooks teach it *how to do it well*. When an agent looks
for something it can do, the playbook that fits is handed to it — only from those its roles are
taught. A playbook is guidance, never permission: an agent that ignores one still cannot go
beyond its role.

<!-- generated:playbooks -->
<a id="playbook-admin-costs"></a>

### What the deployment costs

Where the deployment's spend goes, and where it cannot be counted: cost by model and provider, the trend, and the models running without a price.

**Use for:** "what are we spending", "which model costs the most", "is cost going up", "which models run without a price", "why does a model show zero cost".

*Taught to Admin Monitor · reads only · calls `consumption.system_summary`, `costs.coverage`, `costs.models`, `costs.analytics`*

<a id="playbook-admin-health"></a>

### How is the deployment doing

The whole deployment at a glance — consumption across users, the database and backups, maintenance jobs, providers — in totals, never anyone's conversations.

**Use for:** "how is the deployment doing", "how many users were active this month", "is the database healthy", "when was the last backup", "did the maintenance jobs run", "is a model provider failing".

*Taught to Admin Monitor · reads only · calls `consumption.system_summary`, `admin.stats`, `admin.db_health`, `admin.platform_jobs`, `scheduler.status`, `llm.provider_health`, `costs.coverage`*

<a id="playbook-agents-inspect"></a>

### Inspect an agent

What one agent is and what it can use: its class, model, data source, components, skills and trust state, in a short answer.

**Use for:** "what is @SALES", "what can @SALES do", "how is @SALES configured", "which components does @SALES have".

*Taught to Guide, Agent Builder · reads only · calls `profiles.overview`, `profiles.resources`*

<a id="playbook-automation-create-flow"></a>

### Create a flow from a template

Build a multi-step automation from one of the platform's topology templates — a pipeline, a fork–join, a debate — with the user's agents in its slots, and submit it for their approval.

**Use for:** "build a flow where @SALES and @DOCS answer and @COORD combines them", "create a pipeline of three agents", "set up a debate between two agents", "make an automation from a template", "copy the platform's weekly report flow".

*Taught to Automation Builder · submits a change · calls `flows.templates`, `profiles.list`, `flows.template`, `flows.create`, `flows.platform`, `flows.fork`*

<a id="playbook-automation-run-flow"></a>

### Run a flow

Run one of the user's flows now, follow the run, and report what it produced — or stop it.

**Use for:** "run the quarterly answer flow", "execute my report flow with region EMEA", "start the pipeline", "stop the running flow", "cancel that run".

*Taught to Automation Builder · submits a change · calls `flows.list`, `flows.run`, `flows.run_status`, `flows.run_cancel`*

<a id="playbook-automation-schedule"></a>

### Schedule a question for an agent

Have one of the user's agents answer a question on a timer — every weekday morning, every hour — and pause, resume, change or remove such a schedule. Every schedule is approved by the user.

**Use for:** "every weekday at 9 ask @SALES for yesterday's orders", "schedule a daily summary from @DOCS", "run this every hour", "pause the morning report", "change the schedule to Mondays", "stop the daily summary".

*Taught to Automation Builder · submits a change · calls `profiles.list`, `scheduled_tasks.create`, `scheduled_tasks.list`, `scheduled_tasks.update`, `scheduled_tasks.run_now`, `scheduled_tasks.delete`*

<a id="playbook-automation-status"></a>

### What runs on its own

What the user has automated — flows and scheduled questions — and how their recent runs went.

**Use for:** "what is scheduled", "which automations do I have", "did the morning report run", "how did my flows do this week", "why did the daily summary fail", "is the scheduler running".

*Taught to Guide, Automation Builder · reads only · calls `scheduled_tasks.list`, `flows.list`, `scheduled_tasks.runs`, `flows.runs`, `flows.run_status`, `scheduler.status`*

<a id="playbook-building-bind-graph"></a>

### Give an agent a knowledge graph

Choose which knowledge graph an agent reads: assign a graph to it, make it the agent's active graph, or take it away — each submitted for the user's approval.

**Use for:** "let @SALES use the Retail graph", "which agents have the Policing graph", "make the new ontology the one @OPS reads", "stop @HR using the Staff graph", "which graph does @SALES read".

*Taught to Agent Builder · submits a change · calls `knowledge_graph.list`, `knowledge_graph.assignments`, `knowledge_graph.assignments_set`, `knowledge_graph.activate`, `knowledge_graph.context`*

<a id="playbook-building-configure-agent"></a>

### Change what an agent is made of

Change an existing agent's composition — its model, its knowledge repositories, its experts, its skills, its capabilities, its context window — and submit the change for the user's approval.

**Use for:** "add the chart component to @SALES", "give @DOCS the Trust campaign corpus too", "turn off dashboards for my agent", "add a skill to my agent", "let @COORD also consult @HR", "switch @SALES to another model".

*Taught to Agent Builder · submits a change · calls `profiles.overview`, `rag.collections`, `profiles.list`, `components.list`, `skills.list`, `llm.configurations`, `context_window_types.list`, `profiles.update`*

<a id="playbook-building-create-agent"></a>

### Create an agent

Compose a new agent of any class from what the user already has — its model, its data source, its knowledge, its experts, its skills and capabilities — and submit it for the user's approval.

**Use for:** "create an agent for …", "build me an agent that …", "make a new Optimize agent on the sales database", "build an agent that answers from our documentation", "a coordinator over @SALES and @DOCS".

*Taught to Agent Builder · submits a change · calls `llm.configurations`, `profiles.default`, `mcp.servers`, `rag.collections`, `profiles.list`, `components.list`, `skills.list`, `context_window_types.list`, `profiles.create`*

<a id="playbook-insight-canvases"></a>

### Keep canvases tidy

Find, rename, roll back or remove the user's canvases — the documents and code kept from conversations — each change submitted for the user's approval.

**Use for:** "which canvases do I have", "rename the SQL draft canvas", "go back to the previous version of my report canvas", "remove the old canvases", "show me the canvas about churn".

*Taught to Insight Builder · submits a change · calls `canvases.list`, `canvases.get`, `canvases.update`, `canvases.versions`, `canvases.version_restore`, `canvases.delete`*

<a id="playbook-insight-dashboards"></a>

### Keep dashboards in order

Create, rename, arrange or remove the user's dashboards and their tiles, and refresh a tile through its own agent — each change submitted for the user's approval.

**Use for:** "make a dashboard called Weekly Sales", "rename my dashboard", "refresh the revenue tile", "move the chart to the top", "make the map tile wider", "remove the old tile", "remove the test dashboard", "what is on my dashboards".

*Taught to Insight Builder · submits a change · calls `dashboards.list`, `dashboards.tiles`, `dashboards.create`, `dashboards.update`, `dashboards.tile_update`, `dashboards.layout`, `dashboards.tile_refresh`, `dashboards.tile_delete`, `dashboards.delete`*

<a id="playbook-insight-data-products"></a>

### What the data products can answer

Explain which data products a knowledge graph offers, what each measures and how far it is trusted, and show the SQL a request would run — without running it.

**Use for:** "which data products are on the Sales graph", "what can I ask the revenue product", "is the orders product validated", "what SQL would revenue by region last quarter run", "why does my request to the product fail".

*Taught to Insight Builder · reads only · calls `knowledge_graph.list`, `data_products.catalog`, `data_products.get`, `data_products.compile`*

<a id="playbook-knowledge-add-documents"></a>

### Add documents to a knowledge repository

Put documents the user has in their Files into one of their knowledge repositories — creating the repository first when they want a new one — and submit it for their approval.

**Use for:** "add the handbook to the Sales repository", "put my uploaded PDFs into a knowledge repository", "create a repository for our policies", "make a new knowledge base from these files".

*Taught to Knowledge Curator · submits a change · calls `files.list`, `files.knowledge_targets`, `rag.collection_create`, `files.to_knowledge`*

<a id="playbook-knowledge-clean-up"></a>

### Clean up a knowledge repository

Keep a repository's content right — quarantine or remove a wrong document, sync, screen, change settings — each submitted for the user's approval.

**Use for:** "quarantine the vendor FAQ", "remove the old terms from Policies", "take that document out of the repository", "sync the documentation repository", "screen my repository", "rename the Sales repository", "change how often Policies syncs".

*Taught to Knowledge Curator · submits a change · calls `knowledge.documents`, `knowledge.document_quarantine`, `knowledge.document_delete`, `knowledge.sync`, `knowledge.scan`, `knowledge.repository_settings`, `rag.collection_update`, `rag.collection_delete`*

<a id="playbook-knowledge-graph-governance"></a>

### A graph's versions, lifecycle and conformance

Where a knowledge graph stands — its saved versions, its lifecycle, whether it conforms, the tuning proposed for it — and saving a version before it changes.

**Use for:** "is my Policing graph ready to publish", "which versions of the Retail graph do I have", "save the graph before I change it", "is my ontology conformant", "who approved the Sales ontology", "are there tuning proposals for my graph", "which of my ontologies are published".

*Taught to Knowledge Curator · submits a change · calls `knowledge_graph.list`, `knowledge_graph.lifecycle`, `knowledge_graph.conformance`, `knowledge_graph.versions`, `knowledge_graph.version_save`, `knowledge_graph.tuning_proposals`, `ontologies.list`*

<a id="playbook-knowledge-graphs"></a>

### Explore a knowledge graph

What one of the user's knowledge graphs holds: its entity types and how they connect, the entities a question is about, and what sits next to one of them.

**Use for:** "what is in my Policing graph", "which tables does the Retail graph know", "what is Customer connected to", "how big is the Sales ontology", "which business concepts does my graph define", "which graphs do I have".

*Taught to Guide, Knowledge Curator · reads only · calls `knowledge_graph.list`, `knowledge_graph.schema_overview`, `knowledge_graph.stats`, `knowledge_graph.entities`, `knowledge_graph.node_neighborhood`, `knowledge_graph.subgraph`*

<a id="playbook-knowledge-health"></a>

### Check a knowledge repository's health

How sound a knowledge repository is: what it holds, whether its documents are screened, what the screening found, what is quarantined, and which documents the user rated badly.

**Use for:** "is my Policies repository healthy", "what's wrong with the documentation repository", "which documents in Sales are rated badly", "are there security findings in my knowledge".

*Taught to Guide, Knowledge Curator · reads only · calls `rag.collections`, `knowledge.health`, `knowledge.feedback`, `knowledge.documents`*

<a id="playbook-knowledge-search"></a>

### Try a question against a knowledge repository

See what an agent would retrieve from a knowledge repository for a question — which documents answer it and how well — without asking the agent.

**Use for:** "what does the Policies repository find for travel expenses", "does my documentation cover SSO", "test the knowledge base with this question", "why didn't my agent find the refund rule".

*Taught to Guide, Knowledge Curator · reads only · calls `rag.collections`, `knowledge.search`, `profiles.overview`*

<a id="playbook-publishing-bundle"></a>

### Bundle agents into a pack

Put some of the user's agents into a new draft pack — ready to be proposed and published by the user — or keep a pack's name, description and version right.

**Use for:** "bundle @SALES and @DOCS into a pack", "make a pack from my policing agents", "rename the Sales Assist pack", "set the pack's version to 1.2.0", "package these agents for the marketplace".

*Taught to Publisher · submits a change · calls `profiles.list`, `agent_packs.create_and_install`, `agent_packs.list`, `agent_packs.update`*

<a id="playbook-publishing-marketplace"></a>

### What is in the marketplace

Find packs others have published, read what one offers, and see what it would need from the user's data sources — without installing anything.

**Use for:** "which packs are about policing", "what is new in the marketplace", "what does the Policing pack contain", "what would the Policing pack need to run here", "who is my pack offered to".

*Taught to Publisher · reads only · calls `marketplace.agent_packs`, `marketplace.agent_pack`, `marketplace.agent_pack_requirements`, `marketplace.agent_pack_audience`*

<a id="playbook-publishing-readiness"></a>

### Is the pack ready to publish

Whether one of the user's packs can be published now, and if not, what stands in the way and who acts next.

**Use for:** "is Sales Assist ready to publish", "what blocks my pack", "can I publish my pack", "why can't I publish", "where is my pack in approval".

*Taught to Publisher · reads only · calls `agent_packs.list`, `agent_packs.lifecycle`, `agent_packs.governance_policy`, `agent_packs.get`, `trust.agents`*

<a id="playbook-quality-inbox"></a>

### What needs me

What waits for the user: incidents in the Assurance Inbox and items in the review queue — withheld answers, fired gates, changes agents submitted.

**Use for:** "what needs my attention", "what is in my inbox", "anything waiting for my approval", "what did my agents submit", "are there trust incidents".

*Taught to Guide, Quality Analyst · reads only · calls `assurance.inbox_count`, `assurance.inbox`, `assurance.reviews`*

<a id="playbook-quality-regression"></a>

### When did it get worse, and what changed

Find when an agent's quality, cost or latency moved, and line it up with what changed around then — its model, its configuration, its signature, its knowledge.

**Use for:** "@SALES got worse this week", "when did quality drop", "why is @DOCS slower", "did my change break @COORD", "is @OPTIM regressing".

*Taught to Quality Analyst · reads only · calls `evals.agent_timeline`, `assurance.trust_history`, `assurance.agent_story`, `evals.agent_runs`, `profiles.overview`*

<a id="playbook-quality-rescore"></a>

### Score a turn again, or try a policy

Check one turn again against what a right answer says (optionally with model judges), or see what a stricter trust policy would have done to past turns — before anyone switches it on.

**Use for:** "was this answer right — it should say 42", "score the last turn of that conversation again", "judge that answer", "what would enforce grounding have done to @SALES", "if I turned on stricter checks, what would change".

*Taught to Quality Analyst · submits a change · calls `sessions.list`, `evals.score_turn`, `evals.backtest`*

<a id="playbook-quality-whats-failing"></a>

### What is failing, and why

Which quality checks fail, for which agents, and what the failing turns have in common — read from the scores the platform records on every turn.

**Use for:** "which checks fail most", "why is @SALES failing", "what is wrong with my agents", "which agent has the worst quality", "show me failing turns for @DOCS".

*Taught to Quality Analyst · reads only · calls `evals.kpi_dashboard`, `evals.agent_runs`, `sessions.details`, `assurance.vocabulary`*

<a id="playbook-solving-gaps"></a>

### What is missing to answer a question

When no one can answer a question, find out what is missing — an expert or the data itself — and what would close the gap.

**Use for:** "nobody can answer this", "what would I need to answer this", "what am I missing to answer this", "build something that can answer this".

*Taught to Problem Solver · submits a change · calls `profiles.list`, `mcp.servers`, `rag.collections`, `knowledge_graph.list`, `profiles.create`*

<a id="playbook-solving-route"></a>

### Answer a question that spans several fields

Get a question answered that no single expert answers alone: ask the ones that fit each part and combine their answers, naming who answered what and what remains unanswered.

**Use for:** "who can answer this question", "answer this from my data and my documents together", "why did claims rise where incidents fell", "combine what my data expert and my policy expert know".

*Taught to Problem Solver · reads only · calls `profiles.list`, `profiles.overview`, `trust.agents`, `evals.kpi_dashboard`*

<a id="playbook-solving-second-opinion"></a>

### Get a second opinion

Check an answer by putting the same question to another expert, and report where the two answers agree and where they differ.

**Use for:** "check this answer with @FINANCE", "get a second opinion", "does @OPS agree with this", "verify this answer".

*Taught to Problem Solver · reads only · calls `profiles.list`, `trust.agents`*

<a id="playbook-trust-explain-agent"></a>

### Explain an agent's trust state

Why one agent reads trusted, changed, unsigned or not attested here — named down to the ingredient that decides it.

**Use for:** "is @SALES trusted", "why is @SALES changed", "why can't I sign @SALES", "what does the badge on @SALES mean".

*Taught to Guide, Quality Analyst · reads only · calls `trust.agent`*

<a id="playbook-trust-my-agents"></a>

### Trust across my agents

Which of the user's agents are trusted, changed, unsigned or not attested here — one overview.

**Use for:** "which of my agents are signed", "what needs signing", "show my trust overview".

*Taught to Guide, Quality Analyst · reads only · calls `trust.agents`*

<a id="playbook-usage-this-period"></a>

### My usage this period

What the user has spent this period — tokens, cost, turns, limits — and where it went.

**Use for:** "how much have I used", "what did I spend this month", "am I close to my limits", "which questions cost the most".

*Taught to Guide · reads only · calls `consumption.summary`, `consumption.turns`, `consumption.history`*
<!-- /generated:playbooks -->

---

<a id="appendix-operations"></a>

## Appendix B — Operations by role

For administrators and developers: each active role in Uderia's own operation names —
the changes it can submit and how each is reviewed, what it reads, what it lists but cannot
call (with why), and which operations and arguments each of its boundaries covers.

<!-- generated:operations -->
<a id="ops-guide"></a>

### Guide

Signed as `platform:role=guide@f4e91ca5dad4` — [back to the role](#role-guide)

**Reads**

- Agents: `profiles.active_for_consumption`, `profiles.classification`, `profiles.components`, `profiles.connector_settings`, `profiles.default`, `profiles.learning_repository`, `profiles.list`, `profiles.overview`, `profiles.resources`, `profiles.skills`, `profiles.test`
- Assurance: `assurance.agent_story`, `assurance.inbox`, `assurance.inbox_count`, `assurance.reviews`, `assurance.trust_history`
- Canvases: `canvases.list`
- Components: `components.get`, `components.list`
- Context windows: `context_window_types.get`, `context_window_types.list`
- Conversations: `sessions.analytics`, `sessions.list`
- Dashboards: `dashboards.get`, `dashboards.list`, `dashboards.tiles`
- Data products: `data_products.catalog`, `data_products.get`
- Flows: `flows.get`, `flows.list`, `flows.platform`, `flows.run_status`, `flows.runs`, `flows.templates`
- Knowledge: `knowledge.documents`, `knowledge.feedback`, `knowledge.health`, `knowledge.search`
- Knowledge graphs: `knowledge_graph.assignments`, `knowledge_graph.conformance`, `knowledge_graph.entities`, `knowledge_graph.lifecycle`, `knowledge_graph.list`, `knowledge_graph.node_neighborhood`, `knowledge_graph.relationships`, `knowledge_graph.schema_overview`, `knowledge_graph.stats`, `knowledge_graph.subgraph`, `knowledge_graph.subscriptions`, `knowledge_graph.versions`
- Ontologies: `ontologies.list`
- Quality: `evals.agent_timeline`, `evals.kpi_dashboard`
- Repositories: `rag.collection`, `rag.collections`
- Scheduler: `scheduler.status`
- Schedules: `scheduled_tasks.get`, `scheduled_tasks.list`, `scheduled_tasks.runs`
- Skills: `skills.list`
- Trust: `trust.agent`, `trust.agents`, `trust.artifact`, `trust.artifacts`, `trust.history`, `trust.keys`, `trust.verify`
- Usage: `consumption.history`, `consumption.summary`, `consumption.system_summary` *(only for admins)*, `consumption.turns`, `consumption.warnings`

**Listed, not callable**

- A person's act — the user does this in Uderia; an agent never does: `profiles.set_active_for_consumption`, `profiles.set_default`, `trust.action_sign_off`, `trust.agent_attest_deployment`, `trust.agent_pack_sign_off`, `trust.agent_sign_off`, `trust.assurance_module_sign_off`, `trust.canvas_sign_off`, `trust.dashboard_sign_off`, `trust.data_product_sign_off`, `trust.extension_sign_off`, `trust.flow_sign_off`, `trust.knowledge_sign_off`, `trust.ontology_extension_sign_off`, `trust.ontology_sign_off`, `trust.planner_sign_off`, `trust.revoke_signature`, `trust.skill_sign_off`, `trust.unassigned_assign` *(only for admins)*
- Outside this agent's role (Guide — read only); an agent with another role, or you, can do it: `profiles.activate`, `profiles.connector_settings_update`, `profiles.create`, `profiles.delete`, `profiles.learning_repository_own`, `profiles.reclassify`, `profiles.update`
- Reaches other users' data — not offered to agents yet: `consumption.users` *(only for admins)*, `trust.unassigned` *(only for admins)*

**What each boundary covers**

- Any change — to an agent, a repository, a flow or a schedule: `profiles.activate`, `profiles.connector_settings_update`, `profiles.create`, `profiles.delete`, `profiles.learning_repository_own`, `profiles.reclassify`, `profiles.update`
- Signing anything, attesting a deployment, or withdrawing a signature: `trust.*_sign_off`, `trust.agent_attest_deployment`, `trust.revoke_signature`
- Opening an agent to the REST API, or making one your default: `profiles.set_active_for_consumption`, `profiles.set_default`
- Other users' usage, conversations or unattributed signatures: `consumption.users`, `trust.unassigned`, `trust.unassigned_assign`

<a id="ops-agent-builder"></a>

### Agent Builder

Signed as `platform:role=agent-builder@9b669fee81ba` — [back to the role](#role-agent-builder)

**Changes it can submit**

- `knowledge_graph.activate` — always reviewed (it changes what an agent may do)
- `knowledge_graph.assignments_set` — always reviewed (it changes what an agent may do)
- `profiles.activate` — follows the write mode
- `profiles.connector_settings_update` — always reviewed (it changes what an agent may do)
- `profiles.create` — always reviewed (it changes what an agent may do)
- `profiles.delete` — always reviewed (it deletes)
- `profiles.learning_repository_own` — follows the write mode
- `profiles.reclassify` — follows the write mode
- `profiles.update` — always reviewed (it changes what an agent may do)

**Reads**

- Agents: `profiles.active_for_consumption`, `profiles.classification`, `profiles.components`, `profiles.connector_settings`, `profiles.default`, `profiles.learning_repository`, `profiles.list`, `profiles.overview`, `profiles.resources`, `profiles.skills`, `profiles.test`
- Components: `components.get`, `components.list`
- Context windows: `context_window_types.get`, `context_window_types.list`
- Data sources: `mcp.servers`
- Knowledge: `knowledge.documents`
- Knowledge graphs: `knowledge_graph.assignments`, `knowledge_graph.context`, `knowledge_graph.list`, `knowledge_graph.stats`
- Models: `llm.configurations`, `llm.configurations_context_limit`, `llm.configurations_test`, `llm.provider_health`
- Repositories: `rag.collection`, `rag.collections`
- Skills: `skills.list`
- Trust: `trust.agent`, `trust.agents`

**Listed, not callable**

- A person's act — the user does this in Uderia; an agent never does: `llm.configurations_activate`, `llm.configurations_create`, `llm.configurations_get`, `llm.configurations_update`, `mcp.servers_activate`, `mcp.servers_create`, `mcp.servers_import`, `mcp.servers_update`, `profiles.set_active_for_consumption`, `profiles.set_default`

**What each boundary covers**

- Entering, changing or reading back credentials — LLM configurations and MCP servers: `llm.configurations_create`, `llm.configurations_update`, `llm.configurations_get`, `mcp.servers_create`, `mcp.servers_update`, `mcp.servers_import`
- Choosing which LLM or MCP server is your active one: `llm.configurations_activate`, `mcp.servers_activate`
- Opening an agent to the REST API, or making one your default: `profiles.set_active_for_consumption`, `profiles.set_default`
- An agent's authority limits and its additional data sources: `profiles.create` (authorityConfig, additionalMcpServers), `profiles.update` (authorityConfig)

<a id="ops-knowledge-curator"></a>

### Knowledge Curator

Signed as `platform:role=knowledge-curator@f2c9086db8cf` — [back to the role](#role-knowledge-curator)

**Changes it can submit**

- `files.to_knowledge` — follows the write mode
- `knowledge.document_delete` — always reviewed (it deletes)
- `knowledge.document_quarantine` — follows the write mode
- `knowledge.repository_settings` — follows the write mode
- `knowledge.scan` — follows the write mode
- `knowledge.sync` — follows the write mode
- `knowledge_graph.rename` — follows the write mode
- `knowledge_graph.version_save` — follows the write mode
- `rag.collection_create` — follows the write mode
- `rag.collection_delete` — always reviewed (it deletes)
- `rag.collection_update` — follows the write mode

**Reads**

- Agents: `profiles.list`, `profiles.overview`
- Files: `files.knowledge_targets`, `files.list`
- Knowledge: `knowledge.documents`, `knowledge.feedback`, `knowledge.health`, `knowledge.search`
- Knowledge graphs: `knowledge_graph.assignments`, `knowledge_graph.authored`, `knowledge_graph.conformance`, `knowledge_graph.context`, `knowledge_graph.entities`, `knowledge_graph.lifecycle`, `knowledge_graph.list`, `knowledge_graph.node_neighborhood`, `knowledge_graph.relationships`, `knowledge_graph.schema_overview`, `knowledge_graph.stats`, `knowledge_graph.subgraph`, `knowledge_graph.subscriptions`, `knowledge_graph.tuning_proposals`, `knowledge_graph.versions`
- Marketplace: `marketplace.knowledge_graph`, `marketplace.knowledge_graph_audience`, `marketplace.knowledge_graphs`
- Ontologies: `ontologies.list`
- Repositories: `rag.collection`, `rag.collections`
- Trust: `trust.artifact`, `trust.artifacts`, `trust.history`

**Listed, not callable**

- A person's act — the user does this in Uderia; an agent never does: `knowledge.document_acknowledge_finding`, `knowledge.document_release`, `knowledge.document_source`, `knowledge_graph.apply_update`, `knowledge_graph.authored_deploy`, `knowledge_graph.authored_publish`, `knowledge_graph.authored_withdraw`, `knowledge_graph.clear`, `knowledge_graph.lifecycle_transition`, `knowledge_graph.marketplace_publish`, `knowledge_graph.redeploy_metric_views`, `knowledge_graph.redeploy_physical`, `knowledge_graph.reload_demo_data`, `knowledge_graph.tuning_decide`, `knowledge_graph.tuning_proposal_delete`, `knowledge_graph.version_delete`, `knowledge_graph.version_restore`, `marketplace.knowledge_graph_audience_set`, `marketplace.knowledge_graph_fork`, `marketplace.knowledge_graph_install`, `marketplace.knowledge_graph_rate`, `marketplace.knowledge_graph_subscribe`, `marketplace.knowledge_graph_sync`, `marketplace.knowledge_graph_unpublish`, `marketplace.knowledge_graph_unsubscribe`, `trust.knowledge_sign_off`, `trust.ontology_sign_off`

**What each boundary covers**

- Releasing a quarantined document, or acknowledging a security finding: `knowledge.document_release`, `knowledge.document_acknowledge_finding`
- Choosing where a document or a repository syncs from: `knowledge.document_source`, `knowledge.repository_settings` (source_root)
- The repository's standing policy after a sync: `knowledge.repository_settings` (sync_auto_ack_findings, sync_auto_resign)
- Another vector store, or its configuration: `rag.collection_create` (backend_type, backend_config, vector_store_config_id)
- Unlocking a planner repository's trust seal: `rag.collection_update` (trust_locked)
- Signing a repository: `trust.knowledge_sign_off`
- Moving an ontology along its lifecycle — proposing, approving, publishing, deprecating: `knowledge_graph.lifecycle_transition`, `trust.ontology_sign_off`
- Deleting a graph, restoring or deleting a saved version, rebuilding a graph from its source: `knowledge_graph.clear`, `knowledge_graph.version_restore`, `knowledge_graph.version_delete`, `knowledge_graph.apply_update`
- Changing a database from a graph — deploying drafted tables, re-creating tables, views or demo data, landing a tuning proposal: `knowledge_graph.authored_deploy`, `knowledge_graph.redeploy_physical`, `knowledge_graph.redeploy_metric_views`, `knowledge_graph.reload_demo_data`, `knowledge_graph.tuning_decide`, `knowledge_graph.tuning_proposal_delete`
- Publishing or withdrawing what people drafted on a graph: `knowledge_graph.authored_publish`, `knowledge_graph.authored_withdraw`
- Publishing a graph to the marketplace, choosing its audience, or installing, forking, subscribing to and rating someone else's: `knowledge_graph.marketplace_publish`, `marketplace.knowledge_graph_unpublish`, `marketplace.knowledge_graph_audience_set`, `marketplace.knowledge_graph_install`, `marketplace.knowledge_graph_fork`, `marketplace.knowledge_graph_subscribe`, `marketplace.knowledge_graph_unsubscribe`, `marketplace.knowledge_graph_sync`, `marketplace.knowledge_graph_rate`

<a id="ops-automation-builder"></a>

### Automation Builder

Signed as `platform:role=automation-builder@230d81e397c0` — [back to the role](#role-automation-builder)

**Changes it can submit**

- `flows.create` — follows the write mode
- `flows.delete` — always reviewed (it deletes)
- `flows.fork` — follows the write mode
- `flows.run` — follows the write mode
- `flows.run_cancel` — follows the write mode
- `flows.update` — follows the write mode
- `flows.version_restore` — follows the write mode
- `flows.version_save` — follows the write mode
- `scheduled_tasks.create` — always reviewed (it changes what an agent may do)
- `scheduled_tasks.delete` — always reviewed (it deletes)
- `scheduled_tasks.run_now` — follows the write mode
- `scheduled_tasks.update` — always reviewed (it changes what an agent may do)

**Reads**

- Agents: `profiles.list`, `profiles.overview`
- Flows: `flows.get`, `flows.list`, `flows.platform`, `flows.run_status`, `flows.runs`, `flows.template`, `flows.templates`, `flows.variables`, `flows.versions`
- Scheduler: `scheduler.status`
- Schedules: `scheduled_tasks.get`, `scheduled_tasks.list`, `scheduled_tasks.runs`
- Trust: `trust.agents`

**Listed, not callable**

- A person's act — the user does this in Uderia; an agent never does: `flows.toggle_platform` *(only for admins)*

**What each boundary covers**

- Where a schedule sends its results: `scheduled_tasks.create` (output_channel, output_config), `scheduled_tasks.update` (output_channel, output_config)
- Scheduling a database action: `scheduled_tasks.create` (action_spec), `scheduled_tasks.update` (action_spec)
- Editing a flow's steps: `flows.create` (definition), `flows.update` (definition)
- Debugging options when running a flow: `flows.run` (start_node_id, run_mode)
- Sharing a flow with every user: `flows.toggle_platform`

<a id="ops-quality-analyst"></a>

### Quality Analyst

Signed as `platform:role=quality-analyst@a3071c39d5a0` — [back to the role](#role-quality-analyst)

**Changes it can submit**

- `evals.score_turn` — follows the write mode

**Reads**

- Agents: `profiles.list`, `profiles.overview`
- Assurance: `assurance.agent_story`, `assurance.inbox`, `assurance.inbox_count`, `assurance.reviews`, `assurance.trust_history`, `assurance.trust_trip`, `assurance.vocabulary`
- Conversations: `sessions.analytics`, `sessions.context_analytics`, `sessions.details`, `sessions.list`
- Quality: `evals.agent_runs`, `evals.agent_timeline`, `evals.backtest`, `evals.kpi_dashboard`, `evals.library_outcomes`, `evals.online`, `evals.online_dashboard`, `evals.scoring_thresholds`, `evals.trust_behavior`, `evals.turn_score`
- Trust: `trust.agent`, `trust.agents`, `trust.artifact`, `trust.artifacts`, `trust.history`, `trust.keys`, `trust.verify`
- Usage: `consumption.history`, `consumption.summary`, `consumption.system_summary` *(only for admins)*, `consumption.turns`, `consumption.warnings`

**Listed, not callable**

- A person's act — the user does this in Uderia; an agent never does: `assurance.inbox_ack`, `assurance.inbox_ack_derived`, `assurance.review_decide`, `assurance.review_withdraw`, `assurance.trust_trip_reset`, `trust.action_sign_off`, `trust.agent_attest_deployment`, `trust.agent_pack_sign_off`, `trust.agent_sign_off`, `trust.assurance_module_sign_off`, `trust.canvas_sign_off`, `trust.dashboard_sign_off`, `trust.data_product_sign_off`, `trust.extension_sign_off`, `trust.flow_sign_off`, `trust.knowledge_sign_off`, `trust.ontology_extension_sign_off`, `trust.ontology_sign_off`, `trust.planner_sign_off`, `trust.revoke_signature`, `trust.skill_sign_off`, `trust.unassigned_assign` *(only for admins)*
- Reaches other users' data — not offered to agents yet: `consumption.users` *(only for admins)*, `trust.unassigned` *(only for admins)*

**What each boundary covers**

- Acknowledging an incident; approving, rejecting or withdrawing a request: `assurance.inbox_ack`, `assurance.inbox_ack_derived`, `assurance.review_decide`, `assurance.review_withdraw`
- Starting an agent's trust readings afresh: `assurance.trust_trip_reset`
- Signing anything, attesting a deployment, or withdrawing a signature: `trust.*_sign_off`, `trust.agent_attest_deployment`, `trust.revoke_signature`
- Other users' quality, usage or conversations: `consumption.users`, `trust.unassigned`, `trust.unassigned_assign`, `evals.kpi_dashboard` (scope), `evals.online_dashboard` (scope), `sessions.list` (all_users)

<a id="ops-insight-builder"></a>

### Insight Builder

Signed as `platform:role=insight-builder@4524ce6c248c` — [back to the role](#role-insight-builder)

**Changes it can submit**

- `canvases.delete` — always reviewed (it deletes)
- `canvases.update` — follows the write mode
- `canvases.version_restore` — follows the write mode
- `dashboards.create` — follows the write mode
- `dashboards.delete` — always reviewed (it deletes)
- `dashboards.layout` — follows the write mode
- `dashboards.tile_delete` — always reviewed (it deletes)
- `dashboards.tile_refresh` — follows the write mode
- `dashboards.tile_update` — follows the write mode
- `dashboards.update` — follows the write mode

**Reads**

- Agents: `profiles.list`, `profiles.overview`
- Canvases: `canvases.get`, `canvases.list`, `canvases.versions`
- Conversations: `sessions.list`
- Dashboards: `dashboards.get`, `dashboards.list`, `dashboards.tiles`
- Data products: `data_products.catalog`, `data_products.compile`, `data_products.get`
- Knowledge graphs: `knowledge_graph.list`

**What each boundary covers**

- Adding a tile, or changing what a tile shows or which question it asks: `dashboards.tile_update` (html, spec, source_prompt, profile_id)
- Choosing which agent answers when a tile refreshes: `dashboards.tile_refresh` (profile_id)
- How often a dashboard refreshes on its own: `dashboards.update` (auto_refresh_s)
- Writing a canvas's content: `canvases.update` (content)

<a id="ops-publisher"></a>

### Publisher

Signed as `platform:role=publisher@be691dc8e517` — [back to the role](#role-publisher)

**Changes it can submit**

- `agent_packs.create_and_install` — follows the write mode
- `agent_packs.update` — follows the write mode

**Reads**

- Agent packs: `agent_packs.get`, `agent_packs.governance_policy`, `agent_packs.lifecycle`, `agent_packs.list`
- Agents: `profiles.list`, `profiles.overview`
- Marketplace: `marketplace.agent_pack`, `marketplace.agent_pack_audience`, `marketplace.agent_pack_requirements`, `marketplace.agent_packs`
- Trust: `trust.agent`, `trust.agents`, `trust.artifact`, `trust.history`

**Listed, not callable**

- A person's act — the user does this in Uderia; an agent never does: `agent_packs.governance_policy_set` *(only for admins)*, `agent_packs.lifecycle_transition`, `agent_packs.notify_author`, `agent_packs.notify_author_update`, `agent_packs.publish`, `agent_packs.sla`, `agent_packs.sync_subscribers`, `marketplace.agent_pack_audience_set`, `marketplace.agent_pack_fork`, `marketplace.agent_pack_install`, `marketplace.agent_pack_rate`, `marketplace.agent_pack_sync`, `marketplace.agent_pack_unpublish`, `marketplace.agent_pack_unsubscribe`, `trust.agent_pack_sign_off`

**What each boundary covers**

- Proposing, approving, publishing, deprecating or withdrawing a pack: `agent_packs.lifecycle_transition`, `agent_packs.publish`, `marketplace.agent_pack_unpublish`
- Choosing who a pack is offered to: `marketplace.agent_pack_audience_set`
- Pushing a new version to subscribers: `agent_packs.sync_subscribers`
- Setting a pack's service level: `agent_packs.sla`
- Installing, forking, updating or leaving someone else's pack: `marketplace.agent_pack_install`, `marketplace.agent_pack_fork`, `marketplace.agent_pack_sync`, `marketplace.agent_pack_unsubscribe`
- Rating a pack, or emailing a pack's author: `marketplace.agent_pack_rate`, `agent_packs.notify_author`, `agent_packs.notify_author_update`
- The deployment's publishing rules: `agent_packs.governance_policy_set`
- Signing a pack: `trust.agent_pack_sign_off`

<a id="ops-admin-monitor"></a>

### Admin Monitor

Signed as `platform:role=admin-monitor@d7dd73f3c108` — [back to the role](#role-admin-monitor)

**Reads**

- Administration: `admin.db_health` *(only for admins)*, `admin.platform_jobs` *(only for admins)*, `admin.stats` *(only for admins)*
- Costs: `costs.analytics` *(only for admins)*, `costs.coverage` *(only for admins)*, `costs.models` *(only for admins)*
- Models: `llm.provider_health`
- Scheduler: `scheduler.status`
- Usage: `consumption.system_summary` *(only for admins)*

**Listed, not callable**

- Reaches other users' data — not offered to agents yet: `consumption.users` *(only for admins)*, `trust.unassigned` *(only for admins)*

**What each boundary covers**

- Any user's conversations, questions or answers — or usage named per user: `consumption.users`
- Signatures not yet attributed to an account: `trust.unassigned`
- Filling in missing prices, running a backup or a maintenance job: `costs.coverage` (fill)

<a id="ops-problem-solver"></a>

### Problem Solver

Signed as `platform:role=problem-solver@e8ba897af037` — [back to the role](#role-problem-solver)

**Reads**

- Agents: `profiles.active_for_consumption`, `profiles.components`, `profiles.list`, `profiles.overview`, `profiles.skills`
- Data products: `data_products.catalog`
- Data sources: `mcp.servers`
- Knowledge graphs: `knowledge_graph.context`, `knowledge_graph.list`
- Quality: `evals.kpi_dashboard`, `evals.trust_behavior`
- Repositories: `rag.collection`, `rag.collections`
- Trust: `trust.agent`, `trust.agents`

**Listed, not callable**

- Outside this agent's role (Problem Solver — read only); an agent with another role, or you, can do it: `profiles.create`

**What each boundary covers**

- Building the agent it finds missing: `profiles.create`
<!-- /generated:operations -->

---

<a id="about"></a>

## About this guide

The role sections and both appendices are **generated** from the roles
(`components/builtin/platform/roles/`), the operation catalog and the playbook library that
Uderia enforces — `python maintenance/platform_operations.py manual --write` — and a gate fails
when they fall behind. Each role's examples and boundaries are written in its own file, beside
its definition; everything else here is written by hand. Developer contract:
[PLATFORM_OPERATIONS_ARCHITECTURE.md](../Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md).
