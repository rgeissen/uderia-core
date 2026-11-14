"""KG seed utilization — the semantic channel finally has a NUMBER (Sep 2026).

Gate for the increment that followed the dead-channel finding: the channel was down for
18 days and nothing measured it (the bench disables it for determinism; no post-turn
reading aggregated `bridged`). Covered here:

  §1  `kgstore.usage.touched_entities` — THE rule (whole-identifier leaf match in tool
      ARGUMENTS only), its stop set, both consumers reading one rule.
  §2  `record_used_async` — the `used` telemetry that was declared and never recorded.
  §3  `RunRecord` accessors for the seeding report / bridged / rendered names.
  §4  `KgSeedUtilizationScorer` — every abstain path is an abstention (never a pass),
      the measured path is informational, registered for the tool classes only.
  §5  the partition: owned at GROUNDED (trusted_scope), staged at RETRIEVE, mirrored
      on the ring.
  §6  the recording seam: `_record_kg_used` beside `_run_obda_scan` at BOTH engines,
      NOT gated on OBDA-READ; functional through the real method.
  §7  the event carries WHICH graph enriched the turn + the rendered names (item 2).
  §8  `core.log_once.warn_once` — one WARNING per (key, process), and the three KG
      seams route through it (item 3).

CI-pure: no server, no model, no real graph. Run: PYTHONPATH=src python3 test/test_kg_seed_utilization.py
"""
import ast
import asyncio
import inspect
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def run(fn):
    """A raising test is a NAMED failure, never an aborted run."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")


from trusted_data_agent.kgstore import usage as U  # noqa: E402

SQL_CALL = {"tool_name": "base_readQuery",
            "arguments": {"sql": "SELECT COUNT(*) FROM NIBRS_Source_Domain.Victim v WHERE v.V40191 = '0'"},
            "result": {"rows": [{"Offender": 1}]}}
TABLE_CALL = {"tool_name": "base_tableList", "arguments": {"database_name": "CASE_Source_Domain"}, "result": None}


def t_touched_rule():
    print("[§1] touched_entities — the ONE rule")
    names = ["NIBRS_Source.Victim", "NIBRS_Source.Incident.V40191", "NIBRS_Source.Offender",
             "NIBRS_Source.Victims", "NIBRS_Source.Incident.id", "CASE_Source_Domain"]
    got = U.touched_entities(names, [SQL_CALL, TABLE_CALL])
    check("a table leaf in the SQL is touched", "NIBRS_Source.Victim" in got, got)
    check("an attribute leaf (column) in the SQL is touched", "NIBRS_Source.Incident.V40191" in got, got)
    check("a name that appears ONLY in a result is NOT touched (results are never scanned)",
          "NIBRS_Source.Offender" not in got, got)
    check("whole-identifier match — `Victims` does not match `Victim`",
          "NIBRS_Source.Victims" not in got, got)
    check("a generic leaf (`id`) is excluded rather than counted everywhere",
          "NIBRS_Source.Incident.id" not in got, got)
    # `count` is 5 chars — past the length floor — and literally present as COUNT(*) in the
    # SQL: ONLY the stop set can exclude it. (A first version of this gate used `id`, which
    # the length floor already excluded, so dropping the stop set escaped — half a gate.)
    check("a generic leaf past the length floor (`count`, present as COUNT(*)) is excluded by the stop set",
          U.touched_entities(["NIBRS_Source.Incident.count"], [SQL_CALL]) == [])
    check("a database name in a non-SQL argument is touched", "CASE_Source_Domain" in got, got)
    check("case-insensitive", U.touched_entities(["x.VICTIM"], [SQL_CALL]) == ["x.VICTIM"])
    check("no tool calls ⇒ nothing touched", U.touched_entities(names, []) == [])
    check("no names ⇒ nothing touched", U.touched_entities([], [SQL_CALL]) == [])
    check("string arguments are scanned too",
          U.touched_entities(["a.Victim"], [{"arguments": "select * from Victim"}]) == ["a.Victim"])
    check("a leaf shorter than the floor is skipped",
          U.touched_entities(["a.ab"], [{"arguments": "ab ab ab"}]) == [])
    check("duplicates count once", U.touched_entities(["a.Victim", "a.Victim"], [SQL_CALL]) == ["a.Victim"])
    frozen = list(names); calls = [dict(SQL_CALL)]
    U.touched_entities(names, calls)
    check("inputs are never mutated", names == frozen and calls[0] == SQL_CALL)
    check("a malformed call (not a dict) is ignored, not fatal",
          U.touched_entities(["a.Victim"], [None, "junk", SQL_CALL]) == ["a.Victim"])


def t_record_used():
    print("[§2] record_used_async — the `used` telemetry")
    import threading
    calls = []
    real_record, real_thread = U.record, threading.Thread

    class _Inline:
        def __init__(self, target=None, **kw): self._t = target
        def start(self): self._t()
    try:
        U.record = lambda kg, names, kind: calls.append((kg, list(names), kind)) or len(names)
        threading.Thread = _Inline
        U.record_used_async("kgA", ["NIBRS_Source.Victim", "NIBRS_Source.Offender"], [SQL_CALL])
        check("records `used` for the touched entities only",
              calls == [("kgA", ["NIBRS_Source.Victim"], "used")], calls)
        calls.clear()
        U.record_used_async("", ["NIBRS_Source.Victim"], [SQL_CALL])
        U.record_used_async("kgA", ["NIBRS_Source.Offender"], [SQL_CALL])
        check("no kg_id or nothing touched ⇒ no write at all", calls == [], calls)
        # a raising recorder never propagates
        U.record = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
        try:
            U.record_used_async("kgA", ["NIBRS_Source.Victim"], [SQL_CALL]); ok = True
        except Exception:  # noqa: BLE001
            ok = False
        check("a failing write never raises into the caller", ok)
    finally:
        U.record, threading.Thread = real_record, real_thread
    src = inspect.getsource(U.record_used_async)
    check("the recorder reads the ONE rule (touched_entities), not a private copy",
          "touched_entities(" in src)


def _turn(seeding=None, calls=True, names=("NIBRS_Source.Victim", "NIBRS_Source.Offender")):
    enr = {"kg_id": "kg-1", "kg_name": "NIBRS", "entity_count": 2, "entity_names": list(names)}
    if seeding is not None:
        enr["semantic_seeding"] = seeding
    t = {"kg_enrichment_event": {"enrichments": [enr]}, "profile_type": "conversation_with_tools",
         "status": "success", "final_summary_text": "ok"}
    if calls:
        t["conversation_agent_events"] = [
            {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery",
                                                              "arguments": SQL_CALL["arguments"]}},
            {"type": "conversation_tool_completed", "payload": {"success": True}}]
    return t


def t_accessors():
    print("[§3] RunRecord accessors")
    from trusted_data_agent.eval.types import RunRecord
    rep = {"state": "ready", "hits": 3, "bridged": [{"name": "NIBRS_Source.Victim", "score": 0.61},
                                                     {"name": "NIBRS_Source.Offender", "score": 0.4}]}
    r = RunRecord(_turn(rep))
    check("kg_seeding_reports returns the stamped report(s)", r.kg_seeding_reports() == [rep])
    check("kg_bridged_entities lists the bridged names in order",
          r.kg_bridged_entities() == ["NIBRS_Source.Victim", "NIBRS_Source.Offender"])
    check("kg_entity_names lists the rendered names",
          r.kg_entity_names() == ["NIBRS_Source.Victim", "NIBRS_Source.Offender"])
    r2 = RunRecord({"kg_enrichment_event": "junk"})
    check("a malformed event yields empties, never raises",
          r2.kg_seeding_reports() == [] and r2.kg_bridged_entities() == [] and r2.kg_entity_names() == [])
    r3 = RunRecord(_turn(None))
    check("an event predating the channel yields no report", r3.kg_seeding_reports() == [])


def t_scorer():
    print("[§4] KgSeedUtilizationScorer")
    from trusted_data_agent.eval.scorers.reliability import KgSeedUtilizationScorer
    from trusted_data_agent.eval.types import RunRecord, EvalCase
    from trusted_data_agent.eval.scorer import scorers_for
    s = KgSeedUtilizationScorer()
    case = EvalCase(id="c", query="q", profile_class="conversation_with_tools")
    bridged2 = {"state": "ready", "hits": 5, "bridged": [{"name": "NIBRS_Source.Victim", "score": 0.6},
                                                          {"name": "NIBRS_Source.Offender", "score": 0.4}]}
    for label, turn in [("no report", _turn(None)),
                        ("state error", _turn({"state": "error", "hits": 10, "bridged": []})),
                        ("state building", _turn({"state": "building", "hits": 0, "bridged": []})),
                        ("ready but nothing bridged", _turn({"state": "ready", "hits": 4, "bridged": []})),
                        ("no tool calls", _turn(bridged2, calls=False))]:
        v = s.score(case, RunRecord(turn))
        check(f"abstains on: {label} (passed=None, no score)", v.passed is None and v.score is None, (v.passed, v.score, v.explanation))
    v = s.score(case, RunRecord(_turn(bridged2)))
    check("measures 1 of 2 bridged entities used ⇒ 0.5", v.score == 0.5, v.score)
    check("informational — never a verdict (passed=None) even when measured", v.passed is None)
    check("evidence names the used AND the unused entity",
          "used: NIBRS_Source.Victim" in v.evidence and "unused: NIBRS_Source.Offender" in v.evidence, v.evidence)
    check("the explanation states the count and names the unused",
          "1 of 2" in v.explanation and "NIBRS_Source.Offender" in v.explanation, v.explanation)
    check("non-gating", s.gating is False)
    for cls in ("tool_enabled", "conversation_with_tools"):
        check(f"registered for {cls}", any(x.dimension == "kg_seed_utilization" for x in scorers_for(cls)))
    for cls in ("llm_only", "rag_focused", "genie"):
        check(f"NOT registered for {cls} (no data tool calls to use anything)",
              not any(x.dimension == "kg_seed_utilization" for x in scorers_for(cls)))
    src = inspect.getsource(KgSeedUtilizationScorer.score)
    check("the scorer reads the ONE rule (touched_entities), not a private copy", "touched_entities(" in src)


def t_partition():
    print("[§5] the partition")
    from trusted_data_agent.agent import assurance_points as AP
    from trusted_data_agent.eval import stages
    check("owned by trusted_scope (GROUNDED)", "kg_seed_utilization" in AP.POINT_DIMENSIONS["trusted_scope"])
    check("counted at GROUNDED by station_dimensions()",
          "kg_seed_utilization" in AP.station_dimensions().get("grounded", ()))
    check("no partition conflict", not AP.partition_conflicts(), AP.partition_conflicts())
    check("staged at RETRIEVE", stages.stage_of("kg_seed_utilization") == stages.RETRIEVE, stages.stage_of("kg_seed_utilization"))
    check("no stage gaps", not stages.partition_gaps(), stages.partition_gaps())
    js = open(os.path.join(ROOT, "static/js/handlers/agentPerformanceHandler.js")).read()
    check("mirrored in the ring's STATION_DIMS.grounded", "'kg_seed_utilization'" in js.split("const STATION_DIMS")[1].split("};")[0])
    qd = open(os.path.join(ROOT, "static/js/handlers/qualityDimensions.js")).read()
    check("declared INFORMATIONAL in the Guide (never a pass/fail claim)",
          "'kg_seed_utilization'" in qd.split("const INFORMATIONAL = new Set([")[1].split("])")[0])
    check("has a logical label + long description in the Guide",
          "kg_seed_utilization:  { label:" in qd and "kg_seed_utilization: 'For KG-grounded" in qd)


def t_seam():
    print("[§6] the recording seam — beside the OBDA-READ scan at BOTH engines, never gated on OBDA-READ")
    from trusted_data_agent.agent import executor as EX
    ex_src = open(EX.__file__).read()
    ce_path = os.path.join(ROOT, "src/trusted_data_agent/agent/engines/conversation_engine.py")
    ce_src = open(ce_path).read()
    check("executor calls _record_kg_used right after _run_obda_scan",
          "self._obda_event = self._run_obda_scan(_kg_calls)\n        self._record_kg_used(_kg_calls)" in ex_src)
    check("conversation engine calls executor._record_kg_used right after the scan",
          "executor._run_obda_scan(_kg_calls)\n            executor._record_kg_used(_kg_calls)" in ce_src)
    body = inspect.getsource(EX.PlanExecutor._record_kg_used)
    check("the seam itself never consults OBDA_ENABLED", "OBDA-READ" not in body)
    # functional: through the REAL method, with a fake self + a captured recorder
    from trusted_data_agent.kgstore import usage as _U
    captured = []
    real = _U.record_used_async
    _U.record_used_async = lambda kid, names, calls: captured.append((kid, list(names), len(calls)))
    try:
        class _Self:
            kg_enrichment_event = {"enrichments": [{"kg_id": "kg-9", "entity_names": ["a.Victim", "a.Offender"]},
                                                   {"kg_id": None, "entity_names": ["b.X"]},
                                                   "junk"]}
        EX.PlanExecutor._record_kg_used(_Self(), [SQL_CALL])
        check("records once per enrichment that carries a kg_id + names (junk / id-less skipped)",
              captured == [("kg-9", ["a.Victim", "a.Offender"], 1)], captured)
        captured.clear()
        EX.PlanExecutor._record_kg_used(_Self(), [])
        _Self.kg_enrichment_event = None
        EX.PlanExecutor._record_kg_used(_Self(), [SQL_CALL])
        check("no tool calls / no event ⇒ records nothing", captured == [], captured)
        _U.record_used_async = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        _Self.kg_enrichment_event = {"enrichments": [{"kg_id": "kg-9", "entity_names": ["a.Victim"]}]}
        try:
            EX.PlanExecutor._record_kg_used(_Self(), [SQL_CALL]); ok = True
        except Exception:  # noqa: BLE001
            ok = False
        check("a raising recorder never breaks the turn", ok)
    finally:
        _U.record_used_async = real


def t_event_identity():
    print("[§7] the event says WHICH graph enriched the turn (item 2)")
    from components.builtin.knowledge_graph import handler as kgh
    cls = kgh.KnowledgeGraphHandler
    src = inspect.getsource(cls.get_context_enrichment)
    blk = src.split("metadata = {")[1].split("}")[0]
    for key in ("kg_id", "kg_name", "entity_names", "kg_owner_profile_id"):
        check(f"metadata carries `{key}`", f'"{key}"' in blk, blk)
    check("kg_name is read from the store's own metadata, best-effort",
          "get_kg_metadata()" in src.split("metadata = {")[0][-1200:])
    ui = open(os.path.join(ROOT, "static/js/ui.js")).read()
    fn = ui.split("function _renderKgEnrichmentDetails(details)")[1].split("\nfunction ")[0]
    check("the card renders a Graph row from kg_name", "kg_name" in fn and ">Graph<" in fn)
    check("…and omits it (never invents one) when the event predates the stamp",
          "if (graphNames.length)" in fn)


def t_warn_once():
    print("[§8] warn_once — one WARNING per (key, process); the three KG seams route through it")
    from trusted_data_agent.core import log_once as LO
    LO._reset_for_tests()
    rec = []

    class _H(logging.Handler):
        def emit(self, r): rec.append((r.levelno, r.getMessage()))
    lg = logging.getLogger("test.warn_once"); lg.setLevel(logging.DEBUG); lg.addHandler(_H())
    a = LO.warn_once(lg, "k1", "first"); b = LO.warn_once(lg, "k1", "second"); c = LO.warn_once(lg, "k2", "other")
    check("first occurrence is a WARNING and reports True", a is True and rec[0] == (logging.WARNING, "first"), rec)
    check("a repeat of the same key is DEBUG and reports False", b is False and rec[1] == (logging.DEBUG, "second"), rec)
    check("a different key is its own first WARNING", c is True and rec[2][0] == logging.WARNING, rec)
    LO._reset_for_tests()
    for i in range(LO._CAP + 5):
        LO.warn_once(lg, f"cap{i}", "m")
    check("the seen-set is bounded (a key with an id component cannot grow it forever)",
          len(LO._SEEN) <= LO._CAP)
    from components.builtin.knowledge_graph import handler as kgh
    from trusted_data_agent.kgstore import semantic_index as si
    hs, ss = open(kgh.__file__).read(), open(si.__file__).read()
    check("handler: bridge-table discovery failure routes through warn_once",
          "kg-bridge-discovery:" in hs and "bridge-table discovery skipped" in hs)
    check("handler: semantic scoring failure routes through warn_once", "kg-semantic-scoring:" in hs)
    check("semantic_index: no-embedder routes through warn_once", "kg-semantic-no-embedder:" in ss)
    check("semantic_index: seeding failure routes through warn_once", "kg-semantic-hits:" in ss)
    check("handler: trusted-scope resolution failure routes through warn_once (the 4th seam)",
          "kg-trusted-scope:" in hs)
    check("persist/load stay DEBUG (a cache that cannot be written still works)",
          "persist skipped" in ss and "load skipped" in ss
          and 'logger.debug(f"KG semantic index: persist skipped' in ss)
    # no KG seam-failure is still swallowed at DEBUG
    leaks = [l.strip() for l in (hs + ss).split("\n")
             if "logger.debug(" in l and any(w in l for w in ("skipped", "failed", "no embedder"))
             and "persist skipped" not in l and "load skipped" not in l]
    check("no other KG seam failure is logged only at DEBUG", not leaks, leaks)


for _fn in (t_touched_rule, t_record_used, t_accessors, t_scorer, t_partition, t_seam, t_event_identity, t_warn_once):
    run(_fn)

print(f"\n=== KG seed utilization: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
