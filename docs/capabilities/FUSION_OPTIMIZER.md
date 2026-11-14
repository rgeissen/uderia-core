# 🎯 The Heart of the Application - The Engine & its Fusion Optimizer

The **Fusion Optimizer** is the execution engine behind the Optimize profile class: a multi-layered planner and executor built for resilient and efficient task execution against enterprise data sources. It plans before it acts, learns from what worked, and corrects itself when a step fails.

## 🧠 The Multi-Layered Planning Process

The Optimizer deconstructs every user request into a hierarchical plan.

1. **Strategic Planner**: For any non-trivial request, the agent first generates a high-level **meta-plan**. This strategic blueprint outlines the major phases required to fulfill the user's goal, such as "Phase 1: Gather table metadata" followed by "Phase 2: Analyze column statistics."

2. **Tactical Execution**: Within each phase, the agent operates tactically, determining the single best next action (a tool or prompt call) to advance the plan.

3. **Recursive Delegation**: The Planner is fully recursive. A single phase in a high-level plan can delegate its execution to a new, subordinate instance of the Planner. This allows the agent to solve complex problems by breaking them down into smaller, self-contained sub-tasks, executing them, and then returning the results to the parent process.

### 💎 Dual-Model Architecture for Cost Optimization

The Fusion Optimizer supports **heterogeneous model assignment** across planning layers, so cost and capability can be traded off per layer:

* **Strategic Model**: More capable model for high-level reasoning
  - Handles complex meta-planning and multi-phase orchestration
  - Examples: a frontier model such as Claude Opus, GPT or a Gemini Pro model
  - Runs once per query (low call frequency)
  - Investment justified by quality of strategic decisions

* **Tactical Model**: Faster, cost-efficient model for execution
  - Handles tool selection and argument generation
  - Examples: a small fast model such as Claude Haiku, a GPT mini model or Gemini Flash-Lite
  - Runs multiple times per phase (high call frequency)
  - Moves the most frequent calls to the cheaper model

* **Real-Time Cost Visibility**: Live Status panel displays color-coded cost breakdown
  - **Strategic cost** (blue): Planning and orchestration overhead
  - **Tactical cost** (green): Per-phase execution costs
  - Enables data-driven model selection and optimization

**Example Configuration:**
```
Strategic: a frontier model (e.g. Claude Opus)
Tactical:  a small fast model (e.g. Claude Haiku)
Result:    most tactical spend moves to the cheap model
```

This architecture is particularly effective for:
- High-volume production workloads where tactical calls dominate
- Iterative refinement queries with multiple tactical cycles
- Multi-turn sessions with shared strategic context
- Budget-conscious deployments requiring predictable costs

## 🔧 Proactive Optimization Engine

Before and during execution, the Optimizer actively seeks to enhance performance and efficiency.

* **Plan Hydration**: The agent intelligently inspects a new plan to see if its initial steps require data that was already generated in the *immediately preceding turn*. If so, it "hydrates" the new plan by injecting the previous results, skipping redundant tool calls and delivering answers faster. This is particularly effective for follow-up clarifications and iterative refinements.

* **Tactical Fast Path**: For simple, single-tool phases where all required arguments are known, the Optimizer bypasses the tactical LLM call entirely and executes the tool directly, which removes a model call and its latency from every such step.

* **Specialized Orchestrators**: The agent is equipped with programmatic orchestrators to handle common complex patterns. For example, it can recognize a date range query (e.g., "last week") and automatically execute a single-day tool iteratively for each day in the range — with independent days running in parallel. Additional orchestrators handle column-iteration patterns and hallucinated-loop recovery.

* **Context Distillation**: To prevent context window overflow with large datasets, the agent automatically distills large tool outputs into concise metadata summaries before passing them to the LLM for planning, ensuring robust performance even with enterprise-scale data.

## 📚 Continuous Improvement through Champion Case Learning

The agent learns from every successful interaction, building an ever-growing repository of "champion" strategies that guide future planning. This closed-loop learning system transforms individual successes into organizational knowledge.

* **Automatic Case Capture**: Each successful Optimize turn whose plan acquired data is stored as a champion case:
  - The question and the successful phases, with their tools and arguments
  - Token counts, provider and model
  - Whether the turn needed self-correction
  - User feedback, when given

* **Champion Strategy Selection**: For each question, the champion is the successful case with the best user feedback, then the fewest output tokens. A downvoted case is never served.

* **Few-Shot Learning Injection**: Planning-time retrieval enhances strategic decisions:
  - The planner retrieves analogous champion cases from the Planner Repository by semantic similarity
  - Top-K similar cases injected into strategic planner context
  - LLM leverages past successes to guide current planning
  - Continuous improvement without model retraining
  - Per-user savings attribution for efficiency tracking
  - Reads the repositories the agent binds plus its own learning repository; an agent with its own learning repository never reads another agent's lessons (an older agent without one shares the user's default planner repository)
  - A question that matches a signed or locked seed word for word replays that plan verbatim at zero planner tokens

* **Off the critical path**: Case archiving runs on a background queue after the answer is delivered; champion retrieval adds one vector search before planning. If the learning system is unavailable, the turn plans without champions.

## 📊 Performance Metrics and Resource Limits

The engine provides comprehensive observability and built-in safeguards against runaway execution.

**Real-Time Performance Tracking:**

* **Token Consumption Monitoring**: Per-turn and cumulative tracking:
  - Input tokens (prompt + context + few-shot examples)
  - Output tokens (strategic plan + tactical steps + tool arguments + final response)
  - Token-to-cost mapping with provider-specific pricing
  - Historical trends

* **Execution Time Profiling**: Detailed timing breakdown:
  - Strategic planning latency
  - Tactical loop execution time per iteration
  - Tool invocation duration (network + processing)
  - Response generation time

**Built-in Safeguards:**

* **Bounded Execution**: Per-phase retries are capped (default 5 attempts) and each tool call gets up to 3 self-correction attempts. A phase that still fails is recorded as failed, the phases that depend on it are skipped, and the final report states what is missing.
* **Context Window Management**: Budget-aware five-pass assembly with automatic condensation when approaching model limits ([architecture details](../Architecture/CONTEXT_WINDOW_ARCHITECTURE.md))
* **Timeouts and budgets**: Each MCP tool call times out after 120 seconds (`TDA_MCP_TOOL_TIMEOUT`), and an agent can carry an optional per-turn budget of steps, tokens and time (the Visible Budget).

## 🔄 Autonomous Self-Correction & Healing

When errors occur, the Optimizer runs a multi-tiered recovery process.

1. **Pattern-Based Correction**: The agent first checks for known, recoverable errors (e.g., "table not found," "column not found").

2. **Targeted Recovery Prompts**: For these specific errors, it uses highly targeted, specialized prompts that provide the LLM with the exact context of the failure and guide it toward a precise correction (e.g., "You tried to query table 'X', which does not exist. Here is a list of similar tables...").

3. **Generic Correction**: A novel error goes to a generic correction prompt. A phase that keeps failing ends as failed, and the report says so honestly rather than inventing a result.

The champion case learning system works before this point, not during it: proven strategies are retrieved before planning, so a good plan is chosen up front.

## 🛡️ Robust Safeguards

The Optimizer carries several safeguards that bound what a run can do.

* **Plan Refinement Pipeline**: 19 deterministic rewrite passes repair the meta-plan before any tool runs — placeholder or invented-database SQL is deferred, text dates are cast, loops are wired, results are reused for charts, and a final report is guaranteed. Each pass that changes the plan emits a `plan_rewrite_diff` event.

* **Preflight SQL**: every statement is checked against the ontology (join key, fan-out, cross-domain, text dates, values, columns) and rewritten without an LLM where possible — [Preflight SQL →](../Architecture/PREFLIGHT_SQL_ARCHITECTURE.md)

* **Deterministic Plan Validation**: Before execution begins, the agent deterministically validates the LLM-generated meta-plan for common structural errors (e.g., misclassifying a prompt as a tool) and corrects them, preventing entire classes of failures proactively.

* **Hallucination Prevention**: Specialized orchestrators detect and correct "hallucinated loops," where the LLM incorrectly plans to iterate over a list of strings instead of a valid data source. The agent semantically understands the intent and executes a deterministic, correct loop instead.

* **Definitive Error Handling**: The agent recognizes unrecoverable errors (e.g., database permission denied) and halts execution immediately, providing a clear explanation to the user instead of wasting resources on futile retry attempts.

For comprehensive details on the Fusion Optimizer — including its five active subsystems (Deterministic Plan Validation, Proactive Re-planning, Hallucination Prevention, Intelligent Error Correction and Context Distillation), the full optimization pipeline, observability events, and configuration reference — see:
[**Fusion Optimizer Architecture (docs/Architecture/FUSION_OPTIMIZER_ARCHITECTURE.md)**](../Architecture/FUSION_OPTIMIZER_ARCHITECTURE.md) · [**Engine Modularization →**](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md)
