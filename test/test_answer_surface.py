"""The Answer Surface renderer (docs/roadmap/ANSWER_SURFACE_GRAMMAR.md) — the ONE way an
answer body draws a lead figure, a note and an evidence row. Pure, CI-only.

What is pinned: the three shapes and their fixed anatomy · every kind has a word, an accent
and a disclosure default · a chart opens and everything else is one gesture away · the
summary is escaped, the body is the producer's verbatim · no class name contains "card" (a
global [class*="card"] rule paints anything so named) · the stylesheet uses theme tokens
only and declares the token block once · the specimen builds through the real renderer.
"""
import os, pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trusted_data_agent.agent import answer_surface as A

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

print("[1] the vocabulary")
check("every evidence kind has a word, an accent and a disclosure default", all(set(v) >= {"word", "accent", "open"} and re.match(r"^#[0-9a-fA-F]{6}$", v["accent"]) for v in A.EVIDENCE_KINDS.values()))
check("a chart opens by default; every other kind is one gesture away", A.EVIDENCE_KINDS["chart"]["open"] is True and all(not v["open"] for k, v in A.EVIDENCE_KINDS.items() if k != "chart"))
check("the kinds the grammar names exist", {"data", "chart", "compute", "synthesis", "sources", "component", "attachment", "step", "experts"} <= set(A.EVIDENCE_KINDS))
check("every note kind carries a tone from the attention vocabulary (finding=warn · withheld=danger · disclosure=muted)",
      all(v["tone"] in A.TONES for v in A.NOTE_KINDS.values()) and A.NOTE_KINDS["withheld"]["tone"] == "danger" and A.NOTE_KINDS["honesty"]["tone"] == "warn" and A.NOTE_KINDS["budget"]["tone"] == "muted")

print("[2] the lead figure — never framed")
lf = A.lead_figure("5,518.60", "Average revenue")
check("figure then label, one group, no frame class", 'class="ans-lead"' in lf and '<div class="ans-lead-v">5,518.60</div>' in lf and '<div class="ans-lead-l">Average revenue</div>' in lf and "ans-ev" not in lf)
check("a caller-owned marked figure passes through verbatim; a plain value is escaped", '<mark>x</mark>' in A.lead_figure("", "l", value_html="<mark>x</mark>") and "&lt;b&gt;" in A.lead_figure("<b>", "l"))

print("[3] a note — kind word first, a tone, an optional route")
n = A.note("honesty", "an <empty> table produces the same result", route="record")
check("role=note, the kind word, the escaped sentence, the tone class", 'role="note"' in n and '<span class="ans-kind">Honesty</span>' in n and "an &lt;empty&gt; table" in n and "ans-tone-warn" in n)
check("the route is a real button carrying data-ans-route", '<button type="button" class="ans-note-route" data-ans-route="record">' in n)
check("no route ⇒ no button; an explicit tone wins; an unknown kind degrades to a titled muted note", "ans-note-route" not in A.note("budget", "x") and "ans-tone-danger" in A.note("honesty", "x", tone="danger") and "ans-tone-muted" in A.note("weird_kind", "x") and "Weird Kind" in A.note("weird_kind", "x"))

print("[4] an evidence row — the summary in words, the provenance on the right, the body verbatim")
body = '<table class="x"><tr><td>a & b</td></tr></table>'
r = A.evidence_row("compute", "AVG(miles) over 5 rows → 516.8", body, provenance="Tool output · TDA_Compute")
check("a <details> with the kind, the accent as a custom property, closed by default", r.startswith('<details class="ans-ev" data-kind="compute" style="--ans-accent:#14b8a6">') and " open" not in r.split(">")[0])
check("summary: chevron, kind icon, kind word, the summary text, the provenance chip — in that order",
      re.search(r'<summary class="ans-ev-s"><svg[^>]*>.*?</svg><svg[^>]*>.*?</svg><span class="ans-kind">Compute</span><span class="ans-ev-t">AVG\(miles\) over 5 rows → 516\.8</span><span class="ans-prov">Tool output · TDA_Compute</span></summary>', r) is not None, r[:400])
check("the body is the producer's html, byte for byte", f'<div class="ans-ev-b">{body}</div>' in r)
check("summary text is escaped; provenance is escaped", "&lt;s&gt;" in A.evidence_row("data", "<s>", "", provenance="<p>") and "&lt;p&gt;" in A.evidence_row("data", "<s>", "", provenance="<p>"))
check("open=True / open=False override the kind default both ways", ' open>' in A.evidence_row("data", "s", "", open=True) and " open" not in A.evidence_row("chart", "s", "", open=False).split("<summary")[0])
check("an unknown kind degrades to a titled, muted, closed row (never a crash)", 'data-kind="mystery"' in A.evidence_row("mystery", "s", "") and "Mystery" in A.evidence_row("mystery", "s", ""))
check("the rail wraps the rows and counts them; an empty rail is nothing", A.evidence_rail(["<a/>", "", "<b/>"]).startswith('<div class="ans-rail" data-rows="2">') and A.evidence_rail([]) == "")

print("[5] no class name may contain 'card' (the global [class*=\"card\"] rule)")
allhtml = lf + n + r + A.evidence_rail([r])
check("renderer output carries no 'card' in any class", not re.search(r'class="[^"]*card', allhtml))
css = (ROOT / "static/css/answer.css").read_text()
check("the stylesheet declares no 'card' class of its own (it may DISSOLVE a producer's .response-card inside a row)", not re.search(r"\.ans-[a-z-]*card", css))

print("[6] the stylesheet — tokens once, theme variables only")
check("the token block is declared once and names the scale", css.count(":root {") == 1 and all(t in css for t in ("--ans-body", "--ans-lead", "--ans-unit", "--ans-row-h", "--ans-radius", "--ans-hair")))
lit = re.findall(r"#[0-9a-fA-F]{3,6}\b", re.sub(r"var\([^)]*\)", "", css))
check("no hex colour outside a var() fallback (colour is a token or a kind accent set by the renderer)", not lit, lit[:5])
check("no shadow, no gradient, no blur in the answer body", "box-shadow: 0" not in css and "linear-gradient" not in css and "backdrop-filter: blur" not in css)
check("light theme overrides exist for the row background and the two attention tones", '[data-theme="light"] .ans-ev' in css and '[data-theme="light"] .ans-tone-warn' in css and '[data-theme="light"] .ans-tone-danger' in css)
check("reduced motion is honoured", "prefers-reduced-motion" in css)

check("the accent brackets the row — left AND right edges carry the kind's identity", "border-left: 3px solid var(--ans-accent" in css and "border-right: 3px solid var(--ans-accent" in css)
print("[6b] a component row — identity from the manifest, disclosure from the manifest, the seam wraps every component the same way")
cr = A.component_row("database_navigator", "fitness_db.Sales · Fact · 5 columns", "<div>body</div>", manifest={"display_name": "Database Navigator", "icon_accent": "#84cc16", "answer_surface": {"open": False}, "tool_definition": {"name": "TDA_DatabaseNavigator"}}, tool_name="TDA_DatabaseNavigator")
check("the row wears the component's word + accent, the tool as provenance, the manifest's disclosure, the body verbatim",
      'data-kind="component"' in cr and "--ans-accent:#84cc16" in cr and '<span class="ans-kind">Database Navigator</span>' in cr and '<span class="ans-prov">Tool output · TDA_DatabaseNavigator</span>' in cr and ' open' not in cr.split("<summary")[0] and '<div class="ans-ev-b"><div>body</div></div>' in cr and 'data-component="database_navigator"' in cr)
check("answer_surface.kind / .word / .open are honoured", 'data-kind="compute"' in A.component_row("compute", "s", "b", manifest={"display_name": "Compute with Lineage", "icon_accent": "#14b8a6", "answer_surface": {"open": True, "kind": "compute", "word": "Compute"}}) and '<span class="ans-kind">Compute</span>' in A.component_row("compute", "s", "b", manifest={"display_name": "Compute with Lineage", "answer_surface": {"kind": "compute", "word": "Compute"}}) and ' open ' in A.component_row("chart", "s", "b", manifest={"answer_surface": {"open": True}}).split("<summary")[0])
check("no manifest ⇒ still a titled, closed, generic component row (never a crash)", '<span class="ans-kind">Mystery Thing</span>' in A.component_row("mystery_thing", "s", "b") and 'data-kind="component"' in A.component_row("mystery_thing", "s", "b"))
from trusted_data_agent.components.utils import generate_component_html as _gch, _component_row_html as _crh
check("the seam: a body is wrapped into its row; a body that already IS a row passes through (never double-framed); a spec-only payload keeps the renderer route",
      _gch([{"component_id": "compute", "spec": {"x": 1}, "html": "<p>b</p>", "title": "S"}]).strip().startswith('<details class="ans-ev"')
      and _crh("compute", {}, '<details class="ans-ev" data-kind="compute">x</details>').count("<details") == 1
      and "data-spec=" in _gch([{"component_id": "chart", "spec": {"x": 1}}]) and 'class="ans-ev"' in _gch([{"component_id": "chart", "spec": {"x": 1}}]))
check("the html route never emits a .response-card frame (the row is the frame)", "response-card" not in _gch([{"component_id": "knowledge_graph", "spec": {}, "html": "<p>x</p>"}]) and "response-card" not in _gch([{"component_id": "compute", "spec": {"a": 1}, "html": "<p>x</p>", "title": "t"}]))

print("[6c] the Optimize formatter speaks the grammar: lead figure, plain observations, the drawer as an evidence rail")
from trusted_data_agent.agent.formatter import OutputFormatter as _OF
from trusted_data_agent.agent.response_models import KeyMetric as _KM, Observation as _Ob
_f = _OF.__new__(_OF); _f.processed_data_indices = set()
km = _f._render_key_metric(_KM(value="5,518.60", label="Average revenue"))
check("the key metric is the lead figure — unframed, never the centered panel", 'class="ans-lead"' in km and "key-metric-card" not in km and "text-center" not in km)
ob = _f._render_observations([_Ob(text="one"), _Ob(text="two")])
check("observations are a plain list on the bubble — no ruled 'Key Observations' heading", '<ul class="ans-obs">' in ob and "<h3" not in ob and "Key Observations" not in ob and ob.count("<li>") == 2)
tbl = _f._render_table({"results": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], "metadata": {}}, 0, "base_readQuery")
step = "<div class='response-card mb-4' data-ev='step' data-ev-summary='Skipped · dba_x'><p class='text-sm text-gray-400 italic'>Skipped Step: <strong>dba_x</strong>. Reason: no</p></div>"
chart = '<div class="response-card mb-4" data-ev=\'chart\' data-ev-summary=\'Chart\'><div id="chart-render-target-1" class="chart-render-target" data-spec=\'{}\'></div></div>'
rail = _f._evidence_rail_from(tbl + step + chart)
check("every drawer block becomes ONE row of its declared kind, in order, the body byte-identical inside",
      re.findall(r'data-kind="(\w+)"', rail) == ["data", "step", "chart"] and tbl.strip() in rail and step in rail and chart in rail)
check("the summary is in words with the key fact, the provenance is the tool, a chart row opens and a data row does not",
      re.findall(r'<span class="ans-ev-t">(.*?)</span>', rail) == ["a, b · 2 rows", "Skipped · dba_x", "Chart"]
      and '<span class="ans-prov">base_readQuery</span>' in rail and '<span class="ans-prov">TDA_Charting</span>' in rail
      and rail.split('data-kind="chart"')[1].split("<summary")[0].count(" open") == 1 and rail.split('data-kind="data"')[1].split("<summary")[0].count(" open") == 0)
check("an empty drawer renders nothing; a block with no kind is a data row named for its title, never dropped",
      _f._evidence_rail_from("") == "" and 'data-kind="data"' in _f._evidence_rail_from("<div class='response-card mb-4'><h4>Custom</h4><p>x</p></div>") and "Custom" in _f._evidence_rail_from("<div class='response-card mb-4'><h4>Custom</h4><p>x</p></div>"))
fsrc = (ROOT / "src/trusted_data_agent/agent/formatter.py").read_text()
check("no drawer literal survives — every collateral/synthesis site routes through the one converter", "Collateral Report for:" not in fsrc and "Synthesis Report for:" not in fsrc and "Intermediate Synthesis for:" not in fsrc and "Collateral Data for:" not in fsrc and fsrc.count("self._evidence_rail_from(") >= 5)
check("every drawer producer declares its kind (tables, charts, steps, synthesis)", fsrc.count("data-ev='data'") + fsrc.count('data-ev="data"') >= 2 and fsrc.count("data-ev='chart'") >= 4 and fsrc.count("data-ev='step'") >= 7 and fsrc.count("data-ev='synthesis'") >= 3)

print("[6d] the notes: every body note is drawn by the ONE note renderer")
from trusted_data_agent.agent.grounding import adapter as _GA
from trusted_data_agent.agent.honesty import adapter as _HA
from trusted_data_agent.agent import compute_lineage as _CL
from trusted_data_agent.core import knowledge_provenance as _KP
gn = _GA.annotation_html({"annotation_md": "Reached `finance` outside the scope"})
check("grounding annotation → note(scope, warn), no blockquote, no inline style", 'data-kind="scope"' in gn and "ans-tone-warn" in gn and "<blockquote" not in gn and "style=" not in gn and "finance" in gn and "`" not in gn)
hn = _HA.apply_to_html("<p>a</p>", {"flagged": True}, "x")
check("honesty flag → note(honesty), the answer kept in front", hn.startswith("<p>a</p>") and 'data-kind="honesty"' in hn and "honesty-note" not in hn)
hn2 = _HA.apply_to_html("<p>a</p>", {"disclosure_appended": True}, "answer\n\nNote: the grain moved")
check("honesty disclosure → note(honesty) carrying only the disclosure", hn2.startswith("<p>a</p>") and 'data-kind="honesty"' in hn2 and "the grain moved" in hn2 and "answer\n" not in hn2)
sn = _KP.sensitivity_note_html([{"sensitivity": "confidential", "collection_name": "HR Policies"}])
check("confidential source → note(confidential, warn)", 'data-kind="confidential"' in sn and "ans-tone-warn" in sn and "HR Policies" in sn and "sensitivity-note" not in sn)
check("no confidential source → no note", _KP.sensitivity_note_html([{"sensitivity": "internal"}]) is None)
for mod, cls in ((_GA, "grounding-note"), (_HA, "honesty-note"), (_CL, "compute-lineage-note")):
    check(f"{mod.__name__.split('.')[-1]}: the old class is gone from the source", cls not in pathlib.Path(mod.__file__).read_text())
check("the budget finish line routes through note(budget)", '_ans.note("budget"' in (ROOT / "src/trusted_data_agent/agent/executor.py").read_text() and "budget-finish" not in (ROOT / "src/trusted_data_agent/agent/executor.py").read_text())
check("no dead note CSS survives in the platform stylesheets", all(k not in (ROOT / "static/css/main.css").read_text() for k in (".budget-finish", "rag-sources-section", "rag-source-card")) and ".rag-score-high" not in (ROOT / "static/css/themes.css").read_text())

print("[6e] the Focus sources: ONE `sources` row, each document a source item")
_f.rag_focused_sources = None
docs = [{"content": "alpha **beta**", "similarity_score": 0.91, "collection_name": "Uderia Docs", "metadata": {"title": "README"}, "source_trust": "signed", "verified_days_ago": 3},
        {"content": "gamma", "similarity_score": 0.62, "collection_name": "HR Policies", "metadata": {"filename": "leave.md"}, "sensitivity": "confidential", "sync_off": True}]
sr = _f._render_rag_sources(docs)
check("one sources row wearing the count and the repositories as provenance", sr.count('data-kind="sources"') == 1 and '<span class="ans-ev-t">2 documents</span>' in sr and '<span class="ans-prov">Uderia Docs, HR Policies</span>' in sr and "response-card" not in sr and "onclick" not in sr and "<style>" not in sr)
check("each document is a source item: name · relevance · repository · governance chips, content behind it",
      sr.count('<details class="ans-src">') == 2 and '<span class="ans-src-n">README</span>' in sr and "0.91" in sr and '<span class="ans-src-coll">Uderia Docs</span>' in sr
      and all(c in sr for c in ("signed", "verified 3d ago", "sync off", "confidential")) and "<strong>beta</strong>" in sr)
_chrome = re.sub(r'<div class="ans-src-b">.*?</div></details>', "", sr, flags=re.S)   # the row's own markup, bodies (the shared prose renderer) aside
check("the row chrome carries no Tailwind grey, no emoji glyph, no hardcoded colour", "text-gray" not in _chrome and "bg-gray" not in _chrome and "▶" not in sr and "#374151" not in sr and "<style>" not in sr)
check("a document with neither title nor filename is named honestly, never dropped", "Unknown Source" in _f._render_rag_sources([{"content": "x", "collection_name": "C", "metadata": {}}]))
check("the Focus summary is plain prose — no framed 'Summary' box", '<h3 class="text-lg font-bold text-white mb-3">Summary</h3>' not in fsrc and 'rag-synthesis-section">' in fsrc)
check("the source-item styles ship on the stylesheet", all(k in css for k in (".ans-src {", ".ans-src-s", ".ans-src-n", ".ans-src-b")))

print("[6g] THE data table — one industrial grid for every table, the footer carries the statement and the acts")
_t = _f._render_table({"results": [{"Name": "A", "Revenue": "6,999.00", "Share": "12%"}, {"Name": "B", "Revenue": "5999.99", "Share": "9.5%"}], "metadata": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}}, 0, "base_readQuery")
check("no heading restates the row (the summary already names the tool); the old grid is gone", "Data: Result for" not in _t and "assistant-table" not in _t and "table-container" not in _t and "Copy Table" not in _t)
check("the grid: ans-tw wrap + table.ans-t, numerics marked ans-num (mono, tabular figures), text cells not", '<div class="ans-tw"><table class="ans-t">' in _t and _t.count('<td class="ans-num">') == 4 and '<td>A</td>' in _t)
check("the footer: statement left, acts right — Copy wired to the existing copy handler, Pin hidden until the client reveals it",
      '<div class="ans-tf"><div class="ans-tf-l"><details class="ans-stmt">' in _t and 'class="ans-act copy-button" data-copy-type="table"' in _t and "data-table='" in _t and 'class="ans-act ans-pin-table" data-pin-table hidden' in _t)
_cd = _f._render_chart_with_details({"type": "chart", "spec": {}}, {"results": [{"a": 1}], "metadata": {"tool_name": "x"}}, 0, 1)
check("the chart's data table is the same grid, no 'Chart Data' heading", "Chart Data" not in _cd and '<table class="ans-t">' in _cd and "assistant-table" not in _cd)
_md = _f._render_standard_markdown("| Col | Val |\n|---|---|\n| a | 1,000 |\n| b | 2 |\n")
check("a markdown table in the prose is the same grid (prose variant) with the same footer acts — copy + a hidden pin, no statement", '<div class="ans-tw ans-tw-prose"><table class="ans-t">' in _md and '<td class="ans-num">1,000</td>' in _md and 'class="ans-tf ans-tf-prose"' in _md and "data-copy-type=\"table\"" in _md and "data-pin-table hidden" in _md and "ans-stmt" not in _md and "assistant-table" not in _md)
_ddl = _f._render_ddl({"results": [{"Request Text": "CREATE TABLE t (x INT)"}], "metadata": {"tool_name": "base_tableDDL"}}, 0)
check("a DDL row: the statement is the body, no inner 'SQL DDL:' header, the copy act in the same footer, provenance declared",
      "SQL DDL:" not in _ddl and "sql-header" not in _ddl and 'class="ans-stmt-b"' in _ddl and 'class="ans-act copy-button" data-copy-type="code"' in _ddl and "data-ev-prov='base_tableDDL'" in _ddl)
check("no producer of the old grid survives in the formatter", "assistant-table" not in fsrc)
check("the grid + footer styles ship on the stylesheet", all(k in css for k in ("table.ans-t th {", "table.ans-t td.ans-num", ".ans-tf {", ".ans-tw {")))
check("one type system per row: every column left-aligned, a numeric cell in the body typeface (tabular figures, never mono)", "table.ans-t th.ans-num, table.ans-t td.ans-num { text-align: left; }" in css and "font-variant-numeric: tabular-nums" in css.split("table.ans-t, #chat-log")[1].split("\n")[0])
_usrc = (ROOT / "static/js/ui.js").read_text()
check("the client never re-wraps a row's table in the viz card; it reveals the row's own Pin act under the same gate",
      "plainTables = allTables.filter(t => !t.closest('.ans-tw'))" in _usrc and "_wirePinActs(messageContent)" in _usrc
      and "querySelectorAll('.ans-pin-table, .ans-pin')" in _usrc and "component_id: 'table'" in _usrc
      and "ComponentGate.isEnabled('dashboard', _deriveTableOrigin(btn).profileId)" in _usrc)

print("[6g2] Pin to dashboard — one act, one place: the footer of every pinnable row")
_pm = {"display_name": "Chart", "icon_accent": "#f43f5e", "answer_surface": {"open": True, "pin": True}}
_pr = A.component_row("chart", "Top products · bar", "<div data-component-id=\"chart\"></div>", manifest=_pm, tool_name="TDA_Charting")
check("a pinnable component's row carries the pin act in the row footer — the same footer and button class as a table's",
      '<div class="ans-tf"><div class="ans-tf-l"></div><div class="ans-tf-r"><button type="button" class="ans-act ans-pin" data-pin-component="chart" hidden>Pin to dashboard</button>' in _pr)
check("…drawn hidden (the client reveals it under the Dashboard gate)", 'data-pin-component="chart" hidden' in _pr)
check("…inside the row body, after the component's own output", _pr.index('data-component-id="chart"') < _pr.index("ans-pin"))
check("a component that does not declare pin gets no act", "ans-pin" not in A.component_row("memory", "s", "b", manifest={"answer_surface": {"open": True}}))
check("a payload may withhold it for one action (answer_surface.pin false)", "ans-pin" not in A.component_row("chart", "s", "b", manifest=_pm, override={"pin": False}))
import json as _json
_pinnable = sorted(c for c in os.listdir(ROOT / "components/builtin")
                   if (ROOT / f"components/builtin/{c}/manifest.json").exists()
                   and (_json.loads((ROOT / f"components/builtin/{c}/manifest.json").read_text()).get("answer_surface") or {}).get("pin") is True)
check("the pinnable components are exactly action, chart, knowledge graph, flow and EXPLAIN plan", _pinnable == ["action", "chart", "explain_plan", "flow", "knowledge_graph"], _pinnable)
_m = re.search(r"const _PINNABLE_COMPONENTS = \[([^\]]*)\]", _usrc)
check("the client's list for rows persisted before the flag equals the manifests", _m and sorted(x.strip(" '\"") for x in _m.group(1).split(",")) == _pinnable, _m and _m.group(1))
check("one delegated click handles both acts (table and component)", "closest('.ans-pin-table, .ans-pin')" in _usrc and _usrc.count("addTileFromComponent(payload, meta)") == 2)
_dh = (ROOT / "static/js/handlers/dashboardHandler.js").read_text()
check("no floating hover pin is injected over cards any more", "dash-pin-btn" not in _dh and "dash-pin-btn" not in (ROOT / "components/builtin/dashboard/renderer.js").read_text())
check("one label: 'Pin to dashboard' (the old 'Pin to Dashboard' casing is gone from the answer)", "Pin to Dashboard" not in _usrc and "Pin to Dashboard" not in A.pin_footer("chart"))

print("[6f] the ReAct lane carries its data results as Data rows — the same row as Optimize, persisted in the body")
from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor as _CAE
_ag = _CAE.__new__(_CAE); _ag.data_results = []; _ag._open_data_args = {}
_ag._safe_serialize = lambda x: x
_ag._open_data_args["base_readQuery"] = [{"sql": "SELECT a FROM t LIMIT 5"}]
_ag._capture_data_result("base_readQuery", {"status": "success", "results": [{"a": 1}, {"a": 2}]}, [{"kind": "teradata_top", "executed_sql": "SELECT TOP 5 a FROM t"}])
check("a dict result with rows is captured in the formatter's collected_data shape, with its arguments + the executed statement",
      len(_ag.data_results) == 1 and _ag.data_results[0]["results"] == [{"a": 1}, {"a": 2}] and _ag.data_results[0]["metadata"]["tool_name"] == "base_readQuery"
      and _ag.data_results[0]["metadata"]["arguments"] == {"sql": "SELECT a FROM t LIMIT 5"} and _ag.data_results[0]["metadata"]["executed_sql"] == "SELECT TOP 5 a FROM t" and _ag.data_results[0]["metadata"]["row_count"] == 2)
_ag._capture_data_result("base_readQuery", '{"status":"success","results":[{"b":1}]}', [])
_ag._capture_data_result("base_readQuery", [{"type": "text", "text": '{"status":"success","results":[{"c":1}]}'}], [])
check("a JSON string and MCP content blocks are captured through the ONE coercion", [d["results"] for d in _ag.data_results[1:]] == [[{"b": 1}], [{"c": 1}]])
_n = len(_ag.data_results)
_ag._capture_data_result("TDA_Compute", {"status": "success", "results": [{"x": 1}]}, [])
_ag._capture_data_result("base_readQuery", {"status": "error", "error_message": "3807"}, [])
_ag._capture_data_result("base_readQuery", {"status": "success", "results": []}, [])
_ag._capture_data_result("base_readQuery", "not json", [])
check("a component tool, an error, an empty result and an unreadable output capture nothing", len(_ag.data_results) == _n)
_ag2 = _CAE.__new__(_CAE); _ag2.data_results = []; _ag2._open_data_args = {}; _ag2._safe_serialize = lambda x: x
_ag2._capture_data_result("base_readQuery", {"status": "success", "results": [{"a": i} for i in range(500)]}, [])
check("the capture is bounded and says so: 200 rows kept, the true count kept beside them", len(_ag2.data_results[0]["results"]) == 200 and _ag2.data_results[0]["metadata"]["row_count"] == 500 and _ag2.data_results[0]["metadata"]["truncated"])
_tbl2 = _f._render_table(_ag2.data_results[0], 0, "base_readQuery")
check("the Data row's summary states 'first 200 of 500 rows' — never a smaller number wearing 'rows'", "data-ev-summary='a · first 200 of 500 rows'" in _tbl2)
_tbl3 = _f._render_table(_ag.data_results[0], 0, "base_readQuery")
check("the row body carries the EXECUTED statement behind a Statement disclosure, naming the rewrite", '<details class="ans-stmt">' in _tbl3 and "SELECT TOP 5 a FROM t" in _tbl3 and "rewritten (teradata_top)" in _tbl3 and "LIMIT 5" not in _tbl3)
check("no statement recorded ⇒ no Statement disclosure", "ans-stmt" not in _f._render_table({"results": [{"a": 1}], "metadata": {"tool_name": "t"}}, 0, "t"))
_csrc = (ROOT / "src/trusted_data_agent/agent/conversation_agent.py").read_text()
_esrc = (ROOT / "src/trusted_data_agent/agent/engines/conversation_engine.py").read_text()
check("the capture runs at the tool-end seam (after the rewrites drain) and rides the result dict", "self._capture_data_result(tool_name, tool_output, _rewrites)" in _csrc and '"data_results": self.data_results' in _csrc and _csrc.index("_rewrites = self._rewrite_events_for(tool_name)") < _csrc.index("self._capture_data_result(tool_name, tool_output, _rewrites)"))
check("the engine hands the rows to the formatter as collected_data (persisted inside the body ⇒ plan-reload identical)", '"collected_data": list(result.get("data_results") or [])' in _esrc and '"collected_data": []' not in _esrc)
check("the statement styles ship on the stylesheet", ".ans-stmt" in css and ".ans-stmt-b" in css)

print("[6h] the spec route — every component's inline output enters the grammar through the ONE seam; a silent act never reads as an empty row")
_spec_row = _gch([{"component_id": "database_navigator", "spec": {"action": "describe", "summary": "s"}, "title": "fitness_db.Sales · fact · 5 columns", "metadata": {"tool_name": "TDA_DatabaseNavigator"}}])
check("a spec payload becomes a row: the container is the body, the manifest gives word + accent + disclosure, the title is the summary, the tool the provenance",
      _spec_row.count('<details class="ans-ev"') == 1 and 'data-component="database_navigator"' in _spec_row and "--ans-accent:#84cc16" in _spec_row
      and '<span class="ans-kind">Database Navigator</span>' in _spec_row and "fitness_db.Sales · fact · 5 columns" in _spec_row and "Tool output · TDA_DatabaseNavigator" in _spec_row
      and re.search(r'<div class="ans-ev-b"><div id="component-[0-9a-f]{12}" data-component-id="database_navigator" data-spec=\'', _spec_row) and " open" not in _spec_row.split("<summary")[0])
check("no route emits a .response-card — the row is the frame", all("response-card" not in _gch([{"component_id": c, "spec": {"view": "card"}, "title": "t"}]) for c in ("chart", "dashboard", "flow", "canvas", "scheduler", "memory", "knowledge_graph", "database_navigator")))
_chart_row = _gch([{"component_id": "chart", "spec": {"type": "bar"}, "title": "Revenue by product · bar · 5 points", "metadata": {"tool_name": "TDA_Charting"}}])
check("the manifest's disclosure: a chart opens, a navigator does not", " open" in _chart_row.split("<summary")[0] and 'data-kind="chart"' in _chart_row)
_ov = _gch([{"component_id": "knowledge_graph", "spec": {"nodes": []}, "title": "g", "metadata": {"tool_name": "TDA_KnowledgeGraph", "answer_surface": {"open": True, "word": "Graph"}}}])
check("the payload's own answer_surface refines the manifest per ACTION (a graph the user asked to see opens; the word can be the action's)",
      " open" in _ov.split("<summary")[0] and '<span class="ans-kind">Graph</span>' in _ov and " open" not in _gch([{"component_id": "knowledge_graph", "spec": {"nodes": []}, "title": "g"}]).split("<summary")[0])
for _sil in ({"component_id": "flow", "spec": {"view": "silent_patch"}}, {"component_id": "canvas", "spec": {"view": "silent_list"}}, {"component_id": "dashboard", "spec": {"view": "panel_open"}, "metadata": {"answer_surface": {"row": False}}}):
    _o = _gch([_sil])
    check(f"a silent payload ({_sil['component_id']}) keeps its container in the framework's own .ans-silent wrapper and gets NO row",
          'class="ans-silent"' in _o and "data-spec=" in _o and "ans-ev" not in _o and "response-card" not in _o)
check("the seam's silence rule is the renderers' own (view: silent_*) — one convention, both sides",
      all("startsWith('silent')" in (ROOT / f"components/builtin/{c}/renderer.js").read_text() for c in ("canvas", "knowledge_graph")) and "silent_patch" in (ROOT / "components/builtin/flow/renderer.js").read_text()
      and 'startswith("silent")' in (ROOT / "src/trusted_data_agent/components/utils.py").read_text())
check("every renderer that hides its container's PARENT is scoped to the framework's wrappers, never the message",
      all("parent.classList.contains('ans-silent')" in (ROOT / f"components/builtin/{c}/renderer.js").read_text() for c in ("canvas", "knowledge_graph", "flow"))
      and "if (parent && parent !== document.body)\n" not in (ROOT / "components/builtin/flow/renderer.js").read_text())
check(".ans-silent is never painted", ".ans-silent { display: none; }" in css)
_ui = (ROOT / "static/js/ui.js").read_text()
check("the dispatcher defers a collapsed row's render to its first open (a renderer never sizes at zero width) and hides the row a renderer hid",
      "if (row && !row.open) { container.dataset.ansDeferred = '1'; return; }" in _ui and "document.addEventListener('toggle'" in _ui and "row.querySelectorAll('[data-component-id][data-ans-deferred]')" in _ui
      and "if (row && container.style.display === 'none') row.hidden = true;" in _ui and "}, true);" in _ui.split("document.__ansDeferredRender = true;")[1][:1200])
# the producers state their key fact in words (a collapsed row is never a label without content)
_src = lambda c: (ROOT / f"components/builtin/{c}/handler.py").read_text()
check("chart: the type and the point count are on the line", "title=f\"{title} · {chart_type} · {len(data)} point" in _src("chart"))
check("knowledge graph: the lookup states count + query, the graph its counts and OPENS", "title=f\"{len(results)} entit" in _src("knowledge_graph") and '"answer_surface": {"open": True}' in _src("knowledge_graph") and "· {len(links)} relationship" in _src("knowledge_graph"))
check("navigator: per-action summaries, the error names itself, never the bare component name", _src("database_navigator").count('spec["summary"] = ') >= 5 and 'title = spec.get("summary") or' in _src("database_navigator") and 'title="Database Navigator"' not in _src("database_navigator"))
check("scheduler + memory: the message in words IS the summary", 'title=message or "Task Scheduler"' in _src("scheduler") and "scheduled task{'' if len(tasks) == 1 else 's'}" in _src("scheduler") and 'title=message or "Agent Memory"' in _src("memory"))
check("dashboard: names + counts on the line; the create act (panel opens, nothing to show) declares row: false", 'title=f"{len(dashboards)} dashboard' in _src("dashboard") and '"answer_surface": {"row": False}' in _src("dashboard") and "· {len(tiles)} tile" in _src("dashboard") and 'title=f"Dashboard: ' not in _src("dashboard"))
check("flow: ONE words helper — name · node count · the act", "def _flow_words(" in _src("flow") and _src("flow").count("_flow_words(") >= 5 and 'title=f"Flow: ' not in _src("flow"))
check("canvas: name · lines · language", "· {line_count} line{'' if line_count == 1 else 's'} · {language}" in _src("canvas"))
# (that every manifest declares answer_surface is test_component_manifests' fact — one gate per fact)

print("[7] the specimen builds through the real renderer, both themes")
sys.path.insert(0, str(ROOT / "test"))
import build_answer_specimen as S
for theme in ("dark", "light"):
    page = S.specimen(theme)
    check(f"{theme}: every evidence kind and every note kind is on the sheet",
          all(f'data-kind="{k}"' in page for k in A.EVIDENCE_KINDS) and all(f'role="note" data-kind="{k}"' in page for k in A.NOTE_KINDS))
    check(f"{theme}: the app's real theme attribute + the real stylesheets", ('data-theme="modern"' if theme == "dark" else 'data-theme="light"') in page and "css/answer.css" in page and "css/themes.css" in page)

print("[8] a component row wears the component's OWN icon (the one on its panel and in Workspace)")
import json as _json, glob as _glob
from pathlib import Path
for _f in sorted(_glob.glob(str(ROOT / "components/builtin/*/manifest.json"))):
    _m = _json.loads(Path(_f).read_text())
    if not _m.get("icon_svg"):
        continue
    _row = A.component_row(_m["component_id"], "s", "<p>b</p>", manifest=_m, tool_name="T")
    _paths = A.manifest_icon(_m["icon_svg"]).split(">", 1)[1]
    check(f"{_m['component_id']}: the row's icon is the manifest's", _paths in _row and A._ICONS["component"] not in _row)
check("no manifest icon → the generic component icon", A._ICONS["component"] in A.component_row("x", "s", "b", manifest={}))
for _bad in ('<svg><script>alert(1)</script></svg>', '<svg onload="x"><path d="M0 0"/></svg>',
             '<svg><a href="javascript:x"><path/></a></svg>', '<svg><style>*{}</style></svg>', '<svg><image href="x"/></svg>',
             '<div><path/></div>', '<svg><path d="M0" style="fill:url(#x)"/></svg>'):
    check(f"a manifest icon that is not a plain drawing is refused: {_bad[:34]}", A.manifest_icon(_bad) is None)
check("the row icon keeps the row's size", 'class="ans-ic" width="14" height="14"' in A.manifest_icon('<svg class="h-4 w-4" viewBox="0 0 24 24"><path d="M1 1"/></svg>'))

print(f"\n=== Answer Surface renderer: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
