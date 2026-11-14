"""Gate: the model's markdown renders — never leaks as stray syntax — and the platform's
annotations stay visibly the platform's.

Two problem classes, found on a Focus turn whose answer ended in a literal ``***`` and
``*Source: … (Collection: …)*``, with a Scope note below nine collapsed sources calling the
answer's own citation "unsupported":

  A. RENDERING — every markdown form a model writes either renders or stays as plain,
     readable text (rules, emphasis, strike, links, autolinks, images, ordered/task lists,
     quotations, escapes, closing hashes, setext headings). Raw HTML is never executed.
     Identifiers (snake_case, __init__, a*b*c, *args) are never mangled into emphasis.
     The server renderer (formatter.md_inline + block rules) and the client mirror for the
     line still streaming (ui.js _provInline/_provTailHtml) render IDENTICALLY.
  B. ANNOTATIONS — a platform note sits directly under the prose, above the evidence rail,
     whichever producer adds it; every producer routes through ONE placement; a guardrail
     annotation wears the shared note shape; a model-written quotation is never styled like
     a note; and a citation label the model was SHOWN (document title, repository) is
     support, never an "unsupported claim".

CI-pure: no server, no network (node is used to execute the shipped JS).
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.agent.formatter import OutputFormatter, md_inline  # noqa: E402
from trusted_data_agent.agent import answer_surface as ans  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" — {str(detail)[:400]}" if detail else ""))


F = OutputFormatter.__new__(OutputFormatter)


def R(md):
    return F._render_standard_markdown(md)


P = '<p class="text-gray-300 mb-4">'
HR = '<hr class="border-gray-600 my-4">'


# ── A1. block forms ─────────────────────────────────────────────────────────────────
def t_block_forms():
    for rule in ("***", "---", "___", "* * *", "- - -", "_ _ _", "*****", "  ***"):
        out = R(f"a\n\n{rule}\n\nb")
        check(f"thematic break {rule!r} renders as a rule", out == f"{P}a</p>{HR}{P}b</p>", out)
    check("'* * *' is a rule, never a list item", "<ul" not in R("* * *"))
    out = R("1. one\n2. two")
    check("ordered list renders as <ol>", out.startswith("<ol ") and "<li>one</li><li>two</li></ol>" in out, out)
    check("an ordered list starting at 3 keeps its number", 'start="3"' in R("3. three\n4. four"))
    check("'1)' is an ordered item too", "<ol " in R("1) one"))
    out = R("- a\n1. b")
    check("a list changing type at the same level closes the first", out.count("</ul>") == 1 and out.count("</ol>") == 1
          and out.index("</ul>") < out.index("<ol"), out)
    check("'+' is a bullet", "<li>item</li>" in R("+ item"))
    out = R("- [ ] todo\n- [x] done")
    check("task items render as disabled checkboxes", out.count('type="checkbox"') == 2 and "disabled checked" in out
          and "[ ]" not in out and "[x]" not in out, out)
    out = R("> quoted line\n> more **b**")
    check("consecutive '>' lines are ONE quotation", out.count("<blockquote") == 1, out)
    check("a quotation's body is markdown", "<strong>b</strong>" in out and "&gt;" not in out, out)
    check("a quotation is the model's prose, never a note", 'class="ans-md-quote"' in out and "ans-note" not in out, out)
    check("closing hashes are stripped from a heading", R("## Title ##").endswith(">Title</h3>"), R("## Title ##"))
    check("a setext '===' underline makes a heading", R("Title\n=====").startswith("<h3") and "===" not in R("Title\n====="))
    check("a bare '===' is a rule, not stray signs", R("===") == HR, R("==="))
    out = R("- item\n\n```sql\nSELECT 1\n```\n\n1. after")
    check("lists around a code block still close", out.count("<ul") == out.count("</ul>") and out.count("<ol") == out.count("</ol>"), out)
    out = R("- a\n  1. nested\n- b")
    check("a nested ordered list inside a bullet list closes cleanly",
          out.count("<ul") == out.count("</ul>") == 1 and out.count("<ol") == out.count("</ol>") == 1, out)


# ── A2. inline forms ────────────────────────────────────────────────────────────────
def t_inline_forms():
    cases = {
        "*italic*": "<em>italic</em>",
        "_italic_": "<em>italic</em>",
        "**bold**": "<strong>bold</strong>",
        "***both***": "<strong><em>both</em></strong>",
        "___both___": "<strong><em>both</em></strong>",
        "__Bold words__": "<strong>Bold words</strong>",
        "~~gone~~": "<del>gone</del>",
        "**bold *it* bold**": "<strong>bold <em>it</em> bold</strong>",
        r"a \* star \_x\_": "a * star _x_",
    }
    for src, want in cases.items():
        got = md_inline(src)
        check(f"inline {src!r}", got == want, got)
    got = md_inline("See [the docs](https://x.y/z?a=1&b=2) now")
    check("a web link renders as a safe anchor", '<a class="ans-md-link" href="https://x.y/z?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">the docs</a>' in got, got)
    got = md_inline("[**bold** link](https://x.y)")
    check("a link label is inline markdown", "<strong>bold</strong> link</a>" in got, got)
    got = md_inline("Visit <https://x.y>")
    check("an autolink renders (it used to vanish as an unknown tag)", ">https://x.y</a>" in got, got)
    got = md_inline("mail <mailto:a@b.c>")
    check("a mailto autolink renders", 'href="mailto:a@b.c"' in got, got)
    got = md_inline("![chart](https://i.example/p.png)")
    check("an image is linked by its alt text, never fetched", "<img" not in got and ">chart</a>" in got, got)
    got = md_inline("The *Source: Uderia Platform (Collection: Uderia Documentation)* line")
    check("a model's italic source line renders italic, asterisks gone", "<em>Source:" in got and "*" not in got, got)


# ── A3. safety ──────────────────────────────────────────────────────────────────────
def t_safety():
    got = md_inline("x <img src=x onerror=alert(1)> <script>alert(1)</script>")
    check("raw HTML with attributes is shown as text, never executed", "<img" not in got and "<script" not in got
          and "&lt;img" in got and "&lt;script&gt;" in got, got)
    got = md_inline("a<br>b <b>x</b> <B>y</B>")
    check("attribute-free formatting tags are kept", got == "a<br>b <b>x</b> <b>y</b>", got)
    got = md_inline('<b onclick="x">no</b>')
    check("a formatting tag WITH attributes is escaped", "<b onclick" not in got and "&lt;b onclick" in got, got)
    for href in ("javascript:alert(1)", "data:text/html,x", "/api/v1/admin", "vbscript:x"):
        got = md_inline(f"[click]({href})")
        check(f"a {href.split(':')[0]} link never becomes an anchor", "<a " not in got, got)
    got = md_inline('[x](https://a.b/"onmouseover="y)')
    check("a quote in a URL cannot break out of the attribute", 'onmouseover="y' not in got.replace("&quot;", ""), got)
    got = md_inline("AT&T &amp; &nbsp; &#169; &#x1F600;")
    check("a bare '&' is escaped, an existing entity is kept", got == "AT&amp;T &amp; &nbsp; &#169; &#x1F600;", got)
    got = md_inline("run `SELECT * FROM t WHERE a<b` now")
    check("code-span content is escaped and never read as markup", "a&lt;b" in got and "<em>" not in got, got)
    check("a NUL in the text cannot forge a placeholder", "\x00" not in md_inline("x\x000\x00 *y*"))


# ── A4. identifiers are never mangled ───────────────────────────────────────────────
def t_identifiers():
    for txt in ("base_readQuery", "my_file_name.md", "__init__", "self.__dict__", "a*b*c",
                "2 * 3 * 4", "*args, **kwargs", "snake_case_name", "x_1 and y_2",
                "file*.csv", "C:\\path\\to_dir", "price $5 * qty"):
        got = md_inline(txt)
        check(f"identifier {txt!r} left as written", "<em>" not in got and "<strong>" not in got, got)
    check("an unclosed ** stays literal (as the stream shows it mid-line)", md_inline("Partial **bo") == "Partial **bo")


# ── A5. the client mirror renders identically (executes the shipped ui.js) ──────────
def _js_mirror(lines):
    src = open(os.path.join(ROOT, "static/js/ui.js"), encoding="utf-8").read()
    a = src.index("const _PROV_P = ")
    b = src.index("function _provComposeHtml() {")
    prog = src[a:b] + "\nconst cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
        "process.stdout.write(JSON.stringify(cases.map(c => [_provInline(c), _provTailHtml(c)])));\n"
    out = subprocess.run(["node", "-e", prog], input=json.dumps(lines), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:800])
    return json.loads(out.stdout)


PARITY_LINES = [
    "plain text", "*italic* and _italic_", "**bold** and __Bold words__", "***both*** ___both___",
    "~~gone~~", "**bold *it* bold**", r"a \* star \_x\_ \\ back", "See [the docs](https://x.y/z?a=1) now",
    "[**b** l](https://x.y)", "Visit <https://x.y> and <mailto:a@b.c>", "![alt](https://i/p.png)",
    "[x](javascript:alert(1))", "x <img src=x onerror=y> <b>ok</b> a<br>b", "AT&T &amp; &nbsp;",
    "run `SELECT * FROM t` and ``a`b``", "base_readQuery __init__ a*b*c 2 * 3 *args, **kwargs",
    "*Source: Uderia Platform - Your Trusted Data Agent (Collection: Uderia Documentation)*",
    "Über *schön* und _naïve_ café", "Partial **bo", "# H1 title #", "## H2 *x*", "#### H4",
    "***", "* * *", "---", "___", "===", "- bullet *it*", "+ plus", "* star bullet",
    "1. one", "7) seven", "- [ ] todo", "- [x] **done**", "> quoted *line*", ">", "> - quoted bullet",
]
PARITY_BLOCKS = [
    "- a\n- b\n- c", "1. one\n2. two\n3. three", "> one\n> two", "- [ ] a\n- [x] b",
    "para one\n\n***\n\n*Source: X (Collection: Y)*", "## Head\ntext *it*\n\n1. a\n2. b",
]


def t_client_parity():
    py_inline = [md_inline(c) for c in PARITY_LINES]
    py_block = [R(c) for c in PARITY_LINES + PARITY_BLOCKS]
    try:
        js = _js_mirror(PARITY_LINES + PARITY_BLOCKS)
    except Exception as e:  # noqa: BLE001
        check("the shipped client mirror executes under node", False, e)
        return
    for i, c in enumerate(PARITY_LINES):
        check(f"inline parity {c!r}", js[i][0] == py_inline[i], f"py={py_inline[i]!r}  js={js[i][0]!r}")
    for i, c in enumerate(PARITY_LINES + PARITY_BLOCKS):
        check(f"block parity {c!r}", js[i][1] == py_block[i], f"py={py_block[i]!r}  js={js[i][1]!r}")


# ── B1. note placement ──────────────────────────────────────────────────────────────
RAIL = ans.evidence_rail([ans.evidence_row("sources", "9 documents", "<p>d</p>")])
PROSE = f"<div>{P}answer</p></div>"


def t_note_placement():
    n1 = ans.note("scope", "first")
    n2 = ans.note("honesty", "second")
    out = ans.place_note(PROSE + RAIL, n1)
    check("a note sits directly under the prose, above the evidence rail", out == PROSE + n1 + RAIL, out)
    out = ans.place_note(ans.place_note(PROSE + RAIL, n1), n2)
    check("several notes keep the order they were added in, all above the rail", out == PROSE + n1 + n2 + RAIL, out)
    lone = ans.evidence_row("chart", "c", "<p>x</p>")
    check("a lone evidence row (no rail) also stays below the note", ans.place_note(PROSE + lone, n1) == PROSE + n1 + lone)
    check("no evidence ⇒ the note follows the prose", ans.place_note(PROSE, n1) == PROSE + n1)
    check("an empty note changes nothing", ans.place_note(PROSE + RAIL, "") == PROSE + RAIL)
    check("None prose is safe", ans.place_note(None, n1) == n1)

    from trusted_data_agent.agent.grounding import adapter as g
    ev = {"decision": "annotate", "annotation_md": "> Some statements could not be matched: `X`.",
          "unsupported_claims": []}
    out = g.apply_to_html(PROSE + RAIL, ev)
    check("the grounding gate's note lands above the rail", out.index("ans-note") < out.index("ans-rail"), out)
    from trusted_data_agent.agent.honesty import adapter as h
    for name, ev, txt in (("failed-retrieval note", {"acted": True}, "x"),
                          ("appended disclosure", {"acted": True, "disclosure_appended": True}, "a\n\nNote: grain differs.")):
        out = h.apply_to_html(PROSE + RAIL, ev, txt)
        check(f"the honesty {name} is a note above the rail", 'data-kind="honesty"' in out
              and out.index("ans-note") < out.index("ans-rail"), out)
    from trusted_data_agent.agent.guardrails import adapter as gr
    out = gr.apply_event_to_html(PROSE + RAIL, "", {"action": "annotate", "annotation_md": "> Personal data: `email`."})
    check("a guardrail annotation wears the shared note shape", 'class="ans-note' in out and 'data-kind="guardrail"' in out
          and "guardrail-note" not in out, out)
    check("…and lands above the rail", out.index("ans-note") < out.index("ans-rail"), out)
    check("…without the markdown quote marker or code ticks", "&gt;" not in out.split("ans-note-t")[1][:40] and "`" not in out, out)
    check("'guardrail' is a declared note kind", "guardrail" in ans.NOTE_KINDS)
    out = g.annotation_html({"annotation_md": "Some statements could not be matched (unsupported ≠ false): `Does it mix tools?`, `X`."})
    check("a scope note never reads 'X?.' or 'X..' once the code ticks are gone", "?." not in out and ".." not in out
          and out.rstrip("</span></div>").endswith("X."), out)
    out = g.annotation_html({"annotation_md": "Some statements could not be matched: `Does it mix tools?`."})
    check("…a claim ending in '?' keeps its own mark only", "tools?<" in out and "?." not in out, out)


def t_every_annotation_survives():
    """Every trust annotation still reaches the answer — as a NOTE (kind word + icon + tone
    bar) above the evidence, or as an in-place MARK — and none is drawn as model prose."""
    from trusted_data_agent.agent.grounding import adapter as g
    from trusted_data_agent.agent.guardrails import adapter as gr, policy as gp
    from trusted_data_agent.agent.guardrails.types import Direction
    from trusted_data_agent.agent import compute_lineage as L
    from trusted_data_agent.core import knowledge_provenance as kp
    md = ('It asks "Does the plan mix prompts and tools?" and the "champion case *learning* system adapts". '
          "The agent's plan is \"the agent's own re-planning loop\". The total is 1,550.5 and I estimate 1,700.")
    html = R(md) + RAIL
    claims = ["Does the plan mix prompts and tools?", "champion case *learning* system adapts",
              "the agent's own re-planning loop"]
    out = g.apply_to_html(html, {"decision": "annotate", "annotation_md": "Some statements: `X`.", "unsupported_claims": claims})
    marks = re.findall(r'<mark class="claim-unsupported[^>]*>(.*?)</mark>', out)
    check("a plain claim is highlighted", "Does the plan mix prompts and tools?" in marks, marks)
    check("a claim written with markdown is highlighted as the reader sees it",
          "champion case <em>learning</em> system adapts" in marks, marks)
    check("a claim with an apostrophe is highlighted (it never matched when the marker escaped quotes)",
          "the agent's own re-planning loop" in marks, marks)
    check("…and the Scope note sits above the rail", out.index('data-kind="scope"') < out.index("ans-rail"), out)
    split = g.apply_to_html(f"{P}a <em>b c</em> d</p>", {"decision": "annotate", "annotation_md": "x", "unsupported_claims": ["c d"]})
    check("a claim that would split an element is left unmarked (never mis-nested HTML)", "<mark" not in split, split)
    attr = g.apply_to_html('<div data-spec=\'{"t": "Secret Roadmap Document"}\'></div>',
                           {"decision": "annotate", "annotation_md": "x", "unsupported_claims": ["Secret Roadmap Document"]})
    check("a claim is never marked inside an attribute", "<mark" not in attr, attr)
    c = gr.resolve_config({"guardrailConfig": {"outbound": {"mode": "annotate"}}}, Direction.OUTBOUND, True)
    v = gp.evaluate_outbound("Mail jane@acme.com now.", c)
    out = gr.apply_event_to_html(R("Mail jane@acme.com now.") + RAIL, v.text or "", v.to_event())
    check("the guardrail note renders its bold (no '**')", 'data-kind="guardrail"' in out and "**" not in out
          and "<strong>" in out.split("ans-note-t", 1)[1][:200], out)
    lev = {"decision": "note", "receipted": [{"figure": "1,550.5", "lineage": "SUM(x)", "receipt": 0}], "unreceipted": ["1,700"]}
    out = ans.place_note(L.mark_html(html, lev, turn_id=1), L.note_html(lev))
    check("figure marks survive the new renderer", 'class="figure-receipted' in out and 'class="figure-unreceipted' in out, out)
    check("the Figures note sits above the rail", out.index('data-kind="figures"') < out.index("ans-rail"), out)
    out = ans.place_note(html, kp.sensitivity_note_html([{"collection_name": "HR", "sensitivity": "confidential"}]))
    check("the Confidential note sits above the rail", out.index('data-kind="confidential"') < out.index("ans-rail"), out)


def t_conversation_lane_notes():
    """The Conversation lane used to render the GATED text, so a gate's sentence was drawn
    as the model's prose ("> Some statements…") and never as a note. It now renders the
    model's text and applies each gate through its adapter, like every other lane."""
    from trusted_data_agent.agent.engines.conversation_engine import _apply_gate_verdicts_to_html as conv
    from trusted_data_agent.agent.grounding import adapter as g
    model = 'The planner asks "Does the plan mix prompts and tools?" first.'
    gev = {"decision": "annotate", "annotation_md": "Some statements could not be matched: `Does the plan mix prompts and tools?`.",
           "unsupported_claims": ["Does the plan mix prompts and tools?"]}
    gated = model + "\n\n> " + gev["annotation_md"]
    out = conv(R(model) + RAIL, gated, gev, None, None, None)
    check("the Conversation lane shows the Scope note as a note", 'data-kind="scope"' in out, out)
    check("…with the claim highlighted in place", '<mark class="claim-unsupported' in out, out)
    check("…and never as a model quotation or a '>' paragraph", "ans-md-quote" not in out and "&gt; Some" not in out
          and "> Some" not in re.sub(r"<[^>]+>", "", out), out)
    obs = conv(R(model) + RAIL, model, None, None, {"acted": True, "decision": "flag"}, None)
    check("an observe-mode honesty verdict changes nothing (as on the Optimize lane)", obs == R(model) + RAIL, obs)
    dis = conv(R(model) + RAIL, model + "\n\nNote: a sample.", None, None,
               {"acted": True, "disclosure_appended": True}, None)
    check("an appended honesty disclosure is a note", 'data-kind="honesty"' in dis, dis)
    src = open(os.path.join(ROOT, "src/trusted_data_agent/agent/engines/conversation_engine.py"), encoding="utf-8").read()
    check("the lane renders the model's text, not the gated text", '"llm_response_text": _render_text' in src
          and "_model_text = response_text" in src, "")
    check("…and applies the verdicts after rendering", src.index("_apply_gate_verdicts_to_html(\n") > src.index("formatter.render()"), "")


def t_producers_route_through_place_note():
    """No producer may concatenate a note onto the answer — that is how a note ended up below
    nine collapsed sources. Comments stripped: prose about the code is not the code."""
    files = ["src/trusted_data_agent/agent/grounding/adapter.py", "src/trusted_data_agent/agent/honesty/adapter.py",
             "src/trusted_data_agent/agent/guardrails/adapter.py", "src/trusted_data_agent/agent/executor.py",
             "src/trusted_data_agent/agent/engines/focus_engine.py", "src/trusted_data_agent/agent/engines/ideate_engine.py"]
    bad = re.compile(r"\+\s*_ans\.note\(|\+\s*_lin\.note_html\(|\+=\s*_sens_note|\(marked or \"\"\)\s*\+\s*note|guardrail-note")
    for f in files:
        src = "\n".join(line.split("#", 1)[0] for line in open(os.path.join(ROOT, f), encoding="utf-8").read().splitlines())
        hits = [ln for ln in src.splitlines() if bad.search(ln)]
        check(f"{os.path.basename(f)} adds notes only via place_note", not hits, hits)


def t_quote_vs_note_styling():
    css = open(os.path.join(ROOT, "static/css/answer.css"), encoding="utf-8").read()
    m = re.search(r"blockquote\.ans-md-quote\s*\{([^}]*)\}", css)
    check("a model quotation has its own style", bool(m))
    if m:
        body = m.group(1)
        check("…a 1px neutral hairline, never the note's 3px tone bar",
              "1px" in body and "--ans-tone" not in body and "3px" not in body, body)
    check("the note keeps its 3px tone bar", re.search(r"\.ans-note\s*\{[^}]*3px solid var\(--ans-tone", css) is not None)


# ── B2. a citation label is support ─────────────────────────────────────────────────
TURN3 = {
    "user_query": "how does Proactive Re-planning work?",
    "knowledge_retrieval_event": {
        "collections": ["Uderia Documentation"],
        "chunks": [
            {"source": "Uderia Platform - Your Trusted Data Agent",
             "content": "Strategic Correction with Learning System: the champion case learning system acts as a self-healing mechanism."},
            {"source": "Fusion Optimizer Architecture",
             "content": "Tool errors flow through normal self-correction (S4)."},
            {"source": "README.md", "content": "Plan Hydration injects previous results to skip redundant tool calls."},
        ],
    },
}
TURN3_ANSWER = ("The system uses a champion case learning system. According to the *Fusion Optimizer "
                "Architecture* documentation, tool errors flow through normal self-correction (S4).\n\n***\n\n"
                "*Source: Uderia Platform - Your Trusted Data Agent (Collection: Uderia Documentation); "
                "README.md (Collection: Uderia Documentation)*")


def t_citation_labels_are_support():
    from trusted_data_agent.eval.types import RunRecord, chunk_support_text
    from trusted_data_agent.agent.grounding import corpus
    from trusted_data_agent.agent.grounding.adapter import build_inputs
    rr = RunRecord(TURN3)
    texts = rr.retrieved_chunk_texts()
    check("each chunk's support text carries its citation line", all(t.startswith("Source: ") for t in texts), texts)
    check("…including the repository name", all("(Collection: Uderia Documentation)" in t for t in texts), texts)
    raw = [d["content"] for d in rr.retrieved_documents()]
    before = corpus.assess(TURN3_ANSWER, raw, TURN3["user_query"]).unsupported_spans
    check("the old content-only support set convicted the citation (the reported turn)",
          {"README.md", "Uderia Documentation"} <= set(before), before)
    after = corpus.assess(TURN3_ANSWER, texts, TURN3["user_query"]).unsupported_spans
    check("with the citation line, the model's own citation is supported", after == (), after)
    inp = build_inputs(TURN3, TURN3_ANSWER)
    check("the in-flight gate reads the same support set", tuple(inp.retrieved_chunks) == texts, inp.retrieved_chunks)
    check("a label alone (no content) supports nothing", chunk_support_text({"source": "X"}) == "")
    check("a chunk without labels is just its content", chunk_support_text({"content": "body"}) == "body")
    check("metadata titles/filenames count as labels",
          "guide.pdf" in chunk_support_text({"content": "b", "metadata": {"filename": "guide.pdf"}}))
    # an invented citation is still a claim the corpus does not support
    fake = corpus.assess("See README.md and the Secret Roadmap 2031 doc `planner.v9_internal`.", texts, "")
    check("an identifier no chunk or label carries is still unsupported", "planner.v9_internal" in fake.unsupported_spans,
          fake.unsupported_spans)


def t_chunks_carry_their_repository():
    """The gate is sometimes handed a retrieval event with chunks ONLY (no `collections`
    list) — the repository name must then come from the chunk itself, so every builder
    that makes a chunk records it (found live: the Focus engine dropped it)."""
    from trusted_data_agent.eval.types import RunRecord
    ev = {"knowledge_retrieval_event": {"chunks": [
        {"source": "Guide", "collection_name": "Docs Repo", "content": "Plan hydration skips calls."}]}}
    texts = RunRecord(ev).retrieved_chunk_texts()
    check("a chunk-only event still names the repository", texts and "(Collection: Docs Repo)" in texts[0], texts)
    for f, marker in (("src/trusted_data_agent/agent/engines/focus_engine.py", "knowledge_chunks.append({"),
                      ("src/trusted_data_agent/agent/engines/ideate_engine.py", "knowledge_chunks.append("),
                      ("components/builtin/context_window/modules/knowledge_context/handler.py", "def _provenance_chunks")):
        src = open(os.path.join(ROOT, f), encoding="utf-8").read()
        i = src.find(marker)
        check(f"{os.path.basename(os.path.dirname(f)) if f.endswith('handler.py') else os.path.basename(f)} builds chunks with their repository",
              i >= 0 and '"collection_name"' in src[i:i + 900], f)


def t_both_lanes_share_one_definition():
    from trusted_data_agent.eval.types import RunRecord
    from trusted_data_agent.agent.assurance import run_context as rc
    from trusted_data_agent.agent.grounding.adapter import build_inputs
    ctx = rc.from_run(RunRecord(TURN3))
    check("the post-hoc lane and the in-flight gate see the same chunks",
          tuple(ctx.chunks) == tuple(build_inputs(TURN3, TURN3_ANSWER).retrieved_chunks), ctx.chunks)


# ── the reported turn, end to end through the renderer ──────────────────────────────
def t_reported_turn_renders():
    out = R(TURN3_ANSWER)
    check("no literal '***' survives", "***" not in out, out)
    check("the separator is a rule", HR in out, out)
    check("the source line is italic, not asterisked", "<em>Source:" in out and "*Source" not in out, out)
    check("the inline italic title renders", "<em>Fusion Optimizer \nArchitecture</em>" not in out
          and "<em>Fusion Optimizer" in out, out)


for fn in [t_block_forms, t_inline_forms, t_safety, t_identifiers, t_client_parity, t_note_placement,
           t_every_annotation_survives, t_conversation_lane_notes, t_producers_route_through_place_note, t_quote_vs_note_styling, t_citation_labels_are_support,
           t_chunks_carry_their_repository, t_both_lanes_share_one_definition, t_reported_turn_renders]:
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — a raising section is a NAMED failure, never an abort
        check(f"{fn.__name__} ran", False, repr(e))

total = PASS + FAIL
print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILED'} — {total} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(0 if FAIL == 0 else 1)
