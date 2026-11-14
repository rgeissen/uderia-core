"""
business_terms — a proposal in a person's words becomes a proposal in the model's terms
(PROP-008 OBDA-MODEL; the code half of "the investigator never names a column").

A person extending the model says what the new concept RECORDS in plain words ("Summary",
"Assigned investigator") and what it BELONGS TO (an entity of the model). Landing the draft
needs more than that, and all of it is decidable without a model:

  * every attribute needs an identifier the warehouse accepts — derived from the words in the
    naming style the model already uses (``assigned_investigator`` beside ``incident_number``;
    ``AssignedInvestigator`` beside ``IncidentNumber``); the words stay on the attribute as its
    label;
  * the concept needs an identifier of its own to be a key — added unless the person named one;
  * belonging to an entity needs the reference the landing turns into a foreign key — an
    attribute named exactly like that entity's declared key, added unless one is there.

Pure: no I/O, no model, never raises on odd input. ``propose_entity`` calls ``complete`` for
every face (a conversation, the model-gap form, REST), so the rule holds wherever a proposal
comes from; an agent that already writes identifiers gets them back unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+[a-z]*")


def is_identifier(name: Any) -> bool:
    return bool(_IDENT.match(str(name or "").strip()))


def _words(text: str) -> List[str]:
    out: List[str] = []
    for chunk in _WORD.findall(str(text or "")):
        out.extend(w.lower() for w in _CAMEL.findall(chunk) or [chunk])
    return [w for w in out if w]


def naming_style(names: Iterable[Any]) -> str:
    """``snake`` | ``pascal`` | ``camel`` — the style most of ``names`` (leaf identifiers of the
    model) already use; ``snake`` when the model says nothing."""
    votes = {"snake": 0, "pascal": 0, "camel": 0}
    for n in names or []:
        leaf = str(n or "").rsplit(".", 1)[-1]
        if not is_identifier(leaf) or len(_words(leaf)) < 2:
            continue                       # one-word names carry no style
        if "_" in leaf or leaf.islower():
            votes["snake"] += 1
        elif leaf[0].isupper():
            votes["pascal"] += 1
        else:
            votes["camel"] += 1
    best = max(votes, key=lambda k: votes[k])
    return best if votes[best] else "snake"


def _in_style(ident: str, style: str) -> bool:
    if style == "pascal":
        return ident[:1].isupper() and "_" not in ident
    if style == "camel":
        return ident[:1].islower() and "_" not in ident
    return ident == ident.lower()


def identifier_for(text: Any, style: str = "snake") -> str:
    """The identifier for a person's words, in ``style`` — the model's own; an identifier that
    already reads in that style is returned as is (``LeadID`` in a snake_case model is
    ``lead_id``; ``incident_number`` stays)."""
    s = str(text or "").strip()
    if is_identifier(s) and _in_style(s, style):
        return s
    ws = _words(s)
    if not ws:
        return ""
    if style == "pascal":
        ident = "".join(w.capitalize() for w in ws)
    elif style == "camel":
        ident = ws[0] + "".join(w.capitalize() for w in ws[1:])
    else:
        ident = "_".join(ws)
    return ident if not ident[0].isdigit() else f"_{ident}"


def label_for(name: Any) -> str:
    """A person's words for an identifier: ``assigned_investigator`` → ``Assigned investigator``."""
    ws = _words(str(name or ""))
    return " ".join(ws).capitalize() if ws else str(name or "")


def _same(a: str, b: str) -> bool:
    return "".join(_words(a)) == "".join(_words(b))


def complete(entity_name: str, attributes: List[Dict[str, Any]], *, key: Optional[List[str]] = None,
             parent_name: Optional[str] = None, parent_key: Optional[List[str]] = None,
             parent_key_types: Optional[Dict[str, str]] = None,
             model_names: Iterable[Any] = ()) -> Dict[str, Any]:
    """The proposal in the model's terms.

    ``attributes``: ``[{name, data_type?, description?}]`` as the person (or agent) gave them.
    Returns ``{"attributes": [...], "key": [...], "added": [...]}`` — each attribute carries
    ``name`` (an identifier), ``label`` (the person's words), ``added`` (``key`` | ``reference``
    when the platform added it); ``added`` lists those in words for the proposal row.
    """
    style = naming_style(model_names)
    ent_leaf = str(entity_name or "").rsplit(".", 1)[-1]
    out: List[Dict[str, Any]] = []
    seen = set()
    for a in attributes or []:
        raw = str((a or {}).get("name") or "").strip()
        ident = identifier_for(raw, style)
        if not ident or ident.lower() in seen:
            continue
        seen.add(ident.lower())
        item = dict(a)
        item["name"] = ident
        # a label the proposal already carries (a Draft it re-runs the same proposal) is kept
        item["label"] = str(a.get("label") or "") or (
            raw if (not is_identifier(raw) or raw[:1].isupper() and " " not in raw and len(_words(raw)) == 1)
            else label_for(raw))
        out.append(item)
    added: List[Dict[str, str]] = []

    # the key: one the person named, else an attribute that IS the concept's identifier, else added
    k = [identifier_for(x, style) for x in (key or []) if str(x).strip()]
    if not k:
        own_id = [a["name"] for a in out if _same(a["name"], ent_leaf + "id") or _same(a["name"], ent_leaf + "_id")]
        if own_id:
            k = own_id[:1]
        else:
            kid = identifier_for(f"{ent_leaf} id", style)
            out.insert(0, {"name": kid, "label": f"{label_for(ent_leaf)} identifier", "data_type": "",
                           "description": f"Identifies one {label_for(ent_leaf).lower()}.", "added": "key"})
            added.append({"what": "key", "label": f"{label_for(ent_leaf)} identifier"})
            k = [kid]

    # the reference to what it belongs to: an attribute named exactly like the parent's key
    if parent_name and parent_key:
        p_leaf = str(parent_name).rsplit(".", 1)[-1]
        have = {a["name"].lower() for a in out}
        for pk in parent_key:
            if str(pk).lower() in have:
                continue
            # the reference takes the type the parent's key HAS (a foreign key must match it)
            out.append({"name": str(pk), "label": f"The {label_for(p_leaf).lower()} it belongs to",
                        "data_type": str((parent_key_types or {}).get(pk) or ""),
                        "description": f"Refers to one {p_leaf} ({pk}).", "added": "reference"})
            added.append({"what": "reference", "label": f"the {p_leaf} it belongs to"})
    return {"attributes": out, "key": k, "added": added, "style": style}
