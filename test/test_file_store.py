"""
Files and file stores (PROP-010) — the backend gate. No server, no network.

Sections
  A  envelope encryption: round trip, tamper, truncation, wrong key, rewrap, fingerprints
  B  backends: the conformance contract (local), key safety, S3 without boto3 fails softly
  C  resolution chain: explicit → agent → admin default → platform local; tier gate; recorded store wins
  D  the write path: no plaintext and no filename in any store; quota; store capacity; tier gate
  E  attachments: encrypted manifest, no plaintext in the session folder, delete, conversation purge,
     a kept attachment survives its conversation, the legacy migration (idempotent)
  F  keep from a turn: CSV from the persisted answer, chart pair, whole answer, duplicates, provenance
  G  download: policy (allowed · except data-derived · disabled), formats, formula neutralisation,
     with-evidence zip, headers, an audit row per download, bulk zip names refusals
  H  lifecycle: Recently deleted, restore, purge (object AND key), retention 0, migration between stores
  I  listing: facets, search over names/conversations/questions/agents, kept anchors
  J  capacity governance: provisioned vs held vs promised; per-user overrides
  K  policy settings validation (hardened mode locks encryption on)
  L  saved exports: a turn's, a component's, a conversation's; HTML refused; duplicates
  M  server-built exports: the tier's download policy and the audit; browser downloads recorded
  N  dashboard snapshot: one PDF from the tiles' own data, pictures only for drawn tiles
  O  flow File input / File output nodes
  P  the agent's own file acts (TDA_Files): the opt-in gate, list/read/save, Agent Authority
  Q  a file saved during its turn gets the turn's bill of materials afterwards
  R  tables are attachments: CSV · TSV · XLSX read as a bounded table, by upload and by reference
  S  cloud backends (Azure Blob, GCS) on fake SDK clients, and the one backend catalog
  T  an external key manager wraps the data keys: switch, read-through, binding, re-wrap, revoke

Run: PYTHONPATH=src python3 test/test_file_store.py
"""
import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

TMP = Path(tempfile.mkdtemp(prefix="uderia-files-"))
os.environ["TDA_FILES_DIR"] = str(TMP / "files")
os.environ["TDA_SESSIONS_DIR"] = str(TMP / "sessions")
# BOTH database handles point at the throwaway file: the sqlite_util one (APP_CONFIG) and the
# SQLAlchemy engine (TDA_AUTH_DB_URL, read at import) — the audit log and user lookups use it.
os.environ["TDA_AUTH_DB_URL"] = f"sqlite:///{TMP / 'auth.db'}"
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
APP_CONFIG.AUTH_DB_PATH = f"sqlite:///{TMP / 'auth.db'}"
from trusted_data_agent.api import routes  # noqa: E402,F401 — before the test tables (the prompt loader reads them)
from trusted_data_agent.auth import database as _authdb  # noqa: E402
assert str(TMP) in str(_authdb.DATABASE_URL), "the auth engine must not point at a real database"
from trusted_data_agent.auth.models import Base as _Base, User as _User  # noqa: E402
for _t in _Base.metadata.sorted_tables:
    try:
        _t.create(bind=_authdb.engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — an index the import already created
        pass
with _authdb.get_db_session() as _s:
    for _uid, _tier in (("u-files-0001", "user"), ("u-files-0002", "user"), ("admin", "admin")):
        _s.add(_User(id=_uid, username=_uid[:30], email=f"{_uid}@example.com", password_hash="x", profile_tier=_tier))

from trusted_data_agent.filestore import crypto, db, factory, formats, service, session_files  # noqa: E402
from trusted_data_agent.filestore import settings as fset  # noqa: E402
from trusted_data_agent.filestore.base import FileStoreCapability, FileStoreError, ObjectNotFound  # noqa: E402
from trusted_data_agent.filestore.local_backend import LocalFileStoreBackend  # noqa: E402

_passed = _failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:  # noqa: BLE001
        return e
    return None


def run(coro):
    return asyncio.run(coro)


MARK = b"PLAINTEXT-MARKER-7f3a9c"
USER = "u-files-0001"
OTHER = "u-files-0002"


def scan_for(marker: bytes, root: Path) -> list:
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and marker in p.read_bytes():
            hits.append(str(p))
    return hits


# ── A ────────────────────────────────────────────────────────────────────────
print("\nA  envelope encryption")
for size in (0, 1, 1000, crypto.CHUNK - 1, crypto.CHUNK, crypto.CHUNK + 1, 3 * crypto.CHUNK + 17):
    data = os.urandom(size)
    blob, wk = crypto.encrypt_object(data, "fid-1")
    check(f"round trip at {size} bytes", crypto.decrypt_object(blob, wk, "fid-1") == data)
blob, wk = crypto.encrypt_object(MARK * 100, "fid-2")
check("ciphertext does not contain the plaintext", MARK not in blob)
bad = bytearray(blob); bad[40] ^= 1
check("a flipped bit does not decrypt", isinstance(raises(lambda: crypto.decrypt_object(bytes(bad), wk, "fid-2")), crypto.FileCryptoError))
big, bwk = crypto.encrypt_object(os.urandom(2 * crypto.CHUNK + 5), "fid-3")
check("a truncated object does not decrypt (final-chunk flag)", isinstance(
    raises(lambda: crypto.decrypt_object(big[:crypto._HEADER.size + crypto.CHUNK + 16], bwk, "fid-3")), crypto.FileCryptoError))
check("a wrapped key is bound to its file id", isinstance(raises(lambda: crypto.unwrap_key(wk, "another-id")), crypto.FileCryptoError))
check("another master key cannot unwrap", isinstance(raises(lambda: crypto.unwrap_key(wk, "fid-2", "a-different-master-key-xxxxxxxxxxxxxx")), crypto.FileCryptoError))
re_wk = crypto.rewrap(wk, "fid-2", None or __import__("trusted_data_agent.core.key_policy", fromlist=["x"]).master_key(), "new-master-key-0123456789abcdefghijkl")
check("rewrap: the object opens under the new key, unchanged", crypto.decrypt_object(blob, re_wk, "fid-2", "new-master-key-0123456789abcdefghijkl") == MARK * 100)
check("fingerprint is stable and keyed", crypto.fingerprint(b"x") == crypto.fingerprint(b"x") and crypto.fingerprint(b"x") != __import__("hashlib").sha256(b"x").hexdigest()
      and crypto.fingerprint(b"x") != crypto.fingerprint(b"x", "other-master-key-zzzzzzzzzzzzzzzzzzzz"))
rec = crypto.seal_record({"a": 1, "t": "é"}, "manifest:u:s")
check("a sealed record opens only in its context", crypto.open_record(rec, "manifest:u:s") == {"a": 1, "t": "é"}
      and isinstance(raises(lambda: crypto.open_record(rec, "manifest:u:other")), crypto.FileCryptoError))

# ── B ────────────────────────────────────────────────────────────────────────
print("\nB  backends")
lb = LocalFileStoreBackend({"root": str(TMP / "conf")})
lb.initialize()
lb.put("owner1/obj1", b"hello")
check("local: put/get", lb.get("owner1/obj1") == b"hello")
check("local: exists", lb.exists("owner1/obj1") and not lb.exists("owner1/none"))
lb.delete("owner1/obj1"); lb.delete("owner1/obj1")
check("local: delete is idempotent and the object is gone", not lb.exists("owner1/obj1"))
check("local: a missing object raises ObjectNotFound", isinstance(raises(lambda: lb.get("owner1/none")), ObjectNotFound))
for key in ("../etc/passwd", "a/b/c", "a", "a/..", "a/b c", "/abs/key"):
    check(f"local: key {key!r} is refused", isinstance(raises(lambda k=key: lb.put(k, b"x")), FileStoreError))
t = lb.test_connection()
check("local: test_connection writes, reads, deletes a probe", t["success"] and "probe" in t["message"], t)
check("local: the probe leaves nothing behind", not any((TMP / "conf" / "_probe").glob("*")) if (TMP / "conf" / "_probe").exists() else True)
from trusted_data_agent.filestore.s3_backend import S3FileStoreBackend  # noqa: E402
try:
    import boto3  # noqa: F401
    HAVE_BOTO = True
except ImportError:
    HAVE_BOTO = False
s3 = S3FileStoreBackend({"bucket": "b", "endpoint_url": "http://127.0.0.1:1", "access_key_id": "x", "secret_access_key": "y", "region": "us-east-1"})
r = s3.test_connection()
check("s3: an unreachable endpoint fails test_connection softly (no raise)", r["success"] is False and r["message"], r)

# ── C ────────────────────────────────────────────────────────────────────────
print("\nC  resolution chain")
db.ensure_tables()
check("no stores configured → platform local", factory.resolve_store_id(None, USER) == fset.PLATFORM_LOCAL_ID)
eu = db.save_store_config({"name": "EU archive", "backend_type": "local", "config": {"root": str(TMP / "eu")}, "encrypt": True})
nfs = db.save_store_config({"name": "Shared NFS", "backend_type": "local", "config": {"root": str(TMP / "nfs")}, "encrypt": True})
check("store config secrets never come back masked as real values", "••••••" not in json.dumps(db.get_store_config(eu["id"], True)))
fset.save_settings({"tiers": {"user": {"allowed_stores": [fset.PLATFORM_LOCAL_ID, eu["id"]]}}})
check("an agent bound to an allowed store resolves to it",
      factory.resolve_store_id("p1", USER, profile={"fileStoreConfigId": eu["id"]}) == eu["id"])
check("an agent bound to a store its tier may not use falls back to the default",
      factory.resolve_store_id("p1", USER, profile={"fileStoreConfigId": nfs["id"]}) == fset.PLATFORM_LOCAL_ID)
db.set_default_store(eu["id"])
check("the admin default applies when the agent names nothing",
      factory.resolve_store_id("p1", USER, profile={}) == eu["id"] and db.default_store_id() == eu["id"])
check("an explicit store id wins (admin tooling)", factory.resolve_store_id("p1", USER, explicit=nfs["id"]) == nfs["id"])
check("a bound store that no longer exists falls back",
      factory.resolve_store_id("p1", USER, profile={"fileStoreConfigId": "fs-gone"}) == eu["id"])
db.set_default_store(fset.PLATFORM_LOCAL_ID)

# ── D ────────────────────────────────────────────────────────────────────────
print("\nD  the write path")
row = service.put_file(user_uuid=USER, data=MARK * 50, filename="secret-report-name.csv", source_kind="table",
                       profile={"fileStoreConfigId": eu["id"]}, session_id="s1", turn_id=1)
check("the file lands in the agent's store", row["store_config_id"] == eu["id"])
check("no plaintext in any store", scan_for(MARK, TMP / "eu") == [] and scan_for(MARK, TMP / "files") == [])
check("no filename reaches the store", not any("secret-report-name" in str(p) for p in (TMP / "eu").rglob("*")))
check("the object key is opaque", "/" in row["object_key"] and "secret" not in row["object_key"])
check("read_bytes returns the plaintext", service.read_bytes(row) == MARK * 50)
check("the content fingerprint is the keyed HMAC", row["content_hmac"] == crypto.fingerprint(MARK * 50))
db.set_default_store(eu["id"])
r2 = service.put_file(user_uuid=USER, data=b"later", filename="later.txt", source_kind="export", profile={})
db.set_default_store(fset.PLATFORM_LOCAL_ID)
check("rebinding later does not move a file: it is read from its recorded store", service.read_bytes(row) == MARK * 50 and r2["store_config_id"] == eu["id"])
fset.save_settings({"tiers": {"user": {"quota_mb": 1}}})
e = raises(lambda: service.put_file(user_uuid=USER, data=os.urandom(2 * 1024 * 1024), filename="big.bin", source_kind="export"), service.FilesError)
check("quota: a kept file over the tier quota is refused with 507 and both sizes", e and e.status == 507 and "of" in e.message, e and e.message)
att = service.put_file(user_uuid=USER, data=os.urandom(2 * 1024 * 1024), filename="att.pdf", source_kind="upload",
                       lifetime="conversation", origin_kind="upload", enforce_quota=False)
check("quota: a conversation attachment is not refused (chat keeps working)", att["lifetime"] == "conversation")
service.purge(att)
fset.save_settings({"tiers": {"user": {"quota_mb": 1024}}})
db.save_store_config({"id": nfs["id"], "capacity_mb": 1})
e = raises(lambda: service.put_file(user_uuid=USER, data=os.urandom(1024 * 1024 + 1), filename="x.bin", source_kind="export", store_id=nfs["id"]), service.FilesError)
check("capacity: a store at its provisioned capacity refuses with store_full", e and e.code == "store_full", e and e.message)
fset.save_settings({"tiers": {"user": {"files_enabled": False}}})
e = raises(lambda: service.put_file(user_uuid=USER, data=b"x", filename="x.md", source_kind="answer"), service.FilesError)
check("tier gate: keeping is refused when Files is off for the tier", e and e.code == "files_disabled")
fset.save_settings({"tiers": {"user": {"files_enabled": True}}})
e = raises(lambda: service.put_file(user_uuid=USER, data=b"x" * (fset.max_file_bytes() + 1), filename="x.md", source_kind="answer"), service.FilesError)
check("size limit: a file over max_file_mb is refused with 413", e and e.status == 413)

# ── E ────────────────────────────────────────────────────────────────────────
print("\nE  attachments")
SID = "sess-att-1"
(TMP / "sessions" / USER).mkdir(parents=True, exist_ok=True)
(TMP / "sessions" / USER / f"{SID}.json").write_text(json.dumps({"id": SID, "name": "Q3 revenue review", "user_uuid": USER}))
ent = routes._ingest_upload(USER, SID, "notes.txt", MARK + b" in a text file")
up = session_files.upload_dir(USER, SID)
check("the session folder holds only the sealed manifest", sorted(p.name for p in up.iterdir()) == ["manifest.enc"])
check("no plaintext anywhere under sessions or stores", scan_for(MARK, TMP / "sessions") == [] and scan_for(MARK, TMP / "files") == [])
frow = db.get_file(ent["file_id"], USER)
check("the attachment is a conversation-lifetime upload file with its session and title",
      frow and frow["lifetime"] == "conversation" and frow["source_kind"] == "upload" and frow["session_title"] == "Q3 revenue review")
check("the manifest references the file, no path", "path" not in session_files.load_manifest(USER, SID)[ent["file_id"]]
      and session_files.load_manifest(USER, SID)[ent["file_id"]]["file_ref"] == ent["file_id"])
check("another user cannot read it through the accessor", session_files.read_bytes(OTHER, SID, ent["file_id"]) is None)
ent2 = routes._ingest_upload(USER, SID, "keepme.md", b"# Kept " + MARK)
service.keep(ent2["file_id"], USER)
check("Keep flips the lifetime without copying", db.get_file(ent2["file_id"], USER)["lifetime"] == "kept"
      and len(db.query_files(USER, session_id=SID)) == 2)
check("deleting a pending attachment removes it (and its file)", routes._delete_upload(USER, SID, ent["file_id"])
      and db.get_file(ent["file_id"], USER) is None)
from trusted_data_agent.core import session_manager  # noqa: E402
session_manager._cleanup_session_uploads(USER, SID)
check("deleting the conversation keeps a kept attachment", db.get_file(ent2["file_id"], USER) is not None
      and service.read_bytes(db.get_file(ent2["file_id"], USER)).startswith(b"# Kept"))
check("…and removes the session's uploads folder", not up.exists())
# legacy migration
LSID = "sess-legacy-1"
ld = session_files.upload_dir(USER, LSID, create=True)
lid = "11111111-2222-3333-4444-555555555555"
(ld / f"{lid}.txt").write_bytes(MARK + b" legacy")
(ld / "manifest.json").write_text(json.dumps({lid: {"path": str(ld / f"{lid}.txt"), "filename": "old.txt", "content_type": "text/plain",
                                                    "file_size": 30, "extracted_text": "legacy text", "is_image": False}}))
check("legacy: readable before migration", session_files.read_bytes(USER, LSID, lid) == MARK + b" legacy")
m1 = session_files.migrate_legacy()
check("legacy: migration moves it", m1["moved"] == 1 and m1["failed"] == 0, m1)
check("legacy: no plaintext left, only the sealed manifest", sorted(p.name for p in ld.iterdir()) == ["manifest.enc"]
      and scan_for(MARK, TMP / "sessions") == [])
check("legacy: same bytes and same extracted text afterwards", session_files.read_bytes(USER, LSID, lid) == MARK + b" legacy"
      and session_files.load_manifest(USER, LSID)[lid]["extracted_text"] == "legacy text")
m2 = session_files.migrate_legacy()
check("legacy: migration is idempotent", m2["moved"] == 0 and m2["sessions"] == 0, m2)
check("block_bytes reads a data block", session_files.block_bytes({"data": b"abc"}) == b"abc")

# ── F ────────────────────────────────────────────────────────────────────────
print("\nF  keep from a turn")
KSID = "sess-keep-1"
spec = json.dumps({"type": "Column", "options": {"data": [{"region": "EMEA", "q3": 4.58}, {"region": "APAC", "q3": 3.27}]}})
html = ('<div class="ans-ev"><div class="ans-tw"><table class="ans-t"><thead><tr><th>Region</th><th>Q3</th></tr></thead>'
        '<tbody><tr><td>EMEA</td><td>4.58</td></tr><tr><td>=HYPERLINK("http://x")</td><td>-3.0</td></tr></tbody></table></div>'
        '<div class="ans-tf"><details class="ans-stmt"><summary>Statement</summary><pre>SELECT 1</pre></details></div></div>'
        f"<div class=\"response-card\"><div id=\"chart-render-target-1\" class=\"chart-render-target\" data-spec='{spec}'></div></div>")
bom = {"profile_id": "prof-optim", "agent": {"state": "trusted"}, "deployment": {"state": "trusted", "summary": {"model": "gemini-x"}},
       "parts": [{"state": "trusted"}, {"state": "trusted"}]}
(TMP / "sessions" / USER / f"{KSID}.json").write_text(json.dumps({
    "id": KSID, "name": "Revenue review", "user_uuid": USER, "profile_id": "prof-optim",
    "last_turn_data": {"workflow_history": [{"turn": 4, "user_query": "Revenue by region for Q3", "final_summary_html": html,
                                              "final_summary_text": "Q3 revenue grew.\n\n- EMEA up", "profile_tag": "OPTIM",
                                              "profile_type": "tool_enabled", "tools_used": ["base_readQuery"], "bom": bom}]}}))
out = run(service.keep_from_turn(USER, session_id=KSID, turn_id=4, kind="table", anchor="table:0", headers=["Region", "Q3"]))
t = out["files"][0]
check("table: one CSV named from its headers", t["filename"] == "region_q3.csv" and t["category"] == "table", t["filename"])
trow = db.get_file(t["id"], USER)
check("table: the CSV comes from the persisted answer", b"EMEA,4.58" in service.read_bytes(trow))
check("table: provenance stamped (session, turn, question, agent, bom, data-derived)",
      trow["session_id"] == KSID and trow["turn_id"] == 4 and trow["question"] == "Revenue by region for Q3"
      and trow["profile_tag"] == "OPTIM" and trow["data_derived"] and json.loads(trow["bom_json"])["profile_id"] == "prof-optim")
check("table: the statement inside the row's footer is not part of the table", b"SELECT" not in service.read_bytes(trow))
e = raises(lambda: run(service.keep_from_turn(USER, session_id=KSID, turn_id=4, kind="table", anchor="table:0", headers=["Region", "Q3"])), service.FilesError)
check("duplicate: keeping the same table again offers the existing file (409)", e and e.code == "duplicate" and e.extra["file"]["id"] == t["id"])
out2 = run(service.keep_from_turn(USER, session_id=KSID, turn_id=4, kind="table", anchor="table:0", allow_duplicate=True))
check("duplicate: 'Keep another copy' keeps a second file", out2["files"][0]["id"] != t["id"])
png = b"\x89PNG\r\n\x1a\nfakepng"
oc = run(service.keep_from_turn(USER, session_id=KSID, turn_id=4, kind="chart", anchor="chart:0", client_bytes=png))
check("chart: a PNG and the CSV of its own data, as one pair", len(oc["files"]) == 2 and oc["files"][0]["pair_id"]
      and oc["files"][0]["pair_id"] == oc["files"][1]["pair_id"] and {f["ext"] for f in oc["files"]} == {"png", "csv"})
csvrow = db.get_file([f for f in oc["files"] if f["ext"] == "csv"][0]["id"], USER)
check("chart: the CSV holds the spec's data rows", service.read_bytes(csvrow).decode("utf-8-sig").splitlines()[1] == "EMEA,4.58")
oa = run(service.keep_from_turn(USER, session_id=KSID, turn_id=4, kind="answer"))
check("answer: Markdown of the whole answer, named from the question",
      oa["files"][0]["filename"] == "revenue_by_region_for_q3.md" and service.read_bytes(db.get_file(oa["files"][0]["id"], USER)).startswith(b"Q3 revenue grew."))
d = service.describe(trow, service.store_index())
check("describe: evidence summary from the stored bill of materials", d["evidence"] == "4 parts · all signed when answered" and d["model"] == "gemini-x", d["evidence"])
anchors = service.kept_anchors(USER, KSID, 4)
check("kept anchors: the table, chart and answer are listed for 'Kept · Open'",
      {a["anchor"] for a in anchors} >= {"4:table:0", "4:chart:0", "4:answer"}, anchors)
e = raises(lambda: run(service.keep_from_turn(USER, session_id=KSID, turn_id=99, kind="answer")), service.FilesError)
check("keep from an answer that does not exist → 404", e and e.status == 404)
e = raises(lambda: run(service.keep_from_turn(OTHER, session_id=KSID, turn_id=4, kind="answer")), service.FilesError)
check("another user cannot keep from my conversation", e and e.status == 404)

# ── G ────────────────────────────────────────────────────────────────────────
print("\nG  download")
data, name, media = run(service.download(trow, USER, "csv"))
lines = data.decode("utf-8-sig").splitlines()
check("CSV: UTF-8 with a BOM", data.startswith(b"\xef\xbb\xbf"))
check("CSV: a formula cell is neutralised, a negative number is not", "'=HYPERLINK(\"\"http://x\"\")" in lines[2] and lines[2].endswith(",-3.0"), lines)
if formats.xlsx_available():
    data, name, media = run(service.download(trow, USER, "xlsx"))
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    check("XLSX: a workbook, numbers as numbers, formulas neutralised", name.endswith(".xlsx") and ws["B2"].value == 4.58
          and str(ws["A3"].value).startswith("'="), (ws["B2"].value, ws["A3"].value))
mdrow = db.get_file(oa["files"][0]["id"], USER)
data, name, media = run(service.download(mdrow, USER, "docx"))
check("Markdown → DOCX", name.endswith(".docx") and zipfile.is_zipfile(io.BytesIO(data)))
data, name, media = run(service.download(mdrow, USER, "pdf"))
check("Markdown → PDF", name.endswith(".pdf") and data.startswith(b"%PDF"))
check("an unoffered format is refused", isinstance(raises(lambda: run(service.download(mdrow, USER, "xlsx")), service.FilesError), service.FilesError))
data, name, media = run(service.download(trow, USER, "csv", with_evidence=True))
z = zipfile.ZipFile(io.BytesIO(data))
check("with evidence: a zip of the file and the evidence (record when the live bundle is unavailable)",
      name.endswith("_with_evidence.zip") and any(n.endswith(".csv") for n in z.namelist())
      and ("evidence.json" in z.namelist() or "evidence_record.json" in z.namelist()) and "README.txt" in z.namelist(), z.namelist())
fset.save_settings({"tiers": {"user": {"download_policy": "allowed_except_data_derived"}}})
e = raises(lambda: run(service.download(trow, USER, "csv")), service.FilesError)
check("policy: data-derived files are refused under 'allowed except data-derived', naming the policy",
      e and e.code == "download_data_derived" and "data source" in e.message)
plain = service.put_file(user_uuid=USER, data=b"no data here", filename="note.md", source_kind="answer", data_derived=False)
check("policy: a file not from a data source still downloads", run(service.download(plain, USER, "md"))[0] == b"no data here")
fset.save_settings({"tiers": {"user": {"download_policy": "disabled"}}})
e = raises(lambda: run(service.download(plain, USER)), service.FilesError)
check("policy: 'disabled' refuses every download with the act owed", e and e.code == "download_disabled" and "admin" in e.message)
fset.save_settings({"tiers": {"user": {"download_policy": "allowed"}}})
check("ownership: another user's download is refused", isinstance(raises(lambda: run(service.download(trow, OTHER))), service.FilesError))
zbytes, skipped = run(service.download_many([t["id"], plain["id"], "no-such-id"], USER))
zz = zipfile.ZipFile(io.BytesIO(zbytes))
check("bulk: a zip of what may go, the refusals named inside", len(skipped) == 1 and "NOT_INCLUDED.txt" in zz.namelist()
      and sum(1 for n in zz.namelist() if n != "NOT_INCLUDED.txt") == 2, (skipped, zz.namelist()))
from trusted_data_agent.auth.models import AuditLog  # noqa: E402
from trusted_data_agent.auth.database import get_db_session  # noqa: E402
try:
    with get_db_session() as sess:
        n_audit = sess.query(AuditLog).filter_by(action="file_download").count()
    check("audit: downloads are recorded as file_download", n_audit >= 6, n_audit)
except Exception as ex:  # noqa: BLE001
    print(f"  NOTE  audit table not reachable in this isolated run ({ex.__class__.__name__}); covered by the REST rehearsal")

# ── H ────────────────────────────────────────────────────────────────────────
print("\nH  lifecycle")
service.soft_delete(plain["id"], USER)
pr = db.get_file(plain["id"], USER)
check("delete moves to Recently deleted with a purge date", pr["deleted_at"] and pr["purge_after"])
check("a deleted file is not downloadable", isinstance(raises(lambda: run(service.download(pr, USER))), service.FilesError))
service.restore(plain["id"], USER)
check("restore brings it back", db.get_file(plain["id"], USER)["deleted_at"] is None)
service.soft_delete(plain["id"], USER)
db.update_file(plain["id"], purge_after="2000-01-01T00:00:00Z")
obj_key, sid = pr["object_key"], pr["store_config_id"]
check("purge_due removes what retention no longer keeps", service.purge_due() == 1 and db.get_file(plain["id"]) is None)
check("…the object is gone from its store too", not factory.backend_for_store(sid)[1].exists(obj_key))
fset.save_settings({"retention_days": 0})
gone = service.put_file(user_uuid=USER, data=b"bye", filename="bye.md", source_kind="answer")
service.soft_delete(gone["id"], USER)
check("retention 0: delete purges at once", db.get_file(gone["id"]) is None)
fset.save_settings({"retention_days": 30})
db.save_store_config({"id": nfs["id"], "capacity_mb": ""})
plan = service.migration_plan(eu["id"], nfs["id"])
check("migration plan counts files and bytes", plan["files"] >= 2 and plan["bytes"] > 0, plan)
res = service.migrate(eu["id"], nfs["id"], "admin")
check("migration moves every file and they still read", res["failed"] == 0 and res["moved"] == plan["files"]
      and service.read_bytes(db.get_file(row["id"], USER)) == MARK * 50 and db.files_in_store(eu["id"]) == [], res)

# ── I ────────────────────────────────────────────────────────────────────────
print("\nI  listing")
lst = service.list_files(USER)
check("facets count every category", lst["facets"]["all"] == len(lst["files"]) and lst["facets"]["table"] >= 2, lst["facets"])
_rr = service.list_files(USER, q="revenue review")["files"]
check("search reads conversation titles", len(_rr) >= 4 and all("revenue review" in (f["session_title"] or "").lower() for f in _rr)
      and any(f["session_title"] == "Revenue review" for f in _rr))
check("search reads questions", service.list_files(USER, q="region for q3")["total"] >= 4)
check("search reads IFOC agent words", service.list_files(USER, q="optimize")["total"] >= 4)
check("kind=deleted lists Recently deleted only", all(f["deleted_at"] for f in service.list_files(USER, kind="deleted")["files"]))
check("another user sees none of mine", service.list_files(OTHER)["total"] == 0)
check("describe marks non-attachable types with the reason", (not service.describe({**trow, "filename": "g.ttl"})["attachable"])
      and "Canvas" in service.describe({**trow, "filename": "g.ttl"})["attach_note"])

# ── J ────────────────────────────────────────────────────────────────────────
print("\nJ  capacity governance")
db.save_store_config({"id": nfs["id"], "capacity_mb": 100})
rep = service.capacity_report()
st = {s["id"]: s for s in rep["stores"]}
check("capacity: a provisioned store reports provisioned, held and its disk", st[nfs["id"]]["provisioned_bytes"] == 100 * 1024 * 1024
      and st[nfs["id"]]["held_bytes"] > 0 and st[nfs["id"]]["disk_free_label"])
check("capacity: totals report used and promised", rep["used_bytes"] > 0 and "promised_bytes" in rep and len(rep["tiers"]) == 3)
fset.set_user_quota(USER, 2)
check("override: a per-user quota wins over the tier's", fset.quota_bytes_for_user(USER, "user") == 2 * 1024 * 1024)
fset.set_user_quota(USER, None)
check("override: removing it restores the tier quota", fset.quota_bytes_for_user(USER, "user") == fset.quota_bytes("user"))
check("override: a negative quota is refused", isinstance(raises(lambda: fset.set_user_quota(USER, -1), ValueError), ValueError))

# ── K ────────────────────────────────────────────────────────────────────────
print("\nK  policy settings")
check("an unknown download policy is refused", isinstance(raises(lambda: fset.save_settings({"tiers": {"user": {"download_policy": "sometimes"}}}), ValueError), ValueError))
check("retention outside 0..3650 is refused", isinstance(raises(lambda: fset.save_settings({"retention_days": 99999}), ValueError), ValueError))
check("the admin tier cannot be gated", isinstance(raises(lambda: fset.save_settings({"tiers": {"admin": {"download_policy": "disabled"}}}), ValueError), ValueError))
os.environ["TDA_SECURITY_PROFILE"] = "hardened"
check("hardened: encryption reads as required", fset.require_encryption() is True and fset.get_settings()["require_encryption_locked"])
check("hardened: turning encryption off is refused", isinstance(raises(lambda: fset.save_settings({"require_encryption": False}), ValueError), ValueError))
os.environ.pop("TDA_SECURITY_PROFILE")

# ── L ────────────────────────────────────────────────────────────────────────
print("\nL  saved exports (Save to Files)")
rcpt = b'{"receipt_version": 1, "turn": 4}'
ex = run(service.save_export(USER, rcpt, "uderia_answer_receipt_turn4.json", session_id=KSID, turn_id=4))
exrow = db.get_file(ex["id"], USER)
check("a turn's export carries the turn's provenance (conversation, turn, question, agent, bill of materials)",
      exrow["session_id"] == KSID and exrow["turn_id"] == 4 and exrow["question"] == "Revenue by region for Q3"
      and exrow["bom_json"] and exrow["source_kind"] == "component")
kg = run(service.save_export(USER, b"@prefix ex: <x> .", "knowledge-graph-sales.ttl", component_id="knowledge_graph",
                             instance_id="kg-42", profile_id="prof-optim"))
kgrow = db.get_file(kg["id"], USER)
check("a component's export names the component and its instance", kgrow["component_id"] == "knowledge_graph"
      and kgrow["instance_id"] == "kg-42" and kgrow["origin_kind"] == "export" and kg["component_id"] == "knowledge_graph")
cv = run(service.save_export(USER, b'{"session": 1}', "session_x.json", session_id=KSID))
check("a conversation-level export is data-derived when a turn read a data source", db.get_file(cv["id"], USER)["data_derived"] == 1
      and cv["session_title"] == "Revenue review")
check("an HTML export is never stored", isinstance(raises(lambda: run(service.save_export(USER, b"<html>", "page.html")), service.FilesError), service.FilesError))
e = raises(lambda: run(service.save_export(USER, b"@prefix ex: <x> .", "again.ttl")), service.FilesError)
check("the same export again answers 409 with the existing file", e and e.code == "duplicate" and e.extra["file"]["id"] == kg["id"])
check("…unless another copy is asked for", run(service.save_export(USER, b"@prefix ex: <x> .", "again.ttl", allow_duplicate=True))["id"] != kg["id"])

# ── M ────────────────────────────────────────────────────────────────────────
print("\nM  server-built exports: the tier's policy and the audit")
from trusted_data_agent.auth.models import AuditLog as _Audit  # noqa: E402
service.govern_export(USER, what="knowledge graph", filename="kg.json", size=12)
with _authdb.get_db_session() as _s:
    _rows = [r for r in _s.query(_Audit).filter_by(user_id=USER, action="file_download").all() if "kg.json" in (r.details or "")]
    check("an allowed server export is recorded as file_download, naming what left", bool(_rows) and "knowledge graph" in _rows[-1].details)
fset.save_settings({"tiers": {"user": {"download_policy": "disabled"}}})
e = raises(lambda: service.govern_export(USER, what="metric", filename="m.ttl", size=3), service.FilesError)
check("downloads off for the tier: a server export is refused (403)", e and e.status == 403 and e.code == "download_disabled")
fset.save_settings({"tiers": {"user": {"download_policy": "allowed_except_data_derived"}}})
check("except data-derived: a provenance export (not data) still leaves",
      raises(lambda: service.govern_export(USER, what="provenance", filename="p.json", size=3)) is None)
e = raises(lambda: service.govern_export(USER, what="instance", filename="d.json", size=3, data_derived=True), service.FilesError)
check("except data-derived: a data-derived export is refused", e and e.code == "download_data_derived")
fset.save_settings({"tiers": {"user": {"download_policy": "allowed"}}})
service.record_browser_export(USER, what="audit pack", filename="uderia_audit_pack.json", size=99)
with _authdb.get_db_session() as _s:
    check("a browser-built download is recorded too", any("uderia_audit_pack.json" in (r.details or "")
          for r in _s.query(_Audit).filter_by(user_id=USER, action="file_download").all()))

# ── N ────────────────────────────────────────────────────────────────────────
print("\nN  dashboard snapshot")
import sqlite3 as _sq  # noqa: E402
_c = _sq.connect(str(TMP / "auth.db"))
_c.executescript((ROOT / "schema" / "39_dashboards.sql").read_text())
_c.execute("INSERT INTO dashboards (id, user_id, name) VALUES ('dash-1', ?, 'Weekly sales')", (USER,))
_c.execute("INSERT INTO dashboards (id, user_id, name) VALUES ('dash-empty', ?, 'Empty')", (USER,))
_tbl = "<table><thead><tr><th>Region</th><th>Sales</th></tr></thead><tbody><tr><td>EMEA</td><td>10</td></tr></tbody></table>"
for tid, title, comp, html_, ref in (("t-chart", "Revenue trend", "chart", "", "2026-10-24T08:00:00Z"),
                                     ("t-table", "By region", "table", _tbl, "2026-10-24T09:30:00Z"),
                                     ("t-kpi", "Orders", "kpi", "<div><b>42</b><p>orders today</p></div>", None)):
    _c.execute("INSERT INTO dashboard_tiles (id, dashboard_id, user_id, title, component_id, html, profile_id, profile_tag, last_refreshed) "
               "VALUES (?, 'dash-1', ?, ?, ?, ?, 'prof-optim', 'OPTIM', ?)", (tid, USER, title, comp, html_, ref))
_c.commit(); _c.close()
from PIL import Image  # noqa: E402
_b = io.BytesIO(); Image.new("RGB", (600, 300), (40, 90, 160)).save(_b, "PNG")
snap = service.snapshot_dashboard(USER, "dash-1", {"t-chart": _b.getvalue()})
srow = db.get_file(snap["id"], USER)
sdata = service.read_bytes(srow)
check("snapshot: one PDF, kept in Files", sdata.startswith(b"%PDF") and snap["ext"] == "pdf" and srow["source_kind"] == "snapshot")
src = json.loads(srow["source_json"])
check("snapshot: records the dashboard and each tile's refresh time",
      srow["component_id"] == "dashboard" and srow["instance_id"] == "dash-1" and src["dashboard_name"] == "Weekly sales"
      and {t_["tile_id"]: t_["last_refreshed"] for t_ in src["tiles"]} == {"t-chart": "2026-10-24T08:00:00Z", "t-table": "2026-10-24T09:30:00Z", "t-kpi": None})
check("snapshot: what the browser drew is a picture, the rest is written from the tile's own data",
      {t_["tile_id"]: t_["captured_as"] for t_ in src["tiles"]} == {"t-chart": "picture", "t-table": "table", "t-kpi": "text"})
check("snapshot: placed by the one agent all tiles share, data-derived", srow["profile_id"] == "prof-optim" and srow["data_derived"] == 1)
e = raises(lambda: service.snapshot_dashboard(OTHER, "dash-1", {}), service.FilesError)
check("snapshot: another user's dashboard is not found", e and e.status == 404)
e = raises(lambda: service.snapshot_dashboard(USER, "dash-empty", {}), service.FilesError)
check("snapshot: an empty dashboard says so", e and e.code == "empty")
check("snapshot: a picture that is not an image falls back to the tile's data",
      service.read_bytes(db.get_file(service.snapshot_dashboard(USER, "dash-1", {"t-chart": b"not a png"})["id"], USER)).startswith(b"%PDF"))

# ── O ────────────────────────────────────────────────────────────────────────
print("\nO  flow File input / File output nodes")
from trusted_data_agent.core import flow_executor as fx, flow_db as _fdb  # noqa: E402
tin = run(fx._execute_file_input_node({"id": "n1", "label": "Accounts", "data": {"file_id": t["id"]}}, {}, USER))
check("file input: a kept table arrives as rows — the shape a query gives", tin["columns"] == ["Region", "Q3"]
      and tin["output"][0] == {"Region": "EMEA", "Q3": "4.58"} and tin["row_count"] == 2)
check("file input: the Excel-safety apostrophe is not part of the value", tin["output"][1]["Region"] == '=HYPERLINK("http://x")', tin["output"][1])
tmd = run(fx._execute_file_input_node({"id": "n2", "data": {"file_id": oa["files"][0]["id"]}}, {}, USER))
check("file input: a document arrives as its text", isinstance(tmd["output"], str) and tmd["output"].startswith("Q3 revenue grew."))
e = raises(lambda: run(fx._execute_file_input_node({"id": "n3", "label": "Gone", "data": {"file_id": "nope"}}, {}, USER)), RuntimeError)
check("file input: a missing file names the node and the fix", e and "Gone" in str(e) and "inspector" in str(e))
_png_id = [f for f in oc["files"] if f["ext"] == "png"][0]["id"]
e = raises(lambda: run(fx._execute_file_input_node({"id": "n4", "label": "Pic", "data": {"file_id": _png_id}}, {}, USER)), RuntimeError)
check("file input: a picture cannot be read, and says what can", e and "table, JSON, text or document" in str(e))
e = raises(lambda: run(fx._execute_file_input_node({"id": "n5", "label": "Theirs", "data": {"file_id": t["id"]}}, {}, OTHER)), RuntimeError)
check("file input: another user's file is not readable", e is not None)
check("file output bytes: rows → CSV, an object → JSON, text → Markdown",
      fx._file_output_bytes([{"a": 1, "b": None}], "auto")[1] == "csv" and fx._file_output_bytes({"k": 1}, "auto")[1] == "json"
      and fx._file_output_bytes("# hi", "auto") == (b"# hi", "md"))
check("file output bytes: a list of dicts writes the union of their keys", fx._file_output_bytes([{"a": 1}, {"b": 2}], "csv")[0].decode("utf-8-sig").splitlines()[0] == "a,b")
_orig = (_fdb.get_run, _fdb.get_flow)
_fdb.get_run = lambda rid: {"flow_id": "flow-7", "run_id": rid, "started_at": "2026-10-24T10:00:00Z"}
_fdb.get_flow = lambda fid, uid: {"flow_id": fid, "name": "Account summaries"}
try:
    fo = run(fx._execute_file_output_node({"id": "n9", "label": "Keep summary", "data": {"filename": "summary_{region}_{date}"}},
                                          {"output": [{"account": "A", "n": 3}], "region": "emea"}, USER, "run-abcdef1234",
                                          caller_session_id=KSID))
finally:
    _fdb.get_run, _fdb.get_flow = _orig
forow = db.get_file(fo["file_id"], USER)
check("file output: kept as CSV under the rendered name", forow["filename"].startswith("summary_emea_2") and forow["filename"].endswith(".csv"), forow["filename"])
check("file output: its origin is the flow run (flow, run, node, flow and node names)",
      forow["origin_kind"] == "flow_run" and forow["flow_id"] == "flow-7" and forow["flow_run_id"] == "run-abcdef1234"
      and forow["flow_node_id"] == "n9" and json.loads(forow["source_json"])["flow_name"] == "Account summaries")
check("file output: the input passes on unchanged", fo["output"] == [{"account": "A", "n": 3}])
e = raises(lambda: run(fx._execute_file_output_node({"id": "n10", "label": "Empty", "data": {}}, {"output": ""}, USER, "run-x")), RuntimeError)
check("file output: nothing to keep is an error that names the node", e and "Empty" in str(e))
check("flow listing: the run's files are found by flow", any(f["id"] == fo["file_id"] for f in service.list_files(USER, flow_id="flow-7")["files"]))

# ── P ────────────────────────────────────────────────────────────────────────
print("\nP  the agent's own file acts (TDA_Files)")
sys.path.insert(0, str(ROOT))
from components.builtin.files.handler import FilesComponentHandler  # noqa: E402
from trusted_data_agent.agent import result_sets  # noqa: E402
from trusted_data_agent.core import agent_authority as _aa  # noqa: E402
H = FilesComponentHandler()
OPT = {"componentConfig": {"files": {"enabled": True, "agentTool": True}}}
check("tool gate: offered only with the owner's opt-in", H.offers_tool(OPT) is True and H.offers_tool({}) is False
      and H.offers_tool({"componentConfig": {"files": {"enabled": True}}}) is False)
_prof = {"cur": {}}
H._profile = lambda ctx: _prof["cur"]
CTX = {"user_uuid": USER, "session_id": KSID, "profile_id": "prof-optim", "current_turn_number": 5, "user_query": "Keep the totals"}
r = run(H.process({"action": "list"}, CTX))
check("a call without the opt-in is refused, naming where to turn it on", r.spec.get("error") and "Capabilities" in r.spec["error"])
_prof["cur"] = OPT
r = run(H.process({"action": "list", "query": "region"}, CTX))
check("list: finds files by name, headless", r.metadata.get("suppress_inline") and any(f["name"] == "region_q3.csv" for f in r.spec["files"]))
APP_CONFIG.COMPUTE_LINEAGE = True
result_sets.begin_turn(KSID, 5)
r = run(H.process({"action": "read", "name": "region_q3.csv"}, CTX))
check("read: a table arrives as columns and rows, and as a statement of this turn",
      r.spec.get("columns") == ["Region", "Q3"] and r.spec.get("row_count") == 2 and r.spec.get("statement") == 1
      and result_sets.get(KSID, 1)["source"]["file_id"] == r.spec["file_id"], r.spec)
check("read: the newest file of that name is the one read", r.spec["file_id"] == out2["files"][0]["id"])
check("read: the use is recorded (Used in)", any(u["turn_id"] == 5 for u in db.uses_of(r.spec["file_id"])))
_ann = []
_orig_ann = service._announce
service._announce = lambda u, a, ids=None, sid=None: _ann.append((a, list(ids or [])))
try:
    service.record_use(t["id"], USER, KSID, 6)
finally:
    service._announce = _orig_ann
check("a recorded use is announced live ('used'), so an open panel shows it without a reload", ("used", [t["id"]]) in _ann, _ann)
r = run(H.process({"action": "read", "name": "q3"}, CTX))
check("read: several matches ask for the file_id", r.spec.get("error") and "file_id" in r.spec["error"], r.spec)
result_sets.record(KSID, "base_readQuery", {"sql": "SELECT region, total FROM s"}, {"results": [{"region": "EMEA", "total": 10}, {"region": "APAC", "total": 7}]})
r = run(H.process({"action": "save", "name": "region totals", "statement": "latest"}, CTX))
srow2 = db.get_file(r.spec["file"]["id"], USER)
check("save statement: the CSV is written from the rows the platform holds, never retyped",
      service.read_bytes(srow2).decode("utf-8-sig").splitlines() == ["region,total", "EMEA,10", "APAC,7"] and srow2["data_derived"] == 1)
check("save: the file carries the turn (conversation, turn, question, agent) and renders inline",
      srow2["session_id"] == KSID and srow2["turn_id"] == 5 and srow2["question"] == "Keep the totals"
      and srow2["source_kind"] == "agent" and not r.metadata.get("suppress_inline"))
r2 = run(H.process({"action": "save", "name": "notes.md", "content": "# Totals\n\nEMEA leads."}, CTX))
check("save content: text the model wrote, typed by its extension", r2.spec["file"]["filename"] == "notes.md", r2.spec)
r3 = run(H.process({"action": "save", "name": "notes again.md", "content": "# Totals\n\nEMEA leads."}, CTX))
check("save: the same content again returns the existing file, said so", r3.spec["already"] is True and r3.spec["file"]["id"] == r2.spec["file"]["id"])
r4 = run(H.process({"action": "save", "name": "page.html", "content": "<script>x</script>"}, CTX))
check("save: an HTML file is refused", bool(r4.spec.get("error")))
r5 = run(H.process({"action": "save", "statement": "9"}, CTX))
check("save: a statement this turn does not have is refused, with the alternative", r5.spec.get("error") and "content" in r5.spec["error"], r5.spec)
_tok = _aa.set_current_principal(_aa.AgentPrincipal(owner_uuid=USER, profile_id="prof-optim",
                                 scope=_aa.AgentScope(data_surface_products=frozenset({"p"}), data_surface_exclusive=True)))
try:
    r6 = run(H.process({"action": "read", "file_id": t["id"]}, CTX))
    r7 = run(H.process({"action": "read", "file_id": r2.spec["file"]["id"]}, CTX))
finally:
    _aa.reset_current_principal(_tok)
check("Agent Authority: a surface-only agent may not read a data-derived file", r6.spec.get("error") and _aa.SURFACE_DENIAL_MARKER in r6.spec["error"], r6.spec)
check("Agent Authority: …but may read a file that is not data", not r7.spec.get("error") and r7.spec.get("text", "").startswith("# Totals"), r7.spec)
APP_CONFIG.COMPUTE_LINEAGE = False

# ── Q ────────────────────────────────────────────────────────────────────────
print("\nQ  the bill of materials of a file saved during its turn")
_sf = TMP / "sessions" / USER / f"{KSID}.json"
_sd = json.loads(_sf.read_text())
_sd["last_turn_data"]["workflow_history"].append({"turn": 5, "user_query": "Keep the totals", "bom": bom, "tools_used": ["base_readQuery"]})
_sf.write_text(json.dumps(_sd))
before = db.get_file(r2.spec["file"]["id"], USER)
check("no bill of materials while the turn runs", not before["bom_json"])
prov = run(service.provenance(before, USER))
after = db.get_file(r2.spec["file"]["id"], USER)
check("the first read after the turn copies it onto the file (it outlives the conversation)",
      prov["bom"] and json.loads(after["bom_json"])["profile_id"] == "prof-optim" and after["data_derived"] == 1)
_sd = json.loads(_sf.read_text()); _sd["name"] = "Renamed after the save"; _sf.write_text(json.dumps(_sd))
_prov2 = run(service.provenance(db.get_file(r2.spec["file"]["id"], USER), USER))
check("provenance names the conversation as it is called now, not the name it had at save time",
      _prov2.get("session_title") == "Renamed after the save", _prov2.get("session_title"))

# ── R ────────────────────────────────────────────────────────────────────────
print("\nR  tables are attachments (CSV · TSV · XLSX), with attachment sources off")
from trusted_data_agent.api import routes as _routes  # noqa: E402
APP_CONFIG.ATTACHMENT_SOURCES = False
_ext = _routes.allowed_upload_extensions()
check("CSV, TSV and XLSX are accepted without the attachment-sources switch", {".csv", ".tsv", ".xlsx"} <= _ext, sorted(_ext))
check("…so a kept CSV is attachable", service.describe(db.get_file(t["id"], USER), service.store_index())["attachable"] is True)
_csv = b"Region,Q3\r\nEMEA,4.58\r\n'=HYPERLINK(\"http://x\"),-3.0\r\n"
_e = _routes._ingest_upload(USER, "sess-tables", "regions.csv", _csv)
_m = session_files.load_manifest(USER, "sess-tables")
_txt = _m[_e["file_id"]]["extracted_text"]
check("a CSV upload reads as a bounded pipe table", _txt.startswith("[2 rows · 2 columns]") and "Region | Q3" in _txt and "EMEA | 4.58" in _txt, _txt)
check("…the Excel-safety apostrophe is not part of the value", '=HYPERLINK("http://x") | -3.0' in _txt, _txt)
_tsv = _routes._ingest_upload(USER, "sess-tables", "regions.tsv", b"a\tb\n1\t2\n")
check("a TSV upload reads as a table", "a | b" in session_files.load_manifest(USER, "sess-tables")[_tsv["file_id"]]["extracted_text"])
if formats.xlsx_available():
    _x = _routes._ingest_upload(USER, "sess-tables", "regions.xlsx", formats.csv_to_xlsx(b"Region,Q3\r\nEMEA,4.58\r\n"))
    _xt = session_files.load_manifest(USER, "sess-tables")[_x["file_id"]]["extracted_text"]
    check("an XLSX upload reads its first sheet as a table", "Region | Q3" in _xt and "EMEA | 4.58" in _xt, _xt)
_big = ("n,v\n" + "".join(f"{i},{i * 2}\n" for i in range(2000))).encode()
_b = _routes._ingest_upload(USER, "sess-tables", "big.csv", _big)
_bt = session_files.load_manifest(USER, "sess-tables")[_b["file_id"]]["extracted_text"]
check("a long table is bounded and says how much is shown", _bt.startswith("[2000 rows · 2 columns — the first 500 are shown]"), _bt[:80])
from trusted_data_agent.api import files_routes as _fr  # noqa: E402
_att = _fr._attach_by_reference(USER, "sess-tables", db.get_file(t["id"], USER))
check("a kept CSV attaches by reference and reads as a table",
      _att["from_files"] and "Region | Q3" in session_files.load_manifest(USER, "sess-tables")[t["id"]]["extracted_text"])

# ── S ────────────────────────────────────────────────────────────────────────
print("\nS  cloud backends (Azure Blob · Google Cloud Storage) and the one backend catalog")
import types  # noqa: E402
from trusted_data_agent.filestore import catalog  # noqa: E402
from trusted_data_agent.filestore.azure_backend import AzureBlobFileStoreBackend  # noqa: E402
from trusted_data_agent.filestore.gcs_backend import GCSFileStoreBackend  # noqa: E402

check("the catalog declares local, s3, azure_blob and gcs", set(catalog.BACKENDS) == {"local", "s3", "azure_blob", "gcs"})
check("the factory, the validator and the admin list read the catalog",
      set(factory.BACKEND_TYPES) == set(catalog.BACKENDS) and {b["id"] for b in catalog.public_list()} == set(catalog.BACKENDS))
check("every secret field is masked in records",
      {"secret_access_key", "access_key_id", "connection_string", "account_key", "sas_token", "credentials_json"} <= set(catalog.SECRET_KEYS))
for bt, cfg, word in (("azure_blob", {"account_url": "https://a.blob.core.windows.net"}, "container"),
                      ("azure_blob", {"container": "c"}, "connection string or account url"),
                      ("gcs", {}, "bucket"), ("s3", {}, "bucket"), ("local", {}, "folder")):
    e = raises(lambda bt=bt, cfg=cfg: catalog.validate_config(bt, cfg), ValueError)
    check(f"{bt}: a missing {word} is named", e is not None and word in str(e).lower(), str(e))
check("an unknown backend is refused", isinstance(raises(lambda: catalog.validate_config("ftp", {}), ValueError), ValueError))
check("a complete Azure config passes", raises(lambda: catalog.validate_config("azure_blob", {"container": "c", "connection_string": "x"})) is None)

# without the SDK: the backend constructs and its test fails softly, naming the package
_saved_mods = {k: sys.modules.get(k) for k in ("azure", "azure.storage", "azure.storage.blob", "google.cloud.storage")}
if catalog.sdk_note("azure_blob"):
    r = factory.build_backend("azure_blob", {"container": "c", "connection_string": "x"}, cache=False).test_connection()
    check("azure: without azure-storage-blob the test fails softly and names the package",
          r["success"] is False and "azure-storage-blob" in r["message"], r)
    check("azure: the admin list marks it unavailable, with the install hint",
          any(b["id"] == "azure_blob" and not b["available"] and "pip install azure-storage-blob" in b["note"] for b in catalog.public_list()))


class _AzNotFound(Exception):
    pass
_AzNotFound.__name__ = "ResourceNotFoundError"
_AZ = {}


class _AzBlobClient:
    def __init__(self, name): self.name = name
    def exists(self): return self.name in _AZ


class _AzDownload:
    def __init__(self, data): self.data = data
    def readall(self): return self.data


class _AzContainer:
    def __init__(self, name): self.name = name
    def upload_blob(self, name, data, overwrite=False): _AZ[name] = bytes(data)
    def download_blob(self, name):
        if name not in _AZ:
            raise _AzNotFound(name)
        return _AzDownload(_AZ[name])
    def delete_blob(self, name):
        if name not in _AZ:
            raise _AzNotFound(name)
        del _AZ[name]
    def get_blob_client(self, name): return _AzBlobClient(name)


class _AzService:
    made = []
    def __init__(self, account_url=None, credential=None): _AzService.made.append(("url", account_url, credential))
    @classmethod
    def from_connection_string(cls, cs):
        _AzService.made.append(("cs", cs, None)); return cls.__new__(cls)
    def get_container_client(self, name): return _AzContainer(name)


_az_blob = types.ModuleType("azure.storage.blob"); _az_blob.BlobServiceClient = _AzService
sys.modules.update({"azure": types.ModuleType("azure"), "azure.storage": types.ModuleType("azure.storage"),
                    "azure.storage.blob": _az_blob})


class _GNotFound(Exception):
    pass
_GNotFound.__name__ = "NotFound"
_GCS = {}


class _GBlob:
    def __init__(self, name): self.name = name
    def upload_from_string(self, data, content_type=None): _GCS[self.name] = bytes(data)
    def download_as_bytes(self):
        if self.name not in _GCS:
            raise _GNotFound(self.name)
        return _GCS[self.name]
    def delete(self):
        if self.name not in _GCS:
            raise _GNotFound(self.name)
        del _GCS[self.name]
    def exists(self): return self.name in _GCS


class _GBucket:
    def __init__(self, name): self.name = name
    def blob(self, name): return _GBlob(name)


class _GClient:
    made = []
    def __init__(self, project=None): _GClient.made.append(("adc", project))
    @classmethod
    def from_service_account_info(cls, info, project=None):
        _GClient.made.append(("key", project)); return cls.__new__(cls)
    def bucket(self, name): return _GBucket(name)


_g_storage = types.ModuleType("google.cloud.storage"); _g_storage.Client = _GClient
import google.cloud as _gc  # noqa: E402
_saved_gc_attr = getattr(_gc, "storage", None)
sys.modules["google.cloud.storage"] = _g_storage
_gc.storage = _g_storage
try:
    for label, backend, store in (("azure", AzureBlobFileStoreBackend({"container": "files", "prefix": "uderia/",
                                                                       "connection_string": "DefaultEndpointsProtocol=x"}), _AZ),
                                  ("gcs", GCSFileStoreBackend({"bucket": "files", "prefix": "uderia",
                                                               "credentials_json": '{"project_id": "p1"}'}), _GCS)):
        backend.put("owner1/obj1", b"hello")
        check(f"{label}: put/get round-trip under the prefix", backend.get("owner1/obj1") == b"hello"
              and ("uderia/owner1/obj1" in store), sorted(store))
        check(f"{label}: exists", backend.exists("owner1/obj1") and not backend.exists("owner1/none"))
        backend.delete("owner1/obj1")
        check(f"{label}: delete is idempotent and the object is gone",
              raises(lambda b=backend: b.delete("owner1/obj1")) is None and not backend.exists("owner1/obj1"))
        check(f"{label}: a missing object raises ObjectNotFound", isinstance(raises(lambda b=backend: b.get("owner1/none")), ObjectNotFound))
        check(f"{label}: a key outside the closed alphabet is refused", isinstance(raises(lambda b=backend: b.put("../x", b"x")), FileStoreError))
        t_ = backend.test_connection()
        check(f"{label}: test_connection writes, reads back and deletes a probe", t_["success"] and "probe" in t_["message"]
              and not any("_probe" in k for k in store), t_)
        check(f"{label}: the platform's encryption sits on top of the provider's", FileStoreCapability.SERVER_SIDE_ENCRYPTION in backend.capabilities())
    check("azure: a connection string is used as given", _AzService.made[0][0] == "cs")
    AzureBlobFileStoreBackend({"container": "c", "account_url": "https://a.blob.core.windows.net", "account_key": "k"}).exists("o/x")
    check("azure: an account URL with a key passes the key as the credential", ("url", "https://a.blob.core.windows.net", "k") in _AzService.made)
    check("gcs: a service account key is used, its project taken from the key", ("key", "p1") in _GClient.made)
    GCSFileStoreBackend({"bucket": "b", "project": "p2"}).exists("o/x")
    check("gcs: no key → the host's default credentials, with the given project", ("adc", "p2") in _GClient.made)
    r = GCSFileStoreBackend({"bucket": "b", "credentials_json": "{not json"}).test_connection()
    check("gcs: an invalid key says so, softly", r["success"] is False and "not valid JSON" in r["message"], r)

    # a store on a cloud backend, end to end through the one write path, encrypted
    az = db.save_store_config({"name": "Azure EU", "backend_type": "azure_blob",
                               "config": {"container": "files", "connection_string": "secret-cs"}})
    view = [s for s in db.list_store_configs() if s["id"] == az["id"]][0]
    check("azure store: its connection string is masked in records", view["config"]["connection_string"] == catalog.MASK)
    db.save_store_config({"id": az["id"], "config": {"container": "files", "connection_string": catalog.MASK}})
    check("azure store: saving the mask keeps the secret", db.get_store_config(az["id"], with_secrets=True)["config"]["connection_string"] == "secret-cs")
    factory.reset_cache()
    row = service.put_file(user_uuid=USER, data=MARK * 3, filename="cloud.txt", source_kind="upload", store_id=az["id"])
    check("azure store: a file round-trips through the one write and read path", service.read_bytes(row) == MARK * 3)
    check("azure store: the cloud holds only ciphertext under an opaque key",
          all(MARK not in v for v in _AZ.values()) and not any("cloud.txt" in k for k in _AZ))
finally:
    for k, v in _saved_mods.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    if _saved_gc_attr is None:
        if hasattr(_gc, "storage"):
            delattr(_gc, "storage")
    else:
        _gc.storage = _saved_gc_attr
    factory.reset_cache()

# ── T ────────────────────────────────────────────────────────────────────────
print("\nT  an external key manager wraps the data keys (a fake in place of the AWS KMS client)")
from trusted_data_agent.filestore import kms  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM  # noqa: E402
_KMS_KEYS = {}      # key ref → a key only the fake "service" holds
_KMS_CALLS = {"enc": 0, "dec": 0}


def _fake_aws(cfg, key_ref):
    if cfg.get("access_key_id") == "wrong":
        raise kms.KeyManagerError("AccessDenied: the credentials are not allowed to use this key")
    k = _KMS_KEYS.setdefault(key_ref, _AESGCM.generate_key(bit_length=256))

    def enc(pt):
        _KMS_CALLS["enc"] += 1
        n = os.urandom(12)
        return n + _AESGCM(k).encrypt(n, pt, b"kms")

    def dec(ct):
        _KMS_CALLS["dec"] += 1
        if key_ref not in _KMS_KEYS:
            raise RuntimeError("DisabledException: the key is disabled")
        return _AESGCM(_KMS_KEYS[key_ref]).decrypt(ct[:12], ct[12:], b"kms")
    return enc, dec


_saved_factory = kms._FACTORIES["aws_kms"]
kms._FACTORIES["aws_kms"] = _fake_aws
try:
    kms.clear_cache()
    check("the platform key is the default", kms.active_provider() == kms.PLATFORM)
    before = service.put_file(user_uuid=USER, data=MARK + b"-platform", filename="before.txt", source_kind="upload")
    check("a platform-wrapped key reads as version 1", before["key_version"] == crypto.KEY_VERSION
          and not kms.is_external(before["wrapped_key"]))
    cfg_a = {"key_id": "arn:aws:kms:eu:1:key/AAA", "region": "eu-central-1", "access_key_id": "ak", "secret_access_key": "sk"}
    check("a missing key id is named", isinstance(raises(lambda: kms.validate("aws_kms", {}), ValueError), ValueError))
    check("bad credentials fail the test softly, with the service's words",
          kms.test("aws_kms", {**cfg_a, "access_key_id": "wrong"}) == {"success": False,
          "message": "AccessDenied: the credentials are not allowed to use this key"})
    t_ = kms.test("aws_kms", cfg_a)
    check("a working key passes the test (wrap + unwrap a probe)", t_["success"] and "AWS KMS" in t_["message"], t_)
    kms.save("aws_kms", cfg_a, "admin")
    check("the key manager is now active", kms.active_provider() == "aws_kms")
    check("its secrets are masked in the admin view, the key id is shown",
          [p for p in kms.public_view()["providers"] if p["id"] == "aws_kms"][0]["config"]["secret_access_key"] == kms.MASK
          and kms.public_view()["active_key"] == cfg_a["key_id"])
    check("the settings are stored sealed, never in the clear",
          "sk" not in fset._raw("kek_configs") and cfg_a["key_id"] not in fset._raw("kek_configs"))
    check("a masked secret posted back keeps the saved one",
          kms.merge_masked("aws_kms", {**cfg_a, "secret_access_key": kms.MASK})["secret_access_key"] == "sk")
    after = service.put_file(user_uuid=USER, data=MARK + b"-kms", filename="after.txt", source_kind="upload")
    check("a new file's key is wrapped by the key manager and names the key",
          kms.is_external(after["wrapped_key"]) and after["key_version"] == crypto.KEY_VERSION_EXTERNAL
          and kms.describe_wrapping(after["wrapped_key"]) == ("aws_kms", cfg_a["key_id"]))
    kms.clear_cache(); _KMS_CALLS["dec"] = 0
    check("it reads back through the key manager", service.read_bytes(after) == MARK + b"-kms" and _KMS_CALLS["dec"] == 1)
    service.read_bytes(after)
    check("…and a second read within the cache window needs no second call", _KMS_CALLS["dec"] == 1)
    check("files kept before still read with the platform key", service.read_bytes(before) == MARK + b"-platform")
    e = raises(lambda: crypto.unwrap_key(after["wrapped_key"], before["id"]), crypto.FileCryptoError)
    check("a wrapped key copied onto another file does not open it", e is not None and "another file" in str(e), str(e))
    check("master-key rotation leaves an external key as it is",
          crypto.rewrap(after["wrapped_key"], after["id"], "old-master-0123456789abcdefghijklmnop", "new-master-0123456789abcdefghijklmnop") == after["wrapped_key"])
    usage_ = {(u["provider"], u["key"]) for u in service.key_usage()}
    check("key usage names both keys", ("platform", None) in usage_ and ("aws_kms", cfg_a["key_id"]) in usage_, usage_)
    plan = service.rewrap_to_active("admin", dry_run=True)
    check("a dry run counts the files not under the active key", plan["files"] >= 1 and plan["to"] == "AWS KMS", plan)
    res = service.rewrap_to_active("admin", dry_run=False)
    moved = db.get_file(before["id"], USER)
    check("re-wrapping moves the platform-wrapped keys under the key manager, no object rewritten",
          not res["failed"] and kms.describe_wrapping(moved["wrapped_key"]) == ("aws_kms", cfg_a["key_id"])
          and service.read_bytes(moved) == MARK + b"-platform", res)
    check("…after which nothing is left to move", service.rewrap_to_active("admin", dry_run=True)["files"] == 0)
    cfg_b = {**cfg_a, "key_id": "arn:aws:kms:eu:1:key/BBB"}
    kms.save("aws_kms", cfg_b, "admin")
    check("files wrapped by the previous key of the same provider still read", service.read_bytes(after) == MARK + b"-kms")
    kms.save(kms.PLATFORM, {}, "admin")
    kms.clear_cache()
    check("back on the platform key, key-manager files still read (their provider's settings are kept)",
          service.read_bytes(after) == MARK + b"-kms")
    res2 = service.rewrap_to_active("admin", dry_run=False)
    check("re-wrapping back to the platform key leaves no external key", not res2["failed"]
          and all(not kms.is_external(r["wrapped_key"]) for r in db.all_wrapped_files()), res2)
    kms.clear_cache()
    _ghost = kms.wrap(os.urandom(32), "f-ghost", "aws_kms", {"key_id": "arn:aws:kms:eu:1:key/AAA"})
    _KMS_KEYS.pop("arn:aws:kms:eu:1:key/AAA")
    e = raises(lambda: crypto.unwrap_key(_ghost, "f-ghost"), crypto.FileCryptoError)
    check("a revoked key at the key manager makes its files unreadable here, said in words",
          e is not None and "did not unwrap" in str(e), str(e))
    check("the admin list knows every provider and which can run here",
          {p["id"] for p in kms.public_view()["providers"]} == {"platform", "aws_kms", "azure_key_vault", "gcp_kms", "vault_transit"})
    # Azure Key Vault and Google Cloud KMS: no emulator exists, so their SDK modules are faked
    # with the real clients' call shapes (CryptographyClient(key, credential).encrypt(alg, pt);
    # KeyManagementServiceClient.encrypt(request={name, plaintext})) — the platform's use of each
    _AZK = _AESGCM.generate_key(bit_length=256)
    _calls = []

    class _Res:
        def __init__(self, **kw): self.__dict__.update(kw)

    class _CryptoClient:
        def __init__(self, key, credential):
            _calls.append(("azure", key, type(credential).__name__))
        def encrypt(self, alg, pt):
            n = os.urandom(12); return _Res(ciphertext=n + _AESGCM(_AZK).encrypt(n, pt, str(alg).encode()))
        def decrypt(self, alg, ct):
            return _Res(plaintext=_AESGCM(_AZK).decrypt(ct[:12], ct[12:], str(alg).encode()))

    class _Alg:
        rsa_oaep_256 = "RSA-OAEP-256"

    class _SecretCred:
        def __init__(self, tenant, client, secret): pass

    class _DefaultCred:
        pass

    _GK = _AESGCM.generate_key(bit_length=256)

    class _GKms:
        def __init__(self): _calls.append(("gcp", "adc"))
        @classmethod
        def from_service_account_info(cls, info):
            _calls.append(("gcp", info.get("client_email"))); return cls.__new__(cls)
        def encrypt(self, request):
            n = os.urandom(12); return _Res(ciphertext=n + _AESGCM(_GK).encrypt(n, request["plaintext"], request["name"].encode()))
        def decrypt(self, request):
            ct = request["ciphertext"]; return _Res(plaintext=_AESGCM(_GK).decrypt(ct[:12], ct[12:], request["name"].encode()))

    _mods = {"azure.keyvault.keys.crypto": types.ModuleType("azure.keyvault.keys.crypto"),
             "azure.identity": types.ModuleType("azure.identity"),
             "google.cloud.kms": types.ModuleType("google.cloud.kms")}
    _mods["azure.keyvault.keys.crypto"].CryptographyClient = _CryptoClient
    _mods["azure.keyvault.keys.crypto"].EncryptionAlgorithm = _Alg
    _mods["azure.identity"].ClientSecretCredential = _SecretCred
    _mods["azure.identity"].DefaultAzureCredential = _DefaultCred
    _mods["google.cloud.kms"].KeyManagementServiceClient = _GKms
    _prev = {k: sys.modules.get(k) for k in list(_mods) + ["azure", "azure.keyvault", "azure.keyvault.keys"]}
    _prev_gc_kms = getattr(_gc, "kms", None)
    for k in ("azure", "azure.keyvault", "azure.keyvault.keys"):
        sys.modules.setdefault(k, types.ModuleType(k))
    sys.modules.update(_mods)
    _gc.kms = _mods["google.cloud.kms"]
    try:
        az_cfg = {"key_id": "https://v.vault.azure.net/keys/files/1", "tenant_id": "t", "client_id": "c", "client_secret": "s"}
        check("Azure Key Vault: a service principal wraps and unwraps a probe", kms.test("azure_key_vault", az_cfg)["success"])
        check("…using the client-secret credential for the named key",
              ("azure", az_cfg["key_id"], "_SecretCred") in _calls)
        kms.test("azure_key_vault", {"key_id": az_cfg["key_id"]})
        check("…and the host's managed identity when no secret is given", ("azure", az_cfg["key_id"], "_DefaultCred") in _calls)
        g_cfg = {"key_name": "projects/p/locations/eu/keyRings/r/cryptoKeys/files",
                 "credentials_json": '{"client_email": "sa@p.iam.gserviceaccount.com"}'}
        check("Google Cloud KMS: a service account wraps and unwraps a probe", kms.test("gcp_kms", g_cfg)["success"])
        check("…with that service account's key", ("gcp", "sa@p.iam.gserviceaccount.com") in _calls)
        check("…an invalid key file says so", "not valid JSON" in kms.test("gcp_kms", {**g_cfg, "credentials_json": "{x"})["message"])
        kms.save("gcp_kms", g_cfg, "admin")
        g_file = service.put_file(user_uuid=USER, data=MARK + b"-gcp", filename="gcp.txt", source_kind="upload")
        kms.clear_cache()
        check("a file kept under Cloud KMS reads back", kms.describe_wrapping(g_file["wrapped_key"])[0] == "gcp_kms"
              and service.read_bytes(g_file) == MARK + b"-gcp")
        kms.save(kms.PLATFORM, {}, "admin")
    finally:
        for k, v in _prev.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        if _prev_gc_kms is None:
            if hasattr(_gc, "kms"):
                delattr(_gc, "kms")
        else:
            _gc.kms = _prev_gc_kms
finally:
    kms._FACTORIES["aws_kms"] = _saved_factory
    kms.clear_cache()

# ── U ────────────────────────────────────────────────────────────────────────
print("\nU  a change made in a worker thread still reaches the open pages (the live notice)")
import asyncio as _aio
from trusted_data_agent.core import trust_events as _te
from trusted_data_agent.core.config import APP_STATE as _AS
from trusted_data_agent.core.ui_notify import announce_files_change as _afc


async def _live_probe():
    q = _aio.Queue()
    _AS.setdefault("notification_queues", {}).setdefault("u-live", set()).add(q)
    _prev = _AS.get("serving_loop")
    _AS["serving_loop"] = _aio.get_running_loop()
    try:
        # the REST routes run rename/delete/restore/keep through asyncio.to_thread
        await _aio.to_thread(_afc, "u-live", "renamed", ["f-1"])
        try:
            got_thread = await _aio.wait_for(q.get(), 2)
        except _aio.TimeoutError:
            got_thread = {}
        _afc("u-live", "deleted", ["f-2"])                      # on the loop: as before
        got_loop = q.get_nowait()
        _AS["serving_loop"] = None                              # a script / test without a server
        await _aio.to_thread(_afc, "u-live", "renamed", ["f-3"])
        await _aio.sleep(0.05)
        return got_thread, got_loop, q.empty()
    finally:
        _AS["notification_queues"]["u-live"].discard(q)
        _AS["serving_loop"] = _prev

_gt, _gl, _empty = _aio.run(_live_probe())
check("a notice raised in a worker thread is delivered through the serving loop",
      _gt.get("type") == "component_changed" and _gt.get("payload", {}).get("component_id") == "files"
      and _gt.get("payload", {}).get("action") == "renamed" and _gt.get("payload", {}).get("ids") == ["f-1"], _gt)
check("a notice raised on the loop is delivered directly, as before", _gl["payload"].get("action") == "deleted", _gl)
check("with no serving loop a worker-thread notice is skipped, never raised", _empty)

# ── V ────────────────────────────────────────────────────────────────────────
print("\nV  a deleted attachment: no turn reads it, the purge forgets its text, the chip can say so")
from trusted_data_agent.agent import executor as _ex  # noqa: E402
APP_CONFIG.ATTACHMENT_SOURCES = False
fset.save_settings({"retention_days": 30})
VMARK = "DELETED-TEXT-MARK-51c0"
_v = _routes._ingest_upload(USER, "sess-v1", "brief.md", f"# Brief\n{VMARK}".encode())
VID = _v["file_id"]
service.keep(VID, USER)
_fr._attach_by_reference(USER, "sess-v2", db.get_file(VID, USER))     # the same file in a second conversation
_atts = [{"file_id": VID, "filename": "brief.md"}]


def _doc_text(sid):
    txt, _ = _ex.load_document_context(USER, sid, _atts)
    return txt or ""


check("a live attachment is read by a turn, in both conversations", VMARK in _doc_text("sess-v1") and VMARK in _doc_text("sess-v2"))
service.soft_delete(VID, USER)
check("deleted from Files: no turn reads its text, in either conversation (the id named again is refused)",
      VMARK not in _doc_text("sess-v1") and VMARK not in _doc_text("sess-v2"))
_mm_blocks, _mm_text = _ex.load_multimodal_document_content(USER, "sess-v2", _atts, "Unknown", "unknown")
check("…nor the multimodal loader's text fallback", not _mm_blocks and VMARK not in (_mm_text or ""), _mm_text)
_st = session_files.entry_state(USER, session_files.load_manifest(USER, "sess-v1")[VID])
check("its chip state is 'deleted', restorable until purge_after", _st["state"] == "deleted" and _st.get("purge_after"), _st)
check("Recently deleted keeps the text so a restore is complete", VMARK in session_files.load_manifest(USER, "sess-v2")[VID]["extracted_text"])
service.restore(VID, USER)
check("restored: turns read it again, nothing lost", VMARK in _doc_text("sess-v1") and VMARK in _doc_text("sess-v2")
      and session_files.entry_state(USER, session_files.load_manifest(USER, "sess-v1")[VID])["state"] == "live")
service.soft_delete(VID, USER)
db.update_file(VID, purge_after="2000-01-01T00:00:00")
check("the expiry purge removes it", service.purge_due() >= 1 and db.get_file(VID, USER) is None)
check("…and forgets its entry — and the text read from it — in every conversation that referenced it",
      VID not in (session_files.load_manifest(USER, "sess-v1") or {}) and VID not in (session_files.load_manifest(USER, "sess-v2") or {}))
check("a chip whose entry was forgotten reads 'gone'", session_files.entry_state(USER, None)["state"] == "gone")
# retention 0: the delete purges at once, and forgets at once
fset.save_settings({"retention_days": 0})
_v0 = _routes._ingest_upload(USER, "sess-v3", "now.md", f"# Now\n{VMARK}".encode())
service.keep(_v0["file_id"], USER)
service.soft_delete(_v0["file_id"], USER)
check("retention 0: the delete purges and forgets at once", _v0["file_id"] not in (session_files.load_manifest(USER, "sess-v3") or {}))
fset.save_settings({"retention_days": 30})
check("forgetting for a user with no conversations is a no-op, never an error", session_files.forget_files("u-nobody", ["x"]) == 0)
# the turn readers read the guarded manifest — a new reader cannot quietly take the raw one
_ex_src = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
_is_src = (ROOT / "src/trusted_data_agent/llm/image_sources.py").read_text()
check("every turn reader reads session_files.readable_manifest, never the raw manifest",
      "session_files.load_manifest" not in _ex_src and "session_files.load_manifest" not in _is_src
      and _ex_src.count("session_files.readable_manifest") >= 4 and "session_files.readable_manifest" in _is_src)

print("\nW  a file names the agent that placed it — as the agent is now, never 'No agent' when it has one")
# Prod, 2026-10-25: + Add in the Files panel stored only the agent's id, and every surface read the
# tag/class columns, so a file whose agent existed was listed under "No agent".
_agents = {"prof-w": {"tag": "WAGENT", "profile_type": "rag_focused"}}
_orig_agents_of = service.agents_of
service.agents_of = lambda u: dict(_agents)
_w = asyncio.run(service.add_direct(USER, "w-notes.md", b"# W\nnotes", "prof-w"))
_wrow = db.get_file(_w["id"], USER)
check("an id-only writer still records the agent's tag and class (put_file fills them)",
      _wrow["profile_tag"] == "WAGENT" and _wrow["profile_type"] == "rag_focused", (_wrow["profile_tag"], _wrow["profile_type"]))
check("…and the panel/Workspace/TDA_Files description names it", _w["profile_tag"] == "WAGENT" and _w["profile_type"] == "rag_focused")
db.update_file(_w["id"], profile_tag=None, profile_type=None)          # a row written before the fix
_lst = service.list_files(USER, q="w-notes")
check("a row holding only the agent's id reads its agent from the id (no backfill needed)",
      _lst["files"] and _lst["files"][0]["profile_tag"] == "WAGENT" and _lst["files"][0]["profile_type"] == "rag_focused",
      _lst["files"][:1])
check("search finds it by the agent's tag and by its class word",
      service.list_files(USER, q="wagent")["total"] == 1 and service.list_files(USER, q="focus")["total"] >= 1)
_agents["prof-w"]["tag"] = "WRENAMED"
check("a renamed agent reads renamed", service.list_files(USER, q="w-notes")["files"][0]["profile_tag"] == "WRENAMED")
db.update_file(_w["id"], profile_tag="WOLD", profile_type="llm_only")
_agents.clear()
check("an agent since deleted reads as it was when the file was stored",
      service.list_files(USER, q="w-notes")["files"][0]["profile_tag"] == "WOLD")
service.agents_of = _orig_agents_of
_svc_src = (ROOT / "src/trusted_data_agent/filestore/service.py").read_text()
check("ONE reading: describe takes agent_label, and no second tag lookup remains",
      "a_tag, a_type = agent_label(row, agents)" in _svc_src and "_profile_tag_of" not in _svc_src)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
