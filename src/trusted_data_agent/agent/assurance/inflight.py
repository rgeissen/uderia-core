"""
The IN-FLIGHT seam — where a bound module reaches a gate, and can move an answer.

Without this, the plane is half a plane: a third party could measure a turn after it
shipped and never affect one. `PROP-003` shipped with only two properties consulted in
flight, and the studio said so on every other cell — honest, but it made the in-flight
lane a decoration.

    augment(mechanism, own_properties, ctx) -> [Finding, ...]

ONE call per gate. Returns what a bound module CONTRIBUTES; the gate then applies its own
policy to the union of its native findings and these. There is no return value that can
REMOVE a native finding — the "a plugin may tighten, never loosen" invariant is structural
here rather than remembered at six call sites.

────────────────────────────────────────────────────────────────────────────────
SIBLINGS — the shape this is FOR
────────────────────────────────────────────────────────────────────────────────
The intended third-party model is not "replace the platform's PII scan". It is:

    a pack declares its OWN property        acme.phi — "does the answer carry PHI as
                                            our jurisdiction defines it?"
    binds ONE module to BOTH lanes          in flight the gate acts on it; afterwards
                                            the scorer grades the SAME computation
    → registry.symmetry() derives `shared`  → the Guide draws the S badge

That is the substrate's own model — one assessment, two lanes — and it is what makes a
third-party capability trustworthy: the thing that withheld the answer and the thing that
graded it are not two implementations that agree today, they are one that cannot disagree.

It also dissolves the question that would otherwise have to be answered ("may a plugin
replace the platform's own detector, and therefore weaken it?"): a pack ADDS a property,
so nothing native is displaced. A module rebound onto a PLATFORM property is still
permitted, but it is treated as an addition to that mechanism's own assessment, never as a
substitution for it — see `_is_shipped`.

────────────────────────────────────────────────────────────────────────────────
WHAT THIS DOES NOT DO
────────────────────────────────────────────────────────────────────────────────
It returns findings. It never decides. The posture ladder, the redaction, the withholding,
the parking, the event and the rendering stay exactly where they were — which is the whole
of Tenet A, and the reason a third-party finding is safe to act on at all.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

from .capabilities import Lane
from .contract import AssessmentContext, Finding

logger = logging.getLogger("quart.app")

#: pack property -> the MECHANISM whose gate consults it in flight.
#:
#: Populated by the module manager from each property's `enforced_by`. A closed set: the
#: value must name a mechanism that already exists, so a pack cannot invent a gate — it can
#: only ask an existing one to look at one more thing.
_ENFORCED_BY: Dict[str, str] = {}


def route_property(property_key: str, mechanism: str) -> None:
    """Declare that a gate should consult this pack property in flight."""
    _ENFORCED_BY[property_key] = mechanism


def clear_routes(prefix: str = "") -> None:
    for k in [k for k in _ENFORCED_BY if not prefix or k.startswith(prefix)]:
        _ENFORCED_BY.pop(k, None)


def routed_to(mechanism: str) -> Tuple[str, ...]:
    return tuple(sorted(k for k, v in _ENFORCED_BY.items() if v == mechanism))


def consulted_properties(mechanism: str, own: Sequence[str]) -> Tuple[str, ...]:
    """Every property this mechanism consults in flight.

    Three sources, and the third is the one that keeps the model consistent:
      * what the mechanism assesses natively (`own`)
      * a property routed by the `enforced_by` shorthand
      * **a property some CONTROL at this chokepoint fires on** — a control's `fires_on`
        IS its routing, so a pack declares the fact once. Without this, a pack that
        shipped a control but not the shorthand had its property left unconsulted and its
        control could never fire.
    """
    out = list(own)
    for k in routed_to(mechanism):
        if k not in out:
            out.append(k)
    try:
        from . import controls as _c
        cp = _c.chokepoint_of(mechanism)
        if cp:
            for k in _c.triggers_at(cp):
                if k not in out:
                    out.append(k)
    except Exception:  # noqa: BLE001
        pass
    return tuple(out)


def _is_shipped(assessor_key: str, prop: str) -> bool:
    """Is this the platform's own module for a platform property?

    If so the gate has ALREADY performed that assessment natively, and running it again
    here would double-count one finding. Only a THIRD-PARTY module (namespaced, so it
    carries a dot) contributes through this seam.
    """
    return "." not in (assessor_key or "") and "." not in (prop or "")


def own_properties_for(mechanism: str) -> Tuple[str, ...]:
    """What this mechanism assesses that a plugin may ADD to — DERIVED, never listed.

    `POINT_PROPERTIES` is already the one declaration of which mechanism owns which
    property; a literal at each gate was a second copy of that fact, and the copy would go
    stale silently — a property added to the registry simply would not be consulted in
    flight, which is precisely the invisible gap this seam exists to close.

    Minus the properties the gate RESOLVES ITSELF (in `INFLIGHT_CONSUMERS` but not in
    `SEAM_CONSULTED`): `context_use` is the grounding gate's own assessment, so routing it
    through here as well would ask one module twice on one turn.

    The import is deferred — `assurance_points` imports THIS package, so a module-level
    dependency would be a cycle; both directions are lazy, which is why this works.
    """
    try:
        from trusted_data_agent.agent.assurance_points import POINT_PROPERTIES
        from . import INFLIGHT_CONSUMERS, SEAM_CONSULTED
    except Exception:  # noqa: BLE001
        return ()
    direct = {p for p, m in INFLIGHT_CONSUMERS.items()
              if m == mechanism and p not in SEAM_CONSULTED}
    return tuple(p for p in POINT_PROPERTIES.get(mechanism, ()) if p not in direct)


def augment(mechanism: str, own_properties: Optional[Sequence[str]] = None,
            ctx: Optional[AssessmentContext] = None, *,
            lane: Lane = Lane.IN_FLIGHT) -> List[Finding]:
    """Third-party findings this mechanism should consider, this turn.

    `own_properties` defaults to the DERIVED set (`own_properties_for`). A caller may pass
    one to narrow the scope — tests do — but no gate should: a literal there is a second
    copy of `POINT_PROPERTIES` and will drift from it.

    FAILS OPEN TO NOTHING. Every failure path returns `[]`, which means the gate behaves
    exactly as it did before this seam existed. A plugin layer that could break a gate by
    misbehaving would be a worse risk than the one it manages.
    """
    if own_properties is None:
        own_properties = own_properties_for(mechanism)
    if ctx is None:
        return []
    try:
        from . import registry as _reg
        from . import bootstrap as _bootstrap
        _bootstrap()
    except Exception as exc:  # noqa: BLE001
        logger.debug("assurance: in-flight augmentation unavailable (%s)", exc)
        return []

    out: List[Finding] = []
    for prop in consulted_properties(mechanism, own_properties):
        try:
            assessor = (_reg.resolve(prop, lane)
                        or _reg.resolve(prop, Lane.IN_FLIGHT_TRIGGERED))
            if assessor is None:
                continue
            if _is_shipped(assessor.key, prop):
                # The mechanism's own assessment, already made natively.
                continue
            f = assessor.run(ctx)          # the envelope turns any failure into abstain
            if f.abstained or not f.triggered:
                continue
            out.append(f)
        except Exception as exc:  # noqa: BLE001 — one bad module must not break the gate
            logger.warning("assurance: in-flight module for %s failed (%s) — the gate "
                           "proceeds on its own findings", prop, exc)
            continue
    return out


def summarize(findings: Sequence[Finding]) -> Optional[str]:
    """One line naming what the plugins found — for the gate's own event/reason text.

    Labels and summaries only. A Finding's evidence may quote the answer, and these strings
    reach surfaces that render them, so the privacy rule the guardrail envelope already
    enforces is enforced here too rather than trusted to each caller.
    """
    if not findings:
        return None
    parts = [f"{f.property} ({f.assessor})" for f in findings]
    return "third-party assessment: " + ", ".join(parts)
