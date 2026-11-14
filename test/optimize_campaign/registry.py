"""The Optimize lane's registry — EXTRACTED from the source, never hand-listed.

The lane's deterministic machinery is what a weak model is carried by: the rewrite passes
(`planner.py`), the safeguards and FASTPATH (`phase_executor.py`, `executor.py`) and the
orchestrators (`orchestrators.py`). Each must be provoked by at least one campaign case
(`coverage.py --strict`), so a pass added to the source without a case cannot hide — the
same rule the trust campaign applies to `assurance_points.REGISTRY`.

Two halves:
    REGISTRY   {key: Entry}   what exists, read from the source at import time
    fired(turn) -> set        which keys a persisted turn shows in its trace

A pass stamps `{"pass": <name>}` in a `TDA_SystemLog` detail when it CHANGED the plan;
safeguards and orchestrators stamp a `message` and a `summary`. The markers are the ones
the hardening skill's catalogue names (`.claude/skills/fusion-hardening/SKILL.md` §3).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Set

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AGENT = os.path.join(ROOT, "src", "trusted_data_agent", "agent")


@dataclass(frozen=True)
class Entry:
    key: str
    kind: str          # "pass" | "safeguard" | "orchestrator" | "fastpath"
    where: str         # source file
    #: how the trace shows it: ("pass", name) · ("message", text) · ("summary", prefix)
    marker: tuple


def _src(name: str) -> str:
    try:
        return open(os.path.join(AGENT, name), encoding="utf-8").read()
    except OSError:
        return ""


def _passes() -> Dict[str, Entry]:
    names = sorted(set(re.findall(r'_pass\("([a-z_]+)"', _src("planner.py"))))
    return {n: Entry(n, "pass", "planner.py", ("pass", n)) for n in names}


# Safeguards and FASTPATH: a marker is REGISTERED only if its text exists in the source,
# so a renamed or removed safeguard leaves the registry rather than a dead entry.
_SAFEGUARDS = (
    # key                      kind          file                 marker
    ("fastpath",               "fastpath",   "phase_executor.py", ("summary", "FASTPATH initiated")),
    ("fastpath_loop",          "fastpath",   "phase_executor.py", ("summary", "FASTPATH Loop")),
    ("fastpath_data_expansion", "fastpath",  "phase_executor.py", ("summary", "FASTPATH Data Expansion")),
    ("plan_hydration_injected", "safeguard", "planner.py",        ("summary", "PLAN HYDRATION")),
    ("self_correction",        "safeguard",  "phase_executor.py", ("message", "System Self-Correction")),
    ("argument_refinement",    "safeguard",  "phase_executor.py", ("message", "Calling LLM for Argument Refinement")),
    ("argument_enrichment",    "safeguard",  "phase_executor.py", ("message", "Calling LLM for Argument Enrichment")),
    ("context_distillation",   "safeguard",  "phase_executor.py", ("message", "Context Optimization")),
    ("scope_dispatcher",       "safeguard",  "phase_executor.py", ("message", "Scope-Aware Dispatcher Action")),
    ("system_correction",      "safeguard",  "phase_executor.py", ("message", "System Correction")),
    ("proactive_replanning",   "safeguard",  "executor.py",       ("message", "Re-planning for Efficiency")),
    ("sql_consolidation_llm",  "safeguard",  "planner.py",        ("message", "Optimizing SQL Plan")),
    ("loop_classification",    "safeguard",  "planner.py",        ("message", "Analyzing Plan Efficiency")),
    ("temporal_preprocessing", "safeguard",  "planner.py",        ("summary", "Temporal preprocessing")),
    ("conversational_replan",  "safeguard",  "planner.py",        ("summary", "conversational re-plan")),
    ("strategic_parse_retry",  "safeguard",  "planner.py",        ("summary", "parse-error retry")),
)


def _safeguards() -> Dict[str, Entry]:
    out = {}
    lane = {fn: _src(fn) for fn in ("phase_executor.py", "executor.py", "planner.py", "orchestrators.py")}
    for key, kind, fn, marker in _SAFEGUARDS:
        text = marker[1]
        # a marker may live in a neighbour of the file the catalogue names; what matters is
        # that it exists SOMEWHERE on the lane, else the entry is gone, not dead
        where = next((f for f in (fn, *lane) if text in lane.get(f, "")), None)
        if where:
            out[key] = Entry(key, kind, where, marker)
    return out


def _orchestrators() -> Dict[str, Entry]:
    names = re.findall(r"^async def execute_([a-z_]+)\(", _src("orchestrators.py"), re.M)
    out = {}
    for n in names:
        key = n.replace("_orchestrator", "")
        out[f"orchestrator:{key}"] = Entry(f"orchestrator:{key}", "orchestrator", "orchestrators.py",
                                          ("orchestration", key))
    return out


REGISTRY: Dict[str, Entry] = {**_passes(), **_safeguards(), **_orchestrators()}


def keys(kind: str | None = None) -> list:
    return sorted(k for k, e in REGISTRY.items() if kind is None or e.kind == kind)


# ── reading a persisted turn ─────────────────────────────────────────────────
def _systemlog_entries(turn: dict):
    for e in (turn or {}).get("execution_trace") or []:
        a = e.get("action") or {}
        if a.get("tool_name") == "TDA_SystemLog":
            args = a.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:  # noqa: BLE001
                    args = {"message": args}
            yield args.get("message") or "", args.get("details")
        elif a.get("tool_name") == "TDA_SystemOrchestration":
            yield "System Orchestration", a


def _summary_of(details) -> str:
    if isinstance(details, dict):
        return str(details.get("summary") or details.get("details") or "")
    return str(details or "")


def fired(turn: dict) -> Set[str]:
    """Registry keys this turn's trace shows."""
    out = set()
    for msg, details in _systemlog_entries(turn):
        d = details if isinstance(details, dict) else {}
        p = d.get("pass") or (d.get("details") or {}).get("pass") if isinstance(d.get("details"), dict) else d.get("pass")
        if p and p in REGISTRY:
            out.add(p)
        summ = _summary_of(details)
        for k, e in REGISTRY.items():
            kind, text = e.marker
            if kind == "message" and msg == text:
                out.add(k)
            elif kind == "summary" and text in summ:
                out.add(k)
            elif kind == "orchestration" and msg == "System Orchestration" and \
                    text in json.dumps(details, default=str):
                out.add(k)
    return out


def _react_events(turn: dict) -> list:
    t = turn or {}
    return [] if t.get("original_plan") else (t.get("conversation_agent_events") or [])


def _payload(ev: dict) -> dict:
    p = ev.get("payload") if isinstance(ev, dict) else None
    return p if isinstance(p, dict) else (ev or {})


def llm_calls(turn: dict) -> list:
    """[(message, input_tokens, output_tokens, cost)] for every LLM call the trace records.
    A ReAct-lane turn records them as `conversation_llm_step` events (the cross-lane lever)."""
    ev = _react_events(turn)
    if ev:
        return [(str(_payload(e).get("step_name") or "ReAct step"), int(_payload(e).get("input_tokens") or 0),
                 int(_payload(e).get("output_tokens") or 0), float(_payload(e).get("cost_usd") or 0))
                for e in ev if e.get("type") == "conversation_llm_step"]
    out = []
    for msg, details in _systemlog_entries(turn):
        if msg.startswith("Calling LLM") and isinstance(details, dict) and "input_tokens" in details:
            out.append((msg, int(details.get("input_tokens") or 0), int(details.get("output_tokens") or 0),
                        float(details.get("cost_usd") or 0)))
    return out


def tool_calls(turn: dict) -> list:
    """[(tool_name, arguments, ok)] for every real tool execution in the trace. On a ReAct-lane
    turn: each `conversation_tool_invoked` paired, in order, with its `conversation_tool_completed`."""
    ev = _react_events(turn)
    if ev:
        out, pending = [], []
        for e in ev:
            p = _payload(e)
            if e.get("type") == "conversation_tool_invoked":
                args = p.get("arguments")
                pending.append([str(p.get("tool_name") or ""), args if isinstance(args, dict) else {}, True])
            elif e.get("type") == "conversation_tool_completed":
                i = next((k for k, x in enumerate(pending) if x[0] == str(p.get("tool_name") or "")), None)
                if i is not None:
                    rec = pending.pop(i)
                    rec[2] = bool(p.get("success", True))
                    out.append(tuple(rec))
        out.extend(tuple(x) for x in pending)   # invoked, never completed: counted, ok as recorded
        return [x for x in out if not x[0].startswith("TDA_System")]
    out = []
    for e in (turn or {}).get("execution_trace") or []:
        a = e.get("action") or {}
        tn = a.get("tool_name") or ""
        if tn.startswith("TDA_System") or tn == "TDA_FinalReport":
            continue
        r = e.get("result") or {}
        ok = not (isinstance(r, dict) and (r.get("status") == "error" or r.get("error")))
        out.append((tn, a.get("arguments") or {}, ok))
    return out


if __name__ == "__main__":
    for kind in ("pass", "fastpath", "safeguard", "orchestrator"):
        print(f"{kind}: {', '.join(keys(kind))}")
    print(f"{len(REGISTRY)} entries")
