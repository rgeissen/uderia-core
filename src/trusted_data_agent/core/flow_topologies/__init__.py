"""
Flow topology templates — PROP-007. ONE generator, three callers (picker · REST ·
TDA_Flow). See registry.py for the catalog, slots.py for the safety rule.

    generate(key, params, fills=None, resolver=None) → (definition, report)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from . import registry
from .registry import (TEMPLATES, TEMPLATE_VERSION, catalog, get_template, template_keys,
                       is_available, unavailable_reason, clamp_params, default_params)
from .slots import (FlowSlotUnfilledError, FlowTemplateUnavailable, JUDGE_PANEL, open_slots,
                    all_slots, apply_fills, decorate_flow_record, slot_of, slot_is_open,
                    slot_refusal_message, template_stamp)
from .prefill import experts_of, adapt_params, apply_prefill
from .recent import add_recent_suggestions, recent_agents

from . import pipeline as _pipeline, parallel as _parallel, fork_join as _fork_join, \
    conditional as _conditional, blank as _blank, iterative as _iterative, debate as _debate, \
    teacher_student as _teacher_student, pair_programming as _pair_programming

_GENERATORS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = {
    "pipeline": _pipeline.generate,
    "parallel": _parallel.generate,
    "fork-join": _fork_join.generate,
    "conditional": _conditional.generate,
    "iterative": _iterative.generate,
    "debate": _debate.generate,
    "teacher-student": _teacher_student.generate,
    "pair-programming": _pair_programming.generate,
    "blank": _blank.generate,
}


def generate(key: str, params: Optional[Dict[str, Any]] = None, fills: Optional[Dict[str, Any]] = None,
             resolver: Optional[Callable[[str], Optional[str]]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate a skeleton for `key` with `params` (clamped), then apply `fills`.

    Raises FlowTemplateUnavailable for an unknown key, an unshipped shape or a
    hand-off row — the message is the one the picker shows on the greyed card.
    Returns (definition, fill_report). The definition has positions at (0,0);
    callers lay it out (create_flow does) — the generator stays pure."""
    t = get_template(key)
    if t is None:
        raise FlowTemplateUnavailable(f"unknown topology '{key}'. Known: {', '.join(template_keys())}")
    reason = unavailable_reason(t)
    if reason:
        raise FlowTemplateUnavailable(f"topology '{t['key']}' — {reason}")
    gen = _GENERATORS.get(t["key"])
    if gen is None:  # a row marked available with no generator is a registry defect
        raise FlowTemplateUnavailable(f"topology '{t['key']}' has no generator")
    p = clamp_params(t, params)
    definition = gen(t, p)
    report = apply_fills(definition, fills, resolver)
    return definition, report


def preview(key: str, params: Optional[Dict[str, Any]] = None,
            source: Optional[Dict[str, Any]] = None, profiles: Optional[list] = None,
            touched: Optional[set] = None, recent: Optional[list] = None) -> Dict[str, Any]:
    """The picker's live preview — PROP-007 §6/§7, ONE implementation shared with the
    tool handler's pre-fill: the skeleton for `key` with the pre-fill a `source`
    profile would give, and nothing created.

    `source` is the profile the picker was opened from (a plain dict). A coordinator
    pre-fills by the three rules (`apply_prefill`), and its expert count sets the
    adapting parameter — UNLESS the consumer has touched that parameter (`touched`),
    in which case their value stands. A non-coordinator source fills nothing by
    itself and is offered as a suggestion on every slot that accepts its class.

    Returns {template, params, definition, prefill, slots, open_slots}. Positions
    are (0,0); the route lays the skeleton out. Raises FlowTemplateUnavailable as
    `generate()` does."""
    t = get_template(key)
    if t is None:
        raise FlowTemplateUnavailable(f"unknown topology '{key}'. Known: {', '.join(template_keys())}")
    reason = unavailable_reason(t)
    if reason:
        raise FlowTemplateUnavailable(f"topology '{t['key']}' — {reason}")
    experts = experts_of(source, profiles or []) if source else []
    touched = set(touched or ())
    ap = registry.adapting_parameter(t)
    if experts and ap and ap["name"] not in touched:
        p = adapt_params(t, len(experts), params)
    else:
        p = clamp_params(t, params)
    definition = _GENERATORS[t["key"]](t, p)
    report = apply_prefill(definition, t, experts) if experts else {
        "filled": [], "suggestions": {}, "not_placed": [], "source_count": 0}
    if source and not experts and source.get("profile_type") != "genie":
        # A single agent is a suggestion, never a fill by order (the consumer decides).
        for n in definition.get("nodes", []):
            slot = slot_of(n)
            if not slot or n.get("data", {}).get("profile_id"):
                continue
            accepts = slot.get("accepts") or []
            if not accepts or source.get("profile_type") in accepts:
                report["suggestions"][n["id"]] = [{"id": source.get("id"), "tag": source.get("tag")}]
    # Recently used agents: suggestions on open slots only, after the source's own.
    add_recent_suggestions(definition, report["suggestions"], recent)
    return {
        "template": next(r for r in catalog() if r["key"] == t["key"]),
        "params": p,
        "definition": definition,
        "prefill": report,
        "slots": all_slots(definition),
        "open_slots": open_slots(definition),
    }


def default_name(key: str, source_tag: Optional[str] = None) -> str:
    """Name, don't ask: `<Topology> · <tag>` or just the topology."""
    t = get_template(key) or {"label": key}
    return f"{t['label']} · @{source_tag.lstrip('@')}" if source_tag else t["label"]


__all__ = [
    "recent_agents", "add_recent_suggestions",
    "TEMPLATES", "TEMPLATE_VERSION", "catalog", "get_template", "template_keys", "is_available",
    "unavailable_reason", "clamp_params", "default_params", "generate", "default_name",
    "FlowSlotUnfilledError", "FlowTemplateUnavailable", "JUDGE_PANEL", "open_slots", "all_slots",
    "apply_fills", "decorate_flow_record", "slot_of", "slot_is_open", "slot_refusal_message",
    "template_stamp", "experts_of", "adapt_params", "apply_prefill", "preview", "registry",
]
