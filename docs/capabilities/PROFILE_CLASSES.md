# 🎭 Profile Classes: The IFOC Workflow

The **IFOC Workflow** is four execution modes that follow how experts solve problems: explore an idea, verify it against sources, execute it with tools, coordinate several specialists.

## The IFOC Philosophy: Ideate → Focus → Optimize → Coordinate

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           THE IFOC WORKFLOW                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   🟢 IDEATE          🔵 FOCUS           🟣 OPTIMIZE        🟠 COORDINATE        │
│   ─────────          ───────           ──────────        ────────────          │
│   Brainstorm         Research          Execute           Orchestrate           │
│   Explore            Verify            Deliver           Scale                 │
│   Draft              Ground            Operate           Synthesize            │
│                                                                                 │
│   "What if...?"      "What does        "Do it."          "Handle              │
│                       policy say?"                        everything."         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Every profile belongs to one of four classes, each designed for a specific phase of intelligent work. Together, they create a composable AI architecture that adapts to any challenge.

---

### 1. 🟢 IDEATE - Conversation Focused (LLM)

**Philosophy: Creative exploration without constraints**

```
┌─────────────────────────────────────────┐
│  User Question                          │
│  "How do I optimize this query?"        │
└──────────────┬──────────────────────────┘
               │
               ▼
         ┌──────────┐
         │   LLM    │  ← Pure reasoning by default
         │ Analysis │  ← Optional MCP / component tools (ReAct)
         └─────┬────┘
               │
               ▼
┌──────────────────────────────────────────┐
│  Expert Advice + Code Examples           │
│  Ready for review before execution       │
└──────────────────────────────────────────┘
```

**The Value:**
Transform your LLM into a trusted thought partner. Explore possibilities, brainstorm solutions, and draft approaches—all without touching live systems. The **Ideate** phase is where creativity flows freely.

**When to Use IDEATE:**
- **Exploring new ideas**: "What approaches could solve this problem?"
- **Learning concepts**: "Explain CTEs in SQL with examples"
- **Drafting solutions**: "Write a query to calculate customer lifetime value"
- **Planning ahead**: "What should I consider before migrating this database?"

**Breakthrough Potential:**
- **Zero-Cost Exploration**: Learn complex concepts without expensive tool invocations
- **Rapid Prototyping**: Draft SQL, APIs, and workflows before committing resources
- **Risk-Free Testing**: Validate approaches before touching production systems
- **Training Ground**: Onboard new team members without data exposure
- **Optional Tools**: Enable *Use MCP tools* to give Ideate a streaming ReAct tool loop over the agent's MCP tools and component tools — [Engine Modularization →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md)

**Example Profiles:**
- `@CHAT` - Your AI thought partner for any question
- `@ARCHITECT` - System design and architecture guidance
- `@MENTOR` - Code review and technical mentoring

---

### 2. 🔵 FOCUS - Knowledge Focused (RAG)

**Philosophy: Grounded answers from verified sources**

```
┌─────────────────────────────────────────┐
│  User Question                          │
│  "What's our remote work policy?"       │
└───────────────┬─────────────────────────┘
                │
                ▼
    ┌───────────────────────┐
    │  Semantic Search      │
    │  Your Document Store  │ ← Policies, SOPs, Manuals
    └───────┬───────────────┘
            │
            ▼
      ┌──────────┐
      │   LLM    │
      │ Synthesis│ ← ONLY uses retrieved docs
      └─────┬────┘  NO general knowledge allowed
            │
            ▼
┌───────────────────────────────────────────┐
│  Answer + Source Citations                │
│  "Per HR Policy 3.2, page 7..."          │
│  [View Source Document]                   │
└───────────────────────────────────────────┘
```

**The Value:**
The **Focus** phase grounds every answer in your documents, policies and institutional knowledge, and the grounding gate checks the answer against what was retrieved. When accuracy matters more than creativity, Focus answers from the sources and cites them.

**When to Use FOCUS:**
- **Compliance questions**: "What does policy say about data retention?"
- **Reference lookups**: "What's the approved vendor list?"
- **Verification**: "Does this approach meet our security standards?"
- **Institutional knowledge**: "How did we handle this situation before?"

**Breakthrough Potential:**
- **Zero Hallucination Guarantee**: Answers only from your verified documents, checked by the grounding gate's corpus-support trigger before delivery ([Answer gates →](../Architecture/ANSWER_GATES_ARCHITECTURE.md))
- **Institutional Memory**: Never lose domain expertise when people leave
- **Compliance Confidence**: All responses traceable to source documents
- **Instant Expertise**: New hires access decades of knowledge immediately

**Example Profiles:**
- `@POLICY` - Corporate policies and procedures
- `@LEGAL` - Contracts, compliance, and regulations
- `@TECHNICAL` - Engineering documentation and runbooks

[Architecture →](../Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md)

---

### 3. 🟣 OPTIMIZE - Efficiency Focused (Tool)

**Philosophy: Strategic execution that learns and heals**

```
┌──────────────────────────────────────────┐
│  User Request                            │
│  "Show Q4 revenue by region"             │
└───────────────┬──────────────────────────┘
                │
                ▼
      ┌─────────────────────┐
      │ FUSION OPTIMIZER    │
      │ Strategic Planning  │ ← Multi-phase meta-plan
      └────────┬────────────┘
               │
               ▼
      ┌─────────────────────┐
      │ Tactical Execution  │ ← Per-phase tool selection
      │ + Self-Correction   │ ← Autonomous error recovery
      └────────┬────────────┘
               │
               ▼
    ┌───────────────────────┐
    │  Execute Operations   │ ← Database queries, APIs, tools
    │  via MCP Server       │ ← MCP Tools + Prompts access
    └───────┬───────────────┘
            │
            ▼
┌───────────────────────────────────────────┐
│  Results + Visualizations                 │
│  Strategic plans + Self-healing execution │
│  Full transparency + audit trail          │
└───────────────────────────────────────────┘
```

**The Value:**
The **Optimize** phase runs on the **Fusion Optimizer**, a multi-layered planner and executor that plans strategically, learns from past successes and corrects its own failed steps.

**When to Use OPTIMIZE:**
- **Live data operations**: "Show me Q4 results by region"
- **Complex workflows**: "Calculate inventory turnover and flag anomalies"
- **Automated tasks**: "Export customer segments to CSV"
- **Real-time monitoring**: "Alert if error rate exceeds threshold"

**Breakthrough Potential:**
- **Strategic Intelligence**: Creates multi-phase plans, not just single-shot responses
- **Autonomous Self-Correction**: Detects and fixes errors without human intervention
- **Full MCP Integration**: The only profile class that supports both **MCP Tools AND MCP Prompts**—execute pre-built workflows and complex multi-step operations directly
- **Cost Optimization**: plan hydration and the tactical fast path skip model calls whose answer is already known
- **Proactive Optimization**: Learns from context to skip redundant operations
- **Democratize Expertise**: Non-technical users execute complex operations through conversation
- **Complete Transparency**: See every decision, every tool call, every self-correction in real-time

**Real-World Transformation:**
- **Before**: Write SQL → Debug errors → Retry → Export → Format → Email
- **After**: "Analyze Q4 sales trends and email the exec team" (one request; it self-corrects and learns)

**Example Profiles:**
- `@OPTIMIZER` - Full Fusion Optimizer with all features enabled
- `@PROD` - Production database operations with enterprise LLM
- `@ANALYTICS` - Business intelligence and self-service reporting
- `@DEVOPS` - Infrastructure monitoring and intelligent automation

[Architecture →](../Architecture/FUSION_OPTIMIZER_ARCHITECTURE.md)

---

### 4. 🟠 COORDINATE - Genie (Multi-Profile)

**Philosophy: Autonomous orchestration at scale**

**The Value:**
**This is the breakthrough.** The **Coordinate** phase creates **autonomous AI organizations** where specialized agents collaborate intelligently. One question triggers a cascade of expert consultations, data retrievals, and synthesis—all happening automatically.

**When to Use COORDINATE:**
- **Multi-domain questions**: "Analyze Q4, check compliance, and recommend strategy"
- **Complex investigations**: "Research this issue across all our systems"
- **Executive summaries**: "Prepare a board presentation on performance"
- **Cross-functional work**: "Coordinate finance, legal, and engineering review"

**Breakthrough Potential:**
- **Multi-Level Intelligence**: Coordinators can orchestrate other Coordinators, creating hierarchical AI organizations
- **Compound Expertise**: Combine database operations + knowledge retrieval + analysis in a single workflow
- **Adaptive Problem Solving**: The system decides which experts to consult based on the question
- **Conversational State**: Each expert maintains context across the entire conversation
- **Scalable Architecture**: Build AI "departments" with master coordinators managing specialized teams

**Nested Coordination**
Coordinate profiles can orchestrate *other Coordinate profiles*, enabling organizational depth:

```
                         ┌─────────────────────────────────────┐
                         │  User: "@CEO, analyze Q4 and       │
                         │   recommend strategy"               │
                         └────────────────┬────────────────────┘
                                          │
                                          ▼
                              ╔═══════════════════════╗
                              ║   @CEO (Level 0)      ║  ← Master Coordinator
                              ║   Strategic Genie     ║
                              ╚═══════════╤═══════════╝
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    │                     │                     │
                    ▼                     ▼                     ▼
         ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
         │ @CFO (Level 1)   │  │ @CTO (Level 1)   │  │ @LEGAL (Level 1) │
         │ Financial Genie  │  │ Technical Genie  │  │ Policy Knowledge │
         └────────┬─────────┘  └────────┬─────────┘  └──────────────────┘
                  │                     │
         ┌────────┴────────┐   ┌───────┴────────┐
         │                 │   │                │
         ▼                 ▼   ▼                ▼
    ┌─────────┐      ┌─────────┐  ┌─────────┐  ┌─────────┐
    │ @ACCT   │      │ @AUDIT  │  │ @DB_ADM │  │ @SECURE │
    │ DB Ops  │      │ Checks  │  │ Schema  │  │ Analysis│
    │(Level 2)│      │(Level 2)│  │(Level 2)│  │(Level 2)│
    └─────────┘      └─────────┘  └─────────┘  └─────────┘
```

**How It Works:**
1. `@CEO` receives question, delegates to financial, technical, and legal experts
2. `@CFO` (itself a Genie) autonomously coordinates `@ACCT` and `@AUDIT`
3. `@CTO` (itself a Genie) autonomously coordinates `@DB_ADM` and `@SECURE`
4. Each specialist executes its task (queries, document retrieval, analysis)
5. Results cascade back up: specialists → coordinators → master
6. `@CEO` synthesizes comprehensive strategic recommendation

**All of this happens automatically from a single user question.**

**Real-World Transformation:**

*Before Genie Profiles:*
- User manually runs 5 separate queries
- Copies results between tools
- Synthesizes insights manually
- Repetitive manual work

*With Genie Profiles:*
- User: "@CEO, analyze Q4 performance, check compliance, and recommend next quarter strategy"
- Genie autonomously: Queries financial database → Retrieves policy docs → Analyzes trends → Cross-checks regulations → Synthesizes strategic recommendations
- Result delivered in one request, fully documented with an audit trail

**Example Profiles:**
- `@EXECUTIVE` - C-level strategic intelligence coordinator
- `@ANALYST` - Coordinates data retrieval, policy checks, and reporting
- `@AUDITOR` - Multi-source compliance verification
- `@RESEARCHER` - Deep-dive investigations across systems and knowledge bases

**Safeguards & Control:**
- **Circular Dependency Prevention**: Automatic detection prevents infinite loops
- **Depth Limits**: Configurable maximum nesting (default: 3 levels, Administration → Optimizer)
- **Cost Visibility**: Real-time token tracking across all coordination levels
- **Transparent Execution**: See exactly which experts are consulted and why
- **Context Preservation**: Each expert maintains conversation history across turns

**Coordinator Pass-Through Optimization:**

When consulting a single expert is sufficient, the coordinator skips its own synthesis LLM call and passes the expert's answer through directly. This saves the coordinator's synthesis call, its tokens and its latency, on focused single-expert questions.

*Pass-through fires when all of the following are true:*
- Exactly **one expert** was consulted (routing LLM called a single tool)
- **No prior conversation history** is present — i.e. the first turn in Full Context mode, or any turn when Turn Summaries mode is active
- The expert returned a non-empty answer

*Pass-through is always skipped when:*
- **Multiple experts** were consulted — their answers must be combined
- **Conversation history is present** (Full Context mode, turn 2 and beyond) — the coordinator weaves prior turns into its answer
- A **knowledge-only expert** (Ideate/Focus) answered but the coordinator also has an **Optimize expert it has not called** — synthesis decides whether the executor runs next (e.g. a dictionary → executor pipeline)

Otherwise every profile type qualifies for pass-through: Optimize (`tool_enabled`) experts run their own internal synthesis pipeline, so the coordinator receives a finished natural-language answer, not raw data.

**Full result pass-through (per Optimize child):** tick *Full data* next to an Optimize expert (Capabilities → Available Child Profiles) to hand the coordinator its raw result rows as well as its answer — for enumerations the coordinator must list.

The Live Status panel shows a **"Synthesis Skipped"** card (instead of "LLM Synthesis Started / Results") when pass-through fires, making the optimization visible.

**Configuration Example:**
```json
{
  "tag": "CEO",
  "profile_type": "genie",
  "genieConfig": {
    "slaveProfiles": ["profile-…cfo", "profile-…cto", "profile-…legal"],
    "slaveProfileSettings": {
      "profile-…cfo": { "fullResultPassthrough": true }
    }
  }
}
```

`slaveProfiles` holds child **profile IDs**; the CFO and CTO children are themselves Genie profiles that coordinate their own specialist teams—creating true organizational intelligence. Nesting depth is a deployment setting (default 3, lockable): **Administration → Optimizer → Genie Coordination → Max Nesting Depth**.

[Architecture →](../Architecture/COORDINATOR_ARCHITECTURE.md)

---

## IFOC Selection Guide: Choose the Right Phase

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                              IFOC SELECTION MATRIX                                       │
├──────────────┬────────────────┬───────────────┬──────────────┬─────────────────────────┤
│              │🟢 IDEATE       │🔵 FOCUS       │🟣 OPTIMIZE   │🟠 COORDINATE            │
│              │ (Conversation) │ (Knowledge)   │ (Efficiency) │ (Multi-Profile)         │
│              │                │               │              │                         │
├──────────────┼────────────────┼───────────────┼──────────────┼─────────────────────────┤
│ PHILOSOPHY   │ Explore        │ Verify        │ Execute      │ Orchestrate             │
│              │ Brainstorm     │ Ground        │ Deliver      │ Scale                   │
│              │ Draft          │ Reference     │ Operate      │ Synthesize              │
├──────────────┼────────────────┼───────────────┼──────────────┼─────────────────────────┤
│ DATA ACCESS  │ Optional       │ Documents     │ Full (MCP    │ All Sources (Adaptive)  │
│              │ (MCP Tools/RAG)│ Only          │ Tools+Prompts│                         │
├──────────────┼────────────────┼───────────────┼──────────────┼─────────────────────────┤
│ SAFETY       │ Exploratory    │ Zero          │ Governed     │ Composite (Inherits)    │
│              │ (Interactive)  │ Hallucinate   │ Audit Trail  │                         │
├──────────────┼────────────────┼───────────────┼──────────────┼─────────────────────────┤
│ COST         │ Low per turn   │ Low-Moderate  │ Lowest for   │ Variable (Scales with   │
│ (complex)    │ (many turns)   │ (documents)   │ complex tasks│ complexity & depth)     │
├──────────────┼────────────────┼───────────────┼──────────────┼─────────────────────────┤
│ SPEED        │ Fastest        │ Fast          │ Fast + Smart │ Comprehensive           │
│              │                │               │ (Self-heals) │ (Auto-parallel)         │
├──────────────┼────────────────┼───────────────┼──────────────┼─────────────────────────┤
│ USE WHEN     │ "How do I      │ "What does    │ "Show me Q4  │ "Analyze Q4, check      │
│              │  optimize?"    │  policy say?" │  results"    │  compliance, recommend" │
└──────────────┴────────────────┴───────────────┴──────────────┴─────────────────────────┘
```

## IFOC Workflow Patterns

**Pattern 1: Ideate → Focus → Optimize**
```
1. 🟢 IDEATE  "Draft a query to find inactive customers"
              → Get SQL without execution (safe, cheap)

2. 🔵 FOCUS   "What's our customer data retention policy?"
              → Verify compliance from documents

3. 🟣 OPTIMIZE "Execute the query I just drafted"
               → Run against live database (controlled)
```

**Pattern 2: Coordinate for Strategic Work**
```
🟠 COORDINATE  "Prepare board presentation on Q4 performance"

Automatically triggers:
  → 🟣 @CFO      (OPTIMIZE: Financial analysis + database queries)
  → 🔵 @LEGAL    (FOCUS: Compliance checks from policies)
  → 🟣 @ANALYST  (OPTIMIZE: Trend analysis + visualizations)
  → Synthesis   (Coordinated strategic narrative)

Result: Complete board deck in minutes, not days
```

**Pattern 3: Learn → Apply → Deploy**
```
1. 🟢 @MENTOR   "Explain CTEs in SQL"        ← IDEATE: Learning
2. 🟢 @CHAT     "Draft a CTE for X"          ← IDEATE: Practice
3. 🟣 @DEV      "Test this CTE"              ← OPTIMIZE: Safe execution
4. 🟣 @PROD     "Deploy to production"       ← OPTIMIZE: Controlled rollout
```

## Why IFOC Matters

**Uderia's IFOC Architecture:**
- **Right phase for the task**: Match your intent to the appropriate mode
- **Composable intelligence**: Combine phases for compound expertise
- **Governed execution**: Clear boundaries for safety and compliance
- **Organizational scale**: Coordinate specialists like a real team

**The Bottom Line:**
Stop treating AI as a single assistant. The IFOC workflow mirrors how experts actually work: **Ideate** possibilities, **Focus** on verified knowledge, **Optimize** execution, and **Coordinate** complex multi-domain work. Build an AI organization where specialized experts collaborate intelligently.

## Strategic Planner Intelligence

The strategic planner understands profile class context and adapts behavior. It disambiguates SQL queries when switching between profile classes, prioritizing:
1. SQL mentioned in most recent llm_only conversation
2. SQL from most recent tool execution
3. Historical queries with explicit turn metadata

This prevents the planner from executing the wrong query when users switch from `@IDEAT` to `@OPTIM`.

## Profile Class Specifications

### Session Metadata Tracking

Every turn records its profile; when building planning context, the planner annotates each earlier turn with `turn_metadata`:

```json
{
  "turn": 3,
  "profile_id": "profile-uuid",
  "profile_tag": "CHAT",
  "profile_type": "llm_only",
  "turn_metadata": {
    "turn_number": 3,
    "profile_tag": "CHAT",
    "profile_type": "llm_only",
    "is_most_recent": true,
    "sql_mentioned_in_conversation": [
      "SELECT UserName FROM DBC.SessionsV WHERE SessionID <> 0"
    ]
  }
}
```

**Key Fields:**
- `profile_type` - "llm_only", "tool_enabled", "rag_focused", or "genie"
- `profile_tag` - Short identifier for quick switching
- `sql_mentioned_in_conversation` - Extracted SQL from llm_only responses
- `execution_trace` - Structured tool calls (only in tool_enabled)
- `knowledge_retrieval_event` - Document retrieval details (only in rag_focused)
- `genie_metadata` - Coordination details and child sessions (only in genie)

[Session record & the three histories →](../Architecture/SESSION_ARCHITECTURE.md)

### Profile Classification Modes

Profiles can be classified as:

**Light Classification:**
- Simple filter-based tool/prompt selection
- Fast, deterministic, no LLM call required
- Suitable for well-defined tool sets

**Full Classification (LLM-Assisted):**
- Dynamic categorization using LLM intelligence
- Adapts to ambiguous or complex tool selection
- Higher cost but more flexible

**Note:** Classification only applies to MCP-enabled profiles with multiple tools/prompts available.

### Session Primer - Automatic Context Initialization

**The Value:**
Session Primer allows each profile to automatically execute an initialization question when a new session starts, pre-populating the context window with domain-specific knowledge. This transforms generic AI agents into **instantly educated specialists**.

**Why This Matters:**
Instead of manually explaining your database schema, business rules, or domain terminology at the start of every conversation, the Session Primer does it automatically. The agent starts every session already understanding your context.

**Configuration:**
In profile settings, enable "Session Primer" and provide an initialization question:
- `"Describe the database schema and explain the business meaning of each table"`
- `"What KPIs are tracked in this system and how are they calculated?"`
- `"Educate yourself on the API endpoints and their authentication requirements"`

**Specialized Expert Teams**

Session Primer becomes transformational with Genie profiles. Build teams of pre-educated specialists:

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                    BUILDING AN AI EXPERT ORGANIZATION                          │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│   @ANALYST (Genie Coordinator)                                                 │
│   └─ Primer: "You coordinate business analysis. Understand the team below."   │
│                                                                                │
│       ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐     │
│       │  @KPI_EXPERT      │  │  @SCHEMA_EXPERT   │  │  @SQL_EXECUTOR    │     │
│       │                   │  │                   │  │                   │     │
│       │  Primer:          │  │  Primer:          │  │  Primer:          │     │
│       │  "Learn all KPI   │  │  "Learn the DB    │  │  "Learn the       │     │
│       │   definitions,    │  │   schema and the  │  │   available SQL   │     │
│       │   formulas, and   │  │   business context│  │   tools and       │     │
│       │   business        │  │   of each table   │  │   execution       │     │
│       │   thresholds"     │  │   and column"     │  │   patterns"       │     │
│       │                   │  │                   │  │                   │     │
│       │  → Knows: Revenue │  │  → Knows: Orders  │  │  → Knows: How to  │     │
│       │    targets, churn │  │    = transactions,│  │    write safe,    │     │
│       │    definitions,   │  │    Customers =    │  │    optimized      │     │
│       │    seasonality    │  │    B2B accounts   │  │    queries        │     │
│       └───────────────────┘  └───────────────────┘  └───────────────────┘     │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Single Question, Compound Intelligence:**

```
User: "@ANALYST, why did Q4 revenue drop?"

Execution Flow:
1. @KPI_EXPERT (already knows KPI definitions from primer)
   → "Revenue = sum(order_total) where status='completed'. Q4 target was $2M."

2. @SCHEMA_EXPERT (already knows table relationships from primer)
   → "Revenue data lives in orders table. Check order_status and created_at."

3. @SQL_EXECUTOR (already knows query patterns from primer)
   → Executes: SELECT month, SUM(order_total) FROM orders WHERE...
   → Returns: October $580K, November $420K, December $310K

4. @ANALYST synthesizes: "Q4 revenue was $1.31M vs $2M target (-34.5%).
   December showed steepest decline. Recommend investigating..."
```

**Without Session Primer:** Each expert starts blank. User must explain schemas, KPIs, and context repeatedly.

**With Session Primer:** Each expert is pre-educated. They collaborate immediately with full domain understanding.

**Best Practices:**
- **Efficiency Focused profiles**: Prime with schema descriptions, API documentation
- **Knowledge Focused profiles**: Prime with "summarize the key topics in the knowledge base"
- **Genie profiles**: Prime with team structure and delegation guidelines
- **Conversation profiles**: Prime with domain terminology and business rules

[Architecture →](../Architecture/SESSION_ARCHITECTURE.md)

## Real-World Usage Patterns

### Pattern 1: Learn, Then Execute

```
@CHAT: "How do I calculate the average sale price by region?"
  → Agent provides SQL template and explanation (Conversation Focused)

@OPTIM: "execute this query for the sales_data table"
  → Agent runs the query against live database (Efficiency Focused)
```

### Pattern 2: Review Before Production

```
@CHAT: "Write a query to delete inactive customers"
  → Agent drafts DELETE query for review (Conversation Focused)

[User reviews, approves]

@PROD: "execute this query"
  → Agent executes against production database with audit trail (Efficiency Focused)
```

### Pattern 3: Document-Driven Decisions

```
@RAG: "What are our approved customer retention strategies?"
  → Agent retrieves from strategy documents, synthesizes answer (Knowledge Focused)

@CHAT: "Help me design a retention campaign based on those strategies"
  → Agent provides implementation guidance (Conversation Focused)

@OPTIM: "Execute a query to identify at-risk customers for the campaign"
  → Agent runs the query against live database (Efficiency Focused)
```

### Pattern 4: Compliance and Policy Verification

```
@RAG: "What does our security policy say about API key rotation?"
  → Agent retrieves exact policy language with citations (Knowledge Focused)

@OPTIM: "Check which API keys in our system are older than 90 days"
  → Agent queries credential store via MCP tools (Efficiency Focused)
```

## Implementation Details

**Profile Switching:**
- Type `@` in chat input to see all available profiles
- Select with Tab/Enter or click
- Profile badge shows active override
- Session header displays both default (★) and override (⚡)

**Execution Context:**
- Conversation Focused (LLM): System prompt + conversation history
- MCP-Enabled Profiles: System prompt + conversation + tools + prompts + resources
- Knowledge Focused (RAG): RAG synthesis prompt + conversation + retrieved documents

**Cost Implications:** input size grows with what each class sends: conversation only (Ideate), plus retrieved documents (Focus), plus tool context (Ideate with tools, Optimize; Optimize adds the planner context). The actual tokens and cost of every turn are shown in Live Status and Awareness.

**Historical Tracking:**
- `profile_tags_used[]` - All profiles used in session
- `models_used[]` - All LLM models used in session
- `knowledge_retrieval_event` - Document sources and relevance scores (RAG profiles)
- Complete audit trail for cost attribution

## Best Practices

1. **Start Conversational:** Use Conversation Focused profiles to explore, learn, and draft queries
2. **Verify with Documents:** Use Knowledge Focused profiles for policy, compliance, and reference lookups
3. **Execute When Needed:** Switch to Efficiency Focused profiles only when live data operations are required
4. **Review Before Execution:** Draft destructive queries in `@CHAT`, review, then execute in `@OPTIM`
5. **Cost Attribution:** Use profile tags to track which workloads drive costs
6. **Security:** Restrict MCP-enabled profiles to authorized users via role-based access
7. **Knowledge Quality:** Ensure Knowledge Focused profiles have well-curated knowledge collections

The four IFOC classes are realized as modular per-class execution engines — see [**Engine Modularization Architecture →**](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md). For Genie coordinator architecture and nested multi-level coordination, see [**Coordinator Architecture →**](../Architecture/COORDINATOR_ARCHITECTURE.md).
