#!/usr/bin/env python3
"""The governed lane must survive the plan-rewrite pipeline.

Two passes, one property: a plan that consumes a signed data product must still
COMPILE it and EXECUTE what it compiled after every rewrite has run.

WHY THIS GATE EXISTS
Found live, in a guided walkthrough of the OBDA-READ surface. The model planned the
governed lane CORRECTLY:

    phase 2: TDA_KnowledgeGraph query_product(support_load, …)   -> compiles SQL
    phase 4: base_readQuery  sql = {source: result_of_phase_2, key: "sql"}

`query_product` compiles governed, signed, deterministic SQL; `base_readQuery`
executes exactly what it returned. That is the whole OBDA-READ seam.

The consolidation pass then collected `args["sql"]` from both phases as if it
were query TEXT — but it was a placeholder dict. With nothing to consolidate,
the consolidation LLM invented SQL from the user's goal:

    SELECT s.state … FROM states s LEFT JOIN customers c … LEFT JOIN support_tickets t

None of those tables exist. The turn executed hallucinated SQL, failed, and
answered with no numbers at all — while the receipt honestly reported
`lane: mixed · 3 governed · 1 free-formed`, which is what exposed it.

The damage is worse under a surface-only agent: the fabricated statement is
denied fail-closed, so the agent is left with no path at all.

Consolidation is an OPTIMISATION. A skipped merge costs one round trip; a
destroyed reference costs correctness — and, on the governed lane, replaces an
attested statement with an invented one.

Run: python3 test/test_governed_lane_plan.py            (no server, no network)
"""
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

# OBDA-READ IS ADDITIVE: the master defaults OFF and this suite's SUBJECT is the
# enabled lane, so it declares the master explicitly (a separate process — the
# module default is untouched elsewhere). The master-off truth table lives in
# its own section/suite.
from trusted_data_agent.core.config import APP_CONFIG as _APP_CONFIG
_OP.force("read", True)

from trusted_data_agent.agent.planner import Planner  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


lit = Planner._sql_arg_is_literal

# ---------------------------------------------------------------- literals
check("plain SQL is literal",
      lit({"arguments": {"sql": "SELECT 1 FROM fitness_db.Customers"}}))
check("SQL naming a table that merely LOOKS like a ref is still literal",
      lit({"arguments": {"sql": "SELECT * FROM phaser_log"}}),
      "the detector must key on the reference form, not on a substring")
check("a phase with no SQL argument does not block its siblings",
      lit({"arguments": {"action": "list_products"}}),
      "it contributes no query; blocking on it would disable consolidation broadly")
check("a phase with no arguments at all is inert",
      lit({}))

# ------------------------------------------------------------- references
check("the canonical {source: …} dict is NOT literal",
      not lit({"arguments": {"sql": {"source": "result_of_phase_2", "key": "sql"}}}),
      "this is the exact shape the model produced on the governed lane")
check("the legacy result_of_phase_N string is NOT literal",
      not lit({"arguments": {"sql": "result_of_phase_2"}}))
check("the legacy phase_N string is NOT literal",
      not lit({"arguments": {"sql": "phase_3"}}))
check("a reference embedded in surrounding text is NOT literal",
      not lit({"arguments": {"sql": "USE result_of_phase_2 here"}}),
      "a partially-referenced statement is still not SQL at plan time")
check("a list argument is NOT literal", not lit({"arguments": {"sql": ["a", "b"]}}))
check("None is NOT literal", not lit({"arguments": {"sql": None}}))

# the synonyms the pass itself accepts must all be covered, or the guard is
# bypassable by whichever name the planner happened to emit
for syn in ("sql", "query", "query_request"):
    check(f"the '{syn}' synonym is guarded",
          not lit({"arguments": {syn: {"source": "result_of_phase_1", "key": "sql"}}}),
          "the pass reads all three; a guard covering fewer is a hole")

# ------------------------------------------- the decision the pass makes
def would_skip(sequence):
    """Mirrors the pass's guard: any non-literal in the run ⇒ do not merge."""
    return any(not lit(p) for p in sequence)


governed = [
    {"phase": 4, "arguments": {"sql": {"source": "result_of_phase_2", "key": "sql"}}},
    {"phase": 5, "arguments": {"sql": {"source": "result_of_phase_3", "key": "sql"}}},
]
check("the observed governed plan is NOT consolidated", would_skip(governed),
      "this exact pair was merged into hallucinated SQL in the live run")

ordinary = [
    {"phase": 2, "arguments": {"sql": "SELECT a FROM t1"}},
    {"phase": 3, "arguments": {"sql": "SELECT b FROM t2"}},
]
check("an ordinary two-query plan is STILL consolidated", not would_skip(ordinary),
      "the optimisation must survive — this fix narrows the pass, never disables it")

mixed = [
    {"phase": 2, "arguments": {"sql": "SELECT a FROM t1"}},
    {"phase": 3, "arguments": {"sql": {"source": "result_of_phase_1", "key": "sql"}}},
]
check("one reference makes the whole run unmergeable", would_skip(mixed),
      "a sequence is merged as a unit; merging around a reference is not defined")

# -------------------------------------------------- the guard is wired in
import inspect  # noqa: E402
src = inspect.getsource(Planner._rewrite_plan_for_sql_consolidation)
# Written defensively ON PURPOSE: the first version of this section indexed into
# the source unconditionally, so REMOVING the guard (the exact defect under test)
# crashed the run instead of naming a failure. A gate that dies without saying
# what broke is half a gate.
wired = "_sql_arg_is_literal" in src
check("the pass consults the predicate", wired)
after = src.split("_sql_arg_is_literal", 1)[1][:400] if wired else ""
check("the pass SKIPS rather than merging", "continue" in after,
      "absent" if not wired else "the guard runs but does not bail out")
check("the skip happens BEFORE the consolidation LLM call",
      wired and "consolidation_prompt" in src
      and src.index("_sql_arg_is_literal") < src.index("consolidation_prompt"),
      "absent" if not wired else "guarding after the call still pays for the fabricated query")

# ------------------------- the REJECT headline must carry the real message
adapter = open(os.path.join(os.path.dirname(__file__), "..", "src",
                            "trusted_data_agent", "mcp_adapter", "adapter.py")).read()
check("a component error no longer reports a constant headline",
      '"error": "Component Error"' not in adapter,
      "a query_product REJECT names the closest declared member and the whole "
      "admissible set — that IS the self-correction food, and a constant headline buries it")


# ==========================================================================
# [B] A compiled statement that NOTHING executes.
#
# Observed live, twice, on the SAME question. One turn planned the seam
# correctly; the next planned three query_product phases and a final report —
# three governed compiles, ZERO rows, and an answer of pure prose:
#   "Customer counts and support ticket volumes vary by state."
#
# query_product COMPILES and deliberately never runs the statement (executing
# from inside a component would bypass the Agent-Authority chokepoint). So the
# PLAN must carry the execution. Which product and which slice is judgement and
# stays with the model; "compiled SQL must then be executed" is mechanical.
# ==========================================================================


class _FakeExecutor:
    """Only what the pass touches — a real Planner needs far more."""

    def __init__(self, plan, tools=("base_readQuery",)):
        self.meta_plan = plan
        self.dependencies = {"STATE": {"mcp_tools": {t: {} for t in tools}}}

    def _format_sse_with_depth(self, ev):
        return ev



    def _resolve_gate_profile(self, gate_name):
        # a Option-C profile (products granted) — the pass is per-agent gated
        # (Option A profiles get NO OBDA-READ planner behaviour), so the fixture
        # declares its participation the same way a real profile does
        return {"authorityConfig": {"dataSurface": {"products": ["support_load"]}}}

def run_pass(plan, tools=("base_readQuery",)):
    """Drive the REAL pass over a plan; returns (plan, emitted events)."""
    p = Planner.__new__(Planner)
    p.executor = _FakeExecutor(plan, tools)
    events = list(Planner._rewrite_plan_for_governed_execution(p))
    return p.executor.meta_plan, events


COMPILE = {"phase": 2, "goal": "Query the support_load product.",
           "relevant_tools": ["TDA_KnowledgeGraph"],
           "arguments": {"action": "query_product", "product_name": "support_load",
                         "measures": ["ticket_count"], "dimensions": ["Customers.State"]}}

# --- the broken shape: nothing executes the compiled statement ---------------
broken = [dict(COMPILE),
          {"phase": 3, "goal": "Report.", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
out, evs = run_pass(broken)
check("an unexecuted compile gains an execution phase", len(out) == 3,
      f"got {len(out)} phases")
inserted = out[1]
check("the inserted phase runs the data tool",
      inserted.get("relevant_tools") == ["base_readQuery"])
check("it is wired to the compiling phase by canonical reference",
      inserted["arguments"]["sql"] == {"source": "result_of_phase_2", "key": "sql"},
      f'got {inserted["arguments"].get("sql")!r}')
check("it never writes SQL of its own",
      not isinstance(inserted["arguments"]["sql"], str),
      "inventing SQL here would be the consolidation defect wearing a new hat")
check("the inserted phase takes the freed slot", inserted["phase"] == 3)
check("following phases are renumbered", out[2]["phase"] == 4,
      "leaving phase 3 twice would make every later reference ambiguous")
check("the correction is surfaced, not silent", len(evs) == 1
      and evs[0]["details"]["correction_type"] == "governed_execution_guarantee",
      "a plan the system rewrote must say so")

# --- the model's own CORRECT plan must be byte-identical ---------------------
correct = [dict(COMPILE),
           {"phase": 3, "goal": "Execute it.", "relevant_tools": ["base_readQuery"],
            "arguments": {"sql": {"source": "result_of_phase_2", "key": "sql"}}},
           {"phase": 4, "goal": "Report.", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
before = json.dumps(correct, sort_keys=True)
out, evs = run_pass(correct)
check("a correctly-planned seam is left untouched",
      json.dumps(out, sort_keys=True) == before and not evs,
      "the pass fires ONLY on the provably broken shape")

# the legacy string reference counts as consumption too
legacy = [dict(COMPILE),
          {"phase": 3, "relevant_tools": ["base_readQuery"],
           "arguments": {"sql": "result_of_phase_2"}}]
out, _ = run_pass(legacy)
check("a legacy string reference also counts as consumed", len(out) == 2)

# --- no data tool ⇒ never invent one ----------------------------------------
out, evs = run_pass([dict(COMPILE)], tools=())
check("with no data tool available, nothing is inserted", len(out) == 1 and not evs,
      "planning a tool the profile does not have guarantees a failed phase")

# --- compile_metric is NOT a governed execution (operator correction) --------
# The first version of this suite pinned "compile_metric gets the same
# guarantee" — encoding the scope creep that made the pass the author of a
# dangling reference (dd40d071: compile_metric('Revenue') failed, the inserted
# phase executed a reference to nothing, and its goal text called an unsigned
# metric compilation "the signed, deterministic contract"). A compiled metric
# is in no governed registry and carries no signature; its pre-OBDA-READ behaviour
# never had an inserted executor.
metric = [{"phase": 1, "relevant_tools": ["TDA_KnowledgeGraph"],
           "arguments": {"action": "compile_metric", "metric_name": "revenue"}}]
out, _ = run_pass(metric)
check("compile_metric gets NO inserted executor (not a governed execution)",
      len(out) == 1)

# --- per-agent Option A: no OBDA-READ planner behaviour for an opted-out profile --
_out_a, _evs_a = (lambda: (None, None))() or (None, None)
class _AExec(_FakeExecutor):
    def _resolve_gate_profile(self, gate_name):
        return {"authorityConfig": {}}          # Option A — the default
_pa = Planner.__new__(Planner)
_pa.executor = _AExec([dict(COMPILE)])
list(Planner._rewrite_plan_for_governed_execution(_pa))
check("Option A profile: the pass is inert even on an unconsumed compile",
      len(_pa.executor.meta_plan) == 1)

# --- OBDA-READ IS ADDITIVE: the pass does not exist with the master off -----------
_OP.force("read", False)
try:
    out, evs = run_pass([dict(COMPILE)])
    check("master OFF: the pass is inert even on an unconsumed query_product",
          len(out) == 1 and not evs,
          "a plan narrating a governed statement on an OBDA-disabled deployment")
finally:
    _OP.force("read", True)

# --- other KG actions are NOT compile-only ----------------------------------
for action in ("list_products", "query", "get_context", "add_entity"):
    out, _ = run_pass([{"phase": 1, "relevant_tools": ["TDA_KnowledgeGraph"],
                        "arguments": {"action": action}}])
    check(f"'{action}' is not treated as a compile", len(out) == 1,
          "these return data or mutate; adding a SQL phase after them is nonsense")

# --- several unexecuted compiles in one plan --------------------------------
two = [dict(COMPILE, phase=1),
       dict(COMPILE, phase=2),
       {"phase": 3, "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
out, evs = run_pass(two)
check("every unexecuted compile is covered", len(out) == 5 and len(evs) == 2,
      f"got {len(out)} phases / {len(evs)} events")
refs = [p["arguments"]["sql"]["source"] for p in out if p.get("relevant_tools") == ["base_readQuery"]]
check("each execution references ITS OWN compile", refs == ["result_of_phase_1", "result_of_phase_3"],
      f"got {refs} — a mis-wired reference would run the wrong product's SQL")

# --- degenerate input must not raise (the pass is fault-isolated, but a pass
#     that throws is a pass that silently does nothing) ------------------------
for bad in (None, [], [{}], [{"phase": "x", "relevant_tools": ["TDA_KnowledgeGraph"],
                         "arguments": {"action": "query_product"}}]):
    try:
        run_pass(bad if isinstance(bad, list) else [])
        check(f"degenerate plan {str(bad)[:24]} does not raise", True)
    except Exception as e:
        check(f"degenerate plan {str(bad)[:24]} does not raise", False, repr(e))

# --- the pass is actually WIRED into the pipeline, before consolidation ------
pipeline = inspect.getsource(Planner)
check("the pass is registered in the rewrite pipeline",
      '_pass("governed_execution"' in pipeline,
      "an unwired pass is a pass that never runs")
check("it runs BEFORE consolidation",
      pipeline.index('_pass("governed_execution"') < pipeline.index('_pass("sql_consolidation"'),
      "complete the plan, then optimise it")



# ==========================================================================
# [C] Dead-phase elimination — surface-only turns drop hand-written SQL.
#
# Observed (session 933edb5f): the model planned query_product correctly AND a
# literal `SELECT SUM(customer_count) FROM support_load` — SQL against the
# product's NAME as if it were a table. The guarantee pass rightly saw that no
# phase consumed the compiled statement and inserted the real executor, leaving
# TWO base_readQuery phases: the correct one and a dead one that burned a
# denial + a recovery round at runtime. Under an exclusive surface a literal-SQL
# phase is denied by construction, so removing it is dead-code elimination
# against a fail-closed rule — not a judgement about the model's intent.
# ==========================================================================
from trusted_data_agent.core import agent_authority as _aa  # noqa: E402

SURFACE_PROFILE = {"id": "x", "authorityConfig": {
    "dataSurface": {"products": ["support_load"], "exclusive": True, "advisory": False}}}


def confined(fn):
    tok = _aa.set_current_principal(_aa.derive_principal(SURFACE_PROFILE, "u1"))
    try:
        return fn()
    finally:
        _aa.reset_current_principal(tok)


LITERAL = {"phase": 2, "goal": "Sum it.", "relevant_tools": ["base_readQuery"],
           "arguments": {"sql": "SELECT SUM(customer_count) FROM support_load"}}
REPORT = {"phase": 3, "goal": "Report.", "relevant_tools": ["TDA_FinalReport"],
          "arguments": {"source_data": ["result_of_phase_2"]}}

# the observed live shape: compile + doomed literal + report
out, evs = confined(lambda: run_pass([dict(COMPILE, phase=1),
                                      dict(LITERAL), dict(REPORT)]))
tools = [(p.get("relevant_tools") or ["?"])[0] for p in out]
check("surface-only: exactly ONE executor — the doomed literal is REWIRED in place",
      tools == ["TDA_KnowledgeGraph", "base_readQuery", "TDA_FinalReport"],
      f"got {tools} — the live turn carried TWO base_readQuery phases")
check("the rewired phase references the compile, never literal text",
      out[1]["arguments"]["sql"] == {"source": "result_of_phase_1", "key": "sql"})
check("phase numbers are untouched (in-place, no renumbering)",
      [p["phase"] for p in out] == [1, 2, 3])
check("downstream references stay valid and point at the ROWS-yielding phase",
      out[2]["arguments"]["source_data"] == ["result_of_phase_2"],
      "a first version removed+renumbered, and the gate caught the report's "
      "reference being renumbered into pointing at itself")
check("the rewiring is surfaced, not silent, and names the replaced SQL",
      any((e.get("details") or {}).get("correction_type") == "surface_dead_sql_rewired"
          and "SUM(customer_count)" in str((e.get("details") or {}).get("replaced_sql"))
          for e in evs))

# a REFERENCE-carrying phase is consumption, never dead code
ref_plan = [dict(COMPILE, phase=1),
            {"phase": 2, "relevant_tools": ["base_readQuery"],
             "arguments": {"sql": {"source": "result_of_phase_1", "key": "sql"}}}]
out, evs = confined(lambda: run_pass([dict(p) for p in ref_plan]))
check("surface-only: a reference phase is kept untouched",
      len(out) == 2 and not evs)

# UNCONFINED: literal SQL is normal operation — never removed
out, evs = run_pass([dict(COMPILE, phase=1), dict(LITERAL), dict(REPORT)])
check("unconfined: the literal phase survives",
      sum(1 for p in out if (p.get("relevant_tools") or ["?"])[0] == "base_readQuery") == 2,
      "removing model-authored SQL on an unconfined turn would rewrite legitimate plans")

# no compile phase ⇒ nothing removed even confined — the runtime denial is
# recovery-shaped and is the mechanism that teaches the pivot
out, evs = confined(lambda: run_pass([dict(LITERAL, phase=1), dict(REPORT, phase=2,
                    arguments={"source_data": ["result_of_phase_1"]})]))
check("surface-only with NO compile phase: the plan is left alone",
      len(out) == 2 and not evs,
      "deleting the only data phase would leave the turn answerless with no signal")


# ==========================================================================
# [D] A key-less phase reference resolves by the ARGUMENT'S OWN NAME first.
#
# Observed (the "1,000 customers" turn): the model authored the legacy form
# `sql: "result_of_phase_1"` against a query_product result. The resolver's
# blind unwrap returned the WHOLE component payload as the SQL text; the tool
# errored, self-correction fell back to raw SQL (denied under the surface), and
# the report fabricated "1,000 customers" from the payload's TOP 1000 clause.
# The reference names its key implicitly — the argument is CALLED sql.
# ==========================================================================
from trusted_data_agent.agent.executor import PlanExecutor as _PX  # noqa: E402


def _resolver(workflow_state):
    e = object.__new__(_PX)
    e.workflow_state = dict(workflow_state)
    e.events_to_yield = []
    e.meta_plan = []
    e._format_sse_with_depth = lambda *a, **k: ""
    return e


KG_PAYLOAD = [{"type": "knowledge_graph", "spec": {},
               "metadata": {"tool_name": "TDA_KnowledgeGraph",
                            "sql": "SELECT TOP 1000 \"customer_count\" FROM x",
                            "lane": "governed"}}]

e = _resolver({"result_of_phase_1": KG_PAYLOAD})
out = e._resolve_arguments({"sql": "result_of_phase_1"})
check("a bare-string ref resolves by the argument's own name",
      out.get("sql") == "SELECT TOP 1000 \"customer_count\" FROM x",
      f"got {str(out.get('sql'))[:80]!r} — the blind unwrap shipped the whole payload "
      "to the warehouse as SQL")

out = e._resolve_arguments({"sql": {"source": "result_of_phase_1"}})
check("a key-less dict ref resolves the same way",
      out.get("sql") == "SELECT TOP 1000 \"customer_count\" FROM x")

out = e._resolve_arguments({"sql": {"source": "result_of_phase_1", "key": "sql"}})
check("the canonical dict-with-key form is unchanged",
      out.get("sql") == "SELECT TOP 1000 \"customer_count\" FROM x")

# the unwrap FALLBACK must survive — a scalar result has no key named after the arg
e2 = _resolver({"result_of_phase_1": [{"results": [{"CurrentDate": "2026-08-16"}]}]})
out = e2._resolve_arguments({"date": "result_of_phase_1"})
check("a scalar reference still unwraps exactly as before",
      out.get("date") == "2026-08-16",
      "the arg-name lookup must be a refinement, never a replacement")

# an unresolvable source keeps today's behaviour
e3 = _resolver({})
out = e3._resolve_arguments({"sql": "result_of_phase_9"})
check("an unknown source passes through unchanged",
      out.get("sql") == "result_of_phase_9")


# ==========================================================================
# [E] A structured refusal that names its own remedy never costs an LLM round.
#
# Operator intent, stated and confirmed: self-correction is for errors whose
# recovery requires JUDGEMENT. The off-surface refusal — "'fitness_sales' is
# not on this agent's data surface — its surface is: support_load" — contains
# its remedy verbatim, and the observed LLM round (~2,800 tokens) did exactly
# the one-argument swap the sentence dictates. Three mechanical recoveries:
# the product swap, the non-string-SQL fail-fast, and filter-shape coercion.
# ==========================================================================
from trusted_data_agent.agent.phase_executor import (  # noqa: E402
    DEFINITIVE_TOOL_ERRORS, CorrectionHandler, SurfaceRefusalStrategy)

REFUSAL = ("'fitness_sales' is not on this agent's data surface \u2014 "
           "its surface is: support_load")


def _strategy():
    st = object.__new__(SurfaceRefusalStrategy)
    return st


st = _strategy()
check("the strategy recognises the refusal", st.can_handle(REFUSAL))
check("it does not fire on unrelated errors",
      not st.can_handle("Database 'x' does not exist")
      and not st.can_handle("query_product rejected (unknown_member): …"))

import asyncio as _aio  # noqa: E402
FAILED = {"tool_name": "TDA_KnowledgeGraph",
          "arguments": {"action": "query_product", "product_name": "fitness_sales",
                        "measures": ["ticket_count"]}}
corrected, evs = _aio.run(st.generate_correction(
    dict(FAILED), {"data": REFUSAL, "error": REFUSAL, "status": "error"}))
check("a single-product surface is swapped with ZERO LLM calls",
      corrected is not None
      and corrected["arguments"]["product_name"] == "support_load",
      "the refusal named the only legal door; reading it is not judgement")
check("everything else in the call is preserved",
      corrected["arguments"]["measures"] == ["ticket_count"]
      and corrected["arguments"]["action"] == "query_product")
check("the swap is surfaced as a deterministic correction",
      any((e[0].get("details") or {}).get("correction_type") == "surface_product_swap"
          for e in evs))
check("no event in the deterministic path is an LLM call",
      not any("LLM" in str((e[0] or {}).get("step", "")) for e in evs))

multi = REFUSAL.replace("support_load", "support_load, fitness_360")
corrected, evs = _aio.run(st.generate_correction(
    dict(FAILED), {"data": multi, "error": multi, "status": "error"}))
check("a MULTI-product surface falls through to the LLM (a real choice)",
      corrected is None and not evs,
      "which product answers the question is judgement; the swap is only "
      "deterministic when there is exactly one legal door")

# already at the only door -> nothing to swap; let the real error surface
at_door = {"tool_name": "TDA_KnowledgeGraph",
           "arguments": {"action": "query_product", "product_name": "support_load"}}
corrected, evs = _aio.run(st.generate_correction(
    dict(at_door), {"data": REFUSAL, "error": REFUSAL, "status": "error"}))
check("already-at-the-door falls through rather than looping", corrected is None)

# the strategy is registered AHEAD of the generic LLM strategy
import inspect as _insp  # noqa: E402
reg = _insp.getsource(CorrectionHandler.__init__)
# defensive on purpose: the first version used .index(), so REMOVING the
# registration — the exact mutation under test — crashed the run instead of
# naming a failure. A gate that dies without saying what broke is half a gate.
check("the strategy is registered before the generic LLM strategy",
      "SurfaceRefusalStrategy" in reg
      and reg.index("SurfaceRefusalStrategy") < reg.index("GenericCorrectionStrategy"),
      "unregistered — every off-surface refusal pays the LLM round again")

# --- [E2] non-string SQL fails FAST and DEFINITIVELY, on BOTH chokepoints ----
from trusted_data_agent.agent.tool_enforcement import nonstring_sql_block  # noqa: E402

blk = nonstring_sql_block("base_readQuery", {"sql": [{"metadata": {"sql": "SELECT 1"}}]})
check("a non-string sql argument is refused with the definitive marker",
      isinstance(blk, dict) and "produced no SQL" in blk.get("error", ""))
check("a string sql argument passes",
      nonstring_sql_block("base_readQuery", {"sql": "SELECT 1"}) is None)
check("an absent sql argument passes",
      nonstring_sql_block("base_listTables", {"database_name": "x"}) is None)
check("TDA_ framework tools are exempt",
      nonstring_sql_block("TDA_KnowledgeGraph", {"sql": {"a": 1}}) is None)
for syn in ("sql", "query", "query_request"):
    check(f"the '{syn}' synonym is guarded on the shared helper",
          nonstring_sql_block("t", {syn: {"x": 1}}) is not None)

adapter_src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                   "trusted_data_agent", "mcp_adapter", "adapter.py")).read()
te_src = open(os.path.join(os.path.dirname(__file__), "..", "src",
              "trusted_data_agent", "agent", "tool_enforcement.py")).read()
check("the MCP adapter routes through the ONE shared helper",
      "sql_gate_before(" in adapter_src
      and "nonstring_sql_block(tool_name, arguments)" in te_src.split("def sql_gate_before")[1].split("\ndef ")[0],
      "an inline copy is how the ReAct path ended up uncovered in the first place")
check("the ReAct wrapper routes through the SAME helper",
      te_src.count("nonstring_sql_block(tool_name, arguments)") >= 1
      and "_guarded" in te_src,
      "the cross-class audit's finding: ReAct bypasses invoke_mcp_tool entirely")
check("no inline copy survives in the adapter",
      "is not SQL text" not in adapter_src,
      "one rule, one home")
import re as _re  # noqa: E402
check("the refusal is in the DEFINITIVE set (no correction rounds)",
      any(_re.search(pat, "argument 'sql' is not SQL text (got list) \u2014 the "
                          "upstream reference produced no SQL.")
          for pat in DEFINITIVE_TOOL_ERRORS),
      "retrying cannot change what the upstream phase produced")

# --- [E3] filter-shape coercion (the bad_filter LLM round) ------------------
from trusted_data_agent.dbdeploy.product_compiler import normalize_filters  # noqa: E402

canon = [{"attr": "Products.ProductType", "op": "eq", "value": "shoes"}]
check("the canonical shape passes through untouched",
      normalize_filters(list(canon)) == canon)
check("the observed LLM shape {attr: value} coerces to canonical",
      normalize_filters({"Products.ProductType": "shoes"}) == canon,
      "the model paid an LLM round to be told the shape; reshaping is mechanical")
check("a MULTI-pair dict coerces to one filter per pair",
      normalize_filters({"Products.ProductType": "shoes", "Customers.State": "CA"})
      == [{"attr": "Products.ProductType", "op": "eq", "value": "shoes"},
          {"attr": "Customers.State", "op": "eq", "value": "CA"}],
      "the single-pair case is also caught by the item branch, so only the "
      "multi-pair case makes the dict branch load-bearing")
check("a list value coerces to the `in` operator",
      normalize_filters({"Customers.State": ["CA", "NY"]})
      == [{"attr": "Customers.State", "op": "in", "value": ["CA", "NY"]}])
check("an op-less item coerces to eq",
      normalize_filters([{"attr": "Customers.State", "value": "CA"}])
      == [{"attr": "Customers.State", "op": "eq", "value": "CA"}])
check("a [attr, value] pair coerces",
      normalize_filters([["Customers.State", "CA"]])
      == [{"attr": "Customers.State", "op": "eq", "value": "CA"}])
check("a single-key dict item coerces",
      normalize_filters([{"Products.ProductType": "shoes"}]) == canon)
check("empty stays empty", normalize_filters(None) == [] and normalize_filters([]) == [])
check("an unknown shape passes through for the strict REJECT",
      normalize_filters([42]) == [42],
      "coercion widens accepted SYNTAX, never accepted meaning — garbage still "
      "earns the precise bad_filter message")


# ==========================================================================
# [F] Dependency-failure skip — a phase whose upstream FAILED never runs.
#
# Observed twice live: a phase referencing a REJECTed compile ran anyway; its
# reference resolved to nothing (argument omitted) or to the error payload, the
# tool errored, and an LLM self-correction round filled the hole with
# hand-written SQL. A phase whose upstream failed cannot succeed.
# ==========================================================================
from trusted_data_agent.agent.phase_executor import PhaseExecutor as _PE  # noqa: E402

up = _PE._failed_upstream_refs

FAILED_WS = {"result_of_phase_1": {"status": "error",
                                   "error": "query_product rejected (unknown_member)"}}
OK_WS = {"result_of_phase_1": {"status": "success",
                               "results": [{"customer_count": "100"}]}}

canon = {"phase": 2, "arguments": {"sql": {"source": "result_of_phase_1", "key": "sql"}}}
check("a canonical ref to a FAILED upstream is detected",
      up(canon, FAILED_WS) == [("result_of_phase_1",
                                "query_product rejected (unknown_member)")])
check("the same ref to a SUCCESSFUL upstream is clean", up(canon, OK_WS) == [])
check("a legacy string ref is detected",
      up({"arguments": {"sql": "result_of_phase_1"}}, FAILED_WS) != [])
check("the phase_N short form is normalised and detected",
      up({"arguments": {"sql": "phase_1"}}, FAILED_WS) != [])
check("a loop_over ref is detected",
      up({"arguments": {}, "loop_over": "result_of_phase_1"}, FAILED_WS) != [])
# The dd40d071 shape: a COMPONENT failure carries its error in spec/metadata
# with a success-looking top level — this helper read it as clean, so the
# dependent phase executed a reference to nothing and base_readQuery went to
# the warehouse with NO sql at all.
COMPONENT_FAIL_WS = {"result_of_phase_1": {
    "type": "knowledge_graph", "spec": {},
    "metadata": {"tool_name": "TDA_KnowledgeGraph", "action": "compile_metric",
                 "error": "no metric named 'Revenue' in this ontology"}}}
check("a component metadata-error upstream is detected (dd40d071)",
      up(canon, COMPONENT_FAIL_WS) == [("result_of_phase_1",
                                        "no metric named 'Revenue' in this ontology")])
SPEC_FAIL_WS = {"result_of_phase_1": {"spec": {"error": "compile failed"},
                                      "metadata": {}}}
check("a spec.error upstream is detected", up(canon, SPEC_FAIL_WS) != [])
META_TRUE_WS = {"result_of_phase_1": {"spec": {}, "metadata": {"error": True},
                                      "results": [{"a": 1}]}}
check("metadata error:True WITH results is not a failure (boolean flag + data)",
      up(canon, META_TRUE_WS) == [])
GOOD_COMPONENT_WS = {"result_of_phase_1": {
    "type": "knowledge_graph", "spec": {"sql": "SELECT 1"},
    "metadata": {"action": "query_product"}}}
check("a clean component payload is not a failure", up(canon, GOOD_COMPONENT_WS) == [])

check("a MISSING upstream is NOT a failure (parallel groups resolve later)",
      up(canon, {}) == [],
      "treating absent as failed would skip phases whose upstream is mid-flight")
check("an upstream with an error field BESIDE results is not a failure",
      up(canon, {"result_of_phase_1": {"error": "1 row had nulls",
                                       "results": [{"a": 1}]}}) == [],
      "partial-success results are real data; skipping would discard them")
check("a phase with no refs is clean",
      up({"arguments": {"sql": "SELECT 1"}}, FAILED_WS) == [])
check("phase 1 is not matched by phase 10",
      up({"arguments": {"sql": "result_of_phase_10"}}, FAILED_WS) == [])
for bad in (None, {}, {"arguments": None}, {"arguments": {"x": object()}}):
    try:
        up(bad if isinstance(bad, dict) else {}, FAILED_WS)
        check(f"degenerate phase {str(bad)[:20]} never raises", True)
    except Exception as exc:
        check(f"degenerate phase {str(bad)[:20]} never raises", False, repr(exc))

# wiring: the skip runs BEFORE argument resolution, records its own error result
# (so a later phase referencing THIS one cascades), and is surfaced
pe_src = _insp.getsource(_PE)
_skip_at = pe_src.find("_failed_upstream_refs(phase, self.executor.workflow_state)")
_res_at = pe_src.find('strategic_args = self.executor._resolve_arguments(phase.get("arguments", {}), loop_item=loop_item)')
check("the skip is wired BEFORE argument resolution",
      -1 < _skip_at < _res_at,
      "resolving first is how the omitted-argument garbage reached the tool")
_w = pe_src[_skip_at:_skip_at + 2200]
check("the skipped phase records its OWN error result (cascade)",
      '"skipped_upstream_failure": True' in _w)
check("the skip is surfaced as a correction event",
      '"correction_type": "upstream_failure_skip"' in _w)
check("the skip closes its phase honestly",
      '"status": "skipped"' in _w)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — the governed lane survives the rewrite pipeline"))
sys.exit(1 if FAIL else 0)
