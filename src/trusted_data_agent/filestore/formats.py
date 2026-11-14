"""
Download formats (PROP-010 §4.4) and the turn → file builders.

A file is kept once, in its source format; conversion happens on the way out, inside the
download route, so it is authorized and audited like any download.

Spreadsheet safety: every CSV/XLSX cell that a spreadsheet would run as a formula (a leading
``=``, ``+``, ``-``, ``@``, tab or carriage return) gets a leading apostrophe, so a value
read from a data source can never become a formula on the user's machine. A plain negative
number (``-3.0``, ``-12%``) is a number, not a formula, and is left alone.
"""
from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple

_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"^[+-]?(\d[\d,]*)(\.\d+)?%?$")


def neutralize_cell(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in _FORMULA_LEAD and not _NUMBER.match(s.replace("−", "-")):
        return "'" + s
    return s


def rows_to_csv(header: Sequence[str], rows: Sequence[Sequence[str]], bom: bool = True) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([neutralize_cell(h) for h in header])
    for r in rows:
        w.writerow([neutralize_cell(c) for c in r])
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def read_csv(data: bytes) -> Tuple[List[str], List[List[str]]]:
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:  # noqa: BLE001
        dialect = csv.excel
    grid = [row for row in csv.reader(io.StringIO(text, newline=""), dialect)]
    if not grid:
        return [], []
    return grid[0], grid[1:]


def _restore_cell(s: str) -> str:
    """Undo ``neutralize_cell`` — the apostrophe it adds is for Excel, not for a reader."""
    return s[1:] if len(s) > 1 and s[0] == "'" and s[1] in _FORMULA_LEAD else s


def read_table(data: bytes, ext: str) -> Tuple[List[str], List[List[str]]]:
    """The header and rows of a kept CSV, TSV or XLSX, values as their reader should see them."""
    if ext == "xlsx":
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        grid = [["" if c is None else str(c) for c in r] for r in wb.active.iter_rows(values_only=True)]
        wb.close()
        grid = [r for r in grid if any(v != "" for v in r)]
    else:
        header, rows = read_csv(data)
        grid = [header] + rows if header else []
    if not grid:
        return [], []
    grid = [[_restore_cell(v) for v in r] for r in grid]
    return grid[0], grid[1:]


def csv_excel_safe(data: bytes) -> bytes:
    header, rows = read_csv(data)
    return rows_to_csv(header, rows)


def xlsx_available() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def csv_to_xlsx(data: bytes, sheet: str = "Sheet1") -> bytes:
    import openpyxl  # type: ignore
    header, rows = read_csv(data)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (re.sub(r"[\[\]\*\?/\\:]", " ", sheet)[:31] or "Sheet1")
    ws.append([neutralize_cell(h) for h in header])
    for r in rows:
        out = []
        for c in r:
            raw = (c or "").replace("−", "-").replace(",", "")
            if _NUMBER.match(raw) and not raw.endswith("%"):
                try:
                    out.append(float(raw) if "." in raw else int(raw))
                    continue
                except ValueError:
                    pass
            out.append(neutralize_cell(c))
        ws.append(out)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


# ── Markdown → DOCX / PDF (deliberately small: headings, lists, paragraphs, tables) ──

def _md_blocks(text: str):
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            yield ("h", len(m.group(1)), m.group(2).strip())
            i += 1
            continue
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if not re.match(r"^\s*\|?\s*:?-{2,}", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            yield ("table", rows)
            continue
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", ln)
        if m:
            items = []
            while i < len(lines) and re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", lines[i]):
                items.append(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", lines[i]))
                i += 1
            yield ("list", items)
            continue
        para = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6})\s|^\s*(?:[-*+]|\d+[.)])\s|^\s*\|", lines[i]):
            para.append(lines[i])
            i += 1
        yield ("p", " ".join(s.strip() for s in para))


def _plain(s: str) -> str:
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2), s)
    s = re.sub(r"\*([^*]+)\*|_([^_]+)_", lambda m: m.group(1) or m.group(2), s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s


def markdown_to_docx(text: str, title: Optional[str] = None) -> bytes:
    from docx import Document  # type: ignore
    d = Document()
    if title:
        d.core_properties.title = title
    for blk in _md_blocks(text):
        if blk[0] == "h":
            d.add_heading(_plain(blk[2]), level=min(blk[1], 4))
        elif blk[0] == "p":
            d.add_paragraph(_plain(blk[1]))
        elif blk[0] == "list":
            for it in blk[1]:
                d.add_paragraph(_plain(it), style="List Bullet")
        elif blk[0] == "table" and blk[1]:
            cols = max(len(r) for r in blk[1])
            t = d.add_table(rows=0, cols=cols)
            t.style = "Table Grid"
            for r in blk[1]:
                cells = t.add_row().cells
                for j in range(cols):
                    cells[j].text = _plain(r[j]) if j < len(r) else ""
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _latin1(s: str) -> str:
    s = (s.replace("—", "-").replace("–", "-").replace("−", "-").replace("’", "'")
          .replace("‘", "'").replace("“", '"').replace("”", '"').replace("…", "...")
          .replace(" ", " ").replace("•", "-"))
    return s.encode("latin-1", errors="replace").decode("latin-1")


def markdown_to_pdf(text: str, title: Optional[str] = None) -> bytes:
    from fpdf import FPDF  # type: ignore
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if title:
        pdf.set_title(_latin1(title))
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for blk in _md_blocks(text):
        if blk[0] == "h":
            pdf.set_font("Helvetica", "B", {1: 16, 2: 14, 3: 12}.get(blk[1], 11))
            pdf.multi_cell(width, 8, _latin1(_plain(blk[2])), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif blk[0] == "p":
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(width, 6, _latin1(_plain(blk[1])), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif blk[0] == "list":
            pdf.set_font("Helvetica", "", 11)
            for it in blk[1]:
                pdf.multi_cell(width, 6, _latin1("- " + _plain(it)), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif blk[0] == "table" and blk[1]:
            pdf.set_font("Helvetica", "", 9)
            cols = max(len(r) for r in blk[1])
            cw = width / max(cols, 1)
            for ri, r in enumerate(blk[1]):
                pdf.set_font("Helvetica", "B" if ri == 0 else "", 9)
                for j in range(cols):
                    pdf.cell(cw, 6, _latin1(_plain(r[j]) if j < len(r) else "")[:40], border=1)
                pdf.ln(6)
            pdf.ln(2)
    return bytes(pdf.output())


def formats_for(filename: str, media_type: Optional[str]) -> List[Dict[str, str]]:
    """What the download menu offers for this file, in order; the first is the default."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("csv", "tsv"):
        out = [{"id": "csv", "label": "CSV", "hint": "UTF-8, opens cleanly in Excel; formulas neutralised"}]
        if xlsx_available():
            out.append({"id": "xlsx", "label": "XLSX", "hint": "Excel workbook"})
        return out
    if ext in ("md", "markdown", "txt"):
        return [{"id": "md" if ext != "txt" else "original", "label": "Markdown" if ext != "txt" else "Text", "hint": "As kept"},
                {"id": "docx", "label": "DOCX", "hint": "Word document"},
                {"id": "pdf", "label": "PDF", "hint": "For sharing and printing"}]
    return [{"id": "original", "label": ext.upper() or "File", "hint": "As kept"}]


# ── reading tables and charts back out of a persisted answer ──────────────────

class _Tables(HTMLParser):
    """Every <table> in document order: header cells and body rows as text; and every chart
    container's spec (``chart-render-target`` or ``data-component-id="chart"``)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[Dict[str, List]] = []
        self.charts: List[str] = []
        self._stack: List[Dict] = []
        self._cell: Optional[List[str]] = None
        self._row: Optional[List[str]] = None
        self._in_head = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            cls = a.get("class") or ""
            if ("chart-render-target" in cls or a.get("data-component-id") == "chart") and a.get("data-spec"):
                self.charts.append(a["data-spec"])
        if tag == "table":
            self._stack.append({"header": [], "rows": []})
        elif not self._stack:
            return
        elif tag == "thead":
            self._in_head = True
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag in ("details", "button", "script", "style") and self._cell is not None:
            self._skip += 1

    def handle_endtag(self, tag):
        if not self._stack:
            return
        t = self._stack[-1]
        if tag in ("details", "button", "script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._in_head:
                t["header"] = self._row
            else:
                t["rows"].append(self._row)
            self._row = None
        elif tag == "thead":
            self._in_head = False
        elif tag == "table":
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def tables_in_html(html: str) -> List[Dict[str, List]]:
    p = _Tables()
    p.feed(html or "")
    out = []
    for t in p.tables:
        if not t["header"] and t["rows"]:
            t = {"header": t["rows"][0], "rows": t["rows"][1:]}
        out.append(t)
    return out


def charts_in_html(html: str) -> List[str]:
    p = _Tables()
    p.feed(html or "")
    return p.charts


def chart_rows(spec_json: str) -> Tuple[List[str], List[List[str]]]:
    import json
    try:
        spec = json.loads(spec_json)
    except Exception:  # noqa: BLE001
        return [], []
    data = None
    for path in (("options", "data"), ("data",), ("spec", "options", "data"), ("spec", "data")):
        cur = spec
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, list) and cur:
            data = cur
            break
    if not data or not isinstance(data[0], dict):
        return [], []
    header: List[str] = []
    for row in data:
        for k in row:
            if k not in header:
                header.append(k)
    return header, [["" if r.get(h) is None else str(r.get(h)) for h in header] for r in data]


# ── dashboard snapshot (PROP-010 §4.10) ──────────────────────────────────────

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    BLOCK = ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "section", "header", "footer", "pre", "table")

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "button", "svg"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        else:
            self.parts.append(" ")    # an inline boundary still separates words ("4 steps" · "total")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "button", "svg") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        else:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """The readable text of a fragment of HTML (a KPI, a narrative tile), lines kept."""
    p = _Text()
    p.feed(html or "")
    lines = [" ".join(ln.split()) for ln in "".join(p.parts).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def dashboard_snapshot_pdf(title: str, taken_at: str, tiles: Sequence[Dict]) -> bytes:
    """One PDF of a dashboard as it stood: each tile with its title, when it was last refreshed
    and by which agent, and its content — the picture the browser drew of it, else its table,
    else its text. ``tiles``: ``{title, kind, refreshed, profile_tag, image, table, text}``."""
    from fpdf import FPDF  # type: ignore
    pdf = FPDF(orientation="L")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_title(_latin1(title))
    width = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(width, 8, _latin1(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(width, 5, _latin1(f"Snapshot taken {taken_at} · {len(tiles)} tile{'s' if len(tiles) != 1 else ''}"),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)
    for t in tiles:
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.set_draw_color(200, 200, 200)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(width, 6, _latin1(t.get("title") or "Untitled"), new_x="LMARGIN", new_y="NEXT")
        meta = [m for m in (t.get("kind"), (f"refreshed {t['refreshed']}" if t.get("refreshed") else "never refreshed"),
                            (f"@{t['profile_tag']}" if t.get("profile_tag") else None)) if m]
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(110, 110, 110)
        pdf.multi_cell(width, 4, _latin1(" · ".join(meta)), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)
        drawn = False
        if t.get("image"):
            try:
                from PIL import Image  # type: ignore
                im = Image.open(io.BytesIO(t["image"]))
                w_px, h_px = im.size
                # a picture is read at the page's width, never taller than about half a page
                w_mm = width
                h_mm = w_mm * h_px / max(w_px, 1)
                max_h = min(110.0, pdf.h - pdf.t_margin - pdf.b_margin - 20)
                if h_mm > max_h:
                    h_mm, w_mm = max_h, max_h * w_px / max(h_px, 1)
                if pdf.get_y() + h_mm > pdf.h - pdf.b_margin:
                    pdf.add_page()
                pdf.image(io.BytesIO(t["image"]), x=pdf.l_margin, y=pdf.get_y(), w=w_mm, h=h_mm)
                pdf.set_y(pdf.get_y() + h_mm + 2)
                drawn = True
            except Exception:  # noqa: BLE001 — an unreadable picture falls back to the tile's data
                drawn = False
        tbl = t.get("table")
        if not drawn and tbl and tbl.get("header"):
            header, rows = tbl["header"], tbl.get("rows") or []
            cols = max(len(header), max((len(r) for r in rows), default=0))
            cw = width / max(cols, 1)
            for ri, r in enumerate([header] + list(rows[:60])):
                pdf.set_font("Helvetica", "B" if ri == 0 else "", 8)
                for j in range(cols):
                    pdf.cell(cw, 5, _latin1(_plain(r[j]) if j < len(r) else "")[:max(8, int(cw / 1.6))], border=1)
                pdf.ln(5)
            if len(rows) > 60:
                pdf.set_font("Helvetica", "I", 8)
                pdf.multi_cell(width, 4, _latin1(f"… {len(rows) - 60} more rows"), new_x="LMARGIN", new_y="NEXT")
            drawn = True
        if not drawn and t.get("text"):
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(width, 5, _latin1(t["text"][:3000]), new_x="LMARGIN", new_y="NEXT")
            drawn = True
        if not drawn:
            pdf.set_font("Helvetica", "I", 9)
            pdf.multi_cell(width, 5, "No content was captured for this tile.", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
    return bytes(pdf.output())
