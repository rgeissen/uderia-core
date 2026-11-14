"""The ONE reader of an execution-trace entry's output.

A turn's ``execution_trace`` is the executor's ``turn_action_history``: entries of the shape
``{"action": {"tool_name", "arguments", …}, "result": <the tool's output dict>}`` (see
``phase_executor`` / ``orchestrators`` — the appenders — and ``eval/types.RunRecord``). For a
long time four readers (the planner's hydration pass, the ``workflow_history`` and
``plan_hydration`` context modules, the session store's turn ingestion) read a phantom
``phase["tool_output_summary"]`` that NO producer ever wrote, so every phase read
``unknown (0 rows)`` and hydration never fired. The planner was fixed in place (its C3 note);
this module is that fix as ONE definition for every reader, with the same defensive fallback
for a legacy entry that nested its output under ``tool_output_summary``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def phase_output(entry: Any) -> Dict[str, Any]:
    """The output dict of one trace entry — ``{}`` when the entry carries none."""
    if not isinstance(entry, dict):
        return {}
    result = entry.get("result")
    if not isinstance(result, dict):
        legacy = entry.get("tool_output_summary")
        return legacy if isinstance(legacy, dict) else {}
    nested = result.get("tool_output_summary")
    return nested if isinstance(nested, dict) else result


def phase_action(entry: Any) -> Dict[str, Any]:
    """The action dict of one trace entry — ``{}`` when the entry carries none."""
    if not isinstance(entry, dict):
        return {}
    action = entry.get("action")
    return action if isinstance(action, dict) else {}


def phase_rows(entry: Any) -> Optional[list]:
    """The row list of a trace entry's output (``results`` · ``rows`` · ``data``), or None."""
    out = phase_output(entry)
    for key in ("results", "rows", "data"):
        v = out.get(key)
        if isinstance(v, list):
            return v
    return None
