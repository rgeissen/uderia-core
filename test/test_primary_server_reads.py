"""Multi-source implementation plan, stage 1.5 — the agent's first MCP server is read through ONE
accessor (``agent_mcp_servers.primary_of``) on the multi-server paths, and direct reads of the
stored key never grow.

Why: the additional servers were always read through ``servers_of``/``additional_of``, while the
first server's key was read directly in 22 files, several of them on multi-server code paths.
Two ways of reading one agent's servers is how the two halves drift (the plan's F7).

The gate is a RATCHET over the source's syntax tree (comments and docstrings excluded):
1. every file that reads the key ``"mcpServerId"`` directly is on the allow-list below, and reads
   it no more often than recorded — a new direct read fails, naming the file;
2. the multi-server path files read it zero times (they go through ``primary_of``);
3. ``primary_of`` itself behaves: it returns the stored id as a string, None when absent/empty.

When a direct read is removed, lower its count here (the gate then keeps it removed).
No server, no network.
"""
import ast
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

KEY = "mcpServerId"

# file → maximum direct reads (ratchet; lower it when a read is removed, never raise it).
ALLOWED = {
    "src/trusted_data_agent/api/rest_routes.py": 21,
    "src/trusted_data_agent/core/config_manager.py": 10,
    "src/trusted_data_agent/core/agent_pack_manager.py": 6,      # stage 2 (packs) moves these
    "src/trusted_data_agent/api/agent_pack_routes.py": 5,
    "src/trusted_data_agent/core/configuration_service.py": 3,
    "src/trusted_data_agent/core/platform_ontology/projection.py": 3,
    "src/trusted_data_agent/api/database_navigator_routes.py": 2,
    "src/trusted_data_agent/api/routes.py": 2,
    "src/trusted_data_agent/core/profile_config_contract.py": 1,
    "src/trusted_data_agent/core/agent_mcp_servers.py": 1,       # the accessor's own constant
    "src/trusted_data_agent/api/dashboard_routes.py": 1,
    "src/trusted_data_agent/api/admin_routes.py": 1,
    "src/trusted_data_agent/api/auth_routes.py": 1,
    "src/trusted_data_agent/agent/engines/optimize_engine.py": 1,  # the @TAG override profile
    # TDA_Platform names the ARGUMENT `mcpServerId` (its resolver and the model's hint) — it
    # resolves a value a model sent to one of the caller's servers; it never reads a profile's.
    "src/trusted_data_agent/core/platform_operations.py": 2,
}

# Multi-server code paths: must read the first server through primary_of, never directly.
MULTI_SERVER_PATHS = [
    "src/trusted_data_agent/agent/execution_service.py",
    "src/trusted_data_agent/agent/source_routing.py",
    "src/trusted_data_agent/mcp_adapter/adapter.py",
    "src/trusted_data_agent/agent/engines/conversation_engine.py",
    "src/trusted_data_agent/core/trust/deployment.py",
    "src/trusted_data_agent/core/artifact_detectors.py",
    "src/trusted_data_agent/agent/tool_enforcement.py",
    "src/trusted_data_agent/agent/conversation_agent.py",
]

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def direct_reads(path: pathlib.Path) -> int:
    tree = ast.parse(path.read_text())
    docs = {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    # An @operation descriptor DOCUMENTS a body field for agents (api/operations.py); it reads
    # nothing, so like a docstring it is not a read.
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "operation":
            docs |= {id(c) for c in ast.walk(n) if isinstance(c, ast.Constant)}
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and n.value == KEY and id(n) not in docs)


def scan():
    out = {}
    for root in ("src/trusted_data_agent", "components"):
        for p in (ROOT / root).rglob("*.py"):
            try:
                c = direct_reads(p)
            except SyntaxError:
                continue
            if c:
                out[str(p.relative_to(ROOT))] = c
    return out


print("[1] direct reads stay on the allow-list and never grow")
found = scan()
for f, c in sorted(found.items()):
    cap = ALLOWED.get(f)
    check(f"{f} reads the key {c}x (cap {cap})", cap is not None and c <= cap,
          "— a NEW direct read: go through agent_mcp_servers.primary_of" if cap is None
          else f"— grew beyond {cap}: go through agent_mcp_servers.primary_of")
check("the scan found the accessor's own constant (the scan is not vacuous)",
      found.get("src/trusted_data_agent/core/agent_mcp_servers.py") == 1)

print("[2] the multi-server paths read the first server only through primary_of")
for f in MULTI_SERVER_PATHS:
    p = ROOT / f
    check(f"{f}: 0 direct reads", p.exists() and direct_reads(p) == 0, f"({direct_reads(p) if p.exists() else 'missing'})")

print("[3] primary_of")
from trusted_data_agent.core.agent_mcp_servers import primary_of, servers_of  # noqa: E402
check("returns the stored id as a string", primary_of({KEY: 123}) == "123")
check("None when absent", primary_of({}) is None and primary_of(None) is None)
check("None when empty", primary_of({KEY: ""}) is None)
check("servers_of's first server is primary_of's",
      servers_of({KEY: "s1", "tools": ["*"]})[0].server_id == "s1")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
