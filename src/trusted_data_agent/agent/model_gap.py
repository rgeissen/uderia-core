"""
The model gap — a question whose SUBJECT the bound model does not describe ("which leads are
open?" asked of a model with no Lead), delivered under the answer by the platform.

Two seams, two owners (PROP-008 §5a, the deterministic-first imperative):

  DECIDE   the knowledge-graph component, from the seeding search
           (``components/builtin/knowledge_graph/handler.py:_model_gap``): the subject, its
           singular, and whether THIS agent may extend the model (``may_author`` — the same
           gate ``propose_entity`` itself applies). Stamped on the enrichment as
           ``model_gap`` on every engine's ``kg_enrichment_event``; only while OBDA-MODEL · OBDA-ACT is on.
  DELIVER  this module: one platform note under the answer — the kind word states the fact,
           the line offers the path, and the act (when the agent may author) opens a small
           form in place (``static/js/ui.js`` ``_onGapProposeAct``): the person names what
           the concept records and what it belongs to, and the form asks the normalisation
           advisor directly (``POST /v1/knowledge-graph/propose``) — the verdict row and its
           Draft it follow, with no model in between. The model writes the prose and is told
           the offer is the platform's; neither whether the offer APPEARS nor whether the
           proposal is MADE is left to it (an omitted offer, then a proposal answered with an
           invented table instead of the advisor, were the defects this closes).

Every engine calls ``apply`` once, after its gates and before the turn persists, so the note
is in the stored answer and a reload shows exactly what the live turn showed. No gap ⇒ the
answer is returned unchanged (byte-identical). Pure: no I/O, no model, never raises.
"""
from __future__ import annotations

import html as _html
import json
from typing import Any, Dict, Optional, Tuple

from trusted_data_agent.agent import answer_surface as _ans

#: The marker a delivered note carries — ``apply`` never adds a second one.
NOTE_MARKER = 'data-kind="model_gap"'


def gap_of(kg_enrichment_event: Any) -> Optional[Dict[str, Any]]:
    """The first enrichment's model gap, normalised — None when the turn has none."""
    try:
        ev = kg_enrichment_event if isinstance(kg_enrichment_event, dict) else {}
        for enr in ev.get("enrichments") or []:
            mg = (enr or {}).get("model_gap") if isinstance(enr, dict) else None
            if not isinstance(mg, dict):
                continue
            term = str(mg.get("term") or "").strip()
            if not term:
                continue
            sing = str(mg.get("singular") or term).strip() or term
            ents = [str(e) for e in (mg.get("entities") or []) if str(e).strip()]
            return {"term": term, "singular": sing, "may_author": bool(mg.get("may_author")),
                    "kg_id": enr.get("kg_id"), "kg_name": enr.get("kg_name"), "entities": ents}
    except Exception:  # noqa: BLE001 — an unreadable event is no gap, never a broken turn
        return None
    return None


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _display(word: str) -> str:
    """The concept as an entity is named: its first letter capitalised, the rest as asked."""
    return word[:1].upper() + word[1:]


def note_html(gap: Dict[str, Any]) -> str:
    """The note: kind word = the fact; the line = the path; the act only when it can act —
    an agent that may author, on a graph the form can address."""
    sing = gap["singular"]
    name = _display(sing)
    if gap.get("may_author") and gap.get("kg_id"):
        esc = lambda v: _html.escape(str(v), quote=True)  # noqa: E731
        act = ('<button type="button" class="ans-act ans-gap-propose" '
               f'data-gap-term="{esc(gap["term"])}" data-gap-name="{esc(name)}" '
               f'data-kg-id="{esc(gap["kg_id"])}" '
               f'data-gap-entities="{esc(json.dumps(gap.get("entities") or [], ensure_ascii=False))}"'
               f'>Propose {_article(sing)} {esc(name)}</button>')
        return _ans.note("model_gap", f"{gap['term']} — the model can be extended with {_article(sing)} {name}.",
                         act_html=act)
    return _ans.note("model_gap", f"{gap['term']} — extending the model with {_article(sing)} {name} "
                                  f"needs an agent allowed to author it.")


def disclosure_text(gap: Dict[str, Any]) -> str:
    """The same statement in the answer's text form (the conversation history the next turn
    reads, the text a coordinator receives) — so a following "yes, propose it" has its
    referent."""
    sing = gap["singular"]
    if gap.get("may_author"):
        return f"_Not in the model: {gap['term']}. {_article(sing).capitalize()} {_display(sing)} can be proposed as an extension of the model._"
    return f"_Not in the model: {gap['term']}._"


def apply(text: Optional[str], html: Optional[str], kg_enrichment_event: Any
          ) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """``(text, html, gap)`` — the answer with the note placed above its evidence, and the
    gap that was delivered (None ⇒ the answer is returned untouched). Idempotent."""
    try:
        gap = gap_of(kg_enrichment_event)
        if not gap:
            return text, html, None
        if NOTE_MARKER in (html or ""):
            return text, html, gap
        new_html = _ans.place_note(html or "", note_html(gap))
        new_text = ((text or "").rstrip() + "\n\n" + disclosure_text(gap)) if text is not None else text
        return new_text, new_html, gap
    except Exception:  # noqa: BLE001 — a delivery that fails leaves the answer as it was
        return text, html, None
