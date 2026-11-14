"""WS2 tests — MCP HTTP/SSE transport for platform connectors.

Spins a real MCP server over streamable HTTP (FastMCP, in a subprocess) and
exercises the new HTTP transport end-to-end: tool invocation, the shared result
envelope (parity with STDIO), tool discovery, persisted-schema read, and error
paths.

Run:
    PYTHONPATH=src python test/test_mcp_http_connector.py
"""
import os
import sys
import time
import json
import socket
import asyncio
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import platform_connector_registry as reg  # noqa: E402

# Isolate the registry DB to a temp file (the registry reads reg._DB_PATH at call
# time via _get_conn). Created in _setup_db().
_TMP_DB = Path(tempfile.mkdtemp(prefix="tda_pconn_")) / "tda_auth.db"


def _setup_db():
    reg._DB_PATH = _TMP_DB
    import sqlite3
    conn = sqlite3.connect(str(_TMP_DB))
    conn.execute("""
        CREATE TABLE platform_connectors (
            id TEXT PRIMARY KEY, source_id TEXT, name TEXT, display_name TEXT,
            description TEXT, version TEXT, registry_metadata TEXT, install_spec TEXT,
            install_status TEXT, connector_type TEXT DEFAULT 'mcp_stdio',
            enabled INTEGER DEFAULT 0, requires_user_auth INTEGER DEFAULT 0,
            credentials TEXT, available_tools TEXT, created_at TEXT, updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def run(coro):
    return _loop.run_until_complete(coro)


_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


# ── spin a real MCP-over-HTTP server ──────────────────────────────────────────
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


_PORT = _free_port()
_URL = f"http://127.0.0.1:{_PORT}/mcp"
_server_proc = None


def _start_server():
    global _server_proc
    here = os.path.dirname(__file__)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(here, "..", "src")
    _server_proc = subprocess.Popen(
        [sys.executable, os.path.join(here, "mcp_http_test_server.py"), str(_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    if not _wait_port(_PORT):
        raise RuntimeError("MCP HTTP test server did not start")


def _stop_server():
    if _server_proc is not None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except Exception:
            _server_proc.kill()


# ── tests ─────────────────────────────────────────────────────────────────────
def t_envelope_helper():
    # The shared envelope is what guarantees STDIO/HTTP parity.
    e = reg._mcp_result_to_envelope({"content": [{"text": '{"a": 1}'}]})
    check("envelope: JSON text -> dict + status success", e == {"a": 1, "status": "success"})
    e2 = reg._mcp_result_to_envelope({"content": [{"text": "plain text"}]})
    check("envelope: non-JSON text -> result wrapper", e2 == {"status": "success", "result": "plain text"})
    e3 = reg._mcp_result_to_envelope({})
    check("envelope: empty -> success result", e3.get("status") == "success")


async def _invoke_http(tool, args):
    return await reg._call_via_mcp_http(_URL, {}, tool, args)


def t_http_invoke():
    r = run(_invoke_http("echo", {"message": "hi"}))
    check("HTTP invoke echo -> parsed JSON + status", r.get("echoed") == "hi" and r.get("status") == "success")
    r2 = run(_invoke_http("add", {"a": 2, "b": 3}))
    check("HTTP invoke add -> sum 5", r2.get("sum") == 5 and r2.get("status") == "success")


def t_strategy_via_install_spec():
    # Exercise McpHttpStrategy through a connector record (install_spec carries the url).
    server = {"id": "test-remote", "install_spec": {"url": _URL}}
    r = run(reg.McpHttpStrategy().invoke_tool(server, "echo", {"message": "via strategy"}, env={}))
    check("McpHttpStrategy resolves install_spec.url + invokes", r.get("echoed") == "via strategy")
    # host/port/path form
    server2 = {"id": "test-remote2", "install_spec": {"host": "127.0.0.1", "port": _PORT, "path": "/mcp"}}
    r2 = run(reg.McpHttpStrategy().invoke_tool(server2, "add", {"a": 10, "b": 1}, env={}))
    check("McpHttpStrategy resolves host/port/path form", r2.get("sum") == 11)


def t_endpoint_resolution():
    url, headers = reg._http_endpoint({"install_spec": {"url": _URL, "auth": {"bearer": "tok123"}}})
    check("_http_endpoint url", url == _URL)
    check("_http_endpoint bearer -> Authorization header", headers.get("Authorization") == "Bearer tok123")
    url2, _ = reg._http_endpoint({"install_spec": {}})
    check("_http_endpoint missing url -> None", url2 is None)
    # bearer injected via env (credentials)
    _, h3 = reg._http_endpoint({"install_spec": {"url": _URL}}, env={"MCP_BEARER_TOKEN": "envtok"})
    check("_http_endpoint bearer from env creds", h3.get("Authorization") == "Bearer envtok")


def t_error_paths():
    # missing endpoint
    r = run(reg.McpHttpStrategy().invoke_tool({"id": "x", "install_spec": {}}, "echo", {}, env={}))
    check("missing url -> structured error", r.get("status") == "error" and "endpoint" in r.get("error", ""))
    # unreachable endpoint
    bad = run(reg._call_via_mcp_http("http://127.0.0.1:1/mcp", {}, "echo", {"message": "x"}))
    check("unreachable url -> structured error", bad.get("status") == "error")


def t_discover():
    tools = run(reg.discover_http_tools(_URL, {}))
    names = {t["name"] for t in tools}
    check("discover_http_tools finds echo + add", {"echo", "add"} <= names)
    add_tool = next((t for t in tools if t["name"] == "add"), {})
    arg_names = {a["name"] for a in add_tool.get("arguments", [])}
    check("discovered tool carries arguments (a, b)", {"a", "b"} <= arg_names)


def t_install_persist_read():
    res = run(reg.install_remote_connector("remote-test", "Remote Test", _URL))
    check("install_remote_connector succeeds", res.get("status") == "success" and res.get("tool_count") == 2)
    server = reg.get_server("remote-test")
    check("installed connector is mcp_http", server and server.get("connector_type") == "mcp_http")
    check("tool schemas persisted in registry_metadata",
          bool((server.get("registry_metadata") or {}).get("tool_schemas")))
    # sync planning path reads persisted schemas, namespaced
    schemas = reg._fetch_tool_schemas_from_manifest("remote-test")
    sch_names = {s["name"] for s in schemas}
    check("sync schema read returns namespaced tools", reg._namespace_tool("remote-test", "echo") in sch_names)
    # collision guard: re-installing the same id must not clobber
    dup = run(reg.install_remote_connector("remote-test", "dup", _URL))
    check("duplicate connector id rejected", dup.get("status") == "error" and "already exists" in dup.get("error", ""))


def t_refresh():
    res = run(reg.refresh_http_tools("remote-test"))
    check("refresh_http_tools succeeds", res.get("status") == "success" and res.get("tool_count") == 2)
    res2 = run(reg.refresh_http_tools("does-not-exist"))
    check("refresh on missing connector -> error", res2.get("status") == "error")


def t_invoke_via_dispatch():
    # Full public path: invoke_connector_tool -> get_server -> McpHttpStrategy -> remote
    namespaced = reg._namespace_tool("remote-test", "echo")
    r = run(reg.invoke_connector_tool("remote-test", namespaced, {"message": "dispatch"}))
    check("invoke_connector_tool (mcp_http) end-to-end", r.get("echoed") == "dispatch" and r.get("status") == "success")


def main():
    print("== WS2 MCP HTTP connector tests ==")
    _setup_db()
    _start_server()
    try:
        for t in (t_envelope_helper, t_endpoint_resolution, t_http_invoke,
                  t_strategy_via_install_spec, t_error_paths,
                  t_discover, t_install_persist_read, t_refresh, t_invoke_via_dispatch):
            print(f"\n[{t.__name__}]")
            t()
    finally:
        _stop_server()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
