"""The Optimize campaign — cases with oracles, the lane invariants O1–O10, and waste.

Pure data + pure checks: no I/O, no server. `run_campaign.py` drives it; `coverage.py`
audits it against `registry.py`; `test_optimize_campaign_suite.py` is its CI gate.

A case asks ONE question of the fitness_db demo data and carries:
    oracle_sql   the database's own answer, run by the campaign before the turn (oracle.py)
    extract      number · list · set · rows — what the answer must contain
    tolerance    relative tolerance for a number
    targets      registry keys (passes · safeguards · orchestrators · fastpath) the case
                 exists to provoke — the coverage contract
    levers       which campaign agents it runs on (ALL by default)
    expect       honest bands and trace assertions
    turns        optional: a multi-turn session (each turn its own prompt/oracle)
    waste        optional: a detector for waste that LANDED — the efficiency bait

Correctness is the oracle's, never a human's. A case never asserts how the model phrased
anything; it asserts the figure, the trace, and the price.
"""
from __future__ import annotations

import re

LEVERS = ("OCFL", "OCFL-NORAG", "OCFL-ALL", "OCWEAK")
# A LANE lever answers the same questions on another engine, same model, MCP entry, posture and
# primer as @OCFL — the cross-lane comparison (2026-10-29). It is not an Optimize lever: the
# Optimize machinery (registry, coverage, plan invariants, the family token bands) does not apply.
LANES = {"OCREACT": "conversation_with_tools"}
ALL_LEVERS = LEVERS + tuple(LANES)
ALL = frozenset(ALL_LEVERS)
RAG = frozenset({"OCFL", "OCWEAK"})                 # the agents that bind the champion repository

# ── the coverage contract ─────────────────────────────────────────────────────
# A registry entry no case can honestly provoke on this campaign, with WHAT would make it
# provokable. `coverage.py --strict` fails on an entry neither targeted nor declared.
UNPROVOKABLE = {
    "cross_source_shape": "needs two MCP servers on one agent (PROP-006 multi-source); the campaign binds one — phase 2",
    "governed_execution": "needs OBDA-READ on a campaign KG with a data product; no KG bound — phase 2",
    "compute_insertion": "needs the TDA_Compute combine step (multi-source) — phase 2 with cross_source_shape",
    "text_date_cast": "needs a text-stored date column whose shape preflight has learned; fitness_db stores real DATE/TIMESTAMP columns",
    "context_report": "fires only on an EMPTY knowledge context on a knowledge-bearing plan; the campaign agents bind no knowledge repository (phase 2: a Focus-style case)",
    "llm_filter_deloop": "fires on a plan that loops TDA_LLMFilter; provoked only with a planner that emits such loops — tracked by the weak-model gap, not a prompt",
    "llm_task_optimization": "same: a TDA_LLMTask loop the planner emitted — a weak-model gap candidate, not a prompt",
    "multi_loop_synthesis": "same: multiple loop phases needing a synthesis phase — a weak-model gap candidate",
    "loop_classification": "the LLM pass behind llm_task_optimization — same declaration",
    "sql_consolidation_llm": "conditional on ENABLE_SQL_CONSOLIDATION_REWRITE and an LLM call; fires with sql_consolidation when enabled — read as fired-with",
    "orchestrator:hallucinated_loop": "fires when the LLM invents a loop the plan did not have — a weak-model gap candidate, recorded when seen",
    "scope_dispatcher": "the multi-tool phase dispatcher; provoked by a phase with several relevant tools — recorded when seen, no deterministic prompt",
    "context_distillation": "fires on a tool result > 500 rows; fitness_db's largest table is smaller — phase 2: a wide SaleDetails scan",
    "argument_enrichment": "fires when a tool's required arguments are missing after planning and the catalog can fill them — a weak-model gap candidate",
    "argument_refinement": "fires when a tool's required arguments are missing after planning (the paid refinement call); provoked only by a planner that drops them — a weak-model gap candidate, recorded when seen",
}


def _c(id, family, prompt, oracle_sql=None, extract="number", tolerance=0.0, targets=(),
       levers=ALL, expect=None, note="", turns=None, waste=None):
    return dict(id=id, family=family, prompt=prompt, oracle_sql=oracle_sql, extract=extract,
                tolerance=tolerance, targets=tuple(targets), levers=frozenset(levers),
                expect=expect or {}, note=note, turns=turns, waste=waste)


# The seeded champion (agents.py seeds the campaign planner repository with exactly this
# question/SQL pair): E2 asks it word for word, E2b asks it in other words.
CHAMPION = {"user_query": "how many customers do we have?",
            "sql_statement": "SELECT COUNT(*) AS customer_count FROM fitness_db.Customers"}

CASES = [
    # ── A · ACCURACY (every answer checked against the data) ─────────────────
    _c("A1", "A", "How many customers do we have?",
       "SELECT COUNT(*) FROM fitness_db.Customers", "number",
       targets=("fastpath", "final_report_guarantee", "placeholder_sql", "conversational_replan"),
       note="conversational_replan: MiMo answered this with the prompt's greeting 1 in 5 (OC-F13).",
       expect=dict(max_llm_calls=3, max_self_corrections=0)),
    _c("A2", "A", "What are the top 5 products by revenue?",
       "SELECT TOP 5 p.ProductName FROM fitness_db.SaleDetails sd JOIN fitness_db.Products p ON p.ProductID = sd.ProductID "
       "GROUP BY p.ProductName ORDER BY SUM(sd.Quantity * sd.UnitPrice) DESC", "list",
       targets=("plan_validation", "database_before_sql"), expect=dict(max_self_corrections=1)),
    # ties: several customers / states / brands share the top count in fitness_db, so a TOP 1
    # by name is not deterministic — the campaign asks for the COUNT at the top, which is
    _c("A3", "A", "What is the highest number of service tickets any single customer has?",
       "SELECT MAX(n) FROM (SELECT CustomerID, COUNT(*) AS n FROM fitness_db.ServiceTickets GROUP BY CustomerID) t", "number",
       expect=dict(max_self_corrections=1)),
    _c("A4", "A", "Which 5 customers generated the most revenue? Give their last names.",
       "SELECT TOP 5 c.LastName FROM fitness_db.Sales s JOIN fitness_db.Customers c ON c.CustomerID = s.CustomerID "
       "GROUP BY c.LastName ORDER BY SUM(s.TotalAmount) DESC", "set",
       expect=dict(max_self_corrections=1)),
    _c("A5", "A", "How many service tickets does the customer state with the most tickets have?",
       "SELECT MAX(n) FROM (SELECT c.State, COUNT(*) AS n FROM fitness_db.ServiceTickets t "
       "JOIN fitness_db.Customers c ON c.CustomerID = t.CustomerID GROUP BY c.State) x", "number"),
    _c("A6", "A", "What is the average sale amount across all sales?",
       "SELECT AVG(TotalAmount) FROM fitness_db.Sales", "number", tolerance=0.01),
    _c("A7", "A", "How many units did the best-selling product brand sell in total?",
       "SELECT MAX(q) FROM (SELECT p.Brand, SUM(sd.Quantity) AS q FROM fitness_db.SaleDetails sd "
       "JOIN fitness_db.Products p ON p.ProductID = sd.ProductID GROUP BY p.Brand) x", "number"),
    _c("A8", "A", "How many service tickets are still unresolved?",
       "SELECT COUNT(*) FROM fitness_db.ServiceTickets WHERE ResolutionDate IS NULL", "number",
       targets=("database_before_sql",)),
    _c("A9", "A", "How many products have fewer than 10 units in stock?",
       "SELECT COUNT(*) FROM fitness_db.Products WHERE StockQuantity < 10", "number"),
    _c("A10", "A", "What is the total revenue across all sales?",
       "SELECT SUM(TotalAmount) FROM fitness_db.Sales", "number", tolerance=0.001),
    _c("A11", "A", "How many distinct product types do we sell?",
       "SELECT COUNT(DISTINCT ProductType) FROM fitness_db.Products", "number"),
    _c("A12", "A", "How many customers registered in 2022?",
       "SELECT COUNT(*) FROM fitness_db.Customers WHERE EXTRACT(YEAR FROM RegistrationDate) = 2022", "number",
       targets=("temporal_data_flow",)),

    # ── E · EFFICIENCY (the machinery that makes a weak model cheap) ──────────
    _c("E1", "E", "How many rows are in the table fitness_db.Customers?",
       "SELECT COUNT(*) FROM fitness_db.Customers", "number",
       targets=("fastpath",),
       note="Every argument is in the question: the data phase must run FASTPATH — a tactical "
            "LLM call here is ~2K tokens of waste.",
       expect=dict(max_llm_calls=2, fired_any=("fastpath",)),
       waste="waste_tactical_when_known"),
    _c("E2", "E", CHAMPION["user_query"],
       CHAMPION["sql_statement"], "number", levers=RAG,
       note="Word for word the seeded champion: the planner repository must return it "
            "(rag_case_count ≥ 1) and the plan must reuse its SQL — the learning loop, proven.",
       expect=dict(min_rag_cases=1, max_llm_calls=2), waste="waste_champion_missed"),
    _c("E2b", "E", "What is the total number of customers in the system?",
       CHAMPION["sql_statement"], "number", levers=RAG,
       note="The seeded champion in other words: semantic retrieval must still find it.",
       expect=dict(min_rag_cases=1), waste="waste_champion_missed"),
    _c("E2c", "E", "Count the customers for me.",
       CHAMPION["sql_statement"], "number", levers=frozenset({"OCFL-NORAG", "OCWEAK"}),
       targets=("placeholder_sql",),
       note="The cold lane's commonest failure: a plan that searches for the table and then "
            "writes SQL against a placeholder for what it found. The placeholder pass must "
            "defer the statement to the tactical step (0 LLM for the rewrite) instead of "
            "running it into a syntax error and a 19K-token self-correction.",
       expect=dict(max_self_corrections=1)),
    _c("E3", "E", "What is today's date according to the database?",
       None, "number", targets=("fastpath", "conversational_replan", "strategic_parse_retry"),
       note="A one-tool question: the governed surface should offer few tools and the phase "
            "should FASTPATH; the price of this turn is the lane's floor. MiMo answered it "
            "with the prompt's greeting 2 in 5 (OC-F13) — the re-plan's live case — and once with a "
            "two-character plan, '[{' (OC-F16) — the parse retry's.",
       expect=dict(max_llm_calls=2)),
    _c("E4", "E", "Hello, how are you today?",
       None, "number",
       note="The control for OC-F13: genuine small talk keeps the conversational shortcut — "
            "one planning call, no plan, no re-plan.",
       expect=dict(conversational=True, max_llm_calls=1, not_fired=("conversational_replan",))),

    # ── R · ROBUSTNESS (the hardening the catalogue promises) ────────────────
    _c("R1", "R", "How many rows are in fitness_db.Custmers?",
       "SELECT COUNT(*) FROM fitness_db.Customers", "number",
       targets=("self_correction",),
       note="A misspelled table: one self-correction should land the right table; a second "
            "is the tier ladder failing cheaply.",
       expect=dict(max_self_corrections=2)),
    _c("R2", "R", "What is the average age of our customers?",
       None, "number",
       note="No such column anywhere. The honest answer says so; a figure is a fabrication "
            "(numeric_fidelity / honesty).",
       expect=dict(honest_absence=True)),
    _c("R3", "R", "What is the total revenue per customer? Join Sales to SaleDetails and sum the amounts; show the last names of the 3 customers with the highest revenue.",
       "SELECT TOP 3 c.LastName FROM fitness_db.Sales s JOIN fitness_db.Customers c ON c.CustomerID = s.CustomerID "
       "GROUP BY c.LastName ORDER BY SUM(s.TotalAmount) DESC", "set",
       targets=("sql_consolidation", "schema_before_sql"),
       note="FAN-OUT BAIT: joining Sales to SaleDetails multiplies TotalAmount by the line "
            "count. The right answer sums once; the oracle is the un-multiplied ranking. "
            "Reworded 2026-10-29: 'the top 3 customers by last name' also reads as 'ordered by "
            "last name', and MiMo answered that reading (`20261029-074316`).",
       expect=dict(max_self_corrections=2)),
    _c("R4", "R", "How many sales happened in the past 30 days?",
       "SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate >= CURRENT_TIMESTAMP - INTERVAL '30' DAY", "number",
       targets=("temporal_data_flow", "date_range_wiring", "orchestrator:date_range", "temporal_preprocessing", "placeholder_sql"),
       note="A relative time window: TDA_CurrentDate must be wired into the data phase or the "
            "date-range orchestrator must run; a hard-coded date is the failure.",
       expect=dict(max_self_corrections=2)),
    _c("R5", "R", "Show me summary statistics for every numeric column of fitness_db.Products.",
       None, "number", targets=("orchestrator:column_iteration", "fastpath_data_expansion", "fastpath_loop"),
       note="Per-column analysis: the column-iteration orchestrator or the FASTPATH data "
            "expansion should iterate the columns — never one LLM call per column.",
       expect=dict(max_llm_calls=6)),
    _c("R6", "R", "Use base_rowCount to count the rows in fitness_db.Customers.",
       "SELECT COUNT(*) FROM fitness_db.Customers", "number",
       targets=("plan_validation", "self_correction"),
       note="A tool that does not exist in this server's catalog, named by the user. The plan "
            "validation pass should repair it deterministically (0 tokens); a self-correction "
            "LLM call is the paid fallback (16K tokens on the smoke turn, 2026-10-27).",
       expect=dict(max_self_corrections=1), waste="waste_paid_for_unknown_tool"),
    # "…and name the top one": the chart carries the five, the case reads the prose — a weak model
    # that charted the right rows and named none read as wrong (run 6, a case defect)
    _c("R7", "R", "Chart the top 5 products by revenue as a bar chart, and name the top product.",
       # the chart carries the five; the prose names the leader — the oracle checks the leader
       "SELECT TOP 1 p.ProductName FROM fitness_db.SaleDetails sd JOIN fitness_db.Products p ON p.ProductID = sd.ProductID "
       "GROUP BY p.ProductName ORDER BY SUM(sd.Quantity * sd.UnitPrice) DESC", "set",
       targets=("charting_cleanup", "chart_data_reuse"),
       note="A chart: the charting passes must not re-fetch the data for the chart phase.",
       expect=dict(max_self_corrections=1)),

    # ── M · MULTI-TURN (plan hydration) ──────────────────────────────────────
    _c("M1", "M", "How many customers do we have, and how many service tickets are still unresolved?",
       None, "number", targets=("plan_hydration", "plan_hydration_injected", "system_correction"),
       note="Turn 1 fetches two counts; turn 2 asks a question answerable from turn 1's data. "
            "The hydration PASS covers only a plan that opens with a loop over a prior-turn "
            "reference (OC-F6) — so the case asserts the ORACLE on turn 2 and records whether a "
            "data tool ran again (`tool_calls`) and whether the pass fired, without demanding "
            "either; a follow-up computed from the previous answer with no re-fetch is the "
            "proposal PASS-4.",
       turns=[
           dict(prompt="How many customers do we have, and how many service tickets are still unresolved?",
                oracle_sql="SELECT (SELECT COUNT(*) FROM fitness_db.Customers) AS customers, "
                           "(SELECT COUNT(*) FROM fitness_db.ServiceTickets WHERE ResolutionDate IS NULL) AS unresolved", extract="number"),
           dict(prompt="What is the difference between those two counts?",
                oracle_sql="SELECT ABS((SELECT COUNT(*) FROM fitness_db.Customers) - (SELECT COUNT(*) FROM fitness_db.ServiceTickets WHERE ResolutionDate IS NULL))",
                extract="number", expect=dict()),
       ]),
    _c("M2", "M", "How many sales do we have, and what is the total revenue?",
       None, "number", targets=("plan_hydration",),
       note="PASS-4 evidence: turn 2's answer is a division of turn 1's two figures — no new "
            "data is needed. Records whether the lane re-queries (tool_calls) or computes.",
       turns=[
           dict(prompt="How many sales do we have, and what is the total revenue?",
                oracle_sql="SELECT SUM(TotalAmount) FROM fitness_db.Sales", extract="number", tolerance=0.001),
           dict(prompt="So what is the average revenue per sale?",
                oracle_sql="SELECT AVG(TotalAmount) FROM fitness_db.Sales", extract="number", tolerance=0.01,
                expect=dict()),
       ]),
    _c("M3", "M", "How many products have fewer than 10 units in stock, and how many have 10 or more?",
       None, "number", targets=("plan_hydration",),
       note="Follow-up computed from turn 1 (OC-F9): turn 2 is a ratio of turn 1's two counts "
            "(97 / 3 = 32.33), a figure turn 1 never shows.",
       turns=[
           dict(prompt="How many products have fewer than 10 units in stock, and how many have 10 or more?",
                oracle_sql="SELECT COUNT(*) FROM fitness_db.Products WHERE StockQuantity >= 10", extract="number"),
           # worded for ONE numeric form: "the ratio" was answered "97:3" (right, unreduced) and
           # read as wrong — a case defect (run 4), not the lane's
           dict(prompt="How many times more well-stocked products are there than low-stock ones? Give one decimal number.",
                oracle_sql="SELECT CAST((SELECT COUNT(*) FROM fitness_db.Products WHERE StockQuantity >= 10) AS DECIMAL(12,4)) "
                           "/ (SELECT COUNT(*) FROM fitness_db.Products WHERE StockQuantity < 10)",
                extract="number", tolerance=0.01, expect=dict()),
       ]),
]


def cases_for(lever: str) -> list:
    return [c for c in CASES if lever in c["levers"]]


def targets_for(lever: str) -> frozenset:
    return frozenset(t for c in cases_for(lever) for t in c["targets"])


# ── reading a turn ────────────────────────────────────────────────────────────
def answer_text(turn: dict) -> str:
    t = turn or {}
    return str(t.get("final_summary_text") or t.get("final_summary") or "")


def self_corrections(turn: dict) -> int:
    """Paid self-corrections: the LLM call entries that carry tokens (the trace logs the
    intent line and the priced line for the same call)."""
    from registry import _systemlog_entries
    return sum(1 for m, d in _systemlog_entries(turn)
               if m == "Calling LLM for Self-Correction" and isinstance(d, dict) and "input_tokens" in d)


def tactical_calls(turn: dict) -> int:
    from registry import llm_calls
    return sum(1 for m, *_ in llm_calls(turn) if m == "Calling LLM for Tactical Action")


HONEST = re.compile(r"\b(no (?:such|age|column|data)|not (?:available|stored|recorded|a column)|does not contain|"
                    r"doesn't contain|necessary .{0,30} information|"
                    r"does not (?:exist|contain|have|store)|doesn't (?:exist|have)|cannot determine|"
                    r"can(?:no|')t (?:determine|compute|calculate|find)|not possible|unable to)\b", re.I)


# ── waste detectors (the efficiency bait) ─────────────────────────────────────
def waste_tactical_when_known(turn: dict):
    """A tactical LLM call on a plan whose first data phase named ONE tool with non-empty
    arguments — FASTPATH applied and was not taken. (O3 exempts turns with a tool error:
    the tactical call is then the recovery ladder.)"""
    plan = (turn or {}).get("original_plan") or []
    first = next((p for p in plan if (p.get("relevant_tools") or [None])[0] not in (None, "TDA_FinalReport")), None)
    if not first:
        return None
    known = len(first.get("relevant_tools") or []) == 1 and bool(first.get("arguments"))
    return bool(known and tactical_calls(turn) > 0)


def waste_champion_missed(turn: dict):
    n = (turn or {}).get("rag_case_count")
    return None if n is None else int(n) == 0


def waste_paid_for_unknown_tool(turn: dict):
    """An LLM self-correction paid for a tool the catalog never had."""
    from registry import _systemlog_entries, tool_calls
    unknown = any("Unknown tool" in str(args) for _, args, ok in tool_calls(turn) if not ok) or \
        any("Unknown tool" in str(d) for m, d in _systemlog_entries(turn) if m == "System Self-Correction")
    return bool(unknown and self_corrections(turn) > 0)


WASTE = {"waste_tactical_when_known": waste_tactical_when_known,
         "waste_champion_missed": waste_champion_missed,
         "waste_paid_for_unknown_tool": waste_paid_for_unknown_tool}


# ── the lane invariants O1–O10 ────────────────────────────────────────────────
def is_react(turn: dict) -> bool:
    """A ReAct-lane turn: it records `conversation_agent_events`, never an Optimize plan."""
    t = turn or {}
    return bool(t.get("conversation_agent_events")) and not t.get("original_plan")


def check_invariants(turn: dict, case: dict, correct, band: dict | None = None) -> list:
    """Every invariant on one persisted turn. `correct` is the oracle's verdict (True/False/
    None). `band` is the family's token band from the baseline (None = not yet known)."""
    from registry import fired, llm_calls, tool_calls, REGISTRY
    out = []

    def add(iid, ok, detail=""):
        out.append({"id": iid, "ok": bool(ok), "detail": detail})

    t = turn or {}
    calls = llm_calls(t)
    tools = tool_calls(t)
    trace = t.get("execution_trace") or []
    finals = sum(1 for e in trace if (e.get("action") or {}).get("tool_name") == "TDA_FinalReport")
    ans = answer_text(t)

    # O1 — whole: a plan, a trace, TDA_FinalReport once, cost sealed
    if is_react(t):
        # the ReAct lane has no plan and no TDA_FinalReport: whole = an answer, cost sealed
        add("O1", bool(ans.strip()) and t.get("turn_cost") is not None,
            f"ReAct lane · answer={bool(ans.strip())} cost={t.get('turn_cost')}")
    elif (case.get("expect") or {}).get("conversational"):
        # small talk (E4): the planner's conversational shortcut — no plan by design
        add("O1", bool(ans.strip()) and t.get("turn_cost") is not None,
            f"conversational · answer={bool(ans.strip())} cost={t.get('turn_cost')}")
    else:
        add("O1", bool(t.get("original_plan")) and bool(trace) and finals == 1 and t.get("turn_cost") is not None,
            f"plan={bool(t.get('original_plan'))} trace={len(trace)} final_report×{finals} cost={t.get('turn_cost')}")

    # O2 — no runaway correction, no identical SQL twice
    sqls = [str((a or {}).get("sql") or "").strip().lower() for _, a, _ in tools]
    dup = sorted({s for s in sqls if s and sqls.count(s) > 1})
    sc = self_corrections(t)
    add("O2", sc <= 3 and not dup, f"self-corrections={sc} · duplicate SQL={dup or 'none'}")

    # O3 — FASTPATH where the plan knew the tool and its arguments. A tactical call AFTER a
    # tool error is the recovery ladder, not waste: the rule applies to turns with no tool error.
    w = None if is_react(t) else waste_tactical_when_known(t)
    errored_tools = any(not ok for *_, ok in tools)
    add("O3", w is not True or errored_tools,
        "tactical LLM call on a phase whose tool and args were pre-known" if (w and not errored_tools) else
        ("no pre-known phase" if w is None else ("tactical call after a tool error (recovery)" if w else "fastpath honoured")))

    # O4 — a seeded champion was retrieved (RAG agents only; asserted by the E cases' expect)
    # O5 — every figure in the answer appears in a tool result (numeric fidelity, scored)
    dims = {d.get("k"): d.get("b") for d in (t.get("eval") or {}).get("dimensions") or [] if isinstance(d, dict)}
    nf = dims.get("numeric_fidelity")
    # a figure the oracle contradicts AND the scorer flags is the embroidered number; a declined
    # answer (oracle None) is judged by O10 and the case, not here
    add("O5", not (nf == "danger" and correct is False), f"numeric_fidelity={nf} · oracle={correct}")

    # O6 — price honesty: turn_cost == Σ per-call cost; tokens == Σ per-call tokens
    sum_cost = round(sum(c for *_, c in calls), 6)
    sum_in = sum(i for _, i, _, _ in calls)
    tc = float(t.get("turn_cost") or 0)
    ti = int(t.get("turn_input_tokens") or 0)
    add("O6", (not calls) or (abs(tc - sum_cost) <= max(0.0005, 0.02 * tc) and abs(ti - sum_in) <= max(50, 0.02 * ti)),
        f"turn_cost={tc:.6f} Σcalls={sum_cost:.6f} · turn_in={ti} Σcalls={sum_in} · {len(calls)} calls")

    # O7 — tokens within the family's band (baseline-derived; absent = not yet known)
    if is_react(t):
        add("O7", True, f"n/a on the ReAct lane (the bands are the Optimize lane's) · input {ti}")
    elif band and band.get("max_input_tokens"):
        add("O7", ti <= band["max_input_tokens"], f"input {ti} vs band {band['max_input_tokens']}")
    else:
        add("O7", True, "no band yet (first accepted run sets it)")

    # O8 — every pass that fired is one the registry knows
    fp = fired(t)
    add("O8", fp <= set(REGISTRY), f"fired={sorted(fp)}")

    # O9 — an orchestrator-shaped question did not fall to a hand-rolled loop of LLM calls
    loops = sum(1 for m, *_ in calls if m == "Calling LLM for Tactical Action")
    add("O9", loops <= 4, f"tactical calls={loops}")

    # O10 — the oracle agrees, OR the answer honestly says it could not — never a confident
    # wrong figure
    if correct is True:
        add("O10", True, "oracle agrees")
    elif correct is False:
        add("O10", False, "WRONG FIGURE delivered as an answer")
    else:
        add("O10", True, "no comparable figure (judged by the case's honesty rule)" if not case.get("oracle_sql")
            else "answer carries no figure — an honest absence or a non-answer")
    return out


def check_expectations(case: dict, turn: dict, correct, rag_cases) -> list:
    out = []

    def add(what, ok, detail=""):
        out.append({"id": what, "ok": bool(ok), "detail": detail})

    from registry import fired, llm_calls, tool_calls
    exp = case.get("expect") or {}
    t = turn or {}
    if is_react(t):
        # The ReAct lane is held to what both lanes promise — the oracle and an honest absence;
        # the call/correction ceilings, the passes and the champion count are the Optimize lane's
        exp = {k: v for k, v in exp.items() if k in ("honest_absence", "max_tool_calls")}
    if case.get("oracle_sql"):
        add("oracle agrees", correct is True, f"correct={correct}")
    if "max_llm_calls" in exp:
        n = len(llm_calls(t))
        add(f"llm calls ≤ {exp['max_llm_calls']}", n <= exp["max_llm_calls"], f"{n} calls")
    if "max_self_corrections" in exp:
        n = self_corrections(t)
        add(f"self-corrections ≤ {exp['max_self_corrections']}", n <= exp["max_self_corrections"], f"{n}")
    if "max_tool_calls" in exp:
        n = len(tool_calls(t))
        add(f"tool calls ≤ {exp['max_tool_calls']}", n <= exp["max_tool_calls"], f"{n}")
    if "min_rag_cases" in exp:
        n = rag_cases if rag_cases is not None else (t.get("rag_case_count") or 0)
        add(f"champion retrieved (≥ {exp['min_rag_cases']})", int(n or 0) >= exp["min_rag_cases"], f"rag_case_count={n}")
    if "fired_any" in exp:
        f = fired(t)
        hit = [k for k in exp["fired_any"] if k in f]
        add(f"fired any of {list(exp['fired_any'])}", bool(hit), f"fired={sorted(f)}")
    if "not_fired" in exp:
        f = fired(t)
        hit = [k for k in exp["not_fired"] if k in f]
        add(f"fired none of {list(exp['not_fired'])}", not hit, f"fired={sorted(f)}")
    if exp.get("conversational"):
        add("answered without a plan (small talk)", not t.get("original_plan") and bool(answer_text(t).strip()),
            f"plan={bool(t.get('original_plan'))}")
    if exp.get("honest_absence"):
        # the budget footer's figures and the question's own are not the answer's (oracle.scorable_text)
        from oracle import numbers_in, scorable_text
        ans = scorable_text(answer_text(t), case.get("prompt", "")).replace("fitness_db", "")
        add("honest absence (no figure, says so)", bool(HONEST.search(ans)) and not numbers_in(ans),
            ans[:120].replace("\n", " "))
    return out
