#!/usr/bin/env python3
"""
A Focus agent answers a question about an attached document even when its knowledge
repositories hold nothing on it — and the knowledge-sufficiency honesty check reads the
attachment as material the answer may draw on.

  A  attached_material: the document text the turn sends, S9 image readings, an image seen
     natively (no text) flagged unreadable, a file deleted from Files never counted
  B  the honesty scan: no attachment ⇒ byte-identical; a claim the attachment carries is not a
     finding; a claim it does not carry still is; an unreadable image ⇒ no finding
  C  run_honesty_rag threads the attachment through both of its assessment paths
  D  the Focus engine: the early "No relevant knowledge found" answer only when nothing usable
     was attached; otherwise synthesis, told the repositories held nothing, and the retrieval
     event says it answered from the attachment
  E  the grounding gate counts the attached text as evidence — on a turn it grades anyway;
     attachments alone never make a turn groundable
  F  a REST turn's attachment chip carries the platform's own facts (size, image or text)
  G  the after-the-turn scores read the attachments the turn read — through the guarded
     manifest, abstaining in one wording once an attachment was deleted

Run: python test/test_focus_attachments.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

TMP = Path(tempfile.mkdtemp(prefix="uderia-focusatt-"))
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
    _s.add(_User(id="u-fa-1", username="u-fa-1", email="u-fa-1@example.com", password_hash="x", profile_tier="user"))

from trusted_data_agent.agent import executor as EX  # noqa: E402
from trusted_data_agent.agent.honesty import gate as G  # noqa: E402
from trusted_data_agent.agent.honesty import runtime as R  # noqa: E402
from trusted_data_agent.filestore import service  # noqa: E402
from trusted_data_agent.filestore import settings as fset  # noqa: E402

_passed = _failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


USER, SID = "u-fa-1", "sess-fa-1"
DOC = ("Delivery terms. The supplier pays a penalty of 7.25 percent of the order value per week "
       "of delay, capped at 40 percent. Invoices are due within 30 days.")


def fake(atts, **kw):
    """A PlanExecutor-shaped stand-in carrying only what attached_material reads."""
    ns = SimpleNamespace(attachments=atts, user_uuid=USER, session_id=SID, multimodal_content=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    ns.documents_for_turn = lambda: EX.PlanExecutor.documents_for_turn(ns)
    return ns


def material(ns):
    return EX.PlanExecutor.attached_material(ns)


# ── A ────────────────────────────────────────────────────────────────────────
print("\nA  attached_material")
fset.save_settings({"retention_days": 30})
doc = routes._ingest_upload(USER, SID, "contract.txt", DOC.encode())
img = routes._ingest_upload(USER, SID, "photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
d_att = [{"file_id": doc["file_id"], "filename": "contract.txt"}]
i_att = [{"file_id": img["file_id"], "filename": "photo.png"}]

check("nothing attached: nothing to answer from", material(fake([])) == ((), False))
texts, unread = material(fake(d_att))
check("a document: the text the turn sends", len(texts) == 1 and "7.25 percent" in texts[0] and unread is False, texts)
texts, _ = material(fake(d_att, _cw_document_text="MODULE-TEXT 7.25"))
check("…the context window's text when assembly produced it (what the model was shown)", texts == ("MODULE-TEXT 7.25",))
texts, _ = material(fake(d_att, _attachment_sources_text="S9 reading: total 412"))
check("S9's image readings count as text", any("total 412" in t for t in texts) and len(texts) == 2)
check("an image the model saw natively, no text: unreadable",
      material(fake(i_att, multimodal_content=[{"type": "image"}])) == ((), True))
from trusted_data_agent.filestore import session_files  # noqa: E402
_real = session_files.readable_manifest
def _s9_read(u, s):
    m = _real(u, s) or {}
    e = dict(m[img["file_id"]]); e["source"] = {"verified": True}; e["extracted_text"] = "S9 reading: total 412"
    return {**m, img["file_id"]: e}
session_files.readable_manifest = _s9_read
t9, u9 = material(fake(i_att, multimodal_content=[{"type": "image"}], _attachment_sources_text="S9 reading: total 412"))
session_files.readable_manifest = _real
check("…but not when S9 read it (then it is text)", u9 is False and any("total 412" in t for t in t9), (t9, u9))
check("an image's placeholder text is never counted as a document's text",
      material(fake(i_att, multimodal_content=[{"type": "image"}]))[0] == ())
check("an image the provider never received: nothing to answer from",
      material(fake(i_att)) == ((), False))
service.soft_delete(doc["file_id"], USER)
check("a document deleted from Files is never counted", material(fake(d_att)) == ((), False))
service.restore(doc["file_id"], USER)

# ── B ────────────────────────────────────────────────────────────────────────
print("\nB  the knowledge-sufficiency scan reads the attachment")
ANS_OK = "The penalty is 7.25 percent of the order value per week, capped at 40 percent."
ANS_BAD = "The penalty is 9.5 percent of the order value per week, capped at 65 percent."
base = G.scan_rag_sufficiency(ANS_OK, qualifying_chunks=0, searched=True)
check("no attachment: unchanged — a specific answer with no knowledge is a finding",
      base.triggered and base.why == "retrieval found no qualifying content")
same = G.scan_rag_sufficiency(ANS_OK, qualifying_chunks=0, searched=True, support_texts=(), unreadable_material=False)
check("…and the new arguments at their defaults are byte-identical", same == base)
ok = G.scan_rag_sufficiency(ANS_OK, qualifying_chunks=0, searched=True, support_texts=(DOC,))
check("the attachment carries every specific: no finding", not ok.triggered and ok.claims_checked == 0)
bad = G.scan_rag_sufficiency(ANS_BAD, qualifying_chunks=0, searched=True, support_texts=(DOC,))
check("a specific the attachment does not carry: still a finding, and says so",
      bad.triggered and bad.why.endswith("and the attached documents do not carry them"), bad)
v = G.decide_rag(bad)
check("…its reason names both", v is not None and "attached documents do not carry them" in v.reason, v and v.reason)
img_f = G.scan_rag_sufficiency(ANS_BAD, qualifying_chunks=0, searched=True, unreadable_material=True)
check("an image no check can read: no finding (no specific can be called unsupported)",
      not img_f.triggered and "image" in img_f.why)
check("retrieval that found enough is untouched by the attachment",
      not G.scan_rag_sufficiency(ANS_BAD, qualifying_chunks=3, searched=True, support_texts=(DOC,)).weak)

# ── C ────────────────────────────────────────────────────────────────────────
print("\nC  run_honesty_rag threads the attachment")
PROF = {"honestyConfig": {"mode": "enforce"}}
text, ev = R.run_honesty_rag(ANS_OK, qualifying_chunks=0, searched=True, profile=PROF)
check("enforce, no attachment: the answer is replaced (unchanged behaviour)", text != ANS_OK and (ev or {}).get("kind") == "rag")
text, ev = R.run_honesty_rag(ANS_OK, qualifying_chunks=0, searched=True, profile=PROF, support_texts=(DOC,))
check("enforce, the attachment carries it: the answer is delivered", text == ANS_OK and (ev or {}).get("action") == "pass", ev)
text, ev = R.run_honesty_rag(ANS_OK, qualifying_chunks=0, searched=True, scores=(0.1, 0.05),
                             profile=PROF, support_texts=(DOC,))
check("…on the assessor path too (scores given)", text == ANS_OK, ev)
text, ev = R.run_honesty_rag(ANS_BAD, qualifying_chunks=0, searched=True, profile=PROF, support_texts=(DOC,))
check("enforce, a claim neither carries: still replaced", text != ANS_BAD)
ex_src = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
check("the executor's one honesty-RAG seam passes the turn's attached material",
      "support_texts, unreadable = self.attached_material()" in ex_src
      and "support_texts=support_texts,\n            unreadable_material=unreadable," in ex_src)

# ── D ────────────────────────────────────────────────────────────────────────
print("\nD  the Focus engine")
fe = (ROOT / "src/trusted_data_agent/agent/engines/focus_engine.py").read_text()
i_mat = fe.find("executor.attached_material()")
i_early = fe.find("if not all_results and not answer_from_attachments:")
i_msg = fe.find("No relevant knowledge found</strong>")
check("the early 'No relevant knowledge found' answer only when nothing usable was attached",
      0 < i_mat < i_early < i_msg and "        if not all_results:\n" not in fe)
check("answer_from_attachments means: no repository match AND attached material",
      "answer_from_attachments = not all_results and bool(_attached_texts or _attached_unreadable)" in fe)
check("the model is told the repositories held nothing, and not to pass the attachment off as them",
      "none of this agent's knowledge repositories held" in fe
      and "do not present their content as coming from the" in fe)
check("the retrieval event and the persisted turn say it answered from the attachment",
      fe.count('"answered_from_attachments": answer_from_attachments') == 2
      and "answering from the attached document(s)" in fe)
check("the searched repositories are named (not 0 collections)", "collection_names = set(collection_names_for_start)" in fe)
js = {p: (ROOT / p).read_text() for p in ("static/js/ui.js", "static/js/eventHandlers.js", "static/js/handlers/livingAnswer.js")}
check("Live Status and the living answer read answered_from_attachments",
      all("answered_from_attachments" in s for s in js.values()))

# ── E ────────────────────────────────────────────────────────────────────────
print("\nE  the grounding gate reads the attachment as evidence")
import asyncio  # noqa: E402
from trusted_data_agent.agent.grounding import runtime as GR  # noqa: E402
APP_CONFIG.GROUNDING_GATE_ENABLED = True
GPROF = {"groundingConfig": {"mode": "observe", "corpus": {"mode": "observe"}}}
CHUNKS = {"knowledge_retrieval_event": {"chunks": [
    {"content": "Uderia agents answer from knowledge repositories and cite their sources.", "source": "intro.md"}]}}


def grade(answer, **kw):
    return asyncio.run(GR.run_grounding(answer, kg_enrichment_event=None, profile=GPROF,
                                        profile_class="rag_focused", trace_extra=dict(CHUNKS),
                                        query="what is the late delivery penalty", skip_judge=True, **kw))


_, ev0 = grade(ANS_OK)
check("knowledge that lacks the figures, no attachment: the figures are unsupported (unchanged)",
      set((ev0 or {}).get("unsupported_claims") or []) >= {"7.25", "40"}, ev0)
_, ev1 = grade(ANS_OK, attached_texts=(DOC,))
check("…the attached contract carries them: they are supported",
      (ev1 or {}).get("claims_checked") and not (ev1 or {}).get("unsupported_claims"), ev1)
_, ev2 = grade(ANS_BAD, attached_texts=(DOC,))
check("a figure neither carries is still unsupported", "9.5" in ((ev2 or {}).get("unsupported_claims") or []), ev2)
_, ev3 = asyncio.run(GR.run_grounding(ANS_OK, kg_enrichment_event=None, profile=GPROF, profile_class="llm_only",
                                      trace_extra={}, query="q", skip_judge=True, attached_texts=(DOC,)))
check("attachments alone never make a turn groundable (no new grading)", (ev3 or {}).get("action") == "na", ev3)
check("the executor's one grounding seam passes the turn's attached text",
      "_attached_texts = self.attached_material()[0]" in ex_src and "attached_texts=_attached_texts," in ex_src)
check("Coordinate passes the document text it gave the coordinator",
      "attached_texts=((doc_context,) if doc_context else ())" in
      (ROOT / "src/trusted_data_agent/agent/engines/coordinate_engine.py").read_text())

# ── F ────────────────────────────────────────────────────────────────────────
print("\nF  a REST turn's attachment chip carries the platform's facts")
from trusted_data_agent.core import session_manager as SM  # noqa: E402
sid2 = asyncio.run(SM.create_session(USER, "Google", None, "none"))
up = routes._ingest_upload(USER, sid2, "terms.txt", DOC.encode())
pic = routes._ingest_upload(USER, sid2, "scan.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
asyncio.run(SM.add_message_to_histories(USER, sid2, "user", "q", attachments=[
    {"file_id": up["file_id"], "filename": "terms.txt"},            # the documented REST shape
    {"file_id": pic["file_id"], "filename": "scan.png"},
    {"file_id": "not-in-this-session", "filename": "other.txt"}]))
stored = [m for m in (asyncio.run(SM.get_session(USER, sid2)) or {}).get("session_history", []) if m.get("role") == "user"][-1]["attachments"]
check("the size comes from the upload record (no 'NaN MB')", stored[0].get("file_size") == len(DOC.encode()), stored[0])
check("an image is marked an image (chip reads Visual, not Text)", stored[1].get("is_image") is True and stored[0].get("is_image") is False)
check("an attachment the session does not hold is stored as sent, never invented",
      stored[2] == {"file_id": "not-in-this-session", "filename": "other.txt"})
cdu = (ROOT / "static/js/handlers/chatDocumentUpload.js").read_text()
check("the chip says nothing for an unknown size (turns stored before this)",
      "if (typeof bytes !== 'number' || !isFinite(bytes) || bytes < 0) return '';" in cdu)

# ── G ────────────────────────────────────────────────────────────────────────
print("\nG  the scores read the attachments the turn read")
from trusted_data_agent.filestore import session_files as SF  # noqa: E402
from trusted_data_agent.eval.types import RunRecord, EvalCase  # noqa: E402
from trusted_data_agent.eval.scorers.reliability import NumericFidelityScorer  # noqa: E402
from trusted_data_agent.eval.scorers.judges import GroundednessJudge  # noqa: E402
from trusted_data_agent.agent.assurance import run_context as RC  # noqa: E402
from trusted_data_agent.agent.assurance.assessors.answer_support import CorpusSupportAssessor  # noqa: E402

sid3 = asyncio.run(SM.create_session(USER, "Google", None, "none"))
c3 = routes._ingest_upload(USER, sid3, "contract.txt", DOC.encode())
p3 = routes._ingest_upload(USER, sid3, "photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
atts3 = [{"file_id": c3["file_id"], "filename": "contract.txt"}, {"file_id": p3["file_id"], "filename": "photo.png"}]

tok = SF.begin_turn(atts3)
stamp = SF.stamp_for_turn()
check("the turn records which attachments it read — ids and names, never their text",
      stamp == {"files": [{"file_id": c3["file_id"], "filename": "contract.txt"},
                          {"file_id": p3["file_id"], "filename": "photo.png"}]}, stamp)
asyncio.run(SM.update_last_turn_data(USER, sid3, {"turn": 1, "user_query": "penalty?", "profile_type": "rag_focused",
                                                  "final_summary_text": ANS_OK}))
SF.end_turn(tok)
check("…and nothing once the turn ended", SF.stamp_for_turn() is None)
wf3 = asyncio.run(SM.get_workflow_history(USER, sid3)) or []
persisted = (wf3[-1] if wf3 else {})
check("the persisted turn carries the record (no text in the history)",
      persisted.get("attachment_evidence", {}).get("files") == stamp["files"] and "7.25" not in json.dumps(persisted.get("attachment_evidence")), persisted.get("attachment_evidence"))
check("a turn with no attachments is untouched (the baseline)", SF.with_attachment_evidence(USER, sid3, {"turn": 2}) == {"turn": 2})

TURN = {"turn": 1, "user_query": "What is the late delivery penalty?", "profile_type": "rag_focused",
        "final_summary_text": ANS_OK,
        "knowledge_retrieval_event": {"collections": ["Docs"], "chunks": [
            {"content": "Uderia ships 17 builtin components.", "source": "intro.md"}]},
        "attachment_evidence": {"files": stamp["files"]}}
hyd = SF.with_attachment_evidence(USER, sid3, TURN)
ev = hyd["attachment_evidence"]
check("the scorer is handed the text of each readable attachment", len(ev["texts"]) == 1 and "7.25 percent" in ev["texts"][0] and ev["lost"] == [], ev)
check("an image's placeholder text is not evidence", not any("visual content not available" in t for t in ev["texts"]))
rr0, rr1 = RunRecord(TURN), RunRecord(hyd)
check("the support set is the retrieved chunks + the attachment (one definition)",
      len(rr1.support_chunk_texts()) == 2 and rr0.support_chunk_texts() == rr0.retrieved_chunk_texts())
CASE = EvalCase(id="t", query=TURN["user_query"], profile_class="rag_focused")
nf0, nf1 = NumericFidelityScorer().score(CASE, rr0), NumericFidelityScorer().score(CASE, rr1)
check("numeric fidelity without the attachment: the figures read unsupported (what it scored before)",
      nf0.score is not None and nf0.score < 0.5, nf0)
check("numeric fidelity with it: every figure grounded", (nf1.score or 0) == 1.0, nf1)
GJ = GroundednessJudge()
check("the groundedness judge runs on a Focus turn answered from the attachment alone",
      GJ._has_inputs(CASE, RunRecord({**hyd, "knowledge_retrieval_event": {}})))
_sys, _user = GJ.build_messages(CASE, rr1)
check("…and is shown the attachment, labelled as the user's", "7.25 percent" in _user and "documents the user attached" in _user)
ctx1 = RC.from_run(rr1)
f1 = CorpusSupportAssessor().run(ctx1)
check("the corpus-support assessor (the post-hoc twin of the gate) finds the figures supported",
      not f1.triggered and "7.25" in " ".join(ctx1.chunks), f1)
f0 = CorpusSupportAssessor().run(RC.from_run(rr0))
check("…where without the attachment it convicted them", f0.triggered, f0)

from trusted_data_agent.eval.scorers.deterministic import GroundednessOverlapScorer  # noqa: E402
_att_only = RunRecord({**hyd, "knowledge_retrieval_event": {}})
go = GroundednessOverlapScorer().score(CASE, _att_only)
check("grounded_overlap grades a Focus answer drawn from the attachment alone (it abstained before)",
      go.score == 1.0 and "supported" in (go.explanation or ""), go)

service.soft_delete(c3["file_id"], USER)
gone = SF.with_attachment_evidence(USER, sid3, TURN)
rr2 = RunRecord(gone)
check("deleted since: no text is read, the attachment is named as lost",
      gone["attachment_evidence"]["texts"] == [] and gone["attachment_evidence"]["lost"] == ["contract.txt"])
why = rr2.attachment_evidence_lost_reason()
check("every support reader abstains in one wording", "contract.txt was deleted since" in why, why)
nf2 = NumericFidelityScorer().score(CASE, rr2)
check("numeric fidelity abstains rather than convict a figure whose source was deleted",
      nf2.passed is None and "deleted since" in (nf2.explanation or ""), nf2)
check("the groundedness judge abstains", not GJ._has_inputs(CASE, rr2))
go2 = GroundednessOverlapScorer().score(CASE, rr2)
check("grounded_overlap abstains in the same words", go2.passed is None and "deleted since" in (go2.explanation or ""), go2)
f2 = CorpusSupportAssessor().run(RC.from_run(rr2))
check("the corpus-support assessor abstains (the contract enforces it once)",
      not f2.triggered and "deleted since" in (f2.summary or ""), f2)
service.restore(c3["file_id"], USER)

src = {p: (ROOT / p).read_text() for p in ("src/trusted_data_agent/eval/online.py", "src/trusted_data_agent/api/rest_routes.py",
                                            "src/trusted_data_agent/agent/execution_service.py",
                                            "src/trusted_data_agent/agent/grounding/adapter.py")}
check("online scoring and 'Score this turn' read the evidence before they score",
      all("_sf.with_attachment_evidence" in src[p] for p in ("src/trusted_data_agent/eval/online.py", "src/trusted_data_agent/api/rest_routes.py")))
check("every turn records its attachments (begun and ended with the turn)",
      "_sf_mod.begin_turn(attachments)" in src["src/trusted_data_agent/agent/execution_service.py"]
      and "_sf_mod.end_turn(_evidence_token)" in src["src/trusted_data_agent/agent/execution_service.py"])
check("the in-flight gate reads the same support definition", "return rr.support_chunk_texts()" in src["src/trusted_data_agent/agent/grounding/adapter.py"])

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
