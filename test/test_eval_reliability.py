"""WS3 — reliability & output-integrity scorers (CI-pure).

Run:
    PYTHONPATH=src python test/test_eval_reliability.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase, RunRecord  # noqa: E402
from trusted_data_agent.eval.scorer import scorers_for         # noqa: E402
import trusted_data_agent.eval.scorers as _s                   # noqa: E402,F401 (registers)

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def run(case, turn):
    return {v.dimension: v for v in (s.score(case, RunRecord(turn))
            for s in scorers_for(case.profile_class))}


def _opt(**kw):
    t = {"profile_type": "tool_enabled", "status": "success", "isValid": True,
         "final_summary_text": "ok", "duration_ms": 5000,
         "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}},
                              "result": {"tool_output_summary": {"status": "success"}}}]}
    t.update(kw)
    return t


def t_latency():
    c = EvalCase(id="l", query="q", profile_class="tool_enabled", assertions={"max_latency_ms": 4000})
    v = run(c, _opt(duration_ms=5000))
    check("latency: over budget fails", v["latency"].passed is False and v["latency"].score == 5000.0)
    v2 = run(c, _opt(duration_ms=3000))
    check("latency: under budget passes", v2["latency"].passed is True)
    v3 = run(EvalCase(id="l2", query="q", profile_class="tool_enabled"), _opt(duration_ms=5000))
    check("latency: no threshold -> abstain w/ value", v3["latency"].passed is None and v3["latency"].score == 5000.0)


def t_output_sanity():
    c = EvalCase(id="o", query="q", profile_class="llm_only")
    clean = run(c, {"profile_type": "llm_only", "final_summary_text": "We have 100 customers."})
    check("output_sanity: clean passes", clean["output_sanity"].passed is True)
    leak = run(c, {"profile_type": "llm_only",
                   "final_summary_text": 'Error: {"tool_name": "x"} Traceback (most recent call last)'})
    check("output_sanity: leaked internals fails", leak["output_sanity"].passed is False)
    enc = run(c, {"profile_type": "llm_only", "final_summary_text": "Here: [ENCRYPTED CONTENT]"})
    check("output_sanity: encrypted placeholder fails", enc["output_sanity"].passed is False)


def t_tool_reliability():
    c = EvalCase(id="t", query="q", profile_class="tool_enabled",
                 assertions={"min_tool_success_rate": 0.9, "max_tool_repeats": 1})
    trace = [{"action": {"tool_name": "base_readQuery", "arguments": {"sql": "A"}},
              "result": {"tool_output_summary": {"status": "error"}}},
             {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "A"}},
              "result": {"tool_output_summary": {"status": "success"}}}]
    v = run(c, _opt(execution_trace=trace))
    check("tool_success_rate: 1/2 fails 0.9 floor", v["tool_success_rate"].passed is False and v["tool_success_rate"].score == 0.5)
    check("no_tool_loops: same args ×2 > max 1 fails", v["no_tool_loops"].passed is False and v["no_tool_loops"].score == 2.0)
    check("error_recovery: error + completed -> recovered (informational)", v["error_recovery"].passed is None)


def _conv_turn(calls, scope_dbs=None, **kw):
    """Build a conversation_with_tools turn. ``calls`` = [(tool_name, args_dict), ...];
    ``scope_dbs`` = the KG enrichment's declared deployable scope (or None = no KG)."""
    events = []
    for name, args in calls:
        events.append({"type": "conversation_tool_invoked",
                       "payload": {"tool_name": name, "arguments": args}})
        events.append({"type": "conversation_tool_completed", "payload": {"success": True}})
    t = {"profile_type": "conversation_with_tools", "status": "success", "isValid": True,
         "final_summary_text": "answer", "conversation_agent_events": events}
    if scope_dbs is not None:
        t["kg_enrichment_event"] = {"enrichments": [{"scope_databases": scope_dbs}]}
    t.update(kw)
    return t


def t_scope_and_economy():
    # The actual turn-7 shape: KG scope = restaurants_mvm_test; the agent brute-forced
    # finance (domain≠db), airlines_mvm, media_broadcasting_mvm + DBC catalog scans.
    # Both cut points, mirroring the seeded config default ({danger:1, healthy:0} /
    # {danger:14, healthy:7}) so the 3-band semantics are exercised as they run live.
    c = EvalCase(id="s", query="q", profile_class="conversation_with_tools",
                 assertions={"max_out_of_scope_dbs": 1, "max_out_of_scope_dbs__healthy": 0,
                             "max_tool_calls": 14, "max_tool_calls__healthy": 7})
    turn7 = _conv_turn([
        ("TDA_KnowledgeGraph", {"query_text": "average order delivery time", "action": "query"}),
        ("base_tableList", {"database_name": "finance"}),
        ("base_databaseList", {"scope": "all"}),
        ("base_tableList", {"database_name": "airlines_mvm"}),
        ("base_tableList", {"database_name": "media_broadcasting_mvm"}),
        ("base_tableList", {"database_name": "restaurants_mvm_test"}),
        ("base_readQuery", {"sql": "SELECT * FROM DBC.TablesV WHERE DatabaseName = 'airlines_mvm'"}),
        ("base_columnDescription", {"database_name": "restaurants_mvm_test", "obj_name": "maintenance_work_order"}),
        ("base_readQuery", {"sql": "SELECT AVG(x) FROM restaurants_mvm_test.maintenance_work_order"}),
    ], scope_dbs=["restaurants_mvm_test"])
    v = run(c, turn7)
    sa = v["scope_adherence"]
    check("scope_adherence: flags the 3 out-of-scope DBs", sa.score == 3.0)
    check("scope_adherence: finance/airlines/media in evidence",
          set(sa.evidence) == {"finance", "airlines_mvm", "media_broadcasting_mvm"})
    check("scope_adherence: DBC catalog + WHERE-literal NOT flagged",
          "dbc" not in sa.evidence and "airlines_mvm" in sa.evidence)  # airlines from tableList arg, not the WHERE literal
    check("scope_adherence: >1 out-of-scope = danger band", sa.band == "danger" and sa.passed is False)
    check("scope_adherence: non-gating", any(s.dimension == "scope_adherence" and s.gating is False
                                             for s in scorers_for("conversation_with_tools")))
    check("tool_calls: counts 9 invocations", v["tool_calls"].score == 9.0)
    # all-in-scope → healthy, 0 out-of-scope
    clean = _conv_turn([("base_readQuery", {"sql": "SELECT * FROM restaurants_mvm_test.ap_invoice"}),
                        ("base_readQuery", {"sql": "SELECT * FROM DBC.TablesV"})],
                       scope_dbs=["restaurants_mvm_test"])
    cv = run(c, clean)
    check("scope_adherence: all in-scope (+DBC) → 0, healthy", cv["scope_adherence"].score == 0.0 and cv["scope_adherence"].band == "healthy")
    # no KG scope declared → abstain (nothing to adhere to)
    nokg = run(c, _conv_turn([("base_tableList", {"database_name": "airlines_mvm"})]))
    check("scope_adherence: no KG scope → abstain", nokg["scope_adherence"].passed is None and nokg["scope_adherence"].score is None)
    # single out-of-scope DB = amber (review), still passes
    one = run(c, _conv_turn([("base_tableList", {"database_name": "airlines_mvm"})],
                            scope_dbs=["restaurants_mvm_test"]))
    check("scope_adherence: 1 out-of-scope = review (amber), passes", one["scope_adherence"].band == "review" and one["scope_adherence"].passed is True)
    # tool_calls banding
    check("tool_calls: 9 <= 14 → not danger here (review/healthy)", v["tool_calls"].band in ("healthy", "review"))
    big = run(EvalCase(id="s2", query="q", profile_class="conversation_with_tools", assertions={"max_tool_calls": 14}),
              _conv_turn([("base_readQuery", {"sql": f"SELECT {i} FROM restaurants_mvm_test.t"}) for i in range(19)],
                         scope_dbs=["restaurants_mvm_test"]))
    check("tool_calls: 19 > 14 → danger band", big["tool_calls"].score == 19.0 and big["tool_calls"].band == "danger")
    # only tool-using classes get these scorers
    for cls in ("llm_only", "rag_focused", "genie"):
        dims = {s.dimension for s in scorers_for(cls)}
        check(f"scope_adherence/tool_calls absent for {cls}",
              "scope_adherence" not in dims and "tool_calls" not in dims)


def t_numeric_fidelity():
    c = EvalCase(id="n", query="q", profile_class="tool_enabled", assertions={"min_numeric_fidelity": 1.0})
    grounded = _opt(final_summary_text="There are 100 customers.",
                    execution_trace=[{"action": {"tool_name": "base_readQuery", "arguments": {}},
                                      "result": {"result_preview": "CustomerCount: 100"}}])
    v = run(c, grounded)
    check("numeric_fidelity: grounded number passes", v["numeric_fidelity"].passed is True)
    hallucinated = _opt(final_summary_text="There are 250 customers.",
                        execution_trace=[{"action": {"tool_name": "base_readQuery", "arguments": {}},
                                          "result": {"result_preview": "CustomerCount: 100"}}])
    v2 = run(c, hallucinated)
    check("numeric_fidelity: fabricated number fails", v2["numeric_fidelity"].passed is False)


def t_context_health():
    c = EvalCase(id="ch", query="q", profile_class="tool_enabled")
    v = run(c, _opt(context_window_snapshot_event={"condensations": [1, 2], "distillation_events": [1]}))
    check("context_health: counts trims", v["context_health"].score == 3.0)


def t_focus_genie():
    fc = EvalCase(id="f", query="q", profile_class="rag_focused", assertions={"min_retrieval_relevance": 0.7})
    fv = run(fc, {"profile_type": "rag_focused",
                  "knowledge_retrieval_event": {"chunks": [{"similarity_score": 0.9}, {"similarity_score": 0.5}]}})
    check("retrieval_relevance: mean 0.70 >= 0.7 passes", fv["retrieval_relevance"].passed is True and fv["retrieval_relevance"].score == 0.7)
    gc = EvalCase(id="g", query="q", profile_class="genie")
    gv = run(gc, {"profile_type": "genie",
                  "genie_metadata": {"slaves_invoked": [{"profile_tag": "A", "status": "success"},
                                                        {"profile_tag": "B", "status": "error"}]}})
    check("expert_success: a failed expert fails", gv["expert_success"].passed is False)


def t_dispatch():
    cross = {s.dimension for s in scorers_for("llm_only")}
    check("cross-class: output_sanity + latency in Ideate", {"output_sanity", "latency"} <= cross)
    check("cross-class: tool scorers NOT in Ideate", "tool_success_rate" not in cross and "no_tool_loops" not in cross)
    opt = {s.dimension for s in scorers_for("tool_enabled")}
    check("Optimize has tool reliability + plan_execution + numeric_fidelity",
          {"tool_success_rate", "no_tool_loops", "plan_execution", "numeric_fidelity"} <= opt)
    check("retrieval_relevance only Focus", "retrieval_relevance" in {s.dimension for s in scorers_for("rag_focused")}
          and "retrieval_relevance" not in opt)
    check("expert_success only genie", "expert_success" in {s.dimension for s in scorers_for("genie")})


def t_harmonized_kpis():
    """Harmonization layer: tokens is universal; steps is its own KPI for tool/ReAct
    classes; clean_termination derives from the answer when there's no provenance."""
    # tokens — universal core KPI on every class
    for cls in ("llm_only", "conversation_with_tools", "rag_focused", "tool_enabled", "genie"):
        check(f"tokens KPI present for {cls}", "tokens" in {s.dimension for s in scorers_for(cls)})
    tok = run(EvalCase(id="x", query="q", profile_class="llm_only"),
              {"profile_type": "llm_only", "status": "success", "isValid": True,
               "final_summary_text": "hi", "turn_input_tokens": 1200, "turn_output_tokens": 300})
    check("tokens measured on a pure Ideate turn", tok["tokens"].score == 1500.0)

    # tasks — universal execution KPI (every class + flow); steps — Optimize planner only
    dims_react = {s.dimension for s in scorers_for("conversation_with_tools")}
    check("tasks KPI present for LangChain ReAct", "tasks" in dims_react)
    check("steps (planner) absent for ReAct", "steps" not in dims_react)
    check("steps (planner) absent for pure Ideate", "steps" not in {s.dimension for s in scorers_for("llm_only")})
    check("steps (planner) present only for Optimize", "steps" in {s.dimension for s in scorers_for("tool_enabled")})
    check("tasks KPI present for pure Ideate", "tasks" in {s.dimension for s in scorers_for("llm_only")})
    react = run(EvalCase(id="x", query="q", profile_class="conversation_with_tools"),
                {"profile_type": "conversation_with_tools", "status": "success", "isValid": True,
                 "final_summary_text": "12 cakes", "conversation_agent_events": [
                     {"type": "conversation_tool_invoked", "payload": {"tool_name": "a", "arguments": {}}},
                     {"type": "conversation_tool_completed", "payload": {"success": True}},
                     {"type": "conversation_tool_invoked", "payload": {"tool_name": "b", "arguments": {}}},
                     {"type": "conversation_tool_completed", "payload": {"success": False}},
                 ]})
    check("tasks counts ReAct execution units", react["tasks"].score == 2.0)
    check("self_correction determined for ReAct (failed tool)", react["self_correction"].score == 1.0)
    check("clean_termination derived from answer for ReAct (no provenance)",
          react["clean_termination"].passed is True)
    # efficiency no longer masquerades as the task count
    check("efficiency abstains without optimal_tasks", react["efficiency"].passed is None and react["efficiency"].score is None)


def t_surface_violation():
    """OBDA-READ Phase C — denied raw-SQL attempts by a surface-only agent (product-gap signal)."""
    from trusted_data_agent.eval.scorers.reliability import SurfaceViolationScorer
    sc = SurfaceViolationScorer()
    case = EvalCase(id="c", query="q", profile_class="tool_enabled")

    def turn(calls):
        return {"profile_type": "tool_enabled", "final_summary_text": "done",
                "execution_trace": [{"action": {"tool_name": c.get("tool", "base_readQuery"),
                                                "arguments": {"sql": "SELECT 1"}},
                                     "result": c["result"]} for c in calls]}

    DENIED = {"result": {"status": "error", "metadata": {
        "agent_denied": True,
        "policy_reason": "this agent is surface-only — raw SQL is denied"}}}
    OTHER_DENIAL = {"result": {"status": "error", "metadata": {
        "agent_denied": True, "policy_reason": "tool is on the denied list"}}}
    OK = {"result": {"status": "success", "results": [{"n": 1}]}}

    v = sc.score(case, RunRecord(turn([DENIED, OK])))
    check("one surface denial -> metric 1", v.score == 1.0)
    check("surface denial verdict never gates", v.gating is False and v.passed is not False)
    check("the explanation names the product-gap reading", "product-gap" in v.explanation)

    v = sc.score(case, RunRecord(turn([DENIED, DENIED, OK])))
    check("denials are counted, not boolean", v.score == 2.0)

    # A denial by ANOTHER authority rule (denied_tools, deny_writes) is NOT a surface
    # violation — counting it would misattribute the signal to the data surface.
    # `passed is None` cannot distinguish an abstain from an unthresholded metric —
    # the discriminating field is `score` (an abstain carries none).
    v = sc.score(case, RunRecord(turn([OTHER_DENIAL, OK])))
    check("a non-surface authority denial abstains here", v.score is None)
    check("...and does not read as a reported denial count", "denied" not in (v.explanation or "") or "no data-surface" in (v.explanation or ""))

    v = sc.score(case, RunRecord(turn([OK])))
    check("clean turn abstains (no decorative zero)", v.score is None)


def t_action_safety():
    """ACT B4d — the share of the turn's action WRITES whose every check held, read through
    the ONE fold (`action_perform.summarize`)."""
    from trusted_data_agent.eval.scorers.reliability import ActionSafetyScorer
    from trusted_data_agent.eval.scorer import scorers_for
    sc = ActionSafetyScorer()
    case = EvalCase(id="c", query="q", profile_class="conversation_with_tools")
    HOLD = [{"id": "row_exists", "holds": True}, {"id": "one_row_affected", "holds": True}]
    BROKE = [{"id": "row_exists", "holds": True}, {"id": "tier_is_known", "holds": False}]

    def turn(acts, conf=None):
        return {"profile_type": "conversation_with_tools", "final_summary_text": "done",
                "actions": acts, **({"action_confirmations": conf} if conf else {})}

    check("abstains with no action on the turn", sc.score(case, RunRecord(turn([]))).score is None)
    v = sc.score(case, RunRecord(turn([{"action": "a", "outcome": "refused", "refused_check": "not_granted",
                                        "refusal": "not granted"}])))
    check("an act refused before it compiled is not a write — abstains", v.score is None)
    v = sc.score(case, RunRecord(turn([{"action": "a", "outcome": "performed", "checks": HOLD},
                                       {"action": "b", "outcome": "refused", "refused_check": "tier_is_known",
                                        "checks": BROKE}])))
    check("one of two writes held -> 0.5", v.score == 0.5)
    check("the failed check is named, and it says nothing was written",
          "tier_is_known" in v.explanation and "rolled back" in v.explanation)
    check("never a verdict, never gating", v.passed is None and v.gating is False)
    check("a parked write whose checks held counts as held",
          sc.score(case, RunRecord(turn([{"action": "a", "outcome": "parked", "checks": HOLD}]))).score == 1.0)
    # a prepared act a person confirmed later: ONE write (the fold merges, never twice)
    v = sc.score(case, RunRecord(turn(
        [{"action": "a", "outcome": "prepared", "invocation_hash": "h1", "checks": HOLD}],
        {"h1": {"action": "a", "outcome": "performed", "checks": HOLD}})))
    check("a prepared act later confirmed is counted once", v.score == 1.0 and "1 of 1" in v.explanation)
    v = sc.score(case, RunRecord(turn([], {"h9": {"action": "a", "outcome": "performed", "checks": BROKE}})))
    check("a confirmation alone (REST/Inbox) is read through the same fold", v.score == 0.0)
    import inspect
    from trusted_data_agent.core import action_perform as _ap
    _prep = inspect.getsource(_ap.prepare_action)
    check("a PREPARED act's record carries the checks the database evaluated (interactive turns are scored)",
          '"outcome": "prepared"' in _prep
          and '"checks": o.checks,' in _prep[_prep.index('"outcome": "prepared"'):])
    v = sc.score(case, RunRecord(turn([{"action": "a", "outcome": "prepared", "invocation_hash": "h", "checks": HOLD}])))
    check("a prepared write whose checks held scores 1.0", v.score == 1.0)
    check("registered for the action-capable classes",
          any(s.dimension == "action_safety" for s in scorers_for("conversation_with_tools"))
          and any(s.dimension == "action_safety" for s in scorers_for("tool_enabled"))
          and not any(s.dimension == "action_safety" for s in scorers_for("rag_focused")))


def main():
    print("== WS3 reliability & output-integrity scorers ==")
    for t in (t_latency, t_output_sanity, t_tool_reliability, t_scope_and_economy,
              t_numeric_fidelity, t_context_health, t_focus_genie, t_dispatch, t_harmonized_kpis,
              t_honest_absence, t_surface_violation, t_action_safety):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed



# ── honest_absence — the inverse-polarity truthfulness check ──────────────────
def t_honest_absence():
    """The only check that asks whether what the answer DENIES is established."""
    from trusted_data_agent.eval.scorers.reliability import HonestAbsenceScorer
    sc = HonestAbsenceScorer()
    case = EvalCase(id="c", query="q", profile_class="tool_enabled")

    def turn(answer, calls, **extra):
        return {"profile_type": "tool_enabled", "final_summary_text": answer,
                "execution_trace": [{"action": {"tool_name": "base_readQuery",
                                                "arguments": {"sql": c["sql"]}},
                                     "result": c["result"]} for c in calls], **extra}

    FAIL = {"sql": "SELECT * FROM airport.station", "result": {"status": "error", "error": "3802"}}
    OK = {"sql": "SELECT * FROM airport.station", "result": {"status": "success", "results": []}}

    # The canonical failure: every query failed, the answer reports absence anyway.
    v = sc.score(case, RunRecord(turn("There are no airport stations in the database.", [FAIL])))
    check("all queries failed + absence claimed -> fail", (v.passed) == (False))
    check("the failure explains what was not established", "never established" in v.explanation)

    # THE LOAD-BEARING CASE: a legitimate empty result is not a failure. A query that
    # SUCCEEDED with zero rows establishes the absence — treating it as an over-claim
    # would punish the agent for being right.
    v = sc.score(case, RunRecord(turn("There are no airport stations.", [OK])))
    check("succeeded-with-zero-rows -> pass, never a failure", (v.passed) == (True))

    # Mixed: one succeeded, so the absence IS established.
    v = sc.score(case, RunRecord(turn("There are no airport stations.", [FAIL, OK])))
    check("any successful query backs the absence claim", (v.passed) == (True))

    # Abstains, and for the RIGHT reason each time — an abstain that fires on the wrong
    # condition silently removes the check.
    v = sc.score(case, RunRecord(turn("Here are 4 rows of data.", [FAIL])))
    check("no absence claim -> abstain", (v.passed) == (None))
    check("abstain names the reason", "no non-existence claim" in v.explanation)
    v = sc.score(case, RunRecord(turn("There are no airport stations.", [])))
    check("no data queries -> abstain", (v.passed) == (None))
    check("abstain names the reason", "no raw-SQL data queries" in v.explanation)

    # STAMP PRECEDENCE. In enforce the gate REWRITES the answer, so the delivered text
    # carries no absence claim. Re-deriving from it would score the turn CLEAN — the
    # agent over-claimed, the gate caught it, and the record would say nothing happened.
    rewritten = turn("I could not complete the lookup; the query failed.", [FAIL],
                     honesty={"decision": "block", "reason": "gate reason", "mode": "enforce"})
    v = sc.score(case, RunRecord(rewritten))
    check("a rewritten answer still scores as the failure it was", (v.passed) == (False))
    check("evidence names the gate decision", "gate decision: block" in (v.evidence or []))

    # Applicability mirrors the runtime gate exactly — Ideate and Focus run no raw SQL.
    check("applies_to mirrors the classes the runtime gate runs on", (set(sc.applies_to)) == ({"tool_enabled", "conversation_with_tools"}))

    # Boolean: no threshold to configure, so it must never carry one.
    v = sc.score(case, RunRecord(turn("There are no airport stations.", [FAIL])))
    check("a yes/no check carries no threshold", (v.threshold) == (None))

    # Defensive across engine shapes: the ReAct result shape must work identically.
    react = {"profile_type": "conversation_with_tools",
             "final_summary_text": "There are no airport stations.",
             "conversation_agent_events": [
                 {"type": "tool_start", "tool_name": "base_readQuery",
                  "tool_input": {"sql": "SELECT 1 FROM airport.station"}},
                 {"type": "tool_end", "tool_name": "base_readQuery",
                  "output": {"success": False, "error": "3802"}}]}
    v = sc.score(case, RunRecord(react))
    check("ReAct shape is read without throwing", v.passed in (False, None))

    # Never throws on a degenerate turn.
    for bad in ({}, {"profile_type": "tool_enabled"}, {"final_summary_text": None}):
        sc.score(case, RunRecord(bad))
    check("degenerate turns abstain rather than throw", True)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
