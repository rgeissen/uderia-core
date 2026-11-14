"""
Regression tests for universal MCP argument alignment in
``mcp_adapter.adapter.invoke_mcp_tool``.

Background
----------
``_normalize_tool_arguments`` canonicalises argument synonyms (e.g. ``table_name``
-> ``object_name``). Those canonical names are an *internal* convention and do NOT
match the literal parameter names most tools expect, so they must be realigned back
to the tool's own names using its schema.

The bug this guards against: a tool invoked by an MCP *prompt* can be executed even
when it is outside the active profile's filtered tool set, so it is absent from
``STATE['mcp_tools']``. With no schema, alignment was skipped and the canonicalised
name (``object_name``) reached the server, which rejected it as a missing required
``table_name`` property. Observed live on prompt ``base_databaseBusinessDesc`` ->
loop over ``base_tableDDL`` (every iteration + self-correction failed identically).

The fix (universal, provider-agnostic) lives in ``invoke_mcp_tool``:
  1. resolve the tool schema from the full, unfiltered schema cache when it is
     missing from the live (filtered) tool set, then realign as normal;
  2. if no schema exists anywhere, send the caller-provided argument names verbatim
     instead of the canonical forms (which match no real tool parameter).

These tests drive the real ``invoke_mcp_tool`` and capture exactly what reaches the
MCP wire (``call_tool``).
"""

import asyncio
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.mcp_adapter import adapter


# --- Minimal fakes for the MCP wire ----------------------------------------

class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResult:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeSession:
    def __init__(self, capture):
        self._capture = capture

    async def call_tool(self, name, args):
        self._capture["name"] = name
        self._capture["args"] = dict(args)
        return _FakeResult('{"status": "success", "results": []}')


class _FakeSessionCtx:
    def __init__(self, capture):
        self._capture = capture

    async def __aenter__(self):
        return _FakeSession(self._capture)

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, capture):
        self._capture = capture

    def session(self, server_id):
        return _FakeSessionCtx(self._capture)


def _tool(name, *arg_names):
    """A schema object shaped like the adapter's internal SimpleTool."""
    return types.SimpleNamespace(name=name, args={a: {"name": a} for a in arg_names})


def _invoke(state, command):
    capture = {}
    state["mcp_client"] = _FakeClient(capture)
    with patch.object(adapter, "get_user_mcp_server_id", return_value="srv1"):
        asyncio.run(
            adapter.invoke_mcp_tool(
                state, command, user_uuid="u1", session_id="s1", workflow_state={}
            )
        )
    return capture


_DDL_CMD = {
    "tool_name": "base_tableDDL",
    "arguments": {"database_name": "fitness_db", "table_name": "Products"},
}


def test_schema_in_live_set_realigns_to_table_name():
    """Baseline (must not regress): schema present in the live tool set."""
    state = {
        "mcp_tools": {"base_tableDDL": _tool("base_tableDDL", "database_name", "table_name")},
        "mcp_tool_schema_cache": {},
    }
    cap = _invoke(state, dict(_DDL_CMD, arguments=dict(_DDL_CMD["arguments"])))
    assert cap["args"] == {"database_name": "fitness_db", "table_name": "Products"}, cap["args"]


def test_schema_cache_fallback_when_missing_from_live_set():
    """THE BUG: tool absent from filtered live set -> recover schema from the cache."""
    state = {
        "mcp_tools": {},  # base_tableDDL filtered out (prompt-invoked, not profile-enabled)
        "mcp_tool_schema_cache": {
            "srv1": {"tools": [_tool("base_tableDDL", "database_name", "table_name")]}
        },
    }
    cap = _invoke(state, dict(_DDL_CMD, arguments=dict(_DDL_CMD["arguments"])))
    assert cap["args"] == {"database_name": "fitness_db", "table_name": "Products"}, cap["args"]


def test_no_schema_anywhere_sends_caller_names_verbatim():
    """Last-resort net: no schema at all -> caller-provided names, NOT canonical forms."""
    state = {"mcp_tools": {}, "mcp_tool_schema_cache": {}}
    cap = _invoke(state, dict(_DDL_CMD, arguments=dict(_DDL_CMD["arguments"])))
    # Must be the caller's 'table_name', never the canonical 'object_name'.
    assert cap["args"] == {"database_name": "fitness_db", "table_name": "Products"}, cap["args"]
    assert "object_name" not in cap["args"]


def test_cache_fallback_realigns_to_a_different_real_param():
    """
    Proves the cache fallback does *real* realignment, not mere passthrough: a tool
    whose real param is ``obj_name`` (e.g. base_columnDescription). Caller supplies a
    synonym (``table_name``) -> canonical ``object_name`` -> realigned to ``obj_name``.
    """
    state = {
        "mcp_tools": {},
        "mcp_tool_schema_cache": {
            "srv1": {"tools": [_tool("base_columnDescription", "database_name", "obj_name")]}
        },
    }
    cmd = {
        "tool_name": "base_columnDescription",
        "arguments": {"database_name": "fitness_db", "table_name": "Products"},
    }
    cap = _invoke(state, cmd)
    assert cap["args"] == {"database_name": "fitness_db", "obj_name": "Products"}, cap["args"]
    assert "object_name" not in cap["args"]
    assert "table_name" not in cap["args"]


def test_capitalized_synonym_still_realigns_with_live_schema():
    """Regression guard for the normal synonym path: 'TableName' -> 'table_name'."""
    state = {
        "mcp_tools": {"base_tableDDL": _tool("base_tableDDL", "database_name", "table_name")},
        "mcp_tool_schema_cache": {},
    }
    cmd = {
        "tool_name": "base_tableDDL",
        "arguments": {"database_name": "fitness_db", "TableName": "Products"},
    }
    cap = _invoke(state, cmd)
    assert cap["args"] == {"database_name": "fitness_db", "table_name": "Products"}, cap["args"]


def test_empty_args_schema_treated_as_no_schema():
    """A cached tool with an empty args dict must not strand canonical names."""
    state = {
        "mcp_tools": {"base_tableDDL": _tool("base_tableDDL")},  # args == {}
        "mcp_tool_schema_cache": {},
    }
    cap = _invoke(state, dict(_DDL_CMD, arguments=dict(_DDL_CMD["arguments"])))
    assert cap["args"] == {"database_name": "fitness_db", "table_name": "Products"}, cap["args"]
    assert "object_name" not in cap["args"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
