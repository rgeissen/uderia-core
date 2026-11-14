"""
The assessor registry and the BINDING TABLE.

Mirrors `kgstore/factory.py` and `vectorstore/factory.py` — register an implementation,
resolve it by name. What is new here is the second table: a **binding** says which assessor
serves which property in which lane, and it is DATA.

WHY DATA. A later phase exposes remapping through a UX. That is only possible if the
answer to "what assesses answer support in flight?" is a row an operator can change, not
an import at a call site. So:

    register(...)                 an implementation announces itself
    bind(property, key, lanes)    the binding — one row, serializable, overridable
    resolve(property, lane)       what every call site asks

A call site never imports an assessor. It asks the registry. That is the entire mechanism
by which a remap reaches both lanes at once.

FAIL-SAFE, NOT FAIL-OPEN. An unresolved binding returns `None` and the caller must treat
that as "not assessed" — never as "assessed clean". This module deliberately does not
supply a permissive default: a stand-in that returns a clean finding would turn a broken
configuration into a green badge, which is the single worst failure mode this platform
has (observed live: one malformed config block disabled four gates behind a fail-open
`except`, and every surface still read healthy).
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Tuple

from . import properties as _props
from .capabilities import Lane
from .contract import Assessor

logger = logging.getLogger("quart.app")

_LOCK = threading.RLock()
_ASSESSORS: Dict[str, Assessor] = {}
#: (property, lane) -> assessor key. The binding table.
_BINDINGS: Dict[Tuple[str, Lane], str] = {}


# ── registration ──────────────────────────────────────────────────────
def register(assessor: Assessor) -> Assessor:
    """Announce an implementation. Usable as a decorator on an instance factory."""
    if not assessor.key:
        raise ValueError("an assessor must declare a key")
    if not _props.get(assessor.property):
        raise ValueError(
            f"assessor {assessor.key!r} declares unknown property {assessor.property!r}; "
            f"add it to properties.PROPERTIES first — the vocabulary is closed on purpose")
    # Lane eligibility is DERIVED from capabilities, so an assessor declaring none is
    # eligible for the triggered lane alone by accident rather than by intent — and can
    # never serve the two lanes that matter. Enforcing it here makes the declaration the
    # deliberate act `capabilities.py` always described it as.
    if not assessor.capabilities:
        raise ValueError(
            f"assessor {assessor.key!r} declares no capabilities — lane eligibility is "
            f"derived from them, so a module that declares none can serve no lane safely")
    with _LOCK:
        _ASSESSORS[assessor.key] = assessor
    return assessor


def get(key: str) -> Optional[Assessor]:
    return _ASSESSORS.get(key)


def assessors(prop: Optional[str] = None) -> List[Assessor]:
    """Every registered assessor, or those serving one property — the candidate list a
    remapping UX offers."""
    out = [a for a in _ASSESSORS.values() if prop is None or a.property == prop]
    return sorted(out, key=lambda a: a.key)


# ── the binding table ─────────────────────────────────────────────────
def bind(prop: str, assessor_key: str, lanes: Tuple[Lane, ...],
         *, validate: bool = True) -> List[str]:
    """Bind an assessor to a property for one or more lanes.

    Returns the reasons it was REFUSED (empty on success). Refusing loudly and returning
    the reasons is what lets a UX explain itself; a silent partial bind would leave a lane
    unassessed while the screen showed success.
    """
    from .conformance import check_binding  # local: conformance imports this module

    problems: List[str] = []
    for lane in lanes:
        problems.extend(check_binding(prop, assessor_key, lane) if validate else [])
    if problems:
        return problems
    with _LOCK:
        for lane in lanes:
            _BINDINGS[(prop, lane)] = assessor_key
    return []


def unbind(prop: str, lane: Lane) -> None:
    with _LOCK:
        _BINDINGS.pop((prop, lane), None)


def resolve(prop: str, lane: Lane) -> Optional[Assessor]:
    """What a call site asks. `None` means NOT ASSESSED — never assessed-clean."""
    key = _BINDINGS.get((prop, lane))
    if not key:
        return None
    a = _ASSESSORS.get(key)
    if a is None:
        # A binding naming a module that is not loaded is a real fault, not a shrug: the
        # property silently stops being assessed. Loud, and still fail-safe.
        logger.error("assurance: %s/%s is bound to unknown assessor %r — this property "
                     "is NOT being assessed in that lane", prop, lane.name, key)
    return a


def bindings() -> List[Dict[str, object]]:
    """The whole table, serializable — what the remapping UX renders and what an audit
    record captures. Includes properties with NO binding, because 'nothing assesses this'
    is the reading that must not be silent."""
    out: List[Dict[str, object]] = []
    for p in _props.all_properties():
        for lane in (Lane.IN_FLIGHT, Lane.IN_FLIGHT_TRIGGERED, Lane.POST_HOC):
            meaningful = lane in p.lanes
            key = _BINDINGS.get((p.key, lane))
            out.append({
                "property": p.key,
                "label": p.label,
                "lane": lane.name,
                "meaningful": meaningful,
                "assessor": key,
                "bound": bool(key),
                "note": p.lane_notes.get(lane.name.lower()) if not meaningful else None,
            })
    return out


def symmetry(prop: str) -> Dict[str, object]:
    """How symmetric one property is, DERIVED from the bindings.

    This is the reading the Assurance Guide shows, and deriving it is the point: the map
    was a hand-built snapshot that was accurate the day it was written. A derived reading
    cannot go stale, and a property whose lanes drift apart says so on the next page load
    rather than in the next audit.
    """
    p = _props.get(prop)
    if not p:
        return {"property": prop, "state": "unknown"}
    infl = _BINDINGS.get((prop, Lane.IN_FLIGHT)) or _BINDINGS.get(
        (prop, Lane.IN_FLIGHT_TRIGGERED))
    post = _BINDINGS.get((prop, Lane.POST_HOC))
    wants_infl = bool(p.lanes & {Lane.IN_FLIGHT, Lane.IN_FLIGHT_TRIGGERED})
    wants_post = Lane.POST_HOC in p.lanes

    if infl and post:
        state = "shared" if infl == post else "split"
    elif not wants_infl and post:
        state = "by_design_score_only"
    elif not wants_post and infl:
        state = "by_design_act_only"
    elif infl or post:
        state = "gap"
    else:
        state = "unbound"
    return {"property": prop, "label": p.label, "state": state,
            "in_flight": infl, "post_hoc": post,
            "wants_in_flight": wants_infl, "wants_post_hoc": wants_post}


def reset_for_tests() -> None:
    with _LOCK:
        _ASSESSORS.clear()
        _BINDINGS.clear()

def guide_entry(prop: str) -> Optional[Dict[str, object]]:
    """A Guide-shaped entry for one property — the seventh entry class.

    A guarantee is a promise, a mechanism is machinery, a measurement is a grade; a
    PROPERTY is the thing being assessed that all three are about. It is also the unit a
    remapping UX operates on, so it needs a page before that page needs it.

    Carries the LIVE binding rather than a description of one: which module assesses it in
    each lane, and — because a reader arriving from a module row is asking "what grades
    this?" — the measurements that do.
    """
    from . import properties as _p

    pdef = _p.get(prop)
    if not pdef:
        return None
    sym = symmetry(prop)

    # ONE in-flight row, not two. `IN_FLIGHT` and `IN_FLIGHT_TRIGGERED` are ALTERNATIVES —
    # a property is assessed during the turn either unconditionally or behind a trigger,
    # never both — so listing the unused one as "nothing bound" reads as a gap that is not
    # one. What a reader wants is: does anything assess this during the turn, and can it
    # DECIDE or only escalate?
    lanes = []
    _uncond = _BINDINGS.get((prop, Lane.IN_FLIGHT))
    _trig = _BINDINGS.get((prop, Lane.IN_FLIGHT_TRIGGERED))
    _in_key = _uncond or _trig
    _wants_in = bool(pdef.lanes & {Lane.IN_FLIGHT, Lane.IN_FLIGHT_TRIGGERED})
    if _in_key or _wants_in:
        a = _ASSESSORS.get(_in_key) if _in_key else None
        lanes.append({
            "lane": "IN_FLIGHT",
            "assessor": _in_key,
            "label": (a.label or _in_key) if a else None,
            # HOW the bound module assesses, and where it came from — the same two slots
            # for a builtin and a pack, so the Guide's module tooltip explains a module
            # rather than repeating its name.
            "assessor_desc": (a.description if a else "") or "",
            "assessor_origin": (a.origin() if a else "") or "",
            # The difference that matters to a reader: a triggered assessor cannot withhold
            # anything on its own, it can only ask a judge.
            "mode": ("decides" if _uncond else "escalates") if _in_key else None,
            "note": None if _in_key else "nothing is bound to this lane",
        })
    elif pdef.lane_notes.get("in_flight"):
        lanes.append({"lane": "IN_FLIGHT", "assessor": None, "label": None, "mode": None,
                      "note": pdef.lane_notes["in_flight"]})

    _post = _BINDINGS.get((prop, Lane.POST_HOC))
    if _post or Lane.POST_HOC in pdef.lanes:
        a = _ASSESSORS.get(_post) if _post else None
        lanes.append({
            "lane": "POST_HOC", "assessor": _post,
            "label": (a.label or _post) if a else None, "mode": "grades" if _post else None,
            "assessor_desc": (a.description if a else "") or "",
            "assessor_origin": (a.origin() if a else "") or "",
            "note": None if _post else "nothing is bound to this lane",
        })
    elif pdef.lane_notes.get("post_hoc"):
        lanes.append({"lane": "POST_HOC", "assessor": None, "label": None, "mode": None,
                      "note": pdef.lane_notes["post_hoc"]})

    return {
        "k": f"property_{prop}",
        "cls": "property",
        "label": pdef.label,
        "long": pdef.question,
        "property": prop,
        "symmetry": sym.get("state"),
        "lanes": lanes,
    }


def guide_entries() -> List[Dict[str, object]]:
    from . import properties as _p
    return [e for e in (guide_entry(x.key) for x in _p.all_properties()) if e]

