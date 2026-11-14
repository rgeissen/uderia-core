"""
Rigid module test for component-driven companion-skill auto-assign
(resolve_auto_skill_specs) — pure, no server, no DB.

Run: PYTHONPATH=src python3 test/test_companion_skills.py

Rules under test:
- Component enabled + no user override  -> companion skill auto-applies.
- Component disabled + no user override  -> not applied.
- User-owned entry (auto falsey) is respected verbatim (opt-out active=false,
  opt-in active=true regardless of component state).
- Auto-managed entry (auto=true) follows the component, NOT its stored active flag.
- Legacy entries (no `auto` key) are user-owned -> backward compatible.
- Admin-unavailable skills are never applied.
- Non-companion skills behave as before; results are de-duplicated.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.skills.manager import resolve_auto_skill_specs  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


CMAP = {"chart": "chart-expert", "memory": "memory-expert", "flow": "flow-designer"}
ALWAYS = lambda sid: True
BLOCK = lambda blocked: (lambda sid: sid not in blocked)


def ids(specs):
    return sorted(s.name for s in specs)


def t_auto_assign():
    # component enabled, no entry -> auto-applied
    r = resolve_auto_skill_specs({}, {"chart"}, CMAP, ALWAYS)
    check("enabled component + no entry -> companion auto-applied", ids(r) == ["chart-expert"])

    # component disabled, no entry -> not applied
    r = resolve_auto_skill_specs({}, set(), CMAP, ALWAYS)
    check("disabled component + no entry -> not applied", r == [])

    # multiple enabled components
    r = resolve_auto_skill_specs({}, {"chart", "flow"}, CMAP, ALWAYS)
    check("multiple enabled -> both companions", ids(r) == ["chart-expert", "flow-designer"])


def t_user_override():
    # opt-out: user-owned entry active=false + component enabled -> NOT applied
    ps = {"chart-expert": {"active": False, "auto": False}}
    r = resolve_auto_skill_specs(ps, {"chart"}, CMAP, ALWAYS)
    check("user opt-out (auto=false, active=false) honored even when component on", r == [])

    # opt-in: user-owned entry active=true + component disabled -> applied
    ps = {"chart-expert": {"active": True, "auto": False}}
    r = resolve_auto_skill_specs(ps, set(), CMAP, ALWAYS)
    check("user opt-in (auto=false, active=true) honored even when component off", ids(r) == ["chart-expert"])


def t_auto_managed_follows_component():
    # auto-managed entry, stored active=true, but component now OFF -> not applied
    ps = {"chart-expert": {"active": True, "auto": True}}
    r = resolve_auto_skill_specs(ps, set(), CMAP, ALWAYS)
    check("auto-managed ignores stale active=true when component off", r == [])

    # auto-managed entry, stored active=false, but component ON -> applied
    ps = {"chart-expert": {"active": False, "auto": True}}
    r = resolve_auto_skill_specs(ps, {"chart"}, CMAP, ALWAYS)
    check("auto-managed re-applies when component on (stale active=false ignored)", ids(r) == ["chart-expert"])


def t_backward_compat():
    # legacy entry: active=true, NO auto key -> user-owned -> applied regardless of component
    ps = {"chart-expert": {"active": True}}
    r = resolve_auto_skill_specs(ps, set(), CMAP, ALWAYS)
    check("legacy active=true (no auto key) treated as user-owned -> applied", ids(r) == ["chart-expert"])
    # legacy active=false, component on -> user-owned false wins
    ps = {"chart-expert": {"active": False}}
    r = resolve_auto_skill_specs(ps, {"chart"}, CMAP, ALWAYS)
    check("legacy active=false (no auto key) user-owned -> not applied even when component on", r == [])


def t_admin_gate_and_noncompanion():
    # admin-disabled companion skill -> never applied
    r = resolve_auto_skill_specs({}, {"chart"}, CMAP, BLOCK({"chart-expert"}))
    check("admin-unavailable companion skill skipped", r == [])

    # non-companion active skill still applied; companion also auto-applied; no dup
    ps = {"teradata-sql-expert": {"active": True}, "chart-expert": {"active": True, "auto": True}}
    r = resolve_auto_skill_specs(ps, {"chart"}, CMAP, ALWAYS)
    check("non-companion active + auto companion, no duplicates",
          ids(r) == ["chart-expert", "teradata-sql-expert"])

    # param preserved on user-owned entry
    ps = {"chart-expert": {"active": True, "auto": False, "param": "compact"}}
    r = resolve_auto_skill_specs(ps, set(), CMAP, ALWAYS)
    check("param preserved", len(r) == 1 and r[0].param == "compact")


def main():
    for t in (t_auto_assign, t_user_override, t_auto_managed_follows_component,
              t_backward_compat, t_admin_gate_and_noncompanion):
        print(f"\n{t.__name__}:")
        t()
    print("-" * 60)
    print(f"RESULT: {'PASS' if _failed == 0 else 'FAIL'}  ({_passed} passed, {_failed} failed)")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
