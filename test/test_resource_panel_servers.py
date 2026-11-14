"""PROP-006 phase 2 — the resource panel shows EVERY MCP server of an agent, each marked.

Before this, `/v1/profiles/<id>/resources` read only the first server's classification: an
additional server's tools and prompts — which the agent calls — were absent from the panel.

    [1] resource_panel_servers — None for a single-server agent (the response is unchanged);
        an additional server's categories are the runtime's own (`_merge_server_snapshot`):
        `<alias>: <category>`, names `<alias>__<name>`, each flagged by that server's selection
    [2] every server is listed with its classification state — a server with no snapshot, or
        one taken in another mode, is `not_classified` (with the reason), never omitted
    [3] an Ideate+tools agent gets no prompts (LangChain calls tools only), as for the first server
    [4] the endpoint adds the keys only for a multi-server agent and never loses the first
        server's lists when the additional ones cannot be listed

Run: python test/test_resource_panel_servers.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.mcp_adapter.adapter import resource_panel_servers  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail != "" else ""))


SNAP = {"last_classified": True, "classified_with_mode": "light",
        "tools": {"Base": [{"name": "base_readQuery", "description": "run SQL", "arguments": []},
                           {"name": "base_tableList", "description": "list", "arguments": []}],
                  "Quality": [{"name": "qlty_missing", "description": "q", "arguments": []}]},
        "prompts": {"Prompts": [{"name": "base_query", "description": "p", "arguments": []}]}}
NAMES = {"srvA": "Teradata MCP - fitness_manager", "srvB": "Teradata MCP - nibrs_source_agent", "srvC": "Lake"}


def prof(**kw):
    p = {"id": "p1", "profile_type": "tool_enabled", "classification_mode": "light", "mcpServerId": "srvA",
         "tools": ["*"], "prompts": ["*"], "classification_results": {"last_classified": True}}
    p.update(kw)
    return p


print("[1] the categories are the runtime's own")
check("single-server agent → None (the panel response is unchanged)", resource_panel_servers(prof(), NAMES) is None)
out = resource_panel_servers(prof(additionalMcpServers=[{"id": "srvB", "alias": "nibrs", "tools": ["base_readQuery"], "prompts": ["*"]}],
                                  additionalMcpClassification={"srvB": SNAP}), NAMES)
check("an additional server's categories carry its alias", sorted(out["tools"]) == ["nibrs: Base", "nibrs: Quality"], sorted(out["tools"]))
names = {t["name"]: t["disabled"] for items in out["tools"].values() for t in items}
check("its tools are named <alias>__<name>", set(names) == {"nibrs__base_readQuery", "nibrs__base_tableList", "nibrs__qlty_missing"}, names)
check("each is flagged by THAT server's selection", names == {"nibrs__base_readQuery": False, "nibrs__base_tableList": True, "nibrs__qlty_missing": True}, names)
check("its prompts too", [p["name"] for p in out["prompts"]["nibrs: Prompts"]] == ["nibrs__base_query"])
check("every category maps to its server", set(out["category_servers"].values()) == {"srvB"} and len(out["category_servers"]) == 3, out["category_servers"])
check("the first server's id is returned for its own categories", out["primary_server_id"] == "srvA")
check("the snapshot is not modified", SNAP["tools"]["Base"][0]["name"] == "base_readQuery")

print("[2] every server listed, with its state")
out = resource_panel_servers(prof(additionalMcpServers=[
    {"id": "srvB", "alias": "nibrs", "tools": ["*"], "prompts": ["*"]},
    {"id": "srvC", "alias": "lake", "tools": ["*"], "prompts": ["*"]}],
    additionalMcpClassification={"srvB": SNAP}), NAMES)
st = {s["server_id"]: s for s in out["servers"]}
check("the first server comes first, named, with no alias", out["servers"][0] == {"server_id": "srvA", "name": NAMES["srvA"], "alias": None, "state": "classified"}, out["servers"][0])
check("a classified additional server: state + counts", st["srvB"]["state"] == "classified" and st["srvB"]["tools"] == 3 and st["srvB"]["prompts"] == 1, st["srvB"])
check("a server with no snapshot is listed, not omitted", st["srvC"]["state"] == "not_classified" and st["srvC"]["alias"] == "lake", st.get("srvC"))
check("…and contributes no categories", not any(c.startswith("lake: ") for c in out["tools"]))
wrong = dict(SNAP, classified_with_mode="full")
out = resource_panel_servers(prof(additionalMcpServers=[{"id": "srvB", "alias": "nibrs", "tools": ["*"], "prompts": []}],
                                  additionalMcpClassification={"srvB": wrong}), NAMES)
s2 = out["servers"][1]
check("a snapshot taken in another mode is not_classified, with the reason", s2["state"] == "not_classified" and "full mode" in s2.get("reason", ""), s2)
check("…and contributes no categories (a list the agent is not using is never shown as its own)", out["tools"] == {} and out["category_servers"] == {}, out["tools"])
out = resource_panel_servers(prof(classification_results={}, additionalMcpServers=[{"id": "srvB", "alias": "nibrs", "tools": ["*"], "prompts": []}]), {})
check("an unclassified first server says so; an unnamed server shows its id",
      out["servers"][0]["state"] == "not_classified" and out["servers"][1]["name"] == "srvB", out["servers"])

print("[3] Ideate+tools: tools only")
out = resource_panel_servers(prof(profile_type="llm_only", useMcpTools=True,
                                  additionalMcpServers=[{"id": "srvB", "alias": "nibrs", "tools": ["*"], "prompts": ["*"]}],
                                  additionalMcpClassification={"srvB": SNAP}), NAMES)
check("no prompts for an Ideate+tools agent", out["prompts"] == {} and out["tools"], out["prompts"])

print("[4] the endpoint")
src = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_profile_resources")
body = ast.get_source_segment(src, fn)
check("the endpoint asks resource_panel_servers", "resource_panel_servers" in body)
check("…adds servers / category_servers only when it answers", 'if _extra:' in body and body.index('if _extra:') < body.index('payload["servers"]'))
check("…merges, never replaces, the first server's lists", 'payload["tools"] = {**mcp_profile_tools, **_extra["tools"]}' in body)
check("…maps the first server's categories, never the injected ones", "if c not in _INJ" in body)
check("…and a failure keeps the first server's lists (logged, never raised)",
      "except Exception as _e:" in body and body.index("except Exception as _e:") > body.index("_rps(profile, _names)"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
