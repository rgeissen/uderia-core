"""
Pre-fill from a coordinator's experts — PROP-007 §7, the three rules:

  symmetric roles   filled in the coordinator's declared `slaveProfiles` order; the
                    template's ADAPTING parameter takes the expert count (clamped);
                    surplus experts are listed as not placed, never dropped silently
  asymmetric roles  NEVER filled by order — each open slot offers one suggestion
                    chip per admissible expert (class ∈ accepts); the choice is the
                    consumer's
  judge             always the eval judge panel

A pre-fill is a FILL, not a link: the flow does not remember the coordinator.
Pure: profiles arrive as plain dicts {id, tag, profile_type}.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import registry
from .slots import JUDGE_PANEL, slot_of, slot_fill


def experts_of(coordinator: Dict[str, Any], profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The coordinator's experts in DECLARED order (`genieConfig.slaveProfiles`),
    resolved against the user's profiles; an id with no profile is skipped."""
    if not isinstance(coordinator, dict) or coordinator.get("profile_type") != "genie":
        return []
    ids = ((coordinator.get("genieConfig") or {}).get("slaveProfiles") or [])
    by_id = {p.get("id"): p for p in profiles if isinstance(p, dict)}
    return [by_id[i] for i in ids if i in by_id]


def adapt_params(template: Dict[str, Any], n_experts: int, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The adapting parameter follows the expert count (clamped to its range);
    every other parameter keeps its default or the caller's value."""
    out = registry.clamp_params(template, params)
    ap = registry.adapting_parameter(template)
    if ap and n_experts > 0:
        out[ap["name"]] = max(ap["min"], min(ap["max"], int(n_experts)))
    return out


def apply_prefill(definition: Dict[str, Any], template: Dict[str, Any],
                  experts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write the fills the three rules allow; return the report the picker shows.

    {filled: [{node_id, role, profile_id, tag}], suggestions: {node_id: [{id, tag}]},
     not_placed: [{id, tag}], source_count: n}"""
    report: Dict[str, Any] = {"filled": [], "suggestions": {}, "not_placed": [], "source_count": len(experts)}
    kinds = {s["role"]: s.get("kind") for s in template.get("slots", [])}
    nodes = (definition or {}).get("nodes", []) or []
    sym_nodes = [n for n in nodes if kinds.get((slot_of(n) or {}).get("role")) == registry.SYMMETRIC]
    used = 0
    for n, p in zip(sym_nodes, experts):
        slot = slot_of(n)
        accepts = slot.get("accepts") or []
        if accepts and p.get("profile_type") not in accepts:
            # Class mismatch on a symmetric slot: not placed, said so; never forced.
            report["not_placed"].append({"id": p.get("id"), "tag": p.get("tag"), "reason": "class not accepted"})
            continue
        n.setdefault("data", {})["profile_id"] = p.get("id")
        report["filled"].append({"node_id": n.get("id"), "role": slot.get("role"),
                                 "profile_id": p.get("id"), "tag": p.get("tag")})
        used += 1
    for p in experts[len(sym_nodes):]:
        report["not_placed"].append({"id": p.get("id"), "tag": p.get("tag"), "reason": "no slot left"})
    for n in nodes:
        slot = slot_of(n)
        if not slot:
            continue
        kind = kinds.get(slot.get("role"))
        if kind == registry.JUDGE and not slot_fill(n):
            n.setdefault("data", {})["profile_id"] = JUDGE_PANEL
            report["filled"].append({"node_id": n.get("id"), "role": slot.get("role"),
                                     "profile_id": JUDGE_PANEL, "tag": None})
        elif kind == registry.ASYMMETRIC and not slot_fill(n):
            accepts = slot.get("accepts") or []
            report["suggestions"][n.get("id")] = [
                {"id": p.get("id"), "tag": p.get("tag")}
                for p in experts if not accepts or p.get("profile_type") in accepts
            ]
    return report
