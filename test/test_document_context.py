#!/usr/bin/env python3
"""
The document_context module is wired to the turn's attachments, with today's limits as the floor.

  A  the one limit rule (document_char_limit): the allocation, never less than today's fixed limit
  B  the module sees the turn's attachments and measures exactly the text the turn sends
  C  condensing never goes below the floor
  D  a document cut at its excerpt is completed by passages searched for the question — only
     from this turn's readable attachments, never a duplicate of the excerpt, within the room left
  E  the search-based condensation is clamped and filtered the same way
  F  purge drops only the search index — never the attachments (they are Files)
  G  the "no_documents_attached" rule reads the attachments; the engines take one delivery path

Run: python test/test_document_context.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

TMP = Path(tempfile.mkdtemp(prefix="uderia-docctx-"))
os.environ["TDA_FILES_DIR"] = str(TMP / "files")
os.environ["TDA_SESSIONS_DIR"] = str(TMP / "sessions")
os.environ["TDA_AUTH_DB_URL"] = f"sqlite:///{TMP / 'auth.db'}"
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
APP_CONFIG.AUTH_DB_PATH = f"sqlite:///{TMP / 'auth.db'}"
APP_CONFIG.ATTACHMENT_SOURCES = False
from trusted_data_agent.api import routes  # noqa: E402
from trusted_data_agent.auth import database as _authdb  # noqa: E402
assert str(TMP) in str(_authdb.DATABASE_URL), "the auth engine must not point at a real database"
from trusted_data_agent.auth.models import Base as _Base, User as _User  # noqa: E402
for _t in _Base.metadata.sorted_tables:
    try:
        _t.create(bind=_authdb.engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass
with _authdb.get_db_session() as _s:
    _s.add(_User(id="u-doc-1", username="u-doc-1", email="u-doc-1@example.com", password_hash="x", profile_tier="user"))

from trusted_data_agent.agent import executor as EX  # noqa: E402
from trusted_data_agent.filestore import service, session_files  # noqa: E402
from trusted_data_agent.filestore import settings as fset  # noqa: E402
from trusted_data_agent.vectorstore.types import VectorDocument  # noqa: E402
from components.builtin.context_window.module_registry import get_module_registry, reset_module_registry  # noqa: E402
from components.builtin.context_window.base import AssemblyContext  # noqa: E402
from components.builtin.context_window import session_vector_store as SVS  # noqa: E402

reset_module_registry()
MOD = get_module_registry().get_handler("document_context")

_passed = _failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def run(coro):
    return asyncio.run(coro)


USER, SID = "u-doc-1", "sess-doc-1"
FLOOR = APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS
PER_FILE = APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS


def ctx(attachments, query="what does the appendix say about penalties", store=None):
    c = AssemblyContext(profile_type="llm_only", profile_id="p1", session_id=SID, user_uuid=USER,
                        session_data={"attachments": attachments, "current_query": query})
    c.session_store = store
    return c


class FakeStore:
    """Stands in for the session's search index: has_data + retrieve + forget_module."""
    def __init__(self, docs, user=USER):
        self.docs, self._user_uuid, self.forgotten = docs, user, []

    def has_data(self, module_id):
        return module_id == "document_context" and bool(self.docs)

    async def retrieve(self, module_id, query, n_results=5):
        return self.docs[:n_results]

    async def forget_module(self, module_id):
        self.forgotten.append(module_id)
        return True


# ── A ────────────────────────────────────────────────────────────────────────
print("\nA  the one limit rule")
check("no allocation (a type with the module off, or the budget moved away): today's limit",
      EX.document_char_limit(0) == FLOOR and EX.document_char_limit(None) == FLOOR)
check("a small allocation never sends LESS than today", EX.document_char_limit(FLOOR // 8) == FLOOR)
check("a larger allocation sends more", EX.document_char_limit(30_000) == 120_000)

# ── B ────────────────────────────────────────────────────────────────────────
print("\nB  the module sees the turn's attachments and measures what is sent")
fset.save_settings({"retention_days": 30})
short = routes._ingest_upload(USER, SID, "memo.txt", b"Penalties are 5% of the contract value.")
head = "HEAD-SECTION " + ("intro text. " * 2000)                                   # > the per-file limit
tail = "\n\nAPPENDIX-PASSAGE: penalties double after the second breach."
long = routes._ingest_upload(USER, SID, "contract.txt", (head + tail).encode())
atts = [{"file_id": short["file_id"], "filename": "memo.txt"}, {"file_id": long["file_id"], "filename": "contract.txt"}]

empty = run(MOD.contribute(10_000, ctx([])))
check("no attachments: nothing, and says why", empty.content == "" and empty.metadata.get("reason") == "no_attachments")
c = run(MOD.contribute(0, ctx(atts)))
expected, events = EX.load_document_context(USER, SID, atts, max_total_chars=FLOOR)
check("the module's text IS the loader's text under the floor (what every engine sends)", c.content == expected, c.content[:200])
check("its tokens are counted (Live Status no longer shows 0 for an attached document)", c.tokens_used > 1000 and c.metadata["doc_count"] == 2)
check("the cut is reported as events for the trace", len(c.metadata["events"]) == len(events) >= 1)
service.soft_delete(short["file_id"], USER)
c2 = run(MOD.contribute(0, ctx(atts)))
check("a file deleted from Files is not measured nor sent", "Penalties are 5%" not in c2.content and c2.metadata["doc_count"] == 1)
service.restore(short["file_id"], USER)

# ── C ────────────────────────────────────────────────────────────────────────
print("\nC  condensing never goes below the floor")
small = run(MOD.contribute(0, ctx(atts)))
cond = run(MOD.condense(small.content, 100, ctx(atts)))
check("asked for 100 tokens, a document under today's limit is sent whole", cond.content == small.content)
huge = "\n\n".join(f"--- Document: f{i}.txt ---\n{'x' * 30_000}\n--- End of f{i}.txt ---" for i in range(4))
cut = run(MOD.condense(huge, 100, ctx(atts)))
check("over the limit: cut at a document boundary, and never under today's limit",
      FLOOR <= len(cut.content) < len(huge) and "--- End of f0.txt ---" in cut.content and cut.content.endswith("(documents truncated)"),
      len(cut.content))

# ── D ────────────────────────────────────────────────────────────────────────
print("\nD  the rest of a cut document is searched, not lost")
other = routes._ingest_upload(USER, "sess-doc-other", "other.txt", b"OTHER-TURN text")
docs = [
    VectorDocument(id="1", content=head[:400], metadata={"file_id": long["file_id"], "filename": "contract.txt", "chunk_num": 0}),
    VectorDocument(id="2", content="APPENDIX-PASSAGE: penalties double after the second breach.", metadata={"file_id": long["file_id"], "filename": "contract.txt", "chunk_num": 31}),
    VectorDocument(id="3", content="OTHER-TURN text", metadata={"file_id": other["file_id"], "filename": "other.txt", "chunk_num": 0}),
]
d = run(MOD.contribute(0, ctx(atts, store=FakeStore(docs))))
check("the passage from beyond the excerpt is added, labelled as searched", "APPENDIX-PASSAGE" in d.content
      and "Relevant passages from beyond the excerpt" in d.content and d.metadata["passages_retrieved"] == 1, d.content[-400:])
check("a passage already inside the excerpt is not repeated", d.content.count(head[:200]) == 1)
check("a passage from a file not attached to this turn is never used", "OTHER-TURN" not in d.content)
check("the passages stay within the limit", len(d.content) <= FLOOR + 200, len(d.content))
service.soft_delete(long["file_id"], USER)
d2 = run(MOD.contribute(0, ctx(atts, store=FakeStore(docs))))
check("a deleted file's passages are never used, even if the index still holds them", "APPENDIX-PASSAGE" not in d2.content)
service.restore(long["file_id"], USER)
nocut = run(MOD.contribute(0, ctx([atts[0]], store=FakeStore(docs))))
check("nothing was cut: no search, the document alone", "Relevant passages" not in nocut.content)
check("no index (the type truncates): the excerpt alone, as before", "Relevant passages" not in run(MOD.contribute(0, ctx(atts))).content)

# ── E ────────────────────────────────────────────────────────────────────────
print("\nE  search-based condensation: clamped and filtered")
within = run(MOD.condense_rag(small.content, 10, "q", ctx(atts), FakeStore(docs), 30))
check("under today's limit it is left whole", within.content == small.content and within.metadata.get("reason") == "within_floor")
over = run(MOD.condense_rag(huge, 10, "penalties", ctx(atts), FakeStore(docs), 30))
check("over it, results come only from this turn's readable attachments", "OTHER-TURN" not in over.content and "APPENDIX-PASSAGE" in over.content)

# ── F ────────────────────────────────────────────────────────────────────────
print("\nF  purge drops the search index, never the attachments")
before = session_files.load_manifest(USER, SID)
fake = FakeStore(docs)
SVS._SESSION_STORES[SID] = fake
res = run(MOD.purge(SID, USER))
check("the document index is dropped", res.get("purged") is True and fake.forgotten == ["document_context"])
check("the conversation's attachments are untouched (list and files)", session_files.load_manifest(USER, SID) == before
      and session_files.upload_dir(USER, SID).exists() and EX.load_document_context(USER, SID, atts)[0])
check("another user cannot drop it", run(MOD.purge(SID, "someone-else")).get("purged") is False)
SVS._SESSION_STORES.pop(SID, None)
real = SVS.SessionVectorStore(SID, USER)
real._ingested.add("document_context"); real._ingested_chars["document_context"] = 10
check("the store forgets one module's index (tracking cleared)", run(real.forget_module("document_context")) is True
      and not real.has_data("document_context"))

# ── G ────────────────────────────────────────────────────────────────────────
print("\nG  the rule, the hand-off and the one delivery path")
from components.builtin.context_window.handler import ContextWindowHandler  # noqa: E402
H = ContextWindowHandler()
check('"no_documents_attached" is false when the turn has attachments', H._evaluate_condition("no_documents_attached", ctx(atts), []) is False)
check("…and true when it has none", H._evaluate_condition("no_documents_attached", ctx([]), []) is True)
fake_ex = SimpleNamespace(attachments=atts, user_uuid=USER, session_id=SID, _cw_document_text="MODULE-TEXT", _cw_document_events=[{"x": 1}])
check("documents_for_turn returns the context window's text when assembly produced it",
      EX.PlanExecutor.documents_for_turn(fake_ex) == ("MODULE-TEXT", [{"x": 1}]))
fake_ex2 = SimpleNamespace(attachments=atts, user_uuid=USER, session_id=SID)
check("…else the loader under the one limit rule", EX.PlanExecutor.documents_for_turn(fake_ex2)[0] == EX.load_document_context(USER, SID, atts)[0])
SRC = {p: (ROOT / p).read_text() for p in ("src/trusted_data_agent/agent/executor.py",
                                            "src/trusted_data_agent/agent/engines/ideate_engine.py",
                                            "src/trusted_data_agent/agent/engines/focus_engine.py",
                                            "src/trusted_data_agent/agent/engines/coordinate_engine.py",
                                            "components/builtin/context_window/handler.py")}
ex_src = SRC["src/trusted_data_agent/agent/executor.py"]
check("the executor hands the turn's attachments to the assembly", "enriched_session['attachments'] = list(self.attachments or [])" in ex_src)
check("Coordinate hands them too (the rule reads them)", "enriched_session['attachments'] = list(attachments or [])" in SRC["src/trusted_data_agent/agent/engines/coordinate_engine.py"])
check("the search index is filled before the assembly that searches it (awaited, not fire-and-forget)",
      "await asyncio.wait_for(\n                            _ingest_documents_to_session_store(" in ex_src and "create_task(\n                        _ingest_documents_to_session_store" not in ex_src)
check("Ideate and Focus send the context window's document text (one delivery path)",
      "executor.documents_for_turn()" in SRC["src/trusted_data_agent/agent/engines/ideate_engine.py"]
      and "executor.documents_for_turn()" in SRC["src/trusted_data_agent/agent/engines/focus_engine.py"])
check("no site converts the allocation by hand any more — one rule (document_char_limit)",
      all("tokens_allocated * 4" not in s for s in SRC.values()))
check("the assembly hands its search index to the modules", "ctx.session_store = session_store" in SRC["components/builtin/context_window/handler.py"])

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
