#!/usr/bin/env python3
"""
Drift guard for the TDA_Flow LLM tool surface.

Every action the LLM is told about — in the manifest action enum and in
instructions.json — MUST be dispatched by FlowComponentHandler.process(). This
catches the class of bug where guidance advertises an action the handler does
not implement (e.g. the phantom action='snapshot'), which wastes prompt tokens
and produces guaranteed failed tool calls.

Derives the dispatched set straight from handler.py source so the test tracks
the real code, not a hardcoded copy.

Usage:
    python test/test_flow_instructions_consistency.py
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "components" / "builtin" / "flow"

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


def _dispatched_actions():
    """Actions handled by FlowComponentHandler.process(), parsed from source."""
    src = (FLOW / "handler.py").read_text()
    actions = set(re.findall(r'action == "([a-z_]+)"', src))
    # Multi-line tuple memberships: `action in ("add_node", "remove_node", ...)`
    for m in re.finditer(r"action in \(([^)]*)\)", src):
        actions |= set(re.findall(r'"([a-z_]+)"', m.group(1)))
    return actions


def _manifest_enum_actions():
    m = json.loads((FLOW / "manifest.json").read_text())
    desc = m["tool_definition"]["args"]["action"]["description"]
    return set(re.findall(r"'([a-z_]+)'", desc))


def _instruction_actions():
    ins = json.loads((FLOW / "instructions.json").read_text())
    blob = "\n".join(v for v in ins.values() if isinstance(v, str))
    # The LLM is instructed via action='X'
    return set(re.findall(r"action='([a-z_]+)'", blob))


def main():
    print("=" * 70)
    print("TDA_Flow TOOL-SURFACE CONSISTENCY GUARD")
    print("=" * 70)

    dispatched = _dispatched_actions()
    enum_actions = _manifest_enum_actions()
    instr_actions = _instruction_actions()

    print(f"\nDispatched ({len(dispatched)}): {sorted(dispatched)}")
    print(f"Manifest enum ({len(enum_actions)}): {sorted(enum_actions)}")
    print(f"Referenced in instructions ({len(instr_actions)}): {sorted(instr_actions)}")

    print("\n[manifest enum ⊆ dispatched]")
    for a in sorted(enum_actions):
        check(f"manifest action '{a}' is dispatched", a in dispatched)

    print("\n[instructions ⊆ dispatched  (anti-phantom)]")
    for a in sorted(instr_actions):
        check(f"instruction action '{a}' is dispatched", a in dispatched,
              "advertised to the LLM but handler has no branch")

    print("\n[explicit regression guards]")
    check("no phantom 'snapshot' action dispatched", "snapshot" not in dispatched)
    check("'snapshot' not advertised in manifest enum", "snapshot" not in enum_actions)
    check("'snapshot' not advertised in instructions", "snapshot" not in instr_actions)
    check("update_global_params is dispatched", "update_global_params" in dispatched)
    check("update_global_params is advertised in manifest enum",
          "update_global_params" in enum_actions)

    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
