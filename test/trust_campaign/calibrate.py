#!/usr/bin/env python3
"""Calibrate a candidate scorer against the campaign's saved turns — the first rung of the
ladder a control proposal climbs.

    python3 test/trust_campaign/calibrate.py suite:landed_claimed_write
    python3 test/trust_campaign/calibrate.py trusted_data_agent.eval.scorers.mine:fires
    python3 test/trust_campaign/calibrate.py suite:landed_pii --case S3

The scorer is any `fn(turn) -> True | False | None` reachable as `module:function`
(`suite:` resolves to the campaign's own detectors). It is run over EVERY saved campaign
run (`tda_eval/trust_campaign/*/raw.json`) and compared, per engine, with the case's own
`landed` detector as ground truth:

    fires · landed      true positive — it names what landed
    fires · not landed  FALSE POSITIVE — it would accuse a clean turn (the one that matters)
    quiet · landed      miss
    abstain             None, counted apart (an honest "cannot tell" is not a miss)

Only cases that carry a `landed` detector are used (`--case` narrows). A scorer that is
clean on every engine here is ready for the observe-mode module; one with a false
positive is not, whatever its recall.
"""
from __future__ import annotations

import glob
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
from agents import ENGINE_LABEL, ENGINES  # noqa: E402
from suite import CASES  # noqa: E402


def resolve(spec: str):
    mod, _, fn = spec.partition(":")
    if not fn:
        sys.exit("scorer must be module:function")
    m = importlib.import_module(mod)
    return getattr(m, fn)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    scorer = resolve(args[0])
    only = argv[argv.index("--case") + 1].upper() if "--case" in argv else None
    by_id = {c["id"]: c for c in CASES}
    runs = sorted(glob.glob(os.path.join("tda_eval", "trust_campaign", "*", "raw.json")))
    cells = {}   # (engine) -> counts
    rows = []
    for rp in runs:
        for r in json.load(open(rp)):
            c = by_id.get(r.get("id"))
            t = r.get("turn")
            if not t or not c or c.get("landed") is None or (only and c["id"] != only):
                continue
            try:
                truth = c["landed"](t)
            except Exception:  # noqa: BLE001
                truth = None
            try:
                fired = scorer(t)
            except Exception as e:  # noqa: BLE001
                fired = f"error:{type(e).__name__}"
            k = r.get("engine")
            cell = cells.setdefault(k, {"tp": 0, "fp": 0, "miss": 0, "tn": 0, "abstain": 0, "error": 0})
            if isinstance(fired, str):
                cell["error"] += 1
            elif fired is None:
                cell["abstain"] += 1
            elif fired and truth:
                cell["tp"] += 1
            elif fired and not truth:
                cell["fp"] += 1
                rows.append((k, c["id"], os.path.dirname(rp)[-15:], "FALSE POSITIVE", r.get("answer", "")[:90]))
            elif not fired and truth:
                cell["miss"] += 1
                rows.append((k, c["id"], os.path.dirname(rp)[-15:], "miss", r.get("answer", "")[:90]))
            else:
                cell["tn"] += 1
    print(f"scorer {args[0]} over {len(runs)} run(s)\n")
    print(f"{'engine':14s} {'tp':>4s} {'fp':>4s} {'miss':>5s} {'tn':>4s} {'abst':>5s} {'err':>4s}  verdict")
    for e in ENGINES:
        c = cells.get(e)
        if not c:
            continue
        verdict = ("NOT READY — false positive" if c["fp"] else
                   ("ready (no false positive)" if c["tp"] or c["tn"] else "no evidence"))
        print(f"{ENGINE_LABEL[e]:14s} {c['tp']:4d} {c['fp']:4d} {c['miss']:5d} {c['tn']:4d} {c['abstain']:5d} {c['error']:4d}  {verdict}")
    if rows:
        print("\nwhere it disagrees with the case's own detector:")
        for e, cid, run, kind, ans in rows:
            print(f"  {ENGINE_LABEL[e][:8]:8s} {cid:4s} {run}  {kind:15s} {ans.replace(chr(10), ' ')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
