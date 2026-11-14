#!/usr/bin/env python3
"""PROP-006 decision 10 — an agent's MCP servers, one list for every reader.

[1] servers_of / aliases_of / is_multi_server / route over both storage shapes
[2] clean_additional — the door's truth table
[3] the REST door: an empty list removes the key; a bad entry is a 400 naming the problem
[4] M10 — reclassification when the (server, alias) set changes, never for a selection change
[5] M9 — the delete guard and the relationship detector see additional servers
[6] the contract — the server list travels as a binding (by name, resolved on import) and is
    signed as capability; the ids and the classification snapshots stay instance-local
    (implementation plan stage 2, decision 2.0)

Run: python3 test/test_agent_mcp_servers.py   (no server)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import agent_mcp_servers as ams  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {str(detail)[:400]}" if detail else ""))


def raises(fn, needle=""):
    try:
        fn()
    except ValueError as e:
        return needle in str(e)
    return False


SINGLE = {"id": "p1", "profile_type": "tool_enabled", "mcpServerId": "srvA",
          "tools": ["base_readQuery", "base_tableList"], "prompts": ["base_query"]}
MULTI = dict(SINGLE, additionalMcpServers=[
    {"id": "srvB", "alias": "crm", "tools": ["base_readQuery"], "prompts": []},
    {"id": "srvC", "alias": "erp", "tools": ["*"], "prompts": ["*"]},
])

print("[1] one list for every reader")
s1 = ams.servers_of(SINGLE)
check("single server: one entry, the primary", len(s1) == 1 and s1[0].primary and s1[0].server_id == "srvA")
check("single server: its selection is the profile's", s1[0].tools == ("base_readQuery", "base_tableList"))
check("single server: not multi", not ams.is_multi_server(SINGLE) and ams.aliases_of(SINGLE) == ())
check("no server at all: empty", ams.servers_of({"profile_type": "llm_only"}) == ())
sm = ams.servers_of(MULTI)
check("multi: primary first, then in stored order", [s.server_id for s in sm] == ["srvA", "srvB", "srvC"])
check("multi: aliases", ams.aliases_of(MULTI) == ("crm", "erp"))
check("multi: ids", ams.server_ids_of(MULTI) == ("srvA", "srvB", "srvC"))
check("an additional server's names are qualified", sm[1].name_for("base_readQuery") == "crm__base_readQuery")
check("the primary's names are not", sm[0].name_for("base_readQuery") == "base_readQuery")
check("a wildcard selection selects every tool", sm[2].selects_tool("anything") and sm[2].selects_prompt("x"))
check("a literal selection selects only what it lists",
      sm[1].selects_tool("base_readQuery") and not sm[1].selects_tool("base_tableList"))
r = ams.route(MULTI, "crm__base_readQuery")
check("route: a qualified name reaches its server under the base name", r and r[0].server_id == "srvB" and r[1] == "base_readQuery")
r = ams.route(MULTI, "base_readQuery")
check("route: a plain name reaches the primary", r and r[0].server_id == "srvA" and r[1] == "base_readQuery")
r = ams.route(MULTI, "hr__base_readQuery")
check("route: an alias the agent does not have is part of a primary name", r and r[0].primary and r[1] == "hr__base_readQuery")
check("route: no server, no route", ams.route({}, "base_readQuery") is None)
bad = dict(SINGLE, additionalMcpServers=[
    {"id": "srvA", "alias": "self"},             # the primary again
    {"id": "srvB", "alias": "BAD"},              # invalid alias
    {"id": "srvC", "alias": "crm"},
    {"id": "srvD", "alias": "crm"},              # duplicate alias
    {"id": "srvC", "alias": "dup"},              # duplicate id
    "not a dict",
])
check("stored entries that are not valid are skipped, never guessed",
      [s.server_id for s in ams.servers_of(bad)] == ["srvA", "srvC"], [s.server_id for s in ams.servers_of(bad)])

print("[2] clean_additional — the door")
check("None is nothing", ams.clean_additional(None, "srvA") is None)
check("an empty list is nothing (the key is removed)", ams.clean_additional([], "srvA") is None)
ok = ams.clean_additional([{"id": "srvB", "alias": "crm", "tools": ["a", "a", "b"], "extra": 1}], "srvA", ["srvA", "srvB"])
check("a valid entry is kept with only its fields, selection de-duplicated",
      ok == [{"id": "srvB", "alias": "crm", "tools": ["a", "b"], "prompts": []}], ok)
cases = [
    ("not a list", {"id": "x"}, "must be a list"),
    ("too many", [{"id": f"s{i}", "alias": f"a{i}"} for i in range(9)], "at most"),
    ("not an object", ["srvB"], "must be an object"),
    ("no id", [{"alias": "crm"}], "no id"),
    ("the primary", [{"id": "srvA", "alias": "crm"}], "primary"),
    ("listed twice", [{"id": "srvB", "alias": "a"}, {"id": "srvB", "alias": "b"}], "twice"),
    ("unknown server", [{"id": "srvZ", "alias": "crm"}], "not one of your"),
    ("bad alias", [{"id": "srvB", "alias": "Crm"}], "short name"),
    ("alias twice", [{"id": "srvB", "alias": "crm"}, {"id": "srvC", "alias": "crm"}], "used twice"),
    ("selection not a list", [{"id": "srvB", "alias": "crm", "tools": "all"}], "must be a list"),
    ("name no provider accepts", [{"id": "srvB", "alias": "crm", "tools": ["base.readQuery"]}], "not usable"),
    ("name too long once qualified", [{"id": "srvB", "alias": "crm", "tools": ["x" * 62]}], "not usable"),
]
for name, raw, needle in cases:
    check(f"refused: {name}", raises(lambda r=raw: ams.clean_additional(r, "srvA", ["srvA", "srvB", "srvC"] + [f"s{i}" for i in range(9)]), needle))

print("[3] the REST door")
from quart import Quart  # noqa: E402
from trusted_data_agent.api import rest_routes as rr  # noqa: E402


class _CM:
    def get_mcp_servers(self, uid=None):
        return [{"id": "srvA"}, {"id": "srvB"}, {"id": "srvC"}]


app = Quart("t")


async def _door(data, primary, stored):
    async with app.app_context():
        err, remove = rr._clean_additional_mcp_servers(data, primary, stored, "u", _CM())
        if err:
            resp, code = err
            return (await resp.get_json(), code), remove
        return None, remove

d = {"additionalMcpServers": [{"id": "srvB", "alias": "crm", "tools": ["*"]}]}
err, rm = asyncio.run(_door(d, "srvA", None))
check("a valid list is cleaned in place", err is None and not rm and d["additionalMcpServers"][0]["prompts"] == [])
d = {"additionalMcpServers": []}
err, rm = asyncio.run(_door(d, "srvA", [{"id": "srvB", "alias": "crm"}]))
check("an empty list removes the key", err is None and rm and "additionalMcpServers" not in d)
d = {"additionalMcpServers": [{"id": "srvB", "alias": "Crm"}]}
err, rm = asyncio.run(_door(d, "srvA", None))
check("a bad entry is a 400 naming it", err and err[1] == 400 and err[0]["error"] == "invalid_additional_mcp_servers"
      and "short name" in err[0]["message"], err)
d = {"mcpServerId": "srvB"}
err, rm = asyncio.run(_door(d, "srvB", [{"id": "srvB", "alias": "crm"}]))
check("making an additional server the primary without removing it is refused", err and err[1] == 400, err)
d = {"tag": "X"}
err, rm = asyncio.run(_door(d, "srvA", [{"id": "srvB", "alias": "crm"}]))
check("a save without the key leaves it alone", err is None and not rm and d == {"tag": "X"})
src = open(rr.__file__).read()
check("create and update both pass through the door", src.count("_clean_additional_mcp_servers(") >= 3)
check("update removes the stored key when emptied",
      'config_manager.remove_profile_keys(profile_id, ["additionalMcpServers"], user_uuid)' in src)

print("[4] M10 — reclassification")
from trusted_data_agent.core.config_manager import reclassification_needed as rn  # noqa: E402
cur = {"mcpServerId": "a", "classification_results": {"last_classified": "t"},
       "additionalMcpServers": [{"id": "b", "alias": "crm", "tools": ["x"]}]}
check("adding a server flags", rn(cur, {"additionalMcpServers": cur["additionalMcpServers"] + [{"id": "c", "alias": "erp"}]}))
check("removing every server flags", rn(cur, {"additionalMcpServers": []}))
check("renaming a server's alias flags", rn(cur, {"additionalMcpServers": [{"id": "b", "alias": "sales"}]}))
check("changing only its tool selection does not", not rn(cur, {"additionalMcpServers": [{"id": "b", "alias": "crm", "tools": ["y"]}]}))
check("absent from the save is not a change", not rn(cur, {"tag": "X"}))
check("never classified: never flagged", not rn({"mcpServerId": "a"}, {"additionalMcpServers": [{"id": "b", "alias": "crm"}]}))

print("[5] M9 — the delete guard and the relationship detector")
from trusted_data_agent.core import config_manager as cm  # noqa: E402


class _Past(Exception):
    pass


class _Fake:
    def __init__(self, profiles):
        self._p = profiles

    def get_profiles(self, uid=None):
        return self._p

    def get_rag_collections(self, uid=None):
        return []

    def get_mcp_servers(self, uid=None):
        raise _Past()


def _guard(profiles, sid):
    try:
        ok, msg = asyncio.run(cm.ConfigManager.remove_mcp_server(_Fake(profiles), sid, "u"))
        return (not ok), msg
    except _Past:
        return False, None

check("an Optimize agent's ADDITIONAL server cannot be deleted",
      _guard([dict(MULTI, tag="M")], "srvB")[0])
check("an Ideate+tools agent's additional server cannot be deleted",
      _guard([{"id": "i", "tag": "I", "profile_type": "llm_only", "useMcpTools": True, "mcpServerId": "srvA",
               "additionalMcpServers": [{"id": "srvB", "alias": "crm"}]}], "srvB")[0])
check("a Focus agent's stale binding does not block deletion",
      not _guard([{"id": "f", "tag": "F", "profile_type": "rag_focused", "mcpServerId": "srvA"}], "srvA")[0])
check("an Ideate agent without MCP tools does not block deletion",
      not _guard([{"id": "i", "tag": "I", "profile_type": "llm_only", "useMcpTools": False, "mcpServerId": "srvA"}], "srvA")[0])

from unittest.mock import patch  # noqa: E402
from trusted_data_agent.core import artifact_detectors as ad  # noqa: E402


class _CM2:
    def get_profiles(self, uid=None):
        return [dict(MULTI, name="Multi", tag="M"), dict(SINGLE, id="p2", name="Single", tag="S")]


with patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=_CM2()):
    found_b = asyncio.run(ad.McpServerDetector().find_profiles("srvB", "u"))
    found_a = asyncio.run(ad.McpServerDetector().find_profiles("srvA", "u"))
check("the detector finds an agent reading the server as an additional source",
      [(f["profile_tag"], f["relationship_type"]) for f in found_b] == [("M", "mcp_connection_additional")], found_b)
check("the detector's primary relationship is unchanged",
      sorted((f["profile_tag"], f["relationship_type"]) for f in found_a) == [("M", "mcp_connection"), ("S", "mcp_connection")], found_a)

print("[6] the contract — a binding, signed as capability; snapshots instance-local")
from trusted_data_agent.core import profile_config_contract as pcc  # noqa: E402
check("additionalMcpServers is not copied by the contract (its ids are instance-local)",
      ams.ADDITIONAL_KEY not in pcc.portable_keys())
check("…it travels as a declared import binding, resolved by name on import",
      ams.ADDITIONAL_KEY in pcc.binding_keys() and ams.ADDITIONAL_KEY in pcc.IMPORT_BINDING_KEYS)
check("the additional snapshots are deployment-local", ams.ADDITIONAL_CLASSIFICATION_KEY in pcc.DEPLOYMENT_LOCAL_KEYS)
check("…and never import-writable", ams.ADDITIONAL_CLASSIFICATION_KEY not in pcc.import_writable_keys())
check("the server list is signed as capability; the snapshots are not",
      ams.ADDITIONAL_KEY in pcc.signed_keys()
      and ams.ADDITIONAL_CLASSIFICATION_KEY not in pcc.signed_keys())
_sig = pcc.capability_signature({"profile_type": "tool_enabled", "mcpServerId": "a",
                                 ams.ADDITIONAL_KEY: [{"id": "b", "alias": "crm", "tools": ["x"], "prompts": []}]})
check("the signature carries the alias and selection, never the server id",
      "crm" in str(_sig) and "'b'" not in str(_sig.get(ams.ADDITIONAL_KEY)))

print("[7] M5 — the deployment half names each additional server's catalog")
from trusted_data_agent.core.trust import deployment as dep  # noqa: E402
LLM = {"provider": "Google", "model": "g"}
single_sig = dep.build_deployment_signature(LLM, dep.catalog_from_classification(
    dict(SINGLE, classification_results={"tools": {"All Tools": [{"name": "base_readQuery"}]},
                                         "last_classified": "t"})), [])
check("a single-server agent's signature carries no additional_mcp field", "additional_mcp" not in single_sig)
check("…and additional_catalogs is None for it", dep.additional_catalogs(SINGLE) is None)
check("a class that never calls MCP carries none either",
      dep.additional_catalogs(dict(MULTI, profile_type="rag_focused")) is None)
snapB = {"tools": {"All Tools": [{"name": "base_readQuery", "arguments": [{"name": "sql"}]}],
                   "System Tools": [{"name": "TDA_X"}]}, "prompts": {}, "last_classified": "t"}
withsnap = dict(MULTI, additionalMcpClassification={"srvB": snapB})
cats = dep.additional_catalogs(withsnap)
check("one entry per additional server, in the server's own names, injected categories excluded",
      cats[0] == {"server_id": "srvB", "alias": "crm", "catalog": {"tools": ["base_readQuery(sql)"], "prompts": []}}, cats)
check("a server never classified here is UNRESOLVED, not 'no tools'", cats[1]["catalog"] == dep.UNRESOLVED, cats[1])
sig = dep.build_deployment_signature(LLM, None, [], additional_mcp=cats)
check("the field is ordered by alias", [x["alias"] for x in sig["additional_mcp"]] == ["crm", "erp"])
ref = dep.attest_refusal(sig)
check("attesting over an unclassified additional server is refused, naming it",
      ref and "additional_mcp:erp" in ref["unresolved"] and "'erp'" in ref["message"], ref)
sig_ok = dep.build_deployment_signature(LLM, None, [], additional_mcp=cats[:1])
check("with every additional server classified, attestation is allowed", dep.attest_refusal(sig_ok) is None)
check("adding a server moves the deployment hash",
      dep.deployment_hash(sig_ok) != dep.deployment_hash(dep.build_deployment_signature(LLM, None, [])))
grown = [dict(cats[0], catalog={"tools": ["base_readQuery(sql)", "base_writeQuery(sql)"], "prompts": []})]
d = dep.describe_drift(sig_ok, dep.build_deployment_signature(LLM, None, [], additional_mcp=grown))
check("a tool appearing on an additional server is named with its server",
      d == ["MCP server crm: tools appeared: base_writeQuery(sql)"], d)
d = dep.describe_drift(dep.build_deployment_signature(LLM, None, []), sig_ok)
check("an added server is named", d == ["MCP server added: crm (srvB)"], d)
d = dep.describe_drift(sig_ok, dep.build_deployment_signature(LLM, None, [], additional_mcp=[dict(cats[0], alias="sales")]))
check("a renamed alias is named", d == ["MCP server srvB renamed: crm → sales"], d)
check("one naming rule for the primary's catalog and every additional one",
      "names = _catalog_names(cr)" in open(dep.__file__).read())

print("[8] M6 — a planner keyed to an additional server is the agent's, in its turn")
from unittest.mock import patch as _p  # noqa: E402
from trusted_data_agent.agent import rag_retriever as _rr  # noqa: E402
from trusted_data_agent.core import tool_identity as _ti  # noqa: E402
_R = _rr.RAGRetriever.__new__(_rr.RAGRetriever)
with _p.object(_rr, "planner_subscription_keys", return_value=set()):
    check("single server: a planner keyed to another server is not loaded",
          not _R._planner_keyed_to(1, "srvB", "srvA"))
    check("single server: the primary's planner is", _R._planner_keyed_to(1, "srvA", "srvA"))
    _tok = _ti.set_turn_servers({"crm": "srvB"})
    try:
        check("in a turn with srvB as an additional server, its planner is the agent's",
              _R._planner_keyed_to(1, "srvB", "srvA"))
        check("…and a planner of an unrelated server still is not", not _R._planner_keyed_to(1, "srvZ", "srvA"))
    finally:
        _ti.reset_turn_servers(_tok)
    check("after the turn the additional server's planner is not loaded again",
          not _R._planner_keyed_to(1, "srvB", "srvA"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
