"""
Module tests — OBDA-READ (Ontology-Based Data Access), the observable governed-data lane.

CI-pure. Covers: the pure scanner (governed compile vs free-formed SQL vs mixed vs none), the
trace + conversation-events adapters, the Live Status event builder, and the governed_data_ratio
eval scorer — the two ends (observability + eval) that consume the same scan.

Run:  python test/test_obda.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

from trusted_data_agent.agent import obda  # noqa: E402
from trusted_data_agent.eval.types import EvalCase, RunRecord  # noqa: E402
from trusted_data_agent.eval.scorer import scorers_for  # noqa: E402
import trusted_data_agent.eval.scorers as _scorers  # noqa: E402,F401 (registers)

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


# canonical tool-call shapes
_COMPILE = {"tool_name": "TDA_KnowledgeGraph",
            "arguments": {"action": "compile_metric", "metric_name": "Revenue"},
            "result": {"metadata": {"realization": "view", "deployed_view": "sales.rev_v"}}}
_RAWSQL = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT * FROM orders"},
           "result": {"status": "success"}}
_OTHER = {"tool_name": "base_tableList", "arguments": {"database_name": "db"}, "result": {}}
_KG_OTHER = {"tool_name": "TDA_KnowledgeGraph", "arguments": {"action": "visualize"}, "result": {}}


def main():
    print("=" * 68)
    print("OBDA-READ — Ontology-Based Data Access — module tests")
    print("=" * 68)

    # ── scanner: the four lane shapes ──
    s = obda.scan_obda([_COMPILE])
    check("governed compile → 1 governed, 0 free-form", s["governed_count"] == 1 and s["freeform_count"] == 0)
    check("governed compile → ratio 1.0", s["governed_ratio"] == 1.0 and s["has_data_ops"])
    check("governed op carries metric + realization + deployed",
          s["governed_ops"][0]["metric"] == "Revenue" and s["governed_ops"][0]["realization"] == "view"
          and s["governed_ops"][0]["deployed"] == "sales.rev_v")

    s = obda.scan_obda([_RAWSQL])
    check("raw SQL → 0 governed, 1 free-form", s["governed_count"] == 0 and s["freeform_count"] == 1)
    check("raw SQL → ratio 0.0", s["governed_ratio"] == 0.0 and s["has_data_ops"])

    s = obda.scan_obda([_COMPILE, _RAWSQL, _RAWSQL])
    check("mixed → 1 governed, 2 free-form", s["governed_count"] == 1 and s["freeform_count"] == 2)
    check("mixed → ratio 0.3333", abs(s["governed_ratio"] - 0.3333) < 0.001)

    s = obda.scan_obda([_OTHER, _KG_OTHER])
    check("non-data ops (list / visualize) → no data ops", s["has_data_ops"] is False)
    check("no data ops → ratio None", s["governed_ratio"] is None)

    check("empty → no data ops", obda.scan_obda([])["has_data_ops"] is False)
    check("malformed entries tolerated", obda.scan_obda([None, {}, 3])["has_data_ops"] is False)

    # ── the trace adapter (Optimize turn_action_history) ──
    trace = [
        {"action": {"tool_name": "TDA_KnowledgeGraph",
                    "arguments": {"action": "compile_metric", "metric_name": "AOV"}}, "result": {}},
        {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}, "result": {}},
        {"action": {}, "result": {}},  # no tool_name → skipped
    ]
    calls = obda.calls_from_trace(trace)
    check("trace adapter yields 2 calls", len(calls) == 2)
    s = obda.scan_obda(calls)
    check("trace adapter → 1 governed 1 free-form", s["governed_count"] == 1 and s["freeform_count"] == 1)

    # ── the conversation-events adapter (ReAct engine) ──
    events = [
        {"type": "conversation_tool_invoked",
         "payload": {"tool_name": "TDA_KnowledgeGraph", "arguments": {"action": "compile_metric", "metric_name": "R"}}},
        {"type": "conversation_tool_completed", "payload": {"success": True}},
        {"type": "conversation_tool_invoked",
         "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 2"}}},
        {"type": "conversation_tool_completed", "payload": {"success": True}},
    ]
    cc = obda.calls_from_conversation_events(events)
    check("conversation adapter yields 2 calls", len(cc) == 2)
    s = obda.scan_obda(cc)
    check("conversation adapter → 1 governed 1 free-form", s["governed_count"] == 1 and s["freeform_count"] == 1)

    # ── the Live Status event builder (returns the details dict, like _grounding_event) ──
    ev = obda.build_event(obda.scan_obda([_COMPILE]))
    check("event: lane=governed when no free-form", ev["lane"] == "governed")
    check("event: 100% governed", ev["governed_pct"] == 100)
    check("event: carries operations", len(ev["operations"]) == 1 and ev["operations"][0]["metric"] == "Revenue")
    check("event: lane=mixed", obda.build_event(obda.scan_obda([_COMPILE, _RAWSQL]))["lane"] == "mixed")

    # ── Phase B4: the data-product surface joins the governed lane ──
    _QP_SQL = 'SELECT "region", COUNT(*) FROM "mvm"."orders" GROUP BY "region"'
    _QP = {"tool_name": "TDA_KnowledgeGraph",
           "arguments": {"action": "query_product", "product_name": "customer_orders"},
           "result": {"metadata": {"sql": _QP_SQL, "lane": "governed",
                                   "product": "customer_orders"}}}
    s = obda.scan_obda([_QP])
    check("query_product is a governed operation", s["governed_count"] == 1
          and s["freeform_count"] == 0)

    # Executing the COMPILED governed statement through the agent's own data tool
    # IS the governed lane (the component compiles, never executes — the
    # probe_bridge doctrine); a paired execution must not read as free-forming.
    _EXEC_GOV = {"tool_name": "base_readQuery",
                 "arguments": {"sql": _QP_SQL + ";"},   # trailing ; tolerated
                 "result": {"status": "success"}}
    s = obda.scan_obda([_QP, _EXEC_GOV])
    check("executing the compiled product SQL counts GOVERNED, not free-formed",
          s["governed_count"] == 2 and s["freeform_count"] == 0)
    check("…and the execution is named as execute_governed",
          any(g.get("op") == "execute_governed" for g in s["governed_ops"]))

    # A DIFFERENT raw statement in the same turn stays honestly free-formed —
    # the dedupe matches the exact compiled text, never "the agent used SQL".
    _EXEC_OTHER = {"tool_name": "base_readQuery",
                   "arguments": {"sql": "SELECT * FROM somewhere_else"},
                   "result": {}}
    s = obda.scan_obda([_QP, _EXEC_GOV, _EXEC_OTHER])
    check("a different raw statement in the same turn stays free-formed",
          s["governed_count"] == 2 and s["freeform_count"] == 1)
    # whitespace-normalized matching (an executor may reflow the statement)
    _EXEC_WS = {"tool_name": "base_readQuery",
                "arguments": {"sql": _QP_SQL.replace(" ", "\n  ", 3)},
                "result": {}}
    s = obda.scan_obda([_QP, _EXEC_WS])
    check("whitespace reflow of the governed statement still matches",
          s["governed_count"] == 2 and s["freeform_count"] == 0)
    check("event: lane=free-form when no governed", obda.build_event(obda.scan_obda([_RAWSQL]))["lane"] == "free-form")
    check("event: None when no data ops", obda.build_event(obda.scan_obda([_OTHER])) is None)

    # ── the eval scorer (all-engine via RunRecord) ──
    from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402

    def _score(turn, profile_class="tool_enabled", assertions=None):
        case = EvalCase(id="o", query="q", profile_class=profile_class, assertions=assertions or {})
        return {v.dimension: v for v in (s.score(case, RunRecord(turn))
                for s in scorers_for(profile_class))}

    mixed_turn = {"profile_type": "tool_enabled", "status": "success",
                  "execution_trace": [
                      {"action": {"tool_name": "TDA_KnowledgeGraph",
                                  "arguments": {"action": "compile_metric", "metric_name": "R"}}, "result": {}},
                      {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}, "result": {}}]}

    # OBDA-READ global admin gate OFF (the default): the scorer ABSTAINS even on a real data turn,
    # so a deployment without a governed-metric layer is never banded on the preview lane.
    _OP.force("read", False)
    v = _score(mixed_turn)
    check("scorer: gate OFF (default) → abstains on a real data turn (passed None, no score)",
          v["governed_data_ratio"].passed is None and v["governed_data_ratio"].score is None)
    v = _score(mixed_turn, assertions={"min_governed_data_ratio": 0.75})
    check("scorer: gate OFF → abstains even WITH a threshold (never bands)",
          v["governed_data_ratio"].passed is None and v["governed_data_ratio"].score is None)

    # Gate ON — the scoring logic itself (a deployment that adopts OBDA-READ opts in).
    _OP.force("read", True)
    v = _score(mixed_turn)
    check("scorer registered + runs", "governed_data_ratio" in v)
    check("scorer: reports 50% governed (no threshold → abstain-with-value)",
          v["governed_data_ratio"].passed is None and abs(v["governed_data_ratio"].score - 0.5) < 0.001)

    v = _score(mixed_turn, assertions={"min_governed_data_ratio": 0.75})
    check("scorer: below threshold → not passed (banded)", v["governed_data_ratio"].passed is False)
    v = _score({"profile_type": "tool_enabled",
                "execution_trace": [{"action": {"tool_name": "TDA_KnowledgeGraph",
                    "arguments": {"action": "compile_metric", "metric_name": "R"}}, "result": {}}]},
               assertions={"min_governed_data_ratio": 0.75})
    check("scorer: 100% governed → passes threshold", v["governed_data_ratio"].passed is True)

    no_data = {"profile_type": "tool_enabled", "execution_trace": [
        {"action": {"tool_name": "TDA_FinalReport", "arguments": {}}, "result": {}}]}
    v = _score(no_data)
    check("scorer: no data ops → abstains (passed None, no score)",
          v["governed_data_ratio"].passed is None and v["governed_data_ratio"].score is None)

    # ── the mechanism reader (GROUNDED's Governed-data station reading) ──
    # Gate OFF → the scan writes no `obda` stamp, so the reader reads `na` (structurally
    # absent): GROUNDED never sees the advisory warn. This is the fix for the permanent
    # orange. WITH a free-formed stamp (gate ON path) it reads the advisory warn, at GROUNDED.
    from trusted_data_agent.agent.assurance_points import _read_obda  # noqa: E402
    r_absent = _read_obda({}, "tool_enabled")
    check("reader: no obda stamp (gate OFF) → na", r_absent.state == "na")
    check("reader: na reading is at GROUNDED, not JUDGED", r_absent.station == "grounded")
    r_free = _read_obda({"obda": {"lane": "free", "governed_count": 0, "freeform_count": 1}},
                        "tool_enabled")
    check("reader: free-formed stamp → advisory warn", r_free.state == "warn")
    check("reader: warn reading is at GROUNDED (matches its AssurancePoint)",
          r_free.station == "grounded")

    # ── OBDA-READ Phase C4: a DENIED call is neither lane (it never executed) ──
    # The live acceptance turn — 3 raw-SQL attempts, all denied by the data surface,
    # ZERO rows returned — read "3 free-formed operations / lane free-form" before
    # this fix: the scanner counting the confinement WORKING as the escape it
    # prevented, and governed_data_ratio then punishing the agent for being confined.
    _DENIED = {"tool_name": "base_readQuery",
               "arguments": {"sql": "SELECT * FROM fitness_db.Customers"},
               "result": {"status": "error", "metadata": {
                   "agent_denied": True,
                   "policy_reason": "this agent is surface-only — raw SQL is denied"}}}
    s_conf = obda.scan_obda([_DENIED, _DENIED, _DENIED])
    check("denied calls are counted in their own bucket", s_conf["denied_count"] == 3)
    check("denied calls are NOT free-formed (nothing executed)", s_conf["freeform_count"] == 0)
    check("denied calls are NOT governed either", s_conf["governed_count"] == 0)
    check("denied-only turn has no data ops (the ratio scorer abstains)",
          s_conf["has_data_ops"] is False and s_conf["governed_ratio"] is None)
    ev_conf = obda.build_event(s_conf)
    check("denied-only turn still gets a card (the denial is worth showing)",
          isinstance(ev_conf, dict))
    check("denied-only lane reads 'confined', never 'free-form'",
          ev_conf["lane"] == "confined" and ev_conf["denied_count"] == 3)
    # An UNdenied raw call alongside denials still counts free-formed (nothing hides).
    s_mix = obda.scan_obda([_DENIED, _RAWSQL])
    check("a real free-formed call beside a denial still counts",
          s_mix["freeform_count"] == 1 and s_mix["denied_count"] == 1)
    check("mixed denial+freeform lane is not 'confined'",
          obda.build_event(s_mix)["lane"] != "confined")
    # The mechanism reader: confinement holding is the mechanism WORKING → pass.
    r_conf = _read_obda({"obda": obda.build_event(s_conf)}, "tool_enabled")
    check("reader: confined stamp → pass (never a warn for being confined)",
          r_conf.state == "pass")
    check("reader: confined reading names the denials",
          "denied" in (r_conf.detail or r_conf.summary or str(r_conf)))

    # ── O2 (scope §9): governed consumptions carry SEALED receipts ──
    _QP = {"tool_name": "TDA_KnowledgeGraph",
           "arguments": {"action": "query_product", "product": "fitness_sales"},
           "result": {"metadata": {"product": "fitness_sales", "version": 1,
                                   "request_hash": "a" * 64,
                                   "trust_state": "trusted",
                                   "sql": "SELECT state, SUM(x) FROM t GROUP BY state"}}}
    _QP_EXEC = {"tool_name": "base_readQuery",
                "arguments": {"sql": "SELECT  state, SUM(x) FROM t GROUP BY state;"},
                "result": {"status": "success", "results": [{"s": 1}, {"s": 2}, {"s": 3}]}}
    s_o2 = obda.scan_obda([_QP, _QP_EXEC])
    ev_o2 = obda.build_event(s_o2)
    rcs = obda.consumption_receipts(ev_o2)
    check("a query_product compile yields exactly one receipt", len(rcs) == 1)
    r = rcs[0] if rcs else {}
    check("the receipt carries product identity + request hash + trust",
          r.get("product") == "fitness_sales" and r.get("version") == 1
          and r.get("request_hash") == "a" * 64 and r.get("trust_state") == "trusted")
    check("the receipt carries the compiled statement's hash (64 hex)",
          len(r.get("sql_hash") or "") == 64)
    check("the PAIRED execution supplies row_count (whitespace/; tolerant)",
          r.get("row_count") == 3)
    # The sealed content is deterministic and closed — byte-reproducible at
    # verification time from the stamped event alone.
    c1 = obda.receipt_content(r)
    c2 = obda.receipt_content(dict(r, extra_presentation_field="MUST NOT LEAK"))
    check("receipt content is closed (a stray field never enters the seal)", c1 == c2)
    check("receipt content is deterministic", c1 == obda.receipt_content(dict(r)))
    # A REJECTed compile consumed nothing — no receipt.
    _REJ = {"tool_name": "TDA_KnowledgeGraph",
            "arguments": {"action": "query_product", "product": "fitness_sales"},
            "result": {"metadata": {"error": "unknown measure", "sql": None}}}
    check("a REJECTed compile yields NO receipt",
          obda.consumption_receipts(obda.build_event(obda.scan_obda([_REJ]))) == [])
    # compile_metric (Phase 0) is governed but carries no product receipt — unchanged.
    check("compile_metric ops are unchanged (no phantom receipt)",
          obda.consumption_receipts(obda.build_event(obda.scan_obda([_COMPILE]))) == [])

    # ── O2: the EPC seam + offline verification round-trip ──
    from trusted_data_agent.core.provenance import ProvenanceChain, verify_content
    _ch = ProvenanceChain(session_id="s", turn_number=1, user_uuid="u",
                          profile_type="tool_enabled")
    for op in obda.consumption_receipts(ev_o2):
        _ch.add_step("obda.consumption", obda.receipt_content(op), "receipt")
    _env = _ch.finalize()
    check("the receipt seals as a chain step", len(_env.get("provenance_chain") or []) == 1)
    v_ok = verify_content(_env, {"obda": ev_o2})
    check("offline verification re-derives the receipt from the turn's own stamp",
          v_ok["verified"] == 1 and not v_ok["mismatches"])
    # Tamper: the stamped record shows a different row_count than was sealed.
    import copy as _copy
    ev_bad = _copy.deepcopy(ev_o2)
    for op in obda.consumption_receipts(ev_bad):
        op["row_count"] = 9999
    v_bad = verify_content(_env, {"obda": ev_bad})
    check("a tampered record is a NAMED mismatch, never a silent skip",
          v_bad["mismatches"] and "obda.consumption" in v_bad["mismatches"][0])
    check("a turn with no obda stamp at all is a mismatch too (a receipt for a "
          "consumption the record does not show)",
          verify_content(_env, {})["mismatches"])

    # ── O2: the ONE executor seam seals receipts into the live chain ──
    # Driven through the REAL `_run_obda_scan` (unbound, faithful stand-in self)
    # so the sealing site itself is covered — a gate on the pure helpers alone
    # would pass with the seam deleted.
    from types import SimpleNamespace
    from trusted_data_agent.agent.executor import PlanExecutor
    _ch2 = ProvenanceChain(session_id="s", turn_number=1, user_uuid="u",
                           profile_type="tool_enabled")
    _fake = SimpleNamespace(provenance=_ch2)
    ev_seam = PlanExecutor._run_obda_scan(_fake, [_QP, _QP_EXEC])
    _steps = [st for st in _ch2.steps if st.get("step_type") == "obda.consumption"]
    check("the scan seam seals one obda.consumption step per receipt", len(_steps) == 1)
    check("the sealed summary names the product and rows",
          "fitness_sales" in (_steps[0].get("content_summary") or "")
          and "3 row" in (_steps[0].get("content_summary") or ""))
    _env2 = _ch2.finalize()
    v_seam = verify_content(_env2, {"obda": ev_seam})
    check("the seam-sealed step verifies against the returned event",
          v_seam["verified"] == 1 and not v_seam["mismatches"])
    # A sealed chain must never be appended to (the guard, not an exception).
    _fake_sealed = SimpleNamespace(provenance=_ch2)   # _ch2 is now sealed
    ev_after = PlanExecutor._run_obda_scan(_fake_sealed, [_QP, _QP_EXEC])
    check("a sealed chain is left alone (event still returned)",
          ev_after is not None and len(_ch2.steps) == 1)
    # No provenance attr at all (a caller without a chain) → event unaffected.
    ev_nochain = PlanExecutor._run_obda_scan(SimpleNamespace(), [_QP, _QP_EXEC])
    check("no chain on the executor → scan still returns the event",
          ev_nochain is not None and obda.consumption_receipts(ev_nochain))

    # ── the strategic_plan verification fix O2's live acceptance surfaced ──
    # The seal site hashes the POST-refinement plan (stored as `original_plan`);
    # verify_content compared against `raw_llm_plan` (pre-rewrite), so any turn
    # whose rewrite passes changed the plan read as a FALSE mismatch.
    import json as _json
    _raw_plan = [{"phase": 1, "goal": "g", "arguments": {"x": 1, "unused": None}}]
    _refined = [{"phase": 1, "goal": "g", "arguments": {"x": 1}}]   # a rewrite pruned a key
    _ch3 = ProvenanceChain(session_id="s", turn_number=1, user_uuid="u",
                           profile_type="tool_enabled")
    _ch3.add_step("strategic_plan", _json.dumps(_refined, sort_keys=True, default=str), "plan")
    _env3 = _ch3.finalize()
    v = verify_content(_env3, {"original_plan": _refined, "raw_llm_plan": _raw_plan})
    check("a rewritten plan verifies against original_plan (no false mismatch)",
          v["verified"] == 1 and not v["mismatches"])
    v = verify_content(_env3, {"raw_llm_plan": _refined})
    check("an older record with only raw_llm_plan still verifies (fallback)",
          v["verified"] == 1 and not v["mismatches"])
    v = verify_content(_env3, {"original_plan": [{"phase": 1, "goal": "TAMPERED"}],
                               "raw_llm_plan": _raw_plan})
    check("a genuinely tampered plan is still a NAMED mismatch",
          v["mismatches"] and "strategic_plan" in v["mismatches"][0])

    # ── E5 (scope §7.2): the empirical monitor — a breach is a hole report ──
    # Free-formed execution by a SURFACE-ONLY agent means the structural
    # confinement LEAKED. Driven through the REAL _record_surface_breach with
    # the REAL contextvar principal; the event sink is captured, never written.
    from trusted_data_agent.core import agent_authority as aa
    from trusted_data_agent.core import trust_events as te
    recorded = []
    _orig_rec, _orig_bc = te.record_event, te.broadcast_notice
    te.record_event = lambda *a, **k: recorded.append((a, k)) or "id"
    te.broadcast_notice = lambda *a, **k: None
    try:
        _self = SimpleNamespace(user_uuid="u", session_id="s", current_turn_number=1)
        FREE_EV = {"freeform_count": 2, "governed_count": 0, "lane": "free-form"}
        def _with_scope(cfg):
            tok = aa.set_current_principal(aa.derive_principal(
                {"id": "p1", "authorityConfig": {"autonomous": True, "dataSurface": cfg}}, "u"))
            try:
                PlanExecutor._record_surface_breach(_self, FREE_EV)
            finally:
                aa.reset_current_principal(tok)
        _with_scope({"products": ["p"], "exclusive": True, "advisory": False})
        check("surface-only + free-formed execution → a breach is RECORDED",
              len(recorded) == 1)
        if recorded:
            a, k = recorded[0]
            check("…as a DANGER incident named surface_breach",
                  a[1] == "surface_breach" and k.get("severity") == "danger")
            check("…whose summary says hole report, not agent misbehaviour",
                  "hole report" in a[2])
        recorded.clear()
        _with_scope({"products": ["p"], "exclusive": True, "advisory": True})
        check("advisory mode never records a breach (observe-only, E4)",
              recorded == [])
        _with_scope({"products": ["p"], "exclusive": False, "advisory": False})
        check("products-without-exclusive never records (additive, E3)",
              recorded == [])
        tok = aa.set_current_principal(aa.derive_principal(
            {"id": "p1", "authorityConfig": {"autonomous": True,
                "dataSurface": {"products": ["p"], "exclusive": True}}}, "u"))
        try:
            PlanExecutor._record_surface_breach(
                _self, {"freeform_count": 0, "governed_count": 3, "lane": "governed"})
        finally:
            aa.reset_current_principal(tok)
        check("a governed turn never records a breach", recorded == [])
        PlanExecutor._record_surface_breach(_self, FREE_EV)   # no principal at all
        check("no ambient principal (interactive turn) → never records",
              recorded == [])
    finally:
        te.record_event, te.broadcast_notice = _orig_rec, _orig_bc

    # ── rejected_count: a contract REFUSAL is not a data operation that ran ──
    # Operator finding (session 2fcbb51f): an M4 allowlist REJECT was counted as
    # one of "3 governed data operations". The truth table: a REJECTed
    # query_product moves NO data — reported beside the lanes, never inside one.
    print("\n[rejected_count] contract refusals are governance working, not data moving")
    _REJ = {"tool_name": "TDA_KnowledgeGraph",
            "arguments": {"action": "query_product", "product_name": "support_load",
                          "measures": ["ticket_count"],
                          "dimensions": ["Customers.CustomerName"]},
            "result": {"status": "error",
                       "error": "query_product rejected (unknown_member): 'Customers' "
                                "does not expose attribute 'CustomerName' "
                                "(the allowlist governs — M4)."}}
    _QP2 = {"tool_name": "TDA_KnowledgeGraph",
            "arguments": {"action": "query_product", "product_name": "support_load"},
            "result": {"metadata": {"sql": 'SELECT "s", COUNT(*) FROM "t" GROUP BY "s"',
                                    "lane": "governed", "product": "support_load"}}}
    _EX2 = {"tool_name": "base_readQuery",
            "arguments": {"sql": 'SELECT "s", COUNT(*) FROM "t" GROUP BY "s"'},
            "result": {"status": "success"}}
    s = obda.scan_obda([_REJ, _QP2, _EX2])   # the operator's exact 3-call shape
    check("the operator's turn: 2 governed, not 3", s["governed_count"] == 2)
    check("the REJECT is counted as rejected", s["rejected_count"] == 1)
    check("the REJECT is not free-formed either", s["freeform_count"] == 0)
    check("ratio unpolluted by the refusal", s["governed_ratio"] == 1.0)
    # both REJECT result shapes count (status=error / bare error field)
    _REJ2 = {"tool_name": "TDA_KnowledgeGraph",
             "arguments": {"action": "query_product", "product_name": "p"},
             "result": {"error": "query_product rejected (unknown_measure): nope"}}
    check("a bare error field also reads rejected",
          obda.scan_obda([_REJ2])["rejected_count"] == 1)
    # a successful query_product (status absent) is NEVER mistaken for a reject
    check("a status-less success stays governed",
          obda.scan_obda([_QP2])["rejected_count"] == 0
          and obda.scan_obda([_QP2])["governed_count"] == 1)
    # DEFENSE: a hostile reject carrying sql metadata must not allowlist a later
    # raw execution as execute_governed (pass-1 skips rejects too)
    _REJ_SQL = {"tool_name": "TDA_KnowledgeGraph",
                "arguments": {"action": "query_product", "product_name": "p"},
                "result": {"status": "error", "error": "rejected",
                           "metadata": {"sql": "SELECT secret FROM t"}}}
    _RAW_SAME = {"tool_name": "base_readQuery",
                 "arguments": {"sql": "SELECT secret FROM t"}, "result": {}}
    s = obda.scan_obda([_REJ_SQL, _RAW_SAME])
    check("a rejected compile never allowlists raw SQL",
          s["governed_count"] == 0 and s["freeform_count"] == 1)
    # the event carries the count for the capsule/assurance readings
    ev = obda.build_event(obda.scan_obda([_REJ, _QP2, _EX2]))
    check("event carries rejected_count", ev and ev.get("rejected_count") == 1)
    check("event lane stays governed (the refusal is not a lane)",
          ev and ev.get("lane") == "governed")
    # honest abstain: a reject-ONLY turn moved no data → no event (the honesty
    # gate + the REJECT correction card carry that story, not the lane card)
    check("a reject-only turn abstains (no data ran)",
          obda.build_event(obda.scan_obda([_REJ])) is None)

    # ── profile_obda: THE per-agent tri-state (operator model, 2026-08-17) ──
    print("\n[profile_obda] Option A default · B exclusive · C mixed · list = grant")
    _OP.force("read", True)
    P = obda.profile_obda
    check("no profile → A", P(None) == ("off", frozenset()))
    check("no authorityConfig → A (every unconfigured profile)",
          P({}) == ("off", frozenset()))
    check("dataSurface null → A", P({"authorityConfig": {"dataSurface": None}})
          == ("off", frozenset()))
    check("an EMPTY product list → A (a grant of nothing is no grant)",
          P({"authorityConfig": {"dataSurface": {"products": [],
                                                 "exclusive": True}}})
          == ("off", frozenset()))
    check("products + exclusive → B",
          P({"authorityConfig": {"dataSurface": {"products": ["a"],
                                                 "exclusive": True}}})
          == ("exclusive", frozenset({"a"})))
    check("products without exclusive → C",
          P({"authorityConfig": {"dataSurface": {"products": ["a", "b"]}}})
          == ("mixed", frozenset({"a", "b"})))
    check("the legacy identityConfig alias still resolves",
          P({"identityConfig": {"dataSurface": {"products": ["x"],
                                                "exclusive": True}}})[0]
          == "exclusive")
    check("a malformed block reads A, never raises",
          P({"authorityConfig": "broken"}) == ("off", frozenset()))
    _OP.force("read", False)
    check("master off forces A whatever the profile says",
          P({"authorityConfig": {"dataSurface": {"products": ["a"],
                                                 "exclusive": True}}})
          == ("off", frozenset()))

    _OP.force("read", False)  # restore the module default so nothing leaks

    print("\n" + "=" * 68)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 68)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
