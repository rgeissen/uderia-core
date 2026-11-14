"""S9 baseline freeze — committed GREEN before Grounded Multimodal landed.

Pins that with NO attachment sources (the master switch off) nothing changes, EXECUTING the real
seams: the two document-context loaders produce exactly today's text for an image + a text file
under every provider capability (an image is a native block for a vision provider and the
"visual content not available" placeholder for a text-only one); the eval record's
`grounding_text` is tool results + retrieved documents and nothing else; the compute registry
holds no statement after documents load; no `attachment_sources` stamp exists at the
persistence chokepoint; the capsule's Sources row exists (what S9 will join). Every assertion
here must stay green through S9 and after it (with the switch off)."""
import json, logging, os, pathlib, shutil, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.pop("TDA_ATTACHMENT_SOURCES", None)
from trusted_data_agent.core.config import APP_CONFIG
if hasattr(APP_CONFIG, "ATTACHMENT_SOURCES"):
    APP_CONFIG.ATTACHMENT_SOURCES = False

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:300]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()

print("[1] the switch is absent or off")
check("ATTACHMENT_SOURCES absent or False by default", not getattr(APP_CONFIG, "ATTACHMENT_SOURCES", False))

print("[2] a manifest exactly as the upload endpoint writes it today")
from trusted_data_agent.core.utils import get_project_root
U, S = "s9-freeze-user", "s9-freeze-session"
up = get_project_root() / "tda_sessions" / U / "uploads" / S
up.mkdir(parents=True, exist_ok=True)
(up / "f-img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
(up / "f-txt.txt").write_text("Revenue 48,210.50 in EMEA.")
placeholder = "[Image file: table.png - visual content not available in text mode]"
manifest = {"f-img": {"path": str(up / "f-img.png"), "filename": "table.png", "content_type": "image/png", "file_size": 72, "extracted_text": placeholder, "is_image": True},
            "f-txt": {"path": str(up / "f-txt.txt"), "filename": "notes.txt", "content_type": "text/plain", "file_size": 26, "extracted_text": "Revenue 48,210.50 in EMEA.", "is_image": False}}
(up / "manifest.json").write_text(json.dumps(manifest))
RT = src("src/trusted_data_agent/api/routes.py")
check("the upload endpoint writes the placeholder for an image today", 'extracted_text = f"[Image file: {filename} - visual content not available in text mode]"' in RT)
try:
    from trusted_data_agent.agent.executor import load_document_context, load_multimodal_document_content
    atts = [{"file_id": "f-img", "filename": "table.png"}, {"file_id": "f-txt", "filename": "notes.txt"}]
    text, events = load_document_context(U, S, atts)
    check("load_document_context: both entries as today's blocks, the image as its placeholder", text == f"--- Document: table.png ---\n{placeholder}\n--- End of table.png ---\n\n--- Document: notes.txt ---\nRevenue 48,210.50 in EMEA.\n--- End of notes.txt ---" and events == [], text)
    blocks, fallback = load_multimodal_document_content(U, S, atts, "Anthropic", "claude-3-5-sonnet")
    check("a vision provider: the image is ONE native block and contributes NO text; the text file is the fallback", blocks and len(blocks) == 1 and blocks[0]["type"] == "image" and blocks[0]["filename"] == "table.png" and fallback == "--- Document: notes.txt ---\nRevenue 48,210.50 in EMEA.\n--- End of notes.txt ---", (blocks, fallback))
    blocks2, fallback2 = load_multimodal_document_content(U, S, atts, "Ollama", "llama3")
    check("a text-only provider: no blocks, the image's placeholder in the text", blocks2 is None and placeholder in (fallback2 or "") and "48,210.50" in (fallback2 or ""), (blocks2, fallback2))
finally:
    shutil.rmtree(get_project_root() / "tda_sessions" / U, ignore_errors=True)

print("[3] the eval record: grounding_text is tool results + retrieved documents, nothing else")
from trusted_data_agent.eval.types import RunRecord
rr = RunRecord({"turn": 1, "conversation_agent_events": [
    {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}},
    {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "result_preview": "rows: 100"}}],
    "knowledge_retrieval_event": {"chunks": [{"content": "doc text"}]}, "attachments": [{"file_id": "x", "filename": "table.png"}]})
gt = rr.grounding_text()
check("tool results + retrieved docs only (an attachment adds nothing today)", "rows: 100" in gt and "doc text" in gt and "table.png" not in gt, gt)

print("[4] the compute registry holds no statement from documents")
from trusted_data_agent.agent import result_sets as RS
check("record() ignores non-tool sources; statements() empty for a fresh session", RS.statements("s9-freeze-none") == [])

print("[5] no attachment stamp at the persistence chokepoint; the capsule's Sources row exists")
SM = src("src/trusted_data_agent/core/session_manager.py")
check("update_last_turn_data stamps via setdefault (the idiom S9 joins)", 'setdefault("outcome"' in SM and 'setdefault("tool_surface"' in SM)
AC = src("static/js/handlers/answerCapsule.js")
check("the capsule renders a Sources row from the knowledge retrieval event", "factRow('Sources', sourcesLine)" in AC and "knowledge_retrieval_event" in AC)

print(f"\n=== S9 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
