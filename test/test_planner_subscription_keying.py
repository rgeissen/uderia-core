"""Gate: a subscribed PLANNER repository loads for the subscriber, and learning always
has somewhere to land.

A planner repository is loaded only for sessions on the MCP server it is keyed to. A
subscribed one is the PUBLISHER's collection, keyed to the publisher's server — so it
never loaded for the subscriber, whose sessions run on their own server; the UI blocked
subscribe for tool packs because of exactly that. Now `collection_subscriptions.
mcp_server_id` keys the reference, per subscriber, to the server they bound the
consuming agent to, and the retriever reads that key beside the collection's own.

The rules:
  K1  a repository is loadable for the current server when its own key matches OR an
      enabled subscription keys it there — and NOT when neither does
  K2  the three loading sites and the on-demand loader all read ONE predicate
  K3  the subscribe route keys planner references from the profiles it just built
  K4  learning that cannot land in the guiding repository lands in the user's OWN
      default for that server — CREATED if none exists (a user can delete the
      bootstrapped one), never created without a user and a server
  K5  the seeding fallback calls the ensure- form, so a dropped lesson is impossible
  K6  the marketplace no longer renders a disabled Subscribe for tool packs

Run: python3 test/test_planner_subscription_keying.py
"""
from __future__ import annotations
import ast, inspect, os, sys, textwrap
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

PASS = FAIL = 0
def check(name, cond, detail=None):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL {name}" + (f": {detail!r}" if detail is not None else ""))

from trusted_data_agent.agent import rag_retriever as RR
from trusted_data_agent.core import config_manager as CM

def t_keyed_predicate():
    r = RR.RAGRetriever.__new__(RR.RAGRetriever)
    orig = RR.planner_subscription_keys
    try:
        RR.planner_subscription_keys = lambda cid: {"srv-SUB"} if cid == 382 else set()
        check("K1 own key matches -> loadable", r._planner_keyed_to(1, "srv-A", "srv-A") is True)
        check("K1 own key differs, no subscription -> not loadable", r._planner_keyed_to(1, "srv-PUB", "srv-SUB") is False)
        check("K1 own key differs, a subscription keys it here -> loadable", r._planner_keyed_to(382, "srv-PUB", "srv-SUB") is True)
        check("K1 a subscription keyed ELSEWHERE does not load it here", r._planner_keyed_to(382, "srv-PUB", "srv-OTHER") is False)
        check("K1 no current server -> only an own-key match (None == None) loads", r._planner_keyed_to(382, None, None) is True and r._planner_keyed_to(382, "srv-PUB", None) is False)
    finally:
        RR.planner_subscription_keys = orig
    check("K1 an unreadable store yields no keys, never a load", RR.planner_subscription_keys(-1) == set())

def t_one_predicate_everywhere():
    src = inspect.getsource(RR.RAGRetriever)
    la = inspect.getsource(RR.RAGRetriever._load_active_collections)
    check("K2 the bulk loader reads the predicate", "_planner_keyed_to(" in la and "coll_mcp_server_id != current_mcp_server_id" not in la)
    body = inspect.getsource(RR.RAGRetriever.toggle_collection)
    check("K2 the enable path reads the predicate", "_planner_keyed_to(" in body and "coll_mcp_server == current_mcp_server_id" not in body)
    check("K2 the on-demand loader reads the predicate", "_planner_keyed_to(" in inspect.getsource(RR.RAGRetriever.ensure_loaded))

def t_subscribe_route_keys():
    routes = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "api", "agent_pack_routes.py")).read()
    tree = ast.parse(routes)
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "subscribe_marketplace_agent_pack")
    calls = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("K3 the subscribe route keys planner subscriptions", "_key_planner_subscriptions" in calls)
    # the keying helper: a profile with a server keys the planner it reads; one reference, one key
    from trusted_data_agent.api import agent_pack_routes as AR
    hsrc = inspect.getsource(AR._key_planner_subscriptions)
    check("K3 the helper keys by the PROFILE's bound server", 'prof.get("mcpServerId")' in hsrc and "ragCollections" in hsrc)
    check("K3 ...planner repositories only", 'repository_type") != "planner"' in hsrc)
    check("K3 ...first profile wins (one reference, one key)", "setdefault(" in hsrc)

def t_ensure_default():
    cm = CM.ConfigManager.__new__(CM.ConfigManager)
    created = []
    class FakeDB:
        def __init__(self, rows): self.rows = rows
        def get_all_collections(self, *a, **k): return list(self.rows)
        def create_collection(self, data):
            created.append(data); self.rows.append({**data, "id": 900 + len(created)}); return 900 + len(created)
    orig_db = CM.get_collection_db if hasattr(CM, "get_collection_db") else None
    import trusted_data_agent.core.collection_db as CDB
    orig_cdb = CDB.get_collection_db
    try:
        db = FakeDB([{"id": 5, "repository_type": "planner", "mcp_server_id": "srv-A", "owner_user_id": "u1"}])
        CDB.get_collection_db = lambda: db
        cm.get_mcp_servers = lambda user_uuid=None: [{"id": "srv-A", "name": "Teradata MCP - A"}, {"id": "srv-B", "name": "Teradata MCP - B"}]
        cm.get_rag_collections = lambda *a, **k: []
        check("K4 an existing default is returned, nothing created", cm.ensure_default_collection_for_mcp_server("srv-A", "u1") == 5 and not created)
        cid = cm.ensure_default_collection_for_mcp_server("srv-B", "u1")
        check("K4 no default for the server -> one is CREATED", cid == 901 and len(created) == 1, (cid, created))
        check("K4 ...as the user's own private planner repository on that server",
              created and created[0]["owner_user_id"] == "u1" and created[0]["mcp_server_id"] == "srv-B"
              and created[0]["repository_type"] == "planner" and created[0]["visibility"] == "private")
        check("K4 ...named after the server", created and created[0]["name"] == "Default Collection - Teradata MCP - B", created and created[0]["name"])
        check("K4 idempotent: the second call finds what the first created", cm.ensure_default_collection_for_mcp_server("srv-B", "u1") == 901 and len(created) == 1)
        check("K4 never created without a user", cm.ensure_default_collection_for_mcp_server("srv-C", None) is None and len(created) == 1)
        check("K4 never created without a server", cm.ensure_default_collection_for_mcp_server("", "u1") is None and len(created) == 1)
    finally:
        CDB.get_collection_db = orig_cdb

def t_seeding_uses_ensure():
    src = inspect.getsource(RR.RAGRetriever.process_turn_for_rag)
    check("K5 the seeding path resolves the default through the ensure- form",
          src.count("ensure_default_collection_for_mcp_server(") >= 2 and "config_manager.get_default_collection_for_mcp_server(" not in src)

def t_ui_unblocked():
    js = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "handlers", "marketplaceHandler.js")).read()
    check("K6 no disabled Subscribe button for tool packs", 'disabled style="opacity:0.4;cursor:not-allowed" title="${tip}">Subscribe' not in js)
    check("K6 Subscribe is rendered for a subscribable pack", "agent-pack-subscribe-btn" in js)

def t_read_back_own_learning():
    """K7 — what the agent learned is READ BACK: a profile bound to an unwritable planner
    (a subscribed view or a locked repository) also retrieves from the user's own default
    planner for the server — the mirror of K4/K5 — never creating one, never widening
    when nothing is unwritable."""
    from trusted_data_agent.agent import rag_retriever as R
    class Ctx:
        user_id = "u1"
        def __init__(self, writable): self.w = set(writable)
        def validate_collection_access(self, cid, write=False): return (cid in self.w) if write else True
    class CM:
        def __init__(self, default): self.default = default; self.ensured = 0
        def get_default_collection_for_mcp_server(self, sid, uid): return self.default
        def ensure_default_collection_for_mcp_server(self, sid, uid): self.ensured += 1; return self.default
    nl = lambda c: False
    cm = CM(24)
    check("K7 a subscribed view (readable, not writable) pulls in the user's own default planner",
          R.read_back_own_learning({8}, Ctx(writable=set()), "srv", cm, is_locked=nl) == {8, 24})
    check("K7 the get- form only — retrieval never creates a collection", cm.ensured == 0)
    check("K7 a locked repository the user OWNS counts as unwritable (the seeding loop refuses it too)",
          R.read_back_own_learning({8}, Ctx(writable={8}), "srv", cm, is_locked=lambda c: c == 8) == {8, 24})
    same = {8}
    check("K7 every bound planner writable → the allowlist is returned UNCHANGED (identity)",
          R.read_back_own_learning(same, Ctx(writable={8}), "srv", cm, is_locked=nl) is same)
    check("K7 no filter (None / empty) → unchanged: everything accessible is read already",
          R.read_back_own_learning(None, Ctx(writable=set()), "srv", cm, is_locked=nl) is None
          and R.read_back_own_learning(set(), Ctx(writable=set()), "srv", cm, is_locked=nl) == set())
    check("K7 no default planner for the server → unchanged", R.read_back_own_learning(same, Ctx(writable=set()), "srv", CM(None), is_locked=nl) is same)
    already = {8, 24}
    check("K7 already bound → unchanged", R.read_back_own_learning(already, Ctx(writable=set()), "srv", cm, is_locked=nl) is already)
    check("K7 no server → unchanged", R.read_back_own_learning(same, Ctx(writable=set()), None, cm, is_locked=nl) is same)
    class Boom(Ctx):
        def validate_collection_access(self, *a, **k): raise RuntimeError("db down")
    check("K7 a lookup error fails OPEN to the unchanged allowlist", R.read_back_own_learning(same, Boom(set()), "srv", cm, is_locked=nl) is same)
    # wiring: the planner widens the allowlist BEFORE it retrieves, and the rule never uses ensure_
    src = inspect.getsource(R.read_back_own_learning)
    check("K7 the rule reads the default with the get- form and never the ensure- form",
          "get_default_collection_for_mcp_server" in src and "ensure_default_collection_for_mcp_server" not in src)
    _root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    psrc = open(os.path.join(_root, "src", "trusted_data_agent", "agent", "planner.py")).read()
    i_rb = psrc.find("read_back_own_learning("); i_ret = psrc.find("retrieved_cases = None if _no_planner_bound else await self.rag_retriever.retrieve_examples(")
    check("K7 the planner widens the allowlist before champion retrieval", 0 < i_rb < i_ret and "allowed_collection_ids = _widened" in psrc, (i_rb, i_ret))


for fn in (t_keyed_predicate, t_one_predicate_everywhere, t_subscribe_route_keys, t_ensure_default, t_seeding_uses_ensure, t_ui_unblocked, t_read_back_own_learning):
    try: fn()
    except Exception as e:
        FAIL += 1; print(f"  FAIL {fn.__name__} raised {type(e).__name__}: {e}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
