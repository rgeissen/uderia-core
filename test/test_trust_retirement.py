"""#30 — a signature must not outlive its subject.

Pack uninstall deleted profiles, collections and KGs but never touched
`trust_attestations`. Three orphans were observed live, and the failure has two teeth:

  1. A dangling row is inheritable. Collection ids are small integers, so a LATER
     collection can land on a dead id and read 🟠 "signed then modified" — a history
     the new artifact never had (unsigned would be the truth).
  2. Content-bound signatures that legitimately survive (a builtin skill the uninstall
     does not delete) are BY DESIGN — the portability honesty gate re-applies a
     signature only where content is byte-identical. The defect is only the rows whose
     subject is GONE.

Fix: `trust_store.retire(artifact_id, kinds)` called from the artifact DELETION
chokepoints — profile removal (`config_manager.remove_profile`, which pack uninstall
and the REST delete endpoint both route through), collection removal
(`rag_retriever.remove_collection`), and both KG deletion sites (pack uninstall + the
REST endpoint, which has no shared chokepoint).

Pure: no server, no network.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.trust import store as trust_store            # noqa: E402
from trusted_data_agent.core.trust.attestation import resolve_state, TrustState  # noqa: E402

failures = []
_checks = 0


def check(name, got, want):
    global _checks
    _checks += 1
    if got != want:
        failures.append(f"{name}\n      got={got!r} want={want!r}")


V = {"signable": True, "band": "clean", "note": "ok"}

with tempfile.TemporaryDirectory() as d:
    db = os.path.join(d, "t.db")

    def _sign(kind, aid, h):
        trust_store.record_attestation(kind=kind, artifact_id=aid, version_hash=h,
                                       verdict=V, signer_uuid="u1", db_path=db)

    # ── 1 · retire removes exactly the named subject, across kinds ──────────
    _sign("knowledge", "9", "h-A")
    _sign("planner", "9", "h-B")      # same id signed under the other kind too
    _sign("knowledge", "10", "h-C")   # a NEIGHBOUR that must survive
    _sign("agent", "profile-x", "h-D")

    n = trust_store.retire("9", ("knowledge", "planner"), db_path=db)
    check("#30 both kinds of the deleted artifact retired", n, 2)
    check("#30 the deleted artifact reads unsigned",
          trust_store.load_attestation("knowledge", "9", db_path=db), None)
    check("#30 a neighbour's signature survives",
          (trust_store.load_attestation("knowledge", "10", db_path=db) or {}).get("version_hash"),
          "h-C")
    check("#30 an unrelated kind survives",
          (trust_store.load_attestation("agent", "profile-x", db_path=db) or {}).get("version_hash"),
          "h-D")

    # Absent rows are a clean no-op; the return value says nothing was there.
    check("#30 retiring an absent artifact is a no-op", trust_store.retire("404", ("knowledge",), db_path=db), 0)

    # ── 2 · the inheritance scenario the fix exists for ─────────────────────
    # A collection is signed, deleted (retired), and a NEW collection later lands on
    # the same integer id with different content. Without retirement the stale row
    # made it read CHANGED — "signed then modified" — instead of the truthful
    # UNSIGNED.
    _sign("knowledge", "12", "h-old-corpus")
    # (a) the stale-row world: state for new content under the old row
    stale = resolve_state("h-old-corpus", "h-new-corpus")
    check("#30 without retirement the heir reads CHANGED (the falsehood)",
          stale, TrustState.CHANGED)
    # (b) with retirement: no attestation ⇒ unsigned — the truth for a new artifact
    trust_store.retire("12", ("knowledge", "planner"), db_path=db)
    fresh = resolve_state(
        (trust_store.load_attestation("knowledge", "12", db_path=db) or {}).get("version_hash"),
        "h-new-corpus")
    check("#30 with retirement the heir reads UNSIGNED (the truth)",
          fresh, TrustState.UNSIGNED)

    # ── 3 · retire never raises — deletion must not fail on trust cleanup ───
    try:
        got = trust_store.retire("x", ("knowledge",), db_path=os.path.join(d, "nope", "missing.db"))
        check("#30 an unreachable store degrades to 0, never an exception", got, 0)
    except Exception as e:  # noqa: BLE001
        failures.append(f"#30 retire raised on unreachable store: {e}")

# ── 3b · the profile chokepoint EXECUTED, not just grepped ──────────────────
# A source-string assertion cannot tell `retire(...)` from `if False and retire(...)`
# (mutation-proven). So run the real `remove_profile` with the store stubbed: the
# late `from ... import store as _trust_store` resolves the module at call time, so
# patching the module attribute intercepts the genuine call path.
from trusted_data_agent.core.config_manager import ConfigManager  # noqa: E402


class _CM(ConfigManager):
    def __init__(self, profiles):  # noqa: D401 — hermetic: no disk, no auth DB
        self._cfg = {"profiles": profiles, "master_classification_profile_ids": {}}
        self._save_ok = True

    def load_config(self, user_uuid=None):
        return self._cfg

    def save_config(self, config, user_uuid=None):
        if self._save_ok:
            self._cfg = config
        return self._save_ok


_calls = []
_orig_retire = trust_store.retire
trust_store.retire = lambda aid, kinds, **k: (_calls.append((aid, tuple(kinds), k.get("owner_uuid"))), 1)[1]
try:
    ok = _CM([{"id": "p1"}]).remove_profile("p1", "alice")
    # PROP-009: the agent AND its deployment attestation retire, for THIS owner only
    check("#30 remove_profile EXECUTES the retirement (both agent kinds, owner-scoped)", (ok, _calls),
          (True, [("p1", ("agent", "agent_deployment"), "alice")]))

    _calls.clear()
    cm = _CM([{"id": "p1"}])
    cm._save_ok = False
    check("#30 a failed save retires nothing", (cm.remove_profile("p1"), _calls), (False, []))

    _calls.clear()
    check("#30 a missing profile retires nothing",
          (_CM([{"id": "other"}]).remove_profile("p1"), _calls), (False, []))
finally:
    trust_store.retire = _orig_retire

# ── 4 · the wiring: every deletion chokepoint retires (match the CALL) ──────
ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


cfg = _src("src", "trusted_data_agent", "core", "config_manager.py")
rag = _src("src", "trusted_data_agent", "agent", "rag_retriever.py")
pack = _src("src", "trusted_data_agent", "core", "agent_pack_manager.py")
rest = _src("src", "trusted_data_agent", "api", "rest_routes.py")

_retire_at = cfg.find('_trust_store.retire(profile_id, ("agent", "agent_deployment"),')
check("#30 profile removal retires the agent and deployment attestations", _retire_at > 0, True)
check("#30 …after the save succeeded, not before",
      0 < cfg.find("ok = self.save_config(config, user_uuid)") < _retire_at, True)
check("#30 collection removal retires both possible kinds",
      '_trust_store.retire(str(collection_id), ("knowledge", "planner"))' in rag, True)
check("#30 pack uninstall retires the KG attestation",
      '_trust_store.retire(kg_id, ("ontology",))' in pack, True)
check("#30 the REST KG delete retires too",
      '_trust_store.retire(resolved_kg_id, ("ontology",))' in rest, True)
skl = _src("src", "trusted_data_agent", "skills", "manager.py")
check("#30 skill deletion retires the skill attestation (string ids are re-used by name)",
      '_trust_store.retire(name, ("skill",))' in skl, True)
check("#30 ...after the directory is gone, not before",
      skl.find("shutil.rmtree(skill_dir)") < skl.find('_trust_store.retire(name, ("skill",))'), True)
check("#30 pack uninstall retires the pack's own seal",
      '_trust_store.retire(str(installation_id), ("agent_pack",))' in pack, True)
check("#30 ...after the installation row is deleted",
      pack.find('DELETE FROM agent_pack_installations WHERE id = ?')
      < pack.find('_trust_store.retire(str(installation_id), ("agent_pack",))'), True)
# Every site imports defensively — the Ontology Suite is physically removable.
for name, src in (("config_manager", cfg), ("rag_retriever", rag),
                  ("agent_pack_manager", pack), ("skills/manager", skl)):
    i = src.find("_trust_store.retire(")
    seg = src[max(0, i - 600):i]
    check(f"#30 {name} imports the store defensively",
          "from trusted_data_agent.core.trust import store as _trust_store" in seg
          and "except ImportError" in src[i:i + 400], True)

# ── 5 · the on-disk mirror dies with the collection (EXECUTED) ───────────────
# A planner's case_*.json files are a second copy of the store. remove_collection
# used to delete the store, the row and the attestation and leave the directory —
# an artefact of a collection that no longer exists (found on production after a
# pack removal: 16 files each under collection_527/ and collection_529/).
import asyncio  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
import trusted_data_agent.agent.rag_retriever as _rr  # noqa: E402
import trusted_data_agent.core.collection_db as _cdb  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.deleted = []

    def delete_collection(self, name):
        self.deleted.append(name)


class _FakeCollDB:
    def __init__(self):
        self.deleted = []

    def delete_collection(self, cid):
        self.deleted.append(int(cid))
        return True


class _FakeCM:
    def get_rag_collections(self):
        return []


with tempfile.TemporaryDirectory() as _d:
    cases = Path(_d) / "tda_rag_cases"
    mirror = cases / "collection_527"
    mirror.mkdir(parents=True)
    (mirror / "case_a.json").write_text("{}")
    (mirror / "case_b.json").write_text("{}")
    other = cases / "collection_528"
    other.mkdir()
    (other / "case_c.json").write_text("{}")

    r = _rr.RAGRetriever.__new__(_rr.RAGRetriever)
    r.rag_cases_dir = cases
    r.collections = {}
    r._knowledge_backends = {}
    r.client = _FakeClient()
    r.get_collection_metadata = lambda cid: {"id": 527, "collection_name": "col_527",
                                             "repository_type": "planner",
                                             "backend_type": "chromadb"}
    r._is_chromadb_backend = lambda meta: True
    r._get_user_default_collection_id = lambda uid: None
    _fake_db = _FakeCollDB()
    _o_get_db, _o_get_cm, _o_retire = _cdb.get_collection_db, _rr.get_config_manager, trust_store.retire
    _cdb.get_collection_db = lambda: _fake_db
    _rr.get_config_manager = lambda: _FakeCM()
    trust_store.retire = lambda aid, kinds, **k: 0
    try:
        ok = asyncio.run(r.remove_collection(527))
    finally:
        _cdb.get_collection_db, _rr.get_config_manager, trust_store.retire = _o_get_db, _o_get_cm, _o_retire
    check("mirror: remove_collection succeeds", ok, True)
    check("mirror: the store and the row are deleted",
          (r.client.deleted, _fake_db.deleted), (["col_527"], [527]))
    check("mirror: the collection's on-disk case directory is removed", mirror.exists(), False)
    check("mirror: another collection's directory is untouched",
          sorted(p.name for p in other.iterdir()), ["case_c.json"])

# ── report ──────────────────────────────────────────────────────────────────
print()
if failures:
    for f in failures:
        print(f"  ✗ {f}")
    print(f"{_checks - len(failures)} passed, {len(failures)} failed  ({_checks} assertions)")
    sys.exit(1)
print(f"{_checks} passed, 0 failed  ({_checks} assertions)  — #30: a signature does not outlive its subject.")
