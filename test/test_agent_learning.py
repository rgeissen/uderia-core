"""OC-F5 — an Optimize agent learns into its OWN planner repository (operator decision, option B).

FOUND (Optimize campaign, 2026-10-27…29): every successful Optimize turn was stored in the user's
ONE default planner repository per MCP server, and every agent of that user on that server read
it back — one shared notebook. An agent's plans changed because of other agents' turns, a signed
agent's bound repository changed under it after every success, and the campaign's "cold" lever
warmed up within a run (its bound repository was locked, so the read-back pulled the shared
default in).

The rule (`core/agent_learning.py`): an agent HAS its own repository when a planner collection
carries its id in `learning_profile_id`; new Optimize agents get one at creation; such an agent
learns ONLY there (locked ⇒ frozen, nothing stored) and reads it beside what it binds, never the
shared notebook; the repository is never a "default"; it is not a binding, so learning never
moves the agent's signature; removing the agent removes it. Existing agents keep the shared
notebook until opted in. CI-pure (an in-memory collection store with the real interface).
"""
import asyncio
import inspect
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


from trusted_data_agent.core import agent_learning as AL  # noqa: E402


class FakeDB:
    """The CollectionDatabase surface agent_learning uses, in memory."""
    def __init__(self, rows=()):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.next = max(self.rows or [0]) + 1
        self.lock = threading.Lock()

    def get_all_collections(self, user_id=None):
        return [dict(r) for r in self.rows.values()]

    def get_collection_by_id(self, cid):
        r = self.rows.get(int(cid))
        return dict(r) if r else None

    def create_collection(self, data):
        with self.lock:
            cid = self.next
            self.next += 1
        row = dict(data, id=cid, trust_locked=0)
        row.pop("marketplace_metadata", None)
        self.rows[cid] = row
        return cid

    def update_collection(self, cid, updates):
        self.rows[int(cid)].update(updates)
        return True

    def delete_collection(self, cid):
        return self.rows.pop(int(cid), None) is not None


U, OTHER = "u1", "u2"
S1, S2 = "srv-1", "srv-2"
BASE = [
    {"id": 1, "name": "Default Collection - Teradata", "repository_type": "planner", "owner_user_id": U, "mcp_server_id": S1},
    {"id": 2, "name": "Some knowledge", "repository_type": "knowledge", "owner_user_id": U, "mcp_server_id": S1},
]


def install(db):
    AL._db = lambda: db
    AL._refresh_runtime = lambda cid=None: None


print("\n── §1 what an agent repository is")
check("a row with learning_profile_id is one", AL.is_agent_repository({"learning_profile_id": "p1"}))
check("an ordinary repository is not", not AL.is_agent_repository({"id": 1}) and not AL.is_agent_repository(None))
db = FakeDB(BASE)
install(db)
check("an existing agent with no own repository learns into the shared notebook",
      AL.learning_target("p-old", S1, U) == {"mode": "shared"} and not AL.learns_own("p-old", U))

print("\n── §2 a new Optimize agent gets one at creation")
cid = AL.provision_for_new_agent({"id": "p-new", "tag": "NEW", "profile_type": "tool_enabled", "mcpServerId": S1}, U)
row = db.get_collection_by_id(cid) if cid else {}
check("created, owned by the user, on the agent's server, a planner",
      cid and row.get("owner_user_id") == U and row.get("mcp_server_id") == S1 and row.get("repository_type") == "planner")
check("…named after the agent, and linked to it", row.get("name") == "@NEW — learned" and row.get(AL.COLUMN) == "p-new")
check("…idempotent: provisioning again creates nothing",
      AL.provision_for_new_agent({"id": "p-new", "profile_type": "tool_enabled", "mcpServerId": S1}, U) == cid
      and len([r for r in db.rows.values() if r.get(AL.COLUMN) == "p-new"]) == 1)
check("an Ideate / Focus / Coordinate agent gets none (only the Optimize lane learns plans)",
      all(AL.provision_for_new_agent({"id": f"x-{t}", "profile_type": t, "mcpServerId": S1}, U) is None
          for t in ("llm_only", "rag_focused", "genie")))
check("no server or no user → nothing created",
      AL.provision_for_new_agent({"id": "p-x", "profile_type": "tool_enabled"}, U) is None
      and AL.provision_for_new_agent({"id": "p-x", "profile_type": "tool_enabled", "mcpServerId": S1}, None) is None)

print("\n── §3 concurrent first lessons create ONE repository")
import time  # noqa: E402


class SlowDB(FakeDB):
    """Creation takes time, and every caller passes the first (unlocked) check together — the
    window a racing RAG worker and an editor save really have."""
    def create_collection(self, data):
        time.sleep(0.05)
        return super().create_collection(data)


db2 = SlowDB(BASE)
install(db2)
results = []
gate = threading.Barrier(8)
_rf = AL.repository_for
_first_pass = threading.local()


def _racing_repository_for(*a, **k):
    out = _rf(*a, **k)
    if not getattr(_first_pass, "done", False):   # the unlocked check: everyone waits here together
        _first_pass.done = True
        gate.wait()
    return out


AL.repository_for = _racing_repository_for
ts = [threading.Thread(target=lambda: results.append(AL.ensure_repository("p-race", S1, U))) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
AL.repository_for = _rf
check("eight racing callers → one repository, all given its id",
      len({r for r in results}) == 1 and len([r for r in db2.rows.values() if r.get(AL.COLUMN) == "p-race"]) == 1,
      results)

print("\n── §4 where a turn is stored")
install(db)
check("an agent with its own repository stores there", AL.learning_target("p-new", S1, U) == {"mode": "own", "collection_id": cid})
db.rows[cid]["trust_locked"] = 1
check("…a LOCKED own repository is frozen: nothing stored, and never the shared one instead",
      AL.learning_target("p-new", S1, U) == {"mode": "frozen", "collection_id": cid})
db.rows[cid]["trust_locked"] = 0
t2 = AL.learning_target("p-new", S2, U)
check("on a server it has not learned on, its first lesson there creates a repository for that server",
      t2["mode"] == "own" and t2["collection_id"] != cid and db.get_collection_by_id(t2["collection_id"])["mcp_server_id"] == S2)
check("another user's agent with the same id is not this one", AL.learning_target("p-new", S1, OTHER) == {"mode": "shared"})

print("\n── §5 what it reads")
check("its own repository for the turn's server", AL.read_repository("p-new", S1, U) == cid)
check("reading never creates (no repository on an unknown server → None)",
      AL.read_repository("p-new", "srv-9", U) is None and not any(r.get("mcp_server_id") == "srv-9" for r in db.rows.values()))

print("\n── §6 never a default")
from trusted_data_agent.core import config_manager as CM  # noqa: E402
from trusted_data_agent.core import collection_db as CDB  # noqa: E402
db3 = FakeDB([{"id": 5, "name": "@A — learned", "repository_type": "planner", "owner_user_id": U,
               "mcp_server_id": S1, AL.COLUMN: "pA"},
              {"id": 9, "name": "Default Collection - Teradata", "repository_type": "planner",
               "owner_user_id": U, "mcp_server_id": S1}])
_orig_get = CDB.get_collection_db
CDB.get_collection_db = lambda: db3
try:
    cm = CM.ConfigManager.__new__(CM.ConfigManager)
    check("the oldest planner on the server is an agent's — the shared default is still the other one",
          cm.get_default_collection_for_mcp_server(S1, U) == 9)
    db3.rows.pop(9)
    check("…and with the shared default deleted, an agent's repository is NOT promoted to everyone's notebook",
          cm.get_default_collection_for_mcp_server(S1, U) is None)
finally:
    CDB.get_collection_db = _orig_get
from trusted_data_agent.agent import rag_retriever as RR  # noqa: E402
from trusted_data_agent.core.config import APP_STATE  # noqa: E402
_saved = APP_STATE.get("rag_collections")
APP_STATE["rag_collections"] = [{"id": 5, "owner_user_id": U, AL.COLUMN: "pA"}, {"id": 9, "owner_user_id": U}]
try:
    r = RR.RAGRetriever.__new__(RR.RAGRetriever)
    check("the retriever's own default lookup skips it too", r._get_user_default_collection_id(U) == 9)
finally:
    APP_STATE["rag_collections"] = _saved

print("\n── §7 the learning worker honours the target")


class Ctx:
    def __init__(self):
        self.user_id = U
        self.seen = []

    def validate_collection_access(self, cid, write=False):
        self.seen.append((cid, write))
        return True


def run_store(target):
    _lt = AL.learning_target
    AL.learning_target = lambda *a, **k: target
    calls = []
    try:
        ret = RR.RAGRetriever.__new__(RR.RAGRetriever)
        ret._extract_case_from_turn_summary = lambda ts, c: calls.append(c) or None   # stop after the target
        ctx = Ctx()
        asyncio.run(ret.process_turn_for_rag({"user_uuid": U, "mcp_server_id": S1, "profile_id": "p-new"},
                                             collection_id=777, rag_context=ctx))
        return calls, ctx.seen
    finally:
        AL.learning_target = _lt


calls, seen = run_store({"mode": "frozen", "collection_id": cid})
check("frozen → nothing extracted, nothing stored", calls == [] and seen == [])
calls, seen = run_store({"mode": "own", "collection_id": cid})
check("own → stored in the agent's repository, NOT the repository that guided the turn (777)",
      calls == [cid] and (cid, True) in seen, (calls, seen))
calls, seen = run_store({"mode": "shared"})
check("shared → the legacy path, unchanged (the guiding repository)", calls == [777])

print("\n── §8 removing the agent removes what it learned")
install(db)
gone = AL.retire_for_removed_agent("p-new", U)
check("every repository of the agent, on every server, is removed",
      sorted(gone) == sorted([cid, t2["collection_id"]]) and not AL.repositories_of("p-new", U))
check("…and nothing else", 1 in db.rows and 2 in db.rows)

print("\n── §9 wiring")
cms = inspect.getsource(CM.ConfigManager)
check("every creation path provisions (add_profile and add_profile_locked)",
      inspect.getsource(CM.ConfigManager.add_profile).count("_provision_learning(") == 1
      and inspect.getsource(CM.ConfigManager.add_profile_locked).count("_provision_learning(") == 1)
check("the one removal chokepoint retires the repositories",
      "retire_for_removed_agent(profile_id, user_uuid)" in inspect.getsource(CM.ConfigManager.remove_profile))
from trusted_data_agent.agent.engines import optimize_engine as OE  # noqa: E402
check("the queued turn carries the agent's id", '"profile_id": getattr(executor, "active_profile_id", None)' in inspect.getsource(OE))
from trusted_data_agent.agent import planner as PL  # noqa: E402
ps = inspect.getsource(PL.Planner._generate_meta_plan)
check("the planner reads the agent's own repository beside its bindings",
      "_al.read_repository(" in ps and "allowed_collection_ids = set(allowed_collection_ids) | {_own_repo}" in ps
      and "_no_planner_bound, allowed_collection_ids = False, {_own_repo}" in ps)
check("…and an agent in own mode never takes the shared read-back",
      "if _own_repo is None:   # own mode: the shared read-back does not apply" in ps)

print("\n── §11 the paths that bypass the chokepoints (found by the marketplace probe)")
from trusted_data_agent.api import agent_pack_routes as APR  # noqa: E402
cleanup_src = inspect.getsource(APR._cleanup_subscriber_profiles)
check("unpublish (the shared subscriber cleanup) retires the removed agents' repositories",
      "retire_learning_of_removed(removed_ids, user_uuid)" in cleanup_src)
check("unsubscribe (its own bulk write) does too",
      'retire_learning_of_removed(cleanup["removed_ids"], user_uuid)' in inspect.getsource(APR))
from trusted_data_agent.api import auth_routes as AR  # noqa: E402
check("login never binds the shared default to an agent that learns into its own repository",
      'if profile_type == "tool_enabled" and not _learns_own:' in inspect.getsource(AR._update_profiles_with_default_collections))
check("the planner-binding heal drops, never re-points to the shared default, for such an agent",
      'repl = None if (field == "ragCollections" and _learns_own(prof.get("id")))'
      in inspect.getsource(CM.ConfigManager.heal_planner_bindings))
_rl = []
_rf2 = AL.retire_for_removed_agent
AL.retire_for_removed_agent = lambda pid, u: _rl.append((pid, u))
try:
    APR.retire_learning_of_removed({"a", "b"}, U)
finally:
    AL.retire_for_removed_agent = _rf2
check("…the helper retires every removed agent", sorted(_rl) == [("a", U), ("b", U)])

print("\n── §10 learning does not move the signature")
from trusted_data_agent.core.trust import composition as COMP  # noqa: E402
prof = {"id": "p-new", "profile_type": "tool_enabled", "ragCollections": [16]}
check("the own repository is not a bound ingredient (the composition reads only the binding)",
      COMP.bound_repository_ids(prof) == [16])
check("…and its cases are learned, never template seeds — so never replayed verbatim",
      PL.exact_signed_champion("q", [{"full_case_data": {"intent": {"user_query": "q"},
                                                         "metadata": {"collection_id": cid},
                                                         "successful_strategy": {"phases": [{"phase": 1}]}}}],
                               lambda c: True) is None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
