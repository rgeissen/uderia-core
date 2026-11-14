#!/usr/bin/env python3
"""
BASELINE FREEZE for PROP-007 (flow topology templates).

Committed GREEN against the UNMODIFIED code before any feature code lands, and kept
green through every increment. It pins what "new flow" means today, so the picker,
the registry and the slot refusal can be proven to leave every existing path
byte-identical when no template is involved:

  B1  generate_empty_flow()  — start → end, one edge, settings == {autoLayout: True},
      no template stamp anywhere
  B2  FlowDefinition.validate + validate_and_repair accept the empty flow and a plain
      task_llm node with no profile and no slot (adding slot rules must not touch them)
  B3  create_flow() on a plain definition stores no template identity
  B4  the executor's profile cascade for a PLAIN node (no `data.slot`): no profile on
      the node → the CALLER's profile is sent to the REST query body (the fallback the
      slot refusal must run BEFORE, never instead of) — driven through the REAL
      _execute_llm_node_once over a fake HTTP seam
  B5  TDA_Flow create with no topology argument builds the empty flow; the manifest
      action enum is the frozen set below (increment 1 adds ARGS, never actions)
  B6  POST /v1/flows with no definition and no template falls through to
      generate_empty_flow (structural, on the route's source)

Usage:
    python test/test_flow_topology_baseline.py
"""

import asyncio
import ast
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# A fake aiohttp — faithful in the one dimension under test: it records the JSON
# bodies the executor sends and answers the three REST calls a task_llm node makes
# (create session → submit query → poll task). Installed into sys.modules so the
# executor's in-function `import aiohttp` picks it up; restored afterwards.
# ---------------------------------------------------------------------------
class FakeHttp:
    def __init__(self):
        self.calls = []            # (method, url, json)
        self.final_text = "fake answer"

    class _Resp:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        async def json(self):
            return self._payload

        async def text(self):
            return json.dumps(self._payload)

    class _Ctx:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *a):
            return False

    def _route(self, method, url, body):
        self.calls.append((method, url, body))
        if method == "POST" and url.endswith("/v1/sessions"):
            return self._Resp(201, {"session_id": "sess-fake"})
        if method == "POST" and "/query" in url:
            return self._Resp(202, {"task_id": "task-fake"})
        if method == "GET" and "/v1/tasks/" in url:
            return self._Resp(200, {
                "status": "complete",
                "result": {"final_answer_text": self.final_text},
                "events": [{"event_type": "token_update",
                            "event_data": {"turn_input": 10, "turn_output": 5, "cost_usd": 0.001}}],
            })
        return self._Resp(500, {"error": f"unrouted {method} {url}"})

    def module(self):
        fake = self
        mod = types.ModuleType("aiohttp")
        # Start from the REAL module's namespace: anything else imported while the fake is
        # installed (google-auth's aio transport subclasses aiohttp.ClientResponse) must
        # still find the real names. Only the two the executor uses are replaced.
        try:
            import importlib
            _real = sys.modules.get("aiohttp") or importlib.import_module("aiohttp")
            mod.__dict__.update({k: v for k, v in vars(_real).items() if not k.startswith("__")})
        except Exception:
            pass

        class ClientTimeout:
            def __init__(self, total=None):
                self.total = total

        class ClientSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def post(self, url, json=None, headers=None, timeout=None):
                return FakeHttp._Ctx(fake._route("POST", url, json))

            def get(self, url, headers=None, timeout=None):
                return FakeHttp._Ctx(fake._route("GET", url, None))

        mod.ClientTimeout = ClientTimeout
        mod.ClientSession = ClientSession
        return mod


FIXTURE_PROFILES = [
    {"id": "profile-node", "tag": "NODE", "profile_type": "llm_only"},
    {"id": "profile-caller", "tag": "CALLER", "profile_type": "llm_only"},
    {"id": "profile-opt", "tag": "OPT", "profile_type": "tool_enabled"},
]


class _FakeConfigManager:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_profiles(self, user_uuid=None):
        return list(self._profiles)


def install_profiles(profiles):
    """Make `_resolve_profile_ref` see a known profile set (it reads the config
    manager); returns a restore callable."""
    from trusted_data_agent.core import config_manager as cm
    saved = cm.get_config_manager
    cm.get_config_manager = lambda: _FakeConfigManager(profiles)
    return lambda: setattr(cm, "get_config_manager", saved)


def run_llm_node(node, caller_profile_id=None, user_id="user-1", profiles=None):
    """Drive the REAL _execute_llm_node_once over the fake HTTP seam."""
    from trusted_data_agent.core import flow_executor as fe
    restore = install_profiles(FIXTURE_PROFILES if profiles is None else profiles)
    fake = FakeHttp()
    saved = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = fake.module()
    saved_poll = fe.LLM_NODE_POLL_INTERVAL
    fe.LLM_NODE_POLL_INTERVAL = 0
    try:
        out = asyncio.run(fe._execute_llm_node_once(
            node, {}, user_id, "run-1", None,
            caller_session_id=None, node_session_mode="individual",
            caller_profile_id=caller_profile_id,
        ))
        return out, fake
    finally:
        restore()
        fe.LLM_NODE_POLL_INTERVAL = saved_poll
        if saved is not None:
            sys.modules["aiohttp"] = saved
        else:
            sys.modules.pop("aiohttp", None)


def _temp_flow_db():
    """Point flow_db at a throwaway database and apply its schema there."""
    from trusted_data_agent.core import flow_db as fdb
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    fdb._DB_PATH = Path(tmp.name)
    fdb._SCHEMA_APPLIED = False
    fdb._ensure_schema()
    fdb._ensure_columns()
    return fdb


# ---------------------------------------------------------------------------
def test_b1_empty_flow():
    print("\n[B1 generate_empty_flow]")
    from trusted_data_agent.core.flow_executor import generate_empty_flow
    d = generate_empty_flow()
    types_ = [n["type"] for n in d["nodes"]]
    check("two nodes: start, end", types_ == ["start", "end"])
    check("one edge start→end", len(d["edges"]) == 1
          and d["edges"][0]["source"] == d["nodes"][0]["id"]
          and d["edges"][0]["target"] == d["nodes"][1]["id"])
    check("no scopes, no params", d["scopes"] == [] and d["global_params"] == {})
    check("settings exactly {autoLayout: True}", d["settings"] == {"autoLayout": True})
    blob = json.dumps(d)
    check("no template stamp in the empty flow", "template" not in blob and "slot" not in blob)


def test_b2_validation_on_plain_shapes():
    print("\n[B2 validation of plain shapes]")
    from trusted_data_agent.core.flow_executor import generate_empty_flow, FlowDefinition
    from trusted_data_agent.core.flow_validation import validate_and_repair
    d = generate_empty_flow()
    check("FlowDefinition.validate(empty) == []", FlowDefinition(d).validate() == [])
    check("validate_and_repair(empty) == ([], False)", validate_and_repair(d) == ([], False))
    # A plain task_llm node with NO profile and NO slot block — today's ordinary node.
    d2 = generate_empty_flow()
    s, e = d2["nodes"]
    d2["nodes"].insert(1, {"id": "n-plain", "type": "task_llm", "label": "Plain",
                           "position": {"x": 0, "y": 0}, "data": {"prompt": "hello"}})
    d2["edges"] = [{"id": "e1", "source": s["id"], "target": "n-plain", "type": "normal"},
                   {"id": "e2", "source": "n-plain", "target": e["id"], "type": "normal"}]
    check("plain profile-less task_llm validates (FlowDefinition)", FlowDefinition(d2).validate() == [])
    errs, changed = validate_and_repair(d2)
    check("plain profile-less task_llm: validate_and_repair has no errors", errs == [])
    check("…and does not rewrite it", changed is False)


def test_b3_create_flow_plain():
    print("\n[B3 create_flow stores no template identity]")
    fdb = _temp_flow_db()
    from trusted_data_agent.core.flow_executor import generate_empty_flow
    flow = fdb.create_flow("user-1", "Plain", "", generate_empty_flow())
    check("flow created", bool(flow and flow.get("flow_id")))
    check("no template_id", not flow.get("template_id"))
    check("no template_version", not flow.get("template_version"))
    check("definition carries no slot/template keys",
          "slot" not in json.dumps(flow["definition"]) and "template" not in json.dumps(flow["definition"]))
    check("is_template defaults to 0", int(flow.get("is_template") or 0) == 0)
    listed = fdb.list_flows("user-1")
    check("list_flows returns it", any(f["flow_id"] == flow["flow_id"] for f in listed))


def test_b4_profile_cascade_plain_node():
    print("\n[B4 executor profile cascade for a PLAIN node]")
    node = {"id": "n1", "type": "task_llm", "label": "Plain", "data": {"prompt": "hi"}}
    out, fake = run_llm_node(node, caller_profile_id="profile-caller")
    posts = [c for c in fake.calls if c[0] == "POST"]
    check("node without a profile reached the REST seam", len(posts) >= 2)
    create_body = posts[0][2] or {}
    query_body = posts[1][2] or {}
    check("session create carries the CALLER profile (fallback)",
          create_body.get("profile_id") == "profile-caller")
    check("query body carries the CALLER profile (fallback)",
          query_body.get("profile_id") == "profile-caller", str(query_body))
    check("query is tagged source=flow", query_body.get("source") == "flow")
    check("node output is the task's final text", out.get("output") == "fake answer")
    check("tokens/cost read from the token_update",
          out.get("tokens_input") == 10 and out.get("tokens_output") == 5 and abs(out.get("cost_usd") - 0.001) < 1e-9)

    # No caller profile either → the body carries NO profile (execute_query uses the user default).
    out2, fake2 = run_llm_node(node, caller_profile_id=None)
    q2 = [c for c in fake2.calls if c[0] == "POST" and "/query" in c[1]][0][2]
    check("no node + no caller profile → no profile_id in the body (user default)", "profile_id" not in q2)

    # A node WITH its own real profile id wins over the caller.
    node3 = {"id": "n3", "type": "task_llm", "label": "P", "data": {"prompt": "hi", "profile_id": "profile-node"}}
    out3, fake3 = run_llm_node(node3, caller_profile_id="profile-caller")
    q3 = [c for c in fake3.calls if c[0] == "POST" and "/query" in c[1]][0][2]
    check("node profile wins over caller profile", q3.get("profile_id") == "profile-node")

    # A node whose profile reference resolves to NOTHING (deleted / hallucinated id)
    # is dropped and today falls back to the caller — pinned so the slot refusal is
    # measured against this exact behaviour (a slot filled with a dead id is OPEN).
    node4 = {"id": "n4", "type": "task_llm", "label": "P", "data": {"prompt": "hi", "profile_id": "profile-gone"}}
    _, fake4 = run_llm_node(node4, caller_profile_id="profile-caller")
    q4 = [c for c in fake4.calls if c[0] == "POST" and "/query" in c[1]][0][2]
    check("unresolvable node profile is dropped → caller fallback (today)", q4.get("profile_id") == "profile-caller")

    # The fallback line itself — the seam the slot refusal must sit BEFORE.
    src = (ROOT / "src/trusted_data_agent/core/flow_executor.py").read_text()
    check("the cascade fallback line is present verbatim",
          "effective_profile_id = node_profile_id or caller_profile_id" in src)


_FROZEN_ACTION_ENUM = {
    "create", "open", "get", "list", "update", "delete", "insert_node", "add_node",
    "remove_node", "update_node", "add_edge", "remove_edge", "create_scope",
    "update_scope", "update_global_params", "execute", "get_run", "cancel_run",
}
# Named, deliberate additions after the freeze (each with the increment that added it).
# A new action must be listed here with its reason, never folded into the set above.
_DELIBERATE_ACTION_ADDITIONS = {
    "delete_scope",  # PROP-007 increments 3+4: removing a loop routes through core/flow_loops.remove_loop
}
_FROZEN_ACTION_ENUM = _FROZEN_ACTION_ENUM | _DELIBERATE_ACTION_ADDITIONS


def test_b5_tool_create_without_topology():
    print("\n[B5 TDA_Flow create without a topology]")
    flow_dir = ROOT / "components" / "builtin" / "flow"
    manifest = json.loads((flow_dir / "manifest.json").read_text())
    desc = manifest["tool_definition"]["args"]["action"]["description"]
    enum = set(re.findall(r"'([a-z_]+)'", desc))
    check("manifest action enum is the frozen set", enum == _FROZEN_ACTION_ENUM, str(sorted(enum ^ _FROZEN_ACTION_ENUM)))

    # Drive the REAL handler's create path with create_flow stubbed to capture the definition.
    import importlib.util
    spec = importlib.util.spec_from_file_location("flow_handler_under_test", flow_dir / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    handler = mod.FlowComponentHandler.__new__(mod.FlowComponentHandler)

    from trusted_data_agent.core import flow_db as fdb
    captured = {}

    def fake_create_flow(user_id, name, description, definition):
        captured.update({"name": name, "definition": definition})
        return {"flow_id": "flow-fake", "name": name, "description": description, "definition": definition}

    saved = fdb.create_flow
    fdb.create_flow = fake_create_flow
    try:
        payload = asyncio.run(handler._handle_create({"name": "No topology"}, "user-1", {"profile_id": "profile-x"}))
    finally:
        fdb.create_flow = saved
    d = captured.get("definition") or {}
    check("create without topology builds the empty flow (start, end)",
          [n["type"] for n in d.get("nodes", [])] == ["start", "end"])
    check("creator profile stamped as settings.defaultProfileId",
          (d.get("settings") or {}).get("defaultProfileId") == "profile-x")
    check("no slot/template in the created definition",
          "slot" not in json.dumps(d) and "template" not in json.dumps(d))
    check("payload renders the canvas (not an error)",
          getattr(payload, "spec", {}).get("view") == "canvas", str(getattr(payload, "spec", {})))


def test_b6_rest_create_fallthrough():
    print("\n[B6 POST /v1/flows without definition → generate_empty_flow (structural)]")
    src = (ROOT / "src/trusted_data_agent/api/flow_routes.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_flow_endpoint")
    body_src = ast.get_source_segment(src, fn)
    check("route falls through to generate_empty_flow when no definition is given",
          "generate_empty_flow()" in body_src)
    check("route validates a supplied definition with FlowDefinition", "FlowDefinition(" in body_src)
    check("route gates on the flow component", "_assert_component_available" in body_src)


def main():
    print("=" * 70)
    print("PROP-007 BASELINE FREEZE — today's 'new flow' pinned")
    print("=" * 70)
    test_b1_empty_flow()
    test_b2_validation_on_plain_shapes()
    test_b3_create_flow_plain()
    test_b4_profile_cascade_plain_node()
    test_b5_tool_create_without_topology()
    test_b6_rest_create_fallthrough()
    print("\n" + "=" * 70)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
