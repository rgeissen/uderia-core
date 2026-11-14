"""WS3 W3.4 — online-eval hook (tail sampling), OTLP span, signed attestation.

CI-pure: a MemoryExporter captures spans, a seeded RNG makes sampling
deterministic, and session_manager.get_workflow_history is monkeypatched so no
server/session files are needed.

Run:
    PYTHONPATH=src python test/test_eval_online.py
"""
import os
import sys
import random
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import RunRecord, ScoreKind  # noqa: E402
from trusted_data_agent.eval import online  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _good_turn():
    return {"profile_type": "tool_enabled", "status": "success", "isValid": True,
            "final_summary_text": "All good.", "user_query": "list dbs",
            "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {}},
                                 "result": {"tool_output_summary": {"status": "success"}}}],
            "provenance_meta": {"sealed": True, "chain_tip_hash": "abc123"},
            "provenance_chain": [{"step_type": "tactical_plan"}], "turn_cost": 0.01}


def _bad_turn():
    t = _good_turn()
    t.update({"status": "error", "isValid": False, "error_message": "boom"})
    return t


def _patch_wf(turn):
    """Monkeypatch session_manager.get_workflow_history to return [turn]."""
    import trusted_data_agent.core.session_manager as sm

    async def fake(user_uuid, session_id):
        return [turn] if turn is not None else []
    sm.get_workflow_history = fake


# ── judge sampling (deterministic always scores; only judges are stochastic) ────
def t_judge_sampling():
    good = RunRecord(_good_turn())
    bad = RunRecord(_bad_turn())
    # anomalous turn -> always judged
    s, anom = online.should_judge(bad, judge_rate=0.0, always_anomalous=True)
    check("anomalous turn always judged", s is True and anom is True)
    # good turn at judge_rate 0 -> not judged
    s2, anom2 = online.should_judge(good, judge_rate=0.0, always_anomalous=True)
    check("non-anomalous at judge_rate 0 not judged", s2 is False and anom2 is False)
    # good turn at judge_rate 1 -> judged
    s3, _ = online.should_judge(good, judge_rate=1.0, always_anomalous=True)
    check("non-anomalous at judge_rate 1 judged", s3 is True)
    # heavy self-correction counts as anomalous
    t = _good_turn()
    t["provenance_chain"] = [{"step_type": "self_correction"}, {"step_type": "self_correction"}]
    s4, anom4 = online.should_judge(RunRecord(t), judge_rate=0.0, always_anomalous=True)
    check("heavy self-correction is anomalous", s4 is True and anom4 is True)

    # The deterministic-flagged trigger (the policy the reorder enables): a turn the
    # free layer condemned earns judges even at rate 0 — judges refine what a code
    # grader flagged; a clean turn at rate 0 stays judge-free.
    class _V:  # minimal verdict shape
        def __init__(self, passed=True, band=None):
            self.passed, self.band = passed, band
    check("failed deterministic check flags", online.deterministic_flagged([_V(passed=False)]) is True)
    check("danger band flags", online.deterministic_flagged([_V(band='danger')]) is True)
    check("clean verdicts don't flag",
          online.deterministic_flagged([_V(), _V(band='healthy'), _V(passed=None)]) is False)
    check("empty/None don't flag", online.deterministic_flagged([]) is False
          and online.deterministic_flagged(None) is False)
    s5, anom5 = online.should_judge(good, judge_rate=0.0, always_anomalous=True, flagged=True)
    check("deterministic-flagged turn judged at rate 0 (not run-anomalous)",
          s5 is True and anom5 is False)
    s6, _ = online.should_judge(good, judge_rate=0.0, always_anomalous=False, flagged=True)
    check("always_anomalous off ignores the flag too (one master for both triggers)",
          s6 is False)
    # The hook wires the trigger from the DETERMINISTIC verdicts, scored first.
    import inspect as _insp
    src = _insp.getsource(online.evaluate_turn_online)
    check("hook scores deterministically BEFORE the judge decision",
          src.index("score_run_record") < src.index("should_judge"))
    check("hook passes the flag", "flagged=deterministic_flagged(verdicts)" in src)


# ── signed attestation ────────────────────────────────────────────────────────
def t_attestation():
    from trusted_data_agent.eval.runner import score_run_record
    from trusted_data_agent.eval.types import EvalCase
    case = EvalCase(id="a", query="q", profile_class="tool_enabled")
    verdicts = _loop.run_until_complete(score_run_record(case, RunRecord(_good_turn())))
    att = online.sign_attestation("tip-hash", verdicts, "tool_enabled")
    check("attestation has verdict digest", len(att["verdict_digest"]) == 64)
    check("attestation binds tip hash", att["turn_tip_hash"] == "tip-hash")
    # digest is stable for same verdicts, changes with profile_class in payload only via signature
    att2 = online.sign_attestation("tip-hash", verdicts, "tool_enabled")
    check("attestation digest deterministic", att["verdict_digest"] == att2["verdict_digest"])
    # signed flag reflects key availability (either signed True w/ sig, or False w/ empty sig)
    check("attestation signed flag consistent",
          (att["signed"] and att["signature"]) or (not att["signed"] and att["signature"] == ""))


# ── OTLP span shape ───────────────────────────────────────────────────────────
def t_span():
    from trusted_data_agent.eval.runner import score_run_record
    from trusted_data_agent.eval.types import EvalCase
    case = EvalCase(id="s", query="q", profile_class="tool_enabled")
    verdicts = _loop.run_until_complete(score_run_record(case, RunRecord(_good_turn())))
    att = online.sign_attestation("tip", verdicts, "tool_enabled")
    span = online.build_quality_span(session_id="sess1", turn_id=3,
                                     profile_class="tool_enabled", verdicts=verdicts,
                                     sampled=True, anomalous=False, attestation=att,
                                     started_at=1.0)
    check("span name", span["name"] == "agent.turn.eval")
    check("span has trace+span id", len(span["trace_id"]) and len(span["span_id"]))
    check("span attributes carry profile_class", span["attributes"]["eval.profile_class"] == "tool_enabled")
    check("span has per-dimension attrs",
          any(k.startswith("eval.dim.") and k.endswith(".passed") for k in span["attributes"]))
    check("span events mirror verdicts", len(span["events"]) == len(verdicts))
    check("span carries attestation", span["attestation"]["verdict_digest"] == att["verdict_digest"])


# ── full hook (sampled + not, disabled, error isolation) ──────────────────────
def t_hook():
    mem = online.MemoryExporter()
    online.set_exporter(mem)
    orig_enabled = APP_CONFIG.ONLINE_EVAL_ENABLED
    try:
        # disabled -> no-op
        APP_CONFIG.ONLINE_EVAL_ENABLED = False
        _patch_wf(_good_turn())
        r = _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile=None, turn_id=1))
        check("hook disabled -> None, no span", r is None and not mem.spans)

        # enabled + anomalous -> scored, span exported, summary failed
        APP_CONFIG.ONLINE_EVAL_ENABLED = True
        APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES = False
        _patch_wf(_bad_turn())
        events = []

        async def eh(payload, etype):
            events.append((payload, etype))
        r2 = _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile=None, turn_id=2, event_handler=eh))
        check("hook anomalous -> scored summary", r2 is not None and r2["anomalous"] is True)
        check("hook anomalous -> failed (error turn)", r2["passed"] is False)
        check("hook deterministic-only -> not judged", r2.get("judged") is False)
        check("hook deterministic -> zero cost", r2.get("cost_usd") == 0.0)
        check("hook exported one span", len(mem.spans) == 1)
        check("hook emitted turn_score_complete step",
              any(et == "turn_score_complete" and (p or {}).get("type") == "turn_score_complete"
                  for p, et in events))

        # enabled + good turn -> STILL scored (deterministic is unconditional now)
        _patch_wf(_good_turn())
        before = len(mem.spans)
        r3 = _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile=None, turn_id=3,
            rng=random.Random(1)))
        check("hook good turn -> scored every turn", r3 is not None and len(mem.spans) == before + 1)
        check("hook good turn deterministic-only", r3.get("judged") is False)

        # no workflow history -> None
        _patch_wf(None)
        r4 = _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile=None, turn_id=4))
        check("hook no history -> None", r4 is None)
    finally:
        APP_CONFIG.ONLINE_EVAL_ENABLED = orig_enabled
        online.set_exporter(None)


def t_hook_judges():
    """INCLUDE_JUDGES on + a credentialed panel -> judges run on the (sampled) turn."""
    import trusted_data_agent.eval.judge as judge_mod
    import trusted_data_agent.eval.scorers.judges as judges_mod
    from trusted_data_agent.eval.judge import FakePanel
    mem = online.MemoryExporter()
    online.set_exporter(mem)
    orig = (APP_CONFIG.ONLINE_EVAL_ENABLED,
            getattr(APP_CONFIG, "ONLINE_EVAL_INCLUDE_JUDGES", False),
            getattr(APP_CONFIG, "ONLINE_EVAL_JUDGE_SAMPLE_RATE", 0.05))
    orig_build = judge_mod.build_panel
    try:
        APP_CONFIG.ONLINE_EVAL_ENABLED = True
        APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES = True
        APP_CONFIG.ONLINE_EVAL_JUDGE_SAMPLE_RATE = 1.0  # judge every turn
        # fake a 1-model panel returning a clean pass verdict
        fake = FakePanel(['{"verdict":"pass","score":1.0,"evidence":["ok"],"reasoning":"r"}'])
        async def fake_build(**kw):
            return fake
        judge_mod.build_panel = fake_build
        judges_mod.build_panel = fake_build  # if imported by name anywhere
        _patch_wf(_good_turn())
        r = _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile={"profile_type": "llm_only"},
            turn_id=9, rng=random.Random(1)))
        check("hook judges-on -> judged true", r is not None and r.get("judged") is True)
        # the exported span carries a judge-kind verdict
        span = mem.spans[-1]
        has_judge = any((e.get("attributes") or {}).get("kind") == "judge"
                        for e in (span.get("events") or []))
        check("hook judges-on -> judge verdict in span", has_judge)
    finally:
        (APP_CONFIG.ONLINE_EVAL_ENABLED, APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES,
         APP_CONFIG.ONLINE_EVAL_JUDGE_SAMPLE_RATE) = orig
        judge_mod.build_panel = orig_build
        online.set_exporter(None)


class _TimedPanel:
    """Panel whose every judge call takes a fixed time, and which reports usage.

    Models the production shape the fixes were written for: real spend accrues on
    `usage` as calls complete, independently of whether the caller keeps the verdicts.
    """
    def __init__(self, delay=0.0, response=None, delays=None):
        self._delay = delay
        self._delays = list(delays) if delays else None
        self._i = 0
        self.usage = []
        self.calls = 0
        self._response = response or '{"verdict":"pass","score":1.0,"evidence":["ok"],"reasoning":"r"}'

    def __len__(self):
        return 1

    @property
    def models(self):
        return ["fake/judge"]

    async def judge(self, system, user):
        d = self._delay
        if self._delays is not None:
            d = self._delays[min(self._i, len(self._delays) - 1)]
            self._i += 1
        self.calls += 1
        await asyncio.sleep(d)
        self.usage.append(("fake", "fake/judge", 100, 10))  # real spend, always billed
        return [self._response]


def _judge_env(panel):
    """Install `panel` as the judge panel and force judging on. Returns a restore fn."""
    import trusted_data_agent.eval.judge as judge_mod
    import trusted_data_agent.eval.scorers.judges as judges_mod
    orig = (APP_CONFIG.ONLINE_EVAL_ENABLED,
            getattr(APP_CONFIG, "ONLINE_EVAL_INCLUDE_JUDGES", False),
            getattr(APP_CONFIG, "ONLINE_EVAL_JUDGE_SAMPLE_RATE", 0.05),
            getattr(APP_CONFIG, "ONLINE_EVAL_TIMEOUT_SECONDS", 25),
            getattr(APP_CONFIG, "ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS", 20),
            online._JUDGE_LAYER_MARGIN_SECONDS,
            judge_mod.build_panel)
    APP_CONFIG.ONLINE_EVAL_ENABLED = True
    APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES = True
    APP_CONFIG.ONLINE_EVAL_JUDGE_SAMPLE_RATE = 1.0

    async def fake_build(**kw):
        return panel
    judge_mod.build_panel = fake_build
    judges_mod.build_panel = fake_build

    def restore():
        (APP_CONFIG.ONLINE_EVAL_ENABLED, APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES,
         APP_CONFIG.ONLINE_EVAL_JUDGE_SAMPLE_RATE, APP_CONFIG.ONLINE_EVAL_TIMEOUT_SECONDS,
         APP_CONFIG.ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS,
         online._JUDGE_LAYER_MARGIN_SECONDS, judge_mod.build_panel) = orig
        judges_mod.build_panel = orig[6]
    return restore


def _judge_dims(span):
    return sorted((e.get("attributes") or {}).get("dimension")
                  for e in (span.get("events") or [])
                  if (e.get("attributes") or {}).get("kind") == "judge")


def t_judges_run_concurrently():
    """The regression that cost production every judge run it ever paid for.

    `run_judges` used to be a sequential `for … await`, so N judges serialised to
    N × the per-call timeout while the caller capped the WHOLE layer at one. A single
    hung panel member then consumed the entire budget inside the first judge. Timed,
    because the property IS the wall-clock: concurrent judges cost one call window.
    """
    import time as _time
    from trusted_data_agent.eval.types import EvalCase, RunRecord
    from trusted_data_agent.eval.scorers.judges import run_judges
    from trusted_data_agent.eval.scorer import scorers_for, ScoreKind

    n = len([s for s in scorers_for("llm_only", kinds=[ScoreKind.JUDGE])])
    check("llm_only registers more than one judge (else the test proves nothing)", n > 1)

    delay = 0.15
    panel = _TimedPanel(delay=delay)
    case = EvalCase(id="c", query="q", profile_class="llm_only")
    run = RunRecord(_good_turn())
    t0 = _time.monotonic()
    out = _loop.run_until_complete(run_judges(case, run, panel))
    elapsed = _time.monotonic() - t0

    check("every applicable judge produced a verdict", len(out) == n)
    check(f"judges ran CONCURRENTLY ({elapsed:.2f}s for {panel.calls} panel calls "
          f"× {delay}s — sequential would be ≥{panel.calls * delay:.2f}s)",
          panel.calls > 1 and elapsed < (panel.calls * delay) * 0.75)


def t_judge_layer_budget_is_satisfiable():
    """The outer cap must exceed a single judge call, by construction.

    Production shipped a 20s per-call timeout inside a 25s layer cap and, with the
    sequential loop, guaranteed a total loss whenever one model hung. The floor makes
    the relationship a property of the code rather than of whoever last edited the two
    numbers independently.
    """
    check("layer margin is positive", online._JUDGE_LAYER_MARGIN_SECONDS > 0)
    for call_to, layer_to in ((20, 25), (20, 5), (60, 25), (1, 1)):
        effective = max(layer_to, call_to + online._JUDGE_LAYER_MARGIN_SECONDS)
        check(f"per-call {call_to}s inside layer {layer_to}s -> effective {effective}s "
              f"leaves room for one full call", effective > call_to)


def t_judge_timeout_keeps_completed_verdicts():
    """A layer timeout must keep the rulings that FINISHED — they were already billed.

    The old code did `verdicts.extend(await wait_for(...))`: on timeout the extend never
    ran, so completed judge verdicts died with the cancelled coroutine while their tokens
    were still charged. The roster then read "deterministic scoring" on a turn the user
    had paid to have judged (production session 4a2169f3).
    """
    mem = online.MemoryExporter()
    online.set_exporter(mem)
    # one judge returns fast, the rest hang past the layer cap
    panel = _TimedPanel(delays=[0.02, 30.0])
    restore = _judge_env(panel)
    try:
        online._JUDGE_LAYER_MARGIN_SECONDS = 0.05
        APP_CONFIG.ONLINE_EVAL_TIMEOUT_SECONDS = 0.3
        APP_CONFIG.ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS = 0.2
        _patch_wf(_good_turn())
        r = _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile={"profile_type": "llm_only"},
            turn_id=11, rng=random.Random(1)))
        span = mem.spans[-1]
        judges = [(e.get("attributes") or {}) for e in (span.get("events") or [])
                  if (e.get("attributes") or {}).get("kind") == "judge"]
        # The property is that a REAL RULING survived — not merely that the dimension
        # is named. Counting dimensions alone passes on the cut-off abstentions that
        # backfill adds, which is exactly the loss this fix exists to prevent.
        ruled = [j for j in judges if j.get("passed") is not None]
        check("a judge that COMPLETED keeps its actual ruling across the cut-off",
              len(ruled) >= 1)
        check("the surviving ruling is a real verdict, not a cut-off placeholder",
              all("evaluation window" not in str(j.get("explanation") or "") for j in ruled))
        check("eval.judged reflects the RECORD, not the attempt",
              (span.get("attributes") or {}).get("eval.judged") is True)
        check("the summary agrees with the span", r is not None and r.get("judged") is True)
        check("the spend is still billed", (span.get("attributes") or {}).get("eval.input_tokens", 0) > 0)
    finally:
        restore()
        online.set_exporter(None)


def t_cut_judges_are_recorded_not_silently_lost():
    """A judge the cut-off silenced must appear as an abstention NAMING the cut-off.

    Before this, a timed-out judge simply vanished: the span carried whichever
    dimensions happened to finish (including free abstentions that never consulted a
    model) and said nothing about the ones that were attempted and cancelled. A reader
    could not tell "never applicable" from "tried and lost".

    Also pins the invariant the runs ledger depends on: `eval.judged` is true iff the
    record holds judge verdicts — the row's LABEL and its CHIPS can never disagree.
    """
    from trusted_data_agent.eval.scorer import scorers_for, ScoreKind
    # The hook resolves the class from the TURN (RunRecord.profile_type wins over the
    # profile arg), so the applicable judge set must be derived the same way.
    _cls = RunRecord(_good_turn()).profile_type
    applicable = sorted(s.dimension for s in scorers_for(_cls, kinds=[ScoreKind.JUDGE]))

    mem = online.MemoryExporter()
    online.set_exporter(mem)
    panel = _TimedPanel(delay=30.0)  # every panel-consulting judge hangs past the cap
    restore = _judge_env(panel)
    try:
        online._JUDGE_LAYER_MARGIN_SECONDS = 0.05
        APP_CONFIG.ONLINE_EVAL_TIMEOUT_SECONDS = 0.2
        APP_CONFIG.ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS = 0.1
        _patch_wf(_good_turn())
        _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile={"profile_type": "llm_only"},
            turn_id=12, rng=random.Random(1)))
        span = mem.spans[-1]
        attrs = span.get("attributes") or {}
        check("every applicable judge dimension is accounted for, none vanishes",
              _judge_dims(span) == applicable)
        cut = [(e.get("attributes") or {}) for e in (span.get("events") or [])
               if (e.get("attributes") or {}).get("kind") == "judge"
               and "evaluation window" in str((e.get("attributes") or {}).get("explanation") or "")]
        check("the silenced judges say so, by name", len(cut) >= 1)
        check("a cut judge ABSTAINS — it never reads as a pass",
              all(c.get("passed") is None for c in cut))
        check("label agrees with chips: judged true when judge verdicts are on the record",
              attrs.get("eval.judged") is True and _judge_dims(span))
        check("deterministic verdicts are still persisted",
              any((e.get("attributes") or {}).get("kind") in ("deterministic", "metric")
                  for e in (span.get("events") or [])))
    finally:
        restore()
        online.set_exporter(None)

    # The RULE itself, stated once and asserted directly. Backfill currently makes
    # "a panel was attempted" and "the record holds a ruling" coincide in every
    # reachable hook state, so no end-to-end scenario can separate them — which is
    # exactly why the rule is a named function used by BOTH writers of `eval.judged`
    # rather than an inline expression each could drift on its own.
    from trusted_data_agent.eval.types import Verdict as _V
    jfr = online.judged_from_record
    check("rule: a judge verdict on the record ⇒ judged",
          jfr([_V(dimension="d", kind=ScoreKind.JUDGE, passed=None)]) is True)
    check("rule: an ABSTAINING judge still counts (it renders as a judge chip)",
          jfr([_V(dimension="d", kind=ScoreKind.JUDGE, passed=None,
                  explanation="abstained")]) is True)
    check("rule: deterministic/metric verdicts alone ⇒ NOT judged",
          jfr([_V(dimension="a", kind=ScoreKind.DETERMINISTIC, passed=True),
               _V(dimension="b", kind=ScoreKind.METRIC, passed=True)]) is False)
    check("rule: an empty/absent record ⇒ NOT judged",
          jfr([]) is False and jfr(None) is False)
    # both writers of the flag go through the one rule (they used to compute it apart)
    import inspect as _insp
    check("the online hook derives eval.judged from the rule",
          "judged_from_record(verdicts)" in _insp.getsource(online.evaluate_turn_online))

    # …and the other direction: judges off ⇒ no judge chips ⇒ label must NOT say judged.
    mem2 = online.MemoryExporter()
    online.set_exporter(mem2)
    _orig = (APP_CONFIG.ONLINE_EVAL_ENABLED,
             getattr(APP_CONFIG, "ONLINE_EVAL_INCLUDE_JUDGES", False))
    try:
        APP_CONFIG.ONLINE_EVAL_ENABLED = True
        APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES = False
        _patch_wf(_good_turn())
        _loop.run_until_complete(online.evaluate_turn_online(
            user_uuid="u", session_id="s", profile={"profile_type": "llm_only"},
            turn_id=13, rng=random.Random(1)))
        span = mem2.spans[-1]
        check("label agrees with chips: judged false when no judge verdict exists",
              (span.get("attributes") or {}).get("eval.judged") is False
              and _judge_dims(span) == [])
    finally:
        (APP_CONFIG.ONLINE_EVAL_ENABLED, APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES) = _orig
        online.set_exporter(None)


def t_one_broken_judge_does_not_lose_the_panel():
    """A judge that raises degrades to a named abstention; the others still rule."""
    from trusted_data_agent.eval.types import EvalCase, RunRecord
    from trusted_data_agent.eval.scorers.judges import run_judges, JudgeScorer
    from trusted_data_agent.eval.scorer import scorers_for, ScoreKind

    victim = [s for s in scorers_for("llm_only", kinds=[ScoreKind.JUDGE])
              if isinstance(s, JudgeScorer)][0]
    orig = victim.score_async

    async def boom(case, run, panel):
        raise RuntimeError("provider exploded")
    try:
        victim.score_async = boom
        panel = _TimedPanel(delay=0.0)
        out = _loop.run_until_complete(
            run_judges(EvalCase(id="c", query="q", profile_class="llm_only"),
                       RunRecord(_good_turn()), panel))
        n = len([s for s in scorers_for("llm_only", kinds=[ScoreKind.JUDGE])])
        check("a raising judge does not lose the other verdicts", len(out) == n)
        bad = [v for v in out if v.dimension == victim.dimension]
        check("the broken judge abstains rather than passing",
              len(bad) == 1 and bad[0].passed is None and "judge error" in (bad[0].explanation or ""))
    finally:
        victim.score_async = orig


def t_jsonl_exporter():
    import tempfile, json as _json
    path = os.path.join(tempfile.mkdtemp(prefix="tda_online_"), "spans.jsonl")
    exp = online.JsonlExporter(path)
    exp.export({"name": "agent.turn.eval", "x": 1})
    exp.export({"name": "agent.turn.eval", "x": 2})
    with open(path) as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    check("jsonl exporter appends NDJSON", len(lines) == 2 and _json.loads(lines[1])["x"] == 2)


def t_default_thresholds():
    """Admin-set global thresholds turn informational metrics into gating pass/fail at runtime."""
    from trusted_data_agent.core import config as cfg
    from trusted_data_agent.eval.types import EvalCase, RunRecord
    from trusted_data_agent.eval.scorers.reliability import LatencyScorer

    run = RunRecord({"duration_ms": 30000, "profile_type": "llm_only"})
    prev = getattr(cfg.APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {})
    try:
        # No thresholds → informational (passed is None, not gating)
        cfg.APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {}
        case = EvalCase(id="c1", query="x", profile_class="llm_only", assertions=online.default_assertions())
        v = LatencyScorer().score(case, run)
        check("no threshold → latency informational (passed is None)", v.passed is None)
        check("default_assertions empty when unset", online.default_assertions() == {})

        # Admin sets a threshold → same scorer now gates
        cfg.APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {"max_latency_ms": 10000}
        check("default_assertions reflects runtime config",
              online.default_assertions().get("max_latency_ms") == 10000)
        case2 = EvalCase(id="c2", query="x", profile_class="llm_only", assertions=online.default_assertions())
        v2 = LatencyScorer().score(case2, run)
        check("admin threshold → latency gates to fail", v2.passed is False)
    finally:
        cfg.APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = prev


def t_banded_thresholds():
    """3-band thresholds: each metric gets two cut points → Healthy/Review/Danger.
    Review (amber) PASSES the gate but is flagged; only Danger (red) fails."""
    from trusted_data_agent.core import config as cfg
    from trusted_data_agent.eval.types import EvalCase, RunRecord
    from trusted_data_agent.eval.scorers.deterministic import CostCeilingScorer
    from trusted_data_agent.eval.scorers.reliability import ToolSuccessRateScorer

    prev = getattr(cfg.APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {})
    try:
        # max metric (cost): Healthy ≤0.30, Danger >0.50
        cfg.APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {"max_cost_usd": {"healthy": 0.30, "danger": 0.50}}
        a = online.default_assertions()
        check("nested config flattens to gate + __healthy",
              a.get("max_cost_usd") == 0.50 and a.get("max_cost_usd__healthy") == 0.30)
        mk = lambda c: EvalCase(id="c", query="q", profile_class="tool_enabled", assertions=a)
        v_lo = CostCeilingScorer().score(mk(0.2), RunRecord({"turn_cost": 0.20, "profile_type": "tool_enabled"}))
        v_mid = CostCeilingScorer().score(mk(0.4), RunRecord({"turn_cost": 0.40, "profile_type": "tool_enabled"}))
        v_hi = CostCeilingScorer().score(mk(0.7), RunRecord({"turn_cost": 0.70, "profile_type": "tool_enabled"}))
        check("max metric: below healthy → healthy/pass", v_lo.band == "healthy" and v_lo.passed is True)
        check("max metric: between → review/pass (warn not gate)", v_mid.band == "review" and v_mid.passed is True)
        check("max metric: above danger → danger/fail", v_hi.band == "danger" and v_hi.passed is False)

        # min metric (tool success): Healthy ≥0.95, Danger <0.80
        cfg.APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {"min_tool_success_rate": {"healthy": 0.95, "danger": 0.80}}
        a2 = online.default_assertions()
        run = lambda ok, total: RunRecord({"profile_type": "tool_enabled", "execution_trace": (
            [{"action": {"tool_name": "t", "arguments": {}}, "result": {"tool_output_summary": {"status": "success"}}}] * ok +
            [{"action": {"tool_name": "t", "arguments": {}}, "result": {"tool_output_summary": {"status": "error"}}}] * (total - ok))})
        mk2 = lambda: EvalCase(id="c", query="q", profile_class="tool_enabled", assertions=a2)
        v_good = ToolSuccessRateScorer().score(mk2(), run(10, 10))   # 1.0
        v_mid2 = ToolSuccessRateScorer().score(mk2(), run(9, 10))    # 0.9
        v_bad = ToolSuccessRateScorer().score(mk2(), run(5, 10))     # 0.5
        check("min metric: ≥healthy → healthy/pass", v_good.band == "healthy" and v_good.passed is True)
        check("min metric: between → review/pass", v_mid2.band == "review" and v_mid2.passed is True)
        check("min metric: <danger → danger/fail", v_bad.band == "danger" and v_bad.passed is False)

        # legacy scalar = single Danger line (no amber)
        cfg.APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {"max_cost_usd": 0.50}
        a3 = online.default_assertions()
        check("legacy scalar → gate only, no __healthy",
              a3.get("max_cost_usd") == 0.50 and "max_cost_usd__healthy" not in a3)
        v_legacy = CostCeilingScorer().score(
            EvalCase(id="c", query="q", profile_class="tool_enabled", assertions=a3),
            RunRecord({"turn_cost": 0.40, "profile_type": "tool_enabled"}))
        check("legacy scalar: below gate → healthy/pass (no amber band)",
              v_legacy.band == "healthy" and v_legacy.passed is True)
    finally:
        cfg.APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = prev


def t_judge_event_names_its_layer():
    """The judge step must say WHICH layer spent the tokens.

    One event type (`judge_evaluation_complete`) serves both the scoring panel and a
    gate's in-path escalation, and only one of them could change what the user
    received — so a trace that cannot tell them apart reports an in-flight ruling and
    a post-hoc measurement as the same thing. Declared by the caller, never inferred:
    the deferral (which exists FOR the in-path case) defaults to it, and every other
    site states its own.
    """
    import inspect
    from trusted_data_agent.eval import online as O

    sig = inspect.signature(O._record_score_usage).parameters
    check("the sink takes a layer", "layer" in sig)
    check("…defaulting to the post-hoc norm", sig["layer"].default == "post_hoc")
    dsig = inspect.signature(O.defer_judge_usage).parameters
    check("the deferral carries it too", "layer" in dsig)
    check("…defaulting to in-flight, which is why it exists",
          dsig["layer"].default == "in_flight")

    src = inspect.getsource(O)
    check("the step name distinguishes the two in the trace",
          "Gate Consulted a Judge" in src and "Agent Quality Judged" in src)
    check("…and the event carries the layer as a field", '"layer": layer' in src)
    check("an unknown layer degrades to the norm, never to a claim",
          'layer if layer in ("in_flight", "post_hoc") else "post_hoc"' in src)
    check("the online hook declares post_hoc rather than relying on the default",
          'profile_class=profile_class, layer="post_hoc")' in src)
    check("…and the flush carries the entry's own layer",
          'layer=entry.get("layer", "in_flight")' in src)


def t_score_usage_accounting():
    """DESIGN IMPERATIVE — every judge LLM call is allocated consistently: session +
    turn totals (reload-authoritative), the consumption ledger, AND a live-KPI
    token_update delivered over the per-user notification channel (the reliable path
    for the backgrounded online hook + the REST 'Score this turn' button, which have
    no open request stream). Session-tagged so the frontend can guard it. Free
    (deterministic) scoring spends 0 tokens → the whole path is a clean no-op."""
    from trusted_data_agent.core import session_manager as sm
    from trusted_data_agent.core import config as cfg
    import trusted_data_agent.auth.consumption_manager as cmmod
    import trusted_data_agent.auth.database as dbmod

    consumption_calls, turn_count_calls = [], []

    persisted_events = []

    async def fake_update_token_count(uu, sid, i, o):
        return None

    async def fake_get_session(uu, sid):
        return {"input_tokens": 130, "output_tokens": 65,
                "last_turn_data": {"workflow_history": [
                    {"turn": 7, "turn_input_tokens": 20, "turn_output_tokens": 10}]}}

    async def fake_update_turn_token_counts(uu, sid, tid, ti, to, added_cost_usd=None, additive=False):
        # Mirror the real additive contract: with additive=True the args are DELTAS accumulated
        # onto the existing turn totals (20/10 from fake_get_session) atomically. Record the
        # resulting absolutes so the assertion tests the real accumulation path.
        if additive:
            ti = 20 + ti
            to = 10 + to
        turn_count_calls.append((tid, ti, to, additive))

    async def fake_append_system_events_to_turn(uu, sid, tid, events):
        persisted_events.append((tid, events))
        return True

    class _FakeConsumption:
        def __init__(self, db):
            pass

        def update_turn_tokens(self, **kw):
            consumption_calls.append(kw)

    class _FakeDbCtx:
        def __enter__(self):
            return object()

        def __exit__(self, *a):
            return False

    class _FakeQ:
        def __init__(self):
            self.items = []

        async def put(self, item):
            self.items.append(item)

        def put_nowait(self, item):
            # TC-70: emit sites now use put_nowait (an unreferenced create_task could be
            # GC'd before running); the fake mirrors the real asyncio.Queue interface.
            self.items.append(item)

    fq = _FakeQ()
    o_utc, o_gs, o_uttc = sm.update_token_count, sm.get_session, sm.update_turn_token_counts
    o_ase = sm.append_system_events_to_turn
    o_cm, o_gdb = cmmod.ConsumptionManager, dbmod.get_db_session
    o_nq = cfg.APP_STATE.get("notification_queues")
    try:
        sm.update_token_count = fake_update_token_count
        sm.get_session = fake_get_session
        sm.update_turn_token_counts = fake_update_turn_token_counts
        sm.append_system_events_to_turn = fake_append_system_events_to_turn
        cmmod.ConsumptionManager = _FakeConsumption
        dbmod.get_db_session = lambda: _FakeDbCtx()
        cfg.APP_STATE["notification_queues"] = {"u1": {fq}}

        # judge tokens spent → the full accounting path fires. `judge_verdicts` is the
        # RESULTS face (final consensus + per-model votes) — it must ride the SAME
        # persisted event as the bill, or the card shows a spend with no ruling.
        _vsum = [{"dimension": "answer_groundedness", "passed": False, "score": 0.2,
                  "explanation": "panel 0✓/2✗/1? — unsupported claim",
                  "votes": [{"model": "XAI/grok", "verdict": "fail"},
                            {"model": "Google/gemini", "verdict": "fail"},
                            {"model": "OpenRouter/gemma", "verdict": "unknown"}]}]
        _loop.run_until_complete(online._record_score_usage(
            "u1", "s1", 7, 30, 15, 0.0012, event_handler=None,
            judge_models=["Google/gemini"], judge_verdicts=_vsum,
            profile_class="llm_only"))
        _loop.run_until_complete(asyncio.sleep(0.02))  # let the create_task broadcast run

        check("turn totals ACCUMULATE atomically (existing 20/10 + judge delta 30/15, additive)",
              bool(turn_count_calls) and turn_count_calls[-1] == (7, 50, 25, True))
        billed = consumption_calls[-1] if consumption_calls else {}
        check("consumption ledger billed the judge tokens",
              billed.get("additional_input_tokens") == 30 and billed.get("additional_output_tokens") == 15)
        check("consumption ledger billed the judge cost (cents)",
              billed.get("additional_cost_usd_cents") == int(0.0012 * 1000000))
        tok = [it for it in fq.items if it.get("type") == "token_update"]
        check("live token_update broadcast over the notification channel", len(tok) == 1)
        check("token_update is SESSION-TAGGED (frontend can guard it)",
              bool(tok) and tok[0]["payload"].get("session_id") == "s1")
        check("token_update carries the judge cost",
              bool(tok) and tok[0]["payload"].get("cost_usd") == 0.0012)

        # TRANSPARENCY: the judge run is a discrete Live Status STEP — broadcast live
        # AND persisted to the turn so it survives a plan reload, and it carries the
        # judge cost so the panel's per-event costs sum to the turn total.
        jstep = [it for it in fq.items if it.get("type") == "judge_evaluation_complete"]
        check("judge step broadcast live over the notification channel", len(jstep) == 1)
        check("judge step is SESSION-TAGGED",
              bool(jstep) and jstep[0]["payload"]["details"].get("session_id") == "s1")
        check("judge step carries the judge cost (event-sum reconciles to turn total)",
              bool(jstep) and jstep[0]["payload"]["details"].get("cost_usd") == 0.0012)
        check("judge step persisted to the turn (survives a plan reload)",
              bool(persisted_events) and persisted_events[-1][0] == 7
              and persisted_events[-1][1][0].get("type") == "judge_evaluation_complete")
        check("persisted judge step carries cost + tokens (reload event-sum reconciles)",
              bool(persisted_events)
              and persisted_events[-1][1][0]["payload"]["details"].get("cost_usd") == 0.0012
              and persisted_events[-1][1][0]["payload"]["details"].get("input_tokens") == 30)
        # RESULTS TRANSPARENCY — the ruling + the per-model votes ride the SAME
        # persisted event (live + reload identical), and the one-liner leads with
        # the ruling, not the bill.
        _pd = persisted_events[-1][1][0]["payload"]["details"] if persisted_events else {}
        check("persisted judge step carries the VERDICTS (final consensus per dimension)",
              _pd.get("verdicts") == _vsum)
        check("…including the per-model votes (who ruled what)",
              bool(_pd.get("verdicts")) and len(_pd["verdicts"][0]["votes"]) == 3
              and _pd["verdicts"][0]["votes"][0] == {"model": "XAI/grok", "verdict": "fail"})
        check("…and the live broadcast carries the same verdicts (one event, two deliveries)",
              bool(jstep) and jstep[0]["payload"]["details"].get("verdicts") == _vsum)
        check("the summary line leads with the RULING",
              "0 pass · 1 fail" in str(_pd.get("summary", "")))

        # judge_verdict_summary — the ONE summarizer every path uses: judge-kind only,
        # votes passed through, deterministic verdicts excluded, malformed tolerated.
        from trusted_data_agent.eval.types import Verdict, ScoreKind
        _mix = [
            Verdict(dimension="safety", kind=ScoreKind.DETERMINISTIC, passed=True),
            Verdict(dimension="groundedness", kind=ScoreKind.JUDGE, passed=True, score=0.9,
                    explanation="panel 2✓/0✗/0? — supported",
                    votes=[{"model": "a/x", "verdict": "pass"}, {"model": "b/y", "verdict": "pass"}]),
            Verdict(dimension="content_safety", kind=ScoreKind.JUDGE, passed=None,
                    explanation="judge inputs absent — abstained"),
        ]
        _sumr = online.judge_verdict_summary(_mix)
        check("summary keeps ONLY judge-kind verdicts", [r["dimension"] for r in _sumr]
              == ["groundedness", "content_safety"])
        check("summary passes votes through with attribution",
              _sumr[0].get("votes") and _sumr[0]["votes"][0] == {"model": "a/x", "verdict": "pass"})
        check("an abstained judge stays visible (passed=None, no votes key)",
              _sumr[1]["passed"] is None and "votes" not in _sumr[1])
        check("summary survives malformed input", online.judge_verdict_summary([None, 42]) == [])

        # deterministic scoring (0 tokens) → clean no-op (nothing billed / broadcast)
        consumption_calls.clear(); turn_count_calls.clear(); fq.items.clear(); persisted_events.clear()
        _loop.run_until_complete(online._record_score_usage(
            "u1", "s1", 7, 0, 0, 0.0, event_handler=None))
        _loop.run_until_complete(asyncio.sleep(0.02))
        check("deterministic (0 tokens) → nothing billed", not consumption_calls)
        check("deterministic (0 tokens) → no token_update broadcast", not fq.items)
        check("deterministic (0 tokens) → no judge step persisted", not persisted_events)
    finally:
        sm.update_token_count, sm.get_session, sm.update_turn_token_counts = o_utc, o_gs, o_uttc
        sm.append_system_events_to_turn = o_ase
        cmmod.ConsumptionManager, dbmod.get_db_session = o_cm, o_gdb
        if o_nq is not None:
            cfg.APP_STATE["notification_queues"] = o_nq


def t_judge_condemnation():
    """PROP-001 (surfaced per PROP-002) — a DELIVERED answer condemned by a
    groundedness-family judge becomes a danger incident in the Assurance Inbox;
    a withheld one does not (the gate already acted — no double count)."""
    from types import SimpleNamespace as NS
    from trusted_data_agent.core import trust_events
    rec, bn = [], []
    o_rec, o_bn = trust_events.record_event, trust_events.broadcast_notice
    trust_events.record_event = lambda *a, **k: (rec.append((a, k)), "id1")[1]
    trust_events.broadcast_notice = lambda *a, **k: bn.append((a, k))
    try:
        v_fail = NS(kind="judge", dimension="groundedness", passed=False, score=0.2)
        v_det = NS(kind="metric", dimension="grounded_overlap", passed=False, score=0.1)
        v_ok = NS(kind="judge", dimension="groundedness", passed=True, score=0.9)
        # Delivered + judged + failed → one danger incident + live notice.
        online._record_judge_condemnation("u1", "s1", 3, "@OPTIM", [v_fail, v_det],
                                          {"grounding": {"decision": "pass"}}, judged=True)
        check("condemnation records a danger incident",
              len(rec) == 1 and rec[0][0][1] == "judge_condemnation"
              and rec[0][1].get("severity") == "danger")
        check("condemnation broadcasts with danger severity",
              len(bn) == 1 and bn[0][1].get("severity") == "danger")
        check("profile tag stripped of @", rec[0][1].get("profile_tag") == "OPTIM")
        check("payload names the condemning dimensions",
              rec[0][1].get("payload", {}).get("dimensions") == ["groundedness"])
        # Withheld → nothing (the gate already acted; GROUNDED is not lit twice).
        rec.clear(); bn.clear()
        online._record_judge_condemnation("u1", "s1", 3, "OPTIM", [v_fail],
                                          {"grounding": {"decision": "block"}}, judged=True)
        check("withheld turn records nothing (no double count)", not rec and not bn)
        # No judge ran → nothing (a deterministic failure is not a condemnation).
        online._record_judge_condemnation("u1", "s1", 3, "OPTIM", [v_fail], {}, judged=False)
        check("unjudged turn records nothing", not rec)
        online._record_judge_condemnation("u1", "s1", 3, "OPTIM", [v_det, v_ok], {}, judged=True)
        check("passing judge / deterministic failure alone records nothing", not rec)
    finally:
        trust_events.record_event, trust_events.broadcast_notice = o_rec, o_bn


def t_judge_prefs():
    """PROP-002 follow-up — per-profile JUDGED-station resolution (pure)."""
    r = online.resolve_judge_prefs
    # inherit follows the admin default, both ways
    check("inherit follows admin on", r({}, include_default=True, default_rate=0.05)["wanted"] is True)
    check("inherit follows admin off", r({}, include_default=False, default_rate=0.05)["wanted"] is False)
    check("no profile at all inherits", r(None, include_default=True, default_rate=0.05)["wanted"] is True)
    # per-profile override, both directions
    check("profile 'on' overrides admin off",
          r({"judgeConfig": {"judges": "on"}}, include_default=False, default_rate=0.05)["wanted"] is True)
    check("profile 'off' overrides admin on",
          r({"judgeConfig": {"judges": "off"}}, include_default=True, default_rate=0.05)["wanted"] is False)
    # sample rate: override, clamp, malformed falls back (a typo must not disable judging)
    check("rate override wins",
          r({"judgeConfig": {"sampleRate": 1.0}}, include_default=True, default_rate=0.05)["rate"] == 1.0)
    check("rate clamps to [0,1]",
          r({"judgeConfig": {"sampleRate": 7}}, include_default=True, default_rate=0.05)["rate"] == 1.0)
    check("absent rate inherits the admin rate",
          r({"judgeConfig": {}}, include_default=True, default_rate=0.25)["rate"] == 0.25)
    check("malformed rate falls back",
          r({"judgeConfig": {"sampleRate": "lots"}}, include_default=True, default_rate=0.25)["rate"] == 0.25)
    # inbox condemnation: default ON, explicit opt-out only
    check("inbox default on", r({}, include_default=True, default_rate=0.05)["inbox"] is True)
    check("inbox explicit opt-out",
          r({"judgeConfig": {"inboxOnCondemnation": False}}, include_default=True, default_rate=0.05)["inbox"] is False)
    # a malformed judgeConfig block degrades to the defaults
    check("non-dict judgeConfig degrades",
          r({"judgeConfig": "broken"}, include_default=True, default_rate=0.05)["wanted"] is True)
    # portability: judgeConfig is registered in the ONE profile-config contract
    from trusted_data_agent.core import profile_config_contract as pcc
    spec = pcc.spec_for("judgeConfig")
    check("judgeConfig is in the contract registry", spec is not None)
    check("judgeConfig is portable but UNSIGNED (measurement config)",
          spec is not None and spec.portable and not spec.signed)


def main():
    print("== WS3 W3.4 online-eval hook (CI-pure) ==")
    for t in (t_judge_sampling, t_attestation, t_span, t_hook, t_hook_judges, t_jsonl_exporter, t_default_thresholds, t_banded_thresholds, t_judge_event_names_its_layer,
              t_score_usage_accounting, t_judge_condemnation, t_judge_prefs,
              t_judges_run_concurrently, t_judge_layer_budget_is_satisfiable,
              t_judge_timeout_keeps_completed_verdicts, t_cut_judges_are_recorded_not_silently_lost,
              t_one_broken_judge_does_not_lose_the_panel):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
