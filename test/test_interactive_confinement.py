#!/usr/bin/env python3
"""An agent's authority may be declared to apply to INTERACTIVE turns too.

WHY THIS GATE EXISTS
Found by an operator during a walkthrough. They ticked "Surface-only (structural
confinement)" on an agent, asked it a question its signed product answers
exactly, and watched it hand-write

    SELECT COUNT(CustomerID) FROM fitness_db.Customers

four times. Nothing was denied, because a principal is derived only for
AUTONOMOUS_ORIGINS — an interactive turn never gets one, so `is_tool_allowed` is
never consulted at all.

The original reasoning was that a present human is the control. It is thinner
than it looks: what the human sees is prose, not SQL, and a switch labelled
*structural confinement* that silently does not apply to the mode the operator
is actually using is the wrong default.

So the SCOPE became a declared choice rather than a hardcoded constant. This
gate pins the two halves that matter: absent/false is byte-identical to the
historic behaviour, and true confines interactive turns as well.

Run: python3 test/test_interactive_confinement.py       (no server, no network)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import agent_authority as aa  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


SURFACE = {"dataSurface": {"products": ["support_load"], "exclusive": True, "advisory": False}}

# ------------------------------------------------------------ the default is UNCHANGED
check("no authorityConfig ⇒ interactive is not confined",
      aa.enforces_interactive({}) is False)
check("authority set but flag absent ⇒ interactive is not confined",
      aa.enforces_interactive({"authorityConfig": SURFACE}) is False,
      "every profile that exists today must behave exactly as before")
check("flag false ⇒ interactive is not confined",
      aa.enforces_interactive({"authorityConfig": dict(SURFACE, enforce_interactive=False)}) is False)

# only an explicit True opts in — a truthy string must not silently enable enforcement
for truthy in ("true", 1, "yes", [1]):
    check(f"{truthy!r} does not opt in (explicit True only)",
          aa.enforces_interactive(
              {"authorityConfig": dict(SURFACE, enforce_interactive=truthy)}) is False,
          "a hand-edited or migrated config must never start denying tools by accident")

check("explicit True opts in",
      aa.enforces_interactive({"authorityConfig": dict(SURFACE, enforce_interactive=True)}) is True)

# ------------------------------------------------------------------ never raises
for bad in (None, 42, "text", {"authorityConfig": None}, {"authorityConfig": "x"},
            {"authorityConfig": []}):
    try:
        check(f"malformed profile {str(bad)[:22]} degrades to not-confined",
              aa.enforces_interactive(bad) is False)
    except Exception as exc:
        check(f"malformed profile {str(bad)[:22]} degrades to not-confined", False,
              f"raised {exc!r} — a config read must never break a turn")

# ---------------------------------------------------- the runtime consults the choice
import inspect  # noqa: E402

svc_raw = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                            "agent", "execution_service.py")).read()
# Comments STRIPPED before grepping: the first version of this section matched the
# explanatory comment above the gate, so a mutation that reverted the gate itself
# sailed through while the comment kept describing behaviour the code no longer had.
# Prose about the code is not the code.
svc = "\n".join(ln for ln in svc_raw.splitlines() if not ln.lstrip().startswith("#"))

# Anchor on the enclosing `if`, not on a character distance — a distance bound stops
# covering what it names the moment the function grows past it.
_lines = svc.splitlines()
_call = next(n for n, ln in enumerate(_lines) if "set_current_principal" in ln
             and "def " not in ln)
_gate = next((ln for ln in reversed(_lines[max(0, _call - 12):_call])
              if ln.lstrip().startswith("if ") or ln.lstrip().startswith("or ")
              or "AUTONOMOUS_ORIGINS" in ln or "enforces_interactive" in ln), "")
_gate_block = "\n".join(_lines[max(0, _call - 6):_call])

check("the principal gate consults the declared scope",
      "enforces_interactive" in _gate_block,
      f"gate reads: {_gate_block.strip()[:140]!r} — the flag is meaningless unless the "
      "ONE gate that derives a principal reads it")
check("the autonomous origins still derive a principal",
      "AUTONOMOUS_ORIGINS" in _gate_block,
      "opting in must WIDEN the scope, never replace it — a flow/expert/scheduled "
      "turn stays confined whatever this flag says")

# the whole authority applies, not a partial principal
src = inspect.getsource(aa.enforces_interactive)
check("the choice governs the whole authorityConfig",
      "whole authorityConfig" in src or "WHOLE authorityConfig" in src,
      "a partially-applied principal would be a second, subtler notion of authority — "
      "and two scopes is the confusion this removes")

# ------------------------------------------------- weakening it is a pack downgrade
from trusted_data_agent.core import profile_config_contract as pcc  # noqa: E402

spec = next(s for s in pcc.REGISTRY if s.key == "authorityConfig")
check("turning it off is guarded as a downgrade on pack import",
      ("enforce_interactive",) in tuple(spec.flag_paths or ()),
      "a republished pack must not silently un-confine an agent that was confined")

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — the enforcement scope is a declared choice"))
sys.exit(1 if FAIL else 0)
