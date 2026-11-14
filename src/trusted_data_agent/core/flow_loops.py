"""Loop (scope) editing — the ONE set of rules for making, changing and removing a loop.

A loop is a scope: a set of member nodes that run as rounds, closed by a back edge
(`type: "cycle"`) from the member that finishes a round to the member that starts it,
bounded by `max_iterations`, optionally ended early by `exit_when = {node, condition}`
and handed a bounded transcript of earlier rounds (`history_window`).

The topology templates (PROP-007) build loops through `flow_topologies._loop`; a user
builds one on the canvas from selected nodes; the TDA_Flow tool may send a whole scope.
All three meet `apply_patch`, which routes every loop op through this module, so a loop
made by hand obeys exactly the rules a template loop does. Pure: no I/O.

Every refusal is a `LoopError` whose message names the problem and how to fix it.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from trusted_data_agent.core import flow_rounds
from trusted_data_agent.core.safe_expression import safe_eval

# The round-limit range — the canvas stepper offers the same (renderer.js SCOPE_*_ROUNDS).
SCOPE_MIN_ROUNDS, SCOPE_MAX_ROUNDS = 1, 20
# How many earlier rounds a participant may be shown (None = all of them).
HISTORY_MIN, HISTORY_MAX = 1, 20
DEFAULT_ROUNDS = 3

# Node types that can never sit inside a loop.
_NOT_LOOPABLE = {"start", "end"}
# Node types whose output carries a verdict (`passed`, `score`) an exit can read.
VERDICT_TYPES = {"judge"}


class LoopError(ValueError):
    """A loop edit that cannot be applied. `code` is machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def clamp_rounds(value: Any) -> int:
    """A loop's round limit as an int inside the accepted range; unreadable -> the minimum."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = SCOPE_MIN_ROUNDS
    return max(SCOPE_MIN_ROUNDS, min(SCOPE_MAX_ROUNDS, n))


def clamp_history(value: Any) -> Optional[int]:
    """None / 0 / blank = every earlier round; otherwise 1..HISTORY_MAX."""
    if value in (None, "", 0, "0", "all"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return max(HISTORY_MIN, min(HISTORY_MAX, n))


def _nodes(definition: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {n["id"]: n for n in definition.get("nodes", []) or [] if isinstance(n, dict) and n.get("id")}


def _scope_of(definition: Dict[str, Any], node_id: str) -> Optional[str]:
    for sc in definition.get("scopes", []) or []:
        if node_id in (sc.get("node_ids") or []):
            return sc.get("id")
    return None


def _label(n: Dict[str, Any]) -> str:
    return str(n.get("label") or n.get("id"))


def loop_shape(definition: Dict[str, Any], node_ids: List[str],
               ignore_scope: Optional[str] = None) -> Dict[str, Any]:
    """Validate a would-be loop's members and find where a round starts and ends.

    Returns {"members": [...in flow order], "entry": id, "exit": id}. A round must start
    at ONE member (the only one no member feeds) and end at ONE member (the only one that
    feeds no member) — that is where the back edge goes. One member is a loop of itself.
    """
    nodes = _nodes(definition)
    ids: List[str] = []
    for nid in node_ids or []:
        if nid not in ids:
            ids.append(nid)
    if not ids:
        raise LoopError("empty_loop", "Select at least one step to repeat.")
    missing = [i for i in ids if i not in nodes]
    if missing:
        raise LoopError("unknown_node", f"These steps do not exist in this flow: {', '.join(missing)}.")
    bad = [_label(nodes[i]) for i in ids if (nodes[i].get("type") or "") in _NOT_LOOPABLE]
    if bad:
        raise LoopError("not_loopable", f"Start and End cannot repeat — remove {', '.join(bad)} from the selection.")
    taken = [_label(nodes[i]) for i in ids if (_scope_of(definition, i) not in (None, ignore_scope))]
    if taken:
        raise LoopError("already_in_loop", f"{', '.join(taken)} already belong to another loop. Remove that loop first.")

    member = set(ids)
    internal = [e for e in definition.get("edges", []) or []
                if e.get("source") in member and e.get("target") in member and e.get("type") != "cycle"]
    if len(ids) == 1:
        return {"members": ids, "entry": ids[0], "exit": ids[0]}

    # Connected (ignoring direction) — a loop is one chain of work, not two islands.
    adj: Dict[str, set] = {i: set() for i in ids}
    for e in internal:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    seen, stack = {ids[0]}, [ids[0]]
    while stack:
        for nxt in adj[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    if seen != member:
        apart = [_label(nodes[i]) for i in ids if i not in seen]
        raise LoopError("not_connected",
                        f"The selected steps are not connected to each other ({', '.join(apart)} stand apart). "
                        "Select steps that follow one another.")

    has_in = {e["target"] for e in internal}
    has_out = {e["source"] for e in internal}
    entries = [i for i in ids if i not in has_in]
    exits = [i for i in ids if i not in has_out]
    if len(entries) != 1:
        names = ', '.join(_label(nodes[i]) for i in entries) or 'none'
        raise LoopError("ambiguous_entry",
                        f"A round must start at one step; these selected steps all start one: {names}. "
                        "Connect them so one step comes first.")
    if len(exits) != 1:
        names = ', '.join(_label(nodes[i]) for i in exits) or 'none'
        raise LoopError("ambiguous_exit",
                        f"A round must end at one step; these selected steps all end one: {names}. "
                        "Join them into one step (e.g. a Merge or a Judge) so the round ends once.")

    # Flow order: the order the nodes appear, entry first.
    order = [entries[0]] + [i for i in ids if i != entries[0]]
    return {"members": order, "entry": entries[0], "exit": exits[0]}


def default_exit_node(definition: Dict[str, Any], members: List[str]) -> Optional[str]:
    """The step whose verdict ends the loop by default: the last judge among the members
    (a reviewer that decides); none ⇒ the loop runs its fixed number of rounds."""
    nodes = _nodes(definition)
    judges = [m for m in members if (nodes.get(m, {}).get("type") in VERDICT_TYPES)]
    return judges[-1] if judges else None


def exit_condition(mode: Any = None, threshold: Any = None, expression: Any = None) -> str:
    """The exit condition from a mode: 'pass' (the reviewer passes the work), 'threshold'
    (passes AND scores at least `threshold`), or 'expression' (a custom condition over the
    verdict fields — validated before it is stored)."""
    if mode == "expression":
        cond = str(expression or "").strip()
        validate_condition(cond)
        return cond
    if mode == "threshold":
        try:
            t = float(threshold)
        except (TypeError, ValueError):
            raise LoopError("bad_threshold", "The pass score must be a number between 0 and 1.")
        if not 0.0 <= t <= 1.0:
            raise LoopError("bad_threshold", "The pass score must be a number between 0 and 1.")
        return flow_rounds.threshold_condition(t)
    return flow_rounds.threshold_condition(None)


def validate_condition(cond: str) -> None:
    """A custom exit condition must evaluate over a verdict without error."""
    if not cond:
        raise LoopError("empty_condition", "Write a condition, e.g. passed == True and score >= 0.8.")
    sample = {"passed": True, "score": 0.9, "verdict": "pass", "output": "", "feedback": ""}
    try:
        safe_eval(cond, dict(sample))
    except Exception as exc:  # noqa: BLE001 — any evaluation failure refuses the condition
        raise LoopError("bad_condition",
                        f"The condition could not be read ({exc}). Use the verdict fields "
                        "passed, score and verdict, e.g. passed == True and score >= 0.8.")


def _set_exit(definition: Dict[str, Any], scope: Dict[str, Any], exit_when: Any) -> None:
    if exit_when in (None, {}, False):
        scope.pop("exit_when", None)
        return
    if not isinstance(exit_when, dict):
        raise LoopError("bad_exit", "The stop rule must name a step and a condition.")
    node = exit_when.get("node")
    if node not in (scope.get("node_ids") or []):
        raise LoopError("exit_outside_loop", "The step that decides must be inside the loop.")
    nodes = _nodes(definition)
    cond = exit_when.get("condition")
    mode = exit_when.get("mode")
    custom = cond is not None or mode == "expression"
    # A pass / pass-score rule reads a verdict, which only a Judge gives; a custom
    # condition may read any field the step returns (a Function can return `passed`).
    if not custom and (nodes.get(node, {}).get("type") or "") not in VERDICT_TYPES:
        raise LoopError("exit_not_a_judge",
                        f"{_label(nodes.get(node, {'id': node}))} gives no verdict. Choose a Judge step, "
                        "or write a custom condition over the fields this step returns.")
    if cond is None:
        cond = exit_condition(mode, exit_when.get("threshold"), exit_when.get("expression"))
    else:
        cond = str(cond).strip()
        validate_condition(cond)
    scope["exit_when"] = {"node": node, "condition": cond}


def make_loop(definition: Dict[str, Any], node_ids: List[str], name: Optional[str] = None,
              max_rounds: Any = DEFAULT_ROUNDS, exit_when: Any = "default",
              history_window: Any = None, scope_id: Optional[str] = None) -> Dict[str, Any]:
    """Make the selected steps a loop: add the scope and its back edge. Mutates
    `definition` and returns the new scope. `exit_when="default"` picks the last judge."""
    shape = loop_shape(definition, node_ids)
    back = {"id": f"edge-{uuid.uuid4().hex[:8]}", "source": shape["exit"], "target": shape["entry"],
            "type": "cycle"}
    scope = {
        "id": scope_id or f"scope-{uuid.uuid4().hex[:8]}",
        "name": (str(name).strip() if name else "") or "Loop",
        "node_ids": shape["members"],
        "back_edge_id": back["id"],
        "max_iterations": clamp_rounds(max_rounds),
    }
    hw = clamp_history(history_window)
    if hw is not None:
        scope["history_window"] = hw
    if exit_when == "default":
        node = default_exit_node(definition, shape["members"])
        exit_when = {"node": node, "mode": "pass"} if node else None
    _set_exit(definition, scope, exit_when)
    definition.setdefault("edges", []).append(back)
    definition.setdefault("scopes", []).append(scope)
    return scope


def adopt_scope(definition: Dict[str, Any], scope: Dict[str, Any]) -> Dict[str, Any]:
    """A whole scope sent by the TDA_Flow tool: kept as sent where valid, completed where
    not (a missing back edge is added, the round limit clamped). Its members obey the
    same rules as a hand-made loop."""
    scope = dict(scope or {})
    shape = loop_shape(definition, scope.get("node_ids") or [])
    scope["id"] = scope.get("id") or f"scope-{uuid.uuid4().hex[:8]}"
    scope["name"] = scope.get("name") or "Loop"
    scope["node_ids"] = shape["members"]
    scope["max_iterations"] = clamp_rounds(scope.get("max_iterations", DEFAULT_ROUNDS))
    edge_ids = {e.get("id") for e in definition.get("edges", []) or []}
    if not scope.get("back_edge_id") or scope["back_edge_id"] not in edge_ids:
        back = {"id": scope.get("back_edge_id") or f"edge-{uuid.uuid4().hex[:8]}",
                "source": shape["exit"], "target": shape["entry"], "type": "cycle"}
        definition.setdefault("edges", []).append(back)
        scope["back_edge_id"] = back["id"]
    if "history_window" in scope:
        hw = clamp_history(scope.get("history_window"))
        if hw is None:
            scope.pop("history_window", None)
        else:
            scope["history_window"] = hw
    if scope.get("exit_when"):
        _set_exit(definition, scope, scope.get("exit_when"))
    definition.setdefault("scopes", []).append(scope)
    return scope


def update_loop(definition: Dict[str, Any], scope_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Change a loop's name, round limit, history window or stop rule. Other keys are
    refused rather than written blindly (a loop's members and back edge change only by
    removing and re-making it)."""
    scope = next((s for s in definition.get("scopes", []) or [] if s.get("id") == scope_id), None)
    if scope is None:
        raise LoopError("unknown_loop", f"There is no loop '{scope_id}' in this flow.")
    updates = dict(updates or {})
    allowed = {"name", "max_iterations", "history_window", "exit_when"}
    unknown = sorted(k for k in updates if k not in allowed)
    if unknown:
        raise LoopError("unsupported_update",
                        f"A loop's {', '.join(unknown)} cannot be edited in place — remove the loop and make it again.")
    if "name" in updates:
        scope["name"] = str(updates["name"] or "").strip() or "Loop"
    if "max_iterations" in updates:
        scope["max_iterations"] = clamp_rounds(updates["max_iterations"])
    if "history_window" in updates:
        hw = clamp_history(updates["history_window"])
        if hw is None:
            scope.pop("history_window", None)
        else:
            scope["history_window"] = hw
    if "exit_when" in updates:
        _set_exit(definition, scope, updates["exit_when"])
    return scope


def remove_loop(definition: Dict[str, Any], scope_id: str) -> bool:
    """Remove a loop: its steps stay, the back edge and the frame go."""
    scopes = definition.get("scopes", []) or []
    scope = next((s for s in scopes if s.get("id") == scope_id), None)
    if scope is None:
        return False
    back = scope.get("back_edge_id")
    definition["scopes"] = [s for s in scopes if s.get("id") != scope_id]
    if back:
        definition["edges"] = [e for e in definition.get("edges", []) or [] if e.get("id") != back]
    for node in definition.get("nodes", []) or []:
        if node.get("scope_id") == scope_id:
            node.pop("scope_id", None)
    return True


__all__ = ["LoopError", "clamp_rounds", "clamp_history", "loop_shape", "default_exit_node",
           "exit_condition", "validate_condition", "make_loop", "adopt_scope", "update_loop",
           "remove_loop", "SCOPE_MIN_ROUNDS", "SCOPE_MAX_ROUNDS", "HISTORY_MIN", "HISTORY_MAX",
           "VERDICT_TYPES"]
