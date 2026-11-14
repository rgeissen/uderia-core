"""The bounded push must be resolvable — one fact renderer, two audiences.

The enrichment PUSHES a node's facts into every prompt and must bound them: a product
with 1,508 columns and 50-value code lists does not fit. The `query` action PULLS facts
for one node and has no such constraint. That is the natural hand-off — and it did not
work, because the pull returned strictly LESS than the push: name, type and description,
dropping the decoded values, PII class, classification and deployed type that are the
whole reason to look a column up.

So `(+42 more)` was an honest dead end. A live agent met a 50-value offence code
rendered as its first 8, none of them the one it needed, took the FBI code from a
document in its knowledge corpus, queried `26G`, got zero rows, and reported that
hacking offences "may not be captured in this dataset" — of 400,187 that are.

The fix is one renderer used by both surfaces, bounded on one side and complete on the
other, plus a truncation marker that names its own way out. Ranking the bounded slice is
an OPTIMISATION of the push and is asserted as such: it changes which values are visible,
never whether the missing ones are reachable.

CI-pure: no database, no network.
"""
import sys, pathlib, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_passed = _failed = 0
def check(name, cond, detail=""):
    global _passed, _failed
    if cond: _passed += 1; print(f"  ok  {name}")
    else: _failed += 1; print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))

import components.builtin.knowledge_graph.handler as H

# 50 values, the two that matter deliberately outside the first 8 — the live shape.
OFFENCES = ([{"value": str(90 + i), "label": f"Offence {i}"} for i in range(1, 9)]
            + [{"value": "266", "label": "Identity Theft"},
               {"value": "267", "label": "Hacking/Computer Invasion"}]
            + [{"value": str(300 + i), "label": f"Other {i}"} for i in range(40)])
COL = {"description": "UCR OFFENSE CODE - 1", "allowed_values": OFFENCES,
       "deployed_type": "VARCHAR(3)", "is_pii": True, "data_classification": "CONFIDENTIAL"}


def t_the_push_is_still_bounded():
    """Removing the bound is not the fix — it would put 50-value lists on every column
    of every prompt."""
    line = H._coded_line(COL, H._governance_parts(COL))
    shown = line.count("=")
    check("the pushed list is truncated", shown <= H._MAX_CODES_PER_COLUMN + 2, str(shown))
    check("and says how many it withheld", "+42 more" in line, line[:160])


def t_the_marker_names_its_own_way_out():
    """An admission the agent cannot act on is a dead end. This is emitted by CODE, in
    the text the component already produces — not a rule the model has to remember."""
    line = H._coded_line(COL, H._governance_parts(COL))
    check("the marker names the action that resolves it",
          "TDA_KnowledgeGraph" in line and 'action="query"' in line, line[-140:])


def t_the_pull_is_complete():
    full = H._coded_line(COL, H._governance_parts(COL, complete=True))
    check("every declared value is rendered", full.count("=") >= 50, str(full.count("=")))
    check("including the ones the push withheld",
          "'266'=Identity Theft" in full and "'267'=Hacking/Computer Invasion" in full)
    check("and it carries no truncation marker", "more —" not in full, full[-120:])


def t_both_surfaces_use_the_same_renderer():
    """The defect was two surfaces with different ideas of what a node carries. The
    MODEL's surface is the payload metadata, rendered through the shared formatter with
    the complete fact set (asserted structurally). The READER's surface is the card —
    asserted on its OUTPUT (2026-09-17): it used to pin `_coded_line(` in the card's
    source, an implementation pin that broke the moment the card became a card."""
    meta = inspect.getsource(H.KnowledgeGraphHandler._handle_query)
    check("the model gets the facts as text, not only markup",
          '"facts"' in meta and "_coded_line(" in meta and "complete=True" in meta)
    html = _card([_ENT("Policing Core.Arrestee.arrest_offence_code", "attribute", COL)], "Hacking")
    check("the reader's card is COMPLETE: every declared value is in the markup",
          html.count(" = ") >= 50, str(html.count(" = ")))
    check("including the ones the push withheld",
          "267 = Hacking/Computer Invasion" in html and "266 = Identity Theft" in html)
    check("and it carries no truncation marker (the disclosure IS the way out)",
          "more —" not in html and "<details" in html)


def _ENT(name, etype, props):
    return {"name": name, "entity_type": etype, "properties": props}


def _card(ents, query):
    h = H.KnowledgeGraphHandler.__new__(H.KnowledgeGraphHandler)
    return H.KnowledgeGraphHandler._format_query_results_html(h, ents, query)


def t_the_card_reads_as_a_result():
    """The card is for a PERSON (2026-09-17). Its previous form was a flex row whose
    facts span held the whole 50-value domain, so the name column shrank until it
    wrapped LETTER BY LETTER and the fact the reader wanted — which attribute, on which
    entity, PII — sat under fifty offence labels. Asserted on the rendered output."""
    import re
    CHIP = r"<span class='kg-lk-chip[^']*'>([^<]*)</span>"
    html = _card([_ENT("Policing Core.Arrestee.arrest_offence_code", "attribute", COL)], "Hacking")
    text = re.sub(r"<[^>]+>", "|", html)
    check("the leaf name leads, at full weight", "class='kg-lk-name'>arrest_offence_code<" in html)
    check("the owner entity is named beside it, never fused into the name",
          "in Policing Core \u203a Arrestee" in html)
    check("the row never shrinks the name: a wrapping head, no facts span",
          "class='kg-lk-head'" in html and "text-sm ml-2" not in html)
    for b in ("PII", "CONFIDENTIAL", "VARCHAR(3)"):
        check(f"governance is a badge: {b}", f"class='kg-lk-chip'>{b}</span>" in html)
    check("badges stay in flow, never pushed to the right edge (they wrap away from the name)",
          "margin-left:auto" not in html)
    seg = html.split("values (50)")[1]
    chips = re.findall(CHIP, seg)
    check("the value matching the question is the FIRST chip",
          chips[:1] == ["267 = Hacking/Computer Invasion"], chips[:3])
    check("and is marked as the hit", "kg-lk-hit'>267 = Hacking" in seg)
    vis = seg.split("<details")[0]
    check("only a bounded slice is visible before the disclosure",
          len(re.findall(CHIP, vis)) <= H.KnowledgeGraphHandler._LOOKUP_VISIBLE_VALUES)
    check("the disclosure says how many it holds", "+42 more" in html)
    check("no note restates a badge", "PII; CONFIDENTIAL" not in html and "; PII" not in html)
    check("the deployed-as-text trap survives as a note", "compare as strings" in html)
    check("theme tokens, never a hardcoded gray", "var(--text-muted" in html and "text-gray-" not in html)
    check("styles are ONE stylesheet per card, not a style attribute per chip",
          html.count("<style>") == 1 and "<span style=" not in html)
    # the description leads with its first clause; the codebook prose is one click away
    long_desc = dict(COL, description="UCR ARREST OFFENSE CODE - V6011 identifies the offense for which the LEA "
                                      "arrested the arrestee. An LEA can arrest an offender for any of the Group A "
                                      "or Group B offenses except Justifiable Homicide.")
    html3 = _card([_ENT("Policing Core.Arrestee.arrest_offence_code", "attribute", long_desc)], "Hacking")
    lead_seg = html3.split("full description")[0]
    check("a long description leads with its discriminating clause",
          "UCR ARREST OFFENSE CODE" in lead_seg and "V6011 identifies" not in lead_seg)
    check("and the full text is still in the markup, behind a disclosure",
          "V6011 identifies the offense" in html3 and "<summary>full description</summary>" in html3)
    check("a short description gets no disclosure of its own", "full description" not in html)
    # the decoded-label half of a two-column decode, and a plain entity
    lbl = {"description": "Decoded label of arrest_offence_code",
           "decoded_labels": [x["label"] for x in OFFENCES], "decoded_from": "arrest_offence_code"}
    html2 = _card([_ENT("Policing Core.Arrestee.arrest_offence", "attribute", lbl),
                   _ENT("Policing Core.Offence", "entity", {"description": "An offence"})], "Hacking")
    check("a label column lists its labels, hit first",
          "decoded label of arrest_offence_code" in html2
          and re.findall(CHIP, html2.split("values (50)")[1])[:1] == ["Hacking/Computer Invasion"])
    check("an entity with no domain renders no values block",
          "kg-lk-vlabel" not in html2.split("kg-lk-name'>Offence<")[1])
    check("an entity's owner is the model, its name the leaf",
          "kg-lk-name'>Offence<" in html2 and "in Policing Core<" in html2)
    check("markup is escaped", "<script" not in _card([_ENT("<script>x", "attribute", {"description": "<b>"})], "q"))


def t_the_card_is_collapsed_under_the_answer():
    """The model restates the entities in its own prose above the card, so the card is
    the tool's EVIDENCE — one gesture away. The disclosure is the Answer Surface row
    that wraps EVERY component's inline output (docs/roadmap/ANSWER_SURFACE_GRAMMAR.md):
    the lookup's body is FLAT (a second <details> inside the row was two chevrons for
    one gesture), the row's summary line says what was looked up and how much came
    back, and the row is closed by default — never a blank slot, never double-framed."""
    import re as _re
    from trusted_data_agent.components.utils import generate_component_html
    html = _card([_ENT("Policing Core.Offence.offence_code", "attribute", COL),
                  _ENT("Policing Core.Offence", "entity", {})], "Robbery")
    check("the lookup body is flat — no details of its own, the cards directly inside",
          html.lstrip().startswith("<style>") and "<div class='kg-lookup'>" in html and "<details class='kg-lookup'" not in html
          and "kg-lk-chev" not in html and "\u25b6" not in html and "<div class='kg-lk-card'" in html)
    row = generate_component_html([{"component_id": "knowledge_graph", "html": html,
                                    "title": "2 entities matching 'Robbery'", "metadata": {"tool_name": "TDA_KnowledgeGraph", "action": "query"}}])
    check("the row is a closed evidence row wearing the component's identity",
          row.count('<details class="ans-ev"') == 1 and 'data-component="knowledge_graph"' in row
          and " open" not in row.split("<summary")[0] and "response-card" not in row)
    check("its summary carries the count and the query, the provenance is the tool",
          "2 entities matching &#x27;Robbery&#x27;" in row or "2 entities matching 'Robbery'" in row)
    check("the provenance chip names the tool", "Tool output · TDA_KnowledgeGraph" in row)
    check("the cards sit INSIDE the row body", row.index('<div class="ans-ev-b">') < row.index("<div class='kg-lk-card'"))
    src = pathlib.Path(H.__file__).read_text()
    check("the query payload states its own summary (count + query) — one entity reads singular",
          "title=f\"{len(results)} entit{'y' if len(results) == 1 else 'ies'} matching '{query_text}'\"" in src)
    stats = H.KnowledgeGraphHandler._format_stats_html(H.KnowledgeGraphHandler.__new__(H.KnowledgeGraphHandler),
                                                        {"total_entities": 3, "total_relationships": 1})
    check("the stats card (a lookup with no term) IS the answer and is not collapsed",
          "<details" not in stats)


def t_the_pull_keeps_the_governance_facts():
    """A lookup that dropped PII/classification would trade one blind spot for another."""
    full = H._coded_line(COL, H._governance_parts(COL, complete=True))
    for fact in ("PII", "CONFIDENTIAL", "compare as strings"):
        check(f"the pull carries {fact!r}", fact in full)


def t_ranking_is_an_optimisation_not_the_fix():
    terms = ["hacking", "identity"]
    ranked = H._coded_line(COL, H._governance_parts(COL, terms=terms))
    plain  = H._coded_line(COL, H._governance_parts(COL))
    check("ranking surfaces the values the question is about",
          "'266'" in ranked and "'267'" in ranked, ranked[:150])
    check("which the unranked slice does not",
          "'266'" not in plain and "'267'" not in plain, plain[:150])
    # the point: it changes WHICH are visible, never whether the rest are reachable
    check("the withheld count is unchanged", "+42 more" in ranked and "+42 more" in plain)
    check("and the way out is still named", "TDA_KnowledgeGraph" in ranked)


def t_no_terms_renders_byte_identically():
    """Additive by construction: a question matching nothing must not move a single value."""
    before = H._format_codes(OFFENCES, limit=8, quote=True)
    after  = H._format_codes(OFFENCES, limit=8, quote=True, terms=[])
    none_  = H._format_codes(OFFENCES, limit=8, quote=True, terms=["zzzz"])
    check("empty terms change nothing", before == after)
    check("terms that match nothing change nothing", before == none_)
    check("declared order is preserved for unscored values",
          [v["value"] for v in H._rank_codes(OFFENCES, ["zzzz"])]
          == [v["value"] for v in OFFENCES])


def t_degenerate_inputs():
    check("no values -> nothing", H._format_codes([], limit=None) == "")
    check("not a list -> nothing", H._format_codes("nope") == "")
    check("a short list gets no marker", "more" not in H._format_codes(OFFENCES[:3], limit=8))
    check("complete on a short list is the same",
          H._format_codes(OFFENCES[:3], limit=None) == H._format_codes(OFFENCES[:3], limit=8))


def main():
    print("=" * 74); print("KG facts — the bounded push hands off to a complete pull"); print("=" * 74)
    for t in (t_the_push_is_still_bounded, t_the_marker_names_its_own_way_out,
              t_the_pull_is_complete, t_both_surfaces_use_the_same_renderer,
              t_the_pull_keeps_the_governance_facts, t_ranking_is_an_optimisation_not_the_fix,
              t_no_terms_renders_byte_identically, t_degenerate_inputs,
              t_the_card_reads_as_a_result, t_the_card_is_collapsed_under_the_answer):
        try: t()
        except Exception as exc:
            global _failed; _failed += 1
            print(f"  FAIL: {t.__name__} raised {exc.__class__.__name__}: {exc}")
    print(f"\n{_passed} passed, {_failed} failed  ({_passed+_failed} assertions)")
    return 1 if _failed else 0

if __name__ == "__main__":
    sys.exit(main())
