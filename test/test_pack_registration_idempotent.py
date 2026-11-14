#!/usr/bin/env python3
"""Agent-pack registration is idempotent — one pack, one ledger row.

The defect this pins: `create_and_install` (the publisher-registration path behind
`build.py --register`) INSERTed a fresh `agent_pack_installations` row on every run.
One set of agents then ended up claimed by N rows, and every profile/collection card
rendered N identical pack chips for a pack installed once. It is not merely cosmetic:
two ledger rows claim the same resources, and the marketplace listing points at one
of them.

A genuine second installation produces DIFFERENT resources (`expand` renames the
tags, `replace` deletes the originals), so the same owner holding two registrations
of one pack name is always bookkeeping — which is what makes both the reuse and the
card-side dedupe safe.

CI-pure: real sqlite in a temp dir, no server, no network.
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from trusted_data_agent.core.agent_pack_manager import AgentPackManager   # noqa: E402
from trusted_data_agent.core.agent_pack_db import AgentPackDB             # noqa: E402

passed, failures = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(f"{name}{(' — ' + str(detail)) if detail else ''}")


SCHEMA = """
CREATE TABLE agent_pack_installations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, description TEXT, version TEXT, author TEXT,
    coordinator_tag TEXT, coordinator_profile_id TEXT, pack_type TEXT,
    owner_user_id TEXT, installed_at TEXT, manifest_json TEXT
);
CREATE TABLE agent_pack_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_installation_id INTEGER, resource_type TEXT, resource_id TEXT,
    resource_tag TEXT, resource_role TEXT, is_owned INTEGER
);
CREATE TABLE marketplace_agent_packs (
    id TEXT PRIMARY KEY, name TEXT, source_installation_id INTEGER
);
"""

OWNER = "user-a"
OTHER = "user-b"


def fresh_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def manifest(name="Policing Data Products", origin="created", version="1.0.0"):
    m = {"name": name, "description": "d", "version": version, "author": "a"}
    if origin:
        m["_registration_origin"] = origin
    return m


def record(mgr, m, owner=OWNER, profiles=(("p1", "POLICE"),), replace=None, is_owned=False,
           collections=(), kgs=()):
    return mgr._record_installation(
        manifest=m, coordinator_tag=profiles[0][1], coordinator_profile_id=profiles[0][0],
        pack_type="genie",
        profile_ids=[p[0] for p in profiles], profile_tags=[p[1] for p in profiles],
        profile_roles=["coordinator"] * len(profiles),
        collection_ids=[c[0] for c in collections], collection_refs=[c[1] for c in collections],
        kg_ids=[k[0] for k in kgs], kg_names=[k[1] for k in kgs],
        user_uuid=owner,
        is_owned=is_owned, replace_installation_id=replace,
    )


with tempfile.TemporaryDirectory() as td:
    db = os.path.join(td, "t.db")
    fresh_db(db)
    mgr = AgentPackManager(db_path=db)
    pdb = AgentPackDB(db_path=db)

    # ── [1] the resolver finds only this owner's own REGISTRATIONS ───────────
    check("[1] no registration yet", mgr._find_own_registration(OWNER, "Policing Data Products") is None)
    first = record(mgr, manifest())
    check("[1] finds the registration", mgr._find_own_registration(OWNER, "Policing Data Products") == first)
    check("[1] another owner's pack is not found",
          mgr._find_own_registration(OTHER, "Policing Data Products") is None)
    check("[1] a different pack name is not found",
          mgr._find_own_registration(OWNER, "Other Pack") is None)

    # An IMPORTED or SUBSCRIBED pack of the same name is a different act with
    # different ownership semantics — a registration must never hijack it.
    imported = record(mgr, manifest(origin=None), profiles=(("p9", "POLICE"),))
    check("[1] an imported pack of the same name is not treated as a registration",
          mgr._find_own_registration(OWNER, "Policing Data Products") == first)

    # ── [2] re-registration UPDATES in place: id preserved, ledger rebuilt ───
    again = record(mgr, manifest(version="2.0.0"),
                   profiles=(("p1", "POLICE"), ("p2", "NIBRS")), replace=first)
    check("[2] the installation id survives re-registration", again == first)
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM agent_pack_installations WHERE id=?", (first,)).fetchone()
    check("[2] the row is updated, not duplicated", row["version"] == "2.0.0")
    n_rows = conn.execute("SELECT COUNT(*) c FROM agent_pack_installations "
                          "WHERE owner_user_id=? AND name=?", (OWNER, "Policing Data Products")).fetchone()["c"]
    check("[2] still exactly two rows (the registration + the import)", n_rows == 2, n_rows)
    res = {r["resource_id"] for r in conn.execute(
        "SELECT resource_id FROM agent_pack_resources WHERE pack_installation_id=?", (first,))}
    check("[2] resources are rebuilt to what the pack ships NOW", res == {"p1", "p2"}, res)
    conn.close()

    # A member DROPPED from the pack must stop being claimed. The union of old and
    # new happens to equal the expected set when members are only ADDED, so only a
    # shrinking pack proves the ledger is rebuilt rather than appended to.
    record(mgr, manifest(version="3.0.0"), profiles=(("p2", "NIBRS"),), replace=first)
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    res2 = {r["resource_id"] for r in conn.execute(
        "SELECT resource_id FROM agent_pack_resources WHERE pack_installation_id=?", (first,))}
    check("[2] a member the pack no longer ships is no longer claimed", res2 == {"p2"}, res2)
    conn.close()

    # ── [3] the card question: one pack name, reported once ─────────────────
    # Self-contained fixture on its own resource, so no earlier section's state
    # can decide this outcome (an ordering coupling is a gate that reports the
    # wrong thing the moment a section above it changes).
    a = record(mgr, manifest(), profiles=(("pX", "POLICE"),))
    b = record(mgr, manifest(), profiles=(("pX", "POLICE"),))       # a legacy duplicate row
    check("[3] the duplicate really is a second row", a != b)
    scoped = pdb.get_packs_for_resource("profile", "pX", owner_user_id=OWNER)
    names = [p["name"] for p in scoped]
    check("[3] the owner's card shows the pack once", names.count("Policing Data Products") == 1, names)
    unscoped = pdb.get_packs_for_resource("profile", "pX")
    check("[3] the unscoped answer still reports every claim (delete/uninstall policy)",
          len(unscoped) == 2, unscoped)

    # Two genuinely DIFFERENT packs sharing a resource must both still show.
    record(mgr, manifest(name="Second Pack"), profiles=(("pX", "SECOND"),))
    names2 = sorted(p["name"] for p in pdb.get_packs_for_resource("profile", "pX", owner_user_id=OWNER))
    check("[3] distinct pack names are never merged",
          names2 == ["Policing Data Products", "Second Pack"], names2)
    # ...and another user's identically-named pack is not folded into ours.
    foreign = record(mgr, manifest(), owner=OTHER, profiles=(("pX", "POLICE"),))
    mine = pdb.get_packs_for_resource("profile", "pX", owner_user_id=OWNER)
    check("[3] the scope is still the caller's own packs",
          all(p["id"] != foreign for p in mine))

# ── [4] the cleanup script only removes a provable bookkeeping duplicate ────
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "maintenance"))
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "dedupe_pack_registrations",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "maintenance",
                 "dedupe_pack_registrations.py"))
dedupe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dedupe)

with tempfile.TemporaryDirectory() as td:
    db = os.path.join(td, "t.db")
    fresh_db(db)
    mgr = AgentPackManager(db_path=db)

    keep = record(mgr, manifest(), profiles=(("p1", "POLICE"), ("p2", "NIBRS")))
    subset = record(mgr, manifest(), profiles=(("p1", "POLICE"),))            # strict subset
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO marketplace_agent_packs (id,name,source_installation_id) VALUES (?,?,?)",
                 ("listing-1", "Policing Data Products", keep))
    conn.commit()

    items = dedupe.plan(conn)
    removable = [i for i in items if not i["blocked_by"]]
    check("[4] the subset duplicate is removable", [i["remove"] for i in removable] == [subset], items)
    check("[4] the listing's source is the survivor", all(i["keep"] == keep for i in items))

    # A row that OWNS resources is a real installation, never a duplicate.
    owning = record(mgr, manifest(), profiles=(("p1", "POLICE"),), is_owned=True)
    items = dedupe.plan(conn)
    owning_item = next(i for i in items if i["remove"] == owning)
    check("[4] a row that owns resources is left alone",
          any("OWNS" in r for r in owning_item["blocked_by"]), owning_item["blocked_by"])

    # A row claiming something the survivor does not is not a subset.
    extra = record(mgr, manifest(), profiles=(("p1", "POLICE"), ("p99", "EXTRA")))
    items = dedupe.plan(conn)
    extra_item = next(i for i in items if i["remove"] == extra)
    check("[4] a row with resources the survivor lacks is left alone",
          any("does not" in r for r in extra_item["blocked_by"]), extra_item["blocked_by"])

    # A published row is never removed.
    conn.execute("INSERT INTO marketplace_agent_packs (id,name,source_installation_id) VALUES (?,?,?)",
                 ("listing-2", "Policing Data Products", subset))
    conn.commit()
    items = dedupe.plan(conn)
    subset_item = next(i for i in items if i["remove"] == subset)
    check("[4] a row a marketplace listing points at is left alone",
          any("listing" in r for r in subset_item["blocked_by"]), subset_item["blocked_by"])
    conn.close()

# ── [5] the call site is actually wired (on the AST — a comment naming the
#        helper, or a helper nobody calls, must not satisfy this) ────────────
import ast  # noqa: E402
from pathlib import Path as _P  # noqa: E402

_src = _P(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                                 "trusted_data_agent", "core", "agent_pack_manager.py")).read_text()
_tree = ast.parse(_src)
_fn = next((n for n in ast.walk(_tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "create_and_install"), None)
check("[5] create_and_install exists", _fn is not None)
if _fn is not None:
    _calls = [n for n in ast.walk(_fn) if isinstance(n, ast.Call)]
    _names = {getattr(c.func, "attr", None) for c in _calls}
    check("[5] it resolves an existing registration", "_find_own_registration" in _names)
    _rec = [c for c in _calls if getattr(c.func, "attr", None) == "_record_installation"]
    check("[5] it records exactly once", len(_rec) == 1, len(_rec))
    _kw = {k.arg for c in _rec for k in c.keywords}
    check("[5] and passes replace_installation_id", "replace_installation_id" in _kw, _kw)


# ── [6] the publisher's ledger names the same members as a consumer's ───────
#
# The defect: `create_and_install` recorded profiles and collections but NOT the
# pack's knowledge graphs, while a consumer's import recorded its KG copies — so
# the two sides counted different members, `composition_hash_for` could never
# agree, and the marketplace honesty gate correctly refused to reinstate the
# pack's own seal on a fork (measured live 2026-09-08: publisher 8 members,
# fork 11, the extra three being the ontologies).
#
# Driven through the REAL member_refs/composition_hash_for over a temp ledger,
# with content hashes keyed by TAG so the two sides hold the same content under
# DIFFERENT ids — which is the situation that matters and the reason the hash is
# taken over content rather than ids.
from trusted_data_agent.core.trust import pack as _tp              # noqa: E402
import trusted_data_agent.core.agent_pack_db as _apdb_mod          # noqa: E402

_CONTENT = {}   # resource id -> content hash


class _FakeContent:
    @staticmethod
    def live_hash(kind, rid):
        return _CONTENT.get(str(rid))


class _FakeStore:
    @staticmethod
    def attested_hash(kind, rid, db_path=None, owner_uuid=None):
        # signature faithful to the real store (owner_uuid: PROP-009 owner-scoped agents)
        return _CONTENT.get(str(rid))


def _fake_agent_hash(pid, own):
    return _CONTENT.get(str(pid))


def _members(mgr, db, m, owner, prefix):
    """One pack side: three agents, two collections, three graphs.

    The id carries the side (`pub-`/`con-`); the content hash carries only the
    member's identity, so both sides hold identical content under different ids.
    """
    profiles = [(f"{prefix}-p-{t}", t) for t in ("NIBRS", "CASEDF", "POLICE")]
    collections = [(f"{prefix}90{i}", f"c{i}") for i in (1, 2)]
    kgs = [(f"{prefix}-kg-{t}", f"KG {t}") for t in ("NIBRS", "CASEDF", "ESTATE")]
    for rid, tag in profiles:
        _CONTENT[rid] = f"hash-agent-{tag}"
    for cid, ref in collections:
        _CONTENT[str(cid)] = f"hash-coll-{ref}"
    for kid, name in kgs:
        _CONTENT[kid] = f"hash-kg-{name}"
    return record(mgr, m, owner=owner, profiles=tuple(profiles),
                  collections=tuple(collections), kgs=tuple(kgs))


with tempfile.TemporaryDirectory() as td:
    db = os.path.join(td, "t.db")
    fresh_db(db)
    mgr = AgentPackManager(db_path=db)

    _orig_cls, _orig_path = _apdb_mod.AgentPackDB, _apdb_mod._DEFAULT_DB_PATH
    _orig_agent, _orig_content, _orig_store = _tp.agent_live_hash, _tp._content, _tp._store
    _apdb_mod.AgentPackDB = lambda *a, **k: _orig_cls(db_path=db)
    _apdb_mod._DEFAULT_DB_PATH = db
    _tp.agent_live_hash, _tp._content, _tp._store = _fake_agent_hash, _FakeContent, _FakeStore
    try:
        pub = _members(mgr, db, manifest(), OWNER, "pub")
        con = _members(mgr, db, manifest(origin=None), OTHER, "con")

        pub_refs, _ = _tp.member_refs(str(pub))
        con_refs, _ = _tp.member_refs(str(con))
        check("[6] the publisher declares every member kind",
              sorted(r.kind for r in pub_refs) == sorted(r.kind for r in con_refs),
              (sorted(r.kind for r in pub_refs), sorted(r.kind for r in con_refs)))
        check("[6] three ontologies are claimed on both sides",
              sum(1 for r in pub_refs if r.kind == "ontology") == 3, pub_refs)
        check("[6] the ids genuinely differ between the two sides",
              {r.artifact_id for r in pub_refs}.isdisjoint({r.artifact_id for r in con_refs}))
        check("[6] the compositions AGREE — the pack seal can reinstate on a fork",
              _tp.composition_hash_for(str(pub)) == _tp.composition_hash_for(str(con)),
              (_tp.composition_hash_for(str(pub)), _tp.composition_hash_for(str(con))))

        # ...and the reason it agrees is that the graphs are claimed. Drop them from
        # the publisher's ledger (the pre-fix state) and the seal can never travel.
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM agent_pack_resources "
                     "WHERE pack_installation_id=? AND resource_type='knowledge_graph'", (pub,))
        conn.commit(); conn.close()
        check("[6] a publisher that does not claim its graphs diverges (the defect)",
              _tp.composition_hash_for(str(pub)) != _tp.composition_hash_for(str(con)))
    finally:
        _apdb_mod.AgentPackDB, _apdb_mod._DEFAULT_DB_PATH = _orig_cls, _orig_path
        _tp.agent_live_hash, _tp._content, _tp._store = _orig_agent, _orig_content, _orig_store

# ── [7] the derivation reads the manifest, and is what the registration uses ──
_mkg = AgentPackManager._manifest_kg_members
check("[7] no knowledge_graphs section yields nothing", _mkg({"name": "p"}) == ([], []))
check("[7] a graph without a kg_id is skipped (nothing to claim)",
      _mkg({"knowledge_graphs": [{"name": "n"}]}) == ([], []))
_ids, _names = _mkg({"knowledge_graphs": [
    {"kg_id": "a", "name": "KG A"},
    {"kg_id": "b"},                       # falls back to the id as its label
    {"kg_id": "a", "name": "KG A again"},  # the same graph is claimed once
    "not-a-dict",
]})
check("[7] every shipped graph is claimed, once", _ids == ["a", "b"], _ids)
check("[7] a label is always present", _names == ["KG A", "b"], _names)

# ── [8] uninstall: a registration made HERE never deletes what it references ──
_un = next((n for n in ast.walk(_tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "uninstall_pack"), None)
check("[8] uninstall_pack exists", _un is not None)
if _un is not None:
    _un_src = ast.get_source_segment(_src, _un) or ""
    _kg_loop = _un_src.split("for res in kg_resources:", 1)
    check("[8] the KG loop exists", len(_kg_loop) == 2)
    if len(_kg_loop) == 2:
        _body = _kg_loop[1].split("# Step 3b", 1)[0]
        _guarded = "if registration_created_here:" in _body and "continue" in _body
        check("[8] a registration made here keeps its graphs", _guarded, _body[:200])
        # A mutation that removes the guard must fail BY NAME, never by crashing
        # the run — a gate that dies without saying what broke is half a gate.
        check("[8] the guard runs BEFORE anything is removed",
              _guarded and "clear_graph" in _body
              and _body.index("registration_created_here") < _body.index("clear_graph"))
    check("[8] the origin is read from the installation row",
          "_registration_origin" in _un_src and "registration_created_here = " in _un_src)
    # No regression: every other origin still deletes its copies, so an imported or
    # forked pack — and a subscription, which records copies with is_owned=0 while
    # genuinely owning them — cleans up exactly as before.
    check("[8] the guard is scoped to the origin, not to is_owned",
          'res.get("is_owned"' not in _kg_loop[1].split("# Step 3b", 1)[0])

# ── [9] the registration path is wired to the derivation (AST) ──────────────
if _fn is not None:
    _names_called = {getattr(c.func, "attr", None) for c in ast.walk(_fn) if isinstance(c, ast.Call)}
    check("[9] create_and_install derives its graph members", "_manifest_kg_members" in _names_called)
    _rec2 = [c for c in ast.walk(_fn) if isinstance(c, ast.Call)
             and getattr(c.func, "attr", None) == "_record_installation"]
    _kw2 = {k.arg for c in _rec2 for k in c.keywords}
    check("[9] and records them as pack resources", {"kg_ids", "kg_names"} <= _kw2, _kw2)


if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print("  FAIL:", f)
    print(f"\n{passed} passed, {len(failures)} failed")
    sys.exit(1)
print(f"{passed} passed, 0 failed (agent-pack registration is idempotent and claims the graphs it ships)")
