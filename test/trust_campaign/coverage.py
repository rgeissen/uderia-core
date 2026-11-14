#!/usr/bin/env python3
"""Design-time coverage — which registry points the campaign can reach, per engine.

    python3 test/trust_campaign/coverage.py            # the matrix
    python3 test/trust_campaign/coverage.py --strict   # exit 1 on an unreachable point

Pure: imports the registry from `src/` and the suite; no server. This is invariant I1
asked BEFORE a turn runs: a point that no case targets, that no turn exercises on its own,
and that no declaration explains is a gate the campaign cannot see — and a gate the
campaign cannot see is one that can regress without a trace.

The three ways a point is covered (suite.py):
    targeted      some case for the engine names it in `targets`
    every turn    EVERY_TURN — exercised by any executed turn, read by I1 on every case
    declared      UNPROVOKABLE — with the reason, and what would make it provokable
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from suite import ENGINES, EVERY_TURN, UNPROVOKABLE, cases_for, targets_for  # noqa: E402

from trusted_data_agent.agent import assurance_points as ap  # noqa: E402

LABEL = {"llm_only": "Ideate", "conversation_with_tools": "Ideate+tools", "rag_focused": "Focus",
         "tool_enabled": "Optimize", "genie": "Coordinate"}


def applicable(engine: str) -> list:
    """Registry points that apply to this class, excluded ones left out (they never read)."""
    return [p for p in ap.points() if p.applies(engine)]


def coverage(engine: str) -> dict:
    """{point_key: 'targeted' | 'every turn' | 'declared' | 'UNREACHED'}"""
    t = targets_for(engine)
    out = {}
    for p in applicable(engine):
        if p.key in t:
            out[p.key] = "targeted"
        elif p.key in EVERY_TURN:
            out[p.key] = "every turn"
        elif p.key in UNPROVOKABLE:
            out[p.key] = "declared"
        else:
            out[p.key] = "UNREACHED"
    return out


def unknown_targets() -> dict:
    """Targets that name no registry point — a typo in a case is a case that covers nothing."""
    keys = {p.key for p in ap.points(include_excluded=True)}
    bad = {}
    for e in ENGINES:
        for c in cases_for(e):
            for t in c["targets"]:
                if t not in keys:
                    bad.setdefault(c["id"], set()).add(t)
    return bad


def misapplied_targets() -> dict:
    """A case whose target applies to NONE of its engines claims coverage it can deliver
    nowhere. (A target outside SOME of a multi-engine case's classes is fine — `coverage`
    counts it only where the point applies.)"""
    bad = {}
    by = {p.key: p for p in ap.points(include_excluded=True)}
    seen = set()
    for e in ENGINES:
        for c in cases_for(e):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            for t in c["targets"]:
                p = by.get(t)
                if p and not any(p.applies(x) for x in c["engines"]):
                    bad.setdefault(c["id"], set()).add(t)
    return bad


def main(argv) -> int:
    strict = "--strict" in argv
    keys = [p.key for p in ap.points()]
    cov = {e: coverage(e) for e in ENGINES}
    w = max(len(k) for k in keys) + 1
    print("point".ljust(w) + "".join(LABEL[e].rjust(13) for e in ENGINES))
    MARK = {"targeted": "target", "every turn": "turn", "declared": "decl", "UNREACHED": "UNREACHED"}
    gaps = []
    for k in keys:
        row = k.ljust(w)
        for e in ENGINES:
            s = cov[e].get(k)
            row += (MARK[s] if s else "—").rjust(13)
            if s == "UNREACHED":
                gaps.append(f"{k}@{e}")
        print(row)
    print()
    for e in ENGINES:
        n = len(cases_for(e))
        c = cov[e]
        print(f"{LABEL[e]:13s} {n:2d} cases · {sum(1 for v in c.values() if v == 'targeted')} targeted"
              f" · {sum(1 for v in c.values() if v == 'every turn')} every-turn"
              f" · {sum(1 for v in c.values() if v == 'declared')} declared"
              f" · {sum(1 for v in c.values() if v == 'UNREACHED')} unreached")
    if UNPROVOKABLE:
        print("\ndeclared unprovokable (each a to-do, not an exemption):")
        for k, why in UNPROVOKABLE.items():
            print(f"  {k:20s} {why}")
    ut, mt = unknown_targets(), misapplied_targets()
    if ut:
        print(f"\nTARGETS NAMING NO REGISTRY POINT: {ut}")
    if mt:
        print(f"\nTARGETS OUTSIDE THE CASE'S ENGINE CLASS: {mt}")
    if gaps:
        print(f"\nUNREACHED: {gaps}")
    bad = bool(gaps or ut or mt)
    print(f"\n{'coverage incomplete' if bad else 'coverage complete'}")
    return 1 if (strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
