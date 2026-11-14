"""
CONTROLS — the machinery that ACTS, and how a third party ships one.

The plane opened assessment first, and stopped there on the reasoning that a control runs
in the answer path and the posture ladder took five recorded contract breaches to get
right. That reasoning conflated four things:

    the assessment          what was found            → third party (already open)
    the policy              when to act, which act    → THIRD PARTY, declared as data
    the act                 withhold · alter · annotate → PLATFORM performs it
    the ladder + grammar    observe→enforce, parking,  → PLATFORM
                            the event, the reading

Only rows three and four need to be the platform's, and locking rows one and two to
protect them was over-reach. A third party does not need to *implement* withholding — it
needs to *choose* it, and say when. So a pack ships a real control: answers are withheld
or altered because of it, while the act itself, the parking, the retention, the event and
the trust reading are identical however the finding arrived.

TWO THINGS STAY CLOSED, and both are about the platform being able to DESCRIBE what
happened rather than about distrust:

  * `ACTS` — a new kind of effect needs new rendering, a new audit shape and a new reading.
  * `CHOKEPOINTS` — a control attaches where the platform already stands. A pack names one;
    it cannot invent a place in the turn.

DORMANT ON ARRIVAL is the property that replaces the old lock, and it is a better
guarantee because it is an OPERATOR's decision rather than a vendor's: a third-party
control installs at `observe`, records what it would have done, and changes nothing until
an admin raises its posture. Installing a pack can never, by itself, withhold an answer.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

#: The closed vocabulary of EFFECTS on a turn. Each already has rendering, an audit shape
#: and retention rules welded to it; a pack chooses one, it never defines one.
ACTS: Tuple[str, ...] = ("withhold", "alter", "annotate")

#: Where the platform already stands in a turn. A control attaches to one of these.
CHOKEPOINTS: Tuple[str, ...] = ("outbound_answer", "inbound_request")

#: Which mechanism stands at each chokepoint. A control names a CHOKEPOINT (a place in
#: the turn); a property's guarantee names a MECHANISM. This is the one place the two
#: vocabularies meet, so the seam can derive what to consult from the controls declared.
MECHANISM_CHOKEPOINT: Dict[str, str] = {
    "outbound_guard": "outbound_answer",
    "inbound_guard": "inbound_request",
}


def chokepoint_of(mechanism: str) -> Optional[str]:
    return MECHANISM_CHOKEPOINT.get(mechanism)


def triggers_at(chokepoint: str) -> Tuple[str, ...]:
    """Every property some control at this chokepoint fires on.

    THIS IS THE ROUTING. A control declares `fires_on`, and that declaration is what makes
    the property consulted there — a pack does not declare the route twice. Before this,
    a pack shipping a control but not the `enforced_by` shorthand had its property left
    unconsulted, so its control could never fire: two declarations for one fact, and the
    one that mattered was the one nobody wrote.
    """
    return tuple(sorted({c.fires_on for c in controls_at(chokepoint) if c.fires_on}))


#: What each act does, in the operator's words — one declaration, so the studio, the
#: refusal text and the audit record cannot describe the same act differently.
ACT_WORDS: Dict[str, str] = {
    "withhold": "can withhold the answer",
    "alter": "can alter the answer",
    "annotate": "adds a note to the answer",
}

#: The LADDER, and it is the platform's. A pack declares the act it wants at `enforce`;
#: every weaker posture is derived from that, never declared. This is what stops a pack
#: shipping "always withhold, whatever the operator set".
_LADDER = {
    "off": lambda declared: None,
    "observe": lambda declared: None,          # records what it WOULD have done
    "annotate": lambda declared: "annotate",   # never stronger than a note
    "enforce": lambda declared: declared,
    "review": lambda declared: declared,       # + parking, handled by the runtime
}


@dataclass(frozen=True)
class Control:
    """One control. Pure data — the policy is a declaration, never a callable."""

    key: str                 # `<pack>.<local>` for third-party; bare for platform
    label: str
    station: str             # which guarantee it upholds
    attaches_to: str         # a CHOKEPOINT
    fires_on: str            # the property whose finding triggers it
    act: str                 # what it does at `enforce` — from ACTS
    description: str = ""
    version: str = ""

    @property
    def origin(self) -> str:
        return self.key.split(".", 1)[0] if "." in self.key else "platform"

    @property
    def is_third_party(self) -> bool:
        return "." in self.key

    def describe(self) -> Dict[str, object]:
        return {
            "key": self.key, "label": self.label or self.key, "station": self.station,
            "attaches_to": self.attaches_to, "fires_on": self.fires_on,
            "act": self.act, "act_words": ACT_WORDS.get(self.act, self.act),
            "description": self.description, "version": self.version,
            "origin": self.origin, "third_party": self.is_third_party,
        }


_LOCK = threading.RLock()
_CONTROLS: Dict[str, Control] = {}


def register(c: Control) -> List[str]:
    """Announce a control. Returns refusal reasons; empty = accepted."""
    problems: List[str] = []
    if not c.key:
        problems.append("a control must declare a key")
    if c.act not in ACTS:
        problems.append(
            f"{c.key!r} declares act {c.act!r}; the acts are {', '.join(ACTS)} — a new "
            f"kind of effect needs its own rendering and audit shape, so it is a platform "
            f"change rather than a pack")
    if c.attaches_to not in CHOKEPOINTS:
        problems.append(
            f"{c.key!r} attaches to {c.attaches_to!r}; a control attaches where the "
            f"platform already stands ({', '.join(CHOKEPOINTS)})")
    if c.is_third_party and "." not in c.key.split(".", 1)[1] and not c.key.split(".", 1)[1]:
        problems.append(f"{c.key!r} is not a usable namespaced key")
    if not c.fires_on:
        problems.append(f"{c.key!r} names no property to fire on")
    if problems:
        return problems
    with _LOCK:
        _CONTROLS[c.key] = c
    return []


def get(key: str) -> Optional[Control]:
    return _CONTROLS.get(key)


def controls(station: Optional[str] = None) -> List[Control]:
    out = [c for c in _CONTROLS.values() if station is None or c.station == station]
    return sorted(out, key=lambda c: c.key)


def controls_at(chokepoint: str) -> List[Control]:
    return sorted((c for c in _CONTROLS.values() if c.attaches_to == chokepoint),
                  key=lambda c: c.key)


def unregister(prefix: str) -> None:
    with _LOCK:
        for k in [k for k in _CONTROLS if k == prefix or k.startswith(prefix + ".")]:
            _CONTROLS.pop(k, None)


def reset_for_tests() -> None:
    with _LOCK:
        _CONTROLS.clear()


# ── the policy, applied ────────────────────────────────────────────────────
def effective_act(control: Control, posture: str) -> Optional[str]:
    """What this control does at this posture, or `None` for nothing.

    The pack declares the act it wants at `enforce`; the platform derives every weaker
    posture. An unrecognised posture resolves to `observe` — the direction that RECORDS
    rather than acts, because a typo must never silently grant a control more power than
    the operator gave it.
    """
    fn = _LADDER.get(str(posture or "observe").lower())
    if fn is None:
        logger.warning("assurance: control %s has unknown posture %r — treated as "
                       "observe (records, never acts)", control.key, posture)
        return None
    return fn(control.act)


DEFAULT_POSTURE = "observe"


def posture(key: str, db_path=None) -> str:
    """The operator's posture for this control. DORMANT ON ARRIVAL.

    A third-party control that nobody has configured is `observe`: it records what it
    would have done and changes nothing. That is the property that makes shipping a
    control safe to allow at all — the decision to let it act is an operator's, made on a
    surface that says so, not a consequence of installing something.
    """
    try:
        from . import store as _store
        v = _store.control_posture(key, db_path=db_path)
        return v or DEFAULT_POSTURE
    except Exception:  # noqa: BLE001 — unreadable config must not grant power
        return DEFAULT_POSTURE


def fired(control: Control, findings) -> bool:
    """Did this control's trigger property produce an actionable finding this turn?"""
    for f in findings or ():
        if getattr(f, "property", None) == control.fires_on and getattr(f, "triggered", False) \
                and not getattr(f, "abstained", None):
            return True
    return False


def resolve(chokepoint: str, findings, db_path=None) -> List[Tuple[Control, str]]:
    """Every `(control, act)` that should take effect here, this turn.

    A control whose posture yields no act is deliberately absent from this list rather
    than present with `None`: the caller acts on what it is given, and an entry that means
    "do nothing" is one `if` away from meaning "do something".
    """
    out: List[Tuple[Control, str]] = []
    for c in controls_at(chokepoint):
        try:
            if not fired(c, findings):
                continue
            act = effective_act(c, posture(c.key, db_path=db_path))
            if act:
                out.append((c, act))
        except Exception as exc:  # noqa: BLE001 — one bad control must not break the turn
            logger.warning("assurance: control %s skipped (%s)", c.key, exc)
    return out
