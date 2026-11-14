"""The pack ledger is the system of record for what a pack owns.

`agent_pack_resources` records tag -> profile-id for every profile a pack installed.
`user_preferences.profiles` is a mutable COPY of the same fact. When they diverge —
a profile lost out-of-band while the pack row and its resources survive — every path
that resolves a tag against the copy reports "not installed", and the one strategy
built to preserve those IDs (update_in_place) becomes unreachable.

Observed on production: a user's four pack profiles vanished from user_preferences
while installation 86, its resources, its 10-doc collection and its 4,612-entity
knowledge graph all survived, the graph still bound to the coordinator's profile ID.
Re-forking would have minted new IDs and orphaned all of it; re-importing in place
refused to run.

CI-pure: real sqlite for the ledger, fakes for config.
"""
import ast, os, sqlite3, sys, tempfile, pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_p, _f = 0, []
def check(name, cond, detail=""):
    global _p
    if cond:
        _p += 1
    else:
        _f.append(f"{name}: {detail}")

# ─────────────────────────── the ledger lookup ───────────────────────────
from trusted_data_agent.core.agent_pack_db import AgentPackDB

def _ledger_db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE agent_pack_installations (
        id INTEGER PRIMARY KEY, name TEXT, owner_user_id TEXT)""")
    c.execute("""CREATE TABLE agent_pack_resources (
        pack_installation_id INTEGER, resource_type TEXT, resource_id TEXT,
        resource_tag TEXT, resource_role TEXT, is_owned INTEGER)""")
    c.execute("INSERT INTO agent_pack_installations VALUES (86,'Policing','user-A')")
    c.execute("INSERT INTO agent_pack_installations VALUES (83,'Policing','user-B')")
    for tag, pid in [("POLICE","p-05108e97"),("NIBRS","p-27effb16"),
                     ("CASEDF","p-c2a2f047"),("PDOCS","p-3a4839e7")]:
        c.execute("INSERT INTO agent_pack_resources VALUES (86,'profile',?,?,'expert',1)", (pid, tag))
    # user-B owns the SAME tags under a different installation and different IDs
    c.execute("INSERT INTO agent_pack_resources VALUES (83,'profile','p-OTHER','POLICE','coordinator',1)")
    # a non-profile resource must never surface as a profile
    c.execute("INSERT INTO agent_pack_resources VALUES (86,'collection','490','collection_1','collection',1)")
    c.commit(); c.close()
    return path

def t_ledger_lookup():
    path = _ledger_db()
    try:
        db = AgentPackDB(path)
        got = db.get_installations_by_profile_tags(["POLICE","NIBRS","CASEDF","PDOCS"], "user-A")
        check("resolves every owned tag", sorted(got) == ["CASEDF","NIBRS","PDOCS","POLICE"], sorted(got))
        check("carries the ledger's profile id",
              got["POLICE"]["profile_id"] == "p-05108e97", got.get("POLICE"))
        check("carries the installation id",
              got["POLICE"]["installation_id"] == 86, got.get("POLICE"))

        # Scoping: user-B's ledger must never answer for user-A's request and vice versa.
        other = db.get_installations_by_profile_tags(["POLICE"], "user-B")
        check("scoped to the owner", other["POLICE"]["profile_id"] == "p-OTHER", other)
        check("owner scoping is not a filter on the result set",
              db.get_installations_by_profile_tags(["POLICE"], "user-C") == {},
              db.get_installations_by_profile_tags(["POLICE"], "user-C"))

        check("a collection resource is not a profile",
              "collection_1" not in db.get_installations_by_profile_tags(["collection_1"], "user-A"))
        check("empty tag list is a no-op, not a full scan",
              db.get_installations_by_profile_tags([], "user-A") == {})
        check("unknown tag is absent, not None",
              db.get_installations_by_profile_tags(["NOPE"], "user-A") == {})
    finally:
        os.unlink(path)

# ─────────────────────────── the upsert truth table ───────────────────────────
from trusted_data_agent.core.agent_pack_manager import AgentPackManager

class FakeConfig:
    def __init__(self, live_ids):
        self.live = set(live_ids)
        self.updated, self.added = [], []
    def update_profile(self, pid, data, user):
        if pid not in self.live:
            return False               # the real config_manager's contract
        self.updated.append((pid, data.get("id")))
        return True
    def add_profile(self, data, user):
        self.added.append(data.get("id"))
        self.live.add(data.get("id"))
        return True

def _upsert(live, ledger_pid, minted="p-NEW"):
    mgr = AgentPackManager.__new__(AgentPackManager)
    cfg = FakeConfig(live)
    t2p = {}
    mgr._upsert_pack_profile(cfg, {"tag": "POLICE"}, {"id": minted},
                             ledger_pid, "user-A", t2p)
    return cfg, t2p

def t_upsert_truth_table():
    # 1. live profile exists -> update in place, ID preserved, nothing added
    cfg, t2p = _upsert(live=["p-05108e97"], ledger_pid="p-05108e97")
    check("live: updates in place", cfg.updated == [("p-05108e97","p-05108e97")], cfg.updated)
    check("live: adds nothing", cfg.added == [], cfg.added)
    check("live: maps the tag to the ledger id", t2p == {"POLICE":"p-05108e97"}, t2p)

    # 2. no ledger entry -> genuinely new
    cfg, t2p = _upsert(live=[], ledger_pid=None)
    check("new: adds", cfg.added == ["p-NEW"], cfg.added)
    check("new: keeps the minted id", t2p == {"POLICE":"p-NEW"}, t2p)
    check("new: does not attempt an update", cfg.updated == [], cfg.updated)

    # 3. THE REPAIR: ledger owns the tag, no live profile carries the ID.
    cfg, t2p = _upsert(live=[], ledger_pid="p-05108e97")
    check("repair: re-creates rather than silently skipping", cfg.added != [], "nothing was written")
    check("repair: re-creates AT THE LEDGER ID — a fresh id would orphan the "
          "pack's knowledge graph and collection",
          cfg.added == ["p-05108e97"], cfg.added)
    check("repair: maps the tag to the ledger id", t2p == {"POLICE":"p-05108e97"}, t2p)

    # A repair must never be reported as an ordinary update.
    cfg, t2p = _upsert(live=[], ledger_pid="p-05108e97")
    check("repair: no phantom update recorded", cfg.updated == [], cfg.updated)

# ─────────────────────────── the wiring ───────────────────────────
SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent/core/agent_pack_manager.py"

def _calls_in(fn_name):
    """Names called anywhere inside a function. Matches async defs too — a route or
    import path is an AsyncFunctionDef, and matching only FunctionDef makes every
    assertion below pass vacuously against an empty set."""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            out = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            return out
    return None

def t_wiring():
    for fn in ("import_pack", "_handle_update_in_place", "_upsert_pack_profile"):
        check(f"{fn} exists (a rule asserted over a missing function passes vacuously)",
              _calls_in(fn) is not None, fn)

    check("the import gate consults the ledger",
          "get_installations_by_profile_tags" in (_calls_in("import_pack") or set()))
    check("installation detection consults the ledger",
          "get_installations_by_profile_tags" in (_calls_in("_handle_update_in_place") or set()))
    check("the write goes through the one upsert seam",
          "_upsert_pack_profile" in (_calls_in("_handle_update_in_place") or set()))

    # One seam, not two copies: neither loop may call update/add_profile directly.
    inplace = _calls_in("_handle_update_in_place") or set()
    check("update_in_place no longer writes profiles itself",
          "update_profile" not in inplace and "add_profile" not in inplace,
          sorted(inplace & {"update_profile", "add_profile"}))

    # The repair only works because the return value is read.
    body = SRC.read_text()
    seam = body[body.index("def _upsert_pack_profile"):]
    seam = seam[:seam.index("\n    async def ")]
    check("the upsert branches on update_profile's return value",
          "if config_manager.update_profile(" in seam)

for fn in (t_ledger_lookup, t_upsert_truth_table, t_wiring):
    try:
        fn()
    except Exception as e:
        _f.append(f"{fn.__name__} RAISED: {type(e).__name__}: {e}")

print("\n".join("FAIL: " + x for x in _f))
print(f"{_p} passed, {len(_f)} failed  ({_p + len(_f)} assertions)")
sys.exit(1 if _f else 0)
