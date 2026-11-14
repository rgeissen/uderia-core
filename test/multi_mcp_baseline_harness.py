"""PROP-006 phase 2 — the single-server freeze, driven through the REAL seams.

Before any code lets an agent read several MCP servers, this records what ONE server produces
today at every seam phase 2 touches, so the operator's rule — a single-source agent gets no added
burden (tokens, calls, latency, state, storage, signatures) — is measured, not asserted:

  classify   ``mcp_adapter.adapter.load_and_categorize_mcp_resources`` against a fake MCP client
             and a fake LLM, for four profile shapes: which server each MCP session opened, how
             many list_tools/list_prompts/list_resources calls and LLM classification calls ran,
             the classification prompt (hashed), every STATE slot it writes and the snapshot it
             hands to ``save_profile_classification``.
  catalog    the planner's rendered catalog — ``executor.rebuild_tools_and_prompts_context`` over
             the classified state (what the strategic and tactical prompts carry).
  surface    the S6 governed surface over that catalog (offered / held / reasons).
  signature  the deployment half: ``catalog_from_classification`` + ``deployment_hash``.
  react      ``llm.langchain_adapter.load_mcp_tools_for_langchain`` over a fake
             ``langchain_mcp_adapters`` loader: the tools bound, their order, their metadata, the
             connection the loader was handed.
  route      ``invoke_mcp_tool``: the server a call reaches and the name it is sent under, with
             no overlay and with an @TAG overlay naming its own server.
  reclass    ``config_manager.reclassification_needed`` over a truth table.
  delete     ``ConfigManager.remove_mcp_server``'s dependent-profile guard.

``observe()`` returns a JSON-serialisable dict. ``test_multi_mcp_baseline.py`` compares it with
the golden file written from the code BEFORE phase 2. Regenerate only on purpose, and only with a
named, asserted exception in the test (the ``test_sql_gate_lanes`` precedent):
    python test/multi_mcp_baseline_harness.py --write
"""

import asyncio
import collections
import copy
import hashlib
import json
import logging
import os
import sys
import types
from contextlib import ExitStack
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
logging.disable(logging.CRITICAL)

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "multi_mcp_baseline.golden.json")

from trusted_data_agent.mcp_adapter import adapter                    # noqa: E402
from trusted_data_agent.core import config_manager as cm              # noqa: E402
from trusted_data_agent.core.trust import deployment as dep           # noqa: E402
from trusted_data_agent.agent import tool_surface                     # noqa: E402
from trusted_data_agent.agent.executor import rebuild_tools_and_prompts_context  # noqa: E402
from trusted_data_agent.llm import langchain_adapter as lca           # noqa: E402

SERVER = "srv-primary"
USER = "u-freeze"


# ── the one MCP server ───────────────────────────────────────────────────────────────────────
def _tool(name, desc, props=None, required=()):
    schema = {"type": "object", "properties": props or {}, "required": list(required)}
    return types.SimpleNamespace(name=name, description=desc, inputSchema=schema)


TOOLS = [
    _tool("base_readQuery", "Runs a SQL query.\n\nArguments:\n  sql - the SQL to run",
          {"sql": {"type": "string", "title": "Sql"}}, ["sql"]),
    _tool("base_databaseList", "Lists databases.", {"scope": {"type": "string"}}),
    _tool("base_tableList", "Lists the tables of a database.",
          {"database_name": {"type": "string"}}, ["database_name"]),
    _tool("base_columnDescription", "Describes the columns of a table.",
          {"database_name": {"type": "string"}, "obj_name": {"type": "string"}},
          ["database_name", "obj_name"]),
    _tool("qlty_columnSummary", "Column statistics for data quality.",
          {"database_name": {"type": "string"}, "table_name": {"type": "string"},
           "column_name": {"type": "string"}}, ["database_name", "table_name", "column_name"]),
    _tool("dba_tableSpace", "Space used per table.", {"database_name": {"type": "string"}}),
]


class _Arg:
    def __init__(self, **kw):
        self._kw = kw

    def model_dump(self):
        return dict(self._kw)


PROMPTS = [
    types.SimpleNamespace(name="base_query", description="Answer a question about a table. (type: sql)",
                          arguments=[_Arg(name="database_name", description="The database", required=True),
                                     _Arg(name="question", description="The question", required=False)]),
    types.SimpleNamespace(name="qlty_databaseQuality", description="Quality review of a database.",
                          arguments=[_Arg(name="database_name", description="The database", required=True)]),
]
RESOURCES = [types.SimpleNamespace(name="schema_doc", description="The schema reference.")]

CLASSIFY_ANSWER = json.dumps({
    "base_readQuery": {"category": "Query Execution"},
    "base_databaseList": {"category": "Database Information"},
    "base_tableList": {"category": "Database Information"},
    "base_columnDescription": {"category": "Database Information"},
    "qlty_columnSummary": {"category": "Data Quality"},
    "dba_tableSpace": {"category": "Performance"},
    "base_query": {"category": "Query Execution"},
    "qlty_databaseQuality": {"category": "Data Quality"},
    "schema_doc": {"category": "Reference"},
})


class Wire:
    """Records every session the adapter opens and every call made inside it."""

    def __init__(self):
        self.log = []

    def client(self):
        wire = self

        class _Session:
            def __init__(self, sid):
                self.sid = sid

            async def list_tools(self):
                wire.log.append(["list_tools", self.sid])
                return types.SimpleNamespace(tools=list(TOOLS))

            async def list_prompts(self):
                wire.log.append(["list_prompts", self.sid])
                return types.SimpleNamespace(prompts=list(PROMPTS))

            async def list_resources(self):
                wire.log.append(["list_resources", self.sid])
                return types.SimpleNamespace(resources=list(RESOURCES))

            async def call_tool(self, name, args):
                wire.log.append(["call_tool", self.sid, name, dict(args)])
                content = [types.SimpleNamespace(text=json.dumps({"status": "success", "results": [{"n": 1}]}))]
                return types.SimpleNamespace(content=content, isError=False)

        class _Ctx:
            def __init__(self, sid):
                self.sid = sid

            async def __aenter__(self):
                wire.log.append(["session", self.sid])
                return _Session(self.sid)

            async def __aexit__(self, *e):
                return False

        class _Client:
            def session(self, sid):
                return _Ctx(sid)

        return _Client()


# ── a config manager faithful in what these seams read ───────────────────────────────────────
class FakeCM:
    def __init__(self, profiles, enabled_tools=None, servers=None):
        self.profiles = {p["id"]: p for p in profiles}
        self.saved = []
        self._enabled = enabled_tools
        self._servers = servers or []

    def get_profile(self, pid, uid=None):
        return self.profiles.get(pid)

    def get_profiles(self, uid=None):
        return list(self.profiles.values())

    def save_profile_classification(self, profile_id, classification_results, user_uuid=None):
        self.saved.append({"profile_id": profile_id, "results": copy.deepcopy(classification_results)})

    def get_profile_enabled_tools(self, pid, uid=None):
        return list(self._enabled) if self._enabled is not None else ["*"]

    def get_mcp_servers(self, uid=None):
        return copy.deepcopy(self._servers)

    def get_rag_collections(self, uid=None):
        return []


PROFILES = {
    "optimize_light": {"id": "p-opt-light", "tag": "OPTL", "profile_type": "tool_enabled",
                       "classification_mode": "light", "mcpServerId": SERVER},
    "optimize_full": {"id": "p-opt-full", "tag": "OPTF", "profile_type": "tool_enabled",
                      "classification_mode": "full", "mcpServerId": SERVER},
    "ideate_tools": {"id": "p-ideate", "tag": "IDT", "profile_type": "llm_only", "useMcpTools": True,
                     "classification_mode": "full", "mcpServerId": SERVER},
    "focus": {"id": "p-focus", "tag": "FOC", "profile_type": "rag_focused",
              "classification_mode": "full", "mcpServerId": SERVER},
}


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(_jsonable(x) for x in v)
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return repr(type(v).__name__)


def _sha(text):
    return hashlib.sha256((text or "").encode()).hexdigest()


def _tool_obj(t):
    return {"name": t.name, "description": getattr(t, "description", None),
            "args": _jsonable(getattr(t, "args", None))}


def classify(profile_key, platform_tools=()):
    """One activation's classification, exactly as ``switch_profile_context`` drives it."""
    prof = copy.deepcopy(PROFILES[profile_key])
    fake = FakeCM([prof])
    wire = Wire()
    llm_calls = []

    async def _llm(llm, prompt, **kw):
        llm_calls.append({"prompt_sha": _sha(prompt), "prompt_len": len(prompt),
                          "system": kw.get("system_prompt_override")})
        return CLASSIFY_ANSWER, 10, 5, "prov", "model"

    state = {"mcp_client": wire.client(), "llm": object()}
    with ExitStack() as st:
        st.enter_context(patch.object(adapter, "get_user_mcp_server_id", return_value=SERVER))
        st.enter_context(patch.object(adapter.llm_handler, "call_llm_api", side_effect=_llm))
        st.enter_context(patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=fake))
        st.enter_context(patch("trusted_data_agent.core.platform_connector_registry.get_effective_tools",
                               return_value=[copy.deepcopy(t) for t in platform_tools]))
        asyncio.run(adapter.load_and_categorize_mcp_resources(state, user_uuid=USER, profile_id=prof["id"]))

    counts = collections.Counter(e[0] for e in wire.log)
    out = {
        "wire": wire.log,
        "call_counts": dict(sorted(counts.items())),
        "llm_calls": llm_calls,
        "mcp_tools": [_tool_obj(t) for t in state["mcp_tools"].values()],
        "mcp_prompts": sorted((state.get("mcp_prompts") or {}).keys()),
        "structured_tools": _jsonable(state.get("structured_tools")),
        "structured_prompts": _jsonable(state.get("structured_prompts")),
        "structured_resources": _jsonable(state.get("structured_resources")),
        "tool_scopes": _jsonable(state.get("tool_scopes")),
        "tools_context_slot": state.get("tools_context"),
        "prompts_context_slot": state.get("prompts_context"),
        "all_known_mcp_arguments": {k: sorted(v) for k, v in (state.get("all_known_mcp_arguments") or {}).items()},
        "schema_cache_keys": sorted((state.get("mcp_tool_schema_cache") or {}).keys()),
        "state_keys": sorted(k for k in state if k not in ("mcp_client", "llm")),
        "saved": _jsonable(fake.saved),
    }
    return out, state, prof, fake


def catalog_and_surface(state):
    tc, pc = rebuild_tools_and_prompts_context(state=state)
    cat = tool_surface.catalog_from_structured(state.get("structured_tools") or {}, state.get("tool_scopes"))
    s = tool_surface.select("how many customers per state had quality problems", cat, mode="governed")
    return {
        "tools_context": tc,
        "prompts_context": pc,
        "tools_context_sha": _sha(tc),
        "prompts_context_sha": _sha(pc),
        "surface_catalog": [[t.name, t.category] for t in cat],
        "surface_offered": list(s.offered),
        "surface_held": list(s.held),
        "surface_denied": list(s.denied),
        "surface_reasons": dict(s.reasons),
    }


def signature(prof, saved):
    p = copy.deepcopy(prof)
    cr = copy.deepcopy(saved[-1]["results"]) if saved else None
    if cr is not None:
        cr["last_classified"] = "2026-09-28T00:00:00+00:00"
    p["classification_results"] = cr
    catalog = dep.catalog_from_classification(p)
    sig = dep.build_deployment_signature({"provider": "Google", "model": "gemini"}, catalog, [])
    return {"catalog": _jsonable(catalog), "signature": _jsonable(sig), "hash": dep.deployment_hash(sig)}


def react(enabled_tools):
    """The ReAct lane's MCP tool binding for one server."""
    loaded = []

    class _LT:
        def __init__(self, name):
            self.name = name
            self.args_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
            self.metadata = None

    async def _load(session=None, connection=None):
        loaded.append(_jsonable({k: (str(v) if k in ("timeout", "sse_read_timeout") else v)
                                 for k, v in (connection or {}).items()}))
        return [_LT(t.name) for t in TOOLS]

    servers = [{"id": SERVER, "name": "Primary", "host": "db.example", "port": 8001, "path": "/mcp",
                "transport": {"type": "streamable_http"}}]
    fake = FakeCM([PROFILES["ideate_tools"]], enabled_tools=enabled_tools, servers=servers)
    import langchain_mcp_adapters.tools as lmt
    with ExitStack() as st:
        st.enter_context(patch.object(lca, "get_config_manager", return_value=fake))
        st.enter_context(patch.object(lmt, "load_mcp_tools", side_effect=_load))
        tools = asyncio.run(lca.load_mcp_tools_for_langchain(SERVER, PROFILES["ideate_tools"]["id"], USER))
    return {"bound": [t.name for t in tools], "metadata": [_jsonable(t.metadata) for t in tools],
            "connections": loaded}


def route(overlay_server=None):
    wire = Wire()
    base = {"mcp_client": wire.client(),
            "mcp_tools": {"base_readQuery": types.SimpleNamespace(name="base_readQuery",
                                                                   args={"sql": {"name": "sql"}})}}
    state = collections.ChainMap({"mcp_server_id": overlay_server}, base) if overlay_server else base
    with ExitStack() as st:
        st.enter_context(patch.object(adapter, "get_user_mcp_server_id", return_value=SERVER))
        st.enter_context(patch("trusted_data_agent.core.platform_connector_registry.get_connector_for_tool",
                               return_value=None))
        res, i, o = asyncio.run(adapter.invoke_mcp_tool(
            state, {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}},
            user_uuid=USER, session_id="s-freeze", workflow_state={}))
    return {"wire": wire.log, "status": (res or {}).get("status") if isinstance(res, dict) else repr(type(res).__name__)}


RECLASS_CASES = [
    ("never classified", {"mcpServerId": "a"}, {"mcpServerId": "b"}),
    ("server changed", {"mcpServerId": "a", "classification_results": {"last_classified": "t"}}, {"mcpServerId": "b"}),
    ("server same", {"mcpServerId": "a", "classification_results": {"last_classified": "t"}}, {"mcpServerId": "a"}),
    ("server absent in incoming", {"mcpServerId": "a", "classification_results": {"last_classified": "t"}}, {"tag": "X"}),
    ("mode changed", {"classification_mode": "light", "classification_results": {"last_classified": "t"}},
     {"classification_mode": "full"}),
    ("llm changed, light", {"classification_mode": "light", "llmConfigurationId": "l1",
                            "classification_results": {"last_classified": "t"}}, {"llmConfigurationId": "l2"}),
    ("llm changed, full", {"classification_mode": "full", "llmConfigurationId": "l1",
                           "classification_results": {"last_classified": "t"}}, {"llmConfigurationId": "l2"}),
    ("legacy mcp_servers changed", {"mcp_servers": ["a"], "classification_results": {"last_classified": "t"}},
     {"mcp_servers": ["b"]}),
    ("an unknown key", {"mcpServerId": "a", "classification_results": {"last_classified": "t"}},
     {"additionalMcpServers": [{"id": "b", "alias": "crm"}]}),
]


def reclass():
    return {name: cm.reclassification_needed(cur, inc) for name, cur, inc in RECLASS_CASES}


def delete_guard():
    out = {}
    cases = {
        "optimize bound": {"id": "p1", "tag": "O", "profile_type": "tool_enabled", "mcpServerId": SERVER},
        "ideate+tools bound": {"id": "p2", "tag": "I", "profile_type": "llm_only", "useMcpTools": True,
                               "mcpServerId": SERVER},
        "optimize other server": {"id": "p3", "tag": "Q", "profile_type": "tool_enabled", "mcpServerId": "other"},
    }
    for name, prof in cases.items():
        fake = FakeCM([prof], servers=[])
        # anything past the dependent-profile guard means the guard did not refuse
        fake.get_mcp_servers = lambda uid=None: (_ for _ in ()).throw(_PastGuard())
        try:
            ok, msg = asyncio.run(cm.ConfigManager.remove_mcp_server(fake, SERVER, USER))
            out[name] = {"refused": not ok, "message": msg}
        except _PastGuard:
            out[name] = {"refused": False, "message": None}
    return out


class _PastGuard(Exception):
    pass


PLATFORM_TOOL = {"name": "web_search", "description": "Search the web.", "_category": "Platform: Web",
                 "arguments": [{"name": "query", "type": "string", "required": True, "description": "q"}]}


def observe():
    out = {}
    for key in PROFILES:
        cls, state, prof, fake = classify(key)
        entry = {"classify": cls}
        if key in ("optimize_light", "optimize_full"):
            entry.update(catalog_and_surface(state))
        entry["deployment"] = signature(prof, fake.saved)
        out[key] = entry
    cls, state, prof, fake = classify("optimize_light", platform_tools=[PLATFORM_TOOL])
    out["optimize_light_platform_tool"] = {"classify": cls, **catalog_and_surface(state)}
    out["react_all"] = react(["*"])
    out["react_selected"] = react(["base_readQuery", "base_tableList"])
    out["route_plain"] = route()
    out["route_overlay"] = route(overlay_server="srv-override")
    out["reclass"] = reclass()
    out["delete_guard"] = delete_guard()
    return json.loads(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    data = observe()
    if "--write" in sys.argv:
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        print(f"wrote {GOLDEN}")
    else:
        print(json.dumps(data, indent=1, sort_keys=True)[:4000])
