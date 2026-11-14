#!/usr/bin/env python3
"""TC-37 — a profile bound to a planner its owner cannot read is re-pointed to the owner's
own default planner for that server (or the binding is dropped when the profile has no
server), at every login, idempotently. Also pins the ONE read-access rule the heal and trust
composition share (``CollectionDatabase.can_user_read``) to the retrieval gate's semantics.

Why: the bootstrap template carries ``ragCollections: [1]`` — the admin's planner on the
install that produced it — so every bootstrapped account's Optimize/Coordinate agents were
bound to a collection they could not read: inert at runtime, yet a sign-gate block only the
admin could clear. Run: ``python test/test_planner_binding_heal.py`` (no server).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


DEMO, ADMIN, OTHER = "demo-uuid", "admin-uuid", "other-uuid"
SERVER = "srv-td"

ROWS = {
    1:   {"id": 1,   "repository_type": "planner",   "name": "Default Collection - Teradata MCP",
          "owner_user_id": ADMIN, "visibility": "private", "mcp_server_id": SERVER},
    394: {"id": 394, "repository_type": "planner",   "name": "Default Collection - Teradata MCP",
          "owner_user_id": DEMO, "visibility": "private", "mcp_server_id": SERVER},
    13:  {"id": 13,  "repository_type": "knowledge", "name": "Fitness docs",
          "owner_user_id": ADMIN, "visibility": "private", "mcp_server_id": SERVER},
    77:  {"id": 77,  "repository_type": "planner",   "name": "Public planner",
          "owner_user_id": OTHER, "visibility": "public", "mcp_server_id": SERVER},
    88:  {"id": 88,  "repository_type": "planner",   "name": "Platform planner",
          "owner_user_id": None, "visibility": "private", "mcp_server_id": SERVER},
}


def test_read_rule():
    print("\n[1] can_user_read — the retrieval gate's rule, one place")
    from trusted_data_agent.core.collection_db import CollectionDatabase
    db = object.__new__(CollectionDatabase)          # the rule needs no connection for dict rows
    check("owner reads their own", db.can_user_read(ROWS[394], DEMO) is True)
    check("a private collection of another user is NOT readable", db.can_user_read(ROWS[1], DEMO) is False)
    check("a public collection is readable by anyone", db.can_user_read(ROWS[77], DEMO) is True)
    check("a platform-owned (no owner) collection is readable by anyone", db.can_user_read(ROWS[88], DEMO) is True)
    check("unknown collection fails closed", db.can_user_read(None, DEMO) is False)
    check("no user fails closed on a private collection", db.can_user_read(ROWS[1], None) is False)


class _FakeDB:
    def __init__(self):
        self.rows = {k: dict(v) for k, v in ROWS.items()}
    def get_collection_by_id(self, cid):
        return self.rows.get(int(cid))
    def get_all_collections(self, user_id=None):
        return list(self.rows.values())
    def can_user_read(self, coll, uid):
        from trusted_data_agent.core.collection_db import CollectionDatabase
        return CollectionDatabase.can_user_read(object.__new__(CollectionDatabase), coll, uid)


def _manager(profiles):
    from trusted_data_agent.core.config_manager import ConfigManager
    tmp = tempfile.mkdtemp()
    cfg = Path(tmp) / "tda_config.json"
    cfg.write_text(json.dumps({"schema_version": "1.0", "profiles": [], "llm_configurations": [],
                               "mcp_servers": [], "context_window_types": []}))
    cm = ConfigManager(config_path=cfg)
    store = {"profiles": [dict(p) for p in profiles], "saves": 0}
    cm.get_profiles = lambda user_uuid=None: store["profiles"]
    def _save(profs, user_uuid=None, **kw):
        store["profiles"] = profs; store["saves"] += 1; return True
    cm.save_profiles = _save
    return cm, store


def test_heal():
    print("\n[2] heal_planner_bindings — re-point, drop, keep, idempotent")
    from trusted_data_agent.core import collection_db as _cdb
    orig = _cdb.get_collection_db
    try:
        _cdb.get_collection_db = lambda: _FakeDB()
        cm, store = _manager([
            {"id": "optim", "tag": "OPTIM", "profile_type": "tool_enabled", "mcpServerId": SERVER,
             "ragCollections": [1], "autocompleteCollections": [1]},
            {"id": "coord", "tag": "COORD", "profile_type": "genie", "mcpServerId": None,
             "ragCollections": [1], "autocompleteCollections": []},
            {"id": "focus", "tag": "FOCUS", "profile_type": "rag_focused", "mcpServerId": SERVER,
             "ragCollections": [13], "knowledgeConfig": {"collections": [{"id": 13}]}},
            {"id": "mixed", "tag": "MIXED", "profile_type": "tool_enabled", "mcpServerId": SERVER,
             "ragCollections": [1, 394, 77, 88, 999]},
            {"id": "ok", "tag": "OK", "profile_type": "tool_enabled", "mcpServerId": SERVER,
             "ragCollections": [394]},
        ])
        n = cm.heal_planner_bindings(DEMO)
        by = {p["id"]: p for p in store["profiles"]}
        check("three profiles rewritten (OPTIM, COORD, MIXED)", n == 3)
        check("OPTIM: the admin's planner → the demo's own default for that server",
              by["optim"]["ragCollections"] == [394])
        check("OPTIM: autocomplete binding re-pointed too", by["optim"]["autocompleteCollections"] == [394])
        check("COORD (no server): the unreadable planner is dropped, nothing invented",
              by["coord"]["ragCollections"] == [])
        check("FOCUS: a knowledge binding is never rewritten, readable or not",
              by["focus"]["ragCollections"] == [13] and by["focus"]["knowledgeConfig"] == {"collections": [{"id": 13}]})
        check("MIXED: unreadable replaced + deduped against the own default already bound; "
              "public, platform-owned and dangling ids kept",
              by["mixed"]["ragCollections"] == [394, 77, 88, 999])
        check("OK: an already-correct profile is untouched", by["ok"]["ragCollections"] == [394])
        check("one save for the whole pass", store["saves"] == 1)
        n2 = cm.heal_planner_bindings(DEMO)
        check("idempotent: a second pass rewrites nothing", n2 == 0 and store["saves"] == 1)
        check("no user → no-op", cm.heal_planner_bindings("") == 0)
    finally:
        _cdb.get_collection_db = orig


def test_login_seam_wired():
    print("\n[3] the heal runs from the login default-collection seam")
    import ast, inspect
    from trusted_data_agent.api import auth_routes as ar
    src = inspect.getsource(ar._ensure_user_default_collection)
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "heal_planner_bindings"]
    check("_ensure_user_default_collection calls heal_planner_bindings", len(calls) == 1)
    # It must run on the path where NOTHING was created (an existing account heals on login):
    # i.e. the call is not nested inside the `if collections_created:` branch.
    fn = tree.body[0]
    top_level_calls = [n for stmt in fn.body for n in ast.walk(stmt)
                       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr == "heal_planner_bindings"]
    def _inside_if_created(node):
        for n in ast.walk(tree):
            if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "collections_created":
                if any(c is node for c in ast.walk(n)):
                    return True
        return False
    check("the heal is not gated on a collection having been created this login",
          top_level_calls and not _inside_if_created(top_level_calls[0]))


def test_composition_passes_owner():
    print("\n[4] trust composition asks with the agent's owner")
    import inspect
    from trusted_data_agent.core.trust import composition as comp
    src = inspect.getsource(comp.agent_ingredient_refs)
    check("agent_ingredient_refs → _repo_ingredients(profile, user_uuid)",
          "_repo_ingredients(profile, user_uuid)" in src)


def main():
    print("=" * 70)
    print("TC-37 — planner binding heal + the one read-access rule")
    print("=" * 70)
    test_read_rule()
    test_heal()
    test_login_seam_wired()
    test_composition_passes_owner()
    print("\n" + "=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
