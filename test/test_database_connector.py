"""
Module tests — Database Connection connector integration.

CI-pure: validates the builtin manifest (so it appears in Browse Registry with the
right type + credential auth_schema), the connector module contract + status via a
fake store, registry wiring, and that the governance gate is a safe boolean.

Run:
    python test/test_database_connector.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (mcp_servers/builtin)

from trusted_data_agent.core import platform_connector_registry as reg  # noqa: E402
from trusted_data_agent.connectors import registry as conn_registry  # noqa: E402
from trusted_data_agent.connectors import database_connector  # noqa: E402
import trusted_data_agent.core.database_connections as dbc  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


def test_manifest_in_registry():
    print("\n[test_manifest_in_registry]")
    servers = reg._load_builtin_manifests()
    db = next((s for s in servers if s["id"] == "uderia-database"), None)
    check("uderia-database appears in builtin registry (Browse Registry)", db is not None)
    check("connector_type = database", db and db["connector_type"] == reg.CONNECTOR_TYPE_DATABASE)
    check("requires_user_auth (User connector)", db and db["requires_user_auth"] is True)
    check("no LLM tools (credential provider)", db and db["tools"] == [])


def test_auth_schema_hoisted():
    print("\n[test_auth_schema_hoisted]")
    raw = reg._get_builtin_manifest("uderia-database")
    check("manifest has auth_schema", isinstance(raw.get("auth_schema"), dict))
    check("auth_schema.type = db_connections", raw.get("auth_schema", {}).get("type") == "db_connections")
    check("auth_schema lists drivers", "teradata" in (raw.get("auth_schema", {}).get("drivers") or []))
    # _enrich_with_manifest must hoist auth_schema onto a server dict (drives the UI)
    enriched = reg._enrich_with_manifest({"id": "uderia-database"})
    check("auth_schema hoisted onto server dict", enriched.get("auth_schema", {}).get("type") == "db_connections")


def test_connector_module_contract():
    print("\n[test_connector_module_contract]")
    check("SERVER_ID", database_connector.SERVER_ID == "uderia-database")
    check("is_configured True (no admin app-creds)", database_connector.is_configured() is True)
    check("oauth stubs return None", database_connector.initiate_oauth("u", "r") is None
          and database_connector.handle_callback("c", "s", "r") is None)
    check("inject_env_tokens passthrough", database_connector.inject_env_tokens({"A": 1}, "u") == {"A": 1})


def test_status_via_fake_store():
    print("\n[test_status_via_fake_store]")

    class FakeStore:
        def list(self, user, include_legacy=True):
            return [{"id": "dbconn_1", "name": "TD", "driver": "teradata"},
                    {"id": "canvas_conn_x", "name": "Legacy", "driver": "teradata"}]
        def delete(self, user, cid):
            self.deleted = getattr(self, "deleted", [])
            self.deleted.append(cid)

    orig = dbc.get_database_connection_store
    dbc.get_database_connection_store = lambda: FakeStore()
    try:
        st = database_connector.get_connection_status("user-1")
        check("status connected", st["connected"] is True)
        check("status count", st["count"] == 2)
        check("status lists connections", len(st["connections"]) == 2)
    finally:
        dbc.get_database_connection_store = orig


def test_registry_wiring():
    print("\n[test_registry_wiring]")
    check("registry.get('database') resolves module", conn_registry.get("database") is database_connector)
    check("server_id_to_platform maps", conn_registry.server_id_to_platform("uderia-database") == "database")
    check("platform listed", "database" in conn_registry.all_platforms())


def test_gate_is_boolean():
    print("\n[test_gate_is_boolean]")
    # Exercises the gate code path; returns a safe bool regardless of DB state.
    val = reg.is_database_connector_enabled()
    check("gate returns bool without raising", isinstance(val, bool))


def main():
    for fn in [
        test_manifest_in_registry,
        test_auth_schema_hoisted,
        test_connector_module_contract,
        test_status_via_fake_store,
        test_registry_wiring,
        test_gate_is_boolean,
    ]:
        fn()
    print(f"\n{'='*50}\nDatabase connector: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
