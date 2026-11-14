"""The Answer Surface — the ONE renderer for everything an agent's reply carries beside its
prose (design: docs/roadmap/ANSWER_SURFACE_GRAMMAR.md).

Three shapes, and nothing else may draw a frame, a rule, a kind word or a chip inside an
answer body:

    lead_figure(value, label)                  the answer's figure — unframed, on the bubble
    note(kind, text, tone=, route=)            a platform statement ABOUT the answer
    evidence_row(kind, summary, body_html, …)  one row of the evidence rail

Every producer — the formatter, the gate adapters, the compute handler, the Focus sources,
the budget line, `generate_component_html` — calls these. Consistency is then a property
of one implementation, not a review item: two segments with the same role look identical
whatever engine or component produced them.

Pure: no I/O, no model, HTML in / HTML out. Every caller-supplied string is escaped here
unless it is passed as `*_html`, which the caller owns. Class names never contain "card"
(a global `[class*="card"]` rule paints a card background on anything so named).
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

# ── the vocabulary ──────────────────────────────────────────────────────────────────────
# Evidence KINDS: the identity of what a row holds. The accent is the kind's identity colour
# (never a state colour — state belongs to notes and the verdict chip); the word is what the
# reader sees first on the row. `open` is the default disclosure: a visual IS an answer
# form, everything else is one gesture away.
EVIDENCE_KINDS: Dict[str, Dict[str, Any]] = {
    "data":       {"word": "Data",       "accent": "#F15F22", "open": False},
    "chart":      {"word": "Chart",      "accent": "#64748b", "open": True},
    "compute":    {"word": "Compute",    "accent": "#14b8a6", "open": False},
    "synthesis":  {"word": "Synthesis",  "accent": "#a78bfa", "open": False},
    "sources":    {"word": "Sources",    "accent": "#3b82f6", "open": False},
    "component":  {"word": "Component",  "accent": "#6366f1", "open": False},
    "attachment": {"word": "Attachment", "accent": "#0ea5e9", "open": False},
    "step":       {"word": "Step",       "accent": "#94a3b8", "open": False},
    "experts":    {"word": "Experts",    "accent": "#F15F22", "open": False},
}

# Note KINDS: the platform's own word for what it is saying about the answer, and the TONE
# it carries (the trust grammar's attention colours: a finding is warn, a withheld answer is
# danger, a plain disclosure is muted).
NOTE_KINDS: Dict[str, Dict[str, str]] = {
    "scope":    {"word": "Scope",     "tone": "warn"},
    "honesty":  {"word": "Honesty",   "tone": "warn"},
    "figures":  {"word": "Figures",   "tone": "warn"},
    "revised":  {"word": "Revised",   "tone": "muted"},
    "budget":   {"word": "Budget",    "tone": "muted"},
    "stopped":  {"word": "Stopped",   "tone": "muted"},
    "served":   {"word": "Served by", "tone": "muted"},
    "withheld": {"word": "Withheld",  "tone": "danger"},
    "redacted": {"word": "Redacted",  "tone": "warn"},
    "confidential": {"word": "Confidential", "tone": "warn"},
    "guardrail": {"word": "Guardrail", "tone": "warn"},
    # the question's subject is not in the bound model (agent/model_gap.py): a disclosure
    # with the act that extends the model — a path, not a finding, so muted
    "model_gap": {"word": "Not in the model", "tone": "muted"},
}
TONES = ("muted", "warn", "danger")

_ICONS: Dict[str, str] = {
    "data":       '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "chart":      '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "compute":    '<path d="M9 7h6M9 11h.01M12 11h.01M15 11h.01M9 14h.01M12 14h.01M15 14v3M9 17h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"/>',
    "synthesis":  '<path d="M4 7h16M4 12h10M4 17h13"/>',
    "sources":    '<path d="M4 4h12l4 4v12H4z M16 4v4h4 M8 13h8M8 17h6"/>',
    "component":  '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',
    "attachment": '<path d="M21 12l-8 8a5 5 0 01-7-7l9-9a3 3 0 014 4l-9 9a1 1 0 01-2-2l8-8"/>',
    "step":       '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "experts":    '<path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM22 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8"/>',
    "note":       '<path d="M12 8v4m0 4h.01M22 12a10 10 0 11-20 0 10 10 0 0120 0z"/>',
    "chev":       '<path d="M9 6l6 6-6 6"/>',
}


def _esc(s: Any) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _icon(name: str) -> str:
    return ('<svg class="ans-ic" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            + _ICONS.get(name, _ICONS["note"]) + '</svg>')


_SVG_TAGS = {"svg", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "g"}


def manifest_icon(svg: Any) -> Optional[str]:
    """A component's own icon (manifest ``icon_svg``) sized for an evidence row — or None when
    it is absent or not a plain drawing. A manifest can come from a third party, so only the
    basic shape elements pass: no script, no style, no link, no event handler."""
    s = str(svg or "").strip()
    if not s.startswith("<svg") or len(s) > 4000:
        return None
    tags = {m.lower() for m in re.findall(r"</?\s*([a-zA-Z][\w:-]*)", s)}
    if not tags or not tags <= _SVG_TAGS:
        return None
    if re.search(r"\son\w+\s*=|href\s*=|javascript:|url\s*\(|<!|&#", s, re.I):
        return None
    head = re.match(r"<svg\b[^>]*>", s)
    if not head:
        return None
    attrs = re.sub(r'\s(class|width|height|aria-hidden)="[^"]*"', "", head.group(0)[4:-1])
    return f'<svg class="ans-ic" width="14" height="14" aria-hidden="true"{attrs}>' + s[head.end():]


# ── the three shapes ────────────────────────────────────────────────────────────────────
def lead_figure(value: Any, label: str = "", *, value_html: Optional[str] = None) -> str:
    """The answer's figure — the lead of the reply, never framed. `value_html` lets a caller
    pass an already-marked figure (a lineage underline) it owns."""
    v = value_html if value_html is not None else _esc(value)
    lbl = f'<div class="ans-lead-l">{_esc(label)}</div>' if label else ""
    return f'<div class="ans-lead" role="group"><div class="ans-lead-v">{v}</div>{lbl}</div>'


def note(kind: str, text: str, *, tone: Optional[str] = None, route: Optional[str] = None,
         route_label: str = "record", text_html: Optional[str] = None,
         act_html: Optional[str] = None) -> str:
    """A platform statement ABOUT the answer: one line, the kind word first, a left rule in
    the attention tone. `route` is an app route the row offers (opens the trust record, a
    Live Status card …) — rendered as a button carrying `data-ans-route`. `act_html` is an
    act the note offers (caller-owned markup, one `.ans-act` button), placed where the route
    goes; a note carries one or the other."""
    k = NOTE_KINDS.get(kind, {"word": kind.replace("_", " ").title(), "tone": "muted"})
    t = tone if tone in TONES else k["tone"]
    body = text_html if text_html is not None else _esc(text)
    act = (f'<button type="button" class="ans-note-route" data-ans-route="{_esc(route)}">{_esc(route_label)}'
           f'{_icon("chev")}</button>' if route else (act_html or ""))
    return (f'<div class="ans-note ans-tone-{t}" role="note" data-kind="{_esc(kind)}">'
            f'{_icon("note")}<span class="ans-kind">{_esc(k["word"])}</span>'
            f'<span class="ans-note-t">{body}</span>{act}</div>')


# Where the evidence begins: the rail group, or a lone evidence row (a component row is
# appended without a rail). A note belongs ABOVE the first of these.
_EVIDENCE_START = re.compile(r'<div class="ans-rail"|<details class="ans-ev"')


def place_note(answer_html: Optional[str], note_html: Optional[str]) -> str:
    """Put a platform note where the grammar says notes go: directly under the answer's
    prose, ABOVE the evidence rail — never below nine collapsed sources where the reader
    stops looking. Every producer that adds a note calls this instead of concatenating, so
    a note lands in the same place whichever gate, engine or order added it. Several notes
    keep the order they were added in (each one goes just above the rail, after the last)."""
    body = answer_html or ""
    if not note_html:
        return body
    m = _EVIDENCE_START.search(body)
    if not m:
        return body + note_html
    return body[:m.start()] + note_html + body[m.start():]


def evidence_row(kind: str, summary: str, body_html: str, *, provenance: str = "",
                 provenance_html: Optional[str] = None, open: Optional[bool] = None,
                 summary_html: Optional[str] = None, attrs: Optional[Dict[str, str]] = None,
                 word: Optional[str] = None, accent: Optional[str] = None,
                 icon_html: Optional[str] = None) -> str:
    """One row of the evidence rail. The summary carries the row's KEY FACT in words (a
    collapsed row is never a label without content); `provenance` is the identity of the
    source, on the right, the same way on every row ("statement 1", "Tool output ·
    TDA_Compute", a repository name). `body_html` is the producer's content, UNCHANGED —
    the row is the frame, nothing inside it is restyled by this function."""
    k = dict(EVIDENCE_KINDS.get(kind, {"word": kind.replace("_", " ").title(), "accent": "#94a3b8", "open": False}))
    # a component row wears the component's OWN identity (its manifest's display name +
    # icon_accent — the same accent as its split panel and rail item), never a generic one
    if word: k["word"] = str(word)
    if accent and re.match(r"^#[0-9a-fA-F]{6}$", str(accent)): k["accent"] = str(accent)
    is_open = k["open"] if open is None else bool(open)
    prov = (provenance_html if provenance_html is not None
            else (f'<span class="ans-prov">{_esc(provenance)}</span>' if provenance else ""))
    summ = summary_html if summary_html is not None else _esc(summary)
    extra = "".join(f' {_esc(a)}="{_esc(v)}"' for a, v in (attrs or {}).items())
    return (f'<details class="ans-ev" data-kind="{_esc(kind)}" style="--ans-accent:{k["accent"]}"{" open" if is_open else ""}{extra}>'
            f'<summary class="ans-ev-s">{_icon("chev")}{icon_html or _icon(kind)}<span class="ans-kind">{_esc(k["word"])}</span>'
            f'<span class="ans-ev-t">{summ}</span>{prov}</summary>'
            f'<div class="ans-ev-b">{body_html}</div></details>')


def component_row(component_id: str, summary: str, body_html: str, *, manifest: Optional[Dict[str, Any]] = None,
                  tool_name: str = "", open: Optional[bool] = None,
                  override: Optional[Dict[str, Any]] = None) -> str:
    """A component's inline output as ONE evidence row — the seam that keeps every component
    inside the grammar. Identity comes from the MANIFEST (`display_name`, `icon_accent`),
    the disclosure default from its `answer_surface.open` (a visual or an act the user asked
    for opens; supporting evidence stays one gesture away), the provenance is the tool. A
    component author supplies a summary (the payload's `title`) and a body; the frame, the
    kind word, the chip and the disclosure are never theirs to draw.

    `override` is the PAYLOAD's own `metadata.answer_surface` — a per-ACTION refinement of
    the manifest's per-component default (`open`, `word`): a graph the user asked to SEE
    opens although the component's default is collapsed; a stats readout stays collapsed."""
    m = manifest or {}
    a = dict(m.get("answer_surface") or {})
    o = override if isinstance(override, dict) else {}
    kind = str(a.get("kind") or "component")
    if open is None:
        open = bool(o["open"]) if "open" in o else bool(a.get("open", False))
    prov = f"Tool output · {tool_name}" if tool_name else "Tool output"
    # A component whose manifest declares `answer_surface.pin` can be pinned to a dashboard:
    # the act sits in the row's footer — the same footer and the same button a data row's
    # table carries — server-rendered hidden and revealed by the client only when the
    # Dashboard component is enabled for the agent that answered (one rule, one place).
    if a.get("pin") is True and o.get("pin") is not False:
        body_html = f"{body_html}{pin_footer(component_id)}"
    return evidence_row(kind, summary, body_html, provenance=prov, open=bool(open),
                        word=str(o.get("word") or a.get("word") or m.get("display_name") or component_id.replace("_", " ").title()),
                        accent=str(m.get("icon_accent") or ""), attrs={"data-component": component_id},
                        # the component's OWN icon — the one on its split panel and in Workspace
                        icon_html=manifest_icon(m.get("icon_svg")))


def pin_footer(component_id: str) -> str:
    """The row footer that carries "Pin to dashboard" for a pinnable component — the same
    `.ans-tf` footer and `.ans-act` button as a data row's table (formatter._data_table_html),
    hidden until the client has checked that the Dashboard component is enabled for the
    agent. One label everywhere: "Pin to dashboard"."""
    return ('<div class="ans-tf"><div class="ans-tf-l"></div><div class="ans-tf-r">'
            f'<button type="button" class="ans-act ans-pin" data-pin-component="{_esc(component_id)}" hidden>'
            'Pin to dashboard</button></div></div>')


def component_container(component_id: str, container_id: str, spec_json: str, *, silent: bool = False) -> str:
    """The frontend-rendered half of a component: the `<div data-component-id data-spec>`
    the ComponentRendererRegistry dispatches to the component's renderer.js. A SILENT
    payload (an act with nothing to show — a panel opened, a patch delivered to the open
    canvas, a list the model reads) still needs its container in the DOM (the renderer
    runs, fires its event, hides itself) but never a row: it is wrapped in the framework's
    own `.ans-silent` so a renderer that hides its container's PARENT hides only that."""
    inner = f'<div id="{_esc(container_id)}" data-component-id="{_esc(component_id)}" data-spec=\'{spec_json}\'></div>'
    return f'<div class="ans-silent">{inner}</div>' if silent else inner


def source_item(name: str, body_html: str, *, score: Optional[float] = None, collection: str = "",
                chips: Sequence[str] = ()) -> str:
    """One cited document inside a `sources` row: the document's name, its relevance, the
    repository it came from and its governance chips (trust · freshness · sensitivity) on
    the summary line; the full content one gesture away. Every source on every Focus
    answer reads the same way because this is the only place the shape is drawn."""
    sc = f'<span class="ans-chip ans-src-score" title="relevance">{score:.2f}</span>' if isinstance(score, (int, float)) else ""
    coll = f'<span class="ans-src-coll">{_esc(collection)}</span>' if collection else ""
    ch = "".join(f'<span class="ans-chip">{_esc(c)}</span>' for c in chips if c)
    return (f'<details class="ans-src"><summary class="ans-src-s">{_icon("chev")}'
            f'<span class="ans-src-n">{_esc(name)}</span>{sc}{coll}{ch}</summary>'
            f'<div class="ans-src-b">{body_html}</div></details>')


def evidence_rail(rows: Iterable[str]) -> str:
    """The rows as one group (the per-viewer "expand all" preference hangs on the group)."""
    rs = [r for r in rows if r]
    return f'<div class="ans-rail" data-rows="{len(rs)}">{"".join(rs)}</div>' if rs else ""


__all__ = ["EVIDENCE_KINDS", "NOTE_KINDS", "TONES", "lead_figure", "note", "place_note", "evidence_row", "component_row", "pin_footer", "component_container", "source_item", "evidence_rail"]
