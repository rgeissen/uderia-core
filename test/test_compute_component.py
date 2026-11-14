"""S3 — Compute with Lineage (spec: docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S3).

The gate: every op against hand-computed truth incl. NULLs and empty sets · the receipt is
canonical and its figures are what it vouches for · the result-set registry records data
statements only, off ⇒ nothing · the component serves exactly under the switch and refuses
recovery-shaped · the receipts are sealed and VERIFY against the persisted stamp (a tampered
stamp is a named mismatch) · numeric fidelity counts a receipted figure as grounded · the
lineage classifies every figure once and marks the HTML without touching tags · the
assurance reading · the enablement rule keeps an inert component from pulling its companion
skill · the seams are wired (AST). CI-pure: no server, no network, no model.
"""
import ast, asyncio, hashlib, json, logging, os, pathlib, re, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.setdefault("TDA_COMPUTE_LINEAGE", "1")
from trusted_data_agent.core.config import APP_CONFIG
APP_CONFIG.COMPUTE_LINEAGE = True
from trusted_data_agent.agent import compute as C, result_sets as RS, compute_lineage as L

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()

ROWS = [{"city": "A", "miles": "1,200", "state": "CA"}, {"city": "B", "miles": 300, "state": "NY"},
        {"city": "C", "miles": None, "state": "CA"}, {"city": "A", "miles": 50.5, "state": "CA"}]

print("[1] every op against hand-computed truth")
r = C.compute("sum", ROWS); check("sum parses '1,200', skips the NULL, uses 3 of 4 rows", r["value"] == 1550.5 and r["receipt"]["row_count_used"] == 3 and r["receipt"]["row_count"] == 4, r)
check("avg", abs(C.compute("avg", ROWS)["value"] - 516.833333) < 1e-6)
check("min / max", C.compute("min", ROWS)["value"] == 50.5 and C.compute("max", ROWS)["value"] == 1200)
check("count counts rows; count(column) counts non-null", C.compute("count", ROWS)["value"] == 4 and C.compute("count", ROWS, column="miles")["value"] == 3)
rk = C.compute("rank", ROWS, n=2); check("rank top 2 desc with rank numbers", [x["city"] for x in rk["result_rows"]] == ["A", "B"] and rk["result_rows"][0]["rank"] == 1 and rk["receipt"]["values"] == [1200, 300], rk)
check("rank asc", C.compute("rank", ROWS, n=1, order="asc")["result_rows"][0]["miles"] == 50.5)
pv = C.compute("pivot", ROWS, by="state"); check("pivot sum by state keeps first-seen order, honest None for an all-null group", [ (x["state"], x["sum_miles"]) for x in pv["result_rows"]] == [("CA", 1250.5), ("NY", 300)], pv["result_rows"])
pc = C.compute("pivot", ROWS, by="state", agg="count"); check("pivot count needs no column", [x["count"] for x in pc["result_rows"]] == [3, 1])
fl = C.compute("filter", ROWS, where="miles > 100"); check("filter numeric predicate", [x["city"] for x in fl["result_rows"]] == ["A", "B"])
check("filter contains (case-insensitive)", len(C.compute("filter", ROWS, where="state contains c")["result_rows"]) == 3)
try:
    C.compute("filter", ROWS, where="miles ~ 3"); check("filter refuses an unreadable predicate", False)
except C.ComputeError as e:
    check("filter refuses an unreadable predicate, naming the operators", "column op value" in str(e))
pc2 = C.compute("pct_change", [{"m": "jan", "v": 100}, {"m": "feb", "v": 150}, {"m": "mar", "v": 120}], column="v")
check("pct_change first→last + the series", pc2["value"] == 20 and [s.get("pct_change") for s in pc2["result_rows"]] == [None, 50, -20])
jn = C.compute("join", ROWS, rows_b=[{"city": "A", "pop": 9}], on="city"); check("join on the key", len(jn["result_rows"]) == 2 and all(x["pop"] == 9 for x in jn["result_rows"]))
df = C.compute("diff", [{"k": 1, "v": 10}, {"k": 2, "v": 5}], rows_b=[{"k": 1, "v": 4}, {"k": 3, "v": 1}], on="k", column="v")
check("diff per key + one-side-only keys", [(x["k"], x["diff"], x["only_in"]) for x in df["result_rows"]] == [(1, 6, None), (2, None, "a"), (3, None, "b")])
for op in ("sum", "avg", "min", "max", "count", "rank", "pivot", "filter", "pct_change"):
    kw = {"by": "x"} if op == "pivot" else ({"where": "a > 1"} if op == "filter" else {})
    r = C.compute(op, [], **kw)
    check(f"{op} over an EMPTY set is honest (None / 0 / [])", r["value"] in (None, 0) and (r["result_rows"] in (None, []) ), r)
try:
    C.compute("median", ROWS); check("an unknown op is refused", False)
except C.ComputeError as e:
    check("an unknown op is refused naming the closed set", all(o in str(e) for o in C.OPS))
try:
    C.compute("sum", [{"a": 1, "b": 2}]); check("two numeric columns → refusal", False)
except C.ComputeError as e:
    check("two numeric columns → the refusal names both", "a, b" in str(e))
check("column match is case-tolerant", C.compute("sum", ROWS, column="MILES")["value"] == 1550.5)
check("as_number: tolerant strings, never booleans/prose", C.as_number("$1,234.50") == 1234.5 and C.as_number(True) is None and C.as_number("n/a") is None and C.as_number("7 %") == 7)

print("[2] the receipt is canonical")
r1 = C.compute("sum", ROWS, statements=[2]); r2 = C.compute("sum", list(ROWS), statements=[2])
check("same computation ⇒ same receipt hash; fields closed", r1["receipt"]["receipt_hash"] == r2["receipt"]["receipt_hash"] and set(json.loads(C.receipt_content(r1["receipt"])).keys()) == set(C.RECEIPT_FIELDS))
check("a different statement ⇒ a different receipt", C.compute("sum", ROWS, statements=[3])["receipt"]["receipt_hash"] != r1["receipt"]["receipt_hash"])
check("receipt_figures: the scalar and the per-row values", C.receipt_figures(r1["receipt"]) == [1550.5] and C.receipt_figures(rk["receipt"]) == [1200, 300])
check("describe reads like a lineage", C.describe(r1["receipt"]) == "SUM(miles) · 3 of 4 rows · statement 2", C.describe(r1["receipt"]))

print("[3] the result-set registry")
RS.begin_turn("s1", 7)
n1 = RS.record("s1", "base_readQuery", {"sql": "SELECT city, miles FROM t"}, {"status": "success", "results": ROWS})
n2 = RS.record("s1", "base_readQuery", {"sql": "SELECT 1"}, {"status": "success", "results": [{"x": 1}]})
check("data statements are numbered in execution order", (n1, n2) == (1, 2))
check("a framework tool / a row-less result / no session is never a statement",
      RS.record("s1", "TDA_Charting", {}, {"results": ROWS}) is None and RS.record("s1", "base_readQuery", {"sql": "x"}, {"results": []}) is None and RS.record(None, "base_readQuery", {}, {"results": ROWS}) is None)
check("get: latest by default, by number, negative from the end, unknown → None",
      RS.get("s1")["n"] == 2 and RS.get("s1", 1)["n"] == 1 and RS.get("s1", -1)["n"] == 2 and RS.get("s1", 9) is None and RS.get("s1", "latest")["n"] == 2)
RS.begin_turn("s1", 8)
check("begin_turn resets the session's statements", RS.statements("s1") == [] and RS.stamp_for_turn("s1") is None)
APP_CONFIG.COMPUTE_LINEAGE = False
check("switch off ⇒ nothing is recorded", RS.record("s1", "base_readQuery", {"sql": "x"}, {"results": ROWS}) is None and not RS.enabled())
APP_CONFIG.COMPUTE_LINEAGE = True

print("[4] the component: served under the switch, refusals recovery-shaped, receipts recorded")
from trusted_data_agent.components.manager import get_component_manager
from trusted_data_agent.components import enablement as EN
mgr = get_component_manager(); comp = mgr.get_component("compute")
check("the compute component is discovered with its tool", comp is not None and comp.tool_definition["name"] == "TDA_Compute")
check("relevant with the switch on", EN.relevant(comp, {}))
APP_CONFIG.COMPUTE_LINEAGE = False
check("NOT relevant with the switch off (so never served, never pulls its companion skill)", not EN.relevant(comp, {}) and not EN.is_enabled(comp, {"profile_type": "llm_only"}, None))
APP_CONFIG.COMPUTE_LINEAGE = True
check("enabled for a componentConfig-less profile with the switch on", EN.is_enabled(comp, {"profile_type": "llm_only"}, None))
RS.begin_turn("s2", 1); RS.record("s2", "base_readQuery", {"sql": "SELECT city, miles FROM t"}, {"status": "success", "results": ROWS})
async def _drive():
    h = comp.handler
    p = await h.process({"op": "sum"}, {"session_id": "s2"})
    check("sum over the latest statement: value, title with lineage, receipt on metadata", p.metadata.get("value") == 1550.5 and "SUM(miles)" in p.title and p.metadata["receipt"]["statements"] == [1] and p.html and "Computed by code" in p.html)
    p2 = await h.process({"op": "rank", "n": 1, "column": "miles"}, {"session_id": "s2"})
    check("rank returns rows in the spec and a table card", p2.spec["rows"][0]["city"] == "A" and "<table" in p2.html)
    e1 = await h.process({"op": "sum", "column": "nope"}, {"session_id": "s2"})
    check("an unknown column is refused naming the columns (spec.error — the adapter's failed-call contract)", "columns: city, miles" in e1.spec.get("error", "") and e1.metadata.get("suppress_inline") is True)
    e2 = await h.process({"op": "sum"}, {"session_id": "s-none"})
    check("no result set yet → recovery-shaped refusal", "run the query first" in e2.spec.get("error", ""))
    e3 = await h.process({"op": "sum", "statement": 5}, {"session_id": "s2"})
    check("an unknown statement names the available ones", "statements available: 1" in e3.spec.get("error", ""))
    e4 = await h.process({"op": "join"}, {"session_id": "s2"})
    check("join without statement_b is refused before any work", "statement_b" in e4.spec.get("error", ""))
    e5 = await h.process({"op": "median"}, {"session_id": "s2"})
    check("an unknown op is refused with the closed set", "sum, avg" in e5.spec.get("error", ""))
asyncio.run(_drive())
recs = RS.receipts("s2")
check("only the SUCCESSFUL computations left receipts (2), each with sample rows", len(recs) == 2 and all(r.get("sample_rows") for r in recs))
stamp = RS.stamp_for_turn("s2")
check("the turn stamp carries receipts + a compact statement index (no rows)", stamp and len(stamp["receipts"]) == 2 and stamp["statements"][0]["row_count"] == 4 and "rows" not in stamp["statements"][0] and "sample_rows" not in stamp["statements"][0])
# a figure QUOTED from a statement's rows: its statement carries a bounded sample so the lineage
# dialog opens on the row after a reload — the same reach a receipted figure has
RS.begin_turn("s3", 1)
RS.record("s3", "base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM t"}, {"status": "success", "results": [{"n": 747}]})
RS.record("s3", "base_readQuery", {"sql": "SELECT * FROM u"}, {"status": "success", "results": [{"a": i} for i in range(80)]})
check("a turn with tool statements only and nothing computed or quoted has NO stamp (the honest baseline)", RS.stamp_for_turn("s3") is None)
RS.mark_quoted("s3", [1, "1", 99])
st3 = RS.stamp_for_turn("s3")
check("mark_quoted: the stamp appears, the quoted statement carries its rows, the other statement carries none", st3 is not None and st3["receipts"] == [] and st3["statements"][0].get("sample_rows") == [{"n": 747}] and "sample_rows" not in st3["statements"][1])
RS.mark_quoted("s3", [2])
check("the sample is bounded (SAMPLE_ROWS), never the whole result", len(RS.stamp_for_turn("s3")["statements"][1]["sample_rows"]) == RS.SAMPLE_ROWS)
check("mark_quoted on an unknown session or malformed numbers is a no-op, never an error", RS.mark_quoted("nope", [1]) is None and RS.mark_quoted("s3", ["x", None, 3.5]) is None)
_ex = open("src/trusted_data_agent/agent/executor.py").read()
# the property, not the expression: the quoted statements are among what the seam marks,
# before the turn persists (the call grew a second list — the derived statements — Sep 2026)
_mq = _ex[_ex.index("_rs.mark_quoted("):_ex.index("self._compute_lineage_event = event\n            return text, html, event")]
check("the executor marks the quoted statements at the lineage seam, BEFORE the turn persists",
      '[q.get("statement") for q in (event.get("quoted") or [])]' in _mq)
_fl = open("static/js/handlers/figureLineage.js").read()
check("the lineage dialog reads a quoted figure's rows from its STATEMENT first, a receipt's rows as the fallback", "st.sample_rows.length) ? st.sample_rows : (rc ? rc.sample_rows : null)" in _fl)

print("[5] receipts sealed + verified against the persisted stamp")
from trusted_data_agent.core import provenance as P
chain = P.ProvenanceChain(session_id="s2", turn_number=1, user_uuid="u", profile_type="tool_enabled")
for r in recs:
    chain.add_step("compute_step", C.receipt_content(r), C.describe(r))
turn = {"compute": stamp}
v = P.verify_content({"provenance_chain": chain.steps}, turn)
check("every sealed receipt verifies against the stamp", v["verified"] == 2 and not v["mismatches"], v)
bad = json.loads(json.dumps(turn)); bad["compute"]["receipts"][0]["value"] = 9999
v2 = P.verify_content({"provenance_chain": chain.steps}, bad)
check("a tampered receipt value is a NAMED mismatch", len(v2["mismatches"]) == 1 and "compute_step" in v2["mismatches"][0], v2)
v3 = P.verify_content({"provenance_chain": chain.steps}, {"compute": {"receipts": []}})
check("a receipt the record does not show is a mismatch, never a skip", len(v3["mismatches"]) == 2)

print("[6] numeric fidelity counts a receipted figure as grounded")
from trusted_data_agent.eval.scorers.reliability import NumericFidelityScorer
from trusted_data_agent.eval.types import RunRecord, EvalCase
base = {"user_query": "total miles?", "final_summary_text": "B has 300 miles; the total across the cities is 1,550.5.", "status": "success", "profile_type": "llm_only",
        "conversation_agent_events": [{"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT city, miles FROM t"}}},
                                      {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": True, "result_preview": "[{'city':'B','miles':300}]", "result_rows": 4}}]}
case = EvalCase(id="c", query="total miles?", profile_class="conversation_with_tools")
before = NumericFidelityScorer().score(case, RunRecord(base))
withr = NumericFidelityScorer().score(case, RunRecord(dict(base, compute=stamp)))
check("without a receipt the computed total is NOT grounded (1 of 3 tokens: 300 quoted; 1550.5 and its integer part not)", abs(before.score - 1/3) < 1e-3, before.score)
check("with the receipt it IS grounded (3/3) and the explanation says so", withr.score == 1.0 and "by lineage" in (withr.explanation or ""), (withr.score, withr.explanation))
_lin_turn = dict(base, final_summary_text="Row values: 4599 and 24.99; total 1,550.5.", compute=stamp,
                 compute_lineage={"quoted": [{"figure": "4599", "statement": 1, "row": 3}, {"figure": "24.99", "statement": 1, "row": 5}]})
_lv = NumericFidelityScorer().score(case, RunRecord(_lin_turn))
check("a figure the lineage QUOTED from a full result row is grounded even when the scorer's preview lacks it", _lv.score == 1.0, (_lv.score, _lv.explanation))
check("RunRecord.compute_receipts reads the stamp; empty without it", len(RunRecord(dict(base, compute=stamp)).compute_receipts()) == 2 and RunRecord(base).compute_receipts() == [])

print("[7] the lineage: every figure classified once; marks never touch a tag; the disclosure")
sts = RS.statements("s2")
ev = L.assess("B has 300 miles; the total is 1,550.5; I estimate 1,700 next year; 3 cities; asked for 2024.", recs, sts, query="what about 2024?")
check("receipted / quoted / unreceipted classified; the question's number and short tokens never convicted",
      [x["figure"] for x in ev["receipted"]] == ["1,550.5"] and [x["figure"] for x in ev["quoted"]] == ["300"] and ev["unreceipted"] == ["1,700"] and ev["decision"] == "note", ev)
check("quoted lineage names row and statement", ev["quoted"][0]["lineage"] == "row 2 of statement 1")
html = '<p>B has <b>300</b> miles; the total is 1,550.5; I estimate 1,700. <a href="/x?q=1,550.5">link</a></p>'
out = L.mark_html(html, ev, turn_id=4)
check("receipted figure wears its lineage + receipt index + turn", 'class="figure-receipted u-tip"' in out and 'data-receipt="0"' in out and 'data-turn-id="4"' in out and "SUM(miles)" in out)
check("quoted figure wears row/statement", 'class="figure-quoted u-tip"' in out and 'data-statement="1" data-row="2"' in out)
check("a receipted/quoted figure is a keyboard-reachable control named with its lineage; an unreceipted one is not a control",
      'role="button" aria-label="1,550.5 — Computed: SUM(miles)' in out and out.count('tabindex="0"') == 2 and 'aria-label="1,700 — Not in any' in out and out.count('role="button"') == 2)
check("unreceipted figure is marked, never red-classed", 'class="figure-unreceipted u-tip"' in out and "figure-danger" not in out)
check("a figure inside a tag attribute is never wrapped", 'href="/x?q=1,550.5"' in out)
check("na decision ⇒ HTML untouched; no event ⇒ untouched", L.mark_html(html, {"decision": "na"}) == html and L.mark_html(html, None) == html)
_tbl = '<p>Total 1,550.5 over 4 rows.</p><table><tr><td>300</td><td>1,550.5</td></tr></table><code>n=300</code>' + ''.join(f'<td>{i}</td>' for i in range(30))
_ev2 = L.assess("Total 1,550.5 over 4 rows. FB-5000 is a name.", recs, sts)
_out2 = L.mark_html(_tbl, _ev2, 1)
check("table cells, code and pre are the data: never marked; placeholders never leak", "<td>300</td><td>1,550.5</td>" in _out2 and "<code>n=300</code>" in _out2 and "\x00" not in _out2 and _out2.endswith("<td>29</td>") and _out2.count("<mark") == 2)
check("a number glued to an identifier (FB-5000) is not a figure; a statement's row count IS lineage",
      not any(x["figure"] == "-5000" or x["figure"] == "5000" for x in _ev2["quoted"] + _ev2["receipted"]) and "-5000" not in _ev2["unreceipted"]
      and any(q["figure"] == "4" and "number of rows" in q["lineage"] for q in _ev2["quoted"]))
check("matches: precision-tolerant, never a near miss", L.matches("516.83", 516.833333) and L.matches("517", 516.8333) and L.matches("516", 516.8333) and not L.matches("516.9", 516.8333) and not L.matches("520", 516.8))
ev_na = L.assess("nothing", [], []); check("no receipts and no statements ⇒ na", ev_na["decision"] == "na")
ev_pass = L.assess("The total is 1,550.5.", recs, sts); check("every figure traced ⇒ pass, no disclosure", ev_pass["decision"] == "pass" and L.disclosure(ev_pass) == "")
check("the disclosure names the figures and never calls them wrong", "1,700" in L.disclosure(ev) and "own arithmetic" in L.disclosure(ev) and "wrong" not in L.disclosure(ev))
check("the note is an Answer-Surface note (kind figures)", 'data-kind="figures"' in L.note_html(ev) and "ans-note" in L.note_html(ev))

print("[7b] a figure the turn's INPUTS account for — a statement literal, a declared code — is never arithmetic")
# The prod turn that motivated this (03d85a6c t2, 2026-09-22): "There are 179,193 reported
# robberies (offence code '120')" — 179,193 quoted from the row, and '120' called "the agent's
# own arithmetic" by a classifier that knew only rows and receipts.
_sts7 = [{"n": 1, "statement": "SELECT COUNT(*) AS total_robberies FROM Policing_Core.v_Offence WHERE offence_code = '120'",
          "rows": [{"total_robberies": "179193"}], "row_count": 1},
         {"n": 2, "statement": "SELECT TOP 100 SUBSTR(incident_date_text, 8, 4) AS yr FROM v_Incident WHERE V4019 = 0 AND yr BETWEEN '2020' AND 2023 AND loc IN ('2', '7', 'x') AND d >= DATE '2019-01-01'",
          "rows": [{"yr": "2023"}], "row_count": 1}]
_vd7 = [{"table": "Policing_Core.v_Offence", "columns": [
            {"name": "offence_code", "kind": "code", "values": {"120": "Robbery", "267": "Hacking/Computer Invasion"}},
            {"name": "offence_type", "kind": "label", "values": {"Robbery": "Robbery"}}]}]
lits = L.statement_literals(_sts7)
check("a quoted string literal, a comparison, an IN member, a BETWEEN pair, a date literal's year and a TOP bound are literals",
      lits.get("120") == (1, "filter") and lits.get("0") == (2, "filter") and lits.get("2") == (2, "filter") and lits.get("2020") == (2, "filter")
      and lits.get("2023") == (2, "filter") and lits.get("2019") == (2, "filter") and lits.get("100") == (2, "bound"), lits)
check("an identifier fragment (V4019) and a positional function argument (SUBSTR …, 8, 4) are NOT literals",
      "4019" not in lits and "8" not in lits and "4" not in lits, lits)
check("a non-numeric IN member is ignored; the first statement carrying a literal wins", "x" not in lits and lits["120"][0] == 1)
codes = L.declared_codes(_vd7)
check("declared codes come from CODE columns only, every candidate kept with table, column and label",
      codes.get("120") == [("Policing_Core.v_Offence", "offence_code", "Robbery")] and "Robbery" not in codes, codes)
# The label follows the COLUMN, never the value: '120' is Robbery on offence_code and Handgun on
# armed_with_code (the first live replay attached Handgun to the robbery filter — a coincidence of
# value across columns read as a fact). The column is read off the statement the literal sits in.
_vd7x = _vd7 + [{"table": "Policing_Core.v_Arrestee", "columns": [{"name": "armed_with_code", "kind": "code", "values": {"120": "Handgun", "2": "Rifle"}}]},
                {"table": "Policing_Core.v_Agency", "columns": [{"name": "agency_type_code", "kind": "code", "values": {"2": "County"}}]}]
check("literal_column reads the compared column off =, IN and BETWEEN (alias-qualified too) and gives none for a bound",
      L.literal_column("SELECT c FROM t o WHERE o.offence_code = '120'", "120") == "offence_code"
      and L.literal_column("… WHERE loc IN ('2', '7')", "7") == "loc"
      and L.literal_column("… WHERE yr BETWEEN '2020' AND 2023", "2023") == "yr"
      and L.literal_column("SELECT TOP 100 x FROM t", "100") is None)
_ev7x = L.assess("robberies (code '120'); arrestees armed with 120; agencies of type 2; and 267.",
                 [], [{"n": 1, "statement": "SELECT COUNT(*) FROM Policing_Core.v_Offence WHERE offence_code = '120' AND agency_type_code = '2'", "rows": [{"c": "1"}], "row_count": 1}],
                 value_domains=_vd7x)
_lin7x = {l["figure"]: l["lineage"] for l in _ev7x["literals"]}
check("a literal names the declared code of ITS column — Robbery on offence_code, County on agency_type_code — never the other column's label",
      "offence_code (Robbery)" in _lin7x["120"] and "Handgun" not in _lin7x["120"]
      and "agency_type_code (County)" in _lin7x["2"] and "Rifle" not in _lin7x["2"]
      and _ev7x["literals"][0]["column"] == "offence_code", _lin7x)
check("a literal whose column declares no such code carries NO label (a value shared by another column is not a fact about this one)",
      "Robbery" not in L.assess("code 120", [], [{"n": 1, "statement": "SELECT c FROM t WHERE other_code = '120'", "rows": [{"c": "1"}], "row_count": 1}],
                                 value_domains=_vd7x)["literals"][0]["lineage"])
_dec7x = {d["figure"]: d for d in _ev7x["declared"]}
check("a bare code declared on ONE column names it; a bare code declared on several is said to be ambiguous, asserting none of them",
      "offence_code (Hacking" in _dec7x["267"]["lineage"] and _dec7x["267"]["column"] == "offence_code"
      and "declares on 2 columns" in L.assess("code 2", [], [{"n": 1, "statement": "SELECT c FROM t", "rows": [{"c": "1"}], "row_count": 1}], value_domains=_vd7x)["declared"][0]["lineage"]
      and L.assess("code 2", [], [{"n": 1, "statement": "SELECT c FROM t", "rows": [{"c": "1"}], "row_count": 1}], value_domains=_vd7x)["declared"][0]["column"] == "")
for bad in (None, "x", [{"table": 1}], [{"columns": [{"kind": "code", "values": ["120"]}]}]):
    check(f"a malformed value domain yields nothing, never raises: {bad!r}", L.declared_codes(bad) == {}, bad)
ev7 = L.assess("There are 179,193 reported robberies (offence code '120'); hacking is code 267; the top 100 rows; I estimate 1,700 next year.",
               [], _sts7, value_domains=_vd7)
check("the prod turn: 179,193 quoted from the row, '120' a literal of statement 1 carrying the declared label, 267 declared from the graph, 100 the bound",
      [q["figure"] for q in ev7["quoted"]] == ["179,193"]
      and [l["figure"] for l in ev7["literals"]] == ["120", "100"]
      and ev7["literals"][0]["statement"] == 1 and "used as a filter in statement 1" in ev7["literals"][0]["lineage"] and "Robbery" in ev7["literals"][0]["lineage"]
      and ev7["literals"][1]["kind"] == "bound"
      and [d["figure"] for d in ev7["declared"]] == ["267"] and "Hacking" in ev7["declared"][0]["lineage"], ev7)
check("…and the genuine arithmetic (1,700) is STILL convicted beside them — the class narrows the conviction, never the check",
      ev7["unreceipted"] == ["1,700"] and ev7["decision"] == "note" and "1,700" in L.disclosure(ev7) and "120" not in L.disclosure(ev7))
ev7b = L.assess("There are 179,193 reported robberies (offence code '120').", [], _sts7[:1], value_domains=_vd7)
check("the prod answer alone: every figure traces ⇒ pass, no note", ev7b["decision"] == "pass" and L.disclosure(ev7b) == "" and ev7b["unreceipted"] == [])
ev7c = L.assess("There are 179,193 reported robberies (offence code '120').", [], _sts7[:1])
check("without value domains the literal STILL traces (the statement is enough); the declared list is empty",
      ev7c["decision"] == "pass" and [l["figure"] for l in ev7c["literals"]] == ["120"] and ev7c["declared"] == [] and "Robbery" not in ev7c["literals"][0]["lineage"])
# ordering: a row value beats a literal; a literal beats a declared code
ev7d = L.assess("code 120 and 179193", [], [{"n": 1, "statement": "SELECT c FROM t WHERE c = '120'", "rows": [{"c": "120", "n": "179193"}], "row_count": 1}], value_domains=_vd7)
check("a figure that is BOTH a row value and a literal is quoted from the row (the output beats the input)", [q["figure"] for q in ev7d["quoted"]] == ["120", "179193"] and ev7d["literals"] == [])
# no-regression: the legacy event shape is intact, and a turn without literals/codes is byte-identical to before
ev7e = L.assess("The total is 1,550.5.", recs, sts)
check("the legacy keys keep their meaning; the new lists are present and empty when nothing applies",
      ev7e["receipted"] and ev7e["quoted"] == [] and ev7e["literals"] == [] and ev7e["declared"] == [] and ev7e["unreceipted"] == [] and ev7e["decision"] == "pass")
check("the na event carries the new lists too (a reader never branches on their absence)", L.assess("x", [], [])["literals"] == [] and L.assess("x", [], [])["declared"] == [])
_h7 = L.mark_html("<p>There are 179,193 robberies (offence code '120'); hacking is code 267.</p>", ev7, turn_id=2)
check("a literal wears the quoted family's mark, opens its STATEMENT (row 0) and says so; a declared code is a plain marked span naming the graph",
      'class="figure-quoted u-tip" data-tip="From the statement: used as a filter in statement 1' in _h7 and 'data-statement="1" data-row="0" data-literal="filter"' in _h7
      and 'class="figure-declared u-tip" data-tip="Declared in the knowledge graph: a declared code of v_Offence.offence_code (Hacking/Computer Invasion)' in _h7
      and 'role="button" aria-label="120 —' in _h7 and 'role="button" aria-label="267' not in _h7, _h7)
# the seam hands the graph's value domains to the classifier, and the scorer counts both classes as traceable
import inspect as _inspect7
from trusted_data_agent.agent.executor import PlanExecutor as _PE7
_ex7 = _inspect7.getsource(_PE7._run_compute_lineage)
check("the executor seam reads value_domains off the turn's enrichment event and passes them to assess()",
      'get("value_domains")' in _ex7 and "value_domains=_vd" in _ex7)
_sc7 = _inspect7.getsource(NumericFidelityScorer.score)
check("numeric_fidelity counts a literal and a declared code as traceable by lineage", 'lineage.get("literals")' in _sc7 and 'lineage.get("declared")' in _sc7)
_lt7 = dict(base, final_summary_text="179193 robberies (code 120).", compute=stamp,
            compute_lineage={"quoted": [{"figure": "179193", "statement": 1, "row": 1}], "literals": [{"figure": "120", "statement": 1}]})
check("…live: the prod answer scores 2/2 grounded", NumericFidelityScorer().score(case, RunRecord(_lt7)).score == 1.0)

print("[8] the assurance reading (GROUNDED)")
from trusted_data_agent.agent import assurance_points as AP
pt = next(p for p in AP.REGISTRY if p.key == "compute_lineage")
check("registered at GROUNDED as a control that can only annotate, on the data classes", pt.station == "grounded" and pt.power == "alter" and AP.POINT_CLASS["compute_lineage"] == "control" and pt.applies("tool_enabled") and not pt.applies("rag_focused"))
check("owns no dimension (numeric_fidelity stays with the grounding gate) and says why", AP.POINT_DIMENSIONS["compute_lineage"] == () and AP.UNGRADED_REASON["compute_lineage"][0] == "by_design")
check("absent stamp → na, never a pass", AP._read_compute_lineage({}, "tool_enabled").state == "na")
check("unreceipted figure → warn naming it and whether disclosed", AP._read_compute_lineage({"compute_lineage": ev}, "tool_enabled").state == "warn" and "1,700" in AP._read_compute_lineage({"compute_lineage": ev}, "tool_enabled").detail)
check("every figure traced → pass", AP._read_compute_lineage({"compute_lineage": ev_pass}, "tool_enabled").state == "pass")
check("na decision → na", AP._read_compute_lineage({"compute_lineage": ev_na}, "tool_enabled").state == "na")
check("partition still conflict-free; the point is post-door", not AP.partition_conflicts() and "compute_lineage" in AP._POST_DOOR_POINTS)

print("[8b] the executor seam, driven (a stub self)")
from trusted_data_agent.agent.executor import PlanExecutor as _PE
from trusted_data_agent.core import provenance as _P
def _seam(mode):
    RS.begin_turn("s3", 1); RS.record("s3", "base_readQuery", {"sql": "SELECT city, miles FROM t"}, {"status": "success", "results": ROWS})
    asyncio.run(comp.handler.process({"op": "sum"}, {"session_id": "s3"}))
    me = types.SimpleNamespace(session_id="s3", original_user_input="total miles?", current_turn_number=5,
                               provenance=_P.ProvenanceChain(session_id="s3", turn_number=5, user_uuid="u", profile_type="tool_enabled"),
                               _resolve_gate_profile=lambda g: {"honestyConfig": {"mode": mode}})
    text, html, ev = _PE._run_compute_lineage(me, "The total is 1,550.5; I estimate 1,700 next year.", "<p>The total is 1,550.5; I estimate 1,700 next year.</p>")
    return me, text, html, ev
me, text, html, ev = _seam("observe")
check("observe: receipts sealed as compute_step, marks drawn, NO disclosure", ev["sealed"] == 1 and [st["step_type"] for st in me.provenance.steps] == ["compute_step"] and "figure-receipted" in html and "figure-unreceipted" in html and not ev["disclosure_appended"] and "Note:" not in text and 'data-kind="figures"' not in html)
me, text, html, ev = _seam("annotate")
check("annotate: the disclosure is APPENDED to text and html (never a rewrite)", ev["disclosure_appended"] and text.startswith("The total is 1,550.5") and "Note: the figure(s) 1,700" in text and 'data-kind="figures"' in html)
me, text, html, ev = _seam("enforce")
check("enforce: same append — the answer is never withheld for arithmetic", ev["disclosure_appended"] and text.startswith("The total is 1,550.5"))
APP_CONFIG.COMPUTE_LINEAGE = False
_t, _h, _e = _PE._run_compute_lineage(me, "x 1,700", "<p>x 1,700</p>")
check("switch off ⇒ text/html untouched, no event", (_t, _h, _e) == ("x 1,700", "<p>x 1,700</p>", None))
APP_CONFIG.COMPUTE_LINEAGE = True
me2 = types.SimpleNamespace(session_id="s-empty", original_user_input="", current_turn_number=1, provenance=None, _resolve_gate_profile=lambda g: {})
RS.begin_turn("s-empty", 1)
check("a turn with no result sets ⇒ untouched, no event", _PE._run_compute_lineage(me2, "hi 1,700", "<p>hi</p>") == ("hi 1,700", "<p>hi</p>", None))

print("[9] the seams are wired (AST) and the switch is the only door")
ad = src("src/trusted_data_agent/mcp_adapter/adapter.py"); te = src("src/trusted_data_agent/agent/tool_enforcement.py")
# Every lane records through the ONE gate's after-steps (test_sql_gate_lanes drives all three
# lanes into the registry): the gate records, and the adapter goes through the gate.
_after = te.split("def sql_gate_after")[1].split("\ndef ")[0]
# the PROPERTY (the gate records, the adapter goes through the gate) — not the exact call text:
# PROP-006 added a `source=` argument to both calls and the property held throughout.
check("both post-success seams record into the registry",
      "_result_sets.record(session_id, tool_name, arguments or {}, parsed" in te
      and "record_catalog(tool_name, arguments, result, session_id" in _after
      and "sql_gate_after(" in ad and "sql_gate_after(" in te.split("def _make_guarded_coroutine")[1])
es = src("src/trusted_data_agent/agent/execution_service.py"); check("the turn start resets the registry", "_result_sets.begin_turn(session_id)" in es)
sm = src("src/trusted_data_agent/core/session_manager.py"); check("the persistence chokepoint stamps `compute` via setdefault", 'turn_data.setdefault("compute", _compute_stamp)' in sm)
ex = src("src/trusted_data_agent/agent/executor.py")
tree = ast.parse(ex); fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_compute_lineage"); body = ast.unparse(fn)
check("the seam: switch-gated, assesses, seals receipts as compute_step, marks, discloses only under annotate/enforce, stamps",
      "_rs.enabled()" in body and "_lin.assess(" in body and "add_step('compute_step'" in body and "_lin.mark_html(" in body and "('annotate', 'enforce')" in body and "self._compute_lineage_event = event" in body)
check("Optimize + Conversation call the seam after the honesty gate and persist the stamp",
      "self._run_compute_lineage(" in ex and "executor._run_compute_lineage(response_text, final_html)" in src("src/trusted_data_agent/agent/engines/conversation_engine.py")
      and '"compute_lineage": getattr(executor, "_compute_lineage_event", None)' in src("src/trusted_data_agent/agent/engines/optimize_engine.py")
      and '"compute_lineage": _compute_lineage_event' in src("src/trusted_data_agent/agent/engines/conversation_engine.py"))
check("both engines yield the Number Lineage step", ex.count('"type": "compute_lineage"') >= 1 and src("src/trusted_data_agent/agent/engines/conversation_engine.py").count('"type": "compute_lineage"') >= 1)
ui = src("static/js/ui.js"); eh = src("static/js/eventHandlers.js")
check("the Live Status card renders live (both dispatchers) and on reload", ui.count("_renderComputeLineageDetails(details)") >= 3 and "turnData.compute_lineage" in eh)
check("the marks have theme-token CSS, the dialog script is loaded, the dial maps the station", "mark.figure-receipted" in src("static/css/trust.css") and "figureLineage.js" in src("templates/index.html") and "compute_lineage: 'grounded'" in src("static/js/handlers/assuranceDial.js"))
check("the companion skill is registered with its component link", any(e.get("skill_id") == "compute-expert" and e.get("component_id") == "compute" for e in json.loads(src("skills/skill_registry.json"))["skills"]))
check("packs treat compute as an action component (default OFF in focused packs)", '"compute"' in src("agent_packs/common/component_config.py"))
check("COMPUTE_LINEAGE defaults off (env TDA_COMPUTE_LINEAGE)", 'COMPUTE_LINEAGE = os.environ.get("TDA_COMPUTE_LINEAGE", "0")' in src("src/trusted_data_agent/core/config.py"))

print("[10] the receipt card reaches the reader — html wins over spec at the one seam (the blank-box defect)")
from trusted_data_agent.components.utils import generate_component_html as _gch
_render_html = comp.handler.__class__.__module__ and __import__('sys').modules[comp.handler.__class__.__module__]._render_html
RS.begin_turn("s10", 1); RS.record("s10", "base_readQuery", {"sql": "SELECT city, miles FROM t"}, {"status": "success", "results": ROWS})
_pl = asyncio.run(comp.handler.process({"op": "avg", "column": "miles"}, {"session_id": "s10"}))
check("the handler ships BOTH: the receipt as spec and the row BODY as html — never a frame of its own", bool(_pl.spec) and bool(_pl.html) and "<details" not in _pl.html and "card" not in _pl.html)
check("the payload title is the row's summary line: the derivation AND the figure (collapsed loses nothing)", re.match(r"AVG\(miles\) · 3 of 4 rows · statement 1 → 516\.8", _pl.title) is not None, _pl.title)
_h = _pl.html
check("the body keeps the receipt line + the way to the rows (hash on the button)", "Computed by code, not by the model:" in _h and 'class="ans-act compute-open-btn" data-act="rows" data-receipt-hash="' + _pl.spec["receipt"]["receipt_hash"] + '"' in _h and "receipt " + _pl.spec["receipt"]["receipt_hash"][:8] in _h)
check("a row-op's table IS the evidence — no rows action, the table in the body", 'data-act="rows"' not in _render_html("rank", None, "RANK(miles) · 4 rows · statement 1", [{"city": "A", "miles": 1200, "rank": 1}], {"receipt_hash": "abc"}) and 'class="ans-t"' in _render_html("rank", None, "RANK(miles) · 4 rows · statement 1", [{"city": "A", "miles": 1200, "rank": 1}], {"receipt_hash": "abc"}))
check("rows the aggregate skipped are a visible caveat — 3 of 4 rows used — and absent when every row counted",
      "3 of 4 rows used" in _h and "rows used" not in _render_html("count", 4, "COUNT(*) · 4 rows", None, {"row_count": 4, "row_count_used": 4, "receipt_hash": "abc"}))
# the ONE seam draws the row from the manifest: kind word, accent, provenance chip, disclosure
_row = _gch([{"component_id": "compute", "spec": _pl.spec, "html": _pl.html, "title": _pl.title, "metadata": _pl.metadata}])
check("the seam wraps the body into ONE evidence row named by the manifest (Compute · #14b8a6 · closed) with the tool as provenance",
      _row.strip().startswith('<details class="ans-ev" data-kind="compute" style="--ans-accent:#14b8a6"') and ' open' not in _row.split("<summary")[0]
      and '<span class="ans-kind">Compute</span>' in _row and '<span class="ans-prov">Tool output · TDA_Compute</span>' in _row and f'<span class="ans-ev-t">{_pl.title}</span>' in _row and _pl.html in _row, _row[:300])
check("no class contains 'card' (the global [class*=\"card\"] rule), no per-span style attributes, no .response-card frame", "card" not in _row and 'span style="' not in _row)
check("no frontend renderer exists for compute — the data-spec route would render an EMPTY card",
      not (ROOT / "components/builtin/compute/renderer.js").exists() and "'compute'" not in src("static/js/componentRenderers.js"))
_card = _gch([{"component_id": "compute", "spec": _pl.spec, "html": _pl.html, "metadata": _pl.metadata}])
check("html + spec ⇒ the body is wrapped into its row (no .response-card frame), never an empty data-spec box", _card.strip().startswith('<details class="ans-ev"') and "response-card" not in _card and "data-spec=" not in _card and 'data-component-id="compute"' not in _card, _card[:200])
check("spec only (a chart) still takes the renderer route", "data-spec=" in _gch([{"component_id": "chart", "spec": {"a": 1}}]))
check("a suppressed refusal still renders nothing", _gch([{"component_id": "compute", "spec": {"error": "x"}, "html": "", "metadata": {"suppress_inline": True}}]) == "")
# the Optimize lane: the adapter carries the html on the trace result and the final-answer seam hands it over
check("the adapter carries payload.html on the component result", 'result["html"] = payload.html' in ad)
check("the Optimize final-answer seam forwards html + metadata into generate_component_html", '"html": result.get("html") or ""' in ex and '"metadata": result.get("metadata") or {}' in ex)
# the model never reads the markup: both LLM-facing distillers drop it, the persisted trace keeps it
_me = types.SimpleNamespace(_distillation_events=None, _context_distiller=None)
_me._distill_data_for_llm_context = lambda d, _events=None: _PE._distill_data_for_llm_context(_me, d, _events=_events)
_d = _PE._distill_data_for_llm_context(_me, {"type": "compute", "spec": {"op": "avg"}, "html": "<div>card</div>", "metadata": {}})
check("the tactical fallback distiller drops a component result's html (spec stays)", "html" not in _d and _d["spec"] == {"op": "avg"}, _d)
check("…but never an unrelated `html` key (only a type+spec component result)", "html" in _PE._distill_data_for_llm_context(_me, {"html": "<p>doc</p>", "title": "t"}))
from components.builtin.context_window.distiller import ExecutionContextDistiller as _ECD
_u = _ECD().distill({"type": "compute", "spec": {"op": "avg"}, "html": "<div>card</div>"})
check("the unified context-window distiller (the primary path) drops it too", "html" not in _u and _u["spec"] == {"op": "avg"}, _u)
from trusted_data_agent.mcp_adapter import adapter as _AD
_r = _AD._distill_value_for_report({"type": "compute", "spec": {"op": "avg"}, "html": "<div>card</div>"})
check("the report distiller drops it too", "html" not in _r and _r["spec"] == {"op": "avg"}, _r)

print("[11] a refusal reaches the ReAct receipt as a refusal — never as 'done' (3 tool calls, 2 rows explained)")
import json as _json, ast as _ast
from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor as _CAE
_h = ComputeHandler() if "ComputeHandler" in globals() else None
try:
    from components.builtin.compute.handler import ComputeHandler as _CH
    _h = _CH()
except Exception:  # noqa: BLE001
    pass
_ref = _h._err("compute_refused", "more than one numeric column — name one: CustomerID, TotalRevenue")
_wire = _json.dumps(_ref.to_dict() if hasattr(_ref, "to_dict") else _ref.__dict__, default=str)   # the tool's JSON content on the ReAct lane
class _TM:  # a ToolMessage-like output
    def __init__(self, c): self.content = c
check("the real refusal payload reads as a refusal with the component's own reason", _CAE._tool_output_refusal(_TM(_wire)) == "more than one numeric column — name one: CustomerID, TotalRevenue")
check("a successful compute payload and a plain data result read as no refusal", _CAE._tool_output_refusal(_TM('{"status":"success","component_id":"compute","metadata":{"tool_name":"TDA_Compute"},"spec":{"receipt":{}}}')) is None and _CAE._tool_output_refusal('{"status":"success","results":[{"a":1}]}') is None)
_src = pathlib.Path(_CAE.__module__.replace(".", "/") + ".py")
_csrc = open("src/" + str(_src)).read()
check("the ReAct completed event carries `refused` and success is False on a refusal (the receipt's row and the honesty gate read the same fact)",
      "tool_success = not self._tool_output_is_error(tool_output) and tool_refusal is None" in _csrc and '_completed["refused"] = tool_refusal' in _csrc)

# a mark lands in TEXT only — never inside an attribute value, even one that itself carries markup
# (prod/local session 42eb166c turn 2: the dashboard's data-spec JSON held "<table>…" and the
# "a `>` before the next `<`" guard read the tile title "…Trend 2023" as text → broken JSON)
import html as _H, json as _J
_spec = _J.dumps({"view": "visualization", "tile_title": "Monthly Robbery Trend 2023",
                  "content": "<table><tr><td>1</td><td>2023</td></tr></table>"})
_dash = (f"<p>Robberies in 2023 total 179,153.</p><div data-component-id=\"dashboard\" data-spec='{_spec}'></div>"
         f"<span data-tip=\"filtered to 2023\">see</span>")
_evd = {"decision": "pass", "literals": [{"figure": "2023", "statement": 1, "lineage": "used as a filter in statement 1", "kind": "filter"}],
        "quoted": [{"figure": "179,153", "statement": 1, "row": 0, "lineage": "row 1 of statement 1"}]}
_od = L.mark_html(_dash, _evd, 2)
_i = _od.find("data-spec='"); _sp = _od[_i + 11:_od.find("'", _i + 11)]
check("a component's data-spec stays byte-identical (valid JSON) — no mark inside an attribute value", _sp == _spec and _J.loads(_H.unescape(_sp))["tile_title"].endswith("2023"), _sp[:120])
check("…a plain attribute value is never marked either", 'data-tip="filtered to 2023"' in _od)
check("…while the same figures in the visible text ARE marked", _od.count("<mark") == 2 and "<p>Robberies in <mark" in _od and ">179,153</mark>" in _od, _od[:300])
check("tag masking round-trips: stripping the marks restores the input exactly",
      __import__("re").sub(r'<mark class="figure-[^"]*"[^>]*>([^<]*)</mark>', r"\1", _od) == _dash)


print("[12] a figure derived from a result column (its sum or average) is traced; a near miss is named, never rewritten")
_months = ['14573', '12512', '13615', '13854', '15490', '15246', '16569', '16068', '15598', '15982', '14643', '15043']
_mst = [{"n": 1, "statement": "SELECT month_number, incident_month, COUNT(*) AS robbery_count FROM v_Offence WHERE offence_code = '120' AND SUBSTR(d, 8, 4) = '2023' GROUP BY 1, 2",
         "rows": [{"month_number": str(i + 1), "incident_month": m, "robbery_count": v}
                  for i, (m, v) in enumerate(zip("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), _months))],
         "row_count": 12}]
_Q = "Show me robberies per month — is robbery a rising or falling crime?"
_ok = L.assess("Total robberies in 2023: 179,193. July peaked at 16,569.", [], _mst, query=_Q)
check("the correct column sum is DERIVED, not unreceipted (prod turn, Sep 2026)",
      [g["figure"] for g in _ok["derived"]] == ["179,193"] and _ok["unreceipted"] == [] and _ok["decision"] == "pass")
check("its lineage names the column, the aggregate and the rows",
      _ok["derived"][0]["lineage"] == "the sum of robbery_count over the 12 rows of statement 1")
check("a cell is still QUOTED (earlier classes never change)", [q["figure"] for q in _ok["quoted"]] == ["16,569"])
check("a derived turn needs no disclosure", L.disclosure(_ok) == "")
_avg = L.assess("On average 14,933 robberies per month.", [], _mst, query=_Q)
check("the column average, rounded as written, is DERIVED", _avg["derived"] and _avg["derived"][0]["kind"] == "average" and _avg["unreceipted"] == [])
_bad = L.assess("Total robberies in 2023: 180,193.", [], _mst, query=_Q)
check("a near miss STAYS unreceipted — every consumer keeps treating it as untraced",
      _bad["unreceipted"] == ["180,193"] and _bad["decision"] == "note" and _bad["derived"] == [])
check("…and is named as a near miss of the sum", _bad["misderived"] and _bad["misderived"][0]["kind"] == "sum"
      and _bad["misderived"][0]["value"] == 179193)
_bd = L.disclosure(_bad)
check("the disclosure names the value the rows give (179,193)", "180,193 is close to, but is not, the sum of robbery_count" in _bd and "179,193" in _bd)
_far = L.assess("Total robberies in 2023: 179,193; I estimate 250,000 next year.", [], _mst, query=_Q)
check("a figure far from every aggregate is plain unreceipted — no near-miss claim",
      _far["unreceipted"] == ["250,000"] and _far["misderived"] == [])
check("without a near miss the disclosure is BYTE-IDENTICAL to before",
      L.disclosure(_far) == "Note: the figure(s) 250,000 match no result row and no compute receipt in this turn — "
      "they are the agent's own arithmetic, not a computed or quoted value. Ask for the computation to have them verified.")
_mix = L.disclosure(L.assess("Total 180,193; I estimate 250,000 next year.", [], _mst, query=_Q))
check("a mix keeps the old sentence for the rest and adds the precise one", _mix.startswith("Note: the figure(s) 250,000 match") and "180,193 is close to" in _mix)
check("the reason names derived figures only when there are some",
      "derived from a result column" in _ok["reason"] and "derived" not in L.assess("July: 16,569.", [], _mst)["reason"])
# incomplete sets are never summed — a confident wrong total is worse than none
_tr = dict(_mst[0], truncated=True)
check("a TRUNCATED statement is never summed", L.assess("Total 179,193.", [], [_tr])["derived"] == [])
_rc = dict(_mst[0], row_count=5000)
check("a row_count larger than the rows held is never summed", L.assess("Total 179,193.", [], [_rc])["derived"] == [])
check("one row is not an aggregate (it is the row itself)",
      L.assess("Total 14,573.", [], [dict(_mst[0], rows=_mst[0]["rows"][:1], row_count=1)])["derived"] == [])
_mixed = [{"n": 1, "statement": "s", "rows": [{"c": "12"}, {"c": "n/a"}, {"c": "30"}], "row_count": 3}]
check("a column with a non-numeric value is not summed", L.assess("Total 142 things.", [], _mixed)["derived"] == []
      and "142" in L.assess("Total 142 things.", [], _mixed)["unreceipted"])
check("an equal cell wins over an equal sum (quoted first)",
      [q["figure"] for q in L.assess("Value 500.", [], [{"n": 1, "statement": "s", "rows": [{"c": "0"}, {"c": "500"}], "row_count": 2}])["quoted"]] == ["500"])
check("a figure the question asked is never judged, even if it is a sum",
      L.assess("Total 179,193.", [], _mst, query="is the total 179,193?")["derived"] == [])
check("the na event carries the two new lists", L.assess("x", [], [])["derived"] == [] and L.assess("x", [], [])["misderived"] == [])
# ordering: the aggregate check is reached ONLY by a figure that would have been unreceipted
import inspect as _inspect12
_src = _inspect12.getsource(L.assess)
check("the aggregate check sits after the short-figure guard (a strict narrowing of unreceipted)",
      _src.index("MIN_DIGITS_TO_CONVICT") < _src.index("column_aggregates(") < _src.index("unreceipted.append(t)"))
# the marks
_h = L.mark_html("<p>Total robberies in 2023: 179,193.</p>", _ok, turn_id=2)
check("a derived figure is marked as traced and opens its statement",
      'class="figure-quoted' in _h and 'data-derived="sum"' in _h and 'data-column="robbery_count"' in _h and 'data-statement="1"' in _h)
_hb = L.mark_html("<p>Total robberies in 2023: 180,193.</p>", _bad, turn_id=2)
check("a near miss keeps the unreceipted mark and its tip names the value", 'figure-unreceipted' in _hb and "179,193" in _hb)
# the consumers
_ex = open(os.path.join(ROOT, "src/trusted_data_agent/agent/executor.py")).read()
check("the executor persists the rows behind a derived figure", '(event.get("derived") or [])' in _ex)
_rel = open(os.path.join(ROOT, "src/trusted_data_agent/eval/scorers/reliability.py")).read()
check("numeric fidelity counts a derived figure as grounded", '(lineage.get("derived") or [])' in _rel)
from trusted_data_agent.agent import assurance_points as _ap
_rp = _ap._read_compute_lineage({"compute_lineage": _ok}, None)
check("GROUNDED reads a derived figure as traced", _rp.state == "pass" and "derived from a result column" in _rp.detail)
_rw = _ap._read_compute_lineage({"compute_lineage": dict(_bad, disclosure_appended=True)}, None)
check("GROUNDED names the near miss", _rw.state == "warn" and "180,193 is close to, but not, the sum of robbery_count" in _rw.detail)
_rq = _ap._read_compute_lineage({"compute_lineage": L.assess("July: 16,569.", [], _mst)}, None)
check("GROUNDED's pass reading is unchanged when nothing is derived", "derived" not in _rq.detail)

print("[14] the turn's inputs vouch for a figure; the note advises only what the agent can do (prod 502f5e62)")
_pst = [{"n": 1, "tool": "base_readQuery", "statement": "SELECT COUNT(*) AS total_robberies FROM Policing_Core.v_Offence WHERE offence_code = '120'",
         "columns": ["total_robberies"], "row_count": 1, "rows": [{"total_robberies": 179193}]}]
_pans = "There are 179,193 robberies (offence_code '120'). This does not link to any incident in the 2023 NIBRS data."
_p0 = L.assess(_pans, [], _pst, query="How many robberies are there?")
check("without the session's earlier words the primer's year is unreceipted (the defect)", _p0["unreceipted"] == ["2023"])
_p1 = L.assess(_pans, [], _pst, query="How many robberies are there?", vouched_numbers={"2018", "2023"})
check("a number the turn's inputs vouch for is never unreceipted", _p1["unreceipted"] == [] and _p1["decision"] == "pass" and L.disclosure(_p1) == "")
check("vouching changes no earlier class", _p1["quoted"] == _p0["quoted"] and _p1["literals"] == _p0["literals"])
_p2 = L.assess(_pans + " I estimate 250,000 next year.", [], _pst, query="How many robberies are there?", vouched_numbers={"2023"})
check("a figure nothing vouches for is still convicted beside a vouched one", _p2["unreceipted"] == ["250,000"])
from trusted_data_agent.agent.grounding.adapter import session_context_text as _sct
_wf = [{"user_query": "Two facts: Nov 2018 vs the 2023 submission."}, {"user_query": "How many?"}, "junk"]
check("one definition of the session's earlier words", _sct(_wf) == "Two facts: Nov 2018 vs the 2023 submission.\nHow many?" and _sct(None) == "")
check("the grounding gate reads the same definition", "adapter.session_context_text(_wf)" in src("src/trusted_data_agent/agent/grounding/runtime.py"))
_stub = types.SimpleNamespace(original_user_input="How many robberies are there?", kg_enrichment_event=None,
                              previous_turn_data={"workflow_history": _wf})
check("the executor builds the vouched set from the session it holds", {"2018", "2023"} <= _PE._turn_vouched_numbers(_stub))
check("no session held ⇒ only the question's numbers", _PE._turn_vouched_numbers(types.SimpleNamespace(original_user_input="top 5?")) == {"5"})
RS.begin_turn("s14", 2); RS.record("s14", "base_readQuery", {"sql": _pst[0]["statement"]}, {"status": "success", "results": [{"total_robberies": 179193}]})
def _me14(has_compute, wf=_wf):
    return types.SimpleNamespace(session_id="s14", original_user_input="How many robberies are there?", current_turn_number=2, provenance=None,
                                 kg_enrichment_event=None, previous_turn_data={"workflow_history": wf}, user_uuid="u",
                                 _resolve_gate_profile=lambda g: {"honestyConfig": {"mode": "annotate"}},
                                 _get_profile_config=lambda: {"componentConfig": {"compute": {"enabled": has_compute}}})
_t14, _h14, _e14 = _PE._run_compute_lineage(_me14(False), _pans, "<p>" + _pans + "</p>")
check("the prod turn through the seam: no note, nothing appended", _e14["decision"] == "pass" and _t14 == _pans and not _e14["disclosure_appended"])
_bad14 = _pans + " I estimate 250,000 next year."
_t14, _h14, _e14 = _PE._run_compute_lineage(_me14(False, []), _bad14, "<p>" + _bad14 + "</p>")
check("Compute off for the agent ⇒ the note never advises asking for a computation",
      _e14["compute_available"] is False and "250,000" in _e14["note"] and "own arithmetic" in _e14["note"] and "Ask for the computation" not in _t14 and "Ask for the computation" not in _h14)
_t14, _h14, _e14 = _PE._run_compute_lineage(_me14(True, []), _bad14, "<p>" + _bad14 + "</p>")
check("Compute on for the agent ⇒ the advice stays", _e14["compute_available"] is True and "Ask for the computation to have them verified." in _t14)
check("an event without the fact keeps the advice (older events)", "Ask for the computation" in L.disclosure({"unreceipted": ["9,999"]}))
_cv = types.SimpleNamespace(canvas_context={"title": "t.py", "language": "python", "content": "x=1"})
_cv._component_enabled_for_turn = lambda cid: False
check("an open canvas on an agent without Canvas is context only, no tool advice", "TDA_Canvas" not in _PE._format_canvas_context(_cv) and "x=1" in _PE._format_canvas_context(_cv))
_cv._component_enabled_for_turn = lambda cid: True
check("with Canvas enabled the update advice stays", "use TDA_Canvas with the same title" in _PE._format_canvas_context(_cv))

print(f"\n=== S3 compute component: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
