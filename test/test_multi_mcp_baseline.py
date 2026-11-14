#!/usr/bin/env python3
"""PROP-006 phase 2 — single-server freeze, committed GREEN before any phase-2 code.

The operator's rule: an agent with one MCP server gets no added burden from several-server
support — no added tokens (the catalog the planner and the ReAct agent see), calls (MCP sessions,
list_* calls, LLM classification calls), state, stored snapshot, signature or routing change.

``multi_mcp_baseline_harness.observe()`` drives the real seams; this compares every section
with ``fixtures/multi_mcp_baseline.golden.json``, written from the code before phase 2, and
states the load-bearing properties directly so a regenerated golden cannot hide them.

A deliberate change to single-server behaviour is allowed only as a NAMED exception below,
asserted to change exactly what it names (the ``test_sql_gate_lanes`` precedent). There are none
yet.

Run: python3 test/test_multi_mcp_baseline.py   (no server, no network)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import multi_mcp_baseline_harness as H  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {str(detail)[:400]}" if detail else ""))


def _first_diff(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: type {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: present only in {'now' if k in a else 'golden'}"
            d = _first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


if not os.path.exists(H.GOLDEN):
    print(f"FAIL: the golden file is missing: {H.GOLDEN}")
    sys.exit(1)

golden = json.load(open(H.GOLDEN))
now = H.observe()

# NAMED EXCEPTIONS — (section, part) pairs a phase-2 increment changed on purpose, each asserted
# separately below with its new value. Neither changes what a single-server agent sees or pays:
#   M10  reclassification: a profile GAINING an additional server now flags reclassification
#        (the reachable catalog changed); nothing single-server writes that key.
#   M9   the delete guard refuses deleting a server an Ideate+tools agent uses — a pre-existing
#        gap (the guard saw Optimize agents only), fixed for single- and multi-server alike.
#   S6b  (Oct 2026) the Tool Finder is the governed surface's own search: a governed agent on
#        which `TDA_FindTools` is not offered HOLDS NOTHING. The harness's fixture catalogs carry
#        no finder, so their frozen offered/held/reasons split moved — every tool offered, the
#        reason on each chip. The catalog itself (names, order, categories) is unchanged and is
#        still asserted in [3]; the new split is asserted in [8].
EXCEPTIONS: list = [("reclass", "an unknown key"), ("delete_guard", "ideate+tools bound")]
_SURFACE_KEYS = ("optimize_light", "optimize_full", "optimize_light_platform_tool")
EXCEPTIONS += [(k, part) for k in _SURFACE_KEYS for part in ("surface_offered", "surface_held", "surface_reasons")]

print("[1] every frozen section is byte-identical to the golden")
check("the harness covers every frozen section", set(now) == set(golden), sorted(set(now) ^ set(golden)))
for section in sorted(golden):
    for part in sorted(golden[section]) if isinstance(golden[section], dict) else [None]:
        g = golden[section] if part is None else golden[section].get(part)
        n = now.get(section) if part is None else (now.get(section) or {}).get(part)
        if (section, part) in EXCEPTIONS:
            continue
        check(f"{section}{'.' + part if part else ''} unchanged", n == g, _first_diff(n, g))

print("[2] calls — one server, one session, one listing each, and today's LLM classification count")
for key in H.PROFILES:
    c = now[key]["classify"]
    check(f"{key}: exactly one MCP session per activation", c["call_counts"].get("session") == 1, c["call_counts"])
    check(f"{key}: the session is the agent's one server",
          all(e[1] == H.SERVER for e in c["wire"]), c["wire"])
    check(f"{key}: one list_tools, one list_prompts, one list_resources",
          [c["call_counts"].get(k) for k in ("list_tools", "list_prompts", "list_resources")] == [1, 1, 1],
          c["call_counts"])
expected_llm = {"optimize_light": 0, "optimize_full": 1, "ideate_tools": 0, "focus": 0}
for key, n in expected_llm.items():
    check(f"{key}: {n} LLM classification call(s)", len(now[key]["classify"]["llm_calls"]) == n,
          now[key]["classify"]["llm_calls"])

print("[3] tokens — every name the model sees is the server's own name, unqualified")
server_names = [t.name for t in H.TOOLS]
for key in ("optimize_light", "optimize_full"):
    tc = now[key]["tools_context"]
    for n in server_names:
        check(f"{key}: `{n}` rendered under its real name", f"`{n}`" in tc)
    check(f"{key}: no qualified name in the planner catalog", "__base_" not in tc and "__qlty_" not in tc)
    check(f"{key}: the S6 catalog lists the real names in server order",
          [t for t, _ in now[key]["surface_catalog"]] == server_names, now[key]["surface_catalog"])
check("ReAct binds the server's tools under their real names, in server order",
      now["react_all"]["bound"] == server_names, now["react_all"]["bound"])
check("ReAct loads ONE connection for one server", len(now["react_all"]["connections"]) == 1)
check("ReAct keeps the profile's selection, nothing added",
      now["react_selected"]["bound"] == ["base_readQuery", "base_tableList"])

print("[4] storage — the snapshot holds one server's catalog, under today's keys")
for key in ("optimize_light", "optimize_full", "ideate_tools", "focus"):
    saved = now[key]["classify"]["saved"]
    check(f"{key}: one snapshot saved", len(saved) == 1)
    check(f"{key}: the snapshot has exactly today's keys",
          sorted(saved[0]["results"]) == ["classified_with_mode", "prompts", "resources", "tool_scopes", "tools"],
          sorted(saved[0]["results"]))
check("an Ideate+tools snapshot carries no prompts (the ReAct lane cannot use them)",
      now["ideate_tools"]["classify"]["saved"][0]["results"]["prompts"] == {})

print("[5] routing — a call reaches the one server under the name the server gave it")
check("no overlay: the per-user server, the original name",
      now["route_plain"]["wire"] == [["session", H.SERVER], ["call_tool", H.SERVER, "base_readQuery", {"sql": "SELECT 1"}]],
      now["route_plain"]["wire"])
check("an @TAG overlay names its own server, the original name",
      now["route_overlay"]["wire"][-1][:3] == ["call_tool", "srv-override", "base_readQuery"],
      now["route_overlay"]["wire"])

print("[6] signature — the deployment half hashes one catalog; a Focus agent none")
check("an Optimize agent's catalog is the server's tools with their argument names",
      now["optimize_light"]["deployment"]["catalog"]["tools"][0] == "base_readQuery(sql)",
      now["optimize_light"]["deployment"]["catalog"])
check("light and full classification hash the same catalog (categories are not signed)",
      now["optimize_light"]["deployment"]["hash"] == now["optimize_full"]["deployment"]["hash"])
check("a Focus agent's MCP binding is not a surface", now["focus"]["deployment"]["catalog"] is None)
check("the signature carries no field phase 2 would add",
      not any(k in now["optimize_light"]["deployment"]["signature"]
              for k in ("mcp_servers", "additional_mcp", "additionalMcpServers")),
      sorted(now["optimize_light"]["deployment"]["signature"]))

print("[7] the rules that read a profile's binding — and the two named exceptions")
check("golden: before phase 2 an unknown key never flagged reclassification",
      golden["reclass"]["an unknown key"] is False)
check("M10: gaining an additional MCP server flags reclassification",
      now["reclass"]["an unknown key"] is True)
check("every other reclassification case is unchanged",
      {k: v for k, v in now["reclass"].items() if k != "an unknown key"}
      == {k: v for k, v in golden["reclass"].items() if k != "an unknown key"})
check("the delete guard refuses for a bound Optimize agent",
      now["delete_guard"]["optimize bound"]["refused"] is True)
check("golden: before phase 2 the guard let an Ideate+tools agent's server be deleted",
      golden["delete_guard"]["ideate+tools bound"]["refused"] is False)
check("M9: the guard now refuses for an Ideate+tools agent, naming it",
      now["delete_guard"]["ideate+tools bound"]["refused"] is True
      and "@I" in (now["delete_guard"]["ideate+tools bound"]["message"] or ""),
      now["delete_guard"]["ideate+tools bound"])
check("a server no agent uses can still be deleted",
      now["delete_guard"]["optimize other server"]["refused"] is False)

print("[8] S6b — a governed surface with no Tool Finder holds nothing (the frozen split moved on purpose)")
for key in _SURFACE_KEYS:
    cat_names = [t for t, _ in now[key]["surface_catalog"]]
    check(f"{key}: golden held a tool behind a finder the catalog never offered",
          golden[key]["surface_held"] and "TDA_FindTools" not in cat_names, golden[key]["surface_held"])
    check(f"{key}: now nothing is held, every non-denied tool offered, in catalog order",
          now[key]["surface_held"] == [] and now[key]["surface_offered"] == [n for n in cat_names if n not in now[key]["surface_denied"]],
          (now[key]["surface_held"], now[key]["surface_offered"]))
    check(f"{key}: every chip says why",
          all(r == "no Tool Finder on this agent — nothing is held" for n, r in now[key]["surface_reasons"].items() if n in now[key]["surface_offered"]),
          now[key]["surface_reasons"])
    check(f"{key}: the denied set is unchanged", now[key]["surface_denied"] == golden[key]["surface_denied"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
