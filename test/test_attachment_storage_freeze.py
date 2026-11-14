"""
Zero-regression freeze for chat attachments (PROP-010 §4.8 / §9) — written BEFORE the
upload storage moved into the file store, recorded on the code that existed then.

What it freezes is everything a USER or the AGENT can observe of an attachment, for every
file type the composer accepts, with attachment sources off and on:

  - the upload response entry (minus the generated file id);
  - what a turn reads: ``load_document_context``, ``load_multimodal_document_content`` for a
    native-document provider (Google) and a text-only one (Ollama) — block shape AND the
    bytes each block carries — the session-store chunks, ``image_sources.manifest_entries``;
  - the content endpoint's bytes (identical to what was uploaded);
  - deleting one upload, and deleting the conversation (``_cleanup_session_uploads``): the
    turn reads nothing afterwards.

What deliberately may differ (and is not compared): WHERE the bytes sit and that they are
encrypted. A plaintext scan of the session folder is a separate gate (test_file_store.py).

Run:      PYTHONPATH=src python3 test/test_attachment_storage_freeze.py
Re-record (ONLY on the reference code): ... --record
No server, no network. Runs from the project root (one reader resolves a relative path).
"""
import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

# Isolation: the file store writes to a throwaway root and database, never the real ones.
import tempfile  # noqa: E402
_TMP = Path(tempfile.mkdtemp(prefix="uderia-freeze-"))
os.environ["TDA_FILES_DIR"] = str(_TMP / "files")
os.environ["TDA_AUTH_DB_URL"] = f"sqlite:///{_TMP / 'auth.db'}"
from trusted_data_agent.core.config import APP_CONFIG as _CFG  # noqa: E402
_CFG.AUTH_DB_PATH = f"sqlite:///{_TMP / 'auth.db'}"

GOLDEN = ROOT / "test" / "fixtures" / "attachment_storage_freeze.json"
USER = "filesfreeze-user-0001"
SESSION = "filesfreeze-session-0001"

_passed = _failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


# ── fixtures, built deterministically at run time ────────────────────────────

def _pdf_bytes() -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_creation_date(__import__("datetime").datetime(2026, 1, 1))
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in ["Quarterly revenue summary", "EMEA 4.12 4.58", "APAC 2.84 3.27", "Americas 6.30 6.11"]:
        pdf.cell(0, 10, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _docx_bytes() -> bytes:
    from docx import Document
    d = Document()
    d.add_paragraph("Employee policy handbook")
    d.add_paragraph("Section 1. Leave and absence.")
    d.add_paragraph("Section 2. Working from home.")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _png_bytes() -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 120), "white")
    dr = ImageDraw.Draw(im)
    dr.text((10, 10), "Region  Revenue", fill="black")
    dr.text((10, 40), "EMEA    4.58", fill="black")
    dr.text((10, 70), "APAC    3.27", fill="black")
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


def fixtures(sources_on: bool):
    long_text = "\n\n".join(f"Paragraph {i}. " + ("The quick brown fox jumps over the lazy dog. " * 12) for i in range(40))
    out = [
        ("notes.txt", "Line one\r\nLine two\nLíne thrée — ünïcode\n".encode("utf-8")),
        ("readme.md", b"# Title\n\nSome *markdown* text.\n"),
        ("long.txt", long_text.encode("utf-8")),
        ("report.pdf", _pdf_bytes()),
        ("handbook.docx", _docx_bytes()),
        ("chart.png", _png_bytes()),
    ]
    if sources_on:
        out.append(("customers.csv", b"Customer,Segment,Region\r\nNorthwind,Enterprise,EMEA\r\nContoso,Mid-market,APAC\r\n"))
    return out


# ── observation ──────────────────────────────────────────────────────────────

def env_fingerprint():
    from trusted_data_agent.llm import image_sources as isrc
    try:
        import pdfplumber  # noqa: F401
        pdf = "pdfplumber"
    except Exception:  # noqa: BLE001
        pdf = None
    return {"ocr": isrc.ocr_engine()[1], "pdf": pdf}


def _norm(value, fid):
    """Replace the generated file id wherever it appears, recursively."""
    if isinstance(value, str):
        return value.replace(fid, "<FID>")
    if isinstance(value, list):
        return [_norm(v, fid) for v in value]
    if isinstance(value, tuple):
        return [_norm(v, fid) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v, fid) for k, v in value.items()}
    return value


def _block_bytes(block):
    data = block.get("data")
    if data is None and block.get("path"):
        with open(block["path"], "rb") as f:
            data = f.read()
    return data


class _Store:
    def __init__(self):
        self.docs = []

    async def ingest(self, module, docs):
        self.docs.extend((module, d.id, d.content, dict(d.metadata)) for d in docs)


def _reset():
    shutil.rmtree(ROOT / "tda_sessions" / USER, ignore_errors=True)


def observe(sources_on: bool):
    from trusted_data_agent.core.config import APP_CONFIG
    from trusted_data_agent.api import routes
    from trusted_data_agent.agent import executor
    from trusted_data_agent.llm import image_sources as isrc
    from trusted_data_agent.core import session_manager

    APP_CONFIG.ATTACHMENT_SOURCES = sources_on
    obs = {}
    for filename, content in fixtures(sources_on):
        _reset()
        entry = routes._ingest_upload(USER, SESSION, filename, content)
        fid = entry["file_id"]
        att = [{"file_id": fid, "filename": filename}]
        o = {"response": _norm({k: v for k, v in entry.items() if k != "file_id"}, fid)}

        text, events = executor.load_document_context(USER, SESSION, att)
        o["document_context"] = _norm([text, events], fid)
        text, events = executor.load_document_context(USER, SESSION, att, max_total_chars=500)
        o["document_context_budget"] = _norm([text, events], fid)

        for provider, model in (("Google", "gemini-2.5-flash"), ("Ollama", "")):
            blocks, fallback = executor.load_multimodal_document_content(USER, SESSION, att, provider, model)
            shaped = None
            if blocks:
                shaped = [{"type": b["type"], "mime_type": b["mime_type"], "filename": b["filename"],
                           "bytes_equal_upload": _block_bytes(b) == content} for b in blocks]
            o[f"multimodal_{provider}"] = _norm([shaped, fallback], fid)

        store = _Store()
        asyncio.run(executor._ingest_documents_to_session_store(store, SESSION, USER, {"attachments": att}))
        o["session_store"] = _norm(store.docs, fid)

        o["manifest_entries"] = _norm([[s.to_dict(), {k: v for k, v in e.items() if k not in ("path", "file_ref", "stored_filename")}]
                                       for s, e in isrc.manifest_entries(USER, SESSION, att)], fid)

        o["content_bytes_equal_upload"] = routes._upload_bytes(USER, SESSION, fid) == content

        o["delete_returns"] = routes._delete_upload(USER, SESSION, fid)
        o["after_delete_context"] = executor.load_document_context(USER, SESSION, att)[0]
        o["after_delete_bytes"] = routes._upload_bytes(USER, SESSION, fid)
        o["delete_again_returns"] = routes._delete_upload(USER, SESSION, fid)

        # a second upload, then the conversation is deleted
        entry2 = routes._ingest_upload(USER, SESSION, filename, content)
        att2 = [{"file_id": entry2["file_id"], "filename": filename}]
        session_manager._cleanup_session_uploads(USER, SESSION)
        o["after_cleanup_context"] = executor.load_document_context(USER, SESSION, att2)[0]
        o["after_cleanup_bytes"] = routes._upload_bytes(USER, SESSION, entry2["file_id"])
        o["after_cleanup_multimodal"] = list(executor.load_multimodal_document_content(USER, SESSION, att2, "Google", "gemini-2.5-flash"))
        obs[filename] = o
    _reset()
    return obs


def run():
    record = "--record" in sys.argv
    env = env_fingerprint()
    current = {"off": observe(False), "on": observe(True)}
    if record:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps({"env": env, "observations": current}, indent=1, ensure_ascii=False, sort_keys=True))
        print(f"recorded {GOLDEN.relative_to(ROOT)} (env {env})")
        return 0
    golden = json.loads(GOLDEN.read_text())
    same_env = golden["env"] == env
    if not same_env:
        print(f"  NOTE  extractor environment differs from the recording ({golden['env']} vs {env}); "
              "the attachment-sources-ON observations of images and PDFs are skipped")
    cur = json.loads(json.dumps(current, ensure_ascii=False, sort_keys=True))
    for mode in ("off", "on"):
        for filename, want in golden["observations"][mode].items():
            got = cur[mode].get(filename)
            if got is None:
                check(f"[{mode}] {filename}: observed", False, "missing in this run")
                continue
            env_dependent = mode == "on" and filename.endswith((".png", ".pdf"))
            for key in sorted(want):
                if env_dependent and not same_env:
                    continue
                check(f"[{mode}] {filename}: {key}", got.get(key) == want[key],
                      f"want {json.dumps(want[key], ensure_ascii=False)[:300]}\n        got  {json.dumps(got.get(key), ensure_ascii=False)[:300]}")
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(run())
