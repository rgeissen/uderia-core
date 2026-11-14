"""Gate: update_in_place patches a PLANNER collection case-by-case, and never
deletes what the agent learned.

Why this exists
---------------
Adding an Optimize profile to an agent pack pulls its planner repository into the
manifest (the export walks each member's ``ragCollections``). Before this gate the
in-place re-import path ran every collection through ``_diff_and_patch_collection``,
which resolves a *knowledge* backend and raises when there is none:

    RuntimeError: update_in_place: no knowledge backend resolved for collection <id>

So a pack carrying a planner collection could be installed but never REFRESHED — the
subscriber-safe republish path, on a live account.

The rules this pins, each of which is a way to lose data rather than a preference:

  R1  a case the pack ships and the store lacks is ADDED
  R2  a case whose SQL changed is UPDATED in place (id preserved)
  R3  a case that is byte-identical is UNCHANGED (and gets the ownership stamp, so a
      case seeded before the stamp existed becomes removable later)
  R4  a pack-owned case the pack no longer ships is REMOVED
  R5  a case the pack does not own is NEVER removed — it is what the agent learned
      from its own successful turns
  R6  duplicates of a pack-owned case are collapsed (residue from an unguarded
      top-up), duplicates the pack does not own are kept
  R7  the store is reached by NAME through the raw client, never through the
      retriever's loaded map — that map is empty unless the process' MCP context
      matches, and an empty read is indistinguishable from an empty repository

Run: python3 test/test_pack_planner_diff.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from trusted_data_agent.core.agent_pack_manager import AgentPackManager  # noqa: E402

PASS = FAIL = 0


class FakeCollectionDB:
    """The collections table, faithful in the one column under test: a collection's
    store NAME (``col_<uuid>_...``) is not its display name."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def get_collection_by_id(self, cid):
        return self.rows.get(int(cid))

    def update_counts(self, *a, **k):
        return None  # the count refresh is a soft side effect, not under test


# Hermetic for the WHOLE module: the store-name resolver reads the collections table,
# and a real row for id 8 or 11 exists on any developer's instance. Empty by default,
# so every section runs on the caller's hint exactly as before; D7 puts a row in to
# prove the row wins over the hint.
from trusted_data_agent.core import collection_db as _cdb  # noqa: E402
fake_db = FakeCollectionDB()
_cdb.get_collection_db = lambda: fake_db

# Hermetic for the WHOLE module, second half: every patch MIRRORS to disk under the
# project root. With the root left real, the R-sections wrote `case_keep.json` and
# friends into the developer's actual `rag/tda_rag_cases/collection_8/` — four stray
# stamped files that entered the publisher's trust baseline and flipped a signed agent
# to `changed` (found live, 2026-09-07). A gate must never touch the tree it tests.
import pathlib as _pathlib  # noqa: E402
from trusted_data_agent.core import utils as _utils  # noqa: E402
_GATE_ROOT = _pathlib.Path(tempfile.mkdtemp(prefix="planner_diff_gate_"))
_utils.get_project_root = lambda: _GATE_ROOT


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got!r}, want {want!r}")


class FakeStore:
    """A ChromaDB collection, faithful in the dimensions under test: ids are unique,
    get() returns parallel lists, update() merges only what it is given."""

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}

    def get(self, include=None):
        ids = list(self.rows)
        return {
            "ids": ids,
            "metadatas": [self.rows[i]["metadata"] for i in ids],
            "documents": [self.rows[i]["document"] for i in ids],
        }

    def add(self, ids, documents, metadatas):
        for i, cid in enumerate(ids):
            assert cid not in self.rows, "add() must not overwrite an existing id"
            self.rows[cid] = {"id": cid, "document": documents[i], "metadata": metadatas[i]}

    def update(self, ids, documents=None, metadatas=None):
        for i, cid in enumerate(ids):
            if documents is not None:
                self.rows[cid]["document"] = documents[i]
            if metadatas is not None:
                self.rows[cid]["metadata"] = metadatas[i]

    def delete(self, ids):
        for cid in ids:
            self.rows.pop(cid, None)

    def count(self):
        return len(self.rows)


class FakeRetriever:
    def __init__(self, store, expect_name):
        self.client = self
        self._store = store
        self._expect = expect_name
        self.collections = {}          # R7: deliberately EMPTY — the loaded map lies
        self.asked_for = None

    def get_collection(self, name):
        self.asked_for = name
        if name != self._expect:
            # What ChromaDB raises — an exception, so a caller's soft-fail SWALLOWS it
            # and reports 0 stamped; the gate must therefore assert the COUNT, not the
            # absence of a raise.
            raise ValueError(f"Collection [{name}] does not exist")
        return self._store


def _zip_with(cases, path):
    blob = {
        "ids": [c["id"] for c in cases],
        "metadatas": [c["metadata"] for c in cases],
        "documents": [c.get("document", "") for c in cases],
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("collection_metadata.json", json.dumps({"repository_type": "planner"}))
        z.writestr("documents.jsonl", json.dumps(blob))
    return path


def _patch(mgr, **kw):
    """Run the patch, reporting a RAISE as a NAMED failure.

    A mutation that makes the function explode (e.g. reaching the store through the
    retriever's loaded map, which is empty here by construction) would otherwise kill
    the run and print nothing — indistinguishable from a gate that was never run.
    """
    global FAIL
    try:
        return asyncio.new_event_loop().run_until_complete(
            mgr._diff_and_patch_planner_collection(**kw))
    except Exception as e:
        FAIL += 1
        print(f"  FAIL patch raised instead of patching: {type(e).__name__}: {e}")
        return {"added": -1, "updated": -1, "removed": -1,
                "unchanged": -1, "learned_kept": -1}


def _case(cid, q, sql, owner=None):
    md = {"user_query": q, "sql_statement": sql}
    if owner:
        md["uderia_pack_ref"] = owner
    return {"id": cid, "metadata": md, "document": q}


def run():
    global FAIL
    mgr = AgentPackManager.__new__(AgentPackManager)
    PACK = "89"

    # ── key identity ──
    k = AgentPackManager._planner_case_key
    check("R-key normalises whitespace/case",
          k({"user_query": "  How   MANY victims? "}), k({"user_query": "how many victims?"}))
    check("R-key empty on a case with no question", k({}), "")

    tmp = tempfile.mkdtemp()

    # ── R1..R6 in one pass ──
    store = FakeStore([
        _case("keep",   "unchanged question", "SELECT 1", owner=PACK),   # R3
        _case("change", "changed question",   "SELECT old", owner=PACK), # R2
        _case("gone",   "dropped question",   "SELECT 2", owner=PACK),   # R4
        _case("learned", "learned question",  "SELECT 3"),               # R5 (no owner)
        _case("dup1",   "unchanged question", "SELECT 1", owner=PACK),   # R6 pack dup
        _case("dup2",   "learned question",   "SELECT 3"),               # R6 learned dup
        _case("unstamped", "legacy question", "SELECT 4"),               # R3 stamp path
    ])
    zpath = _zip_with([
        _case("p-keep",   "unchanged question", "SELECT 1"),
        _case("p-change", "changed question",   "SELECT NEW"),
        _case("p-new",    "brand new question", "SELECT 5"),
        _case("p-legacy", "legacy question",    "SELECT 4"),
    ], os.path.join(tmp, "planner.zip"))

    ret = FakeRetriever(store, "tda_rag_coll_8_abc")
    stats = _patch(mgr, collection_id=8,
                   collection_meta={"collection_name": "tda_rag_coll_8_abc"},
                   inner_zip=zpath, pack_ref=PACK, retriever=ret)

    check("R7 store reached by name", ret.asked_for, "tda_rag_coll_8_abc")
    check("R1 new case added", stats["added"], 1)
    check("R2 changed case updated", stats["updated"], 1)
    check("R4+R6 pack-owned dropped/dup removed", stats["removed"], 2)
    check("R3 identical + legacy unchanged", stats["unchanged"], 2)
    check("R5+R6 learned kept", stats["learned_kept"], 2)

    ids = set(store.rows)
    check("R2 update preserves the existing id", "change" in ids, True)
    check("R2 update rewrote the SQL",
          (store.rows.get("change") or {}).get("metadata", {}).get("sql_statement"),
          "SELECT NEW")
    check("R1 added under the pack's id", "p-new" in ids, True)
    check("R4 dropped pack case is gone", "gone" in ids, False)
    check("R5 learned case survives", "learned" in ids, True)
    check("R5 learned duplicate survives", "dup2" in ids, True)
    check("R6 pack duplicate collapsed", "dup1" in ids, False)
    def _owner(row_id):
        """Ownership stamp, or a named miss — never a KeyError that kills the run."""
        row = store.rows.get(row_id)
        return (row or {}).get("metadata", {}).get("uderia_pack_ref", f"<no row {row_id}>")
    check("R3 legacy case gained the ownership stamp", _owner("unstamped"), PACK)
    check("R1 added case carries the ownership stamp", _owner("p-new"), PACK)

    # ── a pack that ships nothing removes only its own ──
    store2 = FakeStore([
        _case("mine", "a", "SELECT 1", owner=PACK),
        _case("theirs", "b", "SELECT 2"),
        _case("otherpack", "c", "SELECT 3", owner="77"),
    ])
    z2 = _zip_with([], os.path.join(tmp, "empty.zip"))
    s2 = _patch(mgr, collection_id=9, collection_meta={"collection_name": "n2"},
                inner_zip=z2, pack_ref=PACK, retriever=FakeRetriever(store2, "n2"))
    check("empty pack removes only its own", s2["removed"], 1)
    check("empty pack keeps the rest", s2["learned_kept"], 2)
    check("another pack's case is not ours to delete", "otherpack" in store2.rows, True)

    # ── idempotence: a second identical run changes nothing ──
    ret3 = FakeRetriever(store, "tda_rag_coll_8_abc")
    s3 = _patch(mgr, collection_id=8,
                collection_meta={"collection_name": "tda_rag_coll_8_abc"},
                inner_zip=zpath, pack_ref=PACK, retriever=ret3)
    check("idempotent: nothing added", s3["added"], 0)
    check("idempotent: nothing updated", s3["updated"], 0)
    check("idempotent: nothing removed", s3["removed"], 0)

    # ── R8: the ownership token survives a reinstall ──
    m = {"name": "Policing Data Products"}
    check("R8 token is the pack name, not the installation id",
          AgentPackManager._pack_ref(m), "Policing Data Products")
    check("R8 token is stable when the installation id changes",
          AgentPackManager._pack_ref(m), AgentPackManager._pack_ref(dict(m)))
    check("R8 nameless manifest still yields a token",
          AgentPackManager._pack_ref({}), "agent_pack")

    # ── R9: a planner is keyed to the endpoint of the agent that READS it ──
    man = {"profiles": [
        {"tag": "NIBRSO", "profile_type": "tool_enabled",
         "collection_refs": ["collection_2"], "mcpServerName": "Teradata MCP - nibrs"},
        {"tag": "PDOCS", "profile_type": "rag_focused",
         "collection_refs": ["collection_1"]},
    ]}
    names = {"teradata mcp - nibrs": "server-NIBRS"}
    check("R9 keyed from the reader's server name",
          AgentPackManager._planner_mcp_for_ref("collection_2", man, names, None, "PACK"),
          "server-NIBRS")
    check("R9 an explicit tag binding wins over the name",
          AgentPackManager._planner_mcp_for_ref(
              "collection_2", man, names, {"NIBRSO": "server-EXPLICIT"}, "PACK"),
          "server-EXPLICIT")
    check("R9 falls back to the pack-level id when no reader is named",
          AgentPackManager._planner_mcp_for_ref("collection_9", man, names, None, "PACK"),
          "PACK")
    check("R9 a non-tool_enabled reader does not key a planner",
          AgentPackManager._planner_mcp_for_ref("collection_1", man, names, None, "PACK"),
          "PACK")

    # ── R10: a freshly installed planner is stamped, so the next update can clean it ──
    fresh = FakeStore([_case("a", "q1", "S1"), _case("b", "q2", "S2")])
    n = asyncio.new_event_loop().run_until_complete(
        mgr._stamp_planner_ownership(11, {"collection_name": "n3"}, "PACKX",
                                     retriever=FakeRetriever(fresh, "n3")))
    check("R10 every installed case is stamped", n, 2)
    check("R10 stamp value is the pack token",
          fresh.rows["a"]["metadata"].get("uderia_pack_ref"), "PACKX")
    check("R10 stamping preserves the case content",
          fresh.rows["b"]["metadata"].get("sql_statement"), "S2")
    n0 = asyncio.new_event_loop().run_until_complete(
        mgr._stamp_planner_ownership(12, {"collection_name": "n4"}, "PACKX",
                                     retriever=FakeRetriever(FakeStore([]), "n4")))
    check("R10 an empty planner stamps nothing and does not raise", n0, 0)

    # install-then-drop is now cleanable: stamped cases the pack stops shipping go
    z3 = _zip_with([], os.path.join(tmp, "empty2.zip"))
    s4 = _patch(mgr, collection_id=11, collection_meta={"collection_name": "n3"},
                inner_zip=z3, pack_ref="PACKX", retriever=FakeRetriever(fresh, "n3"))
    check("R10 install-then-drop is cleanable after stamping", s4["removed"], 2)

    try:
        run_disk_mirror()
    except Exception as exc:  # a gate that dies without naming what broke is half a gate
        FAIL += 1
        print(f"  FAIL run_disk_mirror raised {type(exc).__name__}: {exc}")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0

def run_disk_mirror():
    """The disk mirror: a pack-installed planner must be VISIBLE TO THE TRUST HASH.

    A planner repository is stored twice — ChromaDB serves retrieval, the on-disk
    ``case_*.json`` files are the source of truth and are what ``trust.content``
    hashes. The platform's own seeding path writes both; pack import wrote only
    ChromaDB, so on the reference deployment a consumer's copy of a signed agent's
    planner was invisible to its composition: the hashes could never agree, the
    honesty gate refused to reinstate the publisher's signature, and the agent sat
    ``unsigned`` — which its non-admin owner had no way to fix, because sign-off is
    tier-gated. The rules:

      D1  a fresh install writes a case file per case, carrying the ownership stamp
          (without the stamp on DISK the baseline formula is never selected)
      D2  the file names LOCAL truth — the consumer's collection, the endpoint the
          planner was re-keyed to, the consumer's uuid — because a case file naming
          the publisher's collection is a lie the feedback and maintenance paths act on
      D3  remove is mirrored too — a file left behind is one ``_run_maintenance``
          re-adds to ChromaDB, silently undoing the update
      D4  publisher and consumer hash IDENTICALLY once both are mirrored — the
          property the whole change exists to produce
      D5  a mirror failure is soft: the import still succeeds
      D7  the store is reached by the collection ROW's name — the caller's hint is
          the display name on both create sites, and a lookup by it raises, which the
          soft-fail swallows into "stamped 0" (found live, 2026-09-07)
    """
    import pathlib, shutil
    from trusted_data_agent.core.trust import content as _content

    mgr = AgentPackManager.__new__(AgentPackManager)
    PACK = "Policing Data Products"
    root = tempfile.mkdtemp()
    orig = _utils.get_project_root
    try:
        _utils.get_project_root = lambda: pathlib.Path(root)

        # A case as the pack ships it: the ChromaDB metadata carries the publisher's
        # full case study, exactly as the export writes it.
        def _shipped(cid, q, sql, *, coll, srv, usr):
            """A case as a real export delivers it.

            Faithful in the dimension under test: the import re-keys ONLY
            ``mcp_server_id`` (collection_utils._add_batch_to_chroma), so the consumer's
            row still carries the PUBLISHER's collection id and owner in both the flat
            metadata and the carried case study. Handing the fake the consumer's values
            would let the mirror look correct while doing nothing — the mirror's whole
            job on those two fields is to overwrite what the export brought."""
            study = {"case_id": cid.replace("case_", ""),
                     "metadata": {"collection_id": 382, "mcp_server_id": "server-PUB",
                                  "user_uuid": "publisher-uuid", "is_most_efficient": True,
                                  "sql_statement": sql, "user_query": q},
                     "intent": {"user_query": q},
                     "successful_strategy": {"phases": [{"goal": q}]}}
            return {"id": cid,
                    "metadata": {"user_query": q, "sql_statement": sql,
                                 "collection_id": 382,          # the publisher's, verbatim
                                 "mcp_server_id": srv,          # re-keyed by the import
                                 "user_uuid": "publisher-uuid",  # the publisher's, verbatim
                                 "full_case_data": json.dumps(study)},
                    "document": q}

        # ── D1/D2: the fresh-install stamp path mirrors to disk ──
        rows = [_shipped("case_aaa", "how many victims?", "SELECT 1",
                         coll=521, srv="server-CONSUMER", usr="demo-uuid")]
        store = FakeStore(rows)
        n = asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(521, {"collection_name": "n"}, PACK,
                                         retriever=FakeRetriever(store, "n")))
        check("D1 the install stamps every case", n, 1)
        cdir = pathlib.Path(root) / "rag/tda_rag_cases/collection_521"
        files = sorted(cdir.glob("case_*.json")) if cdir.is_dir() else []
        check("D1 a case file reaches disk", len(files), 1)
        # Read defensively: when D1 fails there is no file, and the assertions after it
        # (D4 above all) still have to be evaluated rather than lost to an IndexError.
        disk = json.loads(files[0].read_text(encoding="utf-8")) if files else {"metadata": {}}
        check("D1 the ownership stamp reaches DISK (the hash reads disk)",
              disk["metadata"].get("uderia_pack_ref"), PACK)
        check("D2 the file names the CONSUMER's collection, not the publisher's",
              disk["metadata"].get("collection_id"), 521)
        check("D2 the file names the endpoint the planner was re-keyed to",
              disk["metadata"].get("mcp_server_id"), "server-CONSUMER")
        # NOT rewritten to the installing user, deliberately: a pack's cases are seeded
        # through the template generator and carry the TEMPLATE uuid, which the
        # owned-collection filter ORs in to make them retrievable for ANY consumer.
        # Rewriting it here would be re-synced into ChromaDB by the next maintenance
        # pass and silently narrow retrieval to one user.
        check("D2 the owner is preserved verbatim, never invented",
              disk["metadata"].get("user_uuid"), "publisher-uuid")
        check("D2 the shipped content survives the mirror",
              disk.get("successful_strategy", {}).get("phases", [{}])[0].get("goal"),
              "how many victims?")
        cases, h = _content.planner_cases("521")
        check("D1 the mirrored repository is now visible to the trust hash",
              bool(h) and len(cases) == 1, True)

        # ── D4: the publisher's own repository hashes the SAME ──
        pubstore = FakeStore([_shipped("case_aaa", "how many victims?", "SELECT 1",
                                       coll=382, srv="server-PUB", usr="publisher-uuid")])
        # NB the two stores now differ on disk in exactly the fields the import rewrites —
        # which is what makes D4 a real test of the canonicalization rather than of a copy.
        asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(382, {"collection_name": "p"}, PACK,
                                         retriever=FakeRetriever(pubstore, "p")))
        _pc, hp = _content.planner_cases("382")
        check("D4 publisher and consumer hash identically — the signature reinstates",
              hp == h, True)

        # ...and the guarantee is not vacuous: real content still moves it
        edited = FakeStore([_shipped("case_aaa", "how many victims?", "SELECT EDITED",
                                     coll=999, srv="server-X", usr="u")])
        asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(999, {"collection_name": "e"}, PACK,
                                         retriever=FakeRetriever(edited, "e")))
        _ec, he = _content.planner_cases("999")
        check("D4 an edited shipped case still breaks the match", he != hp, True)

        # ── D3: add / update / remove all reach disk ──
        z = _zip_with([
            {"id": "case_aaa", "metadata": {"user_query": "how many victims?",
                                            "sql_statement": "SELECT 2"},
             "document": "how many victims?"},
            {"id": "case_new", "metadata": {"user_query": "new question",
                                            "sql_statement": "SELECT 3"},
             "document": "new question"},
        ], os.path.join(root, "p.zip"))
        store.rows["case_drop"] = {"id": "case_drop", "document": "dropped",
                                   "metadata": {"user_query": "dropped",
                                                "sql_statement": "S", "uderia_pack_ref": PACK}}
        mgr._mirror_planner_case_to_disk(521, "case_drop",
                                         store.rows["case_drop"]["metadata"], "dropped")
        check("D3 the case to be dropped is on disk before the update",
              (cdir / "case_drop.json").exists(), True)
        _patch(mgr, collection_id=521, collection_meta={"collection_name": "n"},
               inner_zip=z, pack_ref=PACK, retriever=FakeRetriever(store, "n"))
        check("D3 an added case reaches disk", (cdir / "case_new.json").exists(), True)
        check("D3 a removed case's file is deleted (never left for maintenance to re-add)",
              (cdir / "case_drop.json").exists(), False)
        # D6: an install made BEFORE the mirror existed is healed by the update — its
        # cases are stamped in ChromaDB but absent from disk, so the repository is
        # invisible to the trust hash and its agent cannot be signed. An unchanged case
        # must therefore still be mirrored, or the update leaves that state in place.
        legacy = FakeStore([_shipped("case_old", "legacy question", "SELECT 7",
                                     coll=382, srv="server-CONSUMER", usr="p")])
        legacy.rows["case_old"]["metadata"]["uderia_pack_ref"] = PACK   # stamped, no file
        zl = _zip_with([{"id": "case_old",
                         "metadata": {"user_query": "legacy question",
                                      "sql_statement": "SELECT 7"},
                         "document": "legacy question"}], os.path.join(root, "l.zip"))
        ldir = pathlib.Path(root) / "rag/tda_rag_cases/collection_777"
        check("D6 the pre-mirror install has no case file to begin with",
              ldir.exists(), False)
        _patch(mgr, collection_id=777, collection_meta={"collection_name": "l"},
               inner_zip=zl, pack_ref=PACK, retriever=FakeRetriever(legacy, "l"))
        check("D6 an unchanged case is still mirrored, healing a pre-mirror install",
              (ldir / "case_old.json").exists(), True)
        _lc, lh = _content.planner_cases("777")
        check("D6 the healed repository is visible to the trust hash", bool(lh), True)

        upd = json.loads((cdir / "case_aaa.json").read_text(encoding="utf-8"))
        check("D3 an updated case's file carries the new SQL",
              upd["metadata"].get("sql_statement"), "SELECT 2")

        # ── E: an update converges the consumer on the publisher's baseline ──
        # The change detection used to compare metadata["sql_statement"], which a REAL
        # export carries EMPTY (all 14 cases of the reference pack — the SQL lives in
        # the carried case study). So every case compared equal, counted "unchanged",
        # and a rewritten champion case never reached a consumer: the publisher signed a
        # baseline the consumer could never hold, and the two hashes could never agree.
        def _row(cid, q, sql, *, coll, srv):
            r = _shipped(cid, q, sql, coll=coll, srv=srv, usr="p")
            r["metadata"].pop("sql_statement", None)   # as a real export delivers it
            return r

        cons = FakeStore([_row("case_z", "shared question", "SELECT OLD",
                               coll=382, srv="server-CONSUMER")])
        asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(600, {"collection_name": "c6"}, PACK,
                                         retriever=FakeRetriever(cons, "c6")))
        _c0, h_old = _content.planner_cases("600")

        # the publisher rewrites that case and re-signs
        pub2 = FakeStore([_row("case_z", "shared question", "SELECT NEW",
                               coll=382, srv="server-PUB")])
        asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(601, {"collection_name": "c7"}, PACK,
                                         retriever=FakeRetriever(pub2, "c7")))
        _c1, h_pub = _content.planner_cases("601")
        check("E the rewritten baseline really is a different hash", h_pub != h_old, True)

        newpack = _row("case_z", "shared question", "SELECT NEW",
                       coll=382, srv="server-PUB")
        zc = _zip_with([newpack], os.path.join(root, "c.zip"))
        st = _patch(mgr, collection_id=600, collection_meta={"collection_name": "c6"},
                    inner_zip=zc, pack_ref=PACK, retriever=FakeRetriever(cons, "c6"))
        check("E a rewritten case is UPDATED, not counted unchanged", st["updated"], 1)
        check("E ...and is not also counted as unchanged", st["unchanged"], 0)
        _c2, h_new = _content.planner_cases("600")
        check("E the consumer converges on the publisher's new baseline",
              h_new == h_pub, True)

        # no churn: an identical case must still read unchanged
        st2 = _patch(mgr, collection_id=600, collection_meta={"collection_name": "c6"},
                     inner_zip=zc, pack_ref=PACK, retriever=FakeRetriever(cons, "c6"))
        check("E an identical case is still unchanged (no churn)", st2["updated"], 0)
        check("E ...and stays counted as unchanged", st2["unchanged"], 1)

        # A consumer's OWN operational state must not read as a change. `update_case_feedback`
        # and champion re-election rewrite `full_case_data` in place, so after a vote the
        # consumer's carried study genuinely differs from the publisher's — on fields that
        # say nothing about what the pack shipped. Comparing the study RAW would report
        # "changed" on every update from then on, rewriting the case and resetting the
        # consumer's own vote each time.
        voted = json.loads(cons.rows["case_z"]["metadata"]["full_case_data"])
        voted["metadata"]["user_feedback_score"] = -1
        voted["metadata"]["is_most_efficient"] = False
        cons.rows["case_z"]["metadata"]["full_case_data"] = json.dumps(voted)
        st4 = _patch(mgr, collection_id=600, collection_meta={"collection_name": "c6"},
                     inner_zip=zc, pack_ref=PACK, retriever=FakeRetriever(cons, "c6"))
        check("E a consumer's own vote is not mistaken for a content change",
              st4["updated"], 0)
        check("E ...and the case still reads unchanged", st4["unchanged"], 1)

        # back-compat: with no carried study, the old field comparison still decides
        legacy_pair = FakeStore([{"id": "case_l", "document": "q",
                                  "metadata": {"user_query": "q", "sql_statement": "A",
                                               "uderia_pack_ref": PACK}}])
        zlg = _zip_with([{"id": "case_l", "document": "q",
                          "metadata": {"user_query": "q", "sql_statement": "B"}}],
                        os.path.join(root, "lg.zip"))
        st3 = _patch(mgr, collection_id=602, collection_meta={"collection_name": "c8"},
                     inner_zip=zlg, pack_ref=PACK,
                     retriever=FakeRetriever(legacy_pair, "c8"))
        check("E with no carried study the field comparison still decides",
              st3["updated"], 1)

        # ── D7: the store is reached by the ROW's name, never the caller's hint ──
        # Live (2026-09-07, local rehearsal): `import_collection_from_zip` returns the
        # DISPLAY name as `collection_name`; both create sites handed it to the stamp;
        # ChromaDB raised "Collection [Policing Data Products — Planner] does not
        # exist"; the soft-fail logged "stamped 0 case(s)" — on every fresh install ever
        # made. The consumer's planner had 14 cases in ChromaDB and 0 files on disk, and
        # @NIBRSO stayed `unsigned`: the reported bug, intact, one door further in.
        fake_db.rows[23] = {"id": 23, "collection_name": "col_ea3ce2f9_1788782846_f15aa6",
                            "name": "Policing Data Products — Planner"}
        d7rows = [_shipped("case_d7a", "how many arrests?", "SELECT 7",
                           coll=23, srv="server-CONSUMER", usr="demo-uuid"),
                  _shipped("case_d7b", "how many offenders?", "SELECT 8",
                           coll=23, srv="server-CONSUMER", usr="demo-uuid")]
        d7store = FakeStore(d7rows)
        d7ret = FakeRetriever(d7store, "col_ea3ce2f9_1788782846_f15aa6")
        n7 = asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(
                23, {"collection_name": "Policing Data Products — Planner"}, PACK,
                retriever=d7ret))
        check("D7 the stamp reaches the store by the ROW's name when the caller hands "
              "the display name (the shape both create sites use)",
              d7ret.asked_for, "col_ea3ce2f9_1788782846_f15aa6")
        check("D7 every case is stamped — not 'stamped 0' behind a swallowed raise",
              n7, 2)
        d7dir = pathlib.Path(root) / "rag/tda_rag_cases/collection_23"
        check("D7 the mirror wrote a file per case", len(list(d7dir.glob("case_*.json"))), 2)
        st7 = _patch(mgr, collection_id=23,
                     collection_meta={"collection_name": "Policing Data Products — Planner"},
                     inner_zip=_zip_with(d7rows, os.path.join(root, "d7.zip")),
                     pack_ref=PACK, retriever=d7ret)
        check("D7 the update path resolves the same way (one mechanism, both callers)",
              (st7["unchanged"], st7["added"]), (2, 0))
        # ...and a row the DB does not have still honours the hint (a double, a race).
        d7bret = FakeRetriever(FakeStore([]), "hint-only")
        n7b = asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(900, {"collection_name": "hint-only"}, PACK,
                                         retriever=d7bret))
        check("D7 with no row the hint is the fallback, not an error",
              (d7bret.asked_for, n7b), ("hint-only", 0))

        # ── D8: the PUBLISHER's registration stamps its own planner repositories ──
        # The consumer's install is stamped (baseline formula); a publisher registering
        # the same live resources through `create_and_install` was not, so it signed the
        # legacy formula and the signature could never reinstate (found live). Asserted
        # on the AST — a comment naming the call must not satisfy it.
        import ast as _ast, inspect as _inspect, textwrap as _tw
        _tree = _ast.parse(_tw.dedent(_inspect.getsource(AgentPackManager.create_and_install)))
        _calls = {n.func.attr for n in _ast.walk(_tree)
                  if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)}
        check("D8 create_and_install stamps the planner repositories it records",
              "_stamp_planner_ownership" in _calls, True)
        check("D8 ...with the pack's own ref, the token the consumer stamps with",
              "_pack_ref" in _calls, True)

        # ── L: the lock travels with the pack, and is never written by the learning loop ──
        import ast as _ast2, inspect as _inspect2, textwrap as _tw2
        _exp = _inspect2.getsource(AgentPackManager.export_pack)
        check("L the export carries the planner's lock as the publisher's intent",
              'mc_entry["locked"] = bool(coll_meta.get("trust_locked"))' in _exp, True)
        _apm_src = _inspect2.getsource(AgentPackManager)
        _tree = _ast2.parse(_tw2.dedent(_apm_src))
        _lock_calls = [n for n in _ast2.walk(_tree) if isinstance(n, _ast2.Call)
                       and isinstance(n.func, _ast2.Attribute) and n.func.attr == "_apply_planner_lock"]
        check("L the lock is applied at the fresh install, the in-place create and the in-place patch",
              len(_lock_calls), 3)
        _uip = _inspect2.getsource(AgentPackManager._handle_update_in_place)
        check("L an explicit unlock on a locked repository is a posture downgrade",
              "locked → unlocked" in _uip and 'if "locked" not in coll_entry or coll_entry.get("locked"):' in _uip, True)
        # the helper: absence preserves, an explicit value is applied
        _writes = []
        class _LockCDB:
            def update_collection(self, cid, upd): _writes.append((cid, upd)); return True
        _orig = _cdb.get_collection_db
        try:
            _cdb.get_collection_db = lambda: _LockCDB()
            mgr._apply_planner_lock(5, {"ref": "collection_2"})
            check("L absence of the key preserves the consumer's lock", _writes, [])
            mgr._apply_planner_lock(5, {"ref": "collection_2", "locked": True})
            mgr._apply_planner_lock(6, {"ref": "collection_2", "locked": False})
            check("L an explicit value is written", _writes, [(5, {"trust_locked": 1}), (6, {"trust_locked": 0})])
        finally:
            _cdb.get_collection_db = _orig
        # the learning loop
        from trusted_data_agent.agent import rag_retriever as _rr
        _seed = _inspect2.getsource(_rr.RAGRetriever.process_turn_for_rag)
        check("L the seeding loop reads the lock and treats a locked repository as not writable",
              'get("trust_locked")' in _seed and "if _locked or not rag_context.validate_collection_access(collection_id, write=True):" in _seed, True)
        # the card
        _ui = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "js", "ui.js")).read()
        check("L the planner card shows a Locked pill", "col.repository_type === 'planner' && col.trust_locked" in _ui and "> Locked`" in _ui, True)
        check("L ...and an owner-only Lock/Unlock toggle that writes trust_locked",
              "dataset.action = 'toggle-trust-lock'" in _ui and "JSON.stringify({ trust_locked: !col.trust_locked })" in _ui
              and "col.repository_type === 'planner' && col.is_owned" in _ui, True)
        # the toggle is the PLANNER card's alone — the API refuses trust_locked for a knowledge
        # repository, and a stray `lockBtn` reference in createKnowledgeRepositoryCard once threw
        # `lockBtn is not defined` for every user, blanking the whole knowledge-repositories tab
        _kcard = _ui[_ui.index("function createKnowledgeRepositoryCard("):_ui.index("function createCollectionCard(")]
        _kcode = "\n".join(l.split("//")[0] for l in _kcard.splitlines())
        check("L the knowledge-repository card never references the planner card's lock toggle", "lockBtn" not in _kcode, True)

        # ── K: a case reaches the consumer under the CONSUMER's server key ──
        # Found 2026-09-16 on the demo fork (local and production): the 10 seeds a
        # top-up added arrived carrying the publisher's `mcp_server_id`; retrieval
        # filters every case on the key of the collection it reads (the safety layer in
        # `retrieve_examples`), so all 10 were stored and none was ever retrieved — the
        # seeded lane improvised the very statements the seeds existed to supply. The
        # fresh install has always re-keyed at `_add_batch_to_chroma`; the patch path
        # did not. ONE rule now (collection_utils.stamp_planner_server_key), three
        # sites: add, update, and a HEAL of rows an earlier update mis-keyed.
        from trusted_data_agent.core import collection_utils as _cu
        _m = [{"mcp_server_id": "server-PUB"}, {"mcp_server_id": "server-CONSUMER"}, {}]
        check("K the rule re-keys only what differs, and reports how many",
              _cu.stamp_planner_server_key(_m, "server-CONSUMER"), 2)
        check("K ...every row now carries the target key",
              [x.get("mcp_server_id") for x in _m], ["server-CONSUMER"] * 3)
        check("K ...and with no target key it is a no-op (a fresh collection has none yet)",
              _cu.stamp_planner_server_key([{"mcp_server_id": "server-PUB"}], None), 0)
        # fresh-install path calls the same rule (structural: no second copy)
        _cu_src = open(_cu.__file__).read()
        check("K the fresh install stamps through the shared rule, not an inline copy",
              _cu_src.count("stamp_planner_server_key(") >= 2
              and 'meta["mcp_server_id"] = mcp_server_id' not in
              _cu_src[_cu_src.index("def _add_batch_to_chroma("):], True)

        kcons = FakeStore([
            # an original seed, correctly keyed at install
            _row("case_k1", "k one", "SELECT 1", coll=382, srv="server-CONSUMER"),
            # a seed an earlier update wrote under the PUBLISHER's key (the live state)
            _row("case_k2", "k two", "SELECT 2", coll=382, srv="server-PUB"),
        ])
        asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(700, {"collection_name": "k7"}, PACK,
                                         retriever=FakeRetriever(kcons, "k7")))
        kpack = [_row("case_k1", "k one", "SELECT 1", coll=382, srv="server-PUB"),
                 _row("case_k2", "k two", "SELECT 2", coll=382, srv="server-PUB"),
                 _row("case_k3", "k three", "SELECT 3", coll=382, srv="server-PUB"),   # add
                 ]
        zk = _zip_with(kpack, os.path.join(root, "k.zip"))
        kmeta = {"collection_name": "k7", "mcp_server_id": "server-CONSUMER"}
        stk = _patch(mgr, collection_id=700, collection_meta=kmeta,
                     inner_zip=zk, pack_ref=PACK, retriever=FakeRetriever(kcons, "k7"))
        check("K an ADDED case lands under the consumer's key",
              kcons.rows["case_k3"]["metadata"].get("mcp_server_id"), "server-CONSUMER")
        check("K ...and is counted as added", stk["added"], 1)
        check("K a mis-keyed existing case is HEALED on the unchanged path",
              kcons.rows["case_k2"]["metadata"].get("mcp_server_id"), "server-CONSUMER")
        check("K ...and the heal is reported", stk.get("rekeyed"), 1)
        check("K a correctly keyed case is untouched", 
              kcons.rows["case_k1"]["metadata"].get("mcp_server_id"), "server-CONSUMER")
        check("K the heal is not mistaken for a content change", stk["updated"], 0)
        # the update path: a rewritten statement arrives under the publisher's key too
        kpack2 = [_row("case_k1", "k one", "SELECT 1 /* v2 */", coll=382, srv="server-PUB"),
                  _row("case_k2", "k two", "SELECT 2", coll=382, srv="server-PUB"),
                  _row("case_k3", "k three", "SELECT 3", coll=382, srv="server-PUB")]
        zk2 = _zip_with(kpack2, os.path.join(root, "k2.zip"))
        stk2 = _patch(mgr, collection_id=700, collection_meta=kmeta,
                      inner_zip=zk2, pack_ref=PACK, retriever=FakeRetriever(kcons, "k7"))
        check("K an UPDATED case lands under the consumer's key", 
              kcons.rows["case_k1"]["metadata"].get("mcp_server_id"), "server-CONSUMER")
        check("K ...and is counted as updated", stk2["updated"], 1)
        check("K a second run heals nothing (idempotent)", stk2.get("rekeyed"), 0)
        # the key is instance-local: re-keying moves no signature
        _ck, h_k = _content.planner_cases("700")
        kcons2 = FakeStore([dict(r, metadata=dict(r["metadata"], mcp_server_id="server-OTHER"))
                            for r in kcons.rows.values()])
        # (mirror the other-key copy to a second collection and compare)
        for r in kcons2.rows.values():
            mgr._mirror_planner_case_to_disk(701, r["id"], r["metadata"], r["document"])
        _ck2, h_k2 = _content.planner_cases("701")
        check("K the server key never enters the content hash (re-keying moves no seal)",
              h_k == h_k2, True)

        # ── D5: a mirror failure is soft ──
        _utils.get_project_root = lambda: pathlib.Path("/dev/null/nope")
        ok = mgr._mirror_planner_case_to_disk(521, "case_x", {"user_query": "q"}, "q")
        check("D5 an unwritable mirror reports failure rather than raising", ok, False)
        _utils.get_project_root = lambda: pathlib.Path(root)
        n2 = asyncio.new_event_loop().run_until_complete(
            mgr._stamp_planner_ownership(
                521, {"collection_name": "n"}, PACK, retriever=FakeRetriever(store, "n")))
        check("D5 the install still succeeds after a mirror failure", n2 > 0, True)
    finally:
        _utils.get_project_root = orig
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    try:
        rc = run()
    finally:
        import shutil as _shutil
        _shutil.rmtree(_GATE_ROOT, ignore_errors=True)
    sys.exit(rc)
