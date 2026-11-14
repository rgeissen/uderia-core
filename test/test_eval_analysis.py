"""WS3 W3.5 — analysis helpers (failure clustering, diff), store list/annotations,
and the new REST surface. CI-pure.

Run:
    PYTHONPATH=src python test/test_eval_analysis.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.analysis import (  # noqa: E402
    cluster_failures, cluster_online_failures, diff_runs,
    pass_rate_by_dimension, online_pass_rate_by_dimension, online_quality_by_agent,
    dedupe_spans, agent_kpi_dashboard, agent_timeline,
    judge_cleared, judge_flagged, conviction_count,
)
from trusted_data_agent.eval.store import EvalStore  # noqa: E402
from trusted_data_agent.eval.types import EvalCase, Verdict, ScoreKind  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


# ── clustering ────────────────────────────────────────────────────────────────
def t_cluster_failures():
    scores = [
        {"case_id": "c1", "rep_index": 0, "dimension": "tool_subset", "passed": False},
        {"case_id": "c1", "rep_index": 0, "dimension": "safety", "passed": False},
        {"case_id": "c2", "rep_index": 0, "dimension": "tool_subset", "passed": False},
        {"case_id": "c2", "rep_index": 0, "dimension": "safety", "passed": False},
        {"case_id": "c3", "rep_index": 0, "dimension": "cost", "passed": False},
        {"case_id": "c4", "rep_index": 0, "dimension": "tool_subset", "passed": True},
    ]
    clusters = cluster_failures(scores)
    check("clustering: top cluster is the shared failure pair",
          clusters[0]["dimensions"] == ["safety", "tool_subset"] and clusters[0]["count"] == 2)
    check("clustering: top cluster lists both cases", set(clusters[0]["cases"]) == {"c1", "c2"})
    check("clustering: singleton cost cluster present",
          any(c["dimensions"] == ["cost"] and c["count"] == 1 for c in clusters))
    check("clustering: passes excluded", all("tool_subset" != c["signature"] or c["count"] >= 1 for c in clusters))


def t_online_cluster():
    spans = [
        {"span_id": "s1", "attributes": {"turn.id": "1", "eval.dim.cost.passed": False,
                                         "eval.dim.completed.passed": True}},
        {"span_id": "s2", "attributes": {"turn.id": "2", "eval.dim.cost.passed": False}},
    ]
    clusters = cluster_online_failures(spans)
    check("online clustering: cost failure cluster count 2",
          clusters and clusters[0]["dimensions"] == ["cost"] and clusters[0]["count"] == 2)
    rates = online_pass_rate_by_dimension(spans)
    check("online pass rate: cost 0.0", rates.get("cost") == 0.0)
    check("online pass rate: completed 1.0", rates.get("completed") == 1.0)


def t_by_agent():
    spans = [
        {"attributes": {"agent.profile_tag": "OPTIM", "eval.profile_class": "tool_enabled",
                        "eval.gating_failures": 0, "eval.dim.cost.passed": True,
                        "eval.dim.safety.passed": True}},
        {"attributes": {"agent.profile_tag": "OPTIM", "eval.profile_class": "tool_enabled",
                        "eval.gating_failures": 1, "eval.dim.cost.passed": False,
                        "eval.dim.safety.passed": True}},
        {"attributes": {"agent.profile_tag": "FOCUS", "eval.profile_class": "rag_focused",
                        "eval.gating_failures": 0}},
        {"attributes": {"eval.profile_class": "llm_only"}},  # no tag -> ignored
    ]
    by = online_quality_by_agent(spans)
    check("by_agent: OPTIM 2 turns 1 failure", by["OPTIM"]["turns"] == 2 and by["OPTIM"]["failures"] == 1)
    check("by_agent: OPTIM pass_rate 0.5", by["OPTIM"]["pass_rate"] == 0.5)
    check("by_agent: OPTIM series oldest->newest", by["OPTIM"]["series"] == [1, 0])
    check("by_agent: OPTIM carries profile_class", by["OPTIM"]["profile_class"] == "tool_enabled")
    check("by_agent: OPTIM per-dimension cost 0.5", by["OPTIM"]["dimensions"]["cost"] == 0.5)
    check("by_agent: OPTIM per-dimension safety 1.0", by["OPTIM"]["dimensions"]["safety"] == 1.0)
    check("by_agent: FOCUS present, untagged ignored", "FOCUS" in by and len(by) == 2)


def t_vote_alignment():
    """The user's vote vs what the scorers measured — a PREFERENCE reading published
    beside the measurements, never folded into one (Rule 3bb)."""
    from trusted_data_agent.eval.analysis import vote_alignment

    def sp(sid, turn, failed, **extra):
        a = {"session.id": sid, "turn.id": turn, "agent.profile_tag": "OPTIM",
             "eval.dim.cost.passed": not failed}
        a.update(extra)
        return {"attributes": a}

    spans = [sp("s1", 1, False), sp("s1", 2, True), sp("s1", 3, False), sp("s1", 4, True)]
    votes = {("s1", "1"): "down",   # passed but the user rejected it — disagreement
             ("s1", "2"): "up",     # failed but the user liked it — disagreement
             ("s1", "3"): "up",     # agreement
             ("s1", "4"): "down"}   # agreement
    out = vote_alignment(spans, votes)
    check("vote_alignment counts upvotes", out["upvotes"] == 2)
    check("vote_alignment counts downvotes", out["downvotes"] == 2)
    check("both disagreement directions detected", len(out["disagreements"]) == 2)
    check("passed+down is a disagreement",
          any(d["turn"] == 1 and d["vote"] == "down" and d["passed"] for d in out["disagreements"]))
    check("failed+up is a disagreement",
          any(d["turn"] == 2 and d["vote"] == "up" and not d["passed"] for d in out["disagreements"]))
    check("agreements are not disagreements",
          not any(d["turn"] in (3, 4) for d in out["disagreements"]))
    check("no votes ⇒ empty reading, no raise",
          vote_alignment(spans, {}) == {"upvotes": 0, "downvotes": 0, "disagreements": []})
    check("an unvoted turn contributes nothing",
          vote_alignment([sp("s1", 9, True)], votes)["downvotes"] == 0)

    # A refused turn was deliberately never measured — it has no verdict to disagree
    # with, so it counts as a vote but never as a disagreement.
    gate = [sp("s2", 1, False, **{"eval.gate_only": True})]
    out2 = vote_alignment(gate, {("s2", "1"): "down"})
    check("refused turn: vote counted", out2["downvotes"] == 1)
    check("refused turn: never a disagreement", out2["disagreements"] == [])
    # Flow runs are their own agents, keyed by run id — not turn votes.
    flow = [{"attributes": {"agent.kind": "flow", "session.id": "s1", "turn.id": 1}}]
    check("flow spans ignored", vote_alignment(flow, votes)["downvotes"] == 0)


def t_by_agent_votes_are_a_separate_axis():
    """Votes ride ALONGSIDE the quality reading and must not move it."""
    def sp(turn, failed):
        return {"attributes": {"agent.profile_tag": "OPTIM", "session.id": "s1",
                               "turn.id": turn, "eval.profile_class": "tool_enabled",
                               "eval.dim.cost.passed": not failed}}

    spans = [sp(1, False), sp(2, True), sp(3, False)]
    baseline = online_quality_by_agent(spans)
    check("no votes ⇒ vote keys ABSENT (byte-identical baseline)",
          "upvotes" not in baseline["OPTIM"] and "downvotes" not in baseline["OPTIM"])

    votes = {("s1", "1"): "down", ("s1", "2"): "up", ("s1", "3"): "up"}
    withv = online_quality_by_agent(spans, votes=votes)
    check("upvotes counted", withv["OPTIM"]["upvotes"] == 2)
    check("downvotes counted", withv["OPTIM"]["downvotes"] == 1)
    check("both disagreements counted", withv["OPTIM"]["vote_disagreements"] == 2)
    # R3 — the measurement axis is untouched by the preference axis.
    for k in ("turns", "failures", "pass_rate", "series"):
        check(f"votes do not move '{k}' (R3)", withv["OPTIM"][k] == baseline["OPTIM"][k])

    # A vote on a REFUSED turn is real feedback and is counted, while that turn stays
    # out of the measured denominator exactly as before.
    gate = spans + [{"attributes": {"agent.profile_tag": "OPTIM", "session.id": "s1",
                                    "turn.id": 9, "eval.gate_only": True,
                                    "eval.gate.inbound": "block"}}]
    g = online_quality_by_agent(gate, votes={("s1", "9"): "down"})
    check("vote on a refused turn is counted", g["OPTIM"]["downvotes"] == 1)
    check("refused turn still excluded from the measured denominator",
          g["OPTIM"]["turns"] == 3)


def t_agent_runs_vote_is_display_only():
    from trusted_data_agent.eval.analysis import agent_runs

    def sp(turn, failed):
        return {"attributes": {"agent.profile_tag": "OPTIM", "session.id": f"s{turn}",
                               "turn.id": turn, "eval.profile_class": "tool_enabled",
                               "eval.dim.cost.passed": not failed},
                "events": [], "start_time_unix_nano": turn * 10**9}

    spans = [sp(1, False), sp(2, True)]
    base = agent_runs(spans, "OPTIM")
    check("no votes ⇒ no 'vote' key on a run row",
          all("vote" not in r for r in base["runs"]))

    withv = agent_runs(spans, "OPTIM", votes={("s1", "1"): "down"})
    voted = [r for r in withv["runs"] if r["turn_id"] == 1][0]
    unvoted = [r for r in withv["runs"] if r["turn_id"] == 2][0]
    check("the voted run carries its vote", voted.get("vote") == "down")
    check("an unvoted run carries none", "vote" not in unvoted)
    # R3 — the vote must not move the verdict it sits beside.
    b1 = [r for r in base["runs"] if r["turn_id"] == 1][0]
    check("a downvote does not flip 'passed'", voted["passed"] == b1["passed"] is True)
    check("a downvote does not add a conviction",
          voted["convictions"] == b1["convictions"] == 0)


def t_dimension_series():
    """The per-dimension sparkline must be able to show a FALL.

    The append once sat inside `if v:`, so only passing verdicts were kept: every
    sparkline was all-green by construction and a dimension at 25% rendered as a
    single green tick. The series is the trend signal sitting next to the rate, so
    a series that cannot fall is worse than no series at all.
    """
    spans = [
        {"attributes": {"agent.profile_tag": "UDERIA", "eval.profile_class": "rag_focused",
                        "eval.gating_failures": 0, "eval.dim.grounded_overlap.passed": True,
                        "eval.dim.safety.passed": True}},
        {"attributes": {"agent.profile_tag": "UDERIA", "eval.profile_class": "rag_focused",
                        "eval.gating_failures": 1, "eval.dim.grounded_overlap.passed": False,
                        "eval.dim.safety.passed": True}},
        {"attributes": {"agent.profile_tag": "UDERIA", "eval.profile_class": "rag_focused",
                        "eval.gating_failures": 1, "eval.dim.grounded_overlap.passed": False,
                        # an UNDECIDED verdict is not a failure and must not be recorded
                        "eval.dim.safety.passed": None}},
    ]
    by = online_quality_by_agent(spans)
    cell = by["UDERIA"]
    ser = cell["dimension_series"]
    check("series records failures, not only passes", ser["grounded_overlap"] == [1, 0, 0])
    check("series is oldest->newest", ser["grounded_overlap"][0] == 1)
    check("series length == decided-verdict count (the bar's own denominator)",
          all(len(s) == cell["dimension_counts"][d] for d, s in ser.items()))
    check("series agrees with the rate it sits beside",
          round(sum(ser["grounded_overlap"]) / len(ser["grounded_overlap"]), 4)
          == cell["dimensions"]["grounded_overlap"])
    check("undecided verdict excluded from series and count",
          ser["safety"] == [1, 1] and cell["dimension_counts"]["safety"] == 2)

    # A dimension that never passes must yield zeros — not an empty/absent series,
    # which the roster would render as "no trend data" rather than "always failing".
    allbad = online_quality_by_agent([
        {"attributes": {"agent.profile_tag": "X", "eval.gating_failures": 1,
                        "eval.dim.groundedness.passed": False}} for _ in range(3)])
    check("all-failing dimension yields [0,0,0], never an empty series",
          allbad["X"]["dimension_series"]["groundedness"] == [0, 0, 0])


def t_turn_failed_dims():
    """#62b/#63 — the per-turn record of which checks failed.

    `dimension_series` appends only when a dim was DECIDED, so series across dims do
    NOT align by index: a judge sampled once has a 1-entry series whose index 0 is a
    different turn than a full-window dim's index 0. Any per-turn union ("X of N turns
    clean") or corroboration reading ("failed on more than one check") needs THIS
    aligned structure — one entry per scored turn, listing the checks that failed on
    it. Zipping the per-dim series instead is exactly the misalignment that rendered
    @UDERIA's GROUNDED as 0/18 off a single judged turn.
    """
    spans = [
        # turn 1: overlap passes, judge sampled here and FAILS
        {"attributes": {"agent.profile_tag": "U", "eval.gating_failures": 0,
                        "eval.dim.grounded_overlap.passed": True,
                        "eval.dim.groundedness.passed": False}},
        # turn 2: clean on every check that ran (judge abstained → not recorded)
        {"attributes": {"agent.profile_tag": "U", "eval.gating_failures": 0,
                        "eval.dim.grounded_overlap.passed": True,
                        "eval.dim.groundedness.passed": None}},
        # turn 3: overlap AND safety fail — a corroborated turn
        {"attributes": {"agent.profile_tag": "U", "eval.gating_failures": 1,
                        "eval.dim.grounded_overlap.passed": False,
                        "eval.dim.safety.passed": False}},
    ]
    cell = online_quality_by_agent(spans)["U"]
    tf = cell["turn_failed_dims"]
    check("one entry per scored turn — the aligned denominator", len(tf) == cell["turns"] == 3)
    check("a failing check is recorded on ITS turn", tf[0] == ["groundedness"])
    check("a clean turn is an empty list, not an absence", tf[1] == [])
    check("a corroborated turn carries every failing check", tf[2] == ["grounded_overlap", "safety"])
    check("an abstained dim never counts against a turn", "groundedness" not in tf[1])
    # The union the stations need: turns clean on the grounding family.
    g_keys = {"grounded_overlap", "groundedness"}
    clean = sum(1 for fails in tf if not (set(fails) & g_keys))
    check("per-turn union computes the honest x/N", (clean, len(tf)) == (1, 3))

    # A gate-only span (refused turn) is the gate's work, not a scored turn — it must
    # not appear in the aligned record any more than in the turn count.
    with_gate = spans + [{"attributes": {"agent.profile_tag": "U", "eval.gate_only": True,
                                         "eval.gate.acted": True,
                                         "eval.gate.inbound": "block"}}]
    cell2 = online_quality_by_agent(with_gate)["U"]
    check("gate-only spans excluded from the per-turn record",
          len(cell2["turn_failed_dims"]) == cell2["turns"] == 3)

    # Same trailing cap as the sparklines.
    many = [{"attributes": {"agent.profile_tag": "M", "eval.gating_failures": 0,
                            "eval.dim.safety.passed": (i != 24)}} for i in range(25)]
    mcell = online_quality_by_agent(many)["M"]
    check("capped to the trailing window", len(mcell["turn_failed_dims"]) == 20)
    check("…keeping the newest entries", mcell["turn_failed_dims"][-1] == ["safety"])


def t_by_agent_per_user():
    """System-scope keying: tags are only unique PER USER — per_user=True must
    keep two users' same-tag agents in separate cells (never silently merged)."""
    spans = [
        {"attributes": {"agent.profile_tag": "OPTIM", "user.uuid": "u-alice",
                        "eval.profile_class": "tool_enabled", "eval.gating_failures": 0}},
        {"attributes": {"agent.profile_tag": "OPTIM", "user.uuid": "u-alice",
                        "eval.profile_class": "tool_enabled", "eval.gating_failures": 1}},
        {"attributes": {"agent.profile_tag": "OPTIM", "user.uuid": "u-bob",
                        "eval.profile_class": "tool_enabled", "eval.gating_failures": 0}},
        {"attributes": {"agent.profile_tag": "LEGACY", "eval.profile_class": "llm_only",
                        "eval.gating_failures": 0}},  # pre-attribution span (no uuid)
    ]
    by = online_quality_by_agent(spans, per_user=True)
    check("per_user: same tag, two users -> two cells",
          "OPTIM::u-alice" in by and "OPTIM::u-bob" in by)
    check("per_user: counts stay per user",
          by["OPTIM::u-alice"]["turns"] == 2 and by["OPTIM::u-bob"]["turns"] == 1)
    check("per_user: cell carries tag + user_uuid",
          by["OPTIM::u-alice"]["tag"] == "OPTIM" and by["OPTIM::u-alice"]["user_uuid"] == "u-alice")
    check("per_user: pass rates independent",
          by["OPTIM::u-alice"]["pass_rate"] == 0.5 and by["OPTIM::u-bob"]["pass_rate"] == 1.0)
    check("per_user: legacy no-uuid span keyed 'tag::' with user_uuid None",
          "LEGACY::" in by and by["LEGACY::"]["user_uuid"] is None)
    # default (my-scope) shape is byte-identical: plain tag keys, no attribution keys
    flat = online_quality_by_agent(spans)
    check("default: plain tag keys, users merged (my-scope behavior unchanged)",
          "OPTIM" in flat and flat["OPTIM"]["turns"] == 3
          and "tag" not in flat["OPTIM"] and "user_uuid" not in flat["OPTIM"])


def t_pass_rate():
    scores = [
        {"dimension": "tool_subset", "passed": True},
        {"dimension": "tool_subset", "passed": False},
        {"dimension": "safety", "passed": True},
        {"dimension": "cost", "passed": None},  # excluded
    ]
    rates = pass_rate_by_dimension(scores)
    check("pass rate: tool_subset 0.5", rates["tool_subset"] == 0.5)
    check("pass rate: safety 1.0", rates["safety"] == 1.0)
    check("pass rate: None excluded", "cost" not in rates)


# ── diff ──────────────────────────────────────────────────────────────────────
def t_diff():
    base = {"pass_k": 0.9, "per_dimension": {"tool_subset": 1.0, "cost": 0.8},
            "per_cohort_pass_k": {"risky": 0.7}, "per_profile_class": {"tool_enabled": 0.9}}
    cand = {"pass_k": 0.8, "per_dimension": {"tool_subset": 0.6, "cost": 0.9},
            "per_cohort_pass_k": {"risky": 0.7}, "per_profile_class": {"tool_enabled": 0.8}}
    d = diff_runs(base, cand)
    check("diff: pass_k delta -0.1", d["pass_k"]["delta"] == -0.1)
    check("diff: tool_subset regression captured",
          any(r["dimension"] == "tool_subset" and r["delta"] == -0.4 for r in d["regressions"]))
    check("diff: cost improvement captured",
          any(i["dimension"] == "cost" and i["delta"] == 0.1 for i in d["improvements"]))
    check("diff: regressions sorted worst-first", d["regressions"][0]["dimension"] == "tool_subset")
    # missing summary tolerated
    d2 = diff_runs(None, cand)
    check("diff: None baseline tolerated", d2["pass_k"]["baseline"] is None)


# ── store additions ───────────────────────────────────────────────────────────
def t_store():
    store = EvalStore(Path(tempfile.mkdtemp(prefix="tda_an_")) / "e.db")
    store.create_dataset("d", "D")
    store.add_case(EvalCase(id="c1", query="q", profile_class="tool_enabled", dataset_id="d"))
    store.create_run("r1", "d", 1, k=1)
    store.add_score("r1", "c1", 0, Verdict("tool_subset", ScoreKind.DETERMINISTIC, False))
    store.finish_run("r1", {"pass_k": 0.0})
    runs = store.list_runs(dataset_id="d")
    check("store.list_runs returns the run with summary",
          len(runs) == 1 and runs[0]["id"] == "r1" and runs[0]["summary"]["pass_k"] == 0.0)
    store.add_annotation(reviewer="sme", label="disagree", note="judge wrong", case_id="c1")
    anns = store.list_annotations(case_id="c1")
    check("store.list_annotations returns the annotation",
          len(anns) == 1 and anns[0]["label"] == "disagree" and anns[0]["reviewer"] == "sme")
    check("store.list_annotations filters by case", store.list_annotations(case_id="other") == [])


def t_dedupe_spans():
    # two manual re-scores of the SAME turn collapse to the latest
    spans = [
        {"attributes": {"session.id": "s1", "turn.id": "3", "agent.profile_tag": "IDEAT",
                        "eval.gating_failures": 1}},
        {"attributes": {"session.id": "s1", "turn.id": "3", "agent.profile_tag": "IDEAT",
                        "eval.gating_failures": 0}},  # newer re-score
        {"attributes": {"session.id": "s1", "turn.id": "4", "agent.profile_tag": "IDEAT",
                        "eval.gating_failures": 0}},
        {"attributes": {"agent.kind": "flow", "flow.run_id": "frun-1"}},
        {"attributes": {"agent.kind": "flow", "flow.run_id": "frun-1"}},  # dup flow run
    ]
    out = dedupe_spans(spans)
    check("dedupe: same turn collapses to one", sum(1 for s in out if (s.get('attributes') or {}).get('turn.id') == '3') == 1)
    check("dedupe: keeps the latest re-score", next(s for s in out if (s.get('attributes') or {}).get('turn.id') == '3')["attributes"]["eval.gating_failures"] == 0)
    check("dedupe: distinct turns kept", sum(1 for s in out if (s.get('attributes') or {}).get('turn.id') in ('3', '4')) == 2)
    check("dedupe: flow run collapses to one", sum(1 for s in out if (s.get('attributes') or {}).get('agent.kind') == 'flow') == 1)
    # an agent's count reflects the dedup
    by = online_quality_by_agent(out)
    check("dedupe: agent turn count is deduped (2 not 3)", by["IDEAT"]["turns"] == 2)


def t_kpi_dashboard():
    spans = [
        {"attributes": {"agent.profile_tag": "OPTIM", "eval.profile_class": "tool_enabled",
                        "eval.gating_failures": 0, "eval.dim.cost.score": 0.02,
                        "eval.dim.latency.score": 4000, "eval.dim.tool_success_rate.passed": True}},
        {"attributes": {"agent.profile_tag": "OPTIM", "eval.profile_class": "tool_enabled",
                        "eval.gating_failures": 1, "eval.dim.cost.score": 0.04,
                        "eval.dim.latency.score": 6000, "eval.dim.tool_success_rate.passed": False}},
        {"attributes": {"agent.profile_tag": "FOCUS", "eval.profile_class": "rag_focused",
                        "eval.gating_failures": 0, "eval.dim.cost.score": 0.01,
                        "eval.dim.latency.score": 1000}},
        {"attributes": {"agent.kind": "flow", "agent.flow_id": "f1"}},  # excluded
    ]
    d = agent_kpi_dashboard(spans)
    check("kpi: overall turns excludes flow", d["overall"]["turns"] == 3)
    check("kpi: overall pass_rate 2/3", round(d["overall"]["pass_rate"], 2) == 0.67)
    check("kpi: avg_cost mean", abs(d["overall"]["avg_cost"] - 0.0233) < 0.001)
    check("kpi: failing_checks worst-first incl tool_success_rate",
          d["failing_checks"] and d["failing_checks"][0]["dimension"] == "tool_success_rate")
    check("kpi: scoreboard worst-first (OPTIM 0.5 before FOCUS 1.0)",
          d["agents"][0]["tag"] == "OPTIM" and d["agents"][0]["pass_rate"] == 0.5)
    check("kpi: per-agent latency mean", d["agents"][0]["avg_latency_ms"] == 5000)
    check("kpi: recent series chronological", len(d["recent"]) == 3 and d["recent"][1]["passed"] is False)
    check("kpi: empty -> no turns", agent_kpi_dashboard([])["overall"]["turns"] == 0)
    # I1: a refused turn (gate-only span — no bands, no scalar) was a silent PASS here
    # while the roster kept it out of its denominator: two pass rates for one agent.
    refused = {"attributes": {"agent.profile_tag": "OPTIM", "eval.profile_class": "tool_enabled",
                              "eval.gate_only": True, "eval.gate.acted": True}}
    d2 = agent_kpi_dashboard(spans + [refused, refused])
    check("kpi: gate-only spans are not measured turns", d2["overall"]["turns"] == 3)
    check("kpi: …nor passes for their agent",
          next(a for a in d2["agents"] if a["tag"] == "OPTIM")["turns"] == 2)
    check("kpi: …nor points on the trend", len(d2["recent"]) == 3)


def t_agent_timeline():
    day = 86400
    spans = [
        {"start_time_unix_nano": int(1 * day * 1e9), "attributes": {"agent.profile_tag": "OPTIM", "eval.gating_failures": 0, "eval.dim.cost.score": 0.02, "eval.dim.latency.score": 4000}},
        {"start_time_unix_nano": int((1 * day + 100) * 1e9), "attributes": {"agent.profile_tag": "OPTIM", "eval.gating_failures": 1, "eval.dim.cost.score": 0.04, "eval.dim.latency.score": 6000}},
        {"start_time_unix_nano": int(2 * day * 1e9), "attributes": {"agent.profile_tag": "OPTIM", "eval.gating_failures": 0, "eval.dim.cost.score": 0.01, "eval.dim.latency.score": 2000}},
        {"start_time_unix_nano": int(2 * day * 1e9), "attributes": {"agent.profile_tag": "FOCUS", "eval.gating_failures": 0}},
    ]
    d = agent_timeline(spans, "OPTIM")
    check("timeline: agent turns", d["turns"] == 3)
    check("timeline: 2 day buckets", len(d["timeline"]) == 2)
    check("timeline: day1 pass_rate 0.5", d["timeline"][0]["pass_rate"] == 0.5)
    check("timeline: day2 pass_rate 1.0", d["timeline"][1]["pass_rate"] == 1.0)
    check("timeline: day1 avg_cost mean", abs(d["timeline"][0]["avg_cost"] - 0.03) < 1e-9)
    check("timeline: day1 avg_latency mean", d["timeline"][0]["avg_latency_ms"] == 5000)
    check("timeline: recent series length", len(d["recent"]) == 3)
    check("timeline: other agent excluded", agent_timeline(spans, "NOPE")["turns"] == 0)


def t_rest_surface():
    import trusted_data_agent.api.rest_routes as rr
    for fn in ("evals_list_runs", "evals_run_clusters", "evals_diff",
               "evals_online_dashboard", "evals_agents_kpi_dashboard", "evals_agent_timeline",
               "evals_annotations", "evals_online_spans"):
        check(f"REST endpoint defined: {fn}", hasattr(rr, fn))


def t_station_shortfalls():
    """PROP-002b — the red⇒item direction: one ack-able observation per
    (agent, station) whose worst OWNED dimension sits in the danger band."""
    from trusted_data_agent.eval.analysis import station_shortfalls
    dims = {"grounded": ("grounded_overlap", "retrieval_relevance"),
            "guarded": ("output_sanity",)}
    by_agent = {
        # danger on grounded (0.2 < 0.5), healthy on guarded
        "A": {"tag": "A", "dimensions": {"grounded_overlap": 0.2, "output_sanity": 0.9},
              "dimension_counts": {"grounded_overlap": 5, "output_sanity": 5}},
        # review band (0.6) — NOT a shortfall
        "B": {"tag": "B", "dimensions": {"grounded_overlap": 0.6},
              "dimension_counts": {"grounded_overlap": 4}},
        # system-scope key shape — tag comes from the cell, then the key
        "C::u1": {"tag": "C", "dimensions": {"output_sanity": 0.1},
                  "dimension_counts": {"output_sanity": 3}},
        "junk": "not-a-cell",
    }
    out = station_shortfalls(by_agent, review_floor=0.5, station_dims=dims)
    keys = sorted(o["event_type"] for o in out)
    check("exactly the two danger-band shortfalls file",
          keys == ["station_shortfall_grounded::A", "station_shortfall_guarded::C"])
    a = next(o for o in out if o["profile_tag"] == "A")
    check("severity is always danger (the construct's floor)",
          all(o["severity"] == "danger" for o in out))
    check("summary names the station, the agent and the rate",
          "GROUNDED" in a["summary"] and "@A" in a["summary"] and "20%" in a["summary"])
    check("payload carries the driving dimension",
          a["payload"]["dimension"] == "grounded_overlap" and a["payload"]["n"] == 5)
    check("source is derived + session-less (ack keys on the event_type)",
          a["source"] == "derived" and a["session_id"] is None and a["turn_id"] is None)
    check("review-band agents never file", not any(o["profile_tag"] == "B" for o in out))
    check("the JUDGED label is used for the scored wire key",
          "JUDGED" in station_shortfalls(
              {"D": {"tag": "D", "dimensions": {"x": 0.1}, "dimension_counts": {"x": 2}}},
              review_floor=0.5, station_dims={"scored": ("x",)})[0]["summary"])
    check("empty inputs → empty", station_shortfalls({}, review_floor=0.5, station_dims=dims) == []
          and station_shortfalls(by_agent, review_floor=0.5, station_dims={}) == [])



def t_gating_rule_is_current():
    """The turn-failure predicate resolves against the CURRENT registry, not the
    scalar frozen onto the span at write time.

    A sink outlives a policy change. `eval.gating_failures` was computed when the span
    was written, so spans written before `Verdict.gating` became a derived property
    convict on ADVISORY dimensions — and a live agent read 44% (10 of 18) with 3 of
    those 10 convicted by advisory checks alone. The primary facts (per-dimension
    verdicts) are all still on the span, so the aggregate is re-derived from them.
    """
    from trusted_data_agent.eval.analysis import conviction_count, turn_failed
    import trusted_data_agent.eval.scorers  # noqa: F401 — registers the registry

    # A stale span: the stored scalar says one thing, the primary facts another. The
    # rule wins — that is this test's whole subject. (WHICH rule is asserted by
    # t_ledger_band_agrees_with_the_headline; here we only care that the frozen number
    # is not trusted over the evidence beside it.)
    stale = {"eval.gating_failures": 9, "eval.dim.cost.passed": True,
             "eval.dim.latency.passed": True}
    check("the stored scalar is not trusted over the rule", conviction_count(stale) == 0)
    check("...so a clean span does not inherit a retired conviction",
          turn_failed(stale) is False)

    # A measured failure convicts, whatever the stale scalar claims.
    real = {"eval.gating_failures": 0, "eval.dim.retrieval_relevance.passed": False}
    check("a measured failure convicts despite a zero scalar", turn_failed(real) is True)

    # A clean turn is clean.
    amber = {"eval.dim.context_utilization.passed": True, "eval.dim.retrieval_relevance.passed": True}
    check("a turn with nothing in danger does not convict", turn_failed(amber) is False)

    # The mechanism fold: a grounding BLOCK convicts on its own, with no failing
    # measurement at all — otherwise a withheld answer would read clean.
    blocked = {"eval.gate.grounding": "block", "eval.dim.retrieval_relevance.passed": True}
    check("a grounding block convicts with zero failing measurements",
          turn_failed(blocked) is True)

    # Absence tolerance: a span with no per-dimension evidence falls back to the stored
    # scalar rather than silently reading clean.
    legacy = {"eval.gating_failures": 2}
    check("a span with no per-dimension verdicts falls back to the stored scalar",
          conviction_count(legacy) == 2)
    check("a malformed span is not a failure", conviction_count(None) == 0)

    # Every consumer reads the ONE predicate — the pass rate, the sparkline series and
    # the runs ledger cannot disagree about what a failure is.
    import inspect
    from trusted_data_agent.eval import analysis as _an
    src = inspect.getsource(_an)
    body = src.split("def dedupe_spans", 1)[1]        # everything after the helpers
    check("no consumer reads the stored scalar directly",
          "eval.gating_failures" not in body)

    # End to end: the same stale span shape must not inflate an agent's failure count.
    spans = [{"attributes": {"agent.profile_tag": "A", "session.id": "s", "turn.id": i,
                             **stale}} for i in range(3)]
    cell = online_quality_by_agent(spans)["A"]
    check("a stale scalar does not lower a pass rate",
          cell["failures"] == 0 and cell["series"] == [1, 1, 1])




def t_errored_guard_reaches_the_aggregate():
    """A guard that COULD NOT RUN must reach the roster, on the mechanism axis.

    The per-turn instrument has always read it honestly (`assurance_points` returns
    `unknown` — "the guard failed — not recorded as a pass"), but the aggregate had no
    counter for it, so at roster altitude the fact simply vanished: a turn whose
    protections all failed open sat among the quiet ones.

    It is counted on the INTERVENTION axis and deliberately NOT in the pass rate — a
    guard that could not run is not a quality failure, and folding it into the quality
    number would repeat the conflation Rule 3bb exists to prevent.
    """
    sp = lambda **a: {"attributes": {"agent.profile_tag": "A", "session.id": "s",
                                     "eval.profile_class": "rag_focused", **a}}
    spans = [
        sp(**{"turn.id": 1, "eval.gate.outbound": "error", "eval.gate.inbound": "error",
              "eval.dim.retrieval_relevance.passed": True}),
        sp(**{"turn.id": 2, "eval.gate.grounding": "error",
              "eval.dim.retrieval_relevance.passed": True}),
        sp(**{"turn.id": 3, "eval.gate.outbound": "pass",
              "eval.dim.retrieval_relevance.passed": True}),
    ]
    cell = online_quality_by_agent(spans)["A"]
    check("an errored guard is counted", (cell.get("gate") or {}).get("errored") == 2)
    check("...once per turn, however many gates errored",
          (cell.get("gate") or {}).get("errored") == 2)
    check("...and never as a quality failure", cell["failures"] == 0)
    check("...so the pass rate is untouched", cell["pass_rate"] == 1.0)
    check("a clean turn adds nothing to the counter",
          (cell.get("gate") or {}).get("errored") != 3)

    # The roster's mechanism reading must SAY it — colour alone would not.
    import re as _re, pathlib as _pl
    hsrc = _pl.Path("static/js/handlers/agentPerformanceHandler.js").read_text()
    m = _re.search(r"function interventionNote\(o\) \{[\s\S]*?\n    \}", hsrc)
    check("the roster has an intervention reading", m is not None)
    if m:
        check("...that names an errored guard in words",
              "did not run" in m.group(0))
        check("...and reports honestly when nothing acted",
              "no answer altered or withheld" in m.group(0))



def t_judge_flagged_is_the_mirror_reading():
    """Rule 3.0c has FOUR readings; only `disputed` had a name on the surface.

    `judge_only` — the rule measured clean and the judge that refines it found a problem —
    is the mirror, and it was the one reading nothing on the row expressed: a green
    measurement beside a red judge, with nothing saying the two are about one property.
    It must NOT convict (a judge alone never does), and it must never overlap `disputed`.
    """
    judge_only = {"eval.dim.grounded_overlap.passed": True, "eval.dim.groundedness.passed": False}
    disputed = {"eval.dim.grounded_overlap.passed": False, "eval.dim.groundedness.passed": True}
    corrob = {"eval.dim.grounded_overlap.passed": False, "eval.dim.groundedness.passed": False}
    clean = {"eval.dim.grounded_overlap.passed": True, "eval.dim.groundedness.passed": True}

    check("the mirror reading is named", judge_flagged(judge_only) == ("grounded_overlap",))
    check("…and does not convict", conviction_count(judge_only) == 0)
    check("…nor is it called cleared", judge_cleared(judge_only) == ())
    check("the cleared reading is not called flagged", judge_flagged(disputed) == ())
    check("…and is still cleared", judge_cleared(disputed) == ("grounded_overlap",))
    check("a corroborated failure is neither", judge_flagged(corrob) + judge_cleared(corrob) == ())
    check("…and still convicts", conviction_count(corrob) == 1)
    check("agreement is neither", judge_flagged(clean) + judge_cleared(clean) == ())
    for a in (judge_only, disputed, corrob, clean):
        check("no reading is both cleared and flagged",
              not (set(judge_cleared(a)) & set(judge_flagged(a))))
    check("a malformed span yields nothing", judge_flagged(None) == ())


def t_ledger_band_agrees_with_the_headline():
    """ONE conviction rule, read by both surfaces.

    A turn fails when a check a RULE measured landed in danger — unless the judge that
    refines it corrected that (Rule 3.0c `disputed`) — or a gate withheld the answer.

    Gate-eligibility (`Scorer.gating`) used to decide this, and under it a turn where
    `grounded_overlap` AND `groundedness` BOTH found the answer unsupported scored
    clean, because each is individually advisory: the strongest grounding evidence
    available, counted as nothing.

    The row must READ this verdict, never re-derive it. Two earlier versions each
    re-derived and each disagreed with the headline above them (7 vs 11, then 7 vs 10).
    """
    from trusted_data_agent.eval.analysis import (
        agent_runs, turn_failed, convicting_dimensions, judge_cleared, gate_withheld)
    from trusted_data_agent.eval import scorer as _S
    import trusted_data_agent.eval.scorers  # noqa: F401

    _kinds = {getattr(x, "dimension", None):
              str(getattr(x, "kind", "")).split(".")[-1].lower() for x in _S.all_scorers()}

    def sp(turn, dims, gate=None):
        a = {"agent.profile_tag": "A", "session.id": "s", "turn.id": turn,
             "eval.profile_class": "rag_focused",
             **{f"eval.dim.{d}.passed": ok for d, ok in dims}}
        if gate:
            a["eval.gate.grounding"] = gate
        return {"attributes": a, "start_time_unix_nano": turn * 10**9,
                "events": [{"name": "verdict",
                            "attributes": {"dimension": d, "passed": ok,
                                           "kind": _kinds.get(d, "deterministic"),
                                           "band": "healthy" if ok else "danger"}}
                           for d, ok in dims]}

    # 1 · a rule measured a failure → conviction, even though it is not gate-eligible.
    t1 = sp(1, [("grounded_overlap", False), ("groundedness", False)])["attributes"]
    check("a corroborated deterministic failure convicts", turn_failed(t1))
    check("…naming the deterministic check, not the judge",
          convicting_dimensions(t1) == ("grounded_overlap",))

    # 2 · the judge CORRECTED the coarse check → not a conviction, and it is named.
    t2 = sp(2, [("grounded_overlap", False), ("groundedness", True)])["attributes"]
    check("a judge-cleared coarse check does NOT convict", not turn_failed(t2))
    check("…and the correction is named, never swallowed",
          judge_cleared(t2) == ("grounded_overlap",))

    # 3 · a judge alone in danger: a model opined, no rule measured it.
    t3 = sp(3, [("groundedness", False)])["attributes"]
    check("a judge alone does not convict", not turn_failed(t3))

    # 4 · the gate fold: a withheld answer must never read clean, and no measurement
    #     can say so — the scorers deliberately abstain on a blocked turn's text.
    t4 = sp(4, [("cost", True)], gate="block")["attributes"]
    check("a gate that withheld the answer convicts", turn_failed(t4))
    check("…on the mechanism, with no failing measurement",
          gate_withheld(t4) and not convicting_dimensions(t4))

    # 5 · a coarse check with NO refining judge stands alone at full weight.
    t5 = sp(5, [("grounded_overlap", False)])["attributes"]
    check("an unrefined coarse check stands alone", turn_failed(t5))

    check("a clean turn does not convict", not turn_failed(sp(6, [("cost", True)])["attributes"]))

    # THE invariant: the row reads the verdict, so the two surfaces cannot disagree.
    spans = [sp(1, [("grounded_overlap", False), ("groundedness", False)]),
             sp(2, [("grounded_overlap", False), ("groundedness", True)]),
             sp(3, [("groundedness", False)]),
             sp(4, [("cost", True)], gate="block"),
             sp(6, [("cost", True)])]
    runs = agent_runs(spans, "A")["runs"]

    def band(r):
        if r.get("passed") is False:
            return "danger"
        vs = r.get("verdicts") or []
        if any(v.get("band") == "review" for v in vs) or any(v.get("passed") is False for v in vs) \
           or (r.get("disputed") or []):
            return "review"
        return "healthy" if vs else "na"

    check("danger rows EQUAL the headline's failed count",
          sum(1 for r in runs if band(r) == "danger") == sum(1 for r in runs if not r["passed"]))
    check("the judge-cleared turn is review, not danger",
          band(next(r for r in runs if r["turn_id"] == 2)) == "review")
    check("…and its row carries the disputed mark",
          next(r for r in runs if r["turn_id"] == 2)["disputed"] == ["grounded_overlap"])
    check("a persisted verdict is back-filled with its gating flag",
          all("gating" in v for r in runs for v in r["verdicts"]))

    # The renderer READS the verdict; it must not compute one.
    import pathlib as _pl
    hsrc = _pl.Path("static/js/handlers/agentPerformanceHandler.js").read_text()
    i = hsrc.index("function runBand(")
    body = hsrc[i:hsrc.index("\n    }", i)]
    check("the row keys danger on the shared verdict", "run.passed === false" in body)
    check("…and re-derives no conviction rule of its own",
          "gating_failures" not in body and "v.kind !== 'judge'" not in body)
    check("the judge-cleared mark is rendered", "judge-cleared" in hsrc)



def main():
    print("== WS3 W3.5 analysis + store + REST surface (CI-pure) ==")
    for t in (t_cluster_failures, t_online_cluster, t_by_agent, t_vote_alignment,
              t_by_agent_votes_are_a_separate_axis, t_agent_runs_vote_is_display_only,
              t_dimension_series, t_turn_failed_dims, t_by_agent_per_user, t_pass_rate, t_diff, t_dedupe_spans, t_kpi_dashboard, t_agent_timeline, t_station_shortfalls, t_gating_rule_is_current,
              t_errored_guard_reaches_the_aggregate,
              t_judge_flagged_is_the_mirror_reading,
              t_ledger_band_agrees_with_the_headline, t_store, t_rest_surface):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
