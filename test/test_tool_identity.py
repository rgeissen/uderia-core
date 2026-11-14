#!/usr/bin/env python3
"""PROP-006 decision 2 — one name per tool across an agent's MCP servers.

[1] ``tool_identity``: qualify / parse / base_name, the alias rules, the per-turn aliases.
[2] the build gate: no rule in the source compares an MCP tool name without reading its base
    name through ``base_name``.
[3] the gate's self-test: every bypass shape it exists to catch is caught, and the routed forms
    pass (a gate that cannot fail is worse than none).
[4] the routed rules recognise a qualified tool on an additional server exactly as they recognise
    the primary's — and, with no aliases armed, exactly as before.

Run: python3 test/test_tool_identity.py   (no server)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from trusted_data_agent.core import tool_identity as ti  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {str(detail)[:400]}" if detail else ""))


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


print("[1] qualify / parse / base_name")
check("the primary server keeps the real name", ti.qualify(None, "base_readQuery") == "base_readQuery")
check("an empty alias is the primary server", ti.qualify("", "base_readQuery") == "base_readQuery")
check("an additional server's tool is <alias>__<name>", ti.qualify("crm", "base_readQuery") == "crm__base_readQuery")
for bad in ("CRM", "1crm", "crm_", "c__rm", "crm-x", "a" * 13, "crm x", "_crm"):
    check(f"alias {bad!r} is refused", raises(lambda b=bad: ti.qualify(b, "base_readQuery")))
for good in ("crm", "td2", "sales_eu", "a", "a" * 12):
    check(f"alias {good!r} is accepted", ti.qualify(good, "t") == f"{good}__t")
check("a qualified name longer than 64 is refused, never truncated",
      raises(lambda: ti.qualify("crm", "x" * 60)))
check("a qualified name with a character no provider accepts is refused",
      raises(lambda: ti.qualify("crm", "base.readQuery")))
check("parse with a known alias", ti.parse("crm__base_readQuery", ["crm"]) == ti.ToolId("crm", "base_readQuery"))
check("parse: an unknown prefix is part of the name",
      ti.parse("crm__base_readQuery", ["erp"]) == ti.ToolId(None, "crm__base_readQuery"))
check("parse: a primary tool whose own name contains __ is left whole",
      ti.parse("my__tool", ["crm"]) == ti.ToolId(None, "my__tool"))
check("parse: the longest matching alias wins",
      ti.parse("sales_eu__x", ["sales", "sales_eu"]) == ti.ToolId("sales_eu", "x"))
check("parse: an alias alone is not a tool", ti.parse("crm__", ["crm"]) == ti.ToolId(None, "crm__"))
check("parse: a non-string passes through", ti.parse(None, ["crm"]) == ti.ToolId(None, None))
check("round trip", all(ti.parse(ti.qualify(a, n), [a]) == ti.ToolId(a, n)
                        for a in ("crm", "td2") for n in ("base_readQuery", "qlty_columnSummary", "x-y")))

print("[1b] the per-turn aliases")
check("outside a turn no alias is known", ti.turn_aliases() == ())
check("outside a turn base_name is the identity", ti.base_name("crm__base_readQuery") == "crm__base_readQuery")
tok = ti.set_turn_aliases(["crm", "BAD", "td2"])
check("an armed turn knows its valid aliases only", set(ti.turn_aliases()) == {"crm", "td2"}, ti.turn_aliases())
check("inside the turn base_name strips a known alias", ti.base_name("crm__base_readQuery") == "base_readQuery")
check("inside the turn the primary's names are untouched", ti.base_name("base_readQuery") == "base_readQuery")
ti.reset_turn_aliases(tok)
check("reset restores no aliases", ti.turn_aliases() == ())
ti.reset_turn_aliases(tok)            # a second reset never raises
check("a second reset never raises", True)

print("[2] the build gate — no rule compares a tool name without base_name")
import tool_identity_gate as gate  # noqa: E402
v = gate.violations()
check("no bypassing comparison in src/ or components/", v == [], "\n".join(f"{a}:{b}: {c}" for a, b, c in v[:20]))

print("[3] the gate's self-test — each bypass shape is caught, each routed form passes")
BAD = {
    "eq": "def f(tool_name):\n    return tool_name == 'base_readQuery'\n",
    "ne": "def f(t):\n    if t != \"base_databaseList\":\n        return 1\n",
    "in_module_set": "SQL = {'base_readQuery', 'base_readQueries'}\ndef f(t):\n    return t in SQL\n",
    "in_inline": "def f(t):\n    return t in ('base_tableList', 'base_tableDDL')\n",
    "in_local": "def f(t):\n    s = {'base_readQuery'}\n    return t not in s\n",
    "reverse_in": "def f(tools):\n    return 'base_readQuery' in tools\n",
    "set_and": "D = frozenset({'base_tableList'})\ndef f(tools):\n    return set(tools) & D\n",
    "isdisjoint": "D = frozenset({'base_tableList'})\ndef f(tools):\n    return D.isdisjoint(tools)\n",
    "startswith": "def f(name):\n    return name.startswith('base_')\n",
    "named_string": "class K:\n    TOOL = 'base_tableList'\n    def f(self, t):\n        return t == self.TOOL\n",
    "loop_var": "L = ['base_readQuery']\ndef f(avail):\n    return next((t for t in L if t in avail), None)\n",
    "func_return": "def names():\n    return frozenset({'base_tableList'})\ndef f(t):\n    return t in names()\n",
    "dict_get": "M = {'base_readQuery': 1}\ndef f(t):\n    return M.get(t)\n",
    "import_alias": "from x import D as _D\nD = frozenset({'base_tableList'})\ndef f(ts):\n    return ts & _D\n",
}
GOOD = {
    "eq": "from trusted_data_agent.core.tool_identity import base_name\ndef f(t):\n    return base_name(t) == 'base_readQuery'\n",
    "local_parsed": "def f(t):\n    b = base_name(t)\n    return b in {'base_readQuery'}\n",
    "comp": "D = frozenset({'base_tableList'})\ndef f(tools):\n    return {base_name(t) for t in tools} & D\n",
    "command": "def f():\n    return {'tool_name': 'base_readQuery', 'arguments': {}}\n",
    "unrelated_locals": "def g():\n    out = {'base_readQuery'}\n    return out\ndef f(x, out):\n    return x in out\n",
}
with tempfile.TemporaryDirectory() as d:
    for kind, code in list(BAD.items()) + [("good_" + k, c) for k, c in GOOD.items()]:
        p = os.path.join(d, kind + ".py")
        open(p, "w").write(code)
        found = gate.violations([(kind, p)])
        if kind.startswith("good_"):
            check(f"routed form passes: {kind}", found == [], found)
        else:
            check(f"bypass caught: {kind}", len(found) >= 1, code)

print("[4] the routed rules recognise a qualified tool — and nothing moves with no aliases")
from trusted_data_agent.agent.executor import PlanExecutor  # noqa: E402
from trusted_data_agent.agent import tool_surface  # noqa: E402
from trusted_data_agent.agent.phase_executor import DatabaseNotFoundStrategy  # noqa: E402

plan = [{"phase": 1, "relevant_tools": ["crm__base_tableList"]},
        {"phase": 2, "relevant_tools": ["crm__base_readQuery"], "arguments": {}}]
check("no aliases: a qualified-looking name is not recognised (single-server behaviour unchanged)",
      PlanExecutor._phase_discovery_deps(plan[1], plan) == set())
tok = ti.set_turn_aliases(["crm"])
try:
    check("a data phase on an additional server waits for discovery on it",
          PlanExecutor._phase_discovery_deps(plan[1], plan) == {1})
    check("S6: a discovery tool on an additional server is backbone",
          tool_surface.is_backbone(tool_surface.ToolInfo("crm__base_tableList")))
    check("S6: an unrelated qualified tool is not",
          not tool_surface.is_backbone(tool_surface.ToolInfo("crm__dba_tableSpace")))

    strat = DatabaseNotFoundStrategy.__new__(DatabaseNotFoundStrategy)
    avail = {"base_tableList": 1, "crm__base_tableList": 1, "base_databaseList": 1}
    check("O7: the pivot asks the failing call's own server",
          strat._on_failing_server({"tool_name": "crm__base_readQuery"}, "base_tableList", avail) == "crm__base_tableList")
    check("O7: the primary's failing call pivots on the primary",
          strat._on_failing_server({"tool_name": "base_readQuery"}, "base_tableList", avail) == "base_tableList")
    check("O7: the failing server lacks the tool — no pivot to another server",
          strat._on_failing_server({"tool_name": "crm__base_readQuery"}, "base_databaseList", avail) is None)
finally:
    ti.reset_turn_aliases(tok)
check("S6 backbone for the primary unchanged", tool_surface.is_backbone(tool_surface.ToolInfo("base_tableList")))
check("discovery deps for the primary unchanged",
      PlanExecutor._phase_discovery_deps({"phase": 2, "relevant_tools": ["base_readQuery"]},
                                         [{"phase": 1, "relevant_tools": ["base_tableList"]}]) == {1})

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
