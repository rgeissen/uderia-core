#!/usr/bin/env python3
"""A built-in connector's identity is owned by its manifest, not by its row.

WHY THIS GATE EXISTS
`install_server` wrote whatever the installing client sent. A built-in installed from a
bare payload therefore kept display_name = its id, version 0.0.0 and the DEFAULT
connector type forever. Found live: the Database Connection connector (manifest:
connector_type "database", requires_user_auth true) sat in the registry as an
`mcp_stdio` Platform connector, so its card rendered with no connection manager — and
the Canvas's "Manage connections…" pointer landed on a card that could not manage any.

Now, for every built-in connector (not only this one):
  * an install writes the manifest's identity, whatever the payload says;
  * every read overlays the manifest's identity (no stale window);
  * startup reconciles rows written before the rule, and says which moved;
  * the admin's decisions — enabled, credentials, the permitted-tools narrowing — are
    never touched, and a remote/registry connector owns its own row.

Run: python3 test/test_builtin_connector_identity.py     (no server, no network)
"""
import json
import logging
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
logging.disable(logging.CRITICAL)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


from trusted_data_agent.core import platform_connector_registry as reg  # noqa: E402

DDL = """
CREATE TABLE connector_registry_sources (id TEXT PRIMARY KEY);
INSERT INTO connector_registry_sources VALUES ('builtin'), ('remote');
CREATE TABLE platform_connectors (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, name TEXT NOT NULL, display_name TEXT,
    description TEXT, version TEXT NOT NULL DEFAULT '0.0.0', registry_metadata TEXT,
    install_spec TEXT, install_status TEXT DEFAULT 'not_installed',
    connector_type TEXT NOT NULL DEFAULT 'mcp_stdio', enabled INTEGER DEFAULT 0, config TEXT,
    credentials TEXT, available_tools TEXT, auto_opt_in INTEGER DEFAULT 0,
    user_can_opt_out INTEGER DEFAULT 1, user_can_configure_tools INTEGER DEFAULT 0,
    requires_user_auth INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
"""


def fresh_db():
    path = os.path.join(tempfile.mkdtemp(prefix="connid_"), "auth.db")
    c = sqlite3.connect(path)
    c.executescript(DDL)
    c.commit()
    c.close()
    reg._DB_PATH = path
    return path


def row(path, cid):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM platform_connectors WHERE id = ?", (cid,)).fetchone()
    c.close()
    return dict(r) if r else None


MAN = json.load(open(os.path.join(ROOT, "mcp_servers/builtin/uderia-database/manifest.json")))

# ── [1] the seam
ident = reg.builtin_identity("uderia-database")
check("[1] a built-in's identity comes from its manifest",
      ident and ident["connector_type"] == MAN["connector_type"] and ident["requires_user_auth"] is True
      and ident["display_name"] == MAN["display_name"] and ident["version"] == MAN["version"], str(ident))
check("[1] a non-built-in id has no manifest identity", reg.builtin_identity("some-remote-server") is None)
check("[1] every built-in manifest resolves",
      all(reg.builtin_identity(d) for d in os.listdir(os.path.join(ROOT, "mcp_servers/builtin"))
          if os.path.isfile(os.path.join(ROOT, "mcp_servers/builtin", d, "manifest.json"))))

# ── [2] an install from a bare payload writes the manifest's identity
path = fresh_db()
reg.install_server("builtin", "uderia-database", {})
r = row(path, "uderia-database")
check("[2] bare install: connector_type from the manifest", r["connector_type"] == "database", r["connector_type"])
check("[2] bare install: a User connector", r["requires_user_auth"] == 1)
check("[2] bare install: its real name and version", r["display_name"] == MAN["display_name"] and r["version"] == MAN["version"],
      f"{r['display_name']} {r['version']}")
reg.install_server("builtin", "uderia-database", {"connector_type": "mcp_stdio", "requires_user_auth": False,
                                                   "display_name": "spoofed"})
r = row(path, "uderia-database")
check("[2] a payload cannot re-declare a built-in's identity",
      r["connector_type"] == "database" and r["requires_user_auth"] == 1 and r["display_name"] == MAN["display_name"], str(r))

# ── [3] a stale row reads correctly and is healed at startup, admin state untouched
path = fresh_db()
c = sqlite3.connect(path)
c.execute("""INSERT INTO platform_connectors (id, source_id, name, display_name, version, connector_type,
             requires_user_auth, enabled, available_tools, credentials, install_status)
             VALUES ('uderia-database','builtin','uderia-database','uderia-database','0.0.0','mcp_stdio',0,1,'["x"]','ENC','installed')""")
c.execute("""INSERT INTO platform_connectors (id, source_id, name, display_name, version, connector_type,
             requires_user_auth, enabled) VALUES ('my-remote','remote','my-remote','Mine','2.0.0','mcp_http',0,1)""")
c.commit()
c.close()
s = reg.get_server("uderia-database")
check("[3] a stale row READS with its manifest identity (no stale window)",
      s["connector_type"] == "database" and s["requires_user_auth"] is True and s["display_name"] == MAN["display_name"], str(
          {k: s.get(k) for k in ("connector_type", "requires_user_auth", "display_name")}))
check("[3] …and its auth schema drives the connection manager", (s.get("auth_schema") or {}).get("type") == "db_connections")
moved = reg.reconcile_builtin_connectors()
check("[3] startup reconciles the stale built-in and names it", moved == ["uderia-database"], str(moved))
r = row(path, "uderia-database")
check("[3] …the row now carries the manifest identity",
      r["connector_type"] == "database" and r["requires_user_auth"] == 1 and r["version"] == MAN["version"], str(r))
check("[3] …the admin's decisions are untouched",
      r["enabled"] == 1 and r["available_tools"] == '["x"]' and r["credentials"] == "ENC", str(r))
check("[3] a remote connector owns its own row", row(path, "my-remote")["connector_type"] == "mcp_http"
      and reg.get_server("my-remote")["display_name"] == "Mine")
check("[3] reconciling again changes nothing", reg.reconcile_builtin_connectors() == [])
# a registry/remote connector that happens to share a built-in's id is still its own
c = sqlite3.connect(path)
c.execute("""INSERT INTO platform_connectors (id, source_id, name, display_name, version, connector_type,
             requires_user_auth, enabled) VALUES ('uderia-web','remote','uderia-web','Somebody else''s web','9.9.9','mcp_http',0,1)""")
c.commit()
c.close()
s = reg.get_server("uderia-web")
check("[3] a remote row sharing a built-in's id keeps its own identity on read",
      s["connector_type"] == "mcp_http" and s["display_name"] == "Somebody else's web", str({k: s.get(k) for k in ("connector_type", "display_name")}))
reg.reconcile_builtin_connectors()
check("[3] …and reconciliation never rewrites it", row(path, "uderia-web")["version"] == "9.9.9")

# ── [4] fail soft
reg._DB_PATH = "/nonexistent/dir/auth.db"
check("[4] an unreadable registry never stops startup", reg.reconcile_builtin_connectors() == [])

# ── [5] startup calls it
main_src = open(os.path.join(ROOT, "src/trusted_data_agent/main.py")).read()
check("[5] startup reconciles built-in connectors", "reconcile_builtin_connectors()" in main_src)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — a built-in connector is what its manifest says it is"))
sys.exit(1 if FAIL else 0)
