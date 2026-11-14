#!/usr/bin/env python3
"""
Unit tests for Flow Parameters (Flowise-style Flow State).

Covers the engine-side resolution added in flow_executor.py:
- $name reference normalization for Fn expressions / decision conditions
- $name + {name} substitution for LLM prompt templates
- FlowState merge of global params + predecessor outputs into node context
- End-to-end Fn / decision node execution against flow parameters

Usage:
    python test/test_flow_parameters.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import flow_executor as fe  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def test_normalize_param_refs():
    print("\n[normalize $name -> name]")
    check("strips sigil before identifier",
          fe._normalize_param_refs("float(output) * $multiplier") == "float(output) * multiplier")
    check("multiple refs",
          fe._normalize_param_refs("$a + $b_2") == "a + b_2")
    check("no-dollar passthrough",
          fe._normalize_param_refs("output > 5") == "output > 5")
    check("empty passthrough", fe._normalize_param_refs("") == "")
    check("dollar-digit left intact (not an identifier)",
          fe._normalize_param_refs("price == $5") == "price == $5")


def test_substitute_dollar_values():
    print("\n[prompt $name substitution]")
    ctx = {"region": "EMEA", "threshold": "0.8"}
    check("substitutes known $name",
          fe._substitute_dollar_values("for $region", ctx) == "for EMEA")
    check("leaves unknown $name intact",
          fe._substitute_dollar_values("hi $nope", {}) == "hi $nope")
    check("dollar-digit untouched",
          fe._substitute_dollar_values("cost $5 in $region", ctx) == "cost $5 in EMEA")
    # combined with {name} .format afterwards (mirrors executor flow)
    s = fe._substitute_dollar_values("$region t={threshold}", ctx).format(**ctx)
    check("co-exists with {name} format", s == "EMEA t=0.8")


def test_output_alias():
    print("\n[named output (output_as)]")
    # alias publishes the primary output under the chosen name (and keeps "output")
    node = {"id": "n", "data": {"output_as": "customer_count"}}
    aliased = fe._apply_output_alias(node, {"output": 1450, "tokens_input": 5})
    check("alias key added", aliased.get("customer_count") == 1450)
    check("original output preserved", aliased.get("output") == 1450)
    check("other keys preserved", aliased.get("tokens_input") == 5)
    # no output_as -> unchanged
    check("no alias when unset",
          fe._apply_output_alias({"id": "n", "data": {}}, {"output": 1}) == {"output": 1})
    check("no alias when blank",
          fe._apply_output_alias({"id": "n", "data": {"output_as": "  "}}, {"output": 1}) == {"output": 1})
    # fan-in: two predecessors with distinct aliases don't collide
    st = fe.FlowState({})
    st.record_output("a", fe._apply_output_alias({"data": {"output_as": "count"}}, {"output": 10}))
    st.record_output("b", fe._apply_output_alias({"data": {"output_as": "total"}}, {"output": 99}))
    ctx = st.resolve_for_node("merge", ["a", "b"])
    check("fan-in aliases both reachable", ctx.get("count") == 10 and ctx.get("total") == 99)


def test_flowstate_merge():
    print("\n[FlowState context merge]")
    st = fe.FlowState({"multiplier": "3", "region": "EMEA"})
    st.record_output("llm1", {"output": "10"})
    ctx = st.resolve_for_node("fn1", ["llm1"])
    check("global params present", ctx.get("multiplier") == "3" and ctx.get("region") == "EMEA")
    check("predecessor output merged", ctx.get("output") == "10")
    # predecessor output overrides a same-named global (documented last-writer-wins)
    st2 = fe.FlowState({"output": "global"})
    st2.record_output("p", {"output": "pred"})
    check("predecessor wins over global on key clash",
          st2.resolve_for_node("n", ["p"]).get("output") == "pred")


async def _run_async_cases():
    print("\n[Fn / decision node execution with params]")
    st = fe.FlowState({"multiplier": "3"})
    st.record_output("llm1", {"output": "10"})
    ctx = st.resolve_for_node("fn1", ["llm1"])
    fn = {"id": "fn1", "data": {"expression": "float(output) * float($multiplier)"}}
    res = await fe._execute_function_node(fn, ctx, "u", "run1", None)
    check("Fn resolves $param", res == {"output": 30.0})

    fn_bare = {"id": "fn2", "data": {"expression": "int(output) + 5"}}
    res2 = await fe._execute_function_node(fn_bare, ctx, "u", "run1", None)
    check("Fn bare-name still works", res2 == {"output": 15})

    st2 = fe.FlowState({"threshold": "0.8"})
    st2.record_output("a", {"output": "0.9"})
    ctx2 = st2.resolve_for_node("d", ["a"])
    dec = {"id": "d", "data": {"condition": "float(output) > float($threshold)"}}
    res3 = await fe._execute_decision_node(dec, ctx2)
    check("decision resolves $param (True)", res3.get("output") is True)

    dec_false = {"id": "d2", "data": {"condition": "float(output) < float($threshold)"}}
    res4 = await fe._execute_decision_node(dec_false, ctx2)
    check("decision resolves $param (False)", res4.get("output") is False)

    bad = {"id": "d3", "data": {"condition": "this is not python"}}
    res5 = await fe._execute_decision_node(bad, ctx2)
    check("decision fails closed to False on error", res5.get("output") is False)


def test_global_variables():
    print("\n[global variables (cross-flow $vars)]")
    import tempfile
    from pathlib import Path
    from trusted_data_agent.core import flow_db

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_path = flow_db._DB_PATH
    flow_db._DB_PATH = Path(tmp.name)
    flow_db._FLOW_VARS_TABLE_APPLIED = False
    try:
        u = "user-test-1"
        # replace drops blank names, stringifies values
        saved = flow_db.replace_flow_variables(u, {"region": "EMEA", "retries": 3, "  ": "x"})
        check("blank name dropped", all(r["name"].strip() for r in saved) and len(saved) == 2)
        d = flow_db.get_flow_variables_dict(u)
        check("dict has values (stringified)", d.get("region") == "EMEA" and d.get("retries") == "3")
        # isolation per user
        check("other user empty", flow_db.get_flow_variables_dict("user-test-2") == {})
        # replace is a full overwrite
        flow_db.replace_flow_variables(u, {"only": "one"})
        check("replace overwrites set", flow_db.get_flow_variables_dict(u) == {"only": "one"})
        # executor loader + precedence (flow param wins over global var on key clash)
        gv = fe._load_global_variables(u)
        check("loader returns dict", gv == {"only": "one"})
        st = fe.FlowState({**fe._load_global_variables(u), **{"only": "flow_param"}})
        check("flow param overrides global var",
              st.resolve_for_node("n", []).get("only") == "flow_param")
        # loader fails open
        flow_db._DB_PATH = Path("/nonexistent/dir/nope.db")
        flow_db._FLOW_VARS_TABLE_APPLIED = False
        check("loader fails open on error", fe._load_global_variables(u) == {})
    finally:
        flow_db._DB_PATH = orig_path
        flow_db._FLOW_VARS_TABLE_APPLIED = False
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def test_partial_execution():
    print("\n[partial execution (run-from-node / single)]")
    definition = {
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "data": {}},
            {"id": "A", "type": "task_function", "label": "A", "data": {}},
            {"id": "B", "type": "task_function", "label": "B", "data": {}},
            {"id": "C", "type": "task_function", "label": "C", "data": {}},
            {"id": "D", "type": "task_function", "label": "D", "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "A", "type": "normal"},
            {"id": "e2", "source": "A", "target": "B", "type": "normal"},
            {"id": "e3", "source": "B", "target": "C", "type": "normal"},
            {"id": "e4", "source": "B", "target": "D", "type": "normal"},
        ],
        "scopes": [], "global_params": {},
    }
    fd = fe.FlowDefinition(definition)

    check("single = just the node", fe._compute_partial_targets(fd, "B", "single") == {"B"})
    check("downstream = node + descendants", fe._compute_partial_targets(fd, "B", "downstream") == {"B", "C", "D"})
    check("downstream from start = all", fe._compute_partial_targets(fd, "start", "downstream") == {"start", "A", "B", "C", "D"})
    check("leaf node downstream = itself", fe._compute_partial_targets(fd, "C", "downstream") == {"C"})

    order = fe._partial_execution_order(fd, {"B", "C", "D"})
    ids = [nid for (ut, nid) in order]
    check("order restricted to targets", set(ids) == {"B", "C", "D"})
    check("order is topological (B before C,D)", ids.index("B") < ids.index("C") and ids.index("B") < ids.index("D"))
    check("single-target order", [nid for (_, nid) in fe._partial_execution_order(fd, {"B"})] == ["B"])


def main():
    print("=" * 60)
    print("Flow Parameters — unit tests")
    print("=" * 60)
    test_normalize_param_refs()
    test_substitute_dollar_values()
    test_output_alias()
    test_global_variables()
    test_partial_execution()
    test_flowstate_merge()
    asyncio.run(_run_async_cases())
    print("\n" + "=" * 60)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 60)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
