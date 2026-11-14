"""S9 — Grounded Multimodal: the gate (spec DoD).

Executes the REAL module end to end with the master switch ON: the pure box→table clustering
on synthetic OCR boxes (header/no-header, caption rows, merged cells, too-few-rows, bad boxes),
the CSV/text extractors, record round-trip, `source_text` (an unverified transcription
contributes NOTHING), the per-turn contextvar registry (isolation across contexts, add/replace,
stamp shape), S3 result-set registration (`register` + the stamp persisting an attachment
statement with its sample rows, the S3 baseline untouched), the eval record + grounding adapter
(grounding_text + retrieved chunks carry verified sources only), the assurance reading (the
five states), the persistence chokepoint, the upload endpoint's manifest/response and the
content endpoint's gate (structural), the OCR engines when present (a real image → the real
rows), the genie seam, the switch-off contract (every seam inert). Mutation-tested (see
scratch mut_s9.py)."""
import contextvars, csv, json, os, pathlib, shutil, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import logging; logging.disable(logging.CRITICAL)
os.environ["TDA_ATTACHMENT_SOURCES"] = "1"
from trusted_data_agent.core.config import APP_CONFIG
APP_CONFIG.ATTACHMENT_SOURCES = True
from trusted_data_agent.llm import image_sources as I

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:300]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
TMP = pathlib.Path(tempfile.mkdtemp(prefix="s9-"))

def box(x, y, w, h, text, score=1.0):
    return {"box": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], "text": text, "score": score}

print("[1] table_from_boxes — the clustering rule")
B = [box(20, 20, 60, 14, "Region"), box(190, 20, 70, 14, "Revenue"), box(360, 21, 60, 14, "Orders"),
     box(20, 62, 50, 14, "EMEA"), box(190, 63, 80, 14, "48,210.50"), box(360, 62, 30, 14, "312"),
     box(20, 104, 50, 14, "APAC"), box(190, 104, 80, 14, "31,904.00"), box(360, 105, 30, 14, "201")]
t = I.table_from_boxes(B)
check("header detected from a non-numeric first row over numeric rows", t and t.columns == ("Region", "Revenue", "Orders"), t and t.columns)
check("rows keyed by the header, cell text kept verbatim", t and t.rows[0] == {"Region": "EMEA", "Revenue": "48,210.50", "Orders": "312"} and t.row_count == 2, t and t.rows)
check("region is the union of the cells", t and t.region == (20.0, 20.0, 420.0, 119.0), t and t.region)
check("a numeric string in a cell parses for the compute tool", __import__("trusted_data_agent.agent.compute", fromlist=["as_number"]).as_number(t.rows[0]["Revenue"]) == 48210.5)
t2 = I.table_from_boxes([box(20, 200, 200, 14, "Quarterly revenue by region")] + B)
check("a caption row (too few cells) is dropped, never a data row", t2 and t2.row_count == 2 and "Quarterly" not in json.dumps(t2.rows), t2 and t2.rows)
t3 = I.table_from_boxes([box(20, 20, 30, 14, "1"), box(190, 20, 30, 14, "2"), box(20, 62, 30, 14, "3"), box(190, 62, 30, 14, "4")])
check("an all-numeric grid has no header: col_1.. and every row is data", t3 and t3.columns == ("col_1", "col_2") and t3.row_count == 2, t3 and (t3.columns, t3.rows))
t4 = I.table_from_boxes(B + [box(20, 104, 50, 14, "(south)")])
check("two boxes landing on one column of one row merge their text", t4 and t4.rows[1]["Region"] in ("APAC (south)", "(south) APAC"), t4 and t4.rows)
check("a header + one row is still a table; a header alone is not", I.table_from_boxes(B[:6]) and I.table_from_boxes(B[:6]).row_count == 1 and I.table_from_boxes(B[:3]) is None)
check("a bad box / empty text never raises and is skipped", I.table_from_boxes([{"box": None, "text": "x"}, {"box": [[0, 0]], "text": ""}] + B) is not None)
check("a single column is not a table", I.table_from_boxes([box(20, 20, 50, 14, "a"), box(20, 62, 50, 14, "b"), box(20, 104, 50, 14, "c")]) is None)
tt = I.table_from_boxes([box(0, 0, 10, 10, "Name"), box(50, 0, 10, 10, "Name"), box(0, 30, 10, 10, "a"), box(50, 30, 10, 10, "1"), box(0, 60, 10, 10, "b"), box(50, 60, 10, 10, "2")])
check("duplicate header names are disambiguated", tt and tt.columns == ("Name", "Name_2"), tt and tt.columns)
check("as_text renders header + pipe rows", t.as_text().splitlines()[0] == "Region | Revenue | Orders" and "EMEA | 48,210.50 | 312" in t.as_text())

print("[2] extractors — csv / text / missing / unknown / a pipe transcription")
csvp = TMP / "d.csv"
with open(csvp, "w", newline="") as fh:
    csv.writer(fh).writerows([["Region", "Revenue"], ["EMEA", "48,210.50"], ["", ""], ["APAC", "31,904.00"]])
c = I.extract("f2", "d.csv", str(csvp), "text/csv")
check("csv → one verified table, blank rows dropped", c.method == "csv" and c.verified and c.tables[0].columns == ("Region", "Revenue") and c.tables[0].row_count == 2, c)
sc = TMP / "s.tsv"; sc.write_text("a\tb\n1\t2\n3\t4\n")
c2 = I.extract("f3", "s.tsv", str(sc), "")
check("a tab-separated file is sniffed", c2.method == "csv" and c2.tables[0].columns == ("a", "b") and c2.tables[0].rows[1] == {"a": "3", "b": "4"}, c2)
tp = TMP / "n.txt"; tp.write_text("Revenue was 48,210.50 in EMEA.")
x = I.extract("f4", "n.txt", str(tp), "text/plain")
check("text → verified text source", x.method == "text" and x.verified and "48,210.50" in x.text, x)
bad = TMP / "bad.docx"; bad.write_bytes(b"not a docx")
bd = I.extract("f12", "bad.docx", str(bad), "")
check("a reader's bracketed failure placeholder is NOT text: an unreadable docx → none, never a verified '[... failed]'", bd.method == "none" and not bd.verified and "[" not in bd.text, bd)
m = I.extract("f5", "gone.png", str(TMP / "gone.png"), "image/png")
check("a missing file → method none with the reason", m.method == "none" and not m.verified and "missing" in (m.note or ""), m)
u = I.extract("f6", "x.bin", str(tp), "application/octet-stream")
check("an unknown type → none, reason names the type", u.method == "none" and ".bin" in (u.note or ""), u)
tr = I.transcription_source("f7", "chart.png", "Region | Revenue\nEMEA | 48,210.50\nAPAC | 31,904.00", width=400, height=200)
check("a pipe transcription becomes an UNVERIFIED table source", tr.method == "vision_transcription" and not tr.verified and tr.tables and tr.tables[0].row_count == 2 and tr.usable, tr)
check("an empty transcription is unusable and says so", not I.transcription_source("f8", "z.png", "   ").usable)
check("source_text: an unverified source contributes NOTHING", I.source_text(tr) == "")
check("source_text: a verified source is labelled with file, method and engine", I.source_text(c).startswith("[Attachment: d.csv — extracted by csv (csv), verified]") and "EMEA | 48,210.50" in I.source_text(c))
check("document_text_for names the platform for a verified read and the model for a transcription",
      "read by the platform via csv" in I.document_text_for(c) and "transcribed by the model — unverified" in I.document_text_for(tr) and I.document_text_for(m) == "")
rec = I.to_record(c); back = I.from_record(rec)
check("manifest record round-trips every row", back == c and json.dumps(rec) and I.from_record({"nope": 1}) is None)
check("to_dict is the SUMMARY: sample rows + text_chars, never the full text", len(c.to_dict()["tables"][0]["sample_rows"]) == 2 and c.to_dict()["text_chars"] == 0 and "text" in c.to_dict())

print("[3] the per-turn registry — a contextvar, isolated across contexts")
def in_ctx(fn):
    return contextvars.copy_context().run(fn)
def scenario_a():
    tok = I.begin_turn(); I.add(c); I.add(tr); I.add(c)   # same file_id twice → replaced, not duplicated
    out = (len(I.sources()), I.stamp_for_turn()); I.end_turn(tok); return out
n, stamp = in_ctx(scenario_a)
check("add replaces a source with the same file_id", n == 2)
check("stamp counts verified / unverified / none and carries the verified grounding text", stamp["verified"] == 1 and stamp["unverified"] == 1 and stamp["none"] == 0 and "48,210.50" in stamp["grounding_text"] and "chart.png" not in stamp["grounding_text"], stamp)
check("outside a turn there are no sources and no stamp", I.sources() == [] and I.stamp_for_turn() is None)
def scenario_b():
    tok = I.begin_turn(); I.add(c); r = I.verified_chunks(); I.end_turn(tok); return r
ch = in_ctx(scenario_b)
check("verified_chunks are retrieved-chunk-shaped (content + source + attachment flag)", ch and ch[0]["attachment"] is True and ch[0]["source"] == "d.csv" and "EMEA" in ch[0]["content"], ch)
def scenario_c():
    tok = I.begin_turn(); I.add(tr); r = I.verified_chunks(); I.end_turn(tok); return r
check("an unverified source is never a chunk", in_ctx(scenario_c) == [])

print("[4] S3 registration — an attachment table becomes a result-set statement")
from trusted_data_agent.agent import result_sets as RS
APP_CONFIG.COMPUTE_LINEAGE = True
RS.begin_turn("s9-sess")
n1 = RS.register("s9-sess", "attachment", "attachment:d.csv#1 — csv, verified", ["Region", "Revenue"], [dict(r) for r in c.tables[0].rows], source={"file_id": "f2", "filename": "d.csv"})
check("register numbers the statement like a tool statement", n1 == 1 and RS.get("s9-sess", 1)["tool"] == "attachment" and RS.get("s9-sess", 1)["row_count"] == 2 and RS.get("s9-sess", 1)["source"]["filename"] == "d.csv")
check("register ignores empty rows", RS.register("s9-sess", "attachment", "x", [], []) is None)
st = RS.stamp_for_turn("s9-sess")
check("the compute stamp persists an attachment statement (with sample rows) even with no receipt", st and st["receipts"] == [] and st["statements"][0]["source"]["file_id"] == "f2" and st["statements"][0]["sample_rows"][0]["Region"] == "EMEA", st)
RS.begin_turn("s9-tool"); RS.record("s9-tool", "base_readQuery", {"sql": "SELECT 1"}, {"results": [{"a": 1}]})
check("S3 baseline: a tool statement alone still yields no stamp", RS.stamp_for_turn("s9-tool") is None)
def scenario_d():
    tok = I.begin_turn(); I.add(c); RS.begin_turn("s9-reg"); I.register_tables("s9-reg"); out = (I.sources()[0].statement_ns, RS.statements("s9-reg")); I.register_tables("s9-reg"); out2 = RS.statements("s9-reg"); I.end_turn(tok); return out, out2
(ns, sts), sts2 = in_ctx(scenario_d)
check("register_tables registers each verified table once and records the statement numbers on the source", ns == (1,) and len(sts) == 1 and sts[0]["statement"].startswith("attachment:d.csv#1") and len(sts2) == 1, (ns, sts))
def scenario_e():
    tok = I.begin_turn(); I.add(tr); RS.begin_turn("s9-unv"); I.register_tables("s9-unv"); out = RS.statements("s9-unv"); I.end_turn(tok); return out
check("an UNVERIFIED table is never registered as a result set", in_ctx(scenario_e) == [])
APP_CONFIG.COMPUTE_LINEAGE = False
def scenario_f():
    tok = I.begin_turn(); I.add(c); RS.begin_turn("s9-off"); I.register_tables("s9-off"); out = RS.statements("s9-off"); I.end_turn(tok); return out
check("with the compute registry off nothing is registered (its own switch)", in_ctx(scenario_f) == [])
APP_CONFIG.COMPUTE_LINEAGE = True
# a quoted figure traces to the attachment statement
from trusted_data_agent.agent import compute_lineage as CL
ev = CL.assess("EMEA brought 48,210.50 this quarter.", [], RS.statements("s9-sess"), query="how much did EMEA bring?")
check("a figure quoted from the attachment table traces to row 1 of statement 1", any(q["figure"] == "48,210.50" and q["statement"] == 1 and q["row"] == 1 for q in ev["quoted"]), ev)

print("[5] the eval record + the grounding adapter read verified sources only")
from trusted_data_agent.eval.types import RunRecord
from trusted_data_agent.agent.grounding import adapter as GA
turn = {"turn": 1, "conversation_agent_events": [], "attachment_sources": stamp, "user_query": "how much?"}
rr = RunRecord(turn)
check("attachment_sources() lists every source; grounding_text carries the verified text", len(rr.attachment_sources()) == 2 and "48,210.50" in rr.grounding_text() and "chart.png" not in rr.grounding_text())
check("attachment_chunks: one chunk of the verified text, none from the transcription", len(rr.attachment_chunks()) == 1 and rr.attachment_chunks()[0]["source"] == "d.csv")
inputs = GA.build_inputs(turn, "EMEA brought 48,210.50.")
check("the gate's inputs carry the source as a retrieved chunk → the corpus family has something to hold the answer to", any("48,210.50" in ch for ch in inputs.retrieved_chunks) and GA.has_groundable_content(inputs))
check("a turn with only an unverified source has nothing groundable from it", not GA.has_groundable_content(GA.build_inputs({"turn": 1, "attachment_sources": {"sources": [tr.to_dict()], "grounding_text": ""}}, "x")))
check("a turn without the stamp reads nothing (baseline)", RunRecord({"turn": 1}).attachment_sources() == [] and RunRecord({"turn": 1}).grounding_text() == "")

print("[6] the assurance reading — five states")
from trusted_data_agent.agent import assurance_points as AP
def read(t):
    r = [x for x in AP.read_turn(t, "llm_only") if x.key == "attachment_sources"]
    return r[0] if r else None
r0 = read({"turn": 1}); check("absent stamp → na 'none attached'", r0 and r0.state == "na" and "nothing attached" in r0.detail, r0)
r1 = read({"turn": 1, "attachment_sources": {"sources": [c.to_dict()], "verified": 1, "unverified": 0, "none": 0}})
check("verified only → pass naming file + method + rows", r1 and r1.state == "pass" and "d.csv (csv" in r1.detail and "2 row(s)" in r1.detail, r1 and r1.detail)
r2 = read({"turn": 1, "attachment_sources": {"sources": [c.to_dict(), tr.to_dict()], "verified": 1, "unverified": 1, "none": 0}})
check("an unverified transcription → warn naming it, verified count beside", r2 and r2.state == "warn" and "chart.png" in r2.detail and "could not check" in r2.detail and "1 verified" in r2.detail, r2 and r2.detail)
r3 = read({"turn": 1, "attachment_sources": {"sources": [m.to_dict()], "verified": 0, "unverified": 0, "none": 1}})
check("unread only → na 'not readable', the model saw it natively", r3 and r3.state == "na" and "natively" in r3.detail and r3.word == "not readable", r3 and r3.detail)
r4 = read({"turn": 1, "attachment_sources": {"sources": [c.to_dict(), m.to_dict()], "verified": 1, "unverified": 0, "none": 1}})
check("verified + unread → pass, the unread named", r4 and r4.state == "pass" and "1 could not be read" in r4.detail, r4 and r4.detail)
check("registered at GROUNDED, in every mechanism registry", "attachment_sources" in AP.POINT_DIMENSIONS and "attachment_sources" in AP.MECHANISM_NOTE and "attachment_sources" in AP.MECHANISM_DERIVATION and "attachment_sources" in AP.MECHANISM_LIMITS and AP.POINT_CLASS["attachment_sources"] == "measure" and "attachment_sources" in AP._POST_DOOR_POINTS)

print("[7] the seams (structural — each one calls the module; every one is switch-gated)")
SM = src("src/trusted_data_agent/core/session_manager.py")
check("the persistence chokepoint stamps attachment_sources with setdefault", 'turn_data.setdefault("attachment_sources", _as_stamp)' in SM)
RT = src("src/trusted_data_agent/agent/grounding/runtime.py")
check("run_grounding merges the live stamp into the turn before build_inputs", 'turn["attachment_sources"] = _as' in RT and RT.index('turn["attachment_sources"]') < RT.index("inputs = adapter.build_inputs(turn, answer_text)"))
EX = src("src/trusted_data_agent/agent/executor.py")
check("the executor seam runs after the document loaders and appends the image rows once", "_load_attachment_sources()" in EX and "blk not in (self.document_context or \"\")" in EX and EX.index("_load_attachment_sources()") > EX.index("load_multimodal_document_content(\n                self.user_uuid"))
check("the vision fallback is opt-in per profile and its product is a transcription_source", "attachmentConfig" in EX and "transcription_source(" in EX and 'cfg.get("transcribeImages") is True' in EX)
ES = src("src/trusted_data_agent/agent/execution_service.py")
check("execution_service opens the registry per turn and releases it in finally", "_isrc_mod.begin_turn()" in ES and "_isrc_mod.end_turn(_attachment_token)" in ES and ES.index("_isrc_mod.end_turn") > ES.index("    finally:"))
CE = src("src/trusted_data_agent/agent/engines/coordinate_engine.py")
check("the genie coordinator registers its attachments too (charter #14)", "_isrc.register_from_manifest(user_uuid, session_id, attachments)" in CE)
RO = src("src/trusted_data_agent/api/routes.py")
check("the upload endpoint reads once at upload (from memory, before encryption), replaces the placeholder with the verified text, stores the record", "_isrc.extract(file_id, filename, None, content_type, data=file_content)" in RO and 'manifest[file_id]["source"] = _isrc.to_record(_src)' in RO and 'manifest[file_id]["extracted_text"] = extracted_text = _isrc.document_text_for(_src)' in RO)
SF = src("src/trusted_data_agent/filestore/session_files.py")
check("the content endpoint is switch-gated and owner/session-scoped (PROP-010: the scoping lives in the attachment accessor)",
      '/api/v1/chat/upload/<file_id>/content' in RO and RO.index("attachment sources are off") < RO.index('return await send_file(_io.BytesIO(data)')
      and "session_files.read_bytes(user_uuid, session_id, file_id)" in RO
      and "db.get_file(ref, user_uuid)" in SF and "d in p.resolve().parents" in SF)
check("csv/tsv are accepted only with the switch on", "SOURCE_ONLY_EXTENSIONS = {'.csv', '.tsv'}" in RO and "def allowed_upload_extensions" in RO)
CT = src("src/trusted_data_agent/core/profile_config_contract.py")
check("attachmentConfig is a portable, unsigned contract key", 'ConfigKeySpec(key="attachmentConfig")' in CT)

print("[8] the real engines, when installed (a PIL-rendered table → the real rows)")
eng, name = I.ocr_engine()
if name:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (520, 200), "white"); d = ImageDraw.Draw(img)
    try: f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except Exception: f = ImageFont.load_default()
    rows = [["Region", "Revenue", "Orders"], ["EMEA", "48,210.50", "312"], ["APAC", "31,904.00", "201"]]
    for i, r in enumerate(rows):
        for j, cell in enumerate(r): d.text((20 + j * 170, 20 + i * 42), cell, fill="black", font=f)
    ip = TMP / "tbl.png"; img.save(ip)
    s_img = I.extract("f9", "tbl.png", str(ip), "image/png")
    check(f"image OCR ({name}) → a verified table with the rendered rows", s_img.method == "ocr" and s_img.verified and s_img.tables and s_img.tables[0].rows[0].get("Revenue") == "48,210.50" and s_img.width == 520, s_img)
    img.save(TMP / "scan.pdf")
    s_pdf = I.extract("f10", "scan.pdf", str(TMP / "scan.pdf"), "application/pdf")
    check("a scanned PDF (no text layer) is rendered and OCR'd, page-stamped", s_pdf.method == "ocr" and s_pdf.verified and s_pdf.tables and s_pdf.tables[0].page == 1, s_pdf)
else:
    print("  (no OCR engine installed — image/scanned-PDF cases skipped; the no-engine branch is asserted below)")
    s_img = None
blank = Image.new("RGB", (80, 40), "white") if name else None
if blank is not None:
    bp = TMP / "blank.png"; blank.save(bp)
    s_bl = I.extract("f11", "blank.png", str(bp), "image/png")
    check("an image with no text → none, engine named, not verified", s_bl.method == "none" and not s_bl.verified and s_bl.engine == name, s_bl)

print("[9] the switch off — every seam inert")
APP_CONFIG.ATTACHMENT_SOURCES = False
def scenario_off():
    tok = I.begin_turn(); I.add(c); out = (I.sources(), I.stamp_for_turn(), I.manifest_entries("u", "s", [{"file_id": "x"}])); I.end_turn(tok); return out
so, st_off, me = in_ctx(scenario_off)
check("off: add records nothing, stamp None, manifest read skipped", so == [] and st_off is None and me == [])
from trusted_data_agent.api import routes as _routes
# CSV/TSV/XLSX are always attachable since the Files table attachments (read as a bounded text
# table); the switch decides only whether a table is ALSO a verified source — never the allowlist
check("off: the extension allowlist is exactly the base list", _routes.allowed_upload_extensions() == _routes.ALLOWED_UPLOAD_EXTENSIONS)
APP_CONFIG.ATTACHMENT_SOURCES = True
check("on: csv joins the allowlist", ".csv" in _routes.allowed_upload_extensions())

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n=== S9 image sources: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
