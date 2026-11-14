"""Number provenance — every figure in the delivered answer can say where it came from
(spec: BEYOND_CLAUDE_CONVERSATION_AGENT.md §S3, Compute with Lineage).

Pure. Given the answer, the turn's compute receipts and its result sets, every number token in
the answer is classified ONCE:

    receipted   — equals a figure a compute receipt vouches for (a SUM, a rank value…)
    quoted      — appears verbatim in a result row of some statement
    literal     — a value the agent WROTE INTO an executed statement: a filter literal
                  (`offence_code = '120'`), an IN/BETWEEN member, a TOP/LIMIT bound — the
                  turn's own input, not its output, and never arithmetic
    declared    — a code the knowledge graph declares for a coded column ('120' = Robbery)
    derived     — equals the SUM or the AVERAGE of a numeric column of a complete result set
                  ("the sum of robbery_count over the 12 rows of statement 1") — arithmetic,
                  but arithmetic the data vouches for
    unreceipted — none of these: the model's own arithmetic (or a figure from nowhere)
    misderived  — an unreceipted figure that is CLOSE TO, but not, a column's sum or average
                  (a digit slip: 180,193 stated, the rows sum to 179,193). It stays in
                  `unreceipted` — every consumer keeps treating it as untraced — and the
                  disclosure names the value the rows actually give. Never rewritten.

The last two exist because a coded-column question ("how many robberies?" → `WHERE
offence_code = '120'`) states the code in its answer, and a classifier that knows only
rows and receipts called it "the agent's own arithmetic" (prod, Sep 2026). A figure that
traces to the statement or to the ontology is disclosed as such — never convicted.

The classification feeds three surfaces from ONE assessment: the inline lineage marks in the
rendered answer (`mark_html`), the numeric-fidelity measurement (a receipted figure IS
grounded — the gate moves from flagging arithmetic to verifying it), and the honesty kind
`unreceipted_figure` (an append-only disclosure naming the figures — never a red mark on a
number that may still be right).

Conservative by design: a figure that also occurs in the question is never judged (the model
is allowed to repeat what it was asked); short tokens (≤ 2 digits) are marked when they match
but never CONVICTED — "3 cities" is not arithmetic. A precision-tolerant match ("516.83" for
a receipt of 516.833333) is a match; a near miss is not.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from trusted_data_agent.agent.grounding.primitives import NUM_RE, norm_nums
from trusted_data_agent.agent import compute as _compute
from trusted_data_agent.agent.html_marks import mask as _mask, unmask as _unmask, mask_tags as _mask_tags, unmask_tags as _unmask_tags

MIN_DIGITS_TO_CONVICT = 3     # a figure shorter than this is never called unreceipted
NEAR_MISS_REL = 0.05          # a figure within 5% of a column sum/average (but not it) is a near miss
MIN_ROWS_TO_DERIVE = 2        # an aggregate of one row is the row itself (already `quoted`)
MAX_MARKS_PER_FIGURE = 3
# A figure stands on its own: a number glued to an identifier ("FB-5000", "v2", "Q3") or
# inside a longer token is not one the answer STATES.
_FIGURE_RE = re.compile(r"(?<![\w.-])" + NUM_RE.pattern + r"(?![\w]|[.,]\d)")


# ── matching ─────────────────────────────────────────────────────────────────
def _num(tok: str) -> Optional[float]:
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def _precision(tok: str) -> int:
    t = tok.replace(",", "")
    return len(t.split(".")[1]) if "." in t else 0


def matches(tok: str, value: float) -> bool:
    """`tok` (as written in the answer) states `value`: exact, or equal once the value is
    rounded to the token's own precision (an answer that says 516.83 for 516.8333 is right;
    one that says 517 for 516.8333 is right too at 0 dp — a 1% miss is not)."""
    n = _num(tok)
    if n is None:
        return False
    if n == value:
        return True
    p = _precision(tok)
    try:
        if p:
            return round(value, p) == round(n, p) and abs(value - n) < 10 ** (-p)
        # 0 dp: rounded OR truncated ("517" and "516" both state 516.83); never a wider miss
        return n in (float(round(value)), float(int(value)))
    except Exception:  # noqa: BLE001
        return False


def is_plan(st: Dict[str, Any]) -> bool:
    """An EXPLAIN result: plan prose, one line per row — not data."""
    return bool(re.match(r"\s*EXPLAIN\b", str(st.get("statement") or ""), re.I))


def _plan_noise(tok: str) -> bool:
    """In plan prose a small whole number is a step, spool or line number, never a figure an
    answer quotes as data. Estimates (rows, bytes, seconds) are kept."""
    try:
        v = float(tok)
    except ValueError:
        return False
    return v.is_integer() and v < 100


def row_figures(statements: Iterable[Dict[str, Any]]) -> Dict[str, List[Tuple[int, int]]]:
    """Every number token any result row carries → the (statement, row) positions it sits in.
    Keyed by the normalised token (commas stripped), integer parts included for decimals, so
    "1,234" in the answer finds 1234 in a row. An EXPLAIN result contributes its estimates,
    not its step and spool numbers."""
    out: Dict[str, List[Tuple[int, int]]] = {}
    for st in statements or []:
        n = st.get("n")
        plan = is_plan(st)
        for i, row in enumerate(st.get("rows") or []):
            if not isinstance(row, dict):
                continue
            for v in row.values():
                if isinstance(v, bool) or v is None:
                    continue
                if isinstance(v, (int, float)):
                    toks = {str(_compute._clean(float(v)))}
                    if isinstance(v, float) and not float(v).is_integer():
                        toks.add(str(int(v)))
                elif isinstance(v, str):
                    toks = norm_nums(v)
                else:
                    continue
                for t in toks:
                    if plan and _plan_noise(t):
                        continue
                    out.setdefault(t, []).append((n, i + 1))
    return out


def column_aggregates(statements: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The SUM and the AVERAGE of every numeric column of every COMPLETE result set — a
    figure an answer derives from the rows ("179,193 robberies in 2023" over twelve monthly
    rows). Only a statement whose rows are all here is summed: a truncated set would give a
    confident wrong total. A column counts as numeric when every non-empty value parses."""
    out: List[Dict[str, Any]] = []
    for st in statements or []:
        rows = [r for r in (st.get("rows") or []) if isinstance(r, dict)]
        rc = st.get("row_count")
        if st.get("truncated") or len(rows) < MIN_ROWS_TO_DERIVE or (isinstance(rc, int) and rc != len(rows)):
            continue
        cols: List[str] = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        for col in cols:
            vals: List[float] = []
            ok = True
            for r in rows:
                v = r.get(col)
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                if isinstance(v, bool):
                    ok = False; break
                n = float(v) if isinstance(v, (int, float)) else _num(str(v).strip())
                if n is None:
                    ok = False; break
                vals.append(n)
            if not ok or len(vals) < MIN_ROWS_TO_DERIVE:
                continue
            total = sum(vals)
            for kind, value in (("sum", total), ("average", total / len(vals))):
                out.append({"statement": st.get("n"), "column": str(col), "kind": kind,
                            "value": value, "rows": len(vals)})
    return out


def _agg_lineage(a: Dict[str, Any]) -> str:
    return f"the {a['kind']} of {a['column']} over the {a['rows']} rows of statement {a['statement']}"


def _fmt_value(v: float) -> str:
    return f"{int(round(v)):,}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}"


_NUMLIT = r"-?\d[\d,]*(?:\.\d+)?"
# A literal the agent wrote into a statement — where a number is a VALUE, not part of a name:
# a quoted string ('120'), the right-hand side of a comparison (= 0, > 1000), an IN list, a
# BETWEEN pair, a TOP / LIMIT / SAMPLE bound. Positional function arguments (SUBSTR(x, 8, 4))
# and identifier fragments (V4019, table_2023) are deliberately NOT literals.
_LIT_STRING = re.compile(r"'(" + _NUMLIT + r")'")
_LIT_COMPARE = re.compile(r"(?:<>|!=|<=|>=|=|<|>)\s*(" + _NUMLIT + r")(?![\w.])")
_LIT_IN = re.compile(r"\bIN\s*\(([^()]*)\)", re.I)
_LIT_BETWEEN = re.compile(r"\bBETWEEN\s+'?(" + _NUMLIT + r")'?\s+AND\s+'?(" + _NUMLIT + r")'?", re.I)
_LIT_BOUND = re.compile(r"\b(?:TOP|LIMIT|SAMPLE)\s+(\d[\d,]*)\b", re.I)
# a date literal's YEAR is a value the answer states ("in 2023") — `DATE '2023-01-01'`
_LIT_DATE = re.compile(r"'((?:19|20)\d{2})-\d{2}-\d{2}")
_LIT_KIND = {_LIT_STRING: "filter", _LIT_COMPARE: "filter", _LIT_BETWEEN: "filter", _LIT_BOUND: "bound"}


def statement_literals(statements: Iterable[Dict[str, Any]]) -> Dict[str, Tuple[int, str]]:
    """Every number the agent wrote into an executed statement as a VALUE → (statement n,
    kind). Keyed by the normalised token (commas stripped); the first statement that carries
    it wins. Pure; a statement with no text contributes nothing."""
    out: Dict[str, Tuple[int, str]] = {}
    def _put(tok: str, n: int, kind: str) -> None:
        norm = tok.replace(",", "")
        if norm and any(ch.isdigit() for ch in norm):
            out.setdefault(norm, (n, kind))
    for st in statements or []:
        n, text = st.get("n"), st.get("statement")
        if not isinstance(n, int) or not isinstance(text, str) or not text:
            continue
        for rx in (_LIT_STRING, _LIT_COMPARE, _LIT_BOUND):
            for m in rx.finditer(text):
                _put(m.group(1), n, _LIT_KIND[rx])
        for m in _LIT_DATE.finditer(text):
            _put(m.group(1), n, "filter")
        for m in _LIT_BETWEEN.finditer(text):
            _put(m.group(1), n, "filter"); _put(m.group(2), n, "filter")
        for m in _LIT_IN.finditer(text):
            for part in m.group(1).split(","):
                v = part.strip().strip("'\"").strip()
                if re.fullmatch(_NUMLIT, v):
                    _put(v, n, "filter")
    return out


def declared_codes(value_domains: Any) -> Dict[str, List[Tuple[str, str, str]]]:
    """Every code the knowledge graph declares for a CODED column → the (table, column, label)
    candidates that declare it. A code is only meaningful WITH its column ('120' is Robbery on
    offence_code and Handgun on armed_with_code), so every candidate is kept and the caller
    resolves by column. Reads the enrichment event's `value_domains` (`[{table, columns:
    [{name, kind, values: {code: label}}]}]`); any other shape yields {} — never raises. A
    label column's values are words, never figures, so only `kind == "code"` columns count."""
    out: Dict[str, List[Tuple[str, str, str]]] = {}
    try:
        for tbl in value_domains or []:
            if not isinstance(tbl, dict):
                continue
            tname = str(tbl.get("table") or "")
            for col in tbl.get("columns") or []:
                if not isinstance(col, dict) or col.get("kind") != "code":
                    continue
                vals = col.get("values")
                if not isinstance(vals, dict):
                    continue
                for code, label in vals.items():
                    norm = str(code).strip().replace(",", "")
                    if norm and re.fullmatch(_NUMLIT, norm):
                        out.setdefault(norm, []).append((tname, str(col.get("name") or ""), str(label or "")))
    except Exception:  # noqa: BLE001 — a malformed domain must never break the classification
        return out
    return out


def literal_column(statement: str, literal: str) -> Optional[str]:
    """The column a literal is compared against in a statement (`offence_code = '120'`,
    `o.offence_code IN ('120','220')`, `yr BETWEEN '2020' AND 2023`) → its leaf name, or None
    when the statement gives no column for it (a TOP bound, an expression)."""
    if not isinstance(statement, str) or not literal:
        return None
    lit = re.escape(literal)
    pats = (r"([A-Za-z_][\w.]*)\s*(?:<>|!=|<=|>=|=|<|>)\s*'?" + lit + r"'?(?![\w.])",
            r"([A-Za-z_][\w.]*)\s+(?:NOT\s+)?IN\s*\([^()]*'?" + lit + r"'?(?![\w.])[^()]*\)",
            r"([A-Za-z_][\w.]*)\s+(?:NOT\s+)?BETWEEN\s+'?" + lit + r"'?(?![\w.])",
            r"([A-Za-z_][\w.]*)\s+(?:NOT\s+)?BETWEEN\s+'?" + _NUMLIT + r"'?\s+AND\s+'?" + lit + r"'?(?![\w.])")
    for pat in pats:
        m = re.search(pat, statement, re.I)
        if m:
            name = m.group(1)
            if name.upper() in ("AND", "OR", "NOT", "WHERE", "ON", "IN", "BETWEEN", "TOP", "LIMIT", "DATE"):
                continue
            return name.rpartition(".")[2]
    return None


def _code_label(cands: List[Tuple[str, str, str]], column: Optional[str]) -> Optional[str]:
    """The declared-code phrase for a literal: only the candidate on the literal's OWN column
    ever names a label — never a coincidence of value across columns."""
    if not cands or not column:
        return None
    for tname, col, label in cands:
        if col.lower() == column.lower():
            leaf = tname.rpartition(".")[2] or tname
            return f"a declared code of {leaf}.{col}" + (f" ({label})" if label else "")
    return None


def assess(answer_text: str, receipts: List[Dict[str, Any]], statements: List[Dict[str, Any]],
           query: str = "",
           value_domains: Any = None,
           vouched_numbers: Any = None) -> Dict[str, Any]:
    """Classify every figure in the answer. Returns the compute-lineage event (also the
    persisted `compute_lineage` shape): decision `na` (nothing to trace against), `pass`
    (every figure traceable) or `note` (unreceipted figures worth a disclosure)."""
    receipts = [r for r in (receipts or []) if isinstance(r, dict)]
    statements = [s for s in (statements or []) if isinstance(s, dict)]
    if not receipts and not statements:
        return {"ran": True, "decision": "na", "reason": "no result sets or receipts this turn",
                "receipts": 0, "figures": 0, "receipted": [], "quoted": [], "literals": [],
                "declared": [], "derived": [], "unreceipted": [], "misderived": []}
    toks: List[str] = []
    for m in _FIGURE_RE.finditer(answer_text or ""):
        t = m.group(0)
        if t in ("-", "") or not any(ch.isdigit() for ch in t):
            continue
        if t not in toks:
            toks.append(t)
    # what the turn's INPUTS account for: this question, plus the set every figure check
    # shares (`RunRecord.vouched_numbers` — the user's earlier words such as a primer's
    # years, KG-declared codes and spans). The grounding gate accepted a primer's `2023`
    # that this check then called the agent's own arithmetic (prod session 502f5e62).
    asked = norm_nums(query or "") | {str(n) for n in (vouched_numbers or ())}
    rf = [(i, f) for i, r in enumerate(receipts) for f in _compute.receipt_figures(r)]
    rows = row_figures(statements)
    # the number of rows a statement returned is a fact the turn holds ("100 products")
    counts = {}
    for st in statements:
        rc = st.get("row_count")
        if is_plan(st):
            continue   # the number of plan lines is not a count of anything the answer reports
        if isinstance(rc, int) and rc > 0:
            counts.setdefault(str(rc), st.get("n"))
    lits = statement_literals(statements)
    codes = declared_codes(value_domains)
    by_n = {st.get("n"): st.get("statement") or "" for st in statements}
    receipted: List[Dict[str, Any]] = []
    quoted: List[Dict[str, Any]] = []
    literals: List[Dict[str, Any]] = []
    declared: List[Dict[str, Any]] = []
    unreceipted: List[str] = []
    derived: List[Dict[str, Any]] = []
    misderived: List[Dict[str, Any]] = []
    aggs: Optional[List[Dict[str, Any]]] = None     # computed only when a figure reaches them
    for t in toks:
        norm = t.replace(",", "")
        hit = next(((i, f) for i, f in rf if matches(t, f)), None)
        if hit is not None:
            receipted.append({"figure": t, "receipt": hit[0], "value": hit[1],
                              "lineage": _compute.describe(receipts[hit[0]])})
            continue
        where = rows.get(norm) or (rows.get(norm.split(".")[0]) if "." in norm else None)
        if where:
            st, row = where[0]
            quoted.append({"figure": t, "statement": st, "row": row, "lineage": f"row {row} of statement {st}"})
            continue
        if norm in counts:
            quoted.append({"figure": t, "statement": counts[norm], "row": 0,
                           "lineage": f"the number of rows statement {counts[norm]} returned"})
            continue
        lit = lits.get(norm)
        if lit is not None:
            n, kind = lit
            how = "used as a filter in" if kind == "filter" else "the bound of"
            lineage = f"{how} statement {n}"
            col = literal_column(by_n.get(n, ""), norm) if kind == "filter" else None
            phrase = _code_label(codes.get(norm) or [], col)
            if phrase:
                lineage += f" — {phrase}"
            literals.append({"figure": t, "statement": n, "kind": kind, "column": col, "lineage": lineage})
            continue
        cands = codes.get(norm)
        if cands:
            if len(cands) == 1:
                tname, col, label = cands[0]
                lineage = f"a declared code of {tname.rpartition('.')[2] or tname}.{col}" + (f" ({label})" if label else "") + ", from the knowledge graph"
            else:
                # the same code on several columns: name them, assert none
                shown = "; ".join(f"{c} ({l})" if l else c for _, c, l in cands[:3]) + (" …" if len(cands) > 3 else "")
                tname, col, label = "", "", ""
                lineage = f"a code the knowledge graph declares on {len(cands)} columns — {shown}"
            declared.append({"figure": t, "table": tname, "column": col, "label": label, "lineage": lineage})
            continue
        if norm.lstrip("-") in asked:
            continue
        if len(norm.lstrip("-").replace(".", "")) < MIN_DIGITS_TO_CONVICT:
            continue
        # Last: a figure every class above missed may still be arithmetic the data vouches
        # for — a column's sum or average. Reached ONLY by a figure that was unreceipted, so
        # no earlier class ever changes.
        if aggs is None:
            aggs = column_aggregates(statements)
        agg = next((a for a in aggs if matches(t, a["value"])), None)
        if agg is not None:
            derived.append({"figure": t, "statement": agg["statement"], "column": agg["column"],
                            "kind": agg["kind"], "value": agg["value"], "rows": agg["rows"],
                            "lineage": _agg_lineage(agg)})
            continue
        n = _num(t)
        near = None
        if n is not None:
            for a in aggs:
                v = a["value"]
                if v and abs(n - v) / abs(v) <= NEAR_MISS_REL and (near is None or abs(n - v) < abs(n - near["value"])):
                    near = a
        if near is not None:
            misderived.append({"figure": t, "statement": near["statement"], "column": near["column"],
                               "kind": near["kind"], "value": near["value"], "rows": near["rows"],
                               "lineage": _agg_lineage(near)})
        unreceipted.append(t)
    decision = "note" if unreceipted else "pass"
    reason = (f"{len(unreceipted)} figure(s) match no compute receipt, no result row, "
              f"no statement literal and no declared code"
              if unreceipted else "every figure traces to a receipt, a result row, "
                                  "a statement literal or a declared code")
    if derived:
        reason += f"; {len(derived)} derived from a result column (a sum or an average)"
    return {"ran": True, "decision": decision, "receipts": len(receipts), "figures": len(toks),
            "receipted": receipted, "quoted": quoted, "literals": literals, "declared": declared,
            "derived": derived, "unreceipted": unreceipted, "misderived": misderived,
            "reason": reason}


def disclosure(event: Dict[str, Any]) -> str:
    """The append-only honesty note for kind `unreceipted_figure`."""
    figs = event.get("unreceipted") or []
    if not figs:
        return ""
    near = {m.get("figure"): m for m in (event.get("misderived") or []) if isinstance(m, dict)}
    rest = [f for f in figs if f not in near]
    parts: List[str] = []
    if rest:
        shown = ", ".join(rest[:6]) + (" …" if len(rest) > 6 else "")
        # the advice names an act the agent can perform: only where Compute is enabled for
        # it (`compute_available`, stamped at the seam; an event without the key keeps the advice)
        advice = "" if event.get("compute_available") is False else " Ask for the computation to have them verified."
        parts.append(f"Note: the figure(s) {shown} match no result row and no compute receipt in this turn — "
                     f"they are the agent's own arithmetic, not a computed or quoted value.{advice}")
    for f in [f for f in figs if f in near][:6]:
        m = near[f]
        parts.append(f"Note: {f} is close to, but is not, {m.get('lineage')}, which is "
                     f"{_fmt_value(float(m.get('value') or 0))} — the stated figure is the agent's own arithmetic.")
    return " ".join(parts)


# ── the marks ────────────────────────────────────────────────────────────────
def _wrap(final_html: str, figure: str, cls: str, tip: str, extra: str = "") -> str:
    esc = _html.escape(figure)
    core = re.escape(esc)
    core = r"(?<![\w.,])" + core + r"(?!\w|[.,]\d)"
    pat = re.compile(core + r"(?![^<]*>)")
    # a receipted/quoted figure is a control (it opens its rows): focusable, named for a
    # screen reader with its lineage; an unreceipted one is a plain marked span
    a11y = (f' tabindex="0" role="button" aria-label="{esc} — {_html.escape(tip, quote=True)}"'
            if cls in ("figure-receipted", "figure-quoted") else f' aria-label="{esc} — {_html.escape(tip, quote=True)}"')
    # a declared code / a literal shares the quoted family's look; `_wrap` only styles by class
    repl = (f'<mark class="{cls} u-tip" data-tip="{_html.escape(tip, quote=True)}"{a11y}{extra}>{esc}</mark>')
    out, _ = pat.subn(lambda m: repl, final_html, count=MAX_MARKS_PER_FIGURE)
    return out


_CELL_RE = re.compile(r"<(td|th|code|pre)\b[^>]*>.*?</\1>", re.I | re.S)
def _mask_cells(html: str) -> Tuple[str, List[str]]:
    """Table cells, code and pre are the DATA — a figure there needs no lineage mark (its
    lineage is the table). Mask them out of the wrapping, restore after."""
    return _mask(_CELL_RE, html, "\x00")


def _unmask_cells(html: str, kept: List[str]) -> str:
    return _unmask(html, kept, "\x00")


def mark_html(final_html: str, event: Optional[Dict[str, Any]], turn_id: Any = None) -> str:
    """Inline number lineage in the rendered answer: a receipted figure wears a faint dotted
    underline whose hover names its lineage ("SUM(miles) · 149 rows · statement 2"); a quoted
    figure says "row 3 of statement 2"; a literal says "used as a filter in statement 1" and
    opens that statement; a declared code names its column and label from the graph (no
    rows to open); an unreceipted figure carries the honesty note — never
    a red mark on a number that may still be right. Never wraps inside a tag; a figure that
    does not cleanly match is left alone. Best-effort — the original HTML on any error."""
    try:
        if not final_html or not event or event.get("decision") == "na":
            return final_html
        out, kept = _mask_cells(final_html)
        # every remaining tag too: a mark lands in TEXT only, never in an attribute value
        out, tags = _mask_tags(out)
        tid = f' data-turn-id="{_html.escape(str(turn_id), quote=True)}"' if turn_id is not None else ""
        # longest figures first so "1,550.5" is wrapped before "550" could be
        items: List[Tuple[str, str, str, str]] = []
        for r in event.get("receipted") or []:
            items.append((r["figure"], "figure-receipted", f"Computed: {r['lineage']} — click to see the rows",
                          f' data-receipt="{int(r["receipt"])}"{tid}'))
        for q in event.get("quoted") or []:
            items.append((q["figure"], "figure-quoted", f"Quoted from the data: {q['lineage']} — click to see the rows",
                          f' data-statement="{int(q["statement"])}" data-row="{int(q["row"])}"{tid}'))
        for l in event.get("literals") or []:
            items.append((l["figure"], "figure-quoted", f"From the statement: {l['lineage']} — click to see the statement and its rows",
                          f' data-statement="{int(l["statement"])}" data-row="0" data-literal="{_html.escape(str(l.get("kind") or "filter"), quote=True)}"{tid}'))
        for g in event.get("derived") or []:
            items.append((g["figure"], "figure-quoted", f"Computed from the data: {g['lineage']} — click to see the rows",
                          f' data-statement="{int(g["statement"])}" data-row="0" data-derived="{_html.escape(str(g.get("kind") or "sum"), quote=True)}" data-column="{_html.escape(str(g.get("column") or ""), quote=True)}"{tid}'))
        for d in event.get("declared") or []:
            items.append((d["figure"], "figure-declared", f"Declared in the knowledge graph: {d['lineage']}", tid))
        near = {m.get("figure"): m for m in (event.get("misderived") or []) if isinstance(m, dict)}
        for u in event.get("unreceipted") or []:
            tip = ("Not in any result row or compute receipt — the agent's own arithmetic (see the note)"
                   if u not in near else
                   f"Close to, but not, {near[u].get('lineage')} ({_fmt_value(float(near[u].get('value') or 0))}) — the agent's own arithmetic (see the note)")
            items.append((u, "figure-unreceipted", tip, tid))
        for figure, cls, tip, extra in sorted(items, key=lambda x: -len(x[0])):
            if f">{_html.escape(figure)}</mark>" in out:
                continue
            out = _wrap(out, figure, cls, tip, extra)
        return _unmask_cells(_unmask_tags(out, tags), kept)
    except Exception:  # noqa: BLE001
        return final_html


def note_html(event: Optional[Dict[str, Any]]) -> str:
    """The disclosure as an Answer-Surface NOTE (kind `figures`, warn tone)."""
    txt = disclosure(event or {})
    if not txt:
        return ""
    from trusted_data_agent.agent import answer_surface as _ans
    return _ans.note("figures", txt)


def step_title(event: Optional[Dict[str, Any]]) -> str:
    """The Live Status title of the lineage step — ONE rule for every engine's live step and
    (mirrored in `eventHandlers.js:_assuranceStepTitle`) its reload title. A turn that
    combined results says so; one that read several sources says so; otherwise the title is
    exactly what it always was."""
    src = (event or {}).get("sources") if isinstance(event, dict) else None
    if isinstance(src, dict):
        if src.get("combines"):
            return "Number Lineage — Combined Results"
        if len(src.get("sources") or []) > 1:
            return "Number Lineage — Several Sources"
    return "Number Lineage"
