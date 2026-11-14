"""Shared builders for the dialogic shapes (PROP-007 increment 3): a judge node, a
loop (scope) with a convergence exit, and the loop-back edge. Every shape closes its
loop the same way so the executor, the layout and the renderer meet one structure."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._common import edge, nid, node, slot_node
from .slots import JUDGE_PANEL
from .. import flow_rounds


def judge_node(role: str, label: str, slot_row: Dict[str, Any], material: str, rubric: str,
               output_as: str, instructions: Optional[str] = None, default_panel: bool = False) -> Dict[str, Any]:
    """A judge slot. `default_panel` pre-fills the eval judge panel — a sensible default
    that is not an agent choice, so a consumer never has to fill it to run."""
    n = slot_node(role, label, material, slot_row, output_as=output_as, ntype="judge")
    n["data"]["rubric"] = rubric
    if instructions:
        n["data"]["instructions"] = instructions
    if default_panel and JUDGE_PANEL in (slot_row.get("accepts") or []):
        n["data"]["profile_id"] = JUDGE_PANEL
    return n


def loop(name: str, members: List[Dict[str, Any]], back_from: Dict[str, Any], back_to: Dict[str, Any],
         max_rounds: int, exit_node: Dict[str, Any], pass_threshold: Optional[float] = None,
         history_window: Optional[int] = None):
    """(scope, back_edge). The back edge is `type: cycle` — the layout ignores it, the
    executor never activates it (the scope runs its rounds), the canvas draws it dashed."""
    back = edge(back_from["id"], back_to["id"], etype="cycle")
    scope = {
        "id": nid("scope"), "name": name,
        "node_ids": [m["id"] for m in members],
        "back_edge_id": back["id"],
        "max_iterations": int(max_rounds),
        "exit_when": {"node": exit_node["id"], "condition": flow_rounds.threshold_condition(pass_threshold)},
    }
    # How many earlier rounds a participant is shown inside the loop (None = all).
    # A node after the loop always reads the whole transcript.
    if history_window:
        scope["history_window"] = int(history_window)
    return scope, back


__all__ = ["judge_node", "loop", "edge", "node", "slot_node"]
