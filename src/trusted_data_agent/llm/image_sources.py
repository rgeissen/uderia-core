"""S9 — Grounded Multimodal: an attachment is a SOURCE, not a prompt
(spec: docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S9).

The parity move (the model sees the image natively) already exists in ``document_upload.py``.
This module is the beyond: the platform READS the attachment itself, deterministically where it
can — table OCR on an image, table/text extraction on a PDF, a CSV's rows, a text file — and
what it read becomes a *source*: text the grounding gate checks the answer against
(``grounding_text`` + the corpus-support family), rows the compute component (S3) can operate
on, and a citation the capsule can name with the region it came from. A source the platform
extracted itself is **verified**; a transcription the model produced (the optional vision
fallback) is **unverified** and says so — it never enters the grounding text.

Pure core: ``table_from_boxes`` (OCR boxes → a table), ``extract`` (a file → a ``Source``),
``source_text`` (a source → the grounding text). Engines (rapidocr / pytesseract / pdfplumber)
are OPTIONAL extras — absent ⇒ ``method="none"`` with the reason named, never an error. The
per-turn registry is a contextvar (parallel turns never see each other's sources). Off (the
master switch) ⇒ nothing is extracted, registered or stamped — byte-identical.
"""
from __future__ import annotations

import contextvars
import csv
import io
import logging
import os
import re
import statistics
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

app_logger = logging.getLogger("quart.app")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEXT_EXTENSIONS = {".txt", ".md"}
MAX_TABLE_ROWS = 2000
MAX_SOURCE_CHARS = 60_000
_NUMERIC_RE = re.compile(r"^[\s$€£(-]*[\d][\d,.\s]*%?\)?\s*$")

METHODS_VERIFIED = ("ocr", "pdf_table", "pdf_text", "text", "csv", "docx")


def enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "ATTACHMENT_SOURCES", False))
    except Exception:  # noqa: BLE001
        return False


# ── the model ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Table:
    columns: Tuple[str, ...]
    rows: Tuple[Dict[str, str], ...]
    region: Optional[Tuple[float, float, float, float]] = None   # x0, y0, x1, y1 in source pixels / points
    page: Optional[int] = None                                    # 1-based page for PDFs
    index: int = 1                                                # the Nth table of the source

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def as_text(self) -> str:
        head = " | ".join(self.columns)
        lines = [" | ".join(str(r.get(c, "")) for c in self.columns) for r in self.rows]
        return "\n".join([head] + lines)


@dataclass(frozen=True)
class Source:
    file_id: str
    filename: str
    kind: str                       # image | pdf | text | csv | docx | other
    method: str                     # ocr | pdf_table | pdf_text | text | csv | docx | vision_transcription | none
    verified: bool
    text: str = ""                  # the extracted prose / rows as text (what the grounding gate reads)
    tables: Tuple[Table, ...] = ()
    engine: Optional[str] = None    # the extractor that produced it (rapidocr / pytesseract / pdfplumber / …)
    note: Optional[str] = None      # why nothing (or only prose) came out — always stated
    width: Optional[int] = None     # image pixel size, so a region can be drawn as a crop
    height: Optional[int] = None
    statement_ns: Tuple[int, ...] = ()   # the S3 statement numbers its tables were registered as

    @property
    def usable(self) -> bool:
        return bool((self.text or "").strip()) or bool(self.tables)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tables"] = [{"index": t.index, "columns": list(t.columns), "row_count": t.row_count,
                        "region": list(t.region) if t.region else None, "page": t.page,
                        "sample_rows": [dict(r) for r in t.rows[:5]]} for t in self.tables]
        d["text_chars"] = len(self.text or "")
        d["text"] = (self.text or "")[:400]
        d["statement_ns"] = list(self.statement_ns)
        return d


def to_record(src: Source) -> Dict[str, Any]:
    """The FULL source for the upload manifest (every row — the turn rebuilds the source from
    it without re-extracting; deterministic extraction happens once, at upload)."""
    return {"file_id": src.file_id, "filename": src.filename, "kind": src.kind, "method": src.method,
            "verified": bool(src.verified), "text": src.text or "", "engine": src.engine, "note": src.note,
            "width": src.width, "height": src.height,
            "tables": [{"index": t.index, "columns": list(t.columns), "rows": [dict(r) for r in t.rows],
                        "region": list(t.region) if t.region else None, "page": t.page} for t in src.tables]}


def from_record(rec: Dict[str, Any]) -> Optional[Source]:
    """The inverse of ``to_record``; None for a shape this module did not write."""
    try:
        if not isinstance(rec, dict) or not rec.get("file_id") or not rec.get("method"):
            return None
        tables = []
        for i, t in enumerate(rec.get("tables") or [], start=1):
            cols = tuple(str(c) for c in (t.get("columns") or []))
            rows = tuple({str(k): ("" if v is None else str(v)) for k, v in r.items()} for r in (t.get("rows") or []) if isinstance(r, dict))
            reg = t.get("region")
            tables.append(Table(columns=cols, rows=rows, region=tuple(float(v) for v in reg) if reg else None,
                                page=t.get("page"), index=int(t.get("index") or i)))
        return Source(str(rec["file_id"]), str(rec.get("filename") or ""), str(rec.get("kind") or "other"),
                      str(rec["method"]), bool(rec.get("verified")), text=str(rec.get("text") or ""), tables=tuple(tables),
                      engine=rec.get("engine"), note=rec.get("note"), width=rec.get("width"), height=rec.get("height"))
    except Exception:  # noqa: BLE001
        return None


def source_text(src: Source) -> str:
    """The grounding text a VERIFIED source contributes: a labelled block naming the file, the
    method and the engine, then its tables (pipe rows) and prose. An unverified source
    contributes nothing here — a transcription the model wrote is not evidence for the model."""
    if not src.verified or not src.usable:
        return ""
    label = f"[Attachment: {src.filename} — extracted by {src.method}" + (f" ({src.engine})" if src.engine else "") + ", verified]"
    parts = [label]
    for t in src.tables:
        where = f"table {t.index}" + (f" on page {t.page}" if t.page else "")
        parts.append(f"{where}:\n{t.as_text()}")
    if src.text and not src.tables:
        parts.append(src.text)
    elif src.text and src.tables and src.method in ("pdf_table", "pdf_text"):
        parts.append(src.text)
    return "\n".join(parts)[:MAX_SOURCE_CHARS]


# ── OCR boxes → a table (pure) ────────────────────────────────────────────────
def _bbox(box: Any) -> Optional[Tuple[float, float, float, float]]:
    """A box as 4 points [[x,y]…] or (x0,y0,x1,y1) → (x0,y0,x1,y1)."""
    try:
        if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            x0, y0, x1, y1 = box
            return float(min(x0, x1)), float(min(y0, y1)), float(max(x0, x1)), float(max(y0, y1))
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        return min(xs), min(ys), max(xs), max(ys)
    except Exception:  # noqa: BLE001
        return None


def _is_numeric(s: str) -> bool:
    return bool(_NUMERIC_RE.match(s or "")) and any(ch.isdigit() for ch in s)


def table_from_boxes(boxes: Sequence[Dict[str, Any]], min_rows: int = 2) -> Optional[Table]:
    """Cluster OCR word/line boxes into a table: rows by vertical centre (tolerance = 0.6 × the
    median box height), columns by nearest anchor centre (the anchors are the cells of the
    widest row). A row with too few cells to be data (a title, a caption) is dropped. The
    first row becomes the header when it is mostly non-numeric and a later row is mostly
    numeric; else the columns are ``col_1…``. None when fewer than ``min_rows`` data rows
    remain — a picture with a few words is not a table."""
    cells = []
    for b in boxes or []:
        bb = _bbox(b.get("box"))
        text = str(b.get("text") or "").strip()
        if bb is None or not text:
            continue
        cells.append({"bb": bb, "xc": (bb[0] + bb[2]) / 2, "yc": (bb[1] + bb[3]) / 2, "h": bb[3] - bb[1], "text": text})
    if len(cells) < 2:
        return None
    med_h = statistics.median([c["h"] for c in cells]) or 1.0
    tol = max(0.6 * med_h, 1.0)
    cells.sort(key=lambda c: c["yc"])
    rows: List[List[Dict[str, Any]]] = []
    for c in cells:
        if rows and abs(c["yc"] - statistics.mean(x["yc"] for x in rows[-1])) <= tol:
            rows[-1].append(c)
        else:
            rows.append([c])
    for r in rows:
        r.sort(key=lambda c: c["bb"][0])
    widest = max(rows, key=len)
    ncols = len(widest)
    if ncols < 2:
        return None
    anchors = [c["xc"] for c in widest]
    keep = [r for r in rows if len(r) > max(1, ncols // 2)]
    if len(keep) < min_rows:
        return None
    grid: List[List[str]] = []
    for r in keep:
        slots = [""] * ncols
        for c in r:
            j = min(range(ncols), key=lambda k: abs(anchors[k] - c["xc"]))
            slots[j] = (slots[j] + " " + c["text"]).strip()
        grid.append(slots)
    first_non_numeric = sum(1 for s in grid[0] if s and not _is_numeric(s)) >= max(1, ncols - 1)
    later_numeric = any(sum(1 for s in g if _is_numeric(s)) >= 1 for g in grid[1:])
    if first_non_numeric and later_numeric and len(grid) > 1:
        columns = tuple(_dedupe([s or f"col_{i+1}" for i, s in enumerate(grid[0])]))
        data = grid[1:]
    else:
        columns = tuple(f"col_{i+1}" for i in range(ncols))
        data = grid
    if len(data) < 1:
        return None
    all_cells = [c for r in keep for c in r]
    region = (min(c["bb"][0] for c in all_cells), min(c["bb"][1] for c in all_cells),
              max(c["bb"][2] for c in all_cells), max(c["bb"][3] for c in all_cells))
    return Table(columns=columns, rows=tuple({columns[i]: v for i, v in enumerate(g)} for g in data[:MAX_TABLE_ROWS]),
                 region=region)


def _dedupe(names: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out = []
    for n in names:
        k = n
        if k in seen:
            seen[k] += 1
            k = f"{n}_{seen[n]}"
        else:
            seen[k] = 1
        out.append(k)
    return out


# ── optional engines ──────────────────────────────────────────────────────────
_OCR_ENGINE: Any = None
_OCR_NAME: Optional[str] = None


def ocr_engine() -> Tuple[Any, Optional[str]]:
    """The first OCR engine present: rapidocr (ONNX, no system binary) → pytesseract (needs
    the tesseract binary). Cached per process; (None, None) when neither is installed."""
    global _OCR_ENGINE, _OCR_NAME
    if _OCR_NAME is not None:
        return _OCR_ENGINE, (_OCR_NAME or None)
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _OCR_ENGINE, _OCR_NAME = RapidOCR(), "rapidocr"
        return _OCR_ENGINE, _OCR_NAME
    except Exception:  # noqa: BLE001
        pass
    try:
        import pytesseract  # type: ignore
        pytesseract.get_tesseract_version()
        _OCR_ENGINE, _OCR_NAME = pytesseract, "pytesseract"
        return _OCR_ENGINE, _OCR_NAME
    except Exception:  # noqa: BLE001
        pass
    _OCR_ENGINE, _OCR_NAME = None, ""
    return None, None


def _readable(src: Union[str, bytes]):
    """A path stays a path; bytes become a fresh in-memory file. Every extractor below takes
    either, so an upload is read at upload time from memory — before it is encrypted into the
    file store — and no plaintext copy is written for a reader's sake."""
    return io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src


def ocr_boxes(path: Union[str, bytes]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every text box the OCR engine found: ``{box, text, score}``; ``(boxes, engine)`` or
    ``([], None)`` when no engine is installed. ``path`` may be the image's bytes."""
    engine, name = ocr_engine()
    if not name:
        return [], None
    try:
        if name == "rapidocr":
            result, _elapse = engine(path)
            return [{"box": r[0], "text": r[1], "score": float(r[2])} for r in (result or [])], name
        from PIL import Image  # type: ignore
        data = engine.image_to_data(Image.open(_readable(path)), output_type=engine.Output.DICT)
        out = []
        for i, txt in enumerate(data.get("text") or []):
            if not str(txt).strip():
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            out.append({"box": (x, y, x + w, y + h), "text": txt, "score": float(data.get("conf", [0])[i] or 0) / 100.0})
        return out, name
    except Exception as e:  # noqa: BLE001
        app_logger.debug(f"[attachment-sources] OCR failed: {e}")
        return [], name


def _image_size(path: Union[str, bytes]) -> Tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image  # type: ignore
        with Image.open(_readable(path)) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001
        return None, None


def _extract_image(file_id: str, filename: str, path: Union[str, bytes]) -> Source:
    w, h = _image_size(path)
    boxes, engine = ocr_boxes(path)
    if not engine:
        return Source(file_id, filename, "image", "none", False, note="no OCR engine installed (pip install rapidocr-onnxruntime)", width=w, height=h)
    if not boxes:
        return Source(file_id, filename, "image", "none", False, engine=engine, note="the OCR engine found no text in the image", width=w, height=h)
    table = table_from_boxes(boxes)
    prose = " ".join(b["text"] for b in sorted(boxes, key=lambda b: (_bbox(b["box"]) or (0, 0))[1]))
    if table:
        return Source(file_id, filename, "image", "ocr", True, text="", tables=(table,), engine=engine, width=w, height=h)
    return Source(file_id, filename, "image", "ocr", True, text=prose[:MAX_SOURCE_CHARS], engine=engine, width=w, height=h,
                  note="text read from the image; no table structure was found")


def _extract_pdf(file_id: str, filename: str, path: Union[str, bytes]) -> Source:
    tables: List[Table] = []
    text_parts: List[str] = []
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(_readable(path)) as pdf:
            idx = 0
            for pno, page in enumerate(pdf.pages, start=1):
                try:
                    found = page.find_tables()
                except Exception:  # noqa: BLE001
                    found = []
                for tb in found:
                    grid = [[(c or "").strip() for c in row] for row in (tb.extract() or []) if row]
                    grid = [g for g in grid if any(g)]
                    if len(grid) < 2:
                        continue
                    idx += 1
                    cols = tuple(_dedupe([c or f"col_{i+1}" for i, c in enumerate(grid[0])]))
                    rows = tuple({cols[i]: (g[i] if i < len(g) else "") for i in range(len(cols))} for g in grid[1:])
                    tables.append(Table(columns=cols, rows=rows[:MAX_TABLE_ROWS], region=tuple(float(v) for v in tb.bbox), page=pno, index=idx))
                try:
                    t = page.extract_text() or ""
                except Exception:  # noqa: BLE001
                    t = ""
                if t.strip():
                    text_parts.append(t.strip())
        engine = "pdfplumber"
    except ImportError:
        engine = None
    except Exception as e:  # noqa: BLE001
        app_logger.debug(f"[attachment-sources] pdfplumber failed on {filename}: {e}")
        engine = None
    if engine is None:
        t = _platform_text(path, filename)
        if t:
            return Source(file_id, filename, "pdf", "pdf_text", True, text=t[:MAX_SOURCE_CHARS], engine="pypdf2",
                          note="no table extractor installed (pip install pdfplumber); text only")
        return Source(file_id, filename, "pdf", "none", False, note="no PDF text could be read")
    text = "\n\n".join(text_parts)[:MAX_SOURCE_CHARS]
    if tables:
        return Source(file_id, filename, "pdf", "pdf_table", True, text=text, tables=tuple(tables), engine=engine)
    if text.strip():
        return Source(file_id, filename, "pdf", "pdf_text", True, text=text, engine=engine)
    # a scanned PDF: no text layer — rasterize the pages and OCR them (bounded)
    scanned = _ocr_pdf_pages(file_id, filename, path)
    if scanned is not None:
        return scanned
    return Source(file_id, filename, "pdf", "none", False, engine=engine, note="the PDF holds no text layer and no OCR engine is installed (pip install rapidocr-onnxruntime)")


MAX_OCR_PDF_PAGES = 5


def _ocr_pdf_pages(file_id: str, filename: str, path: Union[str, bytes]) -> Optional[Source]:
    """OCR the first pages of a text-less PDF through the image path (pdfplumber renders the
    page; the same box clustering reads the table). None when no OCR engine is installed."""
    engine, name = ocr_engine()
    if not name:
        return None
    try:
        import pdfplumber  # type: ignore
        tables: List[Table] = []
        prose: List[str] = []
        idx = 0
        with pdfplumber.open(_readable(path)) as pdf:
            for pno, page in enumerate(pdf.pages[:MAX_OCR_PDF_PAGES], start=1):
                im = page.to_image(resolution=150).original
                buf = io.BytesIO()
                im.save(buf, format="PNG")     # the rendered page stays in memory
                boxes, _ = ocr_boxes(buf.getvalue())
                if not boxes:
                    continue
                t = table_from_boxes(boxes)
                if t:
                    idx += 1
                    tables.append(Table(columns=t.columns, rows=t.rows, region=t.region, page=pno, index=idx))
                else:
                    prose.append(" ".join(b["text"] for b in sorted(boxes, key=lambda b: (_bbox(b["box"]) or (0, 0))[1])))
        if tables:
            return Source(file_id, filename, "pdf", "ocr", True, text="\n\n".join(prose)[:MAX_SOURCE_CHARS], tables=tuple(tables), engine=name,
                          note="scanned PDF — pages rendered and read by OCR")
        if prose:
            return Source(file_id, filename, "pdf", "ocr", True, text="\n\n".join(prose)[:MAX_SOURCE_CHARS], engine=name,
                          note="scanned PDF — text read by OCR; no table structure was found")
        return Source(file_id, filename, "pdf", "none", False, engine=name, note="the OCR engine found no text in the PDF's pages")
    except Exception as e:  # noqa: BLE001
        app_logger.debug(f"[attachment-sources] PDF OCR failed on {filename}: {e}")
        return None


def _extract_csv(file_id: str, filename: str, path: Union[str, bytes]) -> Source:
    try:
        with (io.TextIOWrapper(io.BytesIO(path), encoding="utf-8", errors="replace") if isinstance(path, (bytes, bytearray))
              else open(path, "r", encoding="utf-8", errors="replace")) as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except Exception:  # noqa: BLE001
                dialect = csv.excel
            reader = csv.reader(f, dialect)
            grid = [[c.strip() for c in row] for row in reader if row and any(c.strip() for c in row)]
    except Exception as e:  # noqa: BLE001
        return Source(file_id, filename, "csv", "none", False, note=f"the file could not be read as CSV: {e}")
    if len(grid) < 2:
        return Source(file_id, filename, "csv", "none", False, note="the CSV holds no data rows")
    cols = tuple(_dedupe([c or f"col_{i+1}" for i, c in enumerate(grid[0])]))
    rows = tuple({cols[i]: (g[i] if i < len(g) else "") for i in range(len(cols))} for g in grid[1:MAX_TABLE_ROWS + 1])
    return Source(file_id, filename, "csv", "csv", True, tables=(Table(columns=cols, rows=rows),), engine="csv")


def _platform_text(path: Union[str, bytes], filename: str = "") -> str:
    """The existing document reader's text (PyPDF2 / python-docx / plain), or "" — its
    bracketed failure placeholders (`[PDF text extraction failed]`) are NOT text."""
    try:
        from trusted_data_agent.llm.document_upload import DocumentUploadHandler
        if isinstance(path, (bytes, bytearray)):
            res = DocumentUploadHandler().extract_text_from_bytes(bytes(path), filename)
        else:
            res = DocumentUploadHandler()._extract_text_from_document(path)
        t = str((res or {}).get("content") or "").strip() if isinstance(res, dict) else ""
    except Exception:  # noqa: BLE001
        return ""
    if not t or (t.startswith("[") and t.endswith("]") and "\n" not in t):
        return ""
    return t


def _extract_text(file_id: str, filename: str, path: Union[str, bytes], kind: str) -> Source:
    t = _platform_text(path, filename)
    if not t:
        return Source(file_id, filename, kind, "none", False, note="no text could be read from the file")
    return Source(file_id, filename, kind, ("docx" if kind == "docx" else "text"), True, text=t[:MAX_SOURCE_CHARS], engine=kind)


def extract(file_id: str, filename: str, path: Optional[str], content_type: str = "",
            data: Optional[bytes] = None) -> Source:
    """An upload → its ``Source``, read from ``data`` (the bytes, at upload) or from ``path``.
    Never raises; a file the platform cannot read yields ``method="none"`` with the reason in
    ``note``."""
    try:
        ext = os.path.splitext(filename or "")[1].lower()
        if data is not None:
            path = bytes(data)
        elif not path or not os.path.exists(path):
            return Source(file_id, filename, "other", "none", False, note="the stored file is missing")
        if ext in IMAGE_EXTENSIONS or (content_type or "").startswith("image/"):
            return _extract_image(file_id, filename, path)
        if ext == ".pdf" or content_type == "application/pdf":
            return _extract_pdf(file_id, filename, path)
        if ext in (".csv", ".tsv"):
            return _extract_csv(file_id, filename, path)
        if ext == ".docx":
            return _extract_text(file_id, filename, path, "docx")
        if ext in TEXT_EXTENSIONS or (content_type or "").startswith("text/"):
            return _extract_text(file_id, filename, path, "text")
        return Source(file_id, filename, "other", "none", False, note=f"no extractor for {ext or content_type or 'this file type'}")
    except Exception as e:  # noqa: BLE001
        app_logger.debug(f"[attachment-sources] extraction failed for {filename}: {e}")
        return Source(file_id, filename, "other", "none", False, note=f"extraction failed: {e}")


def transcription_source(file_id: str, filename: str, text: str, width: Optional[int] = None, height: Optional[int] = None) -> Source:
    """The vision fallback's product: the MODEL's own reading of the image — recorded as a
    source so the answer can name where it came from, but UNVERIFIED (it never enters the
    grounding text; the assurance reading says the answer leaned on a transcription)."""
    t = (text or "").strip()
    table = _table_from_pipe_text(t)
    return Source(file_id, filename, "image", "vision_transcription", False, text=t[:MAX_SOURCE_CHARS],
                  tables=(table,) if table else (), engine="model", width=width, height=height,
                  note="transcribed by the model — unverified" if t else "the model returned no transcription")


def _table_from_pipe_text(text: str) -> Optional[Table]:
    lines = [ln.strip() for ln in (text or "").splitlines() if "|" in ln]
    lines = [ln for ln in lines if not re.match(r"^\|?\s*:?-{2,}", ln.strip("| "))]
    if len(lines) < 2:
        return None
    grid = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
    n = max(len(g) for g in grid)
    cols = tuple(_dedupe([c or f"col_{i+1}" for i, c in enumerate(grid[0] + [""] * (n - len(grid[0])))]))
    rows = tuple({cols[i]: (g[i] if i < len(g) else "") for i in range(n)} for g in grid[1:MAX_TABLE_ROWS + 1])
    return Table(columns=cols, rows=rows) if rows else None


# ── the per-turn registry (a contextvar — parallel turns never mix) ───────────
_TURN: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("attachment_sources", default=None)


def begin_turn() -> contextvars.Token:
    return _TURN.set({"sources": []})


def end_turn(token: Optional[contextvars.Token]) -> None:
    try:
        if token is not None:
            _TURN.reset(token)
    except Exception:  # noqa: BLE001
        pass


def add(source: Source) -> None:
    reg = _TURN.get()
    if reg is None or not enabled():
        return
    reg["sources"] = [s for s in reg["sources"] if s.file_id != source.file_id] + [source]


def sources() -> List[Source]:
    reg = _TURN.get()
    return list(reg["sources"]) if reg else []


def replace(source: Source) -> None:
    add(source)


def grounding_text() -> str:
    """Every VERIFIED source's text, joined — what joins the turn's grounding evidence."""
    return "\n\n".join(t for t in (source_text(s) for s in sources()) if t)


def verified_chunks() -> List[Dict[str, Any]]:
    """The verified sources as retrieved-chunk-shaped dicts (the corpus-support family's
    whitelist reads ``content``; the capsule's Sources row reads ``source``)."""
    out = []
    for s in sources():
        t = source_text(s)
        if t:
            out.append({"content": t, "source": s.filename, "attachment": True, "file_id": s.file_id, "method": s.method})
    return out


def stamp_for_turn() -> Optional[Dict[str, Any]]:
    """The persisted ``attachment_sources`` stamp — every source with its method, verification,
    tables (columns/row counts/regions/sample rows) and S3 statement numbers. None when the
    turn had no sources (an absent stamp is the baseline)."""
    ss = sources()
    if not ss:
        return None
    return {"sources": [s.to_dict() for s in ss],
            "verified": sum(1 for s in ss if s.verified and s.usable),
            "unverified": sum(1 for s in ss if not s.verified and s.usable),
            "none": sum(1 for s in ss if not s.usable),
            # the verified evidence, in full — what the grounding gate reads live and the
            # post-hoc scorer reads from the persisted turn (one text, two moments)
            "grounding_text": grounding_text()}


def register_tables(session_id: Optional[str]) -> None:
    """Register every verified table as an S3 result-set statement (``attachment:<file>#<n>``)
    so ``TDA_Compute`` can operate on it and a figure quoted from it traces to the image
    region. Requires the compute registry to be on (its own switch); a no-op otherwise."""
    try:
        from trusted_data_agent.agent import result_sets as _rs
        if not _rs.enabled() or not session_id:
            return
        reg = _TURN.get()
        if not reg:
            return
        updated = []
        for s in reg["sources"]:
            if not s.verified or not s.tables or s.statement_ns:
                updated.append(s)
                continue
            ns = []
            for t in s.tables:
                where = f"attachment:{s.filename}#{t.index}" + (f" (page {t.page})" if t.page else "") + f" — {s.method}" + (f"/{s.engine}" if s.engine else "") + ", verified"
                n = _rs.register(session_id, "attachment", where, list(t.columns), [dict(r) for r in t.rows],
                                 source={"file_id": s.file_id, "filename": s.filename, "method": s.method, "engine": s.engine,
                                         "region": list(t.region) if t.region else None, "page": t.page,
                                         "width": s.width, "height": s.height, "table": t.index})
                if n:
                    ns.append(n)
            updated.append(Source(**{**asdict_shallow(s), "statement_ns": tuple(ns)}))
        reg["sources"] = updated
    except Exception as e:  # noqa: BLE001
        app_logger.debug(f"[attachment-sources] table registration skipped: {e}")


def manifest_entries(user_uuid: str, session_id: str, attachments: Sequence[Dict[str, Any]]) -> List[Tuple[Source, Dict[str, Any]]]:
    """The turn's attachments as ``(Source, manifest entry)`` pairs, rebuilt from the upload
    manifest (extraction happened once, at upload). Empty when off, nothing attached, or the
    manifest is unreadable. Never raises."""
    if not enabled() or not attachments:
        return []
    try:
        from trusted_data_agent.filestore import session_files
        manifest = session_files.readable_manifest(user_uuid or "", session_id or "")
    except Exception:  # noqa: BLE001
        return []
    if not manifest:
        return []
    out = []
    for att in attachments:
        fid = (att or {}).get("file_id") if isinstance(att, dict) else None
        entry = manifest.get(fid) if fid else None
        if not isinstance(entry, dict):
            continue
        src = from_record(entry.get("source") or {})
        if src is not None:
            out.append((src, entry))
    return out


def register_from_manifest(user_uuid: str, session_id: str, attachments: Sequence[Dict[str, Any]]) -> List[Source]:
    """The engine-agnostic seam: read the turn's sources from the manifest, register them for
    the turn and register their tables as result sets. Returns the sources. (The executor's
    seam adds the optional transcription fallback on top of this.)"""
    pairs = manifest_entries(user_uuid, session_id, attachments)
    for src, _entry in pairs:
        add(src)
    if pairs:
        register_tables(session_id)
    return [p[0] for p in pairs]


def asdict_shallow(s: Source) -> Dict[str, Any]:
    return {"file_id": s.file_id, "filename": s.filename, "kind": s.kind, "method": s.method, "verified": s.verified,
            "text": s.text, "tables": s.tables, "engine": s.engine, "note": s.note, "width": s.width, "height": s.height,
            "statement_ns": s.statement_ns}


def document_text_for(src: Source) -> str:
    """What the MODEL sees for a source (the document-context block): the rows as text plus a
    one-line provenance so it can cite the file; a transcription is labelled as its own."""
    if not src.usable:
        return ""
    head = (f"[{src.filename}: read by the platform via {src.method}" + (f" ({src.engine})" if src.engine else "") + " — verified source]"
            if src.verified else f"[{src.filename}: transcribed by the model — unverified]")
    parts = [head]
    for t in src.tables:
        parts.append((f"table {t.index}" + (f", page {t.page}" if t.page else "") + ":\n") + t.as_text())
    if src.text and (not src.tables or src.method in ("pdf_table", "pdf_text", "vision_transcription")):
        if not (src.method == "vision_transcription" and src.tables):
            parts.append(src.text)
    return "\n".join(parts)
