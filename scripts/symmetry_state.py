#!/usr/bin/env python3
"""The LIVE symmetry state, derived from the assurance registry.

    python3 scripts/symmetry_state.py

Committed because TRUST_SYMMETRY_MAP.md is a point-in-time analysis and this is not: a
hand-kept score is accurate on the day it is written, and the map's own Part E argued that
a second copy of a fact is a defect. This reads the bindings.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.agent import assurance as A            # noqa: E402
from trusted_data_agent.agent.assurance import conformance     # noqa: E402

A.bootstrap()
LABEL = {
    "shared": "both lanes, ONE assessor",
    "split": "both lanes, DIFFERENT assessors",
    "gap": "one lane bound, the other wanted",
    "by_design_score_only": "score-only by design",
    "by_design_act_only": "act-only by design",
    "unbound": "nothing bound",
}
rows = [A.registry.symmetry(p) for p in sorted(
    {b["property"] for b in A.registry.bindings()})]
w = max(len(r["property"]) for r in rows)
print(f"{'PROPERTY':{w}}  {'STATE':22}  IN-FLIGHT / POST-HOC")
print("-" * (w + 60))
for r in rows:
    print(f"{r['property']:{w}}  {LABEL.get(r['state'], r['state']):22}  "
          f"{r['in_flight'] or '—'} / {r['post_hoc'] or '—'}")
n = len(rows)
shared = sum(1 for r in rows if r["state"] == "shared")
bydes = sum(1 for r in rows if r["state"].startswith("by_design"))
print(f"\n{shared} of {n} share one assessment across both lanes · "
      f"{bydes} single-lane by design · "
      f"{n - shared - bydes} outstanding")
bad = conformance.audit()
print(f"binding conformance: {'clean' if not bad else bad}")
sys.exit(1 if bad else 0)
