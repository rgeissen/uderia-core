"""Shared builders for the generators — every shape emits the same node/edge/slot
shapes, so the renderer, validation and the executor meet one vocabulary."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .registry import TEMPLATE_VERSION

BRIEF_PARAM = "task"   # the one global parameter every shape reads: the user's brief


def nid(prefix: str = "node") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def node(ntype: str, label: str, data: Optional[Dict[str, Any]] = None, _id: Optional[str] = None) -> Dict[str, Any]:
    return {"id": _id or nid(), "type": ntype, "label": label,
            "position": {"x": 0, "y": 0}, "data": dict(data or {})}


def slot_node(role: str, label: str, prompt: str, slot_row: Dict[str, Any],
              output_as: Optional[str] = None, ntype: str = "task_llm") -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "prompt": prompt,
        "slot": {"role": role, "required": True,
                 "accepts": list(slot_row.get("accepts") or []),
                 "hint": slot_row.get("hint", "")},
    }
    if output_as:
        data["output_as"] = output_as
    return node(ntype, label, data)


def edge(src: str, tgt: str, etype: str = "normal", condition: Optional[str] = None) -> Dict[str, Any]:
    e: Dict[str, Any] = {"id": nid("edge"), "source": src, "target": tgt, "type": etype}
    if condition is not None:
        e["condition"] = condition
    return e


def definition(nodes: List[Dict], edges: List[Dict], key: str, params: Dict[str, Any],
               scopes: Optional[List[Dict]] = None, settings: Optional[Dict[str, Any]] = None,
               global_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    gp = {BRIEF_PARAM: ""}
    gp.update(global_params or {})
    s = {"autoLayout": True}
    s.update(settings or {})
    return {
        "nodes": nodes, "edges": edges, "scopes": list(scopes or []),
        "global_params": gp, "settings": s,
        # Copy, never link: the stamp records what generated this flow and nothing
        # re-reads it to re-apply a changed template.
        "template": {"id": key, "version": TEMPLATE_VERSION, "params": dict(params)},
    }


def slot_rows(template: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {s["role"]: s for s in template.get("slots", [])}
