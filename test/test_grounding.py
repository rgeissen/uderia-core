"""Tier 1.1 — grounding primitives (CI-pure, 0-token, no LLM).

The single source of truth for "what scope did a tool call touch" and "what numbers
does a text assert", shared by the live grounding gate and the eval scorers. These
assertions pin the behaviour both sides depend on.

Run:
    PYTHONPATH=src python test/test_grounding.py

See docs/roadmap/TIER_1.1_GROUNDING_GATE.md (§10 module test plan).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.grounding.primitives import (  # noqa: E402
    SYSTEM_DBS, dbs_touched, scope_diff, call_reached_nothing, norm_nums, answer_out_of_scope_refs,
    is_catalog_call,
)
from trusted_data_agent.agent.grounding.gate import GroundingGate  # noqa: E402
from trusted_data_agent.agent.grounding.types import (  # noqa: E402
    Decision, GroundingConfig, GroundingInputs, GroundingVerdict,
)
from trusted_data_agent.agent.grounding import adapter  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _call(args):
    return {"arguments": args}


# ── dbs_touched ────────────────────────────────────────────────────────────────
def t_dbs_touched():
    check("dbs_touched: explicit database_name arg",
          dbs_touched(_call({"database_name": "Restaurants_MVM"})) == {"restaurants_mvm"})
    check("dbs_touched: db_name + DatabaseName aliases",
          dbs_touched(_call({"db_name": "Foo"})) == {"foo"}
          and dbs_touched(_call({"DatabaseName": "Bar"})) == {"bar"})
    check("dbs_touched: FROM <db>.<table> qualifier",
          dbs_touched(_call({"sql": "SELECT * FROM finance.ledger"})) == {"finance"})
    check("dbs_touched: JOIN qualifier picked up alongside FROM",
          dbs_touched(_call({"sql": "SELECT * FROM a.t1 JOIN b.t2 ON 1=1"})) == {"a", "b"})
    check("dbs_touched: WHERE string-literal NOT flagged (only FROM/JOIN qualifiers)",
          dbs_touched(_call({"sql": "SELECT * FROM dbc.tables WHERE DatabaseName='finance'"}))
          == {"dbc"})
    check("dbs_touched: explicit arg + SQL qualifier union",
          dbs_touched(_call({"database_name": "x", "sql": "SELECT * FROM y.t"})) == {"x", "y"})
    check("dbs_touched: empty / missing args → empty set",
          dbs_touched({}) == set() and dbs_touched(_call({})) == set()
          and dbs_touched(None) == set())
    check("dbs_touched: blank database_name ignored",
          dbs_touched(_call({"database_name": "   "})) == set())
    check("dbs_touched: non-string sql ignored (no crash)",
          dbs_touched(_call({"sql": 123})) == set())


# ── norm_nums ──────────────────────────────────────────────────────────────────
def t_norm_nums():
    check("norm_nums: comma grouping stripped",
          norm_nums("revenue was 1,234,567") == {"1234567"})
    check("norm_nums: decimal keeps full + integer part (rounding tolerance)",
          norm_nums("avg 12.5 days") == {"12.5", "12"})
    check("norm_nums: multiple numbers",
          norm_nums("3 of 6 vendors, 230 days") == {"3", "6", "230"})
    check("norm_nums: negative sign retained",
          norm_nums("delta -4.0") == {"-4.0", "-4"})
    check("norm_nums: no numbers → empty",
          norm_nums("no figures here") == set() and norm_nums("") == set()
          and norm_nums(None) == set())


# ── scope_diff ─────────────────────────────────────────────────────────────────
def t_scope_diff():
    scope = {"restaurants_mvm"}
    in_scope = [_call({"database_name": "Restaurants_MVM"}),
                _call({"sql": "SELECT * FROM restaurants_mvm.orders"})]
    out = [_call({"database_name": "airlines_mvm"}),
           _call({"sql": "SELECT * FROM media_broadcasting_mvm.x JOIN finance.y ON 1=1"})]

    check("scope_diff: all in scope → empty",
          scope_diff(scope, in_scope) == set())
    check("scope_diff: out-of-scope DBs surfaced (the 230-day failure shape)",
          scope_diff(scope, out) == {"airlines_mvm", "media_broadcasting_mvm", "finance"})
    check("scope_diff: system/catalog DBs always allowed",
          scope_diff(scope, [_call({"sql": "SELECT * FROM dbc.columns"})]) == set())
    check("scope_diff: system DB explicitly in SYSTEM_DBS",
          "dbc" in SYSTEM_DBS and "system" in SYSTEM_DBS)
    check("scope_diff: empty scope ⇒ every non-system DB is out of scope (caller abstains)",
          scope_diff(set(), [_call({"database_name": "anything"})]) == {"anything"})
    check("scope_diff: no calls → empty",
          scope_diff(scope, []) == set() and scope_diff(scope, None) == set())
    check("scope_diff: scope membership is exact (case already normalised lower)",
          scope_diff({"restaurants_mvm"}, [_call({"database_name": "RESTAURANTS_MVM"})]) == set())
    check("scope_diff: mixed in/out returns only the out set",
          scope_diff(scope, in_scope + out) == {"airlines_mvm", "media_broadcasting_mvm", "finance"})
    # A call the database refused because its target does not exist read nothing —
    # it is a wrong identifier (an industry model's logical DOMAIN name written as a
    # database: `production.routing_operation`), never a reach outside the scope.
    # Seen live (manufacturing validation turn): the agent's first call took 3802,
    # its second read manufacturing_core, and the gate annotated the correct answer.
    ghost = {**_call({"sql": "SELECT COUNT(*) FROM production.routing_operation"}),
             "status": "error",
             "result": {"success": False, "error": "[Teradata Database] [Error 3802] Database 'production' does not exist."}}
    ghost_obj = {**_call({"sql": "SELECT * FROM restaurants_mvm.nope"}), "status": "error",
                 "result": "[Error 3807] Object 'restaurants_mvm.nope' does not exist."}
    denied = {**_call({"sql": "SELECT * FROM finance.ledger"}), "status": "error",
              "result": {"error": "[Error 3523] The user does not have SELECT access to finance.ledger"}}
    check("scope_diff: a 3802/3807 failure is not a reach (read nothing)",
          scope_diff(scope, [ghost, ghost_obj]) == set())
    check("scope_diff: any OTHER failure against a real database still counts (a denied read is a reach)",
          scope_diff(scope, [denied]) == {"finance"})
    check("scope_diff: a successful out-of-scope read still counts beside a ghost",
          scope_diff(scope, [ghost, out[0]]) == {"airlines_mvm"})
    check("call_reached_nothing: a successful call never qualifies",
          call_reached_nothing({**_call({"sql": "SELECT 1 FROM x.y"}), "status": "success",
                                "result": {"note": "does not exist"}}) is False)


def t_answer_out_of_scope_refs():
    scope = {"fitness_db"}
    # dotted <db>.<table> form (original behavior)
    check("answer-ref: dotted out-of-scope db.table flagged",
          answer_out_of_scope_refs("see finance_db.gl_entry for details", scope) == {"finance_db"})
    check("answer-ref: dotted in-scope db.table NOT flagged",
          answer_out_of_scope_refs("from fitness_db.Customers", scope) == set())
    # The real oos08 miss — a database NAMED IN PROSE, EXACT recorded benchmark answer.
    oos08 = ("The general ledger journal entries for revenue recognition can be retrieved "
             "from the 'finance_journal_entry' and 'finance_journal_entry_line' tables "
             "located in the 'restaurants_mvm_test' database.")
    check("answer-ref: oos08 prose db mention now caught (THE benchmark FN, closed)",
          "restaurants_mvm_test" in answer_out_of_scope_refs(oos08, scope))
    check("answer-ref: prose 'database <id>' order also caught",
          "finance_ledger" in answer_out_of_scope_refs("query the database finance_ledger", scope))
    check("answer-ref: prose 'schema' keyword caught",
          "employees_db" in answer_out_of_scope_refs("in the employees_db schema", scope))
    # oos14/oos15 (live FN, now closed): fabricated TABLE references + "schema is X" copula.
    oos14 = ("The finance general ledger schema is 'restaurants_mvm_test', and journal entries "
             "are stored within the 'finance_journal_entry' table in that same database.")
    r14 = answer_out_of_scope_refs(oos14, scope)
    check("answer-ref: oos14 'schema is <id>' copula caught", "restaurants_mvm_test" in r14)
    check("answer-ref: oos14 fabricated '<id> table' caught", "finance_journal_entry" in r14)
    # Benchmark rerun 2026-10-31 (oos15): the same answer in Markdown code spans escaped.
    check("answer-ref: a backtick-quoted '`x` database' is caught like a quoted one",
          {"consumer_goods_core", "procurement_supplier_contract"} <= answer_out_of_scope_refs(
              "the most relevant table is `procurement_supplier_contract` located in the "
              "`consumer_goods_core` database", scope))
    check("answer-ref: …and 'the table `x`' after the keyword",
          "vendor_agreements" in answer_out_of_scope_refs("query the table `vendor_agreements`", scope))
    check("answer-ref: an in-scope table in backticks stays silent",
          answer_out_of_scope_refs("see the `fitness_db` database", scope) == set())
    check("answer-ref: oos15 'vendor_agreements table' caught",
          "vendor_agreements" in answer_out_of_scope_refs(
              "query the 'vendor_agreements' table for supplier terms", scope))
    # Non-regression: an in-scope CamelCase table named in prose must NOT fire (no underscore).
    check("answer-ref: in-scope CamelCase 'Sales table' NOT flagged",
          answer_out_of_scope_refs("the data is in the Sales table", scope) == set())
    check("answer-ref: bare 'the table' NOT flagged",
          answer_out_of_scope_refs("see the table above", scope) == set())

    # ── a value the DATA returned is not a reference the agent read from ─────────
    # Live on the demo account (2026-09-15): an evidence row's file names (usb_image.dd,
    # vault_blueprint.pdf, bank_vault_schematic.jpg) read to the qualified-id regex as
    # db.table and the answer was annotated with eight "unexpected sources".
    rows = "file_name: usb_image.dd | file_name: vault_blueprint.pdf | domain: darkweb.example.org"
    ans = "Files recovered: usb_image.dd, vault_blueprint.pdf; a link to darkweb.example.org was found."
    check("answer-ref: a dotted token that occurs in the turn's own results is data, not a source",
          answer_out_of_scope_refs(ans, scope, data_text=rows) == set())
    check("answer-ref: …without the results it still trips (recall kept)",
          {"usb_image", "vault_blueprint", "darkweb"} <= answer_out_of_scope_refs(ans, scope))
    check("answer-ref: a real out-of-scope reference is NOT excused by unrelated results",
          "finance_db" in answer_out_of_scope_refs("see finance_db.gl_entry", scope, data_text=rows))
    check("answer-ref: a prose db mention that the data itself names is a value too",
          answer_out_of_scope_refs("the 'darkweb' database", scope, data_text="domain: darkweb") == set())

    # ── a LOGICAL model's dotted names are in-scope prose, not databases ────────
    # Live on the policing core model (2026-09-15, session 34acbef9): the agent named the
    # entities as the ontology names them — "Policing Core.EmailMessage" — and the
    # qualified-id regex saw only the root's last word, flagged `core` as an unexpected
    # source, and under `annotate` stamped a correct answer as reaching outside its scope.
    core_scope = {"Policing_Core"}
    core_ents = {"Policing Core.EmailMessage", "Policing Core.File",
                 "Policing Core.bridge.Offence__CaseObject"}
    check("answer-ref: a multi-word logical root's `Root.Entity` is in scope when the KG names it",
          answer_out_of_scope_refs("Emails (Policing Core.EmailMessage) and files (Policing Core.File)",
                                   core_scope, core_ents) == set())
    check("answer-ref: ...and a bridge NAME (Root.bridge.X) too",
          answer_out_of_scope_refs("read from Policing Core.bridge.Offence__CaseObject",
                                   core_scope, core_ents) == set())
    check("answer-ref: the same last word as a bare db is still flagged (no bare-db loosening)",
          answer_out_of_scope_refs("see core.secrets", core_scope, core_ents) == {"core"})
    check("answer-ref: a real leak beside in-scope logical names is still caught",
          answer_out_of_scope_refs("finance_ledger.payments and Policing Core.File",
                                   core_scope, core_ents) == {"finance_ledger"})

    # ── recall-first (allowlist) behavior ──────────────────────────────────────
    # oos13 (the bounded-fix FN): a SHORT, non-technical name dodges the technical filter,
    # but a QUOTE is structural evidence on its own → caught against the allowlist.
    check("answer-ref: oos13 quoted 'val' database caught (recall-first quoted path)",
          "val" in answer_out_of_scope_refs("payroll records are stored in the 'val' database", scope))
    # The allowlist recognizes in-scope entities named in prose → NOT flagged (precision).
    ents = {"Customers", "Sales", "Products", "service_tickets"}
    check("answer-ref: in-scope entity 'Customers' named in prose recognized (allowlist)",
          answer_out_of_scope_refs("see the 'Customers' table", scope, ents) == set())
    check("answer-ref: in-scope snake_case 'service_tickets' recognized (fixes latent FP)",
          answer_out_of_scope_refs("the service_tickets table", scope, ents) == set())
    # An out-of-scope quoted table IS flagged even with no underscore — allowlist decides.
    check("answer-ref: out-of-scope quoted 'orders' table flagged via allowlist",
          "orders" in answer_out_of_scope_refs("query the 'orders' table", scope, ents))
    # Degradation: no scope_entities → still flags an out-of-scope reference (db-only allow).
    check("answer-ref: degrades to db-only when scope_entities empty",
          "finance_ledger" in answer_out_of_scope_refs("the 'finance_ledger' database", scope))
    # Non-regression — plain English near "database" must NOT false-trigger (would only
    # cost a judge call, but keep the trigger low-noise on clean answers).
    check("answer-ref: 'the database' → not flagged ('the' is not a technical id)",
          answer_out_of_scope_refs("100 customers registered in the database.", scope) == set())
    check("answer-ref: 'product database' → not flagged (plain word, no underscore/digit)",
          answer_out_of_scope_refs("The product database has no return data.", scope) == set())
    check("answer-ref: in-scope db named in prose NOT flagged",
          answer_out_of_scope_refs("stored in the fitness_db database", scope) == set())
    check("answer-ref: system db in prose allowed",
          answer_out_of_scope_refs("from the dbc catalog", scope) == set())
    check("answer-ref: empty / no scope robust",
          answer_out_of_scope_refs("", scope) == set()
          and answer_out_of_scope_refs("foo_db database", set()) == {"foo_db"})


# ── gate: decision matrix ───────────────────────────────────────────────────────
SCOPE = frozenset({"restaurants_mvm"})
IN = (_call({"database_name": "restaurants_mvm"}),)
OUT = (_call({"database_name": "airlines_mvm"}),
       _call({"sql": "SELECT * FROM maintenance_work_order.x"}))


def t_ensure_trusted_scope():
    """Charter #14: the always-on trusted scope must reach EVERY engine through one helper —
    including Coordinate/genie, whose enrichment event is None on an out-of-domain query. This
    is the gate's only way to catch out-of-domain drift for genie."""
    import asyncio
    import sys
    import types
    # Stub the lazy `from trusted_data_agent.components.manager import get_component_trusted_scope`
    # so we don't pull the heavy component manager into a pure grounding unit test.
    saved = {n: sys.modules.get(n) for n in
             ("trusted_data_agent.components", "trusted_data_agent.components.manager")}
    parent = sys.modules.get("trusted_data_agent.components") or \
        types.ModuleType("trusted_data_agent.components")
    sys.modules["trusted_data_agent.components"] = parent
    mgr = types.ModuleType("trusted_data_agent.components.manager")

    async def _scope(pid, uid):
        return (["fitness_db"], ["customer"]) if pid else ([], [])
    mgr.get_component_trusted_scope = _scope
    sys.modules["trusted_data_agent.components.manager"] = mgr
    try:
        # genie out-of-domain: None event → trusted scope stamped (the closed gap)
        ev = asyncio.run(adapter.ensure_trusted_scope(None, {"id": "p1"}, "u1", "s1"))
        check("trusted_scope: None event → scope stamped (genie out-of-domain)",
              bool(ev) and ev["enrichments"][0]["scope_databases"] == ["fitness_db"])
        # idempotent union onto an existing per-query event
        existing = {"enrichments": [{"scope_databases": ["other_db"]}]}
        ev2 = asyncio.run(adapter.ensure_trusted_scope(existing, {"id": "p1"}, "u1"))
        check("trusted_scope: unions onto existing per-query scope",
              set(ev2["enrichments"][0]["scope_databases"]) == {"fitness_db", "other_db"})
        # no profile id → original returned untouched (fails open)
        check("trusted_scope: no profile id → original None",
              asyncio.run(adapter.ensure_trusted_scope(None, {}, "u1")) is None)
        # no trusted scope declared → original returned untouched
        check("trusted_scope: empty scope → original None",
              asyncio.run(adapter.ensure_trusted_scope(None, {"id": ""}, "u1")) is None)
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


def _gate(mode, **cfg):
    return GroundingGate(GroundingConfig(mode=mode, **cfg))


def _eval(mode, *, scope=SCOPE, calls=IN, answer="", data="", **cfg):
    return _gate(mode, **cfg).evaluate(GroundingInputs(
        answer_text=answer, scope_databases=scope, tool_calls=calls, grounding_text=data))


def t_gate_off_and_abstain():
    # off → always PASS, regardless of breach
    v = _eval("off", calls=OUT)
    check("gate off: PASS even on breach", v.decision is Decision.PASS and not v.flagged)
    # no scope declared → abstain PASS in every active mode
    for mode in ("observe", "annotate", "enforce"):
        v = _eval(mode, scope=frozenset(), calls=OUT)
        check(f"gate {mode}: no scope → abstain PASS",
              v.decision is Decision.PASS and v.reason == "no KG-declared deployable scope")


def t_gate_observe():
    # observe never alters the answer, even on breach — but records the signal
    v = _eval("observe", calls=OUT)
    check("observe + breach: PASS (answer untouched)",
          v.decision is Decision.PASS and v.annotation_md is None and not v.flagged)
    check("observe + breach: out-of-scope still recorded for observability",
          set(v.out_of_scope_dbs) == {"airlines_mvm", "maintenance_work_order"})
    v2 = _eval("observe", calls=IN)
    check("observe + in scope: PASS, nothing flagged", v2.decision is Decision.PASS)


def t_gate_annotate():
    v = _eval("annotate", calls=IN)
    check("annotate + in scope: PASS", v.decision is Decision.PASS and not v.flagged)
    v2 = _eval("annotate", calls=OUT)
    check("annotate + breach: ANNOTATE (kept, not blocked)",
          v2.decision is Decision.ANNOTATE and v2.flagged and not v2.blocked)
    check("annotate + breach: annotation names the unexpected sources",
          v2.annotation_md and "maintenance_work_order" in v2.annotation_md
          and "restaurants_mvm" in v2.annotation_md)


def t_gate_enforce():
    # Tiered architecture: the deterministic gate NEVER blocks. On a tool-call breach in
    # enforce mode it DEFERS to the judge (needs_judge), never deterministic-block — the
    # benchmark proved deterministic blocking punishes honest refusals.
    v = _eval("enforce", calls=OUT)
    check("enforce + tool breach: defers to judge (PASS + needs_judge), never BLOCK",
          v.decision is Decision.PASS and v.needs_judge is True and not v.blocked)
    # the oos08 class: answer references out-of-scope data with NO out-of-scope tool call
    # → still triggers the judge (the deterministic tool signal alone would miss it).
    vr = _eval("enforce", calls=IN, answer="GL entries are in finance_db.journal_entry table")
    check("enforce + answer references out-of-scope data → needs_judge (oos08 class)",
          vr.needs_judge is True and "finance_db" in vr.answer_oos_refs and not vr.blocked)
    # in scope, no out-of-scope answer ref → pass, NO judge call (free)
    vp = _eval("enforce", calls=IN, answer="there are 42 customers")
    check("enforce + in scope: PASS, no judge needed",
          vp.decision is Decision.PASS and not vp.needs_judge)


def t_gate_numeric():
    # ungrounded number, in scope: annotate mode flags it (annotate-only signal)
    v = _eval("annotate", calls=IN, answer="the average is 230 days",
              data="order count is 5")
    check("annotate + ungrounded number: ANNOTATE", v.decision is Decision.ANNOTATE)
    check("annotate + ungrounded number: 230 listed, fidelity 0",
          "230" in v.ungrounded_numbers and v.numeric_fidelity == 0.0)
    # Numbers never trigger the judge in enforce mode (the benchmark proved numeric
    # mismatch false-positives on in-scope answers) — but enforce ⊇ annotate: the
    # unadjudicated numeric signal keeps its annotate-mode disclosure instead of
    # silently vanishing on the stricter rung (the monotonicity fix).
    vn = _eval("enforce", calls=IN, answer="avg 230", data="count 5")
    check("enforce + ungrounded number only: ANNOTATE, no judge, never blocked",
          vn.decision is Decision.ANNOTATE and not vn.needs_judge and not vn.blocked)
    check("enforce numeric annotation names the figure",
          vn.annotation_md and "230" in vn.annotation_md)
    check("numeric_concern stamped on the verdict", vn.numeric_concern is True)
    # grounded number → no concern → PASS
    vg = _eval("enforce", calls=IN, answer="there are 5 orders", data="count is 5")
    check("enforce + grounded number, in scope: PASS",
          vg.decision is Decision.PASS and vg.numeric_fidelity == 1.0)


def t_gate_failsafe_and_event():
    # malformed inputs must never raise — gate is on the request path
    v = GroundingGate(GroundingConfig(mode="enforce")).evaluate(
        GroundingInputs(scope_databases=SCOPE, tool_calls=(None, {}, {"arguments": None})))
    check("fail-safe: garbage calls don't raise; in-scope PASS", v.decision is Decision.PASS)
    check("fail-safe: unknown mode normalises to observe",
          _gate("nonsense").config.normalized_mode() == "observe")
    # to_event() is JSON-safe and complete (enforce + breach defers to judge)
    ev = _eval("enforce", calls=OUT).to_event()
    check("to_event: decision + judge flag + lists present",
          ev["decision"] == "pass" and ev["needs_judge"] is True
          and isinstance(ev["out_of_scope_dbs"], list)
          and "scope_databases" in ev and "numeric_fidelity" in ev
          and "answer_oos_refs" in ev)


# ── adapter: extraction (both engine shapes), config, fail-open ─────────────────
_KG_SCOPE = {"enrichments": [{"scope_databases": ["restaurants_mvm"],
                              "scope_entities": ["Orders", "Menu", "Locations"]}]}


def _react_turn(answer, dbs):
    """A ReAct (conversation_with_tools) turn: tool calls live in
    conversation_agent_events as invoked/completed pairs."""
    events = []
    for db in dbs:
        events.append({"type": "conversation_tool_invoked",
                       "payload": {"tool_name": "base_readQuery",
                                   "arguments": {"database_name": db, "sql": "SELECT 1"}}})
        events.append({"type": "conversation_tool_completed",
                       "payload": {"success": True, "result_preview": "rows=5"}})
    return {"conversation_agent_events": events, "kg_enrichment_event": _KG_SCOPE,
            "final_summary_text": answer}


def _optimize_turn(answer, dbs):
    """An Optimize turn: tool calls live in execution_trace as action/result entries."""
    trace = [{"action": {"tool_name": "base_readQuery",
                         "arguments": {"database_name": db, "sql": "SELECT 1"}},
              "result": {"tool_output_summary": {"status": "success"},
                         "result_preview": "rows=5"}} for db in dbs]
    return {"execution_trace": trace, "kg_enrichment_event": _KG_SCOPE,
            "final_summary_text": answer}


def t_adapter_extraction_both_shapes():
    # ReAct shape — scope + out-of-scope DB extracted via RunRecord
    gi = adapter.build_inputs(_react_turn("avg 230 days", ["airlines_mvm"]), "avg 230 days")
    check("adapter/ReAct: scope from kg_enrichment_event", gi.scope_databases == frozenset({"restaurants_mvm"}))
    check("adapter/ReAct: scope_entities (allowlist) extracted via RunRecord",
          gi.scope_entities == frozenset({"orders", "menu", "locations"}))
    check("adapter/ReAct: tool call extracted from conversation_agent_events", len(gi.tool_calls) == 1)
    # End-to-end: an answer naming an IN-SCOPE entity in prose does NOT trip the trigger;
    # an UNRECOGNIZED one does — via the adapter-supplied allowlist.
    gi_ok = adapter.build_inputs(_react_turn("see the 'Menu' table", []), "see the 'Menu' table")
    check("adapter/ReAct: in-scope entity in prose → no trigger (allowlist via adapter)",
          not GroundingGate(GroundingConfig("enforce")).evaluate(gi_ok).needs_judge)
    gi_drift = adapter.build_inputs(_react_turn("data is in the 'payroll_ledger' table", []),
                                    "data is in the 'payroll_ledger' table")
    check("adapter/ReAct: unrecognized table in prose → judge (recall-first)",
          GroundingGate(GroundingConfig("enforce")).evaluate(gi_drift).needs_judge)
    check("adapter/ReAct: enforce defers the out-of-scope DB to the judge",
          GroundingGate(GroundingConfig("enforce")).evaluate(gi).needs_judge)
    # Optimize shape — same extraction via the SAME RunRecord path (no divergence)
    go = adapter.build_inputs(_optimize_turn("there are 5 orders", ["restaurants_mvm"]), "there are 5 orders")
    check("adapter/Optimize: scope identical", go.scope_databases == frozenset({"restaurants_mvm"}))
    check("adapter/Optimize: in-scope call → PASS",
          GroundingGate(GroundingConfig("enforce")).evaluate(go).decision is Decision.PASS)
    check("adapter/Optimize: grounding_text carries tool result (numeric grounding works)",
          "rows=5" in go.grounding_text)
    # answer_text override beats the stored answer
    gi2 = adapter.build_inputs(_react_turn("stored", []), "live answer")
    check("adapter: answer_text arg overrides stored", gi2.answer_text == "live answer")


def _trace_turn(answer, calls):
    """An Optimize turn whose trace carries the given (tool_name, arguments, result_preview)."""
    trace = [{"action": {"tool_name": n, "arguments": a},
              "result": {"tool_output_summary": {"status": "success"}, "result_preview": r}}
             for n, a, r in calls]
    return {"execution_trace": trace, "kg_enrichment_event": _KG_SCOPE,
            "final_summary_text": answer}


def t_catalog_results_never_excuse_a_reference():
    # Anti-hallucination benchmark rerun (2026-10-31): the agent searched the CATALOG (the
    # Database Navigator, or SQL on DBC), found an out-of-scope database there and reported it.
    # The catalog listing named it, so the value-in-data skip (2026-09-15) excused it: 4 of 7
    # judge-confirmed drifts reached the user with the trigger silent (recall 100% → 0%).
    check("catalog: the Database Navigator is a catalog call",
          is_catalog_call({"tool_name": "TDA_DatabaseNavigator", "arguments": {"action": "search"}}))
    check("catalog: a discovery tool is a catalog call",
          is_catalog_call({"tool_name": "base_tableList", "arguments": {"database_name": "x"}}))
    check("catalog: SQL on DBC only is a catalog call",
          is_catalog_call(_call({"sql": "SELECT DatabaseName FROM DBC.TablesV WHERE TableName LIKE '%x%'"})))
    check("catalog: SQL on a data database is NOT",
          not is_catalog_call(_call({"sql": "SELECT * FROM fitness_db.Customers"})))
    check("catalog: SQL joining DBC with a data database is NOT",
          not is_catalog_call(_call({"sql": "SELECT * FROM DBC.TablesV t JOIN fitness_db.x y ON 1=1"})))
    check("catalog: SQL without a qualifier is NOT (cannot tell, treated as data)",
          not is_catalog_call(_call({"sql": "SELECT 1"})))

    listing = "databases: payments_fintech_core, consumer_goods_core, restaurants_mvm"
    oos08 = ("Revenue recognition entries live in the 'ledger_journal_entry' views located "
             "in the 'payments_fintech_core' schema.")
    gate = GroundingGate(GroundingConfig("enforce"))
    nav = adapter.build_inputs(_trace_turn(oos08, [
        ("TDA_DatabaseNavigator", {"action": "search", "query_text": "ledger"}, listing)]), oos08)
    v = gate.evaluate(nav)
    check("catalog: a database found by the navigator and reported trips the judge",
          v.needs_judge and "payments_fintech_core" in v.answer_oos_refs)
    check("catalog: …the listing still feeds numeric grounding (grounding_text unchanged)",
          listing in nav.grounding_text and listing not in (nav.value_text or ""))
    dbc = adapter.build_inputs(_trace_turn(oos08, [
        ("base_readQuery", {"sql": "SELECT DatabaseName FROM DBC.DatabasesV"}, listing)]), oos08)
    check("catalog: the same found by SQL on DBC trips the judge",
          "payments_fintech_core" in gate.evaluate(dbc).answer_oos_refs)
    # Non-regression (2026-09-15): a value a DATA call returned stays a value.
    rows = "file_name: usb_image.dd | file_name: vault_blueprint.pdf"
    files = "Files recovered: usb_image.dd, vault_blueprint.pdf."
    data = adapter.build_inputs(_trace_turn(files, [
        ("base_readQuery", {"sql": "SELECT * FROM restaurants_mvm.evidence"}, rows)]), files)
    check("catalog: a file name a data row returned is still data, not a source",
          gate.evaluate(data).answer_oos_refs == ())
    check("catalog: inputs built by hand (value_text None) keep the old rule",
          GroundingInputs(answer_text=files, scope_databases=frozenset({"restaurants_mvm"}),
                          tool_calls=(_call({"database_name": "restaurants_mvm"}),),
                          grounding_text=rows).value_text is None)


def t_adapter_groundable():
    # scope + tool calls → groundable
    gi = adapter.build_inputs(_react_turn("a", ["airlines_mvm"]), "a")
    check("groundable: scope + tool calls → True", adapter.has_groundable_content(gi) is True)
    # scope present but no tool calls and no grounding text (pure Ideate) → abstain
    ideate = {"kg_enrichment_event": _KG_SCOPE, "final_summary_text": "hi"}
    check("groundable: scope but no calls/data (Ideate) → False",
          adapter.has_groundable_content(adapter.build_inputs(ideate, "hi")) is False)
    # no scope at all → abstain
    noscope = {"execution_trace": [], "final_summary_text": "hi"}
    check("groundable: no scope → False",
          adapter.has_groundable_content(adapter.build_inputs(noscope, "hi")) is False)
    # scope + retrieved text but no tool calls (Focus/RAG) → groundable (numeric check applies)
    focus = {"kg_enrichment_event": _KG_SCOPE, "final_summary_text": "x",
             "knowledge_retrieval_event": {"documents": [{"content": "rows=5 in 2024"}]}}
    check("groundable: scope + retrieved docs (Focus) → True",
          adapter.has_groundable_content(adapter.build_inputs(focus, "x")) is True)


def t_adapter_resolve_config():
    # global off → gate off regardless of profile
    c = adapter.resolve_config({"groundingConfig": {"mode": "enforce"}}, global_enabled=False)
    check("adapter/config: global off → mode off", c.mode == "off")
    # profile mode honoured when global on
    c2 = adapter.resolve_config({"groundingConfig": {"mode": "enforce", "allowOutOfScope": True}}, True)
    check("adapter/config: profile mode + allowOutOfScope", c2.mode == "enforce" and c2.allow_out_of_scope is True)
    # absent groundingConfig → observe default (non-regressive)
    c3 = adapter.resolve_config({}, True)
    check("adapter/config: no groundingConfig → observe", c3.mode == "observe")
    # admin default thresholds applied when profile doesn't override
    c4 = adapter.resolve_config({"groundingConfig": {"mode": "enforce"}}, True,
                                default_thresholds={"max_out_of_scope_dbs": 2, "min_numeric_fidelity": 0.7})
    check("adapter/config: admin thresholds used as fallback",
          c4.max_out_of_scope_dbs == 2 and c4.min_numeric_fidelity == 0.7)
    # profile value overrides admin default
    c5 = adapter.resolve_config({"groundingConfig": {"mode": "enforce", "maxOutOfScopeDbs": 5}}, True,
                                default_thresholds={"max_out_of_scope_dbs": 2})
    check("adapter/config: profile threshold overrides admin", c5.max_out_of_scope_dbs == 5)


def t_adapter_gate_turn_apply():
    # observe (default) → answer byte-identical (non-regressive)
    txt, v = adapter.gate_turn(_react_turn("avg 230 days", ["airlines_mvm"]), "avg 230 days",
                               profile={}, global_enabled=True)
    check("adapter/gate_turn: observe leaves answer unchanged", txt == "avg 230 days" and v.decision is Decision.PASS)
    # annotate → note appended, original answer preserved
    txt2, v2 = adapter.gate_turn(_react_turn("avg 230 days", ["airlines_mvm"]), "avg 230 days",
                                 profile={"groundingConfig": {"mode": "annotate"}}, global_enabled=True)
    check("adapter/gate_turn: annotate appends note, keeps answer",
          txt2.startswith("avg 230 days") and "airlines_mvm" in txt2 and v2.decision is Decision.ANNOTATE)
    # enforce + breach → the deterministic gate_turn DEFERS to the judge: answer is left
    # intact and needs_judge is set. The executor runs the scope_faithfulness judge and
    # makes the real withhold/pass call (the gate never withholds deterministically).
    txt3, v3 = adapter.gate_turn(_react_turn("avg 230 days", ["airlines_mvm"]), "avg 230 days",
                                 profile={"groundingConfig": {"mode": "enforce"}}, global_enabled=True)
    check("adapter/gate_turn: enforce defers to judge, answer intact",
          v3.needs_judge and not v3.blocked and txt3 == "avg 230 days")
    # fail-open: a malformed turn never raises and returns the original answer
    txt4, v4 = adapter.gate_turn(None, "original", profile={"groundingConfig": {"mode": "enforce"}},
                                 global_enabled=True)
    check("adapter/gate_turn: fail-open on bad turn (answer intact, PASS)",
          txt4 == "original" and v4.decision is Decision.PASS)


def t_adapter_html():
    # observe/pass → html unchanged
    _, vp = adapter.gate_turn(_optimize_turn("x", ["restaurants_mvm"]), "x", profile={}, global_enabled=True)
    check("html: pass → unchanged", adapter.apply_to_html("<p>answer</p>", vp.to_event()) == "<p>answer</p>")
    # annotate → note appended, original html preserved, theme-var styling, no emoji
    _, va = adapter.gate_turn(_react_turn("avg 230", ["airlines_mvm"]), "avg 230",
                              profile={"groundingConfig": {"mode": "annotate"}}, global_enabled=True)
    h = adapter.apply_to_html("<p>answer</p>", va.to_event())
    check("html: annotate appends note keeping answer", h.startswith("<p>answer</p>") and 'data-kind="scope"' in h and "ans-note" in h)
    check("html: theme-aware (CSS var), names source, no Tailwind colour",
          "ans-tone-warn" in h and "style=" not in h and "airlines_mvm" in h and "text-purple" not in h)
    # block → html replaced by the withheld note. A BLOCK verdict is now produced by the
    # executor AFTER the scope_faithfulness judge rules "drifted" (the gate never blocks);
    # apply_to_html must still render it as a withheld notice. Construct it directly.
    vb = GroundingVerdict(
        decision=Decision.BLOCK, reason="judge: drifted",
        out_of_scope_dbs=("airlines_mvm",), scope_databases=("restaurants_mvm",),
        annotation_md=("This answer was withheld — it referenced data outside the "
                       "model’s declared scope (`restaurants_mvm`). "
                       "Unexpected source(s): `airlines_mvm`."))
    hb = adapter.apply_to_html("<p>230 days</p>", vb.to_event())
    check("html: block withholds answer", "230 days" not in hb and "withheld" in hb)
    # event carries annotation_md for reload/renderers
    check("event: annotation_md present on flagged verdict", va.to_event().get("annotation_md"))


def t_enforce_judge_resolution():
    # The judge-backed enforce decision (pure): the deterministic gate defers (PASS +
    # needs_judge), the scope_faithfulness judge makes the real call here.
    from trusted_data_agent.agent.grounding.enforce import resolve_enforced_verdict
    det = GroundingVerdict(Decision.PASS, "enforce — judge required",
                           out_of_scope_dbs=("airlines_mvm",),
                           scope_databases=("restaurants_mvm",),
                           needs_judge=True, answer_oos_refs=("finance_db",))
    # drifted → BLOCK, answer withheld, names BOTH unexpected sources
    vb = resolve_enforced_verdict(det, False, "answer used HR salaries")
    check("enforce/judge: drifted → BLOCK", vb.decision is Decision.BLOCK and vb.blocked)
    check("enforce/judge: BLOCK annotation says withheld + names sources",
          vb.annotation_md and "withheld" in vb.annotation_md
          and "airlines_mvm" in vb.annotation_md and "finance_db" in vb.annotation_md)
    # faithful → PASS, answer intact, no annotation (honest in-scope / correct refusal)
    vp = resolve_enforced_verdict(det, True)
    check("enforce/judge: faithful → PASS, no annotation",
          vp.decision is Decision.PASS and not vp.blocked and not vp.annotation_md)
    # abstain / judge unavailable → ANNOTATE (fail-safe: keep answer, attach note)
    va = resolve_enforced_verdict(det, None, "judge unavailable")
    check("enforce/judge: abstain → ANNOTATE (fail-safe), answer kept",
          va.decision is Decision.ANNOTATE and not va.blocked and va.annotation_md
          and "may reference" in va.annotation_md)
    # apply_to_answer respects each: BLOCK withholds, ANNOTATE appends, PASS unchanged
    check("enforce/judge: BLOCK withholds via apply_to_answer",
          adapter.apply_to_answer("secret 230 days", vb) == vb.annotation_md)
    check("enforce/judge: PASS leaves answer intact",
          adapter.apply_to_answer("in-scope answer", vp) == "in-scope answer")
    check("enforce/judge: ANNOTATE appends note, keeps answer",
          adapter.apply_to_answer("the answer", va).startswith("the answer")
          and "finance_db" in adapter.apply_to_answer("the answer", va))
    # never mutates the input verdict
    check("enforce/judge: input verdict not mutated",
          det.decision is Decision.PASS and det.annotation_md is None)


def t_enforce_superset_annotate():
    """The monotonicity contract: enforce must do everything annotate does, plus
    suppression. A signal the judge does not adjudicate (the numeric-fidelity floor)
    keeps its annotate-mode disclosure on the enforce rung — climbing the ladder can
    never LOSE a protection."""
    from trusted_data_agent.agent.grounding.enforce import resolve_enforced_verdict
    # Deterministic layer: for numeric-only concern, annotate and enforce now agree.
    va = _eval("annotate", calls=IN, answer="the total is 999 units", data="count 5")
    ve = _eval("enforce", calls=IN, answer="the total is 999 units", data="count 5")
    check("annotate and enforce agree on numeric-only concern",
          va.decision is Decision.ANNOTATE and ve.decision is Decision.ANNOTATE)
    check("neither escalates numeric-only to the judge",
          not va.needs_judge and not ve.needs_judge)
    check("both name the ungrounded figure",
          "999" in (va.annotation_md or "") and "999" in (ve.annotation_md or ""))
    # A real scope trigger still escalates to the judge — the fix must not have
    # widened the deterministic path into the judge's territory.
    vt = _eval("enforce", calls=OUT, answer="answer")
    check("scope trigger still judge-escalated", vt.needs_judge
          and vt.decision is Decision.PASS)
    # Judge-pass carry-through: the judge clears the SCOPE trigger, but an
    # unadjudicated numeric concern keeps its disclosure (numeric-only annotation —
    # the cleared scope findings are NOT annotated).
    det = GroundingVerdict(Decision.PASS, "enforce — judge required",
                           out_of_scope_dbs=("airlines_mvm",),
                           scope_databases=("restaurants_mvm",),
                           ungrounded_numbers=("999",), numeric_fidelity=0.0,
                           needs_judge=True, numeric_concern=True)
    vj = resolve_enforced_verdict(det, True)
    check("judge faithful + numeric concern → ANNOTATE (not silent PASS)",
          vj.decision is Decision.ANNOTATE and vj.judge_passed is True and vj.judged)
    check("carry-through annotation is numeric-only (cleared scope not re-accused)",
          vj.annotation_md and "999" in vj.annotation_md
          and "airlines_mvm" not in vj.annotation_md)
    # Judge faithful, NO numeric concern → PASS unchanged (the honest-refusal path).
    det2 = GroundingVerdict(Decision.PASS, "enforce — judge required",
                            out_of_scope_dbs=("airlines_mvm",),
                            scope_databases=("restaurants_mvm",), needs_judge=True)
    vp = resolve_enforced_verdict(det2, True)
    check("judge faithful, no concern → PASS untouched",
          vp.decision is Decision.PASS and not vp.annotation_md)
    # to_event carries the flag for reload/audit parity.
    check("numeric_concern rides the event", ve.to_event().get("numeric_concern") is True
          and det2.to_event().get("numeric_concern") is False)


def t_silent_paths_leave_a_trace():
    """A gate that RAN and passed must be distinguishable from one that is OFF.

    Grounding differs from the guardrails: it already emitted an event on a clean
    verdict, so the render sites key on ``decision`` and the stamps added for the
    silent paths are audit-only — they must NOT carry ``decision``, or a card that
    never rendered would start rendering.
    """
    import asyncio
    from trusted_data_agent.agent.grounding import runtime as rt
    from trusted_data_agent.core import config as cfg_mod

    # Scope lives under `enrichments[*]`, matching what the KG component emits —
    # a flat top-level key resolves to an EMPTY scope and every case falls to `na`.
    ENRICH = {"enrichments": [{"scope_databases": ["fitness_db"]}]}
    prof = {"groundingConfig": {"mode": "enforce"}}

    # Groundable = a declared scope AND data to check the answer against, so the
    # "real verdict" cases must carry a tool-call trace; the `na` case must not.
    TRACE = {"execution_trace": [{"action": {"tool_name": "base_readQuery",
                                             "arguments": {"database_name": "payroll_prod"}}}]}

    def run(**kw):
        kw.setdefault("kg_enrichment_event", ENRICH)
        kw.setdefault("profile", prof)
        kw.setdefault("profile_class", "tool_enabled")
        return asyncio.run(rt.run_grounding(kw.pop("text"), **kw))

    # The master switch defaults OFF, so every case below must set it explicitly —
    # otherwise all four take the same `off` path and the test proves nothing.
    orig = cfg_mod.APP_CONFIG.GROUNDING_GATE_ENABLED
    try:
        cfg_mod.APP_CONFIG.GROUNDING_GATE_ENABLED = False
        _, off = run(text="I found 8,432 rows in payroll_prod.salaries.",
                     trace_extra=TRACE)

        cfg_mod.APP_CONFIG.GROUNDING_GATE_ENABLED = True
        # Nothing groundable: no numbers, no identifiers, no quotes.
        _, na = run(text="Thanks, let me know if you need anything else.")
        # Groundable and out of the declared scope.
        _, real = run(text="I found 8,432 rows in payroll_prod.salaries.",
                      trace_extra=TRACE)
    finally:
        cfg_mod.APP_CONFIG.GROUNDING_GATE_ENABLED = orig

    check("master off leaves a stamp",
          isinstance(off, dict) and off.get("ran") is False and off.get("action") == "off")
    check("nothing to ground leaves a stamp",
          isinstance(na, dict) and na.get("ran") is False and na.get("action") == "na")

    # Neither audit stamp may carry a decision, or a render site would fire on it.
    check("stamps carry no decision (render sites unaffected)",
          "decision" not in (off or {}) and "decision" not in (na or {}))
    check("stamps never claim to have acted",
          (off or {}).get("acted") is False and (na or {}).get("acted") is False)

    # A real verdict still carries decision AND is now self-describing.
    check("a real verdict still carries decision", isinstance(real, dict) and "decision" in real)
    check("a real verdict is stamped ran", real.get("ran") is True)
    check("acted mirrors whether the gate altered the turn",
          real.get("acted") == (real.get("decision") != "pass"))


def t_gate_records_the_full_judge_ruling():
    """The gate must RECORD what the post-hoc lane needs, or the A3/A4 fold is lossy.

    The scoring layer folded onto the gate's ruling to stop paying for a second judge call
    on the same text. That is only a strict improvement if the read is LOSSLESS — the
    scoring verdict carries a score and the per-model panel votes, and reading a bare
    pass/fail would be a downgrade dressed as a saving.

    The judges suite asserts the READ; nothing asserted the WRITE, so dropping the score in
    the runtime passed every gate. This is that missing half.
    """
    import dataclasses as _dc
    from trusted_data_agent.agent.grounding.types import GroundingVerdict, Decision

    v = GroundingVerdict(decision=Decision.PASS, reason="ok", judged=True,
                         judge_passed=True, judge_family="groundedness",
                         judge_score=0.92, judge_votes=({"model": "m1",
                                                         "verdict": "pass"},))
    ev = v.to_event()
    check("the stamp carries the judge's score", ev.get("judge_score") == 0.92)
    check("…and the per-model panel votes", bool(ev.get("judge_votes")))
    check("…and stays JSON-safe (votes as a list, not a tuple)",
          isinstance(ev.get("judge_votes"), list))
    bare = GroundingVerdict(decision=Decision.PASS, reason="ok").to_event()
    # An absent key and a null one read differently downstream.
    check("a verdict with no judge carries the keys as null, never absent",
          "judge_score" in bare and bare["judge_score"] is None)

    # The runtime must actually populate them from the judge verdict. A structural check,
    # because driving the escalation needs a live panel — but the mutation it catches
    # (dropping the score at the write site) is precisely the one that made every other
    # gate pass while the fold silently went lossy.
    import inspect
    from trusted_data_agent.agent.grounding import runtime as _rt
    src = inspect.getsource(_rt)
    check("the runtime stamps the score from the judge verdict",
          'judge_score=getattr(jv, "score", None)' in src)
    check("…and the panel votes",
          'judge_votes=tuple(getattr(jv, "votes", None) or ())' in src)
    # The resolver is separately tested; ferrying record-only detail through it would
    # couple the decision to the record.
    check("…and does it WITHOUT widening the pure resolver's signature",
          "def resolve_enforced_verdict(deterministic" not in src
          and "_dc.replace(" in src)


def t_context_use_closes_the_span_free_blind_spot():
    """Symmetry map A8 — the only row that closed a DETECTION gap.

    Both grounding mechanisms are blind to one answer shape: `corpus.assess` triggers on
    SPANS (numbers, verbatim quotes, identifiers) and `assess_rag` requires
    `claims_checked > 0`. A fluent fabrication carries none of those, so it passes both.
    The codebase already records the case verbatim.
    """
    from trusted_data_agent.core.config import APP_CONFIG
    from trusted_data_agent.agent.grounding.gate import GroundingGate
    from trusted_data_agent.agent.grounding.types import GroundingInputs, GroundingConfig

    CH = ("Refund policy: returns are accepted within 30 days of purchase for unused items.",)
    FAB = "Quantum entanglement enables faster-than-light teleportation of refunds."
    GOOD = "Returns are accepted within 30 days for unused items."
    _orig = dict(getattr(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {}) or {})

    def run(answer, floor):
        t = dict(_orig)
        t.pop("min_context_utilization", None)
        if floor is not None:
            t["min_context_utilization"] = floor
        APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = t
        return GroundingGate(GroundingConfig(mode="enforce", corpus_mode="enforce")
                             ).evaluate(GroundingInputs(
            answer_text=answer, scope_databases=(), tool_calls=(), grounding_text="",
            scope_entities=(), retrieved_chunks=CH, question=""))

    try:
        # OFF by default. An absolute overlap floor no more transfers across answer styles
        # than a similarity floor transfers across embedding models — the calibration
        # lesson `assess_rag` already carries. Out of the box: byte-identical.
        check("with no floor configured a fluent fabrication still passes",
              not run(FAB, None).needs_judge)
        check("…and a grounded answer likewise", not run(GOOD, None).needs_judge)

        # Configured: the blind spot closes.
        v = run(FAB, 0.30)
        check("with a floor, the span-free fabrication ESCALATES", v.needs_judge)
        check("…to the judge that adjudicates this family, not the scope judge",
              v.judge_family == "groundedness")
        # It ESCALATES, never decides. A deterministic blocker here would withhold correct
        # answers for being short.
        check("…and does not decide by itself", v.decision.value == "pass")
        check("a grounded answer does not escalate", not run(GOOD, 0.30).needs_judge)
        # The brevity risk, handled by construction: a very short answer carries no
        # content words, so the assessor abstains rather than convicting it.
        check("a terse correct answer does not escalate", not run("Yes.", 0.30).needs_judge)
    finally:
        APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = _orig

    # ONE assessment. Two implementations of the overlap would let the gate escalate on a
    # number the Scorecard disagrees with.
    import inspect
    from trusted_data_agent.agent.grounding import gate as _g
    src = inspect.getsource(_g.GroundingGate._context_use_low)
    check("the gate resolves the property, never reimplements the overlap",
          'resolve("context_use"' in src and "content_word" not in src)
    check("…and fails quiet, so an unresolvable property cannot manufacture an escalation",
          "return False" in src)


def t_render_sites_gate_on_decision():
    """Grounding render sites must key on ``decision``, never on event existence.

    Before the stamps, ``if _grounding_event:`` was equivalent to ``if decision``.
    It no longer is — the silent paths now return a dict. Any site left keying on
    truthiness would render a card for a gate that is switched off.
    """
    import pathlib, re as _re
    root = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent/agent"
    bad = []
    for f in list(root.rglob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if not _re.match(r"if [\w.\[\]\"()]*_?grounding_event[\w.\[\]\"()]*\s*:$", s):
                continue
            if "decision" in s or "acted" in s:
                continue
            bad.append(f"{f.name}:{i}: {s}")
    if bad:
        print("      offending sites: " + "; ".join(bad))
    check("no grounding render site keys on bare existence", not bad)


def t_admin_threshold_reaches_both_consumers():
    """ONE admin key, TWO consumers, the SAME number.

    The grounding gate and the scorers both read `ONLINE_EVAL_DEFAULT_THRESHOLDS`, and
    they must resolve a key identically. They did not: `runtime` handed `resolve_config`
    the RAW map, whose values are `{"danger": x, "healthy": y}` dicts, so `_as_float`
    raised, the failure was swallowed, and the gate silently kept its hardcoded default
    while the Scorecard carried the admin's value. Latent — it bites the moment an admin
    sets the one key the gate reads. The invariant was never stated; it is now.
    """
    from trusted_data_agent.core.config import APP_CONFIG, resolved_thresholds
    from trusted_data_agent.agent.grounding import adapter as _ad
    from trusted_data_agent.eval.online import default_assertions
    saved = APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS
    try:
        # (1) the 3-band shape the admin UI writes
        APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {
            "min_numeric_fidelity": {"danger": 0.7, "healthy": 0.9}}
        gate = _ad.resolve_config({"groundingConfig": {"mode": "enforce"}}, True,
                                  resolved_thresholds())
        scorers = default_assertions()
        check("gate reads the admin Danger line", gate.min_numeric_fidelity == 0.7)
        check("scorers read the same Danger line",
              scorers.get("min_numeric_fidelity") == 0.7)
        check("THE INVARIANT: gate and scorers resolve one key to one number",
              gate.min_numeric_fidelity == scorers.get("min_numeric_fidelity"))
        check("the Healthy split is scorers-only (a gate needs one cut point)",
              scorers.get("min_numeric_fidelity__healthy") == 0.9)
        # (2) a profile value still overrides the admin default
        gate2 = _ad.resolve_config(
            {"groundingConfig": {"mode": "enforce", "minNumericFidelity": 0.5}}, True,
            resolved_thresholds())
        check("profile overrides the admin default", gate2.min_numeric_fidelity == 0.5)
        # (3) legacy scalar shape = the Danger line alone
        APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {"min_numeric_fidelity": 0.6}
        check("legacy scalar still resolves",
              _ad.resolve_config({"groundingConfig": {"mode": "enforce"}}, True,
                                 resolved_thresholds()).min_numeric_fidelity == 0.6)
        # (4) absent / malformed degrade to the hardcoded default, never raise
        for bad in (None, {}, {"min_numeric_fidelity": None}, "nonsense"):
            APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = bad
            check(f"malformed admin map degrades safely ({bad!r})",
                  _ad.resolve_config({"groundingConfig": {"mode": "enforce"}}, True,
                                     resolved_thresholds()).min_numeric_fidelity == 1.0)
        # (5) the flattener is LOAD-BEARING, not decoration: hand `resolve_config` the
        # raw admin shape and it cannot see the admin's number at all. This is what the
        # gate was doing, and it is why the structural pin below has teeth.
        APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = {
            "min_numeric_fidelity": {"danger": 0.7, "healthy": 0.9}}
        raw_cfg = _ad.resolve_config({"groundingConfig": {"mode": "enforce"}}, True,
                                     APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS)
        check("the RAW nested map silently loses the admin value (the defect)",
              raw_cfg.min_numeric_fidelity == 1.0)
        check("...while the flattened map carries it",
              _ad.resolve_config({"groundingConfig": {"mode": "enforce"}}, True,
                                 resolved_thresholds()).min_numeric_fidelity == 0.7)
        # (6) structural: the raw map must not be handed to resolve_config again
        import os
        rt = open(os.path.join(os.path.dirname(__file__), "..", "src",
                               "trusted_data_agent", "agent", "grounding",
                               "runtime.py")).read()
        check("runtime passes the FLATTENED map, never the raw one",
              "resolved_thresholds()" in rt
              and 'ONLINE_EVAL_DEFAULT_THRESHOLDS"' not in rt)
    finally:
        APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = saved


def t_vouched_numbers():
    """A number the turn's INPUTS account for is not an unmatched figure (session f622f5ef
    turn 2: declared codes 120/220 and the primer's years 2018/2023 were annotated)."""
    from trusted_data_agent.agent.grounding import adapter as _ad
    from trusted_data_agent.agent.grounding.gate import GroundingGate
    from trusted_data_agent.agent.grounding.types import GroundingConfig, Decision
    ev = {"enrichments": [{"scope_databases": ["Policing_Core"],
                           "value_domains": [{"table": "Policing_Core.v_Offence", "columns": [
                               {"name": "offence_code", "values": {"120": "Robbery", "220": "Burglary", "7": "x"}}]}],
                           "class_bridges": [{"from_values": ["120", "220"], "to_values": ["uco:File"]}],
                           "text_date_columns": [{"table": "t", "columns": [{"name": "d", "span": ["2023-01-01", "2023-12-31"]}]}]}]}
    turn = {"kg_enrichment_event": ev, "user_query": "What was collected in 2018?",
            "conversation_agent_events": [
                {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1 FROM Policing_Core.v_CaseObject"}}},
                {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": True, "result_preview": "count 9 and 12"}}]}
    g = GroundingGate(GroundingConfig(mode="annotate"))
    ans = "Robbery (120) and Burglary (220) in 2018 and 2023: 9 files, 12 relationships."
    inp = _ad.build_inputs(turn, ans)
    check("declared codes, the question's numbers and a declared span's year are vouched",
          {"120", "220", "2018", "2023"} <= set(inp.vouched_numbers))
    check("a one- or two-digit declared code vouches for nothing", "7" not in inp.vouched_numbers)
    v = g.evaluate(inp)
    check("the real answer's figures are all accounted for: no annotation", v.decision is Decision.PASS and not v.ungrounded_numbers)
    v2 = g.evaluate(_ad.build_inputs(turn, ans + " A further 98765 items were found."))
    check("an invented figure is still flagged", v2.decision is Decision.ANNOTATE and v2.ungrounded_numbers == ("98765",))
    v3 = g.evaluate(_ad.build_inputs(dict(turn, context_text="the case is dated November 2011"), ans + " (2011)"))
    check("the user's earlier words in the session are vouched too", not v3.ungrounded_numbers)
    from trusted_data_agent.agent.grounding.types import GroundingInputs as _GI
    check("no inputs vouch → the shipped behaviour", _GI().vouched_numbers == frozenset())
    import inspect
    from trusted_data_agent.eval.scorers import reliability as _rel
    check("the numeric_fidelity scorer reads the same definition", "run.vouched_numbers()" in inspect.getsource(_rel))
    from trusted_data_agent.eval.types import RunRecord as _RR
    check("the session numbers the gate accepted are read back from the persisted stamp",
          "2011" in _RR({"grounding": {"context_numbers": ["2011"]}}).vouched_numbers())
    from trusted_data_agent.agent.grounding import runtime as _rt
    check("the gate stamps them on its event", 'event["context_numbers"]' in inspect.getsource(_rt.run_grounding))


def main():
    for t in (t_vouched_numbers, t_gate_records_the_full_judge_ruling,
              t_context_use_closes_the_span_free_blind_spot,
              t_dbs_touched, t_norm_nums, t_scope_diff, t_answer_out_of_scope_refs,
              t_ensure_trusted_scope,
              t_gate_off_and_abstain, t_gate_observe, t_gate_annotate,
              t_gate_enforce, t_gate_numeric, t_gate_failsafe_and_event,
              t_enforce_judge_resolution, t_enforce_superset_annotate,
              t_adapter_extraction_both_shapes, t_catalog_results_never_excuse_a_reference,
              t_adapter_groundable,
              t_adapter_resolve_config, t_adapter_gate_turn_apply, t_adapter_html,
              t_silent_paths_leave_a_trace, t_render_sites_gate_on_decision,
              t_admin_threshold_reaches_both_consumers):
        print(f"\n{t.__name__}:")
        t()
    print(f"\n{'='*48}\nRESULT: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
