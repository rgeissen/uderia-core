#!/usr/bin/env python3
"""Gate — the dashboard KPIs read what their labels say (Oct 2026).

The Awareness · Trust · Anatomy panels each read a different ledger; this gate holds the
two server-side readings that were added so the panels stop re-deriving:

  §1  Velocity (24h) — ONE series on both tabs: 24 hourly points ending now, zero-filled
      (an absent hour drew the sparkline with a non-linear axis; the System tab was
      per-day over 30 days under a card labelled 24h).
  §2  Resolved faculties on GET /v1/profiles — `componentsEnabled` / `skillsApplied` are
      the runtime's own answer (enablement chain · auto companions), so the Anatomy tiles
      no longer count from the raw config (a profile with no componentConfig read as
      having nothing while it runs with memory and the action components on).

Run:  PYTHONPATH=src python3 test/test_dashboard_kpis.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_pass = 0
_fail = []


def check(name, cond, detail=""):
    global _pass
    if cond:
        _pass += 1
        print(f"  \u2713 {name}")
    else:
        _fail.append(name)
        sep = " \u2014 "   # outside the f-string expression: 3.11 rejects a backslash inside one
        print(f"  \u2717 {name}{(sep + str(detail)) if detail else ''}")


def t_hourly_velocity():
    from trusted_data_agent.api.rest_routes import _hourly_velocity
    now = datetime(2026, 10, 22, 14, 37, 12, tzinfo=timezone.utc)
    series = _hourly_velocity({"2026-10-22 14:00:00": 3, "2026-10-21 15:00:00": 1,
                               "2026-10-21 14:00:00": 9}, now)   # the last is outside the window
    check("velocity: exactly 24 points", len(series) == 24, len(series))
    check("velocity: ends at the current hour", series[-1] == {"hour": "2026-10-22 14:00:00", "count": 3}, series[-1])
    check("velocity: starts 23 hours earlier", series[0]["hour"] == "2026-10-21 15:00:00", series[0])
    check("velocity: an hour with nothing is a zero point, not a gap",
          sum(1 for p in series if p["count"] == 0) == 22)
    check("velocity: a count outside the window is dropped", all(p["count"] != 9 for p in series))
    hours = [p["hour"] for p in series]
    check("velocity: hours are strictly consecutive", all(
        datetime.strptime(b, "%Y-%m-%d %H:00:00") - datetime.strptime(a, "%Y-%m-%d %H:00:00") == timedelta(hours=1)
        for a, b in zip(hours, hours[1:])))
    check("velocity: an empty ledger still draws 24 zeros", sum(p["count"] for p in _hourly_velocity({}, now)) == 0)


def t_velocity_is_one_definition():
    src = (Path(__file__).resolve().parents[1] / "src/trusted_data_agent/api/rest_routes.py").read_text()
    check("velocity: both consumption summaries build the series through _hourly_velocity",
          src.count("_hourly_velocity(dict(") == 2, src.count("_hourly_velocity(dict("))
    check("velocity: the 30-day per-day series is gone", "thirty_days_ago" not in src)


def t_profile_faculties():
    """Resolved against the real component/skill registries, on synthetic profiles."""
    from trusted_data_agent.api.rest_routes import _enrich_profile_faculties
    bare = {"id": "p-bare", "tag": "BARE", "profile_type": "tool_enabled"}
    off = {"id": "p-off", "tag": "OFF", "profile_type": "tool_enabled",
           "componentConfig": {"memory": {"enabled": False}, "chart": {"enabled": False}}}
    explicit = {"id": "p-x", "tag": "X", "profile_type": "llm_only",
                "skillsConfig": {"skills": [{"id": "my-own-skill", "active": True}]}}
    profiles = [bare, off, explicit]
    _enrich_profile_faculties(profiles, None)
    for p in profiles:
        check(f"faculties: {p['tag']} carries both fields as lists",
              isinstance(p.get("componentsEnabled"), list) and isinstance(p.get("skillsApplied"), list))
    check("faculties: a profile with NO componentConfig runs with memory on (the manifest default)",
          "memory" in bare["componentsEnabled"], bare["componentsEnabled"])
    check("faculties: …and its companion skills auto-apply",
          "memory-expert" in bare["skillsApplied"] and "chart-expert" in bare["skillsApplied"], bare["skillsApplied"])
    check("faculties: an explicit enabled:false is honoured",
          "memory" not in off["componentsEnabled"] and "chart" not in off["componentsEnabled"], off["componentsEnabled"])
    check("faculties: …and the companion of a disabled component does not apply",
          "chart-expert" not in off["skillsApplied"], off["skillsApplied"])
    check("faculties: an explicit active skill applies beside the companions",
          "my-own-skill" in explicit["skillsApplied"], explicit["skillsApplied"])
    check("faculties: the raw config is left untouched (read-only enrichment)",
          "componentConfig" not in bare and bare.get("skillsConfig") is None)


if __name__ == "__main__":
    for t in (t_hourly_velocity, t_velocity_is_one_definition, t_profile_faculties):
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:  # noqa: BLE001
            _fail.append(f"{t.__name__}: {e!r}")
            print(f"  \u2717 raised {e!r}")
    print(f"\n== {_pass} passed, {len(_fail)} failed ==")
    sys.exit(1 if _fail else 0)
