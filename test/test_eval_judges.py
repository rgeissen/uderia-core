"""WS3 W3.2 — LLM-judge layer (CI-pure via a FAKE panel).

Proves the judge core works without any live LLM: response parsing, panel
consensus (majority + unknown-escape), per-profile-class judge dispatch, the
abstain-on-absent-inputs contract, prompt building, the runner judge_fn wiring,
and the calibration harness (Cohen's κ + gating floor).

Run:
    PYTHONPATH=src python test/test_eval_judges.py
"""
import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase, RunRecord, ScoreKind  # noqa: E402
from trusted_data_agent.eval.judge import (  # noqa: E402
    FakePanel, parse_judge_response, consensus, JUDGE_SYSTEM,
)
from trusted_data_agent.eval.scorers.judges import (  # noqa: E402
    AnswerCorrectnessJudge, GroundednessJudge, InstructionFollowingJudge,
    ToolArgOptimalityJudge, AnswerGroundednessJudge, SynthesisQualityJudge,
    ScopeFaithfulnessJudge, run_judges, make_judge_fn,
)
from trusted_data_agent.eval import calibration  # noqa: E402
from trusted_data_agent.eval.runner import score_run_record  # noqa: E402

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _resp(verdict, score=None, evidence=None, reasoning="r"):
    return json.dumps({"verdict": verdict, "score": score,
                       "evidence": evidence or [], "reasoning": reasoning})


# ── parsing ───────────────────────────────────────────────────────────────────
def t_parse():
    p = parse_judge_response(_resp("pass", 0.9, ["e1"]))
    check("parse: clean pass", p["verdict"] == "pass" and p["score"] == 0.9 and p["evidence"] == ["e1"])
    p2 = parse_judge_response("garbage not json")
    check("parse: garbage -> unknown", p2["verdict"] == "unknown" and p2["score"] is None)
    p3 = parse_judge_response("```json\n{\"verdict\":\"FAIL\",\"score\":2}\n```")
    check("parse: fenced + clamp + lowercase", p3["verdict"] == "fail" and p3["score"] == 1.0)
    p4 = parse_judge_response(_resp("maybe"))
    check("parse: invalid verdict -> unknown", p4["verdict"] == "unknown")
    p5 = parse_judge_response('{"verdict":"pass","evidence":"single"}')
    check("parse: scalar evidence -> list", p5["evidence"] == ["single"])


# ── consensus ─────────────────────────────────────────────────────────────────
def t_consensus():
    passed, score, ev, _ = consensus([parse_judge_response(_resp("pass", 1.0)),
                                      parse_judge_response(_resp("pass", 0.8)),
                                      parse_judge_response(_resp("fail", 0.0))])
    check("consensus: 2-1 majority pass", passed is True)
    check("consensus: mean score", abs(score - 0.6) < 1e-6)
    passed2, *_ = consensus([parse_judge_response(_resp("pass")),
                             parse_judge_response(_resp("fail"))])
    check("consensus: tie -> fail (conservative)", passed2 is False)
    passed3, score3, _, _ = consensus([parse_judge_response("junk"),
                                       parse_judge_response(_resp("unknown"))])
    check("consensus: all-unknown -> None", passed3 is None and score3 is None)
    passed4, *_ = consensus([])
    check("consensus: empty -> None", passed4 is None)


# ── judge dispatch per profile class ──────────────────────────────────────────
def _focus_run():
    return RunRecord({"profile_type": "rag_focused", "final_summary_text": "The sky is blue.",
                      "knowledge_retrieval_event": {"documents": [{"text": "The sky is blue due to Rayleigh scattering."}]}})


def _optimize_run():
    return RunRecord({"profile_type": "tool_enabled", "final_summary_text": "Done.",
                      "execution_trace": [{"action": {"tool_name": "base_readQuery",
                                                       "arguments": {"sql": "SELECT 1"}},
                                           "result": {"tool_output_summary": {"status": "success"}}}]})


def _genie_run():
    return RunRecord({"profile_type": "genie", "final_summary_text": "Combined answer.",
                      "genie_metadata": {"slaves_invoked": [{"profile_tag": "DATA", "final_answer_text": "rows"},
                                                            {"profile_tag": "DOCS", "final_answer_text": "context"}]}})


def t_dispatch():
    panel = FakePanel([_resp("pass", 1.0, ["ok"]), _resp("pass", 0.9)])

    # Focus -> groundedness applies
    case_f = EvalCase(id="f", query="why is the sky blue", profile_class="rag_focused")
    vf = _loop.run_until_complete(run_judges(case_f, _focus_run(), panel))
    dims_f = {v.dimension for v in vf}
    check("Focus dispatch includes groundedness", "groundedness" in dims_f)
    check("Focus dispatch excludes tool_arg_optimality", "tool_arg_optimality" not in dims_f)
    check("Focus groundedness verdict pass", next(v for v in vf if v.dimension == "groundedness").passed is True)

    # Optimize -> tool_arg_optimality applies; groundedness does not
    case_o = EvalCase(id="o", query="run a query", profile_class="tool_enabled")
    vo = _loop.run_until_complete(run_judges(case_o, _optimize_run(), panel))
    dims_o = {v.dimension for v in vo}
    check("Optimize dispatch includes tool_arg_optimality", "tool_arg_optimality" in dims_o)
    check("Optimize dispatch excludes groundedness", "groundedness" not in dims_o)
    # answer_groundedness is a tool-class judge (answer claims supported by tool results)
    check("Optimize dispatch includes answer_groundedness", "answer_groundedness" in dims_o)
    check("Focus dispatch excludes answer_groundedness (tool-class only)",
          "answer_groundedness" not in dims_f)
    check("Optimize answer_groundedness verdict pass",
          next(v for v in vo if v.dimension == "answer_groundedness").passed is True)

    # genie -> synthesis_quality applies
    case_g = EvalCase(id="g", query="multi-domain", profile_class="genie")
    vg = _loop.run_until_complete(run_judges(case_g, _genie_run(), panel))
    check("genie dispatch includes synthesis_quality", "synthesis_quality" in {v.dimension for v in vg})

    # Ideate -> instruction_following applies
    case_i = EvalCase(id="i", query="say hi", profile_class="llm_only")
    vi = _loop.run_until_complete(run_judges(case_i, RunRecord({"final_summary_text": "hi"}), panel))
    check("Ideate dispatch includes instruction_following",
          "instruction_following" in {v.dimension for v in vi})


# ── per-model vote attribution (the "Agent Quality Judged" card's results face) ─
def t_votes():
    """The consensus keeps the tally; `Verdict.votes` keeps the ATTRIBUTION —
    which model ruled what, in panel order. A degraded model's unparseable
    response stays visible as an abstention (unknown), never dropped: hiding it
    would overstate the consensus."""

    class _NamedPanel(FakePanel):
        @property
        def models(self):
            return ["XAI/grok", "Google/gemini", "OpenRouter/gemma"]

    panel = _NamedPanel([_resp("pass", 0.9), _resp("fail", 0.2), "not json at all"])
    j = InstructionFollowingJudge()
    v = _loop.run_until_complete(j.score_async(
        EvalCase(id="v", query="q", profile_class="llm_only"),
        RunRecord({"final_summary_text": "a"}), panel))
    check("votes attach in panel order with model attribution",
          v.votes is not None and [x["model"] for x in v.votes]
          == ["XAI/grok", "Google/gemini", "OpenRouter/gemma"])
    check("each vote carries the model's own ruling",
          [x["verdict"] for x in v.votes] == ["pass", "fail", "unknown"])
    check("a vote carries the model's score where one was given",
          v.votes[0].get("score") == 0.9 and "score" not in v.votes[2])
    check("votes serialize additively (to_dict carries them only when present)",
          "votes" in v.to_dict()
          and "votes" not in _mk_verdict_novotes().to_dict())
    # A panel that cannot name its members (length mismatch) attaches NO votes —
    # a mis-attributed vote is worse than none.
    class _ShortPanel(FakePanel):
        @property
        def models(self):
            return ["only/one"]
    v2 = _loop.run_until_complete(j.score_async(
        EvalCase(id="v2", query="q", profile_class="llm_only"),
        RunRecord({"final_summary_text": "a"}), _ShortPanel([_resp("pass", 1.0), _resp("pass", 1.0)])))
    check("a model-count mismatch attaches no votes (never mis-attributed)", v2.votes is None)


def _mk_verdict_novotes():
    from trusted_data_agent.eval.types import Verdict, ScoreKind
    return Verdict(dimension="x", kind=ScoreKind.DETERMINISTIC, passed=True)


# ── abstain-on-absent-inputs ──────────────────────────────────────────────────
def t_abstain():
    panel = FakePanel([_resp("pass", 1.0)])
    # AnswerCorrectness needs a reference_answer
    j = AnswerCorrectnessJudge()
    case = EvalCase(id="x", query="q", profile_class="llm_only")  # no reference
    v = _loop.run_until_complete(j.score_async(case, RunRecord({"final_summary_text": "a"}), panel))
    check("AnswerCorrectness abstains without reference", v.passed is None)
    # Groundedness needs retrieved docs
    jg = GroundednessJudge()
    vg = _loop.run_until_complete(jg.score_async(
        EvalCase(id="y", query="q", profile_class="rag_focused"),
        RunRecord({"final_summary_text": "a", "knowledge_retrieval_event": {}}), panel))
    check("Groundedness abstains without docs", vg.passed is None)
    # ToolArgOptimality needs tool calls
    jt = ToolArgOptimalityJudge()
    vt = _loop.run_until_complete(jt.score_async(
        EvalCase(id="z", query="q", profile_class="tool_enabled"),
        RunRecord({"final_summary_text": "a"}), panel))
    check("ToolArgOptimality abstains without tool calls", vt.passed is None)
    # AnswerGroundedness needs tool results (grounding text)
    jag = AnswerGroundednessJudge()
    vag = _loop.run_until_complete(jag.score_async(
        EvalCase(id="ag", query="q", profile_class="tool_enabled"),
        RunRecord({"final_summary_text": "a"}), panel))
    check("AnswerGroundedness abstains without tool results", vag.passed is None)
    # sync score() always abstains (no panel)
    check("sync score() abstains", AnswerCorrectnessJudge().score(case, RunRecord({})).passed is None)


# ── prompt building ───────────────────────────────────────────────────────────
def t_prompts():
    j = AnswerCorrectnessJudge()
    case = EvalCase(id="p", query="capital of France?", profile_class="llm_only",
                    reference_answer="Paris")
    system, user = j.build_messages(case, RunRecord({"final_summary_text": "Paris is the capital."}))
    check("prompt: system carries JUDGE_SYSTEM", JUDGE_SYSTEM.split("\n")[0] in system)
    check("prompt: system carries dimension rubric", "REFERENCE" in system)
    check("prompt: user carries query + both answers",
          "capital of France" in user and "Paris" in user and "Paris is the capital" in user)
    # tool judge serializes tool calls as JSON
    jt = ToolArgOptimalityJudge()
    _, ut = jt.build_messages(EvalCase(id="t", query="q", profile_class="tool_enabled"), _optimize_run())
    check("tool prompt includes arguments", "base_readQuery" in ut and "SELECT 1" in ut)


# ── runner integration (judge_fn) ─────────────────────────────────────────────
def t_runner_judge_fn():
    panel = FakePanel([_resp("pass", 1.0)])
    judge_fn = make_judge_fn(panel)
    case = EvalCase(id="r", query="say hi", profile_class="llm_only")
    # include_judges=False -> no judge verdicts
    v_det = _loop.run_until_complete(score_run_record(
        case, RunRecord({"final_summary_text": "hi"}), include_judges=False, judge_fn=judge_fn))
    check("runner: judges excluded by default", not any(v.kind == ScoreKind.JUDGE for v in v_det))
    # include_judges=True -> judge verdicts present
    v_judge = _loop.run_until_complete(score_run_record(
        case, RunRecord({"final_summary_text": "hi"}), include_judges=True, judge_fn=judge_fn))
    check("runner: judges included when requested", any(v.kind == ScoreKind.JUDGE for v in v_judge))
    # regression: judges must NOT be duplicated (abstaining sync score() + judge_fn)
    judge_dims = [v.dimension for v in v_judge if v.kind == ScoreKind.JUDGE]
    check("runner: no duplicate judge dimensions", len(judge_dims) == len(set(judge_dims)))
    # judge layer failure never breaks scoring
    async def boom(c, r):
        raise RuntimeError("panel exploded")
    v_safe = _loop.run_until_complete(score_run_record(
        case, RunRecord({"final_summary_text": "hi"}), include_judges=True, judge_fn=boom))
    check("runner: judge failure isolated", any(v.dimension == "judge_error" for v in v_safe))


# ── panel error isolation ─────────────────────────────────────────────────────
def t_panel_error():
    class BoomPanel(FakePanel):
        async def judge(self, system, user):
            raise RuntimeError("network down")
    v = _loop.run_until_complete(AnswerCorrectnessJudge().score_async(
        EvalCase(id="e", query="q", profile_class="llm_only", reference_answer="ref"),
        RunRecord({"final_summary_text": "a"}), BoomPanel([])))
    check("panel error -> abstain not crash", v.passed is None and "panel error" in v.explanation)


# ── calibration (Cohen's κ) ───────────────────────────────────────────────────
def t_calibration():
    perfect = [("pass", "pass"), ("fail", "fail"), ("pass", "pass"), ("fail", "fail")]
    rep = calibration.calibrate(perfect)
    check("calibration: perfect agreement kappa=1", rep["cohen_kappa"] == 1.0 and rep["gating_ok"])
    check("calibration: agreement 1.0", rep["agreement"] == 1.0)
    # judge always says pass -> kappa 0 (no better than chance)
    biased = [("pass", "pass"), ("fail", "pass"), ("pass", "pass"), ("fail", "pass")]
    repb = calibration.calibrate(biased)
    check("calibration: constant judge -> kappa 0, not gating", repb["cohen_kappa"] == 0.0 and not repb["gating_ok"])
    # abstentions excluded
    repu = calibration.calibrate([("pass", "unknown"), ("pass", "pass"), ("fail", "fail")])
    check("calibration: abstentions excluded", repu["n_decided"] == 2 and repu["n_abstained"] == 1)
    check("calibration: empty -> kappa None", calibration.cohen_kappa([]) is None)
    # confusion + precision/recall
    mixed = [("pass", "pass"), ("pass", "fail"), ("fail", "fail"), ("fail", "pass")]
    repm = calibration.calibrate(mixed)
    check("calibration: confusion counts", repm["confusion"] == {"tp": 1, "tn": 1, "fp": 1, "fn": 1})
    check("calibration: precision 0.5", repm["precision"] == 0.5)


def _scoped_turn(answer="The total revenue is $5000.", with_scope=True):
    t = {"final_summary_text": answer, "profile_type": "tool_enabled"}
    if with_scope:
        t["kg_enrichment_event"] = {"enrichments": [{
            "scope_databases": ["fitness_db"],
            "scope_entities": ["Customers", "Sales", "Products", "ServiceTickets"]}]}
    return t


def t_scope_faithfulness():
    j = ScopeFaithfulnessJudge()
    check("scope_faithfulness: registered for tool classes", "tool_enabled" in j.applies_to)
    # self-describing scope -> readable description
    rr = RunRecord(_scoped_turn())
    desc = rr.kg_scope_description()
    check("kg_scope_description: names db + concepts",
          "fitness_db" in desc and "Customers" in desc and "Sales" in desc)
    # abstains when no scope declared (nothing to be faithful to)
    v_abs = _loop.run_until_complete(
        j.score_async(EvalCase(id="a", query="q", profile_class="tool_enabled"),
                      RunRecord(_scoped_turn(with_scope=False)), FakePanel([_resp("pass", 1.0)])))
    check("scope_faithfulness: abstains with no scope", v_abs.passed is None)
    # build_messages carries the scope + question + answer (NOT tool-call DBs → independent)
    case = EvalCase(id="s", query="What is total revenue?", profile_class="tool_enabled")
    system, user = j.build_messages(case, rr)
    check("scope_faithfulness: prompt has scope + question + answer",
          "TRUSTED SCOPE" in user and "fitness_db" in user and "total revenue" in user.lower()
          and "$5000" in user)
    # faithful → pass; drifted → fail
    v_pass = _loop.run_until_complete(j.score_async(case, rr, FakePanel([_resp("pass", 1.0, ["in scope"])])))
    v_fail = _loop.run_until_complete(j.score_async(case, rr, FakePanel([_resp("fail", 0.0, ["used finance_ledger"])])))
    check("scope_faithfulness: faithful → passed True", v_pass.passed is True)
    check("scope_faithfulness: drifted → passed False, cites evidence",
          v_fail.passed is False and v_fail.evidence)


# ── annotations → κ (the queue's whole point: forming the (human, judge) pair) ─
def _ann(dim, judge_passed, label, **kw):
    d = {"dimension": dim, "judge_passed": judge_passed, "label": label}
    d.update(kw)
    return d


def t_pairs_from_annotations():
    pairs = calibration.pairs_from_annotations([
        _ann("groundedness", 1, "agree"),      # judge passed, human concurs  -> (T, T)
        _ann("groundedness", 1, "disagree"),   # judge passed, human dissents -> (F, T)
        _ann("groundedness", 0, "agree"),      # judge failed, human concurs  -> (F, F)
        _ann("groundedness", 0, "disagree"),   # judge failed, human dissents -> (T, F)
    ])["groundedness"]
    check("pairs: agree with pass -> (T,T)", pairs[0] == (True, True))
    check("pairs: disagree with pass -> (F,T)", pairs[1] == (False, True))
    check("pairs: agree with fail -> (F,F)", pairs[2] == (False, False))
    check("pairs: disagree with fail -> (T,F)", pairs[3] == (True, False))
    # rows that cannot form a pair are notes, never evidence
    out = calibration.pairs_from_annotations([
        _ann(None, 1, "agree"),                       # no dimension
        _ann("groundedness", None, "agree"),          # no frozen ruling
        _ann("groundedness", 1, "needs-work"),        # label outside agree/disagree
        {"label": "agree", "note": "manual form entry"},
        _ann("content_safety", 1, "agree"),
    ])
    check("pairs: unpairable rows skipped, pairable kept",
          list(out.keys()) == ["content_safety"] and len(out["content_safety"]) == 1)
    # dimensions grouped independently
    two = calibration.pairs_from_annotations(
        [_ann("groundedness", 1, "agree"), _ann("content_safety", 0, "disagree")])
    check("pairs: grouped per dimension",
          set(two.keys()) == {"groundedness", "content_safety"})


def t_report_from_annotations():
    floor_n = calibration.DEFAULT_MIN_PAIRS
    # perfect agreement over enough pairs (half pass / half fail so κ is defined)
    good = ([_ann("groundedness", 1, "agree")] * (floor_n // 2)
            + [_ann("groundedness", 0, "agree")] * (floor_n // 2))
    rep = calibration.report_from_annotations(good)["groundedness"]
    check("report: enough agreeing pairs -> calibrated + gating_ok",
          rep["status"] == "calibrated" and rep["gating_ok"] and rep["cohen_kappa"] == 1.0)
    check("report: carries min_pairs", rep["min_pairs"] == floor_n)
    # the same perfect κ over TOO FEW pairs must never clear the gate
    few = [_ann("groundedness", 1, "agree"), _ann("groundedness", 0, "agree")]
    repf = calibration.report_from_annotations(few)["groundedness"]
    check("report: perfect kappa below the pair floor -> insufficient, never gating",
          repf["status"] == "insufficient" and not repf["gating_ok"]
          and repf["cohen_kappa"] == 1.0)
    # ENOUGH pairs (exactly the floor — the boundary), humans dissent often
    # enough that κ lands under 0.6: 4×(T,T) + 2×(F,F) + 2×(F,T) + 2×(T,F)
    # -> agreement .6, κ ≈ .17.
    noisy = ([_ann("content_safety", 1, "agree")] * 4
             + [_ann("content_safety", 0, "agree")] * 2
             + [_ann("content_safety", 1, "disagree")] * 2
             + [_ann("content_safety", 0, "disagree")] * 2)
    assert len(noisy) == floor_n
    repn = calibration.report_from_annotations(noisy)["content_safety"]
    check("report: disagreement -> below_floor, not gating",
          repn["status"] == "below_floor" and not repn["gating_ok"])
    check("report: empty annotations -> empty report",
          calibration.report_from_annotations([]) == {})


def t_judge_panel_optout():
    """A model can be excluded from the judge panel per LLM configuration.

    Motivated by a live panel member that timed out on EVERY judge of EVERY turn:
    it contributed no vote and, because each judge waits out its per-call timeout,
    it set the latency of the whole evaluation layer. Excluding it is the operator's
    call, so the option is an explicit opt-out rather than an auto-eviction.

    The invariant that protects existing deployments: ABSENT means participating.
    """
    from trusted_data_agent.eval import judge as jmod

    # ── the rule, stated once and asserted directly ──────────────────────────
    e = jmod.judge_panel_enabled
    check("absent key ⇒ participates (every pre-existing config is unchanged)", e({}) is True)
    check("explicit True ⇒ participates", e({"judge_panel_enabled": True}) is True)
    check("explicit False ⇒ opted out", e({"judge_panel_enabled": False}) is False)
    check("only a real False opts out — a stray value never silently loses a judge",
          e({"judge_panel_enabled": "false"}) is True
          and e({"judge_panel_enabled": None}) is True
          and e({"judge_panel_enabled": 0}) is True)
    check("a malformed config degrades to participating", e(None) is True)

    # ── and the selection path honours it ────────────────────────────────────
    import trusted_data_agent.core.config_manager as cmmod
    import trusted_data_agent.llm.client_factory as cfmod

    class _CM:
        def __init__(self, cfgs): self._c = cfgs
        def get_llm_configurations(self, u=None): return list(self._c)
        def get_default_profile_id(self, u=None): return None
        def get_profile(self, pid, u=None): return None

    async def _fake_client(provider, model, credentials):
        return object()

    def _panel_for(cfgs):
        orig_cm, orig_cl = cmmod.get_config_manager, cfmod.get_or_create_llm_client
        try:
            cmmod.get_config_manager = lambda: _CM(cfgs)
            cfmod.get_or_create_llm_client = _fake_client
            return _loop.run_until_complete(jmod.build_panel(user_uuid=None, max_models=3))
        finally:
            cmmod.get_config_manager, cfmod.get_or_create_llm_client = orig_cm, orig_cl

    def _cfg(provider, model, **extra):
        return {"id": provider, "provider": provider, "model": model,
                "credentials": {"api_key": "k"}, **extra}

    all_in = [_cfg("Google", "gemini"), _cfg("Friendli", "llama"), _cfg("Anthropic", "opus")]
    check("baseline: with no flag set, every family is on the panel",
          sorted(p for p, _m, _i in _panel_for(all_in)._instances)
          == ["Anthropic", "Friendli", "Google"])

    mixed = [_cfg("Google", "gemini"), _cfg("Friendli", "llama", judge_panel_enabled=False),
             _cfg("Anthropic", "opus")]
    picked = sorted(p for p, _m, _i in _panel_for(mixed)._instances)
    check("an opted-out config is skipped", picked == ["Anthropic", "Google"])
    check("…and it does not consume a panel slot the others could use",
          len(picked) == 2)

    # Excluding a family must not let a SECOND config of that family sneak back in —
    # the opt-out is per configuration, and the loop's family de-dup runs after it.
    two_friendli = [_cfg("Friendli", "llama", judge_panel_enabled=False),
                    _cfg("Friendli", "mixtral"), _cfg("Google", "gemini")]
    picked2 = sorted(f"{p}/{m}" for p, m, _i in _panel_for(two_friendli)._instances)
    check("the opt-out is per CONFIG, not per provider family",
          picked2 == ["Friendli/mixtral", "Google/gemini"])

    check("excluding everything yields an empty panel — judges then abstain",
          len(_panel_for([_cfg("Google", "gemini", judge_panel_enabled=False)])) == 0)


# ── the judge sees what the trigger saw (prod 727292c3, Oct 2026) ─────────────────
# The groundedness judge was handed `docs[:8]` clipped to 5000 chars while the trigger
# read every chunk, and ruled two terms "entirely absent" that sat in chunk 9. The
# evidence is the ONE shared support text (`retrieved_chunk_texts`), budgeted by whole
# chunks from config, and an unavoidable truncation is STATED so the judge can answer
# "unknown" rather than "absent". The trigger's flagged spans lead the adjudication.
def t_groundedness_evidence():
    import trusted_data_agent.core.config as cfg_mod
    chunks = [{"content": f"chunk {i} says something about topic {i}.", "source": f"doc{i}.md"}
              for i in range(10)]
    chunks[8]["content"] = "Receipts are sealed in the turn's Ed25519 chain; eval reads governed_data_ratio."
    turn = {"final_summary_text": "Receipts are sealed into the Ed25519 chain.",
            "knowledge_retrieval_event": {"chunks": chunks, "collections": ["Uderia Documentation"]}}
    case = EvalCase(id="g", query="what is obda?", profile_class="rag_focused")
    j = GroundednessJudge()
    _, user = j.build_messages(case, RunRecord(turn))
    check("every retrieved chunk reaches the judge (chunk 9 included)", "Ed25519 chain" in user)
    check("the citation label travels with the chunk", "doc8.md" in user)
    check("no truncation is stated when none happened", "retrieved documents shown" not in user)
    orig = getattr(cfg_mod.APP_CONFIG, "ONLINE_EVAL_JUDGE_EVIDENCE_CHARS", None)
    try:
        texts = RunRecord(turn).retrieved_chunk_texts()
        cfg_mod.APP_CONFIG.ONLINE_EVAL_JUDGE_EVIDENCE_CHARS = len(texts[0]) + 5 + len(texts[1])
        _, user = j.build_messages(case, RunRecord(turn))
        check("a budget truncates by WHOLE chunks, in retrieval order",
              "chunk 0 says" in user and "chunk 1 says" in user and "chunk 2 says" not in user)
        check("an unavoidable truncation is stated to the judge",
              "2 of 10 retrieved documents shown" in user)
    finally:
        if orig is None:
            delattr(cfg_mod.APP_CONFIG, "ONLINE_EVAL_JUDGE_EVIDENCE_CHARS")
        else:
            cfg_mod.APP_CONFIG.ONLINE_EVAL_JUDGE_EVIDENCE_CHARS = orig
    # the trigger's flagged spans lead the adjudication when the gate escalates
    turn2 = dict(turn, grounding_trigger_spans=["sealed into the Ed25519 chain"])
    _, user = j.build_messages(case, RunRecord(turn2))
    check("flagged spans are handed to the judge", "FLAGGED SPANS" in user and "sealed into the Ed25519 chain" in user)
    _, user = j.build_messages(case, RunRecord(turn))
    check("no flagged spans → no section", "FLAGGED SPANS" not in user)
    # a long answer is not silently cut either
    long_turn = dict(turn, final_summary_text="x" * 2600 + " Ed25519 at the end.")
    _, user = j.build_messages(case, RunRecord(long_turn))
    check("the answer is not clipped at 2000 chars", "Ed25519 at the end." in user)


def main():
    print("== WS3 W3.2 LLM-judge layer (CI-pure) ==")
    for t in (t_parse, t_consensus, t_dispatch, t_votes, t_abstain, t_prompts,
              t_pairs_from_annotations, t_report_from_annotations,
              t_scope_faithfulness, t_runner_judge_fn, t_panel_error, t_calibration,
              t_gate_judge_fold, t_judge_panel_optout, t_groundedness_evidence):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


# ── the A3/A4 fold — the gate already asked THIS judge about THIS text ───────
def t_gate_judge_fold():
    """The gate imports these exact judge classes for its in-path escalation, then the
    scoring layer ran them again — one turn, two verdicts, never reconciled, and a second
    bill in real tokens.

    A raising panel proves nothing here: `score_async` catches panel exceptions and
    returns an abstention, so every negative case would look like a fold. This records
    whether the panel was CONSULTED.
    """
    import asyncio
    from trusted_data_agent.eval.scorers.judges import (
        GroundednessJudge, ScopeFaithfulnessJudge, AnswerGroundednessJudge)

    class Panel:
        def __init__(self): self.called = False
        def __len__(self): return 1
        async def judge(self, *a, **k):
            self.called = True
            return []

    case = EvalCase(id="x", query="q", profile_class="rag_focused")
    base = {"profile_type": "rag_focused", "status": "success", "isValid": True,
            "final_summary_text": "The 2024 total was 41,900 units.",
            "knowledge_retrieval_event": {
                "documents": [{"text": "the 2024 total was 41,900 units"}]}}
    STAMP = {"decision": "pass", "judged": True, "judge_passed": True,
             "judge_family": "groundedness", "judge_score": 0.92,
             "judge_votes": [{"model": "m1", "verdict": "pass"}],
             "reason": "every claim supported"}

    async def run_one(judge, turn):
        p = Panel()
        v = await judge.score_async(case, RunRecord(turn), p)
        return p.called, v

    called, v = asyncio.run(run_one(GroundednessJudge(), dict(base, grounding=STAMP)))
    check("the gate's ruling is READ, not re-judged", not called)
    # Reading a bare pass/fail would have been a downgrade dressed as a saving: the
    # scoring verdict carries a SCORE and the per-model panel VOTES. The gate now records
    # them, which is what makes the read lossless.
    check("…losslessly — the score survives", v.score == 0.92)
    check("…and the panel attribution survives", bool(v.votes))
    check("…and it SAYS it was ruled in flight", "in flight" in (v.explanation or ""))

    # Four cases where reading would attribute a verdict to text it was never given.
    for label, stamp in (
            ("blocked", dict(STAMP, decision="block")),
            ("annotated", dict(STAMP, decision="annotate")),
            ("other family", dict(STAMP, judge_family="scope_faithfulness")),
            ("did not judge", dict(STAMP, judged=False))):
        called, _ = asyncio.run(run_one(GroundednessJudge(), dict(base, grounding=stamp)))
        check(f"the gate {label} → judged fresh, never read", called)
    called, _ = asyncio.run(run_one(GroundednessJudge(), base))
    check("no grounding stamp → judged fresh", called)

    # The declaration is explicit, never inferred from a class name — the same discipline
    # as POINT_JUDGES, where a `…_judge` pattern would catch purely post-hoc judges.
    check("only the two judges a gate consults declare a family",
          GroundednessJudge.gate_family == "groundedness"
          and ScopeFaithfulnessJudge.gate_family == "scope_faithfulness"
          and AnswerGroundednessJudge.gate_family == "")
    called, _ = asyncio.run(run_one(AnswerGroundednessJudge(),
                                    dict(base, profile_type="tool_enabled", grounding=STAMP)))
    check("a judge no gate consults always judges fresh", called)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
