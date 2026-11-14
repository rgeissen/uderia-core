"""
The topology registry — PROP-007 §3 / §7.

Manifest rows, as DATA: key, label, one-line description, the glyph key (the
renderer's `TOPOLOGY_GLYPHS` is keyed identically), the parameters with their
ranges and defaults, the slot roles each shape opens, and `requires` — the engine
primitives a shape needs before it can be generated. A shape whose `requires` are
not yet shipped is DECLARED here so the picker can show it greyed with the reason
(never hidden), and `generate()` refuses it by name.

Why rows and not code: the picker, the REST catalog, the LLM tool contract and the
generators all read the same row, so a parameter range or a slot role can never
drift between the four callers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Bumped when a generator's OUTPUT shape changes for the same (key, params) — the
# stamp on a created flow records which generation produced it (copy, never link:
# a bump never re-applies to flows already created).
TEMPLATE_VERSION = 1

# Engine primitives that exist today. A row's `requires` is checked against this set.
# Increment 3 (Oct 2026) shipped round history, the judge node and the canvas artefact node.
SHIPPED_PRIMITIVES = frozenset({"scope", "decision", "merge", "parallel", "round_history", "judge", "artefact"})

# The IFOC classes a slot may accept (plus the judge panel, a non-profile fill).
IFOC_CLASSES = ("llm_only", "rag_focused", "tool_enabled", "genie")
AGENT_ACCEPTS = ["llm_only", "rag_focused", "tool_enabled"]
JUDGE_ACCEPTS = ["judge_panel", "genie", "llm_only"]

# Slot kinds decide the pre-fill rule (§7): symmetric roles fill in declared order,
# asymmetric roles only ever offer suggestions, the judge is always the panel.
SYMMETRIC, ASYMMETRIC, JUDGE = "symmetric", "asymmetric", "judge"


def _p(name: str, label: str, default: int, lo: int, hi: int, adapts: bool = False) -> Dict[str, Any]:
    """A parameter row. `adapts` marks the ONE parameter a coordinator's expert count
    sets on pre-fill (clamped to [lo, hi])."""
    return {"name": name, "label": label, "type": "int", "default": default, "min": lo, "max": hi, "adapts": adapts}


def _slot(role: str, kind: str, hint: str, accepts: Optional[List[str]] = None, count: Any = 1) -> Dict[str, Any]:
    """A slot-role row. `count` is an int or the name of the parameter that sets it."""
    return {"role": role, "kind": kind, "hint": hint,
            "accepts": list(accepts if accepts is not None else AGENT_ACCEPTS), "count": count}


TEMPLATES: List[Dict[str, Any]] = [
    {
        "key": "pipeline", "label": "Pipeline",
        "description": "Stages in sequence — each agent refines what the previous one produced.",
        "glyph": "pipeline",
        "parameters": [_p("stages", "Stages", 3, 2, 6, adapts=True)],
        "slots": [_slot("stage", SYMMETRIC, "an agent that takes the previous stage's output and produces this stage's", count="stages")],
        "requires": [],
    },
    {
        "key": "parallel", "label": "Parallel",
        "description": "Independent tasks run at once; each agent gets the same brief.",
        "glyph": "parallel",
        "parameters": [_p("tasks", "Tasks", 3, 2, 6, adapts=True)],
        "slots": [_slot("task", SYMMETRIC, "an agent that handles one independent part of the brief", count="tasks")],
        "requires": ["parallel"],
    },
    {
        "key": "fork-join", "label": "Fork–join",
        "description": "Workers fan out, one synthesiser joins their results into a single answer.",
        "glyph": "fork-join",
        "parameters": [_p("branches", "Branches", 3, 2, 6, adapts=True)],
        "slots": [
            _slot("worker", SYMMETRIC, "an agent that works one branch of the problem", count="branches"),
            _slot("synthesiser", ASYMMETRIC, "an agent that combines every branch's result into one answer"),
        ],
        "requires": ["parallel"],
    },
    {
        "key": "conditional", "label": "Conditional",
        "description": "A router classifies the request; exactly one branch agent answers it.",
        "glyph": "conditional",
        "parameters": [_p("branches", "Branches", 2, 2, 4, adapts=True)],
        "slots": [
            _slot("router", ASYMMETRIC, "an agent that names which branch the request belongs to"),
            _slot("branch", SYMMETRIC, "an agent that answers requests of one kind", count="branches"),
        ],
        "requires": ["decision"],
    },
    {
        "key": "iterative", "label": "Iterative",
        "description": "One agent refines its answer round after round until a judge passes it.",
        "glyph": "iterative",
        "parameters": [_p("max_rounds", "Max rounds", 3, 1, 8)],
        "slots": [
            _slot("worker", ASYMMETRIC, "an agent that produces and refines the answer"),
            _slot("judge", JUDGE, "grades each draft; the eval judge panel by default", accepts=JUDGE_ACCEPTS),
        ],
        # A round's worker reads its own previous round (round history) and the loop
        # stops on the judge's verdict (the scope's convergence exit).
        "requires": ["scope", "round_history", "judge"],
    },
    {
        "key": "debate", "label": "Debate",
        "description": "Debaters argue round by round; a moderator calls consensus, a synthesiser writes it up.",
        "glyph": "debate",
        "parameters": [_p("debaters", "Debaters", 3, 2, 4, adapts=True), _p("max_rounds", "Max rounds", 3, 1, 6)],
        "slots": [
            _slot("debater", SYMMETRIC, "an agent that argues one position", count="debaters"),
            _slot("judge", JUDGE, "the moderator — decides whether the debaters have converged; the eval judge panel by default", accepts=JUDGE_ACCEPTS),
            _slot("synthesiser", ASYMMETRIC, "an agent that writes the final answer from the whole debate"),
        ],
        "requires": ["scope", "parallel", "round_history", "judge"],
    },
    {
        "key": "teacher-student", "label": "Teacher–student",
        "description": "A student attempts, a teacher teaches and decides when the student has passed.",
        "glyph": "teacher-student",
        "parameters": [_p("max_rounds", "Max rounds", 3, 1, 8)],
        "slots": [
            _slot("student", ASYMMETRIC, "the agent being taught — it attempts the task each round"),
            # The teacher IS the grader: deciding when a lesson is learned is the teacher's
            # job. An independent grade is the iterative shape (eval judge panel).
            _slot("teacher", ASYMMETRIC, "the agent that explains what is wrong and decides when the student has passed"),
        ],
        "requires": ["scope", "round_history", "judge"],
    },
    {
        "key": "pair-programming", "label": "Pair programming",
        "description": "A driver writes the code into a canvas, a navigator reviews it — until the navigator approves.",
        "glyph": "pair-programming",
        "parameters": [_p("max_rounds", "Max rounds", 3, 1, 8)],
        "slots": [
            _slot("driver", ASYMMETRIC, "the agent that writes the code"),
            # The navigator approves — the reviewing role decides when the code is ready.
            _slot("navigator", ASYMMETRIC, "the agent that reviews the code and approves it when it is ready"),
        ],
        "requires": ["scope", "round_history", "judge", "artefact"],
    },
    {
        "key": "swarm", "label": "Swarm",
        "description": "Experts coordinated by a router — this is a coordinator agent, not a flow.",
        "glyph": "swarm",
        "parameters": [],
        "slots": [],
        "requires": [],
        # Not a flow: the card hands off to the genie profile editor.
        "handoff": {"to": "coordinator", "label": "Create a coordinator with these experts"},
    },
    {
        "key": "blank", "label": "Blank",
        "description": "An empty canvas — start and end, nothing in between.",
        "glyph": "blank",
        "parameters": [],
        "slots": [],
        "requires": [],
    },
]

_BY_KEY: Dict[str, Dict[str, Any]] = {t["key"]: t for t in TEMPLATES}


def get_template(key: str) -> Optional[Dict[str, Any]]:
    return _BY_KEY.get(str(key or "").strip().lower())


def template_keys() -> List[str]:
    return [t["key"] for t in TEMPLATES]


def missing_requirements(template: Dict[str, Any]) -> List[str]:
    """The primitives a template needs that the engine does not ship yet."""
    return [r for r in template.get("requires", []) if r not in SHIPPED_PRIMITIVES]


def is_available(template: Dict[str, Any]) -> bool:
    return not template.get("handoff") and not missing_requirements(template)


def unavailable_reason(template: Dict[str, Any]) -> Optional[str]:
    """The sentence the picker shows on a greyed card — never a bare key."""
    if template.get("handoff"):
        return "not a flow — creates a coordinator agent instead"
    missing = missing_requirements(template)
    if not missing:
        return None
    words = {"round_history": "round history", "judge": "the judge node", "artefact": "the canvas artefact node"}
    return "needs " + " and ".join(words.get(m, m) for m in missing) + " (not yet available)"


def default_params(template: Dict[str, Any]) -> Dict[str, Any]:
    return {p["name"]: p["default"] for p in template.get("parameters", [])}


def clamp_params(template: Dict[str, Any], params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Every default is valid; every supplied value is coerced into its range. An
    unknown parameter is dropped (a typo must not silently change the shape)."""
    out = default_params(template)
    for p in template.get("parameters", []):
        if params and p["name"] in params:
            try:
                v = int(params[p["name"]])
            except (TypeError, ValueError):
                continue
            out[p["name"]] = max(p["min"], min(p["max"], v))
    return out


def slot_count(slot_row: Dict[str, Any], params: Dict[str, Any]) -> int:
    c = slot_row.get("count", 1)
    if isinstance(c, str):
        return int(params.get(c, 1))
    return int(c)


def declared_roles(template: Dict[str, Any]) -> List[str]:
    return [s["role"] for s in template.get("slots", [])]


def adapting_parameter(template: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The one parameter a coordinator's expert count sets on pre-fill, if any."""
    for p in template.get("parameters", []):
        if p.get("adapts"):
            return p
    return None


def catalog() -> List[Dict[str, Any]]:
    """The registry as the picker and the REST catalog serve it (one request)."""
    rows = []
    for t in TEMPLATES:
        rows.append({
            "key": t["key"], "label": t["label"], "description": t["description"], "glyph": t["glyph"],
            "parameters": t["parameters"], "slots": t["slots"], "requires": t["requires"],
            "available": is_available(t), "unavailable_reason": unavailable_reason(t),
            "handoff": t.get("handoff"), "version": TEMPLATE_VERSION,
        })
    return rows
