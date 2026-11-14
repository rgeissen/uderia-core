"""
The model gap, delivered by the platform (PROP-008 §5a — agent/model_gap.py).

A question whose SUBJECT the bound model does not describe is decided in code by the
knowledge-graph component; this gate proves the OUTCOME is code's too, for every engine and
every model — not an instruction the LLM may skip (production, 12 Oct 2026: the agent stated
the gap and left the offer out, so the investigator had nothing to say "yes" to).

  §A  the pure delivery: no gap ⇒ byte-identical; a gap ⇒ one note, above the evidence,
      the act only when the agent may author; idempotent; never raises
  §B  generality: nothing about one domain — any term, any article, any markup in the term
  §C  the decision side: the singular is stamped where the gap is decided; the prompt tells
      the model the offer is the platform's (and still names the act it alone performs)
  §D  every engine delivers it, before the turn persists (a reload shows what live showed)
  §E  the answer surface: the note kind is declared and carries its act

Run: python test/test_model_gap.py
"""
import os
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail!r}"[:300] if detail is not None else ""))


def src(path):
    return open(os.path.join(ROOT, path)).read()


def event(term, singular=None, may_author=True, kg_id="kg-1", extra_first=False):
    enr = {"kg_id": kg_id, "kg_name": "Any Model", "entity_count": 9,
           "model_gap": {"term": term, "may_author": may_author, **({"singular": singular} if singular else {})}}
    enrs = ([{"kg_id": "kg-0", "entity_count": 3}] if extra_first else []) + [enr]
    return {"enrichments": enrs, "total_entities": 12}


from trusted_data_agent.agent import model_gap as MG  # noqa: E402
from trusted_data_agent.agent import answer_surface as A  # noqa: E402

# ── §A ─────────────────────────────────────────────────────────────────────────
print("[§A] the pure delivery")
body = "<p>The model does not record leads.</p>"
for ev, why in ((None, "no event"), ({}, "an empty event"), ({"enrichments": [{"kg_id": "k"}]}, "an enrichment without a gap"),
                ({"enrichments": [{"model_gap": {"term": ""}}]}, "a gap with no term"), ("garbage", "a malformed event")):
    t, h, g = MG.apply("text", body, ev)
    check(f"{why} ⇒ the answer is returned byte-identical", (t, h, g) == ("text", body, None))

t, h, g = MG.apply("The model does not record leads.", body, event("leads", "lead"))
check("a gap ⇒ the gap is returned", g and g["term"] == "leads" and g["singular"] == "lead" and g["may_author"])
check("a gap ⇒ exactly one note of kind model_gap", h.count(MG.NOTE_MARKER) == 1, h)
check("the note's kind word states the fact", "Not in the model" in h)
check("the note offers the act when the agent may author", 'class="ans-act ans-gap-propose"' in h and ">Propose a Lead</button>" in h, h)
check("the act carries the concept's name for the form", 'data-gap-name="Lead"' in h, h)
check("the act names the graph the form proposes into", 'data-kg-id="kg-1"' in h)
check("no model text is pre-written for the person (no composer prefill)", "data-gap-prefill" not in h)
_, hp, gp = MG.apply("", "", {"enrichments": [{"kg_id": "kg-1", "model_gap": {
    "term": "leads", "singular": "lead", "may_author": True,
    "entities": ["Model.CaseObject", 'Model.<x>"y']}}]})
check("the act carries the model's own entities for 'Belongs to' — escaped",
      gp["entities"] == ["Model.CaseObject", 'Model.<x>"y']
      and 'data-gap-entities="[&quot;Model.CaseObject&quot;, &quot;Model.&lt;x&gt;\\&quot;y&quot;]"' in hp, hp)
_, hk, _ = MG.apply("", "", {"enrichments": [{"model_gap": {"term": "leads", "singular": "lead", "may_author": True}}]})
check("no graph to address ⇒ the note without an act (never a button that cannot act)",
      MG.NOTE_MARKER in hk and "ans-gap-propose" not in hk, hk)
check("the text form says it too (the next turn's 'yes' has its referent)",
      t.endswith("_Not in the model: leads. A Lead can be proposed as an extension of the model._"), t)
t2, h2, _ = MG.apply(t, h, event("leads", "lead"))
check("idempotent: a second pass adds no second note", h2 == h and t2 == t)

_, hn, gn = MG.apply("x", body, event("leads", "lead", may_author=False))
check("an agent that may NOT author: the note, and no act (never a button that cannot act)",
      MG.NOTE_MARKER in hn and "ans-gap-propose" not in hn and "needs an agent allowed to author it" in hn, hn)
check("…and its text form makes no offer", MG.disclosure_text(gn) == "_Not in the model: leads._")

rail = '<p>prose</p><div class="ans-rail"><details class="ans-ev">rows</details></div>'
_, hr, _ = MG.apply("x", rail, event("leads", "lead"))
check("the note goes under the prose and ABOVE the evidence rail (place_note)",
      hr.index(MG.NOTE_MARKER) < hr.index('class="ans-rail"'), hr)
_, hx, _ = MG.apply(None, None, event("leads", "lead"))
check("an answer with no text/html still gets the note, and the text stays None", MG.NOTE_MARKER in (hx or ""))
check("the first enrichment WITH a gap is the one delivered", MG.gap_of(event("leads", "lead", extra_first=True))["kg_id"] == "kg-1")
check("no singular stamped (an older event) ⇒ the term itself, never a guess", MG.gap_of(event("staff"))["singular"] == "staff")

# ── §B ─────────────────────────────────────────────────────────────────────────
print("[§B] generality — no domain is special")
for term, sing, art in (("warrants", "warrant", "a"), ("invoices", "invoice", "an"), ("assets", "asset", "an"),
                        ("shipments", "shipment", "a"), ("employees", "employee", "an")):
    _, hh, _ = MG.apply("", "", event(term, sing))
    name = sing[:1].upper() + sing[1:]
    check(f"'{term}' ⇒ 'Propose {art} {name}'", f">Propose {art} {name}</button>" in hh, hh)
_, he, _ = MG.apply("", "", event('<b>x"y</b>s', '<b>x"y</b>'))
check("markup in a term is escaped everywhere (text, attribute, label)",
      "<b>" not in he.replace('<b class', '') and "&lt;b&gt;" in he and 'x&quot;y' in he, he)
mg_src = src("src/trusted_data_agent/agent/model_gap.py")
code_only = re.sub(r'"""[\s\S]*?"""', "", mg_src)            # docstrings may tell the story
code_only = "\n".join(l.split("#", 1)[0] for l in code_only.splitlines())
check("the delivery module names no domain (no lead / policing / case / PXPROD in its code)",
      not re.search(r"\b(lead|leads|polic\w*|heist|nibrs|pxprod)\b", code_only, re.I))

# ── §C ─────────────────────────────────────────────────────────────────────────
print("[§C] the decision side")
from components.builtin.knowledge_graph import handler as KH  # noqa: E402
lines = KH._render_gap_lead("warrants", True)
check("the prompt still states the gap and forbids a substitute",
      lines[0].startswith("NOT IN THIS MODEL") and "Do not present another entity as warrants" in lines[0])
check("the prompt tells the model the offer is the platform's — not to offer it itself",
      len(lines) == 2 and "The platform offers to extend the model with a warrant" in lines[1]
      and "do not offer it yourself" in lines[1], lines)
check("the prompt keeps the one act only the model performs once asked (propose_entity)",
      "Only when asked to, propose the warrant with action 'propose_entity'" in lines[1])
check("no author ⇒ no propose clause at all", len(KH._render_gap_lead("warrants", False)) == 1)
h = src("components/builtin/knowledge_graph/handler.py")
check("the singular is decided where the gap is decided, and stamped on the enrichment",
      re.search(r'metadata\["model_gap"\] = \{"term": _gap, "singular": _singularize\(_gap\) or _gap,', h) is not None)
check("the gap is still decided only while OBDA-MODEL is on for the agent (byte-identical baseline)",
      '_planes_gap.plane_enabled(' in h and '"model", _profile_of(profile_id, user_uuid), user_uuid)' in h)
check("may_author is the SAME gate propose_entity applies", '_may_author = self._act_refusal("propose_entity", {"profile_id": profile_id, "user_uuid": user_uuid}) is None' in h)
check("the candidates for 'Belongs to' are read from the graph that decided the gap — bounded, and only "
      "when a proposal can follow",
      'store.list_entities(entity_type="entity", limit=400)' in h and "[:_GAP_MAX_PARENTS]" in h
      and "if _may_author:" in h and '"entities": _cands}' in h and KH._GAP_MAX_PARENTS <= 200)
check("the candidates are metadata, never prompt text", "_cands" not in "".join(KH._render_gap_lead("x", True)))

# ── §D ─────────────────────────────────────────────────────────────────────────
print("[§D] every engine delivers it, before the turn persists")
ex = src("src/trusted_data_agent/agent/executor.py")
check("one executor method reads the turn's enrichment and delivers through the pure module",
      "def _apply_model_gap(self, final_text: str, final_html: str):" in ex
      and '_mg.apply(final_text, final_html, getattr(self, "kg_enrichment_event", None))' in ex)


def before(path, call, anchor, what):
    s = src(path)
    i, j = s.find(call), s.find(anchor, s.find(call) if s.find(call) >= 0 else 0)
    check(f"{what}: delivered ({call.strip()[:40]}…) before it persists", i >= 0 and j > i, (i, j))


before("src/trusted_data_agent/agent/executor.py",
       "self.final_summary_text, final_html = self._apply_model_gap(self.final_summary_text, final_html)",
       "await session_manager.add_message_to_histories(", "Optimize (tool_enabled)")
before("src/trusted_data_agent/agent/engines/conversation_engine.py",
       "response_text, final_html = executor._apply_model_gap(response_text, final_html)",
       "await session_manager.add_message_to_histories(", "Ideate + tools (conversation engine)")
before("src/trusted_data_agent/agent/engines/ideate_engine.py",
       "response_text, final_html = executor._apply_model_gap(response_text, final_html)",
       "await session_manager.add_message_to_histories(", "Ideate (direct)")
before("src/trusted_data_agent/agent/engines/focus_engine.py",
       "no_results_text, no_results_message = executor._apply_model_gap(no_results_text, no_results_message)",
       "await session_manager.add_message_to_histories(", "Focus — nothing retrieved")
before("src/trusted_data_agent/agent/engines/focus_engine.py",
       "response_text, final_html = executor._apply_model_gap(response_text, final_html)",
       "await session_manager.add_message_to_histories(", "Focus — synthesised")
before("src/trusted_data_agent/agent/engines/coordinate_engine.py",
       "coordinator_response, coordinator_html, _ = _model_gap.apply(",
       "await session_manager.add_message_to_histories(\n                    user_uuid=user_uuid,", "Coordinate (genie)")
cg = src("src/trusted_data_agent/agent/engines/coordinate_engine.py")
check("Coordinate reads its OWN enrichment", "coordinator_html, result.get('kg_enrichment_event'))" in cg)
fe = src("src/trusted_data_agent/agent/engines/focus_engine.py")
check("Focus delivers on the synthesised answer before the final_answer frame is emitted",
      fe.find("response_text, final_html = executor._apply_model_gap(response_text, final_html)")
      < fe.find('"final_answer": final_html,\n            "final_answer_text": response_text,  # Clean text for parent genie'))

# ── §E ─────────────────────────────────────────────────────────────────────────
print("[§E] the answer surface")
check("model_gap is a declared note kind, muted (a path, not a finding)",
      A.NOTE_KINDS.get("model_gap", {}).get("tone") == "muted")
n = A.note("model_gap", "t", act_html='<button class="ans-act x">go</button>')
check("a note carries a caller's act in the act slot", n.endswith('<button class="ans-act x">go</button></div>'), n)
check("a route still wins over an act (one slot)", "ans-note-route" in A.note("budget", "t", route="r", act_html="<b>no</b>")
      and "<b>no</b>" not in A.note("budget", "t", route="r", act_html="<b>no</b>"))
css = src("static/css/answer.css")
check("the note's act is styled from theme variables", ".ans-note .ans-act {" in css and "var(--teradata-orange" in css)


# ── a qualifier is never the subject (prod 86ac6b28 turn 12: "Not in the model: actual") ──
print("[subject] qualifiers before the noun are skipped")
try:
    from components.builtin.knowledge_graph import handler as _KH
    _q = _KH._question_subject
    check("'show me the actual email' → email, not 'actual'", _q("show me the actual email") == "email", _q("show me the actual email"))
    check("'show me one email from the case' → email, not 'one'", _q("Show me one email from the case") == "email", _q("Show me one email from the case"))
    check("'which open leads' still → leads", _q("which open leads do we have") == "leads")
    check("'how many robberies' still → robberies", _q("how many robberies are there") == "robberies")
    check("a model gap is NOT raised for a qualifier the graph never names",
          _KH._model_gap("show me the actual email", ["actual"]) is None)
except Exception as _e:  # noqa: BLE001
    check(f"subject extractor importable ({_e})", False)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
