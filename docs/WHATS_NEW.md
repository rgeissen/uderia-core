# What's New

This list reflects the recent enhancements and updates to Uderia, as shown on the application's welcome screen.

*   **14-Nov-2026:** The Core Is Fair Source — The public core is licensed under the Functional Source License: read it, run it, change it and use it inside your organisation. Each release becomes Apache 2.0 open source two years after it ships. The enterprise package adds the OBDA planes, identity federation, key managers, branding, the prompt editor and signing.
*   **12-Nov-2026:** Ready Sooner, With an Expert Included — The server answers within seconds of starting while the embedding model loads in the background, and a new installation comes with the Uderia Expert agent already installed and its documentation already indexed.
*   **11-Nov-2026:** Your Licence at a Glance — The Info box shows the licence this deployment runs on: its tier, which parts of the enterprise package it covers, who holds it and when it expires.
*   **30-Oct-2026:** A Clearer Assurance Guide — Each control now shows the properties it assesses, the modules it uses during the turn, and only the measures that really share its code. Every S points to its M; click either letter to see the other. [Architecture →](Architecture/ASSURANCE_SUBSTRATE_ARCHITECTURE.md)
*   **30-Oct-2026:** See What Decided the Grounding Gate — The gate states the finding it acted on, such as "1 of 13 statements unmatched — annotated", marks the modules it consulted, and shows what each one read. [Contract →](Architecture/TRUST_STATUS_COMMUNICATION.md)
*   **29-Oct-2026:** Follow-Up Questions Understood Again — Every agent type now receives the conversation's earlier answers, so a follow-up is answered in context instead of repeating the first answer. Turn Summaries mode (the Alt key) applies to every agent type.
*   **29-Oct-2026:** An Honest Answer When the AI Provider Fails — If the model provider fails (no credits, a rejected key, an outage), the answer says so in one plain sentence, the turn is recorded as failed, and the quality score shows it.
*   **29-Oct-2026:** Lower Cost Through Prompt Caching — Optimize planning reuses its cached prompt with Claude (direct, through OpenRouter or on Bedrock), Amazon Nova, OpenAI, Azure and xAI. [Architecture →](Architecture/PROMPT_CACHE_ARCHITECTURE.md)
*   **29-Oct-2026:** Each Agent Learns on Its Own — New Optimize agents learn into their own planner repository, so one agent's lessons never steer another. Existing agents can opt in. [Guide →](RAG/RAG.md)
*   **28-Oct-2026:** Every Model Has a Price — Each model in use is priced automatically or named as unpriced, and spend is booked under the model that was actually called, judges included. [Architecture →](Architecture/COST_TRACKING_ARCHITECTURE.md)
*   **26-Oct-2026:** The Right Tools for Each Question — Relevant per turn now finds tools by meaning as well as by words; the setting lives with each agent under Data sources → Tools.
*   **26-Oct-2026:** Safe Editing in Two Tabs — An open canvas, flow or dashboard follows changes made in another tab without overwriting your unsaved work. [Architecture →](Architecture/COMPONENT_ARCHITECTURE.md)
*   **25-Oct-2026:** Better on iPad — No more zooming into text fields, the keyboard no longer covers the composer, and the page can't be dragged out of place.
*   **24-Oct-2026:** Manage Your Files From Components — Components → Files now has a Files tab to search, rename, keep, download, delete and restore your files, like the tabs for canvases, dashboards and flows. Changes made in another tab or through the API appear at once. [Architecture →](Architecture/FILES_ARCHITECTURE.md)
*   **24-Oct-2026:** Files in Your Cloud, Under Your Keys — Keep files in Azure Blob Storage or Google Cloud Storage as well as a folder or S3, and wrap their keys with AWS KMS, Azure Key Vault, Google Cloud KMS or HashiCorp Vault — each tested before it is used. [Architecture →](Architecture/FILES_ARCHITECTURE.md)
*   **24-Oct-2026:** Ask About a Spreadsheet — Attach a CSV, TSV or Excel file to a question and the agent reads it as a table. The Files panel shows where a file was used as the turn starts, and reopens when you return to the conversation. [Architecture →](Architecture/FILES_ARCHITECTURE.md)
*   **24-Oct-2026:** Save Any Export to Files — Every download — a receipt, a graph, a canvas file, an audit pack — can be kept in Files in one click, and a dashboard becomes a PDF snapshot with each tile's refresh time. [Architecture →](Architecture/FILES_ARCHITECTURE.md)
*   **24-Oct-2026:** Agents and Flows That Work With Files — Let an agent find, read and save files itself, let a flow read a kept table and keep its results, and add a kept document to a knowledge repository. [Architecture →](Architecture/FILES_ARCHITECTURE.md)
*   **24-Oct-2026:** Keep What Your Conversations Produce — Keep any table, chart or answer in one click, then find it again from any conversation, see where it came from, and ask a new question about it. [Architecture →](Architecture/FILES_ARCHITECTURE.md)
*   **24-Oct-2026:** Files You Can Govern — Admins choose where files live — a local folder, a network share or S3 — encrypt them by default, set each tier’s space and downloads, and every download is recorded. [Architecture →](Architecture/FILES_ARCHITECTURE.md)
*   **23-Oct-2026:** An Assurance Guide in Plain Words — Every check is explained in one layout, in words the screen defines, and the Guide opens at the check you clicked — from the Trust Circle or from an answer. [Contract →](Architecture/TRUST_STATUS_COMMUNICATION.md)
*   **22-Oct-2026:** Figures That Mean What They Say — The agent, trust and awareness tiles now count exactly what their labels promise, with one definition of a failed turn and one time window throughout.
*   **20-Oct-2026:** A Trust Circle That Reads at a Glance — The ring opens with a one-sentence verdict, a chosen watch-only setting reads calm instead of alarming, and every station says what it checks. [Contract →](Architecture/TRUST_STATUS_COMMUNICATION.md)
*   **20-Oct-2026:** Smooth Typing Beside Large Graphs — The question box stays instant while a large knowledge graph is open beside the conversation; a hidden loop that kept the page busy is gone.
*   **20-Oct-2026:** Clear When You Are Signed Out — When a session ends, the page says so once and offers to sign in again, instead of every panel quietly showing an empty list.
*   **15-Oct-2026:** Panels That Stay Current — A change made anywhere — another tab, the agent editor or a script calling the API — appears in every open panel without reloading the page.
*   **14-Oct-2026:** Answers, Not SQL — When a model writes a query instead of running it, Uderia runs it safely and answers; a record that no query produced is flagged.
*   **14-Oct-2026:** Token Counts You Can Trust — Streamed responses are counted once, and a turn on another agent records the model that actually served it, so costs match the provider’s bill. [Architecture →](Architecture/COST_TRACKING_ARCHITECTURE.md)
*   **14-Oct-2026:** Signatures That Survive a Sync — When a synced knowledge repository changes, your standing policy decides whether its signature carries forward, renews, or waits for you to review it.
*   **14-Oct-2026:** Proven Answers Replay Instantly — Ask a question that a signed example already answers, word for word, and the proven answer comes back without spending a single token.
*   **13-Oct-2026:** Governed Data as Components — Signed data products, model authoring and actions are now components you switch on per agent, each with one policy: off, observe or enforce. [Architecture →](Architecture/OBDA_ARCHITECTURE.md)
*   **12-Oct-2026:** Design an Action Step by Step — Define an action in the Actions Designer: choose the connection and the concept, and Uderia derives the checks it must pass before it can be signed. [Architecture →](Architecture/ACT_ARCHITECTURE.md)
*   **12-Oct-2026:** A Faster Start — Sign-in, the agent anatomy and the trust pages open without waiting on slow lookups or external libraries, even on large estates.
*   **09-Oct-2026:** Ready for Regulated Use — A hardened mode tightens sign-in, keys, idle sessions and audit for regulated deployments, and refuses to start until every key is properly secured. [Architecture →](Architecture/SECURITY_ARCHITECTURE.md)
*   **09-Oct-2026:** Every Answer Shows What It Was Made With — Open an answer’s trust record to see each part that produced it — agent, ontology, skills, model, data — whether it was signed then, and whether it has changed since. [Architecture →](Architecture/EVIDENCE_ARCHITECTURE.md)
*   **09-Oct-2026:** Evidence Anyone Can Check — Export one answer or your whole estate as a single file and verify it offline, in a browser or with a small script, with no Uderia server involved. [Verifier →](https://trust.uderia.com/verify-evidence.html)
*   **07-Oct-2026:** Agents That Change Records, Safely — Agents can update records through signed action contracts: every write is checked in one transaction, sealed with a receipt, and can be undone.
*   **07-Oct-2026:** Four Eyes on Every Write — Name a supervisor for an agent, and its writes wait for that person’s approval. The owner cannot approve their own agent’s writes.
*   **07-Oct-2026:** Act From Flows, Schedules and Dashboards — A signed action can run as a flow step, on a schedule, or again from a pinned dashboard tile, always under its own rules.
*   **07-Oct-2026:** Faster Queries, Proposed for You — Uderia studies the queries your agents ran and proposes statistics or indexes. A person approves, and the gain is measured.
*   **07-Oct-2026:** One Model, Two Warehouses — A model extension can be deployed to PostgreSQL as well as Teradata, each to its own design standard, and read back to prove it.
*   **06-Oct-2026:** Grow the Model From a Question — When the model lacks a concept a question needs, the agent proposes it. You review the draft, then land it on the warehouse.
*   **05-Oct-2026:** Sharper Knowledge Graph Context — The graph brings in only what the question reaches, and follow-up questions keep the entities the conversation was about.
*   **04-Oct-2026:** A Calmer Question Box — The question box shows progress while the agent works and a receipt afterwards: verdict, time, tokens and cost.
*   **04-Oct-2026:** Answers Read Cleanly — Formatting in answers renders properly, and Uderia’s own notes are clearly marked as Uderia’s, never mistaken for the agent’s words.
*   **03-Oct-2026:** Create From the Workspace — Every Workspace section that can create something now has a “+”, and you can drag panels into the order you work in.
*   **02-Oct-2026:** Flows From a Shape — Pick a pattern such as pipeline, debate or pair programming, and the flow is built and staffed for you. Loops stop once a judge is satisfied.
*   **02-Oct-2026:** See Why a Query Is Slow — Explain any query and see its plan as a graph of steps, with costly patterns flagged first. Change it, explain again, and see what improved.
*   **02-Oct-2026:** Upgrades Back Up First — An update that changes the database layout takes a backup before it starts, and refuses to start if it cannot.
*   **01-Oct-2026:** Proven Teradata SQL Patterns — A library of Teradata SQL patterns, every statement run on a real Teradata before it ships. Agents open only what their own database can run.
*   **30-Sep-2026:** SQL Canvas Meets the Navigator — The SQL editor completes table and column names from your database, suggests joins, and hands a statement to the agent in one click.
*   **29-Sep-2026:** Celeris Provider — Celeris-1, a diffusion language model, is now available as an LLM provider.
*   **28-Sep-2026:** Several MCP Servers per Agent — One agent can read several data servers, such as two warehouses. Each call goes to the right server, and every step shows which one answered. [Details →](Architecture/MULTI_SOURCE_ARCHITECTURE.md)
*   **28-Sep-2026:** Combine Results Across Sources — When an answer needs two systems, code joins their results and checks the join, and every combined figure traces back to each source. [Details →](Architecture/MULTI_SOURCE_ARCHITECTURE.md)
*   **28-Sep-2026:** Ask Across Several Databases — Query PostgreSQL, MySQL, SQLite, DuckDB and MongoDB in one conversation, read-only, with every statement checked in that database’s own dialect first. [Details →](Architecture/DATABASE_NAVIGATOR_ARCHITECTURE.md)
*   **23-Sep-2026:** Every Number Has a Receipt — Figures are computed by code, not by the model’s own arithmetic. Click any marked number to see the exact rows it came from. [Details →](Architecture/COMPONENT_ARCHITECTURE.md#compute-component-the-receipted-figure-reference-s3-sep-2026)
*   **22-Sep-2026:** The Living Answer — The answer streams in place without jumping. When it is done, a short receipt shows its steps and one verdict: Verified, N findings, or Not checked. [Details →](Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-governed-stream-s1--one-delta-sink-every-engine)
*   **21-Sep-2026:** One Clear Layout for Every Answer — The answer comes first. Notes, evidence and actions sit beneath it in the same layout every time, each one a click away.
*   **20-Sep-2026:** Talk to It While It Works — Send a note mid-answer and the agent uses it at its next step. Close the tab, come back, and the answer is still there. [Details →](Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#steer--reattach-s10--talk-to-the-agent-while-it-works-never-lose-a-running-turn)
*   **20-Sep-2026:** Regenerate as an Experiment — A second answer is scored beside the first, so you choose with a reason. Editing a question starts a branch, and nothing is overwritten. [Details →](Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#regenerate-as-an-experiment-s8--a-second-answer-and-the-reason-to-prefer-it)
*   **20-Sep-2026:** Recovery You Can Audit — When a model provider fails, a fallback model you approved takes over and the answer says so. Every recovery is recorded. [Details →](Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-resilience-envelope-s4--one-policy-every-lane)
*   **20-Sep-2026:** Leaner Prompts, Tighter Turns — Repeated prompt content is cached and paid for once. Per agent, show the model only the tools it needs and give a turn a budget it can see. [Details →](Architecture/COST_TRACKING_ARCHITECTURE.md#cache-aware-pricing-s2--the-attested-cache)
*   **20-Sep-2026:** Also Available, Per Agent — Drafts can fix their own flagged issues, attachments can be read as verified sources, and you can watch what the ontology contributed, step by step. [Details →](Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-outcome-loop-s7--the-gates-finding-goes-back-to-the-model-once-on-every-lane)
*   **19-Sep-2026:** Wrong SQL Stopped Before It Runs — Queries are checked against your ontology before they run — wrong joins, missing columns, impossible values — and many are fixed automatically. [Details →](Architecture/PREFLIGHT_SQL_ARCHITECTURE.md)
*   **16-Sep-2026:** A Primer That Folds Away — A session primer can collapse to two headers, so the conversation starts at your first real question.
*   **12-Sep-2026:** The Database Navigator — Browse any connected database as a living catalog: what each table is for, its keys, likely joins and masked previews, for you and for the agent.
*   **12-Sep-2026:** Ground an Agent on a Released Ontology — An agent can read only the published version of its knowledge graph, so work on the draft never changes its answers.
*   **10-Sep-2026:** Knowledge Graphs Searched by Meaning — A question about “customers” now finds an entity called Party. On by default.
*   **07-Sep-2026:** Subscriptions That Follow the Publisher — A subscribed agent pack brings its ontologies, skills and signatures, and updates in place when the publisher republishes.
*   **05-Sep-2026:** Ontologies Validated Against the Data — A knowledge graph can be checked against the real data when it is built, so the agent gets its queries right the first time.
*   **28-Aug-2026:** White-Label Branding — One server can present several brands, each with its own name, look and email identity, chosen by web address.
*   **17-Aug-2026:** Progressive Disclosure Skills — A skill can be a whole library that the agent opens section by section, so deep expertise is on hand without being paid for on every turn. [Architecture →](Architecture/SKILL_ARCHITECTURE.md)
*   **16-Aug-2026:** Signed Data Products — Declare a data product on your ontology and Uderia proves its queries are safe before it can be signed. Every answer from it carries a verifiable receipt. [Architecture →](Architecture/OBDA_ARCHITECTURE.md)
*   **08-Aug-2026:** Your Vote Now Teaches the Agent — A thumbs up or down now improves the part of the agent that produced the answer, and hovering a vote tells you what it will do. [Architecture →](Architecture/FEEDBACK_SIGNAL_ARCHITECTURE.md)
*   **07-Aug-2026:** Trust, Open to Third Parties — Partners can add their own checks and controls to the Trust Circle. Each one arrives switched off and must be signed before use. [Developer guide →](manuals/BUILDING_ASSURANCE_MODULES.md)
*   **07-Aug-2026:** The Trust Panel, Rebuilt — Trust settings are now the Trust Circle itself: pick a guarantee, see what protects it, and save everything with one button.
*   **05-Aug-2026:** Controls That Act Sooner — Repeated calls, runaway budgets and unsupported answers are now stopped during the turn, not just reported after it.
*   **05-Aug-2026:** One Home Per Measurement — Every quality score is counted once and grouped by the step it measures, so a protection doing its job never turns red because of a later score.
*   **19-Jul-2026:** One Design Language — Every panel shares one layout: key figures on top, a resizable divider and clear tabs below, with names that match the navigator.
*   **19-Jul-2026:** The Agent Anatomy — Configuration opens as the agent itself, drawn as a star. Click any part to land where it is configured. Design → (`docs/roadmap/AGENT_ANATOMY_PANEL.md`)
*   **19-Jul-2026:** The Trust Circle — Eight live trust readings around every agent. Click a station to act on it, or press play to hear the agent’s story from its own records. [The story →](https://trust.uderia.com/the-life-of-a-uderia-agent.html)
*   **19-Jul-2026:** From Finding to Chunk — A security finding in a document repository now points to the exact text that caused it, and can be exported as a report. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **19-Jul-2026:** Knowledge Answers, Grounded — Answers from your documents show they stayed inside them: unsupported claims are marked, and the agent says when the documents don’t cover a question. Scope → (`docs/roadmap/RAG_TRUST_HARDENING.md`)
*   **19-Jul-2026:** A Screened, Governed Knowledge Base — Every document is screened as it is added, a bad one is a click from quarantine, and answers show how fresh and trustworthy their sources are. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **19-Jul-2026:** Proven, Not Promised — The knowledge protections ship with a benchmark anyone can rerun for free, plus live attack tests that check the defenses actually held. [Proof pack →](proof-packs/RAG_TRUST_HARDENING.md)
*   **18-Jul-2026:** Trust, Made Visible — See an agent’s data boundary on its knowledge graph, any drift across it in red, and each answer’s signed history as a chain you can verify. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **16-Jul-2026:** Test Your Own Defenses — Attack any agent with a live test set to see whether its guards held, then export a signed audit pack mapped to the EU AI Act, NIST and GDPR. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **15-Jul-2026:** Injection Firewalls — Hidden instructions in memories, documents and tool results are caught, closing the paths a check on your own question would miss. Architecture → (`docs/roadmap/TIER_1.3_GUARDRAILS.md`)
*   **13-Jul-2026:** Human Review — A risky answer can be held for a person to approve, with nothing lost and nothing sent unseen. Ideal for unattended runs. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **11-Jul-2026:** Why Trust This Answer? — Every answer carries a plain-language trust summary and a signed record anyone can verify offline. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **09-Jul-2026:** Assurance Inbox & Policy Backtesting — Trust incidents land in one queue. Before tightening a policy, replay it over your history to see what it would have changed. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **07-Jul-2026:** Behavioral Trust Score — An agent’s trust follows its measured behavior, and a circuit breaker can alert you or pause it when behavior slips. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **05-Jul-2026:** The Trust Journey — Every answer shows, in one compact strip, which protections it passed and which of them acted. [Manual →](manuals/AGENT_TRUST_AND_ASSURANCE.md)
*   **03-Jul-2026:** The Trust Membrane — A signed trust badge on every agent and shareable asset. An agent is trusted only if everything it is built from is, even after it is shared. [Architecture →](Architecture/TRUST_MEMBRANE_ARCHITECTURE.md)
*   **02-Jul-2026:** Agent Authority — An agent working on its own can never exceed the authority you granted, and its actions are recorded as its own, not yours. [Architecture →](Architecture/AGENT_AUTHORITY_ARCHITECTURE.md)
*   **01-Jul-2026:** Governed Metrics & Open Standards — Define a metric once and have the AI and your BI tools use it identically, then export it all in open W3C standards. [Architecture →](Architecture/OPEN_STANDARDS_ARCHITECTURE.md)
*   **30-Jun-2026:** Platform Ontology — A live map of your whole estate: every agent, what grounds it and what it uses, with one-click jumps to where each part is managed. [Architecture →](Architecture/PLATFORM_ONTOLOGY_ARCHITECTURE.md)
*   **28-Jun-2026:** Agent Memory — The agent remembers your preferences across sessions, per agent or shared, and you can view or erase every memory. Architecture → (`docs/roadmap/TIER_2.2_AGENT_MEMORY.md`)
*   **28-Jun-2026:** Uderia as an MCP Server — Use any governed Uderia agent as a tool in Claude Desktop, Cursor or other agents, with every protection still applied. [Architecture →](Architecture/MCP_SERVER_ARCHITECTURE.md)
*   **27-Jun-2026:** AI Guardrails — Personal data is masked, toxic output is blocked, and prompt injection is stopped before it reaches the model. Architecture → (`docs/roadmap/TIER_1.3_GUARDRAILS.md`)
*   **26-Jun-2026:** Grounding Gate — Answers are checked against the agent’s declared data scope, and can be flagged or held when they drift outside it. Architecture → (`docs/roadmap/TIER_1.1_GROUNDING_GATE.md`)
*   **24-Jun-2026:** Ontology — Turn Databricks industry data models into a knowledge graph, and deploy them as real Teradata databases with demo data. [Architecture →](Architecture/ONTOLOGY_ARCHITECTURE.md)
*   **21-Jun-2026:** Agent Observation & Evaluation — Every turn and flow run is scored on quality, cost and safety, with a health view for each agent. [Architecture →](Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md)
*   **21-Jun-2026:** Next Step Recommender — After each answer, one-tap suggestions for useful follow-up questions and actions, at little or no extra cost. [Architecture →](Architecture/NEXT_STEP_RECOMMENDER_ARCHITECTURE.md)
*   **10-Jun-2026:** Flow Parameters & Visual Debugging — Turn a flow into a reusable template, and debug it one node at a time while seeing each node’s input and output. [Architecture →](Architecture/FLOW_COMPONENT_ARCHITECTURE.md)
*   **05-Jun-2026:** Sharper Self-Learning — The agent learns only from turns that actually fetched data, so the approaches it reuses stay accurate.
*   **17-May-2026:** Dashboard Component — Pin any chart or table from the conversation to a persistent dashboard whose tiles refresh on demand. [Architecture →](Architecture/DASHBOARD_ARCHITECTURE.md)
*   **16-May-2026:** Visual Flow Orchestration — Describe a multi-step workflow and the agent builds and runs it as a live graph, with branches and parallel paths. [Architecture →](Architecture/FLOW_COMPONENT_ARCHITECTURE.md)
*   **14-May-2026:** New LLM Providers — MLX for Apple Silicon local inference and xAI for Grok models, expanding provider flexibility from hyperscalers to sovereign on-device execution.
*   **10-May-2026:** Infinity Context — The agent recalls the relevant parts of a long conversation instead of forgetting the oldest ones.
*   **09-May-2026:** Model Guide — Compare providers, models, context sizes and prices in the Uderia Model Guide, one click from the LLM settings.
*   **03-May-2026:** Your Workplace Apps, Connected — The agent works across Google, Microsoft 365 and Slack, with each user connecting their own accounts.
*   **03-May-2026:** Per-User Component Settings — Admins can switch individual components on or off for specific users.
*   **03-May-2026:** Scheduler Platform Jobs — Usage counters reset themselves hourly, daily and monthly, with no manual upkeep.
*   **03-May-2026:** Resource Panel UX — One consistent card design across profiles, collections, connectors and components.
*   **02-May-2026:** Always-Fresh Knowledge — Knowledge repositories update themselves when source documents change, re-indexing only what is new.
*   **02-May-2026:** Security Architecture — A published reference for authentication, access control, secrets, audit and compliance. [Security Architecture](Architecture/SECURITY_ARCHITECTURE.md)
*   **02-May-2026:** JIT Provisioning & Group Sync — User roles update automatically at every SSO login based on the user's current IdP group membership; no manual tier changes needed after org changes.
*   **02-May-2026:** SAML 2.0 Enterprise SSO — Connect to corporate identity providers via SAML 2.0; supports multiple IdP configurations, SP metadata, and both IdP- and SP-initiated login flows.
*   **01-May-2026:** Task Scheduler — Schedule any agent query to run automatically on a cron or interval; manage schedules through conversation or the split-panel UI, with results delivered by email or webhook.
*   **28-Apr-2026:** Parallel Query Execution — Multi-step queries run faster: independent plan phases and date-range iterations now execute in parallel instead of sequentially.
*   **27-Apr-2026:** Engine Modularization — Each agent class now runs on its own engine, so each can be improved without affecting the others. [Architecture →](Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md)
*   **16-Apr-2026:** Knowledge Graphs in Agent Packs — An agent pack now carries each agent’s knowledge graph and restores it on install.
*   **16-Apr-2026:** Knowledge Graph Import — Import a graph as a new named graph or merge it into an existing one, without displacing the active graph.
*   **15-Apr-2026:** Teradata Hybrid Search — Combine keyword and meaning search on Teradata knowledge repositories for more relevant results.
*   **10-Apr-2026:** Genie Coordinator Pass-Through — Single-expert queries skip the synthesis step, cutting coordinator token cost and response time in half.
*   **04-Apr-2026:** OpenRouter Support — Access 100+ models (Llama, Mistral, Gemma, and more) via a single API key.
*   **10-Mar-2026:** Hybrid Search — Choose between semantic, hybrid (RRF), and keyword (BM25) search on Qdrant Cloud knowledge repositories.
*   **08-Mar-2026:** Teradata Vector Store — Keep knowledge repositories in Teradata, with embedding and chunking done on the server.
*   **07-Mar-2026:** ChromaDB & Qdrant Cloud — Store knowledge locally with ChromaDB by default, or in Qdrant Cloud.
*   **12-May-2026:** Scale Knowledge Graphs to Neo4j — Start on the built-in store and move large graphs to Neo4j, without changing agents or workflows.
*   **06-Mar-2026:** Choose Your Vector Store — Knowledge repositories work the same on ChromaDB, Teradata or Qdrant.
*   **01-Mar-2026:** Context Window Management — Control how much of your conversation history and knowledge the agent sees; configure per profile with live token budget visualization.
*   **29-Feb-2026:** Canvas Component — Write and run SQL directly in the chat canvas with syntax highlighting and live result rendering.
*   **28-Feb-2026:** Generative UI Components — Answers can render rich, interactive components, and new ones plug in without changing Uderia.
*   **27-Feb-2026:** Data Visualization — Turn query results into interactive charts (16 types) automatically; the agent picks the right visual for your data.
*   **23-Feb-2026:** Extensions — Transform agent responses on the fly: extract JSON, classify, summarize, generate PDFs, or chain custom transformations with a single # trigger.
*   **22-Feb-2026:** Skills - Pre-processing prompt injections with 5 built-in skills and visual editor
*   **15-Feb-2026:** Dual-Model Cost Breakdown - Live Status displays strategic vs tactical costs for Fusion Optimizer dual-model executions with color-coded visualization
*   **14-Feb-2026:** Theme-Aware Token/Cost KPIs - Live Status token and cost displays now adapt to both dark and light themes with consistent visibility
*   **13-Feb-2026:** n8n Integration - Visual workflow automation with three production-ready templates (Simple Query, Scheduled Reports, Slack Integration) and comprehensive deployment guides
*   **08-Feb-2026:** Agent Packs - Bundle complete agent teams (coordinator, experts, knowledge collections) into portable .agentpack files for one-click install, export, and marketplace sharing
*   **01-Feb-2026:** Text to Speech - Voice output now available for all profile classes with Escape-to-cancel and smart text truncation
*   **31-Jan-2026:** Document Upload & Multimodal Analysis - Attach documents and images in chat with native multimodal delivery and automatic text extraction fallback across all providers
*   **30-Jan-2026:** All Models Available - Access all provider models with ★ recommended highlights
*   **30-Jan-2026:** Light Theme - Full light mode support with proper contrast and visibility across all components
*   **25-Jan-2026:** Legacy Theme - Fixed color bleeding issues with opaque backgrounds and consistent gray palette
*   **23-Jan-2026:** Modern Theme - Enhanced glass-panel effects and refined slate/blue color palette
*   **17-Jan-2026:** Genie Profiles - Hierarchical AI Organizations with multi-level autonomous coordination (Parent → Child agents)
*   **16-Jan-2026:** Session Primer - Auto-initialize sessions with domain knowledge, transforming generic LLMs into pre-educated specialists
*   **16-Jan-2026:** Genie Profile Type (Beta) - Multi-profile coordination with LangChain orchestration
*   **16-Jan-2026:** Genie UI Features - Inline progress cards, collapsible child sessions, split view access
*   **09-Jan-2026:** Knowledge Focused (RAG) Profile Type - Mandatory knowledge retrieval with anti-hallucination safeguards
*   **11-Jan-2026:** Profile Classes - Four Execution Modes (Conversation, Efficiency, Knowledge, Genie)
*   **10-Jan-2026:** Export/Import Knowledge Repositories
*   **09-Jan-2026:** Export/Import Planner Repositories
*   **02-Jan-2026:** OAuth Implementation - Google, Github
*   **02-Jan-2026:** Email Validation for Registration
*   **20-Dec-2025:** Extended Prompt Management System - Dynamic Workflow Prompts
*   **19-Dec-2025:** Extended Prompt Management System - Dynamic Variables
*   **19-Dec-2025:** Extended Bootstrapping - Enhanced Bootstraping Parameter Configuration
*   **12-Dec-2025:** Enhanced Prompt Encryption/Decryption Process using Database Encryption
*   **12-Dec-2025:** Migration from File based Application Configuration to Database Schema
*   **05-Dec-2025:** Consumption Profile Enforcement - Rate Limiting and Usage Quotas
*   **07-Dec-2025:** Financial Governance - Dashboards and LiteLLM Integration
*   **06-Dec-2025:** Planner Constructor: SQL Query - Document Context
*   **05-Dec-2025:** Planner Constructor: SQL Query - Database Context
*   **29-Nov-2025:** Knowledge Repository Constructor - Document Storage
*   **28-Nov-2025:** Knowledge Repository Integration
*   **28-Nov-2025:** Multi-User Authentication - JWT tokens, access tokens, user tiers
*   **22-Nov-2025:** Profile System - Modular Configuration & Temporary Overrides
*   **21-Nov-2025:** Planner Repository Constructors - Modular Plugin System
*   **21-Nov-2025:** Modern UI Design
*   **15-Nov-2025:** Flowise Integration
*   **15-Nov-2025:** Airflow Integration
*   **14-Nov-2025:** Self-Improving AI (RAG)
*   **07-Nov-2025:** UI Real-Time Monitoring of Rest Requests
*   **31-Oct-2025:** Fully configurable Context Management (Turn & Session)
*   **26-Oct-2025:** Turn Replay & Turn Reload Plan
*   **25-Oct-2025:** Stop Button Added - Ability to immediately Stop Workflows
*   **24-Oct-2025:** Robust Multi-Tool Phase Handling
*   **11-Oct-2025:** Friendly.AI Integration
*   **10-Oct-2025:** Context Aware Rendering of the Collateral Report
*   **20-Sep-2025:** Microsoft Azure Integration
*   **19-Sep-2025:** REST Interface for Engine Configuration, Execution & Monitoring
*   **12-Sep-2025:** Significant Formatting Upgrade (Canonical Baseline Model for LLM Provider Rendering)
*   **05-Sep-2025:** Conversation Mode (Google Cloud Credentials required)
