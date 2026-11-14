"""
Slots — PROP-007 §4. A slot is a FIELD on an ordinary node (`data.slot`), never a
node type, so every existing code path sees a normal node once it is filled.

    data.slot = {"role": "debater", "required": true,
                 "accepts": ["llm_only", ...], "hint": "an agent that ..."}

Filling writes `data.profile_id` and removes nothing (the role stays visible, the
accepts list stays checkable, Save-as-template can re-open it).

The safety rule (§4.2) lives here as pure functions the executor, the REST layer
and the tool handler all call: a REQUIRED slot whose profile is unset — or whose
profile reference resolves to nothing — is OPEN, and an open slot is refused
before the profile cascade can fall back to a default. The fallback is right for
an ordinary node the author left blank; it is wrong for a slot, which is a
promise to the consumer that THIS role runs under an agent THEY chose.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

#: Fill value meaning "the eval judge panel" (not a profile). Only judge slots take it.
JUDGE_PANEL = "judge_panel"

SLOT_NODE_TYPES = ("task_llm", "task_prompt")


class FlowSlotUnfilledError(ValueError):
    """A run (or a node) was asked to execute with a REQUIRED slot still open.

    Subclasses ValueError so a caller unaware of slots degrades to its existing
    400/error path rather than a 500; aware callers catch it first and answer
    recovery-shaped (409 `flow_slots_unfilled` + the open-slot list)."""

    def __init__(self, open_slots: List[Dict[str, Any]], flow_id: Optional[str] = None):
        self.open_slots = list(open_slots)
        self.flow_id = flow_id
        super().__init__(slot_refusal_message(self.open_slots))


class FlowTemplateUnavailable(ValueError):
    """`generate()` was asked for a template whose `requires` are not shipped, or
    for a hand-off row (swarm). The message is the picker's greyed-card sentence."""


def slot_of(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = node.get("data") if isinstance(node, dict) else None
    slot = data.get("slot") if isinstance(data, dict) else None
    return slot if isinstance(slot, dict) else None


def is_required_slot(node: Dict[str, Any]) -> bool:
    slot = slot_of(node)
    return bool(slot) and bool(slot.get("required", True))


def slot_fill(node: Dict[str, Any]) -> Optional[str]:
    """The slot's current fill: a profile reference or JUDGE_PANEL, else None."""
    data = node.get("data") or {}
    v = data.get("profile_id")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def slot_is_open(node: Dict[str, Any], resolver: Optional[Callable[[str], Optional[str]]] = None) -> bool:
    """A required slot is open when it has no fill — or, with a resolver, when its
    fill resolves to nothing (a deleted profile is an open slot, not a default)."""
    if not is_required_slot(node):
        return False
    fill = slot_fill(node)
    if not fill:
        return True
    if fill == JUDGE_PANEL:
        return JUDGE_PANEL not in (slot_of(node).get("accepts") or [])
    if resolver is not None:
        return resolver(fill) is None
    return False


def describe_slot(node: Dict[str, Any]) -> Dict[str, Any]:
    slot = slot_of(node) or {}
    return {
        "node_id": node.get("id"),
        "label": node.get("label") or node.get("id"),
        "role": slot.get("role"),
        "accepts": list(slot.get("accepts") or []),
        "hint": slot.get("hint", ""),
        "required": bool(slot.get("required", True)),
        "profile_id": slot_fill(node),
    }


def open_slots(definition: Dict[str, Any], resolver: Optional[Callable[[str], Optional[str]]] = None,
               only_node_ids: Optional[set] = None) -> List[Dict[str, Any]]:
    """Every required slot that is open, in node order (the checklist the canvas
    counts down). `only_node_ids` restricts to a partial run's targets."""
    out = []
    for n in (definition or {}).get("nodes", []) or []:
        if only_node_ids is not None and n.get("id") not in only_node_ids:
            continue
        if slot_is_open(n, resolver):
            out.append(describe_slot(n))
    return out


def all_slots(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [describe_slot(n) for n in (definition or {}).get("nodes", []) or [] if slot_of(n)]


def slot_refusal_message(slots: List[Dict[str, Any]]) -> str:
    """Recovery-shaped: names each open slot, its role and what fills it."""
    if not slots:
        return "Flow has unfilled slots."
    parts = []
    for s in slots:
        acc = ", ".join(s.get("accepts") or []) or "any agent"
        parts.append(f"'{s.get('label')}' (role: {s.get('role')}; accepts: {acc})")
    n = len(slots)
    return (f"Flow cannot run — {n} required slot{'s' if n != 1 else ''} still open: "
            + "; ".join(parts)
            + ". Fill each slot with an agent (set the node's profile_id to the agent's @TAG) and run again.")


def apply_fills(definition: Dict[str, Any], fills: Optional[Dict[str, Any]],
                resolver: Optional[Callable[[str], Optional[str]]] = None) -> Dict[str, Any]:
    """Write fills onto slots. `fills` is keyed by slot NODE ID or by ROLE:

        {"node-abc": "@OPTIM"}           one slot
        {"debater": ["@A", "@B", "@C"]}  symmetric role, in slot order
        {"synthesiser": "@S"}            every unfilled slot of that role

    A reference is passed through `resolver` (tag → id) when given; an
    unresolvable reference is NOT written (it would be an open slot wearing a
    fill's clothes) and is reported under `rejected`. Returns a report:
    {filled: [node_id…], rejected: [{ref, reason}…], ignored_keys: [...]}."""
    report = {"filled": [], "rejected": [], "ignored_keys": []}
    if not fills or not isinstance(fills, dict):
        return report
    nodes = (definition or {}).get("nodes", []) or []
    by_id = {n.get("id"): n for n in nodes}

    def _resolve(ref: Any) -> Optional[str]:
        if not isinstance(ref, str) or not ref.strip():
            return None
        ref = ref.strip()
        if ref == JUDGE_PANEL:
            return ref
        return resolver(ref) if resolver is not None else ref

    def _write(node: Dict[str, Any], ref: Any) -> None:
        slot = slot_of(node)
        if not slot:
            return
        resolved = _resolve(ref)
        if resolved is None:
            report["rejected"].append({"node_id": node.get("id"), "ref": ref,
                                       "reason": "no such agent"})
            return
        if resolved == JUDGE_PANEL and JUDGE_PANEL not in (slot.get("accepts") or []):
            report["rejected"].append({"node_id": node.get("id"), "ref": ref,
                                       "reason": "this slot does not take the judge panel"})
            return
        node.setdefault("data", {})["profile_id"] = resolved
        report["filled"].append(node.get("id"))

    for key, value in fills.items():
        if key in by_id and slot_of(by_id[key]):
            _write(by_id[key], value)
            continue
        role_nodes = [n for n in nodes if (slot_of(n) or {}).get("role") == key]
        if not role_nodes:
            report["ignored_keys"].append(key)
            continue
        if isinstance(value, (list, tuple)):
            # A list fills the role's slots IN ORDER; an explicit None/"" entry means
            # "leave this one open" (the picker sends the whole checklist, holes
            # included — node ids are minted per generation, so a role list is the
            # only stable way to address slots on a flow that does not exist yet).
            for node, ref in zip(role_nodes, value):
                if ref is None or (isinstance(ref, str) and not ref.strip()):
                    continue
                _write(node, ref)
        else:
            for node in role_nodes:
                if not slot_fill(node):
                    _write(node, value)
    return report


def template_stamp(definition: Dict[str, Any]) -> Dict[str, Any]:
    t = (definition or {}).get("template")
    return t if isinstance(t, dict) else {}


def decorate_flow_record(flow: Dict[str, Any], resolver: Optional[Callable[[str], Optional[str]]] = None) -> Dict[str, Any]:
    """Surface the template identity + the open-slot checklist on a flow record
    (GET /v1/flows/<id>, the list, the tool payloads). Pure; adds keys only."""
    if not isinstance(flow, dict):
        return flow
    definition = flow.get("definition") if isinstance(flow.get("definition"), dict) else {}
    stamp = template_stamp(definition)
    if stamp:
        flow["template_id"] = stamp.get("id")
        flow["template_version"] = stamp.get("version")
    slots = all_slots(definition)
    if slots:
        opened = open_slots(definition, resolver)
        flow["slots"] = slots
        flow["open_slots"] = opened
        flow["open_slot_count"] = len(opened)
    return flow
