"""WS3 W3.0 + W3.1 — eval store + deterministic profile-aware scorers.

CI-pure (no LLM): proves the deterministic trajectory/quality scorers across all
five profile classes (Ideate / Focus / Optimize / Coordinate / ReAct) plus the
store round-trip and profile-aware scorer dispatch.

Run:
    PYTHONPATH=src python test/test_eval_deterministic.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase, RunRecord  # noqa: E402
from trusted_data_agent.eval.scorer import scorers_for  # noqa: E402
from trusted_data_agent.eval import scorers as _scorers  # noqa: E402,F401 (registers)
from trusted_data_agent.eval.store import EvalStore  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def run_scorers(case: EvalCase, turn: dict) -> dict:
    """Run every applicable scorer; return {dimension: Verdict}."""
    rr = RunRecord(turn)
    out = {}
    for s in scorers_for(case.profile_class):
        out[s.dimension] = s.score(case, rr)
    return out


def passed(v):  # passed True
    return v is not None and v.passed is True


def failed(v):  # passed False
    return v is not None and v.passed is False


# ── synthetic traces per profile class ────────────────────────────────────────
OPTIMIZE_GOOD = {
    "profile_type": "tool_enabled", "status": "success", "isValid": True,
    "final_summary_text": "Found 15 products with low inventory.",
    "raw_llm_plan": {"phases": [{"relevant_tools": ["base_readQuery"]},
                                {"relevant_tools": ["TDA_FinalReport"]}]},
    "execution_trace": [
        {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT ..."}},
         "result": {"tool_output_summary": {"status": "success", "results": [{"x": 1}]}}},
        {"action": {"tool_name": "TDA_FinalReport", "arguments": {}},
         "result": {"tool_output_summary": {"status": "success"}}},
    ],
    "provenance_chain": [{"step_type": "query_intake"}, {"step_type": "strategic_plan"},
                         {"step_type": "tool_call"}, {"step_type": "synthesis"}],
    "provenance_meta": {"sealed": True, "chain_tip_hash": "abc123def456"},
    "turn_cost": 0.01, "turn_input_tokens": 1000, "turn_output_tokens": 200,
}
OPTIMIZE_BAD = {
    "profile_type": "tool_enabled", "status": "success", "isValid": True,
    "final_summary_text": "Deleted the rows.",
    "raw_llm_plan": {"phases": [{"relevant_tools": ["base_readQuery"]}]},
    "execution_trace": [
        {"action": {"tool_name": "base_writeQuery", "arguments": {"sql": "DELETE ..."}},
         "result": {"tool_output_summary": {"status": "success"}}},
    ],
    "provenance_chain": [{"step_type": "tool_call"}, {"step_type": "error:execution_failure"}],
    "provenance_meta": {"sealed": True}, "turn_cost": 0.30,
}
FOCUS_GOOD = {
    "profile_type": "rag_focused", "status": "success", "isValid": True,
    "final_summary_text": "The refund policy allows returns within 30 days of purchase.",
    "knowledge_retrieval_event": {"documents": [
        {"text": "Refund policy: returns are accepted within 30 days of purchase for unused items."}]},
    "provenance_chain": [{"step_type": "rag_search"}, {"step_type": "synthesis"}],
    "provenance_meta": {"sealed": True}, "turn_cost": 0.004,
}
FOCUS_HALLUCINATION = {
    "profile_type": "rag_focused", "status": "success", "isValid": True,
    "final_summary_text": "Quantum entanglement enables faster-than-light teleportation of refunds.",
    "knowledge_retrieval_event": {"documents": [
        {"text": "Refund policy: returns are accepted within 30 days of purchase for unused items."}]},
    "provenance_meta": {"sealed": True}, "turn_cost": 0.004,
}
GENIE_GOOD = {
    "profile_type": "genie", "status": "success", "isValid": True,
    "final_summary_text": "Synthesized finance + legal view.",
    "genie_metadata": {"slaves_invoked": [{"profile_tag": "FINANCE"}, {"profile_tag": "LEGAL"}]},
    "provenance_chain": [{"step_type": "child_dispatch"}, {"step_type": "coordinator_synthesis"}],
    "provenance_meta": {"sealed": True}, "turn_cost": 0.05,
}
GENIE_REDUNDANT = {
    "profile_type": "genie", "status": "success", "isValid": True,
    "final_summary_text": "...",
    "genie_metadata": {"slaves_invoked": [{"profile_tag": "FINANCE"}, {"profile_tag": "FINANCE"}]},
    "provenance_meta": {"sealed": True}, "turn_cost": 0.05,
}
IDEATE_GOOD = {
    "profile_type": "llm_only", "status": "success", "isValid": True,
    "final_summary_text": "Paris is the capital of France.",
    "provenance_chain": [{"step_type": "llm_call"}], "provenance_meta": {"sealed": True},
    "turn_cost": 0.001,
}
REACT_GOOD = {
    "profile_type": "conversation_with_tools", "status": "success", "isValid": True,
    "final_summary_text": "The weather is sunny.",
    "execution_trace": [
        {"action": {"tool_name": "get_weather", "arguments": {"city": "Paris"}},
         "result": {"tool_output_summary": {"status": "success"}}}],
    "provenance_chain": [{"step_type": "agent_tool_call"}], "provenance_meta": {"sealed": True},
    "turn_cost": 0.002,
}


# ── tests ─────────────────────────────────────────────────────────────────────
def t_dispatch():
    focus_dims = {s.dimension for s in scorers_for("rag_focused")}
    opt_dims = {s.dimension for s in scorers_for("tool_enabled")}
    genie_dims = {s.dimension for s in scorers_for("genie")}
    check("Focus suite has grounding, not tool_subset",
          "grounded_overlap" in focus_dims and "tool_subset" not in focus_dims)
    check("Optimize suite has tool_subset + plan_adherence",
          {"tool_subset", "plan_adherence"} <= opt_dims)
    check("Genie suite has routing, not retrieval",
          "routing" in genie_dims and "retrieval_performed" not in genie_dims)
    check("cross-cutting scorers in every class",
          all({"completed", "cost", "safety", "provenance_integrity"} <= {s.dimension for s in scorers_for(pc)}
              for pc in ("llm_only", "rag_focused", "tool_enabled", "genie", "conversation_with_tools")))


def t_optimize():
    case = EvalCase(id="o1", query="low inventory", profile_class="tool_enabled",
                    assertions={"require_tools_subset": ["base_readQuery"],
                                "must_not_call": ["base_writeQuery"], "max_cost_usd": 0.05})
    g = run_scorers(case, OPTIMIZE_GOOD)
    check("Optimize good: completed", passed(g["completed"]))
    check("Optimize good: tool_subset pass", passed(g["tool_subset"]))
    check("Optimize good: must_not_call pass", passed(g["must_not_call"]))
    check("Optimize good: cost under ceiling", passed(g["cost"]))
    check("Optimize good: chain integrity", passed(g["provenance_integrity"]))
    check("Optimize good: plan adherence ~1.0", g["plan_adherence"].score == 1.0)

    b = run_scorers(case, OPTIMIZE_BAD)
    check("Optimize bad: tool_subset FAIL (missing readQuery)", failed(b["tool_subset"]))
    check("Optimize bad: must_not_call FAIL (writeQuery)", failed(b["must_not_call"]))
    check("Optimize bad: safety FAIL (forbidden + error step)", failed(b["safety"]))
    check("Optimize bad: cost over ceiling", failed(b["cost"]))


def t_focus():
    case = EvalCase(id="f1", query="refund policy", profile_class="rag_focused",
                    assertions={"expect_retrieval": True, "min_grounded": 0.4})
    g = run_scorers(case, FOCUS_GOOD)
    check("Focus good: retrieval performed", passed(g["retrieval_performed"]))
    check("Focus good: grounded overlap pass", passed(g["grounded_overlap"]))
    h = run_scorers(case, FOCUS_HALLUCINATION)
    check("Focus hallucination: grounded overlap FAIL", failed(h["grounded_overlap"]))
    check("Focus hallucination: still retrieved", passed(h["retrieval_performed"]))



def t_grounded_overlap_measures_the_model_not_the_platform():
    """Campaign finding C5 — WHAT the overlap check is allowed to measure.

    Two live defects, both making the grounding station report a shortfall that was
    not the model's:

    (1) When the gate BLOCKS, the delivered text is Uderia's own withholding notice —
        and that notice QUOTES the unsupported spans it is reporting, so it is
        engineered to fail an overlap check. The gate acting manufactured a SECOND
        grounding failure on top of the one it correctly reported.
    (2) The support set was the retrieved documents ALONE, so an answer echoing the
        user's own terms was scored as if it had invented them. `corpus.assess` has
        always included the question; this scorer did not.
    """
    case = EvalCase(id="c5", query="refund policy", profile_class="rag_focused",
                    assertions={"min_grounded": 0.4})
    docs = {"documents": [{"text": "Refund policy: returns accepted within 30 days."}]}

    blocked = {"profile_type": "rag_focused", "status": "success", "isValid": True,
               "final_summary_text": "This answer was withheld after review: the check "
                                     "flagged these statements as unmatched: `README.md`, `1`.",
               "knowledge_retrieval_event": docs,
               "grounding": {"decision": "block", "ran": True, "acted": True},
               "provenance_meta": {"sealed": True}}
    v = run_scorers(case, blocked)["grounded_overlap"]
    check("a withheld turn does not score the platform's own notice", v.score is None)
    check("...and says why", "not a model claim" in (v.explanation or ""))
    # An ANNOTATED answer is the model's text with the gate's note appended; the scorer grades
    # the model's words and the note is dropped (prod 510f7011: three modules "abstained" on a
    # correct answer, hiding the reading that would have shown the annotation was wrong).
    note = "Some statements could not be matched to the knowledge base (unsupported ≠ false): `-1`."
    annotated = {"profile_type": "rag_focused", "status": "success", "isValid": True,
                 "final_summary_text": "Returns are accepted within 30 days.\n\n> " + note,
                 "knowledge_retrieval_event": docs,
                 "grounding": {"decision": "annotate", "annotation_md": note, "ran": True, "acted": True},
                 "provenance_meta": {"sealed": True}}
    from trusted_data_agent.eval.types import RunRecord as _RR
    check("an annotated turn's graded text is the model's words, without the appended note",
          _RR(annotated).final_answer_text == "Returns are accepted within 30 days.")
    va = run_scorers(case, annotated)["grounded_overlap"]
    check("...and the overlap IS graded (it used to abstain like a withheld turn)", va.score is not None and va.score >= 0.4)

    rewritten = dict(blocked, grounding={"decision": "pass", "ran": True},
                     honesty={"decision": "block", "ran": True, "acted": True})
    check("a honesty-rewritten turn likewise abstains",
          run_scorers(case, rewritten)["grounded_overlap"].score is None)

    # The question joins the support set: terms the USER supplied are not inventions.
    echo = {"profile_type": "rag_focused", "status": "success", "isValid": True,
            "final_summary_text": "I cannot reach the finance_ledger database you mentioned.",
            "user_query": "Query the finance_ledger database please.",
            "knowledge_retrieval_event": docs, "provenance_meta": {"sealed": True}}
    with_q = run_scorers(case, echo)["grounded_overlap"].score
    no_q = run_scorers(case, {k: v for k, v in echo.items() if k != "user_query"})["grounded_overlap"].score
    check("echoing the question is not scored as hallucination", with_q > no_q)

    # And the reason the claims-gate idea was reverted must stay true: a fluent
    # fabrication carrying no number, quote or identifier is STILL caught.
    check("a fluent hallucination with no hard tokens still fails",
          failed(run_scorers(case, FOCUS_HALLUCINATION)["grounded_overlap"]))


def t_genie():
    case = EvalCase(id="g1", query="margin review", profile_class="genie",
                    assertions={"expect_slaves": ["FINANCE"], "max_slaves": 3})
    g = run_scorers(case, GENIE_GOOD)
    check("Genie good: routing pass (FINANCE consulted)", passed(g["routing"]))
    check("Genie good: coordination efficient", passed(g["coordination_efficiency"]))
    r = run_scorers(case, GENIE_REDUNDANT)
    check("Genie redundant: coordination efficiency FAIL (dupe expert)", failed(r["coordination_efficiency"]))


def t_ideate():
    case = EvalCase(id="i1", query="capital of France", profile_class="llm_only",
                    assertions={"must_contain": ["Paris"], "must_not_contain": ["London"]})
    g = run_scorers(case, IDEATE_GOOD)
    check("Ideate good: answer present", passed(g["answer_present"]))
    check("Ideate good: format constraints pass", passed(g["format"]))
    bad_case = EvalCase(id="i2", query="x", profile_class="llm_only",
                        assertions={"must_contain": ["London"]})
    b = run_scorers(bad_case, IDEATE_GOOD)
    check("Ideate format FAIL when required substring missing", failed(b["format"]))


def t_react():
    case = EvalCase(id="r1", query="weather", profile_class="conversation_with_tools",
                    assertions={"require_tools_subset": ["get_weather"],
                                "must_not_call": ["delete_account"]})
    g = run_scorers(case, REACT_GOOD)
    check("ReAct good: tool_subset pass", passed(g["tool_subset"]))
    check("ReAct good: must_not_call pass", passed(g["must_not_call"]))

    # Real ReAct shape: tool calls live in conversation_agent_events (execution_trace
    # is empty) — the tool scorers must still see them.
    from trusted_data_agent.eval.types import RunRecord
    react_turn = {"profile_type": "conversation_with_tools", "status": "success",
                  "execution_trace": [],
                  "conversation_agent_events": [
                      {"type": "conversation_tool_invoked", "payload": {"tool_name": "get_weather", "arguments": {"city": "NYC"}}},
                      {"type": "conversation_tool_completed", "payload": {"tool_name": "get_weather", "success": True}},
                      {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {}}},
                      {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": False}}]}
    rr = RunRecord(react_turn)
    check("ReAct: tool_calls read from conversation events", rr.tool_names() == ["get_weather", "base_readQuery"])
    check("ReAct: failed tool -> self_correction count", rr.self_correction_count == 1)
    g2 = run_scorers(case, react_turn)
    check("ReAct conv-events: tool_subset still passes", passed(g2["tool_subset"]))


def t_store():
    tmp = Path(tempfile.mkdtemp(prefix="tda_eval_")) / "eval.db"
    store = EvalStore(tmp)
    store.create_dataset("ds1", "Core suite")
    store.add_case(EvalCase(id="c1", query="q1", profile_class="tool_enabled", dataset_id="ds1"))
    v1 = store.get_dataset("ds1")["version"]
    store.add_case(EvalCase(id="c2", query="q2", profile_class="rag_focused", dataset_id="ds1"))
    v2 = store.get_dataset("ds1")["version"]
    check("store: dataset created + hashed", bool(store.get_dataset("ds1")["content_hash"]))
    check("store: version bumps on content change", v2 > v1)
    cases = store.list_cases("ds1")
    check("store: cases round-trip", {c.id for c in cases} == {"c1", "c2"})
    # run + scores
    store.create_run("run1", "ds1", v2, k=2)
    case = store.get_case("c1")
    rr = RunRecord(OPTIMIZE_GOOD)
    for s in scorers_for(case.profile_class):
        store.add_score("run1", case.id, 0, s.score(case, rr))
    store.finish_run("run1", {"pass_rate": 1.0})
    scores = store.scores_for_run("run1")
    check("store: scores persisted", len(scores) > 0)
    check("store: run finished w/ summary", store.get_run("run1")["summary"]["pass_rate"] == 1.0)


def t_real_shapes():
    """Regression: real turn shapes that previously crashed an accessor (and thus
    the whole online hook) — across profile classes."""
    from trusted_data_agent.eval.types import RunRecord
    # Optimize: raw_llm_plan / original_plan is a LIST of phase dicts, not {phases:[…]}.
    opt = {"profile_type": "tool_enabled", "status": "success", "isValid": True,
           "final_summary_text": "ok",
           "raw_llm_plan": [{"phase": 1, "relevant_tools": ["base_readQuery"]}],
           "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {}},
                                "result": {"tool_output_summary": {"status": "success"}}}],
           "provenance_meta": {"sealed": True}}
    check("list-plan: planned_tools (no crash)", RunRecord(opt).planned_tools() == ["base_readQuery"])
    g = run_scorers(EvalCase(id="ls", query="q", profile_class="tool_enabled"), opt)
    check("list-plan: full tool_enabled suite runs", "plan_adherence" in g and "tool_subset" in g)
    # Focus / Coordinate / all: list-shaped metadata must not crash their accessors.
    rr = RunRecord({"knowledge_retrieval_event": [{"x": 1}], "genie_metadata": [{"y": 2}],
                    "provenance_meta": [1, 2], "provenance_chain": ["notadict", {"step_type": "s"}]})
    check("list knowledge_event -> [] docs", rr.retrieved_documents() == [])
    check("list genie_metadata -> [] slaves", rr.slaves_invoked() == [])
    check("list provenance_meta -> {} ", rr.provenance_meta == {})
    check("non-dict provenance steps skipped", rr.step_types() == ["s"] and rr.self_correction_count == 0)
    # self-correction is surfaced as a METRIC carrying the count, detected from
    # provenance error/self_correction steps OR errored tool calls in the trace.
    err = {"profile_type": "tool_enabled", "status": "success",
           "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {}},
                                "result": {"tool_output_summary": {"status": "error"}}},
                               {"action": {"tool_name": "base_readQuery", "arguments": {}},
                                "result": {"tool_output_summary": {"status": "success"}}}],
           "provenance_chain": [{"step_type": "self_correction"}]}
    check("self_correction counts provenance + trace (max=1)", RunRecord(err).self_correction_count == 1)
    sc = run_scorers(EvalCase(id="sc", query="q", profile_class="tool_enabled"), err)
    check("self_correction is a METRIC", sc["self_correction"].kind.value == "metric")
    check("self_correction chip carries the count", sc["self_correction"].score == 1.0)
    # applies only to the tool-using classes — never Ideate/Focus/Coordinate.
    from trusted_data_agent.eval.scorer import scorers_for
    for cls in ("llm_only", "rag_focused", "genie"):
        check(f"self_correction NOT in {cls} suite",
              "self_correction" not in {s.dimension for s in scorers_for(cls)})
    check("self_correction IN conversation_with_tools suite",
          "self_correction" in {s.dimension for s in scorers_for("conversation_with_tools")})


def t_citations():
    """The citations detector recognizes Uderia's CANONICAL citation grammar (the
    answer's own Sources block + (Collection: ...) markers) — the prod-verified format —
    alongside generic academic forms; the always-attached Source Documents panel is
    deliberately NOT what it measures (that proves retrieval, not attribution)."""
    from trusted_data_agent.eval.scorers.deterministic import CitationPresentScorer
    from trusted_data_agent.eval.types import EvalCase, RunRecord
    s = CitationPresentScorer()
    case = EvalCase(id="t", query="q", profile_class="rag_focused",
                    assertions={"require_citations": 1})
    prod_style = ("Trust transforms an agent into a trusted one.\n\n***\n\n**Sources:**\n"
                  "*   *Uderia Security Architecture (Collection: Uderia Documentation)*")
    check("Uderia Sources block passes (prod-verified format)",
          s.score(case, RunRecord({"final_summary_text": prod_style})).passed is True)
    check("plain Sources: line passes",
          s.score(case, RunRecord({"final_summary_text": "Answer.\nSources:\n- Doc A"})).passed is True)
    check("numeric [1] passes",
          s.score(case, RunRecord({"final_summary_text": "As shown [1], x."})).passed is True)
    check("no attribution fails",
          s.score(case, RunRecord({"final_summary_text": "Retention is 90 days."})).passed is False)
    check("mid-sentence 'sources' word alone does not pass",
          s.score(case, RunRecord({"final_summary_text": "Many sources of error exist."})).passed is False)
    check("flag off abstains",
          s.score(EvalCase(id="t", query="q", profile_class="rag_focused"),
                  RunRecord({"final_summary_text": prod_style})).passed is None)


def main():
    print("== WS3 deterministic eval (W3.0 + W3.1) ==")
    for t in (t_dispatch, t_optimize, t_focus, t_grounded_overlap_measures_the_model_not_the_platform, t_genie, t_ideate, t_react, t_real_shapes, t_store, t_citations,
              t_one_scorer_cannot_lose_the_pass):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


def t_one_scorer_cannot_lose_the_pass():
    """A raising scorer must not take every other verdict with it.

`grounded_overlap` raised a NameError on the unconfigured-threshold path (`_metric`
    lives in reliability.py, not deterministic.py), and the bare list comprehension in
    `score_run_record` would have lost all 20 verdicts with it. Because the online hook is
    fire-and-forget, that failure has no visible symptom beyond a Trust Circle that never
    settles — so it is exactly the class of hole that stays open for a long time.

    Latent, not hit: every test set a threshold, and the path is reachable only where
    `min_grounded_overlap` is unset.
    """
    import asyncio, logging
    from trusted_data_agent.eval import runner as R, scorer as S

    turn = dict(FOCUS_GOOD)
    case = EvalCase(id="iso", query="q", profile_class="rag_focused", assertions={})
    clean = asyncio.run(R.score_run_record(case, turn))
    check("a clean turn scores", len(clean) > 5)

    victim = S.scorers_for("rag_focused")[0]
    orig = victim.score
    victim.score = lambda c, r: (_ for _ in ()).throw(RuntimeError("boom"))
    logging.disable(logging.CRITICAL)
    try:
        vs = asyncio.run(R.score_run_record(case, turn))
        check("one broken scorer does not lose the pass", len(vs) == len(clean))
        named = [v for v in vs if v.dimension == victim.dimension]
        # A dimension that disappears silently is indistinguishable from one that was
        # never applicable — the reading this codebase bans everywhere else.
        check("…and the broken one abstains rather than vanishing",
              len(named) == 1 and named[0].passed is None)
        check("…NAMING the failure", "could not be measured" in (named[0].explanation or ""))
    finally:
        victim.score = orig
        logging.disable(logging.NOTSET)

    # The specific bug: the unconfigured-threshold path of grounded_overlap.
    gv = next(o for o in S.scorers_for("rag_focused")
              if o.dimension == "grounded_overlap")
    v = gv.score(EvalCase(id="nb", query="q", profile_class="rag_focused", assertions={}),
                 RunRecord(FOCUS_GOOD))
    check("grounded_overlap survives an UNCONFIGURED threshold", v is not None)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
