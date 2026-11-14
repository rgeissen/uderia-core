#!/usr/bin/env python3
"""Design-time coverage of the lane's machinery — every registry entry provoked by a case.

    python3 test/optimize_campaign/coverage.py            # the matrix
    python3 test/optimize_campaign/coverage.py --strict   # exit 1 on an unreached entry

Pure: `registry.py` extracts the passes, safeguards and orchestrators from the source; the
suite declares what each case targets; `UNPROVOKABLE` declares what no case can reach and
why. An entry in none of the three is a piece of hardening nobody is watching — it can
regress, or silently stop firing, without a trace.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from registry import REGISTRY  # noqa: E402
from suite import CASES, LEVERS, UNPROVOKABLE, cases_for, targets_for  # noqa: E402


def coverage(lever: str) -> dict:
    t = targets_for(lever)
    out = {}
    for k in REGISTRY:
        out[k] = "targeted" if k in t else ("declared" if k in UNPROVOKABLE else "UNREACHED")
    return out


def unknown_targets() -> dict:
    bad = {}
    for c in CASES:
        for t in c["targets"]:
            if t not in REGISTRY:
                bad.setdefault(c["id"], set()).add(t)
    return bad


def stale_declarations() -> list:
    """A declaration for an entry that no longer exists, or that a case now targets."""
    targeted = {t for c in CASES for t in c["targets"]}
    return sorted(k for k in UNPROVOKABLE if k not in REGISTRY or k in targeted)


def main(argv) -> int:
    strict = "--strict" in argv
    cov = {lv: coverage(lv) for lv in LEVERS}
    w = max(len(k) for k in REGISTRY) + 1
    print("entry".ljust(w) + "".join(lv.rjust(12) for lv in LEVERS))
    gaps = []
    for k, e in sorted(REGISTRY.items(), key=lambda kv: (kv[1].kind, kv[0])):
        row = k.ljust(w)
        for lv in LEVERS:
            s = cov[lv][k]
            row += {"targeted": "target", "declared": "decl", "UNREACHED": "UNREACHED"}[s].rjust(12)
            if s == "UNREACHED":
                gaps.append(f"{k}@{lv}")
        print(row + f"   [{e.kind}]")
    print()
    for lv in LEVERS:
        c = cov[lv]
        print(f"{lv:11s} {len(cases_for(lv)):2d} cases · {sum(v == 'targeted' for v in c.values())} targeted"
              f" · {sum(v == 'declared' for v in c.values())} declared · {sum(v == 'UNREACHED' for v in c.values())} unreached")
    ut, sd = unknown_targets(), stale_declarations()
    if ut:
        print(f"\nTARGETS NAMING NO REGISTRY ENTRY: {ut}")
    if sd:
        print(f"\nSTALE DECLARATIONS (entry gone, or now targeted): {sd}")
    if gaps:
        print(f"\nUNREACHED: {sorted(set(k.split('@')[0] for k in gaps))}")
    bad = bool(gaps or ut or sd)
    print(f"\n{'coverage incomplete' if bad else 'coverage complete'}")
    return 1 if (strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
