#!/usr/bin/env python3
"""Re-analyse a saved campaign run — the raw turn payloads are the evidence.

    python3 test/trust_campaign/analyse.py                  # newest run
    python3 test/trust_campaign/analyse.py <run-dir>
    python3 test/trust_campaign/analyse.py <run-dir> --engine optimize
    python3 test/trust_campaign/analyse.py <run-dir> --case B4      # one case, every engine, in full
    python3 test/trust_campaign/analyse.py <run-dir> --recheck      # re-apply the CURRENT suite's
                                                                    # invariants + expectations to the
                                                                    # saved turns (after a suite fix —
                                                                    # no tokens spent), rewrite raw.json

Recomputes what each surface SHOWS (dial stations folding dimensions in, exactly as
`assuranceDial.stations()` does) and audits the partition per turn: where each failing
dimension surfaced, and whether JUDGED double-counted anything. Then the cross-engine
view: the same case read on every engine side by side — a station that reads differently
for the same provocation is the first thing to look at.
"""
from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agents import ENGINE_LABEL, ENGINES  # noqa: E402
from run_campaign import SHORT, station_states  # noqa: E402
from suite import (CASES, MEASUREMENT_STATION, STATIONS, answer_text,  # noqa: E402
                   check_expectations, check_invariants, labelled_station)

AB = {"pass": "ok", "warn": "WARN", "danger": "DANG", "na": "—", "unknown": "?", "pending": "…"}


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    d = args[0] if args else sorted(glob.glob("tda_eval/trust_campaign/*/"))[-1]
    eng = None
    case = None
    if "--engine" in argv:
        eng = SHORT.get(argv[argv.index("--engine") + 1].lower(), argv[argv.index("--engine") + 1])
    if "--case" in argv:
        case = argv[argv.index("--case") + 1].upper()
    rs = json.load(open(os.path.join(d, "raw.json")))
    if "--recheck" in argv:
        # the saved turn payloads are the evidence; the suite is the judge — a corrected
        # case or invariant is re-applied here without re-running a single turn. The
        # applicability denominator for I1 is the saved reading set itself (what the
        # server declared applicable is what it read with `applies: true`).
        by_id = {c["id"]: c for c in CASES}
        n = 0
        for r in rs:
            t = r.get("turn")
            c = by_id.get(r.get("id"))
            if not t or not c:
                continue
            applicable = {p.get("key") for p in ((t.get("assurance") or {}).get("points") or [])
                          if p.get("applies", True)}
            r["invariants"] = check_invariants(t, applicable, c)
            r["expectations"] = check_expectations(c, t, r.get("engine"))
            n += 1
        json.dump(rs, open(os.path.join(d, "raw.json"), "w"), indent=1, default=str)
        print(f"rechecked {n} turns against the current suite → raw.json rewritten\n")
    if eng:
        rs = [r for r in rs if r.get("engine") == eng]
    if case:
        rs = [r for r in rs if r.get("id") == case]
    print(f"run: {d}   cases: {len(rs)}\n")

    if case:
        for r in rs:
            t = r.get("turn") or {}
            print(f"## {ENGINE_LABEL.get(r.get('engine'), r.get('engine'))} · {r['id']} · status={r.get('status')} class={r.get('class_read')}")
            print(f"   answer: {answer_text(t)[:400].replace(chr(10), ' ')}")
            for st, ps in ((t.get("assurance") or {}).get("by_station") or {}).items():
                for p in ps:
                    if p.get("applies", True):
                        print(f"   {st:9s} {p['key']:20s} {p['state']:7s}{'*' if p.get('acted') else ' '} {p.get('detail','')[:110]}")
            bad = [x for x in (r.get("invariants") or []) + (r.get("expectations") or []) if not x["ok"]]
            for x in bad:
                print(f"   NOT SATISFIED {x['id']} — {x['detail']}")
            print()
        return 0

    print("STATION MATRIX — what the dial shows (mechanisms AND their dimensions)")
    hdr = "engine        case  " + " ".join(f"{s[:2].upper():>5s}" for s in STATIONS)
    print(hdr + "   failing dimensions")
    print("-" * (len(hdr) + 40))
    for r in rs:
        if not r.get("turn"):
            print(f"{ENGINE_LABEL.get(r.get('engine'), '?'):13s} {r['id']:5s} ERROR")
            continue
        st = station_states(r["turn"])
        row = " ".join(f"{AB.get(st[s], st[s]):>5s}" for s in STATIONS)
        print(f"{ENGINE_LABEL.get(r.get('engine'), '?'):13s} {r['id']:5s} {row}   {', '.join(r['failed_dimensions'])[:52]}")

    print("\nCROSS-ENGINE — the same case on every engine (worst station, dims folded in)")
    engines = [e for e in ENGINES if any(r.get("engine") == e for r in rs)]
    cases = []
    for r in rs:
        if r["id"] not in cases:
            cases.append(r["id"])
    print("case  " + "".join(f"{ENGINE_LABEL[e][:12]:>14s}" for e in engines))
    for cid in cases:
        row = f"{cid:5s} "
        for e in engines:
            r = next((x for x in rs if x["id"] == cid and x.get("engine") == e), None)
            if not r:
                row += f"{'·':>14s}"
            elif not r.get("turn"):
                row += f"{'ERROR':>14s}"
            else:
                st = r["stations"]
                worst = max(st.values(), key=lambda v: {"na": 0, "pass": 1, "pending": 2, "unknown": 3, "warn": 4, "danger": 5}[v])
                where = [s[:2].upper() for s in STATIONS if st[s] == worst and worst not in ("na", "pass")]
                row += f"{AB[worst] + ('@' + ','.join(where) if where else ''):>14s}"
        print(row)

    print("\nPARTITION AUDIT — every failing dimension, and where it surfaced")
    bad = 0
    for r in rs:
        t = r.get("turn")
        if not t:
            continue
        meas = (t.get("assurance") or {}).get("measures") or {}
        seen = {}
        for stn, ds in meas.items():
            for dd in (ds or []):
                if isinstance(dd, dict) and dd.get("k"):
                    seen.setdefault(dd["k"], set()).add(stn)
        for dim in r.get("failed_dimensions") or []:
            about = labelled_station(dim) or "—"
            where = sorted(seen.get(dim, []))
            ok = where in ([MEASUREMENT_STATION], [])
            if not ok:
                bad += 1
            print(f"  {ENGINE_LABEL.get(r.get('engine'), '?')[:8]:8s} {r['id']:4s} {dim:24s} about={about:9s} "
                  f"counted at {where or ['(not carried)']}  {'OK' if ok else '*** COUNTED OUTSIDE THE LANE ***'}")
    print(f"\n  measurements counted outside the lane: {bad}")

    print("\nGATE ACTIVITY — what actually acted, and the human work it produced")
    for r in rs:
        t = r.get("turn")
        if not t:
            continue
        g = t.get("grounding") or {}
        gi = t.get("guardrail_inbound") or {}
        go = t.get("guardrail") or {}
        h = t.get("honesty") or {}
        acts = []
        if g.get("decision") in ("block", "annotate"):
            acts.append(f"grounding={g.get('decision')}"
                        + (f"/judge:{g.get('judge_family')}" if g.get("judged") else ""))
        if gi.get("action") not in (None, "pass"):
            acts.append(f"inbound={gi.get('action')}({','.join(gi.get('categories') or [])})")
        if go.get("action") not in (None, "pass"):
            acts.append(f"outbound={go.get('action')}({','.join(go.get('categories') or [])})")
        if h.get("acted"):
            acts.append(f"honesty={h.get('action')}({h.get('kind') or h.get('branch')})")
        parked = [p for p in by_reviewed(t) if p.get("acted")]
        print(f"  {ENGINE_LABEL.get(r.get('engine'), '?')[:8]:8s} {r['id']:4s} {'; '.join(acts) or 'no gate acted':66s}"
              f" {'PARKED' if parked else ''}")

    print("\nLANDED — did the bait land, and did a station name it (I10)")
    for r in rs:
        i10 = next((x for x in (r.get("invariants") or []) if x["id"] == "I10"), None)
        if not i10:
            continue
        mark = "GAP" if not i10["ok"] else ("landed·named" if "named it" in i10["detail"] else
                                           ("—" if "did not land" in i10["detail"] else "?"))
        print(f"  {ENGINE_LABEL.get(r.get('engine'), '?')[:8]:8s} {r['id']:4s} {mark:13s} {i10['detail'][:100]}")

    print("\nINVARIANT / EXPECTATION FAILURES")
    n = 0
    for r in rs:
        for x in (r.get("invariants") or []) + (r.get("expectations") or []):
            if not x["ok"]:
                n += 1
                print(f"  {ENGINE_LABEL.get(r.get('engine'), '?')[:8]:8s} {r['id']:4s} {x['id']:34s} {x['detail'][:100]}")
    print(f"  total: {n}")
    return 0


def by_reviewed(t):
    return ((t.get("assurance") or {}).get("by_station") or {}).get("reviewed") or []


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
