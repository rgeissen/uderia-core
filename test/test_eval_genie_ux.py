"""WS3 — genie coordinator/expert (master/slave) scoring + Agent Performance UX
backend. CI-pure (no LLM, no server).

Covers:
  - RunRecord genie accessors: final_response fallback, top-level slaves_invoked,
    genie_rollup, and SELF-cost turn_cost_usd/total_tokens (rollup is NOT the metric).
  - Genie scorers: answer_present via final_summary_text, cost/tokens self-cost +
    delegated annotation evidence, expert_success / coordination populate.
  - analysis.agent_runs: coordinator ledger + nested master/slave tree (expert
    matched to its own span by session id), synthetic fallback, self-cost no
    double-count, expert ledger.
  - online_quality_by_agent extensions: avg_cost/latency/tokens/tasks +
    direct_turns/expert_turns.
  - build_quality_span: agent.invocation / agent.parent_session_id / eval.experts.
  - REST surface: evals_agent_runs defined.

Run:
    PYTHONPATH=src python test/test_eval_genie_ux.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase, RunRecord, Verdict, ScoreKind  # noqa: E402
from trusted_data_agent.eval.scorer import scorers_for  # noqa: E402
from trusted_data_agent.eval import scorers as _scorers  # noqa: E402,F401 (registers)
from trusted_data_agent.eval.analysis import agent_runs, online_quality_by_agent, dedupe_spans  # noqa: E402
from trusted_data_agent.eval.online import build_quality_span  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _coord_turn(**over):
    """A genie coordinator turn as persisted by coordinate_engine (pass-through to
    one expert): self-cost on the turn, inclusive figure in genie_rollup."""
    t = {
        "profile_type": "genie", "status": "success", "isValid": True,
        "final_summary_text": "The roadmap includes X, Y and Z.",
        "turn_cost": 0.0021, "turn_input_tokens": 8668, "turn_output_tokens": 49,
        "slaves_invoked": [{"profile_tag": "CTF_SME", "slave_session_id": "child1",
                            "status": "success", "input_tokens": 10532, "output_tokens": 628,
                            "cost_usd": 0.0013, "profile_type": "rag_focused",
                            "started_at": 100.0, "completed_at": 103.4, "duration_ms": 3400}],
        "genie_rollup": {"coordinator_cost_usd": 0.0021, "expert_cost_usd": 0.0013,
                         "expert_count": 1, "coordinator_input_tokens": 8668,
                         "coordinator_output_tokens": 49, "expert_input_tokens": 10532,
                         "expert_output_tokens": 628, "input_tokens": 19200,
                         "output_tokens": 677, "cost_usd": 0.0034},
    }
    t.update(over)
    return t


# ── RunRecord genie accessors ─────────────────────────────────────────────────
def t_runrecord_accessors():
    r = RunRecord(_coord_turn())
    check("answer: final_summary_text used", r.final_answer_text.startswith("The roadmap"))
    r2 = RunRecord({"final_response": "legacy answer", "status": "success"})
    check("answer: final_response fallback", r2.final_answer_text == "legacy answer")
    check("answer: empty when none", RunRecord({}).final_answer_text == "")
    check("slaves_invoked top-level read", len(r.slaves_invoked()) == 1
          and r.slaves_invoked()[0]["profile_tag"] == "CTF_SME")
    check("genie_rollup property", r.genie_rollup.get("expert_count") == 1)
    # SELF-cost: the scored metric is coordinator-only, NOT the inclusive rollup.
    check("turn_cost_usd is self-cost (not rollup)", r.turn_cost_usd == 0.0021)
    check("total_tokens is self-cost (not rollup)", r.total_tokens == 8668 + 49)


# ── genie scorers ─────────────────────────────────────────────────────────────
def _score(turn, assertions=None):
    case = EvalCase(id="g", query="q", profile_class="genie", assertions=assertions or {})
    rr = RunRecord(turn)
    return {s.dimension: s.score(case, rr) for s in scorers_for("genie")}


def t_genie_scorers():
    v = _score(_coord_turn(), {"max_cost_usd": 0.05, "max_tokens": 50000})
    check("answer_present passes (final_summary_text)", v["answer_present"].passed is True)
    check("no gating failure on a clean coordinator turn",
          all(x.passed is not False for x in v.values()))
    # cost = coordinator self-cost; evidence carries delegated + turn total annotation.
    check("cost metric is coordinator self-cost", abs(v["cost"].score - 0.0021) < 1e-9)
    check("cost evidence: delegated expert + turn total",
          any("expert" in e for e in v["cost"].evidence)
          and any("turn total" in e for e in v["cost"].evidence))
    check("tokens metric is coordinator self-cost", v["tokens"].score == 8668 + 49)
    check("tokens evidence: delegated annotation", any("expert" in e for e in v["tokens"].evidence))
    check("expert_success passes (all experts ok)", v["expert_success"].passed is True)
    check("coordination_efficiency populated (not abstain on count)",
          v["coordination_efficiency"].score == 1)
    # a failed expert flips expert_success.
    bad = _coord_turn()
    bad["slaves_invoked"] = [dict(bad["slaves_invoked"][0], status="error")]
    vb = _score(bad)
    check("expert_success fails when an expert errored", vb["expert_success"].passed is False)
    # no experts at all → coordination scorers abstain (honest), answer still present.
    none = _coord_turn(); none["slaves_invoked"] = []
    vn = _score(none)
    check("expert_success abstains with no experts", vn["expert_success"].passed is None)


# ── agent_runs: ledger + master/slave tree ────────────────────────────────────
def _span(tag, sess, turn, inv, cost, lat, tok, fails, ns=100.0, experts=None,
          parent=None, verds=None, pclass=None):
    a = {"agent.profile_tag": tag, "session.id": sess, "turn.id": turn,
         "agent.invocation": inv, "eval.profile_class": pclass or "genie",
         "eval.gating_failures": fails, "eval.dim.cost.score": cost,
         "eval.dim.latency.score": lat, "eval.dim.tokens.score": tok}
    if experts is not None:
        a["eval.experts"] = experts
    if parent:
        a["agent.parent_session_id"] = parent
    ev = [{"name": "verdict", "attributes": v} for v in (verds or [])]
    return {"start_time_unix_nano": int(ns * 1e9), "attributes": a, "events": ev}


def t_agent_runs_tree():
    expert = _span("CTF_SME", "child1", 1, "expert", 0.0013, 3400, 11160, 0, parent="master1",
                   pclass="rag_focused",
                   verds=[{"dimension": "groundedness", "kind": "det", "passed": True},
                          {"dimension": "answer_present", "kind": "det", "passed": True}])
    coord = _span("VAT", "master1", 1, "direct", 0.0021, 7300, 8717, 0,
                  experts=[{"profile_tag": "CTF_SME", "slave_session_id": "child1",
                            "status": "success", "cost_usd": 0.0013, "input_tokens": 10532,
                            "output_tokens": 628, "duration_ms": 3400,
                            "started_at": 100.0, "completed_at": 103.4}],
                  verds=[{"dimension": "routing", "kind": "det", "passed": True},
                         {"dimension": "cost", "kind": "metric", "passed": True, "score": 0.0021}])
    spans = dedupe_spans([coord, expert])

    vat = agent_runs(spans, "VAT")
    check("agent_runs VAT: one run", vat["count"] == 1 and len(vat["runs"]) == 1)
    run = vat["runs"][0]
    check("coordinator run: self-cost 0.0021", run["cost"] == 0.0021)
    check("coordinator run: rollup_cost = self + expert", abs(run["rollup_cost"] - 0.0034) < 1e-9)
    check("coordinator run: expert_count 1", run["expert_count"] == 1)
    check("coordinator run: own verdicts present", {v["dimension"] for v in run["verdicts"]} == {"routing", "cost"})
    kid = run["experts"][0]
    check("expert child: matched real span (not synthetic)", not kid.get("synthetic"))
    check("expert child: own verdicts", {v["dimension"] for v in kid["verdicts"]} == {"groundedness", "answer_present"})
    check("expert child: own self-cost 0.0013", kid["cost"] == 0.0013)
    check("expert child: timing for waterfall", kid["duration_ms"] == 3400 and kid["started_at"] == 100.0)

    ctf = agent_runs(spans, "CTF_SME")
    check("agent_runs CTF_SME: own run as expert", ctf["runs"][0]["invocation"] == "expert"
          and {v["dimension"] for v in ctf["runs"][0]["verdicts"]} == {"groundedness", "answer_present"})
    check("self-cost: portfolio sum has no double-count",
          abs(run["cost"] + ctf["runs"][0]["cost"] - 0.0034) < 1e-9)


def t_agent_runs_synthetic():
    # coordinator references an expert that was NOT scored as its own span → synthetic node.
    coord = _span("VAT", "m2", 1, "direct", 0.0021, 7300, 8717, 0,
                  experts=[{"profile_tag": "GHOST", "slave_session_id": "missing",
                            "status": "success", "cost_usd": 0.0009, "input_tokens": 100,
                            "output_tokens": 20, "duration_ms": 1200, "profile_type": "llm_only"}])
    run = agent_runs([coord], "VAT")["runs"][0]
    kid = run["experts"][0]
    check("synthetic expert node when span missing", kid.get("synthetic") is True)
    check("synthetic node carries summary cost", kid["cost"] == 0.0009 and kid["profile_tag"] == "GHOST")
    check("synthetic node tokens summed from summary", kid["tokens"] == 120)


# ── online_quality_by_agent extensions ────────────────────────────────────────
def t_by_agent_extensions():
    spans = [
        _span("VAT", "m1", 1, "direct", 0.0021, 7000, 8000, 0,
              verds=[{"dimension": "tasks", "kind": "metric", "passed": None, "score": 2}]),
        _span("VAT", "m2", 2, "direct", 0.0019, 6000, 9000, 0),
        _span("CTF_SME", "c1", 1, "expert", 0.0013, 3000, 11000, 0, pclass="rag_focused"),
    ]
    # tasks composition needs eval.task.* attrs; add one for VAT m1
    spans[0]["attributes"]["eval.task.reasoning"] = 1
    spans[0]["attributes"]["eval.task.coordination"] = 1
    by = online_quality_by_agent(spans)
    check("by_agent: VAT direct_turns 2 / expert_turns 0",
          by["VAT"]["direct_turns"] == 2 and by["VAT"]["expert_turns"] == 0)
    check("by_agent: CTF_SME expert_turns 1", by["CTF_SME"]["expert_turns"] == 1 and by["CTF_SME"]["direct_turns"] == 0)
    check("by_agent: VAT avg_cost mean of 0.0021,0.0019", abs(by["VAT"]["avg_cost"] - 0.0020) < 1e-9)
    check("by_agent: VAT avg_latency mean", by["VAT"]["avg_latency_ms"] == 6500)
    check("by_agent: VAT avg_tokens mean", by["VAT"]["avg_tokens"] == 8500)
    check("by_agent: VAT avg_tasks from composition", by["VAT"]["avg_tasks"] == 2.0)


# ── build_quality_span attrs ──────────────────────────────────────────────────
def t_span_attrs():
    v = [Verdict(dimension="answer_present", kind=ScoreKind.DETERMINISTIC, passed=True)]
    expert_span = build_quality_span(session_id="child1", turn_id=1, profile_class="rag_focused",
                                     verdicts=v, sampled=True, anomalous=False, attestation={},
                                     started_at=0.0, profile_tag="CTF_SME",
                                     invocation="expert", parent_session_id="master1")
    a = expert_span["attributes"]
    check("span: invocation tag", a["agent.invocation"] == "expert")
    check("span: parent_session_id", a["agent.parent_session_id"] == "master1")
    experts = [{"profile_tag": "CTF_SME", "slave_session_id": "child1", "status": "success"}]
    coord_span = build_quality_span(session_id="master1", turn_id=1, profile_class="genie",
                                    verdicts=v, sampled=True, anomalous=False, attestation={},
                                    started_at=0.0, profile_tag="VAT", invocation="direct",
                                    experts=experts)
    ca = coord_span["attributes"]
    check("span: eval.experts on coordinator", ca["eval.experts"] == experts and ca["eval.expert_count"] == 1)
    check("span: direct invocation default-ish", ca["agent.invocation"] == "direct")


# ── REST surface ──────────────────────────────────────────────────────────────
def t_rest_surface():
    import trusted_data_agent.api.rest_routes as rr
    check("REST endpoint defined: evals_agent_runs", hasattr(rr, "evals_agent_runs"))


def main():
    print("== WS3 genie master/slave scoring + Agent Performance UX (CI-pure) ==")
    for t in (t_runrecord_accessors, t_genie_scorers, t_agent_runs_tree, t_agent_runs_synthetic,
              t_by_agent_extensions, t_span_attrs, t_rest_surface):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
